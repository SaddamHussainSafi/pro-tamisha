"""DEV_AUTO_LOGIN_EMAIL: development-only sign-in without password or MFA."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from pro_trading.config import Settings, get_settings
from pro_trading.models import AuditEvent

URL = "/api/v1/auth/dev-login"


@pytest.fixture
def local(app):
    """A browser on this machine: loopback client, localhost Host header."""
    return TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))


def test_off_by_default(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_auto_login_email", "")
    r = api.post(URL)
    assert r.status_code == 404


def test_signs_in_privileged_user_without_password_or_mfa(local, world, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_auto_login_email", world.emails["owner"].upper())
    r = local.post(URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["employee"]["email"] == world.emails["owner"]
    assert body["mfa_required"] is False
    assert "employee.manage" in body["permissions"]
    assert "HttpOnly" in r.headers["set-cookie"]
    assert local.get("/api/v1/auth/session").json()["mfa_required"] is False
    ev = db.scalars(
        select(AuditEvent).where(
            AuditEvent.actor_id == world.ids["owner"], AuditEvent.action == "auth.dev_login"
        )
    ).all()
    assert ev and ev[0].success


def test_unknown_employee_is_404(local, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_auto_login_email", "nobody@example.test")
    assert local.post(URL).status_code == 404


def test_refused_when_production_like(local, world, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "dev_auto_login_email", world.emails["owner"])
    monkeypatch.setattr(s, "app_env", "production")
    assert local.post(URL).status_code == 404


@pytest.mark.parametrize(
    "client,base_url",
    [
        (("192.168.1.20", 50000), "http://localhost:8000"),  # another machine on the LAN
        (("127.0.0.1", 50000), "http://evil.example:8000"),  # DNS rebinding: foreign Host
        (("10.0.0.5", 50000), "http://192.168.1.10:8000"),  # dev server exposed with --host
    ],
)
def test_refused_for_non_local_requests(app, world, monkeypatch, client, base_url):
    monkeypatch.setattr(get_settings(), "dev_auto_login_email", world.emails["owner"])
    c = TestClient(app, base_url=base_url, client=client)
    r = c.post(URL)
    assert r.status_code == 404
    assert "set-cookie" not in r.headers


def test_refused_when_frontend_is_not_localhost(local, world, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "dev_auto_login_email", world.emails["owner"])
    monkeypatch.setattr(s, "frontend_origin", "https://erp.tamishatrading.com")
    assert local.post(URL).status_code == 404


@pytest.mark.parametrize("env", ["staging", "production"])
def test_settings_refuse_to_load_in_production_like_env(env):
    with pytest.raises(ValidationError):
        Settings(app_env=env, dev_auto_login_email="owner@example.test")


def test_settings_refuse_dev_login_with_a_real_frontend_origin():
    # APP_ENV left at its "development" default on a deployed server must not enable it.
    with pytest.raises(ValidationError, match="APP_ENV|DEV_AUTO_LOGIN_EMAIL"):
        Settings(
            app_env="development",
            frontend_origin="https://erp.tamishatrading.com",
            dev_auto_login_email="owner@example.test",
        )


def test_not_in_public_contract(app):
    assert "/api/v1/auth/dev-login" not in app.openapi()["paths"]


def test_refused_when_a_proxy_forwards_a_remote_client(local, world, monkeypatch):
    # Vite's proxy reaches the API from 127.0.0.1; with `vite --host` the real caller is a LAN
    # address, passed on in X-Forwarded-For.
    monkeypatch.setattr(get_settings(), "dev_auto_login_email", world.emails["owner"])
    r = local.post(URL, headers={"X-Forwarded-For": "192.168.1.20, 127.0.0.1"})
    assert r.status_code == 404


def test_ipv4_mapped_loopback_counts_as_local(app, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_auto_login_email", world.emails["owner"])
    c = TestClient(app, base_url="http://localhost:8000", client=("::ffff:127.0.0.1", 1))
    assert c.post(URL, headers={"X-Forwarded-For": "::1"}).status_code == 200
