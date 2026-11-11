# ruff: noqa: F811 - fixtures re-used from test_orders
"""Packing sheet (owner, 2026-11-11): the picking attempt printed as the office PACKING LIST.

Synthetic data only. The PDF holds 2 copies by default, each with all its pages; the header and
column titles repeat per page; UNITS follow the invoice's case-size rule; the QR is the pick code.
"""

import re
from decimal import Decimal

import pytest

from pro_trading import packing_pdf, qr

from .conftest import idem
from .test_orders import (  # noqa: F401 - fixtures re-used
    P,
    confirmed,
    open_pick,
    owner,
    record,
    setup,
    user,
)


def pages(pdf: bytes) -> int:
    m = re.search(rb"/Type /Pages /Kids \[[^\]]*\] /Count (\d+)", pdf)
    assert m
    return int(m.group(1))


def texts(pdf: bytes) -> list[str]:
    raw = re.findall(rb"\(((?:[^()\\]|\\.)*)\) Tj", pdf)
    return [re.sub(rb"\\(.)", rb"\1", t).decode("cp1252") for t in raw]


def sheet(n: int, **kw):
    lines = [
        {
            "product_name": f"Synthetic product {i + 1}",
            "unit_code": "BOX",
            "requested_qty": "2.0000",
            "units": "CASE-40",
            "lot_code": None,
            "remarks": "",
        }
        for i in range(n)
    ]
    return {
        "pick_code": "SO-20261111-0001-P1",
        "order_no": "SO-20261111-0001",
        "order_date": "2026-11-11",
        "customer_name": "Synthetic Grocer",
        "picked_by_name": None,
        "lines": lines,
        **kw,
    }


# ---------------------------------------------------------------- API


