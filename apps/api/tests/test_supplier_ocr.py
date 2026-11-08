# ruff: noqa: F811, S603, S607, E501 - fixtures re-used by name; test-only tools; sample data
"""Supplier documents read by real OCR (Tesseract): a typed supplier invoice is printed,
rasterized to 300 dpi (no text layer) and read. Header values and lines are measured exactly;
wrong arithmetic and repeated invoice numbers are flagged, never fixed."""

import shutil
import subprocess
from pathlib import Path

import pytest

from pro_trading.invoice_pdf import PAGE_H, _assemble, _Page
from pro_trading.supplier_ocr import money, parse_date

from .conftest import idem
from .test_ocr import drain, owner, setup, upload, user  # noqa: F401

P = "/api/v1"
real = pytest.mark.skipif(
    not (shutil.which("tesseract") and shutil.which("pdftoppm")), reason="no tesseract/pdftoppm"
)


def invoice_pdf(no="ARX-5521", lines=None, subtotal="545.00", tax="0.00", total="545.00") -> bytes:
    pg = _Page()
    top = PAGE_H - 60
    pg.text(50, top, "Accra Roots Exports", 16, bold=True)
    pg.text(50, top - 20, "12 Harbour Road, Tema, Ghana")
    pg.text(50, top - 60, "INVOICE", 14, bold=True)
    pg.text(50, top - 80, f"Invoice No: {no}", 11)
    pg.text(50, top - 96, "Invoice Date: 2026-11-02", 11)
    pg.text(50, top - 112, "Due Date: 2026-12-02", 11)
    pg.text(50, top - 128, "Currency: USD", 11)
    y = top - 170
    pg.text(50, y, "Description", 11, bold=True)
    pg.text(300, y, "Qty", 11, bold=True)
    pg.text(340, y, "Unit", 11, bold=True)
    pg.right(470, y, "Price", 11, bold=True)
    pg.right(550, y, "Amount", 11, bold=True)
    for desc, q, u, pr, amt in lines or [
        ("Cassava frozen", "25", "box", "12.50", "312.50"),
        ("Yellow yam", "30", "lb", "7.75", "232.50"),
    ]:
        y -= 20
        pg.text(50, y, desc, 11)
        pg.text(300, y, q, 11)
        pg.text(340, y, u, 11)
        pg.right(470, y, pr, 11)
        pg.right(550, y, amt, 11)
    y -= 40
    pg.text(340, y, "Subtotal", 11)
    pg.right(550, y, subtotal, 11)
    pg.text(340, y - 18, "Tax", 11)
    pg.right(550, y - 18, tax, 11)
    pg.text(340, y - 36, "Total", 11, bold=True)
    pg.right(550, y - 36, total, 11, bold=True)
    return _assemble([pg], f"Invoice {no}")


def scan(pdf: bytes, tmp: Path) -> bytes:
    (tmp / "i.pdf").write_bytes(pdf)
    subprocess.run(
        ["pdftoppm", "-r", "300", "-png", "-singlefile", str(tmp / "i.pdf"), str(tmp / "i")],
        check=True,
    )
    return (tmp / "i.png").read_bytes()


