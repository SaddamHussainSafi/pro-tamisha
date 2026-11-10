"""Module 06 scanner service: device pairing, signed scan capabilities and document intake.

Trust model (docs/architecture/04-security-and-scanner-bridge.md):
- The browser never holds a long-lived scanner secret. It receives a short-lived capability that
  is HMAC-signed by the server, bound to one job and one device, and usable for a single claim.
- The bridge authenticates as a paired device (random secret; only its SHA-256 is stored) and
  must present the capability to claim the job. A website that reaches the bridge on localhost
  cannot mint a capability, so it cannot make the scanner run.
- Uploaded bytes are content-sniffed, size-limited and deduplicated by SHA-256 per company:
  the same file never creates a second source document.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from . import audit, ratelimit
from .config import get_settings
from .errors import ApiError
from .models import Company, ScanDevice, ScanJob, SourceDocument

ONLINE_WINDOW = timedelta(seconds=90)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)
_EXT = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/tiff": ".tif"}


def now() -> datetime:
    return datetime.now(UTC)


def sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ------------------------------------------------------------------ file checks


def sniff(data: bytes) -> str | None:
    """Real file type from magic bytes (never trust the declared Content-Type or extension)."""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    return None


def safe_filename(name: str, mime: str) -> str:
    """Display-only name: basename, printable, bounded. Storage never uses it as a path."""
    base = re.split(r"[\\/]", name)[-1]
    base = re.sub(r"[^\w .()\-]", "_", base).strip(" .")[:120]
    if not base:
        base = "scan"
    if (
        not base.lower().endswith(_EXT[mime])
        and not (mime == "image/jpeg" and base.lower().endswith(".jpeg"))
        and not (mime == "image/tiff" and base.lower().endswith(".tiff"))
    ):
        base += _EXT[mime]
    return base


def check_upload(data: bytes, declared: str | None) -> str:
    """Validate bytes; return the sniffed MIME type."""
    s = get_settings()
    if not data:
        raise ApiError(422, "EMPTY_FILE", "The file is empty")
    if len(data) > s.scan_max_bytes:
        raise ApiError(413, "FILE_TOO_LARGE", "The file is too large", max_bytes=s.scan_max_bytes)
    mime = sniff(data)
    if mime is None:
        raise ApiError(415, "UNSUPPORTED_FILE", "Only PDF, PNG, JPEG or TIFF files can be uploaded")
    decl = (declared or "").split(";")[0].strip().lower()
    if decl == "image/jpg":
        decl = "image/jpeg"
    if decl not in ("", "application/octet-stream", mime):
        raise ApiError(
            415,
            "FILE_TYPE_MISMATCH",
            "The file contents do not match its type",
            declared=decl,
            detected=mime,
        )
    return mime


def store_document(
    db: Session,
    company_id: uuid.UUID,
    data: bytes,
    mime: str,
    filename: str,
    actor_id: uuid.UUID | None,
    via: str,
) -> tuple[SourceDocument, bool]:
    """Insert-or-get by (company, sha256). Returns (document, already_existed)."""
    digest = sha256hex(data)
    # Serialize concurrent uploads of the same bytes so both see the same row.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"doc:{company_id}:{digest}"},
    )
    doc = db.scalar(
        select(SourceDocument).where(
            SourceDocument.company_id == company_id, SourceDocument.sha256 == digest
        )
    )
    if doc is not None:
        return doc, True
    doc = SourceDocument(
        company_id=company_id,
        sha256=digest,
        mime_type=mime,
        byte_size=len(data),
        original_filename=safe_filename(filename, mime),
        content=data,
        uploaded_by=actor_id,
        uploaded_via=via,
    )
    db.add(doc)
    db.flush()
    return doc, False


# ------------------------------------------------------------------ jobs


def next_job_no(db: Session, company_id: uuid.UUID) -> str:
    db.get(Company, company_id, with_for_update={"key_share": True})  # serialize numbering
    from .trash import next_number  # late: trash imports the module graph scanning sits in

    return f"SCN-{now():%Y%m%d}-{next_number(db, ScanJob.job_no, company_id, 'scan'):04d}"


def sweep(db: Session, company_id: uuid.UUID) -> int:
    """Expire unclaimed capabilities and time out stuck scans. Idempotent; safe on every read."""
    t = now()
    s = get_settings()
    r1 = db.execute(
        update(ScanJob)
        .where(
            ScanJob.company_id == company_id,
            ScanJob.status == "REQUESTED",
            ScanJob.capability_expires_at < t,
        )
        .values(status="EXPIRED", completed_at=t, version=ScanJob.version + 1)
    )
    r2 = db.execute(
        update(ScanJob)
        .where(
            ScanJob.company_id == company_id,
            ScanJob.status == "SCANNING",
            ScanJob.claimed_at < t - timedelta(seconds=s.scan_timeout_seconds),
        )
        .values(
            status="FAILED",
            error_code="SCAN_TIMEOUT",
            error_detail="The scanner did not return a document in time",
            completed_at=t,
            version=ScanJob.version + 1,
        )
    )
    return int(getattr(r1, "rowcount", 0) or 0) + int(getattr(r2, "rowcount", 0) or 0)


# ------------------------------------------------------------------ capabilities


def _key() -> bytes:
    s = get_settings()
    if s.is_production_like and s.scan_capability_key.startswith("dev-only"):
        raise ApiError(
            503, "SCANNER_NOT_CONFIGURED", "Scanner signing key is not configured on this server"
        )
    return s.scan_capability_key.encode()


def issue_capability(job_id: uuid.UUID, device_id: uuid.UUID) -> tuple[str, str, datetime]:
    """Returns (token, nonce_hash, expires_at). Only the nonce hash is stored."""
    ttl = get_settings().scan_capability_ttl_seconds
    exp = int(time.time()) + ttl
    nonce = secrets.token_urlsafe(16)
    body = json.dumps(
        {"a": "scan", "d": str(device_id), "e": exp, "j": str(job_id), "n": nonce},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    sig = hmac.new(_key(), body, hashlib.sha256).digest()
    token = f"{_b64(body)}.{_b64(sig)}"
    return token, sha256hex(nonce.encode()), datetime.fromtimestamp(exp, UTC)


class CapabilityError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def verify_capability(token: str) -> dict[str, Any]:
    try:
        body_s, sig_s = token.split(".")
        body, sig = _unb64(body_s), _unb64(sig_s)
    except ValueError:
        raise CapabilityError("CAPABILITY_INVALID") from None
    if not hmac.compare_digest(hmac.new(_key(), body, hashlib.sha256).digest(), sig):
        raise CapabilityError("CAPABILITY_INVALID")
    try:
        data: dict[str, Any] = json.loads(body)
        uuid.UUID(data["j"])
        uuid.UUID(data["d"])
        exp = int(data["e"])
    except (ValueError, KeyError, TypeError):
        raise CapabilityError("CAPABILITY_INVALID") from None
    if data.get("a") != "scan":
        raise CapabilityError("CAPABILITY_INVALID")
    if exp < time.time():
        raise CapabilityError("CAPABILITY_EXPIRED")
    return data


# ------------------------------------------------------------------ devices


def pairing_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def code_hash(code: str) -> str:
    norm = re.sub(r"[^A-Z0-9]", "", code.upper())
    return sha256hex(f"pair:{norm}".encode())


def secret_hash(secret: str) -> str:
    return sha256hex(f"device:{secret}".encode())


def fingerprint(secret: str) -> str:
    h = sha256hex(f"fingerprint:{secret}".encode())[:8].upper()
    return f"{h[:4]}-{h[4:]}"


def norm_fingerprint(fp: str) -> str:
    f = re.sub(r"[^0-9A-F]", "", fp.upper())
    return f"{f[:4]}-{f[4:]}"


def is_online(d: ScanDevice) -> bool:
    return (
        d.status == "ACTIVE"
        and d.last_seen_at is not None
        and (now() - d.last_seen_at) < ONLINE_WINDOW
    )


def _security_event(request: Request, action: str, detail: dict[str, Any]) -> None:
    audit.record_independent(
        action=action,
        success=False,
        request_id=getattr(request.state, "request_id", None),
        target_type="scan_bridge",
        target_id=request.url.path,
        detail=detail,
    )


def device_auth(
    db: Session, request: Request, *, allow_pending: bool = False, lock: bool = False
) -> ScanDevice:
    """Authorization: Device <device_id>.<secret>. Failures are audited as security events.

    Failures are also counted per client IP; past LOGIN_IP_MAX_FAILURES in the window the
    endpoint answers 429 without touching the database, so it can't be used to flood the audit
    log or guess secrets."""
    s = get_settings()
    ip = request.client.host if request.client else "unknown"
    if ratelimit.attempt("bridge_ip", ip, s.login_ip_window_minutes * 60) > s.login_ip_max_failures:
        raise ApiError(429, "TOO_MANY_ATTEMPTS", "Too many failed scanner sign-ins; wait a while")
    try:
        d = _device_auth(db, request, allow_pending=allow_pending, lock=lock)
    except ApiError as e:
        # Only guesses count: a revoked or not-yet-approved scanner proved its secret (it keeps
        # heartbeating and must not lock out the other scanners behind the same address).
        if e.code != "DEVICE_AUTH_FAILED":
            ratelimit.undo("bridge_ip", ip)
        raise
    ratelimit.undo("bridge_ip", ip)
    return d


def _device_auth(db: Session, request: Request, *, allow_pending: bool, lock: bool) -> ScanDevice:
    raw = request.headers.get("authorization", "")
    fail = ApiError(401, "DEVICE_AUTH_FAILED", "Scanner bridge is not paired or was revoked")
    if not raw.startswith("Device "):
        _security_event(request, "scan.bridge.auth_failed", {"reason": "missing"})
        raise fail
    try:
        dev_id_s, secret = raw[len("Device ") :].split(".", 1)
        dev_id = uuid.UUID(dev_id_s)
    except ValueError:
        _security_event(request, "scan.bridge.auth_failed", {"reason": "malformed"})
        raise fail from None
    d = db.get(ScanDevice, dev_id, with_for_update=lock, populate_existing=lock)
    ok = (
        d is not None
        and d.secret_hash is not None
        and hmac.compare_digest(d.secret_hash, secret_hash(secret))
    )
    if not ok or d is None:
        _security_event(
            request, "scan.bridge.auth_failed", {"reason": "secret", "device": dev_id_s}
        )
        raise fail
    if d.status == "REVOKED" or (d.status == "PENDING" and not allow_pending):
        _security_event(
            request, "scan.bridge.auth_failed", {"reason": d.status.lower(), "device": dev_id_s}
        )
        raise ApiError(
            401 if d.status == "REVOKED" else 403,
            "DEVICE_REVOKED" if d.status == "REVOKED" else "DEVICE_NOT_APPROVED",
            "Scanner bridge was revoked"
            if d.status == "REVOKED"
            else "Confirm this scanner's fingerprint in Pro Trading first",
        )
    return d


def capability_rejected(request: Request, code: str, detail: dict[str, Any]) -> ApiError:
    _security_event(request, "scan.capability.rejected", {"code": code, **detail})
    msg = {
        "CAPABILITY_INVALID": "This scan request is not valid",
        "CAPABILITY_EXPIRED": "This scan request has expired; start a new scan",
        "CAPABILITY_USED": "This scan request was already used",
    }.get(code, "This scan request is not valid")
    return ApiError(403, code, msg)


# Decompression-bomb guards. A few hundred KB of PNG or PDF can describe a page that decodes to
# gigabytes; anything larger than this (about A2 at 300 dpi) is refused before it is decoded.
MAX_IMAGE_PIXELS = 40_000_000
MAX_OCR_PAGES = 20


def image_too_large(width: float, height: float) -> bool:
    return width * height > MAX_IMAGE_PIXELS


def pdf_info(src: Any, first: int, last: int) -> tuple[int, list[tuple[float, float]]]:
    """(page count, [(width, height) in points] of pages first..min(last, count)) from
    poppler's pdfinfo. (0, []) when pdfinfo fails, times out or is missing: callers must then
    refuse the file. Sizes are the CropBox; renderers run with -cropbox so they draw exactly
    that."""
    count = _pdfinfo_pages(src)
    if count == 0 or first > count:
        return count, []
    out = _pdfinfo(src, "-f", str(first), "-l", str(min(last, count)))
    if out is None:
        return 0, []
    # pdfinfo prints big sizes in exponent form ("1e+06"), so accept that too
    sizes = re.findall(rb"Page\s+\d+\s+size:\s+([\d.eE+]+) x ([\d.eE+]+)", out)
    try:
        return count, [(float(w), float(h)) for w, h in sizes]
    except ValueError:
        return 0, []


def _pdfinfo_pages(src: Any) -> int:
    out = _pdfinfo(src)
    m = re.search(rb"Pages:\s+(\d+)", out or b"")
    return int(m.group(1)) if m else 0


def _pdfinfo(src: Any, *args: str) -> bytes | None:
    import subprocess

    try:
        r = subprocess.run(  # noqa: S603 - fixed poppler tool, list args
            ["pdfinfo", *args, str(src)],  # noqa: S607
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def pdf_page_too_large(sizes: list[tuple[float, float]], dpi: int, expected: int) -> bool:
    """True when any page is too large, or when not every expected page size could be read."""
    if len(sizes) != expected:
        return True
    return any(image_too_large(w / 72 * dpi, h / 72 * dpi) for w, h in sizes)


def render_page(content: bytes, mime: str, page: int) -> tuple[bytes, int]:
    """(PNG bytes of one page, page count). Raises 404-style ApiError for a missing page."""
    import io
    import subprocess
    import tempfile
    from pathlib import Path

    from PIL import Image, ImageOps

    if mime != "application/pdf":
        if page != 1:
            raise ApiError(404, "NOT_FOUND", "No such page")
        try:
            with Image.open(io.BytesIO(content)) as im:
                if image_too_large(*im.size):  # header only; nothing decoded yet
                    raise ApiError(422, "IMAGE_TOO_LARGE", "The image is too large to show")
                out = io.BytesIO()
                ImageOps.exif_transpose(im).convert("RGB").save(out, format="PNG")
        except (OSError, Image.DecompressionBombError):
            raise ApiError(422, "UNREADABLE", "The image could not be opened") from None
        return out.getvalue(), 1
    with tempfile.TemporaryDirectory(prefix="ptpage-") as tmp:
        src = Path(tmp) / "d.pdf"
        src.write_bytes(content)
        count, sizes = pdf_info(src, page, page)
        if count == 0:
            raise ApiError(422, "UNREADABLE", "The PDF could not be opened")
        if page > count:
            raise ApiError(404, "NOT_FOUND", "No such page")
        if pdf_page_too_large(sizes, 110, 1):
            raise ApiError(422, "IMAGE_TOO_LARGE", "This page is too large to show")
        subprocess.run(  # noqa: S603
            ["pdftoppm", "-cropbox", "-f", str(page), "-l", str(page), "-r", "110", "-png",  # noqa: S607
             "-singlefile", str(src), str(Path(tmp) / "p")],
            capture_output=True, timeout=60, check=False,
        )  # fmt: skip
        out_png = Path(tmp) / "p.png"
        if not out_png.exists():
            raise ApiError(422, "UNREADABLE", "The PDF page could not be drawn")
        return out_png.read_bytes(), count
