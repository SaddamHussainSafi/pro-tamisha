"""Notification Center: notifications, per-employee recipients (the inbox), preferences and
email delivery attempts. See pro_trading/notifications.py.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIES = (
    "website_order",
    "sales_order",
    "invoice",
    "payment",
    "inventory",
    "receiving",
    "scan",
    "integration",
    "security",
    "system",
)
SEVERITIES = ("info", "success", "warning", "critical")


def _in(values: Sequence[str]) -> str:
    return "IN (" + ",".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    uid = pg.UUID(as_uuid=True)
    now = sa.text("now()")
    op.create_table(
        "notifications",
        sa.Column("id", uid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", uid, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("source_module", sa.String(48), nullable=False),
        sa.Column("link", sa.Text, nullable=False, server_default=""),
        sa.Column("target_type", sa.String(48)),
        sa.Column("target_id", sa.String(64)),
        sa.Column("required_permission", sa.Text),
        sa.Column("any_scope", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("warehouse_id", uid, sa.ForeignKey("warehouses.id")),
        sa.Column("actor_id", uid, sa.ForeignKey("employees.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("company_id", "event_id", name="uq_notifications_event"),
        sa.CheckConstraint(f"category {_in(CATEGORIES)}", name="category_valid"),
        sa.CheckConstraint(f"severity {_in(SEVERITIES)}", name="severity_valid"),
        sa.CheckConstraint("char_length(event_id) BETWEEN 1 AND 200", name="event_id_len"),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 160", name="title_len"),
        sa.CheckConstraint("char_length(body) <= 600", name="body_len"),
        sa.CheckConstraint("link = '' OR link ~ '^/[A-Za-z0-9]'", name="link_relative"),
    )
    op.create_index(
        "ix_notifications_company_created", "notifications", ["company_id", "created_at"]
    )

    op.create_table(
        "notification_recipients",
        sa.Column("seq", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "notification_id",
            uid,
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id", uid, sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("company_id", uid, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("notification_id", "employee_id", name="uq_notification_recipient"),
    )
    op.create_index(
        "ix_notification_recipients_inbox", "notification_recipients", ["employee_id", "seq"]
    )
    op.create_index(
        "ix_notification_recipients_unread",
        "notification_recipients",
        ["employee_id"],
        postgresql_where=sa.text("read_at IS NULL AND archived_at IS NULL"),
    )

    op.create_table(
        "notification_preferences",
        sa.Column(
            "employee_id", uid, sa.ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("company_id", uid, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("in_app", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("desktop", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("email", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("sound", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("grouped", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("quiet_hours", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("quiet_start", sa.Time, nullable=False, server_default="22:00"),
        sa.Column("quiet_end", sa.Time, nullable=False, server_default="07:00"),
        sa.Column("categories", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.CheckConstraint("version >= 1", name="version_pos"),
    )

    op.create_table(
        "notification_delivery_attempts",
        sa.Column("id", uid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "recipient_seq",
            sa.BigInteger,
            sa.ForeignKey("notification_recipients.seq", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("company_id", uid, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("recipient_seq", "channel", name="uq_notification_delivery"),
        sa.CheckConstraint("channel IN ('email')", name="channel_valid"),
        sa.CheckConstraint("status IN ('PENDING','SENT','FAILED','SKIPPED')", name="status_valid"),
    )


def downgrade() -> None:
    op.drop_table("notification_delivery_attempts")
    op.drop_table("notification_preferences")
    op.drop_table("notification_recipients")
    op.drop_table("notifications")
