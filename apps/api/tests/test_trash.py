# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Trash bin (migration 0020): only a Super Admin with two-step sign-in moves records to the
Trash; every move, restore and purge is audited with who, what and a snapshot; repeats are
idempotent; others can only ask. What goes with a record, and what still refuses it, is
covered in test_trash_cascade.py (migration 0026)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading import trash
from pro_trading.db import get_sessionmaker

from .conftest import idem
from .test_invoicing import sales, wh  # noqa: F401 - fixtures re-used
from .test_orders import P, code, confirmed, new_order, open_pick, owner, setup, user  # noqa: F401


def audits(company_id, action, target_id=None):
    q = "select actor_id, target_type, target_id, detail_redacted from audit_events "
    q += "where company_id = :c and action = :a"
    params = {"c": company_id, "a": action}
    if target_id:
        q += " and target_id = :t"
        params["t"] = str(target_id)
    with get_sessionmaker()() as db:
        return db.execute(text(q + " order by created_at"), params).all()


def product(api, sku="TRS", **extra):
    body = {
        "sku": sku,
        "name": "Trash test",
        "stock_unit": "BOX",
        "unit_profiles": [{"code": "CASE10", "unit_code": "BOX", "is_default": True}],
        "aliases": [{"alias": "Trashy thing"}],
        **extra,
    }
    r = api.post(f"{P}/products", json=body, headers=idem())
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ who may delete


def test_only_a_super_admin_with_two_step_sign_in_can_trash(make_api, world, user):
    plain = make_api()
    assert plain.login(world.emails["owner"]).status_code == 200
    r = plain.delete(f"{P}/products/{uuid.uuid4()}", headers=idem())
    assert r.status_code == 403 and code(r) == "TWO_STEP_REQUIRED"
    assert plain.get(f"{P}/trash").status_code == 200  # reading the Trash is allowed

    assert plain.enroll_and_verify().status_code == 200
    p = product(plain)
    for name in ("manager_a", "sales", "finance", "admin_a"):  # admin_a: warehouse-scoped only
        other = user(name)
        r = other.delete(f"{P}/products/{p['id']}", headers=idem())
        assert r.status_code == 403, (name, r.text)
        assert other.get(f"{P}/trash").status_code == 403
    assert plain.get(f"{P}/products/{p['id']}").status_code == 200

    r = plain.delete(f"{P}/products/{p['id']}", headers=idem())
    assert r.status_code == 200, r.text
    assert plain.get(f"{P}/products/{p['id']}").status_code == 404


# ------------------------------------------------------------------ move, restore, purge


def test_trash_restore_and_delete_forever_are_audited_and_idempotent(owner, world):
    p = product(owner)
    key = idem()
    r = owner.delete(f"{P}/products/{p['id']}", headers=key)
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["kind"] == "product" and item["label"] == "TRS — Trash test"
    assert item["days_left"] == 30 and not item["already_in_trash"]

    again = owner.delete(f"{P}/products/{p['id']}", headers=key)
    assert again.headers.get("idempotent-replay") == "true" and again.json() == item
    fresh = owner.delete(f"{P}/products/{p['id']}", headers=idem())
    assert fresh.status_code == 200 and fresh.json()["already_in_trash"]
    assert fresh.json()["id"] == item["id"]

    [(actor, ttype, tid, detail)] = audits(world.company_id, "trash.moved", p["id"])
    assert actor == world.ids["owner"] and ttype == "product" and tid == p["id"]
    assert detail["snapshot"]["sku"] == "TRS" and detail["label"] == "TRS — Trash test"
    assert detail["removed_with_it"] == {"product_aliases": 1, "product_unit_profiles": 1}

    listed = owner.get(f"{P}/trash").json()
    assert [t["id"] for t in listed] == [item["id"]]

    r = owner.post(f"{P}/trash/{item['id']}/restore", headers=idem())
    assert r.status_code == 200, r.text
    back = owner.get(f"{P}/products/{p['id']}").json()
    assert back["sku"] == "TRS" and len(back["aliases"]) == 1
    assert [u["is_default"] for u in back["unit_profiles"]] == [True]
    assert owner.get(f"{P}/trash").json() == []
    assert len(audits(world.company_id, "trash.restored", p["id"])) == 1

    item = owner.delete(f"{P}/products/{p['id']}", headers=idem()).json()
    r = owner.delete(f"{P}/trash/{item['id']}", headers=idem())
    assert r.status_code == 200 and r.json() == {"purged": 1}
    assert owner.post(f"{P}/trash/{item['id']}/restore", headers=idem()).status_code == 404
    assert owner.get(f"{P}/products/{p['id']}").status_code == 404
    [(actor, *_)] = audits(world.company_id, "trash.purged", p["id"])
    assert actor == world.ids["owner"]


