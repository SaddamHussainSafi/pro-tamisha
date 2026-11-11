# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Trash cascade (migration 0026): the Super Admin's delete takes what depends on a record into
the same Trash item and restores it as one unit; loose links are cleared and put back; the
protected books (invoices, payments, stock, the audit log) refuse it with what to do instead.
The confirm dialog's preview names all of it first."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pro_trading.db import get_sessionmaker

from .conftest import idem
from .test_invoicing import issue, sales, stock, wh  # noqa: F401 - fixtures re-used
from .test_ocr import drain, resolve, sheet, upload
from .test_orders import P, code, confirmed, new_order, open_pick, owner, picked, setup, user  # noqa: F401
from .test_trash import audits


def preview(api, kind, item_id):
    r = api.get(f"{P}/trash/preview/{kind}/{item_id}")
    assert r.status_code == 200, r.text
    return r.json()


def by_table(refs):
    return {r["table"]: r for r in refs}


def ocr_reads(job_id):
    with get_sessionmaker()() as db:
        return db.execute(
            text(
                "select e.id, e.status, e.customer_id, e.sales_order_id, "
                "(select count(*) from ocr_fields f where f.extraction_id = e.id), "
                "(select count(*) from ocr_corrections c join ocr_fields f on f.id = c.field_id "
                " where f.extraction_id = e.id) "
                "from ocr_extractions e where e.scan_job_id = :j order by e.run_no"
            ),
            {"j": job_id},
        ).all()


# ------------------------------------------------------------------ the owner's example


def test_a_scan_takes_its_ocr_reads_and_comes_back_whole(owner, setup, world):
    job = upload(owner, world, sheet(world, {"CAS": "15", "PLN": "20?"}))
    drain(world)
    r = owner.post(f"{P}/ocr/extractions", json={"scan_job_id": job["id"]}, headers=idem())
    assert r.status_code == 201, r.text  # read again: two OCR reads use the scan
    drain(world)
    ex = owner.get(f"{P}/ocr/extractions/{r.json()['id']}").json()
    pln = next(f for f in ex["fields"] if f["raw_text"] == "20")
    r = resolve(
        owner, ex, pln, "CORRECT", product_id=setup["p"]["PLN"], unit_code="PIECE", qty="20"
    )
    assert r.status_code == 200, r.text
    before = ocr_reads(job["id"])
    assert len(before) == 2 and before[1][5] == 1  # 2 reads, one correction

    p = preview(owner, "scan", job["id"])
    assert p["deletable"] and p["blocked"] == [] and p["unlinked"] == []
    goes = by_table(p["goes_with"])
    assert goes["ocr_extractions"]["count"] == 2 and goes["ocr_extractions"]["noun"] == "OCR read"
    assert goes["ocr_extractions"]["examples"] == [job["job_no"]]
    assert goes["ocr_fields"]["count"] == before[0][4] + before[1][4]
    assert goes["ocr_corrections"]["count"] == 1 and goes["source_documents"]["count"] == 1

    r = owner.delete(f"{P}/scans/jobs/{job['id']}", headers=idem())
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["label"] == f"Scan {job['job_no']}"
    assert owner.get(f"{P}/scans/jobs/{job['id']}").status_code == 404
    assert owner.get(f"{P}/ocr/extractions/{ex['id']}").status_code == 404
    assert ocr_reads(job["id"]) == []
    [(actor, _, _, detail)] = audits(world.company_id, "trash.moved", job["id"])
    assert actor == world.ids["owner"] and detail["snapshot"]["job_no"] == job["job_no"]
    assert detail["removed_with_it"]["ocr_extractions"] == 2
    assert detail["removed_with_it"]["ocr_corrections"] == 1

    r = owner.post(f"{P}/trash/{item['id']}/restore", headers=idem())
    assert r.status_code == 200, r.text
    assert owner.get(f"{P}/scans/jobs/{job['id']}").json()["status"] == "UPLOADED"
    assert ocr_reads(job["id"]) == before
    back = owner.get(f"{P}/ocr/extractions/{ex['id']}").json()
    assert [c["action"] for f in back["fields"] for c in f["corrections"]] == ["CORRECT"]
    assert len(audits(world.company_id, "trash.restored", job["id"])) == 1


# ------------------------------------------------------------------ cascade and links


