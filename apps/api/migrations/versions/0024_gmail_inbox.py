"""Gmail Inbox: the connected mailbox, its emails, their attachments and each "Use for OCR".

- `gmail_accounts`: one connected Google mailbox per company (a partial unique index allows only
  one that is not DISCONNECTED). Holds the OAuth tokens encrypted (Fernet, GMAIL_TOKEN_KEY),
  the Gmail history cursor, the Pub/Sub watch expiry, the import position and sync health.
- `gmail_messages`: one row per (account, Gmail message id): sender, recipient, subject,
  snippet, body text, allow-listed HTML, received time, labels, read state inside Pro Trading.
- `gmail_attachments`: one row per named file on an email (description only; the bytes stay in
  Gmail until someone previews, downloads or processes the file). Hash, real type and page
  count are filled in once the file was fetched.
- `gmail_processing_jobs`: one row per "Use for OCR" attempt, pointing at the existing
  scan_jobs / ocr_extractions rows that hold the reading, its review and the resulting draft.
- Seven permissions `gmail.*`, granted to the default roles as in `permissions.py`
  (OPEN_DECISIONS D-33).

Additive only: no existing table, column or row is changed.
Rollback: `alembic downgrade 0023` drops the four tables and the seven permissions. Scans, OCR
readings and draft orders created from Gmail attachments stay (they live in their own tables).

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
GEN = sa.text("gen_random_uuid()")
NOW = sa.text("now()")
TS = sa.DateTime(timezone=True)

PERMISSIONS = {
    "gmail.read": "View the Gmail Inbox: senders, subjects, previews and attachment names",
    "gmail.body.read": "Read the body of emails in the Gmail Inbox",
    "gmail.attachment.read": "Preview email attachments (pages shown as pictures)",
    "gmail.attachment.download": "Download email attachments",
    "gmail.process": "Send an email attachment to OCR for review",
    "gmail.reprocess": "Read an already processed email attachment again",
    "gmail.manage": "Connect or disconnect the company's Gmail mailbox",
}
STAFF = (
    "gmail.read", "gmail.body.read", "gmail.attachment.read", "gmail.attachment.download",
    "gmail.process",
)  # fmt: skip
GRANTS = {
    "OWNER_ADMIN": tuple(PERMISSIONS),
    "MANAGER": (*STAFF, "gmail.reprocess"),
    "SALES_OPERATOR": STAFF,
}


def _id() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True, server_default=GEN)


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, TS, nullable=nullable, server_default=None if nullable else NOW)


def upgrade() -> None:
    op.create_table(
        "gmail_accounts",
        _id(),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False, server_default="gmail"),
        sa.Column("email_address", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="CONNECTED"),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=True),
        _ts("access_expires_at"),
        sa.Column("history_id", sa.BigInteger(), nullable=True),
        _ts("watch_expires_at"),
        _ts("watch_renewed_at"),
        sa.Column("backfill_page_token", sa.Text(), nullable=True),
        _ts("backfill_started_at"),
        _ts("backfill_done_at"),
        _ts("last_sync_at"),
        _ts("last_push_at"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(48), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        _ts("last_error_at"),
        sa.Column("connected_by", UUID, sa.ForeignKey("employees.id"), nullable=True),
        _ts("connected_at", nullable=False),
        sa.Column("disconnected_by", UUID, sa.ForeignKey("employees.id"), nullable=True),
        _ts("disconnected_at"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _ts("created_at", nullable=False),
        sa.CheckConstraint(
            "status IN ('CONNECTED','ERROR','REAUTH_REQUIRED','DISCONNECTED')",
            name="status_valid",
        ),
        sa.CheckConstraint("provider = 'gmail'", name="provider_valid"),
        sa.CheckConstraint(
            "status = 'DISCONNECTED' OR refresh_token_enc IS NOT NULL", name="live_has_token"
        ),
    )
    op.create_index(
        "uq_gmail_one_live_account",
        "gmail_accounts",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'DISCONNECTED'"),
    )

    op.create_table(
        "gmail_messages",
        _id(),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("account_id", UUID, sa.ForeignKey("gmail_accounts.id"), nullable=False),
        sa.Column("gmail_message_id", sa.String(32), nullable=False),
        sa.Column("thread_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("from_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("from_email", sa.Text(), nullable=False, server_default=""),
        sa.Column("to_addr", sa.Text(), nullable=False, server_default=""),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_truncated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("received_at", TS, nullable=False),
        sa.Column("labels", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("attachment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pdf_count", sa.Integer(), nullable=False, server_default="0"),
        _ts("read_at"),
        sa.Column("read_by", UUID, sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("gmail_history_id", sa.BigInteger(), nullable=True),
        _ts("removed_at"),
        _ts("synced_at", nullable=False),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("account_id", "gmail_message_id", name="uq_gmail_message"),
    )
    op.create_index(
        "ix_gmail_messages_company_received",
        "gmail_messages",
        ["company_id", "received_at", "id"],
    )
    op.create_index("ix_gmail_messages_thread", "gmail_messages", ["account_id", "thread_id"])

    op.create_table(
        "gmail_attachments",
        _id(),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("message_id", UUID, sa.ForeignKey("gmail_messages.id"), nullable=False),
        sa.Column("part_id", sa.String(32), nullable=False),
        sa.Column("gmail_attachment_id", sa.Text(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False, server_default=""),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inline", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("eligible", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("detected_mime", sa.String(32), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("rejected_code", sa.String(32), nullable=True),
        sa.Column("source_document_id", UUID, sa.ForeignKey("source_documents.id"), nullable=True),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("message_id", "part_id", name="uq_gmail_attachment_part"),
        sa.CheckConstraint("byte_size >= 0", name="size_valid"),
        sa.CheckConstraint("sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
    )
    op.create_index("ix_gmail_attachments_message_id", "gmail_attachments", ["message_id"])
    op.create_index("ix_gmail_attachments_sha", "gmail_attachments", ["company_id", "sha256"])

    op.create_table(
        "gmail_processing_jobs",
        _id(),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("message_id", UUID, sa.ForeignKey("gmail_messages.id"), nullable=False),
        sa.Column("attachment_id", UUID, sa.ForeignKey("gmail_attachments.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("document_category", sa.String(24), nullable=False),
        sa.Column("warehouse_id", UUID, sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("scan_job_id", UUID, sa.ForeignKey("scan_jobs.id"), nullable=False),
        sa.Column("extraction_id", UUID, sa.ForeignKey("ocr_extractions.id"), nullable=True),
        sa.Column("reprocess", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("duplicate_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_by", UUID, sa.ForeignKey("employees.id"), nullable=False),
        _ts("created_at", nullable=False),
        sa.UniqueConstraint("attachment_id", "attempt_no", name="uq_gmail_job_attempt"),
        sa.CheckConstraint("attempt_no >= 1", name="attempt_positive"),
    )
    op.create_index(
        "ix_gmail_processing_jobs_attachment_id", "gmail_processing_jobs", ["attachment_id"]
    )
    op.create_index("ix_gmail_jobs_scan", "gmail_processing_jobs", ["scan_job_id"])
    op.create_index("ix_gmail_jobs_message", "gmail_processing_jobs", ["message_id"])

    bind = op.get_bind()
    for code, desc in PERMISSIONS.items():
        bind.execute(
            sa.text(
                "INSERT INTO permissions (code, description) VALUES (:c, :d) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"c": code, "d": desc},
        )
    for role, codes in GRANTS.items():
        for code in codes:
            bind.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_code) "
                    "SELECT id, :c FROM roles WHERE code = :r ON CONFLICT DO NOTHING"
                ),
                {"c": code, "r": role},
            )


def downgrade() -> None:
    bind = op.get_bind()
    codes = list(PERMISSIONS)
    bind.execute(
        sa.text("DELETE FROM role_permissions WHERE permission_code = ANY(:c)"), {"c": codes}
    )
    bind.execute(sa.text("DELETE FROM permissions WHERE code = ANY(:c)"), {"c": codes})
    op.drop_index("ix_gmail_jobs_message", table_name="gmail_processing_jobs")
    op.drop_index("ix_gmail_jobs_scan", table_name="gmail_processing_jobs")
    op.drop_index("ix_gmail_processing_jobs_attachment_id", table_name="gmail_processing_jobs")
    op.drop_table("gmail_processing_jobs")
    op.drop_index("ix_gmail_attachments_sha", table_name="gmail_attachments")
    op.drop_index("ix_gmail_attachments_message_id", table_name="gmail_attachments")
    op.drop_table("gmail_attachments")
    op.drop_index("ix_gmail_messages_thread", table_name="gmail_messages")
    op.drop_index("ix_gmail_messages_company_received", table_name="gmail_messages")
    op.drop_table("gmail_messages")
    op.drop_index("uq_gmail_one_live_account", table_name="gmail_accounts")
    op.drop_table("gmail_accounts")
