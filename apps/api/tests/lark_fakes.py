"""Local stand-ins for Lark's IMAP and SMTP servers (plain TCP on 127.0.0.1, test only).

They speak enough real protocol for Python's imaplib / smtplib to talk to them, record every
command they receive, and refuse anything that would change a mailbox, so a test can prove the
adapter never sends one. Nothing here touches the network beyond loopback.
"""

from __future__ import annotations

import re
import socket
import socketserver
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MUTATING = {"STORE", "COPY", "MOVE", "EXPUNGE", "DELETE", "APPEND", "SELECT", "CREATE",
            "RENAME", "UID STORE", "UID COPY", "UID MOVE", "UID EXPUNGE"}  # fmt: skip


@dataclass
class FakeMessage:
    uid: int
    raw: bytes
    flags: set[str] = field(default_factory=set)
    internal_date: datetime = field(
        default_factory=lambda: datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
    )


@dataclass
class FakeFolder:
    name: str
    uidvalidity: int = 1000
    attrs: list[str] = field(default_factory=list)
    messages: list[FakeMessage] = field(default_factory=list)

    def add(self, raw: bytes, flags: set[str] | None = None, when: datetime | None = None) -> int:
        uid = max([m.uid for m in self.messages], default=0) + 1
        m = FakeMessage(uid, raw, set(flags or set()))
        if when:
            m.internal_date = when
        self.messages.append(m)
        return uid


@dataclass
class FakeMailbox:
    username: str
    password: str
    folders: dict[str, FakeFolder] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    drop_after_fetches: int | None = None  # simulate a disconnect mid-import
    fetches: int = 0


_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|(\(.*\))|(\S+)')


def _args(s: str) -> list[str]:
    out = []
    for q, paren, atom in _TOKEN.findall(s):
        if q or (not paren and not atom):
            out.append(q.replace('\\"', '"').replace("\\\\", "\\"))
        else:
            out.append(paren or atom)
    return out


def _uidset(spec: str, all_uids: list[int]) -> list[int]:
    out: list[int] = []
    top = max(all_uids, default=0)
    for part in spec.split(","):
        if ":" in part:
            a, b = part.split(":")
            lo = int(a)
            hi = top if b == "*" else int(b)
            lo, hi = min(lo, hi), max(lo, hi)
            out.extend(u for u in all_uids if lo <= u <= hi)
            if b == "*" and all_uids and not any(lo <= u <= hi for u in all_uids):
                out.append(top)  # RFC 3501: "N:*" always includes the last message
        else:
            n = int(part)
            if n in all_uids:
                out.append(n)
    return sorted(set(out))


class _ImapHandler(socketserver.StreamRequestHandler):
    server: _ImapServer

    def send(self, s: str | bytes) -> None:
        self.wfile.write(s if isinstance(s, bytes) else s.encode())

    def handle(self) -> None:
        box = self.server.box
        sel: FakeFolder | None = None
        authed = False
        self.send("* OK [CAPABILITY IMAP4rev1 IDLE UIDPLUS] Fake Lark IMAP ready\r\n")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            tag, _, rest = text.partition(" ")
            cmd, _, argstr = rest.partition(" ")
            cmd = cmd.upper()
            full = cmd
            if cmd == "UID":
                sub, _, argstr = argstr.partition(" ")
                full = f"UID {sub.upper()}"
            box.commands.append(full if cmd != "LOGIN" else "LOGIN")
            if full in MUTATING:
                self.send(f"{tag} NO [CANNOT] forbidden in this test server\r\n")
                continue
            if cmd == "CAPABILITY":
                self.send("* CAPABILITY IMAP4rev1 IDLE UIDPLUS\r\n" + f"{tag} OK done\r\n")
            elif cmd == "LOGIN":
                a = _args(argstr)
                if len(a) == 2 and a[0] == box.username and a[1] == box.password:
                    authed = True
                    self.send(f"{tag} OK [CAPABILITY IMAP4rev1 IDLE UIDPLUS] logged in\r\n")
                else:
                    self.send(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n")
            elif not authed and cmd not in ("LOGOUT", "NOOP"):
                self.send(f"{tag} NO not authenticated\r\n")
            elif cmd == "LIST":
                for f in box.folders.values():
                    attrs = " ".join(["\\HasNoChildren", *f.attrs])
                    self.send(f'* LIST ({attrs}) "/" "{f.name}"\r\n')
                self.send(f"{tag} OK LIST done\r\n")
            elif cmd == "EXAMINE":
                name = _args(argstr)[0]
                sel = box.folders.get(name)
                if sel is None:
                    self.send(f"{tag} NO no such folder\r\n")
                    continue
                nxt = max([m.uid for m in sel.messages], default=0) + 1
                self.send(
                    f"* {len(sel.messages)} EXISTS\r\n* 0 RECENT\r\n"
                    f"* OK [UIDVALIDITY {sel.uidvalidity}] ok\r\n* OK [UIDNEXT {nxt}] ok\r\n"
                    f"* FLAGS (\\Seen \\Flagged)\r\n{tag} OK [READ-ONLY] EXAMINE done\r\n"
                )
            elif full == "UID SEARCH":
                assert sel is not None
                m = re.search(r"UID (\S+)", argstr)
                uids = [x.uid for x in sel.messages]
                got = _uidset(m.group(1), uids) if m else uids
                self.send(f"* SEARCH {' '.join(map(str, got))}\r\n{tag} OK SEARCH done\r\n")
            elif full == "UID FETCH":
                assert sel is not None
                spec, _, items = argstr.partition(" ")
                box.fetches += 1
                if box.drop_after_fetches is not None and box.fetches > box.drop_after_fetches:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                want_body = "BODY.PEEK[]" in items.upper()
                byuid = {x.uid: (i + 1, x) for i, x in enumerate(sel.messages)}
                for u in _uidset(spec, list(byuid)):
                    seq, msg = byuid[u]
                    flags = " ".join(sorted(msg.flags))
                    idate = msg.internal_date.strftime("%d-%b-%Y %H:%M:%S +0000")
                    head = (
                        f'* {seq} FETCH (UID {u} FLAGS ({flags}) INTERNALDATE "{idate}" '
                        f"RFC822.SIZE {len(msg.raw)}"
                    )
                    if want_body:
                        self.send(
                            f"{head} BODY[] {{{len(msg.raw)}}}\r\n".encode() + msg.raw + b")\r\n"
                        )
                    else:
                        self.send(head + ")\r\n")
                self.send(f"{tag} OK FETCH done\r\n")
            elif cmd == "NOOP":
                self.send(f"{tag} OK NOOP\r\n")
            elif cmd == "LOGOUT":
                self.send(f"* BYE bye\r\n{tag} OK LOGOUT\r\n")
                return
            else:
                self.send(f"{tag} BAD unknown command\r\n")


class _ImapServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, box: FakeMailbox) -> None:
        super().__init__(("127.0.0.1", 0), _ImapHandler)
        self.box = box


