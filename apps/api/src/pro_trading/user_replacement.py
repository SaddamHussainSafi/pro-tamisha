"""Replace who may sign in with the owner's seven authorized users (directive 2026-11-12, s.2).

Safe and staged; nothing is ever deleted:

  report   read-only: every account of the company, what it owns in history (foreign-key
           references, counted per table), and what each later step would do.
  backup   pg_dump of the whole database, then a restore into a scratch database to prove it
           restores (row counts compared), then the scratch database is dropped.
  users    upsert the seven identities (existing e-mail = same row and id kept; otherwise a new
           account with an invitation: they choose their own password through the existing
           invite link; nobody's password is set or guessed here), give them their role, and
           prepare their mailboxes (NOT_CONFIGURED: a Super Admin enters each Lark app password
           in Settings > Email Services). info@ is ONE shared mailbox for Saddam and Asha.
  archive  deactivate + archive every other human account of the company: sessions revoked,
           open invite/reset links revoked, hidden from the normal user list. Their rows stay
           (history keeps pointing at them). Refused until at least one of the new Super
           Admins has accepted the invitation and can sign in.

Usage (from apps/api):
  PYTHONPATH=src uv run python -m pro_trading.user_replacement report  --company-id <uuid>
  PYTHONPATH=src uv run python -m pro_trading.user_replacement backup
  PYTHONPATH=src uv run python -m pro_trading.user_replacement users   --company-id <uuid> --by <email>
  PYTHONPATH=src uv run python -m pro_trading.user_replacement archive --company-id <uuid> --by <email>

Rollback of `users`/`archive`: reactivate an archived account in Settings > User management
(or set employees.active = true, archived_at = NULL); the seven new rows can be suspended the
same way. Full rollback: restore the backup taken first.
"""  # noqa: E501

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import accounts, audit
from .bootstrap import ensure_default_roles
from .config import get_settings
from .db import get_sessionmaker
from .larkmail.models import MailAccess, MailAccount
from .models import Employee, EmployeeRole, Role


@dataclass(frozen=True)
class Authorized:
    name: str
    role: str  # role code: OWNER_ADMIN = "Super Admin", MANAGER = "Admin"
    email: str
    mailboxes: tuple[str, ...]


AUTHORIZED: tuple[Authorized, ...] = (
    Authorized("Saddam Hussain", "OWNER_ADMIN", "saddam@tamishatrading.com",
               ("saddam@tamishatrading.com", "info@tamishatrading.com")),
    Authorized("Asha Narine", "OWNER_ADMIN", "asha@tamishatrading.com",
               ("asha@tamishatrading.com", "info@tamishatrading.com")),
    Authorized("Vada Narine", "MANAGER", "vada@tamishatrading.com", ("vada@tamishatrading.com",)),
    Authorized("Ajay Narine", "MANAGER", "ajay@tamishatrading.com", ("ajay@tamishatrading.com",)),
    Authorized("Denise G.", "MANAGER", "denise@tamishatrading.com", ("denise@tamishatrading.com",)),
    Authorized("Nishar Miya", "MANAGER", "nishar@tamishatrading.com", ("nishar@tamishatrading.com",)),  # noqa: E501
    Authorized("Andy Narine", "MANAGER", "andy@tamishatrading.com", ("andy@tamishatrading.com",)),
)  # fmt: skip
SHARED = {"info@tamishatrading.com": "Info Mail"}
AUTH_EMAILS = {a.email for a in AUTHORIZED}


def _company(db: Session, company_id: str) -> uuid.UUID:
    cid = uuid.UUID(company_id)
    if db.scalar(text("SELECT 1 FROM companies WHERE id = :c"), {"c": cid}) is None:
        raise SystemExit("No such company")
    return cid


def _refs(db: Session) -> list[tuple[str, str]]:
    """Every (table, column) with a foreign key to employees."""
    return [
        (r[0], r[1])
        for r in db.execute(
            text(
                "SELECT c.conrelid::regclass::text, a.attname FROM pg_constraint c "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
                "WHERE c.contype = 'f' AND c.confrelid = 'employees'::regclass ORDER BY 1, 2"
            )
        )
    ]


def _roles(db: Session, emp_id: uuid.UUID) -> list[str]:
    return sorted(
        db.scalars(
            select(Role.code).join(EmployeeRole, EmployeeRole.role_id == Role.id)
            .where(EmployeeRole.employee_id == emp_id)
        )
    )  # fmt: skip


