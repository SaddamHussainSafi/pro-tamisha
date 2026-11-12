"""Email module (Lark Mail). Two routers:

- /mail/...        the people who use mailboxes (permission mail.use + an explicit grant per
                   mailbox, checked in larkmail/access.py on every request)
- /mail/admin/...  Super Admin only (mail.manage): mailboxes, passwords, tests, sync control,
                   access grants, diagnostics. Passwords go in and never come back out.

Nothing here deletes an email or changes anything in Lark except sending a message a person
composed and pressed Send on. Read / star state is kept in Pro Trading per person.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Text, and_, cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .. import audit, idempotency, ratelimit
from ..config import get_settings
from ..deps import DbDep, Principal, authorize, require_permission
from ..errors import ApiError
from ..larkmail import access
from ..larkmail.models import (
    MailAccess,
    MailAccount,
    MailAttachment,
    MailAttachmentBlob,
    MailDraft,
    MailFolder,
    MailLink,
    MailMessage,
    MailOutgoing,
    MailSyncRun,
    MailUpload,
    MailUserState,
)
from ..larkmail.outgoing import OutFile, build
from ..larkmail.sanitize import render, text_to_html
from ..larkmail.secrets import SecretUnavailable, seal, unseal
from ..larkmail.sniff import PREVIEWABLE, detect
from ..larkmail.sync import ROLE_ORDER, test_connection
from ..models import AuditEvent, Customer, Employee, Invoice, Payment, SalesOrder, Shipment, Vendor
from ..schemas import EMAIL_RE
from ._common import content_disposition, esc

router = APIRouter(prefix="/mail", tags=["email"])
Use = Depends(require_permission("mail.use"))
Manage = Depends(require_permission("mail.manage"))

MAX_RCPTS = 100
UPLOAD_MAX = 25 * 1024 * 1024


def now() -> datetime:
    return datetime.now(UTC)


# ================================================================== schemas


class Addr(BaseModel):
    name: str = Field(default="", max_length=200)
    email: str = Field(max_length=320)

    @field_validator("email")
    @classmethod
    def _valid(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(EMAIL_RE, v):
            raise ValueError("Enter a valid email address")
        return v


class AccountOut(BaseModel):
    id: uuid.UUID
    address: str
    label: str
    display_name: str
    kind: str
    status: str
    can_send: bool
    unread: int
    last_sync_at: datetime | None
    history_complete: bool
    signature_html: str


class FolderOut(BaseModel):
    id: str
    name: str
    role: str
    total: int
    unread: int
    virtual: bool = False


class MessageRow(BaseModel):
    id: uuid.UUID
    folder_id: uuid.UUID
    thread_key: str
    thread_count: int
    from_name: str
    from_email: str
    to: list[dict[str, str]]
    subject: str
    snippet: str
    received_at: datetime
    unread: bool
    starred: bool
    attachment_count: int
    removed_in_lark: bool
    highlight: str | None = None
    # Up to three files for the chips under the subject (inline pictures left out).
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class MessagePage(BaseModel):
    items: list[MessageRow]
    next_cursor: str | None
    total: int | None = None


class AttachmentOut(BaseModel):
    id: uuid.UUID
    filename: str
    mime_type: str
    byte_size: int
    inline: bool
    previewable: bool
    downloadable: bool
    scan_status: str
    scan_detail: str


class LinkOut(BaseModel):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    label: str
    url: str
    created_by: str
    created_at: datetime


class MessageOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    folder_id: uuid.UUID
    folder_role: str
    message_id_header: str
    from_name: str
    from_email: str
    to: list[dict[str, str]]
    cc: list[dict[str, str]]
    bcc: list[dict[str, str]]
    reply_to: list[dict[str, str]]
    subject: str
    sent_at: datetime | None
    received_at: datetime
    body_html: str
    body_text: str
    body_truncated: bool
    has_remote_images: bool
    remote_images_loaded: bool
    unread: bool
    starred: bool
    removed_in_lark: bool
    attachments: list[AttachmentOut]
    thread: list[MessageRow]
    links: list[LinkOut]
    warnings: list[str]


class StateIn(BaseModel):
    read: bool | None = None
    starred: bool | None = None


class DraftIn(BaseModel):
    account_id: uuid.UUID
    mode: Literal["new", "reply", "reply_all", "forward"] = "new"
    source_message_id: uuid.UUID | None = None
    to: list[Addr] = Field(default_factory=list, max_length=MAX_RCPTS)
    cc: list[Addr] = Field(default_factory=list, max_length=MAX_RCPTS)
    bcc: list[Addr] = Field(default_factory=list, max_length=MAX_RCPTS)
    subject: str = Field(default="", max_length=998)
    body_html: str = Field(default="", max_length=1_000_000)
    upload_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    forward_attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class DraftOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    mode: str
    source_message_id: uuid.UUID | None
    to: list[dict[str, str]]
    cc: list[dict[str, str]]
    bcc: list[dict[str, str]]
    subject: str
    body_html: str
    upload_ids: list[str]
    updated_at: datetime


class SendIn(DraftIn):
    draft_id: uuid.UUID | None = None


class OutgoingOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    status: str
    subject: str
    to: list[dict[str, str]]
    attempts: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None
    seen_in_sent: bool


class UploadOut(BaseModel):
    id: uuid.UUID
    filename: str
    mime_type: str
    byte_size: int


class LinkIn(BaseModel):
    entity_type: Literal["customer", "vendor", "sales_order", "invoice", "shipment", "payment"]
    entity_id: uuid.UUID
    note: str = Field(default="", max_length=500)


class LinkTarget(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    label: str
    sub: str


# ================================================================== helpers


def _read_ids(db: Session, p: Principal, ids: list[uuid.UUID]) -> dict[uuid.UUID, MailUserState]:
    if not ids:
        return {}
    rows = db.scalars(
        select(MailUserState).where(
            MailUserState.employee_id == p.employee.id, MailUserState.message_id.in_(ids)
        )
    )
    return {r.message_id: r for r in rows}


def _unread_filter(p: Principal) -> Any:
    """Unread for this person: not read in Lark and not opened by them here."""
    return and_(
        MailMessage.seen.is_(False),
        ~select(MailUserState.message_id)
        .where(
            MailUserState.message_id == MailMessage.id,
            MailUserState.employee_id == p.employee.id,
            MailUserState.read_at.is_not(None),
        )
        .exists(),
    )


def _starred_filter(p: Principal) -> Any:
    local = (
        select(MailUserState.starred)
        .where(
            MailUserState.message_id == MailMessage.id,
            MailUserState.employee_id == p.employee.id,
        )
        .scalar_subquery()
    )
    return func.coalesce(local, MailMessage.flagged).is_(True)


def _live() -> Any:
    return and_(MailMessage.uidvalidity == MailFolder.uidvalidity)


def _rows(db: Session, p: Principal, msgs: list[MailMessage]) -> list[MessageRow]:
    st = _read_ids(db, p, [m.id for m in msgs])
    keys = {(m.account_id, m.thread_key) for m in msgs}
    counts: dict[tuple[uuid.UUID, str], int] = {}
    if keys:
        for acc_id, tk, n in db.execute(
            select(MailMessage.account_id, MailMessage.thread_key, func.count())
            .where(
                MailMessage.account_id.in_({k[0] for k in keys}),
                MailMessage.thread_key.in_({k[1] for k in keys}),
            )
            .group_by(MailMessage.account_id, MailMessage.thread_key)
        ):
            counts[(acc_id, tk)] = int(n)
    chips: dict[uuid.UUID, list[dict[str, Any]]] = {}
    with_files = [m.id for m in msgs if m.attachment_count]
    if with_files:
        for a in db.scalars(
            select(MailAttachment)
            .where(MailAttachment.message_id.in_(with_files), MailAttachment.inline.is_(False))
            .order_by(MailAttachment.message_id, MailAttachment.part_id)
        ):
            lst = chips.setdefault(a.message_id, [])
            if len(lst) < 3:
                lst.append(_att_out(a).model_dump(mode="json"))
    out = []
    for m in msgs:
        s = st.get(m.id)
        out.append(
            MessageRow(
                id=m.id,
                folder_id=m.folder_id,
                thread_key=m.thread_key,
                thread_count=counts.get((m.account_id, m.thread_key), 1),
                from_name=m.from_name,
                from_email=m.from_email,
                to=m.to_addrs[:5],
                subject=m.subject,
                snippet=m.snippet,
                received_at=m.received_at,
                unread=not m.seen and not (s and s.read_at),
                starred=s.starred if s is not None else m.flagged,
                attachment_count=m.attachment_count,
                removed_in_lark=m.server_removed_at is not None,
                attachments=chips.get(m.id, []),
            )  # fmt: skip
        )
    return out


def _cursor(m: MailMessage) -> str:
    return base64.urlsafe_b64encode(f"{m.received_at.isoformat()}|{m.id}".encode()).decode()


def _uncursor(c: str | None) -> tuple[datetime, uuid.UUID] | None:
    if not c:
        return None
    try:
        a, b = base64.urlsafe_b64decode(c.encode()).decode().split("|")
        return datetime.fromisoformat(a), uuid.UUID(b)
    except (ValueError, UnicodeDecodeError):
        raise ApiError(422, "VALIDATION", "Invalid cursor") from None


class SignatureIn(BaseModel):
    html: str = Field(default="", max_length=20_000)
    fields: dict[str, Any] = Field(default_factory=dict)
    use_on_new: bool = True
    use_on_reply: bool = True


class SignatureOut(SignatureIn):
    mailbox_default_html: str
    logo_cid: str = "tt-logo"


@router.get("/accounts/{account_id}/signature", response_model=SignatureOut)
def get_signature(
    account_id: uuid.UUID, request: Request, db: DbDep, p: Principal = Use
) -> SignatureOut:
    acc = access.account(db, request, p, account_id)
    g = _grant(db, p, acc.id)
    return SignatureOut(html=g.signature_html, fields=g.signature_fields,
                        use_on_new=g.signature_on_new, use_on_reply=g.signature_on_reply,
                        mailbox_default_html=acc.signature_html)  # fmt: skip


@router.put("/accounts/{account_id}/signature", response_model=SignatureOut)
def put_signature(
    account_id: uuid.UUID, body: SignatureIn, request: Request, db: DbDep, p: Principal = Use
) -> SignatureOut:
    """The person's own signature for this mailbox (cleaned by the same allow-list as mail).
    The company logo is written as <img src="cid:tt-logo"> and is embedded when sending."""
    from ..larkmail.sanitize import sanitize

    acc = access.account(db, request, p, account_id)
    g = _grant(db, p, acc.id)
    if len(json.dumps(body.fields)) > 10_000:
        raise ApiError(422, "VALIDATION", "Signature details are too long")
    g.signature_html = sanitize(body.html, 20_000)[0]
    g.signature_fields = body.fields
    g.signature_on_new, g.signature_on_reply = body.use_on_new, body.use_on_reply
    db.commit()
    return get_signature(account_id, request, db, p)


@router.get("/signature-logo")
def signature_logo(p: Principal = Use) -> Response:
    """The company logo as it is embedded in signatures (for the editor preview)."""
    from ..larkmail.outgoing import LOGO

    return Response(LOGO.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=86400"})  # fmt: skip


def _account_out(db: Session, p: Principal, acc: MailAccount, grant: MailAccess) -> AccountOut:
    inbox = db.scalar(
        select(MailFolder.id).where(MailFolder.account_id == acc.id, MailFolder.role == "INBOX")
    )
    unread = 0
    if inbox:
        unread = int(
            db.scalar(
                select(func.count())
                .select_from(MailMessage)
                .join(MailFolder, MailFolder.id == MailMessage.folder_id)
                .where(
                    MailMessage.folder_id == inbox,
                    MailMessage.server_removed_at.is_(None),
                    _live(),
                    _unread_filter(p),
                )
            )
            or 0
        )
    return AccountOut(
        id=acc.id, address=acc.address, label=acc.label or acc.address,
        display_name=acc.display_name, kind=acc.kind, status=acc.status,
        # A sender address is offered only once Lark accepted its SMTP login.
        can_send=grant.can_send and acc.smtp_ok_at is not None and acc.status != "AUTH_FAILED",
        unread=unread, last_sync_at=acc.last_sync_at,
        history_complete=acc.backfill_done_at is not None,
        signature_html=grant.signature_html or acc.signature_html,
    )  # fmt: skip


def _grant(db: Session, p: Principal, account_id: uuid.UUID) -> MailAccess:
    g = db.scalar(
        select(MailAccess).where(
            MailAccess.account_id == account_id,
            MailAccess.employee_id == p.employee.id,
            MailAccess.revoked_at.is_(None),
        )
    )
    assert g is not None
    return g


# ================================================================== mailboxes & messages


@router.get("/accounts", response_model=list[AccountOut])
def my_accounts(db: DbDep, p: Principal = Use) -> list[AccountOut]:
    """Only the mailboxes this person was given. Never a combined inbox."""
    rows = db.execute(
        select(MailAccount, MailAccess)
        .join(MailAccess, MailAccess.account_id == MailAccount.id)
        .where(
            MailAccess.employee_id == p.employee.id,
            MailAccess.revoked_at.is_(None),
            MailAccount.company_id == p.company_id,
        )
        .order_by(MailAccount.kind, MailAccount.label)
    ).all()
    return [_account_out(db, p, a, g) for a, g in rows]


@router.get("/accounts/{account_id}/folders", response_model=list[FolderOut])
def folders(
    account_id: uuid.UUID, request: Request, db: DbDep, p: Principal = Use
) -> list[FolderOut]:
    acc = access.account(db, request, p, account_id)
    fs = db.scalars(
        select(MailFolder).where(
            MailFolder.account_id == acc.id,
            MailFolder.selectable.is_(True),
            MailFolder.gone_at.is_(None),
            MailFolder.role != "TRASH",  # no Trash in this module (directive section 7)
        )
    ).all()
    unread = dict(
        db.execute(
            select(MailMessage.folder_id, func.count())
            .join(MailFolder, MailFolder.id == MailMessage.folder_id)
            .where(
                MailMessage.account_id == acc.id,
                MailMessage.server_removed_at.is_(None),
                _live(),
                _unread_filter(p),
            )
            .group_by(MailMessage.folder_id)
        ).all()
    )
    out = [
        FolderOut(
            id=str(f.id),
            name=f.display_name if f.role == "OTHER" else f.role.title(),
            role=f.role,
            total=f.message_count,
            unread=int(unread.get(f.id, 0)),
        )  # fmt: skip
        for f in sorted(fs, key=lambda f: (ROLE_ORDER.get(f.role, 8), f.name))
    ]
    out.insert(
        1 if any(f.role == "INBOX" for f in fs) else 0,
        FolderOut(id="starred", name="Starred", role="STARRED", total=0, unread=0, virtual=True),
    )
    return out


@router.get("/accounts/{account_id}/messages", response_model=MessagePage)
def list_messages(
    account_id: uuid.UUID,
    request: Request,
    db: DbDep,
    p: Principal = Use,
    folder: Annotated[str, Query(max_length=40)] = "inbox",
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    unread: bool = False,
    has_attachments: bool = False,
) -> MessagePage:
    acc = access.account(db, request, p, account_id)
    q = (
        select(MailMessage)
        .join(MailFolder, MailFolder.id == MailMessage.folder_id)
        .where(MailMessage.account_id == acc.id, _live(), MailFolder.role != "TRASH")
    )
    if folder == "starred":
        q = q.where(_starred_filter(p))
    elif folder == "inbox":
        q = q.where(MailFolder.role == "INBOX")
    else:
        try:
            fid = uuid.UUID(folder)
        except ValueError:
            raise ApiError(422, "VALIDATION", "Unknown folder") from None
        q = q.where(MailMessage.folder_id == fid)
    if unread:
        q = q.where(_unread_filter(p))
    if has_attachments:
        q = q.where(MailMessage.attachment_count > 0)
    c = _uncursor(cursor)
    if c:
        q = q.where(
            or_(
                MailMessage.received_at < c[0],
                and_(MailMessage.received_at == c[0], MailMessage.id < c[1]),
            )
        )
    msgs = list(
        db.scalars(
            q.order_by(MailMessage.received_at.desc(), MailMessage.id.desc()).limit(limit + 1)
        )
    )
    more = len(msgs) > limit
    msgs = msgs[:limit]
    return MessagePage(items=_rows(db, p, msgs), next_cursor=_cursor(msgs[-1]) if more else None)


def _attachments(db: Session, message_id: uuid.UUID) -> list[MailAttachment]:
    return list(
        db.scalars(
            select(MailAttachment)
            .where(MailAttachment.message_id == message_id)
            .order_by(MailAttachment.part_id)
        )
    )


def _att_out(a: MailAttachment) -> AttachmentOut:
    ok = a.stored and a.scan_status not in ("BLOCKED", "INFECTED")
    return AttachmentOut(
        id=a.id, filename=a.filename, mime_type=a.detected_mime or a.declared_mime,
        byte_size=a.byte_size, inline=a.inline,
        previewable=ok and a.detected_mime in PREVIEWABLE, downloadable=ok,
        scan_status=a.scan_status, scan_detail=a.scan_detail,
    )  # fmt: skip


def _links(db: Session, message_id: uuid.UUID) -> list[LinkOut]:
    rows = db.execute(
        select(MailLink, Employee.display_name)
        .join(Employee, Employee.id == MailLink.created_by)
        .where(MailLink.message_id == message_id, MailLink.removed_at.is_(None))
        .order_by(MailLink.created_at)
    ).all()
    out = []
    for link, who in rows:
        label, url = _entity_label(db, link.entity_type, link.entity_id)
        out.append(LinkOut(id=link.id, entity_type=link.entity_type, entity_id=link.entity_id,
                           label=label, url=url, created_by=who, created_at=link.created_at))  # fmt: skip  # noqa: E501
    return out


@router.get("/messages/{message_id}", response_model=MessageOut)
def get_message(
    message_id: uuid.UUID,
    request: Request,
    db: DbDep,
    p: Principal = Use,
    images: bool = False,
) -> MessageOut:
    m = access.message(db, request, p, message_id)
    folder = db.get(MailFolder, m.folder_id)
    assert folder is not None
    atts = _attachments(db, m.id)
    by_cid = {a.content_id: a for a in atts if a.content_id and a.stored}

    def cid_lookup(cid: str) -> tuple[str, bytes] | None:
        a = by_cid.get(cid)
        if (
            a is None
            or a.detected_mime not in ("image/png", "image/jpeg", "image/gif")
            or a.byte_size > 2_000_000
        ):
            return None
        blob = db.get(MailAttachmentBlob, a.id)
        return (a.detected_mime, blob.content) if blob else None

    html = m.body_html or (text_to_html(m.body_text) if m.body_text else "")
    html = render(html, remote_images=images, cid_lookup=cid_lookup)
    thread = list(
        db.scalars(
            select(MailMessage)
            .join(MailFolder, MailFolder.id == MailMessage.folder_id)
            .where(
                MailMessage.account_id == m.account_id,
                MailMessage.thread_key == m.thread_key,
                MailFolder.role != "TRASH",
                _live(),
            )
            .order_by(MailMessage.received_at)
            .limit(100)
        )
    )
    st = _read_ids(db, p, [m.id]).get(m.id)
    return MessageOut(
        id=m.id, account_id=m.account_id, folder_id=m.folder_id, folder_role=folder.role,
        message_id_header=m.message_id_header, from_name=m.from_name, from_email=m.from_email,
        to=m.to_addrs, cc=m.cc_addrs, bcc=m.bcc_addrs, reply_to=m.reply_to, subject=m.subject,
        sent_at=m.sent_at, received_at=m.received_at, body_html=html, body_text=m.body_text,
        body_truncated=m.body_truncated, has_remote_images=m.has_remote_images,
        remote_images_loaded=images and m.has_remote_images,
        unread=not m.seen and not (st and st.read_at),
        starred=st.starred if st is not None else m.flagged,
        removed_in_lark=m.server_removed_at is not None,
        attachments=[_att_out(a) for a in atts if not (a.inline and a.content_id in m.body_html)],
        thread=_rows(db, p, thread), links=_links(db, m.id), warnings=m.parse_warnings,
    )  # fmt: skip


@router.post("/messages/{message_id}/state", status_code=204)
def set_state(
    message_id: uuid.UUID, body: StateIn, request: Request, db: DbDep, p: Principal = Use
) -> Response:
    """Read / starred for this person only. Lark is not changed."""
    m = access.message(db, request, p, message_id)
    vals: dict[str, Any] = {"updated_at": now()}
    if body.read is not None:
        vals["read_at"] = now() if body.read else None
    if body.starred is not None:
        vals["starred"] = body.starred
    ins = insert(MailUserState).values(
        message_id=m.id, employee_id=p.employee.id,
        starred=body.starred if body.starred is not None else m.flagged,
        read_at=vals.get("read_at"),
    )  # fmt: skip
    db.execute(ins.on_conflict_do_update(index_elements=["message_id", "employee_id"], set_=vals))
    db.commit()
    return Response(status_code=204)


@router.get("/attachments/{attachment_id}")
def attachment(
    attachment_id: uuid.UUID,
    request: Request,
    db: DbDep,
    p: Principal = Use,
    inline: bool = False,
) -> Response:
    a = db.get(MailAttachment, attachment_id)
    if a is None:
        raise ApiError(404, "NOT_FOUND", "Attachment not found")
    access.message(db, request, p, a.message_id)
    if a.scan_status in ("BLOCKED", "INFECTED") or not a.stored:
        raise ApiError(
            409, "ATTACHMENT_BLOCKED",
            "This file can't be opened here" + (f": {a.scan_detail}" if a.scan_detail else ""),
        )  # fmt: skip
    blob = db.get(MailAttachmentBlob, a.id)
    if blob is None:
        raise ApiError(404, "NOT_FOUND", "Attachment not stored")
    show_inline = inline and a.detected_mime in PREVIEWABLE
    mime = a.detected_mime if show_inline else "application/octet-stream"
    audit.record(
        db, action="mail.attachment_download", success=True, company_id=p.company_id,
        actor_id=p.employee.id, target_type="mail_attachment", target_id=str(a.id),
        request_id=request.state.request_id, detail={"inline": show_inline},
    )  # fmt: skip
    db.commit()
    return Response(
        blob.content,
        media_type=mime,
        headers={
            "Content-Disposition": content_disposition(
                "inline" if show_inline else "attachment", a.filename
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self' data:; "
            "style-src 'unsafe-inline'; object-src 'self'",
            "Cache-Control": "private, no-store",
        },
    )


# ================================================================== search


class SearchOut(BaseModel):
    items: list[MessageRow]
    next_cursor: str | None


def _words(q: str) -> list[str]:
    return [w for w in re.findall(r"[\w@.\-]+", q.lower()) if w][:8]


def search_query(p: Principal, account_ids: list[uuid.UUID], q: str) -> Any:
    """Indexed full-text (prefix) match plus address / subject / file-name contains, limited
    to the given (already authorized) mailboxes."""
    ws = _words(q)
    conds = []
    if ws:
        tsq = " & ".join(
            re.sub(r"[^\w@.\-]", "", w) + ":*" for w in ws if re.sub(r"[^\w@.\-]", "", w)
        )
        like = f"%{esc(q.strip().lower())}%"
        conds.append(
            or_(
                MailMessage.search_tsv.op("@@")(func.to_tsquery("simple", tsq)),
                func.lower(MailMessage.from_email).like(like),
                func.lower(MailMessage.subject).like(like),
                func.lower(MailMessage.attachment_names).like(like),
            )
        )
    return (
        select(MailMessage)
        .join(MailFolder, MailFolder.id == MailMessage.folder_id)
        .where(
            MailMessage.company_id == p.company_id,
            MailMessage.account_id.in_(account_ids),
            MailFolder.role != "TRASH",
            _live(),
            *conds,
        )
    )


@router.get("/search", response_model=SearchOut)
def search(
    request: Request,
    db: DbDep,
    account_id: Annotated[uuid.UUID, Query()],
    p: Principal = Use,
    q: Annotated[str, Query(max_length=200)] = "",
    folder: Annotated[str | None, Query(max_length=40)] = None,
    from_: Annotated[str | None, Query(alias="from", max_length=320)] = None,
    to: Annotated[str | None, Query(max_length=320)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    has_attachments: bool = False,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> SearchOut:
    """Search one mailbox (the selected one; a person with two mailboxes switches)."""
    if ratelimit.attempt("mail_search", str(p.employee.id), 60) > 240:
        raise ApiError(429, "RATE_LIMITED", "Too many searches; wait a few seconds")
    acc = access.account(db, request, p, account_id)
    stmt = search_query(p, [acc.id], q)
    if folder == "starred":
        stmt = stmt.where(_starred_filter(p))
    elif folder:
        try:
            stmt = stmt.where(MailMessage.folder_id == uuid.UUID(folder))
        except ValueError:
            raise ApiError(422, "VALIDATION", "Unknown folder") from None
    if from_:
        f = f"%{esc(from_.lower())}%"
        stmt = stmt.where(
            or_(
                func.lower(MailMessage.from_email).like(f),
                func.lower(MailMessage.from_name).like(f),
            )
        )
    if to:
        t = f"%{esc(to.lower())}%"
        stmt = stmt.where(
            func.lower(
                cast(MailMessage.to_addrs, Text) + " " + cast(MailMessage.cc_addrs, Text)
            ).like(t)
        )
    if date_from:
        stmt = stmt.where(
            MailMessage.received_at >= datetime.combine(date_from, datetime.min.time(), UTC)
        )
    if date_to:
        stmt = stmt.where(
            MailMessage.received_at
            < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), UTC)
        )
    if has_attachments:
        stmt = stmt.where(MailMessage.attachment_count > 0)
    c = _uncursor(cursor)
    if c:
        stmt = stmt.where(
            or_(
                MailMessage.received_at < c[0],
                and_(MailMessage.received_at == c[0], MailMessage.id < c[1]),
            )
        )
    try:
        with db.begin_nested():
            db.execute(text("SET LOCAL statement_timeout = '5s'"))
            msgs = list(
                db.scalars(
                    stmt.order_by(MailMessage.received_at.desc(), MailMessage.id.desc()).limit(
                        limit + 1
                    )
                )
            )
    except Exception:  # noqa: BLE001 - a bad tsquery must not 500
        raise ApiError(422, "BAD_QUERY", "Search words could not be understood") from None
    more = len(msgs) > limit
    msgs = msgs[:limit]
    rows = _rows(db, p, msgs)
    ws = _words(q)
    for r, m in zip(rows, msgs, strict=True):
        r.highlight = _highlight(m, ws)
    return SearchOut(items=rows, next_cursor=_cursor(msgs[-1]) if more else None)


def _highlight(m: MailMessage, ws: list[str]) -> str | None:
    if not ws:
        return None
    txt = m.body_text or ""
    low = txt.lower()
    pos = min((low.find(w) for w in ws if low.find(w) >= 0), default=-1)
    if pos < 0:
        return None
    start = max(0, pos - 50)
    return (
        ("…" if start else "")
        + txt[start : pos + 110].replace("\n", " ")
        + ("…" if pos + 110 < len(txt) else "")
    )


# ================================================================== drafts, uploads, sending


def _draft_out(d: MailDraft) -> DraftOut:
    return DraftOut(
        id=d.id, account_id=d.account_id, mode=d.mode, source_message_id=d.source_message_id,
        to=d.to_addrs, cc=d.cc_addrs, bcc=d.bcc_addrs, subject=d.subject, body_html=d.body_html,
        upload_ids=[str(x) for x in d.attachment_ids], updated_at=d.updated_at,
    )  # fmt: skip


def _own_uploads(db: Session, p: Principal, ids: list[uuid.UUID]) -> list[MailUpload]:
    if not ids:
        return []
    ups = list(
        db.scalars(
            select(MailUpload).where(
                MailUpload.id.in_(ids), MailUpload.employee_id == p.employee.id
            )
        )
    )
    if len(ups) != len(set(ids)):
        raise ApiError(422, "VALIDATION", "An attached file was not found")
    return ups


@router.get("/accounts/{account_id}/drafts", response_model=list[DraftOut])
def list_drafts(
    account_id: uuid.UUID, request: Request, db: DbDep, p: Principal = Use
) -> list[DraftOut]:
    access.account(db, request, p, account_id)
    ds = db.scalars(
        select(MailDraft)
        .where(
            MailDraft.account_id == account_id,
            MailDraft.employee_id == p.employee.id,
            MailDraft.sent_outgoing_id.is_(None),
            MailDraft.discarded_at.is_(None),
        )
        .order_by(MailDraft.updated_at.desc())
        .limit(200)
    )
    return [_draft_out(d) for d in ds]


def _apply_draft(db: Session, d: MailDraft, body: DraftIn, p: Principal) -> None:
    _own_uploads(db, p, body.upload_ids)
    d.mode, d.source_message_id = body.mode, body.source_message_id
    d.to_addrs = [a.model_dump() for a in body.to]
    d.cc_addrs = [a.model_dump() for a in body.cc]
    d.bcc_addrs = [a.model_dump() for a in body.bcc]
    d.subject, d.body_html = body.subject, body.body_html
    d.attachment_ids = [str(x) for x in body.upload_ids]
    d.updated_at = now()


@router.post("/drafts", status_code=201, response_model=DraftOut)
def create_draft(body: DraftIn, request: Request, db: DbDep, p: Principal = Use) -> DraftOut:
    access.account(db, request, p, body.account_id, send=True)
    if body.source_message_id:
        access.message(db, request, p, body.source_message_id)
    d = MailDraft(company_id=p.company_id, account_id=body.account_id, employee_id=p.employee.id)
    _apply_draft(db, d, body, p)
    db.add(d)
    db.commit()
    return _draft_out(d)


def _own_draft(db: Session, request: Request, p: Principal, draft_id: uuid.UUID) -> MailDraft:
    d = db.get(MailDraft, draft_id, with_for_update=True)
    if d is None or d.employee_id != p.employee.id or d.discarded_at is not None:
        raise ApiError(404, "NOT_FOUND", "Draft not found")
    access.account(db, request, p, d.account_id, send=True)
    if d.sent_outgoing_id is not None:
        raise ApiError(409, "ALREADY_SENT", "This draft was already sent")
    return d


@router.put("/drafts/{draft_id}", response_model=DraftOut)
def save_draft(
    draft_id: uuid.UUID, body: DraftIn, request: Request, db: DbDep, p: Principal = Use
) -> DraftOut:
    """Autosave. The mailbox can't be changed by saving (it is the sender identity)."""
    d = _own_draft(db, request, p, draft_id)
    if body.account_id != d.account_id:
        access.account(db, request, p, body.account_id, send=True)
        d.account_id = body.account_id
    _apply_draft(db, d, body, p)
    db.commit()
    return _draft_out(d)


