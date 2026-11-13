"""Purchases (owner decisions 2026-11-12, docs/modules/08-receiving.md).

One purchase flow for every supplier: the lines of the seller's invoice are added to stock the
moment the purchase is saved. There is no count and no approval step.

- Save: one transaction creates the shipment (POSTED, with its GRN number), one invoice line
  (`DeclaredLine`: what the supplier bills) and one stock line (`GrnLine` + lot) per line, and
  posts the stock ledger with the posting key `grn:{line}:accepted`.
- Later fixes (short, damaged, returned, more than invoiced) are `PurchaseCorrection` rows that
  post new GRN_CORRECTION movements; ledger rows are never edited. Stock never goes negative
  (D-05): what is already invoiced or reserved stays, the rest is flagged for the owner.
- Airport shipments are in stock at once and verified on arrival.
- A quick receive is a purchase with only product + quantity; matching it to the real invoice
  adds the supplier and costs and never posts its stock again.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import inventory as inv
from . import receiving as recv
from .errors import ApiError
from .models import (
    DeclaredLine,
    GrnLine,
    InventoryLot,
    Invoice,
    Product,
    PurchaseCorrection,
    Shipment,
    ShipmentDocument,
    StockMovement,
    Vendor,
)

CENT = Decimal("0.01")
ZERO = Decimal(0)
Key = tuple[uuid.UUID, str]
STAGES = (
    "NOT_IN_STOCK", "TO_COMPLETE", "TO_VERIFY", "TO_APPROVE", "VERIFIED", "IN_STOCK", "CANCELLED",
)  # fmt: skip
QUICK_BATCH_PREFIX = "QR"  # batch names of goods whose supplier is not known yet


def stage(s: Shipment) -> str:
    """Where the purchase stands, in the words the screens use."""
    if s.status == "CANCELLED":
        return "CANCELLED"
    if s.status != "POSTED":
        return "NOT_IN_STOCK"
    if s.needs_details:
        return "TO_COMPLETE"
    return {"TO_VERIFY": "TO_VERIFY", "PENDING_OK": "TO_APPROVE", "VERIFIED": "VERIFIED"}.get(
        s.verify_status or "", "IN_STOCK"
    )


def stage_filter(name: str):  # type: ignore[no-untyped-def]
    """SQL condition for one stage (same rules as stage())."""
    posted = Shipment.status == "POSTED"
    done = Shipment.needs_details.is_(False)
    return {
        "CANCELLED": Shipment.status == "CANCELLED",
        "NOT_IN_STOCK": Shipment.status.in_(("OPEN", "COUNTED")),
        "TO_COMPLETE": posted & Shipment.needs_details.is_(True),
        "TO_VERIFY": posted & done & (Shipment.verify_status == "TO_VERIFY"),
        "TO_APPROVE": posted & done & (Shipment.verify_status == "PENDING_OK"),
        "VERIFIED": posted & done & (Shipment.verify_status == "VERIFIED"),
        "IN_STOCK": posted & done & Shipment.verify_status.is_(None),
    }[name]


def amount(qty: Decimal, cost: Decimal) -> Decimal:
    return (Decimal(qty) * Decimal(cost)).quantize(CENT, ROUND_HALF_UP)


def bill(declared: list[DeclaredLine]) -> tuple[Decimal, int]:
    """(Σ invoice qty × unit cost to the cent, lines with no cost) — the supplier's bill."""
    total, unpriced = ZERO, 0
    for d in declared:
        if d.removed_at is not None:
            continue
        if d.unit_cost is None:
            unpriced += 1
        else:
            total += amount(d.declared_qty, d.unit_cost)
    return total, unpriced


@dataclass
class LineSpec:
    """One invoice line, already checked and in the product's stock unit."""

    product: Product
    unit_code: str
    qty: Decimal
    unit_cost: Decimal | None
    label: str
    batch_ref: str | None = None
    expires_at: datetime | None = None
    note: str | None = None
    shipment_document_id: uuid.UUID | None = None


def batch_prefix(db: Session, s: Shipment) -> str:
    v = db.get(Vendor, s.vendor_id) if s.vendor_id else None
    return recv.supplier_initials(v.name, v.code) if v else QUICK_BATCH_PREFIX