@dataclass
class SentMail:
    mail_from: str
    rcpts: list[str]
    data: bytes


@dataclass
class FakeSmtpState:
    username: str
    password: str
    sent: list[SentMail] = field(default_factory=list)
    logins: int = 0
    drop_during_data: bool = False
    refuse_mail_from: bool = False


class _SmtpHandler(socketserver.StreamRequestHandler):
    server: _SmtpServer

    def send(self, s: str) -> None:
        self.wfile.write(s.encode())

    def handle(self) -> None:
        import base64

        st = self.server.state
        authed = False
        mail_from, rcpts = "", []
        self.send("220 fake-lark ESMTP\r\n")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            up = text.upper()
            if up.startswith(("EHLO", "HELO")):
                self.send("250-fake-lark\r\n250-AUTH PLAIN LOGIN\r\n250 8BITMIME\r\n")
            elif up.startswith("AUTH PLAIN"):
                parts = text.split(" ")
                if len(parts) < 3:
                    self.send("334 \r\n")
                    parts.append(self.rfile.readline().decode().strip())
                raw = base64.b64decode(parts[2]).split(b"\0")
                st.logins += 1
                if raw[1].decode() == st.username and raw[2].decode() == st.password:
                    authed = True
                    self.send("235 2.7.0 ok\r\n")
                else:
                    self.send("535 5.7.8 bad credentials\r\n")
            elif up.startswith("AUTH LOGIN"):
                self.send("334 VXNlcm5hbWU6\r\n")
                u = base64.b64decode(self.rfile.readline().strip()).decode()
                self.send("334 UGFzc3dvcmQ6\r\n")
                pw = base64.b64decode(self.rfile.readline().strip()).decode()
                st.logins += 1
                authed = u == st.username and pw == st.password
                self.send("235 ok\r\n" if authed else "535 bad credentials\r\n")
            elif up.startswith("MAIL FROM"):
                if not authed or st.refuse_mail_from:
                    self.send("550 not allowed\r\n")
                else:
                    mail_from, rcpts = text.split(":", 1)[1].strip(" <>").split(">")[0], []
                    self.send("250 ok\r\n")
            elif up.startswith("RCPT TO"):
                rcpts.append(text.split(":", 1)[1].strip(" <>").split(">")[0])
                self.send("250 ok\r\n")
            elif up == "DATA":
                self.send("354 go\r\n")
                buf = b""
                while True:
                    ln = self.rfile.readline()
                    if not ln:
                        return
                    if ln in (b".\r\n", b".\n"):
                        break
                    buf += ln[1:] if ln.startswith(b"..") else ln
                if st.drop_during_data:
                    st.sent.append(SentMail(mail_from, rcpts, buf))  # it did arrive
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                st.sent.append(SentMail(mail_from, rcpts, buf))
                self.send("250 2.0.0 queued as FAKE1\r\n")
            elif up in ("NOOP", "RSET"):
                self.send("250 ok\r\n")
            elif up == "QUIT":
                self.send("221 bye\r\n")
                return
            else:
                self.send("502 not implemented\r\n")


class _SmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, state: FakeSmtpState) -> None:
        super().__init__(("127.0.0.1", 0), _SmtpHandler)
        self.state = state


class Running:
    def __init__(self, server: socketserver.TCPServer) -> None:
        self.server = server
        self.port = server.server_address[1]
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def start_imap(box: FakeMailbox) -> Running:
    return Running(_ImapServer(box))


def start_smtp(state: FakeSmtpState) -> Running:
    return Running(_SmtpServer(state))


def make_mail(
    *,
    subject: str = "Order for next week",
    sender: str = "Buyer Example <buyer@customer.example>",
    to: str = "andy@tamishatrading.com",
    body: str = "Please send 10 cases.",
    html: str | None = None,
    msgid: str | None = None,
    in_reply_to: str | None = None,
    attachment: tuple[str, str, bytes] | None = None,
    date: str = "Sun, 01 Nov 2026 09:30:00 -0500",
    extra: dict[str, Any] | None = None,
) -> bytes:
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = sender, to, subject, date
    m["Message-ID"] = msgid or f"<{abs(hash((subject, body, date)))}@customer.example>"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    for k, v in (extra or {}).items():
        m[k] = v
    m.set_content(body)
    if html is not None:
        m.add_alternative(html, subtype="html")
    if attachment:
        name, mime, data = attachment
        main, sub = mime.split("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return bytes(m)
