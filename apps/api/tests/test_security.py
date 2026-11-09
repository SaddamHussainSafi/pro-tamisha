"""Security sweep 2026-11 (docs/SECURITY_REVIEW.md): auth, sessions, throttles, settings,
headers, error envelope. Each test fails on the code before the fix it names."""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text, update

from pro_trading import ratelimit
from pro_trading.config import Settings, get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.main import create_app
from pro_trading.models import AuthSession, Company, Employee, EmployeeRole, Role
from pro_trading.security import hash_password

from .conftest import PASSWORD, idem

P = "/api/v1"
WEB = "http://localhost:5173"


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


# ------------------------------------------------------------------ settings


def _prod(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "production",
        "mfa_encryption_key": "q0f5L2m9ZPZ0g3W9H3nmVvG7Pq0V8k1oJ5sQ0yYy7Uk=",
        "scan_capability_key": "x" * 48,
        "cookie_secure": True,
        "frontend_origin": "https://erp.example.test",
        "ocr_provider": "tesseract",
        "dev_auto_login_email": "",  # apps/api/.env may set it for local dev
    }
    return Settings(**{**base, **kw})


def test_production_settings_accept_real_values():
    assert _prod().is_production_like


@pytest.mark.parametrize(
    "override,needle",
    [
        # The dev MFA key is a *valid* Fernet key, so it used to be accepted in production.
        ({"mfa_encryption_key": "dev-only-insecure-key-change-me-000000000000="}, "MFA"),
        ({"scan_capability_key": "dev-only-scan-capability-key-change-me"}, "SCAN"),
        ({"scan_capability_key": "short"}, "SCAN"),
        ({"cookie_secure": False}, "COOKIE_SECURE"),
        ({"frontend_origin": "http://erp.example.test"}, "https"),
        ({"ocr_provider": "fake"}, "OCR_PROVIDER"),
        ({"drive_fault": "timeout"}, "DRIVE_FAULT"),
    ],
)
def test_production_settings_refuse_dev_placeholders(override, needle):
    with pytest.raises(ValidationError, match=needle):
        _prod(**override)


# ------------------------------------------------------------------ login throttle


@pytest.fixture
def from_ip(app):
    """A client with its own address, so the per-IP counter starts at zero."""
    ip = f"203.0.113.{uuid.uuid4().int % 250 + 1}"
    ratelimit.clear("login_ip", ip)
    ratelimit.clear("bridge_ip", ip)
    yield ip, TestClient(app, client=(ip, 40000))
    ratelimit.clear("login_ip", ip)
    ratelimit.clear("bridge_ip", ip)