def test_a_customer_takes_its_orders_and_unlinks_its_ocr_reads(owner, setup, world):
    draft = new_order(owner, world, setup, [("CAS", "BOX", "2")]).json()
    conf = confirmed(owner, world, setup, [("PLN", "PIECE", "5")])
    pick = open_pick(owner, conf).json()
    job = upload(owner, world, sheet(world, {"CAS": "3"}))
    drain(world)
    [(ex_id, _, cust_id, _, _, _)] = ocr_reads(job["id"])
    assert str(cust_id) == setup["cust"]

    p = preview(owner, "customer", setup["cust"])
    assert p["deletable"], p
    goes = by_table(p["goes_with"])
    assert goes["sales_orders"]["count"] == 2
    assert set(goes["sales_orders"]["examples"]) == {draft["order_no"], conf["order_no"]}
    assert goes["picking_attempts"]["examples"] == [pick["pick_code"]]
    assert goes["price_entries"]["count"] == 1  # its own price in the active book
    assert by_table(p["unlinked"])["ocr_extractions"]["examples"] == [job["job_no"]]

    r = owner.delete(f"{P}/customers/{setup['cust']}", headers=idem())
    assert r.status_code == 200, r.text
    item = r.json()
    for o in (draft, conf):
        assert owner.get(f"{P}/sales-orders/{o['id']}").status_code == 404
    assert owner.get(f"{P}/pick-attempts/{pick['id']}").status_code == 404
    assert ocr_reads(job["id"])[0][2] is None  # the scan and its read stay, unmatched
    [(*_, detail)] = audits(world.company_id, "trash.moved", setup["cust"])
    assert detail["removed_with_it"]["sales_orders"] == 2
    assert detail["unlinked"] == {"ocr_extractions": 1}

    r = owner.post(f"{P}/trash/{item['id']}/restore", headers=idem())
    assert r.status_code == 200, r.text
    assert owner.get(f"{P}/sales-orders/{conf['id']}").json()["status"] == "PICKING"
    assert owner.get(f"{P}/pick-attempts/{pick['id']}").json()["pick_code"] == pick["pick_code"]
    assert len(owner.get(f"{P}/sales-orders/{draft['id']}").json()["lines"]) == 1
    assert str(ocr_reads(job["id"])[0][2]) == setup["cust"]
    assert ocr_reads(job["id"])[0][0] == ex_id


def test_a_group_unlinks_its_customers_and_relinks_them(owner, setup, world):
    g = owner.post(
        f"{P}/customer-groups", json={"code": "TRASHG", "name": "Trash group"}, headers=idem()
    ).json()
    mine = owner.post(
        f"{P}/customers",
        json={"external_ref": "C-TG1", "name": "Grouped", "group_id": g["id"]},
        headers=idem(),
    ).json()
    moved = owner.post(
        f"{P}/customers",
        json={"external_ref": "C-TG2", "name": "Moves on", "group_id": g["id"]},
        headers=idem(),
    ).json()
    p = preview(owner, "customer_group", g["id"])
    assert p["goes_with"] == [] and by_table(p["unlinked"])["customers"]["count"] == 2

    item = owner.delete(f"{P}/customer-groups/{g['id']}", headers=idem()).json()
    assert owner.get(f"{P}/customers/{mine['id']}").json()["group_id"] is None
    # someone puts one customer in another group meanwhile; the restore leaves that alone
    g2 = owner.post(
        f"{P}/customer-groups", json={"code": "OTHERG", "name": "Other"}, headers=idem()
    ).json()
    cur = owner.get(f"{P}/customers/{moved['id']}").json()
    r = owner.patch(
        f"{P}/customers/{moved['id']}",
        json={"version": cur["version"], "group_id": g2["id"]},
        headers=idem(),
    )
    assert r.status_code == 200, r.text

    assert owner.post(f"{P}/trash/{item['id']}/restore", headers=idem()).status_code == 200
    assert owner.get(f"{P}/customers/{mine['id']}").json()["group_id"] == g["id"]
    assert owner.get(f"{P}/customers/{moved['id']}").json()["group_id"] == g2["id"]


def test_a_product_takes_its_draft_lines_but_not_other_orders(owner, setup, world):
    draft = new_order(owner, world, setup, [("CAS", "BOX", "2"), ("PLN", "PIECE", "3")]).json()
    conf = confirmed(owner, world, setup, [("CAS", "BOX", "1")])

    r = owner.delete(f"{P}/products/{setup['p']['CAS']}", headers=idem())
    assert r.status_code == 409 and code(r) == "IN_USE", r.text
    err = r.json()["error"]
    lines = by_table(err["references"])["sales_order_lines"]
    assert lines["examples"] == [conf["order_no"]] and lines["count"] == 1
    assert "Delete those orders first." in err["message"] and err["instead"] == "deactivate"

    # the confirmed order (no invoice) can go, with its lines; then the product
    assert owner.delete(f"{P}/sales-orders/{conf['id']}", headers=idem()).status_code == 200
    p = preview(owner, "product", setup["p"]["CAS"])
    goes = by_table(p["goes_with"])
    assert goes["sales_order_lines"]["examples"] == [draft["order_no"]]
    assert goes["price_entries"]["count"] == 3  # base BOX + LB + the customer's own price
    r = owner.delete(f"{P}/products/{setup['p']['CAS']}", headers=idem())
    assert r.status_code == 200, r.text
    left = owner.get(f"{P}/sales-orders/{draft['id']}").json()["lines"]
    assert [ln["sku"] for ln in left] == ["PLN"]

    assert owner.post(f"{P}/trash/{r.json()['id']}/restore", headers=idem()).status_code == 200
    left = owner.get(f"{P}/sales-orders/{draft['id']}").json()["lines"]
    assert sorted(ln["sku"] for ln in left) == ["CAS", "PLN"]