@router.post("/drafts/{draft_id}/discard", status_code=204)
def discard_draft(draft_id: uuid.UUID, request: Request, db: DbDep, p: Principal = Use) -> Response:
    """Hide an unsent Pro Trading draft (kept in the database; Lark is not touched)."""
    d = _own_draft(db, request, p, draft_id)
    d.discarded_at = now()
    db.commit()
    return Response(status_code=204)


@router.post("/uploads", status_code=201, response_model=UploadOut)
async def upload(
    request: Request,
    db: DbDep,
    p: Principal = Use,
    filename: Annotated[str, Query(min_length=1, max_length=255)] = "file",
) -> UploadOut:
    declared = int(request.headers.get("content-length") or 0)
    if declared > UPLOAD_MAX:
        raise ApiError(413, "FILE_TOO_LARGE", "Files can be at most 25 MB", max_bytes=UPLOAD_MAX)
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > UPLOAD_MAX:
            raise ApiError(
                413, "FILE_TOO_LARGE", "Files can be at most 25 MB", max_bytes=UPLOAD_MAX
            )
    data = bytes(buf)
    if not data:
        raise ApiError(422, "EMPTY_FILE", "The file is empty")
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "_", filename).strip() or "file"
    declared_type = (request.headers.get("content-type") or "application/octet-stream").split(";")[
        0
    ][:128]
    sniffed = detect(data, declared_type)
    mime = declared_type if sniffed == "application/octet-stream" else sniffed
    from ..larkmail.clamd import is_blocked, scan

    if is_blocked(name, sniffed):
        raise ApiError(422, "FILE_TYPE_BLOCKED", "Program and script files can't be sent from here")
    status, detail = scan(data)
    if status == "INFECTED":
        raise ApiError(422, "FILE_INFECTED", f"The virus scanner flagged this file: {detail}")
    u = MailUpload(
        company_id=p.company_id, employee_id=p.employee.id, filename=name, mime_type=mime,
        byte_size=len(data), sha256=hashlib.sha256(data).hexdigest(), content=data,
    )  # fmt: skip
    db.add(u)
    db.commit()
    return UploadOut(id=u.id, filename=u.filename, mime_type=u.mime_type, byte_size=u.byte_size)


