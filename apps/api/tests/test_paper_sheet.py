# ruff: noqa: F811 - fixtures re-used by name
"""Owner report 2026-11-09: "when I scan picking sheets or sales orders, the products do not come
up". The office's own paper order sheet (TT-PAPER-1: VEGETABLES / ROOTS / FRUITS tables, columns
ITEM | Q | PKG | WEIGHT | NOTES, quantities handwritten, often scanned sideways) is read into
lines to review:

- picking sheets are read like sales orders (same paper form); "Other" waits for a request;
- printed name + mark in that row's Q box = one line, matched to the SKU via the sheet's own
  spellings (aliases); PKG/WEIGHT/NOTES marks are not quantities; unclear marks need a person;
- a sideways, image-only scan read by Tesseract gives SKUs and quantities to review;
- the same file uploaded again returns the earlier scan, which can then be read;
- nothing invoices, reserves or moves stock.
Synthetic sheets and data only.
"""

import shutil
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from pro_trading import ocr, paper_sheet
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.models import OcrExtraction, ScanJob
from scripts.load_catalog import CATALOG, load_catalog

from .conftest import idem
from .test_ocr import P, code, counts, drain, only, owner, resolve, user  # noqa: F401

D = Decimal


@pytest.fixture
def catalog(owner, world):
    with get_sessionmaker()() as db, db.begin():
        load_catalog(db, world.company_id)
    c = owner.post(
        f"{P}/customers", json={"external_ref": "C-200", "name": "Island Grocers"}, headers=idem()
    )
    assert c.status_code == 201, c.text
    return {"cust": c.json()["id"]}


def blocks() -> list[list[tuple[str, list[str]]]]:
    def names(cat: str) -> list[str]:
        return [printed for _, _, printed in CATALOG[cat]]

    return [
        [("VEGETABLES", names("VEG")), ("PEPPERS", names("PEP"))],
        [("ROOTS", names("ROOT")), ("HERBS", names("HERB")), ("OTHERS", names("OTHER"))],
        [("FRUITS", names("FRUIT"))],
    ]


def post_file(api, world, data: bytes, job_type: str, name: str = "sheet.pdf"):
    q = f"warehouse_id={world.wh_a}&job_type={job_type}&filename={name}"
    h = {**api._h(idem()), "Content-Type": "application/pdf"}
    return api.c.post(f"{P}/scans/uploads?{q}", content=data, headers=h)


# ------------------------------------------------------------------ mapping (engine-neutral)


def tk(text, x, y, w="0.03", hand=False, conf="0.970", page=1):
    x, y, w = D(x), D(y), D(w)
    return ocr.Token(text, page, x, y - D("0.006"), x + w, y + D("0.006"), D(conf), hand)


def mini_sheet() -> list[ocr.Token]:
    toks = [
        tk("TAMISHA", "0.05", "0.03"), tk("TRADING", "0.085", "0.03"), tk("INC", "0.12", "0.03"),
        tk("CUSTOMER", "0.23", "0.03"), tk("NAME:", "0.265", "0.03"),
        tk("Island", "0.40", "0.03", hand=True), tk("Grocers", "0.44", "0.03", hand=True),
        tk("VEGETABLES", "0.15", "0.08", "0.06"), tk("ROOTS", "0.45", "0.08"),
    ]  # fmt: skip
    for dx in ("0", "0.30"):  # two tables: ITEM | Q | PKG | WEIGHT | NOTES
        o = D(dx)
        for text, x in (("ITEM", "0.12"), ("Q", "0.226"), ("PKG", "0.249"), ("WEIGHT", "0.279"),
                        ("NOTES", "0.318")):  # fmt: skip
            toks.append(tk(text, D(x) + o, "0.10", "0.02"))
    q, pkg = D("0.218"), D("0.252")  # where the writing sits in Q and PKG
    rows = [
        # table 1
        (["OKRA"], "0.05", [("12", q, "0.950")]),
        (["INDIAN", "OKRA"], "0.05", [("1", pkg, "0.950")]),  # tick in PKG only: not ordered
        (["SEIM", "(GUYANA)"], "0.05", [("?", q, "0")]),  # something written, unreadable
        (["TUMER1C"], "0.05", [("5", q, "0.950")]),  # OCR slip in the printed name
        (["BREADNUT"], "0.05", []),  # empty Q box: not ordered
        # table 2 (names as printed on the sheet; DASHEENLEAVES lost its space)
        (["YELLOW", "YAM"], "0.35", [("3", q + D("0.30"), "0.950")]),
        (["DASHEENLEAVES"], "0.35", [("2", q + D("0.30"), "0.950")]),
        (["MYSTERY", "ROOT"], "0.35", [("4", q + D("0.30"), "0.950")]),  # not in the catalog
    ]
    for i, (words, x, marks) in enumerate(rows[:5]):
        y = D("0.12") + D("0.02") * i
        xx = D(x)
        for wd in words:
            toks.append(tk(wd, xx, y, "0.035"))
            xx += D("0.042")
        toks += [tk(t, mx, y, "0.02", True, c) for t, mx, c in marks]
    for i, (words, x, marks) in enumerate(rows[5:]):
        y = D("0.121") + D("0.02") * i  # the other table sits a little lower (skewed scan)
        xx = D(x)
        for wd in words:
            toks.append(tk(wd, xx, y, "0.035"))
            xx += D("0.042")
        toks += [tk(t, mx, y, "0.02", True, c) for t, mx, c in marks]
    return toks


