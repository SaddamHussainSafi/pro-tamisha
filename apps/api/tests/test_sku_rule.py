"""Owner rule (2026-11-09): a product SKU is exactly three letters. Plus the order-sheet catalog."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError

from pro_trading.models import Product, ProductAlias
from scripts.load_catalog import CATALOG, load_catalog

from .conftest import idem

P = "/api/v1"


@pytest.fixture
def owner(make_api, world):
    o = make_api()
    o.login(world.emails["owner"])
    assert o.enroll_and_verify().status_code == 200
    return o


def body(sku: str) -> dict:
    return {"sku": sku, "name": f"Product {sku}", "stock_unit": "BOX"}


@pytest.mark.parametrize("sku", ["CA", "CASS", "CA1", "C-A", "", "ÇAS", "CAS-01"])
def test_sku_must_be_three_letters(owner, sku):
    r = owner.post(f"{P}/products", json=body(sku), headers=idem())
    assert r.status_code == 422


def test_lower_case_sku_is_stored_upper_case(owner):
    r = owner.post(f"{P}/products", json=body(" yam "), headers=idem())
    assert r.status_code == 201 and r.json()["sku"] == "YAM"


def test_database_rejects_other_skus(db, world):
    for sku in ["CASS", "ca", "C1A"]:
        with pytest.raises((IntegrityError, DataError)):
            db.execute(
                text(
                    "insert into products (company_id, sku, name, stock_unit) "
                    "values (:c, :s, 'x', 'BOX')"
                ),
                {"c": world.company_id, "s": sku},
            )
        db.rollback()


def test_order_sheet_catalog_is_well_formed():
    items = [i for group in CATALOG.values() for i in group]
    skus = [sku for sku, _, _ in items]
    names = [name.casefold() for _, name, _ in items]
    assert len(items) == 108
    assert len(set(skus)) == len(skus) and len(set(names)) == len(names)
    assert all(len(s) == 3 and s.isascii() and s.isalpha() and s.isupper() for s in skus)


def test_load_catalog_is_idempotent(db, world):
    with db.begin():
        assert load_catalog(db, world.company_id) == 108
    with db.begin():
        assert load_catalog(db, world.company_id) == 0
    prods = db.scalars(select(Product).where(Product.company_id == world.company_id)).all()
    assert len([p for p in prods if p.sku == "TUR"]) == 1
    alias = db.scalar(
        select(ProductAlias).where(
            ProductAlias.company_id == world.company_id, ProductAlias.alias == "Tumeric"
        )
    )
    assert alias is not None and isinstance(alias.product_id, uuid.UUID)
