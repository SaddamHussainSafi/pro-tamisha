"""Load Tamisha Trading's product list (from the paper order sheet, 2026-11-09) into a company.

Every product gets a three-letter SKU, is sold by the BOX, and keeps the order sheet's
own spelling as an alias when it differs from the display name, so scans still match.
Idempotent: products whose SKU already exists are left alone.

    PYTHONPATH=src uv run python scripts/load_catalog.py ["Company legal name"]

Defaults to the demo company. Refuses to run in staging/production.
"""

import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.models import Company, Product, ProductAlias, ProductCategory
from pro_trading.routes.catalog import norm
from pro_trading.seed import DEMO_COMPANY

CATEGORIES = [
    ("VEG", "Vegetables"),
    ("PEP", "Peppers"),
    ("ROOT", "Roots"),
    ("HERB", "Herbs"),
    ("OTHER", "Others"),
    ("FRUIT", "Fruits"),
]

# category code -> [(sku, display name, name as printed on the order sheet)]
CATALOG: dict[str, list[tuple[str, str, str]]] = {
    "VEG": [
        ("BFR", "Breadfruit", "Breadfruit"),
        ("BNT", "Breadnut", "Breadnut"),
        ("CEG", "Chinese Eggplant", "Chinese Eggplant"),
        ("CBM", "Chinese Bitter Melon", "Chinese Bitter Melon"),
        ("IBM", "Indian Bitter Melon", "Indian Bitter Melon"),
        ("IEG", "Indian Eggplant", "Indian Eggplant"),
        ("GEG", "Guyana Eggplant", "Guyana Eggplant"),
        ("OKR", "Okra", "Okra"),
        ("IOK", "Indian Okra", "Indian Okra"),
        ("GMG", "Green Mango", "Green Mango"),
        ("DRM", "Drumstick", "Drumstick"),
        ("WLB", "White Long Beans", "White Long Beans"),
        ("DLB", "Dark Long Beans", "Dark Long Beans"),
        ("TUR", "Turmeric", "Tumeric"),
        ("SGY", "Seim (Guyana)", "Seim (Guyana)"),
        ("STD", "Seim (Trinidad)", "Seim (Trinidad)"),
        ("SQU", "Squash", "Squash"),
        ("PKJ", "Pumpkin (Jamaica)", "Pumpkin (Jam)"),
        ("PKC", "Pumpkin (Cut)", "Pumpkin (Cut)"),
        ("PKN", "Pumpkin", "Pumpkin"),
        ("AEG", "African Eggplant", "African Eggplant"),
        ("TEG", "Trinidad Eggplant", "Trinidad Eggplant"),
        ("GNC", "Ginger Chinese", "Ginger Chinese"),
        ("GNJ", "Ginger Jamaican", "Ginger Jamaican"),
    ],
    "PEP": [
        ("PIM", "Pimento Pepper", "Pimento Pepper"),
        ("RTC", "Red Thai Chilli", "Red Thai Chilli"),
        ("THC", "Thai Chilli", "Thai Chilli"),
        ("LCH", "Long Chilli", "Long Chilli"),
        ("HPP", "Hot Pepper", "Hot Pepper"),
        ("WIR", "Wiri Wiri Peppers", "Wierri Peppers"),
        ("SBP", "Scotch Bonnet Pepper", "Scotch Bonnet Pepper"),
        ("PGJ", "Pepper Green Jumbo", "Pepper Green Jumbo"),
        ("HPR", "Hot Pepper Red", "Hot Pepper Red"),
        ("POR", "Pepper Orange", "Pepper Orange"),
        ("PRL", "Pepper Red Large", "Pepper Red Large"),
    ],
    "ROOT": [
        ("CAS", "Cassava", "Cassava"),
        ("CHO", "Chocho", "Chocho"),
        ("EDD", "Eddoes", "Eddoes"),
        ("HSP", "Honduras Sweet Potato", "Honduras Sweet Potato"),
        ("DAS", "Dasheen", "Dasheen"),
        ("YYM", "Yellow Yam", "Yellow Yam"),
        ("SWP", "Sweet Potato", "Sweet Potato"),
        ("NYM", "Negro Yam (Jamaica)", "Negro Yam (Jam)"),
        ("RCO", "Red Coco", "Red Coco"),
        ("SYM", "Sweet Yam", "Sweet Yam"),
        ("RYM", "Renta Yam", "Renta Yam"),
        ("YAP", "Yampie", "Yampie"),
        ("AYG", "African Yam (Ghana)", "African Yam (Ghana)"),
        ("WYC", "White Yam (Colombia)", "White Yam (Col)"),
        ("WYR", "White Yam (Costa Rica)", "White Yam (CR)"),
        ("PSP", "Purple Sweet Potato", "Purple Sweet Potato"),
        ("MLL", "Malanga Lila", "Malanga Lila"),
    ],
    "HERB": [
        ("MMN", "Married Man", "Married Man"),
        ("BOR", "Bora (Guyana)", "Bora (Guyana)"),
        ("THG", "Thyme (Guyana)", "Thyme (Guyana)"),
        ("THJ", "Thyme (Jamaican)", "Thyme (Jamaican)"),
        ("ESC", "Escallion", "Escallion"),
        ("DSL", "Dasheen Leaves", "Dasheen Leaves"),
        ("CAL", "Callaloo", "Calalloo"),
        ("SHD", "Shadobeni", "Shadobeni"),
        ("CRL", "Curry Leaf", "Curry Leaf"),
    ],
    "OTHER": [
        ("ALO", "Aloe", "Aloe"),
        ("SCJ", "Sugar Cane (Jamaica)", "Sugar Cane (Jam)"),
        ("GPL", "Green Plantains", "Green Plantains"),
        ("RPL", "Ripe Plantain", "Ripe Plantain"),
        ("MPL", "Mocco Plantain", "Mocco Plantian"),
        ("GBN", "Green Banana", "Green Banana"),
        ("GBS", "Green Beans", "Green Beans"),
        ("LIM", "Lime", "Lime"),
        ("PEA", "Peas", "Peas"),
        ("SCB", "Sugarcane Bundle", "Sugarcane Bundle"),
        ("APL", "Apim Plantain", "Apim Plantain"),
    ],
    "FRUIT": [
        ("AVO", "Avocado", "Avocado"),
        ("YMG", "Yulima Mango", "Yulima Mango"),
        ("EIM", "East Indian Mango", "East Indian Mango"),
        ("JMG", "Julie Mango", "Julie Mango"),
        ("PMG", "Palmer Mango", "Palmer Mango"),
        ("SPM", "Spice Mango", "Spice Mango"),
        ("SSP", "Soursop", "Soursop"),
        ("SUM", "Sugar Mango", "Sugar Mango"),
        ("PCR", "Papaya Costa Rica", "Papaya Costa Rica"),
        ("JPL", "June Plum", "June Plum"),
        ("AVJ", "Avocado (Jamaica)", "Avocado (Jam)"),
        ("NAS", "Naseberry", "Naseberry"),
        ("GRA", "Granadilla", "Granadila"),
        ("GEN", "Genip", "Genip"),
        ("AMG", "Apple Mango", "Apple Mango"),
        ("HMG", "Haden Mango", "Haden Mango"),
        ("KMG", "Kent Mango", "Kent Mango"),
        ("LMG", "Long Mango", "Long Mango"),
        ("ATE", "Atemoya", "Atemoya"),
        ("STA", "Star Apple", "Starapple"),
        ("SOR", "Sorrel", "Sorrel"),
        ("WCO", "Water Coconut", "Water Coconut"),
        ("DCO", "Dry Coconut", "Dry Coconut"),
        ("RTO", "Roma Tomato", "Roma Tomato"),
        ("TAM", "Tamarind", "Tamerind"),
        ("MAM", "Mamey Apple", "Mamei Apple"),
        ("SPL", "Small Plum", "Small Plum"),
        ("JAC", "Jackfruit", "Jackfruit"),
        ("FCW", "Frozen Coconut Water", "Frozen Cocunut Water"),
        ("STF", "Starfruit", "Starfruit"),
        ("GUA", "Guava", "Guava"),
        ("MGS", "Mangosteen", "Mangosteen"),
        ("VTO", "Vine Tomato", "Vine Tomato"),
        ("TBS", "Tomato Beefsteak", "Tomato Beefsteak"),
        ("CAU", "Cauliflower", "Cauliflower"),
        ("GBJ", "Green Banana (Jamaica)", "Jam Green Banana"),
    ],
}