def test_restore_refuses_a_clash(owner):
    p = product(owner)
    item = owner.delete(f"{P}/products/{p['id']}", headers=idem()).json()
    product(owner)  # same SKU again while the first is in the Trash
    r = owner.post(f"{P}/trash/{item['id']}/restore", headers=idem())
    assert r.status_code == 409 and code(r) == "RESTORE_CONFLICT", r.text
    assert len(owner.get(f"{P}/trash").json()) == 1  # still there


def test_items_are_purged_after_30_days(owner, world):
    p = product(owner)
    item = owner.delete(f"{P}/products/{p['id']}", headers=idem()).json()
    assert item["purge_after"] > (datetime.now(UTC) + timedelta(days=29)).isoformat()
    with get_sessionmaker()() as db:
        assert trash.purge_expired(db, now=datetime.now(UTC) + timedelta(days=29)) == 0
        assert trash.purge_expired(db, now=datetime.now(UTC) + timedelta(days=31)) >= 1
    assert owner.get(f"{P}/trash").json() == []
    [(actor, *_)] = audits(world.company_id, "trash.auto_purged", p["id"])
    assert actor is None


def test_empty_trash(owner):
    for sku in ("TRA", "TRB"):
        p = product(owner, sku)
        assert owner.delete(f"{P}/products/{p['id']}", headers=idem()).status_code == 200
    r = owner.post(f"{P}/trash/empty", headers=idem())
    assert r.status_code == 200 and r.json() == {"purged": 2}


# ------------------------------------------------------------------ what blocks a delete


def test_a_line_on_a_confirmed_order_names_the_order(owner, setup, world):
    o = confirmed(owner, world, setup, [("PLN", "PIECE", "2")])
    r = owner.delete(f"{P}/products/{setup['p']['PLN']}", headers=idem())
    assert r.status_code == 409 and code(r) == "IN_USE", r.text
    refs = {x["table"]: x for x in r.json()["error"]["references"]}
    assert refs["sales_order_lines"]["examples"] == [o["order_no"]]
    assert o["order_no"] in r.json()["error"]["message"]
    assert owner.get(f"{P}/products/{setup['p']['PLN']}").status_code == 200
    assert audits(world.company_id, "trash.moved") == []

    # the unused, unpriced product goes
    r = owner.delete(f"{P}/products/{setup['p']['NOP']}", headers=idem())
    assert r.status_code == 200, r.text


def test_state_rules(owner, setup, world, wh):
    draft = new_order(owner, world, setup, [("CAS", "BOX", "2")]).json()
    r = owner.delete(f"{P}/sales-orders/{draft['id']}", headers=idem())
    assert r.status_code == 200, r.text
    assert owner.get(f"{P}/sales-orders/{draft['id']}").status_code == 404
    r = owner.post(f"{P}/trash/{r.json()['id']}/restore", headers=idem())
    assert r.status_code == 200
    assert len(owner.get(f"{P}/sales-orders/{draft['id']}").json()["lines"]) == 1

    o = confirmed(owner, world, setup, [("CAS", "BOX", "2")])
    pk = open_pick(owner, o).json()
    r = owner.delete(f"{P}/pick-attempts/{pk['id']}", headers=idem())
    assert r.status_code == 409 and "Abort it first" in r.text
    ab = owner.post(
        f"{P}/pick-attempts/{pk['id']}/abort",
        json={"version": pk["version"], "reason": "sheet lost"},
        headers=idem(),
    )
    assert ab.status_code == 200, ab.text
    r = owner.delete(f"{P}/pick-attempts/{pk['id']}", headers=idem())
    assert r.status_code == 200, r.text
    # a confirmed order (no invoice) goes too, with its picking sheets
    pk2 = open_pick(owner, owner.get(f"{P}/sales-orders/{o['id']}").json()).json()
    r = owner.delete(f"{P}/sales-orders/{o['id']}", headers=idem())
    assert r.status_code == 200, r.text
    assert owner.get(f"{P}/pick-attempts/{pk2['id']}").status_code == 404
    assert owner.post(f"{P}/trash/{r.json()['id']}/restore", headers=idem()).status_code == 200
    assert owner.get(f"{P}/sales-orders/{o['id']}").json()["status"] == "PICKING"

    prod = product(
        owner,
        unit_profiles=[
            {"code": "CASE10", "unit_code": "BOX", "is_default": True},
            {"code": "CASE20", "unit_code": "BOX"},
        ],
    )
    sizes = {u["code"]: u for u in prod["unit_profiles"]}
    r = owner.delete(f"{P}/unit-profiles/{sizes['CASE10']['id']}", headers=idem())
    assert r.status_code == 409 and "default" in r.text
    r = owner.delete(f"{P}/unit-profiles/{sizes['CASE20']['id']}", headers=idem())
    assert r.status_code == 200 and r.json()["label"] == "CASE20 (TRS)"
    left = owner.get(f"{P}/products/{prod['id']}").json()["unit_profiles"]
    assert [u["code"] for u in left] == ["CASE10"]


