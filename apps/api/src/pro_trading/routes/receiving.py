"""Module 08 vendors, shipments (vendor documents + declared lines), receiving count and GRN post.

Reads need `grn.record` or `grn.approve` in the shipment's warehouse. Counting/entry needs
`grn.record`; posting and cancelling need `grn.approve`. Every command locks the shipment row,
checks its optimistic version, and runs inside `mutate()` (idempotency + audit + commit).
Declared quantities never touch stock; only `POST /shipments/{id}/post` does, exactly once.
"""

import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import documents, idempotency, scanning, units
from .. import inventory as inv
from .. import purchases as pur
from .. import receiving as svc
from ..deps import DbDep, Principal, PrincipalDep, authorize, deny, require_permission
from ..errors import ApiError
from ..gmail import processing as gmail_files
from ..gmail.models import GmailAttachment, GmailMessage
from ..models import (
    DeclaredLine,
    Employee,
    GrnLine,
    InventoryLot,
    LandedCost,
    Product,
    ProductAlias,
    PurchaseCorrection,
    ScanJob,
    Shipment,
    ShipmentDocument,
    SourceDocument,
    Vendor,
    Warehouse,
)
from ..schemas import (
    DeclaredLineIn,
    GrnLineEditIn,
    GrnLineIn,
    GrnPostIn,
    LandedCostIn,
    ReasonIn,
    ShipmentDetail,
    ShipmentDocIn,
    ShipmentDocKind,
    ShipmentDocUploadIn,
    ShipmentGmailDocIn,
    ShipmentIn,
    ShipmentPage,
    VendorIn,
    VendorOut,
    VendorPatch,
    VersionIn,
)
from ._common import esc, mutate
from .catalog import norm
from .scans import RawBody, job_out
from .scans import _can_read as can_read_scan

router = APIRouter(tags=["receiving"])
Manage = Depends(require_permission("catalog.manage"))
Result = tuple[dict[str, Any], str, dict[str, Any]]
READ_PERMS = ("grn.record", "grn.approve")


# ------------------------------------------------------------------ helpers


def can_read(p: Principal, warehouse_id: uuid.UUID) -> bool:
    return any(p.can(perm, warehouse_id) for perm in READ_PERMS)


def readable(db: Session, p: Principal) -> set[uuid.UUID]:
    ids = db.scalars(select(Warehouse.id).where(Warehouse.company_id == p.company_id)).all()
    return {w for w in ids if can_read(p, w)}


def get_shipment(db: Session, p: Principal, sid: uuid.UUID, lock: bool = False) -> Shipment:
    s = db.get(Shipment, sid, with_for_update=lock, populate_existing=lock)
    if s is None or s.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Shipment not found")
    if lock:
        db.refresh(s)
    return s


def get_vendor(db: Session, p: Principal, vid: uuid.UUID, lock: bool = False) -> Vendor:
    v = db.get(Vendor, vid, with_for_update=lock, populate_existing=lock)
    if v is None or v.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Vendor not found")
    return v


def check_version(current: int, sent: int) -> None:
    if current != sent:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            "This shipment changed since you loaded it; reload",
            current_version=current,
        )


def need_status(s: Shipment, *allowed: str) -> None:
    if s.status not in allowed:
        raise ApiError(
            409,
            "INVALID_STATE",
            f"Shipment is {s.status.lower()}",
            shipment_status=s.status,
            allowed=list(allowed),
        )


def touch(s: Shipment) -> None:
    s.version += 1
    s.updated_at = svc.now()


def lock_for(
    db: Session,
    request: Request,
    p: Principal,
    sid: uuid.UUID,
    perm: str,
    version: int | None,
    *allowed: str,
) -> Shipment:
    """Load + authorize (404 before 403 is fine: ids are unguessable) + lock + version + state.
    `version=None`: the caller's own record carries the version check (a scan review)."""
    s = get_shipment(db, p, sid)
    authorize(request, p, perm, s.warehouse_id)
    s = get_shipment(db, p, sid, lock=True)
    if version is not None:
        check_version(s.version, version)
    need_status(s, *allowed)
    return s


def product_for(db: Session, p: Principal, pid: uuid.UUID, unit: str) -> Product:
    prod = db.get(Product, pid)
    if prod is None or prod.company_id != p.company_id:
        raise ApiError(422, "INVALID_PRODUCT", "Product not found")
    if not prod.active:
        raise ApiError(409, "PRODUCT_INACTIVE", f"{prod.sku} is inactive")
    inv.check_unit(db, prod, unit)
    return prod


