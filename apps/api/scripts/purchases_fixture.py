"""Synthetic company for the purchases e2e specs (apps/web/e2e/purchases-*.spec.ts,
receiving.spec.ts).

A fresh company with catalog, prices, suppliers and orders but NO stock at all, so every line
is short until something is bought or quick-received. Also:
- two supplier invoices already scanned and read (with the in-process fake reader, never a real
  one), waiting for a person to review them: `local` (Accra Roots: 25 box cassava, 10 box
  plantain) and `match` (Kingston Produce: 5 box cassava, 4 box rice);
- an open packing sheet for 5 boxes of cassava (`packing`), and the seeded verified sheet for
  7 boxes waiting for its invoice (`verified`).
Prints JSON. Refuses to run in staging/production. Synthetic data only.

    PYTHONPATH=src uv run python scripts/purchases_fixture.py
"""

import json
import secrets
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from pro_trading import documents, ocr, scanning
from pro_trading.bootstrap import ensure_default_roles, sync_permissions
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.invoice_pdf import PAGE_H, _assemble, _Page
from pro_trading.models import (
    Company,
    Customer,
    Employee,
    EmployeeRole,
    OutboxEvent,
    PickingAttempt,
    PickingLine,
    Product,
    SalesOrder,
    SalesOrderLine,
    ScanJob,
    Warehouse,
)
from pro_trading.security import hash_password
from pro_trading.seed import seed_catalog, seed_orders, seed_pricing, seed_vendors

Line = tuple[str, str, str, str, str]  # description, qty, unit, price, amount


def invoice_pdf(supplier: str, no: str, lines: list[Line], total: str) -> bytes:
    """A typed supplier invoice (synthetic) the fake reader can read."""
    pg = _Page()
    top = PAGE_H - 60
    pg.text(50, top, supplier, 16, bold=True)
    pg.text(50, top - 20, "12 Harbour Road (synthetic)")
    pg.text(50, top - 60, "INVOICE", 14, bold=True)
    pg.text(50, top - 80, f"Invoice No: {no}", 11)
    pg.text(50, top - 96, "Invoice Date: 2026-11-10", 11)
    pg.text(50, top - 112, "Currency: USD", 11)
    y = top - 170
    pg.text(50, y, "Description", 11, bold=True)
    pg.text(300, y, "Qty", 11, bold=True)
    pg.text(340, y, "Unit", 11, bold=True)
    pg.right(470, y, "Price", 11, bold=True)
    pg.right(550, y, "Amount", 11, bold=True)
    for desc, q, u, pr, amt in lines:
        y -= 20
        pg.text(50, y, desc, 11)
        pg.text(300, y, q, 11)
        pg.text(340, y, u, 11)
        pg.right(470, y, pr, 11)
        pg.right(550, y, amt, 11)
    y -= 40
    pg.text(340, y, "Subtotal", 11)
    pg.right(550, y, total, 11)
    pg.text(340, y - 18, "Tax", 11)
    pg.right(550, y - 18, "0.00", 11)
    pg.text(340, y - 36, "Total", 11, bold=True)
    pg.right(550, y - 36, total, 11, bold=True)
    return _assemble([pg], f"Invoice {no}")


def read_invoice(
    db: Any, co: Company, wh: Warehouse, owner: Employee, pdf: bytes, name: str
) -> str:
    """Store the file as a supplier-document scan and read it (fake reader, in-process)."""
    doc, _ = scanning.store_document(db, co.id, pdf, "application/pdf", name, owner.id, "MANUAL")
    t = scanning.now()
    job = ScanJob(
        company_id=co.id,
        warehouse_id=wh.id,
        job_no=scanning.next_job_no(db, co.id),
        job_type="VENDOR_DOCUMENT",
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
        ex = ocr.queue(db, job, owner.id)
        ocr.run_extraction(db, ex, 1, 1)
        db.query(OutboxEvent).filter_by(aggregate_id=str(ex.id)).update({"status": "PROCESSED"})
    finally:
        settings.ocr_provider = engine
    return str(ex.id)


def open_sheet(db: Any, co: Company, wh: Warehouse, owner: Employee) -> PickingAttempt:
    """A confirmed order for 5 boxes of cassava with its packing sheet printed, nothing packed."""
    now = datetime.now(UTC)
    cust = db.scalar(
        select(Customer).where(Customer.company_id == co.id, Customer.external_ref == "C-1001")
    )
    prod = db.scalar(select(Product).where(Product.company_id == co.id, Product.sku == "CFZ"))
    o = SalesOrder(
        company_id=co.id,
        order_no=f"SO-{now:%Y%m%d}-0099",
        customer_id=cust.id,
        warehouse_id=wh.id,
        status="PICKING",
        source_text="5 box cassava",
        source_snapshot=[],
        note="synthetic purchases fixture",
        created_by=owner.id,
        confirmed_by=owner.id,
        confirmed_at=now,
        version=3,
    )
    db.add(o)
    db.flush()
    a = PickingAttempt(
        company_id=co.id,
        order_id=o.id,
        warehouse_id=wh.id,
        attempt_no=1,
        pick_code=f"{o.order_no}-P1",
        status="CREATED",
        created_by=owner.id,
    )
    db.add(a)
    db.flush()
    ln = SalesOrderLine(
        order_id=o.id,
        line_no=1,
        product_id=prod.id,
        unit_code="BOX",
        ordered_qty=Decimal("5"),
        source_line_text="5 box cassava",
        rate=Decimal("29.0000"),
    )
    db.add(ln)
    db.flush()
    db.add(PickingLine(attempt_id=a.id, order_line_id=ln.id, requested_qty=Decimal("5")))
    db.flush()
    return a


def main() -> int:
    if get_settings().is_production_like:
        print("refusing in", get_settings().app_env, file=sys.stderr)
        return 2
    tag = uuid.uuid4().hex[:8]
    password = "E2e-" + secrets.token_urlsafe(12)
    out: dict[str, Any] = {"password": password, "users": {}}
    with get_sessionmaker()() as db, db.begin():
        sync_permissions(db)
        co = Company(
            legal_name=f"E2E Purchases Co {tag}",
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
            out["users"][key] = email
            emps[key] = emp
        owner = emps["owner"]
        seed_catalog(db, co)
        seed_pricing(db, co, owner)
        seed_orders(db, co, owner)  # no seed_inventory: nothing is in stock
        seed_vendors(db, co)
        out["local"] = read_invoice(
            db, co, wh, owner,
            invoice_pdf(
                "Accra Roots Exports", "ARX-5521",
                [("Cassava frozen", "25", "box", "12.50", "312.50"),
                 ("Plantain green", "10", "box", "30.00", "300.00")],
                "612.50",
            ),
            "ARX-5521.pdf",
        )  # fmt: skip
        out["match"] = read_invoice(
            db, co, wh, owner,
            invoice_pdf(
                "Kingston Produce Co", "KPC-880",
                [("Cassava frozen", "5", "box", "12.00", "60.00"),
                 ("Jasmine rice", "4", "box", "20.00", "80.00")],
                "140.00",
            ),
            "KPC-880.pdf",
        )  # fmt: skip
        sheet = open_sheet(db, co, wh, owner)
        out["packing"] = {"id": str(sheet.id), "code": sheet.pick_code}
        done = db.scalar(
            select(PickingAttempt).where(
                PickingAttempt.company_id == co.id, PickingAttempt.status == "VERIFIED"
            )
        )
        out["verified"] = {"id": str(done.id), "code": done.pick_code}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
