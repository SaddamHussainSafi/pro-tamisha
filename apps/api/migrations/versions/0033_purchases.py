"""Purchases: one purchase saved straight to stock, airport verification, quick receive
(owner decisions 2026-11-12, docs/modules/08-receiving.md).

Additive. No existing row's data is changed: every shipment already in the table gets
entry = 'LEGACY' and the new flags at their "nothing to do" defaults, and no stock movement,
lot, GRN line or declared line is touched.

What changes
- shipments: entry, invoice_date, is_airport, expected_pieces, verify_status, verified_*,
  needs_details, quick_reason, details_completed_*, matched_shipment_id. vendor_id may be NULL,
  only on a quick receive whose supplier is not known yet.
- shipment_guard(): a POSTED shipment used to refuse every UPDATE. Purchases are POSTED the
  moment they are saved, so the guard now lets the follow-up columns change (verification,
  completing a quick receive, version) and still refuses everything else. The supplier,
  invoice number/date and currency can change only while details are still to complete.
- purchase_corrections (new): later fixes to a line in stock, with their own guard.
- stock_movements.type_valid gains GRN_CORRECTION; shipment_documents.kind_valid gains CFIA.

Rollback (downgrade)
1. Take a backup first (pg_dump).
2. The downgrade REFUSES to run while rows exist that the old schema cannot hold:
   GRN_CORRECTION stock movements (the ledger is append-only, they cannot be removed),
   shipments without a supplier, or CFIA documents. Give every quick receive a supplier
   (complete it) before downgrading; if corrections were posted, restore the backup instead.
3. Otherwise it drops purchase_corrections and the new columns and restores the 0009 guard.
   Purchases saved with the new flow stay as ordinary POSTED shipments with their stock.
4. Throwaway databases only (tests): PT_FORCE_DOWNGRADE=1 rolls back anyway, leaving such rows
   in place and restoring the old checks for new rows only (NOT VALID).

Revision ID: 0033
Revises: 0032
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = (
    "'OPENING_BALANCE','GRN_RECEIPT','INVOICE_DISPATCH','ADJUSTMENT','COUNT_ADJUSTMENT',"
    "'DAMAGE','RETURN','REVERSAL'"
)
NEW_TYPES = OLD_TYPES + ",'GRN_CORRECTION'"
OLD_DOC_KINDS = "'AWB','INVOICE','PACKING_LIST','COMBINED','OTHER'"
NEW_DOC_KINDS = "'AWB','INVOICE','PACKING_LIST','COMBINED','CFIA','OTHER'"

# Columns of a POSTED shipment that may still change. Everything else stays final.
FOLLOW_UP = (
    "version", "updated_at",
    "verify_status", "verified_pieces", "verified_by", "verified_at",
    "needs_details", "details_completed_by", "details_completed_at", "matched_shipment_id",
    "vendor_id", "vendor_invoice_no", "invoice_date", "currency",
)  # fmt: skip
_MINUS = " ".join(f"- '{c}'" for c in FOLLOW_UP)

SHIPMENT_FN_NEW = f"""
CREATE OR REPLACE FUNCTION shipment_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Shipments cannot be deleted';
  END IF;
  IF NEW.company_id <> OLD.company_id OR NEW.shipment_no <> OLD.shipment_no
     OR NEW.warehouse_id <> OLD.warehouse_id OR NEW.entry <> OLD.entry THEN
    RAISE EXCEPTION 'Shipment identity is immutable';
  END IF;
  IF OLD.status = 'CANCELLED' THEN
    RAISE EXCEPTION 'Shipment % is final', OLD.status;
  END IF;
  IF OLD.status = 'POSTED' THEN
    IF (to_jsonb(NEW) {_MINUS}) <> (to_jsonb(OLD) {_MINUS}) THEN
      RAISE EXCEPTION 'Shipment POSTED is final';
    END IF;
    IF NOT OLD.needs_details AND (
         NEW.vendor_id IS DISTINCT FROM OLD.vendor_id
         OR NEW.vendor_invoice_no IS DISTINCT FROM OLD.vendor_invoice_no
         OR NEW.invoice_date IS DISTINCT FROM OLD.invoice_date
         OR NEW.currency <> OLD.currency) THEN
      RAISE EXCEPTION 'Shipment POSTED is final';
    END IF;
    IF NEW.needs_details AND NOT OLD.needs_details THEN
      RAISE EXCEPTION 'Shipment POSTED is final';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

