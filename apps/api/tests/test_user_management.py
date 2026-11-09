"""User management & authentication module (2026-11-09): six roles, Super-Admin-only user
administration, invitations, forgot/reset password, sessions, security events, last Super
Admin protection and the bootstrap command. Two-step sign-in itself is in test_mfa.py."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text, update

from pro_trading import create_super_admin
from pro_trading.bootstrap import ensure_default_roles
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.models import (
    AuditEvent,
    AuthSession,
    AuthToken,
    EmailMessage,
    Employee,
    EmployeeRole,
    Role,
)
from pro_trading.permissions import DEFAULT_ROLES
from pro_trading.security import hash_password, verify_password

from .conftest import PASSWORD, World, idem, mailed_token

P = "/api/v1"
NEW_PW = "Plantain-Season-2026!"


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


def add_person(world: World, role: str, *, password: bool = True, wh: Any = None) -> str:
    email = f"{role.lower()}.{uuid.uuid4().hex[:6]}@example.test"
    with get_sessionmaker()() as db, db.begin():
        roles = ensure_default_roles(db, world.company_id)
        e = Employee(
            company_id=world.company_id,
            email=email,
            display_name=f"{role.title()} Person",
            password_hash=hash_password(PASSWORD) if password else None,
        )
        db.add(e)
        db.flush()
        db.add(EmployeeRole(employee_id=e.id, role_id=roles[role].id, warehouse_id=wh))
        world.emails[email], world.ids[email] = email, e.id
    return email


@pytest.fixture
def owner(make_api, world):
    o = make_api()
    assert o.login(world.emails["owner"]).status_code == 200
    return o


def signed_in(make_api, email: str):
    a = make_api()
    assert a.login(email).status_code == 200, email
    return a


def emails_to(addr: str) -> list[EmailMessage]:
    with get_sessionmaker()() as db:
        return list(db.scalars(select(EmailMessage).where(EmailMessage.to_addr == addr)).all())


# ---------------------------------------------------------------- roles


def test_six_roles_with_owner_names(owner, world):
    r = owner.get(f"{P}/roles")
    assert r.status_code == 200, r.text
    names = [(x["code"], x["name"]) for x in r.json()["roles"]]
    assert names == [
        ("OWNER_ADMIN", "Super Admin"),
        ("MANAGER", "Admin"),
        ("SALES_OPERATOR", "Sales"),
        ("WAREHOUSE", "Warehouse"),
        ("FINANCE", "Accounting"),
        ("VIEWER", "Viewer"),
    ]
    by = {x["code"]: x for x in r.json()["roles"]}
    assert all(x["description"] for x in by.values())
    # Only Super Admin can administer users and security settings.
    for c, role in by.items():
        has_admin = {"employee.manage", "feature_flag.manage"} & set(role["permissions"])
        assert bool(has_admin) == (c == "OWNER_ADMIN"), c
    assert set(by["VIEWER"]["permissions"]) == {
        "warehouse.read",
        "catalog.read",
        "sales_order.read",
        "inventory.read",
    }
    assert by["OWNER_ADMIN"]["members"] >= 1


def test_viewer_reads_but_changes_nothing(make_api, world):
    v = signed_in(make_api, add_person(world, "VIEWER"))
    assert v.get(f"{P}/customers").status_code == 200
    assert v.get(f"{P}/products").status_code == 200
    for url in (f"{P}/payments", f"{P}/employees", f"{P}/roles", f"{P}/reports/sales"):
        assert v.get(url).status_code == 403, url
    r = v.post(f"{P}/customers", json={"name": "X", "external_ref": "X1"}, headers=idem())
    assert r.status_code == 403


ADMIN_ONLY = [
    ("post", "/employees", {"email": "e@example.test", "display_name": "E", "role_code": "VIEWER"}),
    ("patch", "/employees/{sales}", {"role_code": "OWNER_ADMIN"}),
    ("patch", "/employees/{sales}", {"active": False}),
    ("post", "/employees/{sales}/invite", None),
    ("post", "/employees/{sales}/reset-password", {"password": NEW_PW}),
    ("post", "/employees/{sales}/reset-mfa", None),
    ("post", "/auth/employees/{sales}/revoke-sessions", None),
    ("get", "/employees", None),
    ("get", "/roles", None),
    ("get", "/security/sessions", None),
    ("get", "/security/events", None),
    ("get", "/security/settings", None),
    ("put", "/feature-flags/require_two_step", {"enabled": True}),
]


@pytest.mark.parametrize("role", ["MANAGER", "SALES_OPERATOR", "WAREHOUSE", "FINANCE", "VIEWER"])
def test_only_super_admin_manages_users_roles_and_security(make_api, world, role):
    a = signed_in(make_api, add_person(world, role))
    for method, path, body in ADMIN_ONLY:
        url = P + path.format(sales=world.ids["sales"])
        kw: dict[str, Any] = {"headers": idem()} if method != "get" else {}
        if body is not None:
            kw["json"] = body
        r = getattr(a, method)(url, **kw)
        assert r.status_code == 403 and code(r) == "FORBIDDEN", (role, method, path, r.text)
    with get_sessionmaker()() as db:  # nothing changed
        e = db.get(Employee, world.ids["sales"])
        assert e is not None and e.active
        roles = db.scalars(
            select(Role.code)
            .join(EmployeeRole, EmployeeRole.role_id == Role.id)
            .where(EmployeeRole.employee_id == e.id)
        ).all()
        assert roles == ["SALES_OPERATOR"]


def test_a_warehouse_scoped_super_admin_cannot_manage_users(make_api, world):
    a = signed_in(make_api, world.emails["admin_a"])  # Super Admin role, warehouse A only
    assert a.get(f"{P}/employees").status_code == 403
    r = a.patch(f"{P}/employees/{world.ids['sales']}", json={"active": False}, headers=idem())
    assert r.status_code == 403


def test_super_admin_assigns_roles(owner, make_api, world):
    sales = signed_in(make_api, world.emails["sales"])
    sid = world.ids["sales"]
    r = owner.patch(f"{P}/employees/{sid}", json={"role_code": "VIEWER"}, headers=idem())
    assert r.status_code == 200 and r.json()["roles"] == ["VIEWER"]
    assert sales.get(f"{P}/auth/session").status_code == 401  # signed out by the change
    v = signed_in(make_api, world.emails["sales"])
    assert v.get(f"{P}/customers").status_code == 200
    assert v.post(f"{P}/sales-orders", json={}, headers=idem()).status_code in (403, 422)


# ---------------------------------------------------------------- passwords


def test_passwords_are_argon2id_hashed():
    h = hash_password(NEW_PW)
    assert h.startswith("$argon2id$") and NEW_PW not in h
    assert verify_password(h, NEW_PW)
    assert not verify_password(h, NEW_PW + "x")
    assert not verify_password(None, NEW_PW)
    assert hash_password(NEW_PW) != h  # salted


def test_no_plaintext_password_is_stored(owner, make_api, world):
    a = signed_in(make_api, world.emails["sales"])
    r = a.post(f"{P}/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PW})
    assert r.status_code == 204
    with get_sessionmaker()() as db:
        dump = db.execute(
            text(
                "select coalesce(string_agg(e::text, ''), '') from employees e"
                " where company_id = :c"
            ),
            {"c": world.company_id},
        ).scalar_one()
        audit = db.execute(
            text(
                "select string_agg(detail_redacted::text, '') from audit_events where company_id=:c"
            ),
            {"c": world.company_id},
        ).scalar_one()
    assert NEW_PW not in dump and PASSWORD not in dump
    assert NEW_PW not in (audit or "")


# ---------------------------------------------------------------- invitations


def invite(owner, email: str, role: str = "SALES_OPERATOR") -> Any:
    body = {"email": email, "display_name": "Ivy Invite", "role_code": role}
    return owner.post(f"{P}/employees", json=body, headers=idem())


def test_invitation_accept_once(owner, make_api, world):
    email = f"ivy.{uuid.uuid4().hex[:6]}@example.test"
    assert invite(owner, email).status_code == 201
    token = mailed_token(email)
    a = make_api()
    r = a.post(f"{P}/auth/token/inspect", json={"token": token, "purpose": "INVITE"})
    assert r.status_code == 200 and r.json()["email"] == email
    r = a.post(f"{P}/auth/token/inspect", json={"token": token, "purpose": "PASSWORD_RESET"})
    assert r.status_code == 400 and code(r) == "TOKEN_INVALID"  # wrong kind of link
    assert (
        a.post(f"{P}/auth/invitation/accept", json={"token": token, "password": NEW_PW}).status_code
        == 204
    )
    r = a.post(f"{P}/auth/invitation/accept", json={"token": token, "password": NEW_PW + "x"})
    assert r.status_code == 400 and code(r) == "TOKEN_INVALID"  # single use
    assert a.login(email, NEW_PW).status_code == 200
    with get_sessionmaker()() as db:
        tok = db.scalar(
            select(AuthToken)
            .where(AuthToken.purpose == "INVITE")
            .order_by(AuthToken.created_at.desc())
        )
        assert tok is not None and token not in tok.token_sha256 and len(tok.token_sha256) == 64


def test_invitation_expires(owner, make_api, world):
    email = f"late.{uuid.uuid4().hex[:6]}@example.test"
    r = invite(owner, email)
    token = mailed_token(email)
    with get_sessionmaker()() as db, db.begin():
        db.execute(
            update(AuthToken)
            .where(AuthToken.employee_id == uuid.UUID(r.json()["id"]))
            .values(
                created_at=func.now() - timedelta(days=5),
                expires_at=func.now() - timedelta(seconds=1),
            )
        )
    a = make_api()
    r2 = a.post(f"{P}/auth/invitation/accept", json={"token": token, "password": NEW_PW})
    assert r2.status_code == 400 and code(r2) == "TOKEN_INVALID"
    # A Super Admin resends; only the newest link works.
    rs = owner.post(f"{P}/employees/{r.json()['id']}/invite")
    assert rs.status_code == 200 and rs.json()["invite_expires_at"]
    fresh = mailed_token(email)
    assert fresh != token
    assert (
        a.post(f"{P}/auth/invitation/accept", json={"token": fresh, "password": NEW_PW}).status_code
        == 204
    )
    assert owner.post(f"{P}/employees/{r.json()['id']}/invite").status_code == 409  # accepted


def test_resend_invite_revokes_the_old_link(owner, make_api, world):
    email = f"two.{uuid.uuid4().hex[:6]}@example.test"
    r = invite(owner, email)
    first = mailed_token(email)
    owner.post(f"{P}/employees/{r.json()['id']}/invite")
    a = make_api()
    r2 = a.post(f"{P}/auth/invitation/accept", json={"token": first, "password": NEW_PW})
    assert r2.status_code == 400


def test_suspending_an_invited_person_kills_the_link(owner, make_api, world):
    email = f"sus.{uuid.uuid4().hex[:6]}@example.test"
    r = invite(owner, email)
    token = mailed_token(email)
    owner.patch(f"{P}/employees/{r.json()['id']}", json={"active": False}, headers=idem())
    a = make_api()
    r2 = a.post(f"{P}/auth/invitation/accept", json={"token": token, "password": NEW_PW})
    assert r2.status_code == 400


def test_link_tokens_are_throttled_per_ip(make_api, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_ip_max_failures", 3)
    from pro_trading import ratelimit

    ratelimit.clear("link_ip", "testclient")
    a = make_api()
    bad = {"token": "x" * 43, "password": NEW_PW}
    for _ in range(3):
        assert a.post(f"{P}/auth/password/reset", json=bad).status_code == 400
    r = a.post(f"{P}/auth/password/reset", json=bad)
    assert r.status_code == 429 and code(r) == "TOO_MANY_ATTEMPTS"
    ratelimit.clear("link_ip", "testclient")


# ---------------------------------------------------------------- forgot / reset


def test_forgot_password_never_reveals_accounts(make_api, world):
    a = make_api()
    unknown = a.post(f"{P}/auth/password/forgot", json={"email": "nobody.here@example.test"})
    known = a.post(f"{P}/auth/password/forgot", json={"email": world.emails["sales"].upper()})
    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert emails_to(world.emails["sales"])  # only the real account gets mail
    assert not emails_to("nobody.here@example.test")


def test_reset_password_once_and_sign_out_everywhere(make_api, world):
    email = world.emails["sales"]
    other_device = signed_in(make_api, email)
    a = make_api()
    a.post(f"{P}/auth/password/forgot", json={"email": email})
    token = mailed_token(email, "reset-password")
    r = a.post(f"{P}/auth/password/reset", json={"token": token, "password": "short"})
    assert r.status_code == 422
    r = a.post(f"{P}/auth/password/reset", json={"token": token, "password": NEW_PW})
    assert r.status_code == 204, r.text
    assert other_device.get(f"{P}/auth/session").status_code == 401  # revoked
    assert make_api().login(email).status_code == 401  # old password gone
    assert make_api().login(email, NEW_PW).status_code == 200
    r = a.post(f"{P}/auth/password/reset", json={"token": token, "password": NEW_PW + "!"})
    assert r.status_code == 400 and code(r) == "TOKEN_INVALID"
    subjects = [m.subject for m in emails_to(email)]
    assert any("password was changed" in s for s in subjects)  # security notice


def test_reset_link_expires(make_api, world):
    email = world.emails["wh_a"]
    a = make_api()
    a.post(f"{P}/auth/password/forgot", json={"email": email})
    token = mailed_token(email, "reset-password")
    with get_sessionmaker()() as db, db.begin():
        db.execute(
            update(AuthToken)
            .where(AuthToken.employee_id == world.ids["wh_a"])
            .values(
                created_at=func.now() - timedelta(hours=2),
                expires_at=func.now() - timedelta(minutes=1),
            )
        )
    r = a.post(f"{P}/auth/password/reset", json={"token": token, "password": NEW_PW})
    assert r.status_code == 400 and code(r) == "TOKEN_INVALID"


def test_a_new_reset_link_replaces_the_old_one_and_is_capped(make_api, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "password_reset_max_per_hour", 2)
    email = world.emails["finance"]
    a = make_api()
    a.post(f"{P}/auth/password/forgot", json={"email": email})
    first = mailed_token(email, "reset-password")
    a.post(f"{P}/auth/password/forgot", json={"email": email})
    second = mailed_token(email, "reset-password")
    assert first != second
    a.post(f"{P}/auth/password/forgot", json={"email": email})  # over the cap: no new mail
    assert mailed_token(email, "reset-password") == second
    r = a.post(f"{P}/auth/password/reset", json={"token": first, "password": NEW_PW})
    assert r.status_code == 400
    assert (
        a.post(f"{P}/auth/password/reset", json={"token": second, "password": NEW_PW}).status_code
        == 204
    )


def test_suspended_people_get_no_reset_mail(owner, make_api, world):
    sid = world.ids["sales"]
    owner.patch(f"{P}/employees/{sid}", json={"active": False}, headers=idem())
    before = len(emails_to(world.emails["sales"]))
    r = make_api().post(f"{P}/auth/password/forgot", json={"email": world.emails["sales"]})
    assert r.status_code == 202
    assert len(emails_to(world.emails["sales"])) == before


# ---------------------------------------------------------------- sign-in protection, sessions


def test_lockout_after_repeated_wrong_passwords(make_api, world):
    s = get_settings()
    a = make_api()
    for _ in range(s.login_max_failures):
        assert a.login(world.emails["wh_a"], "wrong-password!").status_code == 401
    r = a.login(world.emails["wh_a"])  # right password, but locked now
    assert r.status_code == 423 and code(r) == "ACCOUNT_LOCKED"


def test_sessions_expire_when_idle_or_too_old(make_api, world):
    a = signed_in(make_api, world.emails["sales"])
    s = get_settings()
    with get_sessionmaker()() as db, db.begin():
        db.execute(
            update(AuthSession)
            .where(AuthSession.employee_id == world.ids["sales"])
            .values(last_seen_at=func.now() - timedelta(minutes=s.session_idle_minutes + 1))
        )
    assert a.get(f"{P}/auth/session").status_code == 401
    b = signed_in(make_api, world.emails["sales"])
    with get_sessionmaker()() as db, db.begin():
        db.execute(
            update(AuthSession)
            .where(AuthSession.employee_id == world.ids["sales"], AuthSession.revoked_at.is_(None))
            .values(expires_at=func.now() - timedelta(seconds=1))
        )
    r = b.get(f"{P}/auth/session")
    assert r.status_code == 401 and code(r) == "SESSION_INVALID"


def test_logout_ends_the_session(make_api, world):
    a = signed_in(make_api, world.emails["sales"])
    assert a.post(f"{P}/auth/logout").status_code == 204
    assert a.get(f"{P}/auth/session").status_code == 401


def test_my_sessions_and_revoking_them(make_api, world):
    email = world.emails["finance"]
    a, b, c = (signed_in(make_api, email) for _ in range(3))
    rows = a.get(f"{P}/auth/sessions").json()
    assert len(rows) == 3 and sum(r["current"] for r in rows) == 1
    assert all(r["ip_address"] for r in rows)
    b_id = next(r["id"] for r in b.get(f"{P}/auth/sessions").json() if r["current"])
    me = next(r["id"] for r in rows if r["current"])
    assert a.post(f"{P}/auth/sessions/{me}/revoke").status_code == 422  # use Sign out
    assert a.post(f"{P}/auth/sessions/{b_id}/revoke").status_code == 204
    assert b.get(f"{P}/auth/session").status_code == 401
    assert c.get(f"{P}/auth/session").status_code == 200
    other = signed_in(make_api, world.emails["sales"])
    assert other.post(f"{P}/auth/sessions/{me}/revoke").status_code == 404  # not theirs
    assert a.post(f"{P}/auth/sessions/revoke-others").status_code == 204
    assert c.get(f"{P}/auth/session").status_code == 401
    assert a.get(f"{P}/auth/session").status_code == 200


def test_super_admin_sees_and_revokes_company_sessions(owner, make_api, world):
    victim = signed_in(make_api, world.emails["sales"])
    rows = owner.get(f"{P}/security/sessions").json()
    mine = [r for r in rows if r["employee_email"] == world.emails["sales"]]
    assert mine and mine[0]["employee_name"] == "sales"
    assert owner.post(f"{P}/security/sessions/{mine[0]['id']}/revoke").status_code == 204
    assert victim.get(f"{P}/auth/session").status_code == 401
    me = next(r["id"] for r in rows if r["current"])
    assert owner.post(f"{P}/security/sessions/{me}/revoke").status_code == 422
    assert owner.post(f"{P}/security/sessions/{uuid.uuid4()}/revoke").status_code == 404


def test_admin_revoke_all_sessions(owner, make_api, world):
    victim = signed_in(make_api, world.emails["wh_a"])
    r = owner.post(f"{P}/auth/employees/{world.ids['wh_a']}/revoke-sessions")
    assert r.status_code == 204
    assert victim.get(f"{P}/auth/session").status_code == 401


def test_login_history_and_security_events(owner, make_api, world):
    make_api().login(world.emails["sales"], "wrong-password!")
    a = signed_in(make_api, world.emails["sales"])
    hist = a.get(f"{P}/auth/activity").json()
    logins = [h for h in hist if h["action"] == "auth.login"]
    assert [h["success"] for h in logins[:2]] == [True, False]
    assert logins[1]["reason"] == "bad_password" and logins[0]["ip_address"]
    ev = owner.get(f"{P}/security/events", params={"failed_only": True, "kind": "sign_in"}).json()
    assert any(e["actor_name"] == "sales" and e["action"] == "auth.login" for e in ev)
    page1 = owner.get(f"{P}/security/events", params={"limit": 2}).json()
    page2 = owner.get(
        f"{P}/security/events", params={"limit": 2, "before": page1[-1]["created_at"]}
    ).json()
    assert page2 and page2[0]["created_at"] < page1[-1]["created_at"]
    s = owner.get(f"{P}/security/settings").json()
    assert s["two_step_required"] is False and s["password_min_length"] == 12


# ---------------------------------------------------------------- last Super Admin


def super_admins(world: World) -> int:
    """Active, signed-up, company-wide Super Admins (the ones that can manage users)."""
    with get_sessionmaker()() as db:
        return (
            db.scalar(
                select(func.count(func.distinct(Employee.id)))
                .join(EmployeeRole, EmployeeRole.employee_id == Employee.id)
                .join(Role, Role.id == EmployeeRole.role_id)
                .where(
                    Employee.company_id == world.company_id,
                    Employee.active.is_(True),
                    Employee.password_hash.is_not(None),
                    EmployeeRole.warehouse_id.is_(None),
                    Role.code == "OWNER_ADMIN",
                )
            )
            or 0
        )


def test_one_super_admin_always_remains(make_api, world):
    owner = signed_in(make_api, world.emails["owner"])
    oid = world.ids["owner"]
    assert super_admins(world) == 1
    for body in ({"active": False}, {"role_code": "MANAGER"}):  # alone: can't remove self
        r = owner.patch(f"{P}/employees/{oid}", json=body, headers=idem())
        assert r.status_code == 422 and code(r) == "SELF_CHANGE"
    second = add_person(world, "OWNER_ADMIN")
    two = signed_in(make_api, second)
    r = two.patch(f"{P}/employees/{oid}", json={"role_code": "MANAGER"}, headers=idem())
    assert r.status_code == 200  # two Super Admins: one may demote the other
    assert owner.get(f"{P}/auth/session").status_code == 401  # signed out by the change
    assert super_admins(world) == 1
    r = two.patch(f"{P}/employees/{world.ids[second]}", json={"active": False}, headers=idem())
    assert r.status_code == 422 and code(r) == "SELF_CHANGE"
    demoted = signed_in(make_api, world.emails["owner"])  # now an Admin: no user management
    r = demoted.patch(f"{P}/employees/{world.ids[second]}", json={"active": False}, headers=idem())
    assert r.status_code == 403
    assert super_admins(world) == 1


def test_last_super_admin_rule_counts_only_signed_up_company_wide_admins(make_api, world):
    """LAST_OWNER is the backstop behind SELF_CHANGE: an open invitation or a warehouse-scoped
    Super Admin (admin_a) can't manage users, so neither counts as a remaining one."""
    actor = add_person(world, "OWNER_ADMIN")
    x = signed_in(make_api, actor)
    invited = add_person(world, "OWNER_ADMIN", password=False)
    # Take the actor out of the count (as if their account could no longer sign in).
    with get_sessionmaker()() as db, db.begin():
        db.execute(
            update(Employee).where(Employee.id == world.ids[actor]).values(password_hash=None)
        )
    oid = world.ids["owner"]
    for body in ({"active": False}, {"role_code": "VIEWER"}):
        r = x.patch(f"{P}/employees/{oid}", json=body, headers=idem())
        assert r.status_code == 422 and code(r) == "LAST_OWNER", r.text
    with get_sessionmaker()() as db, db.begin():  # the invitee sets a password
        db.execute(
            update(Employee)
            .where(Employee.id == world.ids[invited])
            .values(password_hash=hash_password(PASSWORD))
        )
    r = x.patch(f"{P}/employees/{oid}", json={"active": False}, headers=idem())
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- bootstrap


