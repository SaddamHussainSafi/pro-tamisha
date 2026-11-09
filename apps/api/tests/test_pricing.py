"""Module 02 pricing: Postgres integration, authz, idempotency, concurrency, failure injection.

Synthetic data only. Acceptance (docs/modules/02-pricing.md):
two price versions differ; old document rate unchanged; duplicate CSV upload is not a duplicate
published version; invalid unit rejected.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading import audit
from pro_trading.db import get_sessionmaker

from .conftest import build_world, idem

P = "/api/v1"


@pytest.fixture
def owner(make_api, world):
    o = make_api()
    o.login(world.emails["owner"])
    assert o.enroll_and_verify().status_code == 200
    return o


@pytest.fixture
def setup(owner):
    """Products, a group and two customers (one in the group, one with no group)."""
    prods = {}
    for sku, name, profiles in [
        ("CAS", "Cassava (frozen)", [("CASE20", "BOX", "20"), ("CASE40", "BOX", "40")]),
        ("PLN", "Plantain", [("PC", "PIECE", None)]),
        ("ACK", "=Ackee tins", [("CASE24", "BOX", None)]),
    ]:
        body = {
            "sku": sku,
            "name": name,
            "stock_unit": "LB",
            "unit_profiles": [
                {"code": c, "unit_code": u, "weight_lb_per_unit": w} for c, u, w in profiles
            ],
        }
        r = owner.post(f"{P}/products", json=body, headers=idem())
        assert r.status_code == 201, r.text
        prods[sku] = r.json()["id"]
    g = owner.post(
        f"{P}/customer-groups", json={"code": "WHOLESALE", "name": "Wholesale"}, headers=idem()
    )
    assert g.status_code == 201, g.text
    gid = g.json()["id"]
    c1 = owner.post(
        f"{P}/customers",
        json={"external_ref": "C-100", "name": "Island Grocers", "group_id": gid},
        headers=idem(),
    )
    c2 = owner.post(
        f"{P}/customers", json={"external_ref": "C-200", "name": "Walk-in"}, headers=idem()
    )
    c3 = owner.post(
        f"{P}/customers",
        json={"external_ref": "C-300", "name": "Other Wholesale", "group_id": gid},
        headers=idem(),
    )  # fmt: skip
    assert c1.status_code == c2.status_code == c3.status_code == 201
    return {
        "p": prods,
        "group": gid,
        "c_group_override": c1.json()["id"],
        "c_plain": c2.json()["id"],
        "c_group": c3.json()["id"],
    }


V1_CSV = (
    "sku,product_name,unit,rate,currency,customer_ref,group_code\r\n"
    "CAS,Cassava (frozen),LB,1.50,CAD,,\r\n"
    "CAS,Cassava (frozen),BOX,28.00,CAD,,\r\n"
    "CAS,Cassava (frozen),LB,1.35,CAD,,WHOLESALE\r\n"
    "CAS,Cassava (frozen),LB,1.25,CAD,C-100,\r\n"
    "PLN,Plantain,PIECE,0.45,CAD,,\r\n"
    "ACK,'=Ackee tins,BOX,54.00,CAD,,\r\n"
)


def import_csv(api, csv: str, code: str, key: dict | None = None):
    return api.post(
        f"{P}/price-books/versions/import",
        json={"version_code": code, "csv": csv},
        headers=key or idem(),
    )


def activate(api, vid: str, version: int, effective_at: str | None = None):
    body: dict = {"version": version}
    if effective_at:
        body["effective_at"] = effective_at
    return api.post(f"{P}/price-books/versions/{vid}/activate", json=body, headers=idem())


def publish(api, csv: str, code: str) -> dict:
    r = import_csv(api, csv, code)
    assert r.status_code == 201, r.text
    v = r.json()
    a = activate(api, v["id"], v["version"])
    assert a.status_code == 200, a.text
    return a.json()


def resolve(api, product_id, unit="LB", customer_id=None, at=None):
    params = {"product_id": product_id, "unit_code": unit}
    if customer_id:
        params["customer_id"] = customer_id
    if at:
        params["at"] = at
    return api.get(f"{P}/prices/resolve", params=params)


# ---------------------------------------------------------------- happy path + precedence


def test_import_activate_and_precedence(owner, setup):
    r = import_csv(owner, V1_CSV, "PB-2026-01")
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["status"] == "DRAFT" and v["state"] == "DRAFT" and v["entry_count"] == 6
    assert len(v["source_hash"]) == 64

    # drafts are never used for pricing
    assert resolve(owner, setup["p"]["CAS"]).json()["error"]["code"] == "NO_PRICE"

    a = activate(owner, v["id"], v["version"])
    assert a.status_code == 200, a.text
    assert a.json()["state"] == "CURRENT" and a.json()["approved_by"]

    cas = setup["p"]["CAS"]
    std = resolve(owner, cas).json()
    assert (std["rate"], std["scope"], std["currency"]) == ("1.5000", "STANDARD", "CAD")
    grp = resolve(owner, cas, customer_id=setup["c_group"]).json()
    assert (grp["rate"], grp["scope"]) == ("1.3500", "GROUP")
    cust = resolve(owner, cas, customer_id=setup["c_group_override"]).json()
    assert (cust["rate"], cust["scope"]) == ("1.2500", "CUSTOMER")  # customer > group > standard
    plain = resolve(owner, cas, customer_id=setup["c_plain"]).json()
    assert plain["scope"] == "STANDARD"
    assert resolve(owner, cas, unit="BOX").json()["rate"] == "28.0000"
    nope = resolve(owner, cas, unit="KG")
    assert nope.status_code == 404 and nope.json()["error"]["code"] == "NO_PRICE"

    # alphabetical grid with filters
    g = owner.get(f"{P}/price-books/versions/{v['id']}/entries").json()
    assert [e["product_name"] for e in g["items"]][:1] == ["=Ackee tins"]
    assert g["total"] == 6
    only = owner.get(
        f"{P}/price-books/versions/{v['id']}/entries", params={"scope": "CUSTOMER"}
    ).json()
    assert [(e["target"], e["rate"]) for e in only["items"]] == [("Island Grocers", "1.2500")]
    lit = owner.get(f"{P}/price-books/versions/{v['id']}/entries", params={"q": "%"}).json()
    assert lit["total"] == 0  # LIKE wildcards are literal
    page = owner.get(f"{P}/price-books/versions/{v['id']}/entries", params={"limit": 4}).json()
    assert len(page["items"]) == 4 and page["next_cursor"] == "4"


def test_two_versions_differ_and_old_rate_unchanged(owner, setup):
    """INV-005 style: a rate resolved (and snapshotted) under v1 stays v1's after v2 goes live."""
    v1 = publish(owner, V1_CSV, "PB-1")
    cas = setup["p"]["CAS"]
    q1 = resolve(owner, cas).json()
    assert q1["rate"] == "1.5000" and q1["version_code"] == "PB-1"

    v2_csv = V1_CSV.replace("CAS,Cassava (frozen),LB,1.50", "CAS,Cassava (frozen),LB,1.65")
    pv = owner.post(f"{P}/price-books/imports/preview", json={"csv": v2_csv}, headers=idem())
    d = pv.json()["diff"]
    assert pv.json()["valid"] and d["changed"] == 1 and d["unchanged"] == 5
    assert d["rows"][0]["old_rate"] == "1.5000" and d["rows"][0]["new_rate"] == "1.6500"

    v2 = publish(owner, v2_csv, "PB-2")
    assert v2["state"] == "CURRENT"
    assert resolve(owner, cas).json()["rate"] == "1.6500"
    # the moment v1 was in force still prices at v1 — documents priced then keep their rate
    old = resolve(owner, cas, at=q1["resolved_at"]).json()
    assert (old["rate"], old["version_code"], old["entry_id"]) == ("1.5000", "PB-1", q1["entry_id"])

    versions = {v["version_code"]: v for v in owner.get(f"{P}/price-books/versions").json()}
    assert versions["PB-1"]["state"] == "SUPERSEDED" and versions["PB-2"]["state"] == "CURRENT"
    diff = owner.get(
        f"{P}/price-books/versions/{v2['id']}/diff", params={"against_version_id": v1["id"]}
    ).json()
    assert diff["changed"] == 1 and diff["added"] == diff["removed"] == 0

    # published entries are immutable at the database level, not just in the API
    with get_sessionmaker()() as db, pytest.raises(DBAPIError):
        db.execute(text("UPDATE price_entries SET rate = 9 WHERE id = :i"), {"i": q1["entry_id"]})
        db.flush()
    with get_sessionmaker()() as db, pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM price_book_versions WHERE id = :i"), {"i": v1["id"]})
        db.flush()
    with get_sessionmaker()() as db, pytest.raises(DBAPIError):
        db.execute(
            text("UPDATE price_book_versions SET status='ARCHIVED' WHERE id = :i"), {"i": v1["id"]}
        )
        db.flush()
    put = owner.put(
        f"{P}/price-books/versions/{v1['id']}/entries",
        json={"product_id": cas, "unit_code": "LB", "rate": "9", "version": v1["version"]},
        headers=idem(),
    )
    assert put.status_code == 409 and put.json()["error"]["code"] == "INVALID_STATE"


