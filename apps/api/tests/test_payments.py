# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Module 10 payments: reported != confirmed, verify + apply, duplicate confirmation, append-only
allocations with reversals, concurrency/retries, permissions, aging and due reminders.

Synthetic data only. Invoice of CAD 1,250 = 10 BOX x 125.00 (customer price, no tax rules)."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading import outbox
from pro_trading import payments as svc
from pro_trading.config import Settings
from pro_trading.db import get_sessionmaker

from .conftest import Api, build_world, idem
from .test_invoicing import issue, sales, stock, wh  # noqa: F401 - fixtures re-used
from .test_orders import CSV, P, code, confirmed, owner, picked, setup, user  # noqa: F401

TODAY = datetime.now(UTC).date().isoformat()


@pytest.fixture
def finance(user):
    return user("finance")


@pytest.fixture
def price125(owner, setup):
    csv2 = CSV.replace("BOX,26.50,CAD,C-100", "BOX,125.00,CAD,C-100")
    r = owner.post(
        f"{P}/price-books/versions/import",
        json={"version_code": "PB-2", "csv": csv2},
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
    return setup


def invoice(owner, sales, wh, world, s, qty="10", cust=None):
    stock(owner, world, s, "CAS-01", "BOX", qty)
    o = confirmed(sales, world, s, [("CAS-01", "BOX", qty)], cust=cust)
    pk = picked(sales, wh, o, [(0, qty, None)], verifier=owner)
    r = issue(owner, pk)
    assert r.status_code == 201, r.text
    return r.json()


def report(api, s, amount, inv=None, key=None, cust=None, **extra):
    body = {
        "customer_id": cust or s["cust"],
        "method": "INTERAC",
        "amount": amount,
        "received_on": TODAY,
        "payer_ref": "e-Transfer from Island Grocers",
        "invoice_id": inv["id"] if inv else None,
        **extra,
    }
    return api.post(f"{P}/payments", json=body, headers=key or idem())


def verify(api, pay, ref, allocations=(), key=None):
    body = {
        "version": pay["version"],
        "verification_ref": ref,
        "allocations": [{"invoice_id": i["id"], "amount": a} for i, a in allocations],
    }
    return api.post(f"{P}/payments/{pay['id']}/verify", json=body, headers=key or idem())


def allocate(api, pay, allocations, key=None, version=None):
    body = {
        "version": version or pay["version"],
        "allocations": [{"invoice_id": i["id"], "amount": a} for i, a in allocations],
    }
    return api.post(f"{P}/payments/{pay['id']}/allocations", json=body, headers=key or idem())


def get_inv(api, inv):
    r = api.get(f"{P}/invoices", params={"q": inv["invoice_no"]})
    assert r.status_code == 200, r.text
    return next(i for i in r.json()["items"] if i["id"] == inv["id"])


def reported(api, s, amount, inv=None, **extra):
    r = report(api, s, amount, inv, **extra)
    assert r.status_code == 201, r.text
    return r.json()


def ref() -> str:
    return f"SCOTIA-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------- acceptance


def test_pay001_700_on_1250_leaves_550(owner, sales, wh, finance, world, price125):
    inv = invoice(owner, sales, wh, world, price125)
    assert inv["total"] == "1250.00" and inv["balance"] == "1250.00"

    pay = reported(sales, price125, "700.00", inv)  # customer says sent
    assert pay["status"] == "REPORTED" and pay["payment_no"].startswith("PAY-")
    assert pay["reported_invoice_no"] == inv["invoice_no"]
    i = get_inv(owner, inv)  # sent != confirmed: nothing applied yet
    assert i["state"] == "ISSUED" and i["balance"] == "1250.00"

    r = verify(finance, pay, ref(), [(inv, "700.00")])
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["status"] == "APPLIED" and v["verified_by_name"] == "finance"
    assert v["allocations"][0]["amount"] == "700.00"
    assert v["allocations"][0]["invoice_balance"] == "550.00"
    i = get_inv(owner, inv)
    assert i["state"] == "PARTIALLY_PAID" and i["paid_amount"] == "700.00"
    assert i["balance"] == "550.00"

    # rest arrives as a cheque, verified first and applied in a second step
    pay2 = reported(sales, price125, "550.00", inv, method="CHEQUE")
    r = verify(finance, pay2, "CHQ-000451")
    assert r.status_code == 200 and r.json()["status"] == "VERIFIED", r.text
    r = allocate(finance, r.json(), [(inv, "550.00")])
    assert r.status_code == 200 and r.json()["status"] == "APPLIED", r.text
    i = get_inv(owner, inv)
    assert i["state"] == "PAID" and i["balance"] == "0.00"
    assert (
        get_inv(owner, inv)
        in finance.get(f"{P}/invoices", params={"open": "false"}).json()["items"]
    )

    with get_sessionmaker()() as db:
        acts = db.execute(
            text(
                "select action from audit_events where company_id=:c and action like 'payment.%' "
                "order by created_at"
            ),
            {"c": world.company_id},
        ).scalars()
        assert list(acts) == [
            "payment.record",
            "payment.verify",
            "payment.record",
            "payment.verify",
            "payment.allocate",
        ]


def test_duplicate_confirmation_blocked(owner, sales, wh, finance, world, price125):
    inv = invoice(owner, sales, wh, world, price125)
    pay = reported(sales, price125, "700.00", inv)
    bank = ref()
    key = idem()
    first = verify(finance, pay, bank, [(inv, "700.00")], key=key)
    assert first.status_code == 200, first.text

    again = verify(finance, pay, bank, [(inv, "700.00")], key=key)  # network retry
    assert again.status_code == 200 and again.headers.get("Idempotent-Replay") == "true"
    assert again.json() == first.json()

    twice = verify(finance, pay, bank, [(inv, "700.00")])  # second click, new key
    assert twice.status_code == 409 and code(twice) == "ALREADY_VERIFIED"

    other = reported(sales, price125, "700.00", inv)  # same e-Transfer reported twice
    dup = verify(finance, other, bank.lower())
    assert dup.status_code == 409 and code(dup) == "DUPLICATE_CONFIRMATION"
    assert dup.json()["error"]["payment_no"] == pay["payment_no"]

    assert get_inv(owner, inv)["paid_amount"] == "700.00"  # applied once


def test_concurrent_retries_and_competing_allocations(owner, sales, wh, finance, world, price125):
    inv = invoice(owner, sales, wh, world, price125)
    a = verify(finance, reported(sales, price125, "1000.00"), ref()).json()
    b = verify(finance, reported(sales, price125, "1000.00"), ref()).json()

    key = idem()
    with ThreadPoolExecutor(8) as ex:
        rs = list(ex.map(lambda _: allocate(finance, a, [(inv, "1000.00")], key=key), range(8)))
    assert all(r.status_code in (200, 409) for r in rs), [r.status_code for r in rs]
    assert any(r.status_code == 200 for r in rs)
    for r in rs:
        if r.status_code == 409:
            assert code(r) == "IDEMPOTENCY_IN_PROGRESS"
    assert get_inv(owner, inv)["paid_amount"] == "1000.00"

    # second payment races for the same invoice: only 250 is open
    r = allocate(finance, b, [(inv, "1000.00")])
    assert r.status_code == 409 and code(r) == "EXCEEDS_INVOICE_BALANCE"
    assert r.json()["error"]["balance"] == "250.00"
    c = verify(finance, reported(sales, price125, "300.00"), ref()).json()
    d = verify(finance, reported(sales, price125, "300.00"), ref()).json()
    with ThreadPoolExecutor(2) as ex:
        rs = list(ex.map(lambda pay: allocate(finance, pay, [(inv, "250.00")]), [c, d]))
    assert sorted(r.status_code for r in rs) == [200, 409], [r.text for r in rs]
    lost = next(r for r in rs if r.status_code == 409)
    assert code(lost) in ("EXCEEDS_INVOICE_BALANCE", "INVOICE_NOT_OPEN")
    i = get_inv(owner, inv)
    assert i["paid_amount"] == "1250.00" and i["state"] == "PAID"
    with get_sessionmaker()() as db:
        s = db.execute(
            text("select sum(amount) from payment_allocations where invoice_id=:i"),
            {"i": inv["id"]},
        ).scalar_one()
        assert s == Decimal("1250.00")


def test_allocation_rules(owner, sales, wh, finance, world, price125):
    inv = invoice(owner, sales, wh, world, price125)
    walk = invoice(owner, sales, wh, world, price125, qty="1", cust=price125["walkin"])
    pay = reported(sales, price125, "100.00")
    r = allocate(finance, pay, [(inv, "50.00")])
    assert r.status_code == 409 and code(r) == "NOT_VERIFIED"
    pay = verify(finance, pay, ref()).json()
    r = allocate(finance, pay, [(inv, "150.00")])
    assert r.status_code == 409 and code(r) == "EXCEEDS_PAYMENT"
    r = allocate(finance, pay, [(walk, "10.00")])
    assert r.status_code == 422 and code(r) == "INVOICE_CUSTOMER_MISMATCH"
    r = allocate(finance, pay, [(inv, "10.00"), (inv, "10.00")])
    assert r.status_code == 422 and code(r) == "DUPLICATE_INVOICE"
    r = allocate(finance, pay, [(inv, "10.00")], version=pay["version"] + 5)
    assert r.status_code == 409 and code(r) == "VERSION_CONFLICT"
    r = allocate(finance, pay, [(inv, "0.001")])
    assert r.status_code == 422
    r = allocate(finance, pay, [(inv, "40.00")])
    assert r.status_code == 200 and r.json()["unallocated_amount"] == "60.00", r.text
    assert r.json()["status"] == "VERIFIED"

    r = report(sales, price125, "10.00", inv=walk)
    assert r.status_code == 422 and code(r) == "INVOICE_CUSTOMER_MISMATCH"
    future = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
    r = report(sales, price125, "10.00", received_on=future)
    assert r.status_code == 422 and code(r) == "FUTURE_DATE"
    r = report(sales, price125, "-5.00")
    assert r.status_code == 422


def test_reverse_is_append_only(owner, sales, wh, finance, world, price125):
    inv = invoice(owner, sales, wh, world, price125)
    pay = verify(finance, reported(sales, price125, "700.00"), ref(), [(inv, "700.00")]).json()
    alloc = pay["allocations"][0]
    url = f"{P}/payments/{pay['id']}/allocations/{alloc['id']}/reverse"
    r = finance.post(url, json={"version": pay["version"], "reason": "x"}, headers=idem())
    assert r.status_code == 422  # reason is required
    r = finance.post(
        url, json={"version": pay["version"], "reason": "Applied to wrong invoice"}, headers=idem()
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "VERIFIED" and out["allocated_amount"] == "0.00"
    assert [a["amount"] for a in out["allocations"]] == ["700.00", "-700.00"]
    assert out["allocations"][0]["reversed"] is True
    i = get_inv(owner, inv)
    assert i["state"] == "ISSUED" and i["balance"] == "1250.00"
    r = finance.post(
        url, json={"version": out["version"], "reason": "Applied to wrong invoice"}, headers=idem()
    )
    assert r.status_code == 409 and code(r) == "ALREADY_REVERSED"
    rev = out["allocations"][1]["id"]
    r = finance.post(
        f"{P}/payments/{pay['id']}/allocations/{rev}/reverse",
        json={"version": out["version"], "reason": "Undo the undo"},
        headers=idem(),
    )
    assert r.status_code == 409 and code(r) == "NOT_REVERSIBLE"

    for sql in (
        "update payment_allocations set amount = 1 where id = :a",
        "delete from payment_allocations where id = :a",
        "update payments set amount = 1 where id = :p",
        "update payments set verification_ref = 'X' where id = :p",
        "delete from payments where id = :p",
    ):
        with get_sessionmaker()() as db, pytest.raises(DBAPIError):
            db.execute(text(sql), {"a": alloc["id"], "p": pay["id"]})
            db.commit()


def test_reject_reported_only(owner, sales, wh, finance, world, price125):
    pay = reported(sales, price125, "80.00")
    body = {"version": pay["version"], "reason": "No matching deposit at the bank"}
    r = finance.post(f"{P}/payments/{pay['id']}/reject", json=body, headers=idem())
    assert r.status_code == 200 and r.json()["status"] == "REJECTED", r.text
    r = verify(finance, r.json(), ref())
    assert r.status_code == 409 and code(r) == "INVALID_STATE"
    ok = verify(finance, reported(sales, price125, "80.00"), ref()).json()
    body = {"version": ok["version"], "reason": "Changed my mind"}
    r = finance.post(f"{P}/payments/{ok['id']}/reject", json=body, headers=idem())
    assert r.status_code == 409 and code(r) == "INVALID_STATE"


def test_permissions_and_tenancy(owner, user, sales, wh, finance, world, price125):
    pay = reported(sales, price125, "50.00")
    # warehouse role: no payment access at all
    for r in (
        wh.get(f"{P}/payments"),
        wh.get(f"{P}/receivables"),
        report(wh, price125, "10.00"),
    ):
        assert r.status_code == 403, r.text
    # sales may record and read, but never confirm money
    assert sales.get(f"{P}/payments/{pay['id']}").status_code == 200
    r = verify(sales, pay, ref())
    assert r.status_code == 403 and code(r) == "FORBIDDEN"
    manager = user("manager_a")
    r = verify(manager, pay, ref())
    assert r.status_code == 403
    # finance holds payment.verify through a privileged role: MFA first
    raw = Api(sales.c.app)
    raw.login(world.emails["finance"])
    r = verify(raw, pay, ref())
    assert r.status_code == 403 and code(r) == "MFA_REQUIRED"
    assert verify(finance, pay, ref()).status_code == 200

    other = build_world()
    outsider = Api(sales.c.app)
    outsider.login(other.emails["owner"])
    assert outsider.enroll_and_verify().status_code == 200
    assert outsider.get(f"{P}/payments/{pay['id']}").status_code == 404
    assert outsider.get(f"{P}/payments").json()["total"] == 0
    r = report(outsider, price125, "10.00")
    assert r.status_code == 422 and code(r) == "INVALID_CUSTOMER"

    with get_sessionmaker()() as db:
        denied = db.execute(
            text(
                "select count(*) from audit_events where company_id=:c "
                "and action='permission.denied' and detail_redacted->>'permission' like 'payment.%'"
            ),
            {"c": world.company_id},
        ).scalar_one()
        assert denied >= 3


def test_no_bank_login_or_api_secret():
    names = " ".join(Settings.model_fields).lower()
    for word in ("bank", "scotia", "plaid", "flinks"):
        assert word not in names


def test_receivables_aging(owner, sales, wh, finance, world, price125, monkeypatch):
    inv = invoice(owner, sales, wh, world, price125)  # payment terms 0: due today
    verify(finance, reported(sales, price125, "700.00"), ref(), [(inv, "700.00")])
    verify(finance, reported(sales, price125, "200.00"), ref())  # confirmed, not applied
    reported(sales, price125, "300.00")  # customer says sent, unconfirmed

    r = finance.get(f"{P}/receivables")
    assert r.status_code == 200, r.text
    ag = r.json()
    assert ag["current"] == "550.00" and ag["total"] == "550.00"
    row = next(c for c in ag["customers"] if c["customer_id"] == price125["cust"])
    assert row["unapplied_credit"] == "200.00" and row["reported_pending"] == "300.00"
    assert row["open_invoices"] == 1 and ag["reminder_offsets"] == [-3, 1, 14]

    later = datetime.now(UTC) + timedelta(days=45)
    monkeypatch.setattr(svc, "now", lambda: later)
    ag = finance.get(f"{P}/receivables").json()
    assert ag["current"] == "0.00" and ag["d31_60"] == "550.00"
    assert [svc._bucket(d) for d in (0, 1, 30, 31, 60, 61, 90, 91)] == [
        "current",
        "d1_30",
        "d1_30",
        "d31_60",
        "d31_60",
        "d61_90",
        "d61_90",
        "d90_plus",
    ]


def _reminders(inv_id):
    with get_sessionmaker()() as db:
        return dict(
            db.execute(
                text("select offset_days, status from payment_reminders where invoice_id=:i"),
                {"i": inv_id},
            ).all()
        )


def _dispatch():
    with get_sessionmaker()() as db:
        outbox.dispatch_pending(db, {svc.REMINDER_EVENT: svc.handle_reminder}, limit=500)


def test_due_reminders_once(owner, sales, wh, finance, world, price125):
    with get_sessionmaker()() as db, db.begin():
        db.execute(
            text("update customers set email='ap@island.example.test' where id=:c"),
            {"c": price125["cust"]},
        )
    inv = invoice(owner, sales, wh, world, price125)
    paid = invoice(owner, sales, wh, world, price125, qty="1")
    verify(finance, reported(sales, price125, "125.00"), ref(), [(paid, "125.00")])
    due = datetime.fromisoformat(inv["due_at"])

    with get_sessionmaker()() as db:
        svc.schedule_reminders(db, due - timedelta(days=5))
    assert _reminders(inv["id"]) == {}
    with get_sessionmaker()() as db:
        svc.schedule_reminders(db, due + timedelta(days=2))
        svc.schedule_reminders(db, due + timedelta(days=2, hours=1))  # hourly rerun
    assert _reminders(inv["id"]) == {-3: "SKIPPED", 1: "QUEUED"}
    assert _reminders(paid["id"]) == {}
    _dispatch()
    _dispatch()
    assert _reminders(inv["id"]) == {-3: "SKIPPED", 1: "SENT"}
    with get_sessionmaker()() as db:
        mails = db.execute(
            text(
                "select to_addr, body from email_messages where invoice_id=:i and dedupe_key "
                "like 'payment-reminder:%'"
            ),
            {"i": inv["id"]},
        ).all()
    assert len(mails) == 1 and mails[0][0] == "ap@island.example.test"
    assert "Open balance: CAD 1250.00" in mails[0][1] and "D-16" in mails[0][1]

    # paid in full before the 14-day reminder is delivered -> skipped, no email
    with get_sessionmaker()() as db:
        svc.schedule_reminders(db, due + timedelta(days=20))
    verify(finance, reported(sales, price125, "1250.00"), ref(), [(inv, "1250.00")])
    _dispatch()
    assert _reminders(inv["id"]) == {-3: "SKIPPED", 1: "SENT", 14: "SKIPPED"}