def _out(db: Session, o: MailOutgoing) -> OutgoingOut:
    return OutgoingOut(
        id=o.id, account_id=o.account_id, status=o.status, subject=o.subject, to=o.to_addrs,
        attempts=o.attempts, last_error=o.last_error, created_at=o.created_at, sent_at=o.sent_at,
        seen_in_sent=o.seen_in_sent_at is not None,
    )  # fmt: skip


@router.post("/send", status_code=202, response_model=OutgoingOut)
def send(
    body: SendIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: Principal = Use
) -> OutgoingOut:
    """Queue one message. The same Idempotency-Key always answers with the same queue row, so
    a double click or a retried request never sends twice."""
    acc = access.account(db, request, p, body.account_id, send=True)
    existing = db.scalar(
        select(MailOutgoing).where(
            MailOutgoing.account_id == acc.id, MailOutgoing.idempotency_key == key
        )
    )
    if existing is not None:
        if existing.employee_id != p.employee.id:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "This key was used by someone else")
        return _out(db, existing)
    if acc.smtp_ok_at is None or acc.status in ("AUTH_FAILED", "NOT_CONFIGURED"):
        raise ApiError(
            409, "SENDING_NOT_READY", "This mailbox can't send yet (Lark sign-in not confirmed)"
        )
    if ratelimit.attempt("mail_send", str(p.employee.id), 3600) > 300:
        raise ApiError(429, "RATE_LIMITED", "Too many emails sent this hour")
    rcpts = [*body.to, *body.cc, *body.bcc]
    if not rcpts:
        raise ApiError(422, "NO_RECIPIENT", "Add at least one recipient")
    if len(rcpts) > MAX_RCPTS:
        raise ApiError(422, "TOO_MANY_RECIPIENTS", f"At most {MAX_RCPTS} recipients")
    src = access.message(db, request, p, body.source_message_id) if body.source_message_id else None
    if src is not None and src.account_id != acc.id:
        raise ApiError(422, "VALIDATION", "Reply from the mailbox the email arrived in")
    files = [
        OutFile(u.filename, u.mime_type, u.content) for u in _own_uploads(db, p, body.upload_ids)
    ]
    for aid in body.forward_attachment_ids:
        a = db.get(MailAttachment, aid)
        if a is None or src is None or a.message_id != src.id:
            raise ApiError(422, "VALIDATION", "Forwarded files must come from the original email")
        blob = db.get(MailAttachmentBlob, a.id)
        if blob is None or a.scan_status in ("BLOCKED", "INFECTED"):
            raise ApiError(422, "ATTACHMENT_BLOCKED", f"{a.filename} can't be forwarded")
        files.append(OutFile(a.filename, a.detected_mime or a.declared_mime, blob.content))
    if sum(len(f.content) for f in files) > 25 * 1024 * 1024:
        raise ApiError(422, "TOO_LARGE", "Attachments can be at most 25 MB in total")
    body_html = body.body_html
    raw, mid = build(
        acc,
        to=[a.model_dump() for a in body.to], cc=[a.model_dump() for a in body.cc],
        bcc=[a.model_dump() for a in body.bcc], subject=body.subject, body_html=body_html,
        files=files, reply_to_msg=src if body.mode in ("reply", "reply_all") else None,
    )  # fmt: skip
    o = MailOutgoing(
        company_id=p.company_id, account_id=acc.id, employee_id=p.employee.id,
        idempotency_key=key, mode=body.mode, source_message_id=src.id if src else None,
        message_id_header=mid, from_addr=acc.address,
        to_addrs=[a.model_dump() for a in body.to], cc_addrs=[a.model_dump() for a in body.cc],
        bcc_addrs=[a.model_dump() for a in body.bcc], subject=body.subject, raw_mime=raw,
    )  # fmt: skip
    db.add(o)
    db.flush()
    if body.draft_id:
        d = _own_draft(db, request, p, body.draft_id)
        d.sent_outgoing_id = o.id
    audit.record(
        db, action="mail.send_queued", success=True, company_id=p.company_id,
        actor_id=p.employee.id, target_type="mail_outgoing", target_id=str(o.id),
        request_id=request.state.request_id,
        detail={"account": acc.address, "recipients": len(rcpts), "mode": body.mode},
    )  # fmt: skip
    db.commit()
    return _out(db, o)


