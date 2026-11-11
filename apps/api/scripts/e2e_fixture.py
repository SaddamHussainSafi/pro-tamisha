"""Create a fresh SYNTHETIC company + users for browser E2E and print them as JSON.

Refuses to run outside development/test.
Usage: uv run python scripts/e2e_fixture.py [--sheet PATH] [--paper-sheet PATH]
--sheet also writes a hand-filled synthetic PT-OS-1 order sheet (customer C-1001) for OCR tests.
--paper-sheet loads the office order-sheet catalog and writes a synthetic, sideways, image-only
scan of the office's paper order sheet (TT-PAPER-1) with a few quantities filled in.
--scan-with-reads adds an uploaded order-sheet scan that was read twice (two OCR reads, read
in-process with the fake engine, never a real one), for the Trash cascade test.
"""

import json
import secrets
import sys
import uuid
from pathlib import Path
from typing import Any

from pro_trading import documents, ocr, paper_sheet, scanning
from pro_trading.bootstrap import ensure_default_roles, sync_permissions
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.models import (
    Company,
    Customer,
    Employee,
    EmployeeRole,
    OutboxEvent,
    ScanJob,
    Warehouse,
)
from pro_trading.security import hash_password
from pro_trading.seed import (
    seed_catalog,
    seed_inventory,
    seed_orders,
    seed_pricing,
    seed_vendors,
)


def _scan_with_reads(db: Any, co: Company, wh: Warehouse, owner: Employee) -> dict[str, str]:
    cust = db.query(Customer).filter_by(company_id=co.id, external_ref="C-1001").one()
    pdf = ocr.order_sheet_pdf(db, cust, wh, co.legal_name, fills={"CFZ": "6"})
    doc, _ = scanning.store_document(
        db, co.id, pdf, "application/pdf", "trash-sheet.pdf", owner.id, "MANUAL"
    )
    t = scanning.now()
    job = ScanJob(
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
    db.add(job)
    db.flush()
    documents.file_scan(db, job, doc)
    settings = get_settings()
    engine, settings.ocr_provider = settings.ocr_provider, "fake"
    try:
        for _ in range(2):  # read, then read again: two OCR reads use the scan
            ex = ocr.queue(db, job, owner.id)
            ocr.run_extraction(db, ex, 1, 1)
            db.query(OutboxEvent).filter_by(aggregate_id=str(ex.id)).update({"status": "PROCESSED"})
    finally:
        settings.ocr_provider = engine
    return {"id": str(job.id), "job_no": job.job_no}


def main() -> int:
    if get_settings().is_production_like:
        print("refusing in", get_settings().app_env, file=sys.stderr)
        return 2
    tag = uuid.uuid4().hex[:8]
    password = "E2e-" + secrets.token_urlsafe(12)
    out: dict[str, object] = {"password": password, "users": {}}
    with get_sessionmaker()() as db, db.begin():
        sync_permissions(db)
        co = Company(
            legal_name=f"E2E Synthetic Co {tag}",
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
            ("warehouse", "WAREHOUSE", wh.id),
            ("sales", "SALES_OPERATOR", wh.id),
        ]:
            email = f"{key}.{tag}@example.test"
            emp = Employee(
                company_id=co.id, email=email, display_name=f"E2E {key}", password_hash=pw
            )
            db.add(emp)
            db.flush()
            db.add(EmployeeRole(employee_id=emp.id, role_id=roles[role].id, warehouse_id=scope))
            out["users"][key] = email  # type: ignore[index]
            emps[key] = emp
        seed_catalog(db, co)
        seed_pricing(db, co, emps["owner"])
        seed_inventory(db, co, emps["owner"])
        seed_orders(db, co, emps["owner"])
        seed_vendors(db, co)
        if "--sheet" in sys.argv:
            path = sys.argv[sys.argv.index("--sheet") + 1]
            cust = db.query(Customer).filter_by(company_id=co.id, external_ref="C-1001").one()
            pdf = ocr.order_sheet_pdf(
                db,
                cust,
                wh,
                co.legal_name,
                fills={"CFZ": "12", "PLG": "3?"},
                write_ins=["Mango pulp 4 box"],
            )
            with open(path, "wb") as fh:
                fh.write(pdf)
            out["sheet"] = path
        if "--scan-with-reads" in sys.argv:
            out["scan"] = _scan_with_reads(db, co, wh, emps["owner"])
        if "--paper-sheet" in sys.argv:
            path = sys.argv[sys.argv.index("--paper-sheet") + 1]
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script
            from scripts.load_catalog import CATALOG, load_catalog

            load_catalog(db, co.id)

            def names(cat: str) -> list[str]:
                return [printed for _, _, printed in CATALOG[cat]]

            layout = [
                [("VEGETABLES", names("VEG")), ("PEPPERS", names("PEP"))],
                [("ROOTS", names("ROOT")), ("HERBS", names("HERB")), ("OTHERS", names("OTHER"))],
                [("FRUITS", names("FRUIT"))],
            ]
            fills = {"Okra": "12", "Yellow Yam": "3", "Spice Mango": "2"}
            with open(path, "wb") as fh:
                fh.write(paper_sheet.synthetic_scan(layout, fills, rotate=90))
            out["paper_sheet"] = path
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
