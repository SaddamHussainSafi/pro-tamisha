"""Module 06 scanning: device pairing, bridge scan jobs, manual upload fallback, source documents.

Two kinds of caller:
- Office staff (session cookie): pair/approve/revoke bridges, request a bridge scan, upload a
  file manually, list jobs, open documents.
- The scanner bridge (Authorization: Device <id>.<secret>): pair with a one-time code,
  heartbeat, claim a job with a server-signed capability, upload the scanned bytes, report failure.

Nothing here creates orders, invoices or stock — scanned documents are inputs for human review.
"""

import secrets
import threading
import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, documents, idempotency, ocr
from .. import scanning as svc
from ..config import get_settings
from ..deps import DbDep, Principal, PrincipalDep, authorize
from ..errors import ApiError
from ..models import (
    Employee,
    ScanDevice,
    ScanJob,
    Shipment,
    ShipmentDocument,
    SourceDocument,
    Warehouse,
)
from ..schemas import (
    BridgeClaimIn,
    BridgeClaimOut,
    BridgeFailIn,
    BridgeHeartbeatIn,
    BridgeHeartbeatOut,
    BridgePairIn,
    BridgePairOut,
    DeviceApproveIn,
    DeviceRevokeIn,
    PairingIn,
    PairingOut,
    ScanCancelIn,
    ScanDeviceOut,
    ScanJobCreatedOut,
    ScanJobIn,
    ScanJobOut,
    ScanJobPage,
    ScanJobType,
    ScanUploadIn,
)
from ._common import content_disposition, esc, mutate

router = APIRouter(tags=["scans"])

Result = tuple[dict[str, Any], str, dict[str, Any]]
READ_PERMS = ("scan.create", "scan.review")


# ------------------------------------------------------------------ helpers


def _can_read(p: Principal, warehouse_id: uuid.UUID) -> bool:
    return any(p.can(perm, warehouse_id) for perm in READ_PERMS)


def readable(db: Session, p: Principal) -> set[uuid.UUID]:
    ids = db.scalars(select(Warehouse.id).where(Warehouse.company_id == p.company_id)).all()
    return {w for w in ids if _can_read(p, w)}


def _warehouse(db: Session, p: Principal, wid: uuid.UUID) -> Warehouse:
    w = db.get(Warehouse, wid)
    if w is None or w.company_id != p.company_id:
        raise ApiError(422, "INVALID_WAREHOUSE", "Unknown warehouse")
    return w


def _check_version(current: int, sent: int) -> None:
    if current != sent:
        raise ApiError(
            409, "VERSION_CONFLICT", "Someone else changed this. Reload.", current_version=current
        )


def _name(db: Session, emp_id: uuid.UUID | None) -> str | None:
    if emp_id is None:
        return None
    e = db.get(Employee, emp_id)
    return e.display_name if e else None


def device_out(db: Session, d: ScanDevice) -> dict[str, Any]:
    w = db.get(Warehouse, d.warehouse_id)
    return {
        "id": str(d.id),
        "warehouse_id": str(d.warehouse_id),
        "warehouse_code": w.code if w else "",
        "name": d.name,
        "status": d.status,
        "paired": d.secret_hash is not None,
        "pairing_expires_at": d.pairing_expires_at.isoformat()
        if d.pairing_expires_at and d.secret_hash is None
        else None,
        "online": svc.is_online(d),
        "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
        "scanner_state": d.scanner_state,
        "scanner_detail": d.scanner_detail,
        "bridge_version": d.bridge_version,
        "discover_requested_at": d.discover_requested_at.isoformat()
        if d.discover_requested_at
        else None,
        "detected_scanners": d.detected_scanners,
        "detected_at": d.detected_at.isoformat() if d.detected_at else None,
        "approved_by_name": _name(db, d.approved_by),
        "approved_at": d.approved_at.isoformat() if d.approved_at else None,
        "revoked_reason": d.revoked_reason,
        "version": d.version,
    }


