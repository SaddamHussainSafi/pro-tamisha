# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Security sweep 2026-11: OCR "to receiving" must stay inside the scan's warehouse."""

from .conftest import idem
from .test_ocr import resolve
from .test_supplier_ocr import (  # noqa: F401
    P,
    invoice_pdf,
    owner,
    read_one,
    real,
    scan,
    setup,
    tesseract,
    user,
    vendor,
)


@real
def test_to_receiving_refuses_another_warehouses_shipment(
    owner, world, setup, vendor, tesseract, tmp_path
):
    ex = read_one(owner, world, scan(invoice_pdf(no="ARX-9001"), tmp_path))  # warehouse A
    for f in ex["fields"]:
        ex = resolve(owner, ex, f, "CONFIRM").json()
    r = owner.post(
        f"{P}/shipments",
        json={"warehouse_id": str(world.wh_b), "vendor_id": vendor["id"]},
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    other = r.json()
    body = {
        "version": ex["version"],
        "vendor_id": vendor["id"],
        "shipment_id": other["id"],
        "document_no": "ARX-9001",
        "reviewed_all": True,
    }
    # grn.record is checked for warehouse A only; lines and the document used to land in B
    r = owner.post(f"{P}/ocr/extractions/{ex['id']}/to-receiving", json=body, headers=idem())
    assert r.status_code == 422 and r.json()["error"]["code"] == "WRONG_WAREHOUSE", r.text
    shp = owner.get(f"{P}/shipments/{other['id']}").json()
    assert shp["declared"] == [] and shp["documents"] == []