def test_sheet_pdf_has_paper_columns_and_two_copies(owner, user, world, setup):
    sales, wh = user("sales"), user("wh_a")
    body = {
        "customer_id": setup["cust"],
        "warehouse_id": str(world.wh_a),
        "source_text": "synthetic",
        "lines": [
            {"product_id": setup["p"]["CAS"], "unit_code": "BOX", "qty": "3", "note": "coming"},
            {"product_id": setup["p"]["PLN"], "unit_code": "PIECE", "qty": "20"},
            {"product_id": setup["p"]["CAS"], "unit_code": "LB", "qty": "12.5"},
        ],
    }
    r = sales.post(f"{P}/sales-orders", json=body, headers=idem())
    assert r.status_code == 201, r.text
    o = r.json()
    o = sales.post(
        f"{P}/sales-orders/{o['id']}/confirm", json={"version": o["version"]}, headers=idem()
    ).json()
    pk = open_pick(sales, o).json()

    # The pick carries what the sheet prints
    assert [x["units"] for x in pk["lines"]] == ["CASE-40", "PIECE", "LB"]
    assert [x["remarks"] for x in pk["lines"]] == ["coming", "", ""]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", pk["order_date"])

    r = wh.get(f"{P}/pick-attempts/{pk['id']}/sheet.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "no-store"
    assert f"Packing-sheet-{pk['pick_code']}.pdf" in r.headers["content-disposition"]
    pdf = r.content
    assert pdf.startswith(b"%PDF-1.4") and pages(pdf) == 2  # 1 page x 2 copies
    t = texts(pdf)
    for s in ("TAMISHA TRADING INC.", "PACKING LIST", "Sales Order Number", "CUSTOMER NAME :",
              "EMPLOYEE NAME :", "MISSING GOODS :", "CHK", "QTY", "UNITS", "PRODUCT",
              "Batch Number", "REMARKS", "Total", "Island Grocers", o["order_no"]):  # fmt: skip
        assert t.count(s) == 2, s  # once per copy
    assert t.count("CASE-40") == t.count("PIECE") == 2
    assert t.count("coming") == 2
    assert t.count("23") == 2  # total: 3 boxes + 20 pieces; the 12.5 LB is by weight
    assert t.count("+ 12.5 LB by weight") == 2
    assert t.count("Page 1 of 1") == 2
    assert t.count(f"Packing sheet {pk['pick_code']}") == 2

    # Deterministic; copies 1..4
    assert wh.get(f"{P}/pick-attempts/{pk['id']}/sheet.pdf").content == pdf
    one = wh.get(f"{P}/pick-attempts/{pk['id']}/sheet.pdf?copies=1")
    assert one.status_code == 200 and pages(one.content) == 1
    assert wh.get(f"{P}/pick-attempts/{pk['id']}/sheet.pdf?copies=0").status_code == 422
    assert wh.get(f"{P}/pick-attempts/{pk['id']}/sheet.pdf?copies=5").status_code == 422

    # Recording packed quantities does not change the paper form's columns
    r = record(wh, pk, [(0, "3", None)])
    assert r.status_code == 200, r.text
    assert r.json()["lines"][0]["units"] == "CASE-40"


def test_sheet_pdf_needs_read_access_to_the_warehouse(owner, user, world, setup, make_api):
    sales = user("sales")
    o = confirmed(sales, world, setup, [("CAS", "BOX", "1")], wh=world.wh_b)
    pk = open_pick(sales, o).json()
    assert user("wh_a").get(f"{P}/pick-attempts/{pk['id']}/sheet.pdf").status_code in (403, 404)
    anon = make_api()
    assert anon.get(f"{P}/pick-attempts/{pk['id']}/sheet.pdf").status_code == 401
    assert owner.get(f"{P}/pick-attempts/{pk['id']}/sheet.pdf").status_code == 200


# ---------------------------------------------------------------- renderer


@pytest.mark.parametrize(
    ("lines", "per_copy"),
    [(1, 1), (29, 1), (30, 2), (45, 2), (67, 2), (68, 3), (120, 4)],
)
def test_every_line_prints_on_as_many_pages_as_needed(lines, per_copy):
    pdf = packing_pdf.render(sheet(lines))
    assert pages(pdf) == per_copy * 2
    t = texts(pdf)
    for i in range(lines):
        assert t.count(f"Synthetic product {i + 1}") == 2
    assert t.count("Total") == 2
    # Header and column titles on every page; "Page n of m" per copy
    assert t.count("PACKING LIST") == 2
    assert t.count("PACKING LIST (continued)") == 2 * (per_copy - 1)
    assert t.count("Batch Number") == t.count("Sales Order Number") == per_copy * 2
    for n in range(1, per_copy + 1):
        assert t.count(f"Page {n} of {per_copy}") == 2
    assert t.count(str(2 * lines)) >= 2  # total quantity


def test_render_is_deterministic_and_copies_are_identical():
    s = sheet(40, picked_by_name="Synthetic Picker")
    a, b = packing_pdf.render(s), packing_pdf.render(s)
    assert a == b
    streams = re.findall(rb"stream\n(.*?)\nendstream", a, re.S)
    assert len(streams) == 4 and streams[:2] == streams[2:]
    assert texts(a).count("Synthetic Picker") == 2  # EMPLOYEE NAME on page 1 of each copy
    with pytest.raises(ValueError):
        packing_pdf.render(s, copies=0)


def test_units_label_follows_the_invoice_case_size_rule():
    assert packing_pdf.units_label("BOX", Decimal("40.0000")) == "CASE-40"
    assert packing_pdf.units_label("BOX", Decimal("12.5")) == "CASE-12.5"
    assert packing_pdf.units_label("BOX", None) == "CASE"
    assert packing_pdf.units_label("PIECE", Decimal("50")) == "PIECE-50"
    assert packing_pdf.units_label("PIECE", None) == "PIECE"
    assert packing_pdf.units_label("LB", None) == "LB"
    assert packing_pdf.units_label("KG", Decimal("3")) == "KG"


def test_long_text_is_cut_to_its_column():
    s = sheet(1)
    s["lines"][0]["product_name"] = "A very long synthetic product name " * 4
    s["lines"][0]["remarks"] = "Remark " * 20
    t = texts(packing_pdf.render(s, copies=1))
    prod = next(x for x in t if x.startswith("A very long"))
    assert prod.endswith("…") and len(prod) < 60


# ---------------------------------------------------------------- QR code

# Reference matrix from Project Nayuki's QR generator (the library behind the web app's QR
# code), byte mode, level M, mask 4 - checked against all 8 masks while writing qr.py.
PICK_QR = (
    "1111111010111100101111111",
    "1000001001110110101000001",
    "1011101001110001001011101",
    "1011101011101100101011101",
    "1011101010010101001011101",
    "1000001011100110101000001",
    "1111111010101010101111111",
    "0000000010101011100000000",
    "1000101111100111111111001",
    "0011010110010011010111100",
    "0111111111010111110100110",
    "0010010111001101100001101",
    "0101001101111100101001110",
    "1100010111000111010110001",
    "0001101010000001110101000",
    "0011110110001011100001101",
    "1101001010010110111111101",
    "0000000010101001100010011",
    "1111111010011110101011100",
    "1000001000110101100011101",
    "1011101010001100111111101",
    "1011101000100110001000011",
    "1011101001100001111001010",
    "1000001000001010001011110",
    "1111111011010110011011111",
)


def test_qr_matches_the_reference_encoder():
    m = qr.encode("SO-20261111-0009-P1")
    assert len(m) == 25  # version 2
    rows = ["".join("1" if v else "0" for v in r) for r in m]
    assert tuple(rows) == PICK_QR
    assert qr.encode("SO-20261111-0009-P1") == m
    with pytest.raises(ValueError):
        qr.encode("X" * 200)