@router.get("/accounts/{account_id}/outgoing", response_model=list[OutgoingOut])
def outgoing(
    account_id: uuid.UUID, request: Request, db: DbDep, p: Principal = Use
) -> list[OutgoingOut]:
    access.account(db, request, p, account_id)
    rows = db.scalars(
        select(MailOutgoing)
        .where(MailOutgoing.account_id == account_id, MailOutgoing.employee_id == p.employee.id)
        .order_by(MailOutgoing.created_at.desc())
        .limit(50)
    )
    return [_out(db, o) for o in rows]


class RetryIn(BaseModel):
    confirm_may_duplicate: bool = False


@router.post("/outgoing/{outgoing_id}/retry", response_model=OutgoingOut)
def retry(
    outgoing_id: uuid.UUID, body: RetryIn, request: Request, db: DbDep, p: Principal = Use
) -> OutgoingOut:
    """A person decides to send a FAILED or UNCERTAIN message again (UNCERTAIN needs an explicit
    "it may arrive twice" confirmation)."""
    o = db.get(MailOutgoing, outgoing_id, with_for_update=True)
    if o is None or o.employee_id != p.employee.id:
        raise ApiError(404, "NOT_FOUND", "Not found")
    access.account(db, request, p, o.account_id, send=True)
    if o.status not in ("FAILED", "UNCERTAIN"):
        raise ApiError(409, "NOT_RETRYABLE", "Only failed or uncertain messages can be sent again")
    if o.status == "UNCERTAIN" and not body.confirm_may_duplicate:
        raise ApiError(409, "MAY_DUPLICATE", "It may already have been sent: check Sent first")
    o.status, o.next_attempt_at, o.attempts = "QUEUED", None, 0
    audit.record(
        db, action="mail.send_retry", success=True, company_id=p.company_id,
        actor_id=p.employee.id, target_type="mail_outgoing", target_id=str(o.id),
        request_id=request.state.request_id,
    )  # fmt: skip
    db.commit()
    return _out(db, o)


