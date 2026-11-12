"""Mailbox-level authorization. A mailbox is opened only through an explicit, live mail_access
grant for the signed-in employee: no role (not even Super Admin) reads someone's mail without
one. Every refusal is audited; a mailbox the person may not open answers 404 (no existence leak).
"""

from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..deps import Principal
from ..errors import ApiError
from .models import MailAccess, MailAccount, MailMessage


def accessible_ids(db: Session, p: Principal, *, send: bool = False) -> list[uuid.UUID]:
    q = select(MailAccess.account_id).where(
        MailAccess.employee_id == p.employee.id,
        MailAccess.company_id == p.company_id,
        MailAccess.revoked_at.is_(None),
    )
    if send:
        q = q.where(MailAccess.can_send.is_(True))
    return list(db.scalars(q))


def _denied(request: Request, p: Principal, what: str, target: str) -> ApiError:
    audit.record_independent(
        action="mail.access_denied", success=False, company_id=p.company_id,
        actor_id=p.employee.id, target_type=what, target_id=target,
        request_id=getattr(request.state, "request_id", None),
        detail={"path": f"{request.method} {request.url.path}"},
    )  # fmt: skip
    return ApiError(404, "NOT_FOUND", "Mailbox not found")


def account(
    db: Session, request: Request, p: Principal, account_id: uuid.UUID, *, send: bool = False
) -> MailAccount:
    acc = db.get(MailAccount, account_id)
    if (
        acc is None
        or acc.company_id != p.company_id
        or account_id not in accessible_ids(db, p, send=send)
    ):
        raise _denied(request, p, "mail_account", str(account_id))
    return acc


def message(db: Session, request: Request, p: Principal, message_id: uuid.UUID) -> MailMessage:
    m = db.get(MailMessage, message_id)
    if m is None or m.company_id != p.company_id or m.account_id not in accessible_ids(db, p):
        raise _denied(request, p, "mail_message", str(message_id))
    return m
