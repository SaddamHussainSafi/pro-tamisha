import pytest

from .conftest import idem


@pytest.fixture
def owner(make_api, world):
    o = make_api()
    o.login(world.emails["owner"])
    assert o.enroll_and_verify().status_code == 200
    return o


def test_create_requires_idempotency_key(owner):
    r = owner.post("/api/v1/warehouses", json={"code": "WC", "name": "C"})
    assert r.status_code == 422


def test_create_replay_returns_same_response(owner):
    h = idem()
    r1 = owner.post("/api/v1/warehouses", json={"code": "WC", "name": "C"}, headers=h)
    r2 = owner.post("/api/v1/warehouses", json={"code": "WC", "name": "C"}, headers=h)
    assert r1.status_code == r2.status_code == 201
    assert r1.json() == r2.json()
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert len([w for w in owner.get("/api/v1/warehouses").json() if w["code"] == "WC"]) == 1


def test_key_reuse_with_different_payload_conflicts(owner):
    h = idem()
    owner.post("/api/v1/warehouses", json={"code": "WD", "name": "D"}, headers=h)
    r = owner.post("/api/v1/warehouses", json={"code": "WE", "name": "E"}, headers=h)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_duplicate_code_conflict(owner):
    r = owner.post("/api/v1/warehouses", json={"code": "WA", "name": "Dup"}, headers=idem())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "DUPLICATE"


def test_optimistic_version_conflict(owner, world):
    url = f"/api/v1/warehouses/{world.wh_a}"
    assert (
        owner.patch(
            url, json={"name": "A1", "active": True, "version": 1}, headers=idem()
        ).status_code
        == 200
    )
    r = owner.patch(url, json={"name": "A2", "active": True, "version": 1}, headers=idem())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "VERSION_CONFLICT"
    assert r.json()["error"]["current_version"] == 2


def test_invalid_code_rejected(owner):
    r = owner.post("/api/v1/warehouses", json={"code": "bad code", "name": "x"}, headers=idem())
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION"
