"""SYNTHETIC scans for the animated scanner (Scan Center): browser E2E and screenshots.

Refuses to run outside development/test. Prints JSON (users, password, file paths, scan ids).

Usage: uv run python scripts/scan_ui_fixture.py --dir DIR [--scenarios]

--dir writes into DIR:
  one-page.pdf   a filled PT-OS-1 order sheet, 1 page (customer C-1001)
  ten-page.pdf   the same form over 10 pages, quantities on pages 1, 4, 7 and 10 (one smudged,
                 one a product the catalog doesn't sell)
  broken.pdf     starts like a PDF but is not one: the OCR engine really fails on it
--scenarios also files scans whose state is set directly, standing in for what cannot be made to
happen on demand against the shared dev worker:
  outage    a reading the engine gave up on after retries (OCR_UNAVAILABLE, 5 attempts)
  stalled   a reading queued 10 minutes ago that no worker picked up (opens as OCR_TIMEOUT)
  missing   a bridge scan whose feeder was empty (NO_PAGES)
  waiting   a bridge scan the scanner has not picked up yet (REQUESTED)
  scanning  a bridge scan the scanner is feeding (SCANNING)
  matching  a 3-page reading whose pages are read and products are being matched
  retrying  a reading whose first attempt hit an engine outage and will be tried again
The first two have a real original, so "Read again" on them runs a real reading.
"""

import json
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pro_trading import scanning
from pro_trading.bootstrap import ensure_default_roles, sync_permissions
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.invoice_pdf import MARGIN, PAGE_H, PAGE_W, _assemble, _Page
from pro_trading.models import (
    Company,
    Employee,
    EmployeeRole,
    OcrExtraction,
    ScanDevice,
    ScanEvent,
    ScanJob,
    Warehouse,
)
from pro_trading.security import hash_password
from pro_trading.seed import seed_catalog, seed_pricing

Row = tuple[str, str, str]  # sku, name, unit
ROWS: list[Row] = [  # the synthetic catalog (seed.py)
    ("CFZ", "Cassava frozen", "LB"),
    ("PLG", "Plantain green", "LB"),
    ("RJS", "Jasmine rice", "KG"),
]


def order_sheet(
    pages: int,
    fills: dict[tuple[int, str], str],
    write_ins: dict[int, list[str]] | None = None,
    *,
    ref: str = "C-1001",
    company: str = "Synthetic Co",
    rows: list[Row] = ROWS,
) -> bytes:
    """A synthetic PT-OS-1 order sheet over `pages` pages, laid out like ocr.order_sheet_pdf.
    `fills[(page, sku)]` is the synthetic handwritten quantity; a trailing "?" reads unclear.
    `write_ins[page]` are handwritten "Other items" lines (e.g. a product the catalog lacks)."""
    out: list[_Page] = []
    for n in range(1, pages + 1):
        pg = _Page()
        top = PAGE_H - MARGIN
        pg.text(MARGIN, top, company, 14, bold=True)
        pg.text(MARGIN, top - 18, "ORDER SHEET", 11, bold=True)
        pg.text(PAGE_W - MARGIN - 60, top, "PT-OS-1", 10, bold=True)
        pg.text(MARGIN, top - 40, "Customer: Synthetic customer", 10)
        pg.text(MARGIN, top - 54, "Ref:", 10)
        pg.text(MARGIN + 26, top - 54, ref, 10)
        pg.text(PAGE_W - MARGIN - 60, top - 18, f"Page {n}/{pages}", 8)
        y = top - 84
        pg.text(MARGIN, y, "SKU", 8, bold=True)
        pg.text(MARGIN + 90, y, "Product", 8, bold=True)
        pg.text(400, y, "Unit", 8, bold=True)
        pg.text(460, y, "Quantity", 8, bold=True)
        pg.line(MARGIN, y - 4, PAGE_W - MARGIN, y - 4)
        for sku, name, unit in rows:
            y -= 20
            pg.text(MARGIN, y, sku, 9)
            pg.text(MARGIN + 90, y, name, 9)
            pg.text(400, y, unit, 9)
            pg.line(455, y - 4, PAGE_W - MARGIN, y - 4)
            if (n, sku) in fills:
                pg.hand(470, y - 1, fills[(n, sku)], 9)
        extra = (write_ins or {}).get(n, [])
        if extra:
            y -= 30
            pg.text(MARGIN, y, "Other items (write product and quantity):", 9, bold=True)
            for line in extra:
                y -= 22
                pg.line(MARGIN, y - 4, PAGE_W - MARGIN, y - 4)
                pg.hand(MARGIN + 4, y - 1, line, 9)
        out.append(pg)
    return _assemble(out, f"Order sheet {ref}")


