"""Order lines carry their weight: boxes are sold per lb.

Owner, 2026-11-11: "there is no box price, all products go as per pound". `sales_order_lines.
weight_lb` is the weight of the whole line in pounds (boxes only), e.g. the WEIGHT written on
the order sheet, or typed on the order. Confirming prices such a line as the per-lb rate x this
weight. Empty = not weighed yet (then the product's case size, if any, gives the weight).

Rollback: `alembic downgrade 0024` drops the column (entered weights only; rates already frozen
on confirmed lines keep the weight they were worked out from in their price snapshot).

Revision ID: 0025
Revises: 0024
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sales_order_lines", sa.Column("weight_lb", sa.Numeric(14, 4), nullable=True))
    op.create_check_constraint(
        "weight_pos", "sales_order_lines", "weight_lb IS NULL OR weight_lb > 0"
    )


def downgrade() -> None:
    op.drop_constraint("weight_pos", "sales_order_lines", type_="check")
    op.drop_column("sales_order_lines", "weight_lb")
