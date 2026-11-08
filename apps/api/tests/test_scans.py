"""Module 06 scanner: pairing, capabilities, localhost bridge defences, manual fallback, dedupe.

Acceptance (docs/modules/06-scanner.md, ACCEPTANCE_MATRIX OCR-002 / SEC-002):
- no website can make the bridge scan without an authenticated, server-issued capability;
- an unplugged scanner fails the job cleanly and manual upload still works;
- uploading the same bytes again never creates a second source document.
Synthetic data only.
"""

import http.client
import json
import os
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading import audit
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker

from .conftest import build_world, idem

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "bridge")
)  # apps/bridge (stdlib-only package)

from pro_trading_bridge.client import ApiClient, BridgeApiError  # noqa: E402
from pro_trading_bridge.drivers import FakeDriver  # noqa: E402
from pro_trading_bridge.server import Bridge, make_handler  # noqa: E402

P = "/api/v1"
WEB = "http://localhost:5173"
PDF = b"%PDF-1.4\n% synthetic order sheet A\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def code(r: Any) -> str:
    return str(r.json()["error"]["code"])


@pytest.fixture
def owner(make_api, world):
    o = make_api()
    o.login(world.emails["owner"])
    assert o.enroll_and_verify().status_code == 200
    return o


@pytest.fixture
def user(make_api, world):
    def _login(name):
        a = make_api()
        assert a.login(world.emails[name]).status_code == 200
        if name in ("finance", "manager_a"):
            assert a.enroll_and_verify().status_code == 200
        return a

    return _login


def transport(app_client):
    """Bridge → API through the TestClient (no browser Origin, like a real server call)."""

    def send(method, url, headers, body):
        path = url.split("://", 1)[1]
        path = path[path.index("/") :]
        r = app_client.request(method, path, headers=headers, content=body)
        return r.status_code, r.content

    return send


def upload(api, world, data=PDF, ctype="application/pdf", wh=None, key=None, name="sheet.pdf"):
    q = f"warehouse_id={wh or world.wh_a}&job_type=SALES_ORDER&filename={name}"
    h = {**api._h(key or idem()), "Content-Type": ctype}
    return api.c.post(f"{P}/scans/uploads?{q}", content=data, headers=h)


def pair_device(owner, world, app, wh=None, name="Front desk Toshiba") -> tuple[dict, ApiClient]:
    r = owner.post(
        f"{P}/scan-devices/pairings",
        json={"warehouse_id": str(wh or world.wh_a), "name": name},
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    from fastapi.testclient import TestClient

    client = ApiClient("http://testserver", transport(TestClient(app)))
    fp = client.pair(r.json()["pairing_code"])
    dev = next(d for d in owner.get(f"{P}/scan-devices").json() if d["id"] == client.device_id)
    a = owner.post(
        f"{P}/scan-devices/{dev['id']}/approve",
        json={"version": dev["version"], "fingerprint": fp},
        headers=idem(),
    )
    assert a.status_code == 200, a.text
    return a.json(), client


def new_job(api, world, dev, wh=None):
    r = api.post(
        f"{P}/scans/jobs",
        json={
            "warehouse_id": str(wh or world.wh_a),
            "job_type": "SALES_ORDER",
            "device_id": dev["id"],
            "note": "Island Grocers fax",
        },
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def bridge_server() -> Iterator[Any]:
    """Real HTTP bridge on 127.0.0.1:<random port>; yields a factory(client, driver)."""
    started: list[ThreadingHTTPServer] = []

    def make(client: ApiClient, driver: FakeDriver) -> Bridge:
        b = Bridge(api=client, driver=driver, allowed_origins=frozenset({WEB}), port=0)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(b))
        b.port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return b

    yield make
    for h in started:
        h.shutdown()
        h.server_close()


def call_bridge(b: Bridge, method="POST", path="/v1/scan", body=None, origin=WEB, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", b.port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Host": host or f"127.0.0.1:{b.port}"}
    if origin is not None:
        headers["Origin"] = origin
    if data is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(data))
    conn.request(method, path, body=data, headers=headers)
    r = conn.getresponse()
    raw = r.read()
    out = (r.status, json.loads(raw) if raw else {}, dict(r.getheaders()))
    conn.close()
    return out


