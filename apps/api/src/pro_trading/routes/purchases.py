"""Purchases: one flow for every supplier, in stock the moment it is saved (owner, 2026-11-12).

A purchase is a `Shipment` row (the same record the old Receiving flow used), so the reads are
`GET /shipments*`. The commands here need `grn.record` in the purchase's warehouse; approving,
rejecting or reviewing a correction needs `grn.approve` (the manager's OK). Every command runs
inside `mutate()`: idempotency key + audit + commit in one transaction, and locks the purchase
row before it posts anything. Stock moves only through `inventory.post()` with one posting key
per line, so a retry or two people pressing at once add the stock once.
"""

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import idempotency, units
from .. import inventory as inv
from .. import purchases as pur
from .. import receiving as svc
from ..deps import DbDep, Principal, PrincipalDep, authorize
from ..errors import ApiError
from ..models import GrnLine, PurchaseCorrection, Shipment, Vendor, Warehouse
from ..schemas import (
    CorrectionDecisionIn,
    CorrectionIn,
    PurchaseAirport,
    PurchaseCompleteIn,
    PurchaseHeader,
    PurchaseIn,
    PurchaseLineIn,
    PurchaseLinesIn,
    QuickReceiveIn,
    ShipmentDetail,
    VerifyIn,
    VersionIn,
)
from ._common import mutate
from .receiving import (
    _load,
    detail_out,
    kg_counted_in_lb,
    lock_for,
    product_for,
    touch,
)

router = APIRouter(tags=["purchases"])
Result = tuple[dict[str, Any], str, dict[str, Any]]


# ------------------------------------------------------------------ helpers


def _num(q: Decimal) -> str:
    return f"{q.normalize():f}"


def spec_from(
    db: Session, p: Principal, ln: PurchaseLineIn, document_link: uuid.UUID | None = None
) -> pur.LineSpec:
    """Check one line and put it in the product's stock unit (a kg line for a product kept in
    lb is saved in lb, D-38; its cost per kg becomes the cost per lb)."""
    unit, qty, cost, note = ln.unit_code, ln.qty, ln.unit_cost, None
    if kg_counted_in_lb(db, p, ln.product_id, unit):
        note = f"Entered in kg: {_num(qty)} kg"
        unit, qty = "LB", units.kg_to_lb(qty)
        cost = units.q4(cost * units.LB_TO_KG) if cost is not None else None
    prod = product_for(db, p, ln.product_id, unit)
    qty = inv.check_qty(unit, qty)
    return pur.LineSpec(
        product=prod,
        unit_code=unit,
        qty=qty,
        unit_cost=cost,
        label=(ln.label or "").strip() or prod.name,
        batch_ref=(ln.batch_ref or "").strip() or None,
        expires_at=ln.expires_at,
        note=note,
        shipment_document_id=document_link,
    )


def need_expiry(specs: list[pur.LineSpec]) -> None:
    """Goods going into stock now need their expiry date when the product tracks expiry.
    (Lines that only put a cost on goods already on the shelf do not: their batch has it.)"""
    for sp in specs:
        if sp.product.tracks_expiry and sp.expires_at is None:
            raise ApiError(
                422, "EXPIRY_REQUIRED", f"{sp.product.sku} requires an expiry date",
                product_id=str(sp.product.id),
            )  # fmt: skip


def vendor_for(db: Session, p: Principal, vid: uuid.UUID) -> Vendor:
    """The supplier, locked: two people entering the same invoice wait for each other, so the
    second one meets the duplicate check instead of adding the stock twice."""
    v = db.get(Vendor, vid, with_for_update=True, populate_existing=True)
    if v is None or v.company_id != p.company_id:
        raise ApiError(422, "INVALID_VENDOR", "Supplier not found")
    if not v.active:
        raise ApiError(409, "VENDOR_INACTIVE", f"{v.name} is inactive")
    return v


