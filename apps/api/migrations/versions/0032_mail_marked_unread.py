"""Email: per-person "mark as unread" that also works for mail Lark has as read.

Rollback: `alembic downgrade 0031` drops the column.

Revision ID: 0032
Revises: 0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mail_user_state",
        sa.Column("marked_unread", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("mail_user_state", "marked_unread")