def report(db: Session, company_id: uuid.UUID) -> dict[str, Any]:
    refs = _refs(db)
    people = []
    for e in db.scalars(
        select(Employee).where(Employee.company_id == company_id).order_by(Employee.created_at)
    ):
        owned: dict[str, int] = {}
        for t, c in refs:
            n = db.scalar(text(f'SELECT count(*) FROM {t} WHERE "{c}" = :e'), {"e": e.id}) or 0  # noqa: S608
            if n:
                owned[f"{t}.{c}"] = int(n)
        sessions = db.scalar(
            text("SELECT count(*) FROM auth_sessions WHERE employee_id = :e AND revoked_at IS NULL"
                 " AND expires_at > now()"), {"e": e.id},
        )  # fmt: skip
        authorized = e.email.lower() in AUTH_EMAILS
        people.append({
            "id": str(e.id), "email": e.email, "name": e.display_name, "active": e.active,
            "archived": e.archived_at is not None, "roles": _roles(db, e.id),
            "can_sign_in": e.active and e.password_hash is not None,
            "two_step": e.mfa_enrolled_at is not None, "live_sessions": int(sessions or 0),
            "history_rows": sum(owned.values()), "history": owned,
            "plan": "keep (authorized user)" if authorized else
                    ("archive: deactivate, revoke sessions and links, keep row and history"
                     if e.active else "already inactive: archive, keep row and history"),
        })  # fmt: skip
    have = {str(p["email"]).lower() for p in people}
    return {
        "company_id": str(company_id),
        "generated_at": datetime.now(UTC).isoformat(),
        "foreign_keys_to_employees": len(refs),
        "accounts": people,
        "to_create": [
            {"name": a.name, "email": a.email, "role": a.role, "mailboxes": list(a.mailboxes)}
            for a in AUTHORIZED
            if a.email not in have
        ],  # fmt: skip
        "history_remapping": "none: no legacy account is verifiably one of the seven people, so "
        "historical records keep their original creator / modifier (rule 2.6-2.7).",
    }


def backup() -> dict[str, Any]:
    url = get_settings().database_url.replace("+psycopg", "")
    u = urlsplit(url)
    db_name = u.path.lstrip("/")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs("var/backups", exist_ok=True)
    path = f"var/backups/{db_name}-{stamp}.dump"
    base = ["-h", u.hostname or "127.0.0.1", "-p", str(u.port or 5432), "-U", u.username or ""]
    subprocess.run(["pg_dump", *base, "-Fc", db_name, "-f", path], check=True)  # noqa: S603,S607
    scratch = f"{db_name}_restore_check_{stamp.replace('-', '_')}"
    subprocess.run(["createdb", *base, scratch], check=True)  # noqa: S603,S607
    try:
        subprocess.run(["pg_restore", *base, "-d", scratch, "--no-owner", path], check=True,  # noqa: S603,S607
                       capture_output=True)  # fmt: skip
        counts = {}
        for name in (db_name, scratch):
            out = subprocess.run(  # noqa: S603
                ["psql", *base, "-At", "-d", name, "-c",  # noqa: S607
                 "SELECT (SELECT count(*) FROM employees)||','||(SELECT count(*) FROM invoices)"
                 "||','||(SELECT count(*) FROM sales_orders)||','||(SELECT count(*) FROM "
                 "stock_movements)||','||(SELECT count(*) FROM audit_events)"],
                check=True, capture_output=True, text=True,
            )  # fmt: skip
            counts[name] = out.stdout.strip()
    finally:
        subprocess.run(["dropdb", *base, scratch], check=False)  # noqa: S603,S607
    ok = counts[db_name] == counts[scratch]
    return {"backup": path, "bytes": os.path.getsize(path), "restore_verified": ok,
            "counts employees,invoices,orders,stock_movements,audit": counts}  # fmt: skip