def check_duplicate(
    db: Session, p: Principal, v: Vendor, h: PurchaseHeader, exclude: uuid.UUID | None = None
) -> None:
    no = (h.vendor_invoice_no or "").strip()
    dup = pur.duplicate_of(db, p.company_id, v.id, no or None, exclude)
    if dup is not None and not h.confirm_duplicate:
        raise ApiError(
            409,
            "INVOICE_ALREADY_ENTERED",
            f"Invoice {no} from {v.name} was already entered as {dup.shipment_no}. "
            "Entering it again adds its goods to stock a second time.",
            shipment_id=str(dup.id),
            shipment_no=dup.shipment_no,
        )


def new_purchase(
    db: Session,
    p: Principal,
    warehouse_id: uuid.UUID,
    v: Vendor | None,
    h: PurchaseHeader | None,
    a: PurchaseAirport | None,
    note: str | None,
    *,
    entry: str = "PURCHASE",
    quick_reason: str | None = None,
) -> Shipment:
    """A purchase row that is in stock from its first moment: POSTED with its GRN number."""
    w = db.get(Warehouse, warehouse_id)
    if w is None or w.company_id != p.company_id:
        raise ApiError(422, "INVALID_WAREHOUSE", "Location not found")
    airport = bool(a and a.is_airport)
    t = svc.now()
    s = Shipment(
        company_id=p.company_id,
        warehouse_id=w.id,
        vendor_id=v.id if v else None,
        shipment_no=svc.next_shipment_no(db, p.company_id),
        vendor_invoice_no=((h.vendor_invoice_no or "").strip() or None) if h else None,
        invoice_date=h.invoice_date if h else None,
        currency=(h.currency if h and h.currency else None) or (v.currency if v else "CAD"),
        awb_no=((a.awb_no or "").strip() or None) if a and airport else None,
        expected_on=a.expected_on if a and airport else None,
        expected_pieces=a.expected_pieces if a and airport else None,
        is_airport=airport,
        verify_status="TO_VERIFY" if airport else None,
        note=(note or "").strip() or None,
        created_by=p.employee.id,
        entry=entry,
        needs_details=entry == "QUICK",
        quick_reason=quick_reason,
        status="POSTED",
        grn_no=svc.next_grn_no(db, p.company_id),
        posted_by=p.employee.id,
        posted_at=t,
    )
    db.add(s)
    db.flush()
    return s


def _own_lines(db: Session, s: Shipment) -> list[GrnLine]:
    return list(
        db.scalars(select(GrnLine).where(GrnLine.shipment_id == s.id).order_by(GrnLine.seq))
    )


def _lock_quicks(db: Session, p: Principal, s: Shipment, ids: list[uuid.UUID]) -> list[Shipment]:
    """Quick receives this invoice covers: still waiting for their details, same warehouse.
    Locked in id order (no deadlock between two people matching the same ones)."""
    out: list[Shipment] = []
    for qid in sorted(set(ids), key=str):
        if qid == s.id:
            continue
        q = db.get(Shipment, qid, with_for_update=True, populate_existing=True)
        if q is None or q.company_id != p.company_id:
            raise ApiError(422, "INVALID_QUICK_RECEIVE", "Quick receive not found")
        if q.warehouse_id != s.warehouse_id:
            raise ApiError(
                422, "WRONG_WAREHOUSE", f"{q.shipment_no} belongs to a different warehouse"
            )
        if q.status != "POSTED" or not q.needs_details:
            raise ApiError(
                409,
                "QUICK_ALREADY_MATCHED",
                f"{q.shipment_no} was already matched to an invoice",
                shipment_no=q.shipment_no,
            )
        out.append(q)
    return out


def _alerts(corrs: list[PurchaseCorrection]) -> dict[str, Any]:
    """What the audit event (and so the notification rules) say about corrections."""
    return {
        "corrections": [
            {"id": str(c.id), "kind": c.kind, "qty": str(c.qty), "unit": c.unit_code,
             "status": c.status, "posted": str(c.posted_qty) if c.posted_qty is not None else None,
             "uncovered": str(c.uncovered_qty)}
            for c in corrs
        ],
        "pending": sum(1 for c in corrs if c.status == "PENDING"),
        "flagged": [str(c.id) for c in corrs if c.needs_review],
    }  # fmt: skip


