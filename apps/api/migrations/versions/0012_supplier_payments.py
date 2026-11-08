"""Supplier payments: money we paid suppliers (append-only; void with a reason).

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUARD_FN = """
CREATE OR REPLACE FUNCTION supplier_payment_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'supplier payments cannot be deleted (void instead)';
  END IF;
  IF NEW.company_id <> OLD.company_id OR NEW.payment_no <> OLD.payment_no
     OR NEW.vendor_id <> OLD.vendor_id OR NEW.shipment_id IS DISTINCT FROM OLD.shipment_id
     OR NEW.method <> OLD.method OR NEW.amount <> OLD.amount OR NEW.currency <> OLD.currency
     OR NEW.paid_on <> OLD.paid_on OR NEW.reference IS DISTINCT FROM OLD.reference
     OR NEW.recorded_by <> OLD.recorded_by OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'recorded supplier payment details are immutable';
  END IF;
  IF OLD.voided_at IS NOT NULL AND (NEW.voided_at IS DISTINCT FROM OLD.voided_at
     OR NEW.void_reason IS DISTINCT FROM OLD.void_reason
     OR NEW.voided_by IS DISTINCT FROM OLD.voided_by) THEN
    RAISE EXCEPTION 'a voided supplier payment cannot be changed';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "supplier_payments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("payment_no", sa.String(length=32), nullable=False),
        sa.Column("vendor_id", sa.UUID(), nullable=False),
        sa.Column("shipment_id", sa.UUID(), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("recorded_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by", sa.UUID(), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("method IN ('WIRE','EFT','CHEQUE','CASH','OTHER')", name="method_valid"),
        sa.CheckConstraint("amount > 0", name="amount_positive"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        sa.CheckConstraint(
            "(voided_at IS NULL) = (void_reason IS NULL) AND (voided_at IS NULL) = (voided_by IS NULL)",
            name="void_complete",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"]),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipments.id"]),
        sa.ForeignKeyConstraint(["recorded_by"], ["employees.id"]),
        sa.ForeignKeyConstraint(["voided_by"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "payment_no"),
    )
    op.create_index(
        "ix_supplier_payments_vendor",
        "supplier_payments",
        ["company_id", "vendor_id", "paid_on"],
        unique=False,
    )
    op.execute(GUARD_FN)
    op.execute(
        "CREATE TRIGGER supplier_payments_guard BEFORE UPDATE OR DELETE ON supplier_payments "
        "FOR EACH ROW EXECUTE FUNCTION supplier_payment_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS supplier_payments_guard ON supplier_payments")
    op.execute("DROP FUNCTION IF EXISTS supplier_payment_guard()")
    op.drop_index("ix_supplier_payments_vendor", table_name="supplier_payments")
    op.drop_table("supplier_payments")
