"""Module 07 OCR: read a scanned order sheet, review every line, accept into a DRAFT order.

Acceptance (docs/modules/07-ocr.md, ACCEPTANCE_MATRIX OCR-001):
- OCR never creates an invoice, a reservation or a stock movement;
- unclear quantities (low confidence / unreadable / unknown product) need a manual correction;
- the original scan and what was read are kept unchanged; every correction is recorded;
- accuracy is measured from reviewed readings (target 95% over at least 50 lines).
Synthetic data only (fake provider reading our own synthetic PDFs).
"""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from pro_trading import ocr, outbox
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.models import Customer, OcrExtraction, OutboxEvent, Warehouse

from .conftest import idem

P = "/api/v1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def code(r):
    return r.json()["error"]["code"]


@pytest.fixture
def owner(make_api, world):
    o = make_api()
    o.login(world.emails["owner"])
    assert o.enroll_and_verify().status_code == 200
    return o


@pytest.fixture
def user(make_api, world):
    def _login(name):
        a = make_api()
        assert a.login(world.emails[name]).status_code == 200
        if name in ("finance", "manager_a"):
            assert a.enroll_and_verify().status_code == 200
        return a

    return _login


@pytest.fixture
def setup(owner):
    prods = {}
    for sku, name, unit, profiles, aliases in [
        ("CAS", "Cassava", "BOX", [("CASE40", "BOX", "40")], []),
        ("PLN", "Plantain", "PIECE", [("PC", "PIECE", None)], []),
        ("YAM", "Yellow yam", "LB", [], [{"alias": "white yam", "source_label": "sheet"}]),
    ]:
        body = {
            "sku": sku,
            "name": name,
            "stock_unit": unit,
            "unit_profiles": [
                {"code": c, "unit_code": u, "weight_lb_per_unit": w} for c, u, w in profiles
            ],
            "aliases": aliases,
        }
        r = owner.post(f"{P}/products", json=body, headers=idem())
        assert r.status_code == 201, r.text
        prods[sku] = r.json()["id"]
    c = owner.post(
        f"{P}/customers", json={"external_ref": "C-100", "name": "Island Grocers"}, headers=idem()
    )
    assert c.status_code == 201, c.text
    return {"p": prods, "cust": c.json()["id"]}


def sheet(world, fills=None, write_ins=None, ref="C-100") -> bytes:
    with get_sessionmaker()() as db:
        cust = db.scalar(
            select(Customer).where(
                Customer.company_id == world.company_id, Customer.external_ref == ref
            )
        )
        wh = db.get(Warehouse, world.wh_a)
        assert cust is not None and wh is not None
        return ocr.order_sheet_pdf(db, cust, wh, "Synthetic Co", fills, write_ins)


def upload(api, world, data, ctype="application/pdf", job_type="SALES_ORDER"):
    q = f"warehouse_id={world.wh_a}&job_type={job_type}&filename=sheet"
    h = {**api._h(idem()), "Content-Type": ctype}
    r = api.c.post(f"{P}/scans/uploads?{q}", content=data, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def drain(world, final=outbox.MAX_ATTEMPTS):
    """Run this company's pending OCR events like the worker does (attempt counted first)."""
    with get_sessionmaker()() as db:
        evs = db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.company_id == world.company_id,
                OutboxEvent.event_type == ocr.EXTRACT_EVENT,
                OutboxEvent.status == "PENDING",
            )
        ).all()
        for ev in evs:
            ev.attempts += 1
            try:
                ex = db.get(OcrExtraction, uuid.UUID(ev.payload["extraction_id"]))
                ocr.run_extraction(db, ex, ev.attempts, final)
                ev.status = "PROCESSED"
            except ocr.ProviderError as e:
                ev.last_error = e.code
                if ev.attempts >= final:
                    ev.status = "FAILED"
        db.commit()
        return len(evs)


def only(api, status="NEEDS_REVIEW"):
    r = api.get(f"{P}/ocr/extractions?status={status}")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1, r.json()
    return api.get(f"{P}/ocr/extractions/{r.json()['items'][0]['id']}").json()


def resolve(api, ex, f, action, **kw):
    body = {"version": ex["version"], "action": action, **kw}
    return api.post(
        f"{P}/ocr/extractions/{ex['id']}/fields/{f['id']}/resolve", json=body, headers=idem()
    )


