# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Security sweep 2026-11: supplier payments, OCR into receiving, scan files, error detail."""

import io
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from pro_trading import documents, ocr, scanning
from pro_trading.config import get_settings
from pro_trading.errors import ApiError
from pro_trading.routes._common import content_disposition

from .conftest import idem
from .test_receiving import owner, setup  # noqa: F401
from .test_security import race
from .test_supplier_ledger import pay

P = "/api/v1"


def test_duplicate_bank_reference_cannot_race(owner, world, setup, make_api):
    # The duplicate check ran before the company lock, so two requests with the same reference
    # (two tabs, two finance users) both passed it and the payment was recorded twice.
    second = make_api()
    second.c.cookies.update(owner.c.cookies)
    second.csrf = owner.csrf
    r1, r2 = race(
        world.company_id,
        [
            lambda: pay(owner, setup["vendor"], "100.00", reference="WIRE-77"),
            lambda: pay(second, setup["vendor"], "100.00", reference="wire-77"),
        ],
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 409], (r1.text, r2.text)
    dup = r1 if r1.status_code == 409 else r2
    assert dup.json()["error"]["code"] == "DUPLICATE_REFERENCE"


# ------------------------------------------------------------------ file responses


def test_content_disposition_survives_any_filename():
    h = content_disposition("inline", 'Łódź "invoice"\r\n東京.pdf')
    h.encode("latin-1")  # header values must be Latin-1; this raised for the raw name
    assert h.startswith('inline; filename="')
    assert "\r" not in h and "\n" not in h and '\\"' not in h
    assert "filename*=UTF-8''%C5%81%C3%B3d%C5%BA" in h


def test_scan_with_a_non_latin_filename_can_be_opened(make_api, world):
    a = make_api()
    a.login(world.emails["sales"])
    pdf = b"%PDF-1.4\n% synthetic\n%%EOF\n"
    q = f"warehouse_id={world.wh_a}&job_type=SALES_ORDER&filename=Zamówienie-Łódź.pdf"
    r = a.c.post(
        f"{P}/scans/uploads?{q}",
        content=pdf,
        headers={**a._h(idem()), "Content-Type": "application/pdf"},
    )
    assert r.status_code == 201, r.text
    doc = r.json()["document_id"]
    c = a.get(f"{P}/scans/documents/{doc}/content")
    assert c.status_code == 200  # was a 500 (header not Latin-1 encodable)
    assert "filename*=UTF-8''" in c.headers["content-disposition"]


# ------------------------------------------------------------------ decompression bombs


def _png(w: int, h: int) -> bytes:
    out = io.BytesIO()
    Image.new("1", (w, h)).save(out, format="PNG")  # a few KB on disk, w*h pixels decoded
    return out.getvalue()


def test_viewer_refuses_huge_images_before_decoding():
    big = _png(9000, 5000)  # 45 M pixels
    assert len(big) < 200_000
    with pytest.raises(ApiError) as e:
        scanning.render_page(big, "image/png", 1)
    assert e.value.code == "IMAGE_TOO_LARGE"
    png, pages = scanning.render_page(_png(200, 100), "image/png", 1)
    assert pages == 1 and png.startswith(b"\x89PNG")


def test_ocr_refuses_huge_images_before_decoding(tmp_path: Path):
    img = tmp_path / "in.png"
    img.write_bytes(_png(9000, 5000))
    with pytest.raises(ocr.ProviderError) as e:
        ocr._prepare(img, 0)
    assert e.value.code == "TOO_LARGE"


def _pdf_with_page(w: int, h: int, crop: int | None = None) -> bytes:
    box = b" /CropBox [0 0 %d %d]" % (crop, crop) if crop else b""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d]%s >>" % (w, h, box),
    ]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, start=1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


@pytest.mark.skipif(not Path("/opt/homebrew/bin/pdfinfo").exists() and not Path(
    "/usr/bin/pdfinfo").exists(), reason="no poppler")  # fmt: skip
def test_huge_pdf_pages_are_refused():
    huge = _pdf_with_page(14400, 14400)  # 200 x 200 inches: ~484 M pixels at 110 dpi
    with pytest.raises(ApiError) as e:
        scanning.render_page(huge, "application/pdf", 1)
    assert e.value.code == "IMAGE_TOO_LARGE"
    with pytest.raises(ocr.ProviderError) as pe:
        ocr.tesseract_provider(huge, "application/pdf")
    assert pe.value.code == "TOO_LARGE"
    _, pages = scanning.render_page(_pdf_with_page(612, 792), "application/pdf", 1)
    assert pages == 1


