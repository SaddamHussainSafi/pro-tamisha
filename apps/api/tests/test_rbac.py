from sqlalchemy import select

from pro_trading.models import AuditEvent

from .conftest import idem


def _denials(db, actor_id):
    return db.scalars(
        select(AuditEvent).where(
            AuditEvent.actor_id == actor_id, AuditEvent.action == "permission.denied"
        )
    ).all()


def test_sec001_warehouse_role_cannot_issue_invoice_and_denial_is_audited(api, world, db):
    api.login(world.emails["wh_a"])
    r = api.post(f"/api/v1/_probe/warehouses/{world.wh_a}/invoices/issue")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
    ev = _denials(db, world.ids["wh_a"])
    assert len(ev) == 1
    assert ev[0].success is False
    assert ev[0].detail_redacted["permission"] == "invoice.issue"
    assert ev[0].warehouse_id == world.wh_a
    assert ev[0].request_id == r.headers["X-Request-Id"]


def test_denial_audit_survives_request_rollback(api, world, db):
    api.login(world.emails["sales"])
    api.post("/api/v1/warehouses", json={"code": "ZZ", "name": "Nope"}, headers=idem())
    assert len(_denials(db, world.ids["sales"])) == 1


def test_cross_warehouse_write_blocked(make_api, world, db):
    mgr = make_api()
    mgr.login(world.emails["admin_a"])
    mgr.enroll_and_verify()
    ok = mgr.patch(
        f"/api/v1/warehouses/{world.wh_a}",
        json={"name": "A renamed", "active": True, "version": 1},
        headers=idem(),
    )
    assert ok.status_code == 200, ok.text
    r = mgr.patch(
        f"/api/v1/warehouses/{world.wh_b}",
        json={"name": "B hijacked", "active": False, "version": 1},
        headers=idem(),
    )
    assert r.status_code == 403
    ev = _denials(db, world.ids["admin_a"])
    assert [e.warehouse_id for e in ev] == [world.wh_b]


def test_warehouse_scoped_grant_cannot_do_company_level_create(make_api, world):
    mgr = make_api()
    mgr.login(world.emails["admin_a"])
    mgr.enroll_and_verify()
    r = mgr.post("/api/v1/warehouses", json={"code": "WC", "name": "C"}, headers=idem())
    assert r.status_code == 403


def test_list_warehouses_filtered_by_scope(make_api, world):
    w = make_api()
    w.login(world.emails["wh_a"])
    codes = [x["code"] for x in w.get("/api/v1/warehouses").json()]
    assert codes == ["WA"]


def test_tenant_isolation(make_api, world):
    from .conftest import World  # noqa: F401

    a = make_api()
    a.login(world.emails["owner"])
    a.enroll_and_verify()
    r = a.get("/api/v1/warehouses")
    assert {x["code"] for x in r.json()} == {"WA", "WB"}
    assert all(x["id"] in (str(world.wh_a), str(world.wh_b)) for x in r.json())


def test_audit_read_requires_permission(make_api, world):
    s = make_api()
    s.login(world.emails["sales"])
    assert s.get("/api/v1/audit-events").status_code == 403
    o = make_api()
    o.login(world.emails["owner"])
    o.enroll_and_verify()
    r = o.get("/api/v1/audit-events?action=auth.login")
    assert r.status_code == 200 and len(r.json()) >= 1