# ------------------------------------------------------------------ pairing


def test_pairing_requires_code_and_operator_confirmed_fingerprint(owner, world, app):
    from fastapi.testclient import TestClient

    r = owner.post(
        f"{P}/scan-devices/pairings",
        json={"warehouse_id": str(world.wh_a), "name": "Office scanner"},
        headers=idem(),
    )
    assert r.status_code == 201
    pcode = r.json()["pairing_code"]
    dev_id = r.json()["device_id"]
    client = ApiClient("http://testserver", transport(TestClient(app)))
    with pytest.raises(BridgeApiError) as e:
        client.pair("ZZZZ-ZZZZ")
    assert e.value.code == "PAIRING_CODE_INVALID"
    fp = client.pair(pcode.lower().replace("-", ""))  # tolerant of typing
    # code is single use
    other = ApiClient("http://testserver", transport(TestClient(app)))
    with pytest.raises(BridgeApiError) as e:
        other.pair(pcode)
    assert e.value.code == "PAIRING_CODE_INVALID"
    # pending device can heartbeat but cannot claim
    assert client.heartbeat("READY", None)["status"] == "PENDING"
    with pytest.raises(BridgeApiError) as e:
        client.claim(dev_id, "x" * 40)
    assert e.value.code == "DEVICE_NOT_APPROVED"
    dev = next(d for d in owner.get(f"{P}/scan-devices").json() if d["id"] == dev_id)
    assert dev["paired"] and dev["status"] == "PENDING" and not dev["online"]
    bad = owner.post(
        f"{P}/scan-devices/{dev_id}/approve",
        json={"version": dev["version"], "fingerprint": "0000-0000"},
        headers=idem(),
    )
    assert bad.status_code == 422 and code(bad) == "FINGERPRINT_MISMATCH"
    ok = owner.post(
        f"{P}/scan-devices/{dev_id}/approve",
        json={"version": dev["version"], "fingerprint": fp.lower()},
        headers=idem(),
    )
    assert ok.status_code == 200 and ok.json()["status"] == "ACTIVE"
    client.heartbeat("READY", None)
    assert owner.get(f"{P}/scan-devices").json()[0]["online"] is True
    # wrong secret
    client.device_secret = "nope"
    with pytest.raises(BridgeApiError) as e:
        client.heartbeat("READY", None)
    assert e.value.status == 401 and e.value.code == "DEVICE_AUTH_FAILED"


# ------------------------------------------------------------------ happy path via bridge


def test_bridge_scan_end_to_end_and_capability_single_use(owner, user, world, app, bridge_server):
    dev, client = pair_device(owner, world, app)
    sales = user("sales")
    job = new_job(sales, world, dev)
    assert job["status"] == "REQUESTED" and job["capability"]
    driver = FakeDriver()
    b = bridge_server(client, driver)
    st, body, hdr = call_bridge(b, "GET", "/v1/status")
    assert st == 200 and body["paired"] and body["scanner_state"] == "READY"
    assert hdr["Access-Control-Allow-Origin"] == WEB
    st, pre, hdr = call_bridge(b, "OPTIONS")
    assert st == 204 and hdr["Access-Control-Allow-Private-Network"] == "true"

    st, out = call_bridge(b, body={"job_id": job["id"], "capability": job["capability"]})[:2]
    assert st == 200, out
    assert out["status"] == "UPLOADED" and out["document_mime"] == "application/pdf"
    assert driver.calls == 1
    got = sales.get(f"{P}/scans/jobs/{job['id']}").json()
    assert got["status"] == "UPLOADED" and got["channel"] == "BRIDGE"
    doc = sales.get(f"{P}/scans/documents/{got['document_id']}/content")
    assert doc.status_code == 200 and doc.content == driver.payload
    assert doc.headers["x-content-type-options"] == "nosniff"
    csp = doc.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'self'" in csp
    assert (
        sales.get(
            f"{P}/scans/documents/{got['document_id']}/content",
            headers={"If-None-Match": doc.headers["etag"]},
        ).status_code
        == 304
    )

    # replaying the same capability (e.g. a page that sniffed it) does nothing
    st, out = call_bridge(b, body={"job_id": job["id"], "capability": job["capability"]})[:2]
    assert st == 403 and out["code"] == "CAPABILITY_USED"
    assert driver.calls == 1