# ------------------------------------------------------------------ what stays protected


def test_protected_records_refuse_and_offer_what_to_do_instead(owner, sales, wh, setup, world):
    stock(owner, world, setup, "CAS", "BOX", "10")
    o = confirmed(owner, world, setup, [("CAS", "BOX", "2")])
    pk = picked(owner, wh, o, [(0, "2", None)])
    inv = issue(owner, pk)
    assert inv.status_code == 201, inv.text
    inv = inv.json()

    r = owner.delete(f"{P}/customers/{setup['cust']}", headers=idem())
    assert r.status_code == 409 and code(r) == "IN_USE", r.text
    err = r.json()["error"]
    assert by_table(err["references"])["invoices"]["examples"] == [inv["invoice_no"]]
    assert err["instead"] == "deactivate" and "Deactivate it instead" in err["message"]
    p = preview(owner, "customer", setup["cust"])
    assert not p["deletable"] and p["instead"] == "deactivate" and p["goes_with"] == []
    assert "invoices" in by_table(p["blocked"])

    r = owner.delete(f"{P}/products/{setup['p']['CAS']}", headers=idem())
    refs = by_table(r.json()["error"]["references"])
    assert r.status_code == 409 and {"stock_movements", "invoice_lines"} <= set(refs)
    r = owner.delete(f"{P}/sales-orders/{o['id']}", headers=idem())
    assert r.status_code == 409 and "stays with its invoice" in r.text
    assert audits(world.company_id, "trash.moved") == []

    # one click instead: deactivate, audited, idempotent; others may not
    body = {"kind": "customer", "item_id": setup["cust"]}
    assert sales.post(f"{P}/trash/instead", json=body, headers=idem()).status_code == 403
    key = idem()
    r = owner.post(f"{P}/trash/instead", json=body, headers=key)
    assert r.status_code == 200 and r.json()["changed"] and r.json()["action"] == "deactivate"
    assert owner.post(f"{P}/trash/instead", json=body, headers=key).json() == r.json()
    assert not owner.post(f"{P}/trash/instead", json=body, headers=idem()).json()["changed"]
    assert owner.get(f"{P}/customers/{setup['cust']}").json()["active"] is False
    [(actor, ttype, _, detail)] = audits(world.company_id, "customer.update", setup["cust"])
    assert actor == world.ids["owner"] and detail["instead_of_delete"] is True
    r = owner.post(f"{P}/trash/instead", json={"kind": "scan", "item_id": o["id"]}, headers=idem())
    assert r.status_code == 422


def test_a_user_with_history_is_suspended_instead(owner, user, world):
    user("sales")  # signing in leaves history in the audit log
    r = owner.delete(f"{P}/employees/{world.ids['sales']}", headers=idem())
    assert r.status_code == 409 and r.json()["error"]["instead"] == "suspend"
    r = owner.post(
        f"{P}/trash/instead",
        json={"kind": "user", "item_id": str(world.ids["sales"])},
        headers=idem(),
    )
    assert r.status_code == 200 and r.json()["changed"], r.text
    staff = {e["id"]: e for e in owner.get(f"{P}/employees").json()}
    assert staff[str(world.ids["sales"])]["status"] == "SUSPENDED"
    r = owner.post(
        f"{P}/trash/instead",
        json={"kind": "user", "item_id": str(world.ids["owner"])},
        headers=idem(),
    )
    assert r.status_code == 422 and code(r) == "SELF_CHANGE"


# ------------------------------------------------------------------ the database still guards


def test_new_guard_bypasses_work_only_in_trash_mode(owner, setup, world):
    conf = confirmed(owner, world, setup, [("CAS", "BOX", "1")])
    job = upload(owner, world, sheet(world, {"CAS": "3"}))
    drain(world)
    [(ex_id, *_)] = ocr_reads(job["id"])
    statements = [
        ("delete from ocr_extractions where id = :i", ex_id, "cannot be deleted"),
        ("delete from sales_order_lines where order_id = :i", conf["id"], "only be removed"),
        ("delete from price_entries where version_id = :i", setup["pb"], "immutable"),
        (
            "update ocr_fields set read_product_id = null where extraction_id = :i",
            ex_id,
            "immutable",
        ),
    ]
    with get_sessionmaker()() as db:
        for sql, i, msg in statements:
            with pytest.raises(DBAPIError, match=msg):
                db.execute(text(sql), {"i": i})
            db.rollback()
        # Trash mode clears a link, but never edits what was read
        db.execute(text("select set_config('pro_trading.trash', 'on', true)"))
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(
                text("update ocr_fields set raw_text = 'x' where extraction_id = :i"), {"i": ex_id}
            )
        db.rollback()
