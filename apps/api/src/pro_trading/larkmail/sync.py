"""Non-destructive mailbox sync (worker side; never runs in a browser request).

Per mailbox: one lease (`sync_lease_until`) so two workers never sync the same mailbox. Per
folder: EXAMINE (read-only), new UIDs above `high_uid` first, then history newest-first below
`backfill_uid`, a batch per transaction, so an interrupted import resumes where it stopped.
Messages are keyed by (folder, UIDVALIDITY, UID); when UIDVALIDITY changes, existing rows are
re-keyed by their content hash instead of being imported twice, and rows that never come back
are marked `server_removed_at` (never deleted). Read / star state in Lark is never changed.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_sessionmaker
from . import clamd, mime, sniff
from .imap import ForbiddenImapCommand, ImapAuthError, ImapError, LarkImap
from .models import (
    MailAccount,
    MailAttachment,
    MailAttachmentBlob,
    MailFolder,
    MailMessage,
    MailOutgoing,
    MailSyncRun,
)
from .secrets import SecretUnavailable, unseal
from .smtp import SmtpAuthError, SmtpConfig, SmtpError
from .smtp import check as smtp_check

log = logging.getLogger("pro_trading.larkmail")

LEASE = timedelta(minutes=15)
MAX_BACKOFF = 3600

_SPECIAL = {
    "\\sent": "SENT", "\\drafts": "DRAFTS", "\\junk": "JUNK", "\\trash": "TRASH",
    "\\archive": "ARCHIVE", "\\all": "ALL", "\\flagged": "FLAGGED",
}  # fmt: skip
_NAMES = {
    "SENT": {"sent", "sent messages", "sent items", "sent mail", "已发送", "已發送"},
    "DRAFTS": {"drafts", "draft", "草稿箱", "草稿"},
    "JUNK": {"spam", "junk", "junk email", "junk e-mail", "垃圾邮件", "垃圾郵件"},
    "TRASH": {"trash", "deleted", "deleted items", "deleted messages", "已删除", "已刪除"},
    "ARCHIVE": {"archive", "archives", "归档", "歸檔"},
}
ROLE_ORDER = {"INBOX": 0, "SENT": 2, "DRAFTS": 3, "ARCHIVE": 4, "FLAGGED": 5, "ALL": 6,
              "JUNK": 7, "OTHER": 8, "TRASH": 9}  # fmt: skip


def now() -> datetime:
    return datetime.now(UTC)


def folder_role(name: str, attributes: list[str]) -> str:
    for a in attributes:
        if a.lower() in _SPECIAL:
            return _SPECIAL[a.lower()]
    if name.upper() == "INBOX":
        return "INBOX"
    leaf = name.rsplit("/", 1)[-1].strip().lower()
    for role, names in _NAMES.items():
        if leaf in names:
            return role
    return "OTHER"


def _use_ssl() -> bool:
    return not get_settings().lark_mail_allow_plaintext


def imap_for(acc: MailAccount) -> LarkImap:
    return LarkImap(
        acc.imap_host, acc.imap_port, use_ssl=_use_ssl(),
        timeout=get_settings().lark_mail_timeout_seconds,
    )  # fmt: skip


def smtp_config(acc: MailAccount, password: str) -> SmtpConfig:
    return SmtpConfig(
        acc.smtp_host, acc.smtp_port, acc.username, password, use_ssl=_use_ssl(),
        timeout=get_settings().lark_mail_timeout_seconds,
    )  # fmt: skip


# ------------------------------------------------------------------ connection test


@dataclass
class TestResult:
    imap_ok: bool
    smtp_ok: bool
    imap_error: str | None = None
    smtp_error: str | None = None
    imap_auth_failed: bool = False
    smtp_auth_failed: bool = False
    folders: list[dict[str, str]] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    idle: bool = False


def test_connection(acc: MailAccount, password: str) -> TestResult:
    """Log in to IMAP (list folders only) and to SMTP (no message is sent)."""
    r = TestResult(False, False)
    try:
        with imap_for(acc) as im:
            im.login(acc.username, password)
            r.capabilities = im.capabilities
            r.idle = "IDLE" in im.capabilities
            r.folders = [
                {"name": f.name, "role": folder_role(f.name, f.attributes)}
                for f in im.list_folders()
                if f.selectable
            ]
            r.imap_ok = True
    except ImapAuthError as exc:
        r.imap_error, r.imap_auth_failed = f"Lark refused the password: {exc}", True
    except (ImapError, ForbiddenImapCommand) as exc:
        r.imap_error = str(exc)
    try:
        smtp_check(smtp_config(acc, password))
        r.smtp_ok = True
    except SmtpAuthError as exc:
        r.smtp_error, r.smtp_auth_failed = str(exc), True
    except SmtpError as exc:
        r.smtp_error = str(exc)
    return r


# ------------------------------------------------------------------ storing


def _addr_text(addrs: list[mime.Address]) -> str:
    return " ".join(f"{a.name} {a.email}" for a in addrs)


def _thread_key(db: Session, account_id: uuid.UUID, p: mime.ParsedMail, fallback: str) -> str:
    refs = [r for r in [*p.references, p.in_reply_to] if r]
    if refs:
        parent = db.scalar(
            select(MailMessage.thread_key)
            .where(MailMessage.account_id == account_id, MailMessage.message_id_header.in_(refs))
            .limit(1)
        )
        return parent or refs[0]
    if p.message_id:
        # A reply may already be stored under this message's id (it arrived first).
        return p.message_id
    return fallback


def store(
    db: Session,
    acc: MailAccount,
    folder: MailFolder,
    uid: int,
    raw: bytes | None,
    flags: list[str],
    internal_date: datetime | None,
    size: int,
) -> MailMessage | None:
    """Insert one message (idempotent). Returns None when it was already stored."""
    s = get_settings()
    assert folder.uidvalidity is not None
    exists = db.scalar(
        select(MailMessage.id).where(
            MailMessage.folder_id == folder.id,
            MailMessage.uidvalidity == folder.uidvalidity,
            MailMessage.uid == uid,
        )
    )
    if exists:
        return None
    too_big = raw is None or len(raw) > s.lark_mail_max_message_bytes
    p = mime.parse(raw or b"", max_body=s.lark_mail_max_body_chars) if not too_big else None
    sha = hashlib.sha256(raw or b"").hexdigest() if raw else ""
    if sha:
        # Same email under an older UIDVALIDITY of this folder: re-key it, don't duplicate it.
        old = db.scalar(
            select(MailMessage)
            .where(
                MailMessage.folder_id == folder.id,
                MailMessage.raw_sha256 == sha,
                MailMessage.uidvalidity != folder.uidvalidity,
            )
            .limit(1)
        )
        if old is not None:
            old.uidvalidity, old.uid, old.server_removed_at = folder.uidvalidity, uid, None
            return None
    seen = "\\Seen" in flags
    received = internal_date or (p.sent_at if p and p.sent_at else None) or now()
    if p is None:
        warn = ["too_large"] if raw is not None else ["no_body"]
        m = MailMessage(
            company_id=acc.company_id, account_id=acc.id, folder_id=folder.id,
            uidvalidity=folder.uidvalidity, uid=uid, thread_key=f"uid:{folder.id}:{uid}",
            subject="(message too large to import)", received_at=received, size=size,
            flags=flags, seen=seen, flagged="\\Flagged" in flags, raw_sha256=sha,
            parse_warnings=warn,
        )  # fmt: skip
        db.add(m)
        db.flush()
        return m
    names = " ".join(a.filename for a in p.attachments if not a.inline)
    m = MailMessage(
        company_id=acc.company_id,
        account_id=acc.id,
        folder_id=folder.id,
        uidvalidity=folder.uidvalidity,
        uid=uid,
        message_id_header=p.message_id[:998],
        in_reply_to=p.in_reply_to[:998],
        references=p.references,
        thread_key=_thread_key(db, acc.id, p, f"uid:{folder.id}:{uid}"),
        from_name=p.from_name,
        from_email=p.from_email,
        to_addrs=[a.as_dict() for a in p.to],
        cc_addrs=[a.as_dict() for a in p.cc],
        bcc_addrs=[a.as_dict() for a in p.bcc],
        reply_to=[a.as_dict() for a in p.reply_to],
        subject=p.subject,
        snippet=p.snippet,
        body_text=p.body_text,
        body_html=p.body_html,
        body_truncated=p.truncated,
        has_remote_images=p.remote_images,
        sent_at=p.sent_at,
        received_at=received,
        size=size or len(raw or b""),
        flags=flags,
        seen=seen,
        flagged="\\Flagged" in flags,
        attachment_count=sum(1 for a in p.attachments if not a.inline),
        attachment_names=names[:4000],
        raw_sha256=sha,
        parse_warnings=p.warnings,
        search_tsv=func.to_tsvector(
            "simple",
            " ".join(
                [
                    p.subject,
                    p.from_name,
                    p.from_email,
                    _addr_text(p.to),
                    _addr_text(p.cc),
                    _addr_text(p.bcc),
                    names,
                    p.body_text[:100_000],
                ]
            ),
        ),  # fmt: skip
    )
    db.add(m)
    db.flush()
    for a in p.attachments:
        data = a.data
        stored = len(data) <= s.lark_mail_max_attachment_bytes
        detected = sniff.detect(data, a.declared_mime)
        if clamd.is_blocked(a.filename, detected):
            status, detail = "BLOCKED", "Program or script files are not opened here"
        elif stored:
            status, detail = clamd.scan(data)
        else:
            status, detail = "NOT_SCANNED", "Too large to store"
        att = MailAttachment(
            message_id=m.id, part_id=a.part_id, filename=a.filename,
            declared_mime=a.declared_mime, detected_mime=detected, content_id=a.content_id,
            inline=a.inline, byte_size=len(data), sha256=hashlib.sha256(data).hexdigest(),
            stored=stored and status != "INFECTED", scan_status=status, scan_detail=detail,
        )  # fmt: skip
        db.add(att)
        db.flush()
        if att.stored:
            db.add(MailAttachmentBlob(attachment_id=att.id, content=data))
    if folder.role == "SENT" and p.message_id:
        db.execute(
            update(MailOutgoing)
            .where(
                MailOutgoing.account_id == acc.id,
                MailOutgoing.message_id_header == p.message_id,
                MailOutgoing.seen_in_sent_at.is_(None),
            )
            .values(seen_in_sent_at=now())
        )
    return m


# ------------------------------------------------------------------ the sync


@dataclass
class SyncResult:
    status: str = "OK"
    new: int = 0
    backfilled: int = 0
    folders: int = 0
    error: str | None = None


def _lease(db: Session, account_id: uuid.UUID) -> bool:
    t = now()
    got = db.execute(
        update(MailAccount)
        .where(
            MailAccount.id == account_id,
            or_(MailAccount.sync_lease_until.is_(None), MailAccount.sync_lease_until < t),
        )
        .values(sync_lease_until=t + LEASE, last_sync_attempt_at=t)
    ).rowcount  # type: ignore[attr-defined]
    db.commit()
    return bool(got)


def sync_account(
    account_id: uuid.UUID,
    trigger: str = "beat",
    *,
    sessionmaker: Callable[[], Session] | None = None,
) -> SyncResult:
    sm = sessionmaker or get_sessionmaker()
    res = SyncResult()
    with sm() as db:
        acc = db.get(MailAccount, account_id)
        if acc is None or acc.password_enc is None or acc.status in ("NOT_CONFIGURED", "PAUSED"):
            return SyncResult(status="SKIPPED")
        if not _lease(db, account_id):
            return SyncResult(status="BUSY")
        run = MailSyncRun(account_id=account_id, trigger=trigger)
        db.add(run)
        db.commit()
        run_id = run.id
    try:
        with sm() as db:
            acc = db.get(MailAccount, account_id)
            assert acc is not None and acc.password_enc is not None
            password = unseal(acc.password_enc)
            username = acc.username
            im = imap_for(acc)
        with im:
            im.login(username, password)
            del password
            _sync_folders(sm, account_id, im, res)
        _finish(sm, account_id, run_id, res, None)
    except ImapAuthError as exc:
        res.status, res.error = "AUTH_FAILED", str(exc)
        _finish(sm, account_id, run_id, res, "AUTH_FAILED")
    except (ImapError, SecretUnavailable) as exc:
        res.status, res.error = "ERROR", str(exc)
        _finish(sm, account_id, run_id, res, "ERROR")
    except ForbiddenImapCommand as exc:  # a bug, not a server problem: stop and say so loudly
        log.error("blocked a mailbox-changing IMAP command", extra={"ctx": {"error": str(exc)}})
        res.status, res.error = "ERROR", f"Internal safety stop: {exc}"
        _finish(sm, account_id, run_id, res, "ERROR")
    except Exception as exc:  # noqa: BLE001 - recorded on the run; lease released
        log.exception("mail sync crashed")
        res.status, res.error = "ERROR", f"{type(exc).__name__}"
        _finish(sm, account_id, run_id, res, "ERROR")
    return res


def _finish(
    sm: Callable[[], Session],
    account_id: uuid.UUID,
    run_id: uuid.UUID,
    res: SyncResult,
    failure: str | None,
) -> None:
    with sm() as db:
        acc = db.get(MailAccount, account_id, with_for_update=True)
        run = db.get(MailSyncRun, run_id)
        t = now()
        if run is not None:
            run.finished_at, run.status = t, res.status
            run.new_messages, run.backfilled, run.folders = res.new, res.backfilled, res.folders
            run.error_code = failure
            run.error = (res.error or "")[:1000] or None
        if acc is not None:
            acc.sync_lease_until = None
            if failure is None:
                acc.status = "CONNECTED" if acc.status != "PAUSED" else "PAUSED"
                acc.failure_count, acc.last_sync_at, acc.imap_ok_at = 0, t, t
                acc.last_error_code = acc.last_error = None
                acc.imap_error = None
                acc.next_sync_at = t + timedelta(seconds=acc.sync_interval_seconds)
                _recount(db, acc)
            else:
                acc.failure_count += 1
                acc.last_error_code, acc.last_error, acc.last_error_at = failure, res.error, t
                if failure == "AUTH_FAILED":
                    acc.status, acc.imap_error = "AUTH_FAILED", res.error
                    acc.next_sync_at = None  # waits for a Super Admin
                else:
                    if acc.status != "PAUSED":
                        acc.status = "ERROR"
                    backoff = min(acc.sync_interval_seconds * 2 ** min(acc.failure_count, 6),
                                  MAX_BACKOFF)  # fmt: skip
                    acc.next_sync_at = t + timedelta(seconds=backoff)
        db.commit()


def _recount(db: Session, acc: MailAccount) -> None:
    rows = db.execute(
        select(
            MailMessage.folder_id,
            func.count(),
            func.count().filter(MailMessage.seen.is_(False)),
        )
        .join(MailFolder, MailFolder.id == MailMessage.folder_id)
        .where(
            MailMessage.account_id == acc.id,
            MailMessage.server_removed_at.is_(None),
            MailMessage.uidvalidity == MailFolder.uidvalidity,
        )
        .group_by(MailMessage.folder_id)
    ).all()
    counts = {r[0]: (int(r[1]), int(r[2])) for r in rows}
    total = unread = 0
    for f in db.scalars(select(MailFolder).where(MailFolder.account_id == acc.id)):
        f.message_count, f.unread_count = counts.get(f.id, (0, 0))
        if f.sync_enabled and f.role != "TRASH":
            total += f.message_count
        if f.role == "INBOX":
            unread = f.unread_count
    acc.message_count, acc.unread_count = total, unread


def _sync_folders(
    sm: Callable[[], Session], account_id: uuid.UUID, im: LarkImap, res: SyncResult
) -> None:
    infos = im.list_folders()
    with sm() as db:
        acc = db.get(MailAccount, account_id)
        assert acc is not None
        acc.capabilities = im.capabilities[:100]
        seen_names = set()
        for f in infos:
            seen_names.add(f.name)
            row = db.scalar(
                select(MailFolder).where(
                    MailFolder.account_id == account_id, MailFolder.name == f.name
                )
            )
            role = folder_role(f.name, f.attributes)
            if row is None:
                row = MailFolder(
                    account_id=account_id, name=f.name, raw_name=f.raw_name,
                    display_name=f.name.rsplit(f.delimiter or "/", 1)[-1] if f.delimiter else f.name,  # noqa: E501
                    role=role, sync_enabled=role != "TRASH",
                )  # fmt: skip
                db.add(row)
            row.raw_name, row.attributes, row.selectable, row.gone_at = (
                f.raw_name, f.attributes, f.selectable, None,
            )  # fmt: skip
            if row.role != role:
                row.role = role
        for row in db.scalars(select(MailFolder).where(MailFolder.account_id == account_id)):
            if row.name not in seen_names and row.gone_at is None:
                row.gone_at = now()  # folder no longer on the server: rows are kept
        db.commit()
        folder_ids = [
            r.id
            for r in sorted(
                db.scalars(
                    select(MailFolder).where(
                        MailFolder.account_id == account_id,
                        MailFolder.selectable.is_(True),
                        MailFolder.sync_enabled.is_(True),
                        MailFolder.gone_at.is_(None),
                    )
                ),
                key=lambda r: (ROLE_ORDER.get(r.role, 8), r.name),
            )
        ]
    for fid in folder_ids:
        _sync_folder(sm, account_id, fid, im, res)
        res.folders += 1
    with sm() as db:
        acc = db.get(MailAccount, account_id)
        assert acc is not None
        pending = db.scalar(
            select(func.count()).where(
                MailFolder.account_id == account_id,
                MailFolder.sync_enabled.is_(True),
                MailFolder.selectable.is_(True),
                MailFolder.gone_at.is_(None),
                MailFolder.backfill_done.is_(False),
            )
        )
        if not pending and acc.backfill_done_at is None:
            acc.backfill_done_at = now()
        db.commit()


def _sync_folder(
    sm: Callable[[], Session], account_id: uuid.UUID, folder_id: uuid.UUID, im: LarkImap,
    res: SyncResult,
) -> None:  # fmt: skip
    s = get_settings()
    with sm() as db:
        f = db.get(MailFolder, folder_id)
        assert f is not None
        raw_name = f.raw_name
        ex = im.examine(raw_name)
        if f.uidvalidity != ex.uidvalidity:
            if f.uidvalidity is not None:
                log.warning("UIDVALIDITY changed; re-keying", extra={"ctx": {"folder": f.name}})
            f.uidvalidity = ex.uidvalidity
            all_uids = im.uids(1)
            f.high_uid = max(all_uids, default=0)
            f.backfill_uid = f.high_uid + 1
            f.backfill_done = not all_uids
        f.server_count = ex.exists
        uidvalidity, high = f.uidvalidity, f.high_uid
        db.commit()

    # 1. new mail, oldest first, in batches
    new = im.uids(high + 1)
    for i in range(0, len(new), s.lark_mail_fetch_batch):
        res.new += _import(sm, account_id, folder_id, uidvalidity, im, new[i : i + s.lark_mail_fetch_batch], advance_high=True)  # fmt: skip  # noqa: E501

    # 2. history, newest first, a bounded amount per run (resumable)
    with sm() as db:
        f = db.get(MailFolder, folder_id)
        assert f is not None
        done, low = f.backfill_done, f.backfill_uid
    if not done and low is not None:
        older = [u for u in im.uids(1) if u < low]
        older.sort(reverse=True)
        todo = older[: s.lark_mail_backfill_per_run]
        for i in range(0, len(todo), s.lark_mail_fetch_batch):
            res.backfilled += _import(sm, account_id, folder_id, uidvalidity, im, todo[i : i + s.lark_mail_fetch_batch], advance_high=False)  # fmt: skip  # noqa: E501
        if len(older) <= len(todo):
            with sm() as db:
                f = db.get(MailFolder, folder_id)
                assert f is not None
                f.backfill_done = True
                # Rows of an older UIDVALIDITY that did not come back are gone from the server.
                db.execute(
                    update(MailMessage)
                    .where(
                        MailMessage.folder_id == folder_id,
                        MailMessage.uidvalidity != uidvalidity,
                        MailMessage.server_removed_at.is_(None),
                    )
                    .values(server_removed_at=now())
                )
                db.commit()

    # 3. read / star state of recent mail as Lark sees it (and mail removed in Lark)
    _refresh_flags(sm, folder_id, uidvalidity, im)
    with sm() as db:
        f = db.get(MailFolder, folder_id)
        assert f is not None
        f.last_synced_at = now()
        db.commit()


def _import(
    sm: Callable[[], Session], account_id: uuid.UUID, folder_id: uuid.UUID, uidvalidity: int,
    im: LarkImap, uids: list[int], *, advance_high: bool,
) -> int:  # fmt: skip
    fetched = im.fetch(uids, body=True)
    n = 0
    with sm() as db:
        acc = db.get(MailAccount, account_id)
        f = db.get(MailFolder, folder_id, with_for_update=True)
        assert acc is not None and f is not None
        if f.uidvalidity != uidvalidity:
            return 0
        for m in sorted(fetched, key=lambda x: x.uid):
            with db.begin_nested():
                if store(db, acc, f, m.uid, m.raw, m.flags, m.internal_date, m.size) is not None:
                    n += 1
        got = [m.uid for m in fetched] or uids
        if advance_high:
            f.high_uid = max([f.high_uid, *got])
        else:
            f.backfill_uid = min([f.backfill_uid or (max(uids) + 1), *uids])
        db.commit()
    return n


def _refresh_flags(
    sm: Callable[[], Session], folder_id: uuid.UUID, uidvalidity: int, im: LarkImap
) -> None:
    window = get_settings().lark_mail_flag_window
    if not window:
        return
    with sm() as db:
        f = db.get(MailFolder, folder_id)
        assert f is not None
        high = f.high_uid
    if not high:
        return
    start = max(1, high - window)
    uids = im.uids(start)
    flags = {m.uid: m.flags for m in im.fetch(uids, body=False)} if uids else {}
    with sm() as db:
        rows = db.execute(
            select(
                MailMessage.id,
                MailMessage.uid,
                MailMessage.seen,
                MailMessage.flagged,
                MailMessage.server_removed_at,
            ).where(  # fmt: skip
                MailMessage.folder_id == folder_id,
                MailMessage.uidvalidity == uidvalidity,
                MailMessage.uid >= start,
                MailMessage.uid <= high,
            )
        ).all()
        t = now()
        for mid, uid, seen, flagged, removed in rows:
            fl = flags.get(uid)
            if fl is None:
                if removed is None:
                    db.execute(update(MailMessage).where(MailMessage.id == mid)
                               .values(server_removed_at=t))  # fmt: skip
                continue
            vals: dict[str, Any] = {}
            if ("\\Seen" in fl) != seen:
                vals["seen"] = "\\Seen" in fl
            if ("\\Flagged" in fl) != flagged:
                vals["flagged"] = "\\Flagged" in fl
            if removed is not None:
                vals["server_removed_at"] = None
            if vals:
                vals["flags"] = fl
                db.execute(update(MailMessage).where(MailMessage.id == mid).values(**vals))
        db.commit()


# ------------------------------------------------------------------ scheduling


def due_accounts(db: Session, limit: int = 4) -> list[uuid.UUID]:
    t = now()
    return list(
        db.scalars(
            select(MailAccount.id)
            .where(
                MailAccount.sync_enabled.is_(True),
                MailAccount.status.in_(("CONNECTED", "ERROR")),
                MailAccount.password_enc.is_not(None),
                or_(MailAccount.next_sync_at.is_(None), MailAccount.next_sync_at <= t),
                or_(MailAccount.sync_lease_until.is_(None), MailAccount.sync_lease_until < t),
            )
            .order_by(MailAccount.next_sync_at.asc().nulls_first())
            .limit(limit)
        )
    )


def poll_all() -> dict[str, int]:
    """Beat task body: sync the mailboxes that are due (bounded per tick)."""
    with get_sessionmaker()() as db:
        ids = due_accounts(db)
    out = {"accounts": 0, "new": 0}
    for i in ids:
        r = sync_account(i, "beat")
        out["accounts"] += 1
        out["new"] += r.new + r.backfilled
    return out
