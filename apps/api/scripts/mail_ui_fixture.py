"""SYNTHETIC mailboxes for the Email (Lark Mail) screens: browser E2E and screenshots.

Refuses to run outside development/test. Prints JSON (users, password, account ids).

Usage (from apps/api): PYTHONPATH=src:. uv run python scripts/mail_ui_fixture.py

It creates a fresh synthetic company with a Super Admin, "Andy" (Admin, one personal mailbox),
"Saddam" (Super Admin, personal + shared info@) and "Vada" (Admin, nothing yet). The mail is
imported by the REAL sync code from a local stand-in IMAP server (tests/lark_fakes.py) that runs
only while this script runs. This is NOT a Lark connection and does not pretend to be one: the
mailboxes are left PAUSED and labelled "(synthetic)", so the worker never tries to reach the
stand-in again, and the settings page shows them as paused test mailboxes.
"""

import json
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta

os.environ["LARK_MAIL_ALLOW_PLAINTEXT"] = "true"

from pro_trading.bootstrap import ensure_default_roles, sync_permissions  # noqa: E402
from pro_trading.config import get_settings  # noqa: E402
from pro_trading.db import get_sessionmaker  # noqa: E402
from pro_trading.larkmail.models import MailAccess, MailAccount  # noqa: E402
from pro_trading.larkmail.secrets import seal  # noqa: E402
from pro_trading.larkmail.sync import sync_account, test_connection  # noqa: E402
from pro_trading.models import Company, Customer, Employee, EmployeeRole, Warehouse  # noqa: E402
from pro_trading.security import hash_password  # noqa: E402
from tests.lark_fakes import (  # noqa: E402
    FakeFolder,
    FakeMailbox,
    FakeSmtpState,
    make_mail,
    start_imap,
    start_smtp,
)

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def _box(user: str, pw: str, seed: int) -> FakeMailbox:
    box = FakeMailbox(username=user, password=pw)
    inbox = box.folders.setdefault("INBOX", FakeFolder("INBOX", uidvalidity=100 + seed))
    sent = box.folders.setdefault(
        "Sent", FakeFolder("Sent", uidvalidity=200 + seed, attrs=["\\Sent"])
    )
    box.folders["Drafts"] = FakeFolder("Drafts", uidvalidity=300 + seed, attrs=["\\Drafts"])
    box.folders["Archive"] = FakeFolder("Archive", uidvalidity=400 + seed, attrs=["\\Archive"])
    box.folders["Junk"] = FakeFolder("Junk", uidvalidity=500 + seed, attrs=["\\Junk"])
    t0 = datetime.now(UTC) - timedelta(days=12)
    people = [
        ("Maria Lopez", "maria@greenleaf-grocers.example", "Greenleaf Grocers"),
        ("Kwame Mensah", "orders@accra-imports.example", "Accra Imports"),
        ("Priya Shah", "priya@spiceroute.example", "Spice Route Foods"),
        ("Tom Becker", "ap@northside-market.example", "Northside Market"),
    ]
    n = 0
    for day in range(12):
        for k, (name, email, co) in enumerate(people):
            n += 1
            when = t0 + timedelta(days=day, hours=8 + k * 2)
            att = (f"PO-{5500 + n:04d}.pdf", "application/pdf", PDF) if n % 3 == 0 else None
            subject = [
                f"Order for {co} — week {45 + day % 4}",
                f"Invoice TT2026-{n:06d} payment",
                f"Price list question ({co})",
                f"Delivery Tuesday — {co}",
            ][k]
            html = (
                f"<p>Hi {user.split('@')[0].title()},</p><p>Please confirm the order below "
                f"for <b>{co}</b>:</p><table border='1'><tr><th>Item</th><th>Cases</th></tr>"
                f"<tr><td>Basmati rice 10 kg</td><td>{n % 9 + 1}</td></tr>"
                f"<tr><td>Chickpeas 2 kg</td><td>{n % 5 + 2}</td></tr></table>"
                f"<p>Thanks,<br>{name}</p>"
                + ('<img src="https://tracker.example/open.gif">' if n % 4 == 0 else "")
            )
            mid = f"<m{seed}-{n}@{email.split('@')[1]}>"
            inbox.add(
                make_mail(subject=subject, sender=f"{name} <{email}>", to=user,
                          body=f"Please confirm the order for {co}. Invoice TT2026-{n:06d}.",
                          html=html, msgid=mid, attachment=att,
                          date=when.strftime("%a, %d %b %Y %H:%M:%S +0000")),
                flags={"\\Seen"} if n % 5 else set(),
                when=when,
            )  # fmt: skip
            if n % 4 == 1:
                sent.add(
                    make_mail(subject=f"Re: {subject}", sender=user, to=email,
                              body="Confirmed, thank you.", in_reply_to=mid,
                              msgid=f"<r{seed}-{n}@tamishatrading.com>",
                              date=(when + timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")),  # noqa: E501
                    flags={"\\Seen"}, when=when + timedelta(hours=1),
                )  # fmt: skip
    inbox.add(
        make_mail(subject="Photo of the damaged pallet", sender="Kwame Mensah <orders@accra-imports.example>",  # noqa: E501
                  to=user, body="See the picture.", attachment=("pallet.png", "image/png", PNG)),
        when=datetime.now(UTC) - timedelta(hours=2),
    )  # fmt: skip
    return box


def main() -> None:
    s = get_settings()
    if s.app_env not in ("development", "test"):
        sys.exit("refusing to run outside development/test")
    tag = secrets.token_hex(3)
    pw = f"Synthetic-{secrets.token_hex(4)}-Aa1!"
    out: dict[str, object] = {"password": pw, "tag": tag}
    with get_sessionmaker()() as db, db.begin():
        sync_permissions(db)
        co = Company(legal_name=f"Synthetic Mail Co {tag}", timezone="America/Toronto")
        db.add(co)
        db.flush()
        roles = ensure_default_roles(db, co.id)
        db.add(Warehouse(company_id=co.id, code="MAIN", name="Main Warehouse"))
        db.add(Customer(company_id=co.id, name="Greenleaf Grocers"))
        users = {}
        for key, name, role in [
            ("owner", "Synthetic Owner", "OWNER_ADMIN"),
            ("andy", "Andy (synthetic)", "MANAGER"),
            ("saddam", "Saddam (synthetic)", "OWNER_ADMIN"),
            ("vada", "Vada (synthetic)", "MANAGER"),
        ]:
            e = Employee(company_id=co.id, email=f"{key}.{tag}@example.test", display_name=name,
                         password_hash=hash_password(pw))  # fmt: skip
            db.add(e)
            db.flush()
            db.add(EmployeeRole(employee_id=e.id, role_id=roles[role].id))
            users[key] = e
        out["users"] = {k: e.email for k, e in users.items()}
        out["company_id"] = str(co.id)
        company_id = co.id
        uid = {k: e.id for k, e in users.items()}
    accounts = {}
    for key, addr, label, kind, grantees, seed in [
        ("andy", f"andy.{tag}@tamishatrading.com", "Andy Mail", "PERSONAL", ["andy"], 1),
        ("saddam", f"saddam.{tag}@tamishatrading.com", "Saddam Mail", "PERSONAL", ["saddam"], 2),
        ("info", f"info.{tag}@tamishatrading.com", "Info Mail", "SHARED", ["saddam"], 3),
    ]:
        app_pw = secrets.token_urlsafe(12)
        box = _box(addr, app_pw, seed)
        imap_srv = start_imap(box)
        smtp_srv = start_smtp(FakeSmtpState(addr, app_pw))
        with get_sessionmaker()() as db, db.begin():
            acc = MailAccount(
                company_id=company_id, address=addr, display_name=label.replace(" Mail", ""),
                label=f"{label}", kind=kind, imap_host="127.0.0.1", imap_port=imap_srv.port,
                smtp_host="127.0.0.1", smtp_port=smtp_srv.port, username=addr,
                password_enc=seal(app_pw), status="CONNECTED", sync_enabled=False,
            )  # fmt: skip
            db.add(acc)
            db.flush()
            r = test_connection(acc, app_pw)
            assert r.imap_ok and r.smtp_ok, r
            acc.imap_ok_at = acc.smtp_ok_at = datetime.now(UTC)
            for g in grantees:
                db.add(MailAccess(company_id=company_id, account_id=acc.id, employee_id=uid[g]))
            acc_id = acc.id
        res = sync_account(acc_id, "fixture")
        assert res.status == "OK", res.error
        with get_sessionmaker()() as db, db.begin():
            acc = db.get(MailAccount, acc_id)
            acc.status, acc.sync_enabled, acc.next_sync_at = "PAUSED", False, None
            acc.label = f"{label} (synthetic)"
            acc.last_error = "Synthetic test mailbox: imported from a local stand-in, not Lark"
        imap_srv.stop()
        smtp_srv.stop()
        accounts[key] = str(acc_id)
    out["accounts"] = accounts
    out["note"] = "synthetic stand-in mailboxes (PAUSED); not connected to Lark"
    print(json.dumps(out))
    _ = uuid  # keep import for readers of older versions


if __name__ == "__main__":
    main()
