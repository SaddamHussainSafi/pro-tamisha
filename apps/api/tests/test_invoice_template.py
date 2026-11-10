# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Sales invoice template (D-12) and invoice numbering (TT2026-000001). Synthetic data only.

Renderer: deterministic bytes, black and white only (grey fills, greyscale logo), one receiver
signature, no S/N column, case/weight columns from the snapshot. Numbering: prefix + year +
6-digit yearly sequence. Issue: letterhead, bill-to / ship-to, vendor no., salesperson and
customer PO are copied into the snapshot and printed."""

import re
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import update

from pro_trading import invoice_pdf, invoicing
from pro_trading.db import get_sessionmaker
from pro_trading.models import Company, Invoice, InvoiceDocument, InvoiceSequence

from .conftest import idem
from .test_invoicing import movements, sales, stock, wh  # noqa: F401
from .test_orders import P, owner, setup, user  # noqa: F401


def line(n, desc, qty, pack, rate, unit="BOX", **kw):
    amount = (Decimal(qty) * Decimal(rate)).quantize(Decimal("0.01"))
    return {
        "line_no": n,
        "sku": "SYN",
        "description": desc,
        "unit_code": unit,
        "cases": qty if unit == "BOX" else kw.get("cases"),
        "case_weight_lb": kw.get("case_weight_lb"),
        "billed_qty": qty,
        "unit_rate": rate,
        "line_amount": kw.get("amount", str(amount)),
        "pack_lb": pack,
    }


def snapshot(**kw):
    lines = kw.pop(
        "lines",
        [
            line(1, "Synthetic Banana", "8", "40.0000", "29.00"),
            line(2, "Synthetic Papaya", "12", "32.0000", "42.00"),
            line(3, "Synthetic Thyme - Packaged", "1", "4.0000", "48.00"),
        ],
    )
    total = sum(Decimal(x["line_amount"]) for x in lines)
    return {
        "invoice_no": "TT2026-000042",
        "issued_date": "2026-09-24",
        "currency": "CAD",
        "company": {
            "legal_name": "Synthetic Foods Ltd",
            "address": "1 Example Street, Unit 2\nToronto, ON  A1A 1A1",
            "phone": "555-0100",
            "fax": "555-0101",
            "email": "office@example.test",
            "business_no": "000111222",
        },
        "customer": {
            "name": "Synthetic Grocers #12",
            "bill_to_address": "9 Sample Road\nBrampton, ON  B2B 2B2",
            "ship_to_name": None,
            "ship_to_address": None,
            "phone": "555-0199",
            "fax": None,
            "vendor_no": "7654321",
            "payment_terms_days": 30,
        },
        "salesperson": "Sam Synthetic",
        "customer_po": "PO-77",
        "lines": lines,
        "subtotal": str(total),
        "tax_amount": "0.00",
        "total": str(total),
        "note": "",
        **kw,
    }


TEXT = re.compile(rb"\((?:\\.|[^\\)])*\)")


def streams(pdf: bytes) -> bytes:
    """The page content streams (not the logo's image data)."""
    return b"\n".join(re.findall(rb"<< /Length \d+ >>\nstream\n(.*?)\nendstream", pdf, re.S))


def page_ops(pdf: bytes) -> bytes:
    """Page content with the text strings removed (operators and numbers only)."""
    return TEXT.sub(b"()", streams(pdf))


def shown(pdf: bytes) -> list[str]:
    return [
        m.decode("cp1252").replace("\\(", "(").replace("\\)", ")")
        for m in re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", streams(pdf))
    ]


# ---------------------------------------------------------------- renderer


def test_same_snapshot_gives_same_bytes():
    a = invoice_pdf.render(snapshot())
    assert a == invoice_pdf.render(snapshot())
    assert a != invoice_pdf.render(snapshot(invoice_no="TT2026-000043"))
    assert b"/Producer (Pro Trading sales-invoice-1)" in a
    assert invoice_pdf.TEMPLATE_VERSION == "sales-invoice-1"


def test_black_and_white_only():
    pdf = invoice_pdf.render(snapshot())
    ops = set(re.findall(rb"(?<![\w/])([A-Za-z]+)(?![\w])", page_ops(pdf)))
    colour = {b"rg", b"RG", b"k", b"K", b"cs", b"CS", b"sc", b"SC", b"scn", b"SCN", b"sh"}
    assert not ops & colour, ops & colour
    greys = [float(g) for g in re.findall(rb"([\d.]+) g\b", page_ops(pdf))]
    assert greys and all(0 <= g <= 1 for g in greys)
    # The logo is the only image: a greyscale JPEG.
    assert pdf.count(b"/Subtype /Image") == 1
    assert b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /DCTDecode" in pdf
    data, w, h = invoice_pdf.logo()
    assert data[:2] == b"\xff\xd8" and w > h > 0
    assert invoice_pdf._jpeg_info(data)[2] == 1  # one channel


def test_layout_follows_the_owner_design():
    words = shown(invoice_pdf.render(snapshot()))
    # header
    for t in ("SALES INVOICE", "Page: 1 of 1", "TT2026-000042", "24-09-2026", "000111222"):
        assert t in words, t
    assert "7654321" in words  # Vendor # from the customer
    assert {"Tel:", "Fax:", "Email:", "555-0100", "555-0101", "office@example.test"} <= set(words)
    # bill to / ship to (blank ship-to = bill-to), salesperson strip
    assert words.count("Synthetic Grocers #12") == 2 and words.count("9 Sample Road") == 2
    assert {"Sam Synthetic", "PO-77", "Net 30 Days"} <= set(words)
    # line table: no S/N column, Qty is the first column
    head = ["Qty", "Units", "Description", "LBS", "KG", "Unit Price", "Amount"]
    i = words.index("Qty")
    assert words[i : i + len(head)] == head
    assert "#" not in words and "S/N" not in words
    i = words.index("Synthetic Banana")
    assert words[i - 2 : i + 5] == ["8", "CASE-40", "Synthetic Banana", "320.00", "145.15",
                                    "29.00", "232.00"]  # fmt: skip
    # totals: quantity and weight; notes; amounts; one signature (the receiver's)
    assert "21" in words and "708.00 LBS / 321.14 KG" in words
    for t in (
        "No claims after 24 hours on fruits and vegetables.",
        "Please verify all items and quantities upon receipt.",
        "Thank you for your business.",
        "Gross Amount",
        "Tax Amount",
        "Net Amount",
        "$784.00",
        "$0.00",
    ):
        assert t in words, t
    assert [w for w in words if "Signature" in w] == ["Received By (Name & Signature)"]
    assert not any("Tamisha" in w or "Customer's" in w for w in words)


def test_blank_fields_print_a_dash_and_old_snapshots_still_render():
    s = snapshot(customer_po="", salesperson="")
    s["customer"] |= {"vendor_no": None, "payment_terms_days": 0}
    words = shown(invoice_pdf.render(s))
    assert words.count("—") >= 3 and "Due on receipt" in words
    old = {"invoice_no": "INV-2026-000001", "lines": [line(1, "Old", "1", None, "5.00")],
           "subtotal": "5.00", "tax_amount": "0.00", "total": "5.00"}  # fmt: skip
    assert "CASE" in shown(invoice_pdf.render(old))


def test_weighed_kg_and_piece_rows():
    rv = invoice_pdf.row_values
    # weighed boxes billed per lb (INV-006 shape): cases + entered weight, price per lb
    w = rv(line(1, "Cassava", "40.0000", "20.0000", "1.5000", unit="LB", cases="2.0000",
                case_weight_lb="20.0000"))  # fmt: skip
    assert (w["qty_text"], w["units"], w["lbs"], w["price"], w["amount"]) == (
        "2", "CASE-20", Decimal("40.0000"), "1.50/lb", "60.00"
    )  # fmt: skip
    assert w["counted"] and w["kg"] == Decimal("40.0000") * invoice_pdf.LB_TO_KG
    kg = rv(line(2, "Bulk", "10.0000", None, "3.00", unit="KG"))
    assert (kg["units"], kg["counted"], kg["kg"], kg["lbs"]) == (
        "KG", False, Decimal("10.0000"), Decimal("22.0462")
    )  # fmt: skip
    pc = rv(line(3, "Plantain", "20.0000", None, "0.4500", unit="PIECE"))
    assert (pc["units"], pc["lbs"], pc["price"]) == ("PIECE", None, "0.45")
    box = rv(line(4, "No size yet", "3.0000", None, "10.00"))
    assert (box["units"], box["lbs"], box["kg"]) == ("CASE", None, None)
    assert rv(line(5, "Odd rate", "1", None, "0.4375"))["price"] == "0.4375"


def test_long_text_is_cut_to_its_box():
    s = snapshot(lines=[line(1, "Very long synthetic product name " * 6, "1", "10", "1.00")])
    s["customer"]["name"] = "Synthetic Customer With A Very Long Registered Name " * 3
    words = shown(invoice_pdf.render(s))
    cut = [w for w in words if w.endswith("…")]
    assert len(cut) == 3  # description + bill-to and ship-to names
    assert all(invoice_pdf.text_width(w, 9.5) <= 250 for w in cut)


# ---------------------------------------------------------------- numbering


def test_invoice_number_format():
    assert invoicing.invoice_number("TT", 2026, 1) == "TT2026-000001"
    assert invoicing.invoice_number("TT", 2026, 44_000) == "TT2026-044000"  # ~120 a day
    assert invoicing.invoice_number("TT", 2027, 999_999) == "TT2027-999999"
    assert len(invoicing.invoice_number("ABCDEFGH", 2026, 999_999)) <= 32  # invoice_no column


def test_numbering_continues_the_years_sequence(owner, wh, world, setup):
    """Invoices issued before the change keep their numbers (INV-2026-000041 style); the
    year's sequence carries on under the new prefix, so (year, seq_no) stays unique."""
    stock(owner, world, setup, "CAS", "BOX", "4")
    first = _direct(owner, world, setup, "1")
    assert re.fullmatch(r"TT\d{4}-\d{6}", first["invoice_no"])
    year = int(first["invoice_no"][2:6])
    with get_sessionmaker()() as db, db.begin():
        inv = db.get(Invoice, uuid.UUID(first["id"]))
        assert inv is not None and (inv.fiscal_year, inv.invoice_no[-6:]) == (
            year, f"{inv.seq_no:06d}"
        )  # fmt: skip
        db.execute(  # as if 41 invoices had been numbered INV-{year}-0000NN this year
            update(InvoiceSequence)
            .where(InvoiceSequence.company_id == world.company_id,
                   InvoiceSequence.fiscal_year == year)
            .values(last_no=41)
        )  # fmt: skip
    second = _direct(owner, world, setup, "1")
    assert second["invoice_no"] == f"TT{year}-000042"


# ---------------------------------------------------------------- issue end to end


def _direct(api, world, s, qty, **kw):
    body = {
        "customer_id": s["cust"],
        "warehouse_id": str(world.wh_a),
        "lines": [{"product_id": s["p"]["CAS"], "unit_code": "BOX", "qty": qty}],
        "dispatch_confirmed": True,
        **kw,
    }
    r = api.post(f"{P}/invoices/direct", json=body, headers=idem())
    assert r.status_code == 201, r.text
    return r.json()


def test_issue_prints_customer_record_and_company_defaults(owner, wh, world, setup):
    with get_sessionmaker()() as db, db.begin():
        db.execute(
            update(Company)
            .where(Company.id == world.company_id)
            .values(business_no="000111222", default_salesperson="Sam Synthetic",
                    address="1 Example Street\nToronto, ON", phone="555-0100")
        )  # fmt: skip
    c = owner.get(f"{P}/customers/{setup['cust']}").json()
    r = owner.patch(
        f"{P}/customers/{setup['cust']}",
        json={
            "version": c["version"],
            "bill_to_address": "  9 Sample Road \n\n Brampton, ON  B2B 2B2 ",
            "ship_to_name": "Synthetic Grocers Dock",
            "vendor_no": "7654321",
            "fax": "555-0198",
        },
        headers=idem(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["bill_to_address"] == "9 Sample Road\nBrampton, ON  B2B 2B2"
    settings = owner.get(f"{P}/invoice-settings").json()
    assert (settings["default_salesperson"], settings["business_no"]) == (
        "Sam Synthetic", "000111222"
    )  # fmt: skip
    assert settings["invoice_prefix"] == "TT"

    stock(owner, world, setup, "CAS", "BOX", "3")
    a = _direct(owner, world, setup, "1")  # salesperson left blank -> company default
    b = _direct(owner, world, setup, "1", salesperson="Pat Synthetic", customer_po="PO-9")
    with get_sessionmaker()() as db:
        snaps = {i: db.get(Invoice, uuid.UUID(x["id"])) for i, x in (("a", a), ("b", b))}
        assert snaps["a"] is not None and snaps["b"] is not None
        sa, sb = snaps["a"].snapshot, snaps["b"].snapshot
        assert (sa["salesperson"], sa["customer_po"]) == ("Sam Synthetic", "")
        assert (sb["salesperson"], sb["customer_po"]) == ("Pat Synthetic", "PO-9")
        assert sa["company"]["business_no"] == "000111222"
        assert sa["customer"]["vendor_no"] == "7654321"
        assert snaps["a"].template_version == "sales-invoice-1"
        pdf = db.query(InvoiceDocument).filter_by(invoice_id=snaps["b"].id).one().content
    words = shown(pdf)
    assert {"Pat Synthetic", "PO-9", "7654321", "000111222", "555-0198"} <= set(words)
    assert {"Synthetic Grocers Dock", "9 Sample Road", "Brampton, ON  B2B 2B2"} <= set(words)
    # stored bytes are what the PDF endpoint serves
    r = owner.get(f"{P}/invoices/{b['id']}/pdf")
    assert r.status_code == 200 and r.content == pdf
    n, _ = movements(b["id"])
    assert n == 1


@pytest.mark.parametrize("bad", ["x" * 101])
def test_salesperson_length_is_checked(owner, world, setup, bad):
    r = owner.post(
        f"{P}/invoices/direct/preview",
        json={
            "customer_id": setup["cust"],
            "warehouse_id": str(world.wh_a),
            "lines": [{"product_id": setup["p"]["CAS"], "unit_code": "BOX", "qty": "1"}],
            "salesperson": bad,
        },
    )
    assert r.status_code == 422


def test_new_company_gets_the_letterhead(db):
    """A company created by create_super_admin (fresh production database) prints Tamisha's
    letterhead; migration 0021 only filled companies that already existed."""
    from pro_trading.bootstrap import LETTERHEAD, apply_letterhead

    co = Company(legal_name=f"Synthetic Letterhead {uuid.uuid4().hex[:6]}", phone="555-0100")
    apply_letterhead(co)
    assert co.phone == "555-0100"  # what was set stays
    assert (co.business_no, co.default_salesperson) == (
        LETTERHEAD["business_no"], LETTERHEAD["default_salesperson"]
    )  # fmt: skip
