"""Module 05 invoicing: INV-001..006, permissions, idempotency, concurrency, immutability, email.

Synthetic data only. One verified pick -> one invoice; issue posts stock, numbers and stores
the PDF in one transaction.
"""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading import audit, invoicing, outbox
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker

from .conftest import idem
from .test_orders import (  # noqa: F401 - fixtures re-used
    P,
    code,
    confirmed,
    line,
    owner,
    picked,
    setup,
    user,
)

FUTURE = (datetime.now(UTC) + timedelta(days=200)).isoformat()


def stock(api, world, s, sku, unit, qty, wh=None):
    body = {
        "warehouse_id": str(wh or world.wh_a),
        "product_id": s["p"][sku],
        "expires_at": FUTURE,
        "lines": [{"unit_code": unit, "qty": qty}],
    }
    r = api.post(f"{P}/inventory/opening-balances", json=body, headers=idem())
    assert r.status_code == 201, r.text
    return r.json()["lot_id"]


def on_hand(api, lot, unit):
    items = api.get(f"{P}/inventory/stock", params={"include_zero": "true"}).json()["items"]
    return next(b["qty"] for b in items if b["lot_id"] == lot and b["unit_code"] == unit)


def preview(api, pk, weights=None):
    return api.post(
        f"{P}/pick-attempts/{pk['id']}/invoice-preview", json={"weights": weights or {}}
    )


def issue(api, pk, weights=None, key=None, confirmed_=True, version=None):
    body = {
        "version": version or pk["version"],
        "dispatch_confirmed": confirmed_,
        "weights": weights or {},
        "note": "",
    }
    return api.post(f"{P}/pick-attempts/{pk['id']}/issue-invoice", json=body, headers=key or idem())


def movements(invoice_id):
    with get_sessionmaker()() as db:
        return db.execute(
            text(
                "select count(*), coalesce(sum(delta_qty),0) from stock_movements "
                "where source_type='INVOICE' and source_id=:i"
            ),
            {"i": invoice_id},
        ).one()


@pytest.fixture
def sales(user):
    return user("sales")


@pytest.fixture
def wh(user):
    return user("wh_a")


# ---------------------------------------------------------------- acceptance


def test_inv001_inv002_partial_then_backorder_invoice(owner, sales, wh, world, setup):
    lot = stock(owner, world, setup, "CAS", "BOX", "40")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "15")])
    pk = picked(sales, wh, o, [(0, "10", "OUT_OF_STOCK")], verifier=owner)

    pv = preview(sales, pk)  # sales may prepare (preview) but not issue
    assert pv.status_code == 200, pv.text
    p = pv.json()
    assert p["can_issue"] and p["subtotal"] == "265.00" and p["total"] == "265.00"
    assert p["stock"][0]["deduct_qty"] == "10.0000" and p["tax_amount"] == "0.00"
    assert issue(sales, pk).status_code == 403

    r = issue(owner, pk)
    assert r.status_code == 201, r.text
    a = r.json()
    year = datetime.now(UTC).year
    assert a["invoice_no"] == f"TT{year}-000001" and a["state"] == "ISSUED"
    assert a["total"] == "265.00" and a["lines"][0]["billed_qty"] == "10.0000"
    assert on_hand(owner, lot, "BOX") == "30.0000"
    assert movements(a["id"]) == (1, -10)
    order = owner.get(f"{P}/sales-orders/{o['id']}").json()
    cas = line(order, "CAS")
    assert (cas["dispatched_qty"], cas["pending_qty"]) == ("10.0000", "5.0000")
    assert order["status"] == "PARTIALLY_FULFILLED"

    # INV-002: next pick of the remaining 5 -> invoice B, different number, total -15
    o2 = owner.get(f"{P}/sales-orders/{o['id']}").json()
    pk2 = picked(sales, wh, o2, [(0, "5", None)], verifier=owner)
    r = issue(owner, pk2)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["invoice_no"] == f"TT{year}-000002" and b["id"] != a["id"]
    assert on_hand(owner, lot, "BOX") == "25.0000"
    order = owner.get(f"{P}/sales-orders/{o['id']}").json()
    assert order["status"] == "FULFILLED" and line(order, "CAS")["pending_qty"] == "0.0000"
    listed = owner.get(f"{P}/invoices", params={"sales_order_id": o["id"]}).json()
    assert {x["invoice_no"] for x in listed["items"]} == {a["invoice_no"], b["invoice_no"]}
    # a second issue for the same sheet is refused, pointing at the first invoice
    again = issue(owner, pk | {"version": pk["version"] + 1})
    assert again.status_code == 409 and code(again) == "ALREADY_INVOICED"
    assert again.json()["error"]["invoice_id"] == a["id"]