SHIPMENT_FN_OLD = """
CREATE OR REPLACE FUNCTION shipment_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Shipments cannot be deleted';
  END IF;
  IF NEW.company_id <> OLD.company_id OR NEW.shipment_no <> OLD.shipment_no
     OR NEW.warehouse_id <> OLD.warehouse_id THEN
    RAISE EXCEPTION 'Shipment identity is immutable';
  END IF;
  IF OLD.status IN ('POSTED','CANCELLED') THEN
    RAISE EXCEPTION 'Shipment % is final', OLD.status;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# A correction is never deleted. What it says (line, kind, quantity, reason) never changes;
# a decided one (POSTED / REJECTED) only accepts the owner's review fields.
CORRECTION_FN = """
CREATE OR REPLACE FUNCTION purchase_correction_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Purchase corrections cannot be deleted';
  END IF;
  IF (to_jsonb(NEW) - 'status' - 'posted_qty' - 'uncovered_qty' - 'needs_review'
        - 'review_note' - 'reviewed_by' - 'reviewed_at' - 'decided_by' - 'decided_at'
        - 'decision_note')
     <> (to_jsonb(OLD) - 'status' - 'posted_qty' - 'uncovered_qty' - 'needs_review'
        - 'review_note' - 'reviewed_by' - 'reviewed_at' - 'decided_by' - 'decided_at'
        - 'decision_note') THEN
    RAISE EXCEPTION 'A purchase correction cannot be rewritten';
  END IF;
  IF OLD.status <> 'PENDING' AND (
       NEW.status <> OLD.status
       OR NEW.posted_qty IS DISTINCT FROM OLD.posted_qty
       OR NEW.uncovered_qty <> OLD.uncovered_qty
       OR NEW.decided_by IS DISTINCT FROM OLD.decided_by
       OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
       OR NEW.decision_note IS DISTINCT FROM OLD.decision_note) THEN
    RAISE EXCEPTION 'Purchase correction % is final', OLD.status;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def _emp(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=True)


