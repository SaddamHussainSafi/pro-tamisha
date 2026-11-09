"""Product SKUs are exactly three capital letters (owner rule, 2026-11-09).

Fails if any existing product has another SKU; replace the catalog first
(see `scripts/load_catalog.py`).

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bad = op.get_bind().scalar(sa.text("select count(*) from products where sku !~ '^[A-Z]{3}$'"))
    if bad:
        raise RuntimeError(f"{bad} product(s) have a SKU that is not three capital letters")
    op.drop_constraint("sku_len", "products", type_="check")
    op.alter_column("products", "sku", type_=sa.String(3), existing_nullable=False)
    op.create_check_constraint("sku_three_letters", "products", "sku ~ '^[A-Z]{3}$'")


def downgrade() -> None:
    op.drop_constraint("sku_three_letters", "products", type_="check")
    op.alter_column("products", "sku", type_=sa.String(64), existing_nullable=False)
    op.create_check_constraint("sku_len", "products", "char_length(sku) BETWEEN 1 AND 64")
