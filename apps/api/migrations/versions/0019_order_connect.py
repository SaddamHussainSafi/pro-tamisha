"""Order Connect: website order forms, submissions and their review timeline. Staff alerts use
the Notification Center tables (migration 0018).

Adds `WEB` to sales_orders.source (a converted submission becomes a DRAFT order).
Rollback: `alembic downgrade 0018` drops every Order Connect table. Orders already created
from the web keep source `WEB` (the narrowed check is added NOT VALID, so no row is rewritten).

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = sa.UUID()
GEN = sa.text("gen_random_uuid()")
NOW = sa.text("now()")


def _id() -> sa.Column:
    return sa.Column("id", UUID, server_default=GEN, nullable=False)


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=None if nullable else NOW,
        nullable=nullable,
    )


GUARDS = """
CREATE OR REPLACE FUNCTION oc_version_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'DRAFT' THEN
      RAISE EXCEPTION 'a published order form version cannot be deleted';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status = 'RETIRED' THEN
    RAISE EXCEPTION 'a retired order form version cannot change';
  END IF;
  IF OLD.status = 'PUBLISHED' AND (
       NEW.status <> 'RETIRED' OR NEW.title <> OLD.title OR NEW.intro <> OLD.intro
       OR NEW.success_message <> OLD.success_message OR NEW.theme <> OLD.theme
       OR NEW.logo_id IS DISTINCT FROM OLD.logo_id OR NEW.show_prices <> OLD.show_prices
       OR NEW.product_scope <> OLD.product_scope OR NEW.scope_ids <> OLD.scope_ids
       OR NEW.form_id <> OLD.form_id OR NEW.version_no <> OLD.version_no
       OR NEW.published_at <> OLD.published_at) THEN
    RAISE EXCEPTION 'a published order form version is immutable (only retirement is allowed)';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION oc_field_guard() RETURNS trigger AS $$
