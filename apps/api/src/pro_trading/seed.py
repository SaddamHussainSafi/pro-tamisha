"""Seed SYNTHETIC development data. Refuses to run in staging/production.

Usage: uv run python -m pro_trading.seed
"""

import os
import sys
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .bootstrap import ensure_default_roles, sync_permissions
from .config import get_settings
from .db import get_sessionmaker
from .models import (
    Company,
    Customer,
    CustomerGroup,
    Employee,
    EmployeeRole,
    FeatureFlag,
    PriceBookVersion,
    PriceEntry,
    Product,
    ProductAlias,
    ProductCategory,
    ProductUnitProfile,
    SalesOrder,
    Vendor,
    Warehouse,
)
from .routes.catalog import norm
from .security import hash_password

DEV_PASSWORD_ENV = "SEED_DEV_PASSWORD"  # noqa: S105 - env var name, not a secret
DEMO_COMPANY = "Demo Trading Co (synthetic)"


def main() -> int:
    s = get_settings()
    if s.is_production_like:
        print("refusing to seed synthetic data in", s.app_env, file=sys.stderr)
        return 2
    password = os.environ.get(DEV_PASSWORD_ENV)
    if not password:
        print(f"set {DEV_PASSWORD_ENV} to the dev password to assign", file=sys.stderr)
        return 2
    with get_sessionmaker()() as db, db.begin():
        sync_permissions(db)
        existing = db.scalar(select(Company).where(Company.legal_name == DEMO_COMPANY))
        if existing:
            ensure_default_roles(db, existing.id)  # pick up grants added by later modules
            seed_catalog(db, existing)
            seed_pricing(db, existing)
            seed_inventory(db, existing)
            seed_orders(db, existing)
            seed_vendors(db, existing)
            print("already seeded")
            return 0
        co = Company(legal_name=DEMO_COMPANY, timezone="America/Toronto")
        db.add(co)
        db.flush()
        roles = ensure_default_roles(db, co.id)
        # One warehouse: the business runs from a single site (owner, 2026-11-08).
        main_wh = Warehouse(company_id=co.id, code="MAIN", name="Main Warehouse")
        db.add(main_wh)
        db.flush()
        pw = hash_password(password)
        people = [
            ("owner@example.test", "Olivia Owner", "OWNER_ADMIN", None),
            ("manager@example.test", "Mark Manager", "MANAGER", None),
            ("sales@example.test", "Sam Sales", "SALES_OPERATOR", None),
            ("warehouse@example.test", "Wendy Warehouse", "WAREHOUSE", main_wh.id),
            ("finance@example.test", "Fiona Finance", "FINANCE", None),
        ]
        for email, name, role, wh in people:
            emp = Employee(company_id=co.id, email=email, display_name=name, password_hash=pw)
            db.add(emp)
            db.flush()
            db.add(EmployeeRole(employee_id=emp.id, role_id=roles[role].id, warehouse_id=wh))
        db.add(FeatureFlag(company_id=co.id, key="scanner_bridge", enabled=False, version=1))
        seed_catalog(db, co)
        seed_pricing(db, co)
        seed_inventory(db, co)
        seed_orders(db, co)
        seed_vendors(db, co)
    print("seeded synthetic demo company; two-step sign-in is optional (My account)")
    return 0


# (sku, name, category, stock_unit, expiry, [(code, unit, pieces, lb, default)], [aliases])
_Profile = tuple[str, str, str | None, str | None, bool]  # decimal strings
_Item = tuple[str, str, str, str, bool, list[_Profile], list[str]]
CATALOG: list[_Item] = [
    (
        "CFZ",
        "Cassava frozen",
        "FROZEN",
        "LB",
        True,
        [
            ("CASE20", "BOX", None, "20", True),
            ("CASE25", "BOX", None, "25", False),
            ("CASE30", "BOX", None, "30", False),
            ("CASE40", "BOX", None, "40", False),
            ("PC", "PIECE", None, None, False),
        ],
        ["Yuca congelada", "Cassava FZ"],
    ),
    (
        "PLG",
        "Plantain green",
        "PRODUCE",
        "LB",
        False,
        [("CASE50", "BOX", None, "50", True), ("HAND", "PIECE", "6", None, False)],
        ["Platano verde"],
    ),
    ("RJS", "Jasmine rice", "DRY", "KG", False, [("BAG20", "BOX", None, "44.0925", True)], []),
]