# ------------------------------------------------------------------ error detail


def test_archive_write_errors_do_not_expose_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "drive_local_root", str(tmp_path))
    f = SimpleNamespace(
        id=uuid.uuid4(), company_id=uuid.uuid4(), folder="2026/Invoices", filename="x.pdf",
        sha256="0" * 64,
    )  # fmt: skip

    def boom(path, *_a, **_k):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(documents, "open", boom, raising=False)
    with pytest.raises(documents.DriveError) as e:
        documents.local_provider(f, b"data")  # type: ignore[arg-type]
    assert e.value.code == "DRIVE_IO"
    assert str(tmp_path) not in e.value.message  # stored in last_error, shown in the UI


def test_document_ai_setup_errors_do_not_expose_paths(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(
        s, "ocr_docai_endpoint", "https://eu-documentai.googleapis.com/v1/x:process"
    )
    monkeypatch.setattr(s, "ocr_docai_credentials_file", "/srv/secret/service-account.json")
    with pytest.raises(ocr.ProviderError) as e:
        ocr.document_ai_provider(b"%PDF-1.4", "application/pdf")
    assert e.value.code == "OCR_NOT_CONFIGURED"
    assert "/srv/secret" not in e.value.detail


# ------------------------------------------------------------------ OCR number ranges


def _row(*words: str):
    from decimal import Decimal

    from pro_trading.ocr import Token

    return [
        Token(w, 1, Decimal(i) / 10, Decimal("0.1"), Decimal(i + 1) / 10, Decimal("0.2"),
              Decimal("0.99"), False)
        for i, w in enumerate(words)
    ]  # fmt: skip


def test_ocr_money_refuses_absurd_numbers():
    from pro_trading.supplier_ocr import money

    assert money("1" * 19) is None  # used to overflow Numeric(18,4): the read retried forever
    assert money("999999999999.00") == money("999,999,999,999.00") is not None
    assert money("1,000,000,000,000") is None
    assert money("(5.00)") < 0  # credits on headers still read


def test_negative_line_numbers_are_left_for_a_person(db):
    from decimal import Decimal

    from pro_trading import supplier_ocr

    cat = SimpleNamespace(match=lambda _s: None, by_alias={})
    got = supplier_ocr.read(
        db, uuid.uuid4(), [_row("Yellow", "yam", "-4", "12.50", "-50.00")], cat,
        Decimal("0.9"), lambda **kw: kw,
    )  # fmt: skip
    line = got.proposals[0]
    # before: qty -4 was proposed and, once confirmed, hit the DB check as a "DUPLICATE" 409
    assert line["qty"] is None and line["unit_price"] == Decimal("12.50")
    assert any(w.startswith("LINE_NEGATIVE") for w in got.warnings)


@pytest.mark.skipif(not Path("/opt/homebrew/bin/pdfinfo").exists() and not Path(
    "/usr/bin/pdfinfo").exists(), reason="no poppler")  # fmt: skip
def test_pdf_guard_cannot_be_dodged():
    # pdfinfo reports the CropBox but pdftoppm drew the MediaBox: a 100 pt crop over a
    # 5000 pt page passed the check and then rendered 7639 x 7639 pixels.
    png, _ = scanning.render_page(_pdf_with_page(5000, 5000, crop=100), "application/pdf", 1)
    with Image.open(io.BytesIO(png)) as im:
        assert max(im.size) < 400
    # pdfinfo prints 1e+06 for huge pages; that line used to be skipped, so nothing was checked
    with pytest.raises(ApiError) as e:
        scanning.render_page(_pdf_with_page(1_000_000, 1_000_000), "application/pdf", 1)
    assert e.value.code == "IMAGE_TOO_LARGE"
    # pdfinfo can't read it: refuse instead of rendering blind
    with pytest.raises(ApiError) as e:
        scanning.render_page(b"%PDF-1.4 not really", "application/pdf", 1)
    assert e.value.code == "UNREADABLE"
    with pytest.raises(ocr.ProviderError) as pe:
        ocr.tesseract_provider(b"%PDF-1.4 not really", "application/pdf")
    assert pe.value.code == "UNREADABLE"
