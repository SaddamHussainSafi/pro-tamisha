"""Module 00 foundation tables. Later modules add their own models + migrations.

Schema source: db/schema.sql (reference only), with gaps fixed — see docs/decisions/ADR-0001.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[uuid.UUID] = _pk()
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="America/Toronto")
    created_at: Mapped[datetime] = _created()


class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        UniqueConstraint("company_id", "code"),
        UniqueConstraint("company_id", "name"),
        CheckConstraint("char_length(code) BETWEEN 1 AND 32", name="code_len"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        Index("uq_employees_email_lower", text("lower(email)"), unique=True),
        CheckConstraint("failed_login_count >= 0", name="failed_login_nonneg"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    mfa_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_last_step: Mapped[int | None] = mapped_column(BigInteger)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Permission(Base):
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("company_id", "code"),)
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    privileged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(ForeignKey("permissions.code"), primary_key=True)


class EmployeeRole(Base):
    """Role grant. warehouse_id NULL = company-wide; otherwise scoped to one warehouse."""

    __tablename__ = "employee_roles"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "role_id", "warehouse_id", postgresql_nulls_not_distinct=True
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"))
    created_at: Mapped[datetime] = _created()


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_employee_active", "employee_id", "revoked_at"),)
    id: Mapped[uuid.UUID] = _pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mfa_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)


class FeatureFlag(Base):
    __tablename__ = "feature_flags"
    __table_args__ = (UniqueConstraint("company_id", "key"),)
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    updated_at: Mapped[datetime] = _created()


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("scope", "idem_key"),)
    id: Mapped[uuid.UUID] = _pk()
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    idem_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class AuditEvent(Base):
    """Append-only. UPDATE/DELETE are rejected by a DB trigger (see migration 0001)."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_company_created", "company_id", "created_at"),)
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"))
    request_id: Mapped[str | None] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    detail_redacted: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created()


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','PROCESSED','FAILED')", name="status_valid"),
        Index("ix_outbox_pending", "created_at", postgresql_where=text("status = 'PENDING'")),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"))
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- Module 01 catalog

UNIT_CODES = ("BOX", "PIECE", "LB", "KG")
_UNIT_CHECK = "IN ('BOX','PIECE','LB','KG')"


