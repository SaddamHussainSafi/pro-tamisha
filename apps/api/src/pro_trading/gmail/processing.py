"""From a chosen email attachment to the existing scan -> OCR -> review pipeline.

Nothing here runs on its own: every function is called because a signed-in person asked to
preview, download or process one attachment.

- Bytes are fetched from Gmail on demand, size-checked against the same limit as uploads, and
  identified by their magic bytes (the email's declared type and the file name are not trusted).
- "Use for OCR" stores the file as a `source_documents` row (the app's existing private store,
  one row per company + SHA-256), opens a scan job and queues the existing OCR reading. The
  reading ends in the existing review screen; accepting it creates a DRAFT sales order and
  nothing else.
- Every use is recorded as a `gmail_processing_jobs` attempt. An attachment (or the same file
  from another email or an upload) that was already processed is not processed again unless
  the caller asks for a re-process, and, when a draft already came out of it, confirms that.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import documents, ocr, scanning, supplier_ocr
from ..config import get_settings
from ..db import get_sessionmaker
from ..deps import Principal
from ..errors import ApiError
from ..models import Employee, OcrExtraction, SalesOrder, ScanJob, SourceDocument
from . import client as g
from .models import GmailAccount, GmailAttachment, GmailMessage, GmailProcessingJob
from .sync import mailbox_for

CATEGORIES = (*ocr.ORDER_TYPES, *supplier_ocr.SUPPLIER_TYPES)
MAX_PREVIEW_PAGES = 500

# A few recently opened files, so paging through a preview does not fetch from Gmail each time.
_CACHE: OrderedDict[uuid.UUID, tuple[bytes, str]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_ITEMS = 6


def _cache_get(aid: uuid.UUID) -> tuple[bytes, str] | None:
    with _CACHE_LOCK:
        hit = _CACHE.get(aid)
        if hit is not None:
            _CACHE.move_to_end(aid)
        return hit


def _cache_put(aid: uuid.UUID, data: bytes, mime: str) -> None:
    with _CACHE_LOCK:
        _CACHE[aid] = (data, mime)
        _CACHE.move_to_end(aid)
        while len(_CACHE) > _CACHE_ITEMS:
            _CACHE.popitem(last=False)


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


UNSUPPORTED = "Only PDF, PNG, JPEG or TIFF attachments can be opened or read"


def reject_reason(a: GmailAttachment) -> str | None:
    """Why this attachment cannot be previewed or processed, from what is known without
    fetching it (None = it can be tried)."""
    if a.rejected_code == "FILE_TOO_LARGE" or a.byte_size > get_settings().scan_max_bytes:
        return "The file is larger than the upload limit"
    if a.rejected_code == "UNREADABLE":
        return "The file could not be opened (damaged or password-protected)"
    if not a.eligible or a.rejected_code == "UNSUPPORTED_FILE":
        return UNSUPPORTED
    return None


def _reject(aid: uuid.UUID, code: str) -> None:
    with get_sessionmaker()() as db, db.begin():
        a = db.get(GmailAttachment, aid, with_for_update=True)
        if a is not None:
            a.rejected_code = code


def _from_gmail(att: GmailAttachment, msg: GmailMessage) -> bytes:
    """The attachment's bytes from Gmail. Attachment ids are not stable, so the message is read
    again and the part is found by its part id."""
    acc_id = msg.account_id
    try:
        box = mailbox_for(acc_id)
        full = box.get_message(msg.gmail_message_id)
        part = _find_part(full.get("payload") or {}, att.part_id)
        if part is None:
            raise ApiError(404, "ATTACHMENT_GONE", "This attachment is no longer on the email")
        body = part.get("body") or {}
        if body.get("data"):
            return g.b64url_decode(str(body["data"]))
        if not body.get("attachmentId"):
            raise ApiError(404, "ATTACHMENT_GONE", "This attachment is no longer on the email")
        return box.get_attachment(msg.gmail_message_id, str(body["attachmentId"]))
    except g.ReauthRequired:
        raise ApiError(
            409, "GMAIL_REAUTH_REQUIRED", "The mailbox must be connected again first"
        ) from None
    except g.GmailError as e:
        if e.code == "GMAIL_NOT_CONNECTED":
            raise ApiError(
                409,
                "GMAIL_NOT_CONNECTED",
                "This email's mailbox is not connected, so its file cannot be fetched",
            ) from None
        if e.status == 404:
            raise ApiError(
                404, "ATTACHMENT_GONE", "This email is no longer in the mailbox"
            ) from None
        raise ApiError(
            502, "GMAIL_UNAVAILABLE", "Gmail did not answer. Try again in a moment."
        ) from None


def _find_part(part: dict[str, Any], part_id: str, depth: int = 0) -> dict[str, Any] | None:
    if depth > 20 or not isinstance(part, dict):
        return None
    if str(part.get("partId") or "") == part_id and part.get("filename"):
        return part
    for sub in part.get("parts") or []:
        hit = _find_part(sub, part_id, depth + 1)
        if hit is not None:
            return hit
    return None


def load(db: Session, att: GmailAttachment, msg: GmailMessage) -> tuple[bytes, str]:
    """(bytes, real MIME type) of an attachment, checked. Raises ApiError when it cannot be
    used. No transaction is held on `db` while Gmail is called."""
    s = get_settings()
    if (hit := _cache_get(att.id)) is not None:
        return hit
    if att.source_document_id is not None:  # already kept privately: no need to ask Gmail
        doc = db.get(SourceDocument, att.source_document_id)
        if doc is not None:
            _cache_put(att.id, doc.content, doc.mime_type)
            return doc.content, doc.mime_type
    if not att.eligible:
        raise ApiError(415, "UNSUPPORTED_FILE", UNSUPPORTED)
    if att.byte_size > s.scan_max_bytes:
        raise ApiError(413, "FILE_TOO_LARGE", "The file is too large", max_bytes=s.scan_max_bytes)
    db.rollback()  # nothing open while the network call runs
    data = _from_gmail(att, msg)
    if not data:
        raise ApiError(422, "EMPTY_FILE", "The attachment is empty")
    if len(data) > s.scan_max_bytes:
        _reject(att.id, "FILE_TOO_LARGE")
        raise ApiError(413, "FILE_TOO_LARGE", "The file is too large", max_bytes=s.scan_max_bytes)
    mime = scanning.sniff(data)
    if mime is None:
        _reject(att.id, "UNSUPPORTED_FILE")
        raise ApiError(415, "UNSUPPORTED_FILE", UNSUPPORTED)
    pages = scanning.page_count(data, mime)
    if not pages:
        _reject(att.id, "UNREADABLE")
        raise ApiError(
            422, "UNREADABLE", "The file could not be opened (damaged or password-protected)"
        )
    digest = scanning.sha256hex(data)
    with get_sessionmaker()() as s2, s2.begin():
        a = s2.get(GmailAttachment, att.id, with_for_update=True)
        if a is not None:
            a.sha256, a.detected_mime, a.page_count, a.rejected_code = digest, mime, pages, None
    att.sha256, att.detected_mime, att.page_count = digest, mime, pages
    _cache_put(att.id, data, mime)
    return data, mime


# ------------------------------------------------------------------ history


def _name(db: Session, emp_id: uuid.UUID | None) -> str | None:
    if emp_id is None:
        return None
    e = db.get(Employee, emp_id)
    return e.display_name if e else None


def _live_extraction(db: Session, scan_job_id: uuid.UUID) -> OcrExtraction | None:
    return db.scalar(
        select(OcrExtraction)
        .where(OcrExtraction.scan_job_id == scan_job_id, OcrExtraction.status != "SUPERSEDED")
        .order_by(OcrExtraction.run_no.desc())
        .limit(1)
    )


STATE = {
    "QUEUED": "PROCESSING",
    "NEEDS_REVIEW": "NEEDS_REVIEW",
    "FAILED": "FAILED",
    "ACCEPTED": "PROCESSED",
    "REJECTED": "REJECTED",
}


def _entry(
    db: Session,
    *,
    scan: ScanJob,
    job: GmailProcessingJob | None,
    same_attachment: bool,
) -> dict[str, Any]:
    ex = _live_extraction(db, scan.id)
    so = db.get(SalesOrder, ex.sales_order_id) if ex and ex.sales_order_id else None
    return {
        "job_id": str(job.id) if job else None,
        "attempt_no": job.attempt_no if job else None,
        "source": "gmail" if job else "scan",
        "same_attachment": same_attachment,
        "reprocess": bool(job and job.reprocess),
        "document_category": job.document_category if job else scan.job_type,
        "processed_at": (job.created_at if job else scan.created_at).isoformat(),
        "processed_by_name": _name(db, job.created_by if job else scan.requested_by),
        "scan_job_id": str(scan.id),
        "scan_job_no": scan.job_no,
        "extraction_id": str(ex.id) if ex else None,
        "status": STATE.get(ex.status, "RECEIVED") if ex else "RECEIVED",
        "error_code": ex.error_code if ex else None,
        "shipment_id": str(ex.shipment_id) if ex and ex.shipment_id else None,
        "sales_order_id": str(so.id) if so else None,
        "order_no": so.order_no if so else None,
        "order_status": so.status if so else None,
    }


def history(db: Session, att: GmailAttachment) -> list[dict[str, Any]]:
    """Every earlier use of this attachment, newest first: its own attempts, and (once the file
    was fetched, so its hash is known) the same file processed from another email or uploaded
    by hand."""
    out: list[dict[str, Any]] = []
    seen_scans: set[uuid.UUID] = set()
    jobs = db.scalars(
        select(GmailProcessingJob)
        .where(GmailProcessingJob.attachment_id == att.id)
        .order_by(GmailProcessingJob.attempt_no.desc())
    ).all()
    for j in jobs:
        scan = db.get(ScanJob, j.scan_job_id)
        if scan is None:
            continue
        seen_scans.add(scan.id)
        out.append(_entry(db, scan=scan, job=j, same_attachment=True))
    if att.sha256:
        doc = db.scalar(
            select(SourceDocument).where(
                SourceDocument.company_id == att.company_id, SourceDocument.sha256 == att.sha256
            )
        )
        if doc is not None:
            scans = db.scalars(
                select(ScanJob)
                .where(ScanJob.document_id == doc.id, ScanJob.company_id == att.company_id)
                .order_by(ScanJob.created_at.desc())
            ).all()
            for scan in scans:
                if scan.id in seen_scans:
                    continue
                other = db.scalar(
                    select(GmailProcessingJob).where(GmailProcessingJob.scan_job_id == scan.id)
                )
                out.append(_entry(db, scan=scan, job=other, same_attachment=False))
    out.sort(key=lambda e: e["processed_at"], reverse=True)
    return out


def state_of(db: Session, attachment_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Processing state per attachment for list views (latest attempt's reading)."""
    if not attachment_ids:
        return {}
    latest: dict[uuid.UUID, GmailProcessingJob] = {}
    for j in db.scalars(
        select(GmailProcessingJob)
        .where(GmailProcessingJob.attachment_id.in_(attachment_ids))
        .order_by(GmailProcessingJob.attempt_no)
    ):
        latest[j.attachment_id] = j
    out: dict[uuid.UUID, str] = {}
    for aid, j in latest.items():
        ex = _live_extraction(db, j.scan_job_id)
        out[aid] = STATE.get(ex.status, "RECEIVED") if ex else "RECEIVED"
    return out


