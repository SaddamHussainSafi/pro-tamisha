"""Email (Lark Mail) tables, migration 0030. Separate from the Gmail Inbox tables (gmail_*):
nothing here reads or writes them.

Nothing in this module ever deletes an email. Mail that disappears from the server is marked
(`server_removed_at`); a mailbox whose UIDVALIDITY changed keeps its rows and re-keys them.
"""

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
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..models import _created, _pk

# NOT_CONFIGURED: no password saved yet. CONNECTED: the last login worked. ERROR: the last
# sync failed for a network/server reason (retried with backoff). AUTH_FAILED: Lark refused the
# password (not retried until a Super Admin saves a new one or presses Reconnect). PAUSED: a
# Super Admin paused sync (mail already imported stays readable).
ACCOUNT_STATUSES = ("NOT_CONFIGURED", "CONNECTED", "ERROR", "AUTH_FAILED", "PAUSED")
KINDS = ("PERSONAL", "SHARED")
FOLDER_ROLES = ("INBOX", "SENT", "DRAFTS", "ARCHIVE", "JUNK", "TRASH", "ALL", "FLAGGED", "OTHER")
OUTGOING_STATUSES = ("QUEUED", "SENDING", "SENT", "FAILED", "UNCERTAIN")
LINK_TYPES = ("customer", "vendor", "sales_order", "invoice", "shipment", "payment")


def _in(values: Iterable[str]) -> str:
    return "IN (" + ",".join(f"'{v}'" for v in values) + ")"


class MailAccount(Base):
    """One mailbox = one canonical server connection (info@ is ONE row shared by two people
    through mail_access). The password is Fernet ciphertext under LARK_MAIL_KEY and is never
    returned by any endpoint."""

    __tablename__ = "mail_accounts"
    __table_args__ = (
        CheckConstraint(f"status {_in(ACCOUNT_STATUSES)}", name="status_valid"),
        CheckConstraint(f"kind {_in(KINDS)}", name="kind_valid"),
        CheckConstraint(
            "imap_port BETWEEN 1 AND 65535 AND smtp_port BETWEEN 1 AND 65535", name="ports_valid"
        ),  # fmt: skip
        CheckConstraint("sync_interval_seconds BETWEEN 60 AND 86400", name="interval_valid"),
        CheckConstraint(
            "status = 'NOT_CONFIGURED' OR password_enc IS NOT NULL", name="configured_has_secret"
        ),
        Index("uq_mail_account_address", "company_id", text("lower(address)"), unique=True),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    label: Mapped[str] = mapped_column(Text, nullable=False, server_default="")  # "Andy Mail"
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PERSONAL")
    provider: Mapped[str] = mapped_column(String(16), nullable=False, server_default="lark")
    imap_host: Mapped[str] = mapped_column(Text, nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, server_default="993")
    smtp_host: Mapped[str] = mapped_column(Text, nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False, server_default="465")
    username: Mapped[str] = mapped_column(Text, nullable=False)
    password_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    password_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_set_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="NOT_CONFIGURED")
    sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    sync_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="120"
    )
    imap_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    smtp_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imap_error: Mapped[str | None] = mapped_column(Text)
    smtp_error: Mapped[str | None] = mapped_column(Text)
    capabilities: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error_code: Mapped[str | None] = mapped_column(String(48))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # One sync at a time per mailbox: a lease, so a crashed worker's lock simply expires.
    sync_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backfill_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    signature_html: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = _created()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class MailAccess(Base):
    """Who may open a mailbox. Explicit only: being Super Admin grants nothing here.
    A revoked grant keeps its row (revoked_at) for the audit trail."""

    __tablename__ = "mail_access"
    __table_args__ = (
        Index(
            "uq_mail_access_live",
            "account_id",
            "employee_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_mail_access_employee", "employee_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mail_accounts.id"), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    can_send: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    granted_at: Mapped[datetime] = _created()
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The person's own signature for this mailbox (migration 0031). Empty = the mailbox default.
    signature_html: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    signature_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )  # the editor's template + fields, so it reopens as it was saved
    signature_on_new: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    signature_on_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class MailFolder(Base):
    """An IMAP folder of one mailbox and its sync cursors. `high_uid` = newest UID imported;
    `backfill_uid` = the oldest UID imported so far (history is imported newest first, in
    steps, and resumes from here)."""

    __tablename__ = "mail_folders"
    __table_args__ = (
        UniqueConstraint("account_id", "name", name="uq_mail_folder_name"),
        CheckConstraint(f"role {_in(FOLDER_ROLES)}", name="role_valid"),
    )
    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mail_accounts.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)  # server path (modified UTF-7 decoded)
    raw_name: Mapped[str] = mapped_column(Text, nullable=False)  # as sent to the server
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="OTHER")
    attributes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    selectable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    uidvalidity: Mapped[int | None] = mapped_column(BigInteger)
    high_uid: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    backfill_uid: Mapped[int | None] = mapped_column(BigInteger)
    backfill_done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    server_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class MailMessage(Base):
    """One email in one folder, keyed by (folder, UIDVALIDITY, UID) - never by Message-ID alone.
    Bodies are stored already sanitized (allow-list; remote images rewritten to a blocked
    placeholder unless the reader chooses to load them)."""

    __tablename__ = "mail_messages"
    __table_args__ = (
        UniqueConstraint("folder_id", "uidvalidity", "uid", name="uq_mail_message_uid"),
        Index("ix_mail_messages_list", "folder_id", "received_at", "id"),
        Index("ix_mail_messages_account_date", "account_id", "received_at"),
        Index("ix_mail_messages_thread", "account_id", "thread_key"),
        Index("ix_mail_messages_msgid", "account_id", "message_id_header"),
        Index("ix_mail_messages_sha", "folder_id", "raw_sha256"),
        Index("ix_mail_messages_search", "search_tsv", postgresql_using="gin"),
        Index(
            "ix_mail_messages_subject_trgm",
            "subject",
            postgresql_using="gin",
            postgresql_ops={"subject": "gin_trgm_ops"},
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mail_accounts.id"), nullable=False)
    folder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mail_folders.id"), nullable=False)
    uidvalidity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id_header: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    in_reply_to: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    references: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    thread_key: Mapped[str] = mapped_column(Text, nullable=False)
    from_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    from_email: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    to_addrs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    cc_addrs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    bcc_addrs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    reply_to: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    subject: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    snippet: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    body_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    body_html: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    body_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    has_remote_images: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # Date: header
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    seen: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    attachment_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    attachment_names: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    search_tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=True)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    parse_warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    server_removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = _created()


class MailAttachment(Base):
    """A file on an email. Bytes are kept in mail_attachment_blobs (loaded only on download)."""

    __tablename__ = "mail_attachments"
    __table_args__ = (
        UniqueConstraint("message_id", "part_id", name="uq_mail_attachment_part"),
        CheckConstraint("byte_size >= 0", name="size_valid"),
        Index("ix_mail_attachments_sha", "sha256"),
    )
    id: Mapped[uuid.UUID] = _pk()
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mail_messages.id"), nullable=False, index=True
    )
    part_id: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    declared_mime: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    detected_mime: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    content_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    inline: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    stored: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # CLEAN / INFECTED (clamd answered) or NOT_SCANNED (no scanner configured) or BLOCKED
    # (executable/script type: never served).
    scan_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="NOT_SCANNED"
    )
    scan_detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = _created()


