# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Fast entry grid (Create invoice › Direct, New sales order), synthetic data only:
- GET /product-lookup lists active products with case sizes and aliases in one call;
- POST /sales-orders/preview prices each row as confirming would (box with no box price = per
  lb x box weight), weighs it, lists saleable batches oldest expiry first, reports per-row
  problems instead of failing, and writes nothing;
- a direct invoice line may name the batch handed over; stock then leaves that batch only."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .conftest import idem
from .test_box_pricing import lb_prices, product
from .test_direct_invoice import body, counts
from .test_invoicing import on_hand, sales, wh  # noqa: F401
from .test_orders import P, code, owner, setup, user  # noqa: F401


def lot(api, world, s, sku, unit, qty, lot_code, days):
    r = api.post(
        f"{P}/inventory/opening-balances",
        json={
            "warehouse_id": str(world.wh_a),
            "product_id": s["p"][sku],
            "lot_code": lot_code,
            "expires_at": (datetime.now(UTC) + timedelta(days=days)).isoformat(),
            "lines": [{"unit_code": unit, "qty": qty}],
        },
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    return r.json()["lot_id"]


def grid(world, s, rows, cust=True):
    return {
        "customer_id": s["cust"] if cust else None,
        "warehouse_id": str(world.wh_a),
        "lines": [
            {"product_id": s["p"][sku], "unit_code": u, "qty": q, **({"lot_id": x} if x else {})}
            for sku, u, q, x in rows
        ],
    }


def test_product_lookup_has_case_sizes_and_aliases(owner, setup):
    r = owner.post(
        f"{P}/products/{setup['p']['CAS']}/aliases", json={"alias": "yuca box"}, headers=idem()
    )
    assert r.status_code == 201, r.text
    r = owner.get(f"{P}/product-lookup")
    assert r.status_code == 200, r.text
    cas = next(x for x in r.json() if x["sku"] == "CAS")
    assert [a["alias"] for a in cas["aliases"]] == ["yuca box"]
    assert [(u["unit_code"], Decimal(u["weight_lb_per_unit"])) for u in cas["unit_profiles"]] == [
        ("BOX", Decimal(40))
    ]
    assert all(x["active"] for x in r.json())


def test_line_preview_prices_weighs_and_lists_batches(owner, user, world, setup):
    late = lot(owner, world, setup, "CAS", "BOX", "5", "CAS-LATE", 90)
    soon = lot(owner, world, setup, "CAS", "BOX", "3", "CAS-SOON", 10)
    before = counts(world.company_id)
    rows = [
        ("CAS", "BOX", "4", None),
        ("PLN", "PIECE", "20", None),  # priced, but nothing in stock
        ("NOP", "BOX", "1", None),  # no price per box or per lb
        ("CAS", "BOX", "1", None),  # same product + unit twice
        ("CAS", "BOX", "1.5", None),  # boxes are whole
    ]
    r = owner.post(f"{P}/sales-orders/preview", json=grid(world, setup, rows))
    assert r.status_code == 200, r.text
    pv = r.json()
    cas, pln, nop, dup, half = pv["lines"]
    # Island Grocers' own box price; 4 x 40 lb boxes
    assert (cas["rate"], cas["line_amount"], cas["basis"]) == ("26.5000", "106.00", "PRICE_BOOK")
    assert Decimal(cas["weight_lb"]) == 160 and Decimal(cas["pack_lb"]) == 40
    assert [x["lot_code"] for x in cas["lots"]] == ["CAS-SOON", "CAS-LATE"]  # oldest expiry first
    assert [x["lot_id"] for x in cas["lots"]] == [soon, late]
    assert (Decimal(cas["available_qty"]), cas["stock_ok"], cas["problem"]) == (8, True, None)
    assert (pln["line_amount"], pln["stock_ok"], pln["lots"]) == ("9.00", False, [])
    assert nop["problem"]["code"] == "NO_PRICE" and nop["rate"] is None
    assert dup["problem"]["code"] == "DUPLICATE_LINE" and "line 1" in dup["problem"]["message"]
    assert half["problem"]["code"] == "UNIT_NOT_DIVISIBLE"
    assert (pv["subtotal"], pv["tax_amount"], pv["total"]) == ("115.00", "0.00", "115.00")
    assert (pv["tax_code"], pv["complete"]) == ("UNASSESSED", False)
    assert counts(world.company_id) == before  # nothing written

    # No customer yet: stock and weights, no prices
    r = owner.post(f"{P}/sales-orders/preview", json=grid(world, setup, rows[:1], cust=False))
    one = r.json()["lines"][0]
    assert (one["rate"], one["stock_ok"], len(one["lots"])) == (None, True, 2)
    assert r.json()["complete"] is False

    # A chosen batch counts only that batch
    r = owner.post(
        f"{P}/sales-orders/preview", json=grid(world, setup, [("CAS", "BOX", "4", soon)])
    )
    one = r.json()["lines"][0]
    assert (Decimal(one["available_qty"]), one["stock_ok"]) == (3, False)
    pln_lot = lot(owner, world, setup, "PLN", "PIECE", "9", "PLN-1", 30)
    r = owner.post(
        f"{P}/sales-orders/preview", json=grid(world, setup, [("CAS", "BOX", "1", pln_lot)])
    )
    assert r.json()["lines"][0]["problem"]["code"] == "INVALID_LOT"

    # Sales may preview (orders / invoice.prepare); the warehouse role may not
    ok = grid(world, setup, rows[:1])
    assert user("sales").post(f"{P}/sales-orders/preview", json=ok).status_code == 200
    assert user("wh_a").post(f"{P}/sales-orders/preview", json=ok).status_code == 403


def test_line_preview_box_without_box_price_is_per_lb_times_box(owner, world, setup):
    pid = product(owner, "BXL", "BOX", [("CASE25", "BOX", "25", True)])
    lb_prices(owner, setup, {pid: "1.20"})
    b = {
        "customer_id": setup["cust"],
        "warehouse_id": str(world.wh_a),
        "lines": [{"product_id": pid, "unit_code": "BOX", "qty": "2"}],
    }
    ln = owner.post(f"{P}/sales-orders/preview", json=b).json()["lines"][0]
    assert (ln["rate"], ln["line_amount"], ln["basis"]) == ("30.0000", "60.00", "PER_LB_X_BOX")
    assert Decimal(ln["weight_lb"]) == 50


def test_direct_invoice_takes_the_chosen_batch(owner, world, setup):
    late = lot(owner, world, setup, "CAS", "BOX", "5", "CAS-LATE", 90)
    soon = lot(owner, world, setup, "CAS", "BOX", "3", "CAS-SOON", 10)
    # Default: oldest expiry first, spilling into the next batch
    r = owner.post(f"{P}/invoices/direct/preview", json=body(world, setup, [("CAS", "BOX", "4")]))
    assert r.status_code == 200, r.text
    assert r.json()["stock"][0]["lots"] == ["CAS-SOON", "CAS-LATE"]

    b = body(world, setup, [("CAS", "BOX", "4")])
    b["lines"][0]["lot_id"] = late
    pv = owner.post(f"{P}/invoices/direct/preview", json=b).json()
    assert (pv["stock"][0]["lots"], pv["stock_ok"]) == (["CAS-LATE"], True)
    r = owner.post(f"{P}/invoices/direct", json={**b, "dispatch_confirmed": True}, headers=idem())
    assert r.status_code == 201, r.text
    assert Decimal(on_hand(owner, soon, "BOX")) == 3  # untouched
    assert Decimal(on_hand(owner, late, "BOX")) == 1

    # Too few in the chosen batch is refused even though another batch has stock
    b["lines"][0]["lot_id"] = soon
    b["lines"][0]["qty"] = "4"
    r = owner.post(f"{P}/invoices/direct", json={**b, "dispatch_confirmed": True}, headers=idem())
    assert r.status_code == 409 and code(r) == "INSUFFICIENT_STOCK"
    # Another product's batch is refused
    pln_lot = lot(owner, world, setup, "PLN", "PIECE", "9", "PLN-1", 30)
    b["lines"][0]["lot_id"] = pln_lot
    r = owner.post(f"{P}/invoices/direct/preview", json=b)
    assert r.status_code == 422 and code(r) == "INVALID_LOT"
