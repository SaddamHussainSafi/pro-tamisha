"""Keeping the stored inbox in step with the mailbox.

How a sync works:
1. Read the account's saved history cursor (no lock held while talking to Google).
2. Ask Gmail's History API what changed since that cursor; fetch each new message.
3. In ONE database transaction: lock the account row, insert the messages (ignored if another
   sync already stored them: unique account + Gmail message id), apply label changes, then move
   the cursor forward. The cursor never moves backwards, and it only moves when the rows it
   covers were committed with it. A crash before the commit just means the same history is
   read again; a late or duplicate push finds nothing new.
4. A cursor Gmail no longer knows (404) starts a full resync: take the mailbox's current
   history id as the new cursor *first*, then walk the message list page by page.

A Pub/Sub push, the timer and the Refresh button all run this same function. Nothing here
downloads an attachment's bytes, runs OCR or creates any business record.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .. import audit, outbox
from ..config import get_settings
from ..db import get_sessionmaker
from ..models import OutboxEvent
from ..notifications import notify_safely
from . import client as g
from .models import LIVE_STATUSES, GmailAccount, GmailAttachment, GmailMessage
from .parse import ParsedMessage, parse_message

log = logging.getLogger("pro_trading.gmail")

SYNC_EVENT = "gmail.sync"
SOURCE = "Gmail Inbox"
WATCH_RENEW_AFTER = timedelta(hours=24)  # Google: renew at least every 7 days, daily advised
FAILURES_BEFORE_ALERT = 3
MAX_FETCH_PER_RUN = 500  # messages fetched in one incremental run (the rest follow next run)


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class SyncResult:
    account_id: uuid.UUID
    new_messages: int = 0
    removed: int = 0
    relabelled: int = 0
    backfilled: int = 0
    backfill_done: bool = False
    resynced: bool = False
    skipped: str | None = None
    error: str | None = None
    new_ids: list[uuid.UUID] = field(default_factory=list)


# ------------------------------------------------------------------ tokens


def mailbox_for(account_id: uuid.UUID) -> g.Mailbox:
    """A Gmail client for the account, with a valid access token (refreshed and saved if the
    stored one is missing or about to lapse)."""
    token = _access_token(account_id, force=False)
    return g.Mailbox(token, on_unauthorized=lambda: _access_token(account_id, force=True))


def _access_token(account_id: uuid.UUID, *, force: bool) -> str:
    with get_sessionmaker()() as db:
        acc = db.get(GmailAccount, account_id)
        if acc is None or acc.status == "DISCONNECTED" or acc.refresh_token_enc is None:
            raise g.GmailError("GMAIL_NOT_CONNECTED", "No mailbox is connected")
        if (
            not force
            and acc.access_token_enc is not None
            and acc.access_expires_at is not None
            and acc.access_expires_at - g.ACCESS_SKEW > now()
        ):
            return g.unseal(acc.access_token_enc)
        refresh_token = g.unseal(acc.refresh_token_enc)
    tokens = g.refresh(refresh_token)  # network: no transaction open
    with get_sessionmaker()() as db, db.begin():
        acc = db.get(GmailAccount, account_id, with_for_update=True)
        if acc is None or acc.status == "DISCONNECTED":
            raise g.GmailError("GMAIL_NOT_CONNECTED", "No mailbox is connected")
        acc.access_token_enc = g.seal(tokens.access_token)
        acc.access_expires_at = tokens.expires_at
        if tokens.refresh_token:  # Google may rotate it
            acc.refresh_token_enc = g.seal(tokens.refresh_token)
    return tokens.access_token


# ------------------------------------------------------------------ storing


def store_messages(
    db: Session, acc: GmailAccount, parsed: list[ParsedMessage]
) -> list[GmailMessage]:
    """Insert messages that are not stored yet (and their attachment descriptions). Returns the
    rows that are new. Safe to call twice with the same message: the second time adds nothing."""
    new: list[GmailMessage] = []
    for pm in parsed:
        if not pm.gmail_message_id or not pm.incoming:
            continue
        mid = db.scalar(
            insert(GmailMessage)
            .values(
                company_id=acc.company_id,
                account_id=acc.id,
                gmail_message_id=pm.gmail_message_id,
                thread_id=pm.thread_id,
                from_name=pm.from_name,
                from_email=pm.from_email,
                to_addr=pm.to_addr,
                subject=pm.subject,
                snippet=pm.snippet,
                body_text=pm.body_text,
                body_html=pm.body_html,
                body_truncated=pm.body_truncated,
                received_at=pm.received_at,
                labels=pm.labels,
                attachment_count=len(pm.attachments),
                pdf_count=sum(
                    1
                    for a in pm.attachments
                    if a.mime_type == "application/pdf" or a.filename.lower().endswith(".pdf")
                ),
                gmail_history_id=pm.history_id,
            )
            .on_conflict_do_nothing(constraint="uq_gmail_message")
            .returning(GmailMessage.id)
        )
        if mid is None:
            # Already stored (duplicate push, overlapping sync, resync): refresh labels only.
            db.execute(
                update(GmailMessage)
                .where(
                    GmailMessage.account_id == acc.id,
                    GmailMessage.gmail_message_id == pm.gmail_message_id,
                )
                .values(labels=pm.labels, removed_at=None, synced_at=func.now())
            )
            continue
        for a in pm.attachments:
            db.execute(
                insert(GmailAttachment)
                .values(
                    company_id=acc.company_id,
                    message_id=mid,
                    part_id=a.part_id,
                    gmail_attachment_id=a.gmail_attachment_id,
                    filename=a.filename,
                    mime_type=a.mime_type,
                    byte_size=a.byte_size,
                    inline=a.inline,
                    eligible=a.eligible,
                )
                .on_conflict_do_nothing(constraint="uq_gmail_attachment_part")
            )
        row = db.get(GmailMessage, mid)
        assert row is not None
        new.append(row)
    return new


def _advance(acc: GmailAccount, history_id: int | None) -> None:
    """Move the cursor forward only (duplicate and out-of-order signals carry older ids)."""
    if history_id and (acc.history_id is None or history_id > acc.history_id):
        acc.history_id = history_id


def _ok(acc: GmailAccount) -> None:
    acc.last_sync_at = now()
    acc.failure_count = 0
    acc.last_error_code = acc.last_error = None
    acc.last_error_at = None
    if acc.status == "ERROR":
        acc.status = "CONNECTED"


def _announce(db: Session, acc: GmailAccount, new: list[GmailMessage]) -> None:
    """One notification per sync for new mail that carries a document (PDF or picture).
    Ordinary mail is shown in the inbox without a notification."""
    with_docs: list[tuple[GmailMessage, list[str]]] = []
    for m in new:
        names = list(
            db.scalars(
                select(GmailAttachment.filename).where(
                    GmailAttachment.message_id == m.id,
                    GmailAttachment.eligible.is_(True),
                    GmailAttachment.inline.is_(False),
                )
            ).all()
        )
        if names:
            with_docs.append((m, names))
    if not with_docs:
        return
    first, names = with_docs[0]
    if len(with_docs) == 1:
        title = "New scanned document received"
        more = f" (+{len(names) - 1} more)" if len(names) > 1 else ""
        body = f"{names[0]}{more} from {first.from_name or first.from_email or 'unknown sender'}"
        link = f"/gmail?message={first.id}"
    else:
        title = f"{len(with_docs)} new emails with documents"
        body = ", ".join(n for _, ns in with_docs[:3] for n in ns[:1])
        link = "/gmail?show=not_processed"
    notify_safely(
        db,
        acc.company_id,
        "gmail.message_received",
        event_id=f"gmail.message_received:{first.id}",
        target_type="gmail_message",
        target_id=first.id,
        category="integration",
        severity="info",
        source_module=SOURCE,
        title=title,
        body=body,
        link=link,
        permission="gmail.read",
    )


# ------------------------------------------------------------------ sync


def sync_account(account_id: uuid.UUID, *, backfill: bool = True) -> SyncResult:
    """Bring one account up to date. Never raises: a failure is recorded on the account."""
    res = SyncResult(account_id)
    with get_sessionmaker()() as db:
        acc = db.get(GmailAccount, account_id)
        if acc is None or acc.status not in LIVE_STATUSES:
            res.skipped = "not connected"
            return res
        cursor = acc.history_id
    try:
        box = mailbox_for(account_id)
        _renew_watch(account_id, box)
        if cursor is None:
            _start_resync(account_id, box)
            res.resynced = True
        else:
            try:
                _incremental(account_id, box, cursor, res)
            except g.HistoryExpired:
                _start_resync(account_id, box)
                res.resynced = True
        if backfill:
            _backfill_step(account_id, box, res)
    except g.ReauthRequired as e:
        _reauth_required(account_id, e)
        res.error = e.code
    except g.GmailError as e:
        _failed(account_id, e)
        res.error = e.code
    except Exception as e:  # noqa: BLE001 - recorded; the next run tries again
        log.exception("gmail sync crashed", extra={"ctx": {"account": str(account_id)}})
        _failed(account_id, g.GmailError("GMAIL_SYNC_ERROR", type(e).__name__, transient=True))
        res.error = "GMAIL_SYNC_ERROR"
    return res


def _incremental(account_id: uuid.UUID, box: g.Mailbox, cursor: int, res: SyncResult) -> None:
    added: dict[str, None] = {}  # ordered set of Gmail ids
    deleted: set[str] = set()
    relabel: set[str] = set()
    latest = cursor
    page: str | None = None
    while True:
        data = box.history(cursor, page)
        try:
            latest = max(latest, int(data.get("historyId") or 0))
        except (TypeError, ValueError):
            pass
        for h in data.get("history") or []:
            for x in h.get("messagesAdded") or []:
                mid = str((x.get("message") or {}).get("id") or "")
                if mid:
                    added[mid] = None
            for x in h.get("messagesDeleted") or []:
                mid = str((x.get("message") or {}).get("id") or "")
                if mid:
                    deleted.add(mid)
            for key in ("labelsAdded", "labelsRemoved"):
                for x in h.get(key) or []:
                    mid = str((x.get("message") or {}).get("id") or "")
                    if mid:
                        relabel.add(mid)
        page = data.get("nextPageToken")
        if not page:
            break
    ids = [m for m in added if m not in deleted]
    if ids:  # a repeated signal, or the rest of a cut-short run: skip what is already stored
        with get_sessionmaker()() as db:
            known = set(
                db.scalars(
                    select(GmailMessage.gmail_message_id).where(
                        GmailMessage.account_id == account_id,
                        GmailMessage.gmail_message_id.in_(ids),
                    )
                ).all()
            )
        ids = [m for m in ids if m not in known]
    partial = len(ids) > MAX_FETCH_PER_RUN
    ids = ids[:MAX_FETCH_PER_RUN]
    parsed = _fetch(box, ids)
    relabelled = _fetch(box, [m for m in relabel if m not in added and m not in deleted][:200])

    with get_sessionmaker()() as db, db.begin():
        acc = db.get(GmailAccount, account_id, with_for_update=True)
        if acc is None or acc.status not in LIVE_STATUSES:
            res.skipped = "disconnected during sync"
            return
        new = store_messages(db, acc, parsed)
        for pm in relabelled:
            r = db.execute(
                update(GmailMessage)
                .where(
                    GmailMessage.account_id == acc.id,
                    GmailMessage.gmail_message_id == pm.gmail_message_id,
                )
                .values(labels=pm.labels, synced_at=func.now())
            )
            res.relabelled += int(getattr(r, "rowcount", 0) or 0)
        if deleted:
            r = db.execute(
                update(GmailMessage)
                .where(
                    GmailMessage.account_id == acc.id,
                    GmailMessage.gmail_message_id.in_(sorted(deleted)),
                    GmailMessage.removed_at.is_(None),
                )
                .values(removed_at=func.now())
            )
            res.removed = int(getattr(r, "rowcount", 0) or 0)
        if not partial:  # a cut-short run keeps its cursor: the rest is read next time
            _advance(acc, latest)
        _ok(acc)
        _announce(db, acc, new)
        res.new_messages = len(new)
        res.new_ids = [m.id for m in new]


def _fetch(box: g.Mailbox, ids: list[str]) -> list[ParsedMessage]:
    limit = get_settings().gmail_max_body_chars
    out: list[ParsedMessage] = []
    for mid in ids:
        try:
            out.append(parse_message(box.get_message(mid), limit))
        except g.GmailError as e:
            if e.status == 404:  # deleted between the list and the fetch
                continue
            raise
    return out


def _start_resync(account_id: uuid.UUID, box: g.Mailbox) -> None:
    """First sync, or the cursor expired: take today's history id as the cursor, then let the
    page-by-page import walk the mailbox again (stored messages are skipped by their id)."""
    prof = box.profile()
    try:
        current = int(prof.get("historyId") or 0) or None
    except (TypeError, ValueError):
        current = None
    with get_sessionmaker()() as db, db.begin():
        acc = db.get(GmailAccount, account_id, with_for_update=True)
        if acc is None:
            return
        if current is not None:
            acc.history_id = current  # the old cursor is unusable: replace it
        acc.backfill_page_token = None
        acc.backfill_started_at = now()
        acc.backfill_done_at = None


def _backfill_step(account_id: uuid.UUID, box: g.Mailbox, res: SyncResult) -> None:
    """Import one page of older mail. The page token is saved with the rows of that page, so an
    interrupted import carries on where it stopped."""
    s = get_settings()
    with get_sessionmaker()() as db:
        acc = db.get(GmailAccount, account_id)
        if acc is None or acc.status not in LIVE_STATUSES or acc.backfill_done_at is not None:
            res.backfill_done = acc is not None and acc.backfill_done_at is not None
            return
        token = acc.backfill_page_token
    q = "-in:chats -in:drafts"
    if s.gmail_backfill_days:
        q += f" newer_than:{s.gmail_backfill_days}d"
    data = box.list_messages(token, s.gmail_backfill_batch, q)
    ids = [str(m.get("id")) for m in data.get("messages") or [] if m.get("id")]
    with get_sessionmaker()() as db:
        known = set(
            db.scalars(
                select(GmailMessage.gmail_message_id).where(
                    GmailMessage.account_id == account_id, GmailMessage.gmail_message_id.in_(ids)
                )
            ).all()
        )
    parsed = _fetch(box, [m for m in ids if m not in known])
    nxt = data.get("nextPageToken")
    with get_sessionmaker()() as db, db.begin():
        acc = db.get(GmailAccount, account_id, with_for_update=True)
        if acc is None or acc.status not in LIVE_STATUSES:
            return
        if acc.backfill_page_token != token or acc.backfill_done_at is not None:
            return  # another worker already did this page
        new = store_messages(db, acc, parsed)
        res.backfilled = len(new)
        acc.backfill_page_token = str(nxt) if nxt else None
        if not nxt:
            acc.backfill_done_at = now()
            res.backfill_done = True
        _ok(acc)


def _renew_watch(account_id: uuid.UUID, box: g.Mailbox) -> None:
    """(Re)register the Pub/Sub watch when a topic is configured and the last one is a day old
    or about to lapse. Without a topic there is nothing to renew: the timer does the work."""
    topic = get_settings().gmail_pubsub_topic
    if not topic:
        return
    with get_sessionmaker()() as db:
        acc = db.get(GmailAccount, account_id)
        if acc is None:
            return
        t = now()
        fresh = (
            acc.watch_renewed_at is not None
            and t - acc.watch_renewed_at < WATCH_RENEW_AFTER
            and acc.watch_expires_at is not None
            and acc.watch_expires_at - t > timedelta(days=1)
        )
    if fresh:
        return
    data = box.watch(topic)
    try:
        expires = datetime.fromtimestamp(int(data.get("expiration") or 0) / 1000, UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        expires = now() + timedelta(days=7)
    with get_sessionmaker()() as db, db.begin():
        acc = db.get(GmailAccount, account_id, with_for_update=True)
        if acc is not None:
            acc.watch_expires_at = expires
            acc.watch_renewed_at = now()


# ------------------------------------------------------------------ failures


def _failed(account_id: uuid.UUID, e: g.GmailError) -> None:
    with get_sessionmaker()() as db, db.begin():
        acc = db.get(GmailAccount, account_id, with_for_update=True)
        if acc is None or acc.status not in LIVE_STATUSES:
            return
        acc.failure_count += 1
        acc.last_error_code = e.code[:48]
        acc.last_error = e.detail[:500]
        acc.last_error_at = now()
        if acc.failure_count >= FAILURES_BEFORE_ALERT:
            first = acc.status != "ERROR"
            acc.status = "ERROR"
            if first:
                notify_safely(
                    db,
                    acc.company_id,
                    "gmail.sync_failed",
                    event_id=f"gmail.sync_failed:{acc.id}:{acc.last_error_at:%Y%m%d%H%M}",
                    target_type="gmail_account",
                    target_id=acc.id,
                    category="integration",
                    severity="warning",
                    source_module=SOURCE,
                    title="Gmail Inbox is not syncing",
                    body=f"{acc.email_address}: {e.detail[:200]}. It keeps trying.",
                    link="/gmail",
                    permission="gmail.manage",
                )
    log.warning("gmail sync failed", extra={"ctx": {"account": str(account_id), "code": e.code}})


def _reauth_required(account_id: uuid.UUID, e: g.GmailError) -> None:
    with get_sessionmaker()() as db, db.begin():
        acc = db.get(GmailAccount, account_id, with_for_update=True)
        if acc is None or acc.status not in LIVE_STATUSES:
            return
        acc.status = "REAUTH_REQUIRED"
        acc.access_token_enc = None
        acc.access_expires_at = None
        acc.last_error_code = e.code[:48]
        acc.last_error = e.detail[:500]
        acc.last_error_at = now()
        acc.version += 1
        audit.record(
            db,
            action="gmail_account.reauth_required",
            success=False,
            company_id=acc.company_id,
            target_type="gmail_account",
            target_id=str(acc.id),
            detail={"email": acc.email_address, "reason": e.code},
        )
        notify_safely(
            db,
            acc.company_id,
            "gmail.disconnected",
            event_id=f"gmail.disconnected:{acc.id}:{acc.last_error_at:%Y%m%d%H%M}",
            target_type="gmail_account",
            target_id=acc.id,
            category="integration",
            severity="critical",
            source_module=SOURCE,
            title="Gmail disconnected",
            body=f"Google no longer accepts the saved sign-in for {acc.email_address}. "
            "New mail is not arriving. Connect the mailbox again.",
            link="/gmail",
            permission="gmail.manage",
        )


# ------------------------------------------------------------------ worker entry points


def request_sync(db: Session, acc: GmailAccount, reason: str) -> bool:
    """Queue a sync for the worker (after commit). One waiting job per account is enough."""
    waiting = db.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(
            OutboxEvent.event_type == SYNC_EVENT,
            OutboxEvent.aggregate_id == str(acc.id),
            OutboxEvent.status == "PENDING",
        )
    )
    if waiting:
        return False
    outbox.enqueue(
        db,
        SYNC_EVENT,
        {"account_id": str(acc.id), "reason": reason},
        company_id=acc.company_id,
        aggregate_id=str(acc.id),
    )
    return True


def handle_sync(ev: OutboxEvent) -> None:
    """Outbox handler. The sync records its own failures on the account, so this job itself
    succeeds unless the payload is unusable (the timer is the retry)."""
    try:
        account_id = uuid.UUID(str(ev.payload.get("account_id")))
    except (ValueError, AttributeError):
        return
    sync_account(account_id)


def poll_all() -> dict[str, Any]:
    """Timer (Celery beat): sync every live account. This is the fallback when no push arrives
    and the reconciliation that catches a push Google dropped."""
    with get_sessionmaker()() as db:
        ids = list(
            db.scalars(select(GmailAccount.id).where(GmailAccount.status.in_(LIVE_STATUSES))).all()
        )
    out = {"accounts": len(ids), "new": 0, "errors": 0}
    for aid in ids:
        r = sync_account(aid)
        out["new"] += r.new_messages + r.backfilled
        out["errors"] += 1 if r.error else 0
    return out
