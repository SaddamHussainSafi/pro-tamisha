"""Trash bin: the Super Admin moves records to a 30-day Trash; other roles ask for a delete.

- `trash_items`: one row per trashed record, holding every row that was removed with it
  (enough to restore it) until it is restored, deleted forever or purged after 30 days.
- `delete_requests`: a staff member's request to delete a record; the Super Admin approves
  (moves it to the Trash) or dismisses it.
- Permission `trash.manage`, granted to the Super Admin role (OWNER_ADMIN) only.
- Guard triggers that refuse DELETE on products, case sizes, draft sales orders, finished scan
  jobs, unused source documents, filed documents (not invoice PDFs) and unconverted website
  entries now let a DELETE through only when the transaction set `pro_trading.trash = on`
  (done by `trash.py` alone) and the row is in a deletable state. UPDATE rules are unchanged.
  Invoice, stock ledger, payment, supplier payment and audit triggers are not touched.

Rollback: `alembic downgrade 0019` drops both tables and the permission and restores the
original UPDATE OR DELETE triggers (records already deleted through the Trash stay deleted).

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "product",
    "case_size",
    "customer",
    "customer_group",
    "supplier",
    "price_draft",
    "sales_order",
    "picking_sheet",
    "scan",
    "document",
    "user",
    "notification",
    "website_entry",
)
KIND_CHECK = "kind IN (" + ",".join(f"'{k}'" for k in KINDS) + ")"

TRASH_ON = "coalesce(current_setting('pro_trading.trash', true), '') = 'on'"

# (trigger, table, function, extra condition on OLD for a Trash delete)
GUARDS = [
    ("products_immutable", "products", "catalog_reject_identity_change", "true"),
    (
        "product_unit_profiles_immutable",
        "product_unit_profiles",
        "catalog_reject_identity_change",
        "NOT OLD.is_default",
    ),
    ("sales_orders_guard", "sales_orders", "sales_order_guard", "OLD.status = 'DRAFT'"),
    (
        "scan_jobs_guard",
        "scan_jobs",
        "scan_job_guard",
        "OLD.status IN ('UPLOADED','FAILED','CANCELLED','EXPIRED')",
    ),
    ("source_documents_frozen", "source_documents", "source_document_frozen", "true"),
    ("document_file_guard", "document_files", "document_file_guard", "OLD.kind <> 'INVOICE_PDF'"),
    (
        "order_form_submissions_guard",
        "order_form_submissions",
        "oc_submission_guard",
        "OLD.status <> 'CONVERTED'",
    ),
    ("order_form_submission_items_guard", "order_form_submission_items", "oc_append_only", "true"),
    (
        "order_form_submission_events_guard",
        "order_form_submission_events",
        "oc_append_only",
        "true",
    ),
]


def upgrade() -> None:
    op.create_table(
        "trash_items",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("snapshot", pg.JSONB(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("trashed_by", sa.UUID(), nullable=True),
        sa.Column(
            "trashed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(KIND_CHECK, name="kind_valid"),
        sa.CheckConstraint("purge_after > trashed_at", name="purge_after_later"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["trashed_by"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "kind", "item_id", name="uq_trash_item"),
    )
    op.create_index("ix_trash_purge_after", "trash_items", ["purge_after"])
    op.create_index("ix_trash_company_trashed", "trash_items", ["company_id", "trashed_at"])

    op.create_table(
        "delete_requests",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(12), server_default="PENDING", nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), server_default="", nullable=False),
        sa.CheckConstraint(KIND_CHECK, name="kind_valid"),
        sa.CheckConstraint("status IN ('PENDING','APPROVED','DISMISSED')", name="status_valid"),
        sa.CheckConstraint("char_length(reason) <= 500", name="reason_len"),
        sa.CheckConstraint(
            "(status = 'PENDING') = (decided_at IS NULL)", name="decided_when_closed"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["employees.id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_delete_request_pending",
        "delete_requests",
        ["company_id", "kind", "item_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_delete_requests_company_status", "delete_requests", ["company_id", "status"]
    )

    op.execute(
        "INSERT INTO permissions (code, description) VALUES ('trash.manage', "
        "'Move records to the Trash, restore them and delete them forever (Super Admin only)') "
        "ON CONFLICT (code) DO NOTHING"
    )
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_code) "
        "SELECT id, 'trash.manage' FROM roles WHERE code = 'OWNER_ADMIN' ON CONFLICT DO NOTHING"
    )

    for trg, table, fn, cond in GUARDS:
        op.execute(f"DROP TRIGGER IF EXISTS {trg} ON {table}")
        op.execute(
            f"CREATE TRIGGER {trg} BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {fn}()"
        )
        # The guard function refuses every DELETE it sees; this trigger skips it only for a
        # Trash delete of a row in a deletable state.
        op.execute(
            f"CREATE TRIGGER {trg}_delete BEFORE DELETE ON {table} FOR EACH ROW "
            f"WHEN (NOT ({TRASH_ON} AND {cond})) EXECUTE FUNCTION {fn}()"
        )


def downgrade() -> None:
    for trg, table, fn, _ in GUARDS:
        op.execute(f"DROP TRIGGER IF EXISTS {trg}_delete ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {trg} ON {table}")
        op.execute(
            f"CREATE TRIGGER {trg} BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {fn}()"
        )
    op.execute("DELETE FROM role_permissions WHERE permission_code = 'trash.manage'")
    op.execute("DELETE FROM permissions WHERE code = 'trash.manage'")
    op.drop_index("ix_delete_requests_company_status", table_name="delete_requests")
    op.drop_index("uq_delete_request_pending", table_name="delete_requests")
    op.drop_table("delete_requests")
    op.drop_index("ix_trash_company_trashed", table_name="trash_items")
    op.drop_index("ix_trash_purge_after", table_name="trash_items")
    op.drop_table("trash_items")