def seed_catalog(db: Session, co: Company) -> None:
    """Idempotent: adds missing synthetic catalog rows only."""
    cats: dict[str, ProductCategory] = {}
    for code, name in [("FROZEN", "Frozen"), ("PRODUCE", "Produce"), ("DRY", "Dry goods")]:
        cat = db.scalar(
            select(ProductCategory).where(
                ProductCategory.company_id == co.id, ProductCategory.code == code
            )
        )
        if cat is None:
            cat = ProductCategory(company_id=co.id, code=code, name=name)
            db.add(cat)
            db.flush()
        cats[code] = cat
    for sku, name, cat_code, stock_unit, expiry, profiles, aliases in CATALOG:
        if db.scalar(select(Product).where(Product.company_id == co.id, Product.sku == sku)):
            continue
        prod = Product(
            company_id=co.id,
            sku=sku,
            name=name,
            category_id=cats[cat_code].id,
            stock_unit=stock_unit,
            tracks_expiry=expiry,
        )
        db.add(prod)
        db.flush()
        for code, unit, pieces, lb, default in profiles:
            db.add(
                ProductUnitProfile(
                    product_id=prod.id,
                    code=code,
                    unit_code=unit,
                    pieces_per_unit=Decimal(pieces) if pieces else None,
                    weight_lb_per_unit=Decimal(lb) if lb else None,
                    is_default=default,
                )
            )
        for a in aliases:
            db.add(ProductAlias(company_id=co.id, product_id=prod.id, alias=a, alias_norm=norm(a)))


GROUPS = [("WHOLESALE", "Wholesale"), ("RESTAURANT", "Restaurants")]
CUSTOMERS = [
    ("C-1001", "Island Grocers (synthetic)", "WHOLESALE", 30),
    ("C-1002", "Kingston Kitchen (synthetic)", "RESTAURANT", 14),
    ("C-1003", "Walk-in cash (synthetic)", None, 0),
]
# (sku, unit, rate, customer_ref, group_code) — decimal strings
PRICES = [
    ("CFZ", "LB", "1.50", None, None),
    ("CFZ", "BOX", "29.00", None, None),
    ("CFZ", "LB", "1.40", None, "WHOLESALE"),
    ("CFZ", "LB", "1.35", "C-1001", None),
    ("PLG", "LB", "0.95", None, None),
    ("PLG", "PIECE", "0.40", None, None),
    ("PLG", "BOX", "45.00", None, "RESTAURANT"),
    ("RJS", "KG", "2.10", None, None),
    ("RJS", "BOX", "39.50", None, None),
]


def seed_pricing(db: Session, co: Company, approver: Employee | None = None) -> None:
    """Groups, customers and one ACTIVE price book (idempotent)."""
    groups: dict[str, CustomerGroup] = {}
    for code, name in GROUPS:
        g = db.scalar(
            select(CustomerGroup).where(
                CustomerGroup.company_id == co.id, CustomerGroup.code == code
            )
        )
        if g is None:
            g = CustomerGroup(company_id=co.id, code=code, name=name)
            db.add(g)
            db.flush()
        groups[code] = g
    custs: dict[str, Customer] = {}
    for ref, name, gcode, terms in CUSTOMERS:
        c = db.scalar(
            select(Customer).where(Customer.company_id == co.id, Customer.external_ref == ref)
        )
        if c is None:
            c = Customer(
                company_id=co.id,
                external_ref=ref,
                name=name,
                group_id=groups[gcode].id if gcode else None,
                payment_terms_days=terms,
            )
            db.add(c)
            db.flush()
        custs[ref] = c
    if db.scalar(select(PriceBookVersion).where(PriceBookVersion.company_id == co.id)):
        return
    owner = approver or db.scalar(
        select(Employee).where(Employee.company_id == co.id, Employee.email == "owner@example.test")
    )
    v = PriceBookVersion(
        company_id=co.id, version_code="PB-2026-01", notes="Synthetic opening prices"
    )
    db.add(v)
    db.flush()
    prods = {p.sku: p for p in db.scalars(select(Product).where(Product.company_id == co.id))}
    for sku, unit, rate, cref, gc in PRICES:
        db.add(
            PriceEntry(
                version_id=v.id,
                product_id=prods[sku].id,
                scope="CUSTOMER" if cref else "GROUP" if gc else "STANDARD",
                customer_id=custs[cref].id if cref else None,
                group_id=groups[gc].id if gc else None,
                unit_code=unit,
                rate=Decimal(rate),
                currency="CAD",
            )
        )
    db.flush()
    now = datetime.now(UTC)
    v.status, v.effective_at, v.approved_at = "ACTIVE", now, now
    v.approved_by = owner.id if owner else None
    db.flush()


