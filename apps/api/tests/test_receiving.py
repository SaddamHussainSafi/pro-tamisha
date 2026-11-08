"""Module 08 receiving: vendor documents, declared vs counted, GRN posting.

Acceptance (docs/modules/08-receiving.md): supplier declares 25, warehouse accepts 23 + damaged 2
-> only 23 saleable posted; retry cannot double-add. Declared quantities are never stock; vendor
documents are kept unaltered; documents may disagree; landed cost is captured, not allocated.
Synthetic data only.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading.db import get_sessionmaker

from .conftest import build_world, idem

P = "/api/v1"
PDF = b"%PDF-1.4\n% synthetic vendor invoice\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PDF2 = b"%PDF-1.4\n% synthetic airway bill\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


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
def setup(owner, world):
    prods = {}
    for sku, name, unit, expiry in [
        ("CAS-01", "Cassava", "BOX", False),
        ("YAM-01", "Yellow yam", "LB", False),
        ("MLK-01", "Coconut milk", "PIECE", True),
    ]:
        body = {"sku": sku, "name": name, "stock_unit": unit, "tracks_expiry": expiry}
        r = owner.post(f"{P}/products", json=body, headers=idem())
        assert r.status_code == 201, r.text
        prods[sku] = r.json()["id"]
    v = owner.post(
        f"{P}/vendors",
        json={"code": "V-GH1", "name": "Accra Roots Exports", "country_code": "GH"},
        headers=idem(),
    )
    assert v.status_code == 201, v.text
    return {"p": prods, "vendor": v.json()["id"]}


def new_shipment(api, world, setup, **kw):
    body = {"warehouse_id": str(world.wh_a), "vendor_id": setup["vendor"], **kw}
    r = api.post(f"{P}/shipments", json=body, headers=idem())
    assert r.status_code == 201, r.text
    return r.json()


def upload(api, s, data=PDF, kind="INVOICE", ref="INV-77"):
    r = api.c.post(
        f"{P}/shipments/{s['id']}/documents/upload",
        params={"kind": kind, "version": s["version"], "doc_ref": ref, "filename": f"{kind}.pdf"},
        content=data,
        headers={**api._h(idem()), "Content-Type": "application/pdf"},
    )
    return r


def act(api, s, path, body, method="post", key=None):
    fn = getattr(api, method)
    return fn(
        f"{P}/shipments/{s['id']}/{path}",
        json={"version": s["version"], **body},
        headers=key or idem(),
    )


def declare(api, s, doc, product_id, qty, unit="BOX", label="Cassava frozen", **kw):
    return act(
        api,
        s,
        "declared",
        {
            "shipment_document_id": doc,
            "raw_label": label,
            "product_id": product_id,
            "unit_code": unit,
            "declared_qty": qty,
            **kw,
        },
    )


def count(api, s, product_id, accepted, damaged="0", rejected="0", unit="BOX", **kw):
    return act(
        api,
        s,
        "count",
        {
            "product_id": product_id,
            "unit_code": unit,
            "accepted_qty": accepted,
            "damaged_qty": damaged,
            "rejected_qty": rejected,
            **kw,
        },
    )


def stock_rows(company_id):
    with get_sessionmaker()() as db:
        return db.execute(
            text(
                "SELECT l.lot_code, l.status, b.unit_code, b.qty FROM inventory_balances b "
                "JOIN inventory_lots l ON l.id=b.lot_id WHERE l.company_id=:c ORDER BY l.lot_code"
            ),
            {"c": company_id},
        ).all()


def movements(company_id):
    with get_sessionmaker()() as db:
        return db.execute(
            text(
                "SELECT count(*), coalesce(sum(m.delta_qty),0) FROM stock_movements m "
                "JOIN inventory_lots l ON l.id=m.lot_id WHERE l.company_id=:c"
            ),
            {"c": company_id},
        ).one()


def test_declared_25_accepted_23_damaged_2_posts_only_23_saleable(owner, user, world, setup):
    wh = user("wh_a")
    cas = setup["p"]["CAS-01"]
    s = new_shipment(wh, world, setup, vendor_invoice_no="INV-77", awb_no="AWB-123-456")
    assert s["status"] == "OPEN" and s["shipment_no"].startswith("SHP-")
    r = upload(wh, s)
    assert r.status_code == 201, r.text
    s = r.json()
    doc = s["documents"][0]
    assert doc["kind"] == "INVOICE" and doc["byte_size"] == len(PDF) and doc["scan_job_no"]
    # the vendor document is kept byte-for-byte and the warehouse user can open it
    raw = wh.get(f"{P}/scans/documents/{doc['document_id']}/content")
    assert raw.status_code == 200 and raw.content == PDF

    r = declare(wh, s, doc["id"], cas, "25", unit_cost="12.5000")
    assert r.status_code == 201, r.text
    s = r.json()
    assert movements(world.company_id)[0] == 0  # declared is never stock
    r = count(wh, s, cas, "23", damaged="2")
    assert r.status_code == 201, r.text
    s = r.json()
    row = s["comparison"][0]
    assert (row["declared_qty"], row["accepted_qty"], row["damaged_qty"], row["status"]) == (
        "25.0000",
        "23.0000",
        "2.0000",
        "DAMAGED",
    )
    assert movements(world.company_id)[0] == 0  # counted is not stock until posted

    # warehouse can submit but not post
    s = act(wh, s, "submit-count", {}).json()
    assert s["status"] == "COUNTED"
    r = act(wh, s, "post", {"acknowledge_discrepancies": True, "discrepancy_note": "2 crushed"})
    assert r.status_code == 403

    mgr = user("manager_a")
    r = act(mgr, s, "post", {})
    assert r.status_code == 422 and code(r) == "DISCREPANCY_ACK_REQUIRED"
    key = idem()
    body = {"acknowledge_discrepancies": True, "discrepancy_note": "2 boxes crushed in transit"}
    r = act(mgr, s, "post", body, key=key)
    assert r.status_code == 200, r.text
    posted = r.json()
    assert posted["status"] == "POSTED" and posted["grn_no"].startswith("GRN-")
    assert posted["counted"][0]["lot_code"] and posted["counted"][0]["unit_cost"] == "12.5000"

    rows = stock_rows(world.company_id)
    saleable = [r for r in rows if r.status == "SALEABLE"]
    damaged = [r for r in rows if r.status != "SALEABLE"]
    assert [(r.unit_code, str(r.qty)) for r in saleable] == [("BOX", "23.0000")]
    assert [(r.unit_code, str(r.qty)) for r in damaged] == [("BOX", "2.0000")]
    before = movements(world.company_id)
    assert before[0] == 2

    # retry with same key: replay, nothing doubled
    r2 = act(mgr, s, "post", body, key=key)
    assert r2.status_code == 200 and r2.headers.get("Idempotent-Replay") == "true"
    # retry with a new key: refused (already posted)
    r3 = act(mgr, s, "post", body)
    assert r3.status_code == 409
    assert movements(world.company_id) == before

    # audit captured the acknowledgement
    with get_sessionmaker()() as db:
        n = db.scalar(
            text(
                "SELECT count(*) FROM audit_events WHERE company_id=:c AND action='shipment.post' "
                "AND detail_redacted::text LIKE '%crushed%'"
            ),
            {"c": world.company_id},
        )
    assert n == 1


def test_concurrent_posts_add_stock_once(owner, user, world, setup):
    cas = setup["p"]["CAS-01"]
    s = new_shipment(owner, world, setup)
    s = count(owner, s, cas, "10").json()
    s = act(owner, s, "submit-count", {}).json()

    def go(_):
        body = {"acknowledge_discrepancies": True, "discrepancy_note": "no invoice yet"}
        return act(owner, s, "post", body).status_code

    with ThreadPoolExecutor(4) as ex:
        codes = sorted(ex.map(go, range(4)))
    assert codes.count(200) == 1 and set(codes) <= {200, 409}
    assert movements(world.company_id)[1] == 10


def test_documents_conflict_and_status_rules(owner, world, setup):
    cas, yam = setup["p"]["CAS-01"], setup["p"]["YAM-01"]
    s = new_shipment(owner, world, setup)
    s = upload(owner, s).json()
    s = upload(owner, s, PDF2, kind="AWB", ref="AWB-1").json()
    inv_doc, awb_doc = (d["id"] for d in s["documents"])
    s = declare(owner, s, inv_doc, cas, "25").json()
    s = declare(owner, s, awb_doc, cas, "24").json()
    s = declare(owner, s, inv_doc, yam, "100", unit="LB", label="Yam").json()
    s = count(owner, s, cas, "25").json()
    s = count(owner, s, cas, "1", unit="BOX").json()  # second count line for same product sums
    by = {r["sku"]: r for r in s["comparison"]}
    assert by["CAS-01"]["status"] == "DOC_CONFLICT"
    assert by["CAS-01"]["declared_by_document"] == {inv_doc: "25.0000", awb_doc: "24.0000"}
    assert by["YAM-01"]["status"] == "NOT_RECEIVED" and s["discrepancy_count"] == 2

    # withdraw the mistaken AWB line -> now an overage of 1
    awb_line = next(d for d in s["declared"] if d["shipment_document_id"] == awb_doc)
    r = act(owner, s, f"declared/{awb_line['id']}/withdraw", {"reason": "typed wrong"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert {r["sku"]: r["status"] for r in s["comparison"]}["CAS-01"] == "OVER"
    # same document twice is refused; stale version refused
    assert code(upload(owner, s)) == "ALREADY_ATTACHED"
    stale = {**s, "version": s["version"] - 1}
    assert code(count(owner, stale, cas, "1")) == "VERSION_CONFLICT"

    # submit freezes counting; reopen thaws it
    s = act(owner, s, "submit-count", {}).json()
    assert code(count(owner, s, cas, "1")) == "INVALID_STATE"
    s = act(owner, s, "reopen", {"reason": "recount yams"}).json()
    assert s["status"] == "OPEN"
    # remove a miscount line before posting
    extra = s["counted"][1]
    r = act(owner, s, f"count/{extra['id']}/remove", {})
    assert r.status_code == 200 and len(r.json()["counted"]) == 1
    s = r.json()
    # edit a line
    first = s["counted"][0]
    r = act(
        owner,
        s,
        f"count/{first['id']}",
        {
            "line_version": first["version"],
            "product_id": cas,
            "unit_code": "BOX",
            "accepted_qty": "24",
            "damaged_qty": "0",
            "rejected_qty": "1",
        },
        method="put",
    )
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["comparison"][0]["status"] in ("DAMAGED", "SHORT")
    # cancel; then nothing else allowed
    s = act(owner, s, "cancel", {"reason": "vendor recalled"}).json()
    assert s["status"] == "CANCELLED"
    assert code(act(owner, s, "submit-count", {})) == "INVALID_STATE"
    assert movements(world.company_id)[0] == 0


def test_validation_rules(owner, world, setup):
    cas, milk = setup["p"]["CAS-01"], setup["p"]["MLK-01"]
    s = new_shipment(owner, world, setup)
    assert code(act(owner, s, "submit-count", {})) == "NOTHING_COUNTED"
    assert code(count(owner, s, cas, "0")) == "EMPTY_COUNT"
    assert code(count(owner, s, milk, "5", unit="PIECE")) == "EXPIRY_REQUIRED"
    r = count(owner, s, cas, "1.5")
    assert r.status_code == 422  # BOX is a whole unit
    r = count(owner, s, cas, "2.0", unit="KG")
    assert r.status_code == 422
    r = count(owner, s, milk, "5", unit="PIECE", expires_at="2027-01-31", batch_ref="B-9")
    assert r.status_code == 201, r.text
    assert r.json()["comparison"][0]["status"] == "NOT_DECLARED"
    # floats in JSON are rejected; strings only
    r = owner.post(
        f"{P}/shipments/{s['id']}/count",
        json={
            "version": r.json()["version"],
            "product_id": cas,
            "unit_code": "BOX",
            "accepted_qty": 2.5,
        },
        headers=idem(),
    )
    assert r.status_code == 422
    # vendor inactive -> new shipments refused
    v = owner.get(f"{P}/vendors").json()[0]
    r = owner.patch(
        f"{P}/vendors/{v['id']}",
        json={
            "name": v["name"],
            "country_code": "GH",
            "currency": "USD",
            "active": False,
            "version": v["version"],
        },
        headers=idem(),
    )
    assert r.status_code == 200, r.text
    r = owner.post(
        f"{P}/shipments",
        json={"warehouse_id": str(world.wh_a), "vendor_id": v["id"]},
        headers=idem(),
    )
    assert code(r) == "VENDOR_INACTIVE"


def test_vendor_label_memory(owner, world, setup):
    cas = setup["p"]["CAS-01"]
    s = new_shipment(owner, world, setup)
    r = act(
        owner,
        s,
        "declared",
        {"raw_label": "MANIOC 40LB CTN", "unit_code": "BOX", "declared_qty": "5"},
    )
    assert code(r) == "UNMAPPED_LABEL"
    s = declare(owner, s, None, cas, "5", label="MANIOC 40LB CTN", remember_alias=True).json()
    r = act(
        owner,
        s,
        "declared",
        {"raw_label": "manioc 40lb ctn", "unit_code": "BOX", "declared_qty": "3"},
    )
    assert r.status_code == 201, r.text
    assert [d["sku"] for d in r.json()["declared"]] == ["CAS-01", "CAS-01"]
    # exact SKU also maps without memory
    s = r.json()
    r = act(owner, s, "declared", {"raw_label": "cas-01", "unit_code": "BOX", "declared_qty": "1"})
    assert r.status_code == 201


def test_landed_costs_captured_not_allocated(owner, world, setup):
    cas = setup["p"]["CAS-01"]
    s = new_shipment(owner, world, setup)
    s = upload(owner, s).json()
    s = act(
        owner,
        s,
        "costs",
        {
            "kind": "FREIGHT",
            "amount": "350.00",
            "currency": "USD",
            "shipment_document_id": s["documents"][0]["id"],
        },
    ).json()
    s = act(owner, s, "costs", {"kind": "DUTY", "amount": "80.25", "currency": "USD"}).json()
    assert s["cost_totals"] == {"USD": "430.25"}
    s = count(owner, s, cas, "10").json()
    s = act(owner, s, "submit-count", {}).json()
    s = act(
        owner, s, "post", {"acknowledge_discrepancies": True, "discrepancy_note": "no paperwork"}
    ).json()
    # late bill after posting is allowed; withdrawing one keeps the record
    late = act(owner, s, "costs", {"kind": "BROKERAGE", "amount": "40.00", "currency": "USD"})
    assert late.status_code == 201, late.text
    s2 = late.json()
    assert s2["version"] == s["version"] and s2["cost_totals"] == {"USD": "470.25"}
    duty = next(c for c in s2["costs"] if c["kind"] == "DUTY")
    s3 = act(owner, s2, f"costs/{duty['id']}/withdraw", {"reason": "duplicate entry"}).json()
    assert s3["cost_totals"] == {"USD": "390.00"}
    assert any(c["removed_reason"] == "duplicate entry" for c in s3["costs"])
    with get_sessionmaker()() as db:
        lc = db.scalar(
            text("SELECT landed_unit_cost FROM inventory_lots WHERE company_id=:c LIMIT 1"),
            {"c": world.company_id},
        )
    assert lc is None  # no speculative allocation


def test_permissions_and_scope(owner, user, make_api, world, setup):
    s = new_shipment(owner, world, setup)
    sales = user("sales")
    assert sales.get(f"{P}/shipments").status_code == 403
    assert sales.get(f"{P}/shipments/{s['id']}").status_code == 403
    r = sales.post(
        f"{P}/shipments",
        json={"warehouse_id": str(world.wh_a), "vendor_id": setup["vendor"]},
        headers=idem(),
    )
    assert r.status_code == 403
    wh = user("wh_a")
    r = wh.post(
        f"{P}/shipments",
        json={"warehouse_id": str(world.wh_b), "vendor_id": setup["vendor"]},
        headers=idem(),
    )
    assert r.status_code == 403
    assert wh.get(f"{P}/shipments").json()["total"] == 1
    # wh_a cannot cancel (grn.approve) nor create vendors
    assert act(wh, s, "cancel", {"reason": "nope"}).status_code == 403
    r = wh.post(f"{P}/vendors", json={"code": "X", "name": "X"}, headers=idem())
    assert r.status_code == 403
    # another company cannot see it
    other = build_world()
    o = make_api()
    assert o.login(other.emails["owner"]).status_code == 200
    assert o.enroll_and_verify().status_code == 200
    assert o.get(f"{P}/shipments/{s['id']}").status_code == 404
    assert o.get(f"{P}/shipments").json()["total"] == 0


def test_db_guards(owner, world, setup):
    cas = setup["p"]["CAS-01"]
    s = new_shipment(owner, world, setup)
    s = upload(owner, s).json()
    s = declare(owner, s, s["documents"][0]["id"], cas, "5").json()
    s = count(owner, s, cas, "5").json()
    s = act(owner, s, "submit-count", {}).json()
    s = act(owner, s, "post", {}).json()
    assert s["status"] == "POSTED"
    sid = s["id"]
    bad = [
        "UPDATE shipments SET status='OPEN' WHERE id=:s",
        "DELETE FROM shipments WHERE id=:s",
        "UPDATE grn_lines SET accepted_qty=99 WHERE shipment_id=:s",
        "DELETE FROM grn_lines WHERE shipment_id=:s",
        "UPDATE shipment_declared_lines SET declared_qty=99 WHERE shipment_id=:s",
        "DELETE FROM shipment_declared_lines WHERE shipment_id=:s",
        "DELETE FROM shipment_documents WHERE shipment_id=:s",
        "UPDATE shipment_documents SET kind='OTHER' WHERE shipment_id=:s",
    ]
    for sql in bad:
        with get_sessionmaker()() as db, pytest.raises(DBAPIError):
            db.execute(text(sql), {"s": sid})
            db.commit()


def test_unknown_ids(owner, world, setup):
    r = owner.get(f"{P}/shipments/{uuid.uuid4()}")
    assert r.status_code == 404
    s = new_shipment(owner, world, setup)
    r = act(owner, s, "documents", {"scan_job_id": str(uuid.uuid4()), "kind": "AWB"})
    assert code(r) == "INVALID_SCAN"


def test_vendor_documents_are_filed_in_archive(owner, world, setup):
    s = new_shipment(owner, world, setup)
    up = upload(owner, s)
    assert up.status_code in (200, 201), up.text
    r = owner.get(f"{P}/documents", params={"kind": "VENDOR_DOC", "shipment_id": s["id"]})
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["folder"].startswith("Vendors/V-GH1 Accra Roots Exports/")
    assert items[0]["vendor_name"] == "Accra Roots Exports"
    assert items[0]["filename"].startswith(s["shipment_no"])