def add_invoice_lines(
    db: Session, s: Shipment, specs: list[LineSpec], actor_id: uuid.UUID
) -> list[DeclaredLine]:
    out = []
    for sp in specs:
        d = DeclaredLine(
            shipment_id=s.id,
            shipment_document_id=sp.shipment_document_id,
            raw_label=sp.label[:500],
            product_id=sp.product.id,
            unit_code=sp.unit_code,
            declared_qty=inv.q4(sp.qty),
            unit_cost=sp.unit_cost,
            created_by=actor_id,
        )
        db.add(d)
        out.append(d)
    db.flush()
    return out


def receive(
    db: Session, s: Shipment, specs: list[LineSpec], actor_id: uuid.UUID
) -> tuple[list[GrnLine], list[StockMovement]]:
    """Add lines to stock: one lot + one stock line + one ledger row each, posting key
    `grn:{line}:accepted`. The caller holds the shipment lock (or has just created it) and
    commits; `s.grn_no` must be set."""
    assert s.grn_no is not None
    if not specs:
        return [], []
    seq = db.scalar(select(func.max(GrnLine.seq)).where(GrnLine.shipment_id == s.id)) or 0
    prefix = batch_prefix(db, s)
    t = recv.now()
    lines: list[GrnLine] = []
    postings: list[inv.Posting] = []
    for sp in specs:
        seq += 1
        batch = (sp.batch_ref or "").strip() or recv.next_batch_ref(db, s.company_id, prefix)
        lot = InventoryLot(
            company_id=s.company_id,
            product_id=sp.product.id,
            warehouse_id=s.warehouse_id,
            lot_code=f"{sp.product.sku}-{s.grn_no[4:]}-{seq}"[:64],
            batch_ref=batch,
            received_at=t,
            expires_at=sp.expires_at,
            created_by=actor_id,
        )
        db.add(lot)
        db.flush()
        ln = GrnLine(
            id=uuid.uuid4(),
            shipment_id=s.id,
            seq=seq,
            product_id=sp.product.id,
            unit_code=sp.unit_code,
            accepted_qty=inv.q4(sp.qty),
            damaged_qty=ZERO,
            rejected_qty=ZERO,
            batch_ref=batch,
            expires_at=sp.expires_at,
            note=sp.note,
            unit_cost=sp.unit_cost,
            lot_id=lot.id,
            counted_by=actor_id,
        )
        db.add(ln)
        lines.append(ln)
        postings.append(
            inv.Posting(
                lot=lot,
                unit_code=sp.unit_code,
                delta=inv.q4(sp.qty),
                movement_type="GRN_RECEIPT",
                posting_key=f"grn:{ln.id}:accepted",
                source_type="GRN",
                source_id=s.id,
                source_line_id=ln.id,
                note=f"{s.grn_no} {s.shipment_no}",
            )
        )
    db.flush()
    return lines, inv.post(db, actor_id, postings)


# ------------------------------------------------------------------ corrections


def effective_qty(db: Session, ln: GrnLine) -> Decimal:
    """What the line added to stock, after posted corrections: accepted − short − returned
    + over. Damaged goods arrived, so they still count."""
    q = Decimal(ln.accepted_qty)
    for kind, posted in db.execute(
        select(PurchaseCorrection.kind, PurchaseCorrection.posted_qty).where(
            PurchaseCorrection.grn_line_id == ln.id, PurchaseCorrection.status == "POSTED"
        )
    ):
        if kind == "OVER":
            q += Decimal(posted or 0)
        elif kind in ("SHORT", "RETURNED"):
            q -= Decimal(posted or 0)
    return inv.q4(q)


def new_correction(
    db: Session,
    s: Shipment,
    ln: GrnLine,
    kind: str,
    qty: Decimal,
    reason: str,
    source: str,
    actor_id: uuid.UUID,
) -> PurchaseCorrection:
    if ln.lot_id is None:
        raise ApiError(409, "LINE_NOT_IN_STOCK", "That line is not in stock yet")
    qty = inv.check_qty(ln.unit_code, qty)
    if qty <= 0:
        raise ApiError(422, "ZERO_QUANTITY", "Quantity must be more than zero")
    c = PurchaseCorrection(
        company_id=s.company_id,
        shipment_id=s.id,
        grn_line_id=ln.id,
        kind=kind,
        unit_code=ln.unit_code,
        qty=qty,
        reason=reason.strip(),
        source=source,
        created_by=actor_id,
    )
    db.add(c)
    db.flush()
    return c


