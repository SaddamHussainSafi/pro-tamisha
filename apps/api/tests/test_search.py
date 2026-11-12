"""Universal search (migration 0029): parser, ranking, exact numbers, permissions, sync through
the change queue, Trash, and the fallback when the search table is behind or unavailable.
Synthetic data only."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from pro_trading.db import get_sessionmaker
from pro_trading.models import (
    Customer,
    GrnLine,
    InventoryLot,
    Invoice,
    Payment,
    PickingAttempt,
    Product,
    ProductAlias,
    SalesOrder,
    Shipment,
    SupplierPayment,
    Vendor,
)
from pro_trading.search import engine, indexer
from pro_trading.search.models import SearchDocument, SearchQueueItem
from pro_trading.search.parser import QueryError, parse, tsquery
from pro_trading.search.registry import ident_forms

from .conftest import World, build_world, idem
from .test_orders import P, owner, setup, user  # noqa: F401 - fixtures re-used

S = "/api/v1/search"


# ------------------------------------------------------------------ helpers


def drain() -> int:
    with get_sessionmaker()() as db:
        return indexer.drain_all(db)


def seed(w: World, tag: str = "") -> dict[str, Any]:
    """A small, realistic set of records in two warehouses, then index them via the queue."""
    now = datetime.now(UTC)
    ids: dict[str, Any] = {}
    with get_sessionmaker()() as db, db.begin():
        me = w.ids["owner"]
        cas = Product(company_id=w.company_id, sku="CAS", name="Cassava Frozen", stock_unit="BOX")
        casp = Product(
            company_id=w.company_id, sku="CASP", name="Cassava Frozen - Packaged", stock_unit="BOX"
        )
        pl = Product(company_id=w.company_id, sku="PLG", name="Plantain Green", stock_unit="BOX")
        db.add_all([cas, casp, pl])
        db.flush()
        db.add(
            ProductAlias(
                company_id=w.company_id,
                product_id=cas.id,
                alias="Yuca congelada",
                alias_norm="yuca congelada",
            )
        )
        cust = Customer(
            company_id=w.company_id,
            external_ref="C-1001",
            name="Island Grocers",
            ship_to_name="Island Grocers Kingston St",
        )
        other = Customer(company_id=w.company_id, external_ref="C-2002", name="Harbour Cafe")
        db.add_all([cust, other])
        v = Vendor(
            company_id=w.company_id, code="V-TFS", name="Tropical Fresh Supply", currency="USD"
        )
        db.add(v)
        db.flush()
        shp = Shipment(
            company_id=w.company_id,
            warehouse_id=w.wh_a,
            vendor_id=v.id,
            shipment_no="SHP-20261112-0001",
            vendor_invoice_no="ARX-5521",
            awb_no="176-55512345",
            currency="USD",
            created_by=me,
        )
        shp_b = Shipment(
            company_id=w.company_id,
            warehouse_id=w.wh_b,
            vendor_id=v.id,
            shipment_no="SHP-20261112-0002",
            currency="USD",
            created_by=me,
        )
        db.add_all([shp, shp_b])
        db.flush()
        lot_a = InventoryLot(
            company_id=w.company_id,
            product_id=cas.id,
            warehouse_id=w.wh_a,
            lot_code="CAS-0001-1",
            batch_ref="TFS-0001",
        )
        lot_b = InventoryLot(
            company_id=w.company_id,
            product_id=pl.id,
            warehouse_id=w.wh_b,
            lot_code="PLG-0002-1",
            batch_ref="TFS-0002",
        )
        db.add_all([lot_a, lot_b])
        db.flush()
        db.add(
            GrnLine(
                shipment_id=shp.id,
                seq=1,
                product_id=cas.id,
                unit_code="BOX",
                accepted_qty=Decimal("10"),
                damaged_qty=Decimal("0"),
                rejected_qty=Decimal("0"),
                batch_ref="TFS-0001",
                lot_id=lot_a.id,
                counted_by=me,
            )
        )
        db.add(
            GrnLine(
                shipment_id=shp_b.id,
                seq=1,
                product_id=pl.id,
                unit_code="BOX",
                accepted_qty=Decimal("5"),
                damaged_qty=Decimal("0"),
                rejected_qty=Decimal("0"),
                batch_ref="TFS-0002",
                lot_id=lot_b.id,
                counted_by=me,
            )
        )
        invs = []
        for i, (wh, c, total) in enumerate(
            [(w.wh_a, cust, "1500.00"), (w.wh_b, cust, "250.00"), (w.wh_a, other, "80.00")], 1
        ):
            o = SalesOrder(
                company_id=w.company_id,
                order_no=f"SO-20261112-000{i}",
                customer_id=c.id,
                warehouse_id=wh,
                status="FULFILLED",
                source_snapshot={},
            )
            db.add(o)
            db.flush()
            a = PickingAttempt(
                company_id=w.company_id,
                order_id=o.id,
                warehouse_id=wh,
                attempt_no=1,
                pick_code=f"{o.order_no}-P1",
                status="INVOICED",
            )
            db.add(a)
            db.flush()
            inv = Invoice(
                company_id=w.company_id,
                invoice_no=f"TT2026-00012{i}",
                fiscal_year=2026,
                seq_no=120 + i,
                sales_order_id=o.id,
                picking_attempt_id=a.id,
                customer_id=c.id,
                warehouse_id=wh,
                state="ISSUED",
                subtotal=Decimal(total),
                tax_amount=Decimal("0"),
                total=Decimal(total),
                issued_at=now,
                due_at=now + timedelta(days=30),
                snapshot={},
                pdf_sha256="0" * 64,
                template_version="t",
                email_status="NO_EMAIL",
            )
            db.add(inv)
            db.flush()
            invs.append(inv.id)
        pay = Payment(
            company_id=w.company_id,
            payment_no="PAY-2026-000777",
            customer_id=cust.id,
            method="EFT",
            amount=Decimal("1500.00"),
            received_on=date.today(),
            payer_ref="INTERAC REF 99x",
            recorded_by=me,
        )
        sp = SupplierPayment(
            company_id=w.company_id,
            payment_no="SP-2026-000001",
            vendor_id=v.id,
            shipment_id=shp.id,
            method="WIRE",
            amount=Decimal("900.00"),
            currency="USD",
            paid_on=date.today(),
            reference="WIRE-777",
            recorded_by=me,
        )
        db.add_all([pay, sp])
        db.flush()
        ids.update(
            cas=cas.id,
            casp=casp.id,
            pl=pl.id,
            cust=cust.id,
            other=other.id,
            vendor=v.id,
            shp=shp.id,
            shp_b=shp_b.id,
            lot_a=lot_a.id,
            lot_b=lot_b.id,
            inv_a=invs[0],
            inv_b=invs[1],
            inv_other=invs[2],
            pay=pay.id,
            sp=sp.id,
        )
    drain()
    return ids


def login(make_api: Any, w: World, name: str) -> Any:
    a = make_api()
    assert a.login(w.emails[name]).status_code == 200
    if name in ("finance", "manager_a"):  # privileged roles: two-step sign-in (as test_orders)
        assert a.enroll_and_verify().status_code == 200
    return a


def q(api: Any, query: str, **params: Any) -> dict[str, Any]:
    r = api.get(S, params={"q": query, "limit": 50, **params})
    assert r.status_code == 200, r.text
    return r.json()


def titles(res: dict[str, Any]) -> list[str]:
    return [i["title"] for i in res["items"]]


@pytest.fixture
def seeded(world: World) -> tuple[World, dict[str, Any]]:
    return world, seed(world)


# ------------------------------------------------------------------ parser


def test_parser_keys_dates_amounts_and_plain_text() -> None:
    p = parse(
        'invoice:TT2026-000123 customer:"island grocers" status:issued,paid '
        "date:2026-11 amount:>1000 hello"
    )
    assert p.idents == ["TT2026-000123"] and p.types == {"invoice"}
    assert p.customer == "island grocers" and p.statuses == ["ISSUED", "PAID"]
    assert (p.date_from, p.date_to) == (date(2026, 11, 1), date(2026, 11, 30))
    assert p.amount_min == Decimal("1000.01") and p.amount_max is None
    assert p.text == "hello" and p.needle == "TT2026-000123 hello"
    assert parse("date:2026-02").date_to == date(2026, 2, 28)
    assert parse("date:2026-11-01..2026-11-15").date_to == date(2026, 11, 15)
    assert parse("amount:500..2000").amount_max == Decimal("2000")
    assert parse("batch:TFS-0001").types == {"lot", "shipment"}
    assert parse("sku:cas").sku == "CAS"
    # Not a key: stays text (times, URLs).
    assert parse("meet 12:30 https://x.test").text == "meet 12:30 https://x.test"
    assert parse("type:orders,invoices").types == {"sales_order", "invoice"}
    assert parse("type:invoices invoice:X").types == {"invoice"}
    assert parse("type:invoices batch:X").types == set()  # contradictory: nothing
    assert parse("invoice:").types == {"invoice"} and parse("invoice:").text == ""


@pytest.mark.parametrize(
    "bad",
    [
        "date:2026-13",
        "date:soon",
        "amount:lots",
        "amount:>1e9",
        "sku:CASSAVA",
        "type:spaceships",
        "status:';--",
        "x" * 201,
    ],
)
def test_parser_rejects_invalid_filters(bad: str) -> None:
    with pytest.raises(QueryError):
        parse(bad)


def test_tsquery_only_letters_and_digits() -> None:
    assert tsquery("cass gre") == "cass:* & gre:*"
    assert tsquery("a' | ! & (b") == "a:* & b:*"
    assert tsquery("'';--") is None
    assert ident_forms("176 55512345") == ["176 55512345", "17655512345"]


# ------------------------------------------------------------------ exact numbers and ranking


def test_exact_numbers_come_first(make_api, seeded) -> None:
    w, ids = seeded
    o = login(make_api, w, "owner")
    r = q(o, "TT2026-000121")
    assert r["items"][0]["title"] == "TT2026-000121" and r["items"][0]["match"] == "number"
    assert (
        r["items"][0]["type"] == "invoice" and r["items"][0]["url"] == f"/invoices/{ids['inv_a']}"
    )
    # lowercase / spaces / no dash still exact
    assert q(o, "tt2026-000121")["items"][0]["title"] == "TT2026-000121"
    # batch: the lot first, then the shipment that received it
    r = q(o, "TFS-0001")
    assert [(i["type"], i["match"]) for i in r["items"][:2]] == [
        ("lot", "number"),
        ("shipment", "number"),
    ]
    # airway bill written with a space; supplier invoice; GRN-less shipment number
    assert q(o, "176 55512345")["items"][0]["title"] == "SHP-20261112-0001"
    assert q(o, "17655512345")["items"][0]["title"] == "SHP-20261112-0001"
    assert q(o, "arx-5521")["items"][0]["title"] == "SHP-20261112-0001"
    assert q(o, "PAY-2026-000777")["items"][0]["type"] == "payment"
    assert q(o, "INTERAC REF 99X")["items"][0]["type"] == "payment"
    assert q(o, "WIRE-777")["items"][0]["type"] == "supplier_payment"
    assert q(o, "C-1001")["items"][0]["title"] == "Island Grocers"
    assert q(o, "V-TFS")["items"][0]["title"] == "Tropical Fresh Supply"
    r = q(o, "CAS")
    assert r["items"][0]["title"] == "Cassava Frozen" and r["items"][0]["match"] == "number"
    assert q(o, "SO-20261112-0001")["items"][0]["type"] == "sales_order"
    assert q(o, "SO-20261112-0001-P1")["items"][0]["type"] == "pick"


def test_fuzzy_never_outranks_or_blurs_exact_numbers(make_api, seeded) -> None:
    w, _ = seeded
    o = login(make_api, w, "owner")
    r = q(o, "TT2026-000121")
    exact = [i for i in r["items"] if i["match"] == "number"]
    assert [i["title"] for i in exact] == ["TT2026-000121"]
    # a typo in a number does not "find" a neighbouring invoice
    assert "TT2026-000122" not in titles(q(o, "TT2026-000129"))
    # typo tolerance for names
    r = q(o, "casava")
    assert (
        r["items"][0]["title"].startswith("Cassava")
        and r["items"][0]["match"] == "similar spelling"
    )
    # tiers: name prefix > words > linked record
    r = q(o, "cassava")
    order = [(i["type"], i["match"]) for i in r["items"]]
    assert order[0] == ("product", "starts with") and ("lot", "words") in order
    ranks = [list(engine.MATCH_LABEL.values()).index(m) for _, m in order]
    assert ranks == sorted(ranks)  # tiers never interleave


def test_prefix_partial_multiword_alias_and_highlight_terms(make_api, seeded) -> None:
    w, _ = seeded
    o = login(make_api, w, "owner")
    assert q(o, "TT2026-00012")["total"] == 3  # number prefix
    assert q(o, "000122")["items"][0]["title"] == "TT2026-000122"  # number contains
    r = q(o, "plan gre")  # every word, prefixes
    assert r["items"][0]["title"] == "Plantain Green"
    assert q(o, "yuca")["items"][0]["title"] == "Cassava Frozen"  # alias
    assert q(o, "Isla Groc")["terms"] == ["isla", "groc"]
    r = q(o, "island", per_group=2)
    assert {g["type"] for g in r["groups"]} >= {"customer", "invoice", "sales_order"}
    inv = next(g for g in r["groups"] if g["type"] == "invoice")
    assert inv["count"] == 2 and len(inv["items"]) == 2


def test_filters(make_api, seeded) -> None:
    w, ids = seeded
    o = login(make_api, w, "owner")
    r = q(o, "customer:island type:invoices")
    assert sorted(titles(r)) == ["TT2026-000121", "TT2026-000122"]
    assert {"TT2026-000123", "Harbour Cafe", "SO-20261112-0003"} <= set(
        titles(q(o, "customer:C-2002"))
    )
    assert "TT2026-000121" not in titles(q(o, "customer:C-2002"))
    assert set(titles(q(o, "amount:>1000"))) == {"TT2026-000121", "PAY-2026-000777"}
    assert set(titles(q(o, "amount:100..300"))) == {"TT2026-000122"}
    assert set(titles(q(o, "sku:PLG"))) >= {"Plantain Green", "PLG-0002-1", "SHP-20261112-0002"}
    assert set(titles(q(o, "supplier:tropical type:receiving"))) == {
        "SHP-20261112-0001",
        "SHP-20261112-0002",
    }
    assert titles(q(o, "store:kingston type:customers")) == ["Island Grocers"]
    assert q(o, "status:issued customer:nobody-here")["total"] == 0
    yr = date.today().year
    assert q(o, f"date:{yr - 5} type:invoices")["total"] == 0
    assert (
        q(o, "invoice:TT2026-000121 customer:harbour")["total"] == 0
    )  # live hit must pass filters
    r = o.get(S, params={"q": "date:2026-13"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "BAD_QUERY"


def test_injection_strings_are_plain_text(make_api, seeded) -> None:
    w, _ = seeded
    o = login(make_api, w, "owner")
    for s in [
        "'; DROP TABLE invoices; --",
        "%",
        "_",
        "\\",
        "' OR 1=1 --",
        "a:b:c",
        "(((",
        "!:*&|",
        "ñandú 🍌",
        "\x00",
    ]:
        r = o.get(S, params={"q": s})
        assert r.status_code in (200, 422), (s, r.text)
    with get_sessionmaker()() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Invoice).where(Invoice.company_id == w.company_id)
            )
            == 3
        )
    assert q(o, "%")["total"] == 0 and q(o, "_")["total"] == 0


# ------------------------------------------------------------------ permissions


def test_warehouse_sales_finance_owner_see_only_their_records(make_api, seeded) -> None:
    w, ids = seeded
    owner_ = login(make_api, w, "owner")
    wh = login(make_api, w, "wh_a")  # Warehouse role, warehouse A only
    sales = login(make_api, w, "sales")  # company-wide Sales
    fin = login(make_api, w, "finance")
    mgr = login(make_api, w, "manager_a")  # Admin, warehouse A only

    # Warehouse: no invoices, payments or supplier payments at all; lots only in A.
    r = q(wh, "TT2026-000121")
    assert r["total"] == 0
    assert {t["type"] for t in r["types"]} >= {"lot", "product"} and "invoice" not in {
        t["type"] for t in r["types"]
    }
    assert titles(q(wh, "TFS-0001"))[:1] == ["CAS-0001-1"]
    assert q(wh, "TFS-0002")["total"] == 0  # warehouse B
    assert q(wh, "PAY-2026-000777")["total"] == 0 and q(wh, "WIRE-777")["total"] == 0
    # counts: only what this person may see (no count leakage)
    r = q(wh, "island", per_group=5)
    assert all(g["type"] not in ("invoice", "payment") for g in r["groups"])

    # Admin limited to warehouse A: invoices in A only, counts agree.
    r = q(mgr, "customer:island type:invoices")
    assert titles(r) == ["TT2026-000121"] and r["total"] == 1
    assert q(mgr, "TT2026-000122")["total"] == 0

    # Sales: invoices everywhere and payments; no supplier payments; no activity log.
    assert q(sales, "TT2026-000122")["items"][0]["title"] == "TT2026-000122"
    assert q(sales, "PAY-2026-000777")["total"] == 1
    assert q(sales, "WIRE-777")["total"] == 0
    assert "activity" not in {t["type"] for t in q(sales, "x")["types"]}

    # Accounting: supplier payments and amounts.
    r = q(fin, "WIRE-777")
    assert r["items"][0]["type"] == "supplier_payment" and r["items"][0]["amount"] == "900.00"

    # Super Admin: everything, including the activity log.
    assert "activity" in {t["type"] for t in q(owner_, "x")["types"]}


def test_amount_filter_and_money_are_hidden_without_money_permission(make_api, seeded) -> None:
    w, ids = seeded
    wh = login(make_api, w, "wh_a")
    r = q(wh, "amount:>0")
    assert r["total"] == 0 and r["notes"]
    # Order preview for warehouse staff: no invoice / payment links, no prices.
    pv = wh.get(f"{S}/preview/customer/{ids['cust']}").json()
    assert {x["type"] for x in pv["related"]} <= {
        "sales_order",
        "pick",
        "lot",
        "product",
        "customer",
        "shipment",
        "vendor",
    }
    assert all("CAD" not in x["subtitle"] for x in pv["related"])
    sales = login(make_api, w, "sales")
    pv = sales.get(f"{S}/preview/invoice/{ids['inv_a']}").json()
    assert {f["label"] for f in pv["fields"]} >= {"Total", "Paid"}
    assert any(a["href"].endswith("/pdf") for a in pv["actions"]) is False  # no PDF stored here


def test_cross_company_isolation(make_api, seeded) -> None:
    w, ids = seeded
    w2 = build_world()
    seed(w2)  # same numbers, other company
    o2 = login(make_api, w2, "owner")
    r = q(o2, "TT2026-000121")
    assert r["total"] == 1 and r["items"][0]["id"] != str(ids["inv_a"])
    # previews / recents of the other company's records: 404, never 403 (nothing revealed)
    assert o2.get(f"{S}/preview/invoice/{ids['inv_a']}").status_code == 404
    assert (
        o2.post(f"{S}/recent", json={"type": "invoice", "id": str(ids["inv_a"])}).status_code == 404
    )


def test_preview_related_records_follow_real_links(make_api, seeded) -> None:
    w, ids = seeded
    o = login(make_api, w, "owner")
    pv = o.get(f"{S}/preview/lot/{ids['lot_a']}").json()
    rel = {(x["type"], x["title"]) for x in pv["related"]}
    assert ("product", "Cassava Frozen") in rel and ("shipment", "SHP-20261112-0001") in rel
    assert ("vendor", "Tropical Fresh Supply") in rel
    pv = o.get(f"{S}/preview/customer/{ids['cust']}").json()
    rel = {(x["type"], x["title"]) for x in pv["related"]}
    assert {
        ("invoice", "TT2026-000121"),
        ("invoice", "TT2026-000122"),
        ("payment", "PAY-2026-000777"),
    } <= rel
    pv = o.get(f"{S}/preview/invoice/{ids['inv_a']}").json()
    rel = {x["type"] for x in pv["related"]}
    assert {"customer", "sales_order"} <= rel
    pv = o.get(f"{S}/preview/product/{ids['cas']}").json()
    assert ("product", "Cassava Frozen - Packaged") in {
        (x["type"], x["title"]) for x in pv["related"]
    }
    assert o.get(f"{S}/preview/invoice/{uuid.uuid4()}").status_code == 404
    assert o.get(f"{S}/preview/nonsense/{uuid.uuid4()}").status_code == 404
    # manager limited to warehouse A cannot preview warehouse B's invoice
    assert (
        login(make_api, w, "manager_a").get(f"{S}/preview/invoice/{ids['inv_b']}").status_code
        == 404
    )


def test_recent_is_private_and_clearable(make_api, seeded) -> None:
    w, ids = seeded
    o = login(make_api, w, "owner")
    s = login(make_api, w, "sales")
    assert (
        o.post(f"{S}/recent", json={"type": "invoice", "id": str(ids["inv_a"])}).status_code == 204
    )
    assert (
        o.post(f"{S}/recent", json={"type": "customer", "id": str(ids["cust"])}).status_code == 204
    )
    assert [r["title"] for r in o.get(f"{S}/recent").json()] == ["Island Grocers", "TT2026-000121"]
    assert s.get(f"{S}/recent").json() == []
    wh = login(make_api, w, "wh_a")
    assert (
        wh.post(f"{S}/recent", json={"type": "invoice", "id": str(ids["inv_a"])}).status_code == 404
    )
    assert o.delete(f"{S}/recent").status_code == 204
    assert o.get(f"{S}/recent").json() == []


# ------------------------------------------------------------------ sync, idempotency, Trash


def test_changes_flow_through_the_queue_and_rename_reaches_linked_records(seeded) -> None:
    w, ids = seeded
    with get_sessionmaker()() as db, db.begin():
        db.get(Customer, ids["cust"]).name = "Island Grocers Ltd"
    with get_sessionmaker()() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(SearchQueueItem)
                .where(SearchQueueItem.source_id == ids["cust"])
            )
            >= 1
        )
    drain()  # customer, then its invoices/orders/payments (fan-out), drained in the same call
    with get_sessionmaker()() as db:
        assert db.get(SearchDocument, ("customer", ids["cust"])).title == "Island Grocers Ltd"
        inv = db.get(SearchDocument, ("invoice", ids["inv_a"]))
        assert "island grocers ltd" in inv.names
        assert (
            db.scalar(
                select(func.count())
                .select_from(SearchQueueItem)
                .where(SearchQueueItem.status == "PENDING")
            )
            == 0
        )


def test_reindex_is_idempotent_and_duplicates_coalesce(seeded) -> None:
    w, ids = seeded
    with get_sessionmaker()() as db:
        before = db.get(SearchDocument, ("invoice", ids["inv_a"]))
        b = (before.title, before.identifiers, before.names, before.body)
        indexer.enqueue(db, "invoice", [ids["inv_a"]] * 5)
        db.commit()
        r = indexer.drain(db)
        assert r.claimed == 5 and r.documents == 1
        indexer.reindex(db, "invoice", ids["inv_a"])
        indexer.reindex(db, "invoice", ids["inv_a"])
        db.commit()
        db.expire_all()
        after = db.get(SearchDocument, ("invoice", ids["inv_a"]))
        assert (after.title, after.identifiers, after.names, after.body) == b
        assert (
            db.scalar(
                select(func.count())
                .select_from(SearchDocument)
                .where(SearchDocument.source_id == ids["inv_a"])
            )
            == 1
        )


def test_failures_are_retried_with_backoff_then_marked_failed(seeded, monkeypatch) -> None:
    w, ids = seeded
    calls = {"n": 0}

    def boom(db, sid):
        calls["n"] += 1
        raise RuntimeError("source unavailable")

    monkeypatch.setitem(indexer.BUILDERS, "vendor", boom)
    with get_sessionmaker()() as db:
        indexer.enqueue(db, "vendor", [ids["vendor"]])
        db.commit()
        r = indexer.drain(db)
        assert r.failed == 1 and calls["n"] == 1
        row = db.scalar(select(SearchQueueItem).where(SearchQueueItem.source_id == ids["vendor"]))
        assert (
            row.attempts == 1
            and row.status == "PENDING"
            and row.next_attempt_at > datetime.now(UTC)
        )
        assert indexer.drain(db).claimed == 0  # backing off
        db.execute(
            text(
                "UPDATE search_index_queue SET attempts = :n,"
                " next_attempt_at = now() - interval '1 s' WHERE source_id = :i"
            ),
            {"n": indexer.MAX_ATTEMPTS - 1, "i": ids["vendor"]},
        )
        db.commit()
        indexer.drain(db)
        db.expire_all()
        row = db.scalar(select(SearchQueueItem).where(SearchQueueItem.source_id == ids["vendor"]))
        assert row.status == "FAILED"
        rep = indexer.reconcile(db, w.company_id)
        assert rep["queue_failed"] >= 1 and not rep["ok"]
    monkeypatch.undo()
    with get_sessionmaker()() as db:
        assert indexer.retry_failed(db) >= 1
        indexer.drain_all(db)
        assert (
            db.scalar(
                select(func.count())
                .select_from(SearchQueueItem)
                .where(SearchQueueItem.status == "FAILED")
            )
            == 0
        )


def test_deleted_rows_leave_the_index(seeded) -> None:
    w, ids = seeded
    with get_sessionmaker()() as db, db.begin():
        db.execute(text("DELETE FROM product_aliases WHERE product_id = :p"), {"p": ids["cas"]})
    drain()
    with get_sessionmaker()() as db:
        assert "yuca" not in db.get(SearchDocument, ("product", ids["cas"])).names
        # A vendor with nothing attached can be deleted outright.
        v = Vendor(company_id=w.company_id, code="V-DEL", name="Short Lived Supply", currency="USD")
        db.add(v)
        db.commit()
        indexer.drain_all(db)
        assert db.get(SearchDocument, ("vendor", v.id)) is not None
        db.delete(v)
        db.commit()
        indexer.drain_all(db)
        assert db.get(SearchDocument, ("vendor", v.id)) is None


def test_trash_and_restore_update_search(owner, world) -> None:  # noqa: F811
    r = owner.post(
        f"{P}/products",
        json={
            "sku": "ZQX",
            "name": "Zebra Quince",
            "stock_unit": "BOX",
            "unit_profiles": [{"code": "CASE10", "unit_code": "BOX", "is_default": True}],
        },
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    drain()
    assert titles(q(owner, "zebra quince"))[0] == "Zebra Quince"
    r = owner.delete(f"{P}/products/{pid}", headers=idem())
    assert r.status_code == 200, r.text
    item = r.json()
    drain()
    # gone (its packaged twin "Zebra Quince - Packaged" stays: it was not trashed)
    assert pid not in [i["id"] for i in q(owner, "zebra quince")["items"]]
    assert owner.get(f"{S}/preview/product/{pid}").status_code == 404
    assert owner.post(f"{P}/trash/{item['id']}/restore", headers=idem()).status_code == 200
    drain()
    assert titles(q(owner, "ZQX"))[0] == "Zebra Quince"


def test_reconcile_and_backfill(seeded) -> None:
    w, ids = seeded
    with get_sessionmaker()() as db:
        assert indexer.reconcile(db, w.company_id)["ok"]
        db.execute(
            text("DELETE FROM search_documents WHERE doc_type = 'invoice' AND company_id = :c"),
            {"c": w.company_id},
        )
        db.execute(
            text(
                "INSERT INTO search_documents"
                " (doc_type, source_id, company_id, title, title_norm, url)"
                " VALUES ('invoice', :i, :c, 'ghost', 'ghost', '/')"
            ),
            {"i": uuid.uuid4(), "c": w.company_id},
        )
        db.commit()
        rep = indexer.reconcile(db, w.company_id)
        assert rep["types"]["invoice"]["missing"] == 3 and rep["types"]["invoice"]["orphaned"] == 1
        indexer.backfill(db, ["invoice"], w.company_id)
        assert indexer.reconcile(db, w.company_id)["types"]["invoice"] == {
            "source": 3,
            "documents": 3,
            "missing": 0,
            "orphaned": 0,
        }


# ------------------------------------------------------------------ index behind / unavailable


def test_exact_numbers_work_while_the_index_is_behind(make_api, world) -> None:
    ids = seed(world)
    with get_sessionmaker()() as db, db.begin():
        o = SalesOrder(
            company_id=world.company_id,
            order_no="SO-20261112-0099",
            customer_id=ids["cust"],
            warehouse_id=world.wh_a,
            status="DRAFT",
            source_snapshot={},
        )
        db.add(o)
    # not drained: the search table has never heard of it
    owner_ = login(make_api, world, "owner")
    r = q(owner_, "SO-20261112-0099")
    assert r["items"][0]["title"] == "SO-20261112-0099" and r["items"][0]["match"] == "number"
    assert r["index"]["pending"] >= 1
    # ... and permissions still apply to the live lookup
    assert (
        q(login(make_api, world, "finance"), "SO-20261112-0099")["total"] == 1
    )  # finance: orders company-wide
    wh_b_only = q(login(make_api, world, "wh_a"), "TFS-0002")
    assert wh_b_only["total"] == 0


def test_exact_numbers_work_when_the_index_is_down(make_api, seeded, monkeypatch) -> None:
    w, ids = seeded

    def down(*a, **k):
        raise OperationalError("SELECT", {}, Exception("search_documents unavailable"))

    monkeypatch.setattr(engine, "_index_query", down)
    o = login(make_api, w, "owner")
    r = q(o, "TT2026-000121")
    assert r["index"]["degraded"] is True
    assert r["items"][0]["title"] == "TT2026-000121"
    assert q(o, "TFS-0001")["items"][0]["title"] == "CAS-0001-1"
    assert q(o, "cassava frozen")["items"][0]["title"] == "Cassava Frozen"  # exact name, live
    assert q(login(make_api, w, "wh_a"), "TT2026-000121")["total"] == 0


def test_search_writes_nothing_to_erp_tables(make_api, seeded) -> None:
    w, ids = seeded
    o = login(make_api, w, "owner")
    with get_sessionmaker()() as db:
        before = db.execute(
            text(
                "SELECT (SELECT count(*) FROM stock_movements),"
                " (SELECT sum(total) FROM invoices WHERE company_id = :c)"
            ),
            {"c": w.company_id},
        ).one()
    for s in ["cassava", "TT2026-000121", "amount:>0", "TFS-0001"]:
        q(o, s)
    o.get(f"{S}/preview/lot/{ids['lot_a']}")
    with get_sessionmaker()() as db:
        after = db.execute(
            text(
                "SELECT (SELECT count(*) FROM stock_movements),"
                " (SELECT sum(total) FROM invoices WHERE company_id = :c)"
            ),
            {"c": w.company_id},
        ).one()
    assert before == after


def test_empty_query_lists_permitted_categories_only(make_api, seeded) -> None:
    w, _ = seeded
    r = q(login(make_api, w, "wh_a"), "")
    assert r["total"] == 0 and r["items"] == []
    assert {t["type"] for t in r["types"]} == {
        "lot",
        "stock_count",
        "sales_order",
        "shipment",
        "customer",
        "product",
        "vendor",
        "pick",
    }
    assert login(make_api, w, "wh_a").get(f"{S}/status").json()["missing_modules"]
