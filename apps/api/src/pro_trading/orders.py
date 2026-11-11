"""Module 04 sales orders and picking — quantity rules and read models.

Quantities per order line are kept in the line's own unit and never converted:
    remaining = ordered - cancelled - dispatched
    awaiting  = picked quantities on VERIFIED (not yet invoiced) picking attempts
    pending   = remaining - awaiting           (what the next picking sheet asks for)
A `NO` / short pick leaves the shortfall pending (backorder) — it is never a cancellation.
Only an explicit, reasoned amendment (sales_order.close) cancels quantity.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import inventory as inv
from . import pricing
from .errors import ApiError
from .models import (
    OPEN_PICK_STATUSES,
    Customer,
    Employee,
    InventoryLot,
    PickingAttempt,
    PickingLine,
    Product,
    ProductUnitProfile,
    SalesOrder,
    SalesOrderAmendment,
    SalesOrderLine,
    Warehouse,
)

ZERO = Decimal(0)
TERMINAL = frozenset({"FULFILLED", "CLOSED_WITH_SHORTAGE", "CANCELLED"})


def now() -> datetime:
    return datetime.now(UTC)


def d(v: Decimal) -> str:
    return str(inv.q4(v))


# ------------------------------------------------------------------ quantities


@dataclass
class LineQty:
    line: SalesOrderLine
    awaiting: Decimal

    @property
    def remaining(self) -> Decimal:
        ln = self.line
        return ln.ordered_qty - ln.cancelled_qty - ln.dispatched_qty

    @property
    def pending(self) -> Decimal:
        return self.remaining - self.awaiting


def order_lines(db: Session, order_id: uuid.UUID) -> list[SalesOrderLine]:
    return list(
        db.scalars(
            select(SalesOrderLine)
            .where(SalesOrderLine.order_id == order_id)
            .order_by(SalesOrderLine.line_no)
        ).all()
    )


def awaiting_by_line(db: Session, order_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    rows = db.execute(
        select(PickingLine.order_line_id, func.coalesce(func.sum(PickingLine.picked_qty), 0))
        .join(PickingAttempt, PickingAttempt.id == PickingLine.attempt_id)
        .where(PickingAttempt.order_id == order_id, PickingAttempt.status == "VERIFIED")
        .group_by(PickingLine.order_line_id)
    ).all()
    return {lid: Decimal(q or 0) for lid, q in rows}


def line_quantities(db: Session, order_id: uuid.UUID) -> list[LineQty]:
    aw = awaiting_by_line(db, order_id)
    return [LineQty(ln, aw.get(ln.id, ZERO)) for ln in order_lines(db, order_id)]


def active_attempt(db: Session, order_id: uuid.UUID) -> PickingAttempt | None:
    return db.scalar(
        select(PickingAttempt).where(
            PickingAttempt.order_id == order_id, PickingAttempt.status.in_(OPEN_PICK_STATUSES)
        )
    )


def refresh_status(db: Session, order: SalesOrder) -> None:
    """Derive the post-confirmation status from quantities and attempts."""
    if order.status in ("DRAFT", "CANCELLED", "CLOSED_WITH_SHORTAGE"):
        return
    qs = line_quantities(db, order.id)
    remaining = sum((q.remaining for q in qs), ZERO)
    dispatched = sum((q.line.dispatched_qty for q in qs), ZERO)
    awaiting = sum((q.awaiting for q in qs), ZERO)
    if active_attempt(db, order.id) is not None:
        status = "PICKING"
    elif remaining == 0:
        status = "FULFILLED" if dispatched > 0 else "CANCELLED"
    elif dispatched > 0 or awaiting > 0:
        status = "PARTIALLY_FULFILLED"
    else:
        status = "CONFIRMED"
    if status != order.status:
        order.status = status
        order.updated_at = now()


# ------------------------------------------------------------------ validation


def validate_lines(db: Session, company_id: uuid.UUID, lines: list[Any]) -> list[Product]:
    """Each product active and in this company; unit stockable; whole BOX/PIECE; no repeats."""
    seen: set[tuple[uuid.UUID, str]] = set()
    products: list[Product] = []
    for i, ln in enumerate(lines, start=1):
        prod = db.get(Product, ln.product_id)
        if prod is None or prod.company_id != company_id:
            raise ApiError(422, "INVALID_PRODUCT", f"Line {i}: product not found", line=i)
        if not prod.active:
            raise ApiError(422, "PRODUCT_INACTIVE", f"Line {i}: {prod.sku} is inactive", line=i)
        try:
            inv.check_unit(db, prod, ln.unit_code)
            inv.check_qty(ln.unit_code, ln.qty)
        except ApiError as e:
            e.extra["line"] = i
            e.message = f"Line {i}: {e.message}"
            raise
        if getattr(ln, "weight_lb", None) is not None and ln.unit_code != "BOX":
            raise ApiError(
                422,
                "WEIGHT_NOT_ALLOWED",
                f"Line {i}: a weight is for boxes; {prod.sku} is ordered by {ln.unit_code}",
                line=i,
            )
        k = (prod.id, ln.unit_code)
        if k in seen:
            raise ApiError(
                422,
                "DUPLICATE_LINE",
                f"Line {i}: {prod.sku} in {ln.unit_code} is already on the order",
                line=i,
            )
        seen.add(k)
        products.append(prod)
    return products


def get_customer(db: Session, company_id: uuid.UUID, cid: uuid.UUID) -> Customer:
    c = db.get(Customer, cid)
    if c is None or c.company_id != company_id:
        raise ApiError(422, "INVALID_CUSTOMER", "Customer not found")
    if not c.active:
        raise ApiError(422, "CUSTOMER_INACTIVE", f"{c.name} is inactive")
    return c


def check_lot(db: Session, attempt: PickingAttempt, ln: SalesOrderLine, lot_id: uuid.UUID) -> None:
    lot = db.get(InventoryLot, lot_id)
    if (
        lot is None
        or lot.company_id != attempt.company_id
        or lot.product_id != ln.product_id
        or lot.warehouse_id != attempt.warehouse_id
    ):
        raise ApiError(422, "INVALID_LOT", "Lot is not this product in this warehouse")
    if not inv.is_sellable(lot):
        raise ApiError(409, "LOT_NOT_SALEABLE", f"Lot {lot.lot_code} is not saleable")


def outcome(requested: Decimal, picked: Decimal) -> str:
    if picked == requested:
        return "PICKED"
    return "NO" if picked == 0 else "SHORT"


# ------------------------------------------------------------------ read models


def _names(db: Session, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    real = {i for i in ids if i is not None}
    if not real:
        return {}
    rows = db.execute(select(Employee.id, Employee.display_name).where(Employee.id.in_(real)))
    return {i: n for i, n in rows.all()}


def attempt_summaries(db: Session, where: Any, limit: int = 200) -> list[dict[str, Any]]:
    n_lines = (
        select(func.count())
        .where(PickingLine.attempt_id == PickingAttempt.id)
        .correlate(PickingAttempt)
        .scalar_subquery()
    )
    rows = db.execute(
        select(PickingAttempt, SalesOrder.order_no, Customer.name, Warehouse.code, n_lines)
        .join(SalesOrder, SalesOrder.id == PickingAttempt.order_id)
        .join(Customer, Customer.id == SalesOrder.customer_id)
        .join(Warehouse, Warehouse.id == PickingAttempt.warehouse_id)
        .where(where)
        .order_by(PickingAttempt.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": str(a.id),
            "order_id": str(a.order_id),
            "order_no": no,
            "customer_name": cust,
            "warehouse_code": wh,
            "attempt_no": a.attempt_no,
            "pick_code": a.pick_code,
            "status": a.status,
            "line_count": n,
            "created_at": a.created_at.isoformat(),
            "verified_at": a.verified_at.isoformat() if a.verified_at else None,
        }
        for a, no, cust, wh, n in rows
    ]


def order_out(db: Session, o: SalesOrder) -> dict[str, Any]:
    cust = db.get(Customer, o.customer_id)
    wh = db.get(Warehouse, o.warehouse_id)
    assert cust is not None and wh is not None
    qs = line_quantities(db, o.id)
    prods = {
        p.id: p
        for p in db.scalars(
            select(Product).where(Product.id.in_([q.line.product_id for q in qs] or [uuid.uuid4()]))
        ).all()
    }
    has_verified = (
        db.scalar(
            select(func.count()).where(
                PickingAttempt.order_id == o.id,
                PickingAttempt.status.in_(("VERIFIED", "INVOICED")),
            )
        )
        or 0
    ) > 0
    lines = []
    for q in qs:
        ln, p = q.line, prods[q.line.product_id]
        snap = ln.price_snapshot or {}
        lines.append(
            {
                "id": str(ln.id),
                "line_no": ln.line_no,
                "product_id": str(ln.product_id),
                "sku": p.sku,
                "product_name": p.name,
                "unit_code": ln.unit_code,
                "ordered_qty": d(ln.ordered_qty),
                "cancelled_qty": d(ln.cancelled_qty),
                "dispatched_qty": d(ln.dispatched_qty),
                "awaiting_invoice_qty": d(q.awaiting),
                "remaining_qty": d(q.remaining),
                "pending_qty": d(q.pending),
                "backorder": has_verified and q.pending > 0 and o.status not in TERMINAL,
                "source_line_text": ln.source_line_text,
                "rate": d(ln.rate) if ln.rate is not None else None,
                "price_scope": snap.get("scope"),
                "price_version_code": snap.get("version_code"),
                "note": ln.note,
                "weight_lb": d(ln.weight_lb) if ln.weight_lb is not None else None,
                "lb_rate": snap.get("lb_rate"),
            }
        )
    amends = db.scalars(
        select(SalesOrderAmendment)
        .where(SalesOrderAmendment.order_id == o.id)
        .order_by(SalesOrderAmendment.created_at)
    ).all()
    line_no = {q.line.id: q.line.line_no for q in qs}
    names = _names(db, {o.created_by, o.confirmed_by, *(a.actor_id for a in amends)})
    act = active_attempt(db, o.id)
    return {
        "id": str(o.id),
        "order_no": o.order_no,
        "customer_id": str(cust.id),
        "customer_name": cust.name,
        "customer_ref": cust.external_ref,
        "warehouse_id": str(wh.id),
        "warehouse_code": wh.code,
        "status": o.status,
        "source": o.source,
        "source_text": o.source_text,
        "source_snapshot": o.source_snapshot,
        "requested_for": o.requested_for.isoformat() if o.requested_for else None,
        "note": o.note,
        "created_by_name": names.get(o.created_by) if o.created_by else None,
        "confirmed_by_name": names.get(o.confirmed_by) if o.confirmed_by else None,
        "confirmed_at": o.confirmed_at.isoformat() if o.confirmed_at else None,
        "close_reason": o.close_reason,
        "has_backorder": any(ln["backorder"] for ln in lines),
        "open_attempt_id": str(act.id) if act else None,
        "version": o.version,
        "created_at": o.created_at.isoformat(),
        "lines": lines,
        "attempts": attempt_summaries(db, PickingAttempt.order_id == o.id),
        "amendments": [
            {
                "id": str(a.id),
                "line_no": line_no.get(a.line_id) if a.line_id else None,
                "kind": a.kind,
                "qty": d(a.qty) if a.qty is not None else None,
                "reason": a.reason,
                "actor_name": names.get(a.actor_id) if a.actor_id else None,
                "created_at": a.created_at.isoformat(),
            }
            for a in amends
        ],
    }


def pick_out(db: Session, a: PickingAttempt) -> dict[str, Any]:
    o = db.get(SalesOrder, a.order_id)
    wh = db.get(Warehouse, a.warehouse_id)
    assert o is not None and wh is not None
    cust = db.get(Customer, o.customer_id)
    assert cust is not None
    rows = db.execute(
        select(PickingLine, SalesOrderLine, Product.sku, Product.name, InventoryLot.lot_code)
        .join(SalesOrderLine, SalesOrderLine.id == PickingLine.order_line_id)
        .join(Product, Product.id == SalesOrderLine.product_id)
        .outerjoin(InventoryLot, InventoryLot.id == PickingLine.lot_id)
        .where(PickingLine.attempt_id == a.id)
        .order_by(SalesOrderLine.line_no)
    ).all()
    names = _names(db, {a.created_by, a.picked_by, a.verified_by})
    return {
        "id": str(a.id),
        "order_id": str(o.id),
        "order_no": o.order_no,
        "customer_name": cust.name,
        "warehouse_id": str(wh.id),
        "warehouse_code": wh.code,
        "attempt_no": a.attempt_no,
        "pick_code": a.pick_code,
        "status": a.status,
        "status_note": a.status_note,
        "created_by_name": names.get(a.created_by) if a.created_by else None,
        "picked_by_name": names.get(a.picked_by) if a.picked_by else None,
        "verified_by_name": names.get(a.verified_by) if a.verified_by else None,
        "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
        "verified_at": a.verified_at.isoformat() if a.verified_at else None,
        "created_at": a.created_at.isoformat(),
        "version": a.version,
        "lines": [
            {
                "id": str(pl.id),
                "order_line_id": str(ol.id),
                "line_no": ol.line_no,
                "sku": sku,
                "product_name": pname,
                "unit_code": ol.unit_code,
                "requested_qty": d(pl.requested_qty),
                "picked_qty": d(pl.picked_qty) if pl.picked_qty is not None else None,
                "outcome": pl.outcome,
                "shortage_reason": pl.shortage_reason,
                "disposition": pl.disposition,
                "lot_id": str(pl.lot_id) if pl.lot_id else None,
                "lot_code": lot_code,
                "weight_lb": d(pl.weight_lb) if pl.weight_lb is not None else None,
                "note": pl.note,
            }
            for pl, ol, sku, pname, lot_code in rows
        ],
    }


def price_lines(db: Session, company_id: uuid.UUID, o: SalesOrder, at: datetime) -> None:
    """Freeze each line's rate (customer > group > standard) as of `at`. Used on confirm and by
    direct invoices; later price books never change these lines."""
    cust = get_customer(db, company_id, o.customer_id)
    for ln in order_lines(db, o.id):
        prod = db.get(Product, ln.product_id)
        assert prod is not None
        if not prod.active:
            raise ApiError(
                422, "PRODUCT_INACTIVE", f"Line {ln.line_no}: {prod.sku} is inactive",
                line=ln.line_no,
            )  # fmt: skip
        ln.rate, ln.price_snapshot = quote(
            db, company_id, prod, ln.unit_code, cust, at, ln.line_no,
            partial(_ordered_box_lb, db, o, ln), line_box_lb(ln.weight_lb, ln.ordered_qty),
        )  # fmt: skip


def quote(
    db: Session,
    company_id: uuid.UUID,
    prod: Product,
    unit_code: str,
    cust: Customer,
    at: datetime,
    line_no: int,
    ordered_box_lb: Callable[[], Decimal | None] | None = None,
    weighed_box_lb: Decimal | None = None,
) -> tuple[Decimal, dict[str, Any]]:
    """Rate + snapshot for one line as of `at`, exactly as confirming would freeze it. Raises
    NO_PRICE / WEIGHT_REQUIRED (with `line`) when nothing prices it. Also used by the
    order/invoice line preview. `weighed_box_lb` is the line's weight / boxes: boxes whose weight
    is known are sold per lb (owner, 2026-11-11: "all products go as per pound")."""
    if unit_code == "BOX" and weighed_box_lb is not None:
        try:
            pricing.resolve(db, company_id, prod.id, "LB", cust, at)
        except pricing.NoPriceError:
            pass  # no per-lb price: a box price, if any, still applies
        else:
            return _box_from_lb(db, company_id, prod, cust, at, line_no, lambda: weighed_box_lb)
    try:
        qt = pricing.resolve(db, company_id, prod.id, unit_code, cust, at)
    except pricing.NoPriceError:
        if unit_code != "BOX":
            msg = f"Line {line_no}: no active price for {prod.sku} per {unit_code}"
            raise ApiError(422, "NO_PRICE", msg, line=line_no) from None
        return _box_from_lb(db, company_id, prod, cust, at, line_no, ordered_box_lb)
    return qt.rate, qt.snapshot()


def line_box_lb(weight_lb: Decimal | None, qty: Decimal) -> Decimal | None:
    """Pounds per box from the weight of the whole line, when one was entered."""
    if weight_lb is None or qty <= 0:
        return None
    return (weight_lb / qty).quantize(pricing.Q4)


def _box_from_lb(
    db: Session,
    company_id: uuid.UUID,
    prod: Product,
    cust: Customer,
    at: datetime,
    line_no: int,
    ordered_box_lb: Callable[[], Decimal | None] | None,
) -> tuple[Decimal, dict[str, Any]]:
    """A box with no box price is priced per lb x the box's weight (owner, 2026-11-10): the
    size the customer picked on the online order form, else the product's default case size.
    The snapshot keeps the per-lb price and the weight used, so the invoice shows the box size."""
    try:
        lb = pricing.resolve(db, company_id, prod.id, "LB", cust, at)
    except pricing.NoPriceError:
        msg = f"Line {line_no}: no active price for {prod.sku} per BOX or per LB"
        raise ApiError(422, "NO_PRICE", msg, line=line_no) from None
    box_lb = (ordered_box_lb() if ordered_box_lb else None) or _default_box_lb(db, prod.id)
    if box_lb is None:
        msg = f"Line {line_no}: {prod.sku} is sold per lb. Enter the weight (LB) for this line."
        raise ApiError(422, "WEIGHT_REQUIRED", msg, line=line_no)
    rate = (lb.rate * box_lb).quantize(pricing.Q4)
    snap = lb.snapshot() | {
        "unit_code": "BOX",
        "rate": str(rate),
        "basis": "PER_LB_X_BOX",
        "lb_rate": str(lb.rate),
        "box_lb": str(box_lb),
    }
    return rate, snap


def _ordered_box_lb(db: Session, o: SalesOrder, ln: SalesOrderLine) -> Decimal | None:
    """Box size (lb) the customer picked for this line on an online order, if any."""
    from .order_connect.models import OrderFormSubmission  # local: order_connect imports orders

    sub = db.scalar(select(OrderFormSubmission).where(OrderFormSubmission.sales_order_id == o.id))
    for it in ((sub.raw or {}).get("items") or []) if sub else []:
        if it.get("product_id") == str(ln.product_id) and it.get("unit_code") == "BOX":
            v = it.get("box_size_lb")
            return Decimal(str(v)) if v else None
    return None


def _default_box_lb(db: Session, product_id: uuid.UUID) -> Decimal | None:
    """Weight of the product's active BOX case size, the default first."""
    return db.scalar(
        select(ProductUnitProfile.weight_lb_per_unit)
        .where(
            ProductUnitProfile.product_id == product_id,
            ProductUnitProfile.unit_code == "BOX",
            ProductUnitProfile.active.is_(True),
            ProductUnitProfile.weight_lb_per_unit.is_not(None),
        )
        .order_by(ProductUnitProfile.is_default.desc(), ProductUnitProfile.code)
        .limit(1)
    )