@pytest.fixture
def vendor(owner, setup):
    r = owner.post(
        f"{P}/vendors", json={"code": "V-ACC", "name": "Accra Roots Exports"}, headers=idem()
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def tesseract(monkeypatch):
    from pro_trading.config import get_settings

    monkeypatch.setenv("OCR_PROVIDER", "tesseract")
    get_settings.cache_clear()
    yield
    monkeypatch.setenv("OCR_PROVIDER", "fake")
    get_settings.cache_clear()


def read_one(api, world, png):
    upload(api, world, png, ctype="image/png", job_type="VENDOR_DOCUMENT")
    assert drain(world) == 1
    items = api.get(f"{P}/ocr/extractions").json()["items"]
    return api.get(f"{P}/ocr/extractions/{items[0]['id']}").json()


def test_helpers():
    assert money("1,234.50") == money("$1234.50") and money("(5.00)") < 0 and money("abc") is None
    assert parse_date("Date: 2026-11-02")[0].isoformat() == "2026-11-02"
    assert parse_date("02/11/2026")[1] is True  # day/month unclear -> flagged
    assert parse_date("25/11/2026")[0].isoformat() == "2026-11-25"
    assert parse_date("Nov 2, 2026")[0].isoformat() == "2026-11-02"


@real
def test_invoice_header_and_lines(user, world, setup, vendor, tesseract, tmp_path):
    ex = read_one(
        user("owner_sales") if False else user("sales"), world, scan(invoice_pdf(), tmp_path)
    )
    h = {k: v["value"] for k, v in ex["header"].items()}
    expect = {"supplier": "Accra Roots Exports", "document_no": "ARX-5521", "document_date": "2026-11-02",
              "due_date": "2026-12-02", "currency": "USD", "subtotal": "545.00", "tax": "0.00", "total": "545.00"}  # fmt: skip
    hits = sum(h.get(k) == v for k, v in expect.items())
    lines = {
        (f["read_sku"], f["read_qty"], f["read_unit_price"], f["read_amount"]) for f in ex["fields"]
    }
    want = {("CAS-01", "25.0000", "12.5000", "312.50"), ("YAM-01", "30.0000", "7.7500", "232.50")}
    print(
        f"supplier invoice: header {hits}/{len(expect)}, lines {len(want & lines)}/{len(want)}",
        h,
        lines,
    )
    assert ex["vendor_id"] == vendor["id"]
    assert hits == len(expect) and want <= lines
    assert not [w for w in ex["warnings"] if w.startswith(("LINE_MATH", "TOTALS"))]
    for v in ex["header"].values():
        assert v["confidence"] is None or 0 <= float(v["confidence"]) <= 1


@real
def test_bad_math_and_duplicate_are_flagged(user, world, setup, vendor, tesseract, tmp_path):
    sales = user("sales")
    bad = invoice_pdf(
        lines=[("Cassava frozen", "25", "box", "12.50", "300.00")],
        subtotal="300.00",
        total="310.00",
    )
    ex = read_one(sales, world, scan(bad, tmp_path))
    assert any(w.startswith("LINE_MATH") for w in ex["warnings"])
    assert "TOTALS_DONT_ADD_UP" in ex["warnings"]
    line = ex["fields"][0]
    assert line["status"] == "LOW_CONFIDENCE" and line["read_amount"] == "300.00"  # kept as read
    # same supplier + invoice number on a second, different file
    again = invoice_pdf(
        lines=[("Yellow yam", "30", "lb", "7.75", "232.50")], subtotal="232.50", total="232.50"
    )
    upload(sales, world, scan(again, tmp_path), ctype="image/png", job_type="VENDOR_DOCUMENT")
    assert drain(world) == 1
    items = sales.get(f"{P}/ocr/extractions").json()["items"]
    newest = sales.get(f"{P}/ocr/extractions/{items[0]['id']}").json()
    assert any(w.startswith("POSSIBLE_DUPLICATE") for w in newest["warnings"])


@real
def test_approve_into_receiving(owner, user, world, setup, vendor, tesseract, tmp_path):
    from sqlalchemy import text

    from pro_trading.db import get_sessionmaker

    from .test_ocr import resolve

    ex = read_one(owner, world, scan(invoice_pdf(no="ARX-7001"), tmp_path))
    # a header correction keeps what was read next to the fix
    r = owner.post(
        f"{P}/ocr/extractions/{ex['id']}/header",
        json={
            "version": ex["version"],
            "field": "currency",
            "value": "CAD",
            "reason": "paid in CAD",
        },
    )
    assert r.status_code == 200, r.text
    ex = r.json()
    assert (
        ex["header"]["currency"]["value"] == "USD"
        and ex["header"]["currency"]["corrected"] == "CAD"
    )
    body = {
        "version": ex["version"],
        "vendor_id": vendor["id"],
        "document_no": "ARX-7001",
        "reviewed_all": True,
    }
    r = owner.post(f"{P}/ocr/extractions/{ex['id']}/to-receiving", json=body, headers=idem())
    assert r.status_code == 409 and r.json()["error"]["code"] == "FIELDS_UNRESOLVED"
    for f in ex["fields"]:
        ex = resolve(owner, ex, f, "CONFIRM").json()
    # sales can review but not receive; ordering path refuses supplier documents
    body["version"] = ex["version"]
    assert (
        user("sales")
        .post(f"{P}/ocr/extractions/{ex['id']}/to-receiving", json=body, headers=idem())
        .status_code
        == 403
    )
    acc = {"version": ex["version"], "customer_id": setup["cust"], "reviewed_all": True}
    r = owner.post(f"{P}/ocr/extractions/{ex['id']}/accept", json=acc, headers=idem())
    assert r.json()["error"]["code"] == "USE_RECEIVING"
    r = owner.post(f"{P}/ocr/extractions/{ex['id']}/to-receiving", json=body, headers=idem())
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "ACCEPTED" and done["shipment_id"]
    shp = owner.get(f"{P}/shipments/{done['shipment_id']}").json()
    assert shp["status"] == "OPEN" and shp["vendor_invoice_no"] == "ARX-7001"
    assert len(shp["documents"]) == 1
    declared = {(d["sku"], d["declared_qty"], d["unit_cost"]) for d in shp["declared"]}
    assert declared == {("CAS-01", "25.0000", "12.5000"), ("YAM-01", "30.0000", "7.7500")}
    with get_sessionmaker()() as db:
        moves = db.execute(
            text("select count(*) from stock_movements where company_id=:c"),
            {"c": world.company_id},
        ).scalar_one()
    assert moves == 0  # nothing posted: the count and GRN stay manual steps
    r = owner.post(f"{P}/ocr/extractions/{ex['id']}/to-receiving", json=body, headers=idem())
    assert r.status_code == 409