# (lot_code, sku, expires_in_days, [(unit, qty)]) -- synthetic opening stock in MAIN
LOTS: list[tuple[str, str, int | None, list[tuple[str, str]]]] = [
    ("CAS-FZ-SEED-1", "CFZ", 180, [("BOX", "40"), ("LB", "120.5")]),
    ("CAS-FZ-SEED-2", "CFZ", 20, [("BOX", "8")]),
    ("PLN-GR-SEED-1", "PLG", None, [("BOX", "25"), ("PIECE", "60")]),
    ("RICE-JAS-SEED-1", "RJS", None, [("BOX", "100"), ("KG", "250")]),
]


def seed_inventory(db: Session, co: Company, actor: Employee | None = None) -> None:
    """Opening balances for the MAIN warehouse through the ledger service (idempotent)."""
    from datetime import timedelta

    from . import inventory as inv
    from .models import InventoryLot

    wh = db.scalar(select(Warehouse).where(Warehouse.company_id == co.id, Warehouse.code == "MAIN"))
    if wh is None:
        return
    for code, sku, days, lines in LOTS:
        if db.scalar(
            select(InventoryLot.id).where(
                InventoryLot.company_id == co.id, InventoryLot.lot_code == code
            )
        ):
            continue
        prod = db.scalar(select(Product).where(Product.company_id == co.id, Product.sku == sku))
        if prod is None:
            continue
        now = datetime.now(UTC)
        lot = InventoryLot(
            company_id=co.id,
            product_id=prod.id,
            warehouse_id=wh.id,
            lot_code=code,
            received_at=now,
            expires_at=now + timedelta(days=days) if days else None,
            created_by=actor.id if actor else None,
        )
        db.add(lot)
        db.flush()
        inv.post(
            db,
            actor.id if actor else None,
            [
                inv.Posting(
                    lot=lot,
                    unit_code=unit,
                    delta=Decimal(qty),
                    movement_type="OPENING_BALANCE",
                    posting_key=f"opening:{lot.id}:{unit}",
                    source_type="OPENING_BALANCE",
                    source_id=lot.id,
                    note="synthetic seed",
                )
                for unit, qty in lines
            ],
        )


# (customer_ref, status, [(sku, unit, qty, text)]) -- synthetic orders in MAIN
ORDERS: list[tuple[str, str, list[tuple[str, str, str, str]]]] = [
    (
        "C-1001",
        "CONFIRMED",
        [("CFZ", "BOX", "15", "15 bx yuca"), ("PLG", "PIECE", "24", "2 dz plantain")],
    ),
    ("C-1002", "DRAFT", [("RJS", "BOX", "4", "4 bags jasmine")]),
    # packed + verified, waiting for its invoice (Module 05 demo: catch-weight billing)
    ("C-1002", "VERIFIED", [("CFZ", "BOX", "7", "7 cases yuca, bill by weight")]),
]


