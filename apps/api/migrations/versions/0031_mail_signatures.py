"""Email: a personal signature per person and mailbox (layout fields kept for the editor).

Rollback: `alembic downgrade 0030` drops the four columns (signatures only).

Revision ID: 0031
Revises: 0030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mail_access", sa.Column("signature_html", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "mail_access", sa.Column("signature_fields", JSONB(), nullable=False, server_default="{}")
    )
    op.add_column(
        "mail_access",
        sa.Column("signature_on_new", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "mail_access",
        sa.Column("signature_on_reply", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    for c in ("signature_on_reply", "signature_on_new", "signature_fields", "signature_html"):
        op.drop_column("mail_access", c)
