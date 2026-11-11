"""Turn a Gmail API message into what the inbox stores: header fields, body text, HTML reduced
to an allow-list, and the list of named files. Everything here is untrusted input: it is
decoded, bounded and cleaned, never interpreted."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from typing import Any

from .client import b64url_decode

ELIGIBLE_MIME = {"application/pdf", "image/png", "image/jpeg", "image/tiff"}
_EXT_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
# Labels that mean "not incoming mail": drafts, chats, and mail this account only sent.
SKIP_LABELS = {"DRAFT", "CHAT"}

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS = re.compile(r"[ \t\r\f\v]+")


def clean(s: str, limit: int) -> str:
    """Printable, single-spaced, bounded text for header fields."""
    return _WS.sub(" ", _CTRL.sub("", s or "")).strip()[:limit]


def eligible_mime(mime: str, filename: str) -> str | None:
    """The document type this file claims to be, if it is one the reader accepts."""
    m = (mime or "").split(";")[0].strip().lower()
    if m == "image/jpg":
        m = "image/jpeg"
    if m in ELIGIBLE_MIME:
        return m
    name = (filename or "").lower()
    for ext, mapped in _EXT_MIME.items():
        if name.endswith(ext) and m in ("", "application/octet-stream", "binary/octet-stream"):
            return mapped
    return None


# ------------------------------------------------------------------ HTML allow-list

_ALLOWED = {
    "a", "abbr", "b", "blockquote", "br", "caption", "code", "div", "em", "h1", "h2", "h3",
    "h4", "h5", "h6", "hr", "i", "li", "ol", "p", "pre", "small", "span", "strong", "sub",
    "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
}  # fmt: skip
_VOID = {"br", "hr"}
# Dropped with everything inside them.
_DROP = {
    "script", "style", "head", "title", "iframe", "object", "embed", "form", "svg", "math",
    "noscript", "template", "link", "meta", "base", "audio", "video", "canvas", "select",
    "textarea", "button", "input",
}  # fmt: skip
_DROP_VOID = {"link", "meta", "base", "input"}  # nothing to close
_SAFE_HREF = re.compile(r"^(https?://|mailto:)[^\s\"'<>`]{1,2000}$", re.IGNORECASE)
_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "hr"}


class _Sanitizer(HTMLParser):
    """Rebuilds the document from an allow-list: known tags only, no attributes except a
    checked http(s)/mailto link, no images or other remote resources, no scripts or styles."""

    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.text: list[str] = []
        self.size = 0
        self.limit = limit
        self.truncated = False
        self._skip = 0
        self._open: list[str] = []

    def _emit(self, s: str) -> None:
        if self.truncated:
            return
        if self.size + len(s) > self.limit:
            self.truncated = True
            return
        self.out.append(s)
        self.size += len(s)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP:
            if tag not in _DROP_VOID:
                self._skip += 1
            return
        if self._skip:
            return
        if tag == "img":
            alt = clean(dict(attrs).get("alt") or "", 80)
            self._emit(f"<span>[image{': ' + html.escape(alt) if alt else ''}]</span>")
            return
        if tag in _BLOCK:
            self.text.append("\n")
        if tag not in _ALLOWED:
            return
        if tag == "a":
            href = (dict(attrs).get("href") or "").strip()
            if _SAFE_HREF.match(href):
                self._emit(
                    f'<a href="{html.escape(href, quote=True)}" target="_blank" '
                    'rel="noopener noreferrer nofollow">'
                )
            else:
                self._emit("<a>")
        else:
            self._emit(f"<{tag}>")
        if tag not in _VOID:
            self._open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP:
            if tag not in _DROP_VOID:
                self._skip = max(0, self._skip - 1)
            return
        if self._skip or tag not in _ALLOWED or tag in _VOID or tag not in self._open:
            return
        while self._open:
            t = self._open.pop()
            self._emit(f"</{t}>")
            if t == tag:
                break
        if tag in _BLOCK:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        data = _CTRL.sub("", data)
        self._emit(html.escape(data, quote=False))
        self.text.append(data)

    def result(self) -> tuple[str, str]:
        tail = "".join(f"</{t}>" for t in reversed(self._open))
        return "".join(self.out) + tail, "".join(self.text)


def sanitize_html(raw: str, limit: int) -> tuple[str, str, bool]:
    """(safe HTML, plain text, truncated)."""
    p = _Sanitizer(limit)
    try:
        p.feed(raw)
        p.close()
    except Exception:  # noqa: BLE001 - malformed mail must not stop the sync
        return "", "", True
    safe, text = p.result()
    return safe, _tidy(text), p.truncated


def _tidy(text: str) -> str:
    lines = [_WS.sub(" ", ln).strip() for ln in _CTRL.sub("", text).split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


# ------------------------------------------------------------------ message


@dataclass
class ParsedAttachment:
    part_id: str
    gmail_attachment_id: str | None
    filename: str
    mime_type: str
    byte_size: int
    inline: bool
    eligible: bool
    inline_data: str | None = None  # small files arrive inside the message itself


@dataclass
class ParsedMessage:
    gmail_message_id: str
    thread_id: str
    history_id: int | None
    labels: list[str]
    from_name: str
    from_email: str
    to_addr: str
    subject: str
    snippet: str
    body_text: str
    body_html: str
    body_truncated: bool
    received_at: datetime
    attachments: list[ParsedAttachment] = field(default_factory=list)

    @property
    def incoming(self) -> bool:
        ls = set(self.labels)
        return not (ls & SKIP_LABELS) and not ("SENT" in ls and "INBOX" not in ls)


def _headers(part: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in part.get("headers") or []:
        if isinstance(h, dict) and isinstance(h.get("name"), str):
            out.setdefault(h["name"].lower(), str(h.get("value") or ""))
    return out


def _charset(content_type: str) -> str:
    m = re.search(r'charset="?([A-Za-z0-9._:\-]+)"?', content_type or "", re.IGNORECASE)
    return m.group(1) if m else "utf-8"


def _decode(part: dict[str, Any]) -> str:
    raw = b64url_decode(str((part.get("body") or {}).get("data") or ""))
    try:
        return raw.decode(_charset(_headers(part).get("content-type", "")), "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def _walk(part: dict[str, Any], depth: int = 0) -> list[dict[str, Any]]:
    if depth > 20 or not isinstance(part, dict):
        return []
    out = [part]
    for sub in part.get("parts") or []:
        out.extend(_walk(sub, depth + 1))
    return out


def parse_message(msg: dict[str, Any], max_body: int) -> ParsedMessage:
    payload = msg.get("payload") or {}
    top = _headers(payload)
    name, email = parseaddr(top.get("from", ""))
    to = ", ".join(a for _, a in getaddresses([top.get("to", "")]) if a)
    try:
        received = datetime.fromtimestamp(int(msg.get("internalDate") or 0) / 1000, UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        received = datetime.now(UTC)
    try:
        history_id: int | None = int(msg.get("historyId") or 0) or None
    except (TypeError, ValueError):
        history_id = None

    text_body, html_body = "", ""
    attachments: list[ParsedAttachment] = []
    for part in _walk(payload):
        body = part.get("body") or {}
        filename = clean(str(part.get("filename") or ""), 255)
        mime = clean(str(part.get("mimeType") or ""), 128).lower()
        if filename:
            ph = _headers(part)
            disposition = ph.get("content-disposition", "").lower()
            try:
                size = max(0, int(body.get("size") or 0))
            except (TypeError, ValueError):
                size = 0
            attachments.append(
                ParsedAttachment(
                    part_id=clean(str(part.get("partId") or ""), 32) or str(len(attachments)),
                    gmail_attachment_id=str(body["attachmentId"])
                    if body.get("attachmentId")
                    else None,
                    filename=filename,
                    mime_type=mime,
                    byte_size=size,
                    # A picture placed in the message text (a signature logo). Gmail gives real
                    # attachments a Content-ID too, so that header alone says nothing, and a PDF
                    # is never "inside the message".
                    inline=disposition.startswith("inline") and mime.startswith("image/"),
                    eligible=eligible_mime(mime, filename) is not None,
                    inline_data=str(body["data"]) if body.get("data") else None,
                )
            )
            continue
        if mime == "text/plain" and not text_body and body.get("data"):
            text_body = _decode(part)
        elif mime == "text/html" and not html_body and body.get("data"):
            html_body = _decode(part)

    truncated = False
    safe_html = ""
    if html_body:
        safe_html, from_html, truncated = sanitize_html(html_body, max_body)
        if not text_body:
            text_body = from_html
    text_body = _tidy(text_body)
    if len(text_body) > max_body:
        text_body, truncated = text_body[:max_body], True

    return ParsedMessage(
        gmail_message_id=clean(str(msg.get("id") or ""), 32),
        thread_id=clean(str(msg.get("threadId") or ""), 32),
        history_id=history_id,
        labels=[clean(str(x), 64) for x in (msg.get("labelIds") or []) if isinstance(x, str)][:50],
        from_name=clean(name, 200),
        from_email=clean(email, 320).lower(),
        to_addr=clean(to, 1000),
        subject=clean(top.get("subject", ""), 500),
        snippet=clean(html.unescape(str(msg.get("snippet") or "")), 300),
        body_text=text_body,
        body_html=safe_html,
        body_truncated=truncated,
        received_at=received,
        attachments=attachments,
    )
