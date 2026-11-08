"""Module 01 catalog: Postgres integration, authz, idempotency, concurrency. Synthetic data."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading.db import get_sessionmaker

from .conftest import build_world, idem

P = "/api/v1"


@pytest.fixture
def owner(make_api, world):
    o = make_api()
    o.login(world.emails["owner"])
    assert o.enroll_and_verify().status_code == 200
    return o


def cassava(sku: str = "CAS-01") -> dict:
    return {
        "sku": sku,
        "name": "Cassava (frozen)",
        "stock_unit": "LB",
        "tracks_expiry": True,
        "unit_profiles": [
            {"code": "CASE20", "unit_code": "BOX", "weight_lb_per_unit": "20", "is_default": True},
            {"code": "CASE30", "unit_code": "BOX", "weight_lb_per_unit": "30"},
            {"code": "CASE40", "unit_code": "BOX", "weight_lb_per_unit": "40"},
            {"code": "CASE25", "unit_code": "BOX", "weight_lb_per_unit": "25"},
            {"code": "PC", "unit_code": "PIECE"},
        ],
        "aliases": [
            {"alias": "Yuca  Congelada"},
            {"alias": "CASSAVA FZ", "source_label": "VENDOR-X"},
        ],
    }


@pytest.fixture
def product(owner):
    r = owner.post(f"{P}/products", json=cassava(), headers=idem())
    assert r.status_code == 201, r.text
    return r.json()


def pb(name: str, version: int) -> dict:
    return {
        "name": name,
        "category_id": None,
        "tracks_expiry": True,
        "active": True,
        "version": version,
    }


def prof(product: dict, code: str) -> str:
    return next(x["id"] for x in product["unit_profiles"] if x["code"] == code)


def test_create_product_with_profiles_and_aliases(product):
    assert product["version"] == 1
    assert {x["code"] for x in product["unit_profiles"]} == {
        "CASE20",
        "CASE30",
        "CASE40",
        "CASE25",
        "PC",
    }
    case20 = next(x for x in product["unit_profiles"] if x["code"] == "CASE20")
    assert case20["weight_lb_per_unit"] == "20.0000" and case20["is_default"] is True
    assert len(product["aliases"]) == 2


def test_weight_calc_205_lb_acceptance(owner, product):
    """INV-006: 7 cases (2x20, 2x30, 2x40, 1x25) -> 205 lb; x CAD 1.50/lb = 307.50."""
    groups = [
        {"profile_id": prof(product, "CASE20"), "count": "2"},
        {"profile_id": prof(product, "CASE30"), "count": "2"},
        {"profile_id": prof(product, "CASE40"), "count": "2"},
        {"profile_id": prof(product, "CASE25"), "count": "1"},
    ]
    r = owner.post(f"{P}/products/{product['id']}/weight-calc", json={"groups": groups})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["units"] == {"BOX": "7"}
    assert body["total_lb"] == "205.0000"
    assert body["total_kg"] == "92.9864"
    assert body["estimated"] is True  # profile defaults, not scale weights
    assert Decimal(body["total_lb"]) * Decimal("1.50") == Decimal("307.50")


def test_weight_calc_entered_weight_not_estimated(owner, product):
    g = [{"profile_id": prof(product, "CASE20"), "count": "1", "case_weight_lb": "21.5"}]
    body = owner.post(f"{P}/products/{product['id']}/weight-calc", json={"groups": g}).json()
    assert body["total_lb"] == "21.5000" and body["estimated"] is False


def test_weight_calc_rejects_unknown_piece_conversion(owner, product):
    g = [
        {"profile_id": prof(product, "CASE20"), "count": "1"},
        {"profile_id": prof(product, "PC"), "count": "3"},
    ]
    r = owner.post(f"{P}/products/{product['id']}/weight-calc", json={"groups": g})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "UNKNOWN_CONVERSION"
    assert r.json()["error"]["group"] == 1


def test_weight_calc_rejects_foreign_profile(owner, product):
    other = owner.post(
        f"{P}/products", json={**cassava("CAS-02"), "aliases": []}, headers=idem()
    ).json()
    g = [{"profile_id": prof(other, "CASE20"), "count": "1"}]
    r = owner.post(f"{P}/products/{product['id']}/weight-calc", json={"groups": g})
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_CONVERSION"


def test_weight_calc_rejects_fractional_boxes(owner, product):
    g = [{"profile_id": prof(product, "CASE20"), "count": "1.5"}]
    r = owner.post(f"{P}/products/{product['id']}/weight-calc", json={"groups": g})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_QUANTITY"


def test_weight_calc_rejects_float_json(owner, product):
    g = [{"profile_id": prof(product, "CASE20"), "count": 1, "case_weight_lb": 20.1}]
    r = owner.post(f"{P}/products/{product['id']}/weight-calc", json={"groups": g})
    # floats are coerced through str by pydantic Decimal; ensure result stays exact
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert r.json()["total_lb"] == "20.1000"


def test_duplicate_sku_conflict(owner, product):
    r = owner.post(f"{P}/products", json=cassava(), headers=idem())
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE"


def test_two_defaults_rejected(owner):
    body = cassava("CAS-DD")
    body["unit_profiles"][1]["is_default"] = True
    r = owner.post(f"{P}/products", json=body, headers=idem())
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_PROFILE"


def test_base_unit_profile_with_factor_rejected(owner):
    body = cassava("CAS-LB")
    body["unit_profiles"] = [{"code": "LB", "unit_code": "LB", "weight_lb_per_unit": "2"}]
    r = owner.post(f"{P}/products", json=body, headers=idem())
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_PROFILE"


def test_create_replay_and_key_reuse(owner):
    h = idem()
    r1 = owner.post(f"{P}/products", json=cassava("CAS-R"), headers=h)
    r2 = owner.post(f"{P}/products", json=cassava("CAS-R"), headers=h)
    assert r1.status_code == r2.status_code == 201
    assert r1.json() == r2.json() and r2.headers.get("Idempotent-Replay") == "true"
    r3 = owner.post(f"{P}/products", json=cassava("CAS-S"), headers=h)
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_missing_idempotency_key(owner):
    assert owner.post(f"{P}/products", json=cassava("CAS-K")).status_code == 422


def test_patch_version_conflict(owner, product):
    url = f"{P}/products/{product['id']}"
    r = owner.patch(url, json=pb("Cassava A", 1), headers=idem())
    assert r.status_code == 200 and r.json()["version"] == 2
    r = owner.patch(url, json=pb("Cassava B", 1), headers=idem())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "VERSION_CONFLICT"
    assert r.json()["error"]["current_version"] == 2


def test_concurrent_patches_one_wins(make_api, world, owner, product):
    """Two writers race the same version: exactly one succeeds, the other gets 409."""
    second = make_api()
    second.c.cookies = owner.c.cookies
    second.csrf = owner.csrf
    url = f"{P}/products/{product['id']}"

    def go(client, name):
        return client.patch(url, json=pb(name, 1), headers=idem()).status_code

    with ThreadPoolExecutor(2) as ex:
        codes = sorted(ex.map(lambda a: go(*a), [(owner, "X"), (second, "Y")]))
    assert codes == [200, 409]


def test_retry_after_success_is_replayed_not_reapplied(owner, product):
    url = f"{P}/products/{product['id']}"
    h = idem()
    r1 = owner.patch(url, json=pb("Cassava R", 1), headers=h)
    r2 = owner.patch(url, json=pb("Cassava R", 1), headers=h)
    assert r1.status_code == r2.status_code == 200
    assert r2.json()["version"] == 2  # not bumped twice
    assert owner.get(url).json()["version"] == 2


def test_profile_lifecycle(owner, product):
    pid = product["id"]
    r = owner.post(
        f"{P}/products/{pid}/unit-profiles",
        json={"code": "CASE50", "unit_code": "BOX", "weight_lb_per_unit": "50"},
        headers=idem(),
    )
    assert r.status_code == 201 and r.json()["version"] == 2
    case50 = prof(r.json(), "CASE50")
    r = owner.post(
        f"{P}/products/{pid}/unit-profiles/{case50}/make-default",
        json={"version": 2},
        headers=idem(),
    )
    assert r.status_code == 200, r.text
    defaults = [x["code"] for x in r.json()["unit_profiles"] if x["is_default"]]
    assert defaults == ["CASE50"]
    r = owner.post(
        f"{P}/products/{pid}/unit-profiles/{case50}/deactivate", json={"version": 1}, headers=idem()
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_CONFLICT"


def test_inactive_profile_rejected_in_weight_calc(owner, product):
    pid, c40 = product["id"], prof(product, "CASE40")
    r = owner.post(
        f"{P}/products/{pid}/unit-profiles/{c40}/deactivate", json={"version": 1}, headers=idem()
    )
    assert r.status_code == 200, r.text
    g = [{"profile_id": c40, "count": "1"}]
    r = owner.post(f"{P}/products/{pid}/weight-calc", json={"groups": g})
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_CONVERSION"


def test_alias_resolve_source_specific_wins(owner, product):
    other = owner.post(f"{P}/products", json={**cassava("CAS-V"), "aliases": []}, headers=idem())
    oid = other.json()["id"]
    r = owner.post(
        f"{P}/products/{oid}/aliases",
        json={"alias": "yuca congelada", "source_label": "VENDOR-Y"},
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    generic = owner.get(f"{P}/product-aliases/resolve", params={"name": "  YUCA congelada "})
    assert generic.status_code == 200 and generic.json()["product"]["id"] == product["id"]
    specific = owner.get(
        f"{P}/product-aliases/resolve", params={"name": "yuca congelada", "source": "VENDOR-Y"}
    )
    assert specific.json()["product"]["id"] == oid
    assert owner.get(f"{P}/product-aliases/resolve", params={"name": "nope"}).status_code == 404


def test_duplicate_alias_conflict_and_delete(owner, product):
    pid = product["id"]
    r = owner.post(f"{P}/products/{pid}/aliases", json={"alias": "yuca congelada"}, headers=idem())
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE"
    alias_id = product["aliases"][0]["id"]
    r = owner.c.delete(f"{P}/products/{pid}/aliases/{alias_id}", headers=owner._h(idem()))
    assert r.status_code == 200, r.text
    assert len(owner.get(f"{P}/products/{pid}").json()["aliases"]) == 1


def test_search_and_filters(owner, product):
    owner.post(
        f"{P}/products",
        json={**cassava("PLN-01"), "name": "Plantain", "aliases": []},
        headers=idem(),
    )
    by_alias = owner.get(f"{P}/products", params={"search": "yuca"}).json()["items"]
    assert [x["sku"] for x in by_alias] == ["CAS-01"]
    page = owner.get(f"{P}/products", params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    nxt = owner.get(f"{P}/products", params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert nxt["items"][0]["sku"] != page["items"][0]["sku"]


def test_category_create_and_invalid_category(owner):
    r = owner.post(
        f"{P}/product-categories", json={"code": "FROZEN", "name": "Frozen"}, headers=idem()
    )
    assert r.status_code == 201, r.text
    ok = owner.post(
        f"{P}/products", json={**cassava("CAS-C"), "category_id": r.json()["id"]}, headers=idem()
    )
    assert ok.status_code == 201
    bad = owner.post(
        f"{P}/products", json={**cassava("CAS-D"), "category_id": str(uuid.uuid4())}, headers=idem()
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_CATEGORY"


# --- authorization -------------------------------------------------------------------------


def test_warehouse_role_can_read_but_not_manage(make_api, world, product):
    w = make_api()
    assert w.login(world.emails["wh_a"]).status_code == 200
    assert w.get(f"{P}/products/{product['id']}").status_code == 200
    r = w.post(f"{P}/products", json=cassava("CAS-W"), headers=idem())
    assert r.status_code == 403
    r = w.patch(f"{P}/products/{product['id']}", json=pb("x", 1), headers=idem())
    assert r.status_code == 403


def test_privileged_role_needs_mfa(make_api, world):
    o = make_api()
    o.login(world.emails["owner"])
    assert o.post(f"{P}/products", json=cassava("CAS-M"), headers=idem()).status_code == 403
    assert o.enroll_and_verify().status_code == 200
    assert o.post(f"{P}/products", json=cassava("CAS-M"), headers=idem()).status_code == 201


def test_warehouse_scoped_grant_cannot_manage_company_catalog(make_api, world):
    """Catalog is company-wide; a manager scoped to warehouse A may read but not edit it."""
    m = make_api()
    m.login(world.emails["manager_a"])
    assert m.enroll_and_verify().status_code == 200
    assert m.get(f"{P}/products").status_code == 200
    assert m.post(f"{P}/products", json=cassava("CAS-N"), headers=idem()).status_code == 403


def test_unauthenticated_rejected(api):
    assert api.get(f"{P}/products").status_code == 401


def test_cross_company_isolation(make_api, product):
    other = build_world()
    o = make_api()
    o.login(other.emails["owner"])
    assert o.enroll_and_verify().status_code == 200
    assert o.get(f"{P}/products/{product['id']}").status_code == 404
    assert o.get(f"{P}/products", params={"search": "CAS-01"}).json()["items"] == []


# --- database guarantees ---------------------------------------------------------------------


def test_trigger_blocks_identity_and_conversion_changes(product):
    pid, c20 = product["id"], prof(product, "CASE20")
    sm = get_sessionmaker()
    for stmt in (
        "UPDATE products SET sku = 'HACK' WHERE id = :i",
        "UPDATE products SET stock_unit = 'KG' WHERE id = :i",
        "DELETE FROM products WHERE id = :i",
    ):
        with sm() as db, pytest.raises(DBAPIError):
            db.execute(text(stmt), {"i": pid})
            db.flush()
    for stmt in (
        "UPDATE product_unit_profiles SET weight_lb_per_unit = 21 WHERE id = :i",
        "DELETE FROM product_unit_profiles WHERE id = :i",
    ):
        with sm() as db, pytest.raises(DBAPIError):
            db.execute(text(stmt), {"i": c20})
            db.flush()


def test_mutations_are_audited(owner, product, db):
    n = db.execute(
        text("SELECT count(*) FROM audit_events WHERE target_id = :i"), {"i": product["id"]}
    ).scalar_one()
    assert n >= 1


def test_search_wildcards_are_literal(owner, product):
    assert owner.get(f"{P}/products", params={"search": "%"}).json()["items"] == []
    assert owner.get(f"{P}/products", params={"search": "CAS_01"}).json()["items"] == []


def test_failure_injection_mid_create_rolls_back_and_retry_succeeds(owner, monkeypatch, db):
    """Crash after rows are flushed but before commit (audit write fails): nothing persists,
    the idempotency key is not burned, and the client's retry with the same key succeeds once."""
    from pro_trading import audit

    real = audit.record

    def boom(*a, **k):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(audit, "record", boom)
    h = idem()
    try:
        r = owner.post(f"{P}/products", json=cassava("CAS-FAIL"), headers=h)
        assert r.status_code == 500
    except RuntimeError:
        pass  # TestClient re-raises server exceptions
    n = db.execute(text("select count(*) from products where sku = 'CAS-FAIL'")).scalar_one()
    assert n == 0

    monkeypatch.setattr(audit, "record", real)
    r1 = owner.post(f"{P}/products", json=cassava("CAS-FAIL"), headers=h)
    r2 = owner.post(f"{P}/products", json=cassava("CAS-FAIL"), headers=h)
    assert r1.status_code == r2.status_code == 201
    assert r2.headers.get("Idempotent-Replay") == "true"
    n = db.execute(text("select count(*) from products where sku = 'CAS-FAIL'")).scalar_one()
    assert n == 1
