# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Owner, 2026-11-11: "there is no box price, all products go as per pound". An order line can
carry its weight (the WEIGHT written on the order sheet, or typed on the order); a weighed box is
priced per lb x that weight, and a box with no known weight asks for one instead of for a box
price. The sheet's weight reaches the draft order a scan creates. Synthetic data."""

import json
import uuid
from decimal import Decimal

from sqlalchemy import text

from pro_trading.db import get_sessionmaker
from pro_trading.models import SalesOrderLine
from pro_trading.routes.ocr import sheet_weight

from .conftest import idem
from .test_box_pricing import lb_prices, product
from .test_invoicing import sales, wh  # noqa: F401
from .test_ocr import drain, only, sheet, upload
from .test_orders import P, code, owner, setup, user  # noqa: F401


def order(api, world, s, lines, **extra):
    body = {
        "customer_id": s["cust"],
        "warehouse_id": str(world.wh_a),
        "lines": [
            {"product_id": s["p"][sku], "unit_code": u, "qty": q,
             **({"weight_lb": w} if w else {})}
            for sku, u, q, w in lines
        ],  # fmt: skip
        **extra,
    }
    return api.post(f"{P}/sales-orders", json=body, headers=idem())


def test_weighed_box_is_priced_per_lb_times_its_weight(owner, sales, world, setup):
    lbx = product(owner, "LBX", "LB", [("CASE", "BOX", None, True)])  # no case weight known
    setup["p"]["LBX"] = lbx
    lb_prices(owner, setup, {lbx: "3.00", setup["p"]["CAS"]: "0.50"})
    r = order(sales, world, setup, [("LBX", "BOX", "2", "70"), ("CAS", "BOX", "1", "35")])
    assert r.status_code == 201, r.text
    o = r.json()
    assert [ln["weight_lb"] for ln in o["lines"]] == ["70.0000", "35.0000"]
    assert o["source_snapshot"][0]["weight_lb"] == "70.0000"  # what the sheet said is kept
    c = sales.post(
        f"{P}/sales-orders/{o['id']}/confirm", json={"version": o["version"]}, headers=idem()
    )
    assert c.status_code == 200, c.text
    by = {ln["sku"]: ln for ln in c.json()["lines"]}
    # 3.00/lb x 70 lb over 2 boxes = 105.00 a box; weighed CAS goes per lb too (17.50), not
    # the 26.50 box price
    assert (by["LBX"]["rate"], by["LBX"]["lb_rate"]) == ("105.0000", "3.0000")
    assert (by["CAS"]["rate"], by["CAS"]["lb_rate"]) == ("17.5000", "0.5000")
    with get_sessionmaker()() as db:
        snap = db.get(SalesOrderLine, uuid.UUID(by["LBX"]["id"])).price_snapshot
    assert (snap["basis"], snap["box_lb"]) == ("PER_LB_X_BOX", "35.0000")


def test_box_without_weight_asks_for_it_and_weights_are_for_boxes(owner, sales, world, setup):
    lbx = product(owner, "LBX", "LB", [("CASE", "BOX", None, True)])
    setup["p"]["LBX"] = lbx
    lb_prices(owner, setup, {lbx: "3.00"})
    o = order(sales, world, setup, [("LBX", "BOX", "1", None)]).json()
    c = sales.post(
        f"{P}/sales-orders/{o['id']}/confirm", json={"version": o["version"]}, headers=idem()
    )
    assert c.status_code == 422 and code(c) == "WEIGHT_REQUIRED"
    assert c.json()["error"]["message"] == (
        "Line 1: LBX is sold per lb. Enter the weight (LB) for this line."
    )
    # weigh it on the draft, then it confirms
    body = {
        "version": o["version"],
        "customer_id": o["customer_id"],
        "warehouse_id": o["warehouse_id"],
        "lines": [{"product_id": lbx, "unit_code": "BOX", "qty": "1", "weight_lb": "41.5"}],
    }
    e = sales.put(f"{P}/sales-orders/{o['id']}", json=body, headers=idem())
    assert e.status_code == 200, e.text and e.json()["lines"][0]["weight_lb"] == "41.5000"
    c = sales.post(
        f"{P}/sales-orders/{o['id']}/confirm", json={"version": e.json()["version"]},
        headers=idem(),
    )  # fmt: skip
    assert c.status_code == 200 and c.json()["lines"][0]["rate"] == "124.5000", c.text
    # a weight is only for boxes
    r = order(sales, world, setup, [("PLN", "PIECE", "3", "10")])
    assert r.status_code == 422 and code(r) == "WEIGHT_NOT_ALLOWED"


