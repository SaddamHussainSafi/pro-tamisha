# ruff: noqa: F811 - pytest fixtures are imported and re-used by name
"""Products › Case sizes list (unit profiles across products) and per-product price lookup."""

from .conftest import Api, build_world, idem
from .test_orders import P, owner, setup, user  # noqa: F401


def test_case_sizes_across_products(owner, user, setup):
    cas = setup["p"]["CAS"]
    r = owner.post(
        f"{P}/products/{cas}/unit-profiles",
        json={"code": "CASE35", "unit_code": "BOX", "weight_lb_per_unit": "35", "is_default": True},
        headers=idem(),
    )
    assert r.status_code == 201, r.text
    rows = owner.get(f"{P}/unit-profiles", params={"q": "cassava"}).json()
    assert [(x["sku"], x["code"], x["weight_lb_per_unit"], x["is_default"]) for x in rows] == [
        ("CAS", "CASE35", "35.0000", True),  # the new default is listed first
        ("CAS", "CASE40", "40.0000", False),
    ]
    assert {x["sku"] for x in owner.get(f"{P}/unit-profiles").json()} == {
        "CAS",
        "PLN",
        "NOP",
    }
    # deactivated sizes only when asked for
    prod = owner.get(f"{P}/products/{cas}").json()
    c40 = next(x for x in rows if x["code"] == "CASE40")
    r = owner.post(
        f"{P}/products/{cas}/unit-profiles/{c40['id']}/deactivate",
        json={"version": prod["version"]},
        headers=idem(),
    )
    assert r.status_code == 200, r.text
    assert [x["code"] for x in owner.get(f"{P}/unit-profiles", params={"q": "CAS"}).json()] == [
        "CASE35"
    ]
    every = owner.get(f"{P}/unit-profiles", params={"q": "CAS", "include_inactive": True})
    assert [(x["code"], x["active"]) for x in every.json()] == [("CASE35", True), ("CASE40", False)]
    # warehouse staff can read; another company sees nothing
    assert user("wh_a").get(f"{P}/unit-profiles").status_code == 200
    other = build_world()
    out = Api(owner.c.app)
    out.login(other.emails["owner"])
    assert out.enroll_and_verify().status_code == 200
    assert out.get(f"{P}/unit-profiles").json() == []


def test_price_entries_for_one_product(owner, setup):
    rows = owner.get(f"{P}/price-books/versions/{setup['pb']}/entries").json()["items"]
    assert {x["sku"] for x in rows} == {"CAS", "PLN"}
    only = owner.get(
        f"{P}/price-books/versions/{setup['pb']}/entries",
        params={"product_id": setup["p"]["CAS"]},
    ).json()
    assert only["total"] == 3 and {x["sku"] for x in only["items"]} == {"CAS"}
