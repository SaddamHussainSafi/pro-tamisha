# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Supplier ledger and supplier payments: billed = declared value of POSTED shipments, paid =
non-voided payments, balance per currency, returned quantities per unit, append-only payments
(void with reason; DB guard), duplicate bank reference, permissions and tenancy."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading.db import get_sessionmaker

from .conftest import Api, build_world, idem
from .test_receiving import act, code, count, declare, new_shipment, owner, setup, upload, user  # noqa: F401

P = "/api/v1"
TODAY = date.today().isoformat()


@pytest.fixture
def posted(owner, world, setup):
    """V-GH1: 25 BOX cassava at 12.50 (=312.50) + 4 LB yam with no price; 23 + 2 damaged."""
    cas, yam = setup["p"]["CAS"], setup["p"]["YAM"]
    s = new_shipment(owner, world, setup, vendor_invoice_no="ARX-1")
    s = upload(owner, s).json()
    doc = s["documents"][0]["id"]
    s = declare(owner, s, doc, cas, "25", unit_cost="12.5000").json()
    s = declare(owner, s, doc, yam, "4", unit="LB", label="Yellow yam").json()
    s = count(owner, s, cas, "23", damaged="2").json()
    s = count(owner, s, yam, "4", unit="LB").json()
    s = act(owner, s, "costs", {"kind": "FREIGHT", "amount": "90.00", "currency": "USD"}).json()
    s = act(owner, s, "submit-count", {}).json()
    r = act(owner, s, "post", {"acknowledge_discrepancies": True, "discrepancy_note": "2 crushed"})
    assert r.status_code == 200, r.text
    return r.json()


def pay(api, vendor, amount, **kw):
    body = {"vendor_id": vendor, "method": "WIRE", "amount": amount, "paid_on": TODAY, **kw}
    return api.post(f"{P}/supplier-payments", json=body, headers=idem())


def ledger(api, vendor):
    r = api.get(f"{P}/suppliers/{vendor}/ledger")
    assert r.status_code == 200, r.text
    return r.json()


def test_ledger_billed_paid_returned(owner, user, world, setup, posted):
    v = setup["vendor"]
    fin = user("finance")
    led = ledger(fin, v)
    assert led["totals"] == [
        {"currency": "USD", "billed": "312.50", "paid": "0.00", "balance": "312.50"}
    ]
    shp = led["shipments"][0]
    assert (shp["billed"], shp["unpriced_lines"], shp["vendor_invoice_no"]) == (
        "312.50",
        1,
        "ARX-1",
    )
    assert shp["landed_costs"] == [{"kind": "FREIGHT", "amount": "90.00", "currency": "USD"}]
    assert led["returned"] == [
        {"unit_code": "BOX", "declared": "25.0000", "accepted": "23.0000", "damaged": "2.0000",
         "rejected": "0.0000"},
        {"unit_code": "LB", "declared": "4.0000", "accepted": "4.0000", "damaged": "0.0000",
         "rejected": "0.0000"},
    ]  # fmt: skip
    r = pay(fin, v, "200.00", reference="SWIFT-001", shipment_id=posted["id"])
    assert r.status_code == 201, r.text
    p1 = r.json()
    assert p1["payment_no"].startswith("SPAY-") and p1["currency"] == "USD"
    assert p1["shipment_no"] == posted["shipment_no"]
    led = ledger(fin, v)
    assert led["totals"][0] == {"currency": "USD", "billed": "312.50", "paid": "200.00",
                                "balance": "112.50"}  # fmt: skip
    assert [(e["kind"], e["balance"]) for e in led["entries"]] == [
        ("SHIPMENT", "312.50"),
        ("PAYMENT", "112.50"),
    ]
    # same bank reference again: refused; a CAD payment keeps its own currency line
    r = pay(fin, v, "200.00", reference="swift-001")
    assert r.status_code == 409 and code(r) == "DUPLICATE_REFERENCE"
    assert pay(fin, v, "50.00", currency="CAD").status_code == 201
    totals = {t["currency"]: t for t in ledger(fin, v)["totals"]}
    assert totals["CAD"]["balance"] == "-50.00" and totals["USD"]["balance"] == "112.50"
    # void: back on the ledger, struck through, out of the balance
    r = fin.post(f"{P}/supplier-payments/{p1['id']}/void", json={"reason": "wrong supplier"},
                 headers=idem())  # fmt: skip
    assert r.status_code == 200 and r.json()["void_reason"] == "wrong supplier"
    r = fin.post(f"{P}/supplier-payments/{p1['id']}/void", json={"reason": "again"}, headers=idem())
    assert r.status_code == 409 and code(r) == "ALREADY_VOIDED"
    led = ledger(fin, v)
    assert {t["currency"]: t["balance"] for t in led["totals"]}["USD"] == "312.50"
    assert [e["voided"] for e in led["entries"] if e["kind"] == "PAYMENT"].count(True) == 1
    # reference free again after the void
    assert pay(fin, v, "312.50", reference="SWIFT-001").status_code == 201
    bal = next(b for b in fin.get(f"{P}/suppliers/balances").json() if b["vendor_id"] == v)
    assert bal["shipments"] == 1 and {t["currency"]: t["balance"] for t in bal["totals"]} == {
        "CAD": "-50.00",
        "USD": "0.00",
    }


def test_rules_and_db_guard(owner, world, setup, posted):
    v = setup["vendor"]
    other = owner.post(f"{P}/vendors", json={"code": "V-X", "name": "Other"}, headers=idem()).json()
    r = pay(owner, other["id"], "10.00", shipment_id=posted["id"])
    assert r.status_code == 422 and code(r) == "INVALID_SHIPMENT"
    future = (date.today() + timedelta(days=5)).isoformat()
    r = pay(owner, v, "10.00", paid_on=future)
    assert r.status_code == 422 and code(r) == "FUTURE_DATE"
    for bad in ("0", "-5", "1.001"):
        assert pay(owner, v, bad).status_code == 422, bad
    sp = pay(owner, v, "10.00").json()
    with get_sessionmaker()() as db:
        for sql in (
            "update supplier_payments set amount = 1 where id = :i",
            "delete from supplier_payments where id = :i",
        ):
            with pytest.raises(DBAPIError):
                with db.begin():
                    db.execute(text(sql), {"i": sp["id"]})
    assert Decimal(ledger(owner, v)["totals"][0]["paid"]) == Decimal("10.00")


def test_permissions_and_tenancy(owner, user, world, setup, posted):
    v = setup["vendor"]
    for name in ("sales", "wh_a"):
        a = user(name)
        assert a.get(f"{P}/suppliers/{v}/ledger").status_code == 403, name
        assert pay(a, v, "1.00").status_code == 403, name
    # warehouse-scoped manager: ledger needs a company-wide grant
    assert user("manager_a").get(f"{P}/suppliers/balances").status_code == 403
    other = build_world()
    out = Api(owner.c.app)
    out.login(other.emails["owner"])
    assert out.enroll_and_verify().status_code == 200
    assert out.get(f"{P}/suppliers/{v}/ledger").status_code == 404
    assert out.get(f"{P}/suppliers/balances").json() == []
    r = pay(out, v, "1.00")
    assert r.status_code == 404
    with get_sessionmaker()() as db:
        n = db.execute(
            text(
                "select count(*) from audit_events"
                " where company_id=:c and action like 'supplier_payment.%'"
            ),
            {"c": world.company_id},
        ).scalar_one()
    assert n == 0  # nothing recorded in this test