def test_bootstrap_creates_the_first_super_admin_once():
    tag = uuid.uuid4().hex[:8]
    company = f"Bootstrap Co {tag}"
    email = f"first.{tag}@example.test"
    with get_sessionmaker()() as db, db.begin():
        emp = create_super_admin.create(
            db, company_name=company, email=email, name="First Admin", password=NEW_PW
        )
        assert emp.password_hash and emp.password_hash.startswith("$argon2id$")
    with get_sessionmaker()() as db:
        roles = db.scalars(
            select(Role.code)
            .join(EmployeeRole, EmployeeRole.role_id == Role.id)
            .where(EmployeeRole.employee_id == emp.id)
        ).all()
        assert roles == ["OWNER_ADMIN"]
        n_roles = db.scalar(
            select(func.count()).select_from(Role).where(Role.company_id == emp.company_id)
        )
        assert n_roles == len(DEFAULT_ROLES) == 6
        ev = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "employee.bootstrap_super_admin",
                AuditEvent.target_id == str(emp.id),
            )
        )
        assert ev is not None and NEW_PW not in str(ev.detail_redacted)
    with pytest.raises(ValueError, match="already has a Super Admin"):
        with get_sessionmaker()() as db, db.begin():
            create_super_admin.create(
                db,
                company_name=company,
                email=f"second.{tag}@example.test",
                name="X",
                password=NEW_PW,
            )


def test_bootstrap_cli_refuses_weak_passwords(monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    with pytest.raises(SystemExit):
        create_super_admin.main(
            ["--company", "X", "--email", "x@example.test", "--name", "X", "--password-stdin"]
        )


def test_bootstrap_cli_reads_the_password_from_stdin(monkeypatch, capsys):
    import io

    tag = uuid.uuid4().hex[:8]
    monkeypatch.setattr("sys.stdin", io.StringIO(NEW_PW + "\n"))
    rc = create_super_admin.main(
        [
            "--company",
            f"CLI Co {tag}",
            "--email",
            f"cli.{tag}@example.test",
            "--name",
            "Cli",
            "--password-stdin",
        ]
    )
    assert rc == 0
    out = capsys.readouterr()
    assert NEW_PW not in out.out + out.err
