"""Read-only IMAP adapter for Lark Mail.

Non-destructive by construction: every command imaplib is about to send passes `_guard` first.
Only commands that cannot change the mailbox are allowed (folders are opened with EXAMINE, the
read-only form of SELECT; bodies are fetched with BODY.PEEK so nothing is marked read). STORE,
COPY, MOVE, EXPUNGE, DELETE, APPEND, SELECT, CREATE, RENAME and everything else raise
`ForbiddenImapCommand` before a byte reaches the server. tests/test_lark_mail.py proves it.
"""

from __future__ import annotations

import imaplib
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

ALLOWED = frozenset(
    {"CAPABILITY", "LOGIN", "LIST", "EXAMINE", "STATUS", "NOOP", "LOGOUT", "UID", "NAMESPACE"}
)
ALLOWED_UID = frozenset({"FETCH", "SEARCH"})
# Fetch items that would set \Seen (BODY[...] without .PEEK, RFC822, RFC822.TEXT, BINARY[...]).
_MARKS_SEEN = re.compile(
    r"(?<![.\w])BODY\[|(?<![.\w])BINARY\[|\bRFC822(?!\.(?:SIZE|HEADER))\b", re.IGNORECASE
)


class ForbiddenImapCommand(RuntimeError):  # noqa: N818
    """A command that could change the mailbox was about to be sent. Never caught silently."""


class ImapAuthError(RuntimeError):
    pass


class ImapError(RuntimeError):
    pass


def _guard(name: str, args: tuple[Any, ...]) -> None:
    name = name.upper()
    if name not in ALLOWED:
        raise ForbiddenImapCommand(f"IMAP {name} is not allowed (read-only mailbox access)")
    if name == "UID":
        sub = str(args[0] if args else "").upper()
        if sub not in ALLOWED_UID:
            raise ForbiddenImapCommand(f"IMAP UID {sub} is not allowed (read-only)")
        if sub == "FETCH" and _MARKS_SEEN.search(" ".join(str(a) for a in args[2:])):
            raise ForbiddenImapCommand("IMAP FETCH must use BODY.PEEK (would mark read)")


class _Guarded:
    """Mixin placed before imaplib.IMAP4 in the MRO."""

    def _command(self, name: str, *args: Any) -> Any:
        _guard(name, args)
        return super()._command(name, *args)  # type: ignore[misc]


class GuardedIMAP4(_Guarded, imaplib.IMAP4):
    pass


class GuardedIMAP4_SSL(_Guarded, imaplib.IMAP4_SSL):  # noqa: N801
    pass


# ------------------------------------------------------------------ modified UTF-7 (RFC 3501)


