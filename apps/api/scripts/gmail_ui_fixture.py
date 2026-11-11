"""SYNTHETIC emails for the Gmail Inbox screens: browser E2E and screenshots.

Refuses to run outside development/test. Prints JSON (users, password, message and attachment ids).

Usage: uv run python scripts/gmail_ui_fixture.py

It creates a fresh synthetic company whose inbox holds a handful of made-up emails, as the sync
would have stored them. This is NOT a Gmail connection and does not pretend to be one:
- no Google call is made and no token exists; the mailbox row is DISCONNECTED, so the page
  header truthfully says that no mailbox is connected;
- each attachment's bytes are kept as a source document up front (what "Use for OCR" does on
  first use), so preview, download and processing work here without Google;
- everything after that (scan, OCR reading, review, draft order) is the real pipeline.
"""

import io
import json
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta

from PIL import Image, ImageDraw

from pro_trading import scanning
from pro_trading.bootstrap import ensure_default_roles, sync_permissions
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.gmail.models import GmailAccount, GmailAttachment, GmailMessage
from pro_trading.gmail.parse import eligible_mime, sanitize_html
from pro_trading.models import Company, Employee, EmployeeRole, Warehouse
from pro_trading.security import hash_password
from pro_trading.seed import seed_catalog, seed_pricing

try:
    from scripts.scan_ui_fixture import order_sheet
except ModuleNotFoundError:  # run as a script: this folder is on the path, not its parent
    from scan_ui_fixture import order_sheet

PDF = "application/pdf"
# key, file name, declared type, bytes (None = not kept), inline
File = tuple[str, str, str, bytes | None, bool]
# key, sender name, sender address, subject, age, read, text, html, files
Email = tuple[str, str, str, str, timedelta, bool, str, str, list[File]]
HTML = (
    "<div><p>Hi team,</p><p>Please find <b>this week's price list</b> from the grower. "
    'Details are on <a href="https://example.test/prices">their page</a>.</p>'
    "<script>alert('never runs')</script><img src='https://example.test/pixel.gif' alt='logo'>"
    "<p>Thanks,<br>Maria</p></div>"
)


