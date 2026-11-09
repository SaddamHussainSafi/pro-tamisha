"""Audit trail. Normal events join the caller's transaction; denials commit independently
so they survive the rolled-back request."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from .db import get_sessionmaker
from .models import AuditEvent

_REDACT = {"password", "code", "token", "csrf_token", "secret", "mfa_secret"}


def redact(detail: dict[str, Any] | None) -> dict[str, Any]:
    return {k: ("[REDACTED]" if k in _REDACT else v) for k, v in (detail or {}).items()}


def record(
    db: Session,
    *,
    action: str,
    success: bool,
    company_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    warehouse_id: uuid.UUID | None = None,
    request_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            action=action,
            success=success,
            company_id=company_id,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            warehouse_id=warehouse_id,
            request_id=request_id,
            detail_redacted=redact(detail),
        )
    )
    # Committed business events become notifications in this same transaction.
    from .notification_rules import AuditInfo, on_audit

    on_audit(
        db,
        AuditInfo(
            action=action,
            success=success,
            company_id=company_id,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            warehouse_id=warehouse_id,
            request_id=request_id,
            detail=detail or {},
        ),
    )


def record_independent(**kwargs: Any) -> None:
    with get_sessionmaker()() as db, db.begin():
        record(db, **kwargs)
