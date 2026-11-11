"""Live progress of a scan, from paper to review (the Scan Center's animated scanner).

The five steps the operator sees, and what each one is made of:

  RETRIEVING  the document is on its way in. A bridge scan waits for the scanner (REQUESTED) and
              then feeds pages (SCANNING). A file upload has nothing to fetch: the browser holds
              the file already, so the step is done.
  UPLOADING   the bytes reach the server. The browser measures its own upload; a bridge scan
              uploads as it finishes. Done once the scan is UPLOADED.
  EXTRACTING  the OCR engine reads the pages. The worker reports OCR_STARTED, then PAGE_READ for
              page N of M as each page is read.
  MATCHING    read rows are matched to products: MATCHING, then MATCHED with the counts.
  REVIEW      the reading is NEEDS_REVIEW: a person checks every line before anything is created.

Everything shown comes from committed rows: the scan's status, the reading's status and the
progress events (`scan_events`). The worker writes each event in its own short transaction, so
page 3 of 10 is visible while the reading itself is still open; the statuses stay the truth.
Nothing is estimated from elapsed time. The one time-based rule is the stall check: a reading
still QUEUED that no worker holds, with nothing reported for OCR_STALL_SECONDS, is stopped as
OCR_TIMEOUT so someone can read it again.

Events are best effort: a write that cannot get its locks within half a second is dropped and
logged, never allowed to slow or fail the reading.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Lock
from typing import Any, Protocol

import redis
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import audit, scanning
from .config import get_settings
from .db import get_sessionmaker
from .models import OcrExtraction, OcrField, OutboxEvent, ScanEvent, ScanJob, SourceDocument

log = logging.getLogger("pro_trading.scan_progress")

STEPS = ("RETRIEVING", "UPLOADING", "EXTRACTING", "MATCHING", "REVIEW")
OUTCOMES = ("WORKING", "READY", "DONE", "FAILED", "TIMEOUT", "MISSING", "CANCELLED", "UNREAD")
TERMINAL = frozenset(OUTCOMES) - {"WORKING"}
SAMPLE_WORDS = 8
SAMPLE_CHARS = 80
EXTRACT_EVENT = "ocr.extract"  # = ocr.EXTRACT_EVENT (ocr imports this module)
MAX_EVENTS = 80


def now() -> datetime:
    return datetime.now(UTC)


def channel(job_id: uuid.UUID) -> str:
    return f"scan-progress:{job_id}"


# ------------------------------------------------------------------ writing events (worker)


class _Tok(Protocol):
    text: str
    confidence: Decimal
    x0: Decimal
    y0: Decimal


@dataclass
class _Run:
    company_id: uuid.UUID
    scan_job_id: uuid.UUID
    extraction_id: uuid.UUID
    attempt: int
    pages_reported: set[int] = field(default_factory=set)


_current: ContextVar[_Run | None] = ContextVar("scan_progress_run", default=None)


def _run_of(ex: OcrExtraction, attempt: int) -> _Run:
    return _Run(ex.company_id, ex.scan_job_id, ex.id, attempt)


@contextmanager
def tracking(ex: OcrExtraction, attempt: int) -> Iterator[None]:
    """While an OCR engine reads `ex`, its per-page reports (page_read) go to this reading."""
    tok = _current.set(_run_of(ex, attempt))
    try:
        yield
    finally:
        _current.reset(tok)


def _write(
    run: _Run, kind: str, page: int | None, pages: int | None, detail: dict[str, Any]
) -> None:
    try:
        with get_sessionmaker()() as s, s.begin():
            # Never wait on someone else's lock for long: progress is not worth a stall.
            s.execute(text("SET LOCAL lock_timeout = '500ms'"))
            s.execute(text("SET LOCAL statement_timeout = '3s'"))
            s.add(
                ScanEvent(
                    company_id=run.company_id,
                    scan_job_id=run.scan_job_id,
                    extraction_id=run.extraction_id,
                    kind=kind,
                    attempt=run.attempt,
                    page=page,
                    page_count=pages,
                    detail=detail,
                )
            )
    except SQLAlchemyError as exc:
        log.warning(
            "scan progress event dropped",
            extra={"ctx": {"kind": kind, "error": type(exc).__name__}},
        )
        return
    _publish(run.scan_job_id)


def _publish(job_id: uuid.UUID) -> None:
    try:
        redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.5).publish(
            channel(job_id), "1"
        )
    except (redis.RedisError, OSError):
        pass  # the live view also polls


def event(
    ex: OcrExtraction,
    kind: str,
    attempt: int,
    *,
    page_count: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """One step of reading `ex` (OCR_STARTED, MATCHING, MATCHED, OCR_RETRY, OCR_FAILED)."""
    _write(_run_of(ex, attempt), kind, None, page_count, detail or {})


def _sample(words: Sequence[str]) -> str:
    out: list[str] = []
    for w in words:
        w = w.strip()
        if len(w) < 2 or not re.search(r"[A-Za-z0-9]", w):
            continue
        out.append(w[:24])
        if len(out) >= SAMPLE_WORDS:
            break
    return " ".join(out)[:SAMPLE_CHARS]


def page_read(page: int, pages: int, tokens: Sequence[_Tok]) -> None:
    """The engine finished `page` of `pages`. Called by OCR providers that read page by page;
    a no-op outside a reading (tests calling a provider directly, the review tools)."""
    run = _current.get()
    if run is None or page in run.pages_reported:
        return
    run.pages_reported.add(page)
    ordered = sorted(tokens, key=lambda t: (t.y0, t.x0))
    confs = sorted(t.confidence for t in ordered)
    detail: dict[str, Any] = {"words": len(ordered)}
    if confs:
        detail["confidence"] = str(confs[len(confs) // 2])  # median, as the engine reported it
    sample = _sample([t.text for t in ordered])
    if sample:
        detail["sample"] = sample
    _write(run, "PAGE_READ", page, pages, detail)


class _Paged(Protocol):
    page: int
    text: str
    confidence: Decimal
    x0: Decimal
    y0: Decimal


def pages_done(pages: int, tokens: Sequence[_Paged]) -> None:
    """After the engine answered: report every page it did not report while reading (engines that
    return the whole document at once, like Document AI)."""
    run = _current.get()
    if run is None:
        return
    for n in range(1, pages + 1):
        if n not in run.pages_reported:
            page_read(n, pages, [t for t in tokens if t.page == n])


# ------------------------------------------------------------------ stall check


def sweep_stalled(db: Session, company_id: uuid.UUID, job_id: uuid.UUID | None = None) -> int:
    """Stop readings nobody is working on: QUEUED, not locked by a worker (the worker holds the
    reading and its outbox event while it reads), and nothing reported for OCR_STALL_SECONDS.
    They become FAILED / OCR_TIMEOUT; reading again starts a fresh run. Idempotent."""
    s = get_settings()
    t = now()
    cutoff = t - timedelta(seconds=s.ocr_stall_seconds)
    q = select(OcrExtraction).where(
        OcrExtraction.company_id == company_id,
        OcrExtraction.status == "QUEUED",
        OcrExtraction.created_at < cutoff,
    )
    if job_id is not None:
        q = q.where(OcrExtraction.scan_job_id == job_id)
    n = 0
    for ex in db.scalars(q.with_for_update(skip_locked=True)).all():
        last = db.scalar(
            select(func.max(ScanEvent.created_at)).where(ScanEvent.extraction_id == ex.id)
        )
        if last is not None and last >= cutoff:
            continue
        pending = db.scalars(
            select(OutboxEvent.id).where(
                OutboxEvent.aggregate_id == str(ex.id),
                OutboxEvent.event_type == EXTRACT_EVENT,
                OutboxEvent.status == "PENDING",
            )
        ).all()
        if pending:
            free = db.scalars(
                select(OutboxEvent.id)
                .where(OutboxEvent.id.in_(pending))
                .with_for_update(skip_locked=True)
            ).all()
            if len(free) < len(pending):
                continue  # a worker has it in hand right now
        ex.status, ex.error_code = "FAILED", "OCR_TIMEOUT"
        ex.error_detail = f"No reader finished this scan within {s.ocr_stall_seconds} seconds"
        ex.completed_at = t
        ex.version += 1
        db.add(
            ScanEvent(
                company_id=ex.company_id,
                scan_job_id=ex.scan_job_id,
                extraction_id=ex.id,
                kind="OCR_TIMEOUT",
                detail={"after_seconds": s.ocr_stall_seconds},
            )
        )
        audit.record(
            db,
            action="ocr.extract",
            success=False,
            company_id=ex.company_id,
            target_type="ocr_extraction",
            target_id=str(ex.id),
            warehouse_id=ex.warehouse_id,
            detail={"reason": "OCR_TIMEOUT", "after_seconds": s.ocr_stall_seconds},
        )
        n += 1
    if n:
        db.flush()
    return n


# ------------------------------------------------------------------ reading progress (API)

_PAGES: dict[uuid.UUID, int | None] = {}
_PAGES_LOCK = Lock()


def document_pages(db: Session, document_id: uuid.UUID | None) -> int | None:
    """Pages in a stored document (documents never change, so the answer is kept in memory)."""
    if document_id is None:
        return None
    with _PAGES_LOCK:
        if document_id in _PAGES:
            return _PAGES[document_id]
    doc = db.get(SourceDocument, document_id)
    n = scanning.page_count(doc.content, doc.mime_type) if doc is not None else None
    with _PAGES_LOCK:
        _PAGES[document_id] = n
        while len(_PAGES) > 512:
            _PAGES.pop(next(iter(_PAGES)))
    return n


def _outcome(job: ScanJob, ex: OcrExtraction | None) -> str:
    if job.status == "CANCELLED":
        return "CANCELLED"
    if job.status == "EXPIRED":
        return "TIMEOUT"
    if job.status == "FAILED":
        if job.error_code == "SCAN_TIMEOUT":
            return "TIMEOUT"
        return "MISSING" if job.error_code == "NO_PAGES" else "FAILED"
    if job.status in ("REQUESTED", "SCANNING"):
        return "WORKING"
    if ex is None:
        return "UNREAD"
    return {
        "QUEUED": "WORKING",
        "NEEDS_REVIEW": "READY",
        "ACCEPTED": "DONE",
        "REJECTED": "DONE",
        "FAILED": "TIMEOUT" if ex.error_code == "OCR_TIMEOUT" else "FAILED",
    }.get(ex.status, "WORKING")


def _steps(
    job: ScanJob, ex: OcrExtraction | None, matching: bool, outcome: str
) -> list[dict[str, str]]:
    st: dict[str, str] = dict.fromkeys(STEPS, "waiting")
    received = job.status == "UPLOADED"
    if received or job.channel == "MANUAL":
        st["RETRIEVING"] = "done"
    elif job.status in ("REQUESTED", "SCANNING"):
        st["RETRIEVING"] = "active"
    else:
        st["RETRIEVING"] = "failed"  # failed, expired or cancelled before a document arrived
    if received:
        st["UPLOADING"] = "done"
    if not received:
        return [{"key": k, "state": st[k]} for k in STEPS]
    if ex is None:
        for k in ("EXTRACTING", "MATCHING", "REVIEW"):
            st[k] = "skipped" if outcome == "UNREAD" else "waiting"
    elif ex.status in ("NEEDS_REVIEW", "ACCEPTED", "REJECTED"):
        st["EXTRACTING"] = st["MATCHING"] = st["REVIEW"] = "done"
    elif ex.status == "QUEUED":
        if matching:
            st["EXTRACTING"], st["MATCHING"] = "done", "active"
        else:
            st["EXTRACTING"] = "active"
    elif ex.status == "FAILED":
        if matching:
            st["EXTRACTING"], st["MATCHING"] = "done", "failed"
        else:
            st["EXTRACTING"] = "failed"
    return [{"key": k, "state": st[k]} for k in STEPS]


def snapshot(db: Session, job: ScanJob, *, can_review: bool) -> dict[str, Any]:
    """Where the scan is right now, from committed rows only (see the module docstring)."""
    ex = db.scalar(
        select(OcrExtraction)
        .where(OcrExtraction.scan_job_id == job.id, OcrExtraction.status != "SUPERSEDED")
        .order_by(OcrExtraction.run_no.desc())
        .limit(1)
    )
    evs: list[ScanEvent] = []
    if ex is not None:
        evs = list(
            db.scalars(
                select(ScanEvent)
                .where(ScanEvent.scan_job_id == job.id, ScanEvent.extraction_id == ex.id)
                .order_by(ScanEvent.id)
            ).all()
        )
    seq = db.scalar(select(func.max(ScanEvent.id)).where(ScanEvent.scan_job_id == job.id)) or 0
    attempt = max((e.attempt or 1 for e in evs if e.kind == "OCR_STARTED"), default=None)
    cur = [e for e in evs if attempt is not None and e.attempt == attempt]
    # between attempts nothing is being read: the attempt ended with OCR_RETRY
    started = any(e.kind == "OCR_STARTED" for e in cur) and not any(
        e.kind == "OCR_RETRY" for e in cur
    )
    matching = any(e.kind in ("MATCHING", "MATCHED") for e in cur)
    outcome = _outcome(job, ex)

    doc_pages = document_pages(db, job.document_id) if job.status == "UPLOADED" else None
    read_pages = {e.page: e for e in cur if e.kind == "PAGE_READ" and e.page is not None}
    engine_pages = next(
        (
            e.page_count
            for e in reversed(cur)
            if e.kind in ("PAGE_READ", "MATCHING") and e.page_count
        ),
        None,
    )
    finished = ex is not None and ex.status in ("NEEDS_REVIEW", "ACCEPTED", "REJECTED")
    total = doc_pages or (ex.page_count if ex else None) or engine_pages or 0
    readable = (
        (ex.page_count if finished and ex and ex.page_count else None) or engine_pages or total
    )

    fields: list[OcrField] = []
    if finished and ex is not None:
        fields = list(db.scalars(select(OcrField).where(OcrField.extraction_id == ex.id)).all())
    per_page: dict[int, list[OcrField]] = {}
    for f in fields:
        pg = (f.bbox or {}).get("page")
        if isinstance(pg, int):
            per_page.setdefault(pg, []).append(f)

    pages: list[dict[str, Any]] = []
    reading_next = True
    for n in range(1, total + 1):
        ev = read_pages.get(n)
        if finished:
            state = "READ" if n <= readable else "SKIPPED"
        elif ev is not None:
            state = "READ"
        elif ex is not None and ex.status == "QUEUED" and started and not matching and reading_next:
            state, reading_next = "READING", False
        elif ex is not None and ex.status == "FAILED" and started and not matching and reading_next:
            state, reading_next = "FAILED", False
        else:
            state = "WAITING"
        d = ev.detail if ev is not None else {}
        mine = per_page.get(n, [])
        pages.append(
            {
                "page": n,
                "state": state,
                "words": d.get("words"),
                "confidence": d.get("confidence"),
                "sample": d.get("sample") if can_review else None,
                "lines": sum(1 for f in mine if f.kind != "MANUAL") if finished else None,
                "to_check": sum(1 for f in mine if f.resolution == "NONE" and f.status != "OK")
                if finished
                else None,
            }
        )

    retry = next((e for e in reversed(evs) if e.kind == "OCR_RETRY"), None)
    retrying = None
    if retry is not None and ex is not None and ex.status == "QUEUED" and retry.attempt == attempt:
        retrying = {
            "code": retry.detail.get("code"),
            "attempt": (retry.attempt or 1),
            "max_attempts": retry.detail.get("max"),
        }

    reading = None
    if ex is not None:
        kept = [f for f in fields if f.kind != "MANUAL"]
        reading = {
            "id": str(ex.id),
            "run_no": ex.run_no,
            "status": ex.status,
            "provider": ex.provider,
            "page_count": ex.page_count,
            "error_code": ex.error_code,
            "error_detail": ex.error_detail,
            "lines": len(kept) if finished else None,
            "to_check": sum(1 for f in fields if f.resolution == "NONE" and f.status != "OK")
            if finished
            else None,
            "unknown_products": sum(1 for f in kept if f.status == "UNMAPPED")
            if finished
            else None,
            "unclear_quantities": sum(
                1 for f in kept if f.status in ("LOW_CONFIDENCE", "UNREADABLE")
            )
            if finished
            else None,
            "warnings": [str(w) for w in (ex.warnings or [])] if can_review else [],
            "created_at": ex.created_at.isoformat(),
            "completed_at": ex.completed_at.isoformat() if ex.completed_at else None,
        }

    shown = [e for e in evs if e.attempt == attempt or e.kind in ("OCR_RETRY", "OCR_TIMEOUT")]
    events = [
        {
            "seq": e.id,
            "kind": e.kind,
            "attempt": e.attempt,
            "page": e.page,
            "page_count": e.page_count,
            "at": e.created_at.isoformat(),
            "detail": {k: v for k, v in e.detail.items() if can_review or k != "sample"},
        }
        for e in shown[-MAX_EVENTS:]
    ]
    steps = _steps(job, ex, matching, outcome)
    step = next((s["key"] for s in steps if s["state"] in ("active", "failed")), None)
    if step is None:
        step = (
            "REVIEW"
            if all(s["state"] == "done" for s in steps)
            else next((s["key"] for s in steps if s["state"] == "waiting"), "REVIEW")
        )
    job_failed = job.status in ("FAILED", "EXPIRED", "CANCELLED")
    doc = (
        db.execute(
            select(SourceDocument.mime_type, SourceDocument.original_filename).where(
                SourceDocument.id == job.document_id
            )
        ).first()
        if job.document_id
        else None
    )
    return {
        "job_id": str(job.id),
        "job_no": job.job_no,
        "job_type": job.job_type,
        "channel": job.channel,
        "status": job.status,
        "duplicate": job.duplicate,
        "error_code": job.error_code,
        "error_detail": job.error_detail,
        "document_id": str(job.document_id) if job.document_id else None,
        "document_mime": doc.mime_type if doc else None,
        "document_filename": doc.original_filename if doc else None,
        "page_count": total or None,
        "outcome": outcome,
        "step": step,
        "steps": steps,
        "pages": pages,
        "pages_read": sum(1 for p in pages if p["state"] == "READ"),
        "attempt": attempt,
        "retrying": retrying,
        "reading": reading,
        "events": events,
        "seq": seq,
        "can_read": can_review and outcome == "UNREAD",
        "can_retry": can_review and not job_failed and ex is not None and ex.status == "FAILED",
        "can_review": can_review and outcome in ("READY", "DONE"),
        "version": f"{job.version}.{ex.version if ex else 0}.{seq}",
    }


def fingerprint(snap: dict[str, Any]) -> str:
    return json.dumps(snap, sort_keys=True, default=str)
