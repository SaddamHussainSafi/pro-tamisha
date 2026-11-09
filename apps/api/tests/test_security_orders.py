# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Security sweep 2026-11: orders, invoices and the live stock stream."""

import time
from types import SimpleNamespace

from sqlalchemy import func, update

from pro_trading import invoice_pdf
from pro_trading.db import get_sessionmaker
from pro_trading.models import AuthSession
from pro_trading.routes import inventory as inventory_routes

from .conftest import idem
from .test_invoicing import stock
from .test_orders import CSV, P, code, confirmed, owner, setup, user  # noqa: F401


def sku(setup: dict, prefix: str) -> str:
    """Fixture SKU by prefix ("CAS" or the older "CAS-01"), so these tests don't depend on the
    SKU format the catalog session is changing."""
    return next(k for k in setup["p"] if k.startswith(prefix))


# ------------------------------------------------------------------ invoice PDF


def _snapshot(name: str, product: str) -> dict:
    return {
        "invoice_no": "INV-2026-000001",
        "company": {"legal_name": "Tamisha Trading Inc."},
        "customer": {"name": name, "external_ref": "C-1", "payment_terms_days": 14},
        "issued_date": "2026-11-09",
        "due_date": "2026-11-23",
        "order": {"order_no": "SO-1"},
        "pick_code": "PK-1",
        "currency": "CAD",
        "tax_note": "Tax pending",
        "subtotal": "5.00",
        "tax_amount": "0.00",
        "total": "5.00",
        "lines": [
            {
                "line_no": 1,
                "sku": "BEF",
                "description": product,
                "billed_qty": "1",
                "unit_code": "BOX",
                "unit_rate": "5.00",
                "line_amount": "5.00",
            }
        ],
    }


def test_invoice_pdf_renders_windows_1252_punctuation():
    # The font is /WinAnsiEncoding but the stream was encoded as Latin-1, so any ’ “ ” – — … €
    # in a customer or product name made issuing the invoice fail with a 500.
    pdf = invoice_pdf.render(_snapshot("O’Brien “Island” Grocers", "Beef — boneless … €"))
    assert pdf.startswith(b"%PDF")
    assert "O’Brien".encode("cp1252") in pdf and "—".encode("cp1252") in pdf
    assert invoice_pdf.render(_snapshot("Łódź 東京", "x")).startswith(b"%PDF")  # → '?'


# ------------------------------------------------------------------ orders


def test_line_cancel_idempotency_is_scoped_to_the_line(owner, user, world, setup):
    sales = user("sales")
    cas, pln = sku(setup, "CAS"), sku(setup, "PLN")
    o = confirmed(sales, world, setup, [(cas, "BOX", "10"), (pln, "PIECE", "6")])
    a, b = o["lines"][0]["id"], o["lines"][1]["id"]
    key = idem()
    body = {"version": o["version"], "qty": "1", "reason": "customer reduced"}
    r1 = owner.post(f"{P}/sales-orders/{o['id']}/lines/{a}/cancel", json=body, headers=key)
    assert r1.status_code == 200, r1.text
    # Same key and body on another line used to replay line A's stored response (200) and
    # cancel nothing on line B. Now it is a new request (here refused: stale version).
    r2 = owner.post(f"{P}/sales-orders/{o['id']}/lines/{b}/cancel", json=body, headers=key)
    assert r2.headers.get("Idempotent-Replay") != "true"
    assert r2.status_code == 409, r2.text
    lines = {ln["id"]: ln for ln in owner.get(f"{P}/sales-orders/{o['id']}").json()["lines"]}
    assert lines[a]["cancelled_qty"] == "1.0000" and lines[b]["cancelled_qty"] == "0.0000"


def test_zero_total_invoice_is_paid_at_issue(owner, world, setup):
    nop = sku(setup, "NOP")
    csv = CSV + f"{nop},Unpriced,BOX,0.00,CAD,,\r\n"
    r = owner.post(
        f"{P}/price-books/versions/import",
        json={"version_code": "PB-ZERO", "csv": csv},
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
    stock(owner, world, setup, nop, "BOX", "3")
    body = {
        "customer_id": setup["cust"],
        "warehouse_id": str(world.wh_a),
        "lines": [{"product_id": setup["p"][nop], "unit_code": "BOX", "qty": "1"}],
        "dispatch_confirmed": True,
    }
    r = owner.post(f"{P}/invoices/direct", json=body, headers=idem())
    assert r.status_code == 201, r.text
    inv = r.json()
    # it used to stay ISSUED (open) forever: no payment can be allocated to a $0 balance
    assert (inv["total"], inv["state"]) == ("0.00", "PAID")


# ------------------------------------------------------------------ live stock stream


def test_stock_stream_does_not_block_worker_threads(owner, monkeypatch):
    def blocked(_: float) -> None:
        raise AssertionError("time.sleep in the stream holds a worker thread")

    monkeypatch.setattr(
        inventory_routes, "time", SimpleNamespace(monotonic=time.monotonic, sleep=blocked)
    )
    with owner.c.stream("GET", f"{P}/inventory/events", params={"timeout": 2}) as r:
        assert r.status_code == 200
        lines = [ln for ln in r.iter_lines() if ln]
    assert lines[0] == "event: ready" and ": keep-alive" in lines


def test_stock_streams_are_capped_per_employee(owner, monkeypatch):
    monkeypatch.setattr(inventory_routes, "MAX_STREAMS_PER_EMPLOYEE", 0)
    r = owner.get(f"{P}/inventory/events", params={"timeout": 1})
    assert r.status_code == 429 and code(r) == "TOO_MANY_STREAMS"
    assert not inventory_routes._streams  # nothing leaked


def test_stock_stream_ends_when_the_session_is_revoked(owner, world):
    t0 = time.monotonic()
    with owner.c.stream("GET", f"{P}/inventory/events", params={"timeout": 30}) as r:
        it = r.iter_lines()
        assert next(it) == "event: ready"
        with get_sessionmaker()() as db:
            db.execute(
                update(AuthSession)
                .where(AuthSession.employee_id == world.ids["owner"])
                .values(revoked_at=func.now(), revoked_reason="test")
            )
            db.commit()
        for _ in it:
            pass
    assert time.monotonic() - t0 < 10  # ended long before the 30 s timeout
    assert not inventory_routes._streams


# ------------------------------------------------------------------ damaged lots


def test_damage_works_when_the_dmg_code_is_taken(owner, world, setup):
    from .test_inventory import lot_of, opening

    cas = setup["p"][sku(setup, "CAS")]
    lot_of(opening(owner, world, cas, [("BOX", "5")], lot_code="X-DMG"))  # a user's own lot
    long_code = "L" * 64  # "<64 chars>-DMG" cut to 64 chars was the parent's own code
    for code_ in (long_code, "X"):
        lot = lot_of(opening(owner, world, cas, [("BOX", "5")], lot_code=code_))
        r = owner.post(
            f"{P}/inventory/damage",
            json={"lot_id": lot, "unit_code": "BOX", "qty": "1", "note": "crushed"},
            headers=idem(),
        )
        assert r.status_code == 201, r.text  # was a permanent 409 DUPLICATE
        dmg = owner.get(f"{P}/inventory/lots/{r.json()['lot_id']}").json()
        assert dmg["status"] == "DAMAGED" and len(dmg["lot_code"]) <= 64
        assert dmg["lot_code"] != code_ and "-DMG" in dmg["lot_code"]
