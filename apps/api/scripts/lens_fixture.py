"""SYNTHETIC reading for the scan-review magnifier E2E (e2e/scan-lens.spec.ts).

Refuses to run outside development/test. Prints JSON (user, password, reading id).

A fresh synthetic company, a one-page synthetic order sheet with two handwritten quantities,
read by the built-in "fake" reader (OCR_PROVIDER=fake is forced for this script, so no outside
OCR service is called). The reading is left waiting for review, with each line's position on
the page, which is what the magnifier shows.
"""

import json
import os
import secrets
import sys
import uuid

from sqlalchemy import update

os.environ["OCR_PROVIDER"] = "fake"

from pro_trading import ocr, scanning  # noqa: E402
from pro_trading.bootstrap import ensure_default_roles, sync_permissions  # noqa: E402
from pro_trading.config import get_settings  # noqa: E402
from pro_trading.db import get_sessionmaker  # noqa: E402
from pro_trading.models import (  # noqa: E402
    Company,
    Employee,
    EmployeeRole,
    OutboxEvent,
    ScanJob,
    Warehouse,
)
from pro_trading.security import hash_password  # noqa: E402
from pro_trading.seed import seed_catalog, seed_pricing  # noqa: E402

try:
    from scripts.scan_ui_fixture import order_sheet
except ModuleNotFoundError:  # run as a script: this folder is on the path, not its parent
    from scan_ui_fixture import order_sheet  # type: ignore[no-redef]


def main() -> int:
    if get_settings().is_production_like:
        print("refusing in", get_settings().app_env, file=sys.stderr)
        return 2
    tag = uuid.uuid4().hex[:8]
    password = "E2e-" + secrets.token_urlsafe(12)
    with get_sessionmaker()() as db, db.begin():
        sync_permissions(db)
        co = Company(legal_name=f"Lens Synthetic {tag}", timezone="UTC")
        db.add(co)
        db.flush()
        roles = ensure_default_roles(db, co.id)
        wh = Warehouse(company_id=co.id, code="MAIN", name="Main Warehouse")
        db.add(wh)
        db.flush()
        emp = Employee(
            company_id=co.id,
            email=f"owner.{tag}@example.test",
            display_name="Lens owner",
            password_hash=hash_password(password),
        )
        db.add(emp)
        db.flush()
        db.add(EmployeeRole(employee_id=emp.id, role_id=roles["OWNER_ADMIN"].id, warehouse_id=None))
        seed_catalog(db, co)
        seed_pricing(db, co, emp)
        pdf = order_sheet(1, {(1, "CFZ"): "12", (1, "PLG"): "3?"}, company=co.legal_name)
        doc, _ = scanning.store_document(
            db, co.id, pdf, "application/pdf", "sheet.pdf", emp.id, "MANUAL"
        )
        t = scanning.now()
        job = ScanJob(
            company_id=co.id,
            warehouse_id=wh.id,
            job_no=scanning.next_job_no(db, co.id),
            job_type="SALES_ORDER",
            channel="MANUAL",
            status="UPLOADED",
            requested_by=emp.id,
            document_id=doc.id,
            claimed_at=t,
            completed_at=t,
        )
        db.add(job)
        db.flush()
        ex = ocr.queue(db, job, emp.id)
        ex_id = ex.id
        # Read here, not by the running worker: its queued job is marked done.
        db.flush()
        db.execute(
            update(OutboxEvent)
            .where(OutboxEvent.aggregate_id == str(ex_id), OutboxEvent.status == "PENDING")
            .values(status="PROCESSED")
        )
    with get_sessionmaker()() as db:
        ex = db.get(ocr.OcrExtraction, ex_id)
        assert ex is not None
        ocr.run_extraction(db, ex, 1, 5)
        db.commit()
        status = ex.status
    print(json.dumps({"user": f"owner.{tag}@example.test", "password": password,
                      "reading": str(ex_id), "status": status}))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
