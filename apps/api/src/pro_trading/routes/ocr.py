"""Module 07 OCR review API.

A scan of a sales order is read into proposed lines. A reviewer confirms, corrects or removes every
line (anything unclear can't be confirmed as read), then accepts: that creates a DRAFT sales order
linked to the original scan. OCR never creates invoices, reservations or stock movements.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import audit, idempotency, ocr
from .. import inventory as inv
from .. import orders as order_svc
from .. import receiving as recv
from ..deps import DbDep, Principal, PrincipalDep, authorize
from ..errors import ApiError
from ..models import (
    Company,
    Customer,
    DeclaredLine,
    Employee,
    OcrCorrection,
    OcrExtraction,
    OcrField,
    Product,
    SalesOrder,
    ScanJob,
    Shipment,
    SourceDocument,
    Vendor,
    Warehouse,
)
from ..schemas import (
    OcrAcceptIn,
    OcrAccuracyOut,
    OcrExtractionDetail,
    OcrExtractionPage,
    OcrHeaderFixIn,
    OcrManualLineIn,
    OcrQueueIn,
    OcrResolveIn,
    OcrToReceivingIn,
    OrderIn,
    OrderLineIn,
    ReasonIn,
    ShipmentDocKind,
)
from ._common import content_disposition, esc, mutate
from .orders import insert_order
from .receiving import _attach

router = APIRouter(tags=["ocr"])

Result = tuple[dict[str, Any], str, dict[str, Any]]
PERM = "scan.review"
ACCURACY_TARGET = Decimal("0.95")
MIN_SAMPLES = 50


def _now() -> datetime:
    return datetime.now(UTC)


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


def _name(db: Session, emp_id: uuid.UUID | None) -> str | None:
    if emp_id is None:
        return None
    e = db.get(Employee, emp_id)
    return e.display_name if e else None


def _sku(db: Session, pid: uuid.UUID | None) -> Product | None:
    return db.get(Product, pid) if pid else None


def _readable(db: Session, p: Principal) -> list[uuid.UUID]:
    ids = db.scalars(select(Warehouse.id).where(Warehouse.company_id == p.company_id)).all()
    return [w for w in ids if p.can(PERM, w)]


def extraction_out(db: Session, ex: OcrExtraction, detail: bool = False) -> dict[str, Any]:
    job = db.get(ScanJob, ex.scan_job_id)
    doc = db.get(SourceDocument, ex.document_id)
    wh = db.get(Warehouse, ex.warehouse_id)
    cust = db.get(Customer, ex.customer_id) if ex.customer_id else None
    so = db.get(SalesOrder, ex.sales_order_id) if ex.sales_order_id else None
    fields = db.scalars(
        select(OcrField).where(OcrField.extraction_id == ex.id).order_by(OcrField.seq)
    ).all()
    out: dict[str, Any] = {
        "id": str(ex.id),
        "scan_job_id": str(ex.scan_job_id),
        "scan_job_no": job.job_no if job else "",
        "document_id": str(ex.document_id),
        "document_mime": doc.mime_type if doc else "",
        "warehouse_id": str(ex.warehouse_id),
        "warehouse_code": wh.code if wh else "",
        "run_no": ex.run_no,
        "status": ex.status,
        "provider": ex.provider,
        "template": ex.template,
        "customer_id": str(ex.customer_id) if ex.customer_id else None,
        "customer_name": cust.name if cust else None,
        "warnings": list(ex.warnings or []),
        "header": dict(ex.header or {}),
        "vendor_id": str(ex.vendor_id) if ex.vendor_id else None,
        "shipment_id": str(ex.shipment_id) if ex.shipment_id else None,
        "page_count": ex.page_count,
        "error_code": ex.error_code,
        "error_detail": ex.error_detail,
        "sales_order_id": str(ex.sales_order_id) if ex.sales_order_id else None,
        "sales_order_no": so.order_no if so else None,
        "requested_by_name": _name(db, ex.requested_by),
        "reviewed_by_name": _name(db, ex.reviewed_by),
        "reviewed_at": ex.reviewed_at.isoformat() if ex.reviewed_at else None,
        "review_note": ex.review_note,
        "created_at": ex.created_at.isoformat(),
        "completed_at": ex.completed_at.isoformat() if ex.completed_at else None,
        "field_count": len(fields),
        "open_count": sum(1 for f in fields if f.resolution == "NONE"),
        "version": ex.version,
    }
    if detail:
        out["fields"] = [field_out(db, f) for f in fields]
        out["text_blocks"] = list(ex.text_blocks or [])
    return out


def field_out(db: Session, f: OcrField) -> dict[str, Any]:
    rp, fp = _sku(db, f.read_product_id), _sku(db, f.final_product_id)
    cors = db.scalars(
        select(OcrCorrection)
        .where(OcrCorrection.field_id == f.id)
        .order_by(OcrCorrection.created_at, OcrCorrection.id)
    ).all()
    return {
        "id": str(f.id),
        "seq": f.seq,
        "kind": f.kind,
        "label": f.label,
        "raw_text": f.raw_text,
        "read_product_id": str(f.read_product_id) if f.read_product_id else None,
        "read_sku": rp.sku if rp else None,
        "read_unit_code": f.read_unit_code,
        "read_qty": _s(f.read_qty),
        "read_unit_price": _s(f.read_unit_price),
        "read_amount": _s(f.read_amount),
        "confidence": _s(f.confidence),
        "status": f.status,
        "bbox": f.bbox,
        "resolution": f.resolution,
        "final_product_id": str(f.final_product_id) if f.final_product_id else None,
        "final_sku": fp.sku if fp else None,
        "final_product_name": fp.name if fp else None,
        "final_unit_code": f.final_unit_code,
        "final_qty": _s(f.final_qty),
        "resolved_by_name": _name(db, f.resolved_by),
        "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
        "corrections": [
            {
                "id": str(c.id),
                "action": c.action,
                "before": c.before,
                "after": c.after,
                "reason": c.reason,
                "actor_name": _name(db, c.actor_id) or "",
                "created_at": c.created_at.isoformat(),
            }
            for c in cors
        ],
    }


def get_extraction(
    db: Session, request: Request, p: Principal, eid: uuid.UUID, lock: bool = False
) -> OcrExtraction:
    ex = db.get(OcrExtraction, eid, with_for_update=lock)
    if ex is None or ex.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Extraction not found")
    authorize(request, p, PERM, ex.warehouse_id)
    return ex


def _reviewable(ex: OcrExtraction, version: int) -> None:
    if ex.status != "NEEDS_REVIEW":
        raise ApiError(
            409,
            "INVALID_STATE",
            f"This reading is {ex.status.lower().replace('_', ' ')}",
            current_status=ex.status,
        )
    if ex.version != version:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            "Someone else changed this. Reload.",
            current_version=ex.version,
        )


def _final(f: OcrField) -> dict[str, Any]:
    return {
        "resolution": f.resolution,
        "product_id": str(f.final_product_id) if f.final_product_id else None,
        "unit_code": f.final_unit_code,
        "qty": _s(f.final_qty),
    }


def _valid_line(db: Session, p: Principal, pid: uuid.UUID, unit: str, qty: Decimal) -> Product:
    prod = db.get(Product, pid)
    if prod is None or prod.company_id != p.company_id:
        raise ApiError(422, "INVALID_PRODUCT", "Product not found")
    if not prod.active:
        raise ApiError(422, "PRODUCT_INACTIVE", f"{prod.sku} is inactive")
    inv.check_unit(db, prod, unit)
    inv.check_qty(unit, qty)
    return prod


# ------------------------------------------------------------------ read


@router.get("/ocr/extractions", response_model=OcrExtractionPage)
def list_extractions(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    status: str | None = Query(None, pattern=r"^[A-Z_,]{4,80}$"),
    warehouse_id: uuid.UUID | None = None,
    scan_job_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, pattern=r"^\d{1,7}$"),
) -> dict[str, Any]:
    authorize(request, p, PERM, any_scope=True)
    stmt = (
        select(OcrExtraction)
        .join(ScanJob, ScanJob.id == OcrExtraction.scan_job_id)
        .outerjoin(Customer, Customer.id == OcrExtraction.customer_id)
        .where(
            OcrExtraction.company_id == p.company_id,
            OcrExtraction.warehouse_id.in_(_readable(db, p)),
        )
    )
    if status:
        stmt = stmt.where(OcrExtraction.status.in_(status.split(",")))
    else:
        stmt = stmt.where(OcrExtraction.status != "SUPERSEDED")
    if warehouse_id:
        stmt = stmt.where(OcrExtraction.warehouse_id == warehouse_id)
    if scan_job_id:
        stmt = stmt.where(OcrExtraction.scan_job_id == scan_job_id)
    if q:
        like = f"%{esc(q.strip())}%"
        stmt = stmt.where(
            or_(ScanJob.job_no.ilike(like, escape="\\"), Customer.name.ilike(like, escape="\\"))
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    off = int(cursor or 0)
    rows = db.scalars(
        stmt.order_by(OcrExtraction.created_at.desc(), OcrExtraction.id).offset(off).limit(limit)
    ).all()
    items = [extraction_out(db, e) for e in rows]
    nxt = off + len(items)
    return {"items": items, "total": total, "next_cursor": str(nxt) if nxt < total else None}


@router.get("/ocr/extractions/{extraction_id}", response_model=OcrExtractionDetail)
def read_extraction(
    extraction_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    return extraction_out(db, get_extraction(db, request, p, extraction_id), detail=True)


@router.get("/ocr/accuracy", response_model=OcrAccuracyOut)
def accuracy(request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    """Line accuracy over accepted readings: a line counts as correct when the reviewer confirmed
    it as read. Corrected, removed and manually added (missed) lines count as wrong."""
    authorize(request, p, PERM, any_scope=True)
    rows = db.execute(
        select(OcrField.kind, OcrField.resolution, OcrField.status)
        .join(OcrExtraction, OcrExtraction.id == OcrField.extraction_id)
        .where(
            OcrExtraction.company_id == p.company_id,
            OcrExtraction.status == "ACCEPTED",
            OcrExtraction.warehouse_id.in_(_readable(db, p)),
        )
    ).all()
    samples = len(rows)
    correct = sum(1 for k, r, _ in rows if k != "MANUAL" and r == "CONFIRMED")
    by_status: dict[str, int] = {}
    for k, _, s in rows:
        key = "MISSED" if k == "MANUAL" else s
        by_status[key] = by_status.get(key, 0) + 1
    acc = (Decimal(correct) / Decimal(samples)).quantize(Decimal("0.0001")) if samples else None
    return {
        "samples": samples,
        "correct": correct,
        "accuracy": _s(acc),
        "target": str(ACCURACY_TARGET),
        "minimum_samples": MIN_SAMPLES,
        "meets_target": acc is not None and samples >= MIN_SAMPLES and acc >= ACCURACY_TARGET,
        "by_status": by_status,
    }


@router.get(
    "/ocr/order-sheet",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def order_sheet(
    request: Request, db: DbDep, p: PrincipalDep, customer_id: uuid.UUID, warehouse_id: uuid.UUID
) -> Response:
    """Printable order sheet (template PT-OS-1) the customer fills in by hand; scans of it are
    read line by line."""
    if not p.can("scan.create", warehouse_id):
        authorize(request, p, "sales_order.manage", warehouse_id)
    wh = db.get(Warehouse, warehouse_id)
    if wh is None or wh.company_id != p.company_id:
        raise ApiError(422, "INVALID_WAREHOUSE", "Unknown warehouse")
    cust = order_svc.get_customer(db, p.company_id, customer_id)
    co = db.get(Company, p.company_id)
    pdf = ocr.order_sheet_pdf(db, cust, wh, co.legal_name if co else "")
    fname = f"order-sheet-{cust.external_ref or cust.id}.pdf"
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition("attachment", fname),
            "Cache-Control": "private, no-store",
        },
    )


# ------------------------------------------------------------------ commands


@router.post("/ocr/extractions", status_code=201, response_model=OcrExtractionDetail)
def queue_extraction(
    body: OcrQueueIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """Read (or re-read) a received scan. An unfinished earlier reading is superseded."""
    job = db.get(ScanJob, body.scan_job_id)
    if job is None or job.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Scan not found")
    authorize(request, p, PERM, job.warehouse_id)

    def fn() -> Result:
        j = db.get(ScanJob, body.scan_job_id, with_for_update=True)
        assert j is not None
        ex = ocr.queue(db, j, p.employee.id)
        return extraction_out(db, ex, True), str(ex.id), {"scan_job": j.job_no, "run": ex.run_no}

    return mutate(db, request, p, key, "ocr.queue", body, 201, "ocr_extraction.queue", fn)


@router.post(
    "/ocr/extractions/{extraction_id}/fields/{field_id}/resolve",
    response_model=OcrExtractionDetail,
)
def resolve_field(
    extraction_id: uuid.UUID,
    field_id: uuid.UUID,
    body: OcrResolveIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    get_extraction(db, request, p, extraction_id)

    def fn() -> Result:
        ex = get_extraction(db, request, p, extraction_id, lock=True)
        _reviewable(ex, body.version)
        f = db.get(OcrField, field_id, with_for_update=True)
        if f is None or f.extraction_id != ex.id:
            raise ApiError(404, "NOT_FOUND", "Line not found")
        before = _final(f)
        reason = (body.reason or "").strip() or None
        if body.action == "CONFIRM":
            if f.status != "OK" or f.read_product_id is None or f.read_qty is None:
                raise ApiError(
                    422,
                    "NEEDS_CORRECTION",
                    "This line was not read clearly. Correct it or remove it.",
                    field_status=f.status,
                )
            assert f.read_unit_code is not None
            _valid_line(db, p, f.read_product_id, f.read_unit_code, f.read_qty)
            f.resolution = "CONFIRMED"
            f.final_product_id, f.final_unit_code = f.read_product_id, f.read_unit_code
            f.final_qty = f.read_qty
        elif body.action == "CORRECT":
            if body.product_id is None or body.unit_code is None or body.qty is None:
                raise ApiError(422, "INCOMPLETE_LINE", "Choose a product, unit and quantity")
            if f.status == "OK" and reason is None:
                raise ApiError(422, "REASON_REQUIRED", "Say why the reading is wrong")
            _valid_line(db, p, body.product_id, body.unit_code, body.qty)
            f.resolution = "CORRECTED"
            f.final_product_id, f.final_unit_code = body.product_id, body.unit_code
            f.final_qty = inv.q4(body.qty)
        else:
            if f.status == "OK" and f.kind != "MANUAL" and reason is None:
                raise ApiError(422, "REASON_REQUIRED", "Say why this line should be removed")
            f.resolution = "REMOVED"
            f.final_product_id = f.final_unit_code = f.final_qty = None
        f.resolved_by, f.resolved_at = p.employee.id, _now()
        db.add(
            OcrCorrection(
                field_id=f.id,
                action=body.action,
                before=before,
                after=_final(f),
                reason=reason,
                actor_id=p.employee.id,
            )
        )
        ex.version += 1
        db.flush()
        return (
            extraction_out(db, ex, True),
            str(ex.id),
            {"field": f.seq, "action": body.action, "status": f.status},
        )

    return mutate(
        db, request, p, key, f"ocr.resolve:{field_id}", body, 200, "ocr_extraction.resolve", fn
    )


@router.post("/ocr/extractions/{extraction_id}/lines", response_model=OcrExtractionDetail)
def add_line(
    extraction_id: uuid.UUID,
    body: OcrManualLineIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """A line the reading missed. Counted as a miss in accuracy."""
    get_extraction(db, request, p, extraction_id)

    def fn() -> Result:
        ex = get_extraction(db, request, p, extraction_id, lock=True)
        _reviewable(ex, body.version)
        prod = _valid_line(db, p, body.product_id, body.unit_code, body.qty)
        seq = (
            db.scalar(select(func.max(OcrField.seq)).where(OcrField.extraction_id == ex.id)) or 0
        ) + 1
        f = OcrField(
            extraction_id=ex.id,
            seq=seq,
            kind="MANUAL",
            label=f"{prod.sku} {prod.name}",
            raw_text="",
            status="OK",
            resolution="CORRECTED",
            final_product_id=prod.id,
            final_unit_code=body.unit_code,
            final_qty=inv.q4(body.qty),
            resolved_by=p.employee.id,
            resolved_at=_now(),
        )
        db.add(f)
        db.flush()
        db.add(
            OcrCorrection(
                field_id=f.id,
                action="ADD",
                before={},
                after=_final(f),
                reason=body.reason.strip(),
                actor_id=p.employee.id,
            )
        )
        ex.version += 1
        db.flush()
        return extraction_out(db, ex, True), str(ex.id), {"field": seq, "sku": prod.sku}

    return mutate(
        db, request, p, key, f"ocr.line:{extraction_id}", body, 200, "ocr_extraction.add_line", fn
    )


@router.post("/ocr/extractions/{extraction_id}/accept", response_model=OcrExtractionDetail)
def accept(
    extraction_id: uuid.UUID,
    body: OcrAcceptIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Every line reviewed → a DRAFT sales order (source SCAN, linked to the scan). Nothing else:
    no reservation, no invoice, no stock movement."""
    ex0 = get_extraction(db, request, p, extraction_id)
    authorize(request, p, "sales_order.manage", ex0.warehouse_id)
    if ex0.header:
        raise ApiError(409, "USE_RECEIVING", "Supplier documents go to Receiving, not to an order")

    def fn() -> Result:
        ex = get_extraction(db, request, p, extraction_id, lock=True)
        _reviewable(ex, body.version)
        fields = db.scalars(
            select(OcrField).where(OcrField.extraction_id == ex.id).order_by(OcrField.seq)
        ).all()
        open_ = [f.seq for f in fields if f.resolution == "NONE"]
        if open_:
            raise ApiError(
                409,
                "FIELDS_UNRESOLVED",
                f"{len(open_)} line(s) still need review",
                open_lines=open_,
            )
        keep = [f for f in fields if f.resolution in ("CONFIRMED", "CORRECTED")]
        if not keep:
            raise ApiError(422, "NO_LINES", "There are no lines to put on an order")
        merged: dict[tuple[uuid.UUID, str], tuple[Decimal, list[str]]] = {}
        for f in keep:
            assert f.final_product_id and f.final_unit_code and f.final_qty
            k = (f.final_product_id, f.final_unit_code)
            q0, texts = merged.get(k, (Decimal(0), []))
            merged[k] = (q0 + f.final_qty, [*texts, f.raw_text or f.label])
        order = OrderIn(
            customer_id=body.customer_id,
            warehouse_id=ex.warehouse_id,
            source="SCAN",
            source_text="\n".join(str(b.get("text", "")) for b in ex.text_blocks or [])[:10_000],
            requested_for=body.requested_for,
            note=body.note,
            lines=[
                OrderLineIn(
                    product_id=pid,
                    unit_code=unit,
                    qty=q,
                    source_line_text=" | ".join(t)[:500],
                )
                for (pid, unit), (q, t) in merged.items()
            ],
        )
        so = insert_order(db, p, order, source_document_id=ex.document_id)
        ex.status = "ACCEPTED"
        ex.sales_order_id = so.id
        ex.customer_id = body.customer_id
        ex.reviewed_by, ex.reviewed_at = p.employee.id, _now()
        ex.review_note = body.note.strip() or None
        ex.version += 1
        db.flush()
        return (
            extraction_out(db, ex, True),
            str(ex.id),
            {
                "order_no": so.order_no,
                "lines": len(order.lines),
                "removed": len(fields) - len(keep),
            },
        )

    return mutate(
        db, request, p, key, f"ocr.accept:{extraction_id}", body, 200, "ocr_extraction.accept", fn
    )