def apply_users(db: Session, company_id: uuid.UUID, by_email: str) -> dict[str, Any]:
    by = db.scalar(select(Employee).where(func.lower(Employee.email) == by_email.lower(),
                                          Employee.company_id == company_id))  # fmt: skip
    if by is None or "OWNER_ADMIN" not in _roles(db, by.id) or not by.active:
        raise SystemExit("--by must be an active Super Admin of this company")
    roles = ensure_default_roles(db, company_id)
    s = get_settings()
    done: list[dict[str, Any]] = []
    ids: dict[str, uuid.UUID] = {}
    for a in AUTHORIZED:
        e = db.scalar(select(Employee).where(func.lower(Employee.email) == a.email))
        if e is not None and e.company_id != company_id:
            raise SystemExit(f"{a.email} belongs to another company; not touched")
        created = e is None
        if e is None:
            e = Employee(company_id=company_id, email=a.email, display_name=a.name)
            db.add(e)
            db.flush()
        else:
            e.display_name = a.name
            if e.archived_at is not None or not e.active:
                e.active, e.archived_at, e.archived_reason = True, None, None
        if _roles(db, e.id) != [a.role]:
            db.execute(text("DELETE FROM employee_roles WHERE employee_id = :e"), {"e": e.id})
            db.add(EmployeeRole(employee_id=e.id, role_id=roles[a.role].id))
        db.flush()
        invited = False
        if e.password_hash is None:
            raw, tok = accounts.issue_token(db, e, accounts.INVITE, created_by=by.id)
            accounts.send_mail(
                db, e, subject=f"You're invited to {s.app_name}",
                body=(f"Hello {e.display_name},\n\nYou have been given an account on {s.app_name}. "
                      f"Open this link to choose your password (it works once and expires in "
                      f"{s.invite_ttl_hours} hours):\n\n{{link}}\n\nThen sign in with {e.email}."),
                dedupe_key=f"auth:invite:{tok.id}", secret_link=accounts.link(accounts.INVITE, raw),
            )  # fmt: skip
            invited = True
        audit.record(db, action="employee.replacement_upsert", success=True, company_id=company_id,
                     actor_id=by.id, target_type="employee", target_id=str(e.id),
                     detail={"email": a.email, "role": a.role, "created": created,
                             "invited": invited})  # fmt: skip
        ids[a.email] = e.id
        done.append({"email": a.email, "id": str(e.id), "role": a.role, "created": created,
                     "invited": invited})  # fmt: skip
    # Mailboxes: one canonical row per address; personal = its owner, info@ = Saddam + Asha.
    boxes: dict[str, list[uuid.UUID]] = {}
    for a in AUTHORIZED:
        for addr in a.mailboxes:
            boxes.setdefault(addr, []).append(ids[a.email])
    names = {a.email: a.name for a in AUTHORIZED}
    for addr, owners in boxes.items():
        acc = db.scalar(select(MailAccount).where(MailAccount.company_id == company_id,
                                                  func.lower(MailAccount.address) == addr))  # fmt: skip  # noqa: E501
        if acc is None:
            shared = addr in SHARED
            acc = MailAccount(
                company_id=company_id, address=addr, kind="SHARED" if shared else "PERSONAL",
                display_name="Tamisha Trading" if shared else names[addr],
                label=SHARED.get(addr) or names[addr].split()[0] + " Mail",
                imap_host=s.lark_imap_host, imap_port=s.lark_imap_port,
                smtp_host=s.lark_smtp_host, smtp_port=s.lark_smtp_port, username=addr,
                created_by=by.id,
            )  # fmt: skip
            db.add(acc)
            db.flush()
        live = set(db.scalars(select(MailAccess.employee_id).where(
            MailAccess.account_id == acc.id, MailAccess.revoked_at.is_(None))))  # fmt: skip
        for emp_id in owners:
            if emp_id not in live:
                db.add(MailAccess(company_id=company_id, account_id=acc.id, employee_id=emp_id,
                                  granted_by=by.id))  # fmt: skip
                audit.record(db, action="mail.access_granted", success=True,
                             company_id=company_id, actor_id=by.id, target_type="mail_account",
                             target_id=str(acc.id), detail={"address": addr,
                                                            "employee_id": str(emp_id)})  # fmt: skip  # noqa: E501
    db.commit()
    return {"users": done, "mailboxes": sorted(boxes)}


def archive_legacy(db: Session, company_id: uuid.UUID, by_email: str) -> dict[str, Any]:
    by = db.scalar(select(Employee).where(func.lower(Employee.email) == by_email.lower(),
                                          Employee.company_id == company_id))  # fmt: skip
    if by is None or "OWNER_ADMIN" not in _roles(db, by.id):
        raise SystemExit("--by must be a Super Admin of this company")
    ready = db.scalar(
        select(func.count()).select_from(Employee)
        .join(EmployeeRole, EmployeeRole.employee_id == Employee.id)
        .join(Role, Role.id == EmployeeRole.role_id)
        .where(Employee.company_id == company_id, Employee.active.is_(True),
               Employee.password_hash.is_not(None), func.lower(Employee.email).in_(AUTH_EMAILS),
               Role.code == "OWNER_ADMIN", EmployeeRole.warehouse_id.is_(None))
    )  # fmt: skip
    if not ready:
        raise SystemExit(
            "Refused: neither Saddam nor Asha has accepted the invitation yet, so archiving the "
            "current accounts would leave nobody who can sign in as Super Admin."
        )
    out = []
    t = datetime.now(UTC)
    for e in db.scalars(select(Employee).where(Employee.company_id == company_id,
                                               Employee.archived_at.is_(None))):  # fmt: skip
        if e.email.lower() in AUTH_EMAILS:
            continue
        e.active, e.archived_at = False, t
        e.archived_reason = "Replaced by the authorized user list (directive 2026-11-12)"
        accounts.sign_out(db, e.id, "account_archived")
        accounts.revoke_tokens(db, e.id)
        audit.record(db, action="employee.archived", success=True, company_id=company_id,
                     actor_id=by.id, target_type="employee", target_id=str(e.id),
                     detail={"email": e.email})  # fmt: skip
        out.append(e.email)
    db.commit()
    return {"archived": out}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", choices=["report", "backup", "users", "archive"])
    ap.add_argument("--company-id")
    ap.add_argument("--by", help="e-mail of the Super Admin doing this (for the audit trail)")
    a = ap.parse_args(argv)
    if a.step == "backup":
        print(json.dumps(backup(), indent=2))
        return
    if not a.company_id:
        sys.exit("--company-id is required")
    with get_sessionmaker()() as db:
        cid = _company(db, a.company_id)
        if a.step == "report":
            print(json.dumps(report(db, cid), indent=2))
        elif a.step == "users":
            print(json.dumps(apply_users(db, cid, a.by or ""), indent=2))
        else:
            print(json.dumps(archive_legacy(db, cid, a.by or ""), indent=2))


if __name__ == "__main__":
    main()
