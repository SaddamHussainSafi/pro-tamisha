"""Module 09 documents: file issued invoices, PODs, scans and vendor documents into one archive,
then copy them to Drive asynchronously.

Rules (docs/modules/09-documents.md):
- The archive is an index: bytes stay in ``invoice_documents`` / ``source_documents`` (both
  append-only). ``document_files`` rows can never be deleted and only sync fields may change
  (DB trigger ``document_file_guard``). An issued PDF or a signed POD is never overwritten.
- Drive IO runs from the outbox, never inside the business transaction: a Drive timeout leaves
  the invoice committed with sync status PENDING; retries are automatic, then FAILED with a
  manual retry. Each file gets one stable Drive id (the provider looks up our id before creating).
- Folders: ``Customers/{ref} {name}/{YYYY}/{MM}``, ``Vendors/{code} {name}/{YYYY}/{MM}``,
  ``Scans/{YYYY}/{MM}`` in the company timezone.

Providers run server-side only (credentials never reach the browser):
- ``local``: writes under ``drive_local_root`` (dev/test). Never overwrites a different file.
- ``google``: Drive v3 REST with a service account (UNVERIFIED until the office pilot).
- ``disabled``: files stay in the ERP only (LOCAL_ONLY).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from . import audit, outbox
from .config import get_settings
from .errors import ApiError
from .models import (
    Company,
    Customer,
    DocumentFile,
    Invoice,
    InvoiceDocument,
    OutboxEvent,
    ScanJob,
    Shipment,
    ShipmentDocument,
    SourceDocument,
    Vendor,
)
from .scanning import _EXT

log = logging.getLogger("pro_trading.documents")

DRIVE_EVENT = "document.drive_sync"


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class DriveError(Exception):
    code: str
    message: str
    transient: bool

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


# ------------------------------------------------------------------ folder names


def _clean(s: str, n: int = 60) -> str:
    """Folder-safe: no path separators or control characters, bounded."""
    s = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", s).strip(" .")
    return re.sub(r"\s+", " ", s)[:n] or "-"


def _ym(company: Company, when: datetime) -> str:
    t = when.astimezone(ZoneInfo(company.timezone))
    return f"{t.year:04d}/{t.month:02d}"


def customer_folder(company: Company, c: Customer, when: datetime) -> str:
    ref = c.external_ref or f"C-{str(c.id)[:8]}"
    return f"Customers/{_clean(ref, 32)} {_clean(c.name)}/{_ym(company, when)}"


def vendor_folder(company: Company, v: Vendor, when: datetime) -> str:
    return f"Vendors/{_clean(v.code, 32)} {_clean(v.name)}/{_ym(company, when)}"


def scans_folder(company: Company, when: datetime) -> str:
    return f"Scans/{_ym(company, when)}"


# ------------------------------------------------------------------ filing (in the caller's tx)


def _initial_status() -> str:
    return "LOCAL_ONLY" if get_settings().drive_provider == "disabled" else "PENDING"


def _add(db: Session, f: DocumentFile) -> DocumentFile:
    db.add(f)
    db.flush()
    if f.sync_status == "PENDING":
        _queue(db, f)
    return f


def _queue(db: Session, f: DocumentFile) -> None:
    outbox.enqueue(
        db,
        DRIVE_EVENT,
        {"document_file_id": str(f.id)},
        company_id=f.company_id,
        aggregate_id=str(f.id),
    )


def _existing_source(db: Session, company_id: uuid.UUID, doc_id: uuid.UUID, folder: str) -> Any:
    return db.scalar(
        select(DocumentFile).where(
            DocumentFile.company_id == company_id,
            DocumentFile.source_document_id == doc_id,
            DocumentFile.folder == folder,
        )
    )


def _unique_name(db: Session, company_id: uuid.UUID, folder: str, stem: str, ext: str) -> str:
    """`stem.ext`, or `stem (2).ext` ... when that name is taken in the folder."""
    taken = set(
        db.scalars(
            select(DocumentFile.filename).where(
                DocumentFile.company_id == company_id,
                DocumentFile.folder == folder,
                DocumentFile.filename.like(f"{stem}%"),
            )
        ).all()
    )
    name, n = f"{stem}{ext}", 1
    while name in taken:
        n += 1
        name = f"{stem} ({n}){ext}"
    return name


def file_invoice(
    db: Session, invoice: Invoice, doc: InvoiceDocument, customer: Customer, company: Company
) -> DocumentFile:
    """Called by invoicing.issue in the same transaction as the invoice."""
    return _add(
        db,
        DocumentFile(
            company_id=invoice.company_id,
            warehouse_id=invoice.warehouse_id,
            kind="INVOICE_PDF",
            invoice_document_id=doc.id,
            invoice_id=invoice.id,
            customer_id=customer.id,
            folder=customer_folder(company, customer, invoice.issued_at),
            filename=f"{invoice.invoice_no}.pdf",
            mime_type="application/pdf",
            sha256=doc.sha256,
            byte_size=doc.byte_size,
            sync_status=_initial_status(),
            filed_by=invoice.issued_by,
        ),
    )


def file_scan(db: Session, job: ScanJob, doc: SourceDocument) -> DocumentFile:
    """A scan that arrived (bridge or manual upload). Same bytes again → the existing file."""
    company = db.get(Company, job.company_id)
    assert company is not None
    folder = scans_folder(company, job.completed_at or now())
    if (f := _existing_source(db, job.company_id, doc.id, folder)) is not None:
        return f  # type: ignore[no-any-return]
    return _add(
        db,
        DocumentFile(
            company_id=job.company_id,
            warehouse_id=job.warehouse_id,
            kind="SCAN",
            source_document_id=doc.id,
            scan_job_id=job.id,
            folder=folder,
            filename=_unique_name(
                db, job.company_id, folder, f"{job.job_no} {job.job_type}", _EXT[doc.mime_type]
            ),
            mime_type=doc.mime_type,
            sha256=doc.sha256,
            byte_size=doc.byte_size,
            sync_status=_initial_status(),
            filed_by=job.requested_by,
        ),
    )


def file_vendor_doc(
    db: Session, s: Shipment, link: ShipmentDocument, doc: SourceDocument
) -> DocumentFile:
    """A vendor document attached to a shipment → the vendor's folder (kept unaltered)."""
    company = db.get(Company, s.company_id)
    vendor = db.get(Vendor, s.vendor_id)
    assert company is not None and vendor is not None
    folder = vendor_folder(company, vendor, link.created_at or now())
    if (f := _existing_source(db, s.company_id, doc.id, folder)) is not None:
        return f  # type: ignore[no-any-return]
    ref = f" {_clean(link.doc_ref, 40)}" if link.doc_ref else ""
    return _add(
        db,
        DocumentFile(
            company_id=s.company_id,
            warehouse_id=s.warehouse_id,
            kind="VENDOR_DOC",
            source_document_id=doc.id,
            shipment_id=s.id,
            scan_job_id=link.scan_job_id,
            vendor_id=vendor.id,
            folder=folder,
            filename=_unique_name(
                db, s.company_id, folder, f"{s.shipment_no} {link.kind}{ref}", _EXT[doc.mime_type]
            ),
            mime_type=doc.mime_type,
            sha256=doc.sha256,
            byte_size=doc.byte_size,
            sync_status=_initial_status(),
            filed_by=link.added_by,
        ),
    )