# ------------------------------------------------------------------ SEC-002


def test_sec002_foreign_sites_cannot_make_bridge_scan(owner, user, world, app, bridge_server):
    dev, client = pair_device(owner, world, app)
    sales = user("sales")
    job = new_job(sales, world, dev)
    driver = FakeDriver()
    b = bridge_server(client, driver)
    good = {"job_id": job["id"], "capability": job["capability"]}

    for origin, host in [
        ("https://evil.example", None),  # arbitrary website
        (None, None),  # no Origin (curl / old browser form post)
        ("null", None),  # sandboxed iframe / file://
        ("http://localhost:5173.evil.example", None),  # prefix trick
        (WEB, "evil.example:47615"),  # DNS rebinding: right origin, foreign Host
    ]:
        st, out = call_bridge(b, body=good, origin=origin, host=host)[:2]
        assert st == 403, (origin, host, out)
        assert out["code"] in ("ORIGIN_REJECTED", "HOST_REJECTED")
        st, _, hdr = call_bridge(b, "OPTIONS", origin=origin, host=host)
        assert st == 403 and "Access-Control-Allow-Origin" not in hdr
    assert driver.calls == 0
    assert len(b.security_events) == 10

    # an allowed origin without a server capability gets nothing either
    st, out = call_bridge(b, body={"job_id": job["id"], "capability": "A" * 64})[:2]
    assert st == 403 and out["code"] == "CAPABILITY_INVALID"
    # a capability for job A cannot start job B
    job2 = new_job(sales, world, dev)
    st, out = call_bridge(b, body={"job_id": job2["id"], "capability": job["capability"]})[:2]
    assert st == 403 and out["code"] == "CAPABILITY_INVALID"
    assert driver.calls == 0
    assert sales.get(f"{P}/scans/jobs/{job['id']}").json()["status"] == "REQUESTED"

    # another paired device cannot use this device's capability
    _, client2 = pair_device(owner, world, app, name="Back office")
    with pytest.raises(BridgeApiError) as e:
        client2.claim(job["id"], job["capability"])
    assert e.value.code == "CAPABILITY_INVALID"

    # refusals are persisted as security events
    with get_sessionmaker()() as db:
        n = db.scalar(
            text(
                "SELECT count(*) FROM audit_events WHERE action = 'scan.capability.rejected' "
                "AND detail_redacted->>'device' IN (:a, :b)"
            ),
            {"a": dev["id"], "b": client2.device_id},
        )
    assert n >= 2

    # unauthenticated API calls are refused
    from fastapi.testclient import TestClient

    anon = TestClient(app)
    assert anon.post(f"{P}/scans/jobs", json={}).status_code in (401, 403)
    r = anon.post(f"{P}/scan-bridge/jobs/{job['id']}/claim", json={"capability": "A" * 40})
    assert r.status_code == 401 and r.json()["error"]["code"] == "DEVICE_AUTH_FAILED"


def test_expired_capability_and_sweep(owner, user, world, app, monkeypatch):
    dev, client = pair_device(owner, world, app)
    sales = user("sales")
    monkeypatch.setattr(get_settings(), "scan_capability_ttl_seconds", -5)
    job = new_job(sales, world, dev)
    monkeypatch.undo()
    with pytest.raises(BridgeApiError) as e:
        client.claim(job["id"], job["capability"])
    assert e.value.code == "CAPABILITY_EXPIRED"
    listed = sales.get(f"{P}/scans/jobs?status=EXPIRED").json()["items"]
    assert [j["id"] for j in listed] == [job["id"]]
    # stuck scans time out
    job2 = new_job(sales, world, dev)
    client.claim(job2["id"], job2["capability"])
    with get_sessionmaker()() as db, db.begin():
        db.execute(
            text("UPDATE scan_jobs SET claimed_at = now() - interval '1 hour' WHERE id = :i"),
            {"i": job2["id"]},
        )
    got = sales.get(f"{P}/scans/jobs/{job2['id']}").json()
    assert got["status"] == "FAILED" and got["error_code"] == "SCAN_TIMEOUT"