SHEET_LABEL = "Order sheet"


def load_catalog(db: Session, company_id: uuid.UUID) -> int:
    """Add the order-sheet catalog to a company. Returns how many products were added."""
    cats: dict[str, ProductCategory] = {}
    for code, name in CATEGORIES:
        cat = db.scalar(
            select(ProductCategory).where(
                ProductCategory.company_id == company_id, ProductCategory.code == code
            )
        )
        if cat is None:
            cat = ProductCategory(company_id=company_id, code=code, name=name)
            db.add(cat)
            db.flush()
        cats[code] = cat
    have = set(db.scalars(select(Product.sku).where(Product.company_id == company_id)))
    added = 0
    for cat_code, items in CATALOG.items():
        for sku, name, sheet_name in items:
            if sku in have:
                continue
            prod = Product(
                company_id=company_id,
                sku=sku,
                name=name,
                category_id=cats[cat_code].id,
                stock_unit="BOX",
                tracks_expiry=False,
            )
            db.add(prod)
            db.flush()
            if norm(sheet_name) != norm(name):
                db.add(
                    ProductAlias(
                        company_id=company_id,
                        product_id=prod.id,
                        alias=sheet_name,
                        alias_norm=norm(sheet_name),
                        source_label=SHEET_LABEL,
                    )
                )
            added += 1
    db.flush()
    return added


def main() -> int:
    s = get_settings()
    if s.is_production_like:
        print("refusing to load the catalog in", s.app_env, file=sys.stderr)
        return 2
    company = sys.argv[1] if len(sys.argv) > 1 else DEMO_COMPANY
    with get_sessionmaker()() as db, db.begin():
        co = db.scalar(select(Company).where(Company.legal_name == company))
        if co is None:
            print(f"no company named {company!r}", file=sys.stderr)
            return 1
        n = load_catalog(db, co.id)
    print(f"added {n} products to {company}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