def test_inv003_ten_concurrent_retries_same_key(owner, sales, wh, world, setup):
    lot = stock(owner, world, setup, "CAS", "BOX", "20")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "6")])
    pk = picked(sales, wh, o, [(0, "6", None)], verifier=owner)
    key = idem()
    with ThreadPoolExecutor(10) as ex:
        rs = list(ex.map(lambda _: issue(owner, pk, key=key), range(10)))
    ok = [r for r in rs if r.status_code == 201]
    assert ok and all(r.status_code in (201, 409) for r in rs), [r.status_code for r in rs]
    assert len({r.json()["id"] for r in ok}) == 1
    with get_sessionmaker()() as db:
        n = db.scalar(
            text("select count(*) from invoices where picking_attempt_id=:p"), {"p": pk["id"]}
        )
    assert n == 1
    assert movements(ok[0].json()["id"]) == (1, -6)
    assert on_hand(owner, lot, "BOX") == "14.0000"
    # sequential replay of the same key returns the stored response
    rep = issue(owner, pk, key=key)
    assert rep.status_code == 201 and rep.headers.get("Idempotent-Replay") == "true"
    assert rep.json()["id"] == ok[0].json()["id"]


def test_inv004_two_picks_compete_for_stock(owner, sales, wh, world, setup):
    lot = stock(owner, world, setup, "CAS", "BOX", "8")
    pks = []
    for _ in range(2):
        o = confirmed(sales, world, setup, [("CAS", "BOX", "6")])
        pks.append(picked(sales, wh, o, [(0, "6", None)], verifier=owner))
    with ThreadPoolExecutor(2) as ex:
        rs = list(ex.map(lambda pk: issue(owner, pk), pks))
    codes = sorted(r.status_code for r in rs)
    assert codes == [201, 409], [r.text for r in rs]
    bad = next(r for r in rs if r.status_code == 409)
    assert code(bad) == "INSUFFICIENT_STOCK" and bad.json()["error"]["available"] == "2.0000"
    assert on_hand(owner, lot, "BOX") == "2.0000"
    # preview of the losing sheet shows the shortfall
    loser = pks[[r.status_code for r in rs].index(409)]
    pv = preview(owner, loser).json()
    assert pv["can_issue"] is False and pv["stock"][0]["available_qty"] == "2.0000"
    # the failed attempt consumed no invoice number
    with get_sessionmaker()() as db:
        last = db.scalar(
            text("select last_no from invoice_sequences where company_id=:c"),
            {"c": str(world.company_id)},
        )
    assert last == 1