def file_pod(
    db: Session,
    invoice: Invoice,
    doc: SourceDocument,
    actor_id: uuid.UUID,
    note: str | None,
) -> tuple[DocumentFile, bool]:
    """Signed proof of delivery for an issued invoice. Append-only: a second, different signed
    copy is filed next to the first (``INV-… POD (2).pdf``); the same bytes return the existing
    file. Returns (file, already_filed)."""
    company = db.get(Company, invoice.company_id)
    customer = db.get(Customer, invoice.customer_id)
    assert company is not None and customer is not None
    folder = customer_folder(company, customer, invoice.issued_at)
    if (f := _existing_source(db, invoice.company_id, doc.id, folder)) is not None:
        return f, True
    f = _add(
        db,
        DocumentFile(
            company_id=invoice.company_id,
            warehouse_id=invoice.warehouse_id,
            kind="POD",
            source_document_id=doc.id,
            invoice_id=invoice.id,
            customer_id=customer.id,
            folder=folder,
            filename=_unique_name(
                db, invoice.company_id, folder, f"{invoice.invoice_no} POD", _EXT[doc.mime_type]
            ),
            mime_type=doc.mime_type,
            sha256=doc.sha256,
            byte_size=doc.byte_size,
            sync_status=_initial_status(),
            note=(note or "").strip() or None,
            filed_by=actor_id,
        ),
    )
    return f, False


def content_of(db: Session, f: DocumentFile) -> bytes:
    if f.invoice_document_id is not None:
        idoc = db.get(InvoiceDocument, f.invoice_document_id)
        assert idoc is not None
        return bytes(idoc.content)
    sdoc = db.get(SourceDocument, f.source_document_id)
    assert sdoc is not None
    return bytes(sdoc.content)


def retry(db: Session, f: DocumentFile) -> None:
    """FAILED → PENDING with a fresh outbox event (caller holds the row lock)."""
    if f.sync_status != "FAILED":
        raise ApiError(
            409, "INVALID_STATE", "Only failed copies can be retried", current_status=f.sync_status
        )
    if get_settings().drive_provider == "disabled":
        raise ApiError(409, "DRIVE_DISABLED", "Drive copying is turned off for this installation")
    f.sync_status = "PENDING"
    f.last_error = None
    f.version += 1
    _queue(db, f)


