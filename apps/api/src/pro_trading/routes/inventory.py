"""Module 03 inventory API. Every command: authorize against the target warehouse, claim the
Idempotency-Key, post through `inventory.post()` (locks + exactly-once posting keys), audit,
commit — all in one transaction. Reads are filtered to warehouses the caller may read."""

import asyncio
import json
import threading
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, aliased

from .. import idempotency
from .. import inventory as inv
from ..config import get_settings
from ..db import get_sessionmaker
from ..deps import DbDep, Principal, PrincipalDep, authorize
from ..errors import ApiError
from ..models import (
    AuthSession,
    Company,
    Employee,
    InventoryBalance,
    InventoryLot,
    Product,
    StockCount,
    StockCountLine,
    StockMovement,
    StockReservation,
    Warehouse,
)
from ..schemas import (
    AdjustmentIn,
    AvailabilityOut,
    BalancePage,
    CountDecisionIn,
    CountIn,
    CountOut,
    DamageIn,
    LotOut,
    LotStatusIn,
    MovementPage,
    OpeningBalanceIn,
    PostingOut,
    ReconciliationOut,
    ReservationIn,
    ReservationOut,
    ReturnIn,
    ReverseIn,
)
from ._common import esc, mutate

router = APIRouter(prefix="/inventory", tags=["inventory"])


def UTC_NOW() -> datetime:  # noqa: N802
    return datetime.now(UTC)


# ------------------------------------------------------------------ helpers


def readable_warehouses(db: Session, p: Principal, perm: str = "inventory.read") -> set[uuid.UUID]:
    ids = db.scalars(select(Warehouse.id).where(Warehouse.company_id == p.company_id)).all()
    return {w for w in ids if p.can(perm, w)}


def get_lot(db: Session, p: Principal, lot_id: uuid.UUID, lock: bool = False) -> InventoryLot:
    lot = db.get(InventoryLot, lot_id, with_for_update=lock)
    if lot is None or lot.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Lot not found")
    return lot


def get_warehouse(db: Session, p: Principal, wid: uuid.UUID) -> Warehouse:
    wh = db.get(Warehouse, wid)
    if wh is None or wh.company_id != p.company_id:
        raise ApiError(422, "INVALID_WAREHOUSE", "Warehouse not found")
    if not wh.active:
        raise ApiError(409, "WAREHOUSE_INACTIVE", f"Warehouse {wh.code} is inactive")
    return wh


def _product(db: Session, lot: InventoryLot) -> Product:
    prod = db.get(Product, lot.product_id)
    assert prod is not None
    return prod


def _d(v: Decimal) -> str:
    return str(inv.q4(v))