def job_out(db: Session, j: ScanJob, pages: bool = False) -> dict[str, Any]:
    """`pages=True` also counts the document's pages (one pdfinfo call): single-scan answers only,
    never lists."""
    w = db.get(Warehouse, j.warehouse_id)
    dev = db.get(ScanDevice, j.device_id) if j.device_id else None
    doc = db.get(SourceDocument, j.document_id) if j.document_id else None
    return {
        "id": str(j.id),
        "job_no": j.job_no,
        "warehouse_id": str(j.warehouse_id),
        "warehouse_code": w.code if w else "",
        "job_type": j.job_type,
        "channel": j.channel,
        "status": j.status,
        "device_id": str(j.device_id) if j.device_id else None,
        "device_name": dev.name if dev else None,
        "note": j.note,
        "requested_by_name": _name(db, j.requested_by) or "",
        "created_at": j.created_at.isoformat(),
        "claimed_at": j.claimed_at.isoformat() if j.claimed_at else None,
        "completed_at": j.completed_at.isoformat() if j.completed_at else None,
        "error_code": j.error_code,
        "error_detail": j.error_detail,
        "document_id": str(doc.id) if doc else None,
        "document_sha256": doc.sha256 if doc else None,
        "document_mime": doc.mime_type if doc else None,
        "document_size": doc.byte_size if doc else None,
        "document_filename": doc.original_filename if doc else None,
        "duplicate": j.duplicate,
        "page_count": svc.page_count(doc.content, doc.mime_type) if pages and doc else None,
        "version": j.version,
    }


def get_device(db: Session, request: Request, p: Principal, did: uuid.UUID) -> ScanDevice:
    d = db.get(ScanDevice, did, with_for_update=True, populate_existing=True)
    if d is None or d.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Scanner not found")
    authorize(request, p, "scan.device.manage", d.warehouse_id)
    return d


def get_job(db: Session, request: Request, p: Principal, jid: uuid.UUID) -> ScanJob:
    j = db.get(ScanJob, jid)
    if j is None or j.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Scan not found")
    if not _can_read(p, j.warehouse_id):
        authorize(request, p, "scan.create", j.warehouse_id)
    return j


async def raw_body(request: Request) -> bytes:
    """Read the upload body with an early size check (Content-Length) and a hard cap."""
    limit = get_settings().scan_max_bytes
    try:
        declared = int(request.headers.get("content-length", "0"))
    except ValueError:
        declared = 0
    if declared > limit:
        raise ApiError(413, "FILE_TOO_LARGE", "The file is too large", max_bytes=limit)
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > limit:
            raise ApiError(413, "FILE_TOO_LARGE", "The file is too large", max_bytes=limit)
    return bytes(buf)


RawBody = Annotated[bytes, Depends(raw_body)]


def _bridge_device(request: Request, db: DbDep) -> ScanDevice:
    d = svc.device_auth(db, request)
    # End the read transaction: the upload that follows can take minutes at the client's pace,
    # and must not hold a pooled connection idle in a transaction meanwhile.
    db.commit()
    return d


BridgeDevice = Annotated[ScanDevice, Depends(_bridge_device)]


# ------------------------------------------------------------------ devices (staff)


@router.get("/scan-devices", response_model=list[ScanDeviceOut])
def list_devices(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    warehouse_id: uuid.UUID | None = None,
    include_revoked: bool = False,
) -> list[dict[str, Any]]:
    """Scanners the caller can use (scan.create/review) or manage."""
    if not any(p.can(x, any_scope=True) for x in (*READ_PERMS, "scan.device.manage")):
        authorize(request, p, "scan.create", any_scope=True)
    ids = db.scalars(select(Warehouse.id).where(Warehouse.company_id == p.company_id)).all()
    allowed = {w for w in ids if _can_read(p, w) or p.can("scan.device.manage", w)}
    stmt = select(ScanDevice).where(
        ScanDevice.company_id == p.company_id, ScanDevice.warehouse_id.in_(allowed)
    )
    if warehouse_id:
        stmt = stmt.where(ScanDevice.warehouse_id == warehouse_id)
    if not include_revoked:
        stmt = stmt.where(ScanDevice.status != "REVOKED")
    return [device_out(db, d) for d in db.scalars(stmt.order_by(ScanDevice.created_at)).all()]