# ================================================================== ERP links

_LINK_PERM = {
    "customer": "catalog.read",
    "vendor": "catalog.read",
    "sales_order": "sales_order.read",
    "invoice": "invoice.read",
    "shipment": "grn.record",
    "payment": "payment.record",
}


def _entity_label(db: Session, t: str, i: uuid.UUID) -> tuple[str, str]:
    if t == "customer" and (c := db.get(Customer, i)):
        return c.name, f"/customers/{i}"
    if t == "vendor" and (v := db.get(Vendor, i)):
        return v.name, f"/suppliers/{i}"
    if t == "sales_order" and (o := db.get(SalesOrder, i)):
        return f"Order {o.order_no}", f"/orders/{i}"
    if t == "invoice" and (inv := db.get(Invoice, i)):
        return f"Invoice {inv.invoice_no or 'draft'}", f"/invoices/{i}"
    if t == "shipment" and (s := db.get(Shipment, i)):
        return f"Receiving {s.shipment_no}", f"/receiving/{i}"
    if t == "payment" and (pay := db.get(Payment, i)):
        return f"Payment {pay.payment_no}", f"/payments/{i}"
    return "(record not found)", "#"


_ENTITY = {
    "customer": Customer, "vendor": Vendor, "sales_order": SalesOrder, "invoice": Invoice,
    "shipment": Shipment, "payment": Payment,
}  # fmt: skip


