"""Notification Center API: the signed-in employee's own inbox, preferences and live stream.

Every route is self-service: rows are always filtered to the caller's own recipient rows AND to
notifications whose required permission the caller still holds in this session, so nothing
leaks across users, roles, warehouses or companies. Read/archive commands are naturally
idempotent (setting a timestamp that is already set changes nothing).
"""

import asyncio
import json
import threading
import time
import uuid
import weakref
from collections import Counter
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from typing import Annotated, Any, Literal

import redis
import redis.asyncio as aioredis
from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.orm import Session

from .. import idempotency, ratelimit
from ..config import get_settings
from ..db import get_sessionmaker
from ..deps import DbDep, Principal, PrincipalDep
from ..errors import ApiError
from ..models import AuthSession, Employee
from ..notifications import (
    CATEGORIES,
    SEVERITIES,
    Notification,
    NotificationPreference,
    NotificationRecipient,
    alert_for,
    channel,
    company_now,
    item_out,
    mark_changed,
    prefs_of,
    prefs_out,
    visible_to,
)
from ._common import esc, mutate

router = APIRouter(prefix="/notifications", tags=["notifications"])

Grants = tuple[tuple[str, uuid.UUID | None], ...]
WRITE_LIMIT_PER_MINUTE = 240
REPLAY_MAX = 50
MAX_STREAMS_PER_EMPLOYEE = 4
POLL_SECONDS = 10.0  # safety net when a Redis wake-up is missed
POLL_SECONDS_NO_REDIS = 3.0


# ------------------------------------------------------------------ schemas


class NotificationOut(BaseModel):
    id: uuid.UUID
    seq: int
    kind: str
    category: str
    category_label: str
    severity: Literal["info", "success", "warning", "critical"]
    title: str
    body: str
    source_module: str
    link: str | None
    target_type: str | None
    target_id: str | None
    created_at: datetime
    read: bool
    read_at: datetime | None
    archived: bool


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    next_before: int | None = Field(description="Pass as `before` for the next (older) page")
    unread: int


class SummaryOut(BaseModel):
    unread: int
    critical_unread: int
    latest_seq: int


class CountOut(BaseModel):
    updated: int
    unread: int


class ReadAllIn(BaseModel):
    category: str | None = None

    @field_validator("category")
    @classmethod
    def known(cls, v: str | None) -> str | None:
        if v is not None and v not in CATEGORIES:
            raise ValueError("unknown category")
        return v


class ArchiveIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class CategoryPrefIn(BaseModel):
    in_app: bool = True
    email: bool = True
    desktop: bool = True


HHMM = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class PrefsIn(BaseModel):
    version: int = Field(ge=0)
    in_app: bool
    desktop: bool
    email: bool
    sound: bool
    grouped: bool
    quiet_hours: bool
    quiet_start: HHMM
    quiet_end: HHMM
    categories: dict[str, CategoryPrefIn] = Field(default_factory=dict)

    @field_validator("categories")
    @classmethod
    def known(cls, v: dict[str, CategoryPrefIn]) -> dict[str, CategoryPrefIn]:
        bad = sorted(set(v) - set(CATEGORIES))
        if bad:
            raise ValueError(f"unknown categories: {', '.join(bad)}")
        return v


class PrefsOut(BaseModel):
    in_app: bool
    desktop: bool
    email: bool
    sound: bool
    grouped: bool
    quiet_hours: bool
    quiet_start: str
    quiet_end: str
    categories: dict[str, dict[str, bool]]
    category_labels: dict[str, str]
    can_mute_security: bool
    version: int


# ------------------------------------------------------------------ helpers


def grants_of(p: Principal) -> Grants:
    return tuple(sorted({(g.permission, g.warehouse_id) for g in p.effective()}, key=str))


def _mine(employee_id: uuid.UUID, company_id: uuid.UUID, grants: Grants) -> ColumnElement[bool]:
    return and_(
        NotificationRecipient.employee_id == employee_id,
        NotificationRecipient.company_id == company_id,
        Notification.company_id == company_id,
        visible_to(grants),
    )


def _base(employee_id: uuid.UUID, company_id: uuid.UUID, grants: Grants) -> Any:
    return (
        select(NotificationRecipient, Notification)
        .join(Notification, Notification.id == NotificationRecipient.notification_id)
        .where(_mine(employee_id, company_id, grants))
    )


UNREAD = and_(NotificationRecipient.read_at.is_(None), NotificationRecipient.archived_at.is_(None))


def _visible_ids(employee_id: uuid.UUID, company_id: uuid.UUID, grants: Grants) -> Any:
    return (
        select(NotificationRecipient.seq)
        .join(Notification, Notification.id == NotificationRecipient.notification_id)
        .where(_mine(employee_id, company_id, grants))
    )