def movements_out(db: Session, ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    if not ids:
        return []
    rev = aliased(StockMovement)
    rows = db.execute(_movement_select(rev).where(StockMovement.id.in_(ids))).all()
    by_id = {r[0].id: _movement_row(r) for r in rows}
    return [by_id[i] for i in ids]


def _movement_select(rev: Any) -> Select[Any]:
    return (
        select(
            StockMovement,
            InventoryLot.lot_code,
            Product.sku,
            Warehouse.code,
            Employee.display_name,
            rev.id,
        )
        .join(InventoryLot, InventoryLot.id == StockMovement.lot_id)
        .join(Product, Product.id == StockMovement.product_id)
        .join(Warehouse, Warehouse.id == StockMovement.warehouse_id)
        .outerjoin(Employee, Employee.id == StockMovement.actor_id)
        .outerjoin(rev, rev.reverses_movement_id == StockMovement.id)
    )


def _movement_row(r: Any) -> dict[str, Any]:
    m, lot_code, sku, wh_code, actor, rev_id = r
    return {
        "id": str(m.id),
        "lot_id": str(m.lot_id),
        "lot_code": lot_code,
        "product_id": str(m.product_id),
        "sku": sku,
        "warehouse_id": str(m.warehouse_id),
        "warehouse_code": wh_code,
        "unit_code": m.unit_code,
        "delta_qty": _d(m.delta_qty),
        "qty_after": _d(m.qty_after),
        "movement_type": m.movement_type,
        "reason_code": m.reason_code,
        "note": m.note,
        "source_type": m.source_type,
        "source_id": str(m.source_id),
        "reverses_movement_id": str(m.reverses_movement_id) if m.reverses_movement_id else None,
        "reversed_by_id": str(rev_id) if rev_id else None,
        "actor_name": actor,
        "created_at": m.created_at.isoformat(),
    }


def posting_out(
    db: Session,
    source_type: str,
    source_id: uuid.UUID,
    mvs: list[StockMovement],
    lot_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "source_id": str(source_id),
        "lot_id": str(lot_id) if lot_id else None,
        "movements": movements_out(db, [m.id for m in mvs]),
    }


def _balance_row(
    b: InventoryBalance, lot: InventoryLot, prod: Product, wh: Warehouse
) -> dict[str, Any]:
    sellable = inv.is_sellable(lot)
    return {
        "lot_id": str(lot.id),
        "lot_code": lot.lot_code,
        "lot_status": lot.status,
        "expired": inv.is_expired(lot),
        "batch_ref": lot.batch_ref,
        "received_at": lot.received_at.isoformat() if lot.received_at else None,
        "expires_at": lot.expires_at.isoformat() if lot.expires_at else None,
        "product_id": str(prod.id),
        "sku": prod.sku,
        "product_name": prod.name,
        "warehouse_id": str(wh.id),
        "warehouse_code": wh.code,
        "unit_code": b.unit_code,
        "qty": _d(b.qty),
        "reserved_qty": _d(b.reserved_qty),
        "available_qty": _d(b.qty - b.reserved_qty) if sellable else "0.0000",
        "version": b.version,
    }


def _balance_select() -> Any:
    return (
        select(InventoryBalance, InventoryLot, Product, Warehouse)
        .join(InventoryLot, InventoryLot.id == InventoryBalance.lot_id)
        .join(Product, Product.id == InventoryBalance.product_id)
        .join(Warehouse, Warehouse.id == InventoryBalance.warehouse_id)
    )


def lot_out(db: Session, lot: InventoryLot) -> dict[str, Any]:
    prod = _product(db, lot)
    wh = db.get(Warehouse, lot.warehouse_id)
    assert wh is not None
    comp = db.scalar(select(InventoryLot.id).where(InventoryLot.parent_lot_id == lot.id))
    bals = db.execute(
        _balance_select()
        .where(InventoryBalance.lot_id == lot.id)
        .order_by(InventoryBalance.unit_code)
    ).all()
    return {
        "id": str(lot.id),
        "lot_code": lot.lot_code,
        "status": lot.status,
        "expired": inv.is_expired(lot),
        "product_id": str(prod.id),
        "sku": prod.sku,
        "product_name": prod.name,
        "warehouse_id": str(wh.id),
        "warehouse_code": wh.code,
        "batch_ref": lot.batch_ref,
        "received_at": lot.received_at.isoformat() if lot.received_at else None,
        "expires_at": lot.expires_at.isoformat() if lot.expires_at else None,
        "parent_lot_id": str(lot.parent_lot_id) if lot.parent_lot_id else None,
        "damaged_lot_id": str(comp) if comp else None,
        "landed_unit_cost": _d(lot.landed_unit_cost) if lot.landed_unit_cost is not None else None,
        "cost_currency": lot.cost_currency,
        "version": lot.version,
        "balances": [_balance_row(*r) for r in bals],
    }


def pkey(p: Principal, kind: str, key: str, n: int | str = 0) -> str:
    return f"{kind}:{p.company_id}:{key}:{n}"


# ------------------------------------------------------------------ reads


@router.get("/stock", response_model=BalancePage)
def list_stock(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    warehouse_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    lot_status: str | None = Query(None, pattern=r"^(SALEABLE|DAMAGED|QUARANTINE|EXPIRED)$"),
    q: str | None = Query(None, max_length=100),
    include_zero: bool = False,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, pattern=r"^\d{1,7}$", description="Opaque offset"),
) -> dict[str, Any]:
    authorize(request, p, "inventory.read", any_scope=True)
    whs = readable_warehouses(db, p)
    stmt = _balance_select().where(
        InventoryBalance.company_id == p.company_id, InventoryBalance.warehouse_id.in_(whs)
    )
    if warehouse_id:
        stmt = stmt.where(InventoryBalance.warehouse_id == warehouse_id)
    if product_id:
        stmt = stmt.where(InventoryBalance.product_id == product_id)
    if lot_status:
        stmt = stmt.where(InventoryLot.status == lot_status)
    if not include_zero:
        stmt = stmt.where(InventoryBalance.qty > 0)
    if q:
        like = f"%{esc(q.strip())}%"
        stmt = stmt.where(
            or_(
                Product.sku.ilike(like, escape="\\"),
                Product.name.ilike(like, escape="\\"),
                InventoryLot.lot_code.ilike(like, escape="\\"),
                InventoryLot.batch_ref.ilike(like, escape="\\"),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    off = int(cursor or 0)
    rows = db.execute(
        stmt.order_by(
            Product.sku,
            Warehouse.code,
            InventoryLot.expires_at.asc().nulls_last(),
            InventoryLot.lot_code,
            InventoryBalance.unit_code,
        )
        .offset(off)
        .limit(limit + 1)
    ).all()
    more = len(rows) > limit
    return {
        "items": [_balance_row(*r) for r in rows[:limit]],
        "next_cursor": str(off + limit) if more else None,
        "total": total,
    }


@router.get("/availability", response_model=list[AvailabilityOut])
def availability(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    product_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Per warehouse+unit: available = on-hand - reserved in saleable, unexpired lots."""
    authorize(request, p, "inventory.read", any_scope=True)
    whs = readable_warehouses(db, p)
    stmt = _balance_select().where(
        InventoryBalance.company_id == p.company_id,
        InventoryBalance.product_id == product_id,
        InventoryBalance.warehouse_id.in_(whs),
    )
    if warehouse_id:
        stmt = stmt.where(InventoryBalance.warehouse_id == warehouse_id)
    agg: dict[tuple[uuid.UUID, str], dict[str, Any]] = {}
    for b, lot, _prod, wh in db.execute(stmt).all():
        a = agg.setdefault(
            (wh.id, b.unit_code),
            {
                "product_id": str(product_id),
                "warehouse_id": str(wh.id),
                "warehouse_code": wh.code,
                "unit_code": b.unit_code,
                "on_hand": Decimal(0),
                "reserved": Decimal(0),
                "available": Decimal(0),
                "unavailable": Decimal(0),
            },
        )
        a["on_hand"] += b.qty
        a["reserved"] += b.reserved_qty
        if inv.is_sellable(lot):
            a["available"] += b.qty - b.reserved_qty
        else:
            a["unavailable"] += b.qty
    out = sorted(agg.values(), key=lambda a: (a["warehouse_code"], a["unit_code"]))
    for a in out:
        for k in ("on_hand", "reserved", "available", "unavailable"):
            a[k] = _d(a[k])
    return out


@router.get("/movements", response_model=MovementPage)
def list_movements(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    warehouse_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    lot_id: uuid.UUID | None = None,
    movement_type: str | None = Query(None, pattern=r"^[A-Z_]{3,24}$"),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, pattern=r"^\d{1,7}$", description="Opaque offset"),
) -> dict[str, Any]:
    authorize(request, p, "inventory.read", any_scope=True)
    whs = readable_warehouses(db, p)
    rev = aliased(StockMovement)
    stmt = _movement_select(rev).where(
        StockMovement.company_id == p.company_id, StockMovement.warehouse_id.in_(whs)
    )
    for col, val in [
        (StockMovement.warehouse_id, warehouse_id),
        (StockMovement.product_id, product_id),
        (StockMovement.lot_id, lot_id),
        (StockMovement.movement_type, movement_type),
    ]:
        if val is not None:
            stmt = stmt.where(col == val)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    off = int(cursor or 0)
    rows = db.execute(
        stmt.order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .offset(off)
        .limit(limit + 1)
    ).all()
    more = len(rows) > limit
    return {
        "items": [_movement_row(r) for r in rows[:limit]],
        "next_cursor": str(off + limit) if more else None,
        "total": total,
    }


@router.get("/lots/{lot_id}", response_model=LotOut)
def read_lot(lot_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    lot = get_lot(db, p, lot_id)
    authorize(request, p, "inventory.read", lot.warehouse_id)
    return lot_out(db, lot)


@router.get("/reservations", response_model=list[ReservationOut])
def list_reservations(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    lot_id: uuid.UUID | None = None,
    status: str | None = Query("ACTIVE", pattern=r"^(ACTIVE|RELEASED|CONSUMED)$"),
) -> list[StockReservation]:
    authorize(request, p, "inventory.read", any_scope=True)
    whs = readable_warehouses(db, p)
    stmt = (
        select(StockReservation)
        .join(InventoryLot, InventoryLot.id == StockReservation.lot_id)
        .where(StockReservation.company_id == p.company_id, InventoryLot.warehouse_id.in_(whs))
    )
    if lot_id:
        stmt = stmt.where(StockReservation.lot_id == lot_id)
    if status:
        stmt = stmt.where(StockReservation.status == status)
    return list(db.scalars(stmt.order_by(StockReservation.created_at.desc()).limit(200)).all())


# ------------------------------------------------------------------ commands


@router.post("/opening-balances", status_code=201, response_model=PostingOut)
def opening_balance(
    body: OpeningBalanceIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    authorize(request, p, "inventory.adjust", body.warehouse_id)

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        wh = get_warehouse(db, p, body.warehouse_id)
        prod = db.get(Product, body.product_id)
        if prod is None or prod.company_id != p.company_id:
            raise ApiError(422, "INVALID_PRODUCT", "Product not found")
        if not prod.active:
            raise ApiError(409, "PRODUCT_INACTIVE", f"{prod.sku} is inactive")
        if prod.tracks_expiry and body.expires_at is None:
            raise ApiError(422, "EXPIRY_REQUIRED", f"{prod.sku} requires an expiry date")
        units = [ln.unit_code for ln in body.lines]
        if len(set(units)) != len(units):
            raise ApiError(422, "DUPLICATE_UNIT", "Each unit may appear once per lot")
        for ln in body.lines:
            inv.check_unit(db, prod, ln.unit_code)
            inv.check_qty(ln.unit_code, ln.qty)
        code = body.lot_code or f"{prod.sku}-{UTC_NOW():%y%m%d}-{uuid.uuid4().hex[:4].upper()}"
        if db.scalar(
            select(InventoryLot.id).where(
                InventoryLot.company_id == p.company_id, InventoryLot.lot_code == code
            )
        ):
            raise ApiError(409, "DUPLICATE", f"Lot code {code} already exists")
        lot = InventoryLot(
            company_id=p.company_id,
            product_id=prod.id,
            warehouse_id=wh.id,
            lot_code=code,
            batch_ref=body.batch_ref,
            received_at=body.received_at or UTC_NOW(),
            expires_at=body.expires_at,
            landed_unit_cost=body.landed_unit_cost,
            cost_currency=body.cost_currency
            or ("CAD" if body.landed_unit_cost is not None else None),
            created_by=p.employee.id,
        )
        db.add(lot)
        db.flush()
        mvs = inv.post(
            db,
            p.employee.id,
            [
                inv.Posting(
                    lot=lot,
                    unit_code=ln.unit_code,
                    delta=ln.qty,
                    movement_type="OPENING_BALANCE",
                    posting_key=f"opening:{lot.id}:{ln.unit_code}",
                    source_type="OPENING_BALANCE",
                    source_id=lot.id,
                    note=body.note,
                )
                for ln in body.lines
            ],
        )
        out = posting_out(db, "OPENING_BALANCE", lot.id, mvs, lot.id)
        return (
            out,
            str(lot.id),
            {
                "lot_code": code,
                "sku": prod.sku,
                "lines": [{"unit": ln.unit_code, "qty": str(ln.qty)} for ln in body.lines],
            },
        )

    return mutate(
        db,
        request,
        p,
        key,
        "inventory.opening",
        body,
        201,
        "inventory.opening_balance",
        fn,
        "Lot code already exists",
    )


@router.post("/adjustments", status_code=201, response_model=PostingOut)
def adjust(
    body: AdjustmentIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    lot = get_lot(db, p, body.lot_id)
    authorize(request, p, "inventory.adjust", lot.warehouse_id)

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        inv.check_unit(db, _product(db, lot), body.unit_code)
        src = uuid.uuid4()
        mvs = inv.post(
            db,
            p.employee.id,
            [
                inv.Posting(
                    lot=lot,
                    unit_code=body.unit_code,
                    delta=body.delta_qty,
                    movement_type="ADJUSTMENT",
                    posting_key=pkey(p, "adjust", key),
                    source_type="ADJUSTMENT",
                    source_id=src,
                    reason_code=body.reason_code,
                    note=body.note,
                )
            ],
        )
        return (
            posting_out(db, "ADJUSTMENT", src, mvs, lot.id),
            str(lot.id),
            body.model_dump(mode="json"),
        )

    return mutate(db, request, p, key, "inventory.adjust", body, 201, "inventory.adjust", fn)


@router.post("/damage", status_code=201, response_model=PostingOut)
def damage(
    body: DamageIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """Move damaged stock out of a saleable lot into its DAMAGED companion lot (two rows)."""
    lot = get_lot(db, p, body.lot_id)
    authorize(request, p, "inventory.adjust", lot.warehouse_id)

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        if lot.status == "DAMAGED":
            raise ApiError(
                409, "INVALID_STATE", "Lot is already damaged stock", current_status="DAMAGED"
            )
        comp = inv.damaged_companion(db, lot, p.employee.id)
        src = uuid.uuid4()
        mvs = inv.post(
            db,
            p.employee.id,
            [
                inv.Posting(
                    lot=lt,
                    unit_code=body.unit_code,
                    delta=d,
                    movement_type="DAMAGE",
                    posting_key=pkey(p, "damage", key, leg),
                    source_type="DAMAGE",
                    source_id=src,
                    reason_code="DAMAGED",
                    note=body.note,
                )
                for lt, d, leg in [(lot, -body.qty, "out"), (comp, body.qty, "in")]
            ],
        )
        return (
            posting_out(db, "DAMAGE", src, mvs, comp.id),
            str(lot.id),
            body.model_dump(mode="json"),
        )

    return mutate(db, request, p, key, "inventory.damage", body, 201, "inventory.damage", fn)


@router.post("/returns", status_code=201, response_model=PostingOut)
def customer_return(
    body: ReturnIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """Goods back from a customer: saleable -> original lot, damaged -> DAMAGED companion."""
    lot = get_lot(db, p, body.lot_id)
    authorize(request, p, "inventory.adjust", lot.warehouse_id)

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        inv.check_unit(db, _product(db, lot), body.unit_code)
        target = (
            inv.damaged_companion(db, lot, p.employee.id) if body.condition == "DAMAGED" else lot
        )
        src = uuid.uuid4()
        mvs = inv.post(
            db,
            p.employee.id,
            [
                inv.Posting(
                    lot=target,
                    unit_code=body.unit_code,
                    delta=body.qty,
                    movement_type="RETURN",
                    posting_key=pkey(p, "return", key),
                    source_type="RETURN",
                    source_id=src,
                    reason_code=body.condition,
                    note=f"{body.source_ref}: {body.note}".strip(": "),
                )
            ],
        )
        return (
            posting_out(db, "RETURN", src, mvs, target.id),
            str(target.id),
            body.model_dump(mode="json"),
        )

    return mutate(db, request, p, key, "inventory.return", body, 201, "inventory.return", fn)


@router.post("/movements/{movement_id}/reverse", status_code=201, response_model=PostingOut)
def reverse(
    movement_id: uuid.UUID,
    body: ReverseIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Compensating REVERSAL rows for the whole posting (all rows sharing its source)."""
    mv = db.get(StockMovement, movement_id)
    if mv is None or mv.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Movement not found")
    authorize(request, p, "inventory.reverse", mv.warehouse_id)

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        if mv.movement_type == "REVERSAL":
            raise ApiError(409, "NOT_REVERSIBLE", "A reversal cannot itself be reversed")
        if mv.movement_type not in inv.REVERSIBLE:
            raise ApiError(
                409,
                "SOURCE_DOCUMENT_REQUIRED",
                f"{mv.movement_type} is corrected through its source document",
            )
        group = db.scalars(
            select(StockMovement)
            .where(
                StockMovement.company_id == p.company_id,
                StockMovement.source_type == mv.source_type,
                StockMovement.source_id == mv.source_id,
                StockMovement.movement_type != "REVERSAL",
            )
            .order_by(StockMovement.created_at, StockMovement.id)
        ).all()
        for g in group:
            authorize(request, p, "inventory.reverse", g.warehouse_id)
        done = db.scalar(
            select(StockMovement.id).where(
                StockMovement.reverses_movement_id.in_([g.id for g in group])
            )
        )
        if done:
            raise ApiError(409, "ALREADY_REVERSED", "This posting was already reversed")
        src = uuid.uuid4()
        mvs = inv.post(
            db,
            p.employee.id,
            [
                inv.Posting(
                    lot=get_lot(db, p, g.lot_id),
                    unit_code=g.unit_code,
                    delta=-g.delta_qty,
                    movement_type="REVERSAL",
                    posting_key=f"reverse:{g.id}",
                    source_type="REVERSAL",
                    source_id=src,
                    source_line_id=g.id,
                    reason_code=g.movement_type,
                    note=body.reason,
                    reverses_movement_id=g.id,
                )
                for g in group
            ],
        )
        return (
            posting_out(db, "REVERSAL", src, mvs),
            str(mv.id),
            {
                "reason": body.reason,
                "reversed": [str(g.id) for g in group],
            },
        )

    return mutate(
        db,
        request,
        p,
        key,
        f"inventory.reverse:{movement_id}",
        body,
        201,
        "inventory.reverse",
        fn,
        "This posting was already reversed",
    )


@router.post("/lots/{lot_id}/status", response_model=LotOut)
def set_lot_status(
    lot_id: uuid.UUID,
    body: LotStatusIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    lot = get_lot(db, p, lot_id)
    authorize(request, p, "inventory.adjust", lot.warehouse_id)

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        locked = get_lot(db, p, lot_id, lock=True)
        if locked.status == "DAMAGED":
            raise ApiError(
                409,
                "INVALID_STATE",
                "Damaged stock lots cannot change status",
                current_status="DAMAGED",
            )
        if locked.version != body.version:
            raise ApiError(
                409, "VERSION_CONFLICT", "Lot changed; reload", current_version=locked.version
            )
        if body.status != "SALEABLE":
            # Lock the balance rows (same order as inventory.lock_balances) so a reservation
            # or invoice running now either commits first and is seen here, or waits and then
            # sees the new status.
            bals = db.scalars(
                select(InventoryBalance)
                .where(InventoryBalance.lot_id == lot_id)
                .order_by(InventoryBalance.unit_code)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            reserved = sum((b.reserved_qty for b in bals), Decimal(0))
            if reserved:
                raise ApiError(409, "LOT_RESERVED", "Release reservations on this lot first")
        before = locked.status
        locked.status = body.status
        locked.version += 1
        db.flush()
        return (
            lot_out(db, locked),
            str(lot_id),
            {"from": before, "to": body.status, "reason": body.reason},
        )

    return mutate(
        db, request, p, key, f"inventory.lot_status:{lot_id}", body, 200, "inventory.lot_status", fn
    )


@router.post("/reservations", status_code=201, response_model=ReservationOut)
def create_reservation(
    body: ReservationIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    lot = get_lot(db, p, body.lot_id)
    authorize(request, p, "inventory.reserve", lot.warehouse_id)

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        inv.check_unit(db, _product(db, lot), body.unit_code)
        r = inv.reserve(
            db,
            lot,
            body.unit_code,
            body.qty,
            source_type=body.source_type,
            source_ref=body.source_ref,
            posting_key=pkey(p, "reserve", key),
            actor_id=p.employee.id,
        )
        out = ReservationOut.model_validate(r).model_dump(mode="json")
        return out, str(r.id), body.model_dump(mode="json")

    return mutate(db, request, p, key, "inventory.reserve", body, 201, "inventory.reserve", fn)


@router.post("/reservations/{reservation_id}/release", response_model=ReservationOut)
def release_reservation(
    reservation_id: uuid.UUID,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    r0 = db.get(StockReservation, reservation_id)
    if r0 is None or r0.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Reservation not found")
    lot = get_lot(db, p, r0.lot_id)
    authorize(request, p, "inventory.reserve", lot.warehouse_id)
    body = ReverseIn.model_construct(reason="release")

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        r = db.get(StockReservation, reservation_id, with_for_update=True, populate_existing=True)
        assert r is not None
        inv.release(db, r, p.employee.id)
        return ReservationOut.model_validate(r).model_dump(mode="json"), str(r.id), {}

    return mutate(
        db,
        request,
        p,
        key,
        f"inventory.release:{reservation_id}",
        body,
        200,
        "inventory.release",
        fn,
    )


# ------------------------------------------------------------------ counts


def count_out(db: Session, c: StockCount) -> dict[str, Any]:
    wh = db.get(Warehouse, c.warehouse_id)
    assert wh is not None
    names = dict(
        db.execute(
            select(Employee.id, Employee.display_name).where(
                Employee.id.in_([i for i in (c.counted_by, c.decided_by) if i])
            )
        ).all()
    )
    rows = db.execute(
        select(StockCountLine, InventoryLot.lot_code, Product.sku, InventoryBalance)
        .join(InventoryLot, InventoryLot.id == StockCountLine.lot_id)
        .join(Product, Product.id == InventoryLot.product_id)
        .outerjoin(
            InventoryBalance,
            (InventoryBalance.lot_id == StockCountLine.lot_id)
            & (InventoryBalance.unit_code == StockCountLine.unit_code),
        )
        .where(StockCountLine.count_id == c.id)
        .order_by(Product.sku, InventoryLot.lot_code, StockCountLine.unit_code)
    ).all()
    lines = []
    for ln, lot_code, sku, bal in rows:
        lines.append(
            {
                "lot_id": str(ln.lot_id),
                "lot_code": lot_code,
                "sku": sku,
                "unit_code": ln.unit_code,
                "expected_qty": _d(ln.expected_qty),
                "counted_qty": _d(ln.counted_qty),
                "variance": _d(ln.counted_qty - ln.expected_qty),
                "current_qty": _d(bal.qty) if bal else None,
                "stale": c.status == "SUBMITTED"
                and (bal.version if bal else 0) != ln.expected_version,
                "movement_id": str(ln.movement_id) if ln.movement_id else None,
            }
        )
    return {
        "id": str(c.id),
        "count_no": c.count_no,
        "warehouse_id": str(wh.id),
        "warehouse_code": wh.code,
        "status": c.status,
        "note": c.note,
        "counted_by_name": names.get(c.counted_by) if c.counted_by else None,
        "decided_by_name": names.get(c.decided_by) if c.decided_by else None,
        "created_at": c.created_at.isoformat(),
        "decided_at": c.decided_at.isoformat() if c.decided_at else None,
        "version": c.version,
        "lines": lines,
    }


def get_count(db: Session, p: Principal, cid: uuid.UUID, lock: bool = False) -> StockCount:
    c = db.get(StockCount, cid, with_for_update=lock, populate_existing=lock)
    if c is None or c.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Count not found")
    return c


@router.get("/counts", response_model=list[CountOut])
def list_counts(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    status: str | None = Query(None, pattern=r"^(SUBMITTED|POSTED|REJECTED)$"),
    warehouse_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    authorize(request, p, "inventory.read", any_scope=True)
    stmt = select(StockCount).where(
        StockCount.company_id == p.company_id,
        StockCount.warehouse_id.in_(readable_warehouses(db, p)),
    )
    if status:
        stmt = stmt.where(StockCount.status == status)
    if warehouse_id:
        stmt = stmt.where(StockCount.warehouse_id == warehouse_id)
    return [
        count_out(db, c) for c in db.scalars(stmt.order_by(StockCount.created_at.desc()).limit(50))
    ]


@router.get("/counts/{count_id}", response_model=CountOut)
def read_count(count_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    c = get_count(db, p, count_id)
    authorize(request, p, "inventory.read", c.warehouse_id)
    return count_out(db, c)


@router.post("/counts", status_code=201, response_model=CountOut)
def submit_count(
    body: CountIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """Snapshot each counted balance (qty + version). Nothing posts until a manager posts it."""
    authorize(request, p, "inventory.count", body.warehouse_id)

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        wh = get_warehouse(db, p, body.warehouse_id)
        keys = [(ln.lot_id, ln.unit_code) for ln in body.lines]
        if len(set(keys)) != len(keys):
            raise ApiError(422, "DUPLICATE_LINE", "Each lot/unit may be counted once per count")
        db.get(Company, p.company_id, with_for_update=True)  # serialize count numbering
        n = db.scalar(select(func.count()).where(StockCount.company_id == p.company_id)) or 0
        c = StockCount(
            company_id=p.company_id,
            warehouse_id=wh.id,
            count_no=f"CNT-{UTC_NOW():%Y%m%d}-{n + 1:04d}",
            note=body.note,
            counted_by=p.employee.id,
        )
        db.add(c)
        db.flush()
        for ln in body.lines:
            lot = get_lot(db, p, ln.lot_id)
            if lot.warehouse_id != wh.id:
                raise ApiError(422, "WRONG_WAREHOUSE", f"Lot {lot.lot_code} is not in {wh.code}")
            inv.check_qty(ln.unit_code, ln.counted_qty)
            bal = db.get(InventoryBalance, (lot.id, ln.unit_code))
            if bal is None:
                inv.check_unit(db, _product(db, lot), ln.unit_code)
            db.add(
                StockCountLine(
                    count_id=c.id,
                    lot_id=lot.id,
                    unit_code=ln.unit_code,
                    expected_qty=bal.qty if bal else Decimal(0),
                    expected_version=bal.version if bal else 0,
                    counted_qty=ln.counted_qty,
                )
            )
        db.flush()
        return count_out(db, c), str(c.id), {"count_no": c.count_no, "lines": len(body.lines)}

    return mutate(db, request, p, key, "inventory.count", body, 201, "inventory.count_submit", fn)


@router.post("/counts/{count_id}/post", response_model=CountOut)
def post_count(
    count_id: uuid.UUID,
    body: CountDecisionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Post variances as COUNT_ADJUSTMENT rows. If any balance moved since it was counted the
    whole count is refused (STALE_COUNT) — recount rather than overwrite newer stock."""
    c0 = get_count(db, p, count_id)
    authorize(request, p, "inventory.adjust", c0.warehouse_id)

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        c = get_count(db, p, count_id, lock=True)
        if c.status != "SUBMITTED":
            raise ApiError(409, "INVALID_STATE", f"Count is {c.status}", current_status=c.status)
        if c.version != body.version:
            raise ApiError(
                409, "VERSION_CONFLICT", "Count changed; reload", current_version=c.version
            )
        lines = db.scalars(select(StockCountLine).where(StockCountLine.count_id == c.id)).all()
        lots = {ln.lot_id: get_lot(db, p, ln.lot_id) for ln in lines}
        for ln in lines:
            if ln.counted_qty > 0:
                inv._ensure_balance(db, lots[ln.lot_id], ln.unit_code)
        bals = inv.lock_balances(db, [(ln.lot_id, ln.unit_code) for ln in lines])
        stale = [
            lots[ln.lot_id].lot_code
            for ln in lines
            if (b := bals.get((ln.lot_id, ln.unit_code))) is not None
            and b.version != ln.expected_version
        ]
        if stale:
            raise ApiError(
                409, "STALE_COUNT", "Stock moved after counting; recount these lots", lots=stale
            )
        below = [
            lots[ln.lot_id].lot_code
            for ln in lines
            if (b := bals.get((ln.lot_id, ln.unit_code))) is not None
            and ln.counted_qty < b.reserved_qty
        ]
        if below:
            raise ApiError(
                409, "COUNT_BELOW_RESERVED", "Counted less than reserved; release first", lots=below
            )
        postings = []
        for ln in lines:
            b = bals.get((ln.lot_id, ln.unit_code))
            delta = ln.counted_qty - (b.qty if b else Decimal(0))
            if delta != 0:
                postings.append(
                    (
                        ln,
                        inv.Posting(
                            lot=lots[ln.lot_id],
                            unit_code=ln.unit_code,
                            delta=delta,
                            movement_type="COUNT_ADJUSTMENT",
                            posting_key=f"count:{ln.id}",
                            source_type="STOCK_COUNT",
                            source_id=c.id,
                            source_line_id=ln.id,
                            reason_code="COUNT",
                            note=c.count_no,
                        ),
                    )
                )
        mvs = inv.post(db, p.employee.id, [ps for _, ps in postings])
        for (ln, _), mv in zip(postings, mvs, strict=True):
            ln.movement_id = mv.id
        c.status, c.decided_by, c.decided_at = "POSTED", p.employee.id, UTC_NOW()
        c.version += 1
        db.flush()
        return count_out(db, c), str(c.id), {"count_no": c.count_no, "movements": len(mvs)}

    return mutate(
        db,
        request,
        p,
        key,
        f"inventory.count_post:{count_id}",
        body,
        200,
        "inventory.count_post",
        fn,
    )


@router.post("/counts/{count_id}/reject", response_model=CountOut)
def reject_count(
    count_id: uuid.UUID,
    body: CountDecisionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    c0 = get_count(db, p, count_id)
    authorize(request, p, "inventory.adjust", c0.warehouse_id)

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        c = get_count(db, p, count_id, lock=True)
        if c.status != "SUBMITTED":
            raise ApiError(409, "INVALID_STATE", f"Count is {c.status}", current_status=c.status)
        if c.version != body.version:
            raise ApiError(
                409, "VERSION_CONFLICT", "Count changed; reload", current_version=c.version
            )
        c.status, c.decided_by, c.decided_at = "REJECTED", p.employee.id, UTC_NOW()
        c.version += 1
        db.flush()
        return count_out(db, c), str(c.id), {"count_no": c.count_no, "reason": body.reason}

    return mutate(
        db,
        request,
        p,
        key,
        f"inventory.count_reject:{count_id}",
        body,
        200,
        "inventory.count_reject",
        fn,
    )


# ------------------------------------------------------------------ reconciliation + live


def _recon_out(
    db: Session, checked: int, rows: int, found: list[inv.Discrepancy], rebuilt: bool
) -> dict[str, Any]:
    codes = (
        dict(
            db.execute(
                select(InventoryLot.id, InventoryLot.lot_code).where(
                    InventoryLot.id.in_([d.lot_id for d in found])
                )
            ).all()
        )
        if found
        else {}
    )
    return {
        "checked": checked,
        "ledger_rows": rows,
        "rebuilt": rebuilt,
        "discrepancies": [
            {
                "lot_id": str(d.lot_id),
                "lot_code": codes.get(d.lot_id, ""),
                "unit_code": d.unit_code,
                "cached_qty": _d(d.cached_qty) if d.cached_qty is not None else None,
                "ledger_qty": _d(d.ledger_qty),
                "cached_reserved": _d(d.cached_reserved) if d.cached_reserved is not None else None,
                "active_reserved": _d(d.active_reserved),
            }
            for d in found
        ],
    }


@router.get("/reconciliation", response_model=ReconciliationOut)
def reconciliation(request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    authorize(request, p, "inventory.read", any_scope=True)
    checked, rows, found = inv.reconcile(db, p.company_id, readable_warehouses(db, p))
    return _recon_out(db, checked, rows, found, False)


@router.post("/reconciliation/rebuild", response_model=ReconciliationOut)
def rebuild_balances(
    request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """Reset cached balances to the ledger. Company-wide, owner only (inventory.reconcile)."""
    authorize(request, p, "inventory.reconcile")
    body = ReverseIn.model_construct(reason="rebuild")

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        db.get(Company, p.company_id, with_for_update=True)
        checked, rows, found = inv.reconcile(db, p.company_id)
        inv.rebuild(db, p.company_id, found)
        out = _recon_out(db, checked, rows, found, True)
        return out, str(p.company_id), {"fixed": len(found)}

    return mutate(db, request, p, key, "inventory.rebuild", body, 200, "inventory.rebuild", fn)


def change_cursor(db: Session, company_id: uuid.UUID) -> str:
    """Committed-state cursor: changes whenever a movement/reservation/count/lot commits."""
    stamp = db.execute(
        select(
            select(func.count()).where(StockMovement.company_id == company_id).scalar_subquery(),
            select(func.coalesce(func.sum(InventoryBalance.version), 0))
            .where(InventoryBalance.company_id == company_id)
            .scalar_subquery(),
            select(func.coalesce(func.sum(InventoryLot.version), 0))
            .where(InventoryLot.company_id == company_id)
            .scalar_subquery(),
            select(func.coalesce(func.sum(StockCount.version), 0))
            .where(StockCount.company_id == company_id)
            .scalar_subquery(),
        )
    ).one()
    return "-".join(str(x) for x in stamp)


MAX_STREAMS_PER_EMPLOYEE = 4
_streams: Counter[uuid.UUID] = Counter()
_streams_lock = threading.Lock()


def _poll(company_id: uuid.UUID, session_id: uuid.UUID) -> str | None:
    """Cursor, or None once the session is revoked or expired (the stream then ends)."""
    idle = timedelta(minutes=get_settings().session_idle_minutes)
    with get_sessionmaker()() as s:
        sess = s.get(AuthSession, session_id)
        now = datetime.now(UTC)
        if (
            sess is None
            or sess.revoked_at is not None
            or sess.expires_at <= now
            or sess.last_seen_at + idle <= now
        ):
            return None
        return change_cursor(s, company_id)


@router.get("/events", response_class=StreamingResponse)
def stock_events(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    timeout: int = Query(55, ge=1, le=300, description="Seconds before the stream closes"),
) -> StreamingResponse:
    """Server-Sent Events: `stock` whenever committed inventory state changes (polls committed
    state, so events are only ever emitted after commit). Clients reconnect on close.

    The generator is async: it sleeps on the event loop and only borrows a worker thread for each
    short poll, so open streams can't starve the thread pool every other route runs on. Each
    employee may hold MAX_STREAMS_PER_EMPLOYEE streams; the stream ends if the session does."""
    authorize(request, p, "inventory.read", any_scope=True)
    company_id, session_id, emp_id = p.company_id, p.session.id, p.employee.id
    with _streams_lock:
        if _streams[emp_id] >= MAX_STREAMS_PER_EMPLOYEE:
            raise ApiError(429, "TOO_MANY_STREAMS", "Too many live stock views open; close a tab")
        _streams[emp_id] += 1
    try:
        first = change_cursor(db, company_id)
    except Exception:
        _release(emp_id)
        raise
    db.close()

    async def gen() -> AsyncIterator[str]:
        try:
            last = first
            yield f"event: ready\ndata: {json.dumps({'cursor': last})}\nretry: 3000\n\n"
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                await asyncio.sleep(min(1.5, max(0.0, end - time.monotonic())))
                cur = await run_in_threadpool(_poll, company_id, session_id)
                if cur is None:
                    return
                if cur != last:
                    last = cur
                    yield f"event: stock\ndata: {json.dumps({'cursor': cur})}\n\n"
                else:
                    yield ": keep-alive\n\n"
        finally:
            _release(emp_id)

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
    )


def _release(emp_id: uuid.UUID) -> None:
    with _streams_lock:
        _streams[emp_id] -= 1
        if _streams[emp_id] <= 0:
            del _streams[emp_id]
