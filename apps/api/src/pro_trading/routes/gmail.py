"""Gmail Inbox API: connect one mailbox (read-only), browse the stored mail, preview a chosen
attachment and hand it to the existing OCR review. Design: docs/modules/15-gmail-inbox.md.

Access is company-wide only (like Order Connect): the mailbox is not tied to a warehouse, so a
warehouse-scoped role does not open it. Processing additionally needs `scan.create` in the
warehouse the document is for.

`POST /gmail/push` is the one route without a staff session: Google Pub/Sub calls it. It is
refused unless the caller passes `gmail.pubsub.verify_push`; it stores nothing from the body
and only queues a sync.
"""

import uuid
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from .. import audit, idempotency, ratelimit, scanning
from ..config import GMAIL_READONLY_SCOPE, get_settings
from ..deps import DbDep, Principal, PrincipalDep, authorize
from ..errors import ApiError
from ..gmail import client as g
from ..gmail import processing, pubsub
from ..gmail import sync as gsync
from ..gmail.models import (
    LIVE_STATUSES,
    GmailAccount,
    GmailAttachment,
    GmailMessage,
    GmailProcessingJob,
)
from ..gmail.schemas import (
    DateRange,
    DisconnectIn,
    GmailAccountOut,
    GmailAttachmentDetail,
    GmailMessageDetail,
    GmailMessageOut,
    GmailMessagePage,
    GmailStatusOut,
    OAuthCompleteIn,
    OAuthStartOut,
    ProcessIn,
    ProcessOut,
    ReadIn,
    SyncOut,
)
from ..models import Company, Employee, Warehouse
from ._common import content_disposition, esc, mutate

router = APIRouter(prefix="/gmail", tags=["gmail"])

Result = tuple[dict[str, Any], str, dict[str, Any]]
READ = "gmail.read"
BODY = "gmail.body.read"
PREVIEW = "gmail.attachment.read"
DOWNLOAD = "gmail.attachment.download"
PROCESS = "gmail.process"
REPROCESS = "gmail.reprocess"
MANAGE = "gmail.manage"


# ------------------------------------------------------------------ helpers


def _name(db: Session, emp_id: uuid.UUID | None) -> str | None:
    if emp_id is None:
        return None
    e = db.get(Employee, emp_id)
    return e.display_name if e else None


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def account_out(db: Session, a: GmailAccount) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "email_address": a.email_address,
        "status": a.status,
        "connected_at": a.connected_at.isoformat(),
        "connected_by_name": _name(db, a.connected_by),
        "last_sync_at": _iso(a.last_sync_at),
        "last_error_code": a.last_error_code,
        "last_error": a.last_error,
        "last_error_at": _iso(a.last_error_at),
        "watch_expires_at": _iso(a.watch_expires_at),
        "last_push_at": _iso(a.last_push_at),
        "backfill_done": a.backfill_done_at is not None,
        "version": a.version,
    }


def _current(db: Session, company_id: uuid.UUID, *, lock: bool = False) -> GmailAccount | None:
    """The company's mailbox that is not disconnected (at most one: partial unique index)."""
    stmt = select(GmailAccount).where(
        GmailAccount.company_id == company_id, GmailAccount.status != "DISCONNECTED"
    )
    if lock:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def attachment_out(a: GmailAttachment, state: str | None) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "message_id": str(a.message_id),
        "filename": a.filename,
        "mime_type": a.detected_mime or a.mime_type,
        "byte_size": a.byte_size,
        "inline": a.inline,
        "eligible": a.eligible and a.rejected_code is None,
        "unsupported_reason": processing.reject_reason(a),
        "page_count": a.page_count,
        "sha256": a.sha256,
        "state": state or "NOT_PROCESSED",
    }


_RANK = ["NEEDS_REVIEW", "PROCESSING", "FAILED", "RECEIVED", "PROCESSED", "REJECTED"]