@router.post("/messages/{message_id}/links", status_code=201, response_model=LinkOut)
def add_link(
    message_id: uuid.UUID, body: LinkIn, request: Request, db: DbDep, p: Principal = Use
) -> LinkOut:
    """Explicitly connect an email to an ERP record. Changes nothing in that record."""
    m = access.message(db, request, p, message_id)
    authorize(request, p, _LINK_PERM[body.entity_type], any_scope=True)
    ent = db.get(_ENTITY[body.entity_type], body.entity_id)
    if ent is None or getattr(ent, "company_id", None) != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Record not found")
    exists = db.scalar(
        select(MailLink).where(
            MailLink.message_id == m.id, MailLink.entity_type == body.entity_type,
            MailLink.entity_id == body.entity_id, MailLink.removed_at.is_(None),
        )
    )  # fmt: skip
    if exists is None:
        exists = MailLink(company_id=p.company_id, message_id=m.id, entity_type=body.entity_type,
                          entity_id=body.entity_id, note=body.note, created_by=p.employee.id)  # fmt: skip  # noqa: E501
        db.add(exists)
        db.flush()
        audit.record(
            db, action="mail.link", success=True, company_id=p.company_id, actor_id=p.employee.id,
            target_type=body.entity_type, target_id=str(body.entity_id),
            request_id=request.state.request_id, detail={"message": str(m.id)},
        )  # fmt: skip
    db.commit()
    return next(link for link in _links(db, m.id) if link.id == exists.id)


@router.post("/links/{link_id}/unlink", status_code=204)
def unlink(link_id: uuid.UUID, request: Request, db: DbDep, p: Principal = Use) -> Response:
    """Mark a link as removed (the row stays for the audit trail)."""
    link = db.get(MailLink, link_id, with_for_update=True)
    if link is None or link.removed_at is not None:
        raise ApiError(404, "NOT_FOUND", "Link not found")
    access.message(db, request, p, link.message_id)
    link.removed_at, link.removed_by = now(), p.employee.id
    audit.record(
        db, action="mail.unlink", success=True, company_id=p.company_id, actor_id=p.employee.id,
        target_type=link.entity_type, target_id=str(link.entity_id),
        request_id=request.state.request_id, detail={"message": str(link.message_id)},
    )  # fmt: skip
    db.commit()
    return Response(status_code=204)


@router.get("/link-targets", response_model=list[LinkTarget])
def link_targets(
    request: Request,
    db: DbDep,
    type: Annotated[
        Literal["customer", "vendor", "sales_order", "invoice", "shipment", "payment"], Query()
    ],  # noqa: A002
    q: Annotated[str, Query(min_length=1, max_length=100)],
    p: Principal = Use,
) -> list[LinkTarget]:
    authorize(request, p, _LINK_PERM[type], any_scope=True)
    like = f"%{esc(q.strip())}%"
    model = _ENTITY[type]
    col = {
        "customer": Customer.name, "vendor": Vendor.name, "sales_order": SalesOrder.order_no,
        "invoice": Invoice.invoice_no, "shipment": Shipment.shipment_no, "payment": Payment.payment_no,  # noqa: E501
    }[type]  # fmt: skip
    rows = db.scalars(
        select(model).where(model.company_id == p.company_id, col.ilike(like)).limit(15)  # type: ignore[attr-defined]
    )
    out = []
    for r in rows:
        rid: uuid.UUID = r.id  # type: ignore[attr-defined]
        label, url = _entity_label(db, type, rid)
        out.append(LinkTarget(entity_type=type, entity_id=rid, label=label, sub=url))
    return out


# ================================================================== Super Admin: Email Services

admin = APIRouter(prefix="/mail/admin", tags=["email-admin"])


class AdminAccountOut(BaseModel):
    id: uuid.UUID
    address: str
    label: str
    display_name: str
    kind: str
    status: str
    has_password: bool
    password_set_at: datetime | None
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    username: str
    imap_ok_at: datetime | None
    smtp_ok_at: datetime | None
    imap_error: str | None
    smtp_error: str | None
    sync_enabled: bool
    sync_interval_seconds: int
    last_sync_at: datetime | None
    last_sync_attempt_at: datetime | None
    next_sync_at: datetime | None
    syncing: bool
    failure_count: int
    last_error_code: str | None
    last_error: str | None
    history_complete: bool
    message_count: int
    unread_count: int
    capabilities: list[str]
    idle_supported: bool
    users: list[dict[str, Any]]
    health: str
    signature_html: str


class AccountCreateIn(BaseModel):
    address: str = Field(max_length=320)
    display_name: str = Field(default="", max_length=200)
    label: str = Field(default="", max_length=60)
    kind: Literal["PERSONAL", "SHARED"] = "PERSONAL"
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class AccountPatchIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=60)
    kind: Literal["PERSONAL", "SHARED"] | None = None
    sync_interval_seconds: int | None = Field(default=None, ge=60, le=86400)
    signature_html: str | None = Field(default=None, max_length=20_000)
    username: str | None = Field(default=None, max_length=320)


class PasswordIn(BaseModel):
    password: str | None = Field(default=None, min_length=4, max_length=256)


class TestOut(BaseModel):
    imap_ok: bool
    smtp_ok: bool
    imap_error: str | None
    smtp_error: str | None
    folders: list[dict[str, str]]
    idle_supported: bool
    saved: bool = False


class AccessIn(BaseModel):
    grants: list[dict[str, Any]] = Field(max_length=20)  # [{employee_id, can_send}]


class RunOut(BaseModel):
    id: uuid.UUID
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    new_messages: int
    backfilled: int
    folders: int
    error_code: str | None
    error: str | None


def _health(a: MailAccount) -> str:
    if a.status == "NOT_CONFIGURED":
        return "not_configured"
    if a.status == "PAUSED":
        return "paused"
    if a.status == "AUTH_FAILED":
        return "password_rejected"
    if a.status == "ERROR":
        return "failing"
    if a.last_sync_at and a.last_sync_at < now() - timedelta(
        seconds=a.sync_interval_seconds * 5 + 600
    ):
        return "stale"
    return "healthy"


