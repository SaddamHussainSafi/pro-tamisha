"""Order Connect tables (migration 0017). Customers, products, employees and sales orders are the
existing tables; nothing here duplicates them."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..models import _created, _in, _pk

VERSION_STATUSES = ("DRAFT", "PUBLISHED", "RETIRED")
FIELD_KINDS = ("TEXT", "TEXTAREA", "EMAIL", "PHONE", "DATE", "SELECT", "CHECKBOX", "PRODUCTS")
PRODUCT_SCOPES = ("ALL", "CATEGORIES", "PRODUCTS")
SUBMISSION_STATUSES = (
    "NEW",
    "REVIEWING",
    "APPROVED",
    "CONVERTED",
    "REJECTED",
    "DUPLICATE",
    "SPAM",
)
EVENT_KINDS = ("RECEIVED", "STATUS", "NOTE", "CONVERTED", "ERASED")
LOGO_TYPES = ("image/png",)


class OrderConnectSettings(Base):
    """One row per company. Absent row = defaults (see order_connect.forms.DEFAULT_SETTINGS)."""

    __tablename__ = "order_connect_settings"
    __table_args__ = (
        CheckConstraint("min_fill_seconds BETWEEN 0 AND 120", name="min_fill_range"),
        CheckConstraint("ip_limit_per_hour BETWEEN 1 AND 1000", name="ip_limit_range"),
        CheckConstraint("form_limit_per_hour BETWEEN 1 AND 10000", name="form_limit_range"),
        CheckConstraint(
            "retention_days IS NULL OR retention_days BETWEEN 30 AND 3650", name="retention_range"
        ),
        CheckConstraint("NOT consent_required OR char_length(consent_text) > 0", name="consent"),
        CheckConstraint("version >= 1", name="version_pos"),
    )
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    notify_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    ack_email: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    min_fill_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    ip_limit_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, server_default="20")
    form_limit_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, server_default="300")
    retention_days: Mapped[int | None] = mapped_column(Integer)
    consent_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    consent_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    updated_at: Mapped[datetime] = _created()


class OrderForm(Base):
    __tablename__ = "order_forms"
    __table_args__ = (
        UniqueConstraint("public_id"),
        CheckConstraint("public_id ~ '^[A-Za-z0-9_-]{22,32}$'", name="public_id_format"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="name_len"),
        CheckConstraint("version >= 1", name="version_pos"),
        Index("ix_order_forms_company", "company_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    public_id: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    accepting: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    standalone_link: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    default_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"))
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_form_versions.id", use_alter=True, name="fk_order_forms_published")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class OrderFormLogo(Base):
    """An approved logo, re-encoded server-side as PNG (metadata and any trailing payload are
    dropped). Served only with its own fixed Content-Type."""

    __tablename__ = "order_form_logos"
    __table_args__ = (
        UniqueConstraint("company_id", "sha256"),
        CheckConstraint(f"mime {_in(LOGO_TYPES)}", name="mime_valid"),
        CheckConstraint("byte_size BETWEEN 1 AND 524288", name="size_range"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = _created()


class OrderFormVersion(Base):
    """Form content. One DRAFT and at most one PUBLISHED per form; a PUBLISHED version never
    changes (DB trigger) except to become RETIRED when a newer one is published."""

    __tablename__ = "order_form_versions"
    __table_args__ = (
        UniqueConstraint("form_id", "version_no"),
        CheckConstraint(f"status {_in(VERSION_STATUSES)}", name="status_valid"),
        CheckConstraint(f"product_scope {_in(PRODUCT_SCOPES)}", name="scope_valid"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 120", name="title_len"),
        CheckConstraint("char_length(success_message) BETWEEN 1 AND 500", name="success_len"),
        CheckConstraint("revision >= 1", name="revision_pos"),
        CheckConstraint("(status = 'DRAFT') = (published_at IS NULL)", name="published_stamp"),
        Index(
            "uq_order_form_one_draft",
            "form_id",
            unique=True,
            postgresql_where=text("status = 'DRAFT'"),
        ),
        Index(
            "uq_order_form_one_published",
            "form_id",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    form_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("order_forms.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="DRAFT")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    intro: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    success_message: Mapped[str] = mapped_column(Text, nullable=False)
    theme: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    logo_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("order_form_logos.id"))
    show_prices: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    product_scope: Mapped[str] = mapped_column(String(12), nullable=False, server_default="ALL")
    scope_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderFormField(Base):
    __tablename__ = "order_form_fields"
    __table_args__ = (
        UniqueConstraint("version_id", "key"),
        UniqueConstraint("version_id", "position"),
        CheckConstraint(f"kind {_in(FIELD_KINDS)}", name="kind_valid"),
        CheckConstraint("key ~ '^[a-z][a-z0-9_]{0,39}$'", name="key_format"),
        CheckConstraint("char_length(label) BETWEEN 1 AND 120", name="label_len"),
        CheckConstraint("position >= 0", name="position_nonneg"),
    )
    id: Mapped[uuid.UUID] = _pk()
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_form_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    help_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    options: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class OrderFormAllowedDomain(Base):
    """A website allowed to frame a form (form_id NULL = every form of the company)."""

    __tablename__ = "order_form_allowed_domains"
    __table_args__ = (
        UniqueConstraint("company_id", "form_id", "host", postgresql_nulls_not_distinct=True),
        CheckConstraint(
            r"host ~ '^(\*\.)?[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
            r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$'",
            name="host_format",
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    form_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("order_forms.id"), index=True)
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = _created()


class OrderFormSubmission(Base):
    """What a customer sent. `raw` keeps every submitted value exactly as received (after
    sanitising); it and the items never change (DB trigger), except that a privacy erasure
    blanks the personal fields once. A submission converts at most once: `sales_order_id` is
    unique and set exactly when the status is CONVERTED."""

    __tablename__ = "order_form_submissions"
    __table_args__ = (
        UniqueConstraint("company_id", "reference"),
        UniqueConstraint("form_id", "client_token"),
        UniqueConstraint("sales_order_id"),
        CheckConstraint(f"status {_in(SUBMISSION_STATUSES)}", name="status_valid"),
        CheckConstraint(
            "(status = 'CONVERTED') = (sales_order_id IS NOT NULL)", name="converted_has_order"
        ),
        CheckConstraint("reference ~ '^OC-[0-9A-Z]{6,10}$'", name="reference_format"),
        CheckConstraint("version >= 1", name="version_pos"),
        Index("ix_oc_submissions_company_status", "company_id", "status", "created_at"),
        Index("ix_oc_submissions_company_created", "company_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    form_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("order_forms.id"), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_form_versions.id"), nullable=False
    )
    reference: Mapped[str] = mapped_column(String(16), nullable=False)
    client_token: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="NEW")
    customer_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    business_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    email: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    phone: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    delivery_address: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    requested_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    custom: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_host: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    user_agent: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    spam_reasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    possible_duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_form_submissions.id")
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"))
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales_orders.id"))
    converted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class OrderFormSubmissionItem(Base):
    """One requested product, with the catalog name/SKU as they were when submitted."""

    __tablename__ = "order_form_submission_items"
    __table_args__ = (
        UniqueConstraint("submission_id", "line_no"),
        UniqueConstraint("submission_id", "product_id", "unit_code"),
        CheckConstraint("unit_code IN ('BOX','PIECE','LB','KG')", name="unit_valid"),
        CheckConstraint("qty > 0", name="qty_pos"),
    )
    id: Mapped[uuid.UUID] = _pk()
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_form_submissions.id"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    sku: Mapped[str] = mapped_column(String(3), nullable=False)
    product_name: Mapped[str] = mapped_column(Text, nullable=False)


class OrderFormSubmissionEvent(Base):
    """Append-only timeline (DB trigger rejects UPDATE/DELETE)."""

    __tablename__ = "order_form_submission_events"
    __table_args__ = (
        CheckConstraint(f"kind {_in(EVENT_KINDS)}", name="kind_valid"),
        Index("ix_oc_events_submission", "submission_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_form_submissions.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(12))
    to_status: Mapped[str | None] = mapped_column(String(12))
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = _created()