@router.post("/scan-devices/pairings", status_code=201, response_model=PairingOut)
def start_pairing(
    body: PairingIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """Creates a PENDING scanner and a one-time pairing code (shown once, stored hashed)."""
    authorize(request, p, "scan.device.manage", body.warehouse_id)
    ttl = timedelta(minutes=get_settings().scan_pairing_ttl_minutes)

    def fn() -> Result:
        _warehouse(db, p, body.warehouse_id)
        code = svc.pairing_code()
        exp = svc.now() + ttl
        d = ScanDevice(
            company_id=p.company_id,
            warehouse_id=body.warehouse_id,
            name=body.name.strip(),
            status="PENDING",
            pairing_code_hash=svc.code_hash(code),
            pairing_expires_at=exp,
            created_by=p.employee.id,
        )
        db.add(d)
        db.flush()
        out = {"device_id": str(d.id), "pairing_code": code, "expires_at": exp.isoformat()}
        return out, str(d.id), {"name": d.name}

    return mutate(db, request, p, key, "scan.pair", body, 201, "scan_device.pairing", fn)


@router.post("/scan-devices/{device_id}/approve", response_model=ScanDeviceOut)
def approve_device(
    device_id: uuid.UUID,
    body: DeviceApproveIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Second half of the handshake: the operator types the fingerprint the bridge displays."""

    def fn() -> Result:
        d = get_device(db, request, p, device_id)
        _check_version(d.version, body.version)
        if d.status != "PENDING":
            raise ApiError(
                409, "INVALID_STATE", "Scanner is not waiting for approval", current_status=d.status
            )
        if d.secret_hash is None or d.fingerprint is None:
            raise ApiError(409, "NOT_PAIRED", "The bridge has not used the pairing code yet")
        if svc.norm_fingerprint(body.fingerprint) != d.fingerprint:
            audit.record_independent(
                action="scan_device.fingerprint_mismatch",
                success=False,
                company_id=p.company_id,
                actor_id=p.employee.id,
                target_type="scan_device",
                target_id=str(d.id),
                request_id=request.state.request_id,
            )
            raise ApiError(
                422, "FINGERPRINT_MISMATCH", "The code does not match the one on the scanner PC"
            )
        d.status = "ACTIVE"
        d.approved_by = p.employee.id
        d.approved_at = svc.now()
        d.version += 1
        db.flush()
        return device_out(db, d), str(d.id), {"name": d.name}

    return mutate(
        db, request, p, key, f"scan.approve:{device_id}", body, 200, "scan_device.approve", fn
    )


@router.post("/scan-devices/{device_id}/discover", response_model=ScanDeviceOut)
def discover_scanners(
    device_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    """Ask the office PC's bridge to list the scanners it can see. The bridge answers on its
    next heartbeat (within about 30 seconds); poll the device to read `detected_scanners`."""
    d = get_device(db, request, p, device_id)
    if d.status != "ACTIVE":
        raise ApiError(409, "INVALID_STATE", "Approve the scanner bridge first")
    d.discover_requested_at = svc.now()
    audit.record(
        db, action="scan_device.discover", success=True, company_id=p.company_id,
        actor_id=p.employee.id, target_type="scan_device", target_id=str(d.id),
        warehouse_id=d.warehouse_id, request_id=request.state.request_id,
    )  # fmt: skip
    db.commit()
    return device_out(db, d)


@router.post("/scan-devices/{device_id}/revoke", response_model=ScanDeviceOut)
def revoke_device(
    device_id: uuid.UUID,
    body: DeviceRevokeIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Immediately invalidates the bridge secret; open jobs on it fail."""

    def fn() -> Result:
        d = get_device(db, request, p, device_id)
        _check_version(d.version, body.version)
        if d.status == "REVOKED":
            raise ApiError(
                409, "INVALID_STATE", "Scanner is already revoked", current_status=d.status
            )
        t = svc.now()
        d.status = "REVOKED"
        d.revoked_at = t
        d.revoked_reason = body.reason
        d.pairing_code_hash = None
        d.version += 1
        for j in db.scalars(
            select(ScanJob).where(
                ScanJob.device_id == d.id, ScanJob.status.in_(("REQUESTED", "SCANNING"))
            )
        ).all():
            j.status = "FAILED"
            j.error_code = "DEVICE_REVOKED"
            j.error_detail = "Scanner was revoked"
            j.completed_at = t
            j.version += 1
        db.flush()
        return device_out(db, d), str(d.id), {"reason": body.reason}

    return mutate(
        db, request, p, key, f"scan.revoke:{device_id}", body, 200, "scan_device.revoke", fn
    )


# ------------------------------------------------------------------ jobs (staff)


@router.get("/scans/jobs", response_model=ScanJobPage)
def list_jobs(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    status: str | None = Query(None, pattern=r"^[A-Z_,]{4,80}$"),
    job_type: str | None = Query(None, pattern=r"^[A-Z_]{4,24}$"),
    channel: str | None = Query(None, pattern=r"^(BRIDGE|MANUAL)$"),
    warehouse_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, pattern=r"^\d{1,7}$"),
) -> dict[str, Any]:
    if not any(p.can(x, any_scope=True) for x in READ_PERMS):
        authorize(request, p, "scan.create", any_scope=True)
    if svc.sweep(db, p.company_id):
        db.commit()
    stmt = (
        select(ScanJob)
        .outerjoin(SourceDocument, SourceDocument.id == ScanJob.document_id)
        .where(ScanJob.company_id == p.company_id, ScanJob.warehouse_id.in_(readable(db, p)))
    )
    if status:
        stmt = stmt.where(ScanJob.status.in_(status.split(",")))
    if job_type:
        stmt = stmt.where(ScanJob.job_type == job_type)
    if channel:
        stmt = stmt.where(ScanJob.channel == channel)
    if warehouse_id:
        stmt = stmt.where(ScanJob.warehouse_id == warehouse_id)
    if q:
        like = f"%{esc(q.strip())}%"
        stmt = stmt.where(
            or_(
                ScanJob.job_no.ilike(like, escape="\\"),
                ScanJob.note.ilike(like, escape="\\"),
                SourceDocument.original_filename.ilike(like, escape="\\"),
                SourceDocument.sha256.ilike(like, escape="\\"),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    off = int(cursor or 0)
    rows = db.scalars(
        stmt.order_by(ScanJob.created_at.desc(), ScanJob.job_no.desc()).offset(off).limit(limit)
    ).all()
    items = [job_out(db, j) for j in rows]
    nxt = off + len(items)
    return {"items": items, "total": total, "next_cursor": str(nxt) if nxt < total else None}


@router.get("/scans/jobs/{job_id}", response_model=ScanJobOut)
def read_job(job_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    j = get_job(db, request, p, job_id)
    if j.status in ("REQUESTED", "SCANNING") and svc.sweep(db, p.company_id):
        db.commit()
        db.refresh(j)
    return job_out(db, j, pages=True)


@router.post("/scans/jobs", status_code=201, response_model=ScanJobCreatedOut)
def create_job(
    body: ScanJobIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """Ask a paired bridge to scan. Returns a single-use capability the browser hands to the
    bridge on localhost; the bridge must present it to claim this job."""
    authorize(request, p, "scan.create", body.warehouse_id)

    def fn() -> Result:
        _warehouse(db, p, body.warehouse_id)
        d = db.get(ScanDevice, body.device_id)
        if d is None or d.company_id != p.company_id:
            raise ApiError(422, "INVALID_DEVICE", "Unknown scanner")
        if d.status != "ACTIVE":
            raise ApiError(
                409, "DEVICE_NOT_ACTIVE", "This scanner is not approved", current_status=d.status
            )
        if d.warehouse_id != body.warehouse_id:
            raise ApiError(422, "WRONG_WAREHOUSE", "This scanner belongs to another location")
        j = ScanJob(
            company_id=p.company_id,
            warehouse_id=body.warehouse_id,
            job_no=svc.next_job_no(db, p.company_id),
            job_type=body.job_type,
            channel="BRIDGE",
            status="REQUESTED",
            device_id=d.id,
            note=(body.note or "").strip() or None,
            requested_by=p.employee.id,
        )
        db.add(j)
        db.flush()
        token, nonce_hash, exp = svc.issue_capability(j.id, d.id)
        j.capability_nonce_hash = nonce_hash
        j.capability_expires_at = exp
        db.flush()
        out = {**job_out(db, j), "capability": token, "capability_expires_at": exp.isoformat()}
        return out, str(j.id), {"job_no": j.job_no, "device": str(d.id)}

    return mutate(db, request, p, key, "scan.job", body, 201, "scan_job.request", fn)


@router.post("/scans/jobs/{job_id}/cancel", response_model=ScanJobOut)
def cancel_job(
    job_id: uuid.UUID,
    body: ScanCancelIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    def fn() -> Result:
        j = get_job(db, request, p, job_id)
        authorize(request, p, "scan.create", j.warehouse_id)
        j = db.get(ScanJob, job_id, with_for_update=True, populate_existing=True) or j
        _check_version(j.version, body.version)
        if j.status not in ("REQUESTED", "SCANNING"):
            raise ApiError(
                409, "INVALID_STATE", "This scan is already finished", current_status=j.status
            )
        j.status = "CANCELLED"
        j.completed_at = svc.now()
        j.version += 1
        db.flush()
        return job_out(db, j), str(j.id), {"job_no": j.job_no}

    return mutate(db, request, p, key, f"scan.cancel:{job_id}", body, 200, "scan_job.cancel", fn)


@router.post(
    "/scans/uploads",
    response_model=ScanJobOut,
    responses={201: {"model": ScanJobOut}},
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
def manual_upload(
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
    data: RawBody,
    warehouse_id: uuid.UUID,
    job_type: ScanJobType,
    filename: str = Query("scan", max_length=255),
    note: str | None = Query(None, max_length=500),
) -> JSONResponse:
    """Manual fallback (always available). Raw file body. The same bytes uploaded again return
    the existing scan (200, duplicate) instead of creating a second source record."""
    authorize(request, p, "scan.create", warehouse_id)
    mime = svc.check_upload(data, request.headers.get("content-type"))
    digest = svc.sha256hex(data)
    params = ScanUploadIn(
        warehouse_id=warehouse_id, job_type=job_type, filename=filename, note=note, sha256=digest
    )
    _warehouse(db, p, warehouse_id)
    # Same bytes already on file → return the scan that holds them (no new source, no new job).
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"doc:{p.company_id}:{digest}"},
    )
    existing = db.scalar(
        select(ScanJob)
        .join(SourceDocument, SourceDocument.id == ScanJob.document_id)
        .where(SourceDocument.company_id == p.company_id, SourceDocument.sha256 == digest)
        .order_by(ScanJob.created_at)
        .limit(1)
    )
    if existing is not None:
        if not _can_read(p, existing.warehouse_id):
            # Don't reveal another location's document; still refuse to duplicate it.
            raise ApiError(409, "DUPLICATE_UPLOAD", "This file was already uploaded elsewhere")
        db.rollback()
        out = {**job_out(db, existing, pages=True), "duplicate": True}
        return JSONResponse(out, 200, headers={"X-Duplicate-Of": str(existing.id)})

    def fn() -> Result:
        doc, _ = svc.store_document(db, p.company_id, data, mime, filename, p.employee.id, "MANUAL")
        t = svc.now()
        j = ScanJob(
            company_id=p.company_id,
            warehouse_id=warehouse_id,
            job_no=svc.next_job_no(db, p.company_id),
            job_type=job_type,
            channel="MANUAL",
            status="UPLOADED",
            note=(note or "").strip() or None,
            requested_by=p.employee.id,
            document_id=doc.id,
            claimed_at=t,
            completed_at=t,
        )
        db.add(j)
        db.flush()
        documents.file_scan(db, j, doc)
        ocr.auto_queue(db, j, p.employee.id)
        out = job_out(db, j, pages=True)
        return out, str(j.id), {"job_no": j.job_no, "sha256": digest, "bytes": len(data)}

    return mutate(db, request, p, key, "scan.upload", params, 201, "scan_job.upload", fn)


def _readable_doc(
    db: Session, request: Request, p: Principal, document_id: uuid.UUID
) -> SourceDocument:
    doc = db.get(SourceDocument, document_id)
    if doc is None or doc.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Document not found")
    whs = set(db.scalars(select(ScanJob.warehouse_id).where(ScanJob.document_id == doc.id)).all())
    # Receiving staff may open vendor documents attached to shipments they can see.
    ship_whs = set(
        db.scalars(
            select(Shipment.warehouse_id)
            .join(ShipmentDocument, ShipmentDocument.shipment_id == Shipment.id)
            .where(ShipmentDocument.document_id == doc.id)
        ).all()
    )
    if not any(_can_read(p, w) for w in whs) and not any(
        p.can(perm, w) for w in ship_whs for perm in ("grn.record", "grn.approve")
    ):
        authorize(request, p, "scan.review", next(iter(whs), None))
    return doc


_PAGE_CACHE: dict[tuple[str, int], tuple[bytes, int]] = {}
_PAGE_CACHE_LOCK = threading.Lock()  # sync routes run on many threads at once


@router.get(
    "/scans/documents/{document_id}/pages/{page}",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def document_page(
    document_id: uuid.UUID,
    page: int,
    request: Request,
    db: DbDep,
    p: PrincipalDep,
) -> Response:
    """One page as a PNG for the review viewer (PDF pages rendered at 110 dpi; TIFF/JPEG/PNG
    converted). The original stays untouched. Header X-Page-Count gives the number of pages."""
    doc = _readable_doc(db, request, p, document_id)
    if page < 1 or page > 500:
        raise ApiError(404, "NOT_FOUND", "No such page")
    key = (doc.sha256, page)
    with _PAGE_CACHE_LOCK:
        hit = _PAGE_CACHE.get(key)
    if hit is None:
        hit = svc.render_page(doc.content, doc.mime_type, page)
        with _PAGE_CACHE_LOCK:
            _PAGE_CACHE[key] = hit
            while len(_PAGE_CACHE) > 64:
                _PAGE_CACHE.pop(next(iter(_PAGE_CACHE)))
    png, count = hit
    return Response(
        png,
        media_type="image/png",
        headers={
            "X-Page-Count": str(count),
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/scans/documents/{document_id}/content",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, "image/png": {}, "image/jpeg": {}}}},
)
def document_content(
    document_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> Response:
    """Original bytes, private. Allowed when the caller can read a scan that holds them."""
    doc = _readable_doc(db, request, p, document_id)
    etag = f'"{doc.sha256}"'
    headers = {
        "ETag": etag,
        "X-Content-SHA256": doc.sha256,
        "Cache-Control": "private, max-age=0, must-revalidate",
        "X-Content-Type-Options": "nosniff",
        # Chrome's PDF viewer will not run in a CSP-sandboxed document, so PDFs get a
        # locked-down policy without `sandbox`; every other type stays sandboxed.
        "Content-Security-Policy": (
            "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'"
            if doc.mime_type == "application/pdf"
            else "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
            "frame-ancestors 'self'"
        ),
        "Content-Disposition": content_disposition("inline", doc.original_filename),
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(doc.content, media_type=doc.mime_type, headers=headers)


# ------------------------------------------------------------------ bridge (device auth)


@router.post("/scan-bridge/pair", response_model=BridgePairOut)
def bridge_pair(body: BridgePairIn, request: Request, db: DbDep) -> dict[str, Any]:
    """One-time: exchange the pairing code for a device secret. The bridge shows the
    fingerprint on screen; the operator confirms it in the web app (approve)."""
    d = db.scalar(
        select(ScanDevice)
        .where(ScanDevice.pairing_code_hash == svc.code_hash(body.pairing_code))
        .with_for_update()
    )
    if (
        d is None
        or d.status != "PENDING"
        or d.secret_hash is not None
        or d.pairing_expires_at is None
        or d.pairing_expires_at < svc.now()
    ):
        audit.record_independent(
            action="scan.bridge.pair_failed",
            success=False,
            request_id=request.state.request_id,
            target_type="scan_bridge",
            target_id=request.url.path,
            detail={"reason": "code"},
        )
        raise ApiError(403, "PAIRING_CODE_INVALID", "Pairing code is wrong, used or expired")
    secret = secrets.token_urlsafe(32)
    d.secret_hash = svc.secret_hash(secret)
    d.fingerprint = svc.fingerprint(secret)
    d.pairing_code_hash = None  # single use
    d.paired_at = svc.now()
    d.bridge_version = body.bridge_version
    d.version += 1
    audit.record(
        db,
        action="scan_device.paired",
        success=True,
        company_id=d.company_id,
        target_type="scan_device",
        target_id=str(d.id),
        request_id=request.state.request_id,
        detail={"bridge_version": body.bridge_version},
    )
    db.commit()
    return {"device_id": str(d.id), "device_secret": secret, "fingerprint": d.fingerprint}


@router.post("/scan-bridge/heartbeat", response_model=BridgeHeartbeatOut)
def bridge_heartbeat(body: BridgeHeartbeatIn, request: Request, db: DbDep) -> dict[str, Any]:
    d = svc.device_auth(db, request, allow_pending=True, lock=True)
    d.last_seen_at = svc.now()
    d.scanner_state = body.scanner_state
    d.scanner_detail = body.scanner_detail
    d.bridge_version = body.bridge_version
    if body.scanners is not None:
        d.detected_scanners = [s.model_dump() for s in body.scanners]
        d.detected_at = svc.now()
    discover = (
        d.status == "ACTIVE"
        and d.discover_requested_at is not None
        and (d.detected_at is None or d.detected_at < d.discover_requested_at)
    )
    db.commit()
    return {"status": d.status, "server_time": svc.now().isoformat(), "discover": discover}


def _bridge_job(db: Session, d: ScanDevice, job_id: uuid.UUID) -> ScanJob:
    j = db.get(ScanJob, job_id, with_for_update=True, populate_existing=True)
    if j is None or j.device_id != d.id or j.company_id != d.company_id:
        raise ApiError(404, "NOT_FOUND", "Scan not found")
    return j


@router.post("/scan-bridge/jobs/{job_id}/claim", response_model=BridgeClaimOut)
def bridge_claim(
    job_id: uuid.UUID, body: BridgeClaimIn, request: Request, db: DbDep
) -> dict[str, Any]:
    """Device auth + valid capability for this job and device → SCANNING (single flight)."""
    d = svc.device_auth(db, request)
    try:
        cap = svc.verify_capability(body.capability)
    except svc.CapabilityError as e:
        raise svc.capability_rejected(request, e.code, {"device": str(d.id)}) from None
    if cap["j"] != str(job_id) or cap["d"] != str(d.id):
        raise svc.capability_rejected(request, "CAPABILITY_INVALID", {"device": str(d.id)})
    svc.sweep(db, d.company_id)
    j = _bridge_job(db, d, job_id)
    if j.status == "EXPIRED":
        db.commit()
        raise svc.capability_rejected(request, "CAPABILITY_EXPIRED", {"job": str(j.id)})
    if j.status != "REQUESTED":
        db.commit()
        raise svc.capability_rejected(
            request, "CAPABILITY_USED", {"job": str(j.id), "status": j.status}
        )
    if j.capability_nonce_hash != svc.sha256hex(str(cap["n"]).encode()):
        raise svc.capability_rejected(request, "CAPABILITY_INVALID", {"job": str(j.id)})
    j.status = "SCANNING"
    j.claimed_at = svc.now()
    j.capability_nonce_hash = None  # single use
    j.version += 1
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(
            409, "SCANNER_BUSY", "This scanner is already scanning another sheet"
        ) from None
    audit.record(
        db,
        action="scan_job.claim",
        success=True,
        company_id=d.company_id,
        target_type="scan_job",
        target_id=str(j.id),
        warehouse_id=j.warehouse_id,
        request_id=request.state.request_id,
        detail={"device": str(d.id)},
    )
    s = get_settings()
    out = {
        "job_id": str(j.id),
        "job_no": j.job_no,
        "job_type": j.job_type,
        "max_bytes": s.scan_max_bytes,
        "timeout_seconds": s.scan_timeout_seconds,
    }
    db.commit()
    return out


@router.post(
    "/scan-bridge/jobs/{job_id}/upload",
    response_model=ScanJobOut,
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
def bridge_upload(
    job_id: uuid.UUID,
    request: Request,
    db: DbDep,
    d: BridgeDevice,
    data: RawBody,  # declared after the device: the body is read only once it authenticated
    filename: str = Query("scan", max_length=255),
) -> dict[str, Any]:
    """Scanned bytes for a job this device claimed. Retrying after success returns the job."""
    j = _bridge_job(db, d, job_id)
    mime = svc.check_upload(data, request.headers.get("content-type"))
    digest = svc.sha256hex(data)
    if j.status == "UPLOADED":
        doc = db.get(SourceDocument, j.document_id)
        if doc is not None and doc.sha256 == digest:
            db.commit()
            return job_out(db, j)  # network retry of the same upload
    if j.status != "SCANNING":
        db.commit()
        raise ApiError(
            409, "INVALID_STATE", "This scan is not waiting for a document", current_status=j.status
        )
    doc, existed = svc.store_document(db, d.company_id, data, mime, filename, None, "BRIDGE")
    t = svc.now()
    j.document_id = doc.id
    j.duplicate = existed
    j.status = "UPLOADED"
    j.completed_at = t
    j.version += 1
    audit.record(
        db,
        action="scan_job.upload",
        success=True,
        company_id=d.company_id,
        target_type="scan_job",
        target_id=str(j.id),
        warehouse_id=j.warehouse_id,
        request_id=request.state.request_id,
        detail={"sha256": digest, "bytes": len(data), "duplicate": existed},
    )
    db.flush()
    documents.file_scan(db, j, doc)
    ocr.auto_queue(db, j, j.requested_by)
    db.commit()
    return job_out(db, j)


@router.post("/scan-bridge/jobs/{job_id}/fail", response_model=ScanJobOut)
def bridge_fail(
    job_id: uuid.UUID, body: BridgeFailIn, request: Request, db: DbDep
) -> dict[str, Any]:
    """Scanner unplugged / driver missing / timeout → FAILED; the UI offers manual upload."""
    d = svc.device_auth(db, request)
    j = _bridge_job(db, d, job_id)
    if j.status == "FAILED" and j.error_code == body.error_code:
        db.commit()
        return job_out(db, j)
    if j.status not in ("SCANNING", "REQUESTED"):
        db.commit()
        raise ApiError(
            409, "INVALID_STATE", "This scan is already finished", current_status=j.status
        )
    j.status = "FAILED"
    j.error_code = body.error_code
    j.error_detail = body.detail
    j.completed_at = svc.now()
    j.version += 1
    if body.error_code in ("SCANNER_NOT_FOUND", "DRIVER_MISSING"):
        d.scanner_state = (
            "NOT_FOUND" if body.error_code == "SCANNER_NOT_FOUND" else "DRIVER_MISSING"
        )
        d.scanner_detail = body.detail
    audit.record(
        db,
        action="scan_job.failed",
        success=False,
        company_id=d.company_id,
        target_type="scan_job",
        target_id=str(j.id),
        warehouse_id=j.warehouse_id,
        request_id=request.state.request_id,
        detail={"error_code": body.error_code},
    )
    db.commit()
    return job_out(db, j)
