"""Module 01 catalog: categories, products, aliases, product-specific unit profiles.

Reviewed by hand after autogenerate. Triggers make product identity (company_id, sku,
stock_unit) and unit-profile conversion factors immutable at the database level; rows are
never deleted except aliases (which hold no history-bearing data; removals are audited).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07 22:08:29.518320
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION catalog_reject_identity_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION '% rows cannot be deleted', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
  END IF;
  -- Separate branches: PL/pgSQL resolves every NEW.field in an expression, so a
  -- combined condition would reference columns the other table does not have.
  IF TG_TABLE_NAME = 'products' THEN
    IF NEW.id <> OLD.id OR NEW.company_id <> OLD.company_id
        OR NEW.sku <> OLD.sku OR NEW.stock_unit <> OLD.stock_unit THEN
      RAISE EXCEPTION 'product identity is immutable' USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSIF TG_TABLE_NAME = 'product_unit_profiles' THEN
    IF NEW.id <> OLD.id OR NEW.product_id <> OLD.product_id
        OR NEW.code <> OLD.code OR NEW.unit_code <> OLD.unit_code
        OR NEW.pieces_per_unit IS DISTINCT FROM OLD.pieces_per_unit
        OR NEW.weight_lb_per_unit IS DISTINCT FROM OLD.weight_lb_per_unit THEN
      RAISE EXCEPTION 'unit profile conversion is immutable' USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;
  RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "product_categories",
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
        sa.CheckConstraint("char_length(code) BETWEEN 1 AND 32", name="code_len"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "code"),
        sa.UniqueConstraint("company_id", "name"),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column("stock_unit", sa.String(length=8), nullable=False),
        sa.Column("tracks_expiry", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
        sa.CheckConstraint("stock_unit IN ('BOX','PIECE','LB','KG')", name="stock_unit_valid"),
        sa.CheckConstraint("char_length(sku) BETWEEN 1 AND 64", name="sku_len"),
        sa.CheckConstraint("version >= 1", name="version_pos"),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["product_categories.id"],
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "sku"),
    )
    op.create_index(op.f("ix_products_category_id"), "products", ["category_id"], unique=False)
    op.create_index(
        "ix_products_company_name",
        "products",
        ["company_id", sa.literal_column("lower(name)")],
        unique=False,
    )
    op.create_table(
        "product_aliases",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("alias_norm", sa.Text(), nullable=False),
        sa.Column("source_label", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("char_length(alias_norm) >= 1", name="alias_nonempty"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "alias_norm", "source_label"),
    )
    op.create_index(
        op.f("ix_product_aliases_product_id"), "product_aliases", ["product_id"], unique=False
    )
    op.create_table(
        "product_unit_profiles",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("unit_code", sa.String(length=8), nullable=False),
        sa.Column("pieces_per_unit", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("weight_lb_per_unit", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "unit_code IN ('BOX','PIECE') OR (pieces_per_unit IS NULL AND weight_lb_per_unit IS NULL)",
            name="base_units_no_factors",
        ),
        sa.CheckConstraint("unit_code IN ('BOX','PIECE','LB','KG')", name="unit_code_valid"),
        sa.CheckConstraint("NOT is_default OR active", name="default_is_active"),
        sa.CheckConstraint("pieces_per_unit IS NULL OR pieces_per_unit > 0", name="pieces_pos"),
        sa.CheckConstraint(
            "weight_lb_per_unit IS NULL OR weight_lb_per_unit > 0", name="weight_pos"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "code"),
    )
    op.create_index(
        "uq_unit_profile_one_default",
        "product_unit_profiles",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.execute(IMMUTABLE_FN)
    for t in ("products", "product_unit_profiles"):
        op.execute(
            f"CREATE TRIGGER {t}_immutable BEFORE UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION catalog_reject_identity_change()"
        )


def downgrade() -> None:
    for t in ("products", "product_unit_profiles"):
        op.execute(f"DROP TRIGGER IF EXISTS {t}_immutable ON {t}")
    op.execute("DROP FUNCTION IF EXISTS catalog_reject_identity_change()")
    op.drop_index(
        "uq_unit_profile_one_default",
        table_name="product_unit_profiles",
        postgresql_where=sa.text("is_default"),
    )
    op.drop_table("product_unit_profiles")
    op.drop_index(op.f("ix_product_aliases_product_id"), table_name="product_aliases")
    op.drop_table("product_aliases")
    op.drop_index("ix_products_company_name", table_name="products")
    op.drop_index(op.f("ix_products_category_id"), table_name="products")
    op.drop_table("products")
    op.drop_table("product_categories")
