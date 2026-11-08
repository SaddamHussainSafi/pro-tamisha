# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Direct invoices (no picking sheet): preview changes nothing; issue numbers the invoice,
takes stock first-expiry-first-out, links an order + a "Direct invoice" sheet, is idempotent,
refuses missing stock / price / dispatch confirmation, and needs invoice.issue."""

from decimal import Decimal

from sqlalchemy import text

from pro_trading.db import get_sessionmaker

from .conftest import idem
from .test_invoicing import movements, sales, stock, wh  # noqa: F401
from .test_orders import P, code, owner, setup, user  # noqa: F401


def body(world, s, lines, cust=None, **kw):
    return {
        "customer_id": cust or s["cust"],
        "warehouse_id": str(world.wh_a),
        "lines": [{"product_id": s["p"][sku], "unit_code": u, "qty": q} for sku, u, q in lines],
        **kw,
    }


def counts(company_id):
    with get_sessionmaker()() as db:
        return db.execute(
            text(
                "select (select count(*) from sales_orders where company_id=:c),"
                " (select count(*) from invoices where company_id=:c),"
                " (select count(*) from stock_movements where company_id=:c)"
            ),
            {"c": company_id},
        ).one()


def test_preview_then_issue(owner, world, setup):
    stock(owner, world, setup, "CAS-01", "BOX", "6")
    stock(owner, world, setup, "PLN-01", "PIECE", "50")
    b = body(world, setup, [("CAS-01", "BOX", "4"), ("PLN-01", "PIECE", "20")])
    before = counts(world.company_id)
    r = owner.post(f"{P}/invoices/direct/preview", json=b)
    assert r.status_code == 200, r.text
    pv = r.json()
    # Island Grocers: 26.50/BOX (customer price), plantain 0.45/PC standard
    assert (pv["subtotal"], pv["total"], pv["stock_ok"]) == ("115.00", "115.00", True)
    assert [x["unit_rate"] for x in pv["lines"]] == ["26.5000", "0.4500"]
    assert counts(world.company_id) == before  # preview kept nothing

    r = owner.post(f"{P}/invoices/direct", json={**b, "dispatch_confirmed": False}, headers=idem())
    assert r.status_code == 422 and code(r) == "DISPATCH_NOT_CONFIRMED"
    key = idem()
    r = owner.post(f"{P}/invoices/direct", json={**b, "dispatch_confirmed": True}, headers=key)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["invoice_no"].startswith("INV-") and inv["total"] == "115.00"
    again = owner.post(f"{P}/invoices/direct", json={**b, "dispatch_confirmed": True}, headers=key)
    assert again.status_code == 201 and again.json()["id"] == inv["id"]
    o, i, m = counts(world.company_id)
    assert (o - before[0], i - before[1], m - before[2]) == (1, 1, 2)  # once, two stock lines
    n, total = movements(inv["id"])
    assert (n, Decimal(total)) == (2, Decimal("-24"))  # 4 boxes + 20 pieces left stock
    with get_sessionmaker()() as db:
        status_note, order_text = db.execute(
            text(
                "select a.status_note, o.source_text from invoices i"
                " join picking_attempts a on a.id = i.picking_attempt_id"
                " join sales_orders o on o.id = i.sales_order_id where i.id = :i"
            ),
            {"i": inv["id"]},
        ).one()
    assert "Direct invoice" in status_note and "Direct invoice" in order_text


def test_refusals(owner, user, world, setup):
    stock(owner, world, setup, "CAS-01", "BOX", "2")
    before = counts(world.company_id)
    r = owner.post(
        f"{P}/invoices/direct",
        json={**body(world, setup, [("CAS-01", "BOX", "5")]), "dispatch_confirmed": True},
        headers=idem(),
    )
    assert r.status_code == 409 and code(r) == "INSUFFICIENT_STOCK"
    r = owner.post(
        f"{P}/invoices/direct",
        json={**body(world, setup, [("NOP-01", "BOX", "1")]), "dispatch_confirmed": True},
        headers=idem(),
    )
    assert r.status_code == 422 and code(r) == "NO_PRICE"
    assert counts(world.company_id) == before  # nothing half-written
    # sales may preview (invoice.prepare) but not issue; warehouse may do neither
    sales_api = user("sales")
    b = body(world, setup, [("CAS-01", "BOX", "1")])
    assert sales_api.post(f"{P}/invoices/direct/preview", json=b).status_code == 200
    r = sales_api.post(
        f"{P}/invoices/direct", json={**b, "dispatch_confirmed": True}, headers=idem()
    )
    assert r.status_code == 403
    assert user("wh_a").post(f"{P}/invoices/direct/preview", json=b).status_code == 403
    # another warehouse's or company's id is refused
    r = owner.post(
        f"{P}/invoices/direct/preview", json={**b, "warehouse_id": str(world.company_id)}
    )
    assert r.status_code in (403, 422)
