# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Module 09 documents: archive index, Drive sync with failure injection, POD, immutability.

Synthetic data only. Drive is the local provider rooted in a temp dir per test.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading import documents, invoice_pdf, outbox
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker

from .conftest import idem
from .test_invoicing import issue, sales, stock, wh  # noqa: F401 - fixtures re-used
from .test_orders import P, code, confirmed, owner, picked, setup, user  # noqa: F401

POD = b"%PDF-1.4\n% synthetic signed proof of delivery\n%%EOF\n"


@pytest.fixture(autouse=True)
def drive_root(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "drive_provider", "local")
    monkeypatch.setattr(s, "drive_local_root", str(tmp_path))
    monkeypatch.setattr(s, "drive_fault", "")
    return tmp_path


def _sync():
    with get_sessionmaker()() as db:
        return outbox.dispatch_pending(db, {documents.DRIVE_EVENT: documents.handle_sync})


def _invoice(owner, sales, wh, world, setup, qty="1"):
    stock(owner, world, setup, "CAS-01", "BOX", "10")
    o = confirmed(sales, world, setup, [("CAS-01", "BOX", qty)])
    pk = picked(sales, wh, o, [(0, qty, None)], verifier=owner)
    r = issue(owner, pk)
    assert r.status_code == 201, r.text
    return r.json()