class ProductCategory(Base):
    __tablename__ = "product_categories"
    __table_args__ = (
        UniqueConstraint("company_id", "code"),
        UniqueConstraint("company_id", "name"),
        CheckConstraint("char_length(code) BETWEEN 1 AND 32", name="code_len"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _created()


class Product(Base):
    """Canonical product. id, sku and stock_unit are immutable (stock depends on them)."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("company_id", "sku"),
        CheckConstraint(f"stock_unit {_UNIT_CHECK}", name="stock_unit_valid"),
        CheckConstraint("char_length(sku) BETWEEN 1 AND 64", name="sku_len"),
        CheckConstraint("version >= 1", name="version_pos"),
        Index("ix_products_company_name", "company_id", text("lower(name)")),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_categories.id"), index=True
    )
    stock_unit: Mapped[str] = mapped_column(String(8), nullable=False)
    tracks_expiry: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class ProductAlias(Base):
    """Name used on vendor/customer forms. One normalized alias per source maps to one product."""

    __tablename__ = "product_aliases"
    __table_args__ = (
        UniqueConstraint("company_id", "alias_norm", "source_label"),
        CheckConstraint("char_length(alias_norm) >= 1", name="alias_nonempty"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    alias_norm: Mapped[str] = mapped_column(Text, nullable=False)
    source_label: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = _created()


class ProductUnitProfile(Base):
    """Product-specific packing/conversion rule. Conversion values are immutable once created
    (later documents snapshot them); to change, deactivate and add a new profile."""

    __tablename__ = "product_unit_profiles"
    __table_args__ = (
        UniqueConstraint("product_id", "code"),
        CheckConstraint(f"unit_code {_UNIT_CHECK}", name="unit_code_valid"),
        CheckConstraint("pieces_per_unit IS NULL OR pieces_per_unit > 0", name="pieces_pos"),
        CheckConstraint("weight_lb_per_unit IS NULL OR weight_lb_per_unit > 0", name="weight_pos"),
        CheckConstraint(
            "unit_code IN ('BOX','PIECE') "
            "OR (pieces_per_unit IS NULL AND weight_lb_per_unit IS NULL)",
            name="base_units_no_factors",
        ),
        CheckConstraint("NOT is_default OR active", name="default_is_active"),
        Index(
            "uq_unit_profile_one_default",
            "product_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(8), nullable=False)
    pieces_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    weight_lb_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()


# ------------------------------------------------------------------ Module 02 pricing


class CustomerGroup(Base):
    """Pricing group (e.g. WHOLESALE, RESTAURANT). Code is immutable once used in prices."""

    __tablename__ = "customer_groups"
    __table_args__ = (
        UniqueConstraint("company_id", "code"),
        CheckConstraint("code ~ '^[A-Z0-9_-]{1,32}$'", name="code_format"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _created()


class Customer(Base):
    """Customer master (minimal for pricing; extended by sales/invoicing modules)."""

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("company_id", "external_ref"),
        CheckConstraint("payment_terms_days >= 0", name="terms_nonneg"),
        CheckConstraint("version >= 1", name="version_pos"),
        Index("ix_customers_company_name", "company_id", text("lower(name)")),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(32))
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer_groups.id"), index=True)
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


PRICE_STATUSES = ("DRAFT", "ACTIVE", "ARCHIVED")
PRICE_SCOPES = ("STANDARD", "GROUP", "CUSTOMER")


class PriceBookVersion(Base):
    """A complete price book. DRAFT is editable; ACTIVE/ARCHIVED are immutable (DB trigger),
    except a scheduled (not yet effective) ACTIVE version may be ARCHIVED."""

    __tablename__ = "price_book_versions"
    __table_args__ = (
        UniqueConstraint("company_id", "version_code"),
        CheckConstraint("status IN ('DRAFT','ACTIVE','ARCHIVED')", name="status_valid"),
        CheckConstraint("status = 'DRAFT' OR effective_at IS NOT NULL", name="effective_set"),
        CheckConstraint("version >= 1", name="version_pos"),
        Index(
            "uq_price_version_source_hash",
            "company_id",
            "source_hash",
            unique=True,
            postgresql_where=text("source_hash IS NOT NULL"),
        ),
        Index(
            "uq_price_version_active_effective",
            "company_id",
            "effective_at",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    version_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="DRAFT")
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_hash: Mapped[str | None] = mapped_column(String(64))
    based_on_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("price_book_versions.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()


class PriceEntry(Base):
    """Rate for (product, unit) at one scope. Exactly one target per scope."""

    __tablename__ = "price_entries"
    __table_args__ = (
        CheckConstraint(f"unit_code {_UNIT_CHECK}", name="unit_code_valid"),
        CheckConstraint("scope IN ('STANDARD','GROUP','CUSTOMER')", name="scope_valid"),
        CheckConstraint(
            "(scope = 'STANDARD' AND customer_id IS NULL AND group_id IS NULL)"
            " OR (scope = 'GROUP' AND group_id IS NOT NULL AND customer_id IS NULL)"
            " OR (scope = 'CUSTOMER' AND customer_id IS NOT NULL AND group_id IS NULL)",
            name="scope_target",
        ),
        CheckConstraint("rate >= 0", name="rate_nonneg"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        Index(
            "uq_price_entry_key",
            "version_id",
            "product_id",
            "unit_code",
            "scope",
            text("coalesce(customer_id, group_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
        ),
        Index("ix_price_entry_lookup", "product_id", "unit_code"),
    )
    id: Mapped[uuid.UUID] = _pk()
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("price_book_versions.id"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"))
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer_groups.id"))
    unit_code: Mapped[str] = mapped_column(String(8), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="CAD")


# ------------------------------------------------------------------ Module 03 inventory

LOT_STATUSES = ("SALEABLE", "DAMAGED", "QUARANTINE", "EXPIRED")
MOVEMENT_TYPES = (
    "OPENING_BALANCE",
    "GRN_RECEIPT",
    "INVOICE_DISPATCH",
    "ADJUSTMENT",
    "COUNT_ADJUSTMENT",
    "DAMAGE",
    "RETURN",
    "REVERSAL",
)


def _in(vals: tuple[str, ...]) -> str:
    return "IN (" + ",".join(f"'{v}'" for v in vals) + ")"


class InventoryLot(Base):
    """Physical batch of one product in one warehouse. Identity is immutable (DB trigger).
    A DAMAGED companion lot (parent_lot_id) holds damaged stock split off a saleable lot."""

    __tablename__ = "inventory_lots"
    __table_args__ = (
        UniqueConstraint("company_id", "lot_code"),
        CheckConstraint(f"status {_in(LOT_STATUSES)}", name="status_valid"),
        CheckConstraint("landed_unit_cost IS NULL OR landed_unit_cost >= 0", name="cost_nonneg"),
        CheckConstraint(
            "cost_currency IS NULL OR cost_currency ~ '^[A-Z]{3}$'", name="currency_iso"
        ),
        Index(
            "uq_lot_damaged_companion",
            "parent_lot_id",
            unique=True,
            postgresql_where=text("parent_lot_id IS NOT NULL"),
        ),
        Index("ix_lots_product_wh", "company_id", "product_id", "warehouse_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    lot_code: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_ref: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="SALEABLE")
    parent_lot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_lots.id"))
    landed_unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    cost_currency: Mapped[str | None] = mapped_column(String(3))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()


class InventoryBalance(Base):
    """Cached on-hand per lot and measured unit (boxes and lb are separate measures).
    Always derivable from stock_movements; only changed together with a movement row."""

    __tablename__ = "inventory_balances"
    __table_args__ = (
        CheckConstraint(f"unit_code {_UNIT_CHECK}", name="unit_code_valid"),
        CheckConstraint("qty >= 0", name="qty_nonneg"),
        CheckConstraint("reserved_qty >= 0", name="reserved_nonneg"),
        CheckConstraint("reserved_qty <= qty", name="reserved_le_qty"),
        Index("ix_balances_product_wh", "company_id", "product_id", "warehouse_id"),
    )
    lot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_lots.id"), primary_key=True)
    unit_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, server_default="0")
    reserved_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, server_default="0"
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = _created()


class StockMovement(Base):
    """Append-only signed ledger row (UPDATE/DELETE rejected by trigger). posting_key is the
    exactly-once guard: the same business event can never post twice."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint(f"unit_code {_UNIT_CHECK}", name="unit_code_valid"),
        CheckConstraint("delta_qty <> 0", name="delta_nonzero"),
        CheckConstraint(f"movement_type {_in(MOVEMENT_TYPES)}", name="type_valid"),
        CheckConstraint(
            "(movement_type = 'REVERSAL') = (reverses_movement_id IS NOT NULL)",
            name="reversal_ref",
        ),
        Index(
            "uq_stock_reversed_once",
            "reverses_movement_id",
            unique=True,
            postgresql_where=text("reverses_movement_id IS NOT NULL"),
        ),
        Index("ix_stock_lot_ts", "lot_id", "created_at"),
        Index("ix_stock_src", "source_type", "source_id"),
        Index("ix_stock_company_ts", "company_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    lot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_lots.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(8), nullable=False)
    delta_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    qty_after: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    posting_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    reverses_movement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stock_movements.id"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = _created()


class StockReservation(Base):
    """Soft hold on saleable stock (does not change on-hand). ACTIVE -> RELEASED | CONSUMED."""

    __tablename__ = "stock_reservations"
    __table_args__ = (
        CheckConstraint(f"unit_code {_UNIT_CHECK}", name="unit_code_valid"),
        CheckConstraint("qty > 0", name="qty_pos"),
        CheckConstraint("status IN ('ACTIVE','RELEASED','CONSUMED')", name="status_valid"),
        Index("ix_reservations_lot", "lot_id", "unit_code", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    lot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_lots.id"), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ACTIVE")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    posting_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = _created()
    released_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StockCount(Base):
    """Physical count: SUBMITTED by warehouse staff, POSTED (variances -> ledger) or REJECTED
    by a manager. Lines snapshot the balance version they were counted against."""

    __tablename__ = "stock_counts"
    __table_args__ = (
        UniqueConstraint("company_id", "count_no"),
        CheckConstraint("status IN ('SUBMITTED','POSTED','REJECTED')", name="status_valid"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    count_no: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="SUBMITTED")
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    counted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()


class StockCountLine(Base):
    __tablename__ = "stock_count_lines"
    __table_args__ = (
        UniqueConstraint("count_id", "lot_id", "unit_code"),
        CheckConstraint("counted_qty >= 0", name="counted_nonneg"),
    )
    id: Mapped[uuid.UUID] = _pk()
    count_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stock_counts.id"), nullable=False, index=True
    )
    lot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_lots.id"), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(8), nullable=False)
    expected_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    expected_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    counted_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    movement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stock_movements.id"))


# ---------------------------------------------------------------- Module 04 sales orders

ORDER_STATUSES = (
    "DRAFT",
    "CONFIRMED",
    "PICKING",
    "PARTIALLY_FULFILLED",
    "FULFILLED",
    "CLOSED_WITH_SHORTAGE",
    "CANCELLED",
)
PICK_STATUSES = (
    "CREATED",
    "IN_PROGRESS",
    "SUBMITTED",
    "NEEDS_CORRECTION",
    "VERIFIED",
    "INVOICED",
    "ABORTED",
)
OPEN_PICK_STATUSES = ("CREATED", "IN_PROGRESS", "SUBMITTED", "NEEDS_CORRECTION")
PICK_OUTCOMES = ("PICKED", "SHORT", "NO")
SHORTAGE_REASONS = ("OUT_OF_STOCK", "DAMAGED", "NOT_FOUND", "OTHER")
AMENDMENT_KINDS = ("QTY_CANCELLED", "SHORTAGE_CANCELLED", "ORDER_CLOSED", "ORDER_CANCELLED")


class SalesOrder(Base):
    """Customer request. Never carries an invoice number; the original capture (source_text +
    source_snapshot) is immutable — later changes are amendments."""

    __tablename__ = "sales_orders"
    __table_args__ = (
        UniqueConstraint("company_id", "order_no"),
        CheckConstraint(f"status {_in(ORDER_STATUSES)}", name="status_valid"),
        CheckConstraint("source IN ('MANUAL','SCAN')", name="source_valid"),
        CheckConstraint("version >= 1", name="version_pos"),
        Index("ix_sales_orders_company_status", "company_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    order_no: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="DRAFT")
    source: Mapped[str] = mapped_column(String(8), nullable=False, server_default="MANUAL")
    source_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_snapshot: Mapped[Any] = mapped_column(JSONB, nullable=False)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    requested_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class SalesOrderLine(Base):
    """ordered/cancelled/dispatched per line in the line's own unit (never converted).
    remaining = ordered - cancelled - dispatched. Rate is snapshotted at confirmation."""

    __tablename__ = "sales_order_lines"
    __table_args__ = (
        UniqueConstraint("order_id", "line_no"),
        CheckConstraint(f"unit_code {_UNIT_CHECK}", name="unit_valid"),
        CheckConstraint("ordered_qty > 0", name="ordered_pos"),
        CheckConstraint("cancelled_qty >= 0 AND dispatched_qty >= 0", name="qty_nonneg"),
        CheckConstraint("cancelled_qty + dispatched_qty <= ordered_qty", name="within_ordered"),
        CheckConstraint("rate IS NULL OR rate >= 0", name="rate_nonneg"),
    )
    id: Mapped[uuid.UUID] = _pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_orders.id"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(8), nullable=False)
    ordered_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    cancelled_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, server_default="0"
    )
    dispatched_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, server_default="0"
    )
    source_line_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    price_snapshot: Mapped[Any | None] = mapped_column(JSONB)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = _created()


class SalesOrderAmendment(Base):
    """Append-only change history after confirmation (cancelled quantities, closures)."""

    __tablename__ = "sales_order_amendments"
    __table_args__ = (CheckConstraint(f"kind {_in(AMENDMENT_KINDS)}", name="kind_valid"),)
    id: Mapped[uuid.UUID] = _pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_orders.id"), nullable=False, index=True
    )
    line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales_order_lines.id"))
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    pick_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = _created()


class PickingAttempt(Base):
    """One trip to the shelves for an order's remaining quantities. At most one open attempt
    per order (partial unique index) so remaining quantities are never picked twice."""

    __tablename__ = "picking_attempts"
    __table_args__ = (
        UniqueConstraint("order_id", "attempt_no"),
        UniqueConstraint("company_id", "pick_code"),
        CheckConstraint(f"status {_in(PICK_STATUSES)}", name="status_valid"),
        Index(
            "uq_pick_one_open_per_order",
            "order_id",
            unique=True,
            postgresql_where=text(f"status {_in(OPEN_PICK_STATUSES)}"),
        ),
        Index("ix_pick_company_wh_status", "company_id", "warehouse_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_orders.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    pick_code: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="CREATED")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    picked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()


class PickingLine(Base):
    """requested = line remaining when the sheet was printed. picked may be 0 (`NO`): that is a
    shortage, never a cancellation."""

    __tablename__ = "picking_lines"
    __table_args__ = (
        UniqueConstraint("attempt_id", "order_line_id"),
        CheckConstraint("requested_qty > 0", name="requested_pos"),
        CheckConstraint(
            "picked_qty IS NULL OR (picked_qty >= 0 AND picked_qty <= requested_qty)",
            name="picked_range",
        ),
        CheckConstraint(f"outcome IS NULL OR outcome {_in(PICK_OUTCOMES)}", name="outcome_valid"),
        CheckConstraint(
            f"shortage_reason IS NULL OR shortage_reason {_in(SHORTAGE_REASONS)}",
            name="reason_valid",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome = 'PICKED' OR shortage_reason IS NOT NULL",
            name="shortage_needs_reason",
        ),
        CheckConstraint(
            "disposition IS NULL OR disposition IN ('BACKORDER','CANCEL')", name="disp_valid"
        ),
        CheckConstraint("weight_lb IS NULL OR weight_lb > 0", name="weight_pos"),
    )
    id: Mapped[uuid.UUID] = _pk()
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("picking_attempts.id"), nullable=False, index=True
    )
    order_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_order_lines.id"), nullable=False, index=True
    )
    requested_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    picked_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    outcome: Mapped[str | None] = mapped_column(String(8))
    shortage_reason: Mapped[str | None] = mapped_column(String(16))
    disposition: Mapped[str | None] = mapped_column(String(12))
    lot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_lots.id"))
    weight_lb: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


# ---------------------------------------------------------------- Module 05 invoicing

INVOICE_STATES = ("DRAFT", "ISSUED", "PARTIALLY_PAID", "PAID", "VOIDED", "CREDITED")
EMAIL_STATUSES = ("NO_EMAIL", "QUEUED", "SENT", "FAILED")


class InvoiceSequence(Base):
    """Gapless per-company, per-fiscal-year counter. Locked FOR UPDATE inside the issue
    transaction, so a rolled-back issue never consumes a number."""

    __tablename__ = "invoice_sequences"
    __table_args__ = (CheckConstraint("last_no >= 0", name="last_nonneg"),)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_no: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class Invoice(Base):
    """Final invoice for exactly one verified picking attempt. Everything a printed invoice
    shows lives in `snapshot` (+ lines); the issued PDF bytes are stored once and never change."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("company_id", "invoice_no"),
        UniqueConstraint("company_id", "fiscal_year", "seq_no"),
        UniqueConstraint("picking_attempt_id"),
        CheckConstraint(f"state {_in(INVOICE_STATES)}", name="state_valid"),
        CheckConstraint(f"email_status {_in(EMAIL_STATUSES)}", name="email_status_valid"),
        CheckConstraint("currency = 'CAD'", name="currency_cad"),
        CheckConstraint("subtotal >= 0 AND tax_amount >= 0", name="amounts_nonneg"),
        CheckConstraint("total = subtotal + tax_amount", name="total_sum"),
        CheckConstraint("due_at >= issued_at", name="due_after_issue"),
        CheckConstraint("seq_no > 0", name="seq_pos"),
        CheckConstraint("paid_amount >= 0 AND paid_amount <= total", name="paid_within_total"),
        Index("ix_invoices_company_issued", "company_id", "issued_at"),
        Index("ix_invoices_order", "sales_order_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    invoice_no: Mapped[str] = mapped_column(String(32), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    seq_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sales_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_orders.id"), nullable=False)
    picking_attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("picking_attempts.id"), nullable=False
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ISSUED")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="CAD")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    # Module 10: sum of verified payment allocations (maintained under row locks; the
    # allocation guard trigger re-checks it can never exceed `total`).
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, server_default="0")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    snapshot: Mapped[Any] = mapped_column(JSONB, nullable=False)
    pdf_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str] = mapped_column(String(32), nullable=False)
    email_to: Mapped[str | None] = mapped_column(Text)
    email_status: Mapped[str] = mapped_column(String(12), nullable=False)
    email_sends: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_error: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()


class InvoiceLine(Base):
    """Billed row. Weighted products produce one row per case-weight group of the same
    product (INV-006). Rates and descriptions are copies — never re-read from masters."""

    __tablename__ = "invoice_lines"
    __table_args__ = (
        UniqueConstraint("invoice_id", "line_no"),
        CheckConstraint(f"unit_code {_UNIT_CHECK}", name="unit_valid"),
        CheckConstraint("billed_qty > 0", name="billed_pos"),
        CheckConstraint("unit_rate >= 0", name="rate_nonneg"),
        CheckConstraint("cases IS NULL OR cases > 0", name="cases_pos"),
        CheckConstraint("case_weight_lb IS NULL OR case_weight_lb > 0", name="weight_pos"),
        CheckConstraint(
            "weight_source IS NULL OR weight_source IN ('ENTERED')", name="weight_source_valid"
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    order_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_order_lines.id"), nullable=False
    )
    pick_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("picking_lines.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    unit_code: Mapped[str] = mapped_column(String(8), nullable=False)
    cases: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    case_weight_lb: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    weight_source: Mapped[str | None] = mapped_column(String(16))
    billed_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_rate: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    line_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_code: Mapped[str] = mapped_column(String(24), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    pricing_snapshot: Mapped[Any] = mapped_column(JSONB, nullable=False)


class InvoiceDocument(Base):
    """The issued PDF bytes (same bytes on every reprint). Drive archival is a later module."""

    __tablename__ = "invoice_documents"
    __table_args__ = (
        UniqueConstraint("invoice_id", "kind"),
        CheckConstraint("kind IN ('ISSUED')", name="kind_valid"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ISSUED")
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created()


class EmailMessage(Base):
    """Outgoing mail log. `dedupe_key` is unique, so an outbox retry can never send twice.
    In dev/test this table *is* the mailbox (no provider configured)."""

    __tablename__ = "email_messages"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id"), index=True)
    to_addr: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachment_sha256: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(16), nullable=False, server_default="DEV_SINK")
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------------------------------------
# Module 06 — scanner bridge + document intake
# ---------------------------------------------------------------------------------------------

SCAN_JOB_TYPES = (
    "SALES_ORDER",
    "PICK_SHEET",
    "VENDOR_DOCUMENT",
    "PURCHASE_ORDER",
    "DELIVERY_NOTE",
    "PACKING_LIST",
    "RECEIPT",
    "OTHER",
)
SCAN_CHANNELS = ("BRIDGE", "MANUAL")
SCAN_JOB_STATUSES = ("REQUESTED", "SCANNING", "UPLOADED", "FAILED", "CANCELLED", "EXPIRED")
SCAN_OPEN_STATUSES = ("REQUESTED", "SCANNING")
SCAN_DEVICE_STATUSES = ("PENDING", "ACTIVE", "REVOKED")
SCANNER_STATES = ("READY", "NOT_FOUND", "DRIVER_MISSING", "BUSY", "ERROR", "UNKNOWN")
DOCUMENT_MIME_TYPES = ("application/pdf", "image/png", "image/jpeg", "image/tiff")


class SourceDocument(Base):
    """Original scanned/uploaded bytes. One row per (company, sha256): re-uploading the same file
    never creates a second source. Append-only (DB trigger)."""

    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("company_id", "sha256", name="uq_source_document_hash"),
        CheckConstraint(f"mime_type {_in(DOCUMENT_MIME_TYPES)}", name="mime_valid"),
        CheckConstraint("byte_size > 0", name="size_positive"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    uploaded_via: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _created()


class ScanDevice(Base):
    """A paired office scanner bridge. The bridge holds a random secret; we keep only its hash."""

    __tablename__ = "scan_devices"
    __table_args__ = (
        CheckConstraint(f"status {_in(SCAN_DEVICE_STATUSES)}", name="status_valid"),
        CheckConstraint(f"scanner_state {_in(SCANNER_STATES)}", name="scanner_state_valid"),
        CheckConstraint(
            "status <> 'ACTIVE' OR (secret_hash IS NOT NULL AND approved_by IS NOT NULL)",
            name="active_requires_pairing",
        ),
        Index(
            "uq_scan_device_pairing_code",
            "pairing_code_hash",
            unique=True,
            postgresql_where=text("pairing_code_hash IS NOT NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    pairing_code_hash: Mapped[str | None] = mapped_column(String(64))
    pairing_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    secret_hash: Mapped[str | None] = mapped_column(String(64))
    fingerprint: Mapped[str | None] = mapped_column(String(16))
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scanner_state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="UNKNOWN")
    scanner_detail: Mapped[str | None] = mapped_column(Text)
    bridge_version: Mapped[str | None] = mapped_column(String(32))
    # Scanner discovery: the web app asks; the bridge answers on its next heartbeat.
    discover_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_scanners: Mapped[Any | None] = mapped_column(JSONB)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()


class ScanJob(Base):
    """One request to bring a paper document into the system (bridge scan or manual upload).
    It never creates orders, invoices or stock by itself (OCR review is Module 07)."""

    __tablename__ = "scan_jobs"
    __table_args__ = (
        UniqueConstraint("company_id", "job_no"),
        CheckConstraint(f"job_type {_in(SCAN_JOB_TYPES)}", name="job_type_valid"),
        CheckConstraint(f"channel {_in(SCAN_CHANNELS)}", name="channel_valid"),
        CheckConstraint(f"status {_in(SCAN_JOB_STATUSES)}", name="status_valid"),
        CheckConstraint("channel <> 'BRIDGE' OR device_id IS NOT NULL", name="bridge_has_device"),
        CheckConstraint("status <> 'UPLOADED' OR document_id IS NOT NULL", name="uploaded_has_doc"),
        CheckConstraint("status <> 'FAILED' OR error_code IS NOT NULL", name="failed_has_error"),
        # Single flight: a device scans one sheet at a time.
        Index(
            "uq_scan_one_active_per_device",
            "device_id",
            unique=True,
            postgresql_where=text("status = 'SCANNING'"),
        ),
        Index("ix_scan_jobs_company_status", "company_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    job_no: Mapped[str] = mapped_column(String(32), nullable=False)
    job_type: Mapped[str] = mapped_column(String(24), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scan_devices.id"))
    note: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    capability_nonce_hash: Mapped[str | None] = mapped_column(String(64))
    capability_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_detail: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_documents.id"), index=True
    )
    duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()


# ------------------------------------------------------------------ Module 07: OCR extraction

OCR_EXTRACTION_STATUSES = ("QUEUED", "NEEDS_REVIEW", "FAILED", "ACCEPTED", "REJECTED", "SUPERSEDED")
OCR_FIELD_KINDS = ("LINE", "EXTRA", "MANUAL")
OCR_FIELD_STATUSES = ("OK", "LOW_CONFIDENCE", "UNREADABLE", "UNMAPPED")
OCR_RESOLUTIONS = ("NONE", "CONFIRMED", "CORRECTED", "REMOVED")


class OcrExtraction(Base):
    """One OCR run over a scan's source document. Re-running creates a new version; the old one
    becomes SUPERSEDED. Accepting creates at most a DRAFT order - never an invoice or stock."""

    __tablename__ = "ocr_extractions"
    __table_args__ = (
        UniqueConstraint("scan_job_id", "run_no"),
        CheckConstraint(f"status {_in(OCR_EXTRACTION_STATUSES)}", name="status_valid"),
        CheckConstraint("status <> 'FAILED' OR error_code IS NOT NULL", name="failed_has_error"),
        Index("ix_ocr_extractions_company_status", "company_id", "status"),
        # One live (not superseded) run per scan.
        Index(
            "uq_ocr_one_live_per_job",
            "scan_job_id",
            unique=True,
            postgresql_where=text("status <> 'SUPERSEDED'"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    scan_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scan_jobs.id"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_documents.id"), nullable=False
    )
    run_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="QUEUED")
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    template: Mapped[str | None] = mapped_column(String(32))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"))
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    text_blocks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    page_count: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_detail: Mapped[str | None] = mapped_column(Text)
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales_orders.id"))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()


class OcrField(Base):
    """One extracted order line: what OCR read (immutable) and what the reviewer decided."""

    __tablename__ = "ocr_fields"
    __table_args__ = (
        UniqueConstraint("extraction_id", "seq"),
        CheckConstraint(f"kind {_in(OCR_FIELD_KINDS)}", name="kind_valid"),
        CheckConstraint(f"status {_in(OCR_FIELD_STATUSES)}", name="status_valid"),
        CheckConstraint(f"resolution {_in(OCR_RESOLUTIONS)}", name="resolution_valid"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"
        ),
        CheckConstraint("final_qty IS NULL OR final_qty > 0", name="final_qty_pos"),
    )
    id: Mapped[uuid.UUID] = _pk()
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ocr_extractions.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    # --- as read (never changed after insert; DB trigger)
    label: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    read_product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"))
    read_unit_code: Mapped[str | None] = mapped_column(String(8))
    read_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # --- reviewer decision
    resolution: Mapped[str] = mapped_column(String(10), nullable=False, server_default="NONE")
    final_product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"))
    final_unit_code: Mapped[str | None] = mapped_column(String(8))
    final_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OcrCorrection(Base):
    """Append-only history of every reviewer decision on a field (DB trigger)."""

    __tablename__ = "ocr_corrections"
    id: Mapped[uuid.UUID] = _pk()
    field_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ocr_fields.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    before: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    created_at: Mapped[datetime] = _created()


# ------------------------------------------------------------------ Module 08: receiving

SHIPMENT_STATUSES = ("OPEN", "COUNTED", "POSTED", "CANCELLED")
SHIPMENT_DOC_KINDS = ("AWB", "INVOICE", "PACKING_LIST", "COMBINED", "OTHER")
LANDED_COST_KINDS = ("FREIGHT", "DUTY", "BROKERAGE", "INSURANCE", "OTHER")
_CURRENCY = "~ '^[A-Z]{3}$'"


class Vendor(Base):
    """Supplier master data. Vendor-specific product names are ProductAlias rows whose
    source_label is `vendor:<code>`."""

    __tablename__ = "vendors"
    __table_args__ = (
        UniqueConstraint("company_id", "code"),
        CheckConstraint(f"currency {_CURRENCY}", name="currency_iso"),
        CheckConstraint("country_code IS NULL OR country_code ~ '^[A-Z]{2}$'", name="country_iso"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2))
    email: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class Shipment(Base):
    """One vendor delivery: its documents, what they declare, and what was physically counted.
    The count becomes stock only when the GRN is posted (POSTED is final; DB trigger)."""

    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("company_id", "shipment_no"),
        UniqueConstraint("company_id", "grn_no"),
        CheckConstraint(f"status {_in(SHIPMENT_STATUSES)}", name="status_valid"),
        CheckConstraint(f"currency {_CURRENCY}", name="currency_iso"),
        CheckConstraint(
            "(status = 'POSTED') = (grn_no IS NOT NULL AND posted_at IS NOT NULL)",
            name="posted_has_grn",
        ),
        CheckConstraint("status <> 'CANCELLED' OR cancel_reason IS NOT NULL", name="cancel_reason"),
        Index("ix_shipments_company_status", "company_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    shipment_no: Mapped[str] = mapped_column(String(32), nullable=False)
    vendor_invoice_no: Mapped[str | None] = mapped_column(String(64))
    awb_no: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    expected_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="OPEN")
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    counted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grn_no: Mapped[str | None] = mapped_column(String(32))
    posted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discrepancy_note: Mapped[str | None] = mapped_column(Text)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class ShipmentDocument(Base):
    """A vendor document attached to a shipment. The bytes live unaltered in source_documents;
    this link is append-only (DB trigger)."""

    __tablename__ = "shipment_documents"
    __table_args__ = (
        UniqueConstraint("shipment_id", "document_id"),
        CheckConstraint(f"kind {_in(SHIPMENT_DOC_KINDS)}", name="kind_valid"),
    )
    id: Mapped[uuid.UUID] = _pk()
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_documents.id"), nullable=False
    )
    scan_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scan_jobs.id"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    doc_ref: Mapped[str | None] = mapped_column(String(64))
    added_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    created_at: Mapped[datetime] = _created()


class DeclaredLine(Base):
    """What a vendor document says was shipped. Never stock by itself. Rows are not deleted;
    a mistaken entry is withdrawn (removed_at + reason)."""

    __tablename__ = "shipment_declared_lines"
    __table_args__ = (
        CheckConstraint(f"unit_code {_UNIT_CHECK}", name="unit_code_valid"),
        CheckConstraint("declared_qty > 0", name="qty_positive"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="cost_nonneg"),
        CheckConstraint("removed_at IS NULL OR removed_reason IS NOT NULL", name="removed_reason"),
    )
    id: Mapped[uuid.UUID] = _pk()
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id"), nullable=False, index=True
    )
    shipment_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shipment_documents.id")
    )
    raw_label: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(8), nullable=False)
    declared_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    created_at: Mapped[datetime] = _created()
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    removed_reason: Mapped[str | None] = mapped_column(Text)


class GrnLine(Base):
    """Physical count of one product/unit/batch: accepted (saleable), damaged, rejected.
    Frozen once posted (lot_id set; DB trigger)."""

    __tablename__ = "grn_lines"
    __table_args__ = (
        CheckConstraint(f"unit_code {_UNIT_CHECK}", name="unit_code_valid"),
        CheckConstraint(
            "accepted_qty >= 0 AND damaged_qty >= 0 AND rejected_qty >= 0", name="qty_nonneg"
        ),
        CheckConstraint("accepted_qty + damaged_qty + rejected_qty > 0", name="counted_something"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="cost_nonneg"),
    )
    id: Mapped[uuid.UUID] = _pk()
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(8), nullable=False)
    accepted_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    damaged_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    rejected_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    batch_ref: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    lot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_lots.id"))
    counted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class LandedCost(Base):
    """Freight/duty/brokerage captured against a shipment. Recorded, never allocated to lots
    automatically (allocation basis is a business decision). Withdrawn, not deleted."""

    __tablename__ = "shipment_costs"
    __table_args__ = (
        CheckConstraint(f"kind {_in(LANDED_COST_KINDS)}", name="kind_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(f"currency {_CURRENCY}", name="currency_iso"),
        CheckConstraint("removed_at IS NULL OR removed_reason IS NOT NULL", name="removed_reason"),
    )
    id: Mapped[uuid.UUID] = _pk()
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    shipment_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shipment_documents.id")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    created_at: Mapped[datetime] = _created()
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    removed_reason: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------------------------ Module 09 documents

DOC_FILE_KINDS = ("INVOICE_PDF", "POD", "VENDOR_DOC", "SCAN")
DOC_SYNC_STATUSES = ("PENDING", "SYNCED", "FAILED", "LOCAL_ONLY")


class DocumentFile(Base):
    """One filed copy of a document in the company archive (customer/vendor/year/month folders).
    The bytes stay in invoice_documents/source_documents; this row tracks where the document is
    filed and whether Drive has it. Identity and the Drive file id never change once set."""

    __tablename__ = "document_files"
    __table_args__ = (
        CheckConstraint(f"kind {_in(DOC_FILE_KINDS)}", name="kind_valid"),
        CheckConstraint(f"sync_status {_in(DOC_SYNC_STATUSES)}", name="sync_status_valid"),
        CheckConstraint(
            "(invoice_document_id IS NULL) <> (source_document_id IS NULL)", name="one_source"
        ),
        CheckConstraint(
            "sync_status <> 'SYNCED' OR drive_file_id IS NOT NULL", name="synced_has_id"
        ),
        CheckConstraint("byte_size > 0", name="size_positive"),
        UniqueConstraint("company_id", "folder", "filename", name="uq_document_file_path"),
        UniqueConstraint("invoice_document_id", name="uq_document_file_invoice_doc"),
        UniqueConstraint(
            "company_id", "source_document_id", "folder", name="uq_document_file_source_folder"
        ),
        Index("ix_document_files_company_status", "company_id", "sync_status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    invoice_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoice_documents.id")
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_documents.id"))
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id"), index=True)
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shipments.id"), index=True)
    scan_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scan_jobs.id"))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendors.id"))
    folder: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sync_status: Mapped[str] = mapped_column(String(16), nullable=False)
    drive_file_id: Mapped[str | None] = mapped_column(Text)
    sync_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    filed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = _created()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


# ---------------------------------------------------------------------------------------------
# Module 10 — payments ledger (manual, no bank integration)
# ---------------------------------------------------------------------------------------------

PAYMENT_METHODS = ("INTERAC", "EFT", "CHEQUE", "CASH")
PAYMENT_STATUSES = ("REPORTED", "VERIFIED", "APPLIED", "REJECTED")
REMINDER_STATUSES = ("QUEUED", "SENT", "SKIPPED")


class Payment(Base):
    """A payment the customer says they sent (REPORTED) until an authorised employee checks the
    bank and records the bank reference (VERIFIED). Only verified money is allocated to
    invoices; APPLIED means fully allocated. `allocated_amount` is the cached allocation sum."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("company_id", "payment_no"),
        CheckConstraint(f"method {_in(PAYMENT_METHODS)}", name="method_valid"),
        CheckConstraint(f"status {_in(PAYMENT_STATUSES)}", name="status_valid"),
        CheckConstraint("currency = 'CAD'", name="currency_cad"),
        CheckConstraint("amount > 0", name="amount_pos"),
        CheckConstraint(
            "allocated_amount >= 0 AND allocated_amount <= amount", name="allocated_within"
        ),
        CheckConstraint(
            "status NOT IN ('VERIFIED','APPLIED') OR "
            "(verification_ref IS NOT NULL AND verified_by IS NOT NULL"
            " AND verified_at IS NOT NULL)",
            name="verified_has_evidence",
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR (rejected_reason IS NOT NULL AND rejected_at IS NOT NULL)",
            name="rejected_has_reason",
        ),
        CheckConstraint(
            "(status = 'APPLIED') = (allocated_amount = amount AND status <> 'REJECTED' "
            "AND status <> 'REPORTED')",
            name="applied_iff_full",
        ),
        # The same bank transaction can only confirm one payment.
        Index(
            "uq_payments_verification_ref",
            "company_id",
            "method",
            text("lower(verification_ref)"),
            unique=True,
            postgresql_where=text("status IN ('VERIFIED','APPLIED')"),
        ),
        Index("ix_payments_company_status", "company_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    payment_no: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="REPORTED")
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="CAD")
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    payer_ref: Mapped[str | None] = mapped_column(Text)  # what the customer quoted
    reported_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id"), index=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    verification_ref: Mapped[str | None] = mapped_column(Text)  # bank / cheque / receipt ref
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_note: Mapped[str | None] = mapped_column(Text)
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    allocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = _created()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class PaymentAllocation(Base):
    """Append-only: verified money applied to one invoice. A mistake is undone by a reversal
    row (negative amount, `reverses_id` set), never by editing or deleting."""

    __tablename__ = "payment_allocations"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="amount_nonzero"),
        CheckConstraint(
            "(reverses_id IS NULL AND amount > 0) OR (reverses_id IS NOT NULL AND amount < 0 "
            "AND reason IS NOT NULL)",
            name="reversal_shape",
        ),
        UniqueConstraint("reverses_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payments.id"), nullable=False, index=True
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payment_allocations.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    created_at: Mapped[datetime] = _created()


class PaymentReminder(Base):
    """One row per (invoice, offset day) so a reminder is queued at most once even though the
    scheduler runs every hour; the email itself is deduplicated by its outbox key."""

    __tablename__ = "payment_reminders"
    __table_args__ = (
        UniqueConstraint("invoice_id", "offset_days"),
        CheckConstraint(f"status {_in(REMINDER_STATUSES)}", name="status_valid"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id"), nullable=False, index=True
    )
    offset_days: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default="QUEUED")
    balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    created_at: Mapped[datetime] = _created()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- suppliers: payments we make
SUPPLIER_PAY_METHODS = ("WIRE", "EFT", "CHEQUE", "CASH", "OTHER")


class SupplierPayment(Base):
    """Money we paid a supplier, in the currency paid. Never edited or deleted (DB trigger):
    a mistake is voided with a reason and stays on the ledger, struck through."""

    __tablename__ = "supplier_payments"
    __table_args__ = (
        UniqueConstraint("company_id", "payment_no"),
        CheckConstraint(f"method {_in(SUPPLIER_PAY_METHODS)}", name="method_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(f"currency {_CURRENCY}", name="currency_iso"),
        CheckConstraint(
            "(voided_at IS NULL) = (void_reason IS NULL)"
            " AND (voided_at IS NULL) = (voided_by IS NULL)",
            name="void_complete",
        ),
        Index("ix_supplier_payments_vendor", "company_id", "vendor_id", "paid_on"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    payment_no: Mapped[str] = mapped_column(String(32), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shipments.id"))
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    created_at: Mapped[datetime] = _created()
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    void_reason: Mapped[str | None] = mapped_column(Text)
