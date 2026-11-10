"""Create a fresh SYNTHETIC company + users for browser E2E and print them as JSON.

Refuses to run outside development/test. Usage: uv run python scripts/e2e_fixture.py [--sheet PATH]
--sheet also writes a hand-filled synthetic PT-OS-1 order sheet (customer C-1001) for OCR tests.
"""

import json
import secrets
import sys
import uuid

from pro_trading import ocr
from pro_trading.bootstrap import ensure_default_roles, sync_permissions
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.models import Company, Customer, Employee, EmployeeRole, Warehouse
from pro_trading.security import hash_password
from pro_trading.seed import (
    seed_catalog,
    seed_inventory,
    seed_orders,
    seed_pricing,
    seed_vendors,
)


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
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