def _names(db: Session, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    ids_ = {i for i in ids if i}
    if not ids_:
        return {}
    return {e.id: e.display_name for e in db.scalars(select(Employee).where(Employee.id.in_(ids_)))}


def _d(v: Decimal | None) -> str | None:
    return None if v is None else str(inv.q4(v))


def vendor_alias_label(v: Vendor) -> str:
    return f"vendor:{v.code}"


def _items(
    declared: list[DeclaredLine], counted: list[GrnLine], prods: dict[uuid.UUID, Product]
) -> list[dict[str, Any]]:
    """The goods of a purchase for lists: its invoice lines, or (a quick receive, an old
    shipment with a count only) what was added to stock."""
    live = [d for d in declared if d.removed_at is None]
    rows = (
        [(d.product_id, d.unit_code, d.declared_qty) for d in live]
        if live
        else [(c.product_id, c.unit_code, c.accepted_qty) for c in counted]
    )
    return [
        {
            "product_id": str(pid),
            "sku": prods[pid].sku,
            "product_name": prods[pid].name,
            "unit_code": unit,
            "qty": _d(qty),
        }
        for pid, unit, qty in rows
        if pid in prods
    ]


def _products(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, Product]:
    return {x.id: x for x in db.scalars(select(Product).where(Product.id.in_(ids)))} if ids else {}


def _summary(
    db: Session,
    s: Shipment,
    names: dict[uuid.UUID, str],
    docs: int,
    declared: list[DeclaredLine],
    counted: list[GrnLine],
    prods: dict[uuid.UUID, Product] | None = None,
) -> dict[str, Any]:
    w = db.get(Warehouse, s.warehouse_id)
    v = db.get(Vendor, s.vendor_id) if s.vendor_id else None
    assert w is not None
    if prods is None:
        prods = _products(db, {d.product_id for d in declared} | {c.product_id for c in counted})
    # The old flow compares documents with the count; a purchase has nothing to compare.
    diffs = len(discrepancies(svc.compare(declared, counted))) if s.entry == "LEGACY" else 0
    total, unpriced = pur.bill(declared)
    items = _items(declared, counted, prods)
    pending, review = (
        db.execute(
            select(
                func.count().filter(PurchaseCorrection.status == "PENDING"),
                func.count().filter(PurchaseCorrection.needs_review.is_(True)),
            ).where(PurchaseCorrection.shipment_id == s.id)
        ).one()
        if s.status == "POSTED" and s.entry != "LEGACY"
        else (0, 0)
    )
    matched = db.get(Shipment, s.matched_shipment_id) if s.matched_shipment_id else None
    return {
        "id": str(s.id),
        "shipment_no": s.shipment_no,
        "status": s.status,
        "warehouse_id": str(s.warehouse_id),
        "warehouse_code": w.code,
        "vendor_id": str(v.id) if v else None,
        "vendor_code": v.code if v else "",
        "vendor_name": v.name if v else "",
        "entry": s.entry,
        "stage": pur.stage(s),
        "invoice_date": s.invoice_date.isoformat() if s.invoice_date else None,
        "is_airport": s.is_airport,
        "expected_pieces": s.expected_pieces,
        "verify_status": s.verify_status,
        "verified_pieces": s.verified_pieces,
        "verified_by_name": names.get(s.verified_by) if s.verified_by else None,
        "verified_at": s.verified_at.isoformat() if s.verified_at else None,
        "needs_details": s.needs_details,
        "quick_reason": s.quick_reason,
        "details_completed_by_name": (
            names.get(s.details_completed_by) if s.details_completed_by else None
        ),
        "details_completed_at": (
            s.details_completed_at.isoformat() if s.details_completed_at else None
        ),
        "matched_shipment_id": str(matched.id) if matched else None,
        "matched_shipment_no": matched.shipment_no if matched else None,
        "total": str(total),
        "unpriced_lines": unpriced,
        "line_count": len(items),
        "items": items[:3],
        "pending_corrections": pending,
        "review_count": review,
        "vendor_invoice_no": s.vendor_invoice_no,
        "awb_no": s.awb_no,
        "currency": s.currency,
        "expected_on": s.expected_on.isoformat() if s.expected_on else None,
        "note": s.note,
        "document_count": docs,
        "discrepancy_count": diffs,
        "grn_no": s.grn_no,
        "created_by_name": names.get(s.created_by, ""),
        "counted_by_name": names.get(s.counted_by) if s.counted_by else None,
        "counted_at": s.counted_at.isoformat() if s.counted_at else None,
        "posted_by_name": names.get(s.posted_by) if s.posted_by else None,
        "posted_at": s.posted_at.isoformat() if s.posted_at else None,
        "discrepancy_note": s.discrepancy_note,
        "cancel_reason": s.cancel_reason,
        "version": s.version,
        "created_at": s.created_at.isoformat(),
    }


def _load(
    db: Session, s: Shipment
) -> tuple[list[ShipmentDocument], list[DeclaredLine], list[GrnLine], list[LandedCost]]:
    docs = list(
        db.scalars(
            select(ShipmentDocument)
            .where(ShipmentDocument.shipment_id == s.id)
            .order_by(ShipmentDocument.created_at)
        )
    )
    declared = list(
        db.scalars(
            select(DeclaredLine)
            .where(DeclaredLine.shipment_id == s.id)
            .order_by(DeclaredLine.created_at)
        )
    )
    counted = list(
        db.scalars(select(GrnLine).where(GrnLine.shipment_id == s.id).order_by(GrnLine.seq))
    )
    costs = list(
        db.scalars(
            select(LandedCost).where(LandedCost.shipment_id == s.id).order_by(LandedCost.created_at)
        )
    )
    return docs, declared, counted, costs


def discrepancies(rows: list[svc.Row]) -> list[svc.Row]:
    return [r for r in rows if r.status != "MATCH"]


def batch_prefix(db: Session, s: Shipment) -> str:
    return pur.batch_prefix(db, s)


def _corrections(db: Session, s: Shipment) -> list[PurchaseCorrection]:
    return list(
        db.scalars(
            select(PurchaseCorrection)
            .where(PurchaseCorrection.shipment_id == s.id)
            .order_by(PurchaseCorrection.created_at, PurchaseCorrection.id)
        )
    )


def _absorbed(db: Session, s: Shipment) -> list[Shipment]:
    return list(
        db.scalars(
            select(Shipment)
            .where(Shipment.matched_shipment_id == s.id)
            .order_by(Shipment.created_at, Shipment.id)
        )
    )


def detail_out(db: Session, s: Shipment) -> dict[str, Any]:
    docs, declared, counted, costs = _load(db, s)
    rows = svc.compare(declared, counted)
    corrections = _corrections(db, s)
    absorbed = _absorbed(db, s)
    fix_lines = {
        ln.id: ln
        for ln in db.scalars(
            select(GrnLine).where(GrnLine.id.in_([c.grn_line_id for c in corrections]))
        )
    }
    absorbed_lines = {
        q.id: list(db.scalars(select(GrnLine).where(GrnLine.shipment_id == q.id))) for q in absorbed
    }
    names = _names(
        db,
        {s.created_by, s.counted_by, s.posted_by, s.verified_by, s.details_completed_by}
        | {d.added_by for d in docs}
        | {d.created_by for d in declared}
        | {c.counted_by for c in counted}
        | {c.created_by for c in costs}
        | {x for c in corrections for x in (c.created_by, c.decided_by, c.reviewed_by)}
        | {q.created_by for q in absorbed},
    )
    prods = _products(
        db,
        {d.product_id for d in declared}
        | {c.product_id for c in counted}
        | {ln.product_id for ln in fix_lines.values()}
        | {ln.product_id for lines in absorbed_lines.values() for ln in lines},
    )
    lot_ids = [c.lot_id for c in counted if c.lot_id] + [
        ln.lot_id for ln in fix_lines.values() if ln.lot_id
    ]
    lot_codes = {
        lot.id: lot.lot_code
        for lot in db.scalars(select(InventoryLot).where(InventoryLot.id.in_(lot_ids)))
    }
    src = {d.id: db.get(SourceDocument, d.document_id) for d in docs}
    jobs = {d.id: db.get(ScanJob, d.scan_job_id) if d.scan_job_id else None for d in docs}
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for c in costs:
        if c.removed_at is None:
            totals[c.currency] += c.amount
    out = _summary(db, s, names, len(docs), declared, counted, prods)
    out["documents"] = [_doc_out(d, src[d.id], jobs[d.id], names) for d in docs]
    out["declared"] = [
        {
            "id": str(d.id),
            "shipment_document_id": str(d.shipment_document_id) if d.shipment_document_id else None,
            "raw_label": d.raw_label,
            "product_id": str(d.product_id),
            "sku": prods[d.product_id].sku,
            "product_name": prods[d.product_id].name,
            "unit_code": d.unit_code,
            "declared_qty": _d(d.declared_qty),
            "unit_cost": _d(d.unit_cost),
            "created_by_name": names.get(d.created_by, ""),
            "created_at": d.created_at.isoformat(),
            "removed_at": d.removed_at.isoformat() if d.removed_at else None,
            "removed_reason": d.removed_reason,
        }
        for d in declared
    ]
    out["counted"] = [
        {
            "id": str(c.id),
            "seq": c.seq,
            "product_id": str(c.product_id),
            "sku": prods[c.product_id].sku,
            "product_name": prods[c.product_id].name,
            "unit_code": c.unit_code,
            "accepted_qty": _d(c.accepted_qty),
            "damaged_qty": _d(c.damaged_qty),
            "rejected_qty": _d(c.rejected_qty),
            "batch_ref": c.batch_ref,
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
            "note": c.note,
            "unit_cost": _d(c.unit_cost),
            "lot_id": str(c.lot_id) if c.lot_id else None,
            "lot_code": lot_codes.get(c.lot_id) if c.lot_id else None,
            "counted_by_name": names.get(c.counted_by, ""),
            "version": c.version,
        }
        for c in counted
    ]
    out["costs"] = [
        {
            "id": str(c.id),
            "kind": c.kind,
            "amount": str(c.amount),
            "currency": c.currency,
            "shipment_document_id": str(c.shipment_document_id) if c.shipment_document_id else None,
            "note": c.note,
            "created_by_name": names.get(c.created_by, ""),
            "created_at": c.created_at.isoformat(),
            "removed_at": c.removed_at.isoformat() if c.removed_at else None,
            "removed_reason": c.removed_reason,
        }
        for c in costs
    ]
    out["comparison"] = [_row(r, prods) for r in rows]
    out["next_batch_ref"] = svc.peek_batch_ref(db, s.company_id, batch_prefix(db, s))
    out["cost_totals"] = {k: str(v) for k, v in sorted(totals.items())}
    out["corrections"] = [
        _correction_out(db, c, fix_lines[c.grn_line_id], prods, lot_codes, names)
        for c in corrections
    ]
    out["absorbed"] = [
        {
            "id": str(q.id),
            "shipment_no": q.shipment_no,
            "quick_reason": q.quick_reason,
            "created_by_name": names.get(q.created_by, ""),
            "created_at": q.created_at.isoformat(),
            "items": _items([], absorbed_lines[q.id], prods),
        }
        for q in absorbed
    ]
    out["line_pieces"] = pur.line_pieces(counted)
    return out


def _correction_out(
    db: Session,
    c: PurchaseCorrection,
    ln: GrnLine,
    prods: dict[uuid.UUID, Product],
    lot_codes: dict[uuid.UUID, str],
    names: dict[uuid.UUID, str],
) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "grn_line_id": str(ln.id),
        "product_id": str(ln.product_id),
        "sku": prods[ln.product_id].sku,
        "product_name": prods[ln.product_id].name,
        "lot_id": str(ln.lot_id) if ln.lot_id else None,
        "lot_code": lot_codes.get(ln.lot_id) if ln.lot_id else None,
        "kind": c.kind,
        "unit_code": c.unit_code,
        "qty": _d(c.qty),
        "reason": c.reason,
        "source": c.source,
        "status": c.status,
        "posted_qty": _d(c.posted_qty),
        "uncovered_qty": _d(c.uncovered_qty),
        "needs_review": c.needs_review,
        "review_note": c.review_note,
        "reviewed_by_name": names.get(c.reviewed_by) if c.reviewed_by else None,
        "reviewed_at": c.reviewed_at.isoformat() if c.reviewed_at else None,
        "invoices": (
            pur.invoices_using(db, ln.lot_id) if c.uncovered_qty > 0 and ln.lot_id else []
        ),
        "created_by_name": names.get(c.created_by, ""),
        "created_at": c.created_at.isoformat(),
        "decided_by_name": names.get(c.decided_by) if c.decided_by else None,
        "decided_at": c.decided_at.isoformat() if c.decided_at else None,
        "decision_note": c.decision_note,
    }


