"""Gmail Inbox tables (migration 0024)."""

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..models import _created, _pk

ACCOUNT_STATUSES = ("CONNECTED", "ERROR", "REAUTH_REQUIRED", "DISCONNECTED")
# An account the worker still syncs (ERROR = recent syncs failed; it keeps trying).
LIVE_STATUSES = ("CONNECTED", "ERROR")


def _in(values: Iterable[str]) -> str:
    return "IN (" + ",".join(f"'{v}'" for v in values) + ")"


class GmailAccount(Base):
    """The connected mailbox. One per company at a time (a disconnected row stays as history).
    `refresh_token_enc` / `access_token_enc` are Fernet ciphertext and are wiped on disconnect."""

    __tablename__ = "gmail_accounts"
    __table_args__ = (
        CheckConstraint(f"status {_in(ACCOUNT_STATUSES)}", name="status_valid"),
        CheckConstraint("provider = 'gmail'", name="provider_valid"),
        CheckConstraint(
            "status = 'DISCONNECTED' OR refresh_token_enc IS NOT NULL", name="live_has_token"
        ),
        Index(
            "uq_gmail_one_live_account",
            "company_id",
            unique=True,
            postgresql_where=text("status <> 'DISCONNECTED'"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False, server_default="gmail")
    email_address: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="CONNECTED")
    scopes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Gmail's mailbox history cursor: only ever moves forward.
    history_id: Mapped[int | None] = mapped_column(BigInteger)
    watch_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watch_renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Historical import: resumable page by page.
    backfill_page_token: Mapped[str | None] = mapped_column(Text)
    backfill_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backfill_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error_code: Mapped[str | None] = mapped_column(String(48))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connected_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    connected_at: Mapped[datetime] = _created()
    disconnected_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created()


class GmailMessage(Base):
    """One email, stored once per (account, Gmail message id). Mail that disappears from Gmail
    is marked (`removed_at`), never deleted here."""

    __tablename__ = "gmail_messages"
    __table_args__ = (
        UniqueConstraint("account_id", "gmail_message_id", name="uq_gmail_message"),
        Index("ix_gmail_messages_company_received", "company_id", "received_at", "id"),
        Index("ix_gmail_messages_thread", "account_id", "thread_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gmail_accounts.id"), nullable=False)
    gmail_message_id: Mapped[str] = mapped_column(String(32), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    from_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    from_email: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    to_addr: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    subject: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    snippet: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # Plain text, and HTML already reduced to an allow-list (no scripts, styles, images, forms).
    body_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    body_html: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    body_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    labels: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    attachment_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    pdf_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Read state inside Pro Trading (the mailbox itself is never changed: read-only scope).
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    gmail_history_id: Mapped[int | None] = mapped_column(BigInteger)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = _created()
    created_at: Mapped[datetime] = _created()


class GmailAttachment(Base):
    """A named file on an email. Only its description is stored at sync time; the bytes are
    fetched from Gmail when someone previews, downloads or processes it. Gmail's attachment ids
    change between fetches, so the stable key is the MIME part id inside the message."""

    __tablename__ = "gmail_attachments"
    __table_args__ = (
        UniqueConstraint("message_id", "part_id", name="uq_gmail_attachment_part"),
        CheckConstraint("byte_size >= 0", name="size_valid"),
        CheckConstraint("sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        Index("ix_gmail_attachments_sha", "company_id", "sha256"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gmail_messages.id"), nullable=False, index=True
    )
    part_id: Mapped[str] = mapped_column(String(32), nullable=False)
    gmail_attachment_id: Mapped[str | None] = mapped_column(Text)  # as last seen; not stable
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    inline: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # From the declared type / file name: PDF, PNG, JPEG or TIFF. Checked again on the bytes.
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Known once the bytes were fetched.
    sha256: Mapped[str | None] = mapped_column(String(64))
    detected_mime: Mapped[str | None] = mapped_column(String(32))
    page_count: Mapped[int | None] = mapped_column(Integer)
    rejected_code: Mapped[str | None] = mapped_column(String(32))
    # Private storage: the existing source_documents row, created when it is first processed.
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_documents.id"))
    created_at: Mapped[datetime] = _created()


class GmailProcessingJob(Base):
    """One "Use for OCR" on an attachment. The reading, its lines, the review flags and the
    resulting draft live in the existing scan_jobs / ocr_extractions / ocr_fields rows it points
    to. Processing the same attachment again adds a new attempt; nothing is overwritten."""

    __tablename__ = "gmail_processing_jobs"
    __table_args__ = (
        UniqueConstraint("attachment_id", "attempt_no", name="uq_gmail_job_attempt"),
        CheckConstraint("attempt_no >= 1", name="attempt_positive"),
        Index("ix_gmail_jobs_scan", "scan_job_id"),
        Index("ix_gmail_jobs_message", "message_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gmail_messages.id"), nullable=False)
    attachment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gmail_attachments.id"), nullable=False, index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    document_category: Mapped[str] = mapped_column(String(24), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    scan_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scan_jobs.id"), nullable=False)
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ocr_extractions.id"))
    reprocess: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # True when the person was told a draft already exists for this document and went ahead.
    duplicate_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    created_at: Mapped[datetime] = _created()