ONE_PAGE_FILLS = {(1, "CFZ"): "12", (1, "PLG"): "3?"}
ONE_PAGE_EXTRA = {1: ["Mango pulp 4 box"]}
TEN_PAGE_FILLS = {(1, "CFZ"): "4", (4, "RJS"): "20", (7, "PLG"): "6?"}
TEN_PAGE_EXTRA = {10: ["Mango pulp 8 box"]}
BROKEN_PDF = b"%PDF-1.4\n" + b"this is not a real PDF body\n" * 40


def _scenarios(db, co: Company, wh: Warehouse, owner: Employee) -> dict[str, str]:  # type: ignore[no-untyped-def]
    t = datetime.now(UTC)
    out: dict[str, str] = {}

    def received(name: str, data: bytes) -> ScanJob:
        doc, _ = scanning.store_document(
            db, co.id, data, "application/pdf", name, owner.id, "MANUAL"
        )
        j = ScanJob(
            company_id=co.id,
            warehouse_id=wh.id,
            job_no=scanning.next_job_no(db, co.id),
            job_type="SALES_ORDER",
            channel="MANUAL",
            status="UPLOADED",
            requested_by=owner.id,
            document_id=doc.id,
            claimed_at=t,
            completed_at=t,
        )
        db.add(j)
        db.flush()
        return j

    # The engine gave up after retries (as when Document AI is down for half an hour).
    j = received("outage.pdf", order_sheet(1, {(1, "RJS"): "9"}, company=f"{co.legal_name} A"))
    ex = OcrExtraction(
        company_id=co.id,
        warehouse_id=wh.id,
        scan_job_id=j.id,
        document_id=j.document_id,
        run_no=1,
        status="FAILED",
        provider=get_settings().ocr_provider,
        error_code="OCR_UNAVAILABLE",
        error_detail="The OCR service could not be reached",
        requested_by=owner.id,
        completed_at=t,
    )
    db.add(ex)
    db.flush()
    ids = {"company_id": co.id, "scan_job_id": j.id, "extraction_id": ex.id}
    for a in range(1, 6):
        db.add(ScanEvent(**ids, kind="OCR_STARTED", attempt=a, page_count=1, detail={"max": 5}))
        kind = "OCR_RETRY" if a < 5 else "OCR_FAILED"
        db.add(ScanEvent(**ids, kind=kind, attempt=a, detail={"code": "OCR_UNAVAILABLE", "max": 5}))
    out["outage"] = str(j.id)

    # Queued 10 minutes ago and never picked up (no outbox event: as if the worker were down).
    j = received("stalled.pdf", order_sheet(1, {(1, "CFZ"): "5"}, company=f"{co.legal_name} B"))
    db.add(
        OcrExtraction(
            company_id=co.id,
            warehouse_id=wh.id,
            scan_job_id=j.id,
            document_id=j.document_id,
            run_no=1,
            status="QUEUED",
            provider=get_settings().ocr_provider,
            requested_by=owner.id,
            created_at=t - timedelta(minutes=10),
        )
    )
    out["stalled"] = str(j.id)

    # A bridge scan that came back with no pages (empty feeder).
    dev = ScanDevice(
        company_id=co.id,
        warehouse_id=wh.id,
        name="Front desk scanner",
        status="ACTIVE",
        created_by=owner.id,
        secret_hash=scanning.secret_hash(secrets.token_urlsafe(16)),
        approved_by=owner.id,
        approved_at=t,
    )
    db.add(dev)
    db.flush()
    j = ScanJob(
        company_id=co.id,
        warehouse_id=wh.id,
        job_no=scanning.next_job_no(db, co.id),
        job_type="SALES_ORDER",
        channel="BRIDGE",
        status="FAILED",
        device_id=dev.id,
        requested_by=owner.id,
        claimed_at=t - timedelta(seconds=20),
        completed_at=t,
        error_code="NO_PAGES",
        error_detail="The feeder was empty",
    )
    db.add(j)
    db.flush()
    out["missing"] = str(j.id)

    # In-between states that pass too quickly to photograph on a live worker. No outbox event,
    # so no worker touches them (they time out after OCR_STALL_SECONDS like any stalled reading).
    def bridge(status: str) -> ScanJob:
        j = ScanJob(
            company_id=co.id,
            warehouse_id=wh.id,
            job_no=scanning.next_job_no(db, co.id),
            job_type="SALES_ORDER",
            channel="BRIDGE",
            status=status,
            device_id=dev.id if status == "REQUESTED" else dev2.id,
            requested_by=owner.id,
            capability_expires_at=t + timedelta(minutes=10),
            claimed_at=t if status == "SCANNING" else None,
        )
        db.add(j)
        db.flush()
        return j

    dev2 = ScanDevice(
        company_id=co.id,
        warehouse_id=wh.id,
        name="Back office scanner",
        status="ACTIVE",
        created_by=owner.id,
        secret_hash=scanning.secret_hash(secrets.token_urlsafe(16)),
        approved_by=owner.id,
        approved_at=t,
    )
    db.add(dev2)
    db.flush()
    out["waiting"] = str(bridge("REQUESTED").id)
    out["scanning"] = str(bridge("SCANNING").id)

    def reading(
        name: str, pages: int, kinds: list[tuple[str, int | None, dict[str, object]]]
    ) -> str:
        j = received(name, order_sheet(pages, {(2, "CFZ"): "3"}, company=f"{co.legal_name} {name}"))
        ex = OcrExtraction(
            company_id=co.id,
            warehouse_id=wh.id,
            scan_job_id=j.id,
            document_id=j.document_id,
            run_no=1,
            status="QUEUED",
            provider=get_settings().ocr_provider,
            requested_by=owner.id,
        )
        db.add(ex)
        db.flush()
        for kind, page, detail in kinds:
            db.add(ScanEvent(company_id=co.id, scan_job_id=j.id, extraction_id=ex.id, kind=kind,
                             attempt=1, page=page, page_count=pages, detail=detail))  # fmt: skip
        return str(j.id)

    words = {"words": 28, "confidence": "0.960", "sample": "Synthetic Co PT-OS-1 ORDER SHEET"}
    out["matching"] = reading(
        "matching.pdf",
        3,
        [("OCR_STARTED", None, {"max": 5})]
        + [("PAGE_READ", n, words) for n in (1, 2, 3)]
        + [("MATCHING", None, {})],
    )
    out["retrying"] = reading(
        "retrying.pdf",
        2,
        [
            ("OCR_STARTED", None, {"max": 5}),
            ("OCR_RETRY", None, {"code": "OCR_UNAVAILABLE", "max": 5}),
        ],
    )
    return out