def decode_folder(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        c = raw[i]
        if c == "&":
            j = raw.find("-", i)
            if j < 0:
                return raw
            chunk = raw[i + 1 : j]
            if not chunk:
                out.append("&")
            else:
                import base64

                b = chunk.replace(",", "/")
                b += "=" * (-len(b) % 4)
                try:
                    out.append(base64.b64decode(b).decode("utf-16-be"))
                except (ValueError, UnicodeDecodeError):
                    return raw
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def quote(raw: str) -> str:
    return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ------------------------------------------------------------------ responses

_LIST = re.compile(rb'^\((?P<attrs>[^)]*)\)\s+(?P<delim>"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$')
_UID = re.compile(rb"\bUID (\d+)")
_FLAGS = re.compile(rb"\bFLAGS \(([^)]*)\)")
_IDATE = re.compile(rb'\bINTERNALDATE "([^"]+)"')
_SIZE = re.compile(rb"\bRFC822\.SIZE (\d+)")
_NEW_FETCH = re.compile(rb"^\d+ \(")


@dataclass
class FolderInfo:
    raw_name: str
    name: str
    delimiter: str
    attributes: list[str]

    @property
    def selectable(self) -> bool:
        return not any(a.lower() in ("\\noselect", "\\nonexistent") for a in self.attributes)


@dataclass
class Fetched:
    uid: int
    flags: list[str]
    internal_date: datetime | None
    size: int
    raw: bytes | None


def _text(v: Any) -> str:
    return v.decode("ascii", "replace") if isinstance(v, bytes) else str(v or "")


def _unquote(b: bytes) -> str:
    s = b.decode("utf-8", "replace").strip()
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return s


def parse_list(lines: list[Any]) -> list[FolderInfo]:
    out: list[FolderInfo] = []
    pending: bytes | None = None
    for item in lines:
        if item is None:
            continue
        if isinstance(item, tuple):  # name sent as a literal
            pending = item[0].rsplit(b"{", 1)[0] + b'"' + item[1] + b'"'
            line = pending
        else:
            line = item
        m = _LIST.match(line.strip())
        if not m:
            continue
        delim = m.group("delim")
        raw = _unquote(m.group("name"))
        out.append(
            FolderInfo(
                raw_name=raw,
                name=decode_folder(raw),
                delimiter="" if delim == b"NIL" else _unquote(delim),
                attributes=[a for a in m.group("attrs").decode("ascii", "replace").split() if a],
            )
        )
    return out


def parse_fetch(data: list[Any]) -> list[Fetched]:
    msgs: list[tuple[bytes, bytes | None]] = []
    for item in data:
        if isinstance(item, tuple):
            msgs.append((item[0], item[1]))
        elif isinstance(item, bytes) and _NEW_FETCH.match(item):
            msgs.append((item, None))  # a whole "N (UID .. FLAGS ..)" line without a literal
        elif isinstance(item, bytes) and msgs and item.strip() not in (b")", b""):
            meta, lit = msgs[-1]  # the rest of a response that carried a literal
            msgs[-1] = (meta + b" " + item, lit)
    out = []
    for meta, lit in msgs:
        u = _UID.search(meta)
        if not u:
            continue
        f = _FLAGS.search(meta)
        d = _IDATE.search(meta)
        s = _SIZE.search(meta)
        idate = None
        if d:
            try:
                idate = datetime.strptime(d.group(1).decode(), "%d-%b-%Y %H:%M:%S %z").astimezone(
                    UTC
                )
            except ValueError:
                idate = None
        out.append(
            Fetched(
                uid=int(u.group(1)),
                flags=f.group(1).decode("ascii", "replace").split() if f else [],
                internal_date=idate,
                size=int(s.group(1)) if s else (len(lit) if lit else 0),
                raw=lit if lit is not None and b"BODY[" in meta.upper() else None,
            )
        )
    return out


# ------------------------------------------------------------------ client


@dataclass
class Examined:
    uidvalidity: int
    uidnext: int | None
    exists: int


class LarkImap:
    """One read-only session. Use as a context manager."""

    def __init__(self, host: str, port: int, *, use_ssl: bool = True, timeout: float = 30) -> None:
        self.host, self.port, self.use_ssl, self.timeout = host, port, use_ssl, timeout
        self.conn: imaplib.IMAP4 | None = None
        self.capabilities: list[str] = []

    def __enter__(self) -> LarkImap:
        try:
            if self.use_ssl:
                ctx = ssl.create_default_context()
                self.conn = GuardedIMAP4_SSL(
                    self.host, self.port, ssl_context=ctx, timeout=self.timeout
                )
            else:
                self.conn = GuardedIMAP4(self.host, self.port, timeout=self.timeout)
        except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
            raise ImapError(
                f"Could not reach {self.host}:{self.port} ({type(exc).__name__})"
            ) from None
        self.capabilities = [str(c) for c in self.conn.capabilities]
        return self

    def __exit__(self, *_: Any) -> None:
        if self.conn is not None:
            try:
                self.conn.logout()
            except (OSError, imaplib.IMAP4.error, ForbiddenImapCommand):
                pass
            self.conn = None

    @property
    def c(self) -> imaplib.IMAP4:
        assert self.conn is not None
        return self.conn

    def _ok(self, typ: str, data: Any, what: str) -> Any:
        if typ != "OK":
            detail = (
                data[0].decode("utf-8", "replace")[:200]
                if data and isinstance(data[0], bytes)
                else ""
            )
            raise ImapError(f"{what} refused by the server: {detail}")
        return data

    def login(self, username: str, password: str) -> None:
        try:
            typ, data = self.c.login(username, password)
        except imaplib.IMAP4.error as exc:
            # The server's words only; the password is never part of the message.
            raise ImapAuthError(str(exc).replace(password, "***")[:200]) from None
        except (TimeoutError, OSError) as exc:
            raise ImapError(f"Connection lost during login ({type(exc).__name__})") from None
        self._ok(typ, data, "Login")
        try:
            ctyp, cdata = self.c.capability()
            if ctyp == "OK" and cdata and isinstance(cdata[0], bytes):
                self.capabilities = cdata[0].decode("ascii", "replace").upper().split()
        except imaplib.IMAP4.error:
            pass

    def _call(self, what: str, fn: Any, *args: Any) -> Any:
        try:
            typ, data = fn(*args)
        except (TimeoutError, OSError, imaplib.IMAP4.abort) as exc:
            raise ImapError(f"Connection lost during {what} ({type(exc).__name__})") from None
        except imaplib.IMAP4.error as exc:
            raise ImapError(f"{what} failed: {str(exc)[:200]}") from None
        return self._ok(typ, data, what)

    def list_folders(self) -> list[FolderInfo]:
        return parse_list(self._call("LIST", self.c.list, '""', "*"))

    def examine(self, raw_name: str) -> Examined:
        data = self._call("EXAMINE", self.c.select, quote(raw_name), True)
        exists = int(data[0]) if data and data[0] and data[0].isdigit() else 0
        uv = self.c.untagged_responses.get("UIDVALIDITY", [b"0"])
        un = self.c.untagged_responses.get("UIDNEXT", [None])
        return Examined(
            uidvalidity=int(str(_text(uv[-1]) or 0)),
            uidnext=int(str(_text(un[-1]))) if un and un[-1] else None,
            exists=exists,
        )

    def uids(self, start: int = 1) -> list[int]:
        data = self._call("UID SEARCH", self.c.uid, "SEARCH", None, f"UID {start}:*")
        nums = [int(x) for x in (data[0] or b"").split() if x.isdigit()]
        # "N:*" always returns the last message even when its UID < N (RFC 3501): drop it.
        return sorted(n for n in nums if n >= start)

    def fetch(self, uids: list[int], *, body: bool) -> list[Fetched]:
        if not uids:
            return []
        items = "(UID FLAGS INTERNALDATE RFC822.SIZE" + (" BODY.PEEK[])" if body else ")")
        data = self._call("UID FETCH", self.c.uid, "FETCH", ",".join(map(str, uids)), items)
        return parse_fetch(data)

    def noop(self) -> None:
        self._call("NOOP", self.c.noop)
