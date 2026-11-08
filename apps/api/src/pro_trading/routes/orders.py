"""Module 04 sales orders + picking attempts.

Every command locks the order row (FOR UPDATE) before reading quantities, checks the optimistic
version, and runs inside `mutate()` (idempotency + audit + commit in one transaction). No route
assigns an invoice number or posts stock — that is Module 05 (invoice issue)."""

import uuid
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import idempotency
from .. import inventory as inv
from .. import orders as svc
from ..deps import DbDep, Principal, PrincipalDep, authorize
from ..errors import ApiError
from ..models import (
    Company,
    Customer,
    PickingAttempt,
    PickingLine,
    SalesOrder,
    SalesOrderAmendment,
    SalesOrderLine,
    Warehouse,
)
from ..schemas import (
    LineCancelIn,
    OrderDraftIn,
    OrderIn,
    OrderOut,
    OrderPage,
    PickOut,
    PickRecordIn,
    PickSummaryOut,
    PickVerifyIn,
    ReasonIn,
    VersionIn,
)
from ._common import esc, mutate

router = APIRouter(tags=["sales-orders"])

Result = tuple[dict[str, Any], str, dict[str, Any]]


# ------------------------------------------------------------------ helpers


def readable(db: Session, p: Principal) -> set[uuid.UUID]:
    ids = db.scalars(select(Warehouse.id).where(Warehouse.company_id == p.company_id)).all()
    return {w for w in ids if p.can("sales_order.read", w)}


def get_order(db: Session, p: Principal, oid: uuid.UUID, lock: bool = False) -> SalesOrder:
    o = db.get(SalesOrder, oid, with_for_update=lock)
    if o is None or o.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Order not found")
    if lock:
        db.refresh(o)
    return o


def get_attempt(db: Session, p: Principal, aid: uuid.UUID) -> PickingAttempt:
    a = db.get(PickingAttempt, aid)
    if a is None or a.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Picking attempt not found")
    return a


def check_version(current: int, sent: int, what: str) -> None:
    if current != sent:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            f"{what} changed since you loaded it; reload",
            current_version=current,
        )


def state(obj: Any, allowed: tuple[str, ...], what: str) -> None:
    if obj.status not in allowed:
        raise ApiError(
            409, "INVALID_STATE", f"{what} is {obj.status.lower()}", current_status=obj.status
        )


def warehouse(db: Session, p: Principal, wid: uuid.UUID) -> Warehouse:
    wh = db.get(Warehouse, wid)
    if wh is None or wh.company_id != p.company_id:
        raise ApiError(422, "INVALID_WAREHOUSE", "Warehouse not found")
    if not wh.active:
        raise ApiError(409, "WAREHOUSE_INACTIVE", f"Warehouse {wh.code} is inactive")
    return wh


def bump(o: SalesOrder) -> None:
    o.version += 1
    o.updated_at = svc.now()


def write_lines(db: Session, o: SalesOrder, body: OrderIn | OrderDraftIn) -> None:
    for i, ln in enumerate(body.lines, start=1):
        db.add(
            SalesOrderLine(
                order_id=o.id,
                line_no=i,
                product_id=ln.product_id,
                unit_code=ln.unit_code,
                ordered_qty=inv.q4(ln.qty),
                source_line_text=ln.source_line_text,
                note=ln.note,
            )
        )


# ------------------------------------------------------------------ orders: reads