def summary(
    db: Session, employee_id: uuid.UUID, company_id: uuid.UUID, grants: Grants
) -> dict[str, int]:
    row = db.execute(
        select(
            func.count().filter(UNREAD),
            func.count().filter(and_(UNREAD, Notification.severity == "critical")),
            func.coalesce(func.max(NotificationRecipient.seq), 0),
        )
        .select_from(NotificationRecipient)
        .join(Notification, Notification.id == NotificationRecipient.notification_id)
        .where(_mine(employee_id, company_id, grants))
    ).one()
    return {"unread": int(row[0]), "critical_unread": int(row[1]), "latest_seq": int(row[2])}


def _throttle(p: Principal) -> None:
    if ratelimit.attempt("notify_write", str(p.employee.id), 60) > WRITE_LIMIT_PER_MINUTE:
        raise ApiError(429, "RATE_LIMITED", "Too many changes at once; wait a moment")


def _own(db: Session, p: Principal, nid: uuid.UUID) -> tuple[NotificationRecipient, Notification]:
    row = db.execute(
        _base(p.employee.id, p.company_id, grants_of(p))
        .where(NotificationRecipient.notification_id == nid)
        .with_for_update(of=NotificationRecipient)
    ).first()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Notification not found")
    return row[0], row[1]


def _now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ inbox


