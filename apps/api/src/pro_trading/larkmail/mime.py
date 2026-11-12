"""Raw RFC 822 bytes -> what the Email module stores. Untrusted input: every header is decoded
(RFC 2047, any charset), bounded and cleaned; a broken part produces a warning, never a crash."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage, Message
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from .sanitize import sanitize, tidy

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS = re.compile(r"\s+")
_MSGID = re.compile(r"<[^<>\s]{1,500}>")


def clean(s: object, limit: int) -> str:
    return _WS.sub(" ", _CTRL.sub("", str(s or ""))).strip()[:limit]


@dataclass
class Address:
    name: str
    email: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "email": self.email}


@dataclass
class ParsedAttachment:
    part_id: str
    filename: str
    declared_mime: str
    content_id: str
    inline: bool
    data: bytes


@dataclass
class ParsedMail:
    message_id: str
    in_reply_to: str
    references: list[str]
    from_name: str
    from_email: str
    to: list[Address]
    cc: list[Address]
    bcc: list[Address]
    reply_to: list[Address]
    subject: str
    sent_at: datetime | None
    body_text: str
    body_html: str
    truncated: bool
    remote_images: bool
    raw_sha256: str
    html_text: str = ""  # words of the HTML version, for search when it differs from the text
    attachments: list[ParsedAttachment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def snippet(self) -> str:
        return clean(self.body_text, 200)


def _header(msg: Message, name: str, warnings: list[str]) -> str:
    try:
        v = msg.get(name)
    except Exception:  # noqa: BLE001 - malformed encoded-word
        warnings.append(f"bad_header:{name}")
        raw = msg.get_all(name, failobj=[]) if hasattr(msg, "get_all") else []
        return clean(raw[0] if raw else "", 2000)
    return clean(v if v is not None else "", 4000)


def _addresses(msg: Message, name: str, warnings: list[str]) -> list[Address]:
    try:
        vals = [str(v) for v in msg.get_all(name, failobj=[])]
    except Exception:  # noqa: BLE001
        warnings.append(f"bad_header:{name}")
        return []
    out = []
    for n, a in getaddresses(vals):
        a = clean(a, 320).lower()
        if a and "@" in a:
            out.append(Address(clean(n, 200), a))
    return out[:500]


def _ids(v: str) -> list[str]:
    return _MSGID.findall(v or "")[:100]


def _decode_part(part: Message, warnings: list[str]) -> str:
    try:
        if isinstance(part, EmailMessage):
            return str(part.get_content())
    except (LookupError, UnicodeError, ValueError, AssertionError):
        warnings.append("charset_fallback")
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    cs = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(cs, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


def parse(raw: bytes, *, max_body: int = 400_000, max_parts: int = 200) -> ParsedMail:
    warnings: list[str] = []
    msg = message_from_bytes(raw, policy=policy.default)
    fname, femail = parseaddr(_header(msg, "From", warnings))
    sent_at: datetime | None = None
    date_h = _header(msg, "Date", warnings)
    if date_h:
        try:
            sent_at = parsedate_to_datetime(date_h)
            if sent_at.tzinfo is None:  # no zone given: RFC 5322 says treat as UTC (-0000)
                sent_at = sent_at.replace(tzinfo=UTC)
                warnings.append("date_without_zone")
        except (TypeError, ValueError, IndexError):
            warnings.append("bad_date")
    text_body, html_body = "", ""
    attachments: list[ParsedAttachment] = []
    for n, part in enumerate(msg.walk()):
        if n >= max_parts:
            warnings.append("too_many_parts")
            break
        if part.is_multipart():
            continue
        ctype = part.get_content_type().lower()
        disp = (part.get_content_disposition() or "").lower()
        try:
            filename = part.get_filename() or ""
        except Exception:  # noqa: BLE001
            filename = ""
            warnings.append("bad_filename")
        filename = clean(filename, 255)
        cid = clean(part.get("Content-ID", ""), 300).strip("<>")
        is_body = ctype in ("text/plain", "text/html") and disp != "attachment" and not filename
        if is_body:
            if ctype == "text/plain" and not text_body:
                text_body = _decode_part(part, warnings)
            elif ctype == "text/html" and not html_body:
                html_body = _decode_part(part, warnings)
            continue
        if ctype == "message/rfc822" and not filename:
            filename = "attached-message.eml"
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes):
            data = b""
        if not filename:
            if not cid:
                continue  # an unnamed, unreferenced part (e.g. calendar alternative)
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif"}.get(ctype, "")
            filename = f"inline-{len(attachments) + 1}{ext}"
        attachments.append(
            ParsedAttachment(
                part_id=str(n),
                filename=filename,
                declared_mime=ctype[:128],
                content_id=cid,
                inline=disp == "inline" or (bool(cid) and disp != "attachment"),
                data=data,
            )
        )
    truncated = remote = False
    safe_html = from_html = ""
    if html_body:
        safe_html, from_html, truncated, remote = sanitize(html_body, max_body)
        if not text_body.strip():
            text_body = from_html
    text_body = tidy(text_body)
    if len(text_body) > max_body:
        text_body, truncated = text_body[:max_body], True
    mid = _ids(_header(msg, "Message-ID", warnings))
    irt = _ids(_header(msg, "In-Reply-To", warnings))
    return ParsedMail(
        message_id=mid[0] if mid else "",
        in_reply_to=irt[0] if irt else "",
        references=_ids(_header(msg, "References", warnings)),
        from_name=clean(fname, 200),
        from_email=clean(femail, 320).lower(),
        to=_addresses(msg, "To", warnings),
        cc=_addresses(msg, "Cc", warnings),
        bcc=_addresses(msg, "Bcc", warnings),
        reply_to=_addresses(msg, "Reply-To", warnings),
        subject=clean(_header(msg, "Subject", warnings), 998),
        sent_at=sent_at,
        body_text=text_body,
        body_html=safe_html,
        truncated=truncated,
        remote_images=remote,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        html_text=from_html if html_body else "",
        attachments=attachments,
        warnings=warnings,
    )