def seed_orders(db: Session, co: Company, actor: Employee | None = None) -> None:
    """A confirmed (priced) order and a draft in MAIN, only if the company has no orders."""
    from . import pricing
    from .models import SalesOrder, SalesOrderLine

    if db.scalar(select(SalesOrder.id).where(SalesOrder.company_id == co.id).limit(1)):
        return
    wh = db.scalar(select(Warehouse).where(Warehouse.company_id == co.id, Warehouse.code == "MAIN"))
    if wh is None:
        return
    now = datetime.now(UTC)
    for n, (cref, status, lines) in enumerate(ORDERS, start=1):
        cust = db.scalar(
            select(Customer).where(Customer.company_id == co.id, Customer.external_ref == cref)
        )
        prods = {p.sku: p for p in db.scalars(select(Product).where(Product.company_id == co.id))}
        if cust is None or any(sku not in prods for sku, *_ in lines):
            continue
        o = SalesOrder(
            company_id=co.id,
            order_no=f"SO-{now:%Y%m%d}-{n:04d}",
            customer_id=cust.id,
            warehouse_id=wh.id,
            source_text="\n".join(t for *_, t in lines),
            source_snapshot=[
                {"line_no": str(i), "sku": sku, "unit_code": u, "qty": q, "text": t}
                for i, (sku, u, q, t) in enumerate(lines, start=1)
            ],
            note="synthetic seed",
            created_by=actor.id if actor else None,
        )
        db.add(o)
        db.flush()
        for i, (sku, unit, qty, t) in enumerate(lines, start=1):
            ln = SalesOrderLine(
                order_id=o.id,
                line_no=i,
                product_id=prods[sku].id,
                unit_code=unit,
                ordered_qty=Decimal(qty),
                source_line_text=t,
            )
            if status != "DRAFT":
                qt = pricing.resolve(db, co.id, prods[sku].id, unit, cust, now)
                ln.rate, ln.price_snapshot = qt.rate, qt.snapshot()
            db.add(ln)
        db.flush()
        if status != "DRAFT":
            o.status, o.confirmed_at = "CONFIRMED", now
            o.confirmed_by = actor.id if actor else None
            o.version = 2
            db.flush()
        if status == "VERIFIED":
            _seed_verified_pick(db, o, actor)


def _seed_verified_pick(db: Session, o: SalesOrder, actor: Employee | None) -> None:
    from . import orders as osvc
    from .models import PickingAttempt, PickingLine, SalesOrderLine

    a = PickingAttempt(
        company_id=o.company_id,
        order_id=o.id,
        warehouse_id=o.warehouse_id,
        attempt_no=1,
        pick_code=f"{o.order_no}-P1",
        status="VERIFIED",
        created_by=actor.id if actor else None,
        picked_by=actor.id if actor else None,
        submitted_at=datetime.now(UTC),
        verified_by=actor.id if actor else None,
        verified_at=datetime.now(UTC),
        version=4,
    )
    db.add(a)
    db.flush()
    for ln in db.scalars(select(SalesOrderLine).where(SalesOrderLine.order_id == o.id)):
        db.add(
            PickingLine(
                attempt_id=a.id,
                order_line_id=ln.id,
                requested_qty=ln.ordered_qty,
                picked_qty=ln.ordered_qty,
                outcome=osvc.outcome(ln.ordered_qty, ln.ordered_qty),
            )
        )
    db.flush()
    osvc.refresh_status(db, o)
    o.version += 1
    db.flush()


VENDORS = [
    ("V-ACC", "Accra Roots Exports (synthetic)", "GH", "USD"),
    ("V-KIN", "Kingston Produce Co (synthetic)", "JM", "USD"),
    ("V-LIM", "Lima Dry Goods (synthetic)", "PE", "USD"),
]


def seed_vendors(db: Session, co: Company) -> None:
    """Idempotent: adds missing synthetic vendors plus one remembered vendor label."""
    have = set(db.scalars(select(Vendor.code).where(Vendor.company_id == co.id)))
    for code, name, country, cur in VENDORS:
        if code not in have:
            db.add(
                Vendor(company_id=co.id, code=code, name=name, country_code=country, currency=cur)
            )
    db.flush()
    cas = db.scalar(select(Product).where(Product.company_id == co.id, Product.sku == "CFZ"))
    label = "MANIOC FROZEN 40LB CTN"
    if cas is not None and not db.scalar(
        select(ProductAlias.id).where(
            ProductAlias.company_id == co.id, ProductAlias.source_label == "vendor:V-ACC"
        )
    ):
        db.add(
            ProductAlias(
                company_id=co.id,
                product_id=cas.id,
                alias=label,
                alias_norm=norm(label),
                source_label="vendor:V-ACC",
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
