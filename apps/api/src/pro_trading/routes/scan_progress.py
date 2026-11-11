"""Scan progress for the Scan Center's animated scanner: one snapshot, or a live SSE stream.

Who may look: anyone who may see the scan (scan.create or scan.review in its location). Words
read from the pages (`sample`) and the reading's warnings are shown to scan.review only, like the
reading itself. Opening progress also runs the two idempotent timeout checks (a bridge scan the
scanner never finished, a reading no worker picked up), the way opening a scan already does.
"""

import asyncio
import threading
import time
import uuid
import weakref
from collections import Counter
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

import redis
import redis.asyncio as aioredis
from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import scan_progress as prog
from .. import scanning
from ..config import get_settings
from ..db import get_sessionmaker
from ..deps import DbDep, Principal, PrincipalDep
from ..errors import ApiError
from ..models import ScanJob
from .notifications import _session_ok
from .scans import get_job

router = APIRouter(tags=["scans"])

MAX_STREAMS_PER_EMPLOYEE = 6
POLL_SECONDS = 1.0  # statuses change on commit (no wake-up for those), so look every second
POLL_SECONDS_NO_REDIS = 1.0

StepKey = Literal["RETRIEVING", "UPLOADING", "EXTRACTING", "MATCHING", "REVIEW"]
Outcome = Literal["WORKING", "READY", "DONE", "FAILED", "TIMEOUT", "MISSING", "CANCELLED", "UNREAD"]


class ProgressStep(BaseModel):
    key: StepKey
    state: Literal["waiting", "active", "done", "failed", "skipped"]


class ProgressPage(BaseModel):
    page: int
    state: Literal["WAITING", "READING", "READ", "FAILED", "SKIPPED"]
    words: int | None = None
    confidence: str | None = None  # median engine confidence for the page, "0.000".."1.000"
    sample: str | None = None  # first words read (scan.review only)
    lines: int | None = None  # lines found on this page (once the reading is finished)
    to_check: int | None = None  # of those, still to check (unclear quantity / unknown product)


class ProgressEvent(BaseModel):
    seq: int
    kind: str
    attempt: int | None
    page: int | None
    page_count: int | None
    at: str
    detail: dict[str, Any]


class ProgressRetry(BaseModel):
    code: str | None
    attempt: int
    max_attempts: int | None


class ProgressReading(BaseModel):
    id: str
    run_no: int
    status: str
    provider: str
    page_count: int | None
    error_code: str | None
    error_detail: str | None
    lines: int | None
    to_check: int | None
    unknown_products: int | None
    unclear_quantities: int | None
    warnings: list[str]
    created_at: str
    completed_at: str | None


class ScanProgressOut(BaseModel):
    job_id: str
    job_no: str
    job_type: str
    channel: str
    status: str
    duplicate: bool
    error_code: str | None
    error_detail: str | None
    document_id: str | None
    document_mime: str | None
    document_filename: str | None
    page_count: int | None
    outcome: Outcome
    step: StepKey
    steps: list[ProgressStep]
    pages: list[ProgressPage]
    pages_read: int
    attempt: int | None
    retrying: ProgressRetry | None
    reading: ProgressReading | None
    events: list[ProgressEvent]
    seq: int
    can_read: bool
    can_retry: bool
    can_review: bool
    version: str


def _sweep(db: Session, job: ScanJob) -> None:
    changed = 0
    if job.status in ("REQUESTED", "SCANNING"):
        changed += scanning.sweep(db, job.company_id)
    if job.status == "UPLOADED":
        changed += prog.sweep_stalled(db, job.company_id, job.id)
    if changed:
        db.commit()
        db.refresh(job)
    else:
        db.rollback()  # release the skip-locked probes at once


def _snap(db: Session, p: Principal, job: ScanJob) -> dict[str, Any]:
    _sweep(db, job)
    return prog.snapshot(db, job, can_review=p.can("scan.review", job.warehouse_id))


@router.get("/scans/jobs/{job_id}/progress", response_model=ScanProgressOut)
def read_progress(job_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep) -> Any:
    """Where this scan is: the five steps, every page, the reading's counts and what can be done
    next. Built from committed rows only; nothing is estimated."""
    return _snap(db, p, get_job(db, request, p, job_id))


_streams: Counter[uuid.UUID] = Counter()
_streams_lock = threading.Lock()