# ---------------------------------------------------------------- duplicates


def test_duplicate_upload_is_not_a_duplicate_version(owner, setup):
    v1 = publish(owner, V1_CSV, "PB-1")
    # same content, different order/format (no BOM issues, lower-case unit, extra zeros, blank line)
    lines = V1_CSV.strip().split("\r\n")
    shuffled = "﻿" + "\n".join([lines[0], *reversed(lines[1:])]).replace(",LB,", ",lb,")
    shuffled = shuffled.replace("1.50,", "1.5000,") + "\n\n"
    pv = owner.post(f"{P}/price-books/imports/preview", json={"csv": shuffled}, headers=idem())
    assert pv.json()["duplicate_of"] == v1["id"]
    r = import_csv(owner, shuffled, "PB-1-again")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "DUPLICATE_UPLOAD"
    assert r.json()["error"]["existing_version_id"] == v1["id"]

    # exporting a version and re-importing round-trips to the same content hash
    exp = owner.get(f"{P}/price-books/versions/{v1['id']}/export.csv")
    assert exp.status_code == 200 and exp.headers["content-type"].startswith("text/csv")
    assert "'=Ackee tins" in exp.text  # formula-injection safe
    assert import_csv(owner, exp.text, "PB-x").json()["error"]["code"] == "DUPLICATE_UPLOAD"

    # a manual draft cloned from current with no changes cannot be published again
    d = owner.post(f"{P}/price-books/versions", json={"version_code": "PB-clone"}, headers=idem())
    assert d.status_code == 201 and d.json()["entry_count"] == 6
    a = activate(owner, d.json()["id"], d.json()["version"])
    assert a.status_code == 422 and a.json()["error"]["code"] == "NO_CHANGES"
    actives = owner.get(f"{P}/price-books/versions", params={"status": "ACTIVE"}).json()
    assert [v["version_code"] for v in actives] == ["PB-1"]