def _admin_out(db: Session, a: MailAccount) -> AdminAccountOut:
    users = [
        {
            "employee_id": str(e.id),
            "name": e.display_name,
            "email": e.email,
            "can_send": g.can_send,
            "active": e.active,
        }  # fmt: skip
        for g, e in db.execute(
            select(MailAccess, Employee)
            .join(Employee, Employee.id == MailAccess.employee_id)
            .where(MailAccess.account_id == a.id, MailAccess.revoked_at.is_(None))
            .order_by(Employee.display_name)
        ).all()
    ]
    t = now()
    return AdminAccountOut(
        id=a.id, address=a.address, label=a.label, display_name=a.display_name, kind=a.kind,
        status=a.status, has_password=a.password_enc is not None, password_set_at=a.password_set_at,
        imap_host=a.imap_host, imap_port=a.imap_port, smtp_host=a.smtp_host, smtp_port=a.smtp_port,
        username=a.username, imap_ok_at=a.imap_ok_at, smtp_ok_at=a.smtp_ok_at,
        imap_error=a.imap_error, smtp_error=a.smtp_error, sync_enabled=a.sync_enabled,
        sync_interval_seconds=a.sync_interval_seconds, last_sync_at=a.last_sync_at,
        last_sync_attempt_at=a.last_sync_attempt_at, next_sync_at=a.next_sync_at,
        syncing=bool(a.sync_lease_until and a.sync_lease_until > t), failure_count=a.failure_count,
        last_error_code=a.last_error_code, last_error=a.last_error,
        history_complete=a.backfill_done_at is not None, message_count=a.message_count,
        unread_count=a.unread_count, capabilities=[str(c) for c in a.capabilities][:60],
        idle_supported="IDLE" in a.capabilities, users=users, health=_health(a),
        signature_html=a.signature_html,
    )  # fmt: skip


def _admin_acc(db: Session, p: Principal, account_id: uuid.UUID, lock: bool = False) -> MailAccount:
    a = db.get(MailAccount, account_id, with_for_update=lock, populate_existing=lock)
    if a is None or a.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Mailbox not found")
    return a


def _audit(
    db: Session, request: Request, p: Principal, action: str, a: MailAccount, **detail: Any
) -> None:
    audit.record(
        db, action=action, success=True, company_id=p.company_id, actor_id=p.employee.id,
        target_type="mail_account", target_id=str(a.id), request_id=request.state.request_id,
        detail={"address": a.address, **detail},
    )  # fmt: skip


@admin.get("/accounts", response_model=list[AdminAccountOut])
def admin_accounts(db: DbDep, p: Principal = Manage) -> list[AdminAccountOut]:
    rows = db.scalars(
        select(MailAccount)
        .where(MailAccount.company_id == p.company_id)
        .order_by(MailAccount.label)
    )
    return [_admin_out(db, a) for a in rows]


@admin.post("/accounts", status_code=201, response_model=AdminAccountOut)
def admin_create(
    body: AccountCreateIn, request: Request, db: DbDep, p: Principal = Manage
) -> AdminAccountOut:
    s = get_settings()
    addr = body.address.strip().lower()
    if not re.match(EMAIL_RE, addr):
        raise ApiError(422, "VALIDATION", "Enter a valid email address")
    if s.lark_mail_domain and not addr.endswith("@" + s.lark_mail_domain.lower()):
        raise ApiError(422, "WRONG_DOMAIN", f"Only @{s.lark_mail_domain} mailboxes can be added")
    if db.scalar(select(MailAccount.id).where(MailAccount.company_id == p.company_id,
                                              func.lower(MailAccount.address) == addr)):  # fmt: skip  # noqa: E501
        raise ApiError(409, "EXISTS", "This mailbox is already set up (one connection per mailbox)")
    a = MailAccount(
        company_id=p.company_id, address=addr, display_name=body.display_name.strip(),
        label=body.label.strip() or addr.split("@")[0].title() + " Mail", kind=body.kind,
        imap_host=s.lark_imap_host, imap_port=s.lark_imap_port, smtp_host=s.lark_smtp_host,
        smtp_port=s.lark_smtp_port, username=addr, created_by=p.employee.id,
    )  # fmt: skip
    db.add(a)
    db.flush()
    _set_access(
        db, request, p, a, [{"employee_id": str(u), "can_send": True} for u in body.user_ids]
    )
    _audit(db, request, p, "mail.account_create", a, kind=a.kind)
    db.commit()
    return _admin_out(db, a)


@admin.patch("/accounts/{account_id}", response_model=AdminAccountOut)
def admin_patch(
    account_id: uuid.UUID, body: AccountPatchIn, request: Request, db: DbDep, p: Principal = Manage
) -> AdminAccountOut:
    a = _admin_acc(db, p, account_id, lock=True)
    changes = body.model_dump(exclude_none=True)
    if "signature_html" in changes:
        from ..larkmail.sanitize import sanitize

        changes["signature_html"] = sanitize(changes["signature_html"], 20_000)[0]
    if body.kind == "PERSONAL" and _live_grants(db, a.id) > 1:
        raise ApiError(422, "PERSONAL_ONE_USER", "A personal mailbox belongs to one person")
    for k, v in changes.items():
        setattr(a, k, v)
    a.version += 1
    _audit(db, request, p, "mail.account_update", a, fields=sorted(changes))
    db.commit()
    return _admin_out(db, a)


def _live_grants(db: Session, account_id: uuid.UUID) -> int:
    return int(db.scalar(select(func.count()).where(MailAccess.account_id == account_id,
                                                     MailAccess.revoked_at.is_(None))) or 0)  # fmt: skip  # noqa: E501


def _limit_tests(p: Principal) -> None:
    if ratelimit.attempt("mail_test", str(p.employee.id), 600) > 20:
        raise ApiError(429, "RATE_LIMITED", "Too many connection tests; wait a few minutes")


@admin.post("/accounts/{account_id}/test", response_model=TestOut)
def admin_test(
    account_id: uuid.UUID, body: PasswordIn, request: Request, db: DbDep, p: Principal = Manage
) -> TestOut:
    """Log in to IMAP and SMTP with the typed password (not saved) or the saved one. Sends no
    email. The mailbox is only ever shown as connected after Lark accepted the login."""
    _limit_tests(p)
    a = _admin_acc(db, p, account_id)
    pw = body.password
    if pw is None:
        if a.password_enc is None:
            raise ApiError(422, "NO_PASSWORD", "Type the Lark app password first")
        try:
            pw = unseal(a.password_enc)
        except SecretUnavailable as exc:
            raise ApiError(409, "SECRET_UNAVAILABLE", str(exc)) from None
    r = test_connection(a, pw)
    del pw
    _audit(db, request, p, "mail.account_test", a, imap_ok=r.imap_ok, smtp_ok=r.smtp_ok)
    if body.password is None:  # testing the saved password updates its health
        _record_test(a, r)
    db.commit()
    return TestOut(imap_ok=r.imap_ok, smtp_ok=r.smtp_ok, imap_error=r.imap_error,
                   smtp_error=r.smtp_error, folders=r.folders, idle_supported=r.idle)  # fmt: skip


def _record_test(a: MailAccount, r: Any) -> None:
    t = now()
    if r.imap_ok:
        a.imap_ok_at, a.imap_error = t, None
        if a.status in ("AUTH_FAILED", "ERROR", "NOT_CONFIGURED"):
            a.status = "CONNECTED"
            a.failure_count = 0
    else:
        a.imap_error = r.imap_error
        if r.imap_auth_failed and a.status != "NOT_CONFIGURED":
            a.status = "AUTH_FAILED"
    if r.smtp_ok:
        a.smtp_ok_at, a.smtp_error = t, None
    else:
        a.smtp_error = r.smtp_error
        if r.smtp_auth_failed:
            a.smtp_ok_at = None
    a.capabilities = list(r.capabilities)[:100] if r.imap_ok else a.capabilities


