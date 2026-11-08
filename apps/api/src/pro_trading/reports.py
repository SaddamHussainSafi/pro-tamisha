"""Module 12 reports: read-only SQL aggregates over posted records.

Sources of truth: issued invoices + their lines (sales), the stock ledger `stock_movements`
(stock flow) and posted shipments with declared/GRN lines (receiving). Nothing here writes.

Rules kept on purpose:
- Sales count invoices in ISSUED / PARTIALLY_PAID / PAID. VOIDED and CREDITED are left out; no
  void or credit-note flow exists yet, so "issued less credits" is simply "issued".
- Money is CAD before tax (sum of line amounts) unless named tax/gross.
- Quantities are grouped by unit: boxes, pieces, lb and kg are never added together.
- No cost, margin or profit figures until D-04 (costing method) is decided.
- Dates are calendar days in the company timezone; `date_to` is inclusive.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    Date,
    Numeric,
    and_,
    case,
    cast,
    distinct,
    func,
    literal,
    literal_column,
    select,
    union_all,
)
from sqlalchemy.orm import Session

from .models import (
    Customer,
    DeclaredLine,
    GrnLine,
    Invoice,
    InvoiceLine,
    Product,
    Shipment,
    StockMovement,
    Vendor,
)

SALE_STATES = ("ISSUED", "PARTIALLY_PAID", "PAID")
ZERO = Decimal("0")
RATE = QTY = Decimal("0.0001")
CENT = Decimal("0.01")
Bucket = Literal["day", "month"]
Sort = Literal["top", "least"]


@dataclass(frozen=True)
class Scope:
    company_id: uuid.UUID
    warehouses: frozenset[uuid.UUID]
    tz: str
    date_from: date
    date_to: date

    @property
    def start(self) -> datetime:
        return datetime.combine(self.date_from, time.min, ZoneInfo(self.tz))

    @property
    def end(self) -> datetime:
        """Exclusive upper bound: midnight after `date_to`."""
        return datetime.combine(self.date_to + timedelta(days=1), time.min, ZoneInfo(self.tz))

    def period(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "timezone": self.tz,
            "currency": "CAD",
        }


def _d(v: Any) -> Decimal:
    return Decimal(v) if v is not None else ZERO


def _m(v: Any) -> Decimal:
    """Money, always 2 places."""
    return _d(v).quantize(CENT)


def _q(v: Any) -> Decimal:
    """Quantity, always 4 places (the ledger's scale)."""
    return _d(v).quantize(QTY)


# ---------------------------------------------------------------- sales


def _sales_where(
    sc: Scope, customer_id: uuid.UUID | None, product_id: uuid.UUID | None
) -> list[ColumnElement[bool]]:
    w: list[ColumnElement[bool]] = [
        Invoice.company_id == sc.company_id,
        Invoice.warehouse_id.in_(sc.warehouses),
        Invoice.state.in_(SALE_STATES),
        Invoice.issued_at >= sc.start,
        Invoice.issued_at < sc.end,
    ]
    if customer_id:
        w.append(Invoice.customer_id == customer_id)
    if product_id:
        w.append(InvoiceLine.product_id == product_id)
    return w


def _lines() -> Any:
    return select().select_from(InvoiceLine).join(Invoice, Invoice.id == InvoiceLine.invoice_id)


def _periods(sc: Scope, bucket: Bucket) -> list[date]:
    out: list[date] = []
    d = sc.date_from if bucket == "day" else sc.date_from.replace(day=1)
    while d <= sc.date_to:
        out.append(d)
        if bucket == "day":
            d += timedelta(days=1)
        else:
            d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def sales_summary(
    db: Session,
    sc: Scope,
    customer_id: uuid.UUID | None,
    product_id: uuid.UUID | None,
    bucket: Bucket,
) -> dict[str, Any]:
    where = _sales_where(sc, customer_id, product_id)
    tot = db.execute(
        _lines()
        .add_columns(
            func.count(distinct(Invoice.id)),
            func.count(distinct(Invoice.customer_id)),
            func.sum(InvoiceLine.line_amount),
            func.sum(InvoiceLine.tax_amount),
        )
        .where(*where)
    ).one()
    net, tax = _m(tot[2]), _m(tot[3])
    qty = db.execute(
        _lines()
        .add_columns(InvoiceLine.unit_code, func.sum(InvoiceLine.billed_qty))
        .where(*where)
        .group_by(InvoiceLine.unit_code)
        .order_by(InvoiceLine.unit_code)
    ).all()
    # bucket and tz are validated server values; inline them so SELECT and GROUP BY match.
    local = func.timezone(literal_column(f"'{sc.tz}'"), Invoice.issued_at)
    inner = (
        _lines()
        .add_columns(
            Invoice.id.label("iid"),
            InvoiceLine.line_amount.label("amt"),
            cast(func.date_trunc(literal_column(f"'{bucket}'"), local), Date).label("period"),
        )
        .where(*where)
        .subquery()
    )
    got = {
        r.period: (int(r.n), _m(r.net))
        for r in db.execute(
            select(
                inner.c.period,
                func.count(distinct(inner.c.iid)).label("n"),
                func.sum(inner.c.amt).label("net"),
            ).group_by(inner.c.period)
        )
    }
    none = (0, _m(0))
    series = [
        {"period": p, "invoice_count": got.get(p, none)[0], "net_sales": got.get(p, none)[1]}
        for p in _periods(sc, bucket)
    ]
    return {
        **sc.period(),
        "bucket": bucket,
        "invoice_count": int(tot[0]),
        "customer_count": int(tot[1]),
        "net_sales": net,
        "tax": tax,
        "gross": net + tax,
        "quantities": [{"unit_code": u, "qty": _q(q)} for u, q in qty],
        "series": series,
    }


def sales_by_product(
    db: Session,
    sc: Scope,
    customer_id: uuid.UUID | None,
    product_id: uuid.UUID | None,
    sort: Sort,
    limit: int,
) -> dict[str, Any]:
    g = (
        _lines()
        .add_columns(
            InvoiceLine.product_id.label("pid"),
            InvoiceLine.unit_code.label("unit"),
            func.sum(InvoiceLine.billed_qty).label("qty"),
            func.sum(InvoiceLine.line_amount).label("net"),
            func.count(distinct(Invoice.id)).label("invoices"),
            func.count(distinct(Invoice.customer_id)).label("customers"),
        )
        .where(*_sales_where(sc, customer_id, product_id))
        .group_by(InvoiceLine.product_id, InvoiceLine.unit_code)
        .subquery()
    )
    n, net = db.execute(select(func.count(), func.sum(g.c.net)).select_from(g)).one()
    order = g.c.net.desc() if sort == "top" else g.c.net.asc()
    rows = db.execute(
        select(g, Product.sku, Product.name)
        .join(Product, Product.id == g.c.pid)
        .order_by(order, Product.sku, g.c.unit)
        .limit(limit)
    ).all()
    return {
        **sc.period(),
        "total_rows": int(n),
        "net_sales": _m(net),
        "rows": [
            {
                "product_id": r.pid,
                "sku": r.sku,
                "name": r.name,
                "unit_code": r.unit,
                "qty": _q(r.qty),
                "net_sales": _m(r.net),
                "avg_rate": (_d(r.net) / _d(r.qty)).quantize(RATE) if r.qty else ZERO,
                "invoice_count": int(r.invoices),
                "customer_count": int(r.customers),
            }
            for r in rows
        ],
    }


def sales_by_customer(
    db: Session,
    sc: Scope,
    customer_id: uuid.UUID | None,
    product_id: uuid.UUID | None,
    sort: Sort,
    limit: int,
) -> dict[str, Any]:
    where = _sales_where(sc, customer_id, product_id)
    g = (
        _lines()
        .add_columns(
            Invoice.customer_id.label("cid"),
            func.count(distinct(Invoice.id)).label("invoices"),
            func.sum(InvoiceLine.line_amount).label("net"),
            func.sum(InvoiceLine.tax_amount).label("tax"),
            func.max(Invoice.issued_at).label("last_at"),
        )
        .where(*where)
        .group_by(Invoice.customer_id)
        .subquery()
    )
    n, net = db.execute(select(func.count(), func.sum(g.c.net)).select_from(g)).one()
    order = g.c.net.desc() if sort == "top" else g.c.net.asc()
    rows = db.execute(
        select(g, Customer.name, Customer.external_ref)
        .join(Customer, Customer.id == g.c.cid)
        .order_by(order, Customer.name, g.c.cid)
        .limit(limit)
    ).all()
    qty: dict[uuid.UUID, list[dict[str, Any]]] = {}
    if rows:
        for cid, unit, q in db.execute(
            _lines()
            .add_columns(
                Invoice.customer_id, InvoiceLine.unit_code, func.sum(InvoiceLine.billed_qty)
            )
            .where(*where, Invoice.customer_id.in_([r.cid for r in rows]))
            .group_by(Invoice.customer_id, InvoiceLine.unit_code)
            .order_by(InvoiceLine.unit_code)
        ):
            qty.setdefault(cid, []).append({"unit_code": unit, "qty": _q(q)})
    return {
        **sc.period(),
        "total_rows": int(n),
        "net_sales": _m(net),
        "rows": [
            {
                "customer_id": r.cid,
                "name": r.name,
                "external_ref": r.external_ref,
                "invoice_count": int(r.invoices),
                "net_sales": _m(r.net),
                "gross": _m(r.net) + _m(r.tax),
                "quantities": qty.get(r.cid, []),
                "last_invoice_at": r.last_at,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------- stock flow


def stock_flow(db: Session, sc: Scope, product_id: uuid.UUID | None) -> dict[str, Any]:
    """Per product and unit: opening + received + dispatched + other = closing, all summed from
    the ledger. `other` = opening balances, adjustments, counts, damage, returns, reversals."""
    m = StockMovement
    inside = m.created_at >= sc.start

    def part(cond: ColumnElement[bool]) -> Any:
        return func.coalesce(func.sum(case((cond, m.delta_qty), else_=0)), 0)

    where = [
        m.company_id == sc.company_id,
        m.warehouse_id.in_(sc.warehouses),
        m.created_at < sc.end,
    ]
    if product_id:
        where.append(m.product_id == product_id)
    g = (
        select(
            m.product_id.label("pid"),
            m.unit_code.label("unit"),
            part(~inside).label("opening"),
            part(and_(inside, m.movement_type == "GRN_RECEIPT")).label("received"),
            part(and_(inside, m.movement_type == "INVOICE_DISPATCH")).label("dispatched"),
            part(and_(inside, m.movement_type.notin_(("GRN_RECEIPT", "INVOICE_DISPATCH")))).label(
                "other"
            ),
            func.sum(m.delta_qty).label("closing"),
            func.count(case((inside, 1))).label("moves"),
        )
        .where(*where)
        .group_by(m.product_id, m.unit_code)
        .subquery()
    )
    rows = db.execute(
        select(g, Product.sku, Product.name)
        .join(Product, Product.id == g.c.pid)
        .where((g.c.closing != 0) | (g.c.moves > 0))
        .order_by(Product.sku, g.c.unit)
    ).all()
    return {
        **sc.period(),
        "rows": [
            {
                "product_id": r.pid,
                "sku": r.sku,
                "name": r.name,
                "unit_code": r.unit,
                "opening": _q(r.opening),
                "received": _q(r.received),
                "dispatched": _q(r.dispatched),
                "other": _q(r.other),
                "closing": _q(r.closing),
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------- receiving / vendors


def receiving(
    db: Session, sc: Scope, vendor_id: uuid.UUID | None, product_id: uuid.UUID | None
) -> dict[str, Any]:
    """Posted shipments (by GRN posting date) per vendor and unit: declared on the vendor's
    documents vs counted at the door (accepted / damaged / rejected)."""
    s = Shipment
    where: list[ColumnElement[bool]] = [
        s.company_id == sc.company_id,
        s.warehouse_id.in_(sc.warehouses),
        s.status == "POSTED",
        s.posted_at >= sc.start,
        s.posted_at < sc.end,
    ]
    if vendor_id:
        where.append(s.vendor_id == vendor_id)
    z = literal(ZERO, Numeric(18, 4))
    dl = (
        select(
            s.vendor_id.label("vid"),
            DeclaredLine.unit_code.label("unit"),
            s.id.label("sid"),
            DeclaredLine.declared_qty.label("declared"),
            z.label("accepted"),
            z.label("damaged"),
            z.label("rejected"),
        )
        .join(s, s.id == DeclaredLine.shipment_id)
        .where(*where, DeclaredLine.removed_at.is_(None))
    )
    gl = (
        select(
            s.vendor_id,
            GrnLine.unit_code,
            s.id,
            z,
            GrnLine.accepted_qty,
            GrnLine.damaged_qty,
            GrnLine.rejected_qty,
        )
        .join(s, s.id == GrnLine.shipment_id)
        .where(*where)
    )
    if product_id:
        dl = dl.where(DeclaredLine.product_id == product_id)
        gl = gl.where(GrnLine.product_id == product_id)
    u = union_all(dl, gl).subquery()
    g = (
        select(
            u.c.vid,
            u.c.unit,
            func.count(distinct(u.c.sid)).label("shipments"),
            func.sum(u.c.declared).label("declared"),
            func.sum(u.c.accepted).label("accepted"),
            func.sum(u.c.damaged).label("damaged"),
            func.sum(u.c.rejected).label("rejected"),
        )
        .group_by(u.c.vid, u.c.unit)
        .subquery()
    )
    rows = db.execute(
        select(g, Vendor.code, Vendor.name, Vendor.country_code)
        .join(Vendor, Vendor.id == g.c.vid)
        .order_by(Vendor.name, g.c.unit)
    ).all()
    total = db.scalar(select(func.count()).select_from(s).where(*where)) or 0
    return {
        **sc.period(),
        "shipments": int(total),
        "rows": [
            {
                "vendor_id": r.vid,
                "code": r.code,
                "name": r.name,
                "country_code": r.country_code,
                "unit_code": r.unit,
                "shipments": int(r.shipments),
                "declared": _q(r.declared),
                "accepted": _q(r.accepted),
                "damaged": _q(r.damaged),
                "rejected": _q(r.rejected),
            }
            for r in rows
        ],
    }
