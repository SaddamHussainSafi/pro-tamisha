import time

from sqlalchemy import select

from pro_trading.models import Employee


def test_privileged_user_needs_mfa_before_privileged_actions(api, world):
    body = api.login(world.emails["owner"]).json()
    assert body["mfa_required"] is True
    assert body["permissions"] == []  # privileged grants dormant until MFA
    r = api.put("/api/v1/feature-flags/demo", json={"enabled": True})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "MFA_REQUIRED"


def test_enroll_verify_unlocks_privileged_grants(api, world, db):
    api.login(world.emails["owner"])
    r = api.enroll_and_verify()
    assert r.status_code == 200, r.text
    assert r.json()["mfa_required"] is False
    assert "feature_flag.manage" in r.json()["permissions"]
    assert api.put("/api/v1/feature-flags/demo", json={"enabled": True}).status_code == 200
    emp = db.scalar(select(Employee).where(Employee.id == world.ids["owner"]))
    assert emp is not None and emp.mfa_secret_enc is not None
    assert api.totp is not None
    assert api.totp.secret.encode() not in emp.mfa_secret_enc  # encrypted at rest


def test_totp_code_cannot_be_replayed(make_api, world):
    a = make_api()
    a.login(world.emails["owner"])
    a.enroll_and_verify()
    used = a.totp.now()
    b = make_api()
    b.login(world.emails["owner"])
    b.totp = a.totp
    r = b.post("/api/v1/auth/mfa/verify", json={"code": used})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MFA_INVALID"
    nxt = a.totp.at(time.time() + 30)  # next step is still inside the accepted window
    assert b.post("/api/v1/auth/mfa/verify", json={"code": nxt}).status_code == 200


def test_wrong_code_rejected(api, world):
    api.login(world.emails["finance"])
    api.post("/api/v1/auth/mfa/enroll")
    assert api.post("/api/v1/auth/mfa/verify", json={"code": "000000"}).status_code in (400,)


def test_cannot_re_enroll_once_enrolled(api, world):
    api.login(world.emails["owner"])
    api.enroll_and_verify()
    r = api.post("/api/v1/auth/mfa/enroll")
    assert r.status_code == 409


def test_non_privileged_user_not_required_mfa(api, world):
    body = api.login(world.emails["wh_a"]).json()
    assert body["mfa_required"] is False
    assert "pick.record" in body["permissions"]