def photo(text: str) -> bytes:
    im = Image.new("RGB", (850, 1100), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((40, 40, 810, 1060), outline="black", width=3)
    for i, line in enumerate(text.split("\n")):
        d.text((80, 90 + i * 40), line, fill="black")
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def main() -> int:
    if get_settings().is_production_like:
        print("refusing in", get_settings().app_env, file=sys.stderr)
        return 2
    tag = uuid.uuid4().hex[:8]
    password = "E2e-" + secrets.token_urlsafe(12)
    now = datetime.now(UTC)
    out: dict[str, dict[str, str] | str] = {"password": password}
    users: dict[str, str] = {}
    messages: dict[str, str] = {}
    attachments: dict[str, str] = {}
    with get_sessionmaker()() as db, db.begin():
        sync_permissions(db)
        co = Company(legal_name=f"Gmail UI Synthetic {tag}", timezone="America/Toronto")
        db.add(co)
        db.flush()
        roles = ensure_default_roles(db, co.id)
        wh = Warehouse(company_id=co.id, code="MAIN", name="Main Warehouse")
        db.add(wh)
        db.flush()
        pw = hash_password(password)
        emps: dict[str, Employee] = {}
        for key, role in [
            ("owner", "OWNER_ADMIN"),
            ("sales", "SALES_OPERATOR"),
            ("warehouse", "WAREHOUSE"),
        ]:
            emp = Employee(
                company_id=co.id,
                email=f"{key}.{tag}@example.test",
                display_name=f"Gmail UI {key}",
                password_hash=pw,
            )
            db.add(emp)
            db.flush()
            db.add(EmployeeRole(employee_id=emp.id, role_id=roles[role].id, warehouse_id=None))
            users[key], emps[key] = emp.email, emp
        seed_catalog(db, co)
        seed_pricing(db, co, emps["owner"])  # customers, incl. C-1001

        acc = GmailAccount(
            company_id=co.id,
            email_address=f"scans.{tag}@example.test",
            status="DISCONNECTED",  # a stand-in: there is no Google connection behind it
            last_sync_at=now,
            backfill_done_at=now,
            disconnected_at=now,
        )
        db.add(acc)
        db.flush()

        name = co.legal_name
        sheet1 = order_sheet(1, {(1, "CFZ"): "12", (1, "PLG"): "3?"}, {1: ["Mango pulp 4 box"]},
                             company=f"{name} one")  # fmt: skip
        sheet10 = order_sheet(10, {(1, "CFZ"): "4", (4, "RJS"): "20", (7, "PLG"): "6?"},
                              company=f"{name} ten")  # fmt: skip
        sheet_b = order_sheet(1, {(1, "RJS"): "9"}, company=f"{name} b")
        sheet_old = order_sheet(1, {(1, "CFZ"): "30"}, company=f"{name} old")
        signed = photo("PICKING SHEET\nSigned copy\nSynthetic")
        sheet_older = order_sheet(1, {(1, "PLG"): "18"}, company=f"{name} older")
        emails: list[Email] = [
            ("today_sheet", "Office Scanner", "scanner@example.test", "Scanned picking sheet",
             timedelta(minutes=12), False, "Scanned from the office copier.", "",
             [("sheet", "Picking-Sheet-Nov11.pdf", PDF, sheet1, False)]),
            ("today_two", "Office Scanner", "scanner@example.test", "2 scanned documents",
             timedelta(hours=3), False, "Two documents from this morning's run.", "",
             [("ten", "Order-Sheets-10-pages.pdf", PDF, sheet10, False),
              ("second", "Island-Grocers-order.pdf", PDF, sheet_b, False)]),
            ("yesterday_text", "Maria Lopez", "maria@grower.example.test", "Price list next week",
             timedelta(days=1, hours=2), True, "", HTML, []),
            ("photo", "Warehouse Phone", "warehouse@example.test", "Photo of signed sheet",
             timedelta(days=3), True, "Signed copy attached.", "",
             [("photo", "IMG_2041.png", "image/png", signed, False),
              ("logo", "signature-logo.png", "image/png", photo("logo"), True)]),
            ("spreadsheet", "Accounts", "accounts@supplier.example.test", "Statement October",
             timedelta(days=12), True, "Statement attached as a spreadsheet.", "",
             [("xlsx", "statement-october.xlsx",
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", None, False)]),
            ("old_sheet", "Office Scanner", "scanner@example.test", "Scanned picking sheet",
             timedelta(days=45), True, "Scanned from the office copier.", "",
             [("old", "Picking-Sheet-Sep27.pdf", PDF, sheet_old, False)]),
            ("older_sheet", "Office Scanner", "scanner@example.test", "Scanned picking sheet",
             timedelta(days=110), True, "Scanned from the office copier.", "",
             [("older", "Picking-Sheet-Jul24.pdf", PDF, sheet_older, False)]),
        ]  # fmt: skip
        for i, (key, fname, femail, subject, age, read, text, html, files) in enumerate(emails):
            safe_html, from_html, _ = sanitize_html(html, 200_000) if html else ("", "", False)
            body = text or from_html
            m = GmailMessage(
                company_id=co.id,
                account_id=acc.id,
                gmail_message_id=f"fx{tag}{i:03d}",
                thread_id=f"fx{tag}{i:03d}",
                from_name=fname,
                from_email=femail,
                to_addr=acc.email_address,
                subject=subject,
                snippet=body.replace("\n", " ")[:120],
                body_text=body,
                body_html=safe_html,
                received_at=now - age,
                labels=["INBOX"] if read else ["INBOX", "UNREAD"],
                attachment_count=len(files),
                pdf_count=sum(1 for f in files if f[2] == PDF),
                read_at=(now - age) if read else None,
            )
            db.add(m)
            db.flush()
            messages[key] = str(m.id)
            for n, (akey, filename, mime, data, inline) in enumerate(files, start=1):
                doc = None
                if data is not None:
                    real = scanning.sniff(data)
                    assert real is not None
                    doc, _ = scanning.store_document(
                        db, co.id, data, real, filename, emps["owner"].id, "GMAIL"
                    )
                a = GmailAttachment(
                    company_id=co.id,
                    message_id=m.id,
                    part_id=str(n),
                    filename=filename,
                    mime_type=mime,
                    byte_size=len(data) if data is not None else 48_213,
                    inline=inline,
                    eligible=eligible_mime(mime, filename) is not None,
                    sha256=doc.sha256 if doc else None,
                    detected_mime=doc.mime_type if doc else None,
                    page_count=scanning.page_count(data, doc.mime_type) if doc and data else None,
                    source_document_id=doc.id if doc else None,
                )
                db.add(a)
                db.flush()
                attachments[akey] = str(a.id)
    out["users"] = users
    out["messages"] = messages
    out["attachments"] = attachments
    out["mailbox"] = f"scans.{tag}@example.test"
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
