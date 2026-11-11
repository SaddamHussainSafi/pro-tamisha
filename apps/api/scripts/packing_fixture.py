"""Synthetic company for the packing sheet e2e test (apps/web/e2e/packing-sheet.spec.ts).

Two open packing sheets: one with a single line, one with 45 lines (boxes with case sizes, pieces,
a weighed line, a line note). Prints JSON with the users, password and sheet ids. Refuses to run
in staging/production. Synthetic data only.

    PYTHONPATH=src uv run python scripts/packing_fixture.py
"""

import json
import secrets
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script

from pro_trading.bootstrap import ensure_default_roles, sync_permissions  # noqa: E402
from pro_trading.config import get_settings  # noqa: E402
from pro_trading.db import get_sessionmaker  # noqa: E402
from pro_trading.models import (  # noqa: E402
    Company,
    Customer,
    Employee,
    EmployeeRole,
    PickingAttempt,
    PickingLine,
    Product,
    ProductUnitProfile,
    SalesOrder,
    SalesOrderLine,
    Warehouse,
)
from pro_trading.security import hash_password  # noqa: E402
from scripts.load_catalog import load_catalog  # noqa: E402

CASES = {"BFR": "30", "IOK": "14", "GBN": "40", "SPM": "42"}  # lb per case (synthetic)


def _sheet(db, co, wh, cust, owner, n, lines):  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    o = SalesOrder(
        company_id=co.id,
        order_no=f"SO-{now:%Y%m%d}-{n:04d}",
        customer_id=cust.id,
        warehouse_id=wh.id,
        status="PICKING",
        source_text="synthetic",
        source_snapshot=[],
        note="synthetic packing fixture",
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
    for i, (prod, unit, qty, note) in enumerate(lines, start=1):
        ln = SalesOrderLine(
            order_id=o.id,
            line_no=i,
            product_id=prod.id,
            unit_code=unit,
            ordered_qty=Decimal(qty),
            source_line_text=f"{qty} {prod.name}",
            rate=Decimal("1.0000"),
            note=note,
        )
        db.add(ln)
        db.flush()
        db.add(PickingLine(attempt_id=a.id, order_line_id=ln.id, requested_qty=Decimal(qty)))
    db.flush()
    return a


def main() -> int:
    if get_settings().is_production_like:
        print("refusing in", get_settings().app_env, file=sys.stderr)
        return 2
    tag = uuid.uuid4().hex[:8]
    password = "E2e-" + secrets.token_urlsafe(12)
    out: dict[str, object] = {"password": password, "users": {}}
    with get_sessionmaker()() as db, db.begin():
        sync_permissions(db)
        co = Company(legal_name=f"Packing Synthetic Co {tag}", timezone="America/Toronto")
        db.add(co)
        db.flush()
        roles = ensure_default_roles(db, co.id)
        wh = Warehouse(company_id=co.id, code="MAIN", name="Main Warehouse")
        db.add(wh)
        db.flush()
        pw = hash_password(password)
        emps: dict[str, Employee] = {}
        for key, role, scope in [("owner", "OWNER_ADMIN", None), ("warehouse", "WAREHOUSE", wh.id)]:
            email = f"pack-{key}.{tag}@example.test"
            emp = Employee(
                company_id=co.id, email=email, display_name=f"E2E {key}", password_hash=pw
            )
            db.add(emp)
            db.flush()
            db.add(EmployeeRole(employee_id=emp.id, role_id=roles[role].id, warehouse_id=scope))
            out["users"][key] = email  # type: ignore[index]
            emps[key] = emp
        load_catalog(db, co.id)
        prods = list(
            db.scalars(select(Product).where(Product.company_id == co.id).order_by(Product.sku))
        )
        by_sku = {p.sku: p for p in prods}
        for sku, lb in CASES.items():
            db.add(
                ProductUnitProfile(
                    product_id=by_sku[sku].id, code=f"{lb}LB", unit_code="BOX",
                    weight_lb_per_unit=Decimal(lb), is_default=True,
                )
            )  # fmt: skip
        cust = Customer(
            company_id=co.id, external_ref="C-PK1", name="Synthetic Grocer No. 7934 - Mississauga"
        )
        db.add(cust)
        db.flush()
        one = _sheet(db, co, wh, cust, emps["owner"], 1, [(by_sku["BFR"], "BOX", "2", "")])
        many_lines = []
        for i, p in enumerate(prods[:45]):
            unit, qty, note = "BOX", str(1 + i % 4), ""
            if i == 3:
                unit, qty = "PIECE", "6"
            if i == 7:
                unit, qty = "LB", "12.5"
            if i == 5:
                note = "coming"
            many_lines.append((p, unit, qty, note))
        many = _sheet(db, co, wh, cust, emps["owner"], 2, many_lines)
        out["one"] = {"id": str(one.id), "code": one.pick_code}
        out["many"] = {"id": str(many.id), "code": many.pick_code, "lines": len(many_lines)}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