def test_single_flight_per_device(owner, user, world, app):
    dev, client = pair_device(owner, world, app)
    sales = user("sales")
    j1, j2 = new_job(sales, world, dev), new_job(sales, world, dev)
    client.claim(j1["id"], j1["capability"])
    with pytest.raises(BridgeApiError) as e:
        client.claim(j2["id"], j2["capability"])
    assert e.value.status == 409 and e.value.code == "SCANNER_BUSY"
    # after the first finishes, the second can go (capability was not consumed)
    client.upload(j1["id"], PDF, "application/pdf", "a.pdf")
    assert client.claim(j2["id"], j2["capability"])["job_no"] == j2["job_no"]


# ------------------------------------------------------------------ unplugged → manual fallback


def test_unplugged_scanner_fails_cleanly_and_manual_upload_works(
    owner, user, world, app, bridge_server
):
    dev, client = pair_device(owner, world, app)
    sales = user("sales")
    job = new_job(sales, world, dev)
    b = bridge_server(client, FakeDriver(mode="unplugged"))
    st, body = call_bridge(b, "GET", "/v1/status")[:2]
    assert body["scanner_state"] == "NOT_FOUND"
    st, out = call_bridge(b, body={"job_id": job["id"], "capability": job["capability"]})[:2]
    assert st == 200 and out["status"] == "FAILED" and out["error_code"] == "SCANNER_NOT_FOUND"
    d = next(x for x in sales.get(f"{P}/scan-devices").json() if x["id"] == dev["id"])
    assert d["scanner_state"] == "NOT_FOUND"
    # retrying the fail report is harmless
    assert client.fail(job["id"], "SCANNER_NOT_FOUND", "again")["status"] == "FAILED"
    # bridge can't upload into a failed job
    with pytest.raises(BridgeApiError) as e:
        client.upload(job["id"], PDF, "application/pdf", "late.pdf")
    assert e.value.code == "INVALID_STATE"

    r = upload(sales, world)
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["channel"] == "MANUAL" and m["status"] == "UPLOADED" and m["duplicate"] is False
    assert m["document_filename"] == "sheet.pdf"


# ------------------------------------------------------------------ OCR-002 duplicates


def test_repeat_upload_creates_one_source_document(owner, user, world, app):
    sales = user("sales")
    first = upload(sales, world, data=PDF + b"%dup\n")
    assert first.status_code == 201
    again = upload(sales, world, data=PDF + b"%dup\n", name="renamed.pdf")
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"] and again.json()["duplicate"] is True
    assert again.headers["x-duplicate-of"] == first.json()["id"]
    # concurrent identical uploads (double click, two tabs)
    data = PDF + b"%concurrent\n"
    with ThreadPoolExecutor(6) as ex:
        rs = list(ex.map(lambda _: upload(sales, world, data=data), range(6)))
    assert sorted(r.status_code for r in rs) == [200] * 5 + [201]
    assert len({r.json()["document_id"] for r in rs}) == 1
    # the bridge delivering bytes that were already uploaded links the same document
    dev, client = pair_device(owner, world, app)
    job = new_job(sales, world, dev)
    client.claim(job["id"], job["capability"])
    out = client.upload(job["id"], data, "application/pdf", "x.pdf")
    assert out["duplicate"] is True and out["document_id"] == rs[0].json()["document_id"]
    # network retry of the same bridge upload returns the job, no new rows
    assert client.upload(job["id"], data, "application/pdf", "x.pdf")["id"] == job["id"]
    with get_sessionmaker()() as db:
        n = db.scalar(
            text("SELECT count(*) FROM source_documents WHERE company_id = :c"),
            {"c": world.company_id},
        )
    assert n == 2
    # a retried request (same key, same bytes) returns the same scan
    k = idem()
    a = upload(sales, world, data=PNG, ctype="image/png", key=k)
    b = upload(sales, world, data=PNG, ctype="image/png", key=k)
    assert a.status_code == 201 and b.status_code in (200, 201)
    assert a.json()["id"] == b.json()["id"]
    # non-PDF content is always served in a CSP sandbox
    img = sales.get(f"{P}/scans/documents/{a.json()['document_id']}/content")
    assert img.status_code == 200 and img.headers["content-security-policy"].startswith("sandbox;")


