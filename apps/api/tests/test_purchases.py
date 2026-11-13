# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Purchases (owner decisions 2026-11-12, docs/modules/08-receiving.md).

- A purchase is in stock the moment it is saved: one transaction, one posting key per line,
  a retry or four people pressing at once add the stock once.
- Airport shipments are in stock before pickup and verified later; a difference is a
  correction with a reason and a manager's OK; stock already invoiced is never forced negative
  (D-05), the rest is flagged for the owner and invoicing is not blocked.
- Quick receive: goods on the shelf with no purchase; matched later to the real invoice
  without posting the stock again.
Synthetic data only.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading.db import get_sessionmaker

from .conftest import idem
from .test_orders import P, code, owner, setup, user  # noqa: F401

PDF = b"%PDF-1.4\n% synthetic supplier invoice\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
def vendor(owner):
    r = owner.post(
        f"{P}/vendors",
        json={"code": "V-JM1", "name": "Kingston Fresh Supply", "currency": "CAD"},
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def line(s, sku, unit, qty, cost=None, **kw):
    return {
        "product_id": s["p"][sku], "unit_code": unit, "qty": qty, "unit_cost": cost, **kw
    }  # fmt: skip


def purchase(api, world, vendor, lines, key=None, **kw):
    body = {"warehouse_id": str(world.wh_a), "vendor_id": vendor, "lines": lines, **kw}
    return api.post(f"{P}/purchases", json=body, headers=key or idem())


def quick(api, world, s, sku="CAS", unit="BOX", qty="5", key=None, **kw):
    body = {
        "warehouse_id": str(world.wh_a),
        "product_id": s["p"][sku],
        "unit_code": unit,
        "qty": qty,
        "reason": "Found on the shelf while packing",
        **kw,
    }
    return api.post(f"{P}/purchases/quick", json=body, headers=key or idem())


def act(api, pur, path, body=None, key=None):
    return api.post(
        f"{P}/purchases/{pur['id']}/{path}",
        json={"version": pur["version"], **(body or {})},
        headers=key or idem(),
    )


def saleable(company_id, product_id, unit="BOX"):
    """Free saleable stock of a product (on hand − reserved), straight from the balances."""
    with get_sessionmaker()() as db:
        return db.execute(
            text(
                "select coalesce(sum(b.qty - b.reserved_qty), 0) from inventory_balances b"
                " join inventory_lots l on l.id = b.lot_id"
                " where b.company_id = :c and b.product_id = :p and b.unit_code = :u"
                " and l.status = 'SALEABLE'"
            ),
            {"c": company_id, "p": product_id, "u": unit},
        ).scalar()


def ledger(company_id):
    """(rows, net quantity) of the whole stock ledger, and it must equal the balances."""
    with get_sessionmaker()() as db:
        n, total = db.execute(
            text(
                "select count(*), coalesce(sum(delta_qty),0) from stock_movements"
                " where company_id = :c"
            ),
            {"c": company_id},
        ).one()
        bal = db.execute(
            text("select coalesce(sum(qty),0) from inventory_balances where company_id = :c"),
            {"c": company_id},
        ).scalar()
    assert Decimal(total) == Decimal(bal)  # cached balances always equal the ledger
    return n, Decimal(total)


def invoice(owner, world, s, sku, unit, qty):
    r = owner.post(
        f"{P}/invoices/direct",
        json={
            "customer_id": s["cust"],
            "warehouse_id": str(world.wh_a),
            "lines": [{"product_id": s["p"][sku], "unit_code": unit, "qty": qty}],
            "dispatch_confirmed": True,
        },
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- stock on save


def test_saving_a_purchase_adds_its_lines_to_stock_at_once(owner, user, world, setup, vendor):
    wh = user("wh_a")  # warehouse staff: grn.record only, no approval step exists
    r = purchase(
        wh,
        world,
        vendor,
        [line(setup, "CAS", "BOX", "10", "20.00"), line(setup, "PLN", "PIECE", "50", "0.30")],
        vendor_invoice_no="INV-1001",
        invoice_date="2026-11-10",
    )
    assert r.status_code == 201, r.text
    p = r.json()
    assert (p["status"], p["stage"], p["entry"]) == ("POSTED", "IN_STOCK", "PURCHASE")
    assert p["grn_no"].startswith("GRN-") and p["posted_at"] and p["verify_status"] is None
    assert (p["total"], p["unpriced_lines"], p["line_count"]) == ("215.00", 0, 2)
    assert (p["vendor_invoice_no"], p["invoice_date"]) == ("INV-1001", "2026-11-10")
    assert [(c["sku"], c["accepted_qty"], bool(c["lot_id"])) for c in p["counted"]] == [
        ("CAS", "10.0000", True),
        ("PLN", "50.0000", True),
    ]
    assert [d["unit_cost"] for d in p["declared"]] == ["20.0000", "0.3000"]
    assert saleable(world.company_id, setup["p"]["CAS"]) == 10
    assert saleable(world.company_id, setup["p"]["PLN"], "PIECE") == 50
    with get_sessionmaker()() as db:
        rows = db.execute(
            text(
                "select movement_type, posting_key, source_type, delta_qty from stock_movements"
                " where source_id = :s order by delta_qty"
            ),
            {"s": p["id"]},
        ).all()
        audited = db.execute(
            text(
                "select count(*) from audit_events where action = 'shipment.purchase'"
                " and target_id = :s"
            ),
            {"s": p["id"]},
        ).scalar()
    assert [(r[0], r[2], r[3]) for r in rows] == [
        ("GRN_RECEIPT", "GRN", Decimal(10)),
        ("GRN_RECEIPT", "GRN", Decimal(50)),
    ]
    assert {r[1] for r in rows} == {f"grn:{c['id']}:accepted" for c in p["counted"]}
    assert audited == 1
    # it can be invoiced straight away
    assert invoice(owner, world, setup, "CAS", "BOX", "3")["invoice_no"].startswith("TT")
    # and feeds the supplier balance as before: invoice qty x cost
    led = owner.get(f"{P}/suppliers/{vendor}/ledger").json()
    assert [t["billed"] for t in led["totals"]] == ["215.00"]


def test_a_bad_line_saves_nothing(owner, world, setup, vendor):
    before = ledger(world.company_id)
    r = purchase(
        owner, world, vendor, [line(setup, "CAS", "BOX", "4"), line(setup, "CAS", "BOX", "2.5")]
    )
    assert r.status_code == 422 and code(r) == "UNIT_NOT_DIVISIBLE"
    r = purchase(
        owner,
        world,
        vendor,
        [line(setup, "CAS", "BOX", "4"), {"product_id": str(uuid.uuid4()), "unit_code": "BOX",
                                         "qty": "1"}],
    )  # fmt: skip
    assert r.status_code == 422 and code(r) == "INVALID_PRODUCT"
    assert purchase(owner, world, vendor, []).status_code == 422
    assert ledger(world.company_id) == before
    assert owner.get(f"{P}/shipments").json()["total"] == 0


def test_retry_and_people_pressing_at_once_add_stock_once(owner, world, setup, vendor):
    key = idem()
    lines = [line(setup, "CAS", "BOX", "10", "20.00")]

    def go(_):
        return purchase(owner, world, vendor, lines, key=key, vendor_invoice_no="INV-7")

    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(go, range(4)))
    assert {r.status_code for r in res} == {201}, [r.text for r in res]
    assert len({r.json()["id"] for r in res}) == 1
    assert ledger(world.company_id) == (1, Decimal(10))
    again = go(0)  # a late retry replays the stored answer
    assert again.status_code == 201 and again.headers["Idempotent-Replay"] == "true"
    assert ledger(world.company_id) == (1, Decimal(10))

    # The same invoice entered again (a new request, not a retry) is refused unless confirmed.
    r = purchase(owner, world, vendor, lines, vendor_invoice_no="inv-7")
    assert r.status_code == 409 and code(r) == "INVOICE_ALREADY_ENTERED"
    assert r.json()["error"]["shipment_no"] == res[0].json()["shipment_no"]
    assert ledger(world.company_id) == (1, Decimal(10))
    r = purchase(owner, world, vendor, lines, vendor_invoice_no="INV-7", confirm_duplicate=True)
    assert r.status_code == 201
    assert ledger(world.company_id) == (2, Decimal(20))


def test_two_people_entering_the_same_invoice_at_once(owner, world, setup, vendor):
    lines = [line(setup, "CAS", "BOX", "6", "20.00")]

    def go(_):
        return purchase(owner, world, vendor, lines, vendor_invoice_no="INV-88").status_code

    with ThreadPoolExecutor(4) as ex:
        codes = sorted(ex.map(go, range(4)))
    assert codes == [201, 409, 409, 409]
    assert ledger(world.company_id) == (1, Decimal(6))


def test_permissions_are_enforced_on_the_server(owner, user, world, setup, vendor):
    lines = [line(setup, "CAS", "BOX", "1")]
    for who in ("sales", "finance"):
        api = user(who)
        assert purchase(api, world, vendor, lines).status_code == 403
        assert quick(api, world, setup).status_code == 403
        assert api.get(f"{P}/shipments").status_code == 403
    # another warehouse is out of a warehouse role's reach
    r = user("wh_a").post(
        f"{P}/purchases",
        json={"warehouse_id": str(world.wh_b), "vendor_id": vendor, "lines": lines},
        headers=idem(),
    )
    assert r.status_code == 403
    assert ledger(world.company_id) == (0, Decimal(0))


def test_kg_line_for_a_product_kept_in_lb_is_saved_in_lb(owner, world, setup, vendor):
    r = purchase(owner, world, vendor, [line(setup, "CAS", "KG", "10", "4.00")])
    assert r.status_code == 201, r.text
    c, d = r.json()["counted"][0], r.json()["declared"][0]
    assert (c["unit_code"], c["accepted_qty"], c["note"]) == (
        "LB", "22.0462", "Entered in kg: 10 kg"
    )  # fmt: skip
    assert (d["unit_code"], d["declared_qty"], d["unit_cost"]) == ("LB", "22.0462", "1.8144")
    assert r.json()["total"] == "40.00"


def test_expiry_is_asked_only_for_goods_going_into_stock_now(owner, world, setup, vendor):
    r = owner.post(
        f"{P}/products",
        json={"sku": "MLK", "name": "Coconut milk", "stock_unit": "PIECE", "tracks_expiry": True},
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    setup["p"]["MLK"] = r.json()["id"]
    exp = "2027-06-30T12:00:00Z"
    r = purchase(owner, world, vendor, [line(setup, "MLK", "PIECE", "24", "1.10")])
    assert r.status_code == 422 and code(r) == "EXPIRY_REQUIRED"
    assert quick(owner, world, setup, sku="MLK", unit="PIECE").status_code == 422
    r = purchase(owner, world, vendor, [line(setup, "MLK", "PIECE", "24", "1.10", expires_at=exp)])
    assert r.status_code == 201 and r.json()["counted"][0]["expires_at"].startswith("2027-06-30")
    q = quick(owner, world, setup, sku="MLK", unit="PIECE", qty="6", expires_at=exp)
    assert q.status_code == 201, q.text
    # matching it later only adds the cost: its batch already has the date
    r = act(
        owner,
        q.json(),
        "complete",
        {"vendor_id": vendor, "lines": [line(setup, "MLK", "PIECE", "6", "1.00")]},
    )
    assert r.status_code == 200, r.text
    assert saleable(world.company_id, setup["p"]["MLK"], "PIECE") == 30


def test_a_line_missed_on_a_saved_purchase_is_added_to_stock(owner, user, world, setup, vendor):
    wh = user("wh_a")
    p = purchase(wh, world, vendor, [line(setup, "CAS", "BOX", "4", "20.00")]).json()
    key = idem()
    r = act(wh, p, "lines", {"lines": [line(setup, "PLN", "PIECE", "12", "0.30")]}, key=key)
    assert r.status_code == 201, r.text
    again = act(wh, p, "lines", {"lines": [line(setup, "PLN", "PIECE", "12", "0.30")]}, key=key)
    assert again.status_code == 201 and again.headers["Idempotent-Replay"] == "true"
    d = r.json()
    assert (d["total"], d["line_count"], len(d["counted"])) == ("83.60", 2, 2)
    assert saleable(world.company_id, setup["p"]["PLN"], "PIECE") == 12  # once
    assert saleable(world.company_id, setup["p"]["CAS"]) == 4  # the first line was not touched
    q = quick(wh, world, setup).json()
    r = act(wh, q, "lines", {"lines": [line(setup, "PLN", "PIECE", "1")]})
    assert r.status_code == 409 and code(r) == "INVALID_STATE"  # complete it first


# ---------------------------------------------------------------- airport shipments


def airport(api, world, setup, vendor, qty="10", **kw):
    r = purchase(
        api,
        world,
        vendor,
        [line(setup, "CAS", "BOX", qty, "20.00")],
        is_airport=True,
        awb_no="176-1234 5675",
        expected_on="2026-11-14",
        **kw,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_airport_shipment_is_in_stock_before_pickup_then_verified(owner, user, world, setup,
                                                                 vendor):  # fmt: skip
    wh = user("wh_a")
    p = airport(wh, world, setup, vendor, expected_pieces=10)
    assert (p["stage"], p["verify_status"], p["is_airport"]) == ("TO_VERIFY", "TO_VERIFY", True)
    assert (p["awb_no"], p["expected_on"], p["expected_pieces"]) == (
        "176-1234 5675", "2026-11-14", 10
    )  # fmt: skip
    assert saleable(world.company_id, setup["p"]["CAS"]) == 10  # before the goods are fetched
    invoice(owner, world, setup, "CAS", "BOX", "4")  # invoices can be printed on arrival

    listed = wh.get(f"{P}/shipments", params={"stage": "TO_VERIFY"}).json()
    assert [x["id"] for x in listed["items"]] == [p["id"]]
    # a count that differs needs the lines that differ
    r = act(wh, p, "verify", {"pieces_counted": 8})
    assert r.status_code == 422 and code(r) == "PIECES_DIFFER"
    r = act(wh, p, "verify", {"pieces_counted": 10})
    assert r.status_code == 200, r.text
    v = r.json()
    assert (v["stage"], v["verified_pieces"], v["corrections"]) == ("VERIFIED", 10, [])
    assert v["verified_by_name"] and v["verified_at"]
    assert saleable(world.company_id, setup["p"]["CAS"]) == 6  # verification moved no stock
    r = act(wh, v, "verify", {"pieces_counted": 10})
    assert r.status_code == 409 and code(r) == "INVALID_STATE"
    # a local purchase is never verified
    local = purchase(owner, world, vendor, [line(setup, "CAS", "BOX", "1")]).json()
    r = act(owner, local, "verify", {"pieces_counted": 1})
    assert r.status_code == 409 and code(r) == "NOT_AIRPORT"


def test_a_shortage_waits_for_the_managers_ok_and_posts_once(owner, user, world, setup, vendor):
    wh, mgr = user("wh_a"), user("manager_a")
    p = airport(wh, world, setup, vendor)
    ln = p["counted"][0]["id"]
    short = {"pieces_counted": 8, "lines": [{"line_id": ln, "arrived_qty": "8"}]}
    r = act(wh, p, "verify", short)
    assert r.status_code == 422 and code(r) == "REASON_REQUIRED"
    r = act(wh, p, "verify", {**short, "reason": "Two boxes missing at the airport"})
    assert r.status_code == 200, r.text
    v = r.json()
    assert (v["stage"], v["verify_status"], v["pending_corrections"]) == (
        "TO_APPROVE", "PENDING_OK", 1
    )  # fmt: skip
    c = v["corrections"][0]
    assert (c["kind"], c["qty"], c["status"], c["source"]) == ("SHORT", "2.0000", "PENDING",
                                                               "VERIFY")  # fmt: skip
    assert saleable(world.company_id, setup["p"]["CAS"]) == 10  # nothing moves without the OK
    r = act(wh, v, f"corrections/{c['id']}/approve")
    assert r.status_code == 403

    def go(_):
        return act(mgr, v, f"corrections/{c['id']}/approve").status_code

    with ThreadPoolExecutor(4) as ex:
        codes = sorted(ex.map(go, range(4)))
    assert codes.count(200) == 1 and set(codes) <= {200, 409}, codes
    assert saleable(world.company_id, setup["p"]["CAS"]) == 8
    done = mgr.get(f"{P}/shipments/{p['id']}").json()
    assert (done["stage"], done["pending_corrections"], done["review_count"]) == ("VERIFIED", 0, 0)
    fixed = done["corrections"][0]
    assert (fixed["status"], fixed["posted_qty"], fixed["uncovered_qty"]) == (
        "POSTED", "2.0000", "0.0000"
    )  # fmt: skip
    with get_sessionmaker()() as db:
        rows = db.execute(
            text(
                "select movement_type, reason_code, delta_qty, posting_key from stock_movements"
                " where source_id = :s and movement_type = 'GRN_CORRECTION'"
            ),
            {"s": p["id"]},
        ).all()
    assert [tuple(r) for r in rows] == [
        ("GRN_CORRECTION", "SHORT", Decimal(-2), f"grnfix:{c['id']}:out")
    ]
    assert ledger(world.company_id) == (2, Decimal(8))
    # the supplier's bill is still what the invoice says; the shortage shows as not accepted
    led = owner.get(f"{P}/suppliers/{vendor}/ledger").json()
    assert led["totals"][0]["billed"] == "200.00"
    u = led["shipments"][0]["units"][0]
    assert (u["declared"], u["accepted"], u["rejected"]) == ("10.0000", "8.0000", "2.0000")


def test_a_manager_verifying_posts_the_difference_at_once_and_retry_is_safe(
    owner, user, world, setup, vendor
):
    p = airport(owner, world, setup, vendor)
    ln = p["counted"][0]["id"]
    key = idem()
    body = {
        "pieces_counted": 9,
        "lines": [{"line_id": ln, "arrived_qty": "9", "damaged_qty": "1"}],
        "reason": "One short, one crushed",
    }
    r = act(owner, p, "verify", body, key=key)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["stage"] == "VERIFIED"
    assert sorted((c["kind"], c["posted_qty"]) for c in v["corrections"]) == [
        ("DAMAGED", "1.0000"),
        ("SHORT", "1.0000"),
    ]
    assert saleable(world.company_id, setup["p"]["CAS"]) == 8  # 10 - 1 short - 1 damaged
    again = act(owner, p, "verify", body, key=key)
    assert again.status_code == 200 and again.headers["Idempotent-Replay"] == "true"
    assert saleable(world.company_id, setup["p"]["CAS"]) == 8
    with get_sessionmaker()() as db:
        dmg = db.execute(
            text(
                "select coalesce(sum(b.qty),0) from inventory_balances b join inventory_lots l"
                " on l.id = b.lot_id where b.company_id = :c and l.status = 'DAMAGED'"
            ),
            {"c": world.company_id},
        ).scalar()
    assert dmg == 1  # damaged goods are put aside, not lost
    assert ledger(world.company_id) == (4, Decimal(9))


def test_refusing_a_difference_sends_the_shipment_back_to_be_counted(owner, user, world, setup,
                                                                    vendor):  # fmt: skip
    wh = user("wh_a")
    p = airport(wh, world, setup, vendor)
    ln = p["counted"][0]["id"]
    v = act(
        wh,
        p,
        "verify",
        {"pieces_counted": 7, "lines": [{"line_id": ln, "arrived_qty": "7"}], "reason": "typo"},
    ).json()
    cid = v["corrections"][0]["id"]
    r = act(owner, v, f"corrections/{cid}/reject")
    assert r.status_code == 422 and code(r) == "REASON_REQUIRED"
    r = act(owner, v, f"corrections/{cid}/reject", {"note": "Recount: all ten are there"})
    assert r.status_code == 200, r.text
    back = r.json()
    assert (back["stage"], back["corrections"][0]["status"]) == ("TO_VERIFY", "REJECTED")
    assert saleable(world.company_id, setup["p"]["CAS"]) == 10
    assert act(wh, back, "verify", {"pieces_counted": 10}).json()["stage"] == "VERIFIED"


def test_shortage_on_stock_already_invoiced_is_flagged_never_negative(owner, user, world, setup,
                                                                     vendor):  # fmt: skip
    """10 entered before pickup, 9 invoiced, 8 arrived: only 1 box is still on the shelf to
    take. It is taken, the other missing box is flagged for the owner, stock stays at zero
    (D-05) and invoicing carries on."""
    p = airport(owner, world, setup, vendor)
    inv_no = invoice(owner, world, setup, "CAS", "BOX", "9")["invoice_no"]
    ln = p["counted"][0]["id"]
    r = act(
        owner,
        p,
        "verify",
        {
            "pieces_counted": 8,
            "lines": [{"line_id": ln, "arrived_qty": "8"}],
            "reason": "Two boxes short at the airport",
        },
    )
    assert r.status_code == 200, r.text  # still posted, never refused
    v = r.json()
    c = v["corrections"][0]
    assert (c["status"], c["posted_qty"], c["uncovered_qty"], c["needs_review"]) == (
        "POSTED", "1.0000", "1.0000", True
    )  # fmt: skip
    assert c["invoices"] == [inv_no]
    assert (v["stage"], v["review_count"]) == ("VERIFIED", 1)
    assert saleable(world.company_id, setup["p"]["CAS"]) == 0
    with get_sessionmaker()() as db:
        lowest = db.execute(
            text("select min(qty) from inventory_balances where company_id = :c"),
            {"c": world.company_id},
        ).scalar()
        alerts = db.execute(
            text(
                "select severity, title from notifications where company_id = :c"
                " and kind = 'purchase.review'"
            ),
            {"c": world.company_id},
        ).all()
    assert lowest == 0
    assert len(alerts) == 1 and alerts[0][0] == "critical"
    # invoicing is not blocked: new stock can be sold as usual
    purchase(owner, world, vendor, [line(setup, "CAS", "BOX", "3", "20.00")])
    invoice(owner, world, setup, "CAS", "BOX", "3")
    # the owner clears the flag with a note on what was done
    r = act(user("wh_a"), v, f"corrections/{c['id']}/reviewed", {"note": "Credited"})
    assert r.status_code == 403
    r = act(owner, v, f"corrections/{c['id']}/reviewed", {"note": "Customer credited one box"})
    assert r.status_code == 200, r.text
    assert (r.json()["review_count"], r.json()["corrections"][0]["review_note"]) == (
        0, "Customer credited one box"
    )  # fmt: skip


# ---------------------------------------------------------------- quick receive


def test_quick_receive_makes_stock_available_and_asks_for_details(owner, user, world, setup):
    wh = user("wh_a")
    bad = quick(wh, world, setup, reason="x")
    assert bad.status_code == 422  # a reason is required
    key = idem()
    r = quick(wh, world, setup, needed_for="Packing sheet PK-0007", key=key)
    assert r.status_code == 201, r.text
    q = r.json()
    assert (q["entry"], q["stage"], q["needs_details"], q["vendor_id"]) == (
        "QUICK", "TO_COMPLETE", True, None
    )  # fmt: skip
    assert (q["quick_reason"], q["note"]) == (
        "Found on the shelf while packing", "Packing sheet PK-0007"
    )  # fmt: skip
    assert q["items"] == [
        {
            "product_id": setup["p"]["CAS"], "sku": "CAS", "product_name": "Cassava",
            "unit_code": "BOX", "qty": "5.0000",
        }
    ]  # fmt: skip
    assert (
        quick(wh, world, setup, key=key, needed_for="Packing sheet PK-0007").json()["id"]
        == (q["id"])
    )
    assert saleable(world.company_id, setup["p"]["CAS"]) == 5  # once, and available at once
    invoice(owner, world, setup, "CAS", "BOX", "2")  # the blocked invoice can go out
    todo = wh.get(f"{P}/shipments", params={"stage": "TO_COMPLETE"}).json()
    assert [x["shipment_no"] for x in todo["items"]] == [q["shipment_no"]]
    with get_sessionmaker()() as db:
        ev = db.execute(
            text(
                "select detail_redacted from audit_events where action = 'shipment.quick_receive'"
                " and target_id = :s"
            ),
            {"s": q["id"]},
        ).scalar()
    assert ev["reason"] == "Found on the shelf while packing" and ev["qty"] == "5.0000"
    assert ev["needed_for"] == "Packing sheet PK-0007"
    # no supplier, no cost: nobody is billed yet
    assert all(not b["totals"] for b in owner.get(f"{P}/suppliers/balances").json())
    # documents and costs wait for the details
    r = wh.c.post(
        f"{P}/shipments/{q['id']}/documents/upload",
        params={"kind": "INVOICE", "version": q["version"], "filename": "inv.pdf"},
        content=PDF,
        headers={**wh._h(idem()), "Content-Type": "application/pdf"},
    )
    assert r.status_code == 409 and code(r) == "SUPPLIER_NEEDED"


def test_matching_a_quick_receive_never_posts_its_stock_again(owner, user, world, setup, vendor):
    wh = user("wh_a")
    q = quick(wh, world, setup).json()
    before = ledger(world.company_id)
    body = {
        "vendor_id": vendor,
        "vendor_invoice_no": "INV-300",
        "invoice_date": "2026-11-09",
        "lines": [line(setup, "CAS", "BOX", "5", "18.00")],
    }
    r = act(wh, q, "complete", body)
    assert r.status_code == 200, r.text
    done = r.json()
    assert (done["stage"], done["needs_details"], done["vendor_id"]) == ("IN_STOCK", False, vendor)
    assert (done["total"], done["vendor_invoice_no"], done["corrections"]) == (
        "90.00", "INV-300", []
    )  # fmt: skip
    assert done["details_completed_by_name"] and len(done["counted"]) == 1
    assert ledger(world.company_id) == before  # same stock, same single ledger row
    assert wh.get(f"{P}/shipments", params={"stage": "TO_COMPLETE"}).json()["total"] == 0
    led = owner.get(f"{P}/suppliers/{vendor}/ledger").json()
    assert led["totals"][0]["billed"] == "90.00"  # billed only once completed
    r = act(wh, done, "complete", body)
    assert r.status_code == 409 and code(r) == "INVALID_STATE"


def test_matching_with_a_different_quantity_becomes_a_correction(owner, user, world, setup,
                                                                vendor):  # fmt: skip
    wh = user("wh_a")
    q = quick(wh, world, setup).json()  # 5 boxes on the shelf
    body = {
        "vendor_id": vendor,
        "vendor_invoice_no": "INV-301",
        "lines": [line(setup, "CAS", "BOX", "6", "18.00"), line(setup, "PLN", "PIECE", "20")],
    }
    r = act(wh, q, "complete", body)
    assert r.status_code == 422 and code(r) == "MATCH_REASON_REQUIRED"
    d = r.json()["error"]["differences"][0]
    assert (d["sku"], d["invoice_qty"], d["received_qty"], d["difference"]) == (
        "CAS", "6.0000", "5.0000", "1.0000"
    )  # fmt: skip
    assert saleable(world.company_id, setup["p"]["PLN"], "PIECE") == 0  # nothing half-saved
    r = act(wh, q, "complete", {**body, "reason": "Sixth box was behind the pallet"})
    assert r.status_code == 200, r.text
    done = r.json()
    # the plantain on the invoice was never entered: it goes to stock now
    assert saleable(world.company_id, setup["p"]["PLN"], "PIECE") == 20
    # the extra box waits for a manager (warehouse staff cannot approve)
    c = done["corrections"][0]
    assert (c["kind"], c["qty"], c["status"], c["source"]) == ("OVER", "1.0000", "PENDING",
                                                               "MATCH")  # fmt: skip
    assert saleable(world.company_id, setup["p"]["CAS"]) == 5
    ok = act(owner, done, f"corrections/{c['id']}/approve")
    assert ok.status_code == 200, ok.text
    assert saleable(world.company_id, setup["p"]["CAS"]) == 6
    assert ok.json()["total"] == "108.00" and ok.json()["unpriced_lines"] == 1

    # the invoice says fewer than were added: the difference comes off, posted by a manager
    q2 = quick(owner, world, setup, qty="4").json()
    r = act(
        owner,
        q2,
        "complete",
        {
            "vendor_id": vendor,
            "lines": [line(setup, "CAS", "BOX", "3", "18.00")],
            "reason": "Counted one box twice",
        },
    )
    assert r.status_code == 200, r.text
    c2 = r.json()["corrections"][0]
    assert (c2["kind"], c2["status"], c2["posted_qty"]) == ("SHORT", "POSTED", "1.0000")
    assert saleable(world.company_id, setup["p"]["CAS"]) == 9  # 6 + 4 - 1


def test_an_invoice_can_cover_quick_receives_made_earlier(owner, user, world, setup, vendor):
    wh = user("wh_a")
    q1 = quick(wh, world, setup, qty="5").json()
    q2 = quick(wh, world, setup, sku="PLN", unit="PIECE", qty="30").json()
    lines = [
        line(setup, "CAS", "BOX", "5", "18.00"),
        line(setup, "PLN", "PIECE", "30", "0.25"),
        line(setup, "NOP", "BOX", "2", "9.00"),
    ]
    r = purchase(wh, world, vendor, lines, absorb_quick_ids=[q1["id"], q2["id"]])
    assert r.status_code == 201, r.text
    p = r.json()
    # only the goods that were not on the shelf yet are added now
    assert [(c["sku"], c["accepted_qty"]) for c in p["counted"]] == [("NOP", "2.0000")]
    assert saleable(world.company_id, setup["p"]["CAS"]) == 5
    assert saleable(world.company_id, setup["p"]["PLN"], "PIECE") == 30
    assert saleable(world.company_id, setup["p"]["NOP"]) == 2
    assert (p["total"], sorted(a["shipment_no"] for a in p["absorbed"])) == (
        "115.50", sorted([q1["shipment_no"], q2["shipment_no"]])
    )  # fmt: skip
    m = wh.get(f"{P}/shipments/{q1['id']}").json()
    assert (m["stage"], m["matched_shipment_no"], m["vendor_id"]) == (
        "IN_STOCK", p["shipment_no"], vendor
    )  # fmt: skip
    assert wh.get(f"{P}/shipments", params={"stage": "TO_COMPLETE"}).json()["total"] == 0
    # billed once, on the invoice's purchase
    led = owner.get(f"{P}/suppliers/{vendor}/ledger").json()
    assert led["totals"][0]["billed"] == "115.50" and len(led["shipments"]) == 1
    # a quick receive is matched once, and only to an invoice that lists its goods
    r = purchase(wh, world, vendor, lines[:1], absorb_quick_ids=[q1["id"]])
    assert r.status_code == 409 and code(r) == "QUICK_ALREADY_MATCHED"
    q3 = quick(wh, world, setup, qty="1").json()
    r = purchase(wh, world, vendor, lines[1:2], absorb_quick_ids=[q3["id"]])
    assert r.status_code == 422 and code(r) == "QUICK_NOT_ON_INVOICE"
    assert saleable(world.company_id, setup["p"]["PLN"], "PIECE") == 30


def test_quick_receives_at_once_and_negative_stock_stays_refused(owner, user, world, setup):
    wh = user("wh_a")

    def go(_):
        return quick(wh, world, setup, qty="2").status_code

    with ThreadPoolExecutor(4) as ex:
        assert list(ex.map(go, range(4))) == [201] * 4  # four separate finds, four records
    assert saleable(world.company_id, setup["p"]["CAS"]) == 8
    r = owner.post(
        f"{P}/invoices/direct",
        json={
            "customer_id": setup["cust"],
            "warehouse_id": str(world.wh_a),
            "lines": [{"product_id": setup["p"]["CAS"], "unit_code": "BOX", "qty": "9"}],
            "dispatch_confirmed": True,
        },
        headers=idem(),
    )
    assert r.status_code == 409 and code(r) == "INSUFFICIENT_STOCK"  # D-05 unchanged


def test_daily_reminder_while_purchases_wait_for_their_invoice(owner, user, world, setup, vendor):
    from datetime import UTC, datetime

    from pro_trading import purchases as pur

    def reminders():
        with get_sessionmaker()() as db:
            return db.execute(
                text(
                    "select title, link from notifications where company_id = :c"
                    " and kind = 'purchase.to_complete' order by created_at"
                ),
                {"c": world.company_id},
            ).all()

    def run(at):
        with get_sessionmaker()() as db:
            pur.remind_to_complete(db, at)

    noon = datetime(2026, 11, 13, 17, 0, tzinfo=UTC)
    run(noon)
    assert reminders() == []  # nothing is waiting
    q = quick(user("wh_a"), world, setup).json()
    quick(user("wh_a"), world, setup, sku="PLN", unit="PIECE", qty="3")
    run(datetime(2026, 11, 13, 3, 0, tzinfo=UTC))  # 03:00 company time: not in the night
    assert reminders() == []
    run(noon)
    run(noon)  # the hourly check runs again: still one reminder for the day
    assert [tuple(r) for r in reminders()] == [
        ("2 purchases to complete", "/purchases?view=complete")
    ]
    run(datetime(2026, 11, 14, 17, 0, tzinfo=UTC))
    assert len(reminders()) == 2  # next day, still waiting
    # once matched, the reminders stop
    for x in owner.get(f"{P}/shipments", params={"stage": "TO_COMPLETE"}).json()["items"]:
        sku, unit = x["items"][0]["sku"], x["items"][0]["unit_code"]
        r = act(
            owner,
            x,
            "complete",
            {"vendor_id": vendor, "lines": [line(setup, sku, unit, x["items"][0]["qty"], "1.00")]},
        )
        assert r.status_code == 200, r.text
    run(datetime(2026, 11, 15, 17, 0, tzinfo=UTC))
    assert len(reminders()) == 2 and q["needs_details"]


# ---------------------------------------------------------------- later fixes, documents, costs


def test_later_corrections_are_new_movements_with_a_reason(owner, user, world, setup, vendor):
    wh = user("wh_a")
    p = purchase(owner, world, vendor, [line(setup, "CAS", "BOX", "10", "20.00")]).json()
    ln = p["counted"][0]["id"]
    r = act(owner, p, "corrections", {"line_id": ln, "kind": "DAMAGED", "qty": "2", "reason": ""})
    assert r.status_code == 422
    r = act(
        owner,
        p,
        "corrections",
        {"line_id": ln, "kind": "RETURNED", "qty": "11", "reason": "Sent back"},
    )
    assert r.status_code == 422 and code(r) == "MORE_THAN_RECEIVED"
    r = act(
        owner,
        p,
        "corrections",
        {"line_id": ln, "kind": "DAMAGED", "qty": "2", "reason": "Crushed in the cooler"},
    )
    assert r.status_code == 201, r.text
    p = r.json()
    assert saleable(world.company_id, setup["p"]["CAS"]) == 8
    r = act(
        wh,
        p,
        "corrections",
        {"line_id": ln, "kind": "RETURNED", "qty": "3", "reason": "Wrong variety, sent back"},
    )
    assert r.status_code == 201 and r.json()["corrections"][1]["status"] == "PENDING"
    assert saleable(world.company_id, setup["p"]["CAS"]) == 8  # waits for the manager's OK
    # the receipt row itself is never edited
    with get_sessionmaker()() as db:
        first = db.execute(
            text(
                "select delta_qty from stock_movements where source_id = :s"
                " and movement_type = 'GRN_RECEIPT'"
            ),
            {"s": p["id"]},
        ).scalar()
    assert first == 10


def test_documents_can_be_added_later_and_landed_costs_are_for_airport_only(
    owner, world, setup, vendor
):
    local = purchase(owner, world, vendor, [line(setup, "CAS", "BOX", "1", "20.00")]).json()
    up = owner.c.post(
        f"{P}/shipments/{local['id']}/documents/upload",
        params={"kind": "INVOICE", "version": local["version"], "filename": "inv.pdf"},
        content=PDF,
        headers={**owner._h(idem()), "Content-Type": "application/pdf"},
    )
    assert up.status_code == 201, up.text  # a purchase already in stock still takes documents
    doc = up.json()["documents"][0]
    raw = owner.get(f"{P}/scans/documents/{doc['document_id']}/content")
    assert raw.status_code == 200 and raw.content == PDF  # kept exactly as received
    cost = {"version": 1, "kind": "FREIGHT", "amount": "120.00", "currency": "CAD"}
    r = owner.post(f"{P}/shipments/{local['id']}/costs", json=cost, headers=idem())
    assert r.status_code == 422 and code(r) == "NOT_AIRPORT"
    air = airport(owner, world, setup, vendor)
    r = owner.post(f"{P}/shipments/{air['id']}/costs", json=cost, headers=idem())
    assert r.status_code == 201 and r.json()["cost_totals"] == {"CAD": "120.00"}
    cfia = owner.c.post(
        f"{P}/shipments/{air['id']}/documents/upload",
        params={"kind": "CFIA", "version": air["version"], "filename": "cfia.pdf"},
        content=PDF + b"cfia",
        headers={**owner._h(idem()), "Content-Type": "application/pdf"},
    )
    assert cfia.status_code == 201 and cfia.json()["documents"][0]["kind"] == "CFIA"
    # landed costs stay out of what the supplier is owed
    led = owner.get(f"{P}/suppliers/{vendor}/ledger").json()
    assert led["totals"][0]["billed"] == "220.00"


# ---------------------------------------------------------------- old shipments, database guards


def test_an_old_open_shipment_is_added_to_stock_without_approval(owner, user, world, setup,
                                                                 vendor):  # fmt: skip
    wh = user("wh_a")
    r = wh.post(
        f"{P}/shipments",
        json={"warehouse_id": str(world.wh_a), "vendor_id": vendor},
        headers=idem(),
    )
    assert r.status_code == 201 and (r.json()["entry"], r.json()["stage"]) == (
        "LEGACY", "NOT_IN_STOCK"
    )  # fmt: skip
    s = r.json()
    empty = act(wh, s, "stock")
    assert empty.status_code == 422 and code(empty) == "NO_LINES"
    r = wh.post(
        f"{P}/shipments/{s['id']}/declared",
        json={
            "version": s["version"], "raw_label": "Cassava", "product_id": setup["p"]["CAS"],
            "unit_code": "BOX", "declared_qty": "7", "unit_cost": "19.00",
        },
        headers=idem(),
    )  # fmt: skip
    assert r.status_code == 201, r.text
    r = act(wh, r.json(), "stock")
    assert r.status_code == 200, r.text
    done = r.json()
    assert (done["status"], done["stage"], done["total"]) == ("POSTED", "IN_STOCK", "133.00")
    assert saleable(world.company_id, setup["p"]["CAS"]) == 7
    assert act(wh, done, "stock").status_code == 409


def test_the_database_keeps_posted_purchases_and_corrections_final(owner, world, setup, vendor):
    p = purchase(owner, world, vendor, [line(setup, "CAS", "BOX", "4", "20.00")]).json()
    q = quick(owner, world, setup).json()
    ln = p["counted"][0]["id"]
    c = act(
        owner, p, "corrections", {"line_id": ln, "kind": "SHORT", "qty": "1", "reason": "One short"}
    ).json()["corrections"][0]
    refused = [
        ("update shipments set grn_no = 'GRN-X' where id = :p", "final"),
        ("update shipments set status = 'OPEN', grn_no = null, posted_at = null where id = :p",
         "final"),
        ("update shipments set vendor_invoice_no = 'CHANGED' where id = :p", "final"),
        ("update shipments set is_airport = true where id = :p", "final"),
        ("update shipments set entry = 'PURCHASE' where id = :q", "immutable"),
        ("update shipments set needs_details = true where id = :p", "final"),
        ("delete from shipments where id = :p", "cannot be deleted"),
        ("update purchase_corrections set qty = 3 where id = :c", "rewritten"),
        ("update purchase_corrections set status = 'PENDING', posted_qty = null where id = :c",
         "final"),
        ("delete from purchase_corrections where id = :c", "cannot be deleted"),
        ("update stock_movements set delta_qty = 1 where source_id = :p", "append-only"),
    ]  # fmt: skip
    for sql, why in refused:
        with get_sessionmaker()() as db, pytest.raises(DBAPIError) as e:
            db.execute(text(sql), {"p": p["id"], "q": q["id"], "c": c["id"]})
        assert why in str(e.value).lower(), (sql, str(e.value))
    # what follow-up work needs is allowed: a quick receive's supplier, then nothing more
    with get_sessionmaker()() as db, db.begin():
        db.execute(
            text("update shipments set vendor_id = :v, needs_details = false where id = :q"),
            {"v": vendor, "q": q["id"]},
        )
    with get_sessionmaker()() as db, pytest.raises(DBAPIError):
        db.execute(text("update shipments set vendor_id = null where id = :q"), {"q": q["id"]})
