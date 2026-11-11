# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Trash bin: website order-form entries (Order Connect). A converted entry can go too; the
sales order it became stays."""

from .conftest import idem
from .test_order_connect import (  # noqa: F401 - fixtures re-used
    OC,
    _fresh_get_limit,
    as_,
    cat,
    code,
    convert_body,
    live,
    move,
    ok,
    owner,
    received,
    settings_fast,
)

P_TRASH = "/api/v1/trash"


def test_website_entries_go_to_the_trash_even_converted(owner, received, world):
    assert owner.enroll_and_verify().status_code == 200
    r = owner.delete(f"{OC}/submissions/{received}", headers=idem())
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "website_entry"
    assert owner.get(f"{OC}/submissions/{received}").status_code == 404
    assert owner.post(f"{P_TRASH}/{r.json()['id']}/restore", headers=idem()).status_code == 200
    d = ok(owner.get(f"{OC}/submissions/{received}"))
    assert d["items"] and d["events"]

    ok(move(owner, received, "REVIEWING"))
    ok(move(owner, received, "APPROVED"))
    ok(
        owner.post(
            f"{OC}/submissions/{received}/convert",
            json=convert_body(owner, received, world),
            headers=idem(),
        ),
        201,
    )
    order_id = ok(owner.get(f"{OC}/submissions/{received}"))["sales_order_id"]
    r = owner.delete(f"{OC}/submissions/{received}", headers=idem())
    assert r.status_code == 200, r.text
    assert owner.get(f"{OC}/submissions/{received}").status_code == 404
    assert owner.get(f"/api/v1/sales-orders/{order_id}").status_code == 200
    assert owner.post(f"{P_TRASH}/{r.json()['id']}/restore", headers=idem()).status_code == 200
    assert ok(owner.get(f"{OC}/submissions/{received}"))["status"] == "CONVERTED"
