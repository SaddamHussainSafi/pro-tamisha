"""Compose -> MIME -> queue -> SMTP. One queue row per Send press (idempotency key); the worker
sends QUEUED rows. A failure before Lark could have accepted the message is retried with
backoff (at most MAX_ATTEMPTS); a failure after the data was handed over is UNCERTAIN and waits
for a person (the Sent folder sync then shows whether it went out)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid

from sqlalchemy import or_, select

from ..db import get_sessionmaker
from .models import MailAccount, MailMessage, MailOutgoing
from .sanitize import render, sanitize
from .secrets import SecretUnavailable, unseal
from .smtp import SmtpAuthError, SmtpError, submit
from .sync import smtp_config

log = logging.getLogger("pro_trading.larkmail")
MAX_ATTEMPTS = 4
STALE_SENDING = timedelta(minutes=10)


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class OutFile:
    filename: str
    mime_type: str
    content: bytes


def _fmt(addrs: list[dict[str, str]]) -> str:
    return ", ".join(formataddr((a.get("name") or "", a["email"])) for a in addrs)


def build(
    acc: MailAccount,
    *,
    to: list[dict[str, str]],
    cc: list[dict[str, str]],
    bcc: list[dict[str, str]],
    subject: str,
    body_html: str,
    files: list[OutFile],
    reply_to_msg: MailMessage | None,
) -> tuple[bytes, str]:
    """(raw bytes, Message-ID). Bcc recipients are not written into the headers."""
    domain = acc.address.rsplit("@", 1)[-1]
    mid = make_msgid(domain=domain)
    safe, text, _, _ = sanitize(body_html or "", 2_000_000)
    safe = render(safe, remote_images=True, cid_lookup=lambda _c: None)
    m = EmailMessage()
    m["From"] = formataddr((acc.display_name or "", acc.address))
    if to:
        m["To"] = _fmt(to)
    if cc:
        m["Cc"] = _fmt(cc)
    m["Subject"] = subject
    m["Date"] = format_datetime(now())
    m["Message-ID"] = mid
    if reply_to_msg is not None and reply_to_msg.message_id_header:
        m["In-Reply-To"] = reply_to_msg.message_id_header
        refs = [*reply_to_msg.references, reply_to_msg.message_id_header][-20:]
        m["References"] = " ".join(refs)
    m.set_content(text or " ")
    m.add_alternative(f"<!doctype html><html><body>{safe}</body></html>", subtype="html")
    for f in files:
        main, _, sub = (f.mime_type or "application/octet-stream").partition("/")
        m.add_attachment(f.content, maintype=main or "application", subtype=sub or "octet-stream",
                         filename=f.filename)  # fmt: skip
    return bytes(m), mid


def process_queue(limit: int = 10) -> int:
    """Worker: send due QUEUED rows; mark long-stuck SENDING rows UNCERTAIN."""
    sm = get_sessionmaker()
    t = now()
    with sm() as db:
        stuck = db.scalars(
            select(MailOutgoing).where(
                MailOutgoing.status == "SENDING",
                MailOutgoing.next_attempt_at < t - STALE_SENDING,
            )
        ).all()
        for o in stuck:
            o.status = "UNCERTAIN"
            o.last_error = "The send was interrupted; check the Sent folder before sending again"
        db.commit()
        ids = list(
            db.scalars(
                select(MailOutgoing.id)
                .where(
                    MailOutgoing.status == "QUEUED",
                    or_(MailOutgoing.next_attempt_at.is_(None), MailOutgoing.next_attempt_at <= t),
                )
                .order_by(MailOutgoing.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for i in ids:
            row = db.get(MailOutgoing, i)
            assert row is not None
            row.status, row.attempts, row.next_attempt_at = "SENDING", row.attempts + 1, t
        db.commit()
    sent = 0
    for i in ids:
        sent += send_one(sm, i)
    return sent


def send_one(sm, outgoing_id: uuid.UUID) -> int:  # type: ignore[no-untyped-def]
    with sm() as db:
        o = db.get(MailOutgoing, outgoing_id)
        acc = db.get(MailAccount, o.account_id) if o else None
        if o is None or acc is None or o.status != "SENDING":
            return 0
        try:
            cfg = smtp_config(acc, unseal(acc.password_enc or b""))
        except SecretUnavailable as exc:
            o.status, o.last_error = "FAILED", str(exc)
            db.commit()
            return 0
        rcpts = [a["email"] for a in [*o.to_addrs, *o.cc_addrs, *o.bcc_addrs]]
        raw, from_addr, attempts = o.raw_mime, o.from_addr, o.attempts
    try:
        resp = submit(cfg, from_addr, rcpts, raw)
        status, err, retry = "SENT", None, False
    except SmtpAuthError as exc:
        status, err, retry, resp = "FAILED", str(exc), False, None
    except SmtpError as exc:
        resp = None
        if exc.uncertain:
            status, err, retry = "UNCERTAIN", str(exc), False
        elif exc.permanent or attempts >= MAX_ATTEMPTS:
            status, err, retry = "FAILED", str(exc), False
        else:
            status, err, retry = "QUEUED", str(exc), True
    with sm() as db:
        o = db.get(MailOutgoing, outgoing_id)
        assert o is not None
        o.status, o.last_error, o.smtp_response = status, err, resp
        if status == "SENT":
            o.sent_at = now()
        if retry:
            o.next_attempt_at = now() + timedelta(seconds=30 * 2**o.attempts)
        acc = db.get(MailAccount, o.account_id)
        if acc is not None:
            if status == "SENT":
                acc.smtp_ok_at, acc.smtp_error = now(), None
                # Lark files its own copy in Sent: fetch it on the next beat (~20 s), not
                # after the mailbox's normal interval.
                if acc.sync_enabled and acc.status in ("CONNECTED", "ERROR"):
                    acc.next_sync_at = now()
            elif err:
                acc.smtp_error = err
        db.commit()
    return 1 if status == "SENT" else 0