def test_preview_prices_a_weighed_box_per_lb(owner, sales, world, setup):
    lbx = product(owner, "LBX", "LB", [("CASE", "BOX", None, True)])
    lb_prices(owner, setup, {lbx: "2.00"})
    body = {
        "customer_id": setup["cust"],
        "warehouse_id": str(world.wh_a),
        "lines": [
            {"product_id": lbx, "unit_code": "BOX", "qty": "1", "weight_lb": "35"},
            {"product_id": lbx, "unit_code": "LB", "qty": "4", "weight_lb": "4"},
        ],
    }
    r = sales.post(f"{P}/sales-orders/preview", json=body)
    assert r.status_code == 200, r.text
    box, lb = r.json()["lines"]
    assert (box["weight_lb"], box["lb_rate"], box["rate"], box["line_amount"]) == (
        "35.0000", "2.0000", "70.0000", "70.00")  # fmt: skip
    assert box["problem"] is None and lb["problem"]["code"] == "WEIGHT_NOT_ALLOWED"
    body["lines"] = body["lines"][:1]
    body["lines"][0].pop("weight_lb")
    nw = sales.post(f"{P}/sales-orders/preview", json=body).json()["lines"][0]
    assert nw["problem"]["code"] == "WEIGHT_REQUIRED" and nw["weight_lb"] is None


def test_sheet_weight_reads_plain_pounds_only():
    assert sheet_weight({"weight": "35"}) == Decimal("35")
    assert sheet_weight({"weight": " 41.5 lb "}) == Decimal("41.5")
    for blank in ({}, None, {"weight": None}, {"weight": ""}, {"weight": "?"}, {"weight": "3/4"},
                  {"weight": "0"}, {"weight": "35 kg"}):  # fmt: skip
        assert sheet_weight(blank) is None


def test_scanned_weight_reaches_the_draft_order(user, world, setup):
    sales = user("sales")
    upload(sales, world, sheet(world, {"CAS": "2"}, ref="C-100"))
    drain(world)
    ex = only(sales)
    f = next(x for x in ex["fields"] if x["read_sku"] == "CAS")
    with get_sessionmaker()() as db, db.begin():  # as the handwriting reader records it
        db.execute(
            text("UPDATE ocr_fields SET detail = CAST(:d AS jsonb) WHERE id = :i"),
            {"d": json.dumps({"weight": "70"}), "i": f["id"]},
        )
    r = sales.post(
        f"{P}/ocr/extractions/{ex['id']}/fields/{f['id']}/resolve",
        json={"version": ex["version"], "action": "CONFIRM"},
        headers=idem(),
    )
    assert r.status_code == 200, r.text
    acc = {"version": r.json()["version"], "customer_id": setup["cust"], "reviewed_all": True}
    a = sales.post(f"{P}/ocr/extractions/{ex['id']}/accept", json=acc, headers=idem())
    assert a.status_code == 200, a.text
    so = sales.get(f"{P}/sales-orders/{a.json()['sales_order_id']}").json()
    assert [(ln["sku"], ln["ordered_qty"], ln["weight_lb"]) for ln in so["lines"]] == [
        ("CAS", "2.0000", "70.0000")
    ]
