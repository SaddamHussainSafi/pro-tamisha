"""Module 09 documents: archive list/detail/content, Drive retry, signed POD upload.

The archive is read-only except for two commands: retry a failed Drive copy, and file a signed
proof of delivery against an issued invoice (append-only; the issued PDF is never replaced)."""

import uuid
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import documents as svc
from .. import idempotency, scanning
from ..config import get_settings
from ..deps import DbDep, Principal, PrincipalDep, authorize
from ..errors import ApiError
from ..models import DocumentFile, Invoice, Warehouse
from ..schemas import (
    DocFileKind,
    DocSyncStatus,
    DocumentFileOut,
    DocumentPage,
    DocumentRetryIn,
    PodUploadIn,
)
from ._common import content_disposition, esc, mutate
from .invoices import get_invoice
from .orders import check_version
from .scans import RawBody

router = APIRouter(tags=["documents"])

Result = tuple[dict[str, Any], str, dict[str, Any]]


def readable(db: Session, p: Principal) -> set[uuid.UUID]:
    ids = db.scalars(select(Warehouse.id).where(Warehouse.company_id == p.company_id)).all()
    return {w for w in ids if p.can("document.read", w)}


def get_file(db: Session, request: Request, p: Principal, fid: uuid.UUID) -> DocumentFile:
    f = db.get(DocumentFile, fid)
    if f is None or f.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Document not found")
    authorize(request, p, "document.read", f.warehouse_id)
    return f


@router.get("/documents", response_model=DocumentPage)
def list_documents(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    kind: DocFileKind | None = None,
    sync_status: DocSyncStatus | None = None,
    warehouse_id: uuid.UUID | None = None,
    invoice_id: uuid.UUID | None = None,
    shipment_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, pattern=r"^\d{1,7}$"),
) -> dict[str, Any]:
    """Archive index, newest first. `counts` are per sync status over every readable location."""
    authorize(request, p, "document.read", any_scope=True)
    whs = readable(db, p)
    stmt = select(DocumentFile).where(
        DocumentFile.company_id == p.company_id, DocumentFile.warehouse_id.in_(whs)
    )
    if kind:
        stmt = stmt.where(DocumentFile.kind == kind)
    if sync_status:
        stmt = stmt.where(DocumentFile.sync_status == sync_status)
    if warehouse_id:
        stmt = stmt.where(DocumentFile.warehouse_id == warehouse_id)
    if invoice_id:
        stmt = stmt.where(DocumentFile.invoice_id == invoice_id)
    if shipment_id:
        stmt = stmt.where(DocumentFile.shipment_id == shipment_id)
    if q:
        like = f"%{esc(q.strip())}%"
        stmt = stmt.where(
            or_(
                DocumentFile.filename.ilike(like, escape="\\"),
                DocumentFile.folder.ilike(like, escape="\\"),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    off = int(cursor or 0)
    rows = db.scalars(
        stmt.order_by(DocumentFile.created_at.desc(), DocumentFile.id).offset(off).limit(limit)
    ).all()
    items = [svc.file_out(db, f) for f in rows]
    nxt = off + len(items)
    return {
        "items": items,
        "total": total,
        "next_cursor": str(nxt) if nxt < total else None,
        "counts": svc.status_counts(db, p.company_id, whs),
        "drive_provider": get_settings().drive_provider,
    }


@router.get("/documents/{document_file_id}", response_model=DocumentFileOut)
def read_document(
    document_file_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    return svc.file_out(db, get_file(db, request, p, document_file_id))


@router.get(
    "/documents/{document_file_id}/content",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, "image/png": {}, "image/jpeg": {}}}},
)
def document_content(
    document_file_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> Response:
    """The archived bytes exactly as filed (issued PDF or original scan; never re-rendered)."""
    f = get_file(db, request, p, document_file_id)
    data = svc.content_of(db, f)
    etag = f'"{f.sha256}"'
    headers = {
        "ETag": etag,
        "Cache-Control": "private, max-age=0, must-revalidate",
        "Content-Disposition": content_disposition("inline", f.filename),
        "X-Content-SHA256": f.sha256,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(data, media_type=f.mime_type, headers=headers)


@router.post("/documents/{document_file_id}/retry-sync", response_model=DocumentFileOut)
def retry_sync(
    document_file_id: uuid.UUID,
    body: DocumentRetryIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Queue a fresh Drive copy for a FAILED file. The ERP copy is unaffected either way."""
    f0 = get_file(db, request, p, document_file_id)
    authorize(request, p, "document.manage", f0.warehouse_id)

    def fn() -> Result:
        f = db.get(DocumentFile, document_file_id, with_for_update=True, populate_existing=True)
        assert f is not None
        db.refresh(f)
        check_version(f.version, body.version, "This document")
        svc.retry(db, f)
        db.flush()
        return svc.file_out(db, f), str(f.id), {"path": f"{f.folder}/{f.filename}"}

    return mutate(
        db,
        request,
        p,
        key,
        f"document.retry:{document_file_id}",
        body,
        200,
        "document.retry_sync",
        fn,
    )


@router.post(
    "/invoices/{invoice_id}/pod",
    status_code=201,
    response_model=DocumentFileOut,
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
def upload_pod(
    invoice_id: uuid.UUID,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
    data: RawBody,
    filename: str = Query("pod", max_length=255),
    note: str | None = Query(None, max_length=500),
) -> JSONResponse:
    """Signed proof of delivery (raw file body). Kept unaltered next to the invoice; a second
    different copy is added beside it, the same bytes again return the existing file (200)."""
    inv0 = get_invoice(db, request, p, invoice_id)
    authorize(request, p, "document.manage", inv0.warehouse_id)
    mime = scanning.check_upload(data, request.headers.get("content-type"))
    digest = scanning.sha256hex(data)
    params = PodUploadIn(invoice_id=invoice_id, filename=filename, note=note, sha256=digest)
    state: dict[str, bool] = {}

    def fn() -> Result:
        inv = db.get(Invoice, invoice_id, with_for_update=True, populate_existing=True)
        assert inv is not None
        name = scanning.safe_filename(filename, mime)
        doc, _ = scanning.store_document(
            db, p.company_id, data, mime, name, p.employee.id, "MANUAL"
        )
        f, already = svc.file_pod(db, inv, doc, p.employee.id, note)
        state["already"] = already
        return (
            svc.file_out(db, f),
            str(f.id),
            {"invoice_no": inv.invoice_no, "sha256": digest, "already_filed": already},
        )

    resp = mutate(db, request, p, key, f"invoice.pod:{invoice_id}", params, 201, "invoice.pod", fn)
    if state.get("already"):
        resp.status_code = 200
    return resp
