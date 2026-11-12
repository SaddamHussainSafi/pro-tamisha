"""Universal search: one table of search documents, a change queue fed by triggers, and
case-insensitive indexes for the exact-number lookups that must work even when the search
table is behind.

- `search_documents`: one row per searchable record (product, customer, invoice, batch, ...).
  It is a copy for finding things; the ERP tables stay the source of truth.
- `search_index_queue`: append-only list of "this record changed". AFTER triggers on the source
  tables add a row inside the business transaction (a plain INSERT, never an upsert, so two
  transactions touching the same customer never wait on each other). The Celery worker drains
  it, rebuilds each document from the source tables and deletes the rows it handled.
- `upper(...)` indexes: exact lookups of invoice / order / shipment / GRN / airway bill /
  supplier invoice / batch / lot / scan / payment numbers and customer / supplier codes run
  straight against the ERP tables, so they work before the index catches up.

Revision ID: 0029
Revises: 0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (source table, search document type, column holding that document's id)
TRIGGERS: tuple[tuple[str, str, str], ...] = (
    ("products", "product", "id"),
    ("product_aliases", "product", "product_id"),
    ("product_unit_profiles", "product", "product_id"),
    ("customers", "customer", "id"),
    ("vendors", "vendor", "id"),
    ("inventory_lots", "lot", "id"),
    ("stock_counts", "stock_count", "id"),
    ("sales_orders", "sales_order", "id"),
    ("sales_order_lines", "sales_order", "order_id"),
    ("picking_attempts", "pick", "id"),
    ("invoices", "invoice", "id"),
    ("invoice_lines", "invoice", "invoice_id"),
    ("shipments", "shipment", "id"),
    ("shipment_documents", "shipment", "shipment_id"),
    ("shipment_declared_lines", "shipment", "shipment_id"),
    ("grn_lines", "shipment", "shipment_id"),
    ("shipment_costs", "shipment", "shipment_id"),
    ("payments", "payment", "id"),
    ("payment_allocations", "payment", "payment_id"),
    ("supplier_payments", "supplier_payment", "id"),
    ("scan_jobs", "scan", "id"),
    ("ocr_extractions", "scan", "scan_job_id"),
    ("document_files", "document", "id"),
    ("gmail_messages", "gmail", "id"),
    ("gmail_attachments", "gmail", "message_id"),
)

# Exact-lookup indexes: (index name, table, column)
UPPER_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_search_up_invoice_no", "invoices", "invoice_no"),
    ("ix_search_up_order_no", "sales_orders", "order_no"),
    ("ix_search_up_pick_code", "picking_attempts", "pick_code"),
    ("ix_search_up_shipment_no", "shipments", "shipment_no"),
    ("ix_search_up_grn_no", "shipments", "grn_no"),
    ("ix_search_up_awb_no", "shipments", "awb_no"),
    ("ix_search_up_vendor_invoice_no", "shipments", "vendor_invoice_no"),
    ("ix_search_up_doc_ref", "shipment_documents", "doc_ref"),
    ("ix_search_up_grn_batch", "grn_lines", "batch_ref"),
    ("ix_search_up_lot_code", "inventory_lots", "lot_code"),
    ("ix_search_up_lot_batch", "inventory_lots", "batch_ref"),
    ("ix_search_up_count_no", "stock_counts", "count_no"),
    ("ix_search_up_job_no", "scan_jobs", "job_no"),
    ("ix_search_up_payment_no", "payments", "payment_no"),
    ("ix_search_up_payer_ref", "payments", "payer_ref"),
    ("ix_search_up_verification_ref", "payments", "verification_ref"),
    ("ix_search_up_sup_payment_no", "supplier_payments", "payment_no"),
    ("ix_search_up_sup_reference", "supplier_payments", "reference"),
    ("ix_search_up_customer_ref", "customers", "external_ref"),
    ("ix_search_up_vendor_code", "vendors", "code"),
)


def _fn(col: str) -> str:
    return f"search_enqueue_by_{col}"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "search_documents",
        sa.Column("doc_type", sa.String(24), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        # NULL = a company-level record (product, customer, payment, ...)
        sa.Column("warehouse_id", UUID(as_uuid=True)),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subtitle", sa.Text(), nullable=False, server_default=""),
        sa.Column("title_norm", sa.Text(), nullable=False),
        # Upper-cased numbers/codes: as written and with spaces/dashes removed.
        sa.Column("identifiers", ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("ident_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("names", sa.Text(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("ocr_text", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "tsv",
            sa.dialects.postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('simple', title), 'A') || "
                "setweight(to_tsvector('simple', names), 'B') || "
                "setweight(to_tsvector('simple', body), 'C')",
                persisted=True,
            ),
        ),
        sa.Column(
            "ocr_tsv",
            sa.dialects.postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', left(ocr_text, 100000))", persisted=True),
        ),
        sa.Column("status", sa.String(32)),
        sa.Column("doc_date", sa.Date()),
        sa.Column("amount", sa.Numeric(18, 2)),
        sa.Column("currency", sa.String(3)),
        sa.Column("customer_id", UUID(as_uuid=True)),
        sa.Column("vendor_id", UUID(as_uuid=True)),
        sa.Column("product_ids", ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"),
        sa.Column("attrs", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "indexed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("doc_type", "source_id", name="pk_search_documents"),
    )
    op.execute("CREATE INDEX ix_search_identifiers ON search_documents USING gin (identifiers)")
    op.execute(
        "CREATE INDEX ix_search_ident_trgm ON search_documents USING gin (ident_text gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_search_title_trgm ON search_documents USING gin (title_norm gin_trgm_ops)"
    )
    op.execute("CREATE INDEX ix_search_tsv ON search_documents USING gin (tsv)")
    op.execute("CREATE INDEX ix_search_ocr_tsv ON search_documents USING gin (ocr_tsv)")
    op.execute("CREATE INDEX ix_search_product_ids ON search_documents USING gin (product_ids)")
    op.create_index(
        "ix_search_company_type_date", "search_documents", ["company_id", "doc_type", "doc_date"]
    )
    op.create_index("ix_search_customer", "search_documents", ["customer_id"])
    op.create_index("ix_search_vendor", "search_documents", ["vendor_id"])

    op.create_table(
        "search_index_queue",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("doc_type", sa.String(24), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("status IN ('PENDING','FAILED')", name="search_queue_status_valid"),
    )
    op.execute(
        "CREATE INDEX ix_search_queue_pending ON search_index_queue (next_attempt_at, id) "
        "WHERE status = 'PENDING'"
    )
    op.create_index("ix_search_queue_key", "search_index_queue", ["doc_type", "source_id"])

    # Recently opened records, per employee (private; the employee can clear them). No foreign
    # key on purpose: the Trash refuses to remove rows that unknown keys point at.
    op.create_table(
        "search_recent",
        sa.Column("employee_id", UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False),
        sa.Column("doc_type", sa.String(24), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("employee_id", "doc_type", "source_id", name="pk_search_recent"),
    )
    op.create_index("ix_search_recent_opened", "search_recent", ["employee_id", "opened_at"])

    # One trigger function per id column. Plain INSERT: never waits on another transaction.
    for col in sorted({c for _, _, c in TRIGGERS}):
        # DDL from the constant TRIGGERS table above (no outside input).
        op.execute(
            f"""
            CREATE FUNCTION {_fn(col)}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF TG_OP <> 'INSERT' AND OLD.{col} IS NOT NULL THEN
                INSERT INTO search_index_queue (doc_type, source_id) VALUES (TG_ARGV[0], OLD.{col});
              END IF;
              IF TG_OP <> 'DELETE' AND NEW.{col} IS NOT NULL
                 AND (TG_OP = 'INSERT' OR NEW.{col} IS DISTINCT FROM OLD.{col}) THEN
                INSERT INTO search_index_queue (doc_type, source_id) VALUES (TG_ARGV[0], NEW.{col});
              END IF;
              RETURN NULL;
            END $$
            """  # noqa: S608
        )
    for table, doc_type, col in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER trg_search_{table} AFTER INSERT OR UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {_fn(col)}('{doc_type}')"
        )

    for name, table, col in UPPER_INDEXES:
        op.execute(f"CREATE INDEX {name} ON {table} (upper({col}))")


def downgrade() -> None:
    for name, _, _ in UPPER_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    for table, _, _ in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS trg_search_{table} ON {table}")
    for col in sorted({c for _, _, c in TRIGGERS}):
        op.execute(f"DROP FUNCTION IF EXISTS {_fn(col)}()")
    op.drop_table("search_recent")
    op.drop_table("search_index_queue")
    op.drop_table("search_documents")
    # pg_trgm is left installed: dropping an extension could break objects created later.
