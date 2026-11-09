"""Review workflow, customer matching, privacy erasure and retention."""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..errors import ApiError
from ..models import Customer
from ..notifications import Notification
from . import forms
from .models import (
    OrderConnectSettings,
    OrderFormSubmission,
    OrderFormSubmissionEvent,
)

# Allowed moves. CONVERTED is reached only through conversion and is final.
TRANSITIONS: dict[str, frozenset[str]] = {
    "NEW": frozenset({"REVIEWING", "REJECTED", "DUPLICATE", "SPAM"}),
    "REVIEWING": frozenset({"APPROVED", "REJECTED", "DUPLICATE", "SPAM"}),
    "APPROVED": frozenset({"REVIEWING", "REJECTED"}),
    "REJECTED": frozenset({"REVIEWING"}),
    "DUPLICATE": frozenset({"REVIEWING"}),
    "SPAM": frozenset({"REVIEWING"}),
    "CONVERTED": frozenset(),
}
TERMINAL_FOR_RETENTION = ("CONVERTED", "REJECTED", "DUPLICATE", "SPAM")


def permission_for(current: str, target: str) -> str:
    """Approving (or taking an approval back) needs the approve permission."""
    return "order_connect.approve" if "APPROVED" in (current, target) else "order_connect.review"


def check_transition(current: str, target: str) -> None:
    if current == "CONVERTED":
        raise ApiError(409, "ALREADY_CONVERTED", "This submission is already a sales order")
    if target not in TRANSITIONS[current]:
        raise ApiError(
            409,
            "INVALID_TRANSITION",
            f"A {current.lower()} submission can't become {target.lower()}",
            current_status=current,
        )


def event(
    db: Session,
    sub: OrderFormSubmission,
    kind: str,
    actor: uuid.UUID | None,
    *,
    note: str = "",
    from_status: str | None = None,
    to_status: str | None = None,
) -> None:
    db.add(
        OrderFormSubmissionEvent(
            company_id=sub.company_id,
            submission_id=sub.id,
            kind=kind,
            from_status=from_status,
            to_status=to_status,
            note=note,
            actor_id=actor,
        )
    )


def set_status(
    db: Session, sub: OrderFormSubmission, target: str, actor: uuid.UUID, note: str
) -> None:
    check_transition(sub.status, target)
    old = sub.status
    sub.status = target
    sub.version += 1
    sub.updated_at = forms.now()
    event(db, sub, "STATUS", actor, note=note, from_status=old, to_status=target)


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())[-10:]


def customer_matches(db: Session, sub: OrderFormSubmission, limit: int = 8) -> list[dict[str, Any]]:
    """Existing customers that look like the sender, with the reasons. Staff choose; nothing
    is matched automatically."""
    conds = []
    email = sub.email.strip().lower()
    phone = _digits(sub.phone)
    names = [n.strip().lower() for n in (sub.business_name, sub.customer_name) if n.strip()]
    if email:
        conds.append(func.lower(Customer.email) == email)
    if len(phone) >= 7:
        conds.append(func.right(func.regexp_replace(Customer.phone, r"\D", "", "g"), 10) == phone)
    for n in names:
        conds.append(func.lower(Customer.name).contains(n, autoescape=True))
    if not conds:
        return []
    rows = db.scalars(
        select(Customer)
        .where(Customer.company_id == sub.company_id, or_(*conds))
        .order_by(Customer.active.desc(), Customer.name)
        .limit(50)
    ).all()
    out = []
    for c in rows:
        why = []
        if email and (c.email or "").lower() == email:
            why.append("Same email")
        if len(phone) >= 7 and _digits(c.phone or "") == phone:
            why.append("Same phone")
        cname = c.name.lower()
        if cname in names:
            why.append("Same name")
        elif any(n in cname for n in names):
            why.append("Similar name")
        out.append(
            {
                "id": str(c.id),
                "name": c.name,
                "email": c.email,
                "phone": c.phone,
                "external_ref": c.external_ref,
                "active": c.active,
                "reasons": why,
                "score": 4 * ("Same email" in why)
                + 3 * ("Same phone" in why)
                + 2 * ("Same name" in why)
                + ("Similar name" in why),
            }
        )
    out.sort(key=lambda m: (-int(str(m["score"])), str(m["name"])))
    return out[:limit]


ERASED_RAW = {"erased": True}


def erase(db: Session, sub: OrderFormSubmission, actor: uuid.UUID | None, reason: str) -> None:
    """Blank the sender's personal data (privacy request or retention). Products, status,
    reference and the link to any sales order stay; the sales order keeps its own copy."""
    if sub.erased_at is not None:
        raise ApiError(409, "ALREADY_ERASED", "Personal data was already erased")
    sub.customer_name = sub.business_name = sub.email = sub.phone = ""
    sub.delivery_address = sub.notes = sub.user_agent = sub.ip_hash = ""
    sub.custom = []
    sub.raw = dict(ERASED_RAW)
    sub.erased_at = forms.now()
    sub.version += 1
    sub.updated_at = sub.erased_at
    event(db, sub, "ERASED", actor, note=reason)
    db.execute(
        update(Notification)
        .where(
            Notification.company_id == sub.company_id,
            Notification.target_type == "order_form_submission",
            Notification.target_id == str(sub.id),
        )
        .values(body="Personal data erased")
    )


def purge_expired(db: Session) -> int:
    """Celery beat: erase personal data of finished submissions older than each company's
    retention period. Companies without a retention period keep everything (OC-D5)."""
    done = 0
    for company_id, days in db.execute(
        select(OrderConnectSettings.company_id, OrderConnectSettings.retention_days).where(
            OrderConnectSettings.retention_days.is_not(None)
        )
    ).all():
        if days is None:
            continue
        cutoff = forms.now() - timedelta(days=days)
        subs = db.scalars(
            select(OrderFormSubmission)
            .where(
                OrderFormSubmission.company_id == company_id,
                OrderFormSubmission.erased_at.is_(None),
                OrderFormSubmission.status.in_(TERMINAL_FOR_RETENTION),
                OrderFormSubmission.created_at < cutoff,
            )
            .with_for_update(skip_locked=True)
            .limit(500)
        ).all()
        for sub in subs:
            erase(db, sub, None, f"Retention period of {days} days ended")
            done += 1
    db.commit()
    return done
