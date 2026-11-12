"""User replacement (directive 2026-11-12, s.2) on a synthetic company: the seven identities are
upserted with invitations, nothing is deleted, history stays attributed, and archiving is
refused until a new Super Admin can actually sign in."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text

from pro_trading import user_replacement as ur
from pro_trading.db import get_sessionmaker
from pro_trading.larkmail.models import MailAccess, MailAccount
from pro_trading.models import AuditEvent, AuthSession, Employee
from pro_trading.security import hash_password

from .conftest import PASSWORD


def _cleanup_global_emails() -> None:
    """The seven addresses are unique across companies: free them from an earlier test run by
    renaming (never deleting) the rows."""
    with get_sessionmaker()() as db, db.begin():
        db.execute(
            text("UPDATE employees SET email = id::text || '.old@example.test' "
                 "WHERE lower(email) = ANY(:e)"),
            {"e": sorted(ur.AUTH_EMAILS)},
        )  # fmt: skip
        db.execute(
            text("UPDATE mail_accounts SET address = id::text || '.old@example.test' "
                 "WHERE lower(address) LIKE '%@tamishatrading.com'")
        )  # fmt: skip


def test_replacement_is_staged_safe_and_idempotent(world: Any, make_api: Any) -> None:
    _cleanup_global_emails()
    cid = world.company_id
    with get_sessionmaker()() as db:
        rep = ur.report(db, cid)
        assert len(rep["to_create"]) == 7 and rep["foreign_keys_to_employees"] > 20
        assert all(a["plan"].startswith("archive") for a in rep["accounts"])
        out = ur.apply_users(db, cid, world.emails["owner"])
    assert {u["email"] for u in out["users"]} == ur.AUTH_EMAILS
    assert len(out["mailboxes"]) == 8
    with get_sessionmaker()() as db:
        again = ur.apply_users(db, cid, world.emails["owner"])
        assert not any(u["created"] for u in again["users"])
        assert db.scalar(select(func.count()).where(Employee.company_id == cid)) == 6 + 7
        info = db.scalar(
            select(MailAccount).where(MailAccount.address == "info@tamishatrading.com")
        )
        assert info.kind == "SHARED" and info.status == "NOT_CONFIGURED"
        owners = db.scalars(select(Employee.display_name).join(
            MailAccess, MailAccess.employee_id == Employee.id).where(
            MailAccess.account_id == info.id, MailAccess.revoked_at.is_(None))).all()  # fmt: skip
        assert sorted(owners) == ["Asha Narine", "Saddam Hussain"]
        assert db.scalar(select(func.count()).select_from(MailAccount).where(
            MailAccount.company_id == cid)) == 8  # fmt: skip
        andy = db.scalar(select(Employee).where(Employee.email == "andy@tamishatrading.com"))
        assert andy.password_hash is None  # nobody's password is set here: invitation only
        try:
            ur.archive_legacy(db, cid, world.emails["owner"])
            raise AssertionError("archive must be refused before a new Super Admin can sign in")
        except SystemExit as e:
            assert "Refused" in str(e)
    # a legacy user signed in now; Saddam accepts the invitation (simulated: password set)
    legacy = make_api()
    assert legacy.login(world.emails["sales"]).status_code == 200
    with get_sessionmaker()() as db, db.begin():
        s = db.scalar(select(Employee).where(Employee.email == "saddam@tamishatrading.com"))
        s.password_hash = hash_password(PASSWORD)
    with get_sessionmaker()() as db:
        res = ur.archive_legacy(db, cid, world.emails["owner"])
    assert len(res["archived"]) == 6
    with get_sessionmaker()() as db:
        rows = db.scalars(select(Employee).where(Employee.company_id == cid)).all()
        assert len(rows) == 13  # nothing deleted
        active = sorted(e.email for e in rows if e.active)
        assert active == sorted(ur.AUTH_EMAILS)
        assert db.scalar(select(func.count()).select_from(AuthSession).where(
            AuthSession.employee_id == world.ids["sales"], AuthSession.revoked_at.is_(None))) == 0  # fmt: skip  # noqa: E501
        assert db.scalar(select(func.count()).where(AuditEvent.action == "employee.archived",
                                                    AuditEvent.company_id == cid)) == 6  # fmt: skip
    # legacy accounts can't sign in; the old session no longer works
    assert legacy.get("/api/v1/auth/session").status_code == 401
    assert make_api().login(world.emails["sales"]).status_code in (401, 403)
    # Saddam (Super Admin) signs in; archived accounts leave the normal user list
    sa = make_api()
    assert sa.login("saddam@tamishatrading.com").status_code == 200
    names = [u["email"] for u in sa.get("/api/v1/employees").json()]
    assert sorted(names) == sorted(ur.AUTH_EMAILS)
    assert len(sa.get("/api/v1/employees?archived=true").json()) == 6
    # The Super Admin has no access to anyone's mail content just by being Super Admin:
    assert sorted(a["address"] for a in sa.get("/api/v1/mail/accounts").json()) == [
        "info@tamishatrading.com", "saddam@tamishatrading.com"]  # fmt: skip
    _cleanup_global_emails()
