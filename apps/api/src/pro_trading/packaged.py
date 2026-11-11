"""Packaged items (owner, 2026-11-11): every product has a packaged twin, its own product with its
own price and stock, filed under the "Packaged" category, which is listed last.

The twin's SKU is the product's three letters plus "P" (CAL -> CALP) and its name ends in
" - Packaged", like the Focus items ("Callaloo Jamaican - Packaged"). On the office's paper order
sheet a number in the PKG column orders the packaged twin; the Q column orders the loose item.
New twins start with the product's stock unit, no case sizes and no price: those are set by hand.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from .models import Product, ProductCategory

CATEGORY_CODE = "PACKAGED"
CATEGORY_NAME = "Packaged"
SUFFIX = " - Packaged"


def is_packaged(sku: str) -> bool:
    return len(sku) == 4 and sku.endswith("P")


def twin_sku(sku: str) -> str:
    return f"{sku}P"


def base_sku(sku: str) -> str | None:
    return sku[:3] if is_packaged(sku) else None


def sku_order() -> tuple[ColumnElement[int], InstrumentedAttribute[str]]:
    """Loose items first, then every packaged item (the Packaged category at the bottom)."""
    return func.char_length(Product.sku), Product.sku


def category(db: Session, company_id: uuid.UUID) -> ProductCategory:
    cat = db.scalar(
        select(ProductCategory).where(
            ProductCategory.company_id == company_id, ProductCategory.code == CATEGORY_CODE
        )
    )
    if cat is None:
        cat = ProductCategory(company_id=company_id, code=CATEGORY_CODE, name=CATEGORY_NAME)
        db.add(cat)
        db.flush()
    return cat


def ensure_twins(
    db: Session, company_id: uuid.UUID, only: list[Product] | None = None
) -> list[Product]:
    """Create the packaged twin of each active loose product that has none. Returns the new ones."""
    prods = (
        only
        if only is not None
        else db.scalars(
            select(Product).where(Product.company_id == company_id, Product.active.is_(True))
        ).all()
    )
    loose = [p for p in prods if p.active and not is_packaged(p.sku)]
    if not loose:
        return []
    have = set(
        db.scalars(
            select(Product.sku).where(
                Product.company_id == company_id,
                Product.sku.in_([twin_sku(p.sku) for p in loose]),
            )
        ).all()
    )
    todo = [p for p in loose if twin_sku(p.sku) not in have]
    if not todo:
        return []
    cat = category(db, company_id)
    made = [
        Product(
            company_id=company_id,
            sku=twin_sku(p.sku),
            name=f"{p.name}{SUFFIX}",
            category_id=cat.id,
            stock_unit=p.stock_unit,
            tracks_expiry=p.tracks_expiry,
        )
        for p in todo
    ]
    db.add_all(made)
    db.flush()
    return made