@router.get("/sales-orders", response_model=OrderPage)
def list_orders(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    status: str | None = Query(None, pattern=r"^[A-Z_]{4,24}(,[A-Z_]{4,24})*$"),
    warehouse_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    backorder: bool | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, pattern=r"^\d{1,7}$"),
) -> dict[str, Any]:
    authorize(request, p, "sales_order.read", any_scope=True)
    n_lines = (
        select(func.count())
        .where(SalesOrderLine.order_id == SalesOrder.id)
        .correlate(SalesOrder)
        .scalar_subquery()
    )
    stmt = (
        select(SalesOrder, Customer.name, Warehouse.code, n_lines)
        .join(Customer, Customer.id == SalesOrder.customer_id)
        .join(Warehouse, Warehouse.id == SalesOrder.warehouse_id)
        .where(SalesOrder.company_id == p.company_id, SalesOrder.warehouse_id.in_(readable(db, p)))
    )
    if status:
        stmt = stmt.where(SalesOrder.status.in_(status.split(",")))
    if warehouse_id:
        stmt = stmt.where(SalesOrder.warehouse_id == warehouse_id)
    if customer_id:
        stmt = stmt.where(SalesOrder.customer_id == customer_id)
    if backorder:
        # open orders with an verified/invoiced attempt and quantity still pending
        picked = select(PickingAttempt.order_id).where(
            PickingAttempt.status.in_(("VERIFIED", "INVOICED"))
        )
        stmt = stmt.where(
            SalesOrder.id.in_(picked),
            SalesOrder.status.in_(("PICKING", "PARTIALLY_FULFILLED", "CONFIRMED")),
        )
    if q:
        like = f"%{esc(q.strip())}%"
        stmt = stmt.where(
            or_(
                SalesOrder.order_no.ilike(like, escape="\\"),
                Customer.name.ilike(like, escape="\\"),
                Customer.external_ref.ilike(like, escape="\\"),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    off = int(cursor or 0)
    rows = db.execute(
        stmt.order_by(SalesOrder.created_at.desc(), SalesOrder.order_no.desc())
        .offset(off)
        .limit(limit)
    ).all()
    items = [
        {
            "id": str(o.id),
            "order_no": o.order_no,
            "customer_name": cname,
            "warehouse_code": wcode,
            "status": o.status,
            "line_count": n,
            "requested_for": o.requested_for.isoformat() if o.requested_for else None,
            "created_at": o.created_at.isoformat(),
        }
        for o, cname, wcode, n in rows
    ]
    nxt = off + len(items)
    return {"items": items, "next_cursor": str(nxt) if nxt < total else None, "total": total}


@router.get("/sales-orders/{order_id}", response_model=OrderOut)
def read_order(order_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    o = get_order(db, p, order_id)
    authorize(request, p, "sales_order.read", o.warehouse_id)
    return svc.order_out(db, o)


# ------------------------------------------------------------------ orders: commands


def insert_order(
    db: Session, p: Principal, body: OrderIn, source_document_id: uuid.UUID | None = None
) -> SalesOrder:
    """Insert a DRAFT order (validated, numbered). Shared by the order form and OCR acceptance."""
    wh = warehouse(db, p, body.warehouse_id)
    svc.get_customer(db, p.company_id, body.customer_id)
    prods = svc.validate_lines(db, p.company_id, body.lines)
    db.get(Company, p.company_id, with_for_update=True)  # serialize numbering
    n = db.scalar(select(func.count()).where(SalesOrder.company_id == p.company_id)) or 0
    o = SalesOrder(
        company_id=p.company_id,
        order_no=f"SO-{svc.now():%Y%m%d}-{n + 1:04d}",
        customer_id=body.customer_id,
        warehouse_id=wh.id,
        source=body.source,
        source_text=body.source_text,
        source_snapshot=[
            {
                "line_no": str(i),
                "sku": pr.sku,
                "unit_code": ln.unit_code,
                "qty": svc.d(ln.qty),
                "text": ln.source_line_text,
            }
            for i, (ln, pr) in enumerate(zip(body.lines, prods, strict=True), start=1)
        ],
        requested_for=body.requested_for,
        note=body.note,
        created_by=p.employee.id,
        source_document_id=source_document_id,
    )
    db.add(o)
    db.flush()
    write_lines(db, o, body)
    db.flush()
    return o


@router.post("/sales-orders", status_code=201, response_model=OrderOut)
def create_order(
    body: OrderIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """New DRAFT. The original request (text + per-line snapshot) is frozen here forever."""
    authorize(request, p, "sales_order.manage", body.warehouse_id)

    def fn() -> Result:
        o = insert_order(db, p, body)
        return svc.order_out(db, o), str(o.id), {"order_no": o.order_no, "lines": len(body.lines)}

    return mutate(db, request, p, key, "sales_order.create", body, 201, "sales_order.create", fn)


@router.put("/sales-orders/{order_id}", response_model=OrderOut)
def update_draft(
    order_id: uuid.UUID,
    body: OrderDraftIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    o0 = get_order(db, p, order_id)
    authorize(request, p, "sales_order.manage", o0.warehouse_id)
    authorize(request, p, "sales_order.manage", body.warehouse_id)

    def fn() -> Result:
        o = get_order(db, p, order_id, lock=True)
        state(o, ("DRAFT",), "Order")
        check_version(o.version, body.version, "Order")
        warehouse(db, p, body.warehouse_id)
        svc.get_customer(db, p.company_id, body.customer_id)
        svc.validate_lines(db, p.company_id, body.lines)
        for ln in svc.order_lines(db, o.id):
            db.delete(ln)
        db.flush()
        o.customer_id, o.warehouse_id = body.customer_id, body.warehouse_id
        o.requested_for, o.note = body.requested_for, body.note
        write_lines(db, o, body)
        bump(o)
        db.flush()
        return svc.order_out(db, o), str(o.id), {"lines": len(body.lines)}

    return mutate(
        db, request, p, key, f"sales_order.edit:{order_id}", body, 200, "sales_order.edit", fn
    )


@router.post("/sales-orders/{order_id}/confirm", response_model=OrderOut)
def confirm_order(
    order_id: uuid.UUID,
    body: VersionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """DRAFT -> CONFIRMED. Prices resolve now (customer > group > standard) and are frozen on
    each line; later price books never change this order. No invoice number is assigned."""
    o0 = get_order(db, p, order_id)
    authorize(request, p, "sales_order.manage", o0.warehouse_id)

    def fn() -> Result:
        o = get_order(db, p, order_id, lock=True)
        state(o, ("DRAFT",), "Order")
        check_version(o.version, body.version, "Order")
        at = svc.now()
        svc.price_lines(db, p.company_id, o, at)
        o.status, o.confirmed_by, o.confirmed_at = "CONFIRMED", p.employee.id, at
        bump(o)
        db.flush()
        return svc.order_out(db, o), str(o.id), {"order_no": o.order_no}

    return mutate(
        db, request, p, key, f"sales_order.confirm:{order_id}", body, 200, "sales_order.confirm", fn
    )


@router.post("/sales-orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(
    order_id: uuid.UUID,
    body: ReasonIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Cancel a whole order that has never been picked. Drafts: sales_order.manage;
    confirmed orders: sales_order.close."""
    o0 = get_order(db, p, order_id)
    perm = "sales_order.manage" if o0.status == "DRAFT" else "sales_order.close"
    authorize(request, p, perm, o0.warehouse_id)

    def fn() -> Result:
        o = get_order(db, p, order_id, lock=True)
        state(o, ("DRAFT", "CONFIRMED"), "Order")
        check_version(o.version, body.version, "Order")
        if o.status != o0.status:
            raise ApiError(409, "INVALID_STATE", "Order changed; reload", current_status=o.status)
        used = db.scalar(
            select(func.count()).where(
                PickingAttempt.order_id == o.id, PickingAttempt.status != "ABORTED"
            )
        )
        if used:
            raise ApiError(409, "ORDER_HAS_PICKS", "Order has been picked; close it instead")
        for q in svc.line_quantities(db, o.id):
            if q.remaining > 0:
                q.line.cancelled_qty += q.remaining
        db.add(
            SalesOrderAmendment(
                order_id=o.id, kind="ORDER_CANCELLED", reason=body.reason, actor_id=p.employee.id
            )
        )
        o.status, o.closed_by, o.closed_at, o.close_reason = (
            "CANCELLED",
            p.employee.id,
            svc.now(),
            body.reason,
        )
        bump(o)
        db.flush()
        return svc.order_out(db, o), str(o.id), {"reason": body.reason}

    return mutate(
        db, request, p, key, f"sales_order.cancel:{order_id}", body, 200, "sales_order.cancel", fn
    )


@router.post("/sales-orders/{order_id}/lines/{line_id}/cancel", response_model=OrderOut)
def cancel_line_qty(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    body: LineCancelIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Cancel part of a line's pending quantity (e.g. customer no longer wants the backorder)."""
    o0 = get_order(db, p, order_id)
    authorize(request, p, "sales_order.close", o0.warehouse_id)

    def fn() -> Result:
        o = get_order(db, p, order_id, lock=True)
        state(o, ("CONFIRMED", "PARTIALLY_FULFILLED"), "Order")
        check_version(o.version, body.version, "Order")
        q = next((x for x in svc.line_quantities(db, o.id) if x.line.id == line_id), None)
        if q is None:
            raise ApiError(404, "NOT_FOUND", "Order line not found")
        qty = inv.check_qty(q.line.unit_code, body.qty)
        if qty > q.pending:
            raise ApiError(
                409,
                "EXCEEDS_PENDING",
                f"Only {svc.d(q.pending)} {q.line.unit_code} is pending on line {q.line.line_no}",
                pending=svc.d(q.pending),
            )
        q.line.cancelled_qty += qty
        db.add(
            SalesOrderAmendment(
                order_id=o.id,
                line_id=q.line.id,
                kind="QTY_CANCELLED",
                qty=qty,
                reason=body.reason,
                actor_id=p.employee.id,
            )
        )
        db.flush()
        svc.refresh_status(db, o)
        bump(o)
        db.flush()
        return svc.order_out(db, o), str(o.id), {"line": q.line.line_no, "qty": svc.d(qty)}

    return mutate(
        db,
        request,
        p,
        key,
        f"sales_order.line_cancel:{order_id}",
        body,
        200,
        "sales_order.line_cancel",
        fn,
    )


@router.post("/sales-orders/{order_id}/close", response_model=OrderOut)
def close_order(
    order_id: uuid.UUID,
    body: ReasonIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Close with shortage: every pending quantity is cancelled with the reason. Refused while
    a pick is in progress or picked goods still await invoicing."""
    o0 = get_order(db, p, order_id)
    authorize(request, p, "sales_order.close", o0.warehouse_id)

    def fn() -> Result:
        o = get_order(db, p, order_id, lock=True)
        state(o, ("CONFIRMED", "PARTIALLY_FULFILLED"), "Order")
        check_version(o.version, body.version, "Order")
        qs = svc.line_quantities(db, o.id)
        if any(q.awaiting > 0 for q in qs):
            raise ApiError(
                409, "AWAITING_INVOICE", "Picked goods are awaiting invoice; invoice first"
            )
        for q in qs:
            if q.pending > 0:
                db.add(
                    SalesOrderAmendment(
                        order_id=o.id,
                        line_id=q.line.id,
                        kind="ORDER_CLOSED",
                        qty=q.pending,
                        reason=body.reason,
                        actor_id=p.employee.id,
                    )
                )
                q.line.cancelled_qty += q.pending
        dispatched = sum((q.line.dispatched_qty for q in qs), svc.ZERO)
        o.status = "CLOSED_WITH_SHORTAGE" if dispatched > 0 else "CANCELLED"
        o.closed_by, o.closed_at, o.close_reason = p.employee.id, svc.now(), body.reason
        bump(o)
        db.flush()
        return svc.order_out(db, o), str(o.id), {"reason": body.reason}

    return mutate(
        db, request, p, key, f"sales_order.close:{order_id}", body, 200, "sales_order.close", fn
    )


# ------------------------------------------------------------------ picking attempts


@router.post("/sales-orders/{order_id}/pick-attempts", status_code=201, response_model=PickOut)
def open_pick_attempt(
    order_id: uuid.UUID,
    body: VersionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Issue a picking sheet for every line's pending quantity. One active sheet per order."""
    o0 = get_order(db, p, order_id)
    authorize(request, p, "sales_order.manage", o0.warehouse_id)

    def fn() -> Result:
        o = get_order(db, p, order_id, lock=True)
        state(o, ("CONFIRMED", "PICKING", "PARTIALLY_FULFILLED"), "Order")
        check_version(o.version, body.version, "Order")
        if (act := svc.active_attempt(db, o.id)) is not None:
            raise ApiError(
                409,
                "OPEN_PICK_EXISTS",
                f"Picking sheet {act.pick_code} is still open",
                attempt_id=str(act.id),
            )
        todo = [q for q in svc.line_quantities(db, o.id) if q.pending > 0]
        if not todo:
            raise ApiError(409, "NOTHING_TO_PICK", "Nothing is pending on this order")
        n = (
            db.scalar(
                select(func.max(PickingAttempt.attempt_no)).where(PickingAttempt.order_id == o.id)
            )
            or 0
        ) + 1
        a = PickingAttempt(
            company_id=p.company_id,
            order_id=o.id,
            warehouse_id=o.warehouse_id,
            attempt_no=n,
            pick_code=f"{o.order_no}-P{n}",
            created_by=p.employee.id,
        )
        db.add(a)
        db.flush()
        for q in todo:
            db.add(PickingLine(attempt_id=a.id, order_line_id=q.line.id, requested_qty=q.pending))
        o.status = "PICKING"
        bump(o)
        db.flush()
        return svc.pick_out(db, a), str(a.id), {"order_no": o.order_no, "pick_code": a.pick_code}

    return mutate(db, request, p, key, f"pick.open:{order_id}", body, 201, "pick.open", fn)


@router.get("/pick-attempts", response_model=list[PickSummaryOut])
def list_attempts(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    status: str | None = Query(None, pattern=r"^[A-Z_]{4,24}(,[A-Z_]{4,24})*$"),
    warehouse_id: uuid.UUID | None = None,
    code: str | None = Query(None, max_length=48),
) -> list[dict[str, Any]]:
    """Warehouse queue. `code` matches the QR/printed pick code exactly."""
    authorize(request, p, "sales_order.read", any_scope=True)
    where: Any = (PickingAttempt.company_id == p.company_id) & PickingAttempt.warehouse_id.in_(
        readable(db, p)
    )
    if status:
        where = where & PickingAttempt.status.in_(status.split(","))
    if warehouse_id:
        where = where & (PickingAttempt.warehouse_id == warehouse_id)
    if code:
        where = where & (PickingAttempt.pick_code == code.strip().upper())
    return svc.attempt_summaries(db, where)


@router.get("/pick-attempts/{pick_id}", response_model=PickOut)
def read_attempt(
    pick_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    a = get_attempt(db, p, pick_id)
    authorize(request, p, "sales_order.read", a.warehouse_id)
    return svc.pick_out(db, a)


def _locked(db: Session, p: Principal, pick_id: uuid.UUID) -> tuple[SalesOrder, PickingAttempt]:
    """Lock order then attempt (same order everywhere -> no deadlocks)."""
    a0 = get_attempt(db, p, pick_id)
    o = get_order(db, p, a0.order_id, lock=True)
    a = db.get(PickingAttempt, pick_id, with_for_update=True)
    assert a is not None
    db.refresh(a)
    return o, a


@router.post("/pick-attempts/{pick_id}/record", response_model=PickOut)
def record_pick(
    pick_id: uuid.UUID,
    body: PickRecordIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Warehouse enters what was actually packed. 0 = `NO` (a shortage, not a cancellation);
    anything below the request needs a shortage reason."""
    a0 = get_attempt(db, p, pick_id)
    authorize(request, p, "pick.record", a0.warehouse_id)

    def fn() -> Result:
        _, a = _locked(db, p, pick_id)
        state(a, ("CREATED", "IN_PROGRESS", "NEEDS_CORRECTION"), "Picking sheet")
        check_version(a.version, body.version, "Picking sheet")
        lines = {
            pl.id: pl
            for pl in db.scalars(select(PickingLine).where(PickingLine.attempt_id == a.id)).all()
        }
        seen: set[uuid.UUID] = set()
        for row in body.lines:
            pl = lines.get(row.line_id)
            if pl is None:
                raise ApiError(422, "INVALID_LINE", "Line is not on this picking sheet")
            if pl.id in seen:
                raise ApiError(422, "DUPLICATE_LINE", "Each line may appear once")
            seen.add(pl.id)
            ol = db.get(SalesOrderLine, pl.order_line_id)
            assert ol is not None
            picked = inv.check_qty(ol.unit_code, row.picked_qty)
            if picked > pl.requested_qty:
                raise ApiError(
                    422,
                    "OVER_PICK",
                    f"Line {ol.line_no}: picked {svc.d(picked)}, "
                    f"only {svc.d(pl.requested_qty)} requested",
                    line=ol.line_no,
                )
            result = svc.outcome(pl.requested_qty, picked)
            if result != "PICKED" and row.shortage_reason is None:
                raise ApiError(
                    422,
                    "SHORTAGE_REASON_REQUIRED",
                    f"Line {ol.line_no}: say why less was packed",
                    line=ol.line_no,
                )
            if row.lot_id is not None:
                svc.check_lot(db, a, ol, row.lot_id)
            pl.picked_qty, pl.outcome = picked, result
            pl.shortage_reason = row.shortage_reason if result != "PICKED" else None
            pl.lot_id, pl.weight_lb, pl.note = row.lot_id, row.weight_lb, row.note
        a.status = "IN_PROGRESS"
        a.version += 1
        db.flush()
        return svc.pick_out(db, a), str(a.id), {"lines": len(body.lines)}

    return mutate(db, request, p, key, f"pick.record:{pick_id}", body, 200, "pick.record", fn)


@router.post("/pick-attempts/{pick_id}/submit", response_model=PickOut)
def submit_pick(
    pick_id: uuid.UUID,
    body: VersionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    a0 = get_attempt(db, p, pick_id)
    authorize(request, p, "pick.record", a0.warehouse_id)

    def fn() -> Result:
        _, a = _locked(db, p, pick_id)
        state(a, ("IN_PROGRESS",), "Picking sheet")
        check_version(a.version, body.version, "Picking sheet")
        missing = db.scalar(
            select(func.count()).where(
                PickingLine.attempt_id == a.id, PickingLine.picked_qty.is_(None)
            )
        )
        if missing:
            raise ApiError(
                422,
                "PICK_INCOMPLETE",
                f"{missing} line(s) have no packed quantity",
                missing=missing,
            )
        a.status, a.picked_by, a.submitted_at = "SUBMITTED", p.employee.id, svc.now()
        a.version += 1
        db.flush()
        return svc.pick_out(db, a), str(a.id), {}

    return mutate(db, request, p, key, f"pick.submit:{pick_id}", body, 200, "pick.submit", fn)


@router.post("/pick-attempts/{pick_id}/verify", response_model=PickOut)
def verify_pick(
    pick_id: uuid.UUID,
    body: PickVerifyIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """SUBMITTED -> VERIFIED. Shortfalls stay pending (BACKORDER) unless the verifier, holding
    sales_order.close, explicitly cancels them with a reason."""
    a0 = get_attempt(db, p, pick_id)
    authorize(request, p, "pick.verify", a0.warehouse_id)
    if "CANCEL" in body.dispositions.values():
        authorize(request, p, "sales_order.close", a0.warehouse_id)
        if len(body.reason.strip()) < 3:
            raise ApiError(422, "REASON_REQUIRED", "Give a reason for cancelling the shortfall")

    def fn() -> Result:
        o, a = _locked(db, p, pick_id)
        state(a, ("SUBMITTED",), "Picking sheet")
        check_version(a.version, body.version, "Picking sheet")
        pls = db.scalars(select(PickingLine).where(PickingLine.attempt_id == a.id)).all()
        by_ol = {pl.order_line_id: pl for pl in pls}
        for ol_id in body.dispositions:
            if ol_id not in by_ol:
                raise ApiError(422, "INVALID_LINE", "Disposition for a line not on this sheet")
        cancelled = 0
        for pl in pls:
            assert pl.picked_qty is not None
            short = pl.requested_qty - pl.picked_qty
            if short <= 0:
                continue
            disp = body.dispositions.get(pl.order_line_id, "BACKORDER")
            pl.disposition = disp
            if disp == "CANCEL":
                ol = db.get(SalesOrderLine, pl.order_line_id)
                assert ol is not None
                ol.cancelled_qty += short
                db.add(
                    SalesOrderAmendment(
                        order_id=o.id,
                        line_id=ol.id,
                        kind="SHORTAGE_CANCELLED",
                        qty=short,
                        reason=body.reason.strip(),
                        pick_attempt_id=a.id,
                        actor_id=p.employee.id,
                    )
                )
                cancelled += 1
        a.status, a.verified_by, a.verified_at = "VERIFIED", p.employee.id, svc.now()
        a.version += 1
        db.flush()
        svc.refresh_status(db, o)
        bump(o)
        db.flush()
        return svc.pick_out(db, a), str(a.id), {"cancelled_lines": cancelled}

    return mutate(db, request, p, key, f"pick.verify:{pick_id}", body, 200, "pick.verify", fn)


@router.post("/pick-attempts/{pick_id}/return", response_model=PickOut)
def return_pick(
    pick_id: uuid.UUID,
    body: ReasonIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Verifier sends a submitted sheet back to the warehouse for correction."""
    a0 = get_attempt(db, p, pick_id)
    authorize(request, p, "pick.verify", a0.warehouse_id)

    def fn() -> Result:
        _, a = _locked(db, p, pick_id)
        state(a, ("SUBMITTED",), "Picking sheet")
        check_version(a.version, body.version, "Picking sheet")
        a.status, a.status_note = "NEEDS_CORRECTION", body.reason
        a.version += 1
        db.flush()
        return svc.pick_out(db, a), str(a.id), {"reason": body.reason}

    return mutate(db, request, p, key, f"pick.return:{pick_id}", body, 200, "pick.return", fn)


@router.post("/pick-attempts/{pick_id}/abort", response_model=PickOut)
def abort_pick(
    pick_id: uuid.UUID,
    body: ReasonIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Void a sheet that was never verified (e.g. lost/reprinted). Its quantities return to
    pending; the record is kept."""
    a0 = get_attempt(db, p, pick_id)
    authorize(request, p, "sales_order.manage", a0.warehouse_id)

    def fn() -> Result:
        o, a = _locked(db, p, pick_id)
        state(a, ("CREATED", "IN_PROGRESS", "NEEDS_CORRECTION"), "Picking sheet")
        check_version(a.version, body.version, "Picking sheet")
        a.status, a.status_note = "ABORTED", body.reason
        a.version += 1
        db.flush()
        svc.refresh_status(db, o)
        bump(o)
        db.flush()
        return svc.pick_out(db, a), str(a.id), {"reason": body.reason}

    return mutate(db, request, p, key, f"pick.abort:{pick_id}", body, 200, "pick.abort", fn)
