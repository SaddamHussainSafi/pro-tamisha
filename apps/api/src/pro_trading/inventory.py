"""Module 03 inventory engine: the only code path that changes stock.

Invariants (docs/architecture/03-data-and-transactions.md, OPEN_DECISIONS D-05):
- every on-hand change is one append-only `stock_movements` row + the matching cached
  `inventory_balances` change, in the same transaction;
- balance rows are locked FOR UPDATE in stable (lot_id, unit_code) order before any check;
- `posting_key` is UNIQUE: re-posting the same business event returns the original rows;
- on-hand never drops below reserved (so never below zero): oversell is refused, not queued;
- BOX/PIECE quantities are whole numbers; LB/KG are separate measures (no implicit conversion).
Later modules (GRN posting, invoice issue) call `post()` inside their own transaction.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .errors import ApiError
from .models import (
    InventoryBalance,
    InventoryLot,
    Product,
    ProductUnitProfile,
    StockMovement,
    StockReservation,
)

Q4 = Decimal("0.0001")
WHOLE_UNITS = frozenset({"BOX", "PIECE"})
# Types reversible through the generic reversal command; documents (GRN/invoice) are corrected
# through their own compensating documents in later modules.
REVERSIBLE = frozenset({"OPENING_BALANCE", "ADJUSTMENT", "COUNT_ADJUSTMENT", "DAMAGE", "RETURN"})


def q4(v: Decimal) -> Decimal:
    return v.quantize(Q4)


def now() -> datetime:
    return datetime.now(UTC)


def is_expired(lot: InventoryLot, at: datetime | None = None) -> bool:
    return lot.expires_at is not None and lot.expires_at <= (at or now())


def is_sellable(lot: InventoryLot, at: datetime | None = None) -> bool:
    return lot.status == "SALEABLE" and not is_expired(lot, at)


def stock_units(db: Session, product: Product) -> set[str]:
    """Measures a product may be stocked in: its stock unit, LB (catch weight) and any unit with
    an active packing profile. KG only when the product is stocked in KG (no double measure)."""
    units = {product.stock_unit, "LB"}
    units.update(
        db.scalars(
            select(ProductUnitProfile.unit_code).where(
                ProductUnitProfile.product_id == product.id,
                ProductUnitProfile.active.is_(True),
            )
        ).all()
    )
    if product.stock_unit != "KG":
        units.discard("KG")
    return units


def check_qty(unit_code: str, qty: Decimal) -> Decimal:
    qty = q4(qty)
    if unit_code in WHOLE_UNITS and qty != qty.to_integral_value():
        raise ApiError(
            422,
            "UNIT_NOT_DIVISIBLE",
            f"{unit_code} quantities must be whole numbers",
            unit=unit_code,
        )
    return qty


def check_unit(db: Session, product: Product, unit_code: str) -> None:
    allowed = stock_units(db, product)
    if unit_code not in allowed:
        raise ApiError(
            422,
            "INVALID_UNIT",
            f"{product.sku} is not stocked in {unit_code}",
            allowed_units=sorted(allowed),
        )


@dataclass(frozen=True)
class Posting:
    lot: InventoryLot
    unit_code: str
    delta: Decimal
    movement_type: str
    posting_key: str
    source_type: str
    source_id: uuid.UUID
    source_line_id: uuid.UUID | None = None
    reason_code: str | None = None
    note: str = ""
    reverses_movement_id: uuid.UUID | None = None


def lock_balances(
    db: Session, keys: Iterable[tuple[uuid.UUID, str]]
) -> dict[tuple[uuid.UUID, str], InventoryBalance]:
    """Lock balance rows in stable order (deadlock avoidance). Missing rows are absent."""
    out: dict[tuple[uuid.UUID, str], InventoryBalance] = {}
    for lot_id, unit in sorted(set(keys), key=lambda k: (str(k[0]), k[1])):
        bal = db.scalar(
            select(InventoryBalance)
            .where(InventoryBalance.lot_id == lot_id, InventoryBalance.unit_code == unit)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if bal is not None:
            out[(lot_id, unit)] = bal
    return out


def _ensure_balance(db: Session, lot: InventoryLot, unit: str) -> None:
    db.execute(
        insert(InventoryBalance)
        .values(
            lot_id=lot.id,
            unit_code=unit,
            company_id=lot.company_id,
            product_id=lot.product_id,
            warehouse_id=lot.warehouse_id,
        )
        .on_conflict_do_nothing(index_elements=["lot_id", "unit_code"])
    )


def post(
    db: Session, actor_id: uuid.UUID | None, postings: Sequence[Posting]
) -> list[StockMovement]:
    """Apply postings atomically (caller commits). Exactly-once per posting_key: if a key was
    already posted with the same lot/unit/delta the stored row is returned unchanged."""
    for p in postings:
        if p.delta == 0:
            raise ApiError(422, "ZERO_QUANTITY", "Quantity must not be zero")
        check_qty(p.unit_code, abs(p.delta))
        if p.delta > 0:
            _ensure_balance(db, p.lot, p.unit_code)
    bals = lock_balances(db, ((p.lot.id, p.unit_code) for p in postings))
    out: list[StockMovement] = []
    for p in postings:
        prior = db.scalar(select(StockMovement).where(StockMovement.posting_key == p.posting_key))
        if prior is not None:
            if (prior.lot_id, prior.unit_code, prior.delta_qty) != (
                p.lot.id,
                p.unit_code,
                q4(p.delta),
            ):
                raise ApiError(
                    409, "POSTING_KEY_CONFLICT", "This event was already posted differently"
                )
            out.append(prior)
            continue
        bal = bals.get((p.lot.id, p.unit_code))
        available = (bal.qty - bal.reserved_qty) if bal else Decimal(0)
        if p.delta < 0 and (bal is None or bal.qty + p.delta < bal.reserved_qty):
            raise ApiError(
                409,
                "INSUFFICIENT_STOCK",
                f"Only {q4(available)} {p.unit_code} available in lot {p.lot.lot_code}",
                lot_id=str(p.lot.id),
                unit_code=p.unit_code,
                available=str(q4(available)),
                requested=str(q4(-p.delta)),
            )
        assert bal is not None
        bal.qty = q4(bal.qty + p.delta)
        bal.version += 1
        bal.updated_at = now()
        mv = StockMovement(
            company_id=p.lot.company_id,
            lot_id=p.lot.id,
            product_id=p.lot.product_id,
            warehouse_id=p.lot.warehouse_id,
            unit_code=p.unit_code,
            delta_qty=q4(p.delta),
            qty_after=bal.qty,
            movement_type=p.movement_type,
            reason_code=p.reason_code,
            note=p.note,
            source_type=p.source_type,
            source_id=p.source_id,
            source_line_id=p.source_line_id,
            posting_key=p.posting_key,
            reverses_movement_id=p.reverses_movement_id,
            actor_id=actor_id,
        )
        db.add(mv)
        out.append(mv)
    db.flush()
    return out


def damaged_companion(db: Session, lot: InventoryLot, actor_id: uuid.UUID | None) -> InventoryLot:
    """Get or create the DAMAGED lot that holds stock split off `lot` (race-safe)."""
    if lot.status == "DAMAGED":
        return lot
    # "<code>-DMG", unless that name is already another lot's (a user-made "X-DMG", or a
    # 64-char code whose "-DMG" would be cut off): then a per-parent suffix keeps it unique.
    code = f"{lot.lot_code[:60]}-DMG"
    taken = db.scalar(
        select(InventoryLot.id).where(
            InventoryLot.company_id == lot.company_id,
            InventoryLot.lot_code == code,
            or_(InventoryLot.parent_lot_id.is_(None), InventoryLot.parent_lot_id != lot.id),
        )
    )
    if taken is not None:
        code = f"{lot.lot_code[:51]}-DMG-{lot.id.hex[:8]}"
    db.execute(
        insert(InventoryLot)
        .values(
            company_id=lot.company_id,
            product_id=lot.product_id,
            warehouse_id=lot.warehouse_id,
            lot_code=code,
            batch_ref=lot.batch_ref,
            received_at=lot.received_at,
            expires_at=lot.expires_at,
            status="DAMAGED",
            parent_lot_id=lot.id,
            landed_unit_cost=lot.landed_unit_cost,
            cost_currency=lot.cost_currency,
            created_by=actor_id,
        )
        .on_conflict_do_nothing(
            index_elements=["parent_lot_id"], index_where=InventoryLot.parent_lot_id.isnot(None)
        )
    )
    comp = db.scalar(select(InventoryLot).where(InventoryLot.parent_lot_id == lot.id))
    assert comp is not None
    return comp


def reserve(
    db: Session,
    lot: InventoryLot,
    unit_code: str,
    qty: Decimal,
    *,
    source_type: str,
    source_ref: str,
    posting_key: str,
    actor_id: uuid.UUID | None,
) -> StockReservation:
    prior = db.scalar(select(StockReservation).where(StockReservation.posting_key == posting_key))
    if prior is not None:
        return prior
    qty = check_qty(unit_code, qty)
    if not is_sellable(lot):
        raise ApiError(409, "LOT_NOT_SALEABLE", f"Lot {lot.lot_code} is not saleable")
    bal = lock_balances(db, [(lot.id, unit_code)]).get((lot.id, unit_code))
    available = (bal.qty - bal.reserved_qty) if bal else Decimal(0)
    if bal is None or qty > available:
        raise ApiError(
            409,
            "INSUFFICIENT_STOCK",
            f"Only {q4(available)} {unit_code} available in lot {lot.lot_code}",
            lot_id=str(lot.id),
            unit_code=unit_code,
            available=str(q4(available)),
            requested=str(qty),
        )
    bal.reserved_qty = q4(bal.reserved_qty + qty)
    bal.version += 1
    bal.updated_at = now()
    r = StockReservation(
        company_id=lot.company_id,
        lot_id=lot.id,
        unit_code=unit_code,
        qty=qty,
        source_type=source_type,
        source_ref=source_ref,
        posting_key=posting_key,
        created_by=actor_id,
    )
    db.add(r)
    db.flush()
    return r


def release(db: Session, r: StockReservation, actor_id: uuid.UUID | None) -> StockReservation:
    """Caller must hold r FOR UPDATE."""
    if r.status != "ACTIVE":
        raise ApiError(409, "INVALID_STATE", "Reservation is not active", current_status=r.status)
    bal = lock_balances(db, [(r.lot_id, r.unit_code)])[(r.lot_id, r.unit_code)]
    bal.reserved_qty = q4(bal.reserved_qty - r.qty)
    bal.version += 1
    bal.updated_at = now()
    r.status, r.released_at, r.released_by = "RELEASED", now(), actor_id
    db.flush()
    return r


# ------------------------------------------------------------------ reconciliation


@dataclass(frozen=True)
class Discrepancy:
    lot_id: uuid.UUID
    unit_code: str
    cached_qty: Decimal | None
    ledger_qty: Decimal
    cached_reserved: Decimal | None
    active_reserved: Decimal


def reconcile(
    db: Session, company_id: uuid.UUID, warehouse_ids: set[uuid.UUID] | None = None
) -> tuple[int, int, list[Discrepancy]]:
    """Compare cached balances with the ledger (sum of deltas) and active reservations.
    Returns (balances checked, ledger rows, discrepancies)."""

    def scoped(stmt: Any, col: Any) -> Any:
        return stmt if warehouse_ids is None else stmt.where(col.in_(warehouse_ids))

    ledger_rows = db.execute(
        scoped(
            select(
                StockMovement.lot_id,
                StockMovement.unit_code,
                func.sum(StockMovement.delta_qty),
                func.count(),
            )
            .where(StockMovement.company_id == company_id)
            .group_by(StockMovement.lot_id, StockMovement.unit_code),
            StockMovement.warehouse_id,
        )
    ).all()
    ledger = {(lot, u): Decimal(s) for lot, u, s, _ in ledger_rows}
    n_rows = sum(int(c) for *_, c in ledger_rows)
    res = {
        (lot, u): Decimal(s)
        for lot, u, s in db.execute(
            select(
                StockReservation.lot_id, StockReservation.unit_code, func.sum(StockReservation.qty)
            )
            .where(StockReservation.company_id == company_id, StockReservation.status == "ACTIVE")
            .group_by(StockReservation.lot_id, StockReservation.unit_code)
        ).all()
    }
    cached = {
        (b.lot_id, b.unit_code): b
        for b in db.scalars(
            scoped(
                select(InventoryBalance).where(InventoryBalance.company_id == company_id),
                InventoryBalance.warehouse_id,
            )
        ).all()
    }
    out: list[Discrepancy] = []
    for key in sorted(set(ledger) | set(cached), key=lambda k: (str(k[0]), k[1])):
        b = cached.get(key)
        lq, rq = q4(ledger.get(key, Decimal(0))), q4(res.get(key, Decimal(0)))
        if b is None or q4(b.qty) != lq or q4(b.reserved_qty) != rq:
            out.append(
                Discrepancy(
                    key[0], key[1], b.qty if b else None, lq, b.reserved_qty if b else None, rq
                )
            )
    return len(cached), n_rows, out


def rebuild(db: Session, company_id: uuid.UUID, found: list[Discrepancy]) -> None:
    """Reset cached balances to the ledger (the ledger is the source of truth)."""
    for d in sorted(found, key=lambda d: (str(d.lot_id), d.unit_code)):
        lot = db.get(InventoryLot, d.lot_id)
        assert lot is not None
        _ensure_balance(db, lot, d.unit_code)
        bal = lock_balances(db, [(d.lot_id, d.unit_code)])[(d.lot_id, d.unit_code)]
        if d.ledger_qty < 0 or d.active_reserved > d.ledger_qty:
            raise ApiError(
                409,
                "REBUILD_CONFLICT",
                f"Ledger for lot {lot.lot_code} {d.unit_code} cannot satisfy its reservations",
                lot_id=str(d.lot_id),
            )
        bal.qty, bal.reserved_qty = d.ledger_qty, d.active_reserved
        bal.version += 1
        bal.updated_at = now()
    db.flush()