def post_correction(
    db: Session, c: PurchaseCorrection, actor_id: uuid.UUID, note: str | None = None
) -> list[StockMovement]:
    """Approve a PENDING correction (caller holds the shipment lock and has locked `c`).
    Takes what is free in the line's batch; the part already invoiced or reserved is kept as
    `uncovered_qty` and flagged, never forced below zero (D-05). Posting keys
    `grnfix:{id}:in|out|dmg` make a repeat a no-op."""
    if c.status != "PENDING":
        raise ApiError(409, "INVALID_STATE", f"That correction is already {c.status.lower()}")
    ln = db.get(GrnLine, c.grn_line_id)
    assert ln is not None and ln.lot_id is not None
    lot = db.get(InventoryLot, ln.lot_id)
    s = db.get(Shipment, c.shipment_id)
    assert lot is not None and s is not None
    text = f"{s.shipment_no} {c.kind.lower()}: {c.reason}"[:500]

    def mv(target: InventoryLot, delta: Decimal, part: str) -> inv.Posting:
        return inv.Posting(
            lot=target,
            unit_code=c.unit_code,
            delta=delta,
            movement_type="GRN_CORRECTION",
            posting_key=f"grnfix:{c.id}:{part}",
            source_type="GRN",
            source_id=s.id,
            source_line_id=ln.id,
            reason_code=c.kind,
            note=text,
        )

    if c.kind == "OVER":
        posted, postings = Decimal(c.qty), [mv(lot, Decimal(c.qty), "in")]
    else:
        comp = inv.damaged_companion(db, lot, actor_id) if c.kind == "DAMAGED" else None
        keys = [(lot.id, c.unit_code)]
        if comp is not None:
            inv._ensure_balance(db, comp, c.unit_code)  # so both rows lock in stable order
            keys.append((comp.id, c.unit_code))
        bal = inv.lock_balances(db, keys).get((lot.id, c.unit_code))
        free = max(ZERO, bal.qty - bal.reserved_qty) if bal else ZERO
        posted = min(Decimal(c.qty), free)
        postings = []
        if posted > 0:
            postings.append(mv(lot, -posted, "out"))
            if comp is not None:
                postings.append(mv(comp, posted, "dmg"))
    mvs = inv.post(db, actor_id, postings) if postings else []
    c.status = "POSTED"
    c.posted_qty = inv.q4(posted)
    c.uncovered_qty = inv.q4(Decimal(c.qty) - posted)
    c.needs_review = c.uncovered_qty > 0
    c.decided_by, c.decided_at = actor_id, recv.now()
    c.decision_note = (note or "").strip() or None
    db.flush()
    return mvs


def invoices_using(db: Session, lot_id: uuid.UUID) -> list[str]:
    """Numbers of the invoices that already took stock from a batch."""
    return list(
        db.scalars(
            select(Invoice.invoice_no)
            .join(StockMovement, StockMovement.source_id == Invoice.id)
            .where(
                StockMovement.lot_id == lot_id,
                StockMovement.movement_type == "INVOICE_DISPATCH",
                StockMovement.source_type == "INVOICE",
            )
            .distinct()
            .order_by(Invoice.invoice_no)
        )
    )


# ------------------------------------------------------------------ matching


@dataclass
class Difference:
    product: Product
    unit_code: str
    invoice_qty: Decimal
    received_qty: Decimal

    @property
    def diff(self) -> Decimal:
        return inv.q4(self.invoice_qty - self.received_qty)


@dataclass
class Plan:
    new: list[LineSpec]  # lines to add to stock now
    differences: list[Difference]  # invoice vs what is already on the shelf
    already: dict[Key, list[GrnLine]]


def plan(db: Session, specs: list[LineSpec], already: list[GrnLine]) -> Plan:
    """Split the invoice lines into goods to add to stock now and goods that are already in
    stock (quick receives). Stock already added is never posted again: only a difference in
    quantity comes back, as a correction."""
    by_line: dict[Key, list[GrnLine]] = defaultdict(list)
    for ln in already:
        by_line[(ln.product_id, ln.unit_code)].append(ln)
    by_spec: dict[Key, list[LineSpec]] = defaultdict(list)
    for sp in specs:
        by_spec[(sp.product.id, sp.unit_code)].append(sp)
    missing = [k for k in by_line if k not in by_spec]
    if missing:
        prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(
            [k[0] for k in missing])))}  # fmt: skip
        raise ApiError(
            422,
            "QUICK_NOT_ON_INVOICE",
            "The goods already added to stock are not on this invoice: "
            + ", ".join(f"{prods[k[0]].name} ({k[1].lower()})" for k in missing)
            + ". Add them as lines, or leave that quick receive out.",
            lines=[{"product_id": str(k[0]), "unit": k[1]} for k in missing],
        )
    new: list[LineSpec] = []
    diffs: list[Difference] = []
    for key, group in by_spec.items():
        if key not in by_line:
            new.extend(group)
            continue
        inv_qty = inv.q4(sum((g.qty for g in group), ZERO))
        got = inv.q4(sum((effective_qty(db, ln) for ln in by_line[key]), ZERO))
        if inv_qty != got:
            diffs.append(Difference(group[0].product, key[1], inv_qty, got))
    return Plan(new, diffs, dict(by_line))