def test_paper_sheet_maps_q_column_by_table(world, catalog):
    toks = mini_sheet()
    assert paper_sheet.detect(toks)
    with get_sessionmaker()() as db:
        m = ocr.map_result(db, world.company_id, "PICK_SHEET", ocr.OcrResult(1, toks))
    assert m.template == "TT-PAPER-1" and m.warnings == []
    assert m.customer_id is not None  # handwriting after CUSTOMER NAME: matched by name
    got = [(p.label.split()[0] if p.product_id else p.label, p.qty, p.status) for p in m.proposals]
    assert got == [
        ("OKR", D("12"), "OK"),
        ("SGY", None, "UNREADABLE"),  # Seim (Guyana): staff type the quantity
        ("TUR", D("5"), "LOW_CONFIDENCE"),  # matched despite the slip, so it must be checked
        ("YYM", D("3"), "OK"),
        ("DSL", D("2"), "OK"),
        ("MYSTERY ROOT", D("4"), "UNMAPPED"),
    ]
    assert all(p.unit_code == "BOX" for p in m.proposals if p.product_id)


def test_not_the_paper_sheet_without_its_tables(world, catalog):
    toks = [
        tk("TAMISHA", "0.05", "0.03"),
        tk("OKRA", "0.05", "0.2"),
        tk("12", "0.2", "0.2", hand=True),
    ]
    assert not paper_sheet.detect(toks)


# ------------------------------------------------------------------ which scans are read


def test_picking_sheets_are_read_and_other_waits_for_a_request(user, world, catalog):
    sales = user("sales")
    pdf = paper_sheet.synthetic_scan(blocks(), {"Okra": "12"})
    pick = post_file(sales, world, pdf, "PICK_SHEET")
    assert pick.status_code == 201, pick.text
    assert pick.json()["page_count"] == 1  # counted from the document, before any reading
    items = sales.get(f"{P}/ocr/extractions?scan_job_id={pick.json()['id']}").json()["items"]
    assert [x["status"] for x in items] == ["QUEUED"]

    other = post_file(sales, world, pdf + b"\n% other copy\n", "OTHER")
    assert other.status_code == 201
    oid = other.json()["id"]
    assert sales.get(f"{P}/ocr/extractions?scan_job_id={oid}").json()["total"] == 0
    r = sales.post(f"{P}/ocr/extractions", json={"scan_job_id": oid}, headers=idem())
    assert r.status_code == 201 and r.json()["status"] == "QUEUED"
    # a list never pays for page counting
    rows = sales.get(f"{P}/scans/jobs").json()["items"]
    assert rows and all(x["page_count"] is None for x in rows)
    assert sales.get(f"{P}/scans/jobs/{oid}").json()["page_count"] == 1


