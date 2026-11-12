"""Receiving: automatic batch names (owner, 2026-11-11, D-37). A count line saved without a
batch gets the supplier's initials plus a running number, e.g. "TFS-0001". The number runs
per company and is never handed out twice; this table holds the last one used.

Revision ID: 0028
Revises: 0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "batch_counters",
        sa.Column(
            "company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), primary_key=True
        ),
        sa.Column("last_no", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("last_no >= 0", name="batch_counter_nonneg"),
    )


def downgrade() -> None:
    op.drop_table("batch_counters")
