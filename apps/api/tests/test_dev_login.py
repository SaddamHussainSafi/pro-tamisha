"""DEV_AUTO_LOGIN_EMAIL: development-only sign-in without password or MFA."""

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from pro_trading.config import Settings, get_settings
from pro_trading.models import AuditEvent

URL = "/api/v1/auth/dev-login"


def test_off_by_default(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_auto_login_email", "")
    r = api.post(URL)
    assert r.status_code == 404


def test_signs_in_privileged_user_without_password_or_mfa(api, world, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_auto_login_email", world.emails["owner"].upper())
    r = api.post(URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["employee"]["email"] == world.emails["owner"]
    assert body["mfa_required"] is False
    assert "employee.manage" in body["permissions"]
    assert "HttpOnly" in r.headers["set-cookie"]
    api.csrf = body["csrf_token"]
    assert api.get("/api/v1/auth/session").json()["mfa_required"] is False
    ev = db.scalars(
        select(AuditEvent).where(
            AuditEvent.actor_id == world.ids["owner"], AuditEvent.action == "auth.dev_login"
        )
    ).all()
    assert ev and ev[0].success


def test_unknown_employee_is_404(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_auto_login_email", "nobody@example.test")
    assert api.post(URL).status_code == 404


def test_refused_when_production_like(api, world, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "dev_auto_login_email", world.emails["owner"])
    monkeypatch.setattr(s, "app_env", "production")
    assert api.post(URL).status_code == 404


@pytest.mark.parametrize("env", ["staging", "production"])
def test_settings_refuse_to_load_in_production_like_env(env):
    with pytest.raises(ValidationError):
        Settings(app_env=env, dev_auto_login_email="owner@example.test")


def test_not_in_public_contract(app):
    assert "/api/v1/auth/dev-login" not in app.openapi()["paths"]