def counts(world):
    with get_sessionmaker()() as db:
        c = world.company_id
        return tuple(
            db.scalar(text(f"SELECT count(*) FROM {t} WHERE company_id = :c"), {"c": c})  # noqa: S608
            for t in ("invoices", "stock_movements", "stock_reservations")
        )


def test_read_review_accept_creates_draft_only(user, world, setup):
    sales = user("sales")
    before = counts(world)
    job = upload(
        sales,
        world,
        sheet(world, {"CAS": "15", "PLN": "20?"}, ["white yam 12.5 LB", "mango 3"]),
    )
    assert drain(world) == 1
    ex = only(sales)
    assert ex["scan_job_id"] == job["id"] and ex["template"] == "PT-OS-1"
    assert ex["customer_name"] == "Island Grocers" and ex["provider"] == "fake"
    by = {f["raw_text"]: f for f in ex["fields"]}
    cas, pln, yam, mango = by["15"], by["20"], by["white yam 12.5 LB"], by["mango 3"]
    assert (cas["read_sku"], cas["read_unit_code"], cas["read_qty"], cas["status"]) == (
        "CAS",
        "BOX",
        "15.0000",
        "OK",
    )
    assert pln["status"] == "LOW_CONFIDENCE" and pln["confidence"] == "0.620"
    assert (yam["read_sku"], yam["read_qty"], yam["status"]) == ("YAM", "12.5000", "OK")
    assert mango["status"] == "UNMAPPED" and mango["read_product_id"] is None
    assert ex["open_count"] == 4

    # nothing can be accepted until every line is reviewed
    acc = {"version": ex["version"], "customer_id": setup["cust"], "reviewed_all": True}
    r = sales.post(f"{P}/ocr/extractions/{ex['id']}/accept", json=acc, headers=idem())
    assert r.status_code == 409 and code(r) == "FIELDS_UNRESOLVED"
    # an unclear reading cannot be confirmed as read
    r = resolve(sales, ex, pln, "CONFIRM")
    assert r.status_code == 422 and code(r) == "NEEDS_CORRECTION"
    # correcting a clear reading needs a reason
    r = resolve(
        sales, ex, cas, "CORRECT", product_id=setup["p"]["CAS"], unit_code="BOX", qty="16"
    )
    assert r.status_code == 422 and code(r) == "REASON_REQUIRED"
    # whole units only
    r = resolve(
        sales, ex, pln, "CORRECT", product_id=setup["p"]["PLN"], unit_code="PIECE", qty="2.5"
    )
    assert r.status_code == 422 and code(r) == "UNIT_NOT_DIVISIBLE"

    ex = resolve(sales, ex, cas, "CONFIRM").json()
    stale = {**ex, "version": ex["version"] - 1}
    assert code(resolve(sales, stale, yam, "CONFIRM")) == "VERSION_CONFLICT"
    ex = resolve(
        sales, ex, pln, "CORRECT", product_id=setup["p"]["PLN"], unit_code="PIECE", qty="20"
    ).json()
    ex = resolve(sales, ex, yam, "CONFIRM").json()
    ex = resolve(sales, ex, mango, "REMOVE").json()
    assert ex["open_count"] == 0

    acc["version"] = ex["version"]
    acc["note"] = "Phoned in Tuesday"
    r = sales.post(f"{P}/ocr/extractions/{ex['id']}/accept", json=acc, headers=idem())
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "ACCEPTED" and done["sales_order_no"].startswith("SO-")
    so = sales.get(f"{P}/sales-orders/{done['sales_order_id']}").json()
    assert so["status"] == "DRAFT" and so["source"] == "SCAN"
    got = sorted((ln["sku"], ln["unit_code"], ln["ordered_qty"]) for ln in so["lines"])
    assert got == [
        ("CAS", "BOX", "15.0000"),
        ("PLN", "PIECE", "20.0000"),
        ("YAM", "LB", "12.5000"),
    ]
    # OCR never invoices, reserves or moves stock
    assert counts(world) == before

    # history kept: what was read, and every decision
    pln_now = next(f for f in done["fields"] if f["id"] == pln["id"])
    assert pln_now["read_qty"] == "20.0000" and pln_now["resolution"] == "CORRECTED"
    assert [c["action"] for c in pln_now["corrections"]] == ["CORRECT"]
    # accepted is final: can't re-read, can't change
    r = sales.post(f"{P}/ocr/extractions", json={"scan_job_id": job["id"]}, headers=idem())
    assert r.status_code == 409 and code(r) == "EXTRACTION_FINAL"
    assert code(resolve(sales, done, cas, "REMOVE", reason="late change")) == "INVALID_STATE"