def test_guard_triggers_still_refuse_outside_the_trash(owner, setup, world):
    o = confirmed(owner, world, setup, [("CAS", "BOX", "1")])
    draft = new_order(owner, world, setup, [("CAS", "BOX", "1")]).json()
    with get_sessionmaker()() as db:
        with pytest.raises(DBAPIError, match="cannot be deleted"):  # draft, but no Trash flag
            db.execute(text("delete from sales_orders where id = :i"), {"i": draft["id"]})
        db.rollback()
        db.execute(text("select set_config('pro_trading.trash', 'on', true)"))
        with pytest.raises(DBAPIError, match="foreign key"):  # flag on: its lines still hold it
            db.execute(text("delete from sales_orders where id = :i"), {"i": o["id"]})
        db.rollback()
        db.execute(text("select set_config('pro_trading.trash', 'on', true)"))
        with pytest.raises(DBAPIError, match="foreign key"):  # foreign keys still hold
            db.execute(text("delete from products where id = :i"), {"i": setup["p"]["NOP"]})
        db.rollback()


# ------------------------------------------------------------------ users and notifications


def test_users(owner, world, user):
    r = owner.post(
        f"{P}/employees",
        json={
            "email": f"ivy.{uuid.uuid4().hex[:6]}@example.test",
            "display_name": "Ivy",
            "role_code": "VIEWER",
        },
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    ivy = r.json()
    r = owner.delete(f"{P}/employees/{ivy['id']}", headers=idem())
    assert r.status_code == 200, r.text
    assert all(e["id"] != ivy["id"] for e in owner.get(f"{P}/employees").json())
    t = r.json()
    assert owner.post(f"{P}/trash/{t['id']}/restore", headers=idem()).status_code == 200
    back = next(e for e in owner.get(f"{P}/employees").json() if e["id"] == ivy["id"])
    assert back["roles"] == ["VIEWER"]

    r = owner.delete(f"{P}/employees/{world.ids['owner']}", headers=idem())
    assert r.status_code == 409 and "your own account" in r.text

    user("sales")  # signing in leaves history
    r = owner.delete(f"{P}/employees/{world.ids['sales']}", headers=idem())
    assert r.status_code == 409 and code(r) == "IN_USE" and "audit log" in r.text
    assert "Suspend them instead" in r.json()["error"]["message"]


# ------------------------------------------------------------------ delete requests


def test_others_can_only_ask_and_the_super_admin_decides(owner, sales, setup, world):
    draft = new_order(sales, world, setup, [("CAS", "BOX", "1")]).json()
    body = {"kind": "sales_order", "item_id": draft["id"], "reason": "duplicate order"}

    assert sales.delete(f"{P}/sales-orders/{draft['id']}", headers=idem()).status_code == 403
    r = sales.post(f"{P}/delete-requests", json=body, headers=idem())
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "PENDING" and req["label"] == f"Order {draft['order_no']}"
    again = sales.post(f"{P}/delete-requests", json=body, headers=idem())
    assert again.status_code == 200 and again.json()["id"] == req["id"]

    # refused up front when it could not be deleted anyway, or not theirs to edit
    o = confirmed(sales, world, setup, [("CAS", "BOX", "1")])
    pk = open_pick(sales, o).json()
    r = sales.post(
        f"{P}/delete-requests",
        json={"kind": "picking_sheet", "item_id": pk["id"]},
        headers=idem(),
    )
    assert r.status_code == 409 and "Abort it first" in r.text
    r = sales.post(
        f"{P}/delete-requests",
        json={"kind": "product", "item_id": setup["p"]["NOP"]},
        headers=idem(),
    )
    assert r.status_code == 403
    assert sales.get(f"{P}/delete-requests").status_code == 403
    r = sales.post(f"{P}/delete-requests/{req['id']}/approve", headers=idem())
    assert r.status_code == 403

    # the Super Admin is told, then approves
    notes = owner.get(f"{P}/notifications").json()["items"]
    assert any(n["title"] == f"Delete requested: Order {draft['order_no']}" for n in notes)
    assert [x["id"] for x in owner.get(f"{P}/delete-requests").json()] == [req["id"]]
    r = owner.post(f"{P}/delete-requests/{req['id']}/approve", headers=idem())
    assert r.status_code == 200, r.text
    assert owner.get(f"{P}/sales-orders/{draft['id']}").status_code == 404
    done = owner.get(f"{P}/delete-requests", params={"status": "APPROVED"}).json()
    assert done[0]["id"] == req["id"] and done[0]["decided_by"] == "owner"
    r = owner.post(f"{P}/delete-requests/{req['id']}/approve", headers=idem())
    assert r.status_code == 409 and code(r) == "ALREADY_DECIDED"

    # numbers are never handed out twice, even after a delete
    assert d_next_ok(sales, world, setup)

    # dismissing leaves the record alone
    d2 = new_order(sales, world, setup, [("CAS", "BOX", "1")])
    assert d2.status_code == 201, d2.text
    d2 = d2.json()
    req2 = sales.post(
        f"{P}/delete-requests", json={**body, "item_id": d2["id"]}, headers=idem()
    ).json()
    r = owner.post(
        f"{P}/delete-requests/{req2['id']}/dismiss", json={"note": "keep it"}, headers=idem()
    )
    assert r.status_code == 200 and r.json()["status"] == "DISMISSED"
    assert owner.get(f"{P}/sales-orders/{d2['id']}").status_code == 200
    assert len(audits(world.company_id, "trash.request_dismissed", d2["id"])) == 1


def d_next_ok(api, world, setup):
    r = new_order(api, world, setup, [("CAS", "BOX", "1")])
    assert r.status_code == 201, r.text
    return api.delete(f"{P}/sales-orders/{r.json()['id']}", headers=idem()).status_code == 403


def test_notifications_can_be_trashed_and_restored(owner, sales, setup, world):
    draft = new_order(sales, world, setup, [("CAS", "BOX", "1")]).json()
    sales.post(
        f"{P}/delete-requests",
        json={"kind": "sales_order", "item_id": draft["id"]},
        headers=idem(),
    )
    [n] = [
        n
        for n in owner.get(f"{P}/notifications").json()["items"]
        if n["title"].startswith("Delete requested")
    ]
    r = owner.delete(f"{P}/notifications/{n['id']}", headers=idem())
    assert r.status_code == 200, r.text
    assert all(x["id"] != n["id"] for x in owner.get(f"{P}/notifications").json()["items"])
    assert owner.post(f"{P}/trash/{r.json()['id']}/restore", headers=idem()).status_code == 200
    assert any(x["id"] == n["id"] for x in owner.get(f"{P}/notifications").json()["items"])


# ------------------------------------------------------------------ scans and filed documents

PDF = b"%PDF-1.4\n% synthetic trash sheet\n%%EOF\n"


def scan_upload(api, world, job_type, data):
    q = f"warehouse_id={world.wh_a}&job_type={job_type}&filename=sheet.pdf"
    h = {**api._h(idem()), "Content-Type": "application/pdf"}
    r = api.c.post(f"{P}/scans/uploads?{q}", content=data, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_scans_and_filed_documents(owner, world):
    read = scan_upload(owner, world, "SALES_ORDER", PDF + b"%a\n")
    r = owner.delete(f"{P}/scans/jobs/{read['id']}", headers=idem())
    assert r.status_code == 200, r.text  # its OCR read goes with it
    [(_, _, _, detail)] = audits(world.company_id, "trash.moved", read["id"])
    assert detail["removed_with_it"]["ocr_extractions"] == 1

    other = scan_upload(owner, world, "OTHER", PDF + b"%b\n")
    r = owner.delete(f"{P}/scans/jobs/{other['id']}", headers=idem())
    assert r.status_code == 200, r.text
    assert owner.get(f"{P}/scans/jobs/{other['id']}").status_code == 404
    [(_, _, _, detail)] = audits(world.company_id, "trash.moved", other["id"])
    removed = detail["removed_with_it"]
    assert removed["source_documents"] == 1 and removed["document_files"] == 1
    assert set(removed) <= {"source_documents", "document_files", "scan_events"}
    assert "content" not in detail["snapshot"]
    assert owner.post(f"{P}/trash/{r.json()['id']}/restore", headers=idem()).status_code == 200
    assert owner.get(f"{P}/scans/jobs/{other['id']}").json()["status"] == "UPLOADED"

    # the filed copy can go on its own; the scanned file stays with the scan
    docs = owner.get(f"{P}/documents", params={"kind": "SCAN"}).json()["items"]
    [doc] = [d for d in docs if d["scan_job_no"] == other["job_no"]]
    r = owner.delete(f"{P}/documents/{doc['id']}", headers=idem())
    assert r.status_code == 200, r.text
    [(_, _, _, detail)] = audits(world.company_id, "trash.moved", doc["id"])
    assert detail["removed_with_it"] == {}
    assert owner.get(f"{P}/scans/jobs/{other['id']}").status_code == 200