# ------------------------------------------------------------------ process


def start(
    db: Session,
    p: Principal,
    att: GmailAttachment,
    msg: GmailMessage,
    data: bytes,
    mime: str,
    *,
    warehouse_id: uuid.UUID,
    category: str,
    reprocess: bool,
    confirm_duplicate: bool,
) -> tuple[GmailProcessingJob, ScanJob, OcrExtraction]:
    """Hand a fetched, checked attachment to the scan + OCR pipeline (caller's transaction).

    Refuses (409 ALREADY_PROCESSED) when this file was used before and `reprocess` is not set,
    and (409 DRAFT_EXISTS) when a draft already came out of it and `confirm_duplicate` is not
    set. The caller has already checked permissions."""
    digest = scanning.sha256hex(data)
    # One at a time per file: two clicks (or two people) cannot both pass the checks below.
    db.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"doc:{p.company_id}:{digest}", 0)))
    )
    a = db.get(GmailAttachment, att.id, with_for_update=True, populate_existing=True)
    assert a is not None
    a.sha256 = digest
    a.detected_mime = mime
    prior = history(db, a)
    if prior and not reprocess:
        raise ApiError(
            409,
            "ALREADY_PROCESSED",
            "This document was already processed",
            previous=prior[:5],
        )
    drafts = [e for e in prior if e["sales_order_id"] or e["shipment_id"]]
    if drafts and not confirm_duplicate:
        raise ApiError(
            409,
            "DRAFT_EXISTS",
            "A draft was already created from this document. Confirm to read it again.",
            previous=drafts[:5],
        )
    doc, _existed = scanning.store_document(
        db, p.company_id, data, mime, a.filename, p.employee.id, "GMAIL"
    )
    a.source_document_id = doc.id
    t = scanning.now()
    sender = msg.from_name or msg.from_email or "unknown sender"
    scan = ScanJob(
        company_id=p.company_id,
        warehouse_id=warehouse_id,
        job_no=scanning.next_job_no(db, p.company_id),
        job_type=category,
        channel="MANUAL",  # no scanner bridge involved; the Gmail origin is on the job below
        status="UPLOADED",
        note=f"Gmail: {a.filename} from {sender}"[:500],
        requested_by=p.employee.id,
        document_id=doc.id,
        claimed_at=t,
        completed_at=t,
        duplicate=bool(prior),
    )
    db.add(scan)
    db.flush()
    documents.file_scan(db, scan, doc)
    ex = ocr.queue(db, scan, p.employee.id)
    attempt = (
        db.scalar(
            select(func.max(GmailProcessingJob.attempt_no)).where(
                GmailProcessingJob.attachment_id == a.id
            )
        )
        or 0
    ) + 1
    job = GmailProcessingJob(
        company_id=p.company_id,
        message_id=msg.id,
        attachment_id=a.id,
        attempt_no=attempt,
        document_category=category,
        warehouse_id=warehouse_id,
        scan_job_id=scan.id,
        extraction_id=ex.id,
        reprocess=bool(prior),
        duplicate_confirmed=bool(drafts),
        created_by=p.employee.id,
    )
    db.add(job)
    db.flush()
    return job, scan, ex


def account_of(db: Session, msg: GmailMessage) -> GmailAccount | None:
    return db.get(GmailAccount, msg.account_id)
