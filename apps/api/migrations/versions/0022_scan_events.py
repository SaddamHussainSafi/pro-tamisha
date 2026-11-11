"""Scan progress events: what the OCR reader is doing right now, page by page.

- `scan_events`: one row per step the OCR worker reports while it reads a scan (reading
  started, page N of M read, matching products, products matched, will try again, gave up,
  timed out). The worker writes each one in its own short transaction, so the browser sees
  page 3 of 10 while the reading itself is still open. The scan's and the reading's own status
  columns stay the truth; these rows only say how far along an unfinished reading is.
- Rows go with their scan (ON DELETE CASCADE), so a scan purged from the Trash takes its
  progress (which can hold a few words read from the page) with it. `extraction_id` is a plain
  column on purpose: a foreign key to `ocr_extractions` would wait on the worker's own row lock.

Rollback: `alembic downgrade 0021` drops the table (progress history only; scans and readings
are untouched).

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "OCR_STARTED",
    "PAGE_READ",
    "MATCHING",
    "MATCHED",
    "OCR_RETRY",
    "OCR_FAILED",
    "OCR_TIMEOUT",
)


def upgrade() -> None:
    kinds = ", ".join(f"'{k}'" for k in KINDS)
    op.create_table(
        "scan_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "company_id", pg.UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column(
            "scan_job_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("scan_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("extraction_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("detail", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"kind IN ({kinds})", name="kind_valid"),
        sa.CheckConstraint("page IS NULL OR page >= 1", name="page_positive"),
        sa.CheckConstraint("page_count IS NULL OR page_count >= 0", name="page_count_valid"),
        sa.CheckConstraint("attempt IS NULL OR attempt >= 1", name="attempt_valid"),
    )
    op.create_index("ix_scan_events_job_seq", "scan_events", ["scan_job_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_scan_events_job_seq", table_name="scan_events")
    op.drop_table("scan_events")