def test_file_validation(user, world, monkeypatch):
    sales = user("sales")
    assert code(upload(sales, world, data=b"")) == "EMPTY_FILE"
    r = upload(sales, world, data=b"MZ\x90\x00 not a scan", ctype="application/pdf")
    assert r.status_code == 415 and code(r) == "UNSUPPORTED_FILE"
    r = upload(sales, world, data=PNG, ctype="application/pdf")
    assert r.status_code == 415 and code(r) == "FILE_TYPE_MISMATCH"
    r = upload(sales, world, data=PDF + b"%path\n", name="../../etc/passwd")
    assert r.status_code == 201 and r.json()["document_filename"] == "passwd.pdf"
    monkeypatch.setattr(get_settings(), "scan_max_bytes", 64)
    r = upload(sales, world, data=PDF + b"x" * 100)
    assert r.status_code == 413 and code(r) == "FILE_TOO_LARGE"


# ------------------------------------------------------------------ authz / tenancy


def test_permissions_scope_and_tenancy(owner, user, world, app, make_api):
    dev, client = pair_device(owner, world, app)
    wh = user("wh_a")
    assert upload(wh, world).status_code == 403
    assert wh.get(f"{P}/scans/jobs").status_code == 403
    fin = user("finance")
    assert upload(fin, world).status_code == 403
    sales = user("sales")
    r = sales.post(
        f"{P}/scan-devices/pairings",
        json={"warehouse_id": str(world.wh_a), "name": "Rogue"},
        headers=idem(),
    )
    assert r.status_code == 403
    mgr = user("manager_a")
    assert upload(mgr, world, data=PDF + b"%b\n", wh=world.wh_b).status_code == 403
    assert upload(mgr, world, data=PDF + b"%a\n").status_code == 201
    # device in WA can't scan for WB
    r = owner.post(
        f"{P}/scans/jobs",
        json={"warehouse_id": str(world.wh_b), "job_type": "OTHER", "device_id": dev["id"]},
        headers=idem(),
    )
    assert r.status_code == 422 and code(r) == "WRONG_WAREHOUSE"
    mine = upload(sales, world, data=PDF + b"%mine\n").json()

    other = build_world()
    o2 = make_api()
    o2.login(other.emails["sales"])
    assert o2.get(f"{P}/scans/jobs/{mine['id']}").status_code == 404
    assert o2.get(f"{P}/scans/documents/{mine['document_id']}/content").status_code == 404
    assert o2.get(f"{P}/scans/jobs").json()["total"] == 0
    # same bytes in another company are a separate, private source
    r = upload(o2, other, data=PDF + b"%mine\n")
    assert r.status_code == 201 and r.json()["document_id"] != mine["document_id"]


def test_revoke_blocks_bridge_and_fails_open_jobs(owner, user, world, app):
    dev, client = pair_device(owner, world, app)
    sales = user("sales")
    job = new_job(sales, world, dev)
    r = owner.post(
        f"{P}/scan-devices/{dev['id']}/revoke",
        json={"version": dev["version"], "reason": "PC replaced"},
        headers=idem(),
    )
    assert r.status_code == 200 and r.json()["status"] == "REVOKED"
    got = sales.get(f"{P}/scans/jobs/{job['id']}").json()
    assert got["status"] == "FAILED" and got["error_code"] == "DEVICE_REVOKED"
    with pytest.raises(BridgeApiError) as e:
        client.heartbeat("READY", None)
    assert e.value.code == "DEVICE_REVOKED"
    r = sales.post(
        f"{P}/scans/jobs",
        json={"warehouse_id": str(world.wh_a), "job_type": "OTHER", "device_id": dev["id"]},
        headers=idem(),
    )
    assert r.status_code == 409 and code(r) == "DEVICE_NOT_ACTIVE"
    assert dev["id"] not in [d["id"] for d in owner.get(f"{P}/scan-devices").json()]