@admin.post("/accounts/{account_id}/credentials", response_model=TestOut)
def admin_credentials(
    account_id: uuid.UUID, body: PasswordIn, request: Request, db: DbDep, p: Principal = Manage
) -> TestOut:
    """Test the typed Lark app password and save it (encrypted) only if Lark accepted the IMAP
    login. Rotation: saving a new password keeps every imported email."""
    _limit_tests(p)
    if not body.password:
        raise ApiError(422, "NO_PASSWORD", "Type the Lark app password")
    a = _admin_acc(db, p, account_id, lock=True)
    r = test_connection(a, body.password)
    _record_test(a, r)
    saved = False
    if r.imap_ok:
        try:
            a.password_enc = seal(body.password)
        except SecretUnavailable as exc:
            raise ApiError(409, "SECRET_UNAVAILABLE", str(exc)) from None
        a.password_set_at, a.password_set_by = now(), p.employee.id
        a.status = "CONNECTED" if a.status != "PAUSED" else "PAUSED"
        if a.status == "CONNECTED":
            a.sync_enabled = True
        a.next_sync_at = now()
        a.failure_count, a.last_error, a.last_error_code = 0, None, None
        saved = True
    _audit(db, request, p, "mail.credentials_saved" if saved else "mail.credentials_rejected", a,
           imap_ok=r.imap_ok, smtp_ok=r.smtp_ok)  # fmt: skip
    db.commit()
    return TestOut(imap_ok=r.imap_ok, smtp_ok=r.smtp_ok, imap_error=r.imap_error,
                   smtp_error=r.smtp_error, folders=r.folders, idle_supported=r.idle, saved=saved)  # fmt: skip  # noqa: E501


@admin.post("/accounts/{account_id}/sync", response_model=AdminAccountOut)
def admin_sync_now(
    account_id: uuid.UUID, request: Request, db: DbDep, p: Principal = Manage
) -> AdminAccountOut:
    a = _admin_acc(db, p, account_id, lock=True)
    if a.status not in ("CONNECTED", "ERROR"):
        raise ApiError(409, "NOT_CONNECTED", "Connect the mailbox first (or resume it)")
    a.next_sync_at, a.sync_enabled = now(), True
    _audit(db, request, p, "mail.sync_now", a)
    db.commit()
    return _admin_out(db, a)


@admin.post("/accounts/{account_id}/pause", response_model=AdminAccountOut)
def admin_pause(
    account_id: uuid.UUID, request: Request, db: DbDep, p: Principal = Manage
) -> AdminAccountOut:
    a = _admin_acc(db, p, account_id, lock=True)
    if a.status == "NOT_CONFIGURED":
        raise ApiError(409, "NOT_CONNECTED", "Nothing to pause yet")
    a.status, a.sync_enabled = "PAUSED", False
    _audit(db, request, p, "mail.sync_paused", a)
    db.commit()
    return _admin_out(db, a)


@admin.post("/accounts/{account_id}/resume", response_model=AdminAccountOut)
def admin_resume(
    account_id: uuid.UUID, request: Request, db: DbDep, p: Principal = Manage
) -> AdminAccountOut:
    a = _admin_acc(db, p, account_id, lock=True)
    if a.status != "PAUSED" or a.password_enc is None:
        raise ApiError(409, "NOT_PAUSED", "This mailbox is not paused")
    # Back to CONNECTED only after Lark accepts the login on the next sync (ERROR until then).
    a.status, a.sync_enabled, a.next_sync_at = "ERROR", True, now()
    a.last_error = "Resumed; waiting for the next sync to confirm the connection"
    _audit(db, request, p, "mail.sync_resumed", a)
    db.commit()
    return _admin_out(db, a)


@admin.post("/accounts/{account_id}/reconnect", response_model=TestOut)
def admin_reconnect(
    account_id: uuid.UUID, request: Request, db: DbDep, p: Principal = Manage
) -> TestOut:
    """Try the saved password again (after Lark had refused it or the server was down)."""
    out = admin_test(account_id, PasswordIn(password=None), request, db, p)
    a = _admin_acc(db, p, account_id, lock=True)
    if out.imap_ok:
        a.sync_enabled, a.next_sync_at = True, now()
    _audit(db, request, p, "mail.reconnect", a, imap_ok=out.imap_ok)
    db.commit()
    return out


def _set_access(
    db: Session, request: Request, p: Principal, a: MailAccount, grants: list[dict[str, Any]]
) -> None:
    wanted: dict[uuid.UUID, bool] = {}
    for g in grants:
        try:
            wanted[uuid.UUID(str(g["employee_id"]))] = bool(g.get("can_send", True))
        except (KeyError, ValueError):
            raise ApiError(422, "VALIDATION", "Each grant needs an employee_id") from None
    if a.kind == "PERSONAL" and len(wanted) > 1:
        raise ApiError(
            422, "PERSONAL_ONE_USER", "A personal mailbox belongs to one person; make it Shared"
        )
    if wanted:
        emps = db.scalars(select(Employee).where(Employee.id.in_(wanted))).all()
        if len(emps) != len(wanted) or any(
            e.company_id != p.company_id or not e.active for e in emps
        ):
            raise ApiError(
                422, "VALIDATION", "Mailboxes can only be given to active users of this company"
            )
    live = {g.employee_id: g for g in db.scalars(
        select(MailAccess).where(MailAccess.account_id == a.id, MailAccess.revoked_at.is_(None)))}  # fmt: skip  # noqa: E501
    for emp_id, grant in live.items():
        if emp_id not in wanted:
            grant.revoked_at, grant.revoked_by = now(), p.employee.id
            _audit(db, request, p, "mail.access_revoked", a, employee_id=str(emp_id))
        elif grant.can_send != wanted[emp_id]:
            grant.can_send = wanted[emp_id]
            _audit(
                db,
                request,
                p,
                "mail.access_changed",
                a,
                employee_id=str(emp_id),
                can_send=grant.can_send,
            )
    for emp_id, can_send in wanted.items():
        if emp_id not in live:
            db.add(MailAccess(company_id=p.company_id, account_id=a.id, employee_id=emp_id,
                              can_send=can_send, granted_by=p.employee.id))  # fmt: skip
            _audit(
                db, request, p, "mail.access_granted", a, employee_id=str(emp_id), can_send=can_send
            )
    db.flush()


@admin.put("/accounts/{account_id}/access", response_model=AdminAccountOut)
def admin_access(
    account_id: uuid.UUID, body: AccessIn, request: Request, db: DbDep, p: Principal = Manage
) -> AdminAccountOut:
    a = _admin_acc(db, p, account_id, lock=True)
    _set_access(db, request, p, a, body.grants)
    db.commit()
    return _admin_out(db, a)


@admin.get("/accounts/{account_id}/runs", response_model=list[RunOut])
def admin_runs(account_id: uuid.UUID, db: DbDep, p: Principal = Manage) -> list[RunOut]:
    _admin_acc(db, p, account_id)
    rows = db.scalars(
        select(MailSyncRun).where(MailSyncRun.account_id == account_id)
        .order_by(MailSyncRun.started_at.desc()).limit(50)
    )  # fmt: skip
    return [RunOut.model_validate(r, from_attributes=True) for r in rows]


@admin.get("/accounts/{account_id}/log", response_model=list[dict[str, Any]])
def admin_log(account_id: uuid.UUID, db: DbDep, p: Principal = Manage) -> list[dict[str, Any]]:
    """Security log: configuration changes, tests, access grants and refused access attempts."""
    a = _admin_acc(db, p, account_id)
    rows = db.execute(
        select(AuditEvent, Employee.display_name)
        .outerjoin(Employee, Employee.id == AuditEvent.actor_id)
        .where(
            AuditEvent.company_id == p.company_id,
            AuditEvent.action.like("mail.%"),
            or_(
                AuditEvent.target_id == str(a.id),
                and_(AuditEvent.action == "mail.access_denied", AuditEvent.target_id == str(a.id)),
            ),  # fmt: skip
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(100)
    ).all()
    return [
        {
            "at": e.created_at.isoformat(),
            "action": e.action,
            "success": e.success,
            "by": who or "system",
            "detail": e.detail_redacted,
        }  # fmt: skip
        for e, who in rows
    ]


@admin.get("/users", response_model=list[dict[str, Any]])
def admin_users(db: DbDep, p: Principal = Manage) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Employee).where(Employee.company_id == p.company_id, Employee.active.is_(True))
        .order_by(Employee.display_name)
    )  # fmt: skip
    return [{"id": str(e.id), "name": e.display_name, "email": e.email} for e in rows]