def test_failed_sign_ins_are_throttled_per_ip(from_ip, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_ip_max_failures", 3)
    _, c = from_ip
    # password spraying: one guess each against different accounts never trips the
    # per-account lockout, so only a per-IP limit stops it
    for name in ("sales", "finance", "wh_a"):
        r = c.post(f"{P}/auth/login", json={"email": world.emails[name], "password": "guess-1!"})
        assert r.status_code == 401
    r = c.post(f"{P}/auth/login", json={"email": world.emails["owner"], "password": PASSWORD})
    assert r.status_code == 429 and code(r) == "TOO_MANY_ATTEMPTS"
    # another address is unaffected
    assert (
        TestClient(c.app, client=("198.51.100.7", 1))
        .post(f"{P}/auth/login", json={"email": world.emails["owner"], "password": PASSWORD})
        .status_code
        == 200
    )


def test_unknown_emails_count_toward_the_ip_limit(from_ip, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_ip_max_failures", 2)
    ip, c = from_ip
    for _ in range(2):
        body = {"email": f"nobody-{uuid.uuid4().hex[:6]}@example.test", "password": "x" * 12}
        assert c.post(f"{P}/auth/login", json=body).status_code == 401
    assert ratelimit.failures("login_ip", ip) == 2
    body = {"email": "nobody@example.test", "password": "x" * 12}
    assert c.post(f"{P}/auth/login", json=body).status_code == 429


def test_scanner_auth_failures_are_throttled_per_ip(from_ip, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_ip_max_failures", 2)
    _, c = from_ip
    url = f"{P}/scan-bridge/jobs/{uuid.uuid4()}/upload"
    bad = {"Authorization": f"Device {uuid.uuid4()}.nope", "Content-Type": "application/pdf"}
    for _ in range(2):
        assert c.post(url, content=b"%PDF-1.4", headers=bad).status_code == 401
    r = c.post(url, content=b"%PDF-1.4", headers=bad)
    assert r.status_code == 429 and code(r) == "TOO_MANY_ATTEMPTS"


def test_scanner_upload_authenticates_before_reading_the_body(api, monkeypatch):
    # Before: the whole body (up to SCAN_MAX_BYTES) was buffered first, so anonymous clients
    # could make the API hold large uploads in memory; the size error (413) came before 401.
    monkeypatch.setattr(get_settings(), "scan_max_bytes", 1024)
    r = api.c.post(
        f"{P}/scan-bridge/jobs/{uuid.uuid4()}/upload",
        content=b"%PDF-1.4" + b"0" * 4096,
        headers={"Content-Type": "application/pdf"},
    )
    assert r.status_code == 401 and code(r) == "DEVICE_AUTH_FAILED"


# ------------------------------------------------------------------ MFA


def test_mfa_codes_cannot_be_brute_forced(api, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "mfa_max_failures", 5)
    api.login(world.emails["finance"])
    assert api.enroll_and_verify().status_code == 200
    api.login(world.emails["finance"])  # new session, MFA pending again
    for _ in range(5):
        r = api.post(f"{P}/auth/mfa/verify", json={"code": "000000"})
        assert r.status_code == 400 and code(r) == "MFA_INVALID"
    assert api.totp is not None
    # even the right code is refused now, until the window passes
    r = api.post(f"{P}/auth/mfa/verify", json={"code": api.totp.now()})
    assert r.status_code == 429 and code(r) == "TOO_MANY_ATTEMPTS"
    assert api.get(f"{P}/auth/session").json()["mfa_required"] is True


def test_a_success_resets_the_mfa_counter(api, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "mfa_max_failures", 3)
    api.login(world.emails["finance"])
    api.post(f"{P}/auth/mfa/enroll")
    for _ in range(2):
        assert api.post(f"{P}/auth/mfa/verify", json={"code": "000000"}).status_code == 400
    assert api.enroll_and_verify().status_code in (200, 409)
    # 2 failures + 1 success: the next wrong code is an ordinary 400, not a lockout
    assert api.post(f"{P}/auth/mfa/verify", json={"code": "000000"}).status_code == 400


# ------------------------------------------------------------------ logout


def _session_row(cookie: str) -> AuthSession:
    from pro_trading.security import sha256_hex

    with get_sessionmaker()() as db:
        s = db.scalar(select(AuthSession).where(AuthSession.token_sha256 == sha256_hex(cookie)))
        assert s is not None
        return s


def test_logout_from_a_tab_without_the_csrf_token(api, world):
    # The CSRF token lives in per-tab sessionStorage. A new tab has none; its "Sign out" used to
    # fail with CSRF_FAILED and leave the session alive on the server.
    api.login(world.emails["sales"])
    cookie = api.c.cookies.get("tami_session")
    api.csrf = None
    r = api.c.post(f"{P}/auth/logout", headers={"Origin": WEB})
    assert r.status_code == 204
    assert _session_row(cookie).revoked_reason == "logout"
    api.c.cookies.set("tami_session", cookie)
    assert api.get(f"{P}/auth/session").status_code == 401


def test_logout_still_needs_token_or_same_origin(api, world):
    api.login(world.emails["sales"])
    cookie = api.c.cookies.get("tami_session")
    api.csrf = None
    r = api.c.post(f"{P}/auth/logout")  # no token, no Origin header
    assert r.status_code == 403 and code(r) == "CSRF_FAILED"
    assert _session_row(cookie).revoked_at is None


def test_logout_with_an_expired_session_clears_the_cookie(api, world, db):
    api.login(world.emails["sales"])
    cookie = api.c.cookies.get("tami_session")
    s = _session_row(cookie)
    db.execute(
        update(AuthSession)
        .where(AuthSession.id == s.id)
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    db.commit()
    r = api.post(f"{P}/auth/logout")
    assert r.status_code == 204  # was 401, leaving the dead cookie in the browser
    assert 'tami_session=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"]


# ------------------------------------------------------------------ my password


def test_privileged_password_change_needs_mfa(api, world):
    # A stolen Owner password alone could change the password and sign the real Owner out.
    api.login(world.emails["owner"])
    r = api.post(
        f"{P}/auth/password",
        json={"current_password": PASSWORD, "new_password": "Brand-new-pass-77!"},
    )
    assert r.status_code == 403 and code(r) == "MFA_REQUIRED"
    assert api.enroll_and_verify().status_code == 200
    r = api.post(
        f"{P}/auth/password",
        json={"current_password": PASSWORD, "new_password": "Brand-new-pass-77!"},
    )
    assert r.status_code == 204, r.text


def test_guessing_the_current_password_locks_the_account(api, world, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_max_failures", 3)
    api.login(world.emails["sales"])
    for _ in range(3):
        r = api.post(
            f"{P}/auth/password",
            json={"current_password": "Wrong-guess-123!", "new_password": "Whatever-pass-9!"},
        )
        assert r.status_code in (401, 422)
    assert api.get(f"{P}/auth/session").status_code == 401  # signed out everywhere
    emp = db.get(Employee, world.ids["sales"])
    assert emp is not None and emp.locked_until is not None
    assert api.login(world.emails["sales"]).status_code == 423


# ------------------------------------------------------------------ owner race


def _second_owner(world) -> tuple[str, uuid.UUID]:
    email = f"owner2.{uuid.uuid4().hex[:6]}@example.test"
    with get_sessionmaker()() as db, db.begin():
        role = db.scalar(
            select(Role).where(Role.company_id == world.company_id, Role.code == "OWNER_ADMIN")
        )
        assert role is not None
        e = Employee(
            company_id=world.company_id,
            email=email,
            display_name="owner2",
            password_hash=hash_password(PASSWORD),
        )
        db.add(e)
        db.flush()
        db.add(EmployeeRole(employee_id=e.id, role_id=role.id))
        # leave exactly two active Owner / Admins: owner and owner2
        db.execute(update(Employee).where(Employee.id == world.ids["admin_a"]).values(active=False))
        return email, e.id


def _waiting_on_company_lock() -> int:
    with get_sessionmaker()() as db:
        return int(
            db.scalar(
                text(
                    "select count(*) from pg_stat_activity where datname = current_database()"
                    " and wait_event_type = 'Lock' and query ilike '%FROM companies%FOR UPDATE%'"
                )
            )
            or 0
        )


def race(company_id: uuid.UUID, calls: list[Any], waiting: int | None = None) -> list[Any]:
    """Hold the company row lock, start every call, wait until they all queue on it (or the
    given number does), release, and return the responses in call order."""
    results: list[Any] = [None] * len(calls)
    with get_sessionmaker()() as holder:
        holder.execute(select(Company).where(Company.id == company_id).with_for_update())

        def run(i: int, fn: Any) -> None:
            results[i] = fn()

        threads = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(calls)]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 10
        want = len(calls) if waiting is None else waiting
        while _waiting_on_company_lock() < want and time.monotonic() < deadline:
            time.sleep(0.05)
        holder.commit()
    for t in threads:
        t.join(15)
    return results


def test_two_owners_cannot_disable_each_other_at_once(make_api, world):
    email2, id2 = _second_owner(world)
    o1, o2 = make_api(), make_api()
    for a, email in ((o1, world.emails["owner"]), (o2, email2)):
        a.login(email)
        assert a.enroll_and_verify().status_code == 200
    r1, r2 = race(
        world.company_id,
        [
            lambda: o1.patch(f"{P}/employees/{id2}", json={"active": False}, headers=idem()),
            lambda: o2.patch(
                f"{P}/employees/{world.ids['owner']}", json={"active": False}, headers=idem()
            ),
        ],
    )
    assert sorted([r1.status_code, r2.status_code]) == [200, 422], (r1.text, r2.text)
    with get_sessionmaker()() as db:
        n = db.scalar(
            text(
                "select count(distinct e.id) from employees e join employee_roles er"
                " on er.employee_id = e.id join roles r on r.id = er.role_id"
                " where e.company_id = :c and e.active and r.code = 'OWNER_ADMIN'"
            ),
            {"c": world.company_id},
        )
    assert n == 1


# ------------------------------------------------------------------ feature flags


def test_feature_flags_need_the_manage_permission(make_api, world):
    sales = make_api()
    sales.login(world.emails["sales"])
    r = sales.get(f"{P}/feature-flags")
    assert r.status_code == 403 and code(r) == "FORBIDDEN"


def test_feature_flag_keys_are_validated(make_api, world):
    o = make_api()
    o.login(world.emails["owner"])
    assert o.enroll_and_verify().status_code == 200
    r = o.put(f"{P}/feature-flags/{'x' * 65}", json={"enabled": True})
    assert r.status_code == 422  # was a 500 (String(64) overflow)
    assert o.put(f"{P}/feature-flags/Bad-Key", json={"enabled": True}).status_code == 422
    assert o.put(f"{P}/feature-flags/scanner_bridge", json={"enabled": True}).status_code == 200


# ------------------------------------------------------------------ headers and errors


def test_security_headers_on_api_responses(api):
    r = api.get("/health/live")
    h = r.headers
    assert h["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in h["content-security-policy"]
    assert "script-src" not in h["content-security-policy"]  # default-src 'none' covers it
    assert h["cache-control"] == "no-store"
    assert h["x-content-type-options"] == "nosniff"
    assert "strict-transport-security" not in h  # plain-http dev


def test_hsts_when_cookies_are_secure(monkeypatch):
    monkeypatch.setattr(get_settings(), "cookie_secure", True)
    r = TestClient(create_app()).get("/health/live")
    assert r.headers["strict-transport-security"].startswith("max-age=")


def test_api_schema_and_docs_are_off_in_production(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_env", "production")
    c = TestClient(create_app())
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404, path


def test_unexpected_errors_use_the_envelope_and_hide_details():
    app = create_app()
    boom = APIRouter()

    @boom.get("/api/v1/_boom")
    def _boom() -> None:
        raise RuntimeError("password=hunter2 at /Users/owner/secret/path.py")

    app.include_router(boom)
    r = TestClient(app, raise_server_exceptions=False).get("/api/v1/_boom")
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "INTERNAL" and body["error"]["request_id"]
    assert "hunter2" not in r.text and "/Users/" not in r.text
