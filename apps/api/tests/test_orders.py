"""Module 04 sales orders + picking: Postgres integration, authz, idempotency, concurrency.

Synthetic data only. Acceptance (docs/modules/04-sales-orders.md): 15 requested, first 10
supplied -> 5 pending; NO = 0 packed, not cancelled; worker cannot change the order price or
issue an invoice; original request preserved.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading import audit
from pro_trading.db import get_sessionmaker

from .conftest import idem

P = "/api/v1"

CSV = (
    "sku,product_name,unit,rate,currency,customer_ref,group_code\r\n"
    "CAS,Cassava,BOX,28.00,CAD,,\r\n"
    "CAS,Cassava,LB,1.50,CAD,,\r\n"
    "CAS,Cassava,BOX,26.50,CAD,C-100,\r\n"
    "PLN,Plantain,PIECE,0.45,CAD,,\r\n"
)


def code(r):
    return r.json()["error"]["code"]


@pytest.fixture
def owner(make_api, world):
    o = make_api()
    o.login(world.emails["owner"])
    assert o.enroll_and_verify().status_code == 200
    return o


@pytest.fixture
def user(make_api, world):
    def _login(name):
        a = make_api()
        a.login(world.emails[name])
        if name in ("finance", "manager_a"):
            assert a.enroll_and_verify().status_code == 200
        return a

    return _login


@pytest.fixture
def setup(owner):
    prods = {}
    for sku, name, stock_unit, profiles in [
        ("CAS", "Cassava", "BOX", [("CASE40", "BOX", "40")]),
        ("PLN", "Plantain", "PIECE", [("PC", "PIECE", None)]),
        ("NOP", "Unpriced", "BOX", [("CASE", "BOX", None)]),
    ]:
        body = {
            "sku": sku,
            "name": name,
            "stock_unit": stock_unit,
            "unit_profiles": [
                {"code": c, "unit_code": u, "weight_lb_per_unit": w} for c, u, w in profiles
            ],
        }
        r = owner.post(f"{P}/products", json=body, headers=idem())
        assert r.status_code == 201, r.text
        prods[sku] = r.json()["id"]
    c = owner.post(
        f"{P}/customers", json={"external_ref": "C-100", "name": "Island Grocers"}, headers=idem()
    )
    c2 = owner.post(
        f"{P}/customers", json={"external_ref": "C-200", "name": "Walk-in"}, headers=idem()
    )
    assert c.status_code == c2.status_code == 201
    r = owner.post(
        f"{P}/price-books/versions/import",
        json={"version_code": "PB-1", "csv": CSV},
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    v = r.json()
    a = owner.post(
        f"{P}/price-books/versions/{v['id']}/activate",
        json={"version": v["version"]},
        headers=idem(),
    )
    assert a.status_code == 200, a.text
    return {"p": prods, "cust": c.json()["id"], "walkin": c2.json()["id"], "pb": v["id"]}


def new_order(api, world, s, lines, key=None, cust=None, wh=None, **extra):
    body = {
        "customer_id": cust or s["cust"],
        "warehouse_id": str(wh or world.wh_a),
        "source_text": "Island Grocers: 15 bx cassava, 20 plantain",
        "lines": [
            {"product_id": s["p"][sku], "unit_code": u, "qty": q, "source_line_text": f"{q} {sku}"}
            for sku, u, q in lines
        ],
        **extra,
    }
    return api.post(f"{P}/sales-orders", json=body, headers=key or idem())


def confirmed(api, world, s, lines, **kw):
    r = new_order(api, world, s, lines, **kw)
    assert r.status_code == 201, r.text
    o = r.json()
    c = api.post(
        f"{P}/sales-orders/{o['id']}/confirm", json={"version": o["version"]}, headers=idem()
    )
    assert c.status_code == 200, c.text
    return c.json()


def open_pick(api, o, key=None):
    return api.post(
        f"{P}/sales-orders/{o['id']}/pick-attempts",
        json={"version": o["version"]},
        headers=key or idem(),
    )


def record(api, pick, rows, key=None):
    """rows: list of (line_index, qty, reason)."""
    lines = [
        {"line_id": pick["lines"][i]["id"], "picked_qty": q, "shortage_reason": r}
        for i, q, r in rows
    ]
    return api.post(
        f"{P}/pick-attempts/{pick['id']}/record",
        json={"version": pick["version"], "lines": lines},
        headers=key or idem(),
    )


def submit(api, pick):
    return api.post(
        f"{P}/pick-attempts/{pick['id']}/submit", json={"version": pick["version"]}, headers=idem()
    )


def verify(api, pick, dispositions=None, reason=""):
    body = {"version": pick["version"], "dispositions": dispositions or {}, "reason": reason}
    return api.post(f"{P}/pick-attempts/{pick['id']}/verify", json=body, headers=idem())


def picked(api, wh_api, o, rows, verifier=None, **vkw):
    """Full cycle: open (sales) -> record + submit (warehouse) -> verify."""
    r = open_pick(api, o)
    assert r.status_code == 201, r.text
    pk = r.json()
    r = record(wh_api, pk, rows)
    assert r.status_code == 200, r.text
    r = submit(wh_api, r.json())
    assert r.status_code == 200, r.text
    r = verify(verifier or api, r.json(), **vkw)
    assert r.status_code == 200, r.text
    return r.json()


def line(order, sku):
    return next(x for x in order["lines"] if x["sku"] == sku)


# ---------------------------------------------------------------- acceptance


def test_fifteen_requested_ten_supplied_leaves_five_pending(owner, user, world, setup):
    sales, wh = user("sales"), user("wh_a")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "15"), ("PLN", "PIECE", "20")])
    assert o["status"] == "CONFIRMED" and o["order_no"].startswith("SO-")
    cas = line(o, "CAS")
    assert cas["rate"] == "26.5000" and cas["price_scope"] == "CUSTOMER"
    assert cas["price_version_code"] == "PB-1"

    pk = picked(
        sales,
        wh,
        o,
        [(0, "10", "OUT_OF_STOCK"), (1, "0", "NOT_FOUND")],
        verifier=owner,
    )
    assert pk["status"] == "VERIFIED"
    assert [x["outcome"] for x in pk["lines"]] == ["SHORT", "NO"]
    assert [x["disposition"] for x in pk["lines"]] == ["BACKORDER", "BACKORDER"]

    o = owner.get(f"{P}/sales-orders/{o['id']}").json()
    cas, pln = line(o, "CAS"), line(o, "PLN")
    assert (cas["ordered_qty"], cas["awaiting_invoice_qty"], cas["pending_qty"]) == (
        "15.0000",
        "10.0000",
        "5.0000",
    )
    # NO = 0 packed, still pending in full -- NOT cancelled
    assert (pln["cancelled_qty"], pln["pending_qty"]) == ("0.0000", "20.0000")
    assert cas["backorder"] and pln["backorder"] and o["has_backorder"]
    assert o["status"] == "PARTIALLY_FULFILLED"
    # original capture preserved
    assert o["source_text"].startswith("Island Grocers")
    assert [x["qty"] for x in o["source_snapshot"]] == ["15.0000", "20.0000"]

    # next sheet asks only for what is still pending (not the 10 awaiting invoice)
    r = open_pick(sales, o)
    assert r.status_code == 201, r.text
    nxt = r.json()
    assert nxt["attempt_no"] == 2 and nxt["pick_code"] == f"{o['order_no']}-P2"
    assert [x["requested_qty"] for x in nxt["lines"]] == ["5.0000", "20.0000"]
    # backorder filter
    bo = owner.get(f"{P}/sales-orders", params={"backorder": "true"}).json()
    assert [x["id"] for x in bo["items"]] == [o["id"]]


def test_price_frozen_at_confirmation(owner, user, world, setup):
    sales = user("sales")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "3")], cust=setup["walkin"])
    assert line(o, "CAS")["rate"] == "28.0000"
    # new price book later: the confirmed order keeps 28
    csv2 = CSV.replace("BOX,28.00", "BOX,31.00")
    r = owner.post(
        f"{P}/price-books/versions/import",
        json={"version_code": "PB-2", "csv": csv2},
        headers=idem(),
    )
    v = r.json()
    a = owner.post(
        f"{P}/price-books/versions/{v['id']}/activate",
        json={"version": v["version"]},
        headers=idem(),
    )
    assert a.status_code == 200, a.text
    again = owner.get(f"{P}/sales-orders/{o['id']}").json()
    assert line(again, "CAS")["rate"] == "28.0000"
    o2 = confirmed(sales, world, setup, [("CAS", "BOX", "3")], cust=setup["walkin"])
    assert line(o2, "CAS")["rate"] == "31.0000"

    # the database refuses to change a frozen rate or a confirmed line, even bypassing the API
    with get_sessionmaker()() as db:
        for sql in [
            "UPDATE sales_order_lines SET rate = 1 WHERE order_id = :o",
            "UPDATE sales_order_lines SET ordered_qty = 99 WHERE order_id = :o",
            "DELETE FROM sales_order_lines WHERE order_id = :o",
            "UPDATE sales_orders SET source_text = 'x' WHERE id = :o",
            "DELETE FROM sales_orders WHERE id = :o",
        ]:
            with pytest.raises(DBAPIError):
                db.execute(text(sql), {"o": o["id"]})
            db.rollback()


def test_worker_cannot_change_price_confirm_or_close(owner, user, world, setup):
    sales, wh = user("sales"), user("wh_a")
    r = new_order(wh, world, setup, [("CAS", "BOX", "2")])
    assert r.status_code == 403
    d = new_order(sales, world, setup, [("CAS", "BOX", "2")]).json()
    assert (
        wh.post(
            f"{P}/sales-orders/{d['id']}/confirm", json={"version": d["version"]}, headers=idem()
        ).status_code
        == 403
    )
    o = confirmed(sales, world, setup, [("CAS", "BOX", "2")])
    # no route edits a confirmed order (price, qty); warehouse cannot close or open sheets
    body = {
        "version": o["version"],
        "customer_id": setup["cust"],
        "warehouse_id": str(world.wh_a),
        "lines": [{"product_id": setup["p"]["CAS"], "unit_code": "BOX", "qty": "2"}],
    }
    assert sales.put(f"{P}/sales-orders/{o['id']}", json=body, headers=idem()).status_code == 409
    assert wh.put(f"{P}/sales-orders/{o['id']}", json=body, headers=idem()).status_code == 403
    reason = {"version": o["version"], "reason": "no longer needed"}
    assert (
        wh.post(f"{P}/sales-orders/{o['id']}/close", json=reason, headers=idem()).status_code == 403
    )
    assert (
        sales.post(f"{P}/sales-orders/{o['id']}/close", json=reason, headers=idem()).status_code
        == 403
    )
    assert open_pick(wh, o).status_code == 403
    pk = open_pick(sales, o).json()
    # sales cannot record or verify a pick; warehouse cannot verify its own pick
    assert record(sales, pk, [(0, "2", None)]).status_code == 403
    rec = record(wh, pk, [(0, "2", None)]).json()
    sub = submit(wh, rec).json()
    assert verify(wh, sub).status_code == 403
    assert verify(sales, sub).status_code == 403  # sales has no pick.verify
    # no invoice route exists in this module, and warehouse never has invoice.issue
    assert "invoice.issue" not in wh.get(f"{P}/auth/session").json()["permissions"]
    # finance reads but cannot manage
    fin = user("finance")
    assert fin.get(f"{P}/sales-orders/{o['id']}").status_code == 200
    assert new_order(fin, world, setup, [("CAS", "BOX", "1")]).status_code == 403


def test_validation_and_draft_editing(owner, user, world, setup):
    sales = user("sales")
    r = new_order(sales, world, setup, [("CAS", "KG", "2")])
    assert r.status_code == 422 and code(r) == "INVALID_UNIT"
    r = new_order(sales, world, setup, [("CAS", "BOX", "1.5")])
    assert r.status_code == 422 and code(r) == "UNIT_NOT_DIVISIBLE"
    r = new_order(sales, world, setup, [("CAS", "BOX", "1"), ("CAS", "BOX", "2")])
    assert r.status_code == 422 and code(r) == "DUPLICATE_LINE"
    r = new_order(sales, world, setup, [("CAS", "BOX", "1")], cust=str(world.wh_a))
    assert r.status_code == 422 and code(r) == "INVALID_CUSTOMER"

    d = new_order(sales, world, setup, [("NOP", "BOX", "1")]).json()
    assert d["status"] == "DRAFT" and d["lines"][0]["rate"] is None
    c = sales.post(
        f"{P}/sales-orders/{d['id']}/confirm", json={"version": d["version"]}, headers=idem()
    )
    assert c.status_code == 422 and code(c) == "NO_PRICE"

    body = {
        "version": d["version"],
        "customer_id": setup["cust"],
        "warehouse_id": str(world.wh_a),
        "lines": [{"product_id": setup["p"]["PLN"], "unit_code": "PIECE", "qty": "12"}],
    }
    e = sales.put(f"{P}/sales-orders/{d['id']}", json=body, headers=idem())
    assert e.status_code == 200, e.text
    e = e.json()
    assert [x["sku"] for x in e["lines"]] == ["PLN"] and e["version"] == d["version"] + 1
    assert e["source_snapshot"][0]["sku"] == "NOP"  # original capture untouched
    stale = sales.put(f"{P}/sales-orders/{d['id']}", json=body, headers=idem())
    assert stale.status_code == 409 and code(stale) == "VERSION_CONFLICT"

    cancel = sales.post(
        f"{P}/sales-orders/{d['id']}/cancel",
        json={"version": e["version"], "reason": "customer called"},
        headers=idem(),
    )
    assert cancel.status_code == 200 and cancel.json()["status"] == "CANCELLED"


def test_pick_rules(owner, user, world, setup):
    sales, wh = user("sales"), user("wh_a")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "5")])
    pk = open_pick(sales, o).json()
    assert open_pick(sales, sales.get(f"{P}/sales-orders/{o['id']}").json()).status_code == 409
    assert record(wh, pk, [(0, "6", None)]).json()["error"]["code"] == "OVER_PICK"
    assert record(wh, pk, [(0, "3", None)]).json()["error"]["code"] == "SHORTAGE_REASON_REQUIRED"
    assert record(wh, pk, [(0, "2.5", "DAMAGED")]).json()["error"]["code"] == "UNIT_NOT_DIVISIBLE"
    assert submit(wh, pk).status_code == 409  # nothing recorded yet (CREATED)
    rec = record(wh, pk, [(0, "3", "DAMAGED")]).json()
    stale = record(wh, pk, [(0, "3", "DAMAGED")])
    assert stale.status_code == 409 and code(stale) == "VERSION_CONFLICT"
    sub = submit(wh, rec).json()
    assert sub["status"] == "SUBMITTED"
    # verifier sends it back; warehouse corrects; verify
    back = owner.post(
        f"{P}/pick-attempts/{pk['id']}/return",
        json={"version": sub["version"], "reason": "recount please"},
        headers=idem(),
    ).json()
    assert back["status"] == "NEEDS_CORRECTION" and back["status_note"] == "recount please"
    rec = record(wh, back, [(0, "5", None)]).json()
    sub = submit(wh, rec).json()
    v = verify(owner, sub)
    assert v.status_code == 200 and v.json()["lines"][0]["outcome"] == "PICKED"
    o = owner.get(f"{P}/sales-orders/{o['id']}").json()
    assert line(o, "CAS")["pending_qty"] == "0.0000"
    assert open_pick(sales, o).json()["error"]["code"] == "NOTHING_TO_PICK"
    # awaiting invoice: close refused
    cl = owner.post(
        f"{P}/sales-orders/{o['id']}/close",
        json={"version": o["version"], "reason": "end of day"},
        headers=idem(),
    )
    assert cl.status_code == 409 and code(cl) == "AWAITING_INVOICE"
    # warehouse queue lookup by QR code
    q = wh.get(f"{P}/pick-attempts", params={"code": pk["pick_code"].lower()}).json()
    assert [x["id"] for x in q] == [pk["id"]]


def test_shortage_cancel_and_close(owner, user, world, setup):
    sales, wh = user("sales"), user("wh_a")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "10"), ("PLN", "PIECE", "6")])
    r = open_pick(sales, o)
    pk = r.json()
    sub = submit(wh, record(wh, pk, [(0, "0", "OUT_OF_STOCK"), (1, "6", None)]).json()).json()
    cas_id = pk["lines"][0]["order_line_id"]
    # cancel needs a reason
    bad = verify(owner, sub, {cas_id: "CANCEL"})
    assert bad.status_code == 422 and code(bad) == "REASON_REQUIRED"
    v = verify(owner, sub, {cas_id: "CANCEL"}, reason="customer declined backorder")
    assert v.status_code == 200, v.text
    o = owner.get(f"{P}/sales-orders/{o['id']}").json()
    cas = line(o, "CAS")
    assert (cas["cancelled_qty"], cas["pending_qty"], cas["backorder"]) == (
        "10.0000",
        "0.0000",
        False,
    )
    assert [a["kind"] for a in o["amendments"]] == ["SHORTAGE_CANCELLED"]

    # second order: partial line cancel then close with shortage
    o2 = confirmed(sales, world, setup, [("PLN", "PIECE", "10")])
    lid = o2["lines"][0]["id"]
    big = owner.post(
        f"{P}/sales-orders/{o2['id']}/lines/{lid}/cancel",
        json={"version": o2["version"], "qty": "11", "reason": "typo"},
        headers=idem(),
    )
    assert big.status_code == 409 and code(big) == "EXCEEDS_PENDING"
    lc = owner.post(
        f"{P}/sales-orders/{o2['id']}/lines/{lid}/cancel",
        json={"version": o2["version"], "qty": "4", "reason": "customer reduced"},
        headers=idem(),
    ).json()
    assert lc["lines"][0]["pending_qty"] == "6.0000"
    cl = owner.post(
        f"{P}/sales-orders/{o2['id']}/close",
        json={"version": lc["version"], "reason": "season over"},
        headers=idem(),
    ).json()
    assert cl["status"] == "CANCELLED"  # nothing dispatched -> cancelled, not shortage
    assert [a["kind"] for a in cl["amendments"]] == ["QTY_CANCELLED", "ORDER_CLOSED"]
    with get_sessionmaker()() as db:
        with pytest.raises(DBAPIError):
            db.execute(
                text("DELETE FROM sales_order_amendments WHERE order_id = :o"), {"o": o2["id"]}
            )


def test_abort_returns_quantities_to_pending(owner, user, world, setup):
    sales, wh = user("sales"), user("wh_a")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "4")])
    pk = open_pick(sales, o).json()
    rec = record(wh, pk, [(0, "4", None)]).json()
    ab = sales.post(
        f"{P}/pick-attempts/{pk['id']}/abort",
        json={"version": rec["version"], "reason": "sheet lost"},
        headers=idem(),
    )
    assert ab.status_code == 200 and ab.json()["status"] == "ABORTED"
    o = owner.get(f"{P}/sales-orders/{o['id']}").json()
    assert o["status"] == "CONFIRMED" and o["open_attempt_id"] is None
    assert line(o, "CAS")["pending_qty"] == "4.0000"
    # cannot cancel whole order once any (non-aborted) pick exists -- aborted is fine
    p2 = open_pick(sales, o).json()
    assert p2["attempt_no"] == 2
    o = owner.get(f"{P}/sales-orders/{o['id']}").json()
    cn = owner.post(
        f"{P}/sales-orders/{o['id']}/cancel",
        json={"version": o["version"], "reason": "changed mind"},
        headers=idem(),
    )
    assert cn.status_code == 409


def test_idempotent_replay(owner, user, world, setup):
    sales, wh = user("sales"), user("wh_a")
    key = idem()
    first = new_order(sales, world, setup, [("CAS", "BOX", "2")], key=key)
    for _ in range(20):
        again = new_order(sales, world, setup, [("CAS", "BOX", "2")], key=key)
        assert again.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert owner.get(f"{P}/sales-orders").json()["total"] == 1
    o = sales.post(
        f"{P}/sales-orders/{first.json()['id']}/confirm",
        json={"version": 1},
        headers=idem(),
    ).json()
    k2 = idem()
    a = open_pick(sales, o, key=k2)
    b = open_pick(sales, o, key=k2)
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    k3 = idem()
    r1 = record(wh, a.json(), [(0, "2", None)], key=k3)
    r2 = record(wh, a.json(), [(0, "2", None)], key=k3)
    assert r1.json() == r2.json() and r1.json()["version"] == 2


def test_concurrent_pick_open_and_numbering(owner, user, world, setup):
    sales = user("sales")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "5")])
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda _: open_pick(sales, o), range(8)))
    codes = sorted(r.status_code for r in res)
    assert codes.count(201) == 1 and set(codes) == {201, 409}
    with ThreadPoolExecutor(8) as ex:
        made = list(
            ex.map(lambda _: new_order(sales, world, setup, [("CAS", "BOX", "1")]), range(8))
        )
    nos = [r.json()["order_no"] for r in made if r.status_code == 201]
    assert len(nos) == 8 and len(set(nos)) == 8


def test_scope_and_tenancy(owner, user, world, setup, make_api):
    mgr = user("manager_a")  # MANAGER scoped to WA
    sales = user("sales")
    ob = confirmed(sales, world, setup, [("CAS", "BOX", "1")], wh=world.wh_b)
    oa = confirmed(sales, world, setup, [("CAS", "BOX", "1")])
    assert mgr.get(f"{P}/sales-orders/{ob['id']}").status_code == 403
    ids = [x["id"] for x in mgr.get(f"{P}/sales-orders").json()["items"]]
    assert ids == [oa["id"]]
    assert open_pick(mgr, ob).status_code == 403
    # another company sees nothing
    from .conftest import build_world

    other = build_world()
    x = make_api()
    x.login(other.emails["sales"])
    assert x.get(f"{P}/sales-orders/{oa['id']}").status_code == 404
    assert x.get(f"{P}/sales-orders").json()["total"] == 0


def test_failure_injection_rolls_back(owner, user, world, setup, monkeypatch):
    sales = user("sales")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "3")])
    real = audit.record

    def boom(*a, **kw):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(audit, "record", boom)
    key = idem()
    with pytest.raises(RuntimeError):
        open_pick(sales, o, key=key)
    monkeypatch.setattr(audit, "record", real)
    after = owner.get(f"{P}/sales-orders/{o['id']}").json()
    assert after["attempts"] == [] and after["status"] == "CONFIRMED"
    r = open_pick(sales, o, key=key)  # same key retried after the outage
    assert r.status_code == 201, r.text