def _files(api, **params):
    r = api.get(f"{P}/documents", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _snapshot(n):
    return {
        "invoice_no": "INV-2026-000999",
        "issued_date": "2026-01-05",
        "due_date": "2026-02-04",
        "currency": "CAD",
        "company": {"legal_name": "Synthetic Foods Ltd"},
        "customer": {"name": "Island Grocers", "external_ref": "C-100", "payment_terms_days": 30},
        "order": {"order_no": "SO-1"},
        "pick_code": "PK-1",
        "warehouse_code": "WH-A",
        "lines": [
            {
                "line_no": i,
                "sku": f"SKU-{i:03}",
                "description": "Synthetic product",
                "billed_qty": "1",
                "unit_code": "BOX",
                "unit_rate": "1.00",
                "line_amount": "1.00",
            }
            for i in range(1, n + 1)
        ],
        "subtotal": f"{n}.00",
        "tax_amount": "0.00",
        "total": f"{n}.00",
        "tax_note": "",
        "note": "",
    }


# ---------------------------------------------------------------- acceptance


@pytest.mark.parametrize(("n", "pages"), [(1, 1), (30, 1), (31, 2), (50, 2), (61, 3)])
def test_long_invoice_paginates(n, pages):
    pdf = invoice_pdf.render(_snapshot(n))
    assert f"/Count {pages} >>".encode() in pdf
    assert f"Page {pages} of {pages}".encode() in pdf
    assert b"SKU-%03d" % n in pdf  # last line is printed
    assert pdf.count(b"Subtotal") == 1  # totals once, on the last page


def test_issue_files_invoice_and_syncs_once(owner, sales, wh, world, setup, drive_root):
    inv = _invoice(owner, sales, wh, world, setup)
    page = _files(owner, invoice_id=inv["id"])
    assert page["total"] == 1 and page["drive_provider"] == "local"
    f = page["items"][0]
    assert f["kind"] == "INVOICE_PDF" and f["sync_status"] == "PENDING"
    assert f["filename"] == f"{inv['invoice_no']}.pdf"
    assert f["folder"].startswith("Customers/C-100 Island Grocers/")
    assert f["sha256"] == inv["pdf_sha256"]

    _sync()
    _sync()  # at-least-once: no second copy, same id
    f2 = owner.get(f"{P}/documents/{f['id']}").json()
    assert f2["sync_status"] == "SYNCED" and f2["sync_attempts"] == 1
    assert f2["drive_file_id"] == f"local-{uuid.UUID(f['id']).hex}"
    copies = list(
        (drive_root / str(world.company_id)).rglob("*.pdf")
    )  # other tests share the outbox
    assert len(copies) == 1
    assert hashlib.sha256(copies[0].read_bytes()).hexdigest() == inv["pdf_sha256"]

    # reprint = same issued bytes, from both the invoice and the archive
    pdf = owner.get(f"{P}/invoices/{inv['id']}/pdf").content
    arch = owner.get(f"{P}/documents/{f['id']}/content")
    assert arch.status_code == 200 and arch.content == pdf
    assert arch.headers["x-content-sha256"] == inv["pdf_sha256"]
    etag = arch.headers["etag"]
    again = owner.get(f"{P}/documents/{f['id']}/content", headers={"If-None-Match": etag})
    assert again.status_code == 304


def test_drive_timeout_keeps_invoice_then_retry_syncs(
    owner, sales, wh, world, setup, monkeypatch, drive_root
):
    monkeypatch.setattr(get_settings(), "drive_fault", "timeout")
    inv = _invoice(owner, sales, wh, world, setup)
    f = _files(owner, invoice_id=inv["id"])["items"][0]
    for _ in range(outbox.MAX_ATTEMPTS - 1):
        _sync()
        cur = owner.get(f"{P}/documents/{f['id']}").json()
        assert cur["sync_status"] == "PENDING" and "DRIVE_TIMEOUT" in cur["last_error"]
    _sync()
    cur = owner.get(f"{P}/documents/{f['id']}").json()
    assert cur["sync_status"] == "FAILED" and cur["sync_attempts"] == outbox.MAX_ATTEMPTS
    assert cur["drive_file_id"] is None and not list(
        (drive_root / str(world.company_id)).rglob("*.pdf")
    )
    # the invoice itself is untouched and still printable
    assert owner.get(f"{P}/invoices/{inv['id']}").json()["invoice_no"] == inv["invoice_no"]
    assert owner.get(f"{P}/invoices/{inv['id']}/pdf").status_code == 200
    counts = _files(owner)["counts"]
    assert counts.get("FAILED", 0) >= 1

    stale = owner.post(
        f"{P}/documents/{f['id']}/retry-sync", json={"version": f["version"]}, headers=idem()
    )
    assert stale.status_code == 409 and code(stale) == "VERSION_CONFLICT"

    monkeypatch.setattr(get_settings(), "drive_fault", "")
    key = idem()
    r = owner.post(
        f"{P}/documents/{f['id']}/retry-sync", json={"version": cur["version"]}, headers=key
    )
    assert r.status_code == 200, r.text
    assert r.json()["sync_status"] == "PENDING"
    replay = owner.post(
        f"{P}/documents/{f['id']}/retry-sync", json={"version": cur["version"]}, headers=key
    )
    assert replay.status_code == 200 and replay.json() == r.json()
    _sync()
    done = owner.get(f"{P}/documents/{f['id']}").json()
    assert done["sync_status"] == "SYNCED" and done["last_error"] is None
    again = owner.post(
        f"{P}/documents/{f['id']}/retry-sync", json={"version": done["version"]}, headers=idem()
    )
    assert again.status_code == 409 and code(again) == "INVALID_STATE"


def test_drive_denied_fails_without_retries(owner, sales, wh, world, setup, monkeypatch):
    monkeypatch.setattr(get_settings(), "drive_fault", "denied")
    inv = _invoice(owner, sales, wh, world, setup)
    f = _files(owner, invoice_id=inv["id"])["items"][0]
    _sync()
    cur = owner.get(f"{P}/documents/{f['id']}").json()
    assert cur["sync_status"] == "FAILED" and cur["sync_attempts"] == 1
    assert "DRIVE_DENIED" in cur["last_error"]
    with get_sessionmaker()() as db:
        n = db.execute(
            text(
                "select count(*) from audit_events where action='document.sync_failed' "
                "and target_id=:i"
            ),
            {"i": f["id"]},
        ).scalar_one()
    assert n == 1


def test_disabled_provider_marks_local_only(owner, sales, wh, world, setup, monkeypatch):
    monkeypatch.setattr(get_settings(), "drive_provider", "disabled")
    inv = _invoice(owner, sales, wh, world, setup)
    page = _files(owner, invoice_id=inv["id"])
    assert page["drive_provider"] == "disabled"
    assert page["items"][0]["sync_status"] == "LOCAL_ONLY"
    assert _sync() == 0


def test_archive_rows_are_append_only(owner, sales, wh, world, setup):
    inv = _invoice(owner, sales, wh, world, setup)
    f = _files(owner, invoice_id=inv["id"])["items"][0]
    with get_sessionmaker()() as db, pytest.raises(DBAPIError):
        db.execute(text("delete from document_files where id=:i"), {"i": f["id"]})
    with get_sessionmaker()() as db, pytest.raises(DBAPIError):
        db.execute(text("update document_files set filename='x.pdf' where id=:i"), {"i": f["id"]})
    with get_sessionmaker()() as db, pytest.raises(DBAPIError):
        db.execute(
            text("update document_files set sha256=repeat('0',64) where id=:i"), {"i": f["id"]}
        )
    _sync()
    with get_sessionmaker()() as db, pytest.raises(DBAPIError):
        db.execute(
            text("update document_files set drive_file_id='other' where id=:i"), {"i": f["id"]}
        )
    # sync bookkeeping is still allowed
    with get_sessionmaker()() as db:
        db.execute(
            text("update document_files set last_error='note', version=version+1 where id=:i"),
            {"i": f["id"]},
        )
        db.commit()


# ---------------------------------------------------------------- POD


def _pod(api, inv, data=POD, key=None, note="Signed by J. Doe (synthetic)"):
    return api.c.post(
        f"{P}/invoices/{inv['id']}/pod",
        params={"filename": "pod-scan.pdf", "note": note},
        content=data,
        headers={**api._h(key or idem()), "Content-Type": "application/pdf"},
    )


def test_pod_upload_is_append_only(owner, sales, wh, world, setup):
    inv = _invoice(owner, sales, wh, world, setup)
    r = _pod(sales, inv)
    assert r.status_code == 201, r.text
    pod = r.json()
    assert pod["kind"] == "POD" and pod["filename"] == f"{inv['invoice_no']} POD.pdf"
    assert pod["invoice_id"] == inv["id"] and pod["note"].startswith("Signed")
    same = _pod(sales, inv)
    assert same.status_code == 200 and same.json()["id"] == pod["id"]
    second = _pod(sales, inv, data=POD.replace(b"signed", b"second"))
    assert second.status_code == 201
    assert second.json()["filename"] == f"{inv['invoice_no']} POD (2).pdf"
    kinds = sorted(i["kind"] for i in _files(owner, invoice_id=inv["id"])["items"])
    assert kinds == ["INVOICE_PDF", "POD", "POD"]
    got = owner.get(f"{P}/documents/{pod['id']}/content")
    assert got.content == POD

    bad = _pod(sales, inv, data=b"not a pdf")
    assert bad.status_code in (415, 422)


def test_scans_are_filed(owner, world):
    from .test_scans import upload as supload

    r = supload(owner, world, name="order-sheet.pdf")
    assert r.status_code == 201, r.text
    scans = _files(owner, kind="SCAN")["items"]
    assert len(scans) == 1 and scans[0]["folder"].startswith("Scans/")
    assert scans[0]["scan_job_no"]
    _sync()
    assert _files(owner, kind="SCAN")["items"][0]["sync_status"] == "SYNCED"


# ---------------------------------------------------------------- authz


def test_permissions_and_scope(owner, user, sales, wh, world, setup, make_api):
    inv = _invoice(owner, sales, wh, world, setup)
    f = _files(owner, invoice_id=inv["id"])["items"][0]

    # warehouse role has no document access
    assert wh.get(f"{P}/documents").status_code == 403
    assert wh.get(f"{P}/documents/{f['id']}/content").status_code == 403
    assert _pod(wh, inv).status_code == 403

    fin = user("finance")
    assert _files(fin)["total"] >= 1
    assert fin.get(f"{P}/documents/{f['id']}").status_code == 200

    ma = user("manager_a")  # scoped to warehouse A
    assert ma.get(f"{P}/documents/{f['id']}").status_code == 200
    assert _files(ma, warehouse_id=str(world.wh_b))["total"] == 0

    # another tenant cannot see the file at all
    from .conftest import build_world

    other = build_world()
    x = make_api()
    x.login(other.emails["owner"])
    x.enroll_and_verify()
    assert x.get(f"{P}/documents/{f['id']}").status_code == 404
    assert x.get(f"{P}/documents/{f['id']}/content").status_code == 404
    assert _files(x)["total"] == 0