def match_corrections(
    db: Session, s: Shipment, p: Plan, reason: str, actor_id: uuid.UUID
) -> list[PurchaseCorrection]:
    """One PENDING correction per difference between the invoice and what was quick-received
    (the caller posts them when the person may approve)."""
    out: list[PurchaseCorrection] = []
    for d in p.differences:
        lines = p.already[(d.product.id, d.unit_code)]
        if d.diff > 0:  # invoice says more than was added
            out.append(new_correction(db, s, lines[0], "OVER", d.diff, reason, "MATCH", actor_id))
            continue
        left = -d.diff
        for ln in reversed(lines):
            take = min(left, effective_qty(db, ln))
            if take > 0:
                out.append(new_correction(db, s, ln, "SHORT", take, reason, "MATCH", actor_id))
                left -= take
            if left <= 0:
                break
    return out


def line_pieces(counted: list[GrnLine]) -> int | None:
    """Pieces the shipment should have, when every line is in boxes or pieces."""
    if not counted or any(c.unit_code not in inv.WHOLE_UNITS for c in counted):
        return None
    return int(sum((c.accepted_qty for c in counted), ZERO))


def duplicate_of(
    db: Session, company_id: uuid.UUID, vendor_id: uuid.UUID, invoice_no: str | None,
    exclude: uuid.UUID | None = None,
) -> Shipment | None:  # fmt: skip
    """Another purchase from the same supplier with the same invoice number (not cancelled)."""
    if not invoice_no:
        return None
    q = select(Shipment).where(
        Shipment.company_id == company_id,
        Shipment.vendor_id == vendor_id,
        func.lower(Shipment.vendor_invoice_no) == invoice_no.lower(),
        Shipment.status != "CANCELLED",
    )
    if exclude is not None:
        q = q.where(Shipment.id != exclude)
    return db.scalar(q.order_by(Shipment.created_at).limit(1))


def remind_to_complete(db: Session, at: datetime | None = None) -> int:
    """Daily reminder (worker, hourly beat): one notification per company per local day while
    quick receives are still waiting for their supplier invoice. Safe to run repeatedly: the
    event id carries the date. Returns how many companies were reminded."""
    from zoneinfo import ZoneInfo

    from .models import Company
    from .notifications import notify_safely

    at = at or recv.now()
    rows = db.execute(
        select(Shipment.company_id, func.count(), func.min(Shipment.created_at))
        .where(Shipment.status == "POSTED", Shipment.needs_details.is_(True))
        .group_by(Shipment.company_id)
    ).all()
    sent = 0
    for company_id, n, oldest in rows:
        tz = db.scalar(select(Company.timezone).where(Company.id == company_id)) or "UTC"
        local = at.astimezone(ZoneInfo(tz))
        if local.hour < 7:  # not in the middle of the night
            continue
        days = (local.date() - oldest.astimezone(ZoneInfo(tz)).date()).days
        sent += min(
            1,
            notify_safely(
                db,
                company_id,
                "purchase.to_complete",
                event_id=f"purchase.to_complete:{company_id}:{local.date().isoformat()}",
                category="receiving",
                severity="warning",
                source_module="Purchases",
                title=f"{n} purchase{'' if n == 1 else 's'} to complete",
                body="Goods added by quick receive still need their supplier invoice"
                + (
                    f" · the oldest has waited {days} day{'' if days == 1 else 's'}" if days else ""
                ),
                link="/purchases?view=complete",
                permission="grn.record",
                any_scope=True,
            ),
        )
    db.commit()
    return sent


def doc_count(db: Session, shipment_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).where(ShipmentDocument.shipment_id == shipment_id)) or 0
