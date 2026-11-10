# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Airport delivery packet (CBSA B3 + vendor invoice + CFIA Confirmation of Sale) read by OCR.
Synthetic data only: the layout follows a real Colombian packet, the names and numbers don't."""

import uuid
from decimal import Decimal
from types import SimpleNamespace

from pro_trading import airway_packet
from pro_trading.invoice_pdf import PAGE_H, _assemble, _Page
from pro_trading.ocr import Token

from .conftest import idem
from .test_ocr import drain, resolve
from .test_supplier_ocr import P, owner, real, setup, tesseract, upload, user  # noqa: F401

B3_ROWS = [
    "Canada Border Services Agency Commercial Accounting Declaration",
    "Declaration en detail commerciale",
]
INVOICE_ROWS = [
    "ANDES FRUIT SAS",
    "ELECTRONIC INVOICING NUMBERING AUTHORIZATION No. 1234567890 DATE 26/09/2025",
    "EXPORTER INVOICE NUMBER: AFS 0101",
    "ANDES FRUIT SAS NIT 900.000.000-1 INVOICE DATE: 07-nov-26",
    "PAYMENT CONDITIONS CREDIT (15 DAYS)",
    "FOREIGN PURCHASER A.W.B.: 014 - 1234 5678",
    "PRODUCT BOXES PER PRODUCT KG PER BOX KG PER PRODUCT PRICE PER BOX PRICE PER PRODUCT CAD",
    "SUGAR MANGO 1 | 176 2,0 352,0 $11 1.936",
    "TOTAL 1 GROSS WEIGHT 403 NET WEIGHT 352,00 TOTAL VALUE 1.936",
]
CFIA_ROWS = [
    "CONFIRMATION OF SALE CONFIRMATION DE VENTE",
    "1. Vendor (Name and Address) 2. Purchaser (Name and Address)",
    "ANDES FRUIT S.A.S. SYNTHETIC CO INC.",
    "5c. Conveyance Identification No.",
    "> 014-12345678",
    "6a. Date of Direct Shipment to Canada",
    "2026-11-07",
    "10. Currency of Settlement",
    "FOB delivered CAD",
    "0804500010 |: SUGAR MANGOES. 24 co 352 KG 176 11.00 1936.00",
    "21. Total cost all commodities",
    "Net Gross 4936.00",  # OCR misread of 1936.00: the vendor invoice total still agrees
    "23. Date of delivery if delivered sale",
    "Date d'arrivee si vente a la livraison 2026-11-08",
]


def rows(*pages: list[str]) -> list[list[Token]]:
    out = []
    for p, lines in enumerate(pages, start=1):
        for y, line in enumerate(lines):
            out.append([
                Token(w, p, Decimal(x) / 100, Decimal(y) / 100, Decimal(x + 1) / 100,
                      Decimal(y + 1) / 100, Decimal("0.97"), False)
                for x, w in enumerate(line.split())
            ])  # fmt: skip
    return out


MANGO = SimpleNamespace(id=uuid.uuid4(), sku="MNG", name="Sugar Mango")


def catalog() -> SimpleNamespace:
    return SimpleNamespace(
        match=lambda text: MANGO if text.casefold() == "sugar mango" else None,
        unit_ok=lambda _p, unit: unit == "BOX",
    )


def read(db, *pages):
    from pro_trading.supplier_ocr import _box, _val

    return airway_packet.read(
        db, uuid.uuid4(), rows(*pages), catalog(), Decimal("0.9"), lambda **kw: kw, _val, _box
    )


def test_numbers_in_european_and_us_style():
    assert airway_packet.numbers("1.936") == [Decimal("1936"), Decimal("1.936")]
    assert airway_packet.numbers("2,0") == [Decimal("2.0")]
    assert airway_packet.numbers("352,0") == [Decimal("352.0")]
    assert airway_packet.numbers("1,936.00") == [Decimal("1936.00")]
    assert airway_packet.numbers("$11") == [Decimal("11")]
    assert airway_packet.awb_number("A.W.B.: 014 - 4275 7002") == "014-42757002"
    assert airway_packet.awb_number("> 014-42757002") == "014-42757002"
    assert airway_packet._clean_desc("|: SUGAR MANGOES.’ 24") == "SUGAR MANGOES"
    assert airway_packet._clean_desc("I SUGAR MANGOES") == "SUGAR MANGOES"  # OCR of "|:"


def test_packet_is_recognised():
    assert airway_packet.detect(B3_ROWS + INVOICE_ROWS)
    assert airway_packet.detect(CFIA_ROWS)
    assert not airway_packet.detect(INVOICE_ROWS)  # a plain invoice keeps the generic reader