def test_cancel_job(owner, user, world, app):
    dev, client = pair_device(owner, world, app)
    sales = user("sales")
    job = new_job(sales, world, dev)
    r = sales.post(
        f"{P}/scans/jobs/{job['id']}/cancel", json={"version": job["version"]}, headers=idem()
    )
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    with pytest.raises(BridgeApiError) as e:
        client.claim(job["id"], job["capability"])
    assert e.value.code == "CAPABILITY_USED"
    r = sales.post(
        f"{P}/scans/jobs/{job['id']}/cancel", json={"version": r.json()["version"]}, headers=idem()
    )
    assert r.status_code == 409 and code(r) == "INVALID_STATE"


# ------------------------------------------------------------------ failure injection, triggers


def test_failure_injection_leaves_no_partial_document(user, world, monkeypatch):
    sales = user("sales")
    data = PDF + b"%inject\n"
    real = audit.record

    def boom(*a, **kw):
        if kw.get("action") == "scan_job.upload":
            raise RuntimeError("injected")
        return real(*a, **kw)

    monkeypatch.setattr(audit, "record", boom)
    with pytest.raises(RuntimeError):
        upload(sales, world, data=data)
    monkeypatch.setattr(audit, "record", real)
    with get_sessionmaker()() as db:
        n = db.scalar(
            text("SELECT count(*) FROM source_documents WHERE company_id = :c"),
            {"c": world.company_id},
        )
    assert n == 0
    assert upload(sales, world, data=data).status_code == 201


def test_documents_and_jobs_are_append_only(user, world):
    sales = user("sales")
    j = upload(sales, world, data=PDF + b"%trigger\n").json()
    for sql in [
        "UPDATE source_documents SET content = 'x' WHERE id = :d",
        "DELETE FROM source_documents WHERE id = :d",
        "DELETE FROM scan_jobs WHERE id = :j",
        "UPDATE scan_jobs SET status = 'REQUESTED' WHERE id = :j",
        "UPDATE scan_jobs SET document_id = NULL WHERE id = :j",
    ]:
        with get_sessionmaker()() as db, pytest.raises(DBAPIError):
            db.execute(text(sql), {"d": j["document_id"], "j": j["id"]})
            db.commit()


# ------------------------------------------------------------------ scanner discovery


def test_find_scanners_on_the_office_pc(owner, user, world, app):
    """The web app asks; the bridge lists what it sees on its next heartbeat; nothing else."""
    dev, client = pair_device(owner, world, app)
    assert client.heartbeat("READY", None)["discover"] is False  # nobody asked
    r = owner.post(f"{P}/scan-devices/{dev['id']}/discover")
    assert r.status_code == 200 and r.json()["discover_requested_at"]
    assert r.json()["detected_scanners"] is None
    hb = client.heartbeat("READY", None)
    assert hb["discover"] is True
    found = FakeDriver().list_devices()
    hb = client.heartbeat("READY", None, scanners=found)
    assert hb["discover"] is False  # answered
    got = next(d for d in owner.get(f"{P}/scan-devices").json() if d["id"] == dev["id"])
    assert got["detected_scanners"] == [{"name": "Fake Toshiba e-STUDIO (test)", "driver": "fake"}]
    assert got["detected_at"] >= got["discover_requested_at"]
    assert FakeDriver(mode="unplugged").list_devices() == []
    # only scanner managers may ask; another company can't see the device
    assert user("sales").post(f"{P}/scan-devices/{dev['id']}/discover").status_code == 403
    with get_sessionmaker()() as db:
        n = db.execute(
            text(
                "select count(*) from audit_events"
                " where action='scan_device.discover' and target_id=:t"
            ),
            {"t": dev["id"]},
        ).scalar_one()
    assert n == 1