class MailAttachmentBlob(Base):
    __tablename__ = "mail_attachment_blobs"
    attachment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mail_attachments.id"), primary_key=True
    )
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class MailUserState(Base):
    """Per-person read / star state inside Pro Trading. The mailbox on Lark is never changed
    (no flag writes), so Saddam reading an info@ email does not mark it read for Asha."""

    __tablename__ = "mail_user_state"
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mail_messages.id"), primary_key=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), primary_key=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "Mark as unread" here, even for mail Lark already has as read (migration 0032).
    marked_unread: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    starred: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    updated_at: Mapped[datetime] = _created()


class MailDraft(Base):
    """A draft kept in Pro Trading (autosaved). Drafts are not written to Lark: replacing a
    server draft would need a delete, which this module never does."""

    __tablename__ = "mail_drafts"
    __table_args__ = (Index("ix_mail_drafts_owner", "account_id", "employee_id", "updated_at"),)
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mail_accounts.id"), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default="new")
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mail_messages.id"))
    to_addrs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    cc_addrs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    bcc_addrs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    subject: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    body_html: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    attachment_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    sent_outgoing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mail_outgoing.id"))
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class MailUpload(Base):
    """A file attached in the compose window (before sending). Owned by its uploader."""

    __tablename__ = "mail_uploads"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = _created()


class MailOutgoing(Base):
    """The outgoing queue. One row per Send press (unique idempotency key), so a double click
    or a retried request never sends twice. A submission that may have reached Lark but was not
    confirmed is UNCERTAIN and is never retried automatically."""

    __tablename__ = "mail_outgoing"
    __table_args__ = (
        CheckConstraint(f"status {_in(OUTGOING_STATUSES)}", name="status_valid"),
        UniqueConstraint("account_id", "idempotency_key", name="uq_mail_outgoing_idem"),
        Index("ix_mail_outgoing_status", "status", "next_attempt_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mail_accounts.id"), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default="new")
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mail_messages.id"))
    message_id_header: Mapped[str] = mapped_column(Text, nullable=False)
    from_addr: Mapped[str] = mapped_column(Text, nullable=False)
    to_addrs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    cc_addrs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    bcc_addrs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    subject: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    raw_mime: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    smtp_response: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_in_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class MailSyncRun(Base):
    __tablename__ = "mail_sync_runs"
    __table_args__ = (Index("ix_mail_sync_runs_account", "account_id", "started_at"),)
    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mail_accounts.id"), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)  # beat / manual / test
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="RUNNING")
    new_messages: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    backfilled: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    folders: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(48))
    error: Mapped[str | None] = mapped_column(Text)


class MailLink(Base):
    """An email explicitly linked to an ERP record by a person. Linking changes nothing in the
    ERP record itself."""

    __tablename__ = "mail_links"
    __table_args__ = (
        CheckConstraint(f"entity_type {_in(LINK_TYPES)}", name="entity_type_valid"),
        Index(
            "uq_mail_link_live",
            "message_id",
            "entity_type",
            "entity_id",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
        ),
        Index("ix_mail_links_entity", "entity_type", "entity_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mail_messages.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    created_at: Mapped[datetime] = _created()
    removed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