def messages_out(db: Session, rows: list[GmailMessage]) -> list[dict[str, Any]]:
    ids = [m.id for m in rows]
    atts: dict[uuid.UUID, list[GmailAttachment]] = {i: [] for i in ids}
    if ids:
        for a in db.scalars(
            select(GmailAttachment)
            .where(GmailAttachment.message_id.in_(ids))
            .order_by(GmailAttachment.inline, GmailAttachment.part_id)
        ):
            atts[a.message_id].append(a)
    states = processing.state_of(db, [a.id for v in atts.values() for a in v])
    out = []
    for m in rows:
        mine = atts[m.id]
        seen = [states[a.id] for a in mine if a.id in states]
        state = next((s for s in _RANK if s in seen), "NOT_PROCESSED")
        out.append(
            {
                "id": str(m.id),
                "from_name": m.from_name,
                "from_email": m.from_email,
                "to_addr": m.to_addr,
                "subject": m.subject,
                "snippet": m.snippet,
                "received_at": m.received_at.isoformat(),
                "read": m.read_at is not None,
                "labels": [str(x) for x in (m.labels or [])],
                "attachment_count": m.attachment_count,
                "pdf_count": m.pdf_count,
                "removed": m.removed_at is not None,
                "state": state,
                "attachments": [attachment_out(a, states.get(a.id)) for a in mine],
            }
        )
    return out


def get_message(db: Session, p: Principal, mid: uuid.UUID) -> GmailMessage:
    m = db.get(GmailMessage, mid)
    if m is None or m.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Email not found")
    return m


def get_attachment(
    db: Session, p: Principal, aid: uuid.UUID
) -> tuple[GmailAttachment, GmailMessage]:
    a = db.get(GmailAttachment, aid)
    if a is None or a.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Attachment not found")
    m = db.get(GmailMessage, a.message_id)
    assert m is not None
    return a, m


def _throttle(bucket: str, p: Principal, per_minute: int) -> None:
    if ratelimit.attempt(bucket, str(p.employee.id), 60) > per_minute:
        raise ApiError(429, "TOO_MANY_REQUESTS", "Too many requests; wait a moment")


def _day_bounds(
    db: Session, company_id: uuid.UUID, rng: DateRange, d_from: date | None, d_to: date | None
) -> tuple[datetime | None, datetime | None]:
    """[start, end) of the chosen range, in the company's time zone."""
    tz = ZoneInfo(db.scalar(select(Company.timezone).where(Company.id == company_id)) or "UTC")
    today = datetime.now(tz).date()

    def at(d: date) -> datetime:
        return datetime.combine(d, time.min, tz)

    if rng == "today":
        return at(today), None
    if rng == "yesterday":
        return at(today - timedelta(days=1)), at(today)
    if rng == "7d":
        return at(today - timedelta(days=6)), None
    if rng == "30d":
        return at(today - timedelta(days=29)), None
    if rng == "custom":
        if d_from and d_to and d_to < d_from:
            raise ApiError(422, "VALIDATION", "The end date is before the start date")
        return (at(d_from) if d_from else None, at(d_to + timedelta(days=1)) if d_to else None)
    return None, None


# ------------------------------------------------------------------ connection


