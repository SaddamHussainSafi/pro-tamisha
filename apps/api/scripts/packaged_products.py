"""Give every active product its packaged twin (owner, 2026-11-11): "<name> - Packaged", SKU
<SKU>P, in the "Packaged" category (listed last), same stock unit, no price, no stock. New
products get theirs when they are created; this fills in an existing catalog. Idempotent.

    PYTHONPATH=src uv run python scripts/packaged_products.py ["Company legal name" | --all]

Defaults to the demo company. Refuses to run in staging/production.
"""

import sys

from sqlalchemy import select

from pro_trading import audit, packaged
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.models import Company
from pro_trading.seed import DEMO_COMPANY


def main() -> int:
    if get_settings().is_production_like:
        print("refusing in", get_settings().app_env, file=sys.stderr)
        return 2
    arg = sys.argv[1] if len(sys.argv) > 1 else DEMO_COMPANY
    with get_sessionmaker()() as db, db.begin():
        q = select(Company) if arg == "--all" else select(Company).where(Company.legal_name == arg)
        companies = db.scalars(q).all()
        if not companies:
            print(f"no company {arg!r}", file=sys.stderr)
            return 1
        for co in companies:
            made = packaged.ensure_twins(db, co.id)
            if made:
                audit.record(
                    db,
                    action="product.packaged_twins",
                    success=True,
                    company_id=co.id,
                    target_type="product",
                    detail={"created": [p.sku for p in made], "by": "scripts/packaged_products.py"},
                )
            print(f"{co.legal_name}: {len(made)} packaged item(s) added")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