def stock_lines(
    db: Session,
    p: Principal,
    s: Shipment,
    specs: list[pur.LineSpec],
    quick_ids: list[uuid.UUID],
    reason: str | None,
    *,
    own: list[GrnLine] | None = None,
) -> dict[str, Any]:
    """Put the invoice lines on the purchase and add to stock what is not there yet. Goods
    already added by a quick receive (this one's own, or the ones in `quick_ids`) are not posted
    again: a difference in quantity becomes a correction, posted now when the person may
    approve, otherwise waiting for a manager's OK. Returns the audit detail."""
    quicks = _lock_quicks(db, p, s, quick_ids)
    already = list(own or [])
    for q in quicks:
        already += _own_lines(db, q)
    pl = pur.plan(db, specs, already)
    why = (reason or "").strip()
    if pl.differences and len(why) < 3:
        raise ApiError(
            422,
            "MATCH_REASON_REQUIRED",
            "The invoice and what was already added to stock differ. Say why.",
            differences=[
                {
                    "product_id": str(d.product.id),
                    "sku": d.product.sku,
                    "product_name": d.product.name,
                    "unit": d.unit_code,
                    "invoice_qty": str(d.invoice_qty),
                    "received_qty": str(d.received_qty),
                    "difference": str(d.diff),
                }
                for d in pl.differences
            ],
        )
    need_expiry(pl.new)
    actor = p.employee.id
    pur.add_invoice_lines(db, s, specs, actor)
    lines, mvs = pur.receive(db, s, pl.new, actor)
    corrs = pur.match_corrections(db, s, pl, why, actor)
    if p.can("grn.approve", s.warehouse_id):
        for c in corrs:
            mvs += pur.post_correction(db, c, actor)
    t = svc.now()
    for q in quicks:
        q.matched_shipment_id = s.id
        q.vendor_id, q.currency = s.vendor_id, s.currency
        q.needs_details = False
        q.details_completed_by, q.details_completed_at = actor, t
        touch(q)
    db.flush()
    total, unpriced = pur.bill(_load(db, s)[1])
    return {
        "shipment_no": s.shipment_no,
        "grn_no": s.grn_no,
        "lines": len(specs),
        "added_to_stock": [
            {"sku": sp.product.sku, "unit": sp.unit_code, "qty": str(sp.qty)} for sp in pl.new
        ],
        "movements": [str(m.id) for m in mvs],
        "matched_quick": [q.shipment_no for q in quicks],
        "total": str(total),
        "unpriced_lines": unpriced,
        **_alerts(corrs),
    }


def _settle_verification(db: Session, s: Shipment) -> None:
    """After a decision on a difference found at verification: once none is waiting, the
    shipment is Verified, or goes back to be counted again when one was refused."""
    if s.verify_status != "PENDING_OK":
        return
    rows = list(
        db.scalars(
            select(PurchaseCorrection).where(
                PurchaseCorrection.shipment_id == s.id, PurchaseCorrection.source == "VERIFY"
            )
        )
    )
    if any(c.status == "PENDING" for c in rows):
        return
    # refused in this round: decided after the count that found it
    refused = any(
        c.status == "REJECTED"
        and c.decided_at is not None
        and s.verified_at is not None
        and c.decided_at >= s.verified_at
        for c in rows
    )
    s.verify_status = "TO_VERIFY" if refused else "VERIFIED"


def _correction(db: Session, s: Shipment, cid: uuid.UUID) -> PurchaseCorrection:
    c = db.get(PurchaseCorrection, cid, with_for_update=True, populate_existing=True)
    if c is None or c.shipment_id != s.id:
        raise ApiError(404, "NOT_FOUND", "Correction not found")
    return c


def _line_of(db: Session, s: Shipment, line_id: uuid.UUID) -> GrnLine:
    """A stock line of this purchase, or of a quick receive matched into it."""
    ln = db.get(GrnLine, line_id)
    if ln is not None and ln.shipment_id != s.id:
        owner = db.get(Shipment, ln.shipment_id)
        if owner is None or owner.matched_shipment_id != s.id:
            ln = None
    if ln is None:
        raise ApiError(404, "NOT_FOUND", "Line not found")
    return ln