def test_lines_come_from_the_cfia_confirmation_of_sale(db):
    header, vendor_id, lines, warnings = read(db, B3_ROWS, INVOICE_ROWS, CFIA_ROWS)
    assert len(lines) == 1
    ln = lines[0]
    assert ln["product_id"] == MANGO.id and ln["unit_code"] == "BOX"
    assert (ln["qty"], ln["unit_price"], ln["amount"]) == (
        Decimal("176"), Decimal("11.00"), Decimal("1936.00")
    )  # fmt: skip
    assert ln["status"] == "OK" and "HS 0804500010" in ln["raw_text"]
    v = {k: c["value"] for k, c in header.items()}
    assert v["document_no"] == "AFS 0101"
    assert v["document_date"] == "2026-11-07"  # not the 26/09/2025 authorization date
    assert v["due_date"] == "2026-11-22"  # credit 15 days
    assert v["currency"] == "CAD"
    assert v["awb_no"] == "014-12345678" and v["carrier"] == "Air Canada"
    assert v["arrival_date"] == "2026-11-08"
    assert v["total"] == "1936.00" and v["kind"] == "AIR_PACKET"
    assert vendor_id is None and "SUPPLIER_NOT_FOUND" in warnings
    assert header["supplier"]["value"].startswith("ANDES FRUIT")
    # the CFIA total was misread, but the vendor invoice total agrees with the lines
    assert "PACKET_TOTAL_MISMATCH" not in warnings and "PACKET_INVOICE_MISMATCH" not in warnings


def test_without_the_cfia_page_the_invoice_table_is_read_european_style(db):
    _, _, lines, warnings = read(db, B3_ROWS, INVOICE_ROWS)
    assert len(lines) == 1
    ln = lines[0]
    # "176 2,0 352,0 $11 1.936": 176 boxes at 11 = 1,936 (not 1.936)
    assert (ln["qty"], ln["unit_price"], ln["amount"]) == (
        Decimal("176"), Decimal("11"), Decimal("1936.00")
    )  # fmt: skip
    assert "NO_LINES" not in warnings


def test_words_run_together_and_scattered_totals(db):
    # Row shapes seen on a real scan: the CFIA description loses its space, the total sits on
    # a "Coût total tous produits" row, and the invoice table splits across rows.
    cfia = [
        r.replace("|: SUGAR MANGOES. 24 co", "SUGARMANGOES = co").replace(
            "Net Gross 4936.00", "commodities Cout total tous produits > 1936.00"
        )
        for r in CFIA_ROWS
    ]
    invoice = [r for r in INVOICE_ROWS if not r.startswith(("SUGAR", "TOTAL"))]
    cat = catalog()
    cat.by_name = {"sugar mango": MANGO}  # what Catalog keeps: normalized name -> product
    from pro_trading.supplier_ocr import _box, _val

    _, _, lines, warnings = airway_packet.read(
        db, uuid.uuid4(), rows(invoice + ["SUGAR MANGO 352,0 1.936"], cfia), cat,
        Decimal("0.9"), lambda **kw: kw, _val, _box,
    )  # fmt: skip
    assert lines[0]["product_id"] == MANGO.id
    assert "PACKET_TOTAL_MISMATCH" not in warnings


def test_disagreeing_papers_are_flagged(db):
    bad_cfia = [r.replace("176 11.00 1936.00", "170 11.00 1870.00") for r in CFIA_ROWS]
    _, _, lines, warnings = read(db, INVOICE_ROWS, bad_cfia)
    assert lines[0]["qty"] == Decimal("170")
    assert "PACKET_INVOICE_MISMATCH" in warnings


def _packet_pdf() -> bytes:
    pages = []
    for lines in (B3_ROWS, INVOICE_ROWS, CFIA_ROWS):
        pg = _Page()
        y = PAGE_H - 60
        for line in lines:
            pg.text(40, y, line, 11)
            y -= 24
        pages.append(pg)
    return _assemble(pages, "Synthetic airport packet")


@real
def test_scanned_packet_goes_to_receiving_with_its_airway_bill(owner, world, setup, tesseract):
    r = owner.post(
        f"{P}/products",
        json={"sku": "MNG", "name": "Sugar Mango", "stock_unit": "BOX", "unit_profiles": []},
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    v = owner.post(
        f"{P}/vendors", json={"code": "V-AFS", "name": "Andes Fruit SAS"}, headers=idem()
    )
    assert v.status_code == 201, v.text
    upload(owner, world, _packet_pdf(), ctype="application/pdf", job_type="VENDOR_DOCUMENT")
    assert drain(world) == 1
    items = owner.get(f"{P}/ocr/extractions").json()["items"]
    ex = owner.get(f"{P}/ocr/extractions/{items[0]['id']}").json()
    assert ex["vendor_id"] == v.json()["id"]  # "ANDES FRUIT S.A.S." matched "Andes Fruit SAS"
    h = {k: c["value"] for k, c in ex["header"].items()}
    assert (h["document_no"], h["awb_no"], h["kind"]) == ("AFS 0101", "014-12345678", "AIR_PACKET")
    [line] = ex["fields"]
    assert (line["read_qty"], line["read_unit_code"]) == ("176.0000", "BOX")
    r = resolve(owner, ex, line, "CONFIRM")
    assert r.status_code == 200, r.text
    ex = r.json()
    body = {"version": ex["version"], "vendor_id": v.json()["id"], "document_no": "AFS 0101",
            "reviewed_all": True}  # fmt: skip
    r = owner.post(f"{P}/ocr/extractions/{ex['id']}/to-receiving", json=body, headers=idem())
    assert r.status_code == 200, r.text
    shp = owner.get(f"{P}/shipments/{r.json()['shipment_id']}").json()
    assert shp["awb_no"] == "014-12345678" and shp["vendor_invoice_no"] == "AFS 0101"
    assert shp["documents"][0]["kind"] == "COMBINED"
    assert [(d["sku"], d["declared_qty"], d["unit_cost"]) for d in shp["declared"]] == [
        ("MNG", "176.0000", "11.0000")
    ]
