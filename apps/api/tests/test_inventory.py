"""Module 03 inventory: Postgres integration, authz, idempotency, concurrency, failure injection.

Synthetic data only. Acceptance (docs/modules/03-inventory.md): concurrent oversell rejected;
repeat 100 requests returns prior result; rebuild cached balance equals ledger.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading import audit
from pro_trading.db import get_sessionmaker

from .conftest import build_world, idem

P = "/api/v1"


@pytest.fixture
def owner(make_api, world):
    o = make_api()
    o.login(world.emails["owner"])
    assert o.enroll_and_verify().status_code == 200
    return o


@pytest.fixture
def prods(owner):
    out = {}
    for sku, expiry, profiles in [
        ("CAS", False, [("CASE40", "BOX", "40")]),
        ("PLN", True, [("PC", "PIECE", None)]),
    ]:
        body = {
            "sku": sku,
            "name": f"Product {sku}",
            "stock_unit": "BOX" if sku == "CAS" else "PIECE",
            "tracks_expiry": expiry,
            "unit_profiles": [
                {"code": c, "unit_code": u, "weight_lb_per_unit": w} for c, u, w in profiles
            ],
        }
        r = owner.post(f"{P}/products", json=body, headers=idem())
        assert r.status_code == 201, r.text
        out[sku] = r.json()["id"]
    return out


def opening(api, world, prod, lines, wh=None, key=None, **extra):
    body = {
        "warehouse_id": str(wh or world.wh_a),
        "product_id": prod,
        "lines": [{"unit_code": u, "qty": q} for u, q in lines],
        **extra,
    }
    return api.post(f"{P}/inventory/opening-balances", json=body, headers=key or idem())


def lot_of(r):
    assert r.status_code == 201, r.text
    return r.json()["lot_id"]


def adjust(api, lot, unit, delta, key=None, reason="LOST"):
    body = {
        "lot_id": lot,
        "unit_code": unit,
        "delta_qty": str(delta),
        "reason_code": reason,
        "note": "synthetic test",
    }
    return api.post(f"{P}/inventory/adjustments", json=body, headers=key or idem())


def bal(api, lot, unit):
    for b in api.get(f"{P}/inventory/lots/{lot}").json()["balances"]:
        if b["unit_code"] == unit:
            return b
    return None


def ledger(lot):
    with get_sessionmaker()() as s:
        return s.execute(
            text(
                "SELECT count(*), coalesce(sum(delta_qty),0) FROM stock_movements WHERE lot_id=:l"
            ),
            {"l": lot},
        ).one()


def test_opening_balance_separate_measures_and_reads(owner, world, prods):
    r = opening(owner, world, prods["CAS"], [("BOX", "7"), ("LB", "205.5")], batch_ref="B-1")
    lot = lot_of(r)
    body = r.json()
    assert [m["movement_type"] for m in body["movements"]] == ["OPENING_BALANCE"] * 2
    assert {m["unit_code"]: m["qty_after"] for m in body["movements"]} == {
        "BOX": "7.0000",
        "LB": "205.5000",
    }
    assert bal(owner, lot, "BOX")["available_qty"] == "7.0000"
    stock = owner.get(f"{P}/inventory/stock", params={"q": "cas"}).json()
    assert stock["total"] == 2 and {i["unit_code"] for i in stock["items"]} == {"BOX", "LB"}
    av = owner.get(f"{P}/inventory/availability", params={"product_id": prods["CAS"]}).json()
    assert {a["unit_code"]: a["available"] for a in av} == {"BOX": "7.0000", "LB": "205.5000"}
    mv = owner.get(f"{P}/inventory/movements", params={"lot_id": lot}).json()
    assert mv["total"] == 2 and mv["items"][0]["actor_name"] == "owner"


def test_unit_and_quantity_validation(owner, world, prods):
    assert (
        opening(owner, world, prods["CAS"], [("PIECE", "1")]).json()["error"]["code"]
        == "INVALID_UNIT"
    )
    assert (
        opening(owner, world, prods["CAS"], [("KG", "1")]).json()["error"]["code"]
        == "INVALID_UNIT"
    )
    r = opening(owner, world, prods["CAS"], [("BOX", "1.5")])
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNIT_NOT_DIVISIBLE"
    r = opening(owner, world, prods["PLN"], [("PIECE", "10")])
    assert r.json()["error"]["code"] == "EXPIRY_REQUIRED"
    r = opening(owner, world, prods["CAS"], [("BOX", "1"), ("BOX", "2")])
    assert r.json()["error"]["code"] == "DUPLICATE_UNIT"
    lot = lot_of(opening(owner, world, prods["CAS"], [("BOX", "5")], lot_code="LOT-X"))
    assert (
        opening(owner, world, prods["CAS"], [("BOX", "5")], lot_code="LOT-X").status_code == 409
    )
    assert adjust(owner, lot, "BOX", "0").json()["error"]["code"] == "ZERO_QUANTITY"
    r = adjust(owner, lot, "BOX", "-6")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_STOCK"
    assert r.json()["error"]["available"] == "5.0000"
    assert adjust(owner, lot, "LB", "-1").json()["error"]["code"] == "INSUFFICIENT_STOCK"


def test_repeat_100_requests_returns_prior_result(owner, world, prods):
    lot = lot_of(opening(owner, world, prods["CAS"], [("BOX", "50")]))
    key = idem()
    first = adjust(owner, lot, "BOX", "-3", key)
    assert first.status_code == 201
    for _ in range(100):
        r = adjust(owner, lot, "BOX", "-3", key)
        assert r.status_code == 201 and r.headers["Idempotent-Replay"] == "true"
        assert r.json() == first.json()
    assert ledger(lot) == (2, 47)
    assert bal(owner, lot, "BOX")["qty"] == "47.0000"
    # same key, different body -> refused, not silently re-posted
    assert adjust(owner, lot, "BOX", "-4", key).json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_concurrent_oversell_rejected(owner, world, prods):
    """INV-004 shape: stock 8, ten concurrent removals of 6 -> exactly one succeeds."""
    lot = lot_of(opening(owner, world, prods["CAS"], [("BOX", "8")]))
    with ThreadPoolExecutor(10) as ex:
        codes = list(ex.map(lambda _: adjust(owner, lot, "BOX", "-6").status_code, range(10)))
    assert codes.count(201) == 1 and codes.count(409) == 9, codes
    assert bal(owner, lot, "BOX")["qty"] == "2.0000"
    assert ledger(lot) == (2, 2)


def test_concurrent_identical_key_posts_once(owner, world, prods):
    lot = lot_of(opening(owner, world, prods["CAS"], [("BOX", "20")]))
    key = idem()
    with ThreadPoolExecutor(10) as ex:
        rs = list(ex.map(lambda _: adjust(owner, lot, "BOX", "-5", key), range(10)))
    ok = [r for r in rs if r.status_code == 201]
    assert ok and all(r.status_code in (201, 409) for r in rs)
    assert all(r.json()["source_id"] == ok[0].json()["source_id"] for r in ok)
    assert ledger(lot) == (2, 15)


def test_reservations_hold_stock(owner, world, prods, make_api):
    lot = lot_of(opening(owner, world, prods["CAS"], [("BOX", "10")]))
    sales = make_api()
    sales.login(world.emails["sales"])

    def reserve(api, qty, key=None):
        body = {"lot_id": lot, "unit_code": "BOX", "qty": qty, "source_ref": "SO-1"}
        return api.post(f"{P}/inventory/reservations", json=body, headers=key or idem())

    with ThreadPoolExecutor(5) as ex:
        codes = list(ex.map(lambda _: reserve(sales, "6").status_code, range(5)))
    assert codes.count(201) == 1, codes
    b = bal(owner, lot, "BOX")
    assert (b["qty"], b["reserved_qty"], b["available_qty"]) == ("10.0000", "6.0000", "4.0000")
    # on-hand can't drop below reserved
    assert adjust(owner, lot, "BOX", "-5").json()["error"]["code"] == "INSUFFICIENT_STOCK"
    assert adjust(owner, lot, "BOX", "-4").status_code == 201
    # reserved lot can't be quarantined
    v = owner.get(f"{P}/inventory/lots/{lot}").json()["version"]
    q = owner.post(
        f"{P}/inventory/lots/{lot}/status",
        json={"status": "QUARANTINE", "version": v, "reason": "temp check"},
        headers=idem(),
    )
    assert q.json()["error"]["code"] == "LOT_RESERVED"
    res = sales.get(f"{P}/inventory/reservations", params={"lot_id": lot}).json()
    rid = res[0]["id"]
    k = idem()
    r1 = sales.post(f"{P}/inventory/reservations/{rid}/release", headers=k)
    assert r1.status_code == 200 and r1.json()["status"] == "RELEASED"
    assert sales.post(f"{P}/inventory/reservations/{rid}/release", headers=k).json() == r1.json()
    again = sales.post(f"{P}/inventory/reservations/{rid}/release", headers=idem())
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_STATE"
    assert bal(owner, lot, "BOX")["reserved_qty"] == "0.0000"


def test_damage_return_reverse(owner, world, prods):
    lot = lot_of(opening(owner, world, prods["CAS"], [("BOX", "10")], lot_code="L-DMG"))
    r = owner.post(
        f"{P}/inventory/damage",
        json={"lot_id": lot, "unit_code": "BOX", "qty": "3", "note": "crushed pallet"},
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    comp = r.json()["lot_id"]
    assert [m["delta_qty"] for m in r.json()["movements"]] == ["-3.0000", "3.0000"]
    detail = owner.get(f"{P}/inventory/lots/{lot}").json()
    assert detail["damaged_lot_id"] == comp
    dmg = owner.get(f"{P}/inventory/lots/{comp}").json()
    assert dmg["status"] == "DAMAGED" and dmg["lot_code"] == "L-DMG-DMG"
    assert dmg["balances"][0]["available_qty"] == "0.0000"
    av = owner.get(f"{P}/inventory/availability", params={"product_id": prods["CAS"]}).json()
    assert (av[0]["available"], av[0]["unavailable"], av[0]["on_hand"]) == (
        "7.0000",
        "3.0000",
        "10.0000",
    )
    # damaged customer return goes to the same companion lot
    ret = owner.post(
        f"{P}/inventory/returns",
        json={"lot_id": lot, "unit_code": "BOX", "qty": "1", "condition": "DAMAGED",
              "source_ref": "INV-9"},
        headers=idem(),
    )  # fmt: skip
    assert ret.json()["lot_id"] == comp and bal(owner, comp, "BOX")["qty"] == "4.0000"
    # reversing one leg reverses the whole damage posting
    out_leg = r.json()["movements"][0]["id"]
    k = idem()
    rv = owner.post(
        f"{P}/inventory/movements/{out_leg}/reverse", json={"reason": "not damaged"}, headers=k
    )
    assert rv.status_code == 201, rv.text
    assert sorted(m["delta_qty"] for m in rv.json()["movements"]) == ["-3.0000", "3.0000"]
    assert bal(owner, lot, "BOX")["qty"] == "10.0000"
    assert bal(owner, comp, "BOX")["qty"] == "1.0000"
    assert (
        owner.post(
            f"{P}/inventory/movements/{out_leg}/reverse", json={"reason": "not damaged"}, headers=k
        ).json()
        == rv.json()
    )
    again = owner.post(
        f"{P}/inventory/movements/{out_leg}/reverse", json={"reason": "twice"}, headers=idem()
    )
    assert again.json()["error"]["code"] == "ALREADY_REVERSED"
    rid = rv.json()["movements"][0]["id"]
    rr = owner.post(
        f"{P}/inventory/movements/{rid}/reverse", json={"reason": "x-x-x"}, headers=idem()
    )
    assert rr.json()["error"]["code"] == "NOT_REVERSIBLE"
    mv = owner.get(f"{P}/inventory/movements", params={"lot_id": lot}).json()["items"]
    assert any(m["reversed_by_id"] for m in mv)


def test_reversal_refused_when_stock_already_used(owner, world, prods):
    lot = lot_of(opening(owner, world, prods["CAS"], [("BOX", "5")]))
    found = adjust(owner, lot, "BOX", "5", reason="FOUND").json()["movements"][0]["id"]
    assert adjust(owner, lot, "BOX", "-8").status_code == 201
    r = owner.post(
        f"{P}/inventory/movements/{found}/reverse", json={"reason": "typo"}, headers=idem()
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_STOCK"


def test_stock_count_flow_and_stale_count(owner, world, prods, make_api):
    lot = lot_of(opening(owner, world, prods["CAS"], [("BOX", "10")]))
    wh = make_api()
    wh.login(world.emails["wh_a"])

    def submit(qty):
        body = {
            "warehouse_id": str(world.wh_a),
            "lines": [{"lot_id": lot, "unit_code": "BOX", "counted_qty": qty}],
        }
        return wh.post(f"{P}/inventory/counts", json=body, headers=idem())  # fmt: skip

    c1 = submit("8")
    assert c1.status_code == 201, c1.text
    c1 = c1.json()
    assert c1["lines"][0]["variance"] == "-2.0000" and c1["status"] == "SUBMITTED"
    # warehouse staff count but cannot post
    assert wh.post(f"{P}/inventory/counts/{c1['id']}/post", json={"version": 1},
                   headers=idem()).status_code == 403  # fmt: skip
    # stock moves after counting -> whole count refused
    assert adjust(owner, lot, "BOX", "-1").status_code == 201
    assert owner.get(f"{P}/inventory/counts/{c1['id']}").json()["lines"][0]["stale"] is True
    st = owner.post(f"{P}/inventory/counts/{c1['id']}/post", json={"version": 1}, headers=idem())
    assert st.status_code == 409 and st.json()["error"]["code"] == "STALE_COUNT"
    rj = owner.post(
        f"{P}/inventory/counts/{c1['id']}/reject", json={"version": 1, "reason": "recount"},
        headers=idem(),
    )  # fmt: skip
    assert rj.json()["status"] == "REJECTED"
    c2 = submit("6").json()
    k = idem()
    posted = owner.post(f"{P}/inventory/counts/{c2['id']}/post", json={"version": 1}, headers=k)
    assert posted.status_code == 200, posted.text
    assert posted.json()["status"] == "POSTED" and posted.json()["lines"][0]["movement_id"]
    assert bal(owner, lot, "BOX")["qty"] == "6.0000"
    assert owner.post(f"{P}/inventory/counts/{c2['id']}/post", json={"version": 1},
                      headers=k).json() == posted.json()  # fmt: skip
    again = owner.post(f"{P}/inventory/counts/{c2['id']}/post", json={"version": 2}, headers=idem())
    assert again.json()["error"]["code"] == "INVALID_STATE"
    mv = owner.get(f"{P}/inventory/movements", params={"lot_id": lot,
                   "movement_type": "COUNT_ADJUSTMENT"}).json()  # fmt: skip
    assert mv["total"] == 1 and mv["items"][0]["delta_qty"] == "-3.0000"
    assert [c["count_no"][-4:] for c in owner.get(f"{P}/inventory/counts").json()] == [
        "0002",
        "0001",
    ]


def test_rebuild_cached_balance_equals_ledger(owner, world, prods, make_api):
    lot = lot_of(opening(owner, world, prods["CAS"], [("BOX", "10"), ("LB", "400")]))
    adjust(owner, lot, "BOX", "-2")
    clean = owner.get(f"{P}/inventory/reconciliation").json()
    assert clean["discrepancies"] == [] and clean["ledger_rows"] == 3
    with get_sessionmaker()() as s, s.begin():  # simulate cache drift (e.g. a bad manual fix)
        s.execute(
            text("UPDATE inventory_balances SET qty = 99 WHERE lot_id=:l AND unit_code='BOX'"),
            {"l": lot},
        )
    drift = owner.get(f"{P}/inventory/reconciliation").json()["discrepancies"]
    assert drift == [
        {
            "lot_id": lot,
            "lot_code": drift[0]["lot_code"],
            "unit_code": "BOX",
            "cached_qty": "99.0000",
            "ledger_qty": "8.0000",
            "cached_reserved": "0.0000",
            "active_reserved": "0.0000",
        }
    ]
    mgr = make_api()
    mgr.login(world.emails["manager_a"])
    mgr.enroll_and_verify()
    assert mgr.post(f"{P}/inventory/reconciliation/rebuild", headers=idem()).status_code == 403
    rb = owner.post(f"{P}/inventory/reconciliation/rebuild", headers=idem())
    assert rb.status_code == 200 and rb.json()["rebuilt"] is True
    assert owner.get(f"{P}/inventory/reconciliation").json()["discrepancies"] == []
    assert bal(owner, lot, "BOX")["qty"] == "8.0000" == f"{ledger(lot)[1] - 400:.4f}"


def test_ledger_is_append_only(owner, world, prods):
    lot = lot_of(opening(owner, world, prods["CAS"], [("BOX", "3")]))
    for sql in [
        "UPDATE stock_movements SET delta_qty = 100 WHERE lot_id = :l",
        "DELETE FROM stock_movements WHERE lot_id = :l",
        "DELETE FROM inventory_balances WHERE lot_id = :l",
        "DELETE FROM inventory_lots WHERE id = :l",
        "UPDATE inventory_lots SET warehouse_id = gen_random_uuid() WHERE id = :l",
    ]:
        with get_sessionmaker()() as s, pytest.raises(DBAPIError, match="append-only|cannot be"
                                                       "|immutable"):  # fmt: skip
            s.execute(text(sql), {"l": lot})
            s.flush()


def test_lot_status_and_expiry_affect_availability(owner, world, prods):
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    exp = lot_of(opening(owner, world, prods["PLN"], [("PIECE", "10")], expires_at=past))
    av = owner.get(f"{P}/inventory/availability", params={"product_id": prods["PLN"]}).json()
    assert av[0]["available"] == "0.0000" and av[0]["unavailable"] == "10.0000"
    assert bal(owner, exp, "PIECE")["expired"] is True
    r = owner.post(
        f"{P}/inventory/reservations",
        json={"lot_id": exp, "unit_code": "PIECE", "qty": "1", "source_ref": "SO-2"},
        headers=idem(),
    )
    assert r.json()["error"]["code"] == "LOT_NOT_SALEABLE"
    lot = lot_of(opening(owner, world, prods["CAS"], [("BOX", "4")]))
    q = owner.post(
        f"{P}/inventory/lots/{lot}/status",
        json={"status": "QUARANTINE", "version": 1, "reason": "temperature"},
        headers=idem(),
    )
    assert q.status_code == 200 and q.json()["status"] == "QUARANTINE"
    stale = owner.post(
        f"{P}/inventory/lots/{lot}/status",
        json={"status": "SALEABLE", "version": 1, "reason": "release"},
        headers=idem(),
    )
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"


def test_permissions_and_warehouse_scope(owner, world, prods, make_api):
    a = lot_of(opening(owner, world, prods["CAS"], [("BOX", "5")]))
    b = lot_of(opening(owner, world, prods["CAS"], [("BOX", "5")], wh=world.wh_b))

    wh = make_api()
    wh.login(world.emails["wh_a"])
    items = wh.get(f"{P}/inventory/stock").json()["items"]
    assert {i["lot_id"] for i in items} == {a}
    assert wh.get(f"{P}/inventory/lots/{b}").status_code == 403
    assert adjust(wh, a, "BOX", "-1").status_code == 403
    assert opening(wh, world, prods["CAS"], [("BOX", "1")]).status_code == 403

    sales = make_api()
    sales.login(world.emails["sales"])
    assert sales.get(f"{P}/inventory/stock").json()["total"] == 2
    assert adjust(sales, a, "BOX", "-1").status_code == 403

    mgr = make_api()
    mgr.login(world.emails["manager_a"])
    mgr.enroll_and_verify()  # optional two-step (2026-11-09); harmless here
    assert adjust(mgr, a, "BOX", "-1").status_code == 201
    assert adjust(mgr, b, "BOX", "-1").status_code == 403  # scoped to warehouse A
    assert opening(mgr, world, prods["CAS"], [("BOX", "1")], wh=world.wh_b).status_code == 403

    fin = make_api()
    fin.login(world.emails["finance"])
    fin.enroll_and_verify()
    assert fin.get(f"{P}/inventory/movements").status_code == 200
    assert adjust(fin, a, "BOX", "-1").status_code == 403

    other = build_world()
    x = make_api()
    x.login(other.emails["owner"])
    x.enroll_and_verify()
    assert x.get(f"{P}/inventory/lots/{a}").status_code == 404
    assert adjust(x, a, "BOX", "-1").status_code == 404
    assert x.get(f"{P}/inventory/stock").json()["total"] == 0


def test_failure_mid_command_rolls_back_and_retry_succeeds(owner, world, prods, monkeypatch):
    lot = lot_of(opening(owner, world, prods["CAS"], [("BOX", "9")]))
    real = audit.record

    def boom(*a, **kw):
        raise RuntimeError("injected audit failure")

    key = idem()
    monkeypatch.setattr(audit, "record", boom)
    try:
        assert adjust(owner, lot, "BOX", "-4", key).status_code == 500
    except RuntimeError:
        pass
    monkeypatch.setattr(audit, "record", real)
    assert ledger(lot) == (1, 9) and bal(owner, lot, "BOX")["qty"] == "9.0000"
    assert adjust(owner, lot, "BOX", "-4", key).status_code == 201
    assert ledger(lot) == (2, 5)


def test_sse_stream_emits_ready(owner):
    with owner.c.stream("GET", f"{P}/inventory/events", params={"timeout": 1}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        first = next(r.iter_lines())
    assert first == "event: ready"