DECLARE st text;
BEGIN
  SELECT status INTO st FROM order_form_versions
   WHERE id = (CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END);
  IF st IS NOT NULL AND st <> 'DRAFT' THEN
    RAISE EXCEPTION 'fields of a published order form version cannot change';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.version_id <> OLD.version_id THEN
    RAISE EXCEPTION 'a field cannot move to another version';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION oc_submission_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'order form submissions cannot be deleted (erase personal data instead)';
  END IF;
  IF NEW.company_id <> OLD.company_id OR NEW.form_id <> OLD.form_id
     OR NEW.version_id <> OLD.version_id OR NEW.reference <> OLD.reference
     OR NEW.client_token <> OLD.client_token OR NEW.created_at <> OLD.created_at
     OR NEW.content_hash <> OLD.content_hash OR NEW.source_host <> OLD.source_host
     OR NEW.requested_date IS DISTINCT FROM OLD.requested_date
     OR NEW.consent_at IS DISTINCT FROM OLD.consent_at THEN
    RAISE EXCEPTION 'submitted order form values are immutable';
  END IF;
  IF (NEW.raw <> OLD.raw OR NEW.customer_name <> OLD.customer_name
      OR NEW.business_name <> OLD.business_name OR NEW.email <> OLD.email
      OR NEW.phone <> OLD.phone OR NEW.delivery_address <> OLD.delivery_address
      OR NEW.notes <> OLD.notes OR NEW.custom <> OLD.custom
      OR NEW.user_agent <> OLD.user_agent OR NEW.ip_hash <> OLD.ip_hash)
     AND NOT (OLD.erased_at IS NULL AND NEW.erased_at IS NOT NULL) THEN
    RAISE EXCEPTION 'submitted order form values are immutable (erasure only)';
  END IF;
  IF OLD.erased_at IS NOT NULL AND NEW.erased_at IS DISTINCT FROM OLD.erased_at THEN
    RAISE EXCEPTION 'erasure cannot be undone';
  END IF;
  IF OLD.status = 'CONVERTED' AND (NEW.status <> 'CONVERTED'
      OR NEW.sales_order_id IS DISTINCT FROM OLD.sales_order_id
      OR NEW.customer_id IS DISTINCT FROM OLD.customer_id
      OR NEW.converted_at IS DISTINCT FROM OLD.converted_at) THEN
    RAISE EXCEPTION 'a converted submission cannot change status or order';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION oc_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "order_connect_settings",
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("notify_staff", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("ack_email", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("min_fill_seconds", sa.Integer(), server_default="3", nullable=False),
        sa.Column("ip_limit_per_hour", sa.Integer(), server_default="20", nullable=False),
        sa.Column("form_limit_per_hour", sa.Integer(), server_default="300", nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column(
            "consent_required", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("consent_text", sa.Text(), server_default="", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_by", UUID, nullable=True),
        _ts("updated_at"),
        sa.CheckConstraint("min_fill_seconds BETWEEN 0 AND 120", name="min_fill_range"),
        sa.CheckConstraint("ip_limit_per_hour BETWEEN 1 AND 1000", name="ip_limit_range"),
        sa.CheckConstraint("form_limit_per_hour BETWEEN 1 AND 10000", name="form_limit_range"),
        sa.CheckConstraint(
            "retention_days IS NULL OR retention_days BETWEEN 30 AND 3650",
            name="retention_range",
        ),
        sa.CheckConstraint("NOT consent_required OR char_length(consent_text) > 0", name="consent"),
        sa.CheckConstraint("version >= 1", name="version_pos"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["employees.id"]),
        sa.PrimaryKeyConstraint("company_id"),
    )
    op.create_table(
        "order_forms",
        _id(),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("public_id", sa.String(32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("accepting", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("standalone_link", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("default_warehouse_id", UUID, nullable=True),
        sa.Column("published_version_id", UUID, nullable=True),
        _ts("archived_at", nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", UUID, nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("public_id ~ '^[A-Za-z0-9_-]{22,32}$'", name="public_id_format"),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="name_len"),
        sa.CheckConstraint("version >= 1", name="version_pos"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["default_warehouse_id"], ["warehouses.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index("ix_order_forms_company", "order_forms", ["company_id"])
    op.create_table(
        "order_form_logos",
        _id(),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mime", sa.String(16), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("created_by", UUID, nullable=True),
        _ts("created_at"),
        sa.CheckConstraint("mime IN ('image/png')", name="mime_valid"),
        sa.CheckConstraint("byte_size BETWEEN 1 AND 524288", name="size_range"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "sha256"),
    )
    op.create_table(
        "order_form_versions",
        _id(),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("form_id", UUID, nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), server_default="DRAFT", nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("intro", sa.Text(), server_default="", nullable=False),
        sa.Column("success_message", sa.Text(), nullable=False),
        sa.Column("theme", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("logo_id", UUID, nullable=True),
        sa.Column("show_prices", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("product_scope", sa.String(12), server_default="ALL", nullable=False),
        sa.Column("scope_ids", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", UUID, nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("published_by", UUID, nullable=True),
        _ts("published_at", nullable=True),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED','RETIRED')", name="status_valid"),
        sa.CheckConstraint("product_scope IN ('ALL','CATEGORIES','PRODUCTS')", name="scope_valid"),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 120", name="title_len"),
        sa.CheckConstraint("char_length(success_message) BETWEEN 1 AND 500", name="success_len"),
        sa.CheckConstraint("revision >= 1", name="revision_pos"),
        sa.CheckConstraint("(status = 'DRAFT') = (published_at IS NULL)", name="published_stamp"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["form_id"], ["order_forms.id"]),
        sa.ForeignKeyConstraint(["logo_id"], ["order_form_logos.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["employees.id"]),
        sa.ForeignKeyConstraint(["published_by"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("form_id", "version_no"),
    )
    op.create_index(
        "uq_order_form_one_draft",
        "order_form_versions",
        ["form_id"],
        unique=True,
        postgresql_where=sa.text("status = 'DRAFT'"),
    )
    op.create_index(
        "uq_order_form_one_published",
        "order_form_versions",
        ["form_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
    op.create_foreign_key(
        "fk_order_forms_published",
        "order_forms",
        "order_form_versions",
        ["published_version_id"],
        ["id"],
    )
    op.create_table(
        "order_form_fields",
        _id(),
        sa.Column("version_id", UUID, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("help_text", sa.Text(), server_default="", nullable=False),
        sa.Column("required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("options", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('TEXT','TEXTAREA','EMAIL','PHONE','DATE','SELECT','CHECKBOX','PRODUCTS')",
            name="kind_valid",
        ),
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]{0,39}$'", name="key_format"),
        sa.CheckConstraint("char_length(label) BETWEEN 1 AND 120", name="label_len"),
        sa.CheckConstraint("position >= 0", name="position_nonneg"),
        sa.ForeignKeyConstraint(["version_id"], ["order_form_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "key"),
        sa.UniqueConstraint("version_id", "position"),
    )
    op.create_index("ix_order_form_fields_version_id", "order_form_fields", ["version_id"])
    op.create_table(
        "order_form_allowed_domains",
        _id(),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("form_id", UUID, nullable=True),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("created_by", UUID, nullable=True),
        _ts("created_at"),
        sa.CheckConstraint(
            r"host ~ '^(\*\.)?[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
            r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$'",
            name="host_format",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["form_id"], ["order_forms.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "form_id", "host", postgresql_nulls_not_distinct=True),
    )
    op.create_index(
        "ix_order_form_allowed_domains_form_id", "order_form_allowed_domains", ["form_id"]
    )
    op.create_table(
        "order_form_submissions",
        _id(),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("form_id", UUID, nullable=False),
        sa.Column("version_id", UUID, nullable=False),
        sa.Column("reference", sa.String(16), nullable=False),
        sa.Column("client_token", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), server_default="NEW", nullable=False),
        sa.Column("customer_name", sa.Text(), server_default="", nullable=False),
        sa.Column("business_name", sa.Text(), server_default="", nullable=False),
        sa.Column("email", sa.Text(), server_default="", nullable=False),
        sa.Column("phone", sa.Text(), server_default="", nullable=False),
        sa.Column("delivery_address", sa.Text(), server_default="", nullable=False),
        sa.Column("requested_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column("custom", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("raw", pg.JSONB(), nullable=False),
        _ts("consent_at", nullable=True),
        sa.Column("source_host", sa.Text(), server_default="", nullable=False),
        sa.Column("ip_hash", sa.String(64), server_default="", nullable=False),
        sa.Column("user_agent", sa.Text(), server_default="", nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "spam_reasons", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("possible_duplicate_of", UUID, nullable=True),
        sa.Column("customer_id", UUID, nullable=True),
        sa.Column("sales_order_id", UUID, nullable=True),
        sa.Column("converted_by", UUID, nullable=True),
        _ts("converted_at", nullable=True),
        _ts("erased_at", nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(
            "status IN ('NEW','REVIEWING','APPROVED','CONVERTED','REJECTED','DUPLICATE','SPAM')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'CONVERTED') = (sales_order_id IS NOT NULL)", name="converted_has_order"
        ),
        sa.CheckConstraint("reference ~ '^OC-[0-9A-Z]{6,10}$'", name="reference_format"),
        sa.CheckConstraint("version >= 1", name="version_pos"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["form_id"], ["order_forms.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["order_form_versions.id"]),
        sa.ForeignKeyConstraint(["possible_duplicate_of"], ["order_form_submissions.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["sales_order_id"], ["sales_orders.id"]),
        sa.ForeignKeyConstraint(["converted_by"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "reference"),
        sa.UniqueConstraint("form_id", "client_token"),
        sa.UniqueConstraint("sales_order_id"),
    )
    op.create_index(
        "ix_oc_submissions_company_status",
        "order_form_submissions",
        ["company_id", "status", "created_at"],
    )
    op.create_index(
        "ix_oc_submissions_company_created", "order_form_submissions", ["company_id", "created_at"]
    )
    op.create_table(
        "order_form_submission_items",
        _id(),
        sa.Column("submission_id", UUID, nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("product_id", UUID, nullable=False),
        sa.Column("unit_code", sa.String(8), nullable=False),
        sa.Column("qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("sku", sa.String(3), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.CheckConstraint("unit_code IN ('BOX','PIECE','LB','KG')", name="unit_valid"),
        sa.CheckConstraint("qty > 0", name="qty_pos"),
        sa.ForeignKeyConstraint(["submission_id"], ["order_form_submissions.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_id", "line_no"),
        sa.UniqueConstraint("submission_id", "product_id", "unit_code"),
    )
    op.create_index(
        "ix_order_form_submission_items_submission_id",
        "order_form_submission_items",
        ["submission_id"],
    )
    op.create_table(
        "order_form_submission_events",
        _id(),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("submission_id", UUID, nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("from_status", sa.String(12), nullable=True),
        sa.Column("to_status", sa.String(12), nullable=True),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column("actor_id", UUID, nullable=True),
        _ts("created_at"),
        sa.CheckConstraint(
            "kind IN ('RECEIVED','STATUS','NOTE','CONVERTED','ERASED')", name="kind_valid"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["submission_id"], ["order_form_submissions.id"]),
        sa.ForeignKeyConstraint(["actor_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_oc_events_submission",
        "order_form_submission_events",
        ["submission_id", "created_at"],
    )
    op.execute(GUARDS)
    for table, fn, when in [
        ("order_form_versions", "oc_version_guard", "BEFORE UPDATE OR DELETE"),
        ("order_form_fields", "oc_field_guard", "BEFORE INSERT OR UPDATE OR DELETE"),
        ("order_form_submissions", "oc_submission_guard", "BEFORE UPDATE OR DELETE"),
        ("order_form_submission_items", "oc_append_only", "BEFORE UPDATE OR DELETE"),
        ("order_form_submission_events", "oc_append_only", "BEFORE UPDATE OR DELETE"),
    ]:
        op.execute(
            f"CREATE TRIGGER {table}_guard {when} ON {table} FOR EACH ROW EXECUTE FUNCTION {fn}()"
        )

    op.drop_constraint("source_valid", "sales_orders", type_="check")
    op.create_check_constraint("source_valid", "sales_orders", "source IN ('MANUAL','SCAN','WEB')")


def downgrade() -> None:
    op.drop_constraint("source_valid", "sales_orders", type_="check")
    op.execute(
        "ALTER TABLE sales_orders ADD CONSTRAINT source_valid "
        "CHECK (source IN ('MANUAL','SCAN')) NOT VALID"
    )
    for table in (
        "order_form_submission_events",
        "order_form_submission_items",
        "order_form_submissions",
        "order_form_fields",
        "order_form_versions",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_guard ON {table}")
    for fn in ("oc_version_guard", "oc_field_guard", "oc_submission_guard", "oc_append_only"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    op.drop_table("order_form_submission_events")
    op.drop_table("order_form_submission_items")
    op.drop_table("order_form_submissions")
    op.drop_table("order_form_allowed_domains")
    op.drop_table("order_form_fields")
    op.drop_constraint("fk_order_forms_published", "order_forms", type_="foreignkey")
    op.drop_table("order_form_versions")
    op.drop_table("order_form_logos")
    op.drop_index("ix_order_forms_company", table_name="order_forms")
    op.drop_table("order_forms")
    op.drop_table("order_connect_settings")
