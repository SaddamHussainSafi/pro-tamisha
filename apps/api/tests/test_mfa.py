"""Optional two-step sign-in (TOTP + recovery codes). Not mandatory for anyone (owner,
2026-11-09); the `require_two_step` company flag (off) would make everyone set it up."""

import time
from urllib.parse import parse_qs, urlparse

import pyotp
from sqlalchemy import select

from pro_trading.models import Employee, FeatureFlag, MfaRecoveryCode

from .conftest import PASSWORD

P = "/api/v1"


def code(r):
    return r.json()["error"]["code"]


def test_no_role_needs_two_step_by_default(make_api, world):
    for who in ("owner", "manager_a", "finance", "sales", "wh_a"):
        a = make_api()
        body = a.login(world.emails[who]).json()
        assert body["mfa_required"] is False, who
        assert body["mfa_enrolled"] is False
        assert body["permissions"], who
    owner = make_api()
    owner.login(world.emails["owner"])
    assert owner.put(f"{P}/feature-flags/demo", json={"enabled": True}).status_code == 200


def test_turning_it_on_needs_the_password_and_a_code(api, world, db):
    api.login(world.emails["sales"])
    r = api.post(f"{P}/auth/mfa/enroll")
    assert r.status_code == 422 and code(r) == "PASSWORD_REQUIRED"
    r = api.post(f"{P}/auth/mfa/enroll", json={"password": "wrong-password"})
    assert r.status_code == 422 and code(r) == "WRONG_PASSWORD"
    r = api.post(f"{P}/auth/mfa/enroll", json={"password": PASSWORD})
    assert r.status_code == 200
    secret = parse_qs(urlparse(r.json()["otpauth_uri"]).query)["secret"][0]
    r = api.post(f"{P}/auth/mfa/enroll/confirm", json={"code": "000000"})
    assert r.status_code == 400 and code(r) == "MFA_INVALID"
    assert api.get(f"{P}/auth/session").json()["mfa_enrolled"] is False  # not on yet
    r = api.post(f"{P}/auth/mfa/enroll/confirm", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session"]["mfa_enrolled"] is True and body["session"]["mfa_required"] is False
    assert len(body["recovery_codes"]) == 10 and len(set(body["recovery_codes"])) == 10
    emp = db.scalar(select(Employee).where(Employee.id == world.ids["sales"]))
    assert emp is not None and emp.mfa_secret_enc is not None
    assert secret.encode() not in emp.mfa_secret_enc  # encrypted at rest
    stored = db.scalars(
        select(MfaRecoveryCode.code_sha256).where(MfaRecoveryCode.employee_id == emp.id)
    ).all()
    assert len(stored) == 10 and not set(body["recovery_codes"]) & set(stored)  # hashes only


def test_sign_in_asks_for_the_code_only_when_turned_on(make_api, world):
    a = make_api()
    a.login(world.emails["finance"])
    assert a.enroll_and_verify().status_code == 200
    b = make_api()
    body = b.login(world.emails["finance"]).json()
    assert body["mfa_required"] is True and body["permissions"] == []
    r = b.get(f"{P}/payments")
    assert r.status_code == 403 and code(r) == "MFA_REQUIRED"
    r = b.post(f"{P}/auth/password", json={"current_password": PASSWORD, "new_password": "x" * 12})
    assert r.status_code == 403 and code(r) == "MFA_REQUIRED"  # no self-service while pending
    b.totp = a.totp
    r = b.verify(a.totp.at(time.time() + 30))
    assert r.status_code == 200 and r.json()["mfa_required"] is False
    assert b.get(f"{P}/payments").status_code == 200
    other = make_api()
    assert other.login(world.emails["wh_a"]).json()["mfa_required"] is False  # unaffected


def test_turning_it_on_signs_out_other_sessions(make_api, world):
    a, b = make_api(), make_api()
    a.login(world.emails["sales"])
    b.login(world.emails["sales"])
    assert a.enroll_and_verify().status_code == 200
    assert b.get(f"{P}/auth/session").status_code == 401
    assert a.get(f"{P}/auth/session").status_code == 200


def test_totp_code_cannot_be_replayed(make_api, world):
    a = make_api()
    a.login(world.emails["owner"])
    a.enroll_and_verify()
    used = a.totp.now()
    b = make_api()
    b.login(world.emails["owner"])
    b.totp = a.totp
    r = b.verify(used)
    assert r.status_code == 400 and code(r) == "MFA_INVALID"
    assert b.verify(a.totp.at(time.time() + 30)).status_code == 200


def test_recovery_code_signs_in_once(make_api, world, db):
    a = make_api()
    a.login(world.emails["owner"])
    a.enroll_and_verify()
    rc = a.recovery_codes[0]
    b = make_api()
    b.login(world.emails["owner"])
    r = b.post(f"{P}/auth/mfa/verify", json={"recovery_code": rc.lower().replace("-", " ")})
    assert r.status_code == 200, r.text  # spaces / lower case are fine
    assert r.json()["mfa_required"] is False
    c = make_api()
    c.login(world.emails["owner"])
    r = c.post(f"{P}/auth/mfa/verify", json={"recovery_code": rc})
    assert r.status_code == 400 and code(r) == "MFA_INVALID"  # single use
    r = c.post(f"{P}/auth/mfa/verify", json={"recovery_code": "AAAA-BBBB-CCCC-DDDD"})
    assert r.status_code == 400
    left = db.scalars(
        select(MfaRecoveryCode).where(
            MfaRecoveryCode.employee_id == world.ids["owner"], MfaRecoveryCode.used_at.is_(None)
        )
    ).all()
    assert len(left) == 9


def test_new_recovery_codes_replace_the_old_ones(make_api, world):
    a = make_api()
    a.login(world.emails["sales"])
    a.enroll_and_verify()
    old = a.recovery_codes[1]
    r = a.post(f"{P}/auth/mfa/recovery-codes", json={"password": PASSWORD, "code": "000000"})
    assert r.status_code == 400
    r = a.post(f"{P}/auth/mfa/recovery-codes", json={"password": PASSWORD, "recovery_code": old})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    b = make_api()
    b.login(world.emails["sales"])
    assert b.post(f"{P}/auth/mfa/verify", json={"recovery_code": old}).status_code == 400
    new = r.json()["recovery_codes"][0]
    assert b.post(f"{P}/auth/mfa/verify", json={"recovery_code": new}).status_code == 200


def test_turning_it_off_needs_password_and_code(make_api, world):
    a = make_api()
    a.login(world.emails["sales"])
    a.enroll_and_verify()
    nxt = a.totp.at(time.time() + 30)
    r = a.post(f"{P}/auth/mfa/disable", json={"password": "nope-nope-nope", "code": nxt})
    assert r.status_code == 422 and code(r) == "WRONG_PASSWORD"
    r = a.post(f"{P}/auth/mfa/disable", json={"password": PASSWORD})
    assert r.status_code == 422  # a code or recovery code is required too
    r = a.post(f"{P}/auth/mfa/disable", json={"password": PASSWORD, "code": nxt})
    assert r.status_code == 200 and r.json()["mfa_enrolled"] is False
    b = make_api()
    assert b.login(world.emails["sales"]).json()["mfa_required"] is False


def test_wrong_code_rejected(api, world):
    api.login(world.emails["finance"])
    api.enroll_and_verify()
    api.login(world.emails["finance"])
    assert api.verify("000000").status_code == 400


def test_cannot_set_up_twice(api, world):
    api.login(world.emails["owner"])
    api.enroll_and_verify()
    r = api.post(f"{P}/auth/mfa/enroll", json={"password": PASSWORD})
    assert r.status_code == 409


def test_company_policy_requires_setup_at_next_sign_in(make_api, world, db):
    """Built but switched off (owner, 2026-11-09): with the flag on, everyone sets it up."""
    with db.begin():
        db.add(FeatureFlag(company_id=world.company_id, key="require_two_step", enabled=True))
    a = make_api()
    body = a.login(world.emails["wh_a"]).json()
    assert body["mfa_required"] is True and body["mfa_setup_required"] is True
    assert body["permissions"] == []
    r = a.post(f"{P}/auth/mfa/enroll")  # no password: it was just entered at sign-in
    assert r.status_code == 200
    secret = parse_qs(urlparse(r.json()["otpauth_uri"]).query)["secret"][0]
    r = a.post(f"{P}/auth/mfa/enroll/confirm", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200 and r.json()["session"]["mfa_required"] is False
    assert "pick.record" in r.json()["session"]["permissions"]
    r = a.post(
        f"{P}/auth/mfa/disable",
        json={"password": PASSWORD, "code": pyotp.TOTP(secret).at(time.time() + 30)},
    )
    assert r.status_code == 409 and code(r) == "TWO_STEP_REQUIRED"


def test_two_step_secrets_never_reach_the_audit_log(make_api, world, db):
    from pro_trading.models import AuditEvent

    a = make_api()
    a.login(world.emails["sales"])
    a.enroll_and_verify()
    rows = db.scalars(
        select(AuditEvent.detail_redacted).where(AuditEvent.company_id == world.company_id)
    ).all()
    blob = repr(rows)
    assert a.totp.secret not in blob
    assert not any(c in blob for c in a.recovery_codes)
