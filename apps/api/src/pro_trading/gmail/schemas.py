"""Request / response shapes of the Gmail Inbox API. No field here ever carries a token."""

import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

AccountStatus = Literal["CONNECTED", "ERROR", "REAUTH_REQUIRED", "DISCONNECTED"]
ProcessingState = Literal[
    "NOT_PROCESSED", "RECEIVED", "PROCESSING", "NEEDS_REVIEW", "PROCESSED", "FAILED", "REJECTED"
]
DateRange = Literal["all", "today", "yesterday", "7d", "30d", "custom"]
Category = Literal[
    "SALES_ORDER",
    "PICK_SHEET",
    "VENDOR_DOCUMENT",
    "PURCHASE_ORDER",
    "DELIVERY_NOTE",
    "PACKING_LIST",
    "RECEIPT",
]


class GmailAccountOut(BaseModel):
    id: str
    email_address: str
    status: AccountStatus
    connected_at: str
    connected_by_name: str | None
    last_sync_at: str | None
    last_error_code: str | None
    last_error: str | None
    last_error_at: str | None
    # Live push (Pub/Sub watch) is registered until this time; null = timer only.
    watch_expires_at: str | None
    last_push_at: str | None
    backfill_done: bool
    version: int


class GmailStatusOut(BaseModel):
    # OAuth client configured on this server (GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET).
    configured: bool
    # A Pub/Sub topic and a way to verify its pushes are configured.
    push_configured: bool
    poll_seconds: int
    scope: str
    redirect_uri: str
    account: GmailAccountOut | None
    message_count: int
    unread_count: int
    can_manage: bool
    can_read_body: bool
    can_preview: bool
    can_download: bool
    can_process: bool
    can_reprocess: bool


class OAuthStartOut(BaseModel):
    authorization_url: str


class OAuthCompleteIn(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=4096)


class DisconnectIn(BaseModel):
    version: int = Field(ge=1)


class SyncOut(BaseModel):
    queued: bool


class GmailAttachmentOut(BaseModel):
    id: str
    message_id: str
    filename: str
    mime_type: str
    byte_size: int
    inline: bool
    eligible: bool
    # Why it cannot be previewed or read (null = it can).
    unsupported_reason: str | None
    page_count: int | None
    sha256: str | None
    state: ProcessingState


class GmailMessageOut(BaseModel):
    id: str
    from_name: str
    from_email: str
    to_addr: str
    subject: str
    snippet: str
    received_at: str
    read: bool
    labels: list[str]
    attachment_count: int
    pdf_count: int
    removed: bool
    state: ProcessingState
    attachments: list[GmailAttachmentOut]


class GmailMessagePage(BaseModel):
    items: list[GmailMessageOut]
    next_cursor: str | None
    total: int


class GmailMessageDetail(GmailMessageOut):
    thread_id: str
    # False when the caller may not read email bodies (gmail.body.read): both are then empty.
    body_visible: bool
    body_text: str
    body_html: str
    body_truncated: bool


class ReadIn(BaseModel):
    read: bool


class HistoryEntry(BaseModel):
    job_id: str | None
    attempt_no: int | None
    source: Literal["gmail", "scan"]
    same_attachment: bool
    reprocess: bool
    document_category: str
    processed_at: str
    processed_by_name: str | None
    scan_job_id: str
    scan_job_no: str
    extraction_id: str | None
    status: ProcessingState
    error_code: str | None
    shipment_id: str | None
    sales_order_id: str | None
    order_no: str | None
    order_status: str | None


class GmailAttachmentDetail(GmailAttachmentOut):
    message_subject: str
    message_from: str
    received_at: str
    history: list[HistoryEntry]


class ProcessIn(BaseModel):
    warehouse_id: uuid.UUID
    category: Category = "PICK_SHEET"
    # Read a document again that was already processed (needs gmail.reprocess).
    reprocess: bool = False
    # Go ahead although a draft already exists for this document.
    confirm_duplicate: bool = False


class ProcessOut(BaseModel):
    job_id: str
    attempt_no: int
    attachment_id: str
    scan_job_id: str
    scan_job_no: str
    extraction_id: str
    extraction_status: str
    review_path: str


class ListParams(BaseModel):
    q: str = ""
    range: DateRange = "all"
    date_from: date | None = None
    date_to: date | None = None


JsonObject = dict[str, Any]
