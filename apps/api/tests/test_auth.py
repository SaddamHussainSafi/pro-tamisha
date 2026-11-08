from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from pro_trading.models import AuditEvent, AuthSession, Employee

from .conftest import PASSWORD


def test_login_sets_httponly_cookie_and_returns_session(api, world):
    r = api.login(world.emails["sales"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["employee"]["email"] == world.emails["sales"]
    assert body["mfa_required"] is False
    assert "csrf_token" in body
    cookie = r.headers["set-cookie"]
    assert "tami_session=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert api.get("/api/v1/auth/session").status_code == 200


def test_login_email_is_case_insensitive(api, world):
    assert api.login(world.emails["sales"].upper()).status_code == 200


def test_bad_password_is_generic_and_audited(api, world, db):
    r = api.login(world.emails["sales"], "wrong-password")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    r2 = api.login("nobody@example.test", "wrong-password")
    assert r2.json()["error"] == {**r2.json()["error"], "code": "INVALID_CREDENTIALS"}
    ev = db.scalars(
        select(AuditEvent).where(
            AuditEvent.actor_id == world.ids["sales"], AuditEvent.action == "auth.login"
        )
    ).all()
    assert ev and not ev[0].success
    assert "wrong-password" not in str(ev[0].detail_redacted)


def test_unauthenticated_is_401(api):
    r = api.get("/api/v1/auth/session")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"
    assert r.json()["error"]["request_id"]


def test_logout_revokes_session(api, world):
    api.login(world.emails["sales"])
    old_cookie = api.c.cookies.get("tami_session")
    assert api.post("/api/v1/auth/logout").status_code == 204
    api.c.cookies.set("tami_session", old_cookie)  # replaying the old cookie must fail
    r = api.get("/api/v1/auth/session")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "SESSION_INVALID"


def test_mutation_without_csrf_is_rejected(api, world):
    api.login(world.emails["sales"])
    api.csrf = None
    r = api.post("/api/v1/auth/logout")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_FAILED"


def test_foreign_origin_mutation_rejected(api, world):
    api.login(world.emails["sales"])
    r = api.post("/api/v1/auth/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ORIGIN_REJECTED"


def test_lockout_after_repeated_failures(api, world):
    email = world.emails["sales"]
    for _ in range(5):
        assert api.login(email, "nope").status_code == 401
    r = api.login(email, PASSWORD)  # correct password, but locked
    assert r.status_code == 423
    assert r.json()["error"]["code"] == "ACCOUNT_LOCKED"


def test_lockout_expires(api, world, db):
    db.execute(
        update(Employee)
        .where(Employee.id == world.ids["sales"])
        .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
    )
    db.commit()
    assert api.login(world.emails["sales"]).status_code == 200


def test_idle_session_expires(api, world, db):
    api.login(world.emails["sales"])
    db.execute(
        update(AuthSession)
        .where(AuthSession.employee_id == world.ids["sales"])
        .values(last_seen_at=datetime.now(UTC) - timedelta(hours=2))
    )
    db.commit()
    assert api.get("/api/v1/auth/session").status_code == 401


def test_admin_revokes_all_sessions(make_api, world):
    victim, admin = make_api(), make_api()
    victim.login(world.emails["sales"])
    admin.login(world.emails["owner"])
    assert admin.enroll_and_verify().status_code == 200
    r = admin.post(f"/api/v1/auth/employees/{world.ids['sales']}/revoke-sessions")
    assert r.status_code == 204, r.text
    assert victim.get("/api/v1/auth/session").status_code == 401


def test_warehouse_scoped_manager_cannot_revoke_company_sessions(make_api, world):
    mgr = make_api()
    mgr.login(world.emails["admin_a"])
    assert mgr.enroll_and_verify().status_code == 200
    r = mgr.post(f"/api/v1/auth/employees/{world.ids['sales']}/revoke-sessions")
    assert r.status_code == 403
