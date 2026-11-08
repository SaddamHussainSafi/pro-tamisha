"""Module 02 pricing: customer groups, customers, price book versions and entries.

Reviewed by hand after autogenerate. Triggers make published price books immutable:
entries can only change while their version is DRAFT; ACTIVE/ARCHIVED versions are frozen
except cancelling a *scheduled* (not yet effective) ACTIVE version by archiving it.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07 22:35:12.461667
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PRICE_FN = """
CREATE OR REPLACE FUNCTION price_reject_published_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
  IF TG_TABLE_NAME = 'price_entries' THEN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
      SELECT status INTO st FROM price_book_versions WHERE id = OLD.version_id;
      IF st <> 'DRAFT' THEN
        RAISE EXCEPTION 'published price entries are immutable' USING ERRCODE = 'insufficient_privilege';
      END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
      SELECT status INTO st FROM price_book_versions WHERE id = NEW.version_id;
      IF st <> 'DRAFT' THEN
        RAISE EXCEPTION 'published price entries are immutable' USING ERRCODE = 'insufficient_privilege';
      END IF;
      RETURN NEW;
    END IF;
    RETURN OLD;
  END IF;

  -- price_book_versions
  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'DRAFT' THEN
      RAISE EXCEPTION 'published price versions cannot be deleted' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN OLD;
  END IF;
  IF NEW.id <> OLD.id OR NEW.company_id <> OLD.company_id OR NEW.version_code <> OLD.version_code
      OR NEW.source_hash IS DISTINCT FROM OLD.source_hash
      OR NEW.based_on_id IS DISTINCT FROM OLD.based_on_id
      OR NEW.created_by IS DISTINCT FROM OLD.created_by OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'price version identity is immutable' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status = 'DRAFT' THEN
    IF NEW.status = 'ARCHIVED' THEN
      RAISE EXCEPTION 'drafts are discarded, not archived' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status = 'ACTIVE' AND (NEW.approved_by IS NULL OR NEW.approved_at IS NULL) THEN
      RAISE EXCEPTION 'activation requires an approver' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.status = 'ACTIVE' AND NEW.status = 'ARCHIVED' AND OLD.effective_at > now()
      AND NEW.effective_at = OLD.effective_at AND NEW.notes = OLD.notes
      AND NEW.approved_by IS NOT DISTINCT FROM OLD.approved_by
      AND NEW.approved_at IS NOT DISTINCT FROM OLD.approved_at THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'published price versions are immutable' USING ERRCODE = 'insufficient_privilege';
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "customer_groups",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("code ~ '^[A-Z0-9_-]{1,32}$'", name="code_format"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "code"),
    )
    op.create_table(
        "customers",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("external_ref", sa.String(length=64), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("group_id", sa.UUID(), nullable=True),
        sa.Column("payment_terms_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("payment_terms_days >= 0", name="terms_nonneg"),
        sa.CheckConstraint("version >= 1", name="version_pos"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["customer_groups.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "external_ref"),
    )
    op.create_index(
        "ix_customers_company_name",
        "customers",
        ["company_id", sa.literal_column("lower(name)")],
        unique=False,
    )
    op.create_index(op.f("ix_customers_group_id"), "customers", ["group_id"], unique=False)
    op.create_table(
        "price_book_versions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("version_code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="DRAFT", nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("based_on_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("approved_by", sa.UUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status = 'DRAFT' OR effective_at IS NOT NULL", name="effective_set"),
        sa.CheckConstraint("status IN ('DRAFT','ACTIVE','ARCHIVED')", name="status_valid"),
        sa.CheckConstraint("version >= 1", name="version_pos"),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["employees.id"],
        ),
        sa.ForeignKeyConstraint(
            ["based_on_id"],
            ["price_book_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["employees.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "version_code"),
    )
    op.create_index(
        "uq_price_version_active_effective",
        "price_book_versions",
        ["company_id", "effective_at"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "uq_price_version_source_hash",
        "price_book_versions",
        ["company_id", "source_hash"],
        unique=True,
        postgresql_where=sa.text("source_hash IS NOT NULL"),
    )
    op.create_table(
        "price_entries",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("group_id", sa.UUID(), nullable=True),
        sa.Column("unit_code", sa.String(length=8), nullable=False),
        sa.Column("rate", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="CAD", nullable=False),
        sa.CheckConstraint(
            "(scope = 'STANDARD' AND customer_id IS NULL AND group_id IS NULL) OR (scope = 'GROUP' AND group_id IS NOT NULL AND customer_id IS NULL) OR (scope = 'CUSTOMER' AND customer_id IS NOT NULL AND group_id IS NULL)",
            name="scope_target",
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        sa.CheckConstraint("scope IN ('STANDARD','GROUP','CUSTOMER')", name="scope_valid"),
        sa.CheckConstraint("unit_code IN ('BOX','PIECE','LB','KG')", name="unit_code_valid"),
        sa.CheckConstraint("rate >= 0", name="rate_nonneg"),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["customer_groups.id"],
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["price_book_versions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_price_entries_version_id"), "price_entries", ["version_id"], unique=False
    )
    op.create_index(
        "ix_price_entry_lookup", "price_entries", ["product_id", "unit_code"], unique=False
    )
    op.create_index(
        "uq_price_entry_key",
        "price_entries",
        [
            "version_id",
            "product_id",
            "unit_code",
            "scope",
            sa.literal_column(
                "coalesce(customer_id, group_id, '00000000-0000-0000-0000-000000000000'::uuid)"
            ),
        ],
        unique=True,
    )
    op.execute(PRICE_FN)
    op.execute(
        "CREATE TRIGGER price_entries_immutable BEFORE INSERT OR UPDATE OR DELETE ON price_entries "
        "FOR EACH ROW EXECUTE FUNCTION price_reject_published_change()"
    )
    op.execute(
        "CREATE TRIGGER price_book_versions_immutable BEFORE UPDATE OR DELETE "
        "ON price_book_versions FOR EACH ROW EXECUTE FUNCTION price_reject_published_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS price_entries_immutable ON price_entries")
    op.execute("DROP TRIGGER IF EXISTS price_book_versions_immutable ON price_book_versions")
    op.execute("DROP FUNCTION IF EXISTS price_reject_published_change()")
    op.drop_index("uq_price_entry_key", table_name="price_entries")
    op.drop_index("ix_price_entry_lookup", table_name="price_entries")
    op.drop_index(op.f("ix_price_entries_version_id"), table_name="price_entries")
    op.drop_table("price_entries")
    op.drop_index(
        "uq_price_version_source_hash",
        table_name="price_book_versions",
        postgresql_where=sa.text("source_hash IS NOT NULL"),
    )
    op.drop_index(
        "uq_price_version_active_effective",
        table_name="price_book_versions",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_table("price_book_versions")
    op.drop_index(op.f("ix_customers_group_id"), table_name="customers")
    op.drop_index("ix_customers_company_name", table_name="customers")
    op.drop_table("customers")
    op.drop_table("customer_groups")
