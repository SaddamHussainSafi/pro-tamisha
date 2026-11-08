# ruff: noqa: F811, S603, S607 - fixtures re-used by name; test-only fixed tools
"""Real OCR (Tesseract) on image-only documents: the order sheet is printed to PDF, rasterized
to a 300 dpi PNG (no text layer, like a scan), optionally turned 90 degrees, then read. Accuracy
is measured per expected line (product + unit + quantity), not assumed."""

import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import idem
from .test_ocr import drain, only, owner, setup, sheet, upload, user  # noqa: F401

pytestmark = pytest.mark.skipif(
    not (shutil.which("tesseract") and shutil.which("pdftoppm")),
    reason="tesseract / pdftoppm not installed",
)
FILLS = {"CAS-01": "15", "PLN-01": "20", "YAM-01": "12"}
EXPECT = {("CAS-01", "BOX", "15.0000"), ("PLN-01", "PIECE", "20.0000"), ("YAM-01", "LB", "12.0000")}


def as_scan(pdf: bytes, tmp: Path, rotate: int = 0) -> bytes:
    (tmp / "s.pdf").write_bytes(pdf)
    subprocess.run(
        ["pdftoppm", "-r", "300", "-png", "-singlefile", str(tmp / "s.pdf"), str(tmp / "s")],
        check=True,
    )
    out = tmp / "s.png"
    if rotate:
        if not shutil.which("sips"):
            pytest.skip("no image rotation tool")
        subprocess.run(["sips", "-r", str(rotate), str(out)], check=True, capture_output=True)
    return out.read_bytes()


def read(user, world, monkeypatch, png: bytes):
    monkeypatch.setenv("OCR_PROVIDER", "tesseract")
    from pro_trading.config import get_settings

    get_settings.cache_clear()
    try:
        sales = user("sales")
        upload(sales, world, png, ctype="image/png")
        assert drain(world) == 1
        ex = only(sales)
    finally:
        monkeypatch.setenv("OCR_PROVIDER", "fake")
        get_settings.cache_clear()
    got = {(f["read_sku"], f["read_unit_code"], f["read_qty"]) for f in ex["fields"]}
    hits = len(EXPECT & got)
    print(
        f"tesseract: {hits}/{len(EXPECT)} exact",
        sorted(got - EXPECT, key=str),
        ex["warnings"],
        [b["text"] for b in ex.get("text_blocks", []) if "lant" in b["text"]],
    )
    return ex, hits


@pytest.mark.parametrize("rotate", [0, 90])
def test_scanned_image_is_read(user, world, setup, monkeypatch, tmp_path, rotate):
    ex, hits = read(user, world, monkeypatch, as_scan(sheet(world, FILLS), tmp_path, rotate))
    assert ex["provider"] == "tesseract"
    assert ex["customer_name"] == "Island Grocers"  # header read from pixels
    assert hits == len(EXPECT)
    pln = next(f for f in ex["fields"] if f["read_sku"] == "PLN-01")
    if "PLN-01" not in [b["text"].split()[0] for b in ex["text_blocks"] if b["text"]]:
        assert pln["status"] == "LOW_CONFIDENCE"  # code was corrected from a misread: review it
    for f in ex["fields"]:
        assert f["confidence"] is None or 0 <= float(f["confidence"]) <= 1


def test_damaged_image_fails_cleanly(user, world, setup, monkeypatch):
    """A file that claims to be PNG but isn't: refused at upload or failed by the engine; never
    any proposed line."""
    monkeypatch.setenv("OCR_PROVIDER", "tesseract")
    from pro_trading.config import get_settings

    get_settings.cache_clear()
    try:
        sales = user("sales")
        r = sales.c.post(
            "/api/v1/scans/uploads",
            params={
                "warehouse_id": str(world.wh_a),
                "job_type": "SALES_ORDER",
                "filename": "x.png",
            },
            content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
            headers={**sales._h(idem()), "Content-Type": "image/png"},
        )
        if r.status_code < 300:
            drain(world)
            items = sales.get("/api/v1/ocr/extractions").json()["items"]
            assert items and all(
                x["status"] == "FAILED" or x.get("open_count", 0) == 0 for x in items
            )
        else:
            assert r.status_code in (415, 422)
    finally:
        monkeypatch.setenv("OCR_PROVIDER", "fake")
        get_settings.cache_clear()