def test_idempotent_import_replay(owner, setup):
    key = idem()
    a = import_csv(owner, V1_CSV, "PB-1", key)
    b = import_csv(owner, V1_CSV, "PB-1", key)
    assert a.status_code == b.status_code == 201
    assert b.headers.get("Idempotent-Replay") == "true" and a.json()["id"] == b.json()["id"]
    c = owner.post(
        f"{P}/price-books/versions/import",
        json={"version_code": "PB-other", "csv": V1_CSV},
        headers=key,
    )
    assert c.status_code == 409  # same key, different payload
    assert len(owner.get(f"{P}/price-books/versions").json()) == 1


# ---------------------------------------------------------------- validation


def test_invalid_unit_and_row_errors(owner, setup):
    bad = (
        "sku,unit,rate,currency,customer_ref,group_code\n"
        "CAS,PIECE,1.00,,,\n"  # cassava has no PIECE profile
        "PLN,TONNE,1.00,,,\n"  # not a unit at all
        "NOPE-1,LB,1.00,,,\n"
        "CAS,LB,1.123456,,,\n"
        "CAS,LB,-1,,,\n"
        "CAS,KG,2.00,USD,,\n"
        "CAS,LB,1.00,,C-404,\n"
        "CAS,LB,1.00,,C-100,WHOLESALE\n"
        "CAS,BOX,28,,,\n"
        "CAS,box,29,,,\n"
        "CAS,LB,1,,,,extra\n"
    )
    r = import_csv(owner, bad, "PB-bad")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "CSV_INVALID"
    got = {(e["row"], e["code"]) for e in err["errors"]}
    assert {
        (2, "INVALID_UNIT"),
        (3, "INVALID_UNIT"),
        (4, "UNKNOWN_SKU"),
        (5, "INVALID_RATE"),
        (6, "INVALID_RATE"),
        (7, "INVALID_CURRENCY"),
        (8, "UNKNOWN_CUSTOMER"),
        (9, "AMBIGUOUS_SCOPE"),
        (11, "DUPLICATE_ROW"),
        (12, "EXTRA_CELLS"),
    } <= got
    assert (10, "INVALID_UNIT") not in got
    assert owner.get(f"{P}/price-books/versions").json() == []  # all-or-nothing

    hdr = import_csv(owner, "code,price\nA,1\n", "PB-h").json()["error"]
    assert hdr["errors"][0]["code"] == "INVALID_HEADER"
    pv = owner.post(f"{P}/price-books/imports/preview", json={"csv": bad}, headers=idem()).json()
    assert pv["valid"] is False and pv["diff"] is None and len(pv["errors"]) >= 10

    d = owner.post(
        f"{P}/price-books/versions", json={"version_code": "PB-d"}, headers=idem()
    ).json()
    put = owner.put(
        f"{P}/price-books/versions/{d['id']}/entries",
        json={"product_id": setup["p"]["CAS"], "unit_code": "PIECE", "rate": "1", "version": 1},
        headers=idem(),
    )
    assert put.status_code == 422 and put.json()["error"]["code"] == "INVALID_UNIT"
    put = owner.put(
        f"{P}/price-books/versions/{d['id']}/entries",
        json={
            "product_id": setup["p"]["CAS"],
            "unit_code": "LB",
            "scope": "GROUP",
            "rate": "1",
            "version": 1,
        },
        headers=idem(),
    )
    assert put.json()["error"]["code"] == "INVALID_SCOPE"
    empty = activate(owner, d["id"], 1)
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "EMPTY_VERSION"


