# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Module 12 reports: totals reconcile to issued invoices and to the stock ledger, filters give
reproducible totals, boxes/pieces are never mixed, report.read + warehouse scoping, tenancy.

Synthetic data only. Island Grocers (C-100) pays 26.50/BOX, Walk-in 28.00/BOX, plantain 0.45/PC,
no tax rules."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from pro_trading.db import get_sessionmaker
from pro_trading.models import Employee, EmployeeRole, Role
from pro_trading.security import hash_password

from .conftest import PASSWORD, Api, build_world, idem
from .test_invoicing import issue, sales, stock, wh  # noqa: F401 - fixtures re-used
from .test_orders import P, code, confirmed, owner, picked, setup, user  # noqa: F401
from .test_receiving import act, count, declare, new_shipment, upload

TODAY = datetime.now(UTC).date()
R = f"{P}/reports"


def sell(owner, sales, wh, world, s, boxes, pieces="0", cust=None):
    stock(owner, world, s, "CAS-01", "BOX", boxes)
    lines = [("CAS-01", "BOX", boxes)]
    rows = [(0, boxes, None)]
    if pieces != "0":
        stock(owner, world, s, "PLN-01", "PIECE", pieces)
        lines.append(("PLN-01", "PIECE", pieces))
        rows.append((1, pieces, None))
    o = confirmed(sales, world, s, lines, cust=cust)
    pk = picked(sales, wh, o, rows, verifier=owner)
    r = issue(owner, pk)
    assert r.status_code == 201, r.text
    return r.json()