def test_inv005_price_change_leaves_issued_invoice_identical(owner, sales, wh, world, setup):
    stock(owner, world, setup, "CAS", "BOX", "10")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "4")])
    pk = picked(sales, wh, o, [(0, "4", None)], verifier=owner)
    inv = issue(owner, pk).json()
    pdf1 = owner.get(f"{P}/invoices/{inv['id']}/pdf")
    assert pdf1.status_code == 200 and pdf1.headers["content-type"] == "application/pdf"
    assert pdf1.content.startswith(b"%PDF-")
    assert hashlib.sha256(pdf1.content).hexdigest() == inv["pdf_sha256"]
    assert b"(SALES INVOICE) Tj" in pdf1.content and inv["template_version"] == "sales-invoice-1"

    csv = (
        "sku,product_name,unit,rate,currency,customer_ref,group_code\r\n"
        "CAS,Cassava,BOX,40.00,CAD,,\r\n"
        "CAS,Cassava,LB,2.00,CAD,,\r\n"
        "CAS,Cassava,BOX,39.00,CAD,C-100,\r\n"
        "PLN,Plantain,PIECE,0.60,CAD,,\r\n"
    )
    v = owner.post(
        f"{P}/price-books/versions/import",
        json={"version_code": "PB-2", "csv": csv},
        headers=idem(),
    ).json()
    act = owner.post(
        f"{P}/price-books/versions/{v['id']}/activate",
        json={"version": v["version"]},
        headers=idem(),
    )
    assert act.status_code == 200, act.text

    again = owner.get(f"{P}/invoices/{inv['id']}").json()
    assert again["total"] == inv["total"] == "106.00"
    assert again["lines"] == inv["lines"]
    pdf2 = owner.get(f"{P}/invoices/{inv['id']}/pdf")
    assert pdf2.content == pdf1.content
    etag = pdf2.headers["etag"]
    assert (
        owner.get(f"{P}/invoices/{inv['id']}/pdf", headers={"If-None-Match": etag}).status_code
        == 304
    )
    # DB refuses to rewrite issued content
    with get_sessionmaker()() as db, pytest.raises(DBAPIError):
        db.execute(text("update invoices set total=1, subtotal=1 where id=:i"), {"i": inv["id"]})
    with get_sessionmaker()() as db, pytest.raises(DBAPIError):
        db.execute(
            text("update invoice_lines set unit_rate=1 where invoice_id=:i"), {"i": inv["id"]}
        )
    with get_sessionmaker()() as db, pytest.raises(DBAPIError):
        db.execute(text("delete from invoices where id=:i"), {"i": inv["id"]})
    with get_sessionmaker()() as db, pytest.raises(DBAPIError):
        db.execute(
            text("update invoice_documents set content='x' where invoice_id=:i"), {"i": inv["id"]}
        )


def test_inv006_catch_weight_billing(owner, sales, wh, world, setup):
    lot = stock(owner, world, setup, "CAS", "BOX", "10")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "7")])
    pk = picked(sales, wh, o, [(0, "7", None)], verifier=owner)
    pl = pk["lines"][0]["id"]
    ws = [20, 30, 40, 40, 25, 25, 25]
    weights = {pl: [{"cases": 1, "weight_lb": str(w)} for w in ws]}

    short = preview(owner, pk, {pl: [{"cases": 6, "weight_lb": "30"}]})
    assert short.status_code == 422 and code(short) == "WEIGHTS_MISMATCH"

    pv = preview(owner, pk, weights)
    assert pv.status_code == 200, pv.text
    p = pv.json()
    assert p["weighable_pick_line_ids"] == [pl]
    assert p["subtotal"] == "307.50"
    assert [(x["cases"], x["case_weight_lb"]) for x in p["lines"]] == [
        ("1", "20.0000"),
        ("1", "30.0000"),
        ("2", "40.0000"),
        ("3", "25.0000"),
    ]
    assert all(x["unit_code"] == "LB" and x["unit_rate"] == "1.5000" for x in p["lines"])

    r = issue(owner, pk, weights)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["total"] == "307.50"
    assert sum(float(x["billed_qty"]) for x in inv["lines"]) == 205
    # stock moves in boxes (what was picked), billing is by weight
    assert on_hand(owner, lot, "BOX") == "3.0000"
    assert movements(inv["id"]) == (1, -7)