def main() -> int:
    if get_settings().is_production_like:
        print("refusing in", get_settings().app_env, file=sys.stderr)
        return 2
    if "--dir" not in sys.argv:
        print(__doc__, file=sys.stderr)
        return 2
    folder = Path(sys.argv[sys.argv.index("--dir") + 1])
    folder.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:8]
    password = "E2e-" + secrets.token_urlsafe(12)
    out: dict[str, object] = {"password": password, "users": {}, "files": {}}
    with get_sessionmaker()() as db, db.begin():
        sync_permissions(db)
        co = Company(
            legal_name=f"Scan UI Synthetic {tag}",
            timezone="UTC",
            business_no="000111222",
            default_salesperson="Sam Synthetic",
        )
        db.add(co)
        db.flush()
        roles = ensure_default_roles(db, co.id)
        wh = Warehouse(company_id=co.id, code="MAIN", name="Main Warehouse")
        db.add(wh)
        db.flush()
        pw = hash_password(password)
        emps: dict[str, Employee] = {}
        for key, role, scope in [
            ("owner", "OWNER_ADMIN", None),
            ("sales", "SALES_OPERATOR", wh.id),
        ]:
            emp = Employee(
                company_id=co.id,
                email=f"{key}.{tag}@example.test",
                display_name=f"Scan UI {key}",
                password_hash=pw,
            )
            db.add(emp)
            db.flush()
            db.add(EmployeeRole(employee_id=emp.id, role_id=roles[role].id, warehouse_id=scope))
            out["users"][key] = emp.email  # type: ignore[index]
            emps[key] = emp
        seed_catalog(db, co)
        seed_pricing(db, co, emps["owner"])  # customers, incl. C-1001
        # Each run gets its own bytes (the company name is printed), so no upload is a
        # duplicate of an earlier run's.
        files = {
            "one_page": (
                "one-page.pdf",
                order_sheet(1, ONE_PAGE_FILLS, ONE_PAGE_EXTRA, company=co.legal_name),
            ),
            "ten_page": (
                "ten-page.pdf",
                order_sheet(10, TEN_PAGE_FILLS, TEN_PAGE_EXTRA, company=co.legal_name),
            ),
            "broken": ("broken.pdf", BROKEN_PDF + tag.encode()),
        }
        for key, (name, data) in files.items():
            (folder / name).write_bytes(data)
            out["files"][key] = str(folder / name)  # type: ignore[index]
        if "--scenarios" in sys.argv:
            out["scans"] = _scenarios(db, co, wh, emps["owner"])
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