# ------------------------------------------------------------------ save a purchase


@router.post("/purchases", status_code=201, response_model=ShipmentDetail)
def create_purchase(
    body: PurchaseIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """Save a purchase and add every line to stock at once (no count, no approval). One
    transaction: the purchase, its invoice lines, one batch + ledger row per line."""
    authorize(request, p, "grn.record", body.warehouse_id)

    def fn() -> Result:
        v = vendor_for(db, p, body.vendor_id)
        check_duplicate(db, p, v, body)
        specs = [spec_from(db, p, ln) for ln in body.lines]
        s = new_purchase(db, p, body.warehouse_id, v, body, body, body.note)
        detail = stock_lines(db, p, s, specs, body.absorb_quick_ids, body.reason)
        return detail_out(db, s), str(s.id), {**detail, "vendor": v.code, "airport": s.is_airport}

    return mutate(db, request, p, key, "purchase.create", body, 201, "shipment.purchase", fn)


@router.post("/purchases/quick", status_code=201, response_model=ShipmentDetail)
def quick_receive(
    body: QuickReceiveIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """Goods that are on the shelf but were never entered: add them to stock now. The record
    is a purchase marked "Details to complete" until it is matched to the supplier's invoice."""
    authorize(request, p, "grn.record", body.warehouse_id)

    def fn() -> Result:
        v = vendor_for(db, p, body.vendor_id) if body.vendor_id else None
        spec = spec_from(
            db,
            p,
            PurchaseLineIn(
                product_id=body.product_id,
                unit_code=body.unit_code,
                qty=body.qty,
                expires_at=body.expires_at,
            ),
        )
        need_expiry([spec])
        why = body.reason.strip()
        needed = (body.needed_for or "").strip() or None
        s = new_purchase(
            db, p, body.warehouse_id, v, None, None, needed, entry="QUICK", quick_reason=why
        )
        _, mvs = pur.receive(db, s, [spec], p.employee.id)
        detail = {
            "shipment_no": s.shipment_no,
            "sku": spec.product.sku,
            "product": spec.product.name,
            "unit": spec.unit_code,
            "qty": str(spec.qty),
            "entered": f"{body.qty} {body.unit_code}",
            "reason": why,
            "needed_for": needed,
            "vendor": v.code if v else None,
            "movements": [str(m.id) for m in mvs],
        }
        return detail_out(db, s), str(s.id), detail

    return mutate(db, request, p, key, "purchase.quick", body, 201, "shipment.quick_receive", fn)


@router.post("/purchases/{shipment_id}/complete", response_model=ShipmentDetail)
def complete_purchase(
    shipment_id: uuid.UUID,
    body: PurchaseCompleteIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Match a quick receive to the real supplier invoice: supplier, invoice number and date,
    and the invoice lines with their costs. Its stock is never posted again; a difference in
    quantity becomes a correction. Other invoice lines are new goods and go to stock now."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.record", body.version, "POSTED")
        if not s.needs_details:
            raise ApiError(409, "INVALID_STATE", "This purchase is already complete")
        v = vendor_for(db, p, body.vendor_id)
        check_duplicate(db, p, v, body, exclude=s.id)
        specs = [spec_from(db, p, ln) for ln in body.lines]
        s.vendor_id = v.id
        s.vendor_invoice_no = (body.vendor_invoice_no or "").strip() or None
        s.invoice_date = body.invoice_date
        s.currency = body.currency or v.currency
        detail = stock_lines(
            db, p, s, specs, body.absorb_quick_ids, body.reason, own=_own_lines(db, s)
        )
        s.needs_details = False
        s.details_completed_by, s.details_completed_at = p.employee.id, svc.now()
        touch(s)
        db.flush()
        return detail_out(db, s), str(s.id), {**detail, "vendor": v.code}

    return mutate(
        db, request, p, key, f"purchase.complete:{shipment_id}", body, 200,
        "shipment.complete", fn,
    )  # fmt: skip


@router.post("/purchases/{shipment_id}/lines", status_code=201, response_model=ShipmentDetail)
def add_lines(
    shipment_id: uuid.UUID,
    body: PurchaseLinesIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """More lines of the same invoice (typed by hand after a scan, or missed the first time).
    Each is added to stock at once; lines already saved are never changed."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.record", body.version, "POSTED")
        if s.entry == "LEGACY" or s.needs_details:
            raise ApiError(
                409,
                "INVALID_STATE",
                "Complete this purchase first"
                if s.needs_details
                else "Lines can't be added to a shipment from the old receiving flow",
            )
        specs = [spec_from(db, p, ln) for ln in body.lines]
        detail = stock_lines(db, p, s, specs, [], None)
        touch(s)
        db.flush()
        return detail_out(db, s), str(s.id), detail

    return mutate(
        db, request, p, key, f"purchase.lines:{shipment_id}", body, 201, "shipment.add_lines", fn
    )


@router.post("/purchases/{shipment_id}/stock", response_model=ShipmentDetail)
def stock_old_shipment(
    shipment_id: uuid.UUID,
    body: VersionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """A shipment started in the old flow that never reached stock: add it now, without the
    count-approval step. Its count is posted as counted (good → stock, damaged → put aside);
    with no count, what its documents say is taken as arrived."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.record", body.version, "OPEN", "COUNTED")
        _, declared, counted, _c = _load(db, s)
        actor = p.employee.id
        if counted:
            grn_no, mvs = svc.post_grn(db, s, actor)
            source = "count"
        else:
            rows = svc.compare(declared, [])
            if any(r.status == "DOC_CONFLICT" for r in rows):
                raise ApiError(
                    409,
                    "DOC_CONFLICT",
                    "The supplier documents list different quantities. Withdraw the wrong "
                    "line first.",
                )
            specs = []
            for r in rows:
                assert r.declared_qty is not None
                prod = product_for(db, p, r.product_id, r.unit_code)
                if prod.tracks_expiry:
                    raise ApiError(422, "EXPIRY_REQUIRED", f"{prod.sku} requires an expiry date")
                specs.append(
                    pur.LineSpec(
                        product=prod,
                        unit_code=r.unit_code,
                        qty=r.declared_qty,
                        unit_cost=svc.unit_cost_for(declared, r.product_id, r.unit_code),
                        label=prod.name,
                    )
                )
            if not specs:
                raise ApiError(422, "NO_LINES", "This shipment has no lines to add to stock")
            # POSTED and its GRN number are saved together (the database requires both at once)
            grn_no = svc.next_grn_no(db, s.company_id)
            s.status, s.grn_no = "POSTED", grn_no
            s.posted_by, s.posted_at = actor, svc.now()
            _, mvs = pur.receive(db, s, specs, actor)
            source = "documents"
        s.status, s.grn_no = "POSTED", grn_no
        if s.posted_at is None:
            s.posted_by, s.posted_at = actor, svc.now()
        touch(s)
        db.flush()
        detail = {
            "shipment_no": s.shipment_no,
            "grn_no": grn_no,
            "from": source,
            "movements": [str(m.id) for m in mvs],
        }
        return detail_out(db, s), str(s.id), detail

    return mutate(
        db, request, p, key, f"purchase.stock:{shipment_id}", body, 200, "shipment.stock", fn
    )


# ------------------------------------------------------------------ airport verification


@router.post("/purchases/{shipment_id}/verify", response_model=ShipmentDetail)
def verify_pieces(
    shipment_id: uuid.UUID,
    body: VerifyIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Airport shipment: record the pieces counted on arrival. A match marks it Verified. A
    difference becomes shortage / damage / extra corrections with the reason given: posted at
    once when the person holds `grn.approve`, otherwise waiting for a manager's OK."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.record", body.version, "POSTED")
        if not s.is_airport or s.verify_status is None:
            raise ApiError(409, "NOT_AIRPORT", "Only airport shipments are verified on arrival")
        if s.verify_status != "TO_VERIFY":
            raise ApiError(
                409,
                "INVALID_STATE",
                "This shipment is already verified"
                if s.verify_status == "VERIFIED"
                else "A difference on this shipment is waiting for a manager's OK",
                verify_status=s.verify_status,
            )
        own = {ln.id: ln for ln in _own_lines(db, s)}
        seen: set[uuid.UUID] = set()
        found: list[tuple[GrnLine, str, Decimal]] = []
        for row in body.lines:
            ln = own.get(row.line_id)
            if ln is None or ln.id in seen:
                raise ApiError(422, "INVALID_LINE", "That line is not on this shipment")
            seen.add(ln.id)
            arrived = (
                inv.check_qty(ln.unit_code, row.arrived_qty) if row.arrived_qty else Decimal(0)
            )
            damaged = (
                inv.check_qty(ln.unit_code, row.damaged_qty) if row.damaged_qty else Decimal(0)
            )
            if damaged > arrived:
                raise ApiError(
                    422, "DAMAGED_OVER_ARRIVED", "Damaged can't be more than what arrived"
                )
            expected = pur.effective_qty(db, ln)
            if arrived < expected:
                found.append((ln, "SHORT", expected - arrived))
            elif arrived > expected:
                found.append((ln, "OVER", arrived - expected))
            if damaged > 0:
                found.append((ln, "DAMAGED", damaged))
        expected_pieces = (
            s.expected_pieces
            if s.expected_pieces is not None
            else pur.line_pieces(list(own.values()))
        )
        why = (body.reason or "").strip()
        actor = p.employee.id
        corrs: list[PurchaseCorrection] = []
        if not found:
            if expected_pieces is not None and body.pieces_counted != expected_pieces:
                raise ApiError(
                    422,
                    "PIECES_DIFFER",
                    f"{body.pieces_counted} pieces counted, {expected_pieces} expected. "
                    "Enter what arrived for the lines that differ.",
                    expected=expected_pieces,
                    counted=body.pieces_counted,
                )
            s.verify_status = "VERIFIED"
        else:
            if len(why) < 3:
                raise ApiError(422, "REASON_REQUIRED", "Say why the count differs")
            corrs = [
                pur.new_correction(db, s, ln, kind, qty, why, "VERIFY", actor)
                for ln, kind, qty in found
            ]
            if p.can("grn.approve", s.warehouse_id):
                for c in corrs:
                    pur.post_correction(db, c, actor)
                s.verify_status = "VERIFIED"
            else:
                s.verify_status = "PENDING_OK"
        s.verified_pieces = body.pieces_counted
        s.verified_by, s.verified_at = actor, svc.now()
        touch(s)
        db.flush()
        detail = {
            "shipment_no": s.shipment_no,
            "pieces_counted": body.pieces_counted,
            "pieces_expected": expected_pieces,
            "verify_status": s.verify_status,
            "reason": why or None,
            **_alerts(corrs),
        }
        return detail_out(db, s), str(s.id), detail

    return mutate(
        db, request, p, key, f"purchase.verify:{shipment_id}", body, 200, "shipment.verify", fn
    )


# ------------------------------------------------------------------ corrections


@router.post("/purchases/{shipment_id}/corrections", status_code=201, response_model=ShipmentDetail)
def add_correction(
    shipment_id: uuid.UUID,
    body: CorrectionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """A later fix to a line in stock: short, damaged, returned to the supplier, or more than
    invoiced. Always with a reason. Posted at once by someone holding `grn.approve`; otherwise
    it waits for a manager's OK. The saved line and its ledger rows are never edited."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.record", body.version, "POSTED")
        ln = _line_of(db, s, body.line_id)
        if ln.lot_id is None:
            raise ApiError(409, "LINE_NOT_IN_STOCK", "That line is not in stock")
        qty = inv.check_qty(ln.unit_code, body.qty)
        if body.kind != "OVER" and qty > pur.effective_qty(db, ln):
            raise ApiError(
                422,
                "MORE_THAN_RECEIVED",
                f"Only {_num(pur.effective_qty(db, ln))} {ln.unit_code.lower()} were received on "
                "this line",
            )
        c = pur.new_correction(db, s, ln, body.kind, qty, body.reason, "MANUAL", p.employee.id)
        if p.can("grn.approve", s.warehouse_id):
            pur.post_correction(db, c, p.employee.id)
        touch(s)
        db.flush()
        return detail_out(db, s), str(s.id), {"shipment_no": s.shipment_no, **_alerts([c])}

    return mutate(
        db, request, p, key, f"purchase.correction:{shipment_id}", body, 201,
        "shipment.correction", fn,
    )  # fmt: skip


@router.post(
    "/purchases/{shipment_id}/corrections/{correction_id}/approve", response_model=ShipmentDetail
)
def approve_correction(
    shipment_id: uuid.UUID,
    correction_id: uuid.UUID,
    body: CorrectionDecisionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """The manager's OK: post the correction to stock. Exactly once (row locks + posting keys).
    Stock already invoiced or reserved is left alone and the rest is flagged for review."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.approve", body.version, "POSTED")
        c = _correction(db, s, correction_id)
        mvs = pur.post_correction(db, c, p.employee.id, body.note)
        _settle_verification(db, s)
        touch(s)
        db.flush()
        detail = {
            "shipment_no": s.shipment_no,
            "movements": [str(m.id) for m in mvs],
            "verify_status": s.verify_status,
            **_alerts([c]),
        }
        return detail_out(db, s), str(s.id), detail

    return mutate(
        db, request, p, key, f"purchase.correction_approve:{correction_id}", body, 200,
        "shipment.correction_approve", fn,
    )  # fmt: skip


@router.post(
    "/purchases/{shipment_id}/corrections/{correction_id}/reject", response_model=ShipmentDetail
)
def reject_correction(
    shipment_id: uuid.UUID,
    correction_id: uuid.UUID,
    body: CorrectionDecisionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Refuse a correction that is waiting: nothing is posted. A refused difference from an
    airport verification sends the shipment back to be counted again."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.approve", body.version, "POSTED")
        c = _correction(db, s, correction_id)
        if c.status != "PENDING":
            raise ApiError(409, "INVALID_STATE", f"That correction is already {c.status.lower()}")
        note = (body.note or "").strip()
        if len(note) < 3:
            raise ApiError(422, "REASON_REQUIRED", "Say why it is refused")
        c.status = "REJECTED"
        c.decided_by, c.decided_at, c.decision_note = p.employee.id, svc.now(), note
        db.flush()
        _settle_verification(db, s)
        touch(s)
        db.flush()
        detail = {
            "shipment_no": s.shipment_no,
            "correction_id": str(c.id),
            "kind": c.kind,
            "qty": str(c.qty),
            "note": note,
            "verify_status": s.verify_status,
        }
        return detail_out(db, s), str(s.id), detail

    return mutate(
        db, request, p, key, f"purchase.correction_reject:{correction_id}", body, 200,
        "shipment.correction_reject", fn,
    )  # fmt: skip


@router.post(
    "/purchases/{shipment_id}/corrections/{correction_id}/reviewed", response_model=ShipmentDetail
)
def mark_reviewed(
    shipment_id: uuid.UUID,
    correction_id: uuid.UUID,
    body: CorrectionDecisionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """A shortage on stock that was already invoiced has been looked at (customer credited,
    stock counted, …): clear the flag, with a note on what was done."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.approve", body.version, "POSTED")
        c = _correction(db, s, correction_id)
        if not c.needs_review:
            raise ApiError(409, "INVALID_STATE", "That correction is not waiting for a review")
        note = (body.note or "").strip()
        if len(note) < 3:
            raise ApiError(422, "REASON_REQUIRED", "Say what was done about it")
        c.needs_review = False
        c.review_note, c.reviewed_by, c.reviewed_at = note, p.employee.id, svc.now()
        touch(s)
        db.flush()
        detail = {"shipment_no": s.shipment_no, "correction_id": str(c.id), "note": note}
        return detail_out(db, s), str(s.id), detail

    return mutate(
        db, request, p, key, f"purchase.correction_reviewed:{correction_id}", body, 200,
        "shipment.correction_reviewed", fn,
    )  # fmt: skip
