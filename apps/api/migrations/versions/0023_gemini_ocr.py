"""Gemini OCR: what each reading cost, and the extra things a handwriting reader sees on a row.

- `ocr_engine_calls`: one row per request sent to an outside OCR engine (today: Google Gemini).
  It holds no document content: only the engine, the model, the stage, how it ended, tokens and
  seconds. The reader counts these rows to stay inside the engine's per-minute and per-day limits
  (a row is reserved before the request goes out), to stop calling after repeated failures
  (circuit breaker), and to say what a document cost. Rows are kept when a scan is purged, so
  the day's count stays true. `extraction_id` is a plain column (no foreign key): the worker
  holds a row lock on the reading while it writes these.
- `ocr_extractions.engine`: which engine and model really read the document, requests, tokens,
  seconds, why a fallback was used, the kind of document the reader saw, and the validated
  answer per page (shown on the admin OCR test page).
- `ocr_fields.detail`: per line, kept apart from the quantity: package, weight, case size,
  notes, a crossed-out number and its replacement, a NO written on the row, a checkmark,
  supplied / unavailable quantities, and the checks that sent the line to review.

Rollback: `alembic downgrade 0022` drops the table and the two columns (usage history and the
extra per-line details only; readings, lines and reviewer decisions are untouched).

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTCOMES = (
    "PENDING", "OK", "RATE_LIMITED", "QUOTA_DAY", "HTTP_ERROR", "TIMEOUT", "BAD_JSON", "BLOCKED",
)  # fmt: skip


def upgrade() -> None:
    outcomes = ", ".join(f"'{k}'" for k in OUTCOMES)
    op.create_table(
        "ocr_engine_calls",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("company_id", pg.UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("extraction_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("seconds", sa.Numeric(8, 3), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"outcome IN ({outcomes})", name="outcome_valid"),
    )
    op.create_index(
        "ix_ocr_engine_calls_provider_time", "ocr_engine_calls", ["provider", "created_at"]
    )
    op.create_index("ix_ocr_engine_calls_extraction", "ocr_engine_calls", ["extraction_id"])
    op.add_column(
        "ocr_extractions",
        sa.Column("engine", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("ocr_fields", sa.Column("detail", pg.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("ocr_fields", "detail")
    op.drop_column("ocr_extractions", "engine")
    op.drop_index("ix_ocr_engine_calls_extraction", table_name="ocr_engine_calls")
    op.drop_index("ix_ocr_engine_calls_provider_time", table_name="ocr_engine_calls")
    op.drop_table("ocr_engine_calls")