def test_same_file_again_returns_the_scan_and_it_can_still_be_read(user, world, catalog):
    """The owner's case: a sheet uploaded as a picking sheet before picking sheets were read."""
    sales = user("sales")
    pdf = paper_sheet.synthetic_scan(blocks(), {"Okra": "12"})
    first = post_file(sales, world, pdf, "PICK_SHEET")
    assert first.status_code == 201
    with get_sessionmaker()() as db, db.begin():  # as if it arrived before this change: never read
        jid = uuid.UUID(first.json()["id"])
        for ex in db.scalars(select(OcrExtraction).where(OcrExtraction.scan_job_id == jid)):
            ex.status, ex.version = "SUPERSEDED", ex.version + 1
    again = post_file(sales, world, pdf, "SALES_ORDER", name="renamed.pdf")
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert again.json()["id"] == first.json()["id"] and again.json()["page_count"] == 1
    r = sales.post(f"{P}/ocr/extractions", json={"scan_job_id": first.json()["id"]}, headers=idem())
    assert r.status_code == 201 and r.json()["status"] == "QUEUED"


# ------------------------------------------------------------------ real pixels (Tesseract)

FILLS = {"Okra": "12", "Tumeric": "5", "Wierri Peppers": "4", "Yellow Yam": "3",
         "Calalloo": "6", "Spice Mango": "2", "Jam Green Banana": "10"}  # fmt: skip
EXPECT = {("OKR", "12.0000"), ("TUR", "5.0000"), ("WIR", "4.0000"), ("YYM", "3.0000"),
          ("CAL", "6.0000"), ("SPM", "2.0000"), ("GBJ", "10.0000")}  # fmt: skip


@pytest.mark.skipif(
    not (shutil.which("tesseract") and shutil.which("pdftoppm")), reason="no tesseract/pdftoppm"
)
@pytest.mark.parametrize("job_type", ["PICK_SHEET", "SALES_ORDER"])
def test_sideways_scan_gives_lines_to_review_and_only_a_draft(
    user, world, catalog, monkeypatch, job_type
):
    sales = user("sales")
    before = counts(world)
    pdf = paper_sheet.synthetic_scan(blocks(), FILLS, rotate=90)  # image-only, turned sideways
    monkeypatch.setenv("OCR_PROVIDER", "tesseract")
    get_settings.cache_clear()
    try:
        job = post_file(sales, world, pdf, job_type)
        assert job.status_code == 201, job.text
        assert drain(world) == 1
    finally:
        monkeypatch.setenv("OCR_PROVIDER", "fake")
        get_settings.cache_clear()
    ex = only(sales)
    assert ex["template"] == "TT-PAPER-1" and ex["provider"] == "tesseract"
    assert ex["page_count"] == 1 and ex["warnings"] == []
    got = {(f["read_sku"], f["read_qty"]) for f in ex["fields"]}
    assert got == EXPECT, sorted(got ^ EXPECT, key=str)
    # handwriting read from pixels is never taken as read: every line needs a person
    assert {f["status"] for f in ex["fields"]} == {"LOW_CONFIDENCE"}
    f0 = ex["fields"][0]
    assert code(resolve(sales, ex, f0, "CONFIRM")) == "NEEDS_CORRECTION"
    for f in ex["fields"]:
        r = resolve(sales, ex, f, "CORRECT", product_id=f["read_product_id"], unit_code="BOX",
                    qty=f["read_qty"])  # fmt: skip
        assert r.status_code == 200, r.text
        ex = r.json()
    acc = {"version": ex["version"], "customer_id": catalog["cust"], "reviewed_all": True}
    r = sales.post(f"{P}/ocr/extractions/{ex['id']}/accept", json=acc, headers=idem())
    assert r.status_code == 200, r.text
    so = sales.get(f"{P}/sales-orders/{r.json()['sales_order_id']}").json()
    assert so["status"] == "DRAFT" and so["source"] == "SCAN"
    assert {(ln["sku"], ln["ordered_qty"]) for ln in so["lines"]} == EXPECT
    assert counts(world) == before  # no invoice, reservation or stock movement
    with get_sessionmaker()() as db:
        assert db.get(ScanJob, job.json()["id"]).job_type == job_type
