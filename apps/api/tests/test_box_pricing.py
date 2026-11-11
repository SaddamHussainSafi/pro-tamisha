# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Boxes with no box price (owner, 2026-11-10): on confirm, a BOX line is priced per lb x the box
weight (the size picked on the online order form, else the product's default case size). An
explicit box price wins unless the line was weighed (tests/test_order_weights.py); with no
per-lb price the order refuses with NO_PRICE, with no known weight with WEIGHT_REQUIRED. The invoice prints the box size the price was worked out from. Synthetic data."""

import re
import uuid
from types import SimpleNamespace

from pro_trading import orders
from pro_trading.db import get_sessionmaker
from pro_trading.models import InvoiceDocument, SalesOrderLine

from .conftest import idem
from .test_invoicing import issue, sales, stock, wh  # noqa: F401
from .test_orders import P, code, new_order, owner, picked, setup, user  # noqa: F401


def product(api, sku, stock_unit, profiles):
    body = {
        "sku": sku,
        "name": f"Synthetic {sku}",
        "stock_unit": stock_unit,
        "unit_profiles": [
            {"code": c, "unit_code": u, "weight_lb_per_unit": w, "is_default": d}
            for c, u, w, d in profiles
        ],
    }
    r = api.post(f"{P}/products", json=body, headers=idem())
    assert r.status_code == 201, r.text
    return r.json()["id"]


def lb_prices(api, s, rates):
    """New active price book = the setup book + per-lb prices for `rates` {product_id: rate}."""
    v = api.post(
        f"{P}/price-books/versions",
        json={"version_code": f"PB-LB-{uuid.uuid4().hex[:6]}", "based_on_id": s["pb"]},
        headers=idem(),
    ).json()
    for pid, rate in rates.items():
        r = api.put(
            f"{P}/price-books/versions/{v['id']}/entries",
            json={"product_id": pid, "unit_code": "LB", "rate": rate, "version": v["version"]},
            headers=idem(),
        )
        assert r.status_code == 200, r.text
        v = r.json()
    r = api.post(
        f"{P}/price-books/versions/{v['id']}/activate",
        json={"version": v["version"]},
        headers=idem(),
    )
    assert r.status_code == 200, r.text


def confirm(api, world, s, lines):
    o = new_order(api, world, s, lines)
    assert o.status_code == 201, o.text
    o = o.json()
    return api.post(
        f"{P}/sales-orders/{o['id']}/confirm", json={"version": o["version"]}, headers=idem()
    )


def test_box_without_box_price_is_per_lb_times_case_size(owner, sales, wh, world, setup):
    lbo = product(owner, "LBO", "BOX", [("30LB", "BOX", "30", True), ("40LB", "BOX", "40", False)])
    setup["p"]["LBO"] = lbo
    lb_prices(owner, setup, {lbo: "2.15"})

    r = confirm(sales, world, setup, [("LBO", "BOX", "2"), ("CAS", "BOX", "1")])
    assert r.status_code == 200, r.text
    lines = {ln["product_id"]: ln for ln in r.json()["lines"]}
    box = lines[lbo]
    assert box["rate"] == "64.5000"  # 2.15/lb x 30 lb (the default case size)
    with get_sessionmaker()() as db:
        snap = db.get(SalesOrderLine, uuid.UUID(box["id"])).price_snapshot
    assert (snap["basis"], snap["lb_rate"], snap["box_lb"], snap["unit_code"]) == (
        "PER_LB_X_BOX", "2.1500", "30.0000", "BOX"
    )  # fmt: skip
    assert lines[setup["p"]["CAS"]]["rate"] == "26.5000"  # a box price, when there is one, wins

    # The invoice prints the box size the price came from and bills qty x box price.
    stock(owner, world, setup, "LBO", "BOX", "5")
    stock(owner, world, setup, "CAS", "BOX", "5")
    pk = picked(sales, wh, r.json(), [(0, "2", None), (1, "1", None)], verifier=owner)
    inv = issue(owner, pk)
    assert inv.status_code == 201, inv.text
    row = next(ln for ln in inv.json()["lines"] if ln["sku"] == "LBO")
    assert (row["pack_lb"], row["line_amount"]) == ("30.0000", "129.00")
    with get_sessionmaker()() as db:
        pdf = db.query(InvoiceDocument).filter_by(invoice_id=uuid.UUID(inv.json()["id"])).one()
    words = [m.decode("cp1252") for m in re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", pdf.content)]
    i = words.index("Synthetic LBO")
    assert words[i - 2 : i + 5] == ["2", "CASE-30", "Synthetic LBO", "60.00", "27.22", "64.50",
                                    "129.00"]  # fmt: skip


def test_box_needs_a_per_lb_price_and_a_known_weight(owner, sales, world, setup):
    lbx = product(owner, "LBX", "LB", [("CASE", "BOX", None, True)])  # box with no weight
    setup["p"]["LBX"] = lbx
    lb_prices(owner, setup, {lbx: "3.00"})
    r = confirm(sales, world, setup, [("LBX", "BOX", "1")])
    assert r.status_code == 422 and code(r) == "WEIGHT_REQUIRED"  # 2026-11-11: weigh it
    assert "Enter the weight (LB)" in r.json()["error"]["message"]
    r = confirm(sales, world, setup, [("NOP", "BOX", "1")])  # no box price, no lb price
    assert r.status_code == 422 and code(r) == "NO_PRICE"
    assert "per BOX or per LB" in r.json()["error"]["message"]


def test_online_order_box_size_is_used():
    """The size the customer picked on the online order form beats the default case size."""
    pid, other = uuid.uuid4(), uuid.uuid4()
    items = [
        {"product_id": str(other), "unit_code": "BOX", "box_size_lb": "10"},
        {"product_id": str(pid), "unit_code": "LB", "box_size_lb": None},
        {"product_id": str(pid), "unit_code": "BOX", "box_size_lb": "40"},
    ]
    db = SimpleNamespace(scalar=lambda q: SimpleNamespace(raw={"items": items}))
    o, ln = SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(product_id=pid)
    assert str(orders._ordered_box_lb(db, o, ln)) == "40"  # type: ignore[arg-type]
    none = SimpleNamespace(scalar=lambda q: None)
    assert orders._ordered_box_lb(none, o, ln) is None  # type: ignore[arg-type]