def _doc_out(
    d: ShipmentDocument, sd: SourceDocument | None, j: ScanJob | None, names: dict[uuid.UUID, str]
) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "document_id": str(d.document_id),
        "scan_job_id": str(d.scan_job_id) if d.scan_job_id else None,
        "scan_job_no": j.job_no if j else None,
        "kind": d.kind,
        "doc_ref": d.doc_ref,
        "filename": sd.original_filename if sd else "",
        "mime_type": sd.mime_type if sd else "",
        "byte_size": sd.byte_size if sd else 0,
        "sha256": sd.sha256 if sd else "",
        "added_by_name": names.get(d.added_by, ""),
        "created_at": d.created_at.isoformat(),
    }


def _row(r: svc.Row, prods: dict[uuid.UUID, Product]) -> dict[str, Any]:
    return {
        "product_id": str(r.product_id),
        "sku": prods[r.product_id].sku,
        "product_name": prods[r.product_id].name,
        "unit_code": r.unit_code,
        "declared_qty": _d(r.declared_qty),
        "declared_by_document": {k: str(v) for k, v in r.declared_by_document.items()},
        "accepted_qty": _d(r.accepted_qty),
        "damaged_qty": _d(r.damaged_qty),
        "rejected_qty": _d(r.rejected_qty),
        "difference": _d(r.difference),
        "status": r.status,
    }


def get_doc_link(db: Session, s: Shipment, did: uuid.UUID | None) -> ShipmentDocument | None:
    if did is None:
        return None
    d = db.get(ShipmentDocument, did)
    if d is None or d.shipment_id != s.id:
        raise ApiError(422, "INVALID_DOCUMENT", "That document is not attached to this shipment")
    return d


# ------------------------------------------------------------------ vendors


@router.get("/vendors", response_model=list[VendorOut])
def list_vendors(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    active: bool | None = None,
    q: str | None = Query(None, max_length=100),
) -> list[Vendor]:
    if not any(p.can(x, any_scope=True) for x in ("catalog.read", *READ_PERMS)):
        raise deny(request, p, "catalog.read", None)
    stmt = select(Vendor).where(Vendor.company_id == p.company_id)
    if active is not None:
        stmt = stmt.where(Vendor.active.is_(active))
    if q and q.strip():
        like = f"%{esc(q.strip())}%"
        stmt = stmt.where(or_(Vendor.code.ilike(like), Vendor.name.ilike(like)))
    return list(db.scalars(stmt.order_by(Vendor.code).limit(500)))


