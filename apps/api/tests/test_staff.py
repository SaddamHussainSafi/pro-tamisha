# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Settings › Users and My account: create staff, one role each, disable, reset password and
two-step, self-protection, sign-out on change, passwords never in audit, permissions, tenancy."""

from sqlalchemy import text

from pro_trading.db import get_sessionmaker

from .conftest import PASSWORD, Api, build_world, idem, mailed_token
from .test_orders import P, code, owner, user  # noqa: F401

NEW_PW = "Fresh-Cassava-2026!"


def staff(api, **body):
    b = {
        "email": "new.hire@example.test",
        "display_name": "New Hire",
        "role_code": "SALES_OPERATOR",
    }
    return api.post(f"{P}/employees", json={**b, **body}, headers=idem())


def audit_details(company_id, action):
    with get_sessionmaker()() as db:
        return [
            r[0]
            for r in db.execute(
                text(
                    "select detail_redacted::text from audit_events"
                    " where company_id=:c and action=:a"
                ),
                {"c": company_id, "a": action},
            )
        ]


def test_create_invites_and_the_person_sets_their_password(owner, make_api, world):
    r = staff(owner, email=world.emails["sales"])
    assert r.status_code == 409 and code(r) == "EMAIL_TAKEN"
    assert staff(owner, role_code="KING").status_code == 422
    extra = staff(owner, email=f"x.{world.company_id.hex[:6]}@example.test", password=NEW_PW)
    assert extra.status_code == 201 and extra.json()["status"] == "INVITED"  # field ignored...
    hire = f"hire.{world.company_id.hex[:6]}@example.test"
    r = staff(owner, email=hire)
    assert r.status_code == 201, r.text
    s = r.json()
    assert (s["roles"], s["active"], s["status"]) == (["SALES_OPERATOR"], True, "INVITED")
    assert s["invite_expires_at"] is not None and s["last_login_at"] is None
    assert make_api().login(hire, NEW_PW).status_code == 401  # ...no password until accepted
    token = mailed_token(hire)
    a = make_api()
    for bad in ("short-1!", "aaaaaaaaaaaaa"):
        r = a.post(f"{P}/auth/invitation/accept", json={"token": token, "password": bad})
        assert r.status_code == 422, bad
    r = a.post(f"{P}/auth/invitation/accept", json={"token": token, "password": NEW_PW})
    assert r.status_code == 204, r.text
    assert a.login(hire, NEW_PW).status_code == 200
    assert a.get(f"{P}/customers").status_code == 200  # sales can read customers
    listed = {x["email"]: x for x in owner.get(f"{P}/employees").json()}
    assert listed[hire]["status"] == "ACTIVE" and listed[hire]["last_login_at"] is not None
    assert len(listed) == 8  # 6 fixture people + 2 invited here
    details = audit_details(world.company_id, "employee.create")
    assert details and all(NEW_PW not in d and "password" not in d for d in details)
    assert all(token not in d for d in audit_details(world.company_id, "auth.invitation_accepted"))


def test_role_change_disable_and_self_protection(owner, user, make_api, world):
    sales = user("sales")
    sid = str(world.ids["sales"])
    r = owner.patch(f"{P}/employees/{sid}", json={"role_code": "WAREHOUSE"}, headers=idem())
    assert r.status_code == 200 and r.json()["roles"] == ["WAREHOUSE"]
    assert sales.get(f"{P}/customers").status_code == 401  # signed out by the change
    r = owner.patch(
        f"{P}/employees/{sid}", json={"active": False, "display_name": "Sam (left)"}, headers=idem()
    )
    assert r.status_code == 200 and (r.json()["active"], r.json()["display_name"]) == (
        False,
        "Sam (left)",
    )
    assert make_api().login(world.emails["sales"]).status_code == 401
    me = str(world.ids["owner"])
    for body in ({"active": False}, {"role_code": "MANAGER"}):
        r = owner.patch(f"{P}/employees/{me}", json=body, headers=idem())
        assert r.status_code == 422 and code(r) == "SELF_CHANGE"
    assert owner.post(f"{P}/employees/{me}/reset-mfa").status_code == 422


def test_reset_password_and_two_step(owner, user, make_api, world):
    fin = user("finance")  # privileged, MFA enrolled
    fid = str(world.ids["finance"])
    r = owner.post(f"{P}/employees/{fid}/reset-password", json={"password": "aaaa"})
    assert r.status_code == 422
    r = owner.post(f"{P}/employees/{fid}/reset-password", json={"password": NEW_PW})
    assert r.status_code == 200, r.text
    assert fin.get(f"{P}/payments").status_code == 401
    a = make_api()
    assert a.login(world.emails["finance"]).status_code == 401  # old password gone
    assert a.login(world.emails["finance"], NEW_PW).status_code == 200
    r = owner.post(f"{P}/employees/{fid}/reset-mfa")
    assert r.status_code == 200 and r.json()["mfa_enrolled"] is False
    b = make_api()
    body = b.login(world.emails["finance"], NEW_PW).json()
    assert body["mfa_required"] is False  # two-step is off again: password alone signs in
    assert b.get(f"{P}/payments").status_code == 200
    assert b.enroll_and_verify(NEW_PW).status_code == 200  # can turn it on again
    assert not any(NEW_PW in d for d in audit_details(world.company_id, "employee.password_reset"))


def test_my_account(user, make_api, world):
    sales = user("sales")
    other_device = make_api()
    assert other_device.login(world.emails["sales"]).status_code == 200
    r = sales.patch(f"{P}/auth/me", json={"display_name": "Sam S."})
    assert r.status_code == 200 and r.json()["display_name"] == "Sam S."
    assert sales.get(f"{P}/auth/session").json()["employee"]["display_name"] == "Sam S."
    r = sales.post(f"{P}/auth/password", json={"current_password": "nope", "new_password": NEW_PW})
    assert r.status_code == 422 and code(r) == "WRONG_PASSWORD"
    r = sales.post(
        f"{P}/auth/password", json={"current_password": PASSWORD, "new_password": PASSWORD}
    )
    assert r.status_code == 422 and code(r) == "SAME_PASSWORD"
    r = sales.post(
        f"{P}/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PW}
    )
    assert r.status_code == 204, r.text
    assert sales.get(f"{P}/auth/session").status_code == 200  # this device stays signed in
    assert other_device.get(f"{P}/auth/session").status_code == 401  # others are signed out
    assert make_api().login(world.emails["sales"], NEW_PW).status_code == 200
    assert audit_details(world.company_id, "employee.password_change")


def test_permissions_and_tenancy(owner, user, world):
    users = {name: user(name) for name in ("manager_a", "sales", "finance")}
    for name, a in users.items():
        r = a.get(f"{P}/employees")
        assert r.status_code == 403, name
    r = staff(users["manager_a"], email="sneaky@example.test")
    assert r.status_code == 403 and code(r) == "FORBIDDEN"
    other = build_world()
    out = Api(owner.c.app)
    out.login(other.emails["owner"])
    assert out.enroll_and_verify().status_code == 200
    ids = {x["id"] for x in out.get(f"{P}/employees").json()}
    assert str(world.ids["sales"]) not in ids
    r = out.patch(f"{P}/employees/{world.ids['sales']}", json={"active": False}, headers=idem())
    assert r.status_code == 404
    assert out.post(f"{P}/employees/{world.ids['sales']}/reset-mfa").status_code == 404
