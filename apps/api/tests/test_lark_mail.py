"""Email module (Lark Mail) against local IMAP/SMTP stand-ins (tests/lark_fakes.py).

No real Lark server, password or email is used: these prove the adapter's behaviour (read-only
IMAP, exactly-once sending, mailbox isolation), not that Lark itself accepts it. The real check
is the Super Admin's "Test connection" with the real app password.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select

from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.larkmail import imap as limap
from pro_trading.larkmail import mime, outgoing
from pro_trading.larkmail import sync as lsync
from pro_trading.larkmail.models import (
    MailAccount,
    MailAttachment,
    MailFolder,
    MailMessage,
    MailOutgoing,
)
from pro_trading.larkmail.sanitize import sanitize
from pro_trading.models import AuditEvent, Employee, EmployeeRole, Role
from pro_trading.security import hash_password

from .conftest import PASSWORD, idem
from .lark_fakes import (
    MUTATING,
    FakeFolder,
    FakeMailbox,
    FakeSmtpState,
    make_mail,
    start_imap,
    start_smtp,
)

APP_PW = "synthetic-app-code-123"  # a made-up test value, not a Lark code
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


# ------------------------------------------------------------------ fixtures


def add_emp(world: Any, name: str, role: str) -> tuple[str, uuid.UUID]:
    """A company-wide user (the real company's Admins and Super Admins are company-wide)."""
    with get_sessionmaker()() as db, db.begin():
        r = db.scalar(select(Role).where(Role.company_id == world.company_id, Role.code == role))
        email = f"{name}.{uuid.uuid4().hex[:6]}@example.test"
        e = Employee(company_id=world.company_id, email=email, display_name=name,
                     password_hash=hash_password(PASSWORD))  # fmt: skip
        db.add(e)
        db.flush()
        db.add(EmployeeRole(employee_id=e.id, role_id=r.id))
        return email, e.id


@pytest.fixture
def lark(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    box = FakeMailbox(username="andy@tamishatrading.com", password=APP_PW)
    inbox = box.folders.setdefault("INBOX", FakeFolder("INBOX", uidvalidity=7001))
    sent = box.folders.setdefault("Sent", FakeFolder("Sent", uidvalidity=7002, attrs=["\\Sent"]))
    box.folders["Trash"] = FakeFolder("Trash", uidvalidity=7003, attrs=["\\Trash"])
    box.folders["Trash"].add(make_mail(subject="old deleted thing"))
    first = make_mail(
        subject="Order for next week",
        msgid="<order-1@customer.example>",
        html="<p>Hello <b>Andy</b></p><script>alert(1)</script>"
        '<img src="https://tracker.example/pixel.gif" onerror="x()">'
        '<a href="javascript:alert(1)">bad</a>',
        attachment=("PO-5521.pdf", "application/pdf", PDF),
    )
    inbox.add(first, when=datetime(2026, 11, 1, 14, 0, tzinfo=UTC))
    inbox.add(
        make_mail(subject="=?utf-8?b?Q2Fmw6kgb3JkZXIg4oCU?=", sender="=?utf-8?q?Jos=C3=A9?= <jose@x.example>",  # noqa: E501
                  body="Invoice TT2026-000123 is attached", msgid="<cafe-1@x.example>"),
        flags={"\\Seen"},
        when=datetime(2026, 11, 2, 14, 0, tzinfo=UTC),
    )  # fmt: skip
    inbox.add(make_mail(subject="Danger", msgid="<exe@x.example>",
                        attachment=("setup.exe", "application/octet-stream", b"MZ\x90\x00rest")),
              when=datetime(2026, 11, 3, 14, 0, tzinfo=UTC))  # fmt: skip
    sent.add(
        make_mail(subject="Re: Order for next week", sender="Andy <andy@tamishatrading.com>",
                  to="buyer@customer.example", in_reply_to="<order-1@customer.example>",
                  msgid="<reply-1@tamishatrading.com>"),
        flags={"\\Seen"},
        when=datetime(2026, 11, 1, 15, 0, tzinfo=UTC),
    )  # fmt: skip
    smtp = FakeSmtpState(username="andy@tamishatrading.com", password=APP_PW)
    i, s = start_imap(box), start_smtp(smtp)
    for k, v in {
        "LARK_MAIL_ALLOW_PLAINTEXT": "true",
        "LARK_IMAP_HOST": "127.0.0.1",
        "LARK_IMAP_PORT": str(i.port),
        "LARK_SMTP_HOST": "127.0.0.1",
        "LARK_SMTP_PORT": str(s.port),
        "LARK_MAIL_TIMEOUT_SECONDS": "5",
        "LARK_MAIL_FETCH_BATCH": "2",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield {"box": box, "smtp": smtp, "inbox": inbox, "sent": sent}
    i.stop()
    s.stop()
    get_settings.cache_clear()


def connect_andy(make_api: Any, world: Any) -> dict[str, Any]:
    owner = make_api()
    assert owner.login(world.emails["owner"]).status_code == 200
    andy_email, andy_id = add_emp(world, "Andy Narine", "MANAGER")
    r = owner.post("/api/v1/mail/admin/accounts", json={
        "address": "andy@tamishatrading.com", "display_name": "Andy Narine",
        "label": "Andy Mail", "user_ids": [str(andy_id)],
    })  # fmt: skip
    assert r.status_code == 201, r.text
    acc = r.json()
    assert acc["status"] == "NOT_CONFIGURED" and not acc["has_password"]
    r = owner.post(
        f"/api/v1/mail/admin/accounts/{acc['id']}/credentials", json={"password": APP_PW}
    )
    assert r.status_code == 200, r.text
    assert r.json()["saved"] and r.json()["imap_ok"] and r.json()["smtp_ok"]
    andy = make_api()
    assert andy.login(andy_email).status_code == 200
    return {"owner": owner, "andy": andy, "acc": acc["id"], "andy_id": andy_id}


def sync(acc_id: str) -> lsync.SyncResult:
    return lsync.sync_account(uuid.UUID(acc_id), "test")


# ------------------------------------------------------------------ the read-only guarantee


def test_guard_refuses_every_mailbox_changing_command(lark: dict[str, Any]) -> None:
    box = lark["box"]
    s = get_settings()
    conn = limap.GuardedIMAP4("127.0.0.1", s.lark_imap_port, timeout=5)
    conn.login(box.username, box.password)
    conn.select('"INBOX"', readonly=True)  # EXAMINE: allowed
    attempts = [
        lambda: conn.select('"INBOX"'),  # SELECT (read-write)
        lambda: conn.store("1", "+FLAGS", "(\\Seen)"),
        lambda: conn.uid("STORE", "1", "+FLAGS", "(\\Deleted)"),
        lambda: conn.expunge(),
        lambda: conn.uid("EXPUNGE", "1"),
        lambda: conn.copy("1", '"Trash"'),
        lambda: conn.uid("COPY", "1", '"Trash"'),
        lambda: conn.uid("MOVE", "1", '"Trash"'),
        lambda: conn.delete('"INBOX"'),
        lambda: conn.create('"New"'),
        lambda: conn.rename('"INBOX"', '"X"'),
        lambda: conn.append('"INBOX"', None, None, b"Subject: x\r\n\r\nx"),
        lambda: conn.uid("FETCH", "1", "(BODY[])"),  # would mark read
        lambda: conn.uid("FETCH", "1", "(RFC822)"),
        lambda: conn.uid("FETCH", "1", "(BODY[TEXT])"),
        lambda: conn.fetch("1", "(BODY.PEEK[])"),  # non-UID FETCH is not on the list either
    ]
    for fn in attempts:
        with pytest.raises(limap.ForbiddenImapCommand):
            fn()
    conn.uid("FETCH", "1", "(UID FLAGS BODY.PEEK[])")  # allowed
    conn.logout()
    sent = set(box.commands)
    assert not (sent & MUTATING), sent
    assert "FETCH" not in sent


def test_sync_imports_inbox_and_sent_without_changing_the_mailbox(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    flags_before = {
        (f, m.uid): set(m.flags) for f, fo in lark["box"].folders.items() for m in fo.messages
    }
    r = sync(w["acc"])
    assert r.status == "OK", r.error
    assert r.new + r.backfilled == 4  # 3 inbox + 1 sent; Trash is not imported
    again = sync(w["acc"])
    assert again.status == "OK" and again.new == 0 and again.backfilled == 0
    with get_sessionmaker()() as db:
        n = db.scalar(select(func.count()).where(MailMessage.account_id == uuid.UUID(w["acc"])))
        assert n == 4  # no duplicates after a second sync
        acc = db.get(MailAccount, uuid.UUID(w["acc"]))
        assert acc.status == "CONNECTED" and acc.backfill_done_at is not None
        assert acc.message_count == 4 and acc.unread_count == 2
        roles = set(db.scalars(select(MailFolder.role).where(MailFolder.account_id == acc.id)))
        assert {"INBOX", "SENT", "TRASH"} <= roles
    # The mailbox on the server is exactly as it was: no flag changes, nothing removed.
    assert {
        (f, m.uid): set(m.flags) for f, fo in lark["box"].folders.items() for m in fo.messages
    } == flags_before
    assert not (set(lark["box"].commands) & MUTATING)
    assert "EXAMINE" in lark["box"].commands and "SELECT" not in lark["box"].commands


def test_andy_reads_his_mail_safely(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    sync(w["acc"])
    andy = w["andy"]
    accs = andy.get("/api/v1/mail/accounts").json()
    assert [a["address"] for a in accs] == ["andy@tamishatrading.com"]
    assert accs[0]["can_send"] and accs[0]["unread"] == 2
    folders = andy.get(f"/api/v1/mail/accounts/{w['acc']}/folders").json()
    assert [f["role"] for f in folders][:3] == ["INBOX", "STARRED", "SENT"]
    assert "TRASH" not in [f["role"] for f in folders]
    page = andy.get(f"/api/v1/mail/accounts/{w['acc']}/messages?folder=inbox").json()
    subjects = [m["subject"] for m in page["items"]]
    assert subjects == ["Danger", "Café order —", "Order for next week"]
    cafe = page["items"][1]
    assert cafe["from_name"] == "José" and not cafe["unread"]
    order = page["items"][2]
    assert order["thread_count"] == 2 and order["attachment_count"] == 1 and order["unread"]
    assert [a["filename"] for a in order["attachments"]] == ["PO-5521.pdf"]
    m = andy.get(f"/api/v1/mail/messages/{order['id']}").json()
    assert "<script" not in m["body_html"] and "alert" not in m["body_html"]
    assert "onerror" not in m["body_html"] and "javascript:" not in m["body_html"]
    assert "tracker.example" in m["body_html"] and ' src="https://tracker' not in m["body_html"]
    assert m["has_remote_images"] and not m["remote_images_loaded"]
    assert [t["subject"] for t in m["thread"]] == ["Order for next week", "Re: Order for next week"]
    m2 = andy.get(f"/api/v1/mail/messages/{order['id']}?images=true").json()
    assert 'src="https://tracker.example/pixel.gif"' in m2["body_html"]
    pdf = m["attachments"][0]
    assert (
        pdf["filename"] == "PO-5521.pdf"
        and pdf["previewable"]
        and pdf["scan_status"] == "NOT_SCANNED"
    )
    got = andy.get(f"/api/v1/mail/attachments/{pdf['id']}")
    assert got.status_code == 200 and got.content == PDF
    assert got.headers["content-type"] == "application/octet-stream"
    assert "attachment" in got.headers["content-disposition"]
    inline = andy.get(f"/api/v1/mail/attachments/{pdf['id']}?inline=true")
    assert (
        inline.headers["content-type"] == "application/pdf"
        and "sandbox" in inline.headers["content-security-policy"]
    )
    danger = andy.get(f"/api/v1/mail/messages/{page['items'][0]['id']}").json()
    exe = danger["attachments"][0]
    assert exe["scan_status"] == "BLOCKED" and not exe["downloadable"]
    assert andy.get(f"/api/v1/mail/attachments/{exe['id']}").status_code == 409
    # read / star state is kept here, per person; Lark is not touched
    assert (
        andy.post(
            f"/api/v1/mail/messages/{order['id']}/state", json={"read": True, "starred": True}
        ).status_code
        == 204
    )
    assert andy.get("/api/v1/mail/accounts").json()[0]["unread"] == 1
    starred = andy.get(f"/api/v1/mail/accounts/{w['acc']}/messages?folder=starred").json()["items"]
    assert [s["id"] for s in starred] == [order["id"]]
    assert not (set(lark["box"].commands) & MUTATING)


def test_no_blanket_access_even_for_super_admin(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    sync(w["acc"])
    andy, owner = w["andy"], w["owner"]
    mid = andy.get(f"/api/v1/mail/accounts/{w['acc']}/messages").json()["items"][0]["id"]
    # The Super Admin configured the mailbox but was not given it: no mail content.
    assert owner.get("/api/v1/mail/accounts").json() == []
    assert owner.get(f"/api/v1/mail/accounts/{w['acc']}/messages").status_code == 404
    assert owner.get(f"/api/v1/mail/messages/{mid}").status_code == 404
    # Another Admin: nothing, and cannot reach the settings either.
    other_email, _ = add_emp(world, "Vada", "MANAGER")
    other = make_api()
    other.login(other_email)
    assert other.get(f"/api/v1/mail/messages/{mid}").status_code == 404
    assert other.get(f"/api/v1/mail/search?account_id={w['acc']}&q=order").status_code == 404
    assert other.get("/api/v1/mail/admin/accounts").status_code == 403
    assert (
        other.post(
            "/api/v1/mail/admin/accounts", json={"address": "x@tamishatrading.com"}
        ).status_code
        == 403
    )
    assert andy.get("/api/v1/mail/admin/accounts").status_code == 403
    assert (
        andy.put(f"/api/v1/mail/admin/accounts/{w['acc']}/access", json={"grants": []}).status_code
        == 403
    )
    r = other.post(
        "/api/v1/mail/send",
        json={"account_id": w["acc"], "to": [{"email": "a@b.example"}]},
        headers=idem(),
    )
    assert r.status_code == 404
    with get_sessionmaker()() as db:
        denied = db.scalar(select(func.count()).where(AuditEvent.action == "mail.access_denied",
                                                      AuditEvent.company_id == world.company_id))  # fmt: skip  # noqa: E501
        assert denied >= 4
    # Sales / Warehouse roles don't have the module at all.
    sales = make_api()
    sales.login(world.emails["sales"])
    assert sales.get("/api/v1/mail/accounts").status_code == 403


def test_password_never_leaves_the_server(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    owner = w["owner"]
    for url in ("/api/v1/mail/admin/accounts", f"/api/v1/mail/admin/accounts/{w['acc']}/log"):
        r = owner.get(url)
        assert r.status_code == 200 and APP_PW not in r.text
    with get_sessionmaker()() as db:
        acc = db.get(MailAccount, uuid.UUID(w["acc"]))
        assert APP_PW.encode() not in (acc.password_enc or b"")
        audits = db.scalars(
            select(AuditEvent.detail_redacted).where(AuditEvent.action.like("mail.%"))
        ).all()
        assert all(APP_PW not in str(a) for a in audits)
    # wrong password: refused by "Lark", nothing saved, never shown as connected
    r = owner.post(
        f"/api/v1/mail/admin/accounts/{w['acc']}/credentials", json={"password": "wrong-code-0000"}
    )
    assert r.status_code == 200 and not r.json()["saved"] and not r.json()["imap_ok"]
    assert "wrong-code" not in r.text


def test_wrong_password_never_connects(lark, make_api, world) -> None:
    owner = make_api()
    owner.login(world.emails["owner"])
    _, uid = add_emp(world, "Ajay", "MANAGER")
    acc = owner.post(
        "/api/v1/mail/admin/accounts",
        json={"address": "ajay@tamishatrading.com", "user_ids": [str(uid)]},
    ).json()
    r = owner.post(
        f"/api/v1/mail/admin/accounts/{acc['id']}/credentials", json={"password": "nope-nope"}
    )
    assert not r.json()["saved"]
    a = owner.get("/api/v1/mail/admin/accounts").json()[0]
    assert (
        a["status"] == "NOT_CONFIGURED"
        and not a["has_password"]
        and a["health"] == "not_configured"
    )
    assert lsync.sync_account(uuid.UUID(acc["id"]), "test").status == "SKIPPED"


def test_domain_one_connection_per_mailbox_and_shared_info(lark, make_api, world) -> None:
    owner = make_api()
    owner.login(world.emails["owner"])
    s_email, saddam = add_emp(world, "Saddam", "OWNER_ADMIN")
    a_email, asha = add_emp(world, "Asha", "OWNER_ADMIN")
    assert (
        owner.post("/api/v1/mail/admin/accounts", json={"address": "me@gmail.com"}).json()["error"][
            "code"
        ]
        == "WRONG_DOMAIN"
    )
    r = owner.post("/api/v1/mail/admin/accounts", json={"address": "info@tamishatrading.com", "kind": "PERSONAL",  # noqa: E501
                                                        "user_ids": [str(saddam), str(asha)]})  # fmt: skip  # noqa: E501
    assert r.status_code == 422 and r.json()["error"]["code"] == "PERSONAL_ONE_USER"
    info = owner.post("/api/v1/mail/admin/accounts", json={"address": "info@tamishatrading.com", "kind": "SHARED",  # noqa: E501
                                                           "label": "Info Mail", "user_ids": [str(saddam), str(asha)]}).json()  # fmt: skip  # noqa: E501
    assert len(info["users"]) == 2
    assert (
        owner.post(
            "/api/v1/mail/admin/accounts", json={"address": "INFO@tamishatrading.com"}
        ).status_code
        == 409
    )
    # Same server login as Andy's fake for simplicity: info@ is one canonical connection.
    lark["box"].username = "info@tamishatrading.com"
    lark["smtp"].username = "info@tamishatrading.com"
    assert owner.post(
        f"/api/v1/mail/admin/accounts/{info['id']}/credentials", json={"password": APP_PW}
    ).json()["saved"]
    sync(info["id"])
    sa, aa = make_api(), make_api()
    sa.login(s_email)
    aa.login(a_email)
    ids_s = [
        m["id"] for m in sa.get(f"/api/v1/mail/accounts/{info['id']}/messages").json()["items"]
    ]
    ids_a = [
        m["id"] for m in aa.get(f"/api/v1/mail/accounts/{info['id']}/messages").json()["items"]
    ]
    assert ids_s == ids_a and len(ids_s) == 3  # the same rows, stored once
    # Saddam reading it does not mark it read for Asha
    sa.post(f"/api/v1/mail/messages/{ids_s[-1]}/state", json={"read": True})
    assert sa.get("/api/v1/mail/accounts").json()[0]["unread"] == 1
    assert aa.get("/api/v1/mail/accounts").json()[0]["unread"] == 2
    # revoking Asha takes it away at once (row kept, revoked)
    owner.put(
        f"/api/v1/mail/admin/accounts/{info['id']}/access",
        json={"grants": [{"employee_id": str(saddam)}]},
    )
    assert aa.get(f"/api/v1/mail/messages/{ids_s[0]}").status_code == 404
    assert aa.get("/api/v1/mail/accounts").json() == []


def test_search_is_scoped_to_the_selected_mailbox(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    sync(w["acc"])
    andy = w["andy"]

    def q(s: str, extra: str = "") -> list[str]:
        r = andy.get(f"/api/v1/mail/search?account_id={w['acc']}&q={s}{extra}")
        assert r.status_code == 200, r.text
        return [m["subject"] for m in r.json()["items"]]

    assert q("PO-5521") == ["Order for next week"]  # attachment file name
    assert q("TT2026-000123") == ["Café order —"]  # invoice number in the body
    assert q("jose@x.example") == ["Café order —"]
    assert "Re: Order for next week" in q(
        "order",
        "&folder="
        + next(
            f["id"]
            for f in andy.get(f"/api/v1/mail/accounts/{w['acc']}/folders").json()
            if f["role"] == "SENT"
        ),
    )
    assert q("cases", "&has_attachments=true") == ["Danger", "Order for next week"]
    assert q("nothing-like-this") == []
    assert q("Hello") == ["Order for next week"]  # a word only in the HTML version
    hit = andy.get(f"/api/v1/mail/search?account_id={w['acc']}&q=TT2026").json()["items"][0]
    assert "TT2026-000123" in (hit["highlight"] or "")


def test_send_once_reply_threading_and_bcc(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    sync(w["acc"])
    andy = w["andy"]
    order = andy.get(f"/api/v1/mail/accounts/{w['acc']}/messages").json()["items"][2]
    body = {
        "account_id": w["acc"], "mode": "reply", "source_message_id": order["id"],
        "to": [{"email": "buyer@customer.example", "name": "Buyer"}],
        "bcc": [{"email": "records@tamishatrading.com"}],
        "subject": "Re: Order for next week", "body_html": "<p>Confirmed <script>x</script></p>",
    }  # fmt: skip
    key = idem()
    r1 = andy.post("/api/v1/mail/send", json=body, headers=key)
    r2 = andy.post("/api/v1/mail/send", json=body, headers=key)  # double click / retry
    assert r1.status_code == 202 and r2.status_code == 202 and r1.json()["id"] == r2.json()["id"]
    assert outgoing.process_queue() == 1
    assert outgoing.process_queue() == 0
    sent = lark["smtp"].sent
    assert len(sent) == 1
    assert sorted(sent[0].rcpts) == ["buyer@customer.example", "records@tamishatrading.com"]
    data = sent[0].data.decode()
    assert "In-Reply-To: <order-1@customer.example>" in data
    assert "Bcc" not in data and "records@" not in data and "<script" not in data
    assert "From: Andy Narine <andy@tamishatrading.com>" in data
    st = andy.get(f"/api/v1/mail/accounts/{w['acc']}/outgoing").json()[0]
    assert st["status"] == "SENT"


def test_uncertain_send_is_never_retried_automatically(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    andy = w["andy"]
    lark["smtp"].drop_during_data = True
    r = andy.post("/api/v1/mail/send", json={"account_id": w["acc"], "to": [{"email": "a@b.example"}],  # noqa: E501
                                             "subject": "hi", "body_html": "x"}, headers=idem())  # fmt: skip  # noqa: E501
    oid = r.json()["id"]
    outgoing.process_queue()
    outgoing.process_queue()
    assert len(lark["smtp"].sent) == 1  # it reached the server once, and only once
    st = {o["id"]: o for o in andy.get(f"/api/v1/mail/accounts/{w['acc']}/outgoing").json()}[oid]
    assert st["status"] == "UNCERTAIN"
    r = andy.post(f"/api/v1/mail/outgoing/{oid}/retry", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "MAY_DUPLICATE"


def test_sending_needs_a_confirmed_smtp_login_and_the_own_mailbox(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    with get_sessionmaker()() as db, db.begin():
        db.get(MailAccount, uuid.UUID(w["acc"])).smtp_ok_at = None
    assert not w["andy"].get("/api/v1/mail/accounts").json()[0]["can_send"]
    r = w["andy"].post(
        "/api/v1/mail/send",
        json={"account_id": w["acc"], "to": [{"email": "a@b.example"}]},
        headers=idem(),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "SENDING_NOT_READY"


def test_interrupted_import_resumes_without_duplicates(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    for n in range(7):
        lark["inbox"].add(make_mail(subject=f"bulk {n}", msgid=f"<bulk-{n}@x.example>"))
    lark["box"].drop_after_fetches = 2
    r = sync(w["acc"])
    assert r.status == "ERROR"
    with get_sessionmaker()() as db:
        acc = db.get(MailAccount, uuid.UUID(w["acc"]))
        assert acc.status == "ERROR" and acc.next_sync_at is not None and acc.failure_count == 1
        partial = db.scalar(select(func.count()).where(MailMessage.account_id == acc.id))
    assert 0 < partial < 11
    lark["box"].drop_after_fetches = None
    assert sync(w["acc"]).status == "OK"
    with get_sessionmaker()() as db:
        assert (
            db.scalar(select(func.count()).where(MailMessage.account_id == uuid.UUID(w["acc"])))
            == 11
        )
        assert db.get(MailAccount, uuid.UUID(w["acc"])).status == "CONNECTED"


def test_new_mail_uidvalidity_change_and_server_removal(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    sync(w["acc"])
    lark["inbox"].add(make_mail(subject="Fresh order", msgid="<fresh@x.example>"))
    r = sync(w["acc"])
    assert r.new == 1
    # Lark renumbers the folder: same mail, new UIDVALIDITY/UIDs -> re-keyed, not duplicated
    lark["inbox"].uidvalidity = 9999
    for i, m in enumerate(lark["inbox"].messages):
        m.uid = 100 + i
    assert sync(w["acc"]).status == "OK"
    with get_sessionmaker()() as db:
        acc_id = uuid.UUID(w["acc"])
        rows = db.scalars(select(MailMessage).join(MailFolder).where(
            MailMessage.account_id == acc_id, MailFolder.role == "INBOX")).all()  # fmt: skip
        assert len(rows) == 4 and {r.uidvalidity for r in rows} == {9999}
    # someone removed a message in Lark itself: kept here, marked
    gone = lark["inbox"].messages.pop(0)
    sync(w["acc"])
    with get_sessionmaker()() as db:
        m = db.scalar(
            select(MailMessage).where(MailMessage.account_id == acc_id, MailMessage.uid == gone.uid)
        )
        assert m is not None and m.server_removed_at is not None


def test_auth_failure_stops_retrying_until_a_super_admin_acts(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    lark["box"].password = "rotated-in-lark"
    assert sync(w["acc"]).status == "AUTH_FAILED"
    with get_sessionmaker()() as db:
        acc = db.get(MailAccount, uuid.UUID(w["acc"]))
        assert acc.status == "AUTH_FAILED" and acc.next_sync_at is None
        assert acc.id not in lsync.due_accounts(db, limit=1000)
    # rotation: a new password is saved, imported mail is kept
    lark["smtp"].password = lark["box"].password = "rotated-in-lark"
    r = w["owner"].post(
        f"/api/v1/mail/admin/accounts/{w['acc']}/credentials", json={"password": "rotated-in-lark"}
    )
    assert r.json()["saved"]
    assert (
        w["owner"].post(f"/api/v1/mail/admin/accounts/{w['acc']}/pause").json()["status"]
        == "PAUSED"
    )
    assert sync(w["acc"]).status == "SKIPPED"
    assert w["owner"].post(f"/api/v1/mail/admin/accounts/{w['acc']}/resume").status_code == 200
    assert sync(w["acc"]).status == "OK"


def test_links_to_erp_records_are_explicit_and_audited(lark, make_api, world) -> None:
    from pro_trading.models import Customer

    w = connect_andy(make_api, world)
    sync(w["acc"])
    with get_sessionmaker()() as db, db.begin():
        c = Customer(company_id=world.company_id, name="Synthetic Buyer Ltd")
        db.add(c)
        db.flush()
        cid = str(c.id)
    andy = w["andy"]
    mid = andy.get(f"/api/v1/mail/accounts/{w['acc']}/messages").json()["items"][2]["id"]
    t = andy.get("/api/v1/mail/link-targets?type=customer&q=synthetic buyer").json()
    assert t[0]["entity_id"] == cid
    r = andy.post(
        f"/api/v1/mail/messages/{mid}/links", json={"entity_type": "customer", "entity_id": cid}
    )
    assert r.status_code == 201 and r.json()["label"] == "Synthetic Buyer Ltd"
    assert andy.get(f"/api/v1/mail/messages/{mid}").json()["links"][0]["entity_id"] == cid
    assert andy.post(f"/api/v1/mail/links/{r.json()['id']}/unlink").status_code == 204
    assert andy.get(f"/api/v1/mail/messages/{mid}").json()["links"] == []


# ------------------------------------------------------------------ parsing & sanitizing


def test_mime_decoding_and_timezones() -> None:
    raw = (
        b"From: =?iso-8859-1?q?Fran=E7ois?= <F@Example.COM>\r\n"
        b'To: a@b.example, "Last, First" <c@d.example>\r\n'
        b"Subject: =?utf-8?b?5LiK5rW3IG9yZGVy?=\r\n"
        b"Date: Mon, 02 Nov 2026 08:00:00 +0800\r\n"
        b"Message-ID: <x1@example.com>\r\n"
        b"Content-Type: text/plain; charset=iso-8859-1\r\n"
        b"Content-Transfer-Encoding: quoted-printable\r\n\r\n"
        b"Cr=E8me br=FBl=E9e\r\n"
    )
    p = mime.parse(raw)
    assert p.from_name == "François" and p.from_email == "f@example.com"
    assert p.subject == "上海 order"
    assert [a.email for a in p.to] == ["a@b.example", "c@d.example"] and p.to[
        1
    ].name == "Last, First"
    assert p.sent_at == datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
    assert p.body_text == "Crème brûlée"
    broken = mime.parse(b"Subject: =?bogus-charset?q?x?=\r\nDate: not a date\r\n\r\nhi")
    assert broken.body_text == "hi" and "bad_date" in broken.warnings


def test_sanitizer_allow_list() -> None:
    safe, text, _, remote = sanitize(
        '<div style="color:red;background:url(http://x)">Hi<iframe src="x"></iframe>'
        '<a href="https://ok.example" onclick="x()">ok</a><img src="cid:logo1">'
        '<style>body{}</style><form><input></form><img src="http://t.example/a.png"></div>',
        10_000,
    )
    assert "iframe" not in safe and "onclick" not in safe and "<style" not in safe
    assert "url(" not in safe and "color: red" in safe
    assert 'href="https://ok.example"' in safe and 'rel="noopener noreferrer nofollow"' in safe
    assert 'data-cid="logo1"' in safe and 'data-remote-src="http://t.example/a.png"' in safe
    assert remote and text.startswith("Hi")


def test_attachment_rows_keep_their_bytes(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    sync(w["acc"])
    with get_sessionmaker()() as db:
        atts = db.scalars(select(MailAttachment).join(MailMessage).where(
            MailMessage.account_id == uuid.UUID(w["acc"]))).all()  # fmt: skip
        pdf = next(a for a in atts if a.filename == "PO-5521.pdf")
        import hashlib

        assert pdf.stored and pdf.detected_mime == "application/pdf"
        assert pdf.sha256 == hashlib.sha256(PDF).hexdigest() and pdf.byte_size == len(PDF)


def test_outgoing_rows_record_one_send_per_key(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    with get_sessionmaker()() as db:
        before = db.scalar(select(func.count()).select_from(MailOutgoing))
    body = {
        "account_id": w["acc"],
        "to": [{"email": "a@b.example"}],
        "subject": "s",
        "body_html": "b",
    }
    k = idem()
    for _ in range(3):
        w["andy"].post("/api/v1/mail/send", json=body, headers=k)
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(MailOutgoing)) == before + 1


def test_universal_search_shows_only_my_mailboxes(lark, make_api, world) -> None:
    w = connect_andy(make_api, world)
    sync(w["acc"])
    r = w["andy"].get("/api/v1/search?q=PO-5521&per_group=5")
    assert r.status_code == 200, r.text
    hits = [i for g in r.json()["groups"] if g["type"] == "email" for i in g["items"]]
    assert [h["title"] for h in hits] == ["Order for next week"]
    assert hits[0]["url"].startswith(f"/email?account={w['acc']}&message=")
    assert w["andy"].get("/api/v1/search?q=mail:PO-5521").status_code == 200
    # the Super Admin who set the mailbox up gets no email hits (no grant)
    r = w["owner"].get("/api/v1/search?q=PO-5521&per_group=5")
    assert not [g for g in r.json()["groups"] if g["type"] == "email"]
    assert (
        w["andy"]
        .post("/api/v1/search/recent", json={"type": "email", "id": hits[0]["id"]})
        .status_code
        == 204
    )