def test_draft_editing_versioning_and_stale_flag(owner, setup):
    v1 = publish(owner, V1_CSV, "PB-1")
    d = owner.post(
        f"{P}/price-books/versions", json={"version_code": "PB-2"}, headers=idem()
    ).json()
    assert d["based_on_id"] == v1["id"] and d["stale"] is False
    cas = setup["p"]["CAS"]
    body = {"product_id": cas, "unit_code": "LB", "rate": "1.6", "version": d["version"]}
    r = owner.put(f"{P}/price-books/versions/{d['id']}/entries", json=body, headers=idem())
    assert r.status_code == 200 and r.json()["version"] == d["version"] + 1
    assert r.json()["entry_count"] == 6  # updated in place, not duplicated
    stale = owner.put(f"{P}/price-books/versions/{d['id']}/entries", json=body, headers=idem())
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert stale.json()["error"]["current_version"] == d["version"] + 1

    ver = r.json()["version"]
    entries = owner.get(
        f"{P}/price-books/versions/{d['id']}/entries", params={"q": "plantain"}
    ).json()["items"]
    rm = owner.post(
        f"{P}/price-books/versions/{d['id']}/entries/remove",
        json={"entry_id": entries[0]["id"], "version": ver},
        headers=idem(),
    )
    assert rm.status_code == 200 and rm.json()["entry_count"] == 5

    # another price book goes live while PB-2 is being drafted -> PB-2 is flagged stale
    publish(owner, V1_CSV.replace("0.45", "0.50"), "PB-1b")
    got = owner.get(f"{P}/price-books/versions/{d['id']}").json()
    assert got["stale"] is True

    disc = owner.post(
        f"{P}/price-books/versions/{d['id']}/discard",
        json={"version": got["version"]},
        headers=idem(),
    )
    assert disc.status_code == 200
    assert owner.get(f"{P}/price-books/versions/{d['id']}").status_code == 404
    # published versions cannot be discarded
    cur = owner.post(
        f"{P}/price-books/versions/{v1['id']}/discard",
        json={"version": v1["version"]},
        headers=idem(),
    )
    assert cur.status_code == 409


def test_schedule_and_cancel(owner, setup):
    v1 = publish(owner, V1_CSV, "PB-1")
    r = import_csv(owner, V1_CSV.replace("28.00", "30.00"), "PB-2").json()
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    bad = activate(owner, r["id"], r["version"], past)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "EFFECTIVE_IN_PAST"
    future = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    s = activate(owner, r["id"], r["version"], future)
    assert s.status_code == 200 and s.json()["state"] == "SCHEDULED"
    box = resolve(owner, setup["p"]["CAS"], unit="BOX")
    assert box.json()["rate"] == "28.0000"  # not yet effective
    later = (datetime.now(UTC) + timedelta(days=8)).isoformat()
    assert resolve(owner, setup["p"]["CAS"], unit="BOX", at=later).json()["rate"] == "30.0000"

    no = owner.post(
        f"{P}/price-books/versions/{v1['id']}/archive",
        json={"version": v1["version"]},
        headers=idem(),
    )
    assert no.status_code == 409  # history in force can't be archived
    ok = owner.post(
        f"{P}/price-books/versions/{r['id']}/archive",
        json={"version": s.json()["version"]},
        headers=idem(),
    )
    assert ok.status_code == 200 and ok.json()["state"] == "ARCHIVED"
    assert resolve(owner, setup["p"]["CAS"], unit="BOX", at=later).json()["rate"] == "28.0000"


