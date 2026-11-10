# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Activity feed: real audited business actions as sentences, own-only without audit.read,
warehouse-scoped for scoped audit readers, paged, and never housekeeping."""

import uuid

from pro_trading import audit
from pro_trading.db import get_sessionmaker

from .conftest import idem
from .test_invoicing import issue, sales, stock, wh  # noqa: F401 - fixtures re-used
from .test_orders import CSV, P, code, confirmed, owner, picked, setup, user  # noqa: F401
from .test_payments import finance, invoice, report, verify  # noqa: F401

A = f"{P}/activity"


def feed(api, **params):
    r = api.get(A, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def lines(api, **params):
    return [
        f"{i['actor']['name']} {i['verb']} — {i['object']}" for i in feed(api, **params)["items"]
    ]


def test_business_actions_read_as_sentences(owner, sales, wh, world, setup):
    o = confirmed(sales, world, setup, [("CAS", "BOX", "2")])
    got = feed(owner)
    by_action = {i["action"]: i for i in got["items"]}
    assert f"sales confirmed an order — {o['order_no']}" in lines(owner)
    assert f"sales created an order — {o['order_no']}" in lines(owner)
    assert "owner added a new product — Cassava" in lines(owner)
    item = by_action["sales_order.confirm"]
    assert item["tag"] == "Order Confirmed" and item["tone"] == "info"
    assert item["link"] == f"/orders/{o['id']}"
    # Price import/activation by the owner show as price updates (purple tone).
    assert by_action["price_version.activate"]["tone"] == "price"
    # Sign-ins and other housekeeping never appear.
    assert not [i for i in got["items"] if i["action"].startswith(("auth.", "notification."))]
    assert got["total"] >= len(got["items"]) and got["can_see_others"] is True


def test_money_and_stock_details_come_from_the_record(owner, sales, wh, finance, world, setup):
    pay = report(sales, setup, "125.00").json()
    rec = next(i for i in feed(owner)["items"] if i["action"] == "payment.record")
    assert rec["object"] == pay["payment_no"] and rec["extra"] == "CAD 125.00"
    lot = stock(owner, world, setup, "CAS", "BOX", "10")
    r = owner.post(
        f"{P}/inventory/adjustments",
        json={
            "lot_id": lot,
            "unit_code": "BOX",
            "delta_qty": "-2",
            "reason_code": "LOST",
            "note": "Lost in transit",
        },
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    adj = next(i for i in feed(owner)["items"] if i["action"] == "inventory.adjust")
    assert adj["object"] == "Cassava" and adj["extra"] == "Qty -2 BOX"
    assert adj["tag"] == "Inventory Update" and adj["tone"] == "stock"


def test_people_without_audit_read_see_only_their_own(owner, sales, wh, world, setup):
    confirmed(sales, world, setup, [("CAS", "BOX", "1")])
    mine = feed(sales)
    assert mine["can_see_others"] is False
    assert {i["actor"]["name"] for i in mine["items"]} == {"sales"}
    # Asking for someone else's activity is refused, not silently emptied.
    r = sales.get(A, params={"actor_id": str(world.ids["owner"])})
    assert r.status_code == 403 and code(r) == "FORBIDDEN"
    # The owner sees both, and can narrow to their own or to one person.
    names = {i["actor"]["name"] for i in feed(owner)["items"]}
    assert {"owner", "sales"} <= names
    assert {i["actor"]["name"] for i in feed(owner, mine="true")["items"]} == {"owner"}
    only = feed(owner, actor_id=str(world.ids["sales"]))
    assert {i["actor"]["name"] for i in only["items"]} == {"sales"}
    assert only["total"] == mine["total"]


def test_scoped_audit_reader_sees_their_warehouse_only(make_api, world):
    def log(action, actor, wh, **detail):
        with get_sessionmaker()() as db, db.begin():
            audit.record(
                db,
                action=action,
                success=True,
                company_id=world.company_id,
                actor_id=actor,
                target_type="sales_order",
                target_id=str(uuid.uuid4()),
                warehouse_id=wh,
                detail=detail,
            )

    log("sales_order.create", world.ids["sales"], world.wh_a, order_no="SO-IN-A")
    log("sales_order.create", world.ids["sales"], world.wh_b, order_no="SO-IN-B")
    m = make_api()
    m.login(world.emails["manager_a"])  # MANAGER (audit.read) scoped to warehouse A
    if m.get(f"{P}/auth/session").json().get("mfa_required"):
        m.enroll_and_verify()
    objs = [i["object"] for i in feed(m)["items"]]
    assert "SO-IN-A" in objs and "SO-IN-B" not in objs
    # Another company's reader sees none of it.
    from .conftest import build_world

    other = build_world()
    o = make_api()
    o.login(other.emails["owner"])
    assert feed(o)["total"] == 0


def test_paging_is_stable_and_complete(owner, world):
    for n in range(7):
        with get_sessionmaker()() as db, db.begin():
            audit.record(
                db,
                action="sales_order.create",
                success=True,
                company_id=world.company_id,
                actor_id=world.ids["owner"],
                target_type="sales_order",
                target_id=str(uuid.uuid4()),
                detail={"order_no": f"SO-P{n}"},
            )
    p1 = feed(owner, limit=3)
    p2 = feed(owner, limit=3, before=p1["next_before"])
    p3 = feed(owner, limit=3, before=p2["next_before"])
    seen = [i["id"] for i in p1["items"] + p2["items"] + p3["items"]]
    assert len(seen) == len(set(seen)) == min(9, p1["total"])
    assert [i["object"] for i in p1["items"]] == ["SO-P6", "SO-P5", "SO-P4"]
    assert owner.get(A, params={"limit": 0}).status_code == 422