# ------------------------------------------------------------------ Drive providers


def _fault() -> None:
    fault = get_settings().drive_fault
    if fault == "timeout":
        raise DriveError("DRIVE_TIMEOUT", "Drive did not answer in time", True)
    if fault == "denied":
        raise DriveError("DRIVE_DENIED", "Drive refused access to the archive folder", False)


def local_provider(f: DocumentFile, content: bytes) -> str:
    """Write once under drive_local_root/<company>/<folder>/<filename>. The id is derived from
    our row id, so a retry after a crash returns the same id and never writes a second copy."""
    root = Path(get_settings().drive_local_root).resolve()
    path = (root / str(f.company_id) / f.folder / f.filename).resolve()
    if root not in path.parents:
        raise DriveError("DRIVE_BAD_PATH", "Archive path escapes the archive root", False)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as fh:
            fh.write(content)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != f.sha256:
            raise DriveError(
                "DRIVE_CONFLICT", "A different file already has this name in the archive", False
            ) from None
    except OSError as e:
        # The OS message holds the absolute archive path; keep it in the server log only.
        log.warning("archive write failed", extra={"ctx": {"file_id": str(f.id), "error": str(e)}})
        raise DriveError("DRIVE_IO", "Could not write the archive copy", True) from e
    return f"local-{f.id.hex}"


_DRIVE = "https://www.googleapis.com/drive/v3/files"
_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id"
_FOLDER = "application/vnd.google-apps.folder"


def _gcall(req: urllib.request.Request, timeout: int) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URLs
            return dict(json.loads(r.read() or b"{}"))
    except urllib.error.HTTPError as e:
        transient = e.code in (408, 429) or e.code >= 500
        code = "DRIVE_DENIED" if e.code in (401, 403) else f"DRIVE_HTTP_{e.code}"
        raise DriveError(code, f"Drive answered {e.code}", transient) from e
    except (TimeoutError, urllib.error.URLError) as e:
        log.warning("drive unreachable", extra={"ctx": {"error": str(e)[:300]}})
        raise DriveError("DRIVE_TIMEOUT", "Google Drive could not be reached", True) from e


