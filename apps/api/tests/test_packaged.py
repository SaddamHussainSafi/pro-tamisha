# ruff: noqa: F811 - fixtures are imported and re-used by name
"""Packaged items (owner, 2026-11-11): every product has a packaged twin ("<name> - Packaged",
SKU <SKU>P, own price and stock) in the Packaged category, listed last; on the paper order sheet
a number in PKG orders the packaged item and Q the loose one. Synthetic data only."""

from decimal import Decimal

from pro_trading import ocr, packaged, paper_sheet
from pro_trading.db import get_sessionmaker

from .conftest import idem
from .test_gemini_ocr import by_sku, gemini, needs_tools, no_network, page, row  # noqa: F401
from .test_ocr import P, drain, only, owner, user  # noqa: F401
from .test_paper_sheet import blocks, catalog, mini_sheet, post_file, tk  # noqa: F401

D = Decimal


def twins(world):
    with get_sessionmaker()() as db, db.begin():
        return [p.sku for p in packaged.ensure_twins(db, world.company_id)]


def test_new_products_get_a_packaged_twin_listed_last(owner, world):
    body = {"sku": "ZZA", "name": "Synthetic Zucchini", "stock_unit": "BOX", "tracks_expiry": True}
    for b in (body, {**body, "sku": "AAB", "name": "Synthetic Apple", "tracks_expiry": False}):
        r = owner.post(f"{P}/products", json=b, headers=idem())
        assert r.status_code == 201, r.text
    items = owner.get(f"{P}/products", params={"limit": 1000}).json()["items"]
    skus = [x["sku"] for x in items]
    assert skus == ["AAB", "ZZA", "AABP", "ZZAP"]  # loose first, then the packaged ones
    twin = items[-1]
    assert (twin["name"], twin["stock_unit"], twin["tracks_expiry"]) == (
        "Synthetic Zucchini - Packaged", "BOX", True)  # fmt: skip
    cats = owner.get(f"{P}/product-categories").json()
    assert cats[-1]["code"] == "PACKAGED" and twin["category_id"] == cats[-1]["id"]
    # paging across the loose / packaged boundary keeps every product once, in order
    seen, cur = [], None
    while True:
        q = {"limit": 1, **({"cursor": cur} if cur else {})}
        pg = owner.get(f"{P}/products", params=q).json()
        seen += [x["sku"] for x in pg["items"]]
        if not (cur := pg["next_cursor"]):
            break
    assert seen == skus
    assert [x["sku"] for x in owner.get(f"{P}/product-lookup").json()] == skus
    # SKUs people type stay three letters; a twin is never doubled
    r = owner.post(f"{P}/products", json={**body, "sku": "ABCP"}, headers=idem())
    assert r.status_code == 422
    assert twins(world) == []


def test_backfill_gives_every_active_product_one_twin(world, catalog):
    made = twins(world)
    assert len(made) > 100 and all(packaged.is_packaged(s) for s in made)
    assert twins(world) == []  # idempotent


def test_paper_sheet_pkg_box_orders_the_packaged_item(world, catalog):
    twins(world)
    toks = mini_sheet()
    toks.append(tk("3", D("0.252"), D("0.12"), "0.02", True, "0.950"))  # OKRA: 12 in Q, 3 in PKG
    with get_sessionmaker()() as db:
        m = ocr.map_result(db, world.company_id, "PICK_SHEET", ocr.OcrResult(1, toks))
    got = [(p.label.split()[0] if p.product_id else p.label, p.qty, p.status) for p in m.proposals]
    assert got == [
        ("OKR", D("12"), "OK"),  # Q: the loose item
        ("OKRP", D("3"), "OK"),  # PKG: the packaged item, its own line
        ("IOKP", D("1"), "OK"),  # PKG only: only the packaged item
        ("SGY", None, "UNREADABLE"),
        ("TUR", D("5"), "LOW_CONFIDENCE"),
        ("YYM", D("3"), "OK"),
        ("DSL", D("2"), "OK"),
        ("MYSTERY ROOT", D("4"), "UNMAPPED"),
    ]
    okrp = next(p for p in m.proposals if p.label.startswith("OKRP"))
    assert okrp.raw_text == "PKG: 3" and okrp.label == "OKRP Okra - Packaged"


@needs_tools
def test_gemini_pkg_number_is_the_packaged_item(user, world, catalog, gemini):
    twins(world)
    gemini(
        page(
            [
                row("OKRA", "12", pkg="3"),  # loose 12 and packaged 3
                row("TUMERIC", None, pkg="4", w=True),  # PKG only
                row("YELLOW YAM", "3", t=2, pkg="cs"),  # a unit word in PKG: no packaged line
            ],
            customer="Island Grocers",
        )
    )
    sales = user("sales")
    fills = {"Okra": "12", "Yellow Yam": "3"}
    pdf = paper_sheet.synthetic_scan(blocks(), fills, customer="Island Grocers", rotate=90)
    assert post_file(sales, world, pdf, "SALES_ORDER").status_code == 201
    assert drain(world) == 1
    f = by_sku(only(sales))
    assert set(f) == {"OKR", "OKRP", "TURP", "YYM"}
    okr = f["OKR"]["detail"]
    assert f["OKR"]["read_qty"] == "12.0000" and "PKG_NUMBER" not in okr["flags"]
    assert (okr["pkg"], okr["pkg_raw"]) == (None, "3")  # the 3 is the packaged line's, not a note
    assert (f["OKRP"]["read_qty"], f["OKRP"]["detail"]["packaged_of"]) == ("3.0000", "OKR")
    assert f["TURP"]["read_qty"] == "4.0000" and f["TURP"]["read_unit_code"] == "BOX"
    assert f["YYM"]["detail"]["pkg"] == "cs"
