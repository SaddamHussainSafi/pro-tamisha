"""OCR of supplier documents: header fields, matched supplier, line prices.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ocr_extractions",
        sa.Column("header", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
    )
    op.add_column("ocr_extractions", sa.Column("vendor_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_ocr_extractions_vendor", "ocr_extractions", "vendors", ["vendor_id"], ["id"])
    op.add_column("ocr_extractions", sa.Column("shipment_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_ocr_extractions_shipment", "ocr_extractions", "shipments", ["shipment_id"], ["id"]
    )
    op.add_column("ocr_fields", sa.Column("read_unit_price", sa.Numeric(18, 4), nullable=True))
    op.add_column("ocr_fields", sa.Column("read_amount", sa.Numeric(18, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("ocr_fields", "read_amount")
    op.drop_column("ocr_fields", "read_unit_price")
    op.drop_constraint("fk_ocr_extractions_shipment", "ocr_extractions", type_="foreignkey")
    op.drop_column("ocr_extractions", "shipment_id")
    op.drop_constraint("fk_ocr_extractions_vendor", "ocr_extractions", type_="foreignkey")
    op.drop_column("ocr_extractions", "vendor_id")
    op.drop_column("ocr_extractions", "header")