def test_weights_only_for_box_lines(owner, sales, wh, world, setup):
    stock(owner, world, setup, "PLN", "PIECE", "50")
    o = confirmed(sales, world, setup, [("PLN", "PIECE", "10")])
    pk = picked(sales, wh, o, [(0, "10", None)], verifier=owner)
    pl = pk["lines"][0]["id"]
    r = preview(owner, pk, {pl: [{"cases": 10, "weight_lb": "1"}]})
    assert r.status_code == 422 and code(r) == "WEIGHTS_NOT_ALLOWED"


# ---------------------------------------------------------------- rules


def test_issue_gates(owner, sales, wh, world, setup):
    stock(owner, world, setup, "CAS", "BOX", "10")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "3")])
    pk = picked(sales, wh, o, [(0, "3", None)], verifier=owner)
    r = issue(owner, pk, confirmed_=False)
    assert r.status_code == 422 and code(r) == "DISPATCH_NOT_CONFIRMED"
    r = issue(owner, pk, version=pk["version"] + 5)
    assert r.status_code == 409 and code(r) == "VERSION_CONFLICT"

    # all-NO sheet: nothing to bill
    o2 = confirmed(sales, world, setup, [("CAS", "BOX", "2")])
    nopk = picked(sales, wh, o2, [(0, "0", "NOT_FOUND")], verifier=owner)
    r = issue(owner, nopk)
    assert r.status_code == 422 and code(r) == "NOTHING_TO_INVOICE"

    # not yet verified
    o3 = confirmed(sales, world, setup, [("CAS", "BOX", "1")])
    r = sales.post(
        f"{P}/sales-orders/{o3['id']}/pick-attempts",
        json={"version": o3["version"]},
        headers=idem(),
    )
    open_ = r.json()
    r = issue(owner, open_)
    assert r.status_code == 409 and code(r) == "INVALID_STATE"


def test_tax_unverified_blocks_production(owner, sales, wh, world, setup, monkeypatch):
    stock(owner, world, setup, "CAS", "BOX", "10")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "1")])
    pk = picked(sales, wh, o, [(0, "1", None)], verifier=owner)
    st = get_settings()
    monkeypatch.setattr(type(st), "is_production_like", property(lambda self: True))
    r = issue(owner, pk)
    assert r.status_code == 409 and code(r) == "TAX_RULES_UNVERIFIED"
    monkeypatch.setattr(st, "invoice_tax_verified", True)
    assert issue(owner, pk).status_code == 201


def test_permissions_and_scope(owner, user, sales, wh, world, setup, make_api):
    stock(owner, world, setup, "CAS", "BOX", "10")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "2")])
    pk = picked(sales, wh, o, [(0, "2", None)], verifier=owner)
    assert issue(wh, pk).status_code == 403
    assert preview(wh, pk).status_code == 403
    fin = user("finance")
    assert issue(fin, pk).status_code == 403
    mgr = user("manager_a")
    r = issue(mgr, pk)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["issued_by_name"] == "manager_a"
    assert fin.get(f"{P}/invoices/{inv['id']}").status_code == 200
    assert fin.get(f"{P}/invoices").json()["total"] == 1
    assert wh.get(f"{P}/invoices").status_code == 403
    assert wh.get(f"{P}/invoices/{inv['id']}/pdf").status_code == 403
    # another tenant sees nothing
    from .conftest import build_world

    other = build_world()
    x = make_api()
    x.login(other.emails["owner"])
    assert x.enroll_and_verify().status_code == 200
    assert x.get(f"{P}/invoices/{inv['id']}").status_code == 404
    assert x.get(f"{P}/invoices").json()["total"] == 0