def _vendor_out(v: Vendor) -> dict[str, Any]:
    return VendorOut.model_validate(v).model_dump(mode="json")


@router.post("/vendors", status_code=201, response_model=VendorOut)
def create_vendor(
    body: VendorIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: Principal = Manage
) -> JSONResponse:
    def fn() -> Result:
        v = Vendor(
            company_id=p.company_id,
            code=body.code,
            name=body.name.strip(),
            country_code=body.country_code,
            email=(body.email or "").strip() or None,
            currency=body.currency,
        )
        db.add(v)
        db.flush()
        return _vendor_out(v), str(v.id), body.model_dump(mode="json")

    return mutate(
        db,
        request,
        p,
        key,
        "vendor.create",
        body,
        201,
        "vendor.create",
        fn,
        "Vendor code already exists",
    )


@router.patch("/vendors/{vendor_id}", response_model=VendorOut)
def update_vendor(
    vendor_id: uuid.UUID,
    body: VendorPatch,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def fn() -> Result:
        v = get_vendor(db, p, vendor_id, lock=True)
        db.refresh(v)
        if v.version != body.version:
            raise ApiError(
                409, "VERSION_CONFLICT", "Vendor changed; reload", current_version=v.version
            )
        before = _vendor_out(v)
        v.name = body.name.strip()
        v.country_code = body.country_code
        v.email = (body.email or "").strip() or None
        v.currency = body.currency
        v.active = body.active
        v.version += 1
        v.updated_at = svc.now()
        db.flush()
        out = _vendor_out(v)
        return out, str(v.id), {"before": before, "after": out}

    return mutate(db, request, p, key, f"vendor.update:{vendor_id}", body, 200, "vendor.update", fn)


# ------------------------------------------------------------------ shipments: reads


@router.get("/shipments", response_model=ShipmentPage)
def list_shipments(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    status: str | None = Query(None, max_length=60, description="Comma-separated statuses"),
    stage: str | None = Query(
        None,
        max_length=120,
        description="Comma-separated purchase stages: NOT_IN_STOCK, TO_COMPLETE, TO_VERIFY, "
        "TO_APPROVE, VERIFIED, IN_STOCK, CANCELLED",
    ),
    airport: bool | None = None,
    warehouse_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, pattern=r"^\d{1,9}$"),
) -> dict[str, Any]:
    whs = readable(db, p)
    if warehouse_id is not None:
        if warehouse_id not in whs:
            raise deny(request, p, "grn.record", warehouse_id)
        whs = {warehouse_id}
    if not whs:
        raise deny(request, p, "grn.record", None)
    stmt = select(Shipment).where(
        Shipment.company_id == p.company_id, Shipment.warehouse_id.in_(whs)
    )
    if status:
        stmt = stmt.where(Shipment.status.in_([x.strip().upper() for x in status.split(",")]))
    if stage:
        wanted = [x.strip().upper() for x in stage.split(",") if x.strip()]
        if bad := [x for x in wanted if x not in pur.STAGES]:
            raise ApiError(422, "INVALID_STAGE", f"Unknown stage: {', '.join(bad)}")
        stmt = stmt.where(or_(*[pur.stage_filter(x) for x in wanted]))
    if airport is not None:
        stmt = stmt.where(Shipment.is_airport.is_(airport))
    if vendor_id:
        stmt = stmt.where(Shipment.vendor_id == vendor_id)
    if q and q.strip():
        like = f"%{esc(q.strip())}%"
        stmt = stmt.outerjoin(Vendor, Vendor.id == Shipment.vendor_id).where(
            or_(
                Shipment.shipment_no.ilike(like),
                Shipment.vendor_invoice_no.ilike(like),
                Shipment.awb_no.ilike(like),
                Shipment.grn_no.ilike(like),
                Vendor.name.ilike(like),
                Vendor.code.ilike(like),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    off = int(cursor or 0)
    rows = list(
        db.scalars(stmt.order_by(Shipment.created_at.desc(), Shipment.id).offset(off).limit(limit))
    )
    names = _names(
        db,
        {
            x
            for s in rows
            for x in (
                s.created_by, s.counted_by, s.posted_by, s.verified_by, s.details_completed_by
            )
        },
    )  # fmt: skip
    items = []
    for s in rows:
        _, declared, counted, _c = _load(db, s)
        items.append(_summary(db, s, names, pur.doc_count(db, s.id), declared, counted))
    nxt = off + len(rows)
    return {"items": items, "total": total, "next_cursor": str(nxt) if nxt < total else None}


@router.get("/shipments/{shipment_id}", response_model=ShipmentDetail)
def read_shipment(
    shipment_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    s = get_shipment(db, p, shipment_id)
    if not can_read(p, s.warehouse_id):
        raise deny(request, p, "grn.record", s.warehouse_id)
    return detail_out(db, s)


# ------------------------------------------------------------------ shipments: commands


@router.post("/shipments", status_code=201, response_model=ShipmentDetail)
def create_shipment(
    body: ShipmentIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    authorize(request, p, "grn.record", body.warehouse_id)

    def fn() -> Result:
        w = db.get(Warehouse, body.warehouse_id)
        if w is None or w.company_id != p.company_id:
            raise ApiError(422, "INVALID_WAREHOUSE", "Location not found")
        v = db.get(Vendor, body.vendor_id)
        if v is None or v.company_id != p.company_id:
            raise ApiError(422, "INVALID_VENDOR", "Vendor not found")
        if not v.active:
            raise ApiError(409, "VENDOR_INACTIVE", f"{v.name} is inactive")
        s = Shipment(
            company_id=p.company_id,
            warehouse_id=w.id,
            vendor_id=v.id,
            shipment_no=svc.next_shipment_no(db, p.company_id),
            vendor_invoice_no=(body.vendor_invoice_no or "").strip() or None,
            awb_no=(body.awb_no or "").strip() or None,
            currency=body.currency or v.currency,
            expected_on=body.expected_on,
            note=(body.note or "").strip() or None,
            created_by=p.employee.id,
        )
        db.add(s)
        db.flush()
        return detail_out(db, s), str(s.id), {"shipment_no": s.shipment_no, "vendor": v.code}

    return mutate(db, request, p, key, "shipment.create", body, 201, "shipment.create", fn)


def _attach(
    db: Session,
    s: Shipment,
    p: Principal,
    doc_id: uuid.UUID,
    job_id: uuid.UUID | None,
    kind: ShipmentDocKind,
    doc_ref: str | None,
) -> ShipmentDocument:
    if db.scalar(
        select(ShipmentDocument.id).where(
            ShipmentDocument.shipment_id == s.id, ShipmentDocument.document_id == doc_id
        )
    ):
        raise ApiError(409, "ALREADY_ATTACHED", "This document is already on the shipment")
    if s.vendor_id is None:
        # documents are filed in the supplier's folder
        raise ApiError(
            409,
            "SUPPLIER_NEEDED",
            "Choose the supplier (complete the details) before adding documents",
        )
    link = ShipmentDocument(
        shipment_id=s.id,
        document_id=doc_id,
        scan_job_id=job_id,
        kind=kind,
        doc_ref=(doc_ref or "").strip() or None,
        added_by=p.employee.id,
    )
    db.add(link)
    touch(s)
    db.flush()
    sdoc = db.get(SourceDocument, doc_id)
    assert sdoc is not None
    documents.file_vendor_doc(db, s, link, sdoc)
    return link


@router.post("/shipments/{shipment_id}/documents", status_code=201, response_model=ShipmentDetail)
def attach_scan(
    shipment_id: uuid.UUID,
    body: ShipmentDocIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Attach a document already brought in through the Scan Center (bridge or upload)."""

    def fn() -> Result:
        s = lock_for(
            db, request, p, shipment_id, "grn.record", body.version, "OPEN", "COUNTED", "POSTED"
        )
        j = db.get(ScanJob, body.scan_job_id)
        if j is None or j.company_id != p.company_id or not can_read_scan(p, j.warehouse_id):
            raise ApiError(422, "INVALID_SCAN", "Scan not found")
        if j.status != "UPLOADED" or j.document_id is None:
            raise ApiError(409, "SCAN_NOT_RECEIVED", "That scan has no document yet")
        link = _attach(db, s, p, j.document_id, j.id, body.kind, body.doc_ref)
        return (
            detail_out(db, s),
            str(s.id),
            {"document_id": str(link.document_id), "kind": body.kind, "scan": j.job_no},
        )

    return mutate(
        db,
        request,
        p,
        key,
        f"shipment.doc:{shipment_id}",
        body,
        201,
        "shipment.attach_document",
        fn,
    )


@router.post(
    "/shipments/{shipment_id}/documents/upload",
    status_code=201,
    response_model=ShipmentDetail,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                m: {"schema": {"type": "string", "format": "binary"}}
                for m in ("application/pdf", "image/png", "image/jpeg", "image/tiff")
            },
        }
    },
)
def upload_document(
    shipment_id: uuid.UUID,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
    data: RawBody,
    kind: ShipmentDocKind,
    version: int = Query(ge=1),
    doc_ref: str | None = Query(None, max_length=64),
    filename: str = Query("vendor-document", max_length=255),
) -> JSONResponse:
    """Upload a vendor document straight onto the shipment (raw file body). The bytes are kept
    unaltered as a source document and also appear in the Scan Center as a vendor document."""
    mime = scanning.check_upload(data, request.headers.get("content-type"))
    params = ShipmentDocUploadIn(
        shipment_id=shipment_id,
        kind=kind,
        doc_ref=doc_ref,
        filename=filename,
        sha256=scanning.sha256hex(data),
    )

    def fn() -> Result:
        s = lock_for(
            db, request, p, shipment_id, "grn.record", version, "OPEN", "COUNTED", "POSTED"
        )
        doc, existed = scanning.store_document(
            db, p.company_id, data, mime, filename, p.employee.id, "MANUAL"
        )
        job = _vendor_doc_job(db, p, s, doc, existed, f"Receiving {s.shipment_no}")
        link = _attach(db, s, p, doc.id, job.id, kind, doc_ref)
        return (
            detail_out(db, s),
            str(s.id),
            {
                "document_id": str(link.document_id),
                "kind": kind,
                "sha256": doc.sha256,
                "bytes": len(data),
                "scan": job_out(db, job)["job_no"],
            },
        )

    return mutate(
        db,
        request,
        p,
        key,
        f"shipment.upload:{shipment_id}",
        params,
        201,
        "shipment.upload_document",
        fn,
    )


def _vendor_doc_job(
    db: Session, p: Principal, s: Shipment, doc: SourceDocument, existed: bool, note: str
) -> ScanJob:
    """The Scan Center record of a vendor document brought in from Receiving (reused when the
    same file is already on a scan in this warehouse)."""
    job = (
        db.scalar(
            select(ScanJob)
            .where(ScanJob.document_id == doc.id, ScanJob.warehouse_id == s.warehouse_id)
            .order_by(ScanJob.created_at)
            .limit(1)
        )
        if existed
        else None
    )
    if job is None:
        t = scanning.now()
        job = ScanJob(
            company_id=p.company_id,
            warehouse_id=s.warehouse_id,
            job_no=scanning.next_job_no(db, p.company_id),
            job_type="VENDOR_DOCUMENT",
            channel="MANUAL",
            status="UPLOADED",
            note=note[:500],
            requested_by=p.employee.id,
            document_id=doc.id,
            claimed_at=t,
            completed_at=t,
            duplicate=existed,
        )
        db.add(job)
        db.flush()
    return job


@router.post(
    "/shipments/{shipment_id}/documents/gmail", status_code=201, response_model=ShipmentDetail
)
def attach_gmail(
    shipment_id: uuid.UUID,
    body: ShipmentGmailDocIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Attach a file from the Gmail Inbox to the shipment without downloading it. The file is
    fetched and checked exactly as for a preview (type taken from its bytes, size limit, must
    open), kept unaltered as a source document, and listed in the Scan Center. Nothing is read
    by OCR and no stock changes. Needs `grn.record` here and the right to open Gmail files."""
    s0 = get_shipment(db, p, shipment_id)
    authorize(request, p, "grn.record", s0.warehouse_id)
    authorize(request, p, "gmail.attachment.read")
    a = db.get(GmailAttachment, body.attachment_id)
    if a is None or a.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Attachment not found")
    m = db.get(GmailMessage, a.message_id)
    assert m is not None
    data, mime = gmail_files.load(db, a, m)  # may call Gmail; holds no transaction meanwhile

    def fn() -> Result:
        s = lock_for(
            db, request, p, shipment_id, "grn.record", body.version, "OPEN", "COUNTED", "POSTED"
        )
        doc, existed = scanning.store_document(
            db, p.company_id, data, mime, a.filename, p.employee.id, "GMAIL"
        )
        att = db.get(GmailAttachment, a.id, with_for_update=True)
        if att is not None and att.source_document_id is None:
            att.source_document_id = doc.id
        sender = m.from_name or m.from_email or "unknown sender"
        job = _vendor_doc_job(
            db, p, s, doc, existed, f"Receiving {s.shipment_no} · Gmail: {a.filename} from {sender}"
        )
        link = _attach(db, s, p, doc.id, job.id, body.kind, body.doc_ref)
        return (
            detail_out(db, s),
            str(s.id),
            {
                "document_id": str(link.document_id),
                "kind": body.kind,
                "sha256": doc.sha256,
                "gmail_attachment": str(a.id),
                "filename": a.filename,
                "scan": job.job_no,
            },
        )

    return mutate(
        db,
        request,
        p,
        key,
        f"shipment.gmail:{shipment_id}",
        body,
        201,
        "shipment.attach_gmail",
        fn,
    )


@router.post("/shipments/{shipment_id}/declared", status_code=201, response_model=ShipmentDetail)
def add_declared(
    shipment_id: uuid.UUID,
    body: DeclaredLineIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Record what a vendor document says. Never stock. If no product is chosen, the label is
    matched against the vendor's remembered names, then SKUs/aliases/names company-wide."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.record", body.version, "OPEN", "COUNTED")
        link = get_doc_link(db, s, body.shipment_document_id)
        v = db.get(Vendor, s.vendor_id)
        assert v is not None
        label = body.raw_label.strip()
        pid = body.product_id
        matched_by = "chosen"
        if pid is None:
            n = norm(label)
            hit = (
                db.scalar(
                    select(ProductAlias.product_id).where(
                        ProductAlias.company_id == p.company_id,
                        ProductAlias.alias_norm == n,
                        ProductAlias.source_label == vendor_alias_label(v),
                    )
                )
                or db.scalar(
                    select(Product.id).where(
                        Product.company_id == p.company_id,
                        or_(func.lower(Product.sku) == n, func.lower(Product.name) == n),
                    )
                )
                or db.scalar(
                    select(ProductAlias.product_id)
                    .where(ProductAlias.company_id == p.company_id, ProductAlias.alias_norm == n)
                    .limit(1)
                )
            )
            if hit is None:
                raise ApiError(
                    422,
                    "UNMAPPED_LABEL",
                    f"“{label}” isn't a known product name for {v.name}. Choose the product.",
                )
            pid, matched_by = hit, "alias"
        unit, qty, cost = body.unit_code, body.declared_qty, body.unit_cost
        if kg_counted_in_lb(db, p, pid, unit):  # a kg document for a product kept in lb
            unit, qty = "LB", units.kg_to_lb(qty)
            cost = units.q4(cost * units.LB_TO_KG) if cost is not None else None
        prod = product_for(db, p, pid, unit)
        inv.check_qty(unit, qty)
        d = DeclaredLine(
            shipment_id=s.id,
            shipment_document_id=link.id if link else None,
            raw_label=label,
            product_id=prod.id,
            unit_code=unit,
            declared_qty=inv.q4(qty),
            unit_cost=cost,
            created_by=p.employee.id,
        )
        db.add(d)
        alias_added = False
        if (
            body.remember_alias
            and matched_by == "chosen"
            and norm(label) not in {prod.sku.casefold(), norm(prod.name)}
        ):
            src = vendor_alias_label(v)
            if not db.scalar(
                select(ProductAlias.id).where(
                    ProductAlias.company_id == p.company_id,
                    ProductAlias.alias_norm == norm(label),
                    ProductAlias.source_label == src,
                )
            ):
                db.add(
                    ProductAlias(
                        company_id=p.company_id,
                        product_id=prod.id,
                        alias=label,
                        alias_norm=norm(label),
                        source_label=src,
                    )
                )
                alias_added = True
        touch(s)
        db.flush()
        return (
            detail_out(db, s),
            str(s.id),
            {
                "declared_line_id": str(d.id),
                "sku": prod.sku,
                "unit": unit,
                "qty": str(qty),
                "entered": f"{body.declared_qty} {body.unit_code}",
                "matched_by": matched_by,
                "alias_added": alias_added,
            },
        )

    return mutate(
        db, request, p, key, f"shipment.declare:{shipment_id}", body, 201, "shipment.declare", fn
    )


@router.post("/shipments/{shipment_id}/declared/{line_id}/withdraw", response_model=ShipmentDetail)
def withdraw_declared(
    shipment_id: uuid.UUID,
    line_id: uuid.UUID,
    body: ReasonIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.record", body.version, "OPEN", "COUNTED")
        d = db.get(DeclaredLine, line_id)
        if d is None or d.shipment_id != s.id:
            raise ApiError(404, "NOT_FOUND", "Declared line not found")
        if d.removed_at is not None:
            raise ApiError(409, "ALREADY_WITHDRAWN", "That line was already withdrawn")
        d.removed_at = svc.now()
        d.removed_by = p.employee.id
        d.removed_reason = body.reason.strip()
        touch(s)
        db.flush()
        return (
            detail_out(db, s),
            str(s.id),
            {"declared_line_id": str(d.id), "reason": d.removed_reason},
        )

    return mutate(
        db,
        request,
        p,
        key,
        f"shipment.withdraw:{line_id}",
        body,
        200,
        "shipment.withdraw_declared",
        fn,
    )


def _num(q: Decimal) -> str:
    return f"{q.normalize():f}"


def kg_counted_in_lb(db: Session, p: Principal, pid: uuid.UUID, unit: str) -> bool:
    """A product kept in lb may be counted from a kg document: the kg is converted to lb when
    saved, so its stock stays in one measure (KG is a stock unit only for kg products)."""
    if unit != "KG":
        return False
    prod = db.get(Product, pid)
    return (
        prod is not None
        and prod.company_id == p.company_id
        and "KG" not in inv.stock_units(db, prod)
    )


def _count_in_stock_unit[T: GrnLineIn](db: Session, p: Principal, body: T) -> T:
    if not kg_counted_in_lb(db, p, body.product_id, body.unit_code):
        return body
    parts = [
        f"{_num(q)} kg {what}"
        for q, what in (
            (body.accepted_qty, "good"),
            (body.damaged_qty, "damaged"),
            (body.rejected_qty, "refused"),
        )
        if q > 0
    ]
    said = "Counted in kg: " + ", ".join(parts)
    own = (body.note or "").strip()
    note = f"{own} · {said}" if own else said
    return body.model_copy(
        update={
            "unit_code": "LB",
            "accepted_qty": units.kg_to_lb(body.accepted_qty),
            "damaged_qty": units.kg_to_lb(body.damaged_qty),
            "rejected_qty": units.kg_to_lb(body.rejected_qty),
            "note": note[:500],
        }
    )


def _check_count(db: Session, p: Principal, body: GrnLineIn) -> Product:
    prod = product_for(db, p, body.product_id, body.unit_code)
    total = body.accepted_qty + body.damaged_qty + body.rejected_qty
    if total <= 0:
        raise ApiError(
            422, "EMPTY_COUNT", "Enter at least one accepted, damaged or rejected quantity"
        )
    for q in (body.accepted_qty, body.damaged_qty, body.rejected_qty):
        if q > 0:
            inv.check_qty(body.unit_code, q)
    if prod.tracks_expiry and body.expires_at is None and body.accepted_qty + body.damaged_qty > 0:
        raise ApiError(422, "EXPIRY_REQUIRED", f"{prod.sku} requires an expiry date")
    return prod


@router.post("/shipments/{shipment_id}/count", status_code=201, response_model=ShipmentDetail)
def add_count(
    shipment_id: uuid.UUID,
    body: GrnLineIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Record a physical count line (accepted / damaged / rejected). Not stock until posted."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.record", body.version, "OPEN")
        b = _count_in_stock_unit(db, p, body)
        prod = _check_count(db, p, b)
        seq = (db.scalar(select(func.max(GrnLine.seq)).where(GrnLine.shipment_id == s.id)) or 0) + 1
        ln = GrnLine(
            shipment_id=s.id,
            seq=seq,
            product_id=prod.id,
            unit_code=b.unit_code,
            accepted_qty=inv.q4(b.accepted_qty),
            damaged_qty=inv.q4(b.damaged_qty),
            rejected_qty=inv.q4(b.rejected_qty),
            batch_ref=(b.batch_ref or "").strip()
            or svc.next_batch_ref(db, p.company_id, batch_prefix(db, s)),
            expires_at=b.expires_at,
            note=(b.note or "").strip() or None,
            counted_by=p.employee.id,
        )
        db.add(ln)
        touch(s)
        db.flush()
        return (
            detail_out(db, s),
            str(s.id),
            {
                "grn_line_id": str(ln.id),
                "sku": prod.sku,
                "batch": ln.batch_ref,
                "unit": b.unit_code,
                "accepted": str(b.accepted_qty),
                "damaged": str(b.damaged_qty),
                "rejected": str(b.rejected_qty),
            },
        )

    return mutate(
        db, request, p, key, f"shipment.count:{shipment_id}", body, 201, "shipment.count", fn
    )


@router.put("/shipments/{shipment_id}/count/{line_id}", response_model=ShipmentDetail)
def edit_count(
    shipment_id: uuid.UUID,
    line_id: uuid.UUID,
    body: GrnLineEditIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.record", body.version, "OPEN")
        ln = db.get(GrnLine, line_id, with_for_update=True, populate_existing=True)
        if ln is None or ln.shipment_id != s.id:
            raise ApiError(404, "NOT_FOUND", "Count line not found")
        if ln.version != body.line_version:
            raise ApiError(409, "VERSION_CONFLICT", "That count line changed; reload")
        b = _count_in_stock_unit(db, p, body)
        prod = _check_count(db, p, b)
        before = {
            "accepted": str(ln.accepted_qty),
            "damaged": str(ln.damaged_qty),
            "rejected": str(ln.rejected_qty),
        }
        ln.product_id = prod.id
        ln.unit_code = b.unit_code
        ln.accepted_qty = inv.q4(b.accepted_qty)
        ln.damaged_qty = inv.q4(b.damaged_qty)
        ln.rejected_qty = inv.q4(b.rejected_qty)
        # Left empty: keep the line's batch name, or give it one if it had none (D-37).
        ln.batch_ref = (
            (b.batch_ref or "").strip()
            or ln.batch_ref
            or svc.next_batch_ref(db, p.company_id, batch_prefix(db, s))
        )
        ln.expires_at = b.expires_at
        ln.note = (b.note or "").strip() or None
        ln.counted_by = p.employee.id
        ln.version += 1
        ln.updated_at = svc.now()
        touch(s)
        db.flush()
        after = {
            "accepted": str(ln.accepted_qty),
            "damaged": str(ln.damaged_qty),
            "rejected": str(ln.rejected_qty),
        }
        return (
            detail_out(db, s),
            str(s.id),
            {"grn_line_id": str(ln.id), "sku": prod.sku, "before": before, "after": after},
        )

    return mutate(
        db, request, p, key, f"shipment.count_edit:{line_id}", body, 200, "shipment.count_edit", fn
    )


@router.post("/shipments/{shipment_id}/count/{line_id}/remove", response_model=ShipmentDetail)
def remove_count(
    shipment_id: uuid.UUID,
    line_id: uuid.UUID,
    body: VersionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Drop an unposted count line (a miscount). The audit log keeps what it said."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.record", body.version, "OPEN")
        ln = db.get(GrnLine, line_id)
        if ln is None or ln.shipment_id != s.id:
            raise ApiError(404, "NOT_FOUND", "Count line not found")
        gone = {
            "grn_line_id": str(ln.id),
            "product_id": str(ln.product_id),
            "unit": ln.unit_code,
            "accepted": str(ln.accepted_qty),
            "damaged": str(ln.damaged_qty),
            "rejected": str(ln.rejected_qty),
        }
        db.delete(ln)
        touch(s)
        db.flush()
        return detail_out(db, s), str(s.id), gone

    return mutate(
        db,
        request,
        p,
        key,
        f"shipment.count_remove:{line_id}",
        body,
        200,
        "shipment.count_remove",
        fn,
    )


@router.post("/shipments/{shipment_id}/costs", status_code=201, response_model=ShipmentDetail)
def add_cost(
    shipment_id: uuid.UUID,
    body: LandedCostIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Capture freight/duty/etc. Allowed after posting too (bills arrive late). Never allocated
    to lots automatically. Does not change the shipment's version once it is posted."""

    def fn() -> Result:
        s = get_shipment(db, p, shipment_id)
        authorize(request, p, "grn.record", s.warehouse_id)
        s = get_shipment(db, p, shipment_id, lock=True)
        need_status(s, "OPEN", "COUNTED", "POSTED")
        if s.status != "POSTED":
            check_version(s.version, body.version)
        if s.entry != "LEGACY" and not s.is_airport:
            raise ApiError(
                422,
                "NOT_AIRPORT",
                "Freight, duty and other landed costs are recorded for airport shipments only",
            )
        link = get_doc_link(db, s, body.shipment_document_id)
        c = LandedCost(
            shipment_id=s.id,
            kind=body.kind,
            amount=body.amount,
            currency=body.currency,
            shipment_document_id=link.id if link else None,
            note=(body.note or "").strip() or None,
            created_by=p.employee.id,
        )
        db.add(c)
        if s.status != "POSTED":
            touch(s)
        db.flush()
        return (
            detail_out(db, s),
            str(s.id),
            {"cost_id": str(c.id), "kind": c.kind, "amount": str(c.amount), "currency": c.currency},
        )

    return mutate(
        db, request, p, key, f"shipment.cost:{shipment_id}", body, 201, "shipment.add_cost", fn
    )


@router.post("/shipments/{shipment_id}/costs/{cost_id}/withdraw", response_model=ShipmentDetail)
def withdraw_cost(
    shipment_id: uuid.UUID,
    cost_id: uuid.UUID,
    body: ReasonIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    def fn() -> Result:
        s = get_shipment(db, p, shipment_id)
        authorize(request, p, "grn.record", s.warehouse_id)
        s = get_shipment(db, p, shipment_id, lock=True)
        need_status(s, "OPEN", "COUNTED", "POSTED")
        if s.status != "POSTED":
            check_version(s.version, body.version)
        c = db.get(LandedCost, cost_id)
        if c is None or c.shipment_id != s.id:
            raise ApiError(404, "NOT_FOUND", "Cost not found")
        if c.removed_at is not None:
            raise ApiError(409, "ALREADY_WITHDRAWN", "That cost was already withdrawn")
        c.removed_at = svc.now()
        c.removed_by = p.employee.id
        c.removed_reason = body.reason.strip()
        if s.status != "POSTED":
            touch(s)
        db.flush()
        return detail_out(db, s), str(s.id), {"cost_id": str(c.id), "reason": c.removed_reason}

    return mutate(
        db,
        request,
        p,
        key,
        f"shipment.cost_withdraw:{cost_id}",
        body,
        200,
        "shipment.withdraw_cost",
        fn,
    )


@router.post("/shipments/{shipment_id}/submit-count", response_model=ShipmentDetail)
def submit_count(
    shipment_id: uuid.UUID,
    body: VersionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Counting finished: hand over for approval. Counting is frozen until reopened."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.record", body.version, "OPEN")
        n = db.scalar(select(func.count()).where(GrnLine.shipment_id == s.id)) or 0
        if n == 0:
            raise ApiError(422, "NOTHING_COUNTED", "Count at least one line first")
        s.status = "COUNTED"
        s.counted_by = p.employee.id
        s.counted_at = svc.now()
        touch(s)
        db.flush()
        return detail_out(db, s), str(s.id), {"lines": n}

    return mutate(
        db,
        request,
        p,
        key,
        f"shipment.submit:{shipment_id}",
        body,
        200,
        "shipment.submit_count",
        fn,
    )


@router.post("/shipments/{shipment_id}/reopen", response_model=ShipmentDetail)
def reopen(
    shipment_id: uuid.UUID,
    body: ReasonIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Send a submitted count back for recounting (before posting only)."""

    def fn() -> Result:
        s = get_shipment(db, p, shipment_id)
        if not can_read(p, s.warehouse_id):
            raise deny(request, p, "grn.record", s.warehouse_id)
        s = lock_for(
            db,
            request,
            p,
            shipment_id,
            "grn.record" if p.can("grn.record", s.warehouse_id) else "grn.approve",
            body.version,
            "COUNTED",
        )
        s.status = "OPEN"
        s.counted_by = None
        s.counted_at = None
        touch(s)
        db.flush()
        return detail_out(db, s), str(s.id), {"reason": body.reason.strip()}

    return mutate(
        db, request, p, key, f"shipment.reopen:{shipment_id}", body, 200, "shipment.reopen", fn
    )


@router.post("/shipments/{shipment_id}/post", response_model=ShipmentDetail)
def post_shipment(
    shipment_id: uuid.UUID,
    body: GrnPostIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Approve the GRN: accepted -> saleable stock, damaged -> damaged lot, rejected -> nothing.
    Any difference from the documents must be acknowledged with a note. Exactly once."""

    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.approve", body.version, "COUNTED")
        _, declared, counted, _c = _load(db, s)
        rows = svc.compare(declared, counted)
        diffs = discrepancies(rows)
        note = (body.discrepancy_note or "").strip()
        if diffs and (not body.acknowledge_discrepancies or len(note) < 3):
            raise ApiError(
                422,
                "DISCREPANCY_ACK_REQUIRED",
                f"{len(diffs)} line(s) differ from the vendor documents. "
                "Acknowledge them with a note.",
                lines=[
                    {"product_id": str(r.product_id), "unit": r.unit_code, "status": r.status}
                    for r in diffs
                ],
            )
        grn_no, mvs = svc.post_grn(db, s, p.employee.id)
        s.status = "POSTED"
        s.grn_no = grn_no
        s.posted_by = p.employee.id
        s.posted_at = svc.now()
        s.discrepancy_note = note or None
        touch(s)
        db.flush()
        detail = {
            "grn_no": grn_no,
            "movements": [str(m.id) for m in mvs],
            "discrepancies": [
                {
                    "product_id": str(r.product_id),
                    "unit": r.unit_code,
                    "status": r.status,
                    "difference": _d(r.difference),
                }
                for r in diffs
            ],
            "note": note or None,
        }
        return detail_out(db, s), str(s.id), detail

    return mutate(
        db, request, p, key, f"shipment.post:{shipment_id}", body, 200, "shipment.post", fn
    )


@router.post("/shipments/{shipment_id}/cancel", response_model=ShipmentDetail)
def cancel_shipment(
    shipment_id: uuid.UUID,
    body: ReasonIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    def fn() -> Result:
        s = lock_for(db, request, p, shipment_id, "grn.approve", body.version, "OPEN", "COUNTED")
        s.status = "CANCELLED"
        s.cancel_reason = body.reason.strip()
        touch(s)
        db.flush()
        return detail_out(db, s), str(s.id), {"reason": s.cancel_reason}

    return mutate(
        db, request, p, key, f"shipment.cancel:{shipment_id}", body, 200, "shipment.cancel", fn
    )