# ---------------------------------------------------------------- authz


def test_permissions(owner, setup, make_api, world):
    v = publish(owner, V1_CSV, "PB-1")
    sales = make_api()
    sales.login(world.emails["sales"])
    assert sales.get(f"{P}/price-books/versions").status_code == 200
    assert resolve(sales, setup["p"]["CAS"]).status_code == 200
    assert sales.get(f"{P}/customers").status_code == 200
    assert import_csv(sales, V1_CSV, "PB-s").status_code == 403
    assert sales.post(f"{P}/customers", json={"name": "x"}, headers=idem()).status_code == 403

    wh = make_api()
    wh.login(world.emails["wh_a"])
    assert wh.get(f"{P}/price-books/versions").status_code == 403
    assert resolve(wh, setup["p"]["CAS"]).status_code == 403

    # warehouse-scoped manager can't change the company price book
    mgr = make_api()
    mgr.login(world.emails["manager_a"])
    mgr.enroll_and_verify()
    assert import_csv(mgr, V1_CSV, "PB-m").status_code == 403

    # owner turned two-step on: a new sign-in has no grants until the code is entered
    o2 = make_api()
    o2.login(world.emails["owner"])
    d = o2.post(f"{P}/price-books/versions", json={"version_code": "PB-n"}, headers=idem())
    assert d.status_code == 403

    # another company sees nothing of ours
    other = build_world()
    x = make_api()
    x.login(other.emails["owner"])
    x.enroll_and_verify()
    assert x.get(f"{P}/price-books/versions/{v['id']}").status_code == 404
    assert x.get(f"{P}/price-books/versions").json() == []
    assert resolve(x, setup["p"]["CAS"]).status_code == 404
    assert x.get(f"{P}/customers/{setup['c_plain']}").status_code == 404


# ---------------------------------------------------------------- concurrency + failure


def test_concurrent_activation_same_draft(owner, setup):
    v = import_csv(owner, V1_CSV, "PB-1").json()
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda _: activate(owner, v["id"], v["version"]), range(4)))
    codes = sorted(r.status_code for r in res)
    assert codes.count(200) == 1 and all(c == 409 for c in codes if c != 200), codes
    assert len(owner.get(f"{P}/price-books/versions", params={"status": "ACTIVE"}).json()) == 1


def test_concurrent_activation_same_effective_time(owner, setup):
    a = import_csv(owner, V1_CSV, "PB-a").json()
    b = import_csv(owner, V1_CSV.replace("28.00", "31.00"), "PB-b").json()
    at = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0).isoformat()
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(lambda v: activate(owner, v["id"], v["version"], at), [a, b]))
    codes = sorted(r.status_code for r in res)
    assert codes == [200, 409], [r.text for r in res]


def test_failure_mid_import_rolls_back_and_retry_succeeds(owner, setup, monkeypatch):
    real = audit.record

    def boom(*a, **kw):
        raise RuntimeError("injected audit failure")

    key = idem()
    monkeypatch.setattr(audit, "record", boom)
    try:
        r = import_csv(owner, V1_CSV, "PB-1", key)
        assert r.status_code == 500
    except RuntimeError:
        pass
    monkeypatch.setattr(audit, "record", real)
    assert owner.get(f"{P}/price-books/versions").json() == []
    r = import_csv(owner, V1_CSV, "PB-1", key)
    assert r.status_code == 201 and r.json()["entry_count"] == 6


def test_template_csv(owner, setup):
    publish(owner, V1_CSV, "PB-1")
    t = owner.get(f"{P}/price-books/template.csv")
    assert t.status_code == 200
    lines = t.text.lstrip("﻿").strip().split("\r\n")
    assert lines[0] == "sku,product_name,unit,rate,currency,customer_ref,group_code"
    assert "CAS,Cassava (frozen),LB,1.5000,CAD,," in lines
    assert any(line.startswith("ACK,'=Ackee tins,BOX,54.0000") for line in lines)
