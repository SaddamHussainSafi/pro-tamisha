"""Transactional outbox: external side effects are enqueued in the business transaction
and dispatched by the worker. Handlers must be idempotent (at-least-once delivery)."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import OutboxEvent

MAX_ATTEMPTS = 5
Handler = Callable[[OutboxEvent], None]


def enqueue(
    db: Session,
    event_type: str,
    payload: dict[str, Any],
    *,
    company_id: uuid.UUID | None = None,
    aggregate_id: str | None = None,
) -> OutboxEvent:
    ev = OutboxEvent(
        event_type=event_type, payload=payload, company_id=company_id, aggregate_id=aggregate_id
    )
    db.add(ev)
    return ev


def dispatch_pending(db: Session, handlers: dict[str, Handler], limit: int = 50) -> int:
    """Process a batch; safe with concurrent workers (SKIP LOCKED). Returns processed count."""
    events = db.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.status == "PENDING")
        .order_by(OutboxEvent.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    done = 0
    for ev in events:
        handler = handlers.get(ev.event_type)
        ev.attempts += 1
        try:
            if handler is None:
                raise LookupError(f"no handler for {ev.event_type}")
            handler(ev)
            ev.status, ev.processed_at, ev.last_error = "PROCESSED", datetime.now(UTC), None
            done += 1
        except Exception as exc:  # noqa: BLE001 - recorded, retried
            ev.last_error = f"{type(exc).__name__}: {exc}"[:500]
            if ev.attempts >= MAX_ATTEMPTS:
                ev.status = "FAILED"
                if ev.event_type not in OWN_ALERTS:
                    _alert_failed(db, ev)
    db.commit()
    return done


# Jobs that raise their own, more specific notification when they give up.
OWN_ALERTS = {"invoice.email"}

JOB_LABELS = {
    "invoice.email": "Invoice email",
    "document.drive_sync": "Google Drive copy",
    "payment.reminder": "Payment reminder email",
    "ocr.extract": "OCR reading",
    "notification.email": "Notification email",
    "order_connect.submission_received": "Website order intake",
    "gmail.sync": "Gmail Inbox sync",
}


def _alert_failed(db: Session, ev: OutboxEvent) -> None:
    """A job that gave up after MAX_ATTEMPTS: tell the people who run the company."""
    from .notifications import notify_safely  # notifications imports this module

    label = JOB_LABELS.get(ev.event_type, ev.event_type)
    notify_safely(
        db,
        ev.company_id,
        "system.job_failed",
        event_id=f"system.job_failed:{ev.id}",
        target_type="outbox_event",
        target_id=ev.id,
        category="integration"
        if ev.event_type.endswith(("email", "drive_sync", "reminder"))
        else "system",
        severity="critical",
        source_module="Background jobs",
        title=f"{label} failed",
        body=f"Stopped after {ev.attempts} attempts: {ev.last_error or 'unknown error'}",
        permission="company.manage",
    )