def test_db_keeps_reading_and_history_immutable(user, world, setup):
    sales = user("sales")
    upload(sales, world, sheet(world, {"CAS": "4"}))
    drain(world)
    ex = only(sales)
    f = ex["fields"][0]
    ex = resolve(sales, ex, f, "CONFIRM").json()
    with get_sessionmaker()() as db:
        for sql in (
            "UPDATE ocr_fields SET read_qty = 9 WHERE id = :i",
            "DELETE FROM ocr_fields WHERE id = :i",
            "UPDATE ocr_corrections SET reason = 'x' WHERE field_id = :i",
            "DELETE FROM ocr_corrections WHERE field_id = :i",
        ):
            with pytest.raises(DBAPIError):
                db.execute(text(sql), {"i": f["id"]})
            db.rollback()


def test_image_without_text_and_missed_lines(user, world, setup):
    sales = user("sales")
    upload(sales, world, PNG, "image/png")
    drain(world)
    ex = only(sales)
    assert ex["warnings"] == ["NO_TEXT"] and ex["fields"] == []
    body = {
        "version": ex["version"],
        "product_id": setup["p"]["CAS"],
        "unit_code": "BOX",
        "qty": "3",
        "reason": "Photo too dark; read by eye",
    }
    r = sales.post(f"{P}/ocr/extractions/{ex['id']}/lines", json=body, headers=idem())
    assert r.status_code == 200, r.text
    ex = r.json()
    assert ex["fields"][0]["kind"] == "MANUAL" and ex["open_count"] == 0
    acc = {"version": ex["version"], "customer_id": setup["cust"], "reviewed_all": True}
    r = sales.post(f"{P}/ocr/extractions/{ex['id']}/accept", json=acc, headers=idem())
    assert r.status_code == 200, r.text
    a = sales.get(f"{P}/ocr/accuracy").json()
    assert (a["samples"], a["correct"], a["by_status"]) == (1, 0, {"MISSED": 1})
    assert a["meets_target"] is False and a["minimum_samples"] == 50


def test_accuracy_counts_confirmed_lines(user, world, setup):
    sales = user("sales")
    upload(sales, world, sheet(world, {"CAS": "2", "PLN": "6", "YAM": "10?"}))
    drain(world)
    ex = only(sales)
    for f in ex["fields"]:
        if f["status"] == "OK":
            ex = resolve(sales, ex, f, "CONFIRM").json()
        else:
            ex = resolve(
                sales, ex, f, "CORRECT", product_id=f["read_product_id"], unit_code="LB", qty="11"
            ).json()
    acc = {"version": ex["version"], "customer_id": setup["cust"], "reviewed_all": True}
    assert (
        sales.post(f"{P}/ocr/extractions/{ex['id']}/accept", json=acc, headers=idem()).status_code
        == 200
    )
    a = sales.get(f"{P}/ocr/accuracy").json()
    assert (a["samples"], a["correct"], a["accuracy"]) == (3, 2, "0.6667")


def test_reject_and_reread(user, world, setup):
    sales = user("sales")
    job = upload(sales, world, sheet(world, {"CAS": "1"}))
    drain(world)
    ex = only(sales)
    # re-reading supersedes the unfinished run
    r = sales.post(f"{P}/ocr/extractions", json={"scan_job_id": job["id"]}, headers=idem())
    assert r.status_code == 201, r.text
    assert r.json()["run_no"] == 2 and r.json()["status"] == "QUEUED"
    assert sales.get(f"{P}/ocr/extractions/{ex['id']}").json()["status"] == "SUPERSEDED"
    drain(world)
    ex2 = only(sales)
    r = sales.post(
        f"{P}/ocr/extractions/{ex2['id']}/reject",
        json={"version": ex2["version"], "reason": "Customer cancelled by phone"},
        headers=idem(),
    )
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    r = sales.post(f"{P}/ocr/extractions", json={"scan_job_id": job["id"]}, headers=idem())
    assert code(r) == "EXTRACTION_FINAL"


