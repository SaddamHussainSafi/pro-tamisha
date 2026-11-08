"""Scanner discovery: the web app asks a paired bridge to list the scanners it can see.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scan_devices",
        sa.Column("discover_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "scan_devices",
        sa.Column("detected_scanners", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "scan_devices", sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("scan_devices", "detected_at")
    op.drop_column("scan_devices", "detected_scanners")
    op.drop_column("scan_devices", "discover_requested_at")