def google_provider(f: DocumentFile, content: bytes) -> str:
    """Drive v3: find by appProperties pt_doc_id first (stable id across retries), else create
    the folder chain and upload. UNVERIFIED until the office pilot."""
    s = get_settings()
    if not s.drive_credentials_file or not s.drive_root_folder_id:
        raise DriveError("DRIVE_NOT_CONFIGURED", "Google Drive is not configured", False)
    from .ocr import ProviderError, _google_token

    try:
        token = _google_token(s.drive_credentials_file, s.drive_timeout_seconds)
    except ProviderError as e:
        raise DriveError("DRIVE_NOT_CONFIGURED", e.detail, False) from e
    except (TimeoutError, urllib.error.URLError) as e:
        raise DriveError("DRIVE_TIMEOUT", "Drive sign-in timed out", True) from e
    auth = {"Authorization": f"Bearer {token}"}
    t = s.drive_timeout_seconds

    def find(q: str) -> str | None:
        url = f"{_DRIVE}?" + urllib.parse.urlencode(
            {
                "q": q,
                "fields": "files(id)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
        )
        req = urllib.request.Request(url, headers=auth)  # noqa: S310 - fixed https URL
        files = _gcall(req, t).get("files") or []
        return str(files[0]["id"]) if files else None

    def esc(v: str) -> str:
        return v.replace("\\", "\\\\").replace("'", "\\'")

    found = find(f"appProperties has {{ key='pt_doc_id' and value='{f.id}' }} and trashed=false")
    if found:
        return found
    parent = s.drive_root_folder_id
    for part in [str(f.company_id)[:8], *f.folder.split("/")]:
        q = f"name='{esc(part)}' and '{esc(parent)}' in parents and mimeType='{_FOLDER}'"
        fid = find(q + " and trashed=false")
        if fid is None:
            body = json.dumps({"name": part, "mimeType": _FOLDER, "parents": [parent]}).encode()
            req = urllib.request.Request(  # noqa: S310 - fixed https URL
                f"{_DRIVE}?supportsAllDrives=true&fields=id",
                data=body,
                headers={**auth, "Content-Type": "application/json"},
                method="POST",
            )
            fid = str(_gcall(req, t)["id"])
        parent = fid
    meta = {
        "name": f.filename,
        "parents": [parent],
        "mimeType": f.mime_type,
        "appProperties": {"pt_doc_id": str(f.id), "sha256": f.sha256},
    }
    boundary = f"pt{uuid.uuid4().hex}"
    body = (
        (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta)}\r\n--{boundary}\r\nContent-Type: {f.mime_type}\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(  # noqa: S310 - fixed https URL
        _UPLOAD + "&supportsAllDrives=true",
        data=body,
        headers={**auth, "Content-Type": f"multipart/related; boundary={boundary}"},
        method="POST",
    )
    return str(_gcall(req, t)["id"])


def upload(f: DocumentFile, content: bytes) -> str:
    _fault()
    provider = get_settings().drive_provider
    if provider == "google":
        return google_provider(f, content)
    if provider == "local":
        return local_provider(f, content)
    raise DriveError("DRIVE_DISABLED", "Drive copying is turned off", False)


# ------------------------------------------------------------------ outbox handler


def run_sync(db: Session, f: DocumentFile, attempt: int, final_attempt: int) -> None:
    """Copy one file. Transient failures re-raise so the outbox retries; the last attempt or a
    permanent failure marks FAILED (manual retry). The business records are never touched."""
    if f.sync_status != "PENDING":
        return  # already synced, or superseded by a manual retry event
    f.sync_attempts += 1
    try:
        drive_id = upload(f, content_of(db, f))
    except DriveError as e:
        f.last_error = str(e)[:300]
        f.version += 1
        if e.transient and attempt < final_attempt:
            raise
        f.sync_status = "FAILED"
        audit.record(
            db,
            action="document.sync_failed",
            success=False,
            company_id=f.company_id,
            target_type="document_file",
            target_id=str(f.id),
            warehouse_id=f.warehouse_id,
            detail={"code": e.code, "attempts": f.sync_attempts},
        )
        return
    if f.drive_file_id not in (None, drive_id):
        f.sync_status, f.last_error = "FAILED", "Drive returned a different id for this file"
        f.version += 1
        return
    f.drive_file_id = drive_id
    f.sync_status = "SYNCED"
    f.synced_at = now()
    f.last_error = None
    f.version += 1


def handle_sync(ev: OutboxEvent) -> None:
    db = object_session(ev)
    assert db is not None
    f = db.get(DocumentFile, uuid.UUID(ev.payload["document_file_id"]), with_for_update=True)
    if f is None:
        raise LookupError("document file not found")
    run_sync(db, f, ev.attempts, outbox.MAX_ATTEMPTS)


# ------------------------------------------------------------------ read model


def file_out(db: Session, f: DocumentFile) -> dict[str, Any]:
    names: dict[str, Any] = {}
    if f.invoice_id:
        inv = db.get(Invoice, f.invoice_id)
        names["invoice_no"] = inv.invoice_no if inv else None
    if f.shipment_id:
        sh = db.get(Shipment, f.shipment_id)
        names["shipment_no"] = sh.shipment_no if sh else None
    if f.scan_job_id:
        j = db.get(ScanJob, f.scan_job_id)
        names["scan_job_no"] = j.job_no if j else None
    if f.customer_id:
        c = db.get(Customer, f.customer_id)
        names["customer_name"] = c.name if c else None
    if f.vendor_id:
        v = db.get(Vendor, f.vendor_id)
        names["vendor_name"] = v.name if v else None
    return {
        "id": str(f.id),
        "warehouse_id": str(f.warehouse_id),
        "kind": f.kind,
        "folder": f.folder,
        "filename": f.filename,
        "path": f"{f.folder}/{f.filename}",
        "mime_type": f.mime_type,
        "sha256": f.sha256,
        "byte_size": f.byte_size,
        "sync_status": f.sync_status,
        "drive_file_id": f.drive_file_id,
        "sync_attempts": f.sync_attempts,
        "last_error": f.last_error,
        "synced_at": f.synced_at.isoformat() if f.synced_at else None,
        "note": f.note,
        "invoice_id": str(f.invoice_id) if f.invoice_id else None,
        "shipment_id": str(f.shipment_id) if f.shipment_id else None,
        "scan_job_id": str(f.scan_job_id) if f.scan_job_id else None,
        "invoice_no": names.get("invoice_no"),
        "shipment_no": names.get("shipment_no"),
        "scan_job_no": names.get("scan_job_no"),
        "customer_name": names.get("customer_name"),
        "vendor_name": names.get("vendor_name"),
        "created_at": f.created_at.isoformat(),
        "version": f.version,
    }


def status_counts(db: Session, company_id: uuid.UUID, warehouses: set[uuid.UUID]) -> dict[str, int]:
    rows = db.execute(
        select(DocumentFile.sync_status, func.count())
        .where(DocumentFile.company_id == company_id, DocumentFile.warehouse_id.in_(warehouses))
        .group_by(DocumentFile.sync_status)
    ).all()
    out = {s: 0 for s in ("PENDING", "SYNCED", "FAILED", "LOCAL_ONLY")}
    out.update({s: int(n) for s, n in rows})
    return out