@router.get("", response_model=NotificationPage)
def list_notifications(
    db: DbDep,
    p: PrincipalDep,
    status: Literal["unread", "all", "archived"] = "all",
    category: Annotated[list[str] | None, Query()] = None,
    severity: Annotated[list[str] | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> dict[str, Any]:
    """Newest first, keyset-paginated by `seq` (`before` = the previous page's next_before)."""
    if category and not set(category) <= set(CATEGORIES):
        raise ApiError(422, "VALIDATION", "Unknown category")
    if severity and not set(severity) <= set(SEVERITIES):
        raise ApiError(422, "VALIDATION", "Unknown priority")
    grants = grants_of(p)
    stmt = _base(p.employee.id, p.company_id, grants)
    if status == "unread":
        stmt = stmt.where(UNREAD)
    elif status == "all":
        stmt = stmt.where(NotificationRecipient.archived_at.is_(None))
    else:
        stmt = stmt.where(NotificationRecipient.archived_at.is_not(None))
    if category:
        stmt = stmt.where(Notification.category.in_(category))
    if severity:
        stmt = stmt.where(Notification.severity.in_(severity))
    if q and q.strip():
        like = f"%{esc(q.strip())}%"
        stmt = stmt.where(
            or_(
                Notification.title.ilike(like, escape="\\"),
                Notification.body.ilike(like, escape="\\"),
                Notification.source_module.ilike(like, escape="\\"),
            )
        )
    if before is not None:
        stmt = stmt.where(NotificationRecipient.seq < before)
    rows = db.execute(stmt.order_by(NotificationRecipient.seq.desc()).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [item_out(r, n) for r, n in rows],
        "next_before": rows[-1][0].seq if more and rows else None,
        "unread": summary(db, p.employee.id, p.company_id, grants)["unread"],
    }


@router.get("/summary", response_model=SummaryOut)
def get_summary(db: DbDep, p: PrincipalDep) -> dict[str, int]:
    return summary(db, p.employee.id, p.company_id, grants_of(p))


def _set(
    db: Session, p: Principal, nid: uuid.UUID, change: Callable[[NotificationRecipient], bool]
) -> dict[str, Any]:
    _throttle(p)
    r, n = _own(db, p, nid)
    if change(r):
        mark_changed(db, p.employee.id)
    db.commit()
    return item_out(r, n)


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: uuid.UUID, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    def ch(r: NotificationRecipient) -> bool:
        if r.read_at is not None:
            return False
        r.read_at = _now()
        return True

    return _set(db, p, notification_id, ch)


@router.post("/{notification_id}/unread", response_model=NotificationOut)
def mark_unread(notification_id: uuid.UUID, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    def ch(r: NotificationRecipient) -> bool:
        if r.read_at is None:
            return False
        r.read_at = None
        return True

    return _set(db, p, notification_id, ch)


@router.post("/{notification_id}/archive", response_model=NotificationOut)
def archive(notification_id: uuid.UUID, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    """Dismiss: archived rows leave the inbox and stop counting as unread."""

    def ch(r: NotificationRecipient) -> bool:
        if r.archived_at is not None:
            return False
        now = _now()
        r.archived_at = now
        r.read_at = r.read_at or now
        return True

    return _set(db, p, notification_id, ch)


@router.post("/{notification_id}/unarchive", response_model=NotificationOut)
def unarchive(notification_id: uuid.UUID, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    def ch(r: NotificationRecipient) -> bool:
        if r.archived_at is None:
            return False
        r.archived_at = None
        return True

    return _set(db, p, notification_id, ch)


@router.post("/read-all", response_model=CountOut)
def read_all(body: ReadAllIn, db: DbDep, p: PrincipalDep) -> dict[str, int]:
    _throttle(p)
    grants = grants_of(p)
    seqs = _visible_ids(p.employee.id, p.company_id, grants).where(UNREAD)
    if body.category:
        seqs = seqs.where(Notification.category == body.category)
    res = db.execute(
        update(NotificationRecipient)
        .where(NotificationRecipient.seq.in_(seqs.scalar_subquery()))
        .values(read_at=_now())
    )
    n = int(getattr(res, "rowcount", 0) or 0)
    if n:
        mark_changed(db, p.employee.id)
    db.commit()
    return {"updated": n, "unread": summary(db, p.employee.id, p.company_id, grants)["unread"]}


@router.post("/archive", response_model=CountOut)
def archive_many(body: ArchiveIn, db: DbDep, p: PrincipalDep) -> dict[str, int]:
    """Clear a stack (a group of related notifications) in one go."""
    _throttle(p)
    grants = grants_of(p)
    seqs = _visible_ids(p.employee.id, p.company_id, grants).where(
        NotificationRecipient.notification_id.in_(body.ids),
        NotificationRecipient.archived_at.is_(None),
    )
    now = _now()
    res = db.execute(
        update(NotificationRecipient)
        .where(NotificationRecipient.seq.in_(seqs.scalar_subquery()))
        .values(
            archived_at=now,
            read_at=func.coalesce(NotificationRecipient.read_at, now),
        )
    )
    n = int(getattr(res, "rowcount", 0) or 0)
    if n:
        mark_changed(db, p.employee.id)
    db.commit()
    return {"updated": n, "unread": summary(db, p.employee.id, p.company_id, grants)["unread"]}


# ------------------------------------------------------------------ preferences


def _can_mute_security(p: Principal) -> bool:
    return p.can("employee.manage")


@router.get("/preferences", response_model=PrefsOut)
def get_preferences(db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    return prefs_out(db.get(NotificationPreference, p.employee.id), _can_mute_security(p))


def _hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


@router.put("/preferences", response_model=PrefsOut)
def put_preferences(
    body: PrefsIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """Optimistic concurrency on `version` (0 = never saved). Muting the security category
    needs employee.manage; critical alerts are delivered whatever is chosen here."""
    sec = body.categories.get("security")
    if sec is not None and not sec.in_app and not _can_mute_security(p):
        raise ApiError(
            422,
            "SECURITY_ALERTS_LOCKED",
            "Account and security alerts can't be turned off; ask an administrator",
        )

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        row = db.get(
            NotificationPreference, p.employee.id, with_for_update=True, populate_existing=True
        )
        current = row.version if row else 0
        if current != body.version:
            raise ApiError(
                409,
                "VERSION_CONFLICT",
                "Your notification settings changed in another tab; reload",
                current_version=current,
            )
        before = prefs_out(row, True)
        if row is None:
            row = NotificationPreference(employee_id=p.employee.id, company_id=p.company_id)
            db.add(row)
        else:
            row.version += 1
        row.in_app, row.desktop, row.email = body.in_app, body.desktop, body.email
        row.sound, row.grouped, row.quiet_hours = body.sound, body.grouped, body.quiet_hours
        row.quiet_start, row.quiet_end = _hhmm(body.quiet_start), _hhmm(body.quiet_end)
        row.categories = {c: v.model_dump() for c, v in body.categories.items()}
        row.updated_at = _now()
        db.flush()
        db.refresh(row)
        after = prefs_out(row, _can_mute_security(p))
        changed = sorted(
            k for k in ("in_app", "desktop", "email", "sound", "grouped", "quiet_hours",
                        "quiet_start", "quiet_end", "categories")
            if before[k] != after[k]
        )  # fmt: skip
        muted = sorted(c for c, v in after["categories"].items() if not v["in_app"])
        return after, str(p.employee.id), {"changed": changed, "muted_categories": muted}

    return mutate(
        db, request, p, key, f"notification.prefs:{p.employee.id}", body, 200,
        "notification.preferences_update", fn,
    )  # fmt: skip


# ------------------------------------------------------------------ live stream (SSE)

_streams: Counter[uuid.UUID] = Counter()
_streams_lock = threading.Lock()


def _session_ok(s: Session, session_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
    idle = timedelta(minutes=get_settings().session_idle_minutes)
    sess = s.get(AuthSession, session_id)
    emp = s.get(Employee, employee_id)
    now = _now()
    return not (
        sess is None
        or emp is None
        or not emp.active
        or sess.revoked_at is not None
        or sess.expires_at <= now
        or sess.last_seen_at + idle <= now
    )


def _fetch(
    session_id: uuid.UUID,
    employee_id: uuid.UUID,
    company_id: uuid.UUID,
    grants: Grants,
    after: int,
    limit: int,
) -> tuple[list[dict[str, Any]], bool, dict[str, int]] | None:
    """New rows after `after` (oldest first, with alert hints) + counts; None = session over."""
    with get_sessionmaker()() as s:
        if not _session_ok(s, session_id, employee_id):
            return None
        rows = s.execute(
            _base(employee_id, company_id, grants)
            .where(NotificationRecipient.seq > after, NotificationRecipient.archived_at.is_(None))
            .order_by(NotificationRecipient.seq)
            .limit(limit + 1)
        ).all()
        more = len(rows) > limit
        items: list[dict[str, Any]] = []
        if rows:
            prefs = prefs_of(s.get(NotificationPreference, employee_id))
            now_local = company_now(s, company_id).time()
            for r, n in rows[:limit]:
                items.append(
                    item_out(r, n) | {"alert": alert_for(prefs, n.category, n.severity, now_local)}
                )
        return items, more, summary(s, employee_id, company_id, grants)


def _sse(event: str, data: Any, eid: int | None = None) -> str:
    head = f"id: {eid}\n" if eid is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _subscribe(employee_id: uuid.UUID) -> tuple[Any, Any] | None:
    try:
        client = aioredis.Redis.from_url(get_settings().redis_url, socket_timeout=5)
        ps = client.pubsub()
        await ps.subscribe(channel(employee_id))
        return client, ps
    except (redis.RedisError, OSError):
        return None


async def _wait(sub: tuple[Any, Any] | None, seconds: float) -> None:
    """Sleep until a wake-up for this employee arrives or `seconds` pass."""
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


@router.get("/stream", response_class=StreamingResponse)
def stream(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    timeout: int = Query(55, ge=1, le=300, description="Seconds before the stream closes"),
    after: int | None = Query(None, ge=0, description="Resume after this seq"),
) -> StreamingResponse:
    """Server-Sent Events for the caller's inbox, sent only for committed rows:
    `notification` (id = seq, so the browser's Last-Event-ID resumes after a reconnect),
    `unread` (counts changed, e.g. read in another tab), `resync` (too much was missed: reload),
    and `ready` (id = latest seq). Missed rows are replayed from Last-Event-ID / `after`."""
    emp_id, company_id, session_id = p.employee.id, p.company_id, p.session.id
    grants = grants_of(p)
    raw = request.headers.get("last-event-id")
    resume = after
    if raw and raw.isdigit():
        resume = int(raw)
    with _streams_lock:
        if _streams[emp_id] >= MAX_STREAMS_PER_EMPLOYEE:
            raise ApiError(429, "TOO_MANY_STREAMS", "Too many live notification views; close a tab")
        _streams[emp_id] += 1
    release = _once(emp_id)
    try:
        first = summary(db, emp_id, company_id, grants)
    except Exception:
        release()
        raise
    db.close()

    async def gen() -> AsyncIterator[str]:
        sub = None
        try:
            yield "retry: 3000\n\n"
            last = first["latest_seq"]
            counts = first
            if resume is not None and resume < last:
                got = await run_in_threadpool(
                    _fetch, session_id, emp_id, company_id, grants, resume, REPLAY_MAX
                )
                if got is None:
                    return
                items, more, counts = got
                if more:
                    yield _sse("resync", {"reason": "missed too many"})
                else:
                    for it in items:
                        yield _sse("notification", it, it["seq"])
                last = max(last, counts["latest_seq"])
            yield _sse("ready", counts, last)
            sub = await _subscribe(emp_id)
            poll = POLL_SECONDS if sub is not None else POLL_SECONDS_NO_REDIS
            end = time.monotonic() + timeout
            while (left := end - time.monotonic()) > 0:
                await _wait(sub, min(poll, left))
                got = await run_in_threadpool(
                    _fetch, session_id, emp_id, company_id, grants, last, REPLAY_MAX
                )
                if got is None:
                    return
                items, more, now_counts = got
                if more:
                    yield _sse("resync", {"reason": "burst"})
                    last = now_counts["latest_seq"]
                else:
                    for it in items:
                        last = max(last, it["seq"])
                        yield _sse("notification", it, it["seq"])
                if now_counts != counts:
                    counts = now_counts
                    yield _sse("unread", counts, last)
                elif not items and not more:
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
