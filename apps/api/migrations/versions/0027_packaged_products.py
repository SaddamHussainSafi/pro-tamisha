"""Packaged items: a packaged twin's SKU is the product's three letters plus "P" (owner,
2026-11-11). SKUs are three capital letters, or three plus "P".

The twins themselves are created by the app (new products) and by
`scripts/packaged_products.py` (existing catalogs), so each creation is audited.

Revision ID: 0027
Revises: 0026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("sku_three_letters", "products", type_="check")
    op.alter_column("products", "sku", type_=sa.String(4), existing_nullable=False)
    op.create_check_constraint("sku_letters", "products", "sku ~ '^[A-Z]{3}P?$'")


def downgrade() -> None:
    # Keeps any packaged products: the old three-letter check comes back NOT VALID (checked for
    # new rows only) and the column stays four characters wide.
    op.drop_constraint("sku_letters", "products", type_="check")
    op.execute(
        "ALTER TABLE products ADD CONSTRAINT sku_three_letters CHECK (sku ~ '^[A-Z]{3}$') NOT VALID"
    )
