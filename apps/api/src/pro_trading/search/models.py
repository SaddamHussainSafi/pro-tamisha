"""Search tables (migration 0029). A copy for finding things; the ERP tables stay the truth."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Computed,
    Date,
    DateTime,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class SearchDocument(Base):
    __tablename__ = "search_documents"
    doc_type: Mapped[str] = mapped_column(String(24), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    title_norm: Mapped[str] = mapped_column(Text, nullable=False)
    identifiers: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    ident_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    names: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    ocr_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('simple', title), 'A') || "
            "setweight(to_tsvector('simple', names), 'B') || "
            "setweight(to_tsvector('simple', body), 'C')",
            persisted=True,
        ),
    )
    ocr_tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', left(ocr_text, 100000))", persisted=True)
    )
    status: Mapped[str | None] = mapped_column(String(32))
    doc_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    product_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SearchQueueItem(Base):
    __tablename__ = "search_index_queue"
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SearchRecent(Base):
    """Records an employee opened from search (private to that employee; clearable)."""

    __tablename__ = "search_recent"
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(24), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