def test_provider_outage_retries_then_fails(user, world, setup, monkeypatch):
    calls = []

    def down(content, mime):
        calls.append(1)
        raise ocr.ProviderError("OCR_UNAVAILABLE", "503 from engine", True)

    monkeypatch.setattr(ocr, "provider", lambda: down)
    sales = user("sales")
    job = upload(sales, world, sheet(world, {"CAS": "1"}))
    drain(world, final=3)
    assert only(sales, "QUEUED")["run_no"] == 1  # transient: still queued, will retry
    drain(world, final=3)
    drain(world, final=3)
    ex = only(sales, "FAILED")
    assert ex["error_code"] == "OCR_UNAVAILABLE" and len(calls) == 3
    # the scan itself is untouched and can be read again once the engine is back
    assert sales.get(f"{P}/scans/jobs/{job['id']}").json()["status"] == "UPLOADED"
    monkeypatch.undo()
    r = sales.post(f"{P}/ocr/extractions", json={"scan_job_id": job["id"]}, headers=idem())
    assert r.status_code == 201 and r.json()["run_no"] == 2
    drain(world)
    assert only(sales)["field_count"] == 1


def test_disabled_engine_means_manual_review(user, world, setup, monkeypatch):
    monkeypatch.setattr(get_settings(), "ocr_provider", "disabled")
    sales = user("sales")
    job = upload(sales, world, sheet(world, {"CAS": "1"}))
    assert sales.get(f"{P}/ocr/extractions").json()["total"] == 0  # not auto-read
    r = sales.post(f"{P}/ocr/extractions", json={"scan_job_id": job["id"]}, headers=idem())
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "NEEDS_REVIEW" and r.json()["warnings"] == ["OCR_DISABLED"]


def test_other_document_types_are_not_auto_read(user, world, setup):  # picking sheets / other
    sales = user("sales")
    upload(sales, world, sheet(world, {"CAS": "1"}), job_type="OTHER")
    assert sales.get(f"{P}/ocr/extractions").json()["total"] == 0


def test_permissions(user, world, setup):
    sales = user("sales")
    upload(sales, world, sheet(world, {"CAS": "1"}))
    drain(world)
    ex = only(sales)
    wh = user("wh_a")
    assert wh.get(f"{P}/ocr/extractions").status_code == 403
    assert wh.get(f"{P}/ocr/extractions/{ex['id']}").status_code == 403
    fin = user("finance")
    acc = {"version": ex["version"], "customer_id": setup["cust"], "reviewed_all": True}
    assert (
        fin.post(f"{P}/ocr/extractions/{ex['id']}/accept", json=acc, headers=idem()).status_code
        == 403
    )
    # another company can't see it
    assert sales.get(f"{P}/ocr/extractions/{uuid.uuid4()}").status_code == 404
    # reviewed_all must be explicitly true
    r = sales.post(
        f"{P}/ocr/extractions/{ex['id']}/accept",
        json={**acc, "reviewed_all": False},
        headers=idem(),
    )
    assert r.status_code == 422


def test_worker_handler_registered():
    from pro_trading.worker import HANDLERS

    assert HANDLERS[ocr.EXTRACT_EVENT] is ocr.handle_extract


def test_order_sheet_pdf(user, world, setup):
    sales = user("sales")
    r = sales.get(f"{P}/ocr/order-sheet?customer_id={setup['cust']}&warehouse_id={world.wh_a}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and b"PT-OS-1" in r.content and b"CAS" in r.content
    # the blank sheet reads back as a template with no lines
    res = ocr.fake_provider(r.content, "application/pdf")
    with get_sessionmaker()() as db:
        m = ocr.map_result(db, world.company_id, "SALES_ORDER", res)
    assert m.template == "PT-OS-1" and m.proposals == [] and "NO_LINES" in m.warnings
    assert m.customer_id is not None


def test_parse_qty_and_rows():
    assert ocr.parse_qty("12") == ocr.Decimal("12")
    assert ocr.parse_qty("1,250.5") == ocr.Decimal("1250.5")
    for bad in ("", "0", "-3", "1/2", "abc", "12.12345", "1e3"):
        assert ocr.parse_qty(bad) is None
