# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Customers master data (used by the Customers screen): create/edit rules, clearing contact
details, email format, optimistic versions, idempotent retries, permissions and tenancy."""

from sqlalchemy import text

from pro_trading.db import get_sessionmaker

from .conftest import Api, build_world, idem
from .test_orders import P, code, owner, setup, user  # noqa: F401


def create(api, **body):
    return api.post(f"{P}/customers", json={"name": "Spice Route Market", **body}, headers=idem())


def patch(api, c, key=None, **body):
    return api.patch(
        f"{P}/customers/{c['id']}", json={"version": c["version"], **body}, headers=key or idem()
    )


def test_create_rules(owner, setup):
    r = create(owner, external_ref="C-300", email="", phone="  ", payment_terms_days=30)
    assert r.status_code == 201, r.text
    c = r.json()
    assert (c["email"], c["phone"], c["payment_terms_days"], c["active"]) == (None, None, 30, True)
    r = create(owner, external_ref="C-300")
    assert r.status_code == 409 and code(r) == "DUPLICATE"
    for bad in ("not-an-email", "a@b", "a b@c.d"):
        r = create(owner, external_ref="C-301", email=bad)
        assert r.status_code == 422, bad
    r = create(owner, payment_terms_days=400)
    assert r.status_code == 422
    r = create(owner, group_id=setup["pb"])  # not a group id
    assert r.status_code == 422 and code(r) == "INVALID_GROUP"


def test_edit_clear_conflict_and_retry(owner, setup):
    c = create(
        owner, external_ref="C-310", email="ap@spice.example.test", phone="416-555-0101"
    ).json()
    g = owner.post(
        f"{P}/customer-groups", json={"code": "RETAIL", "name": "Retail"}, headers=idem()
    )
    assert g.status_code == 201, g.text

    key = idem()
    r = patch(owner, c, key=key, email="", phone="", group_id=g.json()["id"], payment_terms_days=14)
    assert r.status_code == 200, r.text
    c2 = r.json()
    assert (c2["email"], c2["phone"], c2["group_id"], c2["version"]) == (
        None,
        None,
        g.json()["id"],
        c["version"] + 1,
    )
    # same key = same answer, nothing applied twice
    again = patch(
        owner, c, key=key, email="", phone="", group_id=g.json()["id"], payment_terms_days=14
    )
    assert again.status_code == 200 and again.json() == c2
    # stale version
    r = patch(owner, c, name="Old copy")
    assert r.status_code == 409 and code(r) == "VERSION_CONFLICT"
    assert r.json()["error"]["current_version"] == c2["version"]
    # clear group, deactivate, bad email refused
    r = patch(owner, c2, clear_group=True, active=False)
    assert r.status_code == 200 and r.json()["group_id"] is None and r.json()["active"] is False
    assert patch(owner, r.json(), email="nope").status_code == 422
    # inactive customers are still listed when asked for
    names = [
        x["external_ref"] for x in owner.get(f"{P}/customers", params={"active": False}).json()
    ]
    assert "C-310" in names
    with get_sessionmaker()() as db:
        n = db.execute(
            text(
                "select count(*) from audit_events where action='customer.update' and target_id=:t"
            ),
            {"t": c["id"]},
        ).scalar_one()
    assert n == 2


def test_permissions_and_tenancy(owner, user, make_api, world, setup):
    c = create(owner, external_ref="C-320").json()
    sales = user("sales")
    assert sales.get(f"{P}/customers/{c['id']}").status_code == 200
    assert any(
        x["id"] == c["id"] for x in sales.get(f"{P}/customers", params={"q": "spice"}).json()
    )
    r = patch(sales, c, name="Renamed by sales")
    assert r.status_code == 403 and code(r) == "FORBIDDEN"
    assert create(sales, external_ref="C-321").status_code == 403
    # catalog.manage held only for warehouse A is not enough for company master data
    assert patch(user("manager_a"), c, name="Renamed").status_code == 403
    other = build_world()
    out = Api(owner.c.app)
    out.login(other.emails["owner"])
    assert out.enroll_and_verify().status_code == 200
    assert out.get(f"{P}/customers/{c['id']}").status_code == 404
    assert patch(out, c, name="Hijack").status_code == 404
    assert out.get(f"{P}/customers").json() == []