def get(api, path, **params):
    r = api.get(f"{R}/{path}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def db_rows(sql, **kw):
    with get_sessionmaker()() as db:
        return db.execute(text(sql), kw).all()


@pytest.fixture
def sold(owner, sales, wh, world, setup):
    a = sell(owner, sales, wh, world, setup, "10", "20")  # 265.00 + 9.00
    b = sell(owner, sales, wh, world, setup, "4", cust=setup["walkin"])  # 112.00
    return a, b


def test_sales_reconcile_to_issued_invoices(owner, world, setup, sold):
    (subtotal, n), *_ = db_rows(
        "select sum(subtotal), count(*) from invoices where company_id=:c "
        "and state in ('ISSUED','PARTIALLY_PAID','PAID')",
        c=world.company_id,
    )
    assert subtotal == Decimal("386.00") and n == 2
    rep = get(owner, "sales")
    assert rep["date_to"] == TODAY.isoformat() and rep["bucket"] == "day"
    assert (rep["net_sales"], rep["tax"], rep["gross"]) == ("386.00", "0.00", "386.00")
    assert (rep["invoice_count"], rep["customer_count"]) == (2, 2)
    # boxes and pieces stay apart
    assert rep["quantities"] == [
        {"unit_code": "BOX", "qty": "14.0000"},
        {"unit_code": "PIECE", "qty": "20.0000"},
    ]
    assert len(rep["series"]) == 30  # every day present, zero-filled
    assert sum(Decimal(p["net_sales"]) for p in rep["series"]) == subtotal
    assert rep["series"][-1] == {
        "period": TODAY.isoformat(),
        "invoice_count": 2,
        "net_sales": "386.00",
    }

    by_c = get(owner, "sales-by-customer")
    assert by_c["total_rows"] == 2 and Decimal(by_c["net_sales"]) == subtotal
    assert sum(Decimal(r["net_sales"]) for r in by_c["rows"]) == subtotal
    top = by_c["rows"][0]
    assert (top["name"], top["net_sales"], top["invoice_count"]) == ("Island Grocers", "274.00", 1)
    assert [q["unit_code"] for q in top["quantities"]] == ["BOX", "PIECE"]

    by_p = get(owner, "sales-by-product")
    assert sum(Decimal(r["net_sales"]) for r in by_p["rows"]) == subtotal
    cas = by_p["rows"][0]
    assert (cas["sku"], cas["unit_code"], cas["qty"], cas["net_sales"]) == (
        "CAS-01",
        "BOX",
        "14.0000",
        "377.00",
    )
    assert cas["avg_rate"] == "26.9286" and cas["customer_count"] == 2
    least = get(owner, "sales-by-product", sort="least", limit=1)
    assert least["total_rows"] == 2 and [r["sku"] for r in least["rows"]] == ["PLN-01"]
    # nothing about cost or margin is exposed (D-04)
    assert not any(k for k in cas if "cost" in k or "margin" in k or "profit" in k)


def test_filters_are_reproducible(owner, world, setup, sold):
    one = get(owner, "sales-by-product", customer_id=setup["walkin"])
    assert get(owner, "sales-by-product", customer_id=setup["walkin"]) == one
    # per-customer rate and volume
    assert [(r["sku"], r["qty"], r["avg_rate"]) for r in one["rows"]] == [
        ("CAS-01", "4.0000", "28.0000")
    ]
    pln = get(owner, "sales", product_id=setup["p"]["PLN-01"])
    assert (pln["net_sales"], pln["invoice_count"]) == ("9.00", 1)
    assert get(owner, "sales", customer_id=setup["cust"])["net_sales"] == "274.00"
    # filtered parts add up to the whole
    parts = [
        get(owner, "sales", customer_id=c)["net_sales"] for c in (setup["cust"], setup["walkin"])
    ]
    assert sum(map(Decimal, parts)) == Decimal(get(owner, "sales")["net_sales"])
    # a period with no invoices
    old = (TODAY - timedelta(days=40)).isoformat()
    empty = get(owner, "sales", date_from=old, date_to=old)
    assert (empty["net_sales"], empty["invoice_count"], empty["quantities"]) == ("0.00", 0, [])
    monthly = get(owner, "sales", bucket="month", date_from=old)
    assert monthly["series"][-1]["net_sales"] == "386.00"
    assert all(p["period"].endswith("-01") for p in monthly["series"])


def test_range_validation(owner, world, setup):
    r = owner.get(f"{R}/sales", params={"date_from": "2026-02-01", "date_to": "2026-01-01"})
    assert r.status_code == 422 and code(r) == "INVALID_RANGE"
    r = owner.get(f"{R}/stock-flow", params={"date_from": "2020-01-01", "date_to": "2026-01-01"})
    assert r.status_code == 422 and code(r) == "RANGE_TOO_LONG"
    r = owner.get(f"{R}/sales-by-product", params={"limit": 501})
    assert r.status_code == 422
    r = owner.get(f"{R}/sales", params={"bucket": "week"})
    assert r.status_code == 422


def test_stock_flow_reconciles_to_ledger_and_balances(owner, world, setup, sold):
    rep = get(owner, "stock-flow")
    rows = {(r["sku"], r["unit_code"]): r for r in rep["rows"]}
    cas = rows[("CAS-01", "BOX")]
    assert (cas["opening"], cas["received"], cas["dispatched"], cas["other"], cas["closing"]) == (
        "0.0000",
        "0.0000",
        "-14.0000",
        "14.0000",
        "0.0000",
    )
    for r in rep["rows"]:
        parts = sum(Decimal(r[k]) for k in ("opening", "received", "dispatched", "other"))
        assert parts == Decimal(r["closing"])
    bal = {
        (sku, u): q
        for sku, u, q in db_rows(
            "select p.sku, b.unit_code, sum(b.qty) from inventory_balances b "
            "join products p on p.id=b.product_id where b.company_id=:c group by 1,2",
            c=world.company_id,
        )
    }
    assert {k: Decimal(v["closing"]) for k, v in rows.items()} == bal
    # a later window: today's movements become the opening balance
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    later = get(owner, "stock-flow", date_from=tomorrow, date_to=tomorrow)
    assert later["rows"] == []  # nothing on hand and nothing moved
    one = get(owner, "stock-flow", product_id=setup["p"]["PLN-01"])
    assert [r["sku"] for r in one["rows"]] == ["PLN-01"]


def test_receiving_by_vendor(owner, user, world, setup):
    v = owner.post(
        f"{P}/vendors",
        json={"code": "V-GH1", "name": "Accra Roots", "country_code": "GH"},
        headers=idem(),
    )
    assert v.status_code == 201, v.text
    cas = setup["p"]["CAS-01"]
    s = new_shipment(owner, world, {"vendor": v.json()["id"]})
    s = upload(owner, s).json()
    s = declare(owner, s, s["documents"][0]["id"], cas, "25").json()
    s = count(owner, s, cas, "23", damaged="2").json()
    s = act(owner, s, "submit-count", {}).json()
    body = {"acknowledge_discrepancies": True, "discrepancy_note": "2 boxes crushed"}
    r = act(owner, s, "post", body)
    assert r.status_code == 200, r.text
    open_ = new_shipment(owner, world, {"vendor": v.json()["id"]})  # not posted: not counted
    assert open_["status"] == "OPEN"

    rep = get(owner, "receiving")
    assert rep["shipments"] == 1
    assert rep["rows"] == [
        {
            "vendor_id": v.json()["id"],
            "code": "V-GH1",
            "name": "Accra Roots",
            "country_code": "GH",
            "unit_code": "BOX",
            "shipments": 1,
            "declared": "25.0000",
            "accepted": "23.0000",
            "damaged": "2.0000",
            "rejected": "0.0000",
        }
    ]
    flow = {r["sku"]: r for r in get(owner, "stock-flow")["rows"]}
    assert flow["CAS-01"]["received"] == "25.0000"  # 23 saleable + 2 into the damaged lot
    other = get(owner, "receiving", vendor_id=str(world.company_id))
    assert other["rows"] == [] and other["shipments"] == 0


def _manager_b(world) -> str:
    """A manager whose grants cover warehouse B only."""
    email = f"manager_b.{world.company_id.hex[:8]}@example.test"
    with get_sessionmaker()() as db, db.begin():
        role = db.query(Role).filter_by(company_id=world.company_id, code="MANAGER").one()
        emp = Employee(
            company_id=world.company_id,
            email=email,
            display_name="manager_b",
            password_hash=hash_password(PASSWORD),
        )
        db.add(emp)
        db.flush()
        db.add(EmployeeRole(employee_id=emp.id, role_id=role.id, warehouse_id=world.wh_b))
    return email


def test_permissions_scope_and_tenancy(owner, user, make_api, world, setup, sold):
    for name in ("wh_a", "sales"):
        a = user(name)
        for path in ("sales", "sales-by-product", "sales-by-customer", "stock-flow", "receiving"):
            r = a.get(f"{R}/{path}")
            assert r.status_code == 403 and code(r) == "FORBIDDEN", (name, path)
    # finance (company-wide) and manager_a (warehouse A) see the sales in A
    assert get(user("finance"), "sales")["net_sales"] == "386.00"
    mgr_a = user("manager_a")
    assert get(mgr_a, "sales")["net_sales"] == "386.00"
    assert get(mgr_a, "sales", warehouse_id=str(world.wh_a))["net_sales"] == "386.00"
    r = mgr_a.get(f"{R}/sales", params={"warehouse_id": str(world.wh_b)})
    assert r.status_code == 403
    # a manager of warehouse B sees nothing from A
    mb = make_api()
    assert mb.login(_manager_b(world)).status_code == 200
    r = mb.get(f"{R}/sales")
    assert r.status_code == 403 and code(r) == "MFA_REQUIRED"
    assert mb.enroll_and_verify().status_code == 200
    rep = get(mb, "sales")
    assert (rep["net_sales"], rep["invoice_count"]) == ("0.00", 0)
    assert get(mb, "stock-flow")["rows"] == []
    assert get(mb, "sales-by-customer")["rows"] == []
    # another company sees none of it, even naming our warehouse
    other = build_world()
    out = Api(owner.c.app)
    out.login(other.emails["owner"])
    assert out.enroll_and_verify().status_code == 200
    assert get(out, "sales", warehouse_id=str(world.wh_a))["net_sales"] == "0.00"
    assert get(out, "sales-by-product", customer_id=setup["cust"])["rows"] == []
    denied = db_rows(
        "select count(*) from audit_events where company_id=:c and action='permission.denied' "
        "and detail_redacted->>'permission' = 'report.read'",
        c=world.company_id,
    )[0][0]
    assert denied >= 10


def test_reports_are_read_only(owner, world, setup, sold):
    before = db_rows(
        "select (select count(*) from audit_events where company_id=:c), "
        "(select count(*) from stock_movements where company_id=:c)",
        c=world.company_id,
    )[0]
    for path in ("sales", "sales-by-product", "sales-by-customer", "stock-flow", "receiving"):
        get(owner, path)
    after = db_rows(
        "select (select count(*) from audit_events where company_id=:c), "
        "(select count(*) from stock_movements where company_id=:c)",
        c=world.company_id,
    )[0]
    assert after == before