@router.post("/ocr/extractions/{extraction_id}/reject", response_model=OcrExtractionDetail)
def reject(
    extraction_id: uuid.UUID,
    body: ReasonIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Not an order (or unusable). The scan and the reading stay on file."""
    get_extraction(db, request, p, extraction_id)

    def fn() -> Result:
        ex = get_extraction(db, request, p, extraction_id, lock=True)
        if ex.status != "FAILED" or ex.version != body.version:
            _reviewable(ex, body.version)
        ex.status = "REJECTED"
        ex.reviewed_by, ex.reviewed_at = p.employee.id, _now()
        ex.review_note = body.reason.strip()
        ex.version += 1
        db.flush()
        return extraction_out(db, ex, True), str(ex.id), {"reason": ex.review_note}

    return mutate(
        db, request, p, key, f"ocr.reject:{extraction_id}", body, 200, "ocr_extraction.reject", fn
    )


# ------------------------------------------------------------------ supplier documents

_DOC_KIND: dict[str, ShipmentDocKind] = {
    "VENDOR_DOCUMENT": "INVOICE",
    "PACKING_LIST": "PACKING_LIST",
}


@router.post("/ocr/extractions/{extraction_id}/header", response_model=OcrExtractionDetail)
def fix_header(
    extraction_id: uuid.UUID, body: OcrHeaderFixIn, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    """Correct a header value. What the engine read stays next to it; the change is audited."""
    ex = get_extraction(db, request, p, extraction_id, lock=True)
    authorize(request, p, "scan.review", ex.warehouse_id)
    _reviewable(ex, body.version)
    if not ex.header:
        raise ApiError(409, "NO_HEADER", "Only supplier documents have header values")
    h = {k: dict(v) for k, v in ex.header.items()}
    cell = h.get(body.field, {"value": None, "confidence": None})
    before = cell.get("corrected", cell.get("value"))
    value = (body.value or "").strip() or None
    if value == cell.get("value"):
        cell.pop("corrected", None)
    else:
        cell["corrected"] = value
    h[body.field] = cell
    ex.header = h
    ex.version += 1
    audit.record(
        db, action="ocr_extraction.header_fix", success=True, company_id=p.company_id,
        actor_id=p.employee.id, target_type="ocr_extraction", target_id=str(ex.id),
        warehouse_id=ex.warehouse_id, request_id=request.state.request_id,
        detail={"field": body.field, "before": before, "after": value, "reason": body.reason},
    )  # fmt: skip
    db.commit()
    return extraction_out(db, ex, True)


@router.post("/ocr/extractions/{extraction_id}/to-receiving", response_model=OcrExtractionDetail)
def to_receiving(
    extraction_id: uuid.UUID,
    body: OcrToReceivingIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Every line reviewed → the document is attached to a shipment (an OPEN one, or a new one)
    and the kept lines become its declared lines. Never stock, never a cost or liability: the
    count and the GRN posting stay the warehouse/manager steps they always were."""
    ex0 = get_extraction(db, request, p, extraction_id)
    authorize(request, p, "grn.record", ex0.warehouse_id)

    def fn() -> Result:
        ex = get_extraction(db, request, p, extraction_id, lock=True)
        _reviewable(ex, body.version)
        if not ex.header:
            raise ApiError(409, "NOT_SUPPLIER_DOC", "This scan is not a supplier document")
        fields = db.scalars(
            select(OcrField).where(OcrField.extraction_id == ex.id).order_by(OcrField.seq)
        ).all()
        if open_ := [f.seq for f in fields if f.resolution == "NONE"]:
            raise ApiError(
                409, "FIELDS_UNRESOLVED", f"{len(open_)} line(s) still need review",
                open_lines=open_,
            )  # fmt: skip
        keep = [f for f in fields if f.resolution in ("CONFIRMED", "CORRECTED")]
        if not keep:
            raise ApiError(422, "NO_LINES", "There are no lines to declare")
        v = db.get(Vendor, body.vendor_id)
        if v is None or v.company_id != p.company_id:
            raise ApiError(422, "INVALID_VENDOR", "Supplier not found")
        doc_no = (body.document_no or "").strip() or None
        if body.shipment_id is not None:
            s = db.get(Shipment, body.shipment_id, with_for_update=True)
            if s is None or s.company_id != p.company_id or s.vendor_id != v.id:
                raise ApiError(422, "INVALID_SHIPMENT", "That shipment is not from this supplier")
            # grn.record was checked for the scan's warehouse only: never write into another
            # warehouse's shipment from here.
            if s.warehouse_id != ex.warehouse_id:
                raise ApiError(
                    422, "WRONG_WAREHOUSE", "That shipment belongs to a different warehouse"
                )
            if s.status != "OPEN":
                raise ApiError(409, "INVALID_STATE", f"Shipment {s.shipment_no} is not open")
        else:
            s = Shipment(
                company_id=p.company_id, warehouse_id=ex.warehouse_id, vendor_id=v.id,
                shipment_no=recv.next_shipment_no(db, p.company_id), vendor_invoice_no=doc_no,
                currency=body.currency or v.currency, note="From scanned document",
                created_by=p.employee.id,
            )  # fmt: skip
            db.add(s)
            db.flush()
        job = db.get(ScanJob, ex.scan_job_id)
        assert job is not None
        link = _attach(
            db, s, p, ex.document_id, job.id, _DOC_KIND.get(job.job_type, "OTHER"), doc_no
        )
        for f in keep:
            assert f.final_product_id and f.final_unit_code and f.final_qty
            db.add(
                DeclaredLine(
                    shipment_id=s.id, shipment_document_id=link.id,
                    raw_label=(f.raw_text or f.label)[:500], product_id=f.final_product_id,
                    unit_code=f.final_unit_code, declared_qty=inv.q4(f.final_qty),
                    unit_cost=f.read_unit_price if f.resolution == "CONFIRMED" else None,
                    created_by=p.employee.id,
                )
            )  # fmt: skip
        ex.status, ex.shipment_id, ex.vendor_id = "ACCEPTED", s.id, v.id
        ex.reviewed_by, ex.reviewed_at = p.employee.id, _now()
        ex.version += 1
        db.flush()
        return (
            extraction_out(db, ex, True),
            str(ex.id),
            {"shipment_no": s.shipment_no, "lines": len(keep), "removed": len(fields) - len(keep)},
        )

    return mutate(
        db, request, p, key, f"ocr.to_receiving:{extraction_id}", body, 200,
        "ocr_extraction.to_receiving", fn,
    )  # fmt: skip
