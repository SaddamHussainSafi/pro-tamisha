"""Module 08 receiving: compare what vendor documents declare with what was physically counted,
and post the GRN to stock exactly once.

Rules (docs/modules/08-receiving.md):
- Declared quantities never become stock. Only the physical count is posted.
- Accepted -> SALEABLE lot. Damaged -> the lot's DAMAGED companion (not saleable). Rejected -> not
  posted (goes back to the vendor).
- Documents may disagree; the comparison shows each document's figure instead of picking one.
- Landed costs are captured, never auto-allocated to lots.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import inventory as inv
from .models import (
    Company,
    DeclaredLine,
    GrnLine,
    InventoryLot,
    Product,
    Shipment,
    StockMovement,
)

MANUAL_SOURCE = "manual"
Key = tuple[uuid.UUID, str]


def now() -> datetime:
    return datetime.now(UTC)


def next_shipment_no(db: Session, company_id: uuid.UUID) -> str:
    db.get(Company, company_id, with_for_update=True)  # serialize numbering
    n = db.scalar(select(func.count()).where(Shipment.company_id == company_id)) or 0
    return f"SHP-{now():%Y%m%d}-{n + 1:04d}"


def next_grn_no(db: Session, company_id: uuid.UUID) -> str:
    db.get(Company, company_id, with_for_update=True)
    n = (
        db.scalar(
            select(func.count()).where(
                Shipment.company_id == company_id, Shipment.grn_no.isnot(None)
            )
        )
        or 0
    )
    return f"GRN-{now():%Y%m%d}-{n + 1:04d}"


@dataclass
class Row:
    product_id: uuid.UUID
    unit_code: str
    declared_qty: Decimal | None
    declared_by_document: dict[str, Decimal]
    accepted_qty: Decimal
    damaged_qty: Decimal
    rejected_qty: Decimal
    difference: Decimal | None
    status: str


def compare(declared: list[DeclaredLine], counted: list[GrnLine]) -> list[Row]:
    """One row per product+unit. Lines within one document add up; separate documents for the
    same goods must agree, otherwise the row is DOC_CONFLICT (no figure is guessed)."""
    by_doc: dict[Key, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for d in declared:
        if d.removed_at is not None:
            continue
        src = str(d.shipment_document_id) if d.shipment_document_id else MANUAL_SOURCE
        by_doc[(d.product_id, d.unit_code)][src] += d.declared_qty
    got: dict[Key, list[Decimal]] = defaultdict(lambda: [Decimal(0)] * 3)
    for c in counted:
        t = got[(c.product_id, c.unit_code)]
        t[0] += c.accepted_qty
        t[1] += c.damaged_qty
        t[2] += c.rejected_qty
    rows: list[Row] = []
    for key in list(dict.fromkeys([*by_doc.keys(), *got.keys()])):
        docs = {k: inv.q4(v) for k, v in by_doc.get(key, {}).items()}
        acc, dmg, rej = (inv.q4(x) for x in got.get(key, [Decimal(0)] * 3))
        received = acc + dmg + rej
        values = set(docs.values())
        decl = next(iter(values)) if len(values) == 1 else None
        if not docs:
            status = "NOT_DECLARED"
        elif decl is None:
            status = "DOC_CONFLICT"
        elif received == 0:
            status = "NOT_RECEIVED"
        elif received < decl:
            status = "SHORT"
        elif received > decl:
            status = "OVER"
        elif dmg or rej:
            status = "DAMAGED"
        else:
            status = "MATCH"
        rows.append(
            Row(
                product_id=key[0],
                unit_code=key[1],
                declared_qty=decl,
                declared_by_document=docs,
                accepted_qty=acc,
                damaged_qty=dmg,
                rejected_qty=rej,
                difference=None if decl is None else inv.q4(received - decl),
                status=status,
            )
        )
    return rows


def unit_cost_for(declared: list[DeclaredLine], product_id: uuid.UUID, unit: str) -> Decimal | None:
    """The vendor's unit price when every live declaration agrees on it; otherwise unknown."""
    costs = {
        d.unit_cost
        for d in declared
        if d.removed_at is None
        and d.product_id == product_id
        and d.unit_code == unit
        and d.unit_cost is not None
    }
    return next(iter(costs)) if len(costs) == 1 else None


def _receipt(
    lot: InventoryLot, ln: GrnLine, shipment_id: uuid.UUID, qty: Decimal, part: str, note: str
) -> inv.Posting:
    return inv.Posting(
        lot=lot,
        unit_code=ln.unit_code,
        delta=qty,
        movement_type="GRN_RECEIPT",
        posting_key=f"grn:{ln.id}:{part}",
        source_type="GRN",
        source_id=shipment_id,
        source_line_id=ln.id,
        reason_code="DAMAGED_ON_ARRIVAL" if part == "damaged" else None,
        note=note,
    )


def post_grn(db: Session, s: Shipment, actor_id: uuid.UUID) -> tuple[str, list[StockMovement]]:
    """Create one lot per counted line and post accepted + damaged quantities. Caller holds the
    shipment row lock, has checked status COUNTED, and commits. Posting keys make a retry a
    no-op even if the status guard were bypassed."""
    lines = list(
        db.scalars(select(GrnLine).where(GrnLine.shipment_id == s.id).order_by(GrnLine.seq))
    )
    declared = list(db.scalars(select(DeclaredLine).where(DeclaredLine.shipment_id == s.id)))
    grn_no = next_grn_no(db, s.company_id)
    t = now()
    postings: list[inv.Posting] = []
    for ln in lines:
        prod = db.get(Product, ln.product_id)
        assert prod is not None
        lot = InventoryLot(
            company_id=s.company_id,
            product_id=ln.product_id,
            warehouse_id=s.warehouse_id,
            lot_code=f"{prod.sku}-{grn_no[4:]}-{ln.seq}"[:64],
            batch_ref=ln.batch_ref,
            received_at=t,
            expires_at=ln.expires_at,
            created_by=actor_id,
        )
        db.add(lot)
        db.flush()
        ln.lot_id = lot.id
        ln.unit_cost = unit_cost_for(declared, ln.product_id, ln.unit_code)
        note = f"{grn_no} {s.shipment_no}"
        if ln.accepted_qty > 0:
            postings.append(_receipt(lot, ln, s.id, ln.accepted_qty, "accepted", note))
        if ln.damaged_qty > 0:
            comp = inv.damaged_companion(db, lot, actor_id)
            postings.append(_receipt(comp, ln, s.id, ln.damaged_qty, "damaged", note))
    mvs = inv.post(db, actor_id, postings) if postings else []
    return grn_no, mvs