def upgrade() -> None:
    op.add_column(
        "shipments", sa.Column("entry", sa.String(8), nullable=False, server_default="LEGACY")
    )
    op.add_column("shipments", sa.Column("invoice_date", sa.Date(), nullable=True))
    op.add_column(
        "shipments",
        sa.Column("is_airport", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("shipments", sa.Column("expected_pieces", sa.Integer(), nullable=True))
    op.add_column("shipments", sa.Column("verify_status", sa.String(16), nullable=True))
    op.add_column("shipments", sa.Column("verified_pieces", sa.Integer(), nullable=True))
    op.add_column("shipments", _emp("verified_by"))
    op.add_column("shipments", sa.Column("verified_at", sa.DateTime(timezone=True)))
    op.add_column(
        "shipments",
        sa.Column("needs_details", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("shipments", sa.Column("quick_reason", sa.Text(), nullable=True))
    op.add_column("shipments", _emp("details_completed_by"))
    op.add_column("shipments", sa.Column("details_completed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "shipments",
        sa.Column(
            "matched_shipment_id", UUID(as_uuid=True), sa.ForeignKey("shipments.id"), nullable=True
        ),
    )
    op.alter_column("shipments", "vendor_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.create_check_constraint("entry_valid", "shipments", "entry IN ('LEGACY','PURCHASE','QUICK')")
    op.create_check_constraint(
        "verify_valid",
        "shipments",
        "verify_status IS NULL OR verify_status IN ('TO_VERIFY','PENDING_OK','VERIFIED')",
    )
    op.create_check_constraint(
        "verify_airport_only", "shipments", "verify_status IS NULL OR is_airport"
    )
    op.create_check_constraint(
        "vendor_known", "shipments", "vendor_id IS NOT NULL OR entry = 'QUICK'"
    )
    op.create_check_constraint(
        "quick_reason", "shipments", "entry <> 'QUICK' OR quick_reason IS NOT NULL"
    )
    op.create_check_constraint(
        "details_quick_only", "shipments", "NOT needs_details OR entry = 'QUICK'"
    )
    op.create_check_constraint(
        "pieces_nonneg",
        "shipments",
        "(expected_pieces IS NULL OR expected_pieces >= 0) AND "
        "(verified_pieces IS NULL OR verified_pieces >= 0)",
    )
    op.create_index(
        "ix_shipments_to_complete",
        "shipments",
        ["company_id"],
        postgresql_where=sa.text("needs_details"),
    )
    op.execute(SHIPMENT_FN_NEW)

    op.drop_constraint("type_valid", "stock_movements", type_="check")
    op.create_check_constraint("type_valid", "stock_movements", f"movement_type IN ({NEW_TYPES})")
    op.drop_constraint("kind_valid", "shipment_documents", type_="check")
    op.create_check_constraint("kind_valid", "shipment_documents", f"kind IN ({NEW_DOC_KINDS})")

    op.create_table(
        "purchase_corrections",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("shipment_id", UUID(as_uuid=True), sa.ForeignKey("shipments.id"), nullable=False),
        sa.Column("grn_line_id", UUID(as_uuid=True), sa.ForeignKey("grn_lines.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("unit_code", sa.String(8), nullable=False),
        sa.Column("qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("posted_qty", sa.Numeric(18, 4), nullable=True),
        sa.Column("uncovered_qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("review_note", sa.Text(), nullable=True),
        _emp("reviewed_by"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        _emp("decided_by"),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('SHORT','DAMAGED','RETURNED','OVER')", name="kind_valid"),
        sa.CheckConstraint("source IN ('VERIFY','MANUAL','MATCH')", name="source_valid"),
        sa.CheckConstraint("status IN ('PENDING','POSTED','REJECTED')", name="status_valid"),
        sa.CheckConstraint("unit_code IN ('BOX','PIECE','LB','KG')", name="unit_code_valid"),
        sa.CheckConstraint("qty > 0", name="qty_positive"),
        sa.CheckConstraint("(status = 'POSTED') = (posted_qty IS NOT NULL)", name="posted_has_qty"),
        sa.CheckConstraint(
            "uncovered_qty >= 0 AND (posted_qty IS NULL OR posted_qty >= 0)", name="qty_nonneg"
        ),
        sa.CheckConstraint("status = 'PENDING' OR decided_at IS NOT NULL", name="decided"),
    )  # fmt: skip
    op.create_index("ix_purchase_corrections_shipment", "purchase_corrections", ["shipment_id"])
    op.create_index(
        "ix_purchase_corrections_review",
        "purchase_corrections",
        ["company_id"],
        postgresql_where=sa.text("needs_review"),
    )
    op.execute(CORRECTION_FN)
    op.execute(
        "CREATE TRIGGER purchase_corrections_guard BEFORE UPDATE OR DELETE ON "
        "purchase_corrections FOR EACH ROW EXECUTE FUNCTION purchase_correction_guard()"
    )


def _forced() -> bool:
    """Throwaway databases (the test suite rebuilds its schema from nothing) may roll back
    even with rows the old schema cannot hold: `PT_FORCE_DOWNGRADE=1` or `-x force=1`."""
    return (
        os.environ.get("PT_FORCE_DOWNGRADE") == "1"
        or context.get_x_argument(as_dictionary=True).get("force") == "1"
    )


def downgrade() -> None:
    bind = op.get_bind()
    blockers = {
        "GRN_CORRECTION stock movements": (
            "SELECT count(*) FROM stock_movements WHERE movement_type = 'GRN_CORRECTION'"
        ),
        "shipments without a supplier": "SELECT count(*) FROM shipments WHERE vendor_id IS NULL",
        "CFIA documents": "SELECT count(*) FROM shipment_documents WHERE kind = 'CFIA'",
    }
    found = {k: bind.execute(sa.text(q)).scalar() or 0 for k, q in blockers.items()}
    forced = _forced()
    if any(found.values()) and not forced:
        raise RuntimeError(
            "0033 cannot be rolled back while these rows exist (see the rollback notes at the "
            f"top of this file): {', '.join(f'{n} {k}' for k, n in found.items() if n)}"
        )
    # forced: the old rules are restored for new rows only; existing rows are left as they are
    not_valid = " NOT VALID" if forced else ""
    op.execute("DROP TRIGGER IF EXISTS purchase_corrections_guard ON purchase_corrections")
    op.execute("DROP FUNCTION IF EXISTS purchase_correction_guard()")
    op.drop_index("ix_purchase_corrections_review", table_name="purchase_corrections")
    op.drop_index("ix_purchase_corrections_shipment", table_name="purchase_corrections")
    op.drop_table("purchase_corrections")

    op.drop_constraint("kind_valid", "shipment_documents", type_="check")
    op.execute(
        "ALTER TABLE shipment_documents ADD CONSTRAINT kind_valid "
        f"CHECK (kind IN ({OLD_DOC_KINDS})){not_valid}"
    )
    op.drop_constraint("type_valid", "stock_movements", type_="check")
    op.execute(
        "ALTER TABLE stock_movements ADD CONSTRAINT type_valid "
        f"CHECK (movement_type IN ({OLD_TYPES})){not_valid}"
    )

    op.execute(SHIPMENT_FN_OLD)
    op.drop_index("ix_shipments_to_complete", table_name="shipments")
    for name in (
        "pieces_nonneg", "details_quick_only", "quick_reason", "vendor_known",
        "verify_airport_only", "verify_valid", "entry_valid",
    ):  # fmt: skip
        op.drop_constraint(name, "shipments", type_="check")
    # dropping columns is DDL: the row guard (UPDATE/DELETE trigger) is not involved
    if not found["shipments without a supplier"]:
        op.alter_column("shipments", "vendor_id", existing_type=UUID(as_uuid=True), nullable=False)
    for col in (
        "matched_shipment_id", "details_completed_at", "details_completed_by", "quick_reason",
        "needs_details", "verified_at", "verified_by", "verified_pieces", "verify_status",
        "expected_pieces", "is_airport", "invoice_date", "entry",
    ):  # fmt: skip
        op.drop_column("shipments", col)