@router.get("/status", response_model=GmailStatusOut)
def status(request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    """Whether Gmail is set up on the server, which mailbox is connected and how its sync is
    doing. Never returns a token or the client secret."""
    authorize(request, p, READ)
    s = get_settings()
    acc = _current(db, p.company_id)
    base = select(func.count()).select_from(GmailMessage).where(
        GmailMessage.company_id == p.company_id
    )  # fmt: skip
    return {
        "configured": s.gmail_configured,
        "push_configured": bool(s.gmail_pubsub_topic) and s.gmail_push_configured,
        "poll_seconds": s.gmail_poll_seconds,
        "scope": GMAIL_READONLY_SCOPE,
        "redirect_uri": s.gmail_redirect if p.can(MANAGE) else "",
        "account": account_out(db, acc) if acc else None,
        "message_count": db.scalar(base) or 0,
        "unread_count": db.scalar(base.where(GmailMessage.read_at.is_(None))) or 0,
        "can_manage": p.can(MANAGE),
        "can_read_body": p.can(BODY),
        "can_preview": p.can(PREVIEW),
        "can_download": p.can(DOWNLOAD),
        "can_process": p.can(PROCESS),
        "can_reprocess": p.can(REPROCESS),
    }


@router.post("/oauth/start", response_model=OAuthStartOut)
def oauth_start(request: Request, p: PrincipalDep) -> dict[str, str]:
    """The Google consent address for connecting the mailbox (read-only scope, offline
    access). The browser goes there; Google sends it back to the app's /gmail/oauth/callback
    page, which calls POST /gmail/oauth/complete."""
    authorize(request, p, MANAGE)
    if not get_settings().gmail_configured:
        raise ApiError(
            503,
            "GMAIL_NOT_CONFIGURED",
            "Gmail is not set up on this server yet (GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET)",
        )
    _throttle("gmail_oauth", p, 10)
    state, challenge = g.new_state(str(p.employee.id), str(p.session.id))
    return {"authorization_url": g.authorization_url(state, challenge)}


@router.post("/oauth/complete", response_model=GmailAccountOut)
def oauth_complete(
    body: OAuthCompleteIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """Finish connecting: exchange Google's one-time code (server to server), check that
    read-only access was granted, store the encrypted tokens and queue the first sync."""
    authorize(request, p, MANAGE)
    s = get_settings()
    if not s.gmail_configured:
        raise ApiError(503, "GMAIL_NOT_CONFIGURED", "Gmail is not set up on this server yet")

    def fn() -> Result:
        try:
            verifier = g.read_state(body.state, str(p.employee.id), str(p.session.id))
            tokens = g.exchange_code(body.code, verifier)
            if GMAIL_READONLY_SCOPE not in tokens.scopes.split():
                raise g.GmailError(
                    "GMAIL_SCOPE_MISSING", "Read-only access to the mailbox was not granted"
                )
            prof = g.Mailbox(tokens.access_token).profile()
        except g.GmailError as e:
            status_code = 502 if e.transient else 400
            raise ApiError(status_code, e.code, e.detail) from None
        email = str(prof.get("emailAddress") or "").strip().lower()
        if not email:
            raise ApiError(502, "GMAIL_ERROR", "Google did not say which mailbox this is")
        allowed = s.gmail_allowed_address.strip().lower()
        if allowed and email != allowed:
            g.revoke(tokens.refresh_token or tokens.access_token)
            raise ApiError(
                409,
                "GMAIL_WRONG_MAILBOX",
                f"Only {allowed} may be connected; you signed in as {email}",
            )
        live = _current(db, p.company_id, lock=True)
        if live is not None and live.email_address != email:
            g.revoke(tokens.refresh_token or tokens.access_token)
            raise ApiError(
                409,
                "GMAIL_ALREADY_CONNECTED",
                f"{live.email_address} is connected. Disconnect it before connecting another.",
            )
        acc = live or db.scalar(
            select(GmailAccount)
            .where(GmailAccount.company_id == p.company_id, GmailAccount.email_address == email)
            .order_by(GmailAccount.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        refresh = tokens.refresh_token or (
            g.unseal(acc.refresh_token_enc) if acc is not None and acc.refresh_token_enc else None
        )
        if refresh is None:
            raise ApiError(
                400,
                "GMAIL_NO_REFRESH_TOKEN",
                "Google did not grant offline access. Remove Pro Trading under the Google "
                "account's third-party access, then connect again.",
            )
        t = gsync.now()
        if acc is None:
            acc = GmailAccount(company_id=p.company_id, email_address=email)
            db.add(acc)
        else:
            acc.version += 1
        acc.status = "CONNECTED"
        acc.scopes = tokens.scopes
        acc.refresh_token_enc = g.seal(refresh)
        acc.access_token_enc = g.seal(tokens.access_token)
        acc.access_expires_at = tokens.expires_at
        acc.connected_by, acc.connected_at = p.employee.id, t
        acc.disconnected_by = acc.disconnected_at = None
        acc.failure_count = 0
        acc.last_error_code = acc.last_error = acc.last_error_at = None
        acc.watch_expires_at = acc.watch_renewed_at = None
        if acc.history_id is None:
            # The cursor starts now: mail from this moment on comes through the history feed;
            # everything older is brought in by the page-by-page import.
            try:
                acc.history_id = int(prof.get("historyId") or 0) or None
            except (TypeError, ValueError):
                acc.history_id = None
            acc.backfill_started_at = t
        db.flush()
        gsync.request_sync(db, acc, "connected")
        return account_out(db, acc), str(acc.id), {"email": email, "scopes": tokens.scopes}

    return mutate(db, request, p, key, "gmail.connect", body, 200, "gmail_account.connect", fn)


@router.post("/disconnect", response_model=GmailAccountOut)
def disconnect(
    body: DisconnectIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    """Stop syncing and forget the tokens (also withdrawn at Google, best effort). Stored
    emails, processed documents and drafts stay."""
    authorize(request, p, MANAGE)

    def fn() -> Result:
        acc = _current(db, p.company_id, lock=True)
        if acc is None:
            raise ApiError(409, "GMAIL_NOT_CONNECTED", "No mailbox is connected")
        if acc.version != body.version:
            raise ApiError(
                409, "VERSION_CONFLICT", "The connection changed; reload", current=acc.version
            )
        revoked = False
        if acc.refresh_token_enc is not None:
            try:
                revoked = g.revoke(g.unseal(acc.refresh_token_enc))
            except g.GmailError:
                revoked = False
        acc.status = "DISCONNECTED"
        acc.refresh_token_enc = acc.access_token_enc = None
        acc.access_expires_at = acc.watch_expires_at = acc.watch_renewed_at = None
        acc.disconnected_by, acc.disconnected_at = p.employee.id, gsync.now()
        acc.version += 1
        db.flush()
        detail = {"email": acc.email_address, "revoked_at_google": revoked}
        return account_out(db, acc), str(acc.id), detail

    return mutate(
        db, request, p, key, "gmail.disconnect", body, 200, "gmail_account.disconnect", fn
    )


@router.post("/sync", response_model=SyncOut, status_code=202)
def sync_now(request: Request, db: DbDep, p: PrincipalDep) -> dict[str, bool]:
    """Refresh: ask the worker to check the mailbox now."""
    authorize(request, p, READ)
    _throttle("gmail_sync", p, 6)
    acc = _current(db, p.company_id)
    if acc is None or acc.status not in LIVE_STATUSES:
        raise ApiError(409, "GMAIL_NOT_CONNECTED", "No mailbox is connected")
    queued = gsync.request_sync(db, acc, "refresh")
    db.commit()
    return {"queued": queued}


# ------------------------------------------------------------------ inbox


@router.get("/messages", response_model=GmailMessagePage)
def list_messages(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    q: Annotated[str, Query(max_length=200)] = "",
    range: DateRange = "all",  # noqa: A002 - the query parameter's name
    date_from: date | None = None,
    date_to: date | None = None,
    has_attachments: bool = False,
    pdf: bool = False,
    processed: bool | None = None,
    unread: bool = False,
    cursor: Annotated[str | None, Query(pattern=r"^\d{1,9}$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """Stored emails, newest first. Search covers sender, subject and attachment file names
    (a search that is a date, YYYY-MM-DD, also matches that day); the body is not searched."""
    authorize(request, p, READ)
    stmt = select(GmailMessage).where(GmailMessage.company_id == p.company_id)
    start, end = _day_bounds(db, p.company_id, range, date_from, date_to)
    if start is not None:
        stmt = stmt.where(GmailMessage.received_at >= start)
    if end is not None:
        stmt = stmt.where(GmailMessage.received_at < end)
    if has_attachments:
        stmt = stmt.where(GmailMessage.attachment_count > 0)
    if pdf:
        stmt = stmt.where(GmailMessage.pdf_count > 0)
    if unread:
        stmt = stmt.where(GmailMessage.read_at.is_(None))
    if processed is not None:
        has_job = exists().where(GmailProcessingJob.message_id == GmailMessage.id)
        if processed:
            stmt = stmt.where(has_job)
        else:  # something that could be read, and nobody has yet
            stmt = stmt.where(
                ~has_job,
                exists().where(
                    GmailAttachment.message_id == GmailMessage.id,
                    GmailAttachment.eligible.is_(True),
                ),
            )
    text = q.strip()
    if text:
        like = f"%{esc(text)}%"
        conds = [
            GmailMessage.from_name.ilike(like, escape="\\"),
            GmailMessage.from_email.ilike(like, escape="\\"),
            GmailMessage.subject.ilike(like, escape="\\"),
            exists().where(
                GmailAttachment.message_id == GmailMessage.id,
                GmailAttachment.filename.ilike(like, escape="\\"),
            ),
        ]
        try:
            day = date.fromisoformat(text)
        except ValueError:
            pass
        else:
            d0, d1 = _day_bounds(db, p.company_id, "custom", day, day)
            conds.append(and_(GmailMessage.received_at >= d0, GmailMessage.received_at < d1))
        stmt = stmt.where(or_(*conds))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    off = int(cursor or 0)
    rows = list(
        db.scalars(
            stmt.order_by(GmailMessage.received_at.desc(), GmailMessage.id.desc())
            .offset(off)
            .limit(limit)
        ).all()
    )
    nxt = off + len(rows)
    return {
        "items": messages_out(db, rows),
        "next_cursor": str(nxt) if nxt < total else None,
        "total": total,
    }


@router.get("/messages/{message_id}", response_model=GmailMessageDetail)
def read_message(
    message_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    """One email. The body (plain text and allow-listed HTML) is only included for callers who
    may read email bodies."""
    authorize(request, p, READ)
    m = get_message(db, p, message_id)
    body = p.can(BODY)
    return {
        **messages_out(db, [m])[0],
        "thread_id": m.thread_id,
        "body_visible": body,
        "body_text": m.body_text if body else "",
        "body_html": m.body_html if body else "",
        "body_truncated": m.body_truncated,
    }


@router.post("/messages/{message_id}/read", response_model=GmailMessageOut)
def mark_read(
    message_id: uuid.UUID, body: ReadIn, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    """Read / unread inside Pro Trading only. The mailbox itself is never changed."""
    authorize(request, p, READ)
    m = db.get(GmailMessage, message_id, with_for_update=True)
    if m is None or m.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Email not found")
    if body.read and m.read_at is None:
        m.read_at, m.read_by = gsync.now(), p.employee.id
    elif not body.read:
        m.read_at = m.read_by = None
    db.commit()
    return messages_out(db, [m])[0]


# ------------------------------------------------------------------ attachments


@router.get("/attachments/{attachment_id}", response_model=GmailAttachmentDetail)
def read_attachment(
    attachment_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    """An attachment and every earlier use of it (who, when, which reading, which draft)."""
    authorize(request, p, READ)
    a, m = get_attachment(db, p, attachment_id)
    hist = processing.history(db, a)
    own = next((h["status"] for h in hist if h["same_attachment"]), None)
    return {
        **attachment_out(a, own),
        "message_subject": m.subject,
        "message_from": m.from_name or m.from_email,
        "received_at": m.received_at.isoformat(),
        "history": hist,
    }


_FILE_HEADERS = {
    "Cache-Control": "private, max-age=0, must-revalidate",
    "X-Content-Type-Options": "nosniff",
}


@router.get(
    "/attachments/{attachment_id}/pages/{page}",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def attachment_page(
    attachment_id: uuid.UUID, page: int, request: Request, db: DbDep, p: PrincipalDep
) -> Response:
    """One page of the attachment as a PNG picture (the file itself is never sent to the
    browser for a preview). Header X-Page-Count gives the number of pages."""
    authorize(request, p, PREVIEW)
    a, m = get_attachment(db, p, attachment_id)
    if page < 1 or page > processing.MAX_PREVIEW_PAGES:
        raise ApiError(404, "NOT_FOUND", "No such page")
    _throttle("gmail_preview", p, 240)
    data, mime = processing.load(db, a, m)
    png, count = scanning.render_page(data, mime, page)
    return Response(
        png, media_type="image/png", headers={**_FILE_HEADERS, "X-Page-Count": str(count)}
    )


@router.get(
    "/attachments/{attachment_id}/content",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, "image/png": {}, "image/jpeg": {}}}},
)
def attachment_content(
    attachment_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> Response:
    """Download the original file (PDF or picture only; its type is taken from its bytes).
    Always sent as a download, never shown inline. Audited."""
    authorize(request, p, DOWNLOAD)
    a, m = get_attachment(db, p, attachment_id)
    _throttle("gmail_download", p, 30)
    data, mime = processing.load(db, a, m)
    audit.record(
        db,
        action="gmail_attachment.download",
        success=True,
        company_id=p.company_id,
        actor_id=p.employee.id,
        target_type="gmail_attachment",
        target_id=str(a.id),
        request_id=request.state.request_id,
        detail={"filename": a.filename, "bytes": len(data), "sha256": a.sha256},
    )
    db.commit()
    return Response(
        data,
        media_type=mime,
        headers={
            **_FILE_HEADERS,
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Content-Disposition": content_disposition(
                "attachment", scanning.safe_filename(a.filename, mime)
            ),
        },
    )


@router.post("/attachments/{attachment_id}/process", status_code=201, response_model=ProcessOut)
def process_attachment(
    attachment_id: uuid.UUID,
    body: ProcessIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Use for OCR: fetch and check the attachment, keep it as a source document, open a scan
    and queue the existing OCR reading. Returns where to review it. Creates no order, invoice
    or stock movement; the reviewed reading becomes a DRAFT sales order only when a person
    accepts it in the review screen."""
    authorize(request, p, PROCESS)
    authorize(request, p, "scan.create", body.warehouse_id)
    if body.reprocess or body.confirm_duplicate:
        authorize(request, p, REPROCESS)
    wh = db.get(Warehouse, body.warehouse_id)
    if wh is None or wh.company_id != p.company_id or not wh.active:
        raise ApiError(404, "NOT_FOUND", "Warehouse not found")
    a, m = get_attachment(db, p, attachment_id)
    _throttle("gmail_process", p, 20)
    data, mime = processing.load(db, a, m)

    def fn() -> Result:
        job, scan, ex = processing.start(
            db,
            p,
            a,
            m,
            data,
            mime,
            warehouse_id=body.warehouse_id,
            category=body.category,
            reprocess=body.reprocess,
            confirm_duplicate=body.confirm_duplicate,
        )
        out = {
            "job_id": str(job.id),
            "attempt_no": job.attempt_no,
            "attachment_id": str(a.id),
            "scan_job_id": str(scan.id),
            "scan_job_no": scan.job_no,
            "extraction_id": str(ex.id),
            "extraction_status": ex.status,
            "review_path": f"/scan/review/{ex.id}",
        }
        detail = {
            "filename": a.filename,
            "sha256": a.sha256,
            "scan_job": scan.job_no,
            "attempt": job.attempt_no,
            "reprocess": job.reprocess,
            "duplicate_confirmed": job.duplicate_confirmed,
            "from": m.from_email,
        }
        return out, str(job.id), detail

    return mutate(
        db,
        request,
        p,
        key,
        f"gmail.process:{attachment_id}",
        body,
        201,
        "gmail_attachment.process",
        fn,
    )


# ------------------------------------------------------------------ Pub/Sub push (no session)


@router.post("/push", status_code=204, response_class=Response)
def push(
    request: Request,
    db: DbDep,
    body: Annotated[dict[str, Any], Body()] = {},  # noqa: B006 - read-only default
    token: Annotated[str | None, Query(max_length=256)] = None,
) -> Response:
    """Google Pub/Sub tells us the mailbox changed. Verified (signed OIDC token and/or shared
    token) before anything is read; then a sync is queued and 204 returned. An unknown mailbox
    or an unreadable body is acknowledged too, so Pub/Sub does not redeliver it forever."""
    s = get_settings()
    ip = request.client.host if request.client else "unknown"
    window = s.login_ip_window_minutes * 60
    if ratelimit.failures("gmail_push_ip", ip) > s.login_ip_max_failures:
        raise ApiError(429, "TOO_MANY_ATTEMPTS", "Too many refused pushes")
    try:
        pubsub.verify_push(request.headers.get("authorization"), token)
    except pubsub.PushRejected as e:
        ratelimit.attempt("gmail_push_ip", ip, window)
        audit.record_independent(
            action="gmail.push.rejected",
            success=False,
            request_id=getattr(request.state, "request_id", None),
            target_type="gmail_push",
            target_id=request.url.path,
            detail={"reason": e.reason},
        )
        if e.reason == "not_configured":
            raise ApiError(
                401, "PUSH_NOT_CONFIGURED", "Push is not set up on this server"
            ) from None
        raise ApiError(401, "PUSH_REJECTED", "This push could not be verified") from None
    try:
        email, _history_id = pubsub.decode_envelope(body)
    except (ValueError, TypeError):
        return Response(status_code=204)
    acc = db.scalar(
        select(GmailAccount)
        .where(GmailAccount.email_address == email, GmailAccount.status.in_(LIVE_STATUSES))
        .with_for_update()
    )
    if acc is not None:
        acc.last_push_at = gsync.now()
        gsync.request_sync(db, acc, "push")
        db.commit()
    return Response(status_code=204)