def test_failure_injection_rolls_back_everything(owner, sales, wh, world, setup, monkeypatch):
    lot = stock(owner, world, setup, "CAS", "BOX", "10")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "4")])
    pk = picked(sales, wh, o, [(0, "4", None)], verifier=owner)
    real = audit.record

    def boom(*a, **kw):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(audit, "record", boom)
    key = idem()
    with pytest.raises(RuntimeError):
        issue(owner, pk, key=key)
    monkeypatch.setattr(audit, "record", real)
    assert on_hand(owner, lot, "BOX") == "10.0000"
    assert owner.get(f"{P}/invoices").json()["total"] == 0
    after = owner.get(f"{P}/pick-attempts/{pk['id']}").json()
    assert after["status"] == "VERIFIED"
    r = issue(owner, pk, key=key)  # retry after the outage: number 1 (gapless)
    assert r.status_code == 201, r.text
    assert r.json()["invoice_no"].endswith("-000001")
    assert on_hand(owner, lot, "BOX") == "6.0000"


# ---------------------------------------------------------------- email


def _dispatch():
    with get_sessionmaker()() as db:
        return outbox.dispatch_pending(db, {invoicing.EMAIL_EVENT: invoicing.handle_email})


def test_email_queued_sent_once_and_resend(owner, sales, wh, world, setup):
    c = owner.get(f"{P}/customers/{setup['cust']}").json()
    r = owner.patch(
        f"{P}/customers/{setup['cust']}",
        json={"email": "ap@island.example.test", "version": c["version"]},
        headers=idem(),
    )
    assert r.status_code == 200, r.text
    stock(owner, world, setup, "CAS", "BOX", "10")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "1")])
    pk = picked(sales, wh, o, [(0, "1", None)], verifier=owner)
    inv = issue(owner, pk).json()
    assert inv["email_status"] == "QUEUED" and inv["email_to"] == "ap@island.example.test"
    r = owner.post(f"{P}/invoices/{inv['id']}/email", headers=idem())
    assert r.status_code == 409 and code(r) == "EMAIL_QUEUED"

    _dispatch()
    _dispatch()  # at-least-once: a second pass sends nothing new
    got = owner.get(f"{P}/invoices/{inv['id']}").json()
    assert got["email_status"] == "SENT" and got["email_sent_at"]

    def mails():
        with get_sessionmaker()() as db:
            return db.execute(
                text(
                    "select dedupe_key, attachment_sha256 from email_messages where invoice_id=:i"
                ),
                {"i": inv["id"]},
            ).all()

    assert len(mails()) == 1 and mails()[0][1] == inv["pdf_sha256"]
    # handler re-run for the same event (crash after send) does not duplicate
    with get_sessionmaker()() as db:
        ev = db.execute(
            text("select payload from outbox_events where aggregate_id=:i"), {"i": inv["id"]}
        ).scalar_one()
    with get_sessionmaker()() as db:
        invoicing.deliver(
            db,
            invoicing.EmailMessage(
                company_id=world.company_id,
                dedupe_key=ev["dedupe_key"],
                invoice_id=inv["id"],
                to_addr="x@example.test",
                subject="s",
                body="b",
                attachment_sha256=inv["pdf_sha256"],
            ),
        )
        db.commit()
    assert len(mails()) == 1

    r = owner.post(f"{P}/invoices/{inv['id']}/email", headers=idem())
    assert r.status_code == 200 and r.json()["email_status"] == "QUEUED"
    _dispatch()
    assert len(mails()) == 2
    assert wh.post(f"{P}/invoices/{inv['id']}/email", headers=idem()).status_code == 403


def test_no_email_address(owner, sales, wh, world, setup):
    stock(owner, world, setup, "CAS", "BOX", "10")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "1")])
    pk = picked(sales, wh, o, [(0, "1", None)], verifier=owner)
    inv = issue(owner, pk).json()
    assert inv["email_status"] == "NO_EMAIL"
    r = owner.post(f"{P}/invoices/{inv['id']}/email", headers=idem())
    assert r.status_code == 422 and code(r) == "NO_EMAIL_ADDRESS"