def _once(emp_id: uuid.UUID) -> Callable[[], None]:
    done = threading.Event()

    def release() -> None:
        if not done.is_set():
            done.set()
            with _streams_lock:
                _streams[emp_id] -= 1
                if _streams[emp_id] <= 0:
                    del _streams[emp_id]

    return release


def _fetch(
    session_id: uuid.UUID, employee_id: uuid.UUID, job_id: uuid.UUID, can_review: bool
) -> dict[str, Any] | None:
    """A fresh snapshot in its own session; None when the sign-in ended or the scan is gone."""
    with get_sessionmaker()() as s:
        if not _session_ok(s, session_id, employee_id):
            return None
        job = s.get(ScanJob, job_id)
        if job is None:
            return None
        _sweep(s, job)
        return prog.snapshot(s, job, can_review=can_review)


def _sse(event: str, data: Any, eid: int | None = None) -> str:
    head = f"id: {eid}\n" if eid is not None else ""
    return f"{head}event: {event}\ndata: {prog.fingerprint(data)}\n\n"


async def _subscribe(job_id: uuid.UUID) -> tuple[Any, Any] | None:
    try:
        client = aioredis.Redis.from_url(get_settings().redis_url, socket_timeout=5)
        ps = client.pubsub()
        await ps.subscribe(prog.channel(job_id))
        return client, ps
    except (redis.RedisError, OSError):
        return None


async def _wait(sub: tuple[Any, Any] | None, seconds: float) -> None:
    if seconds <= 0:
        return
    if sub is None:
        await asyncio.sleep(seconds)
        return
    end = time.monotonic() + seconds
    try:
        while (left := end - time.monotonic()) > 0:
            msg = await sub[1].get_message(ignore_subscribe_messages=True, timeout=left)
            if msg is not None:
                return
    except (redis.RedisError, OSError):
        await asyncio.sleep(max(0.0, end - time.monotonic()))


async def _close(sub: tuple[Any, Any] | None) -> None:
    if sub is None:
        return
    try:
        await sub[1].aclose()
        await sub[0].aclose()
    except (redis.RedisError, OSError, RuntimeError):
        pass


@router.get("/scans/jobs/{job_id}/progress/stream", response_class=StreamingResponse)
def stream_progress(
    job_id: uuid.UUID,
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    timeout: int = Query(55, ge=1, le=300, description="Seconds before the stream closes"),
) -> StreamingResponse:
    """Server-Sent Events: `progress` (a ScanProgressOut, sent at once and again whenever it
    changes) and `end` when the scan reached a resting state (ready for review, finished,
    failed, timed out, missing, cancelled or not read). The browser reconnects after `timeout`."""
    job = get_job(db, request, p, job_id)
    can_review = p.can("scan.review", job.warehouse_id)
    emp_id, session_id = p.employee.id, p.session.id
    with _streams_lock:
        if _streams[emp_id] >= MAX_STREAMS_PER_EMPLOYEE:
            raise ApiError(429, "TOO_MANY_STREAMS", "Too many live scan views; close a tab")
        _streams[emp_id] += 1
    release = _once(emp_id)
    try:
        first = _snap(db, p, job)
    except Exception:
        release()
        raise
    db.close()

    async def gen() -> AsyncIterator[str]:
        sub = None
        try:
            yield "retry: 3000\n\n"
            last = prog.fingerprint(first)
            yield _sse("progress", first, first["seq"])
            if first["outcome"] in prog.TERMINAL:
                yield _sse("end", {"outcome": first["outcome"]})
                return
            sub = await _subscribe(job_id)
            poll = POLL_SECONDS if sub is not None else POLL_SECONDS_NO_REDIS
            end = time.monotonic() + timeout
            while (left := end - time.monotonic()) > 0:
                await _wait(sub, min(poll, left))
                snap = await run_in_threadpool(_fetch, session_id, emp_id, job_id, can_review)
                if snap is None:
                    yield _sse("end", {"outcome": "MISSING"})
                    return
                fp = prog.fingerprint(snap)
                if fp != last:
                    last = fp
                    yield _sse("progress", snap, snap["seq"])
                    if snap["outcome"] in prog.TERMINAL:
                        yield _sse("end", {"outcome": snap["outcome"]})
                        return
                else:
                    yield ": keep-alive\n\n"
        finally:
            await _close(sub)
            release()

    gen_obj = gen()
    weakref.finalize(gen_obj, release)
    return StreamingResponse(
        gen_obj,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
