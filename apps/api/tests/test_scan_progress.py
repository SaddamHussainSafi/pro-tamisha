"""Scan progress (migration 0022): the steps, pages and outcomes the animated scanner shows.

Everything asserted here comes from the backend: the scan's status, the reading's status and the
progress events the OCR worker writes while it reads. Synthetic PDFs, fake OCR engine.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from pro_trading import ocr, scan_progress
from pro_trading.db import get_sessionmaker
from pro_trading.models import OcrExtraction, OutboxEvent, ScanEvent, ScanJob
from scripts.scan_ui_fixture import order_sheet

from .conftest import idem
from .test_ocr import drain
from .test_scans import pair_device

P = "/api/v1"
ROWS = [("CAS", "Cassava", "BOX"), ("PLN", "Plantain", "PIECE")]


@pytest.fixture
def owner(make_api, world):
    o = make_api()
    o.login(world.emails["owner"])
    assert o.enroll_and_verify().status_code == 200
    return o


@pytest.fixture
def sales(make_api, world):
    a = make_api()
    assert a.login(world.emails["sales"]).status_code == 200
    return a


@pytest.fixture
def catalog(owner):
    for sku, name, unit in [("CAS", "Cassava", "BOX"), ("PLN", "Plantain", "PIECE")]:
        r = owner.post(
            f"{P}/products",
            json={"sku": sku, "name": name, "stock_unit": unit, "unit_profiles": [], "aliases": []},
            headers=idem(),
        )
        assert r.status_code == 201, r.text
    c = owner.post(
        f"{P}/customers", json={"external_ref": "C-100", "name": "Island Grocers"}, headers=idem()
    )
    assert c.status_code == 201, c.text


def sheet(pages, fills, extra=None):
    return order_sheet(pages, fills, extra, ref="C-100", company=uuid.uuid4().hex, rows=ROWS)


def upload(api, world, data, job_type="SALES_ORDER", expect=201):
    q = f"warehouse_id={world.wh_a}&job_type={job_type}&filename=sheet.pdf"
    h = {**api._h(idem()), "Content-Type": "application/pdf"}
    r = api.c.post(f"{P}/scans/uploads?{q}", content=data, headers=h)
    assert r.status_code == expect, r.text
    return r.json()


def progress(api, job_id):
    r = api.get(f"{P}/scans/jobs/{job_id}/progress")
    assert r.status_code == 200, r.text
    return r.json()


def steps(p):
    return {s["key"]: s["state"] for s in p["steps"]}


def kinds(p):
    return [(e["kind"], e["page"]) for e in p["events"]]


def test_one_page_scan_goes_from_upload_to_review(sales, catalog, world):
    job = upload(
        sales, world, sheet(1, {(1, "CAS"): "12", (1, "PLN"): "3?"}, {1: ["Mango pulp 4 box"]})
    )
    p = progress(sales, job["id"])
    assert p["outcome"] == "WORKING" and p["step"] == "EXTRACTING"
    assert steps(p) == {
        "RETRIEVING": "done",  # a file upload: the browser already had the file
        "UPLOADING": "done",
        "EXTRACTING": "active",
        "MATCHING": "waiting",
        "REVIEW": "waiting",
    }
    assert p["page_count"] == 1
    assert [x["state"] for x in p["pages"]] == ["WAITING"]  # queued: no reader on it yet
    assert p["events"] == [] and p["reading"]["status"] == "QUEUED"
    assert not p["can_review"] and not p["can_retry"]

    assert drain(world) == 1
    p = progress(sales, job["id"])
    assert p["outcome"] == "READY" and p["step"] == "REVIEW"
    assert set(steps(p).values()) == {"done"}
    assert kinds(p) == [
        ("OCR_STARTED", None),
        ("PAGE_READ", 1),
        ("MATCHING", None),
        ("MATCHED", None),
    ]
    page = p["pages"][0]
    assert page["state"] == "READ" and page["words"] > 10
    assert "PT-OS-1" in page["sample"] or "ORDER" in page["sample"]
    assert Decimal(page["confidence"]) > Decimal("0.9")
    # three lines on the page: one clear, one unclear quantity, one unknown product
    assert page["lines"] == 3 and page["to_check"] == 2
    r = p["reading"]
    assert (r["lines"], r["to_check"], r["unknown_products"], r["unclear_quantities"]) == (
        3,
        2,
        1,
        1,
    )
    matched = next(e for e in p["events"] if e["kind"] == "MATCHED")
    assert matched["detail"] == {"lines": 3, "to_check": 2, "unknown_products": 1}
    assert p["can_review"] and p["pages_read"] == 1


def test_ten_page_pdf_reports_every_page_in_order(sales, catalog, world):
    fills = {(1, "CAS"): "4", (4, "CAS"): "20", (7, "PLN"): "6?"}
    job = upload(sales, world, sheet(10, fills, {10: ["Mango pulp 8 box"]}))
    assert job["page_count"] == 10
    p = progress(sales, job["id"])
    assert p["page_count"] == 10 and [x["page"] for x in p["pages"]] == list(range(1, 11))
    drain(world)
    p = progress(sales, job["id"])
    assert [k for k in kinds(p) if k[0] == "PAGE_READ"] == [("PAGE_READ", n) for n in range(1, 11)]
    assert all(e["page_count"] == 10 for e in p["events"] if e["kind"] == "PAGE_READ")
    lines = {x["page"]: x["lines"] for x in p["pages"]}
    assert lines == {1: 1, 2: 0, 3: 0, 4: 1, 5: 0, 6: 0, 7: 1, 8: 0, 9: 0, 10: 1}
    to_check = {x["page"]: x["to_check"] for x in p["pages"] if x["to_check"]}
    assert to_check == {7: 1, 10: 1}
    assert p["reading"]["page_count"] == 10 and p["pages_read"] == 10


def test_pages_show_while_the_reading_is_still_open(sales, catalog, world):
    """The worker holds the reading locked until it finishes; page events still get through."""
    job = upload(sales, world, sheet(3, {(2, "CAS"): "5"}))
    with get_sessionmaker()() as worker:
        ex = worker.scalar(
            select(OcrExtraction)
            .where(OcrExtraction.scan_job_id == uuid.UUID(job["id"]))
            .with_for_update()
        )
        assert ex is not None
        scan_progress.event(ex, "OCR_STARTED", 1, page_count=3)
        tok = ocr.Token("Cassava", 1, *(Decimal("0.1"),) * 4, Decimal("0.97"), False)
        with scan_progress.tracking(ex, 1):
            scan_progress.page_read(1, 3, [tok])
            scan_progress.page_read(1, 3, [tok])  # reported once only
        p = progress(sales, job["id"])  # another connection, while `worker` is still open
        assert [x["state"] for x in p["pages"]] == ["READ", "READING", "WAITING"]
        assert p["outcome"] == "WORKING" and steps(p)["EXTRACTING"] == "active"
        assert p["pages"][0]["words"] == 1 and p["pages"][0]["sample"] == "Cassava"
        worker.rollback()


def test_engine_outage_retries_then_fails_and_read_again_succeeds(
    sales, catalog, world, monkeypatch
):
    job = upload(sales, world, sheet(1, {(1, "CAS"): "2"}))

    def down(content, mime):
        raise ocr.ProviderError("OCR_UNAVAILABLE", "The OCR service could not be reached", True)

    monkeypatch.setattr(ocr, "provider", lambda: down)
    drain(world, final=2)  # attempt 1 of 2: will try again
    p = progress(sales, job["id"])
    assert p["outcome"] == "WORKING"
    assert p["retrying"] == {"code": "OCR_UNAVAILABLE", "attempt": 1, "max_attempts": 2}
    assert p["pages"][0]["state"] == "WAITING"  # nothing is being read until attempt 2 starts
    drain(world, final=2)  # attempt 2 of 2: gives up
    p = progress(sales, job["id"])
    assert p["outcome"] == "FAILED" and p["retrying"] is None
    assert steps(p)["EXTRACTING"] == "failed" and p["step"] == "EXTRACTING"
    assert p["reading"]["error_code"] == "OCR_UNAVAILABLE"
    assert p["pages"][0]["state"] == "FAILED"
    assert [e["kind"] for e in p["events"]] == ["OCR_RETRY", "OCR_STARTED", "OCR_FAILED"]
    assert p["can_retry"]

    monkeypatch.undo()
    r = sales.post(f"{P}/ocr/extractions", json={"scan_job_id": job["id"]}, headers=idem())
    assert r.status_code == 201, r.text
    p = progress(sales, job["id"])
    assert p["outcome"] == "WORKING" and p["reading"]["run_no"] == 2
    assert p["events"] == []  # the new run starts clean
    drain(world)
    p = progress(sales, job["id"])
    assert p["outcome"] == "READY" and p["reading"]["run_no"] == 2


def test_engine_rejects_a_broken_pdf(sales, catalog, world, monkeypatch):
    monkeypatch.setattr(ocr, "provider", lambda: ocr.tesseract_provider)
    job = upload(sales, world, b"%PDF-1.4\n" + b"not a pdf\n" * 30)
    drain(world)
    p = progress(sales, job["id"])
    assert p["outcome"] == "FAILED" and p["reading"]["error_code"] == "UNREADABLE"
    assert [e["kind"] for e in p["events"]] == ["OCR_STARTED", "OCR_FAILED"]
    assert p["page_count"] is None and p["pages"] == []


def _backdate(job_id, minutes=10):
    with get_sessionmaker()() as db, db.begin():
        db.execute(
            text(
                "UPDATE ocr_extractions SET created_at = now() - make_interval(mins => :m) "
                "WHERE scan_job_id = :j"
            ),
            {"m": minutes, "j": job_id},
        )


def test_a_reading_nobody_picks_up_times_out(sales, catalog, world):
    job = upload(sales, world, sheet(1, {(1, "CAS"): "7"}))
    _backdate(job["id"])
    # A worker has its outbox event in hand: it is being read, not stalled.
    with get_sessionmaker()() as busy:
        busy.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.company_id == world.company_id, OutboxEvent.status == "PENDING")
            .with_for_update()
        ).all()
        assert progress(sales, job["id"])["outcome"] == "WORKING"
        busy.rollback()
    p = progress(sales, job["id"])  # the worker is gone: nobody will read it
    assert p["outcome"] == "TIMEOUT" and p["reading"]["error_code"] == "OCR_TIMEOUT"
    assert [e["kind"] for e in p["events"]] == ["OCR_TIMEOUT"]
    assert p["can_retry"]
    again = progress(sales, job["id"])  # idempotent
    assert [e["kind"] for e in again["events"]] == ["OCR_TIMEOUT"]
    # The late worker finds it closed and does nothing.
    assert drain(world) == 1
    assert progress(sales, job["id"])["outcome"] == "TIMEOUT"
    with get_sessionmaker()() as db:
        rows = db.execute(
            text(
                "SELECT detail_redacted FROM audit_events WHERE target_type = 'ocr_extraction' "
                "AND company_id = :c AND success = false"
            ),
            {"c": world.company_id},
        ).all()
    assert [r[0]["reason"] for r in rows] == ["OCR_TIMEOUT"]


def test_recent_progress_is_not_a_stall(sales, catalog, world):
    job = upload(sales, world, sheet(1, {(1, "CAS"): "7"}))
    _backdate(job["id"])
    with get_sessionmaker()() as db:
        ex = db.scalar(
            select(OcrExtraction).where(OcrExtraction.scan_job_id == uuid.UUID(job["id"]))
        )
        scan_progress.event(ex, "OCR_STARTED", 1, page_count=1)
    assert progress(sales, job["id"])["outcome"] == "WORKING"


def test_duplicate_upload_shows_the_scan_already_on_file(sales, catalog, world):
    data = sheet(1, {(1, "CAS"): "1"})
    first = upload(sales, world, data)
    drain(world)
    again = upload(sales, world, data, expect=200)
    assert again["duplicate"] is True and again["id"] == first["id"]
    p = progress(sales, again["id"])
    assert p["outcome"] == "READY" and p["reading"]["run_no"] == 1
    with get_sessionmaker()() as db:
        n = db.scalar(
            select(text("count(*)"))
            .select_from(OcrExtraction)
            .where(OcrExtraction.scan_job_id == uuid.UUID(first["id"]))
        )
    assert n == 1  # no second reading


def test_other_documents_wait_to_be_read(sales, catalog, world):
    job = upload(sales, world, sheet(1, {}), job_type="OTHER")
    p = progress(sales, job["id"])
    assert p["outcome"] == "UNREAD" and p["can_read"] and p["reading"] is None
    assert steps(p) == {
        "RETRIEVING": "done",
        "UPLOADING": "done",
        "EXTRACTING": "skipped",
        "MATCHING": "skipped",
        "REVIEW": "skipped",
    }


def test_bridge_scan_states(owner, sales, world, app):
    dev, client = pair_device(owner, world, app)
    r = sales.post(
        f"{P}/scans/jobs",
        json={"warehouse_id": str(world.wh_a), "job_type": "SALES_ORDER", "device_id": dev["id"]},
        headers=idem(),
    )
    job = r.json()
    p = progress(sales, job["id"])
    assert p["outcome"] == "WORKING" and p["step"] == "RETRIEVING"
    assert steps(p)["RETRIEVING"] == "active" and steps(p)["UPLOADING"] == "waiting"
    client.claim(job["id"], job["capability"])
    assert progress(sales, job["id"])["status"] == "SCANNING"
    client.fail(job["id"], "NO_PAGES", "Feeder empty")
    p = progress(sales, job["id"])
    assert p["outcome"] == "MISSING" and steps(p)["RETRIEVING"] == "failed"
    assert p["error_code"] == "NO_PAGES" and not p["can_retry"]

    # a scan the scanner never finished times out
    r = sales.post(
        f"{P}/scans/jobs",
        json={"warehouse_id": str(world.wh_a), "job_type": "SALES_ORDER", "device_id": dev["id"]},
        headers=idem(),
    )
    job2 = r.json()
    client.claim(job2["id"], job2["capability"])
    with get_sessionmaker()() as db, db.begin():
        db.execute(
            text("UPDATE scan_jobs SET claimed_at = now() - interval '1 hour' WHERE id = :i"),
            {"i": job2["id"]},
        )
    p = progress(sales, job2["id"])
    assert p["outcome"] == "TIMEOUT" and p["error_code"] == "SCAN_TIMEOUT"


def test_words_read_are_for_reviewers_only(sales, catalog, world):
    job = upload(sales, world, sheet(1, {(1, "CAS"): "9"}))
    drain(world)
    with get_sessionmaker()() as db:
        j = db.get(ScanJob, uuid.UUID(job["id"]))
        assert j is not None
        snap = scan_progress.snapshot(db, j, can_review=False)
    assert snap["pages"][0]["sample"] is None and snap["pages"][0]["words"] > 0
    assert all("sample" not in e["detail"] for e in snap["events"])
    assert not snap["can_review"] and not snap["can_read"]


def test_progress_needs_access_to_the_scan(sales, catalog, world, make_api):
    job = upload(sales, world, sheet(1, {}))
    wh = make_api()
    assert wh.login(world.emails["finance"]).status_code == 200
    assert wh.get(f"{P}/scans/jobs/{job['id']}/progress").status_code == 403
    assert sales.get(f"{P}/scans/jobs/{uuid.uuid4()}/progress").status_code == 404
    anon = make_api()
    assert anon.get(f"{P}/scans/jobs/{job['id']}/progress").status_code == 401


def _events(raw: str) -> list[tuple[str, str]]:
    out = []
    for block in raw.split("\n\n"):
        ev = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if ev:
            out.append((ev, data or ""))
    return out


def test_stream_sends_progress_then_ends_when_ready(sales, catalog, world):
    import json

    job = upload(sales, world, sheet(1, {(1, "CAS"): "3"}))
    url = f"{P}/scans/jobs/{job['id']}/progress/stream"
    with sales.c.stream("GET", url, params={"timeout": 1}, headers=sales._h()) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        evs = _events(r.read().decode())
    assert evs and evs[0][0] == "progress" and json.loads(evs[0][1])["outcome"] == "WORKING"
    assert "end" not in [e for e, _ in evs]  # still working: the stream just times out
    drain(world)
    with sales.c.stream("GET", url, params={"timeout": 5}, headers=sales._h()) as r:
        evs = _events(r.read().decode())
    assert [e for e, _ in evs] == ["progress", "end"]
    assert json.loads(evs[0][1])["outcome"] == "READY"


def test_events_go_with_their_scan(sales, catalog, world):
    job = upload(sales, world, sheet(1, {(1, "CAS"): "3"}))
    drain(world)
    jid = uuid.UUID(job["id"])
    with get_sessionmaker()() as db:
        assert (
            db.scalar(
                select(text("count(*)")).select_from(ScanEvent).where(ScanEvent.scan_job_id == jid)
            )
            == 4
        )
        fk = db.execute(
            text(
                "SELECT confdeltype FROM pg_constraint WHERE conrelid = 'scan_events'::regclass "
                "AND contype = 'f' AND confrelid = 'scan_jobs'::regclass"
            )
        ).scalar()
    assert fk == "c"  # ON DELETE CASCADE: purging a scan from the Trash takes its progress too


def test_event_writes_never_wait_long_on_locks(sales, catalog, world):
    """A request holding the scan row (e.g. starting a re-read) must not stall the worker: the
    event is dropped after the lock timeout instead."""
    job = upload(sales, world, sheet(1, {}))
    with get_sessionmaker()() as other, get_sessionmaker()() as db:
        other.execute(text("SELECT 1 FROM scan_jobs WHERE id = :i FOR UPDATE"), {"i": job["id"]})
        ex = db.scalar(
            select(OcrExtraction).where(OcrExtraction.scan_job_id == uuid.UUID(job["id"]))
        )
        t0 = datetime.now(UTC)
        scan_progress.event(ex, "OCR_STARTED", 1)
        assert (datetime.now(UTC) - t0).total_seconds() < 2.5
        other.rollback()
    assert progress(sales, job["id"])["events"] == []
