"""Notification Center: durable per-employee notifications for real business events.

* `notify()` writes the notification and its recipient rows in the caller's transaction, so a
  rolled-back business change never leaves a notification behind.
* Live delivery happens only after COMMIT: the session's `after_commit` hook publishes a wake-up
  on Redis, and the SSE stream (routes/notifications.py) then reads committed rows. A missed
  wake-up (Redis down) is caught by the stream's periodic poll, and a reconnecting tab resumes
  from its last seen `seq`, so nothing is lost while a browser is offline.
* Duplicates are refused by the database: one notification per (company, event_id) and one
  recipient row per (notification, employee).
* Who may see a notification is checked twice: recipients are chosen by permission (and
  warehouse scope) when it is created, and the reader must still hold that permission in the
  current session (MFA-aware) when it is listed, so revoked access hides old rows too.
"""

import json
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import redis
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ColumnElement,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    and_,
    event,
    false,
    or_,
    select,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column, object_session

from . import outbox
from .config import get_settings
from .db import Base
from .models import (
    Company,
    EmailMessage,
    Employee,
    EmployeeRole,
    OutboxEvent,
    RolePermission,
    _created,
    _pk,
)

log = logging.getLogger("pro_trading.notifications")

# ------------------------------------------------------------------ vocabulary

CATEGORIES: dict[str, str] = {
    "website_order": "Website orders",
    "sales_order": "Sales & warehouse",
    "invoice": "Invoices",
    "payment": "Payments & balances",
    "inventory": "Stock & shortages",
    "receiving": "Vendor receiving",
    "scan": "Scans & OCR",
    "integration": "Drive & email",
    "security": "Account & security",
    "system": "Jobs & system",
}
SEVERITIES = ("info", "success", "warning", "critical")

# Event kinds other modules raise by name (Order Connect calls notify() with its kind only).
KINDS: dict[str, tuple[str, str]] = {  # kind -> (category, source module)
    "order_connect.submission": ("website_order", "Website orders"),
}
# Each kind is only shown to employees who still hold this permission when they read it.
KIND_PERMISSION: dict[str, str] = {"order_connect.submission": "order_connect.read"}
# Kinds that go to every holder of the permission, whatever warehouse their grant is for.
# (Website orders are company-wide on purpose: Order Connect only accepts a company-wide
# order_connect.read, so warehouse-scoped staff must not see customer details in an alert.)
KIND_ANY_SCOPE: frozenset[str] = frozenset()

EMAIL_EVENT = "notification.email"
CHANNEL_PREFIX = "notify"


def _in(values: Iterable[str]) -> str:
    return "IN (" + ",".join(f"'{v}'" for v in values) + ")"


# ------------------------------------------------------------------ tables (migration 0017)


class Notification(Base):
    """One business event, written once. Recipients reference it."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("company_id", "event_id", name="uq_notifications_event"),
        CheckConstraint(f"category {_in(CATEGORIES)}", name="category_valid"),
        CheckConstraint(f"severity {_in(SEVERITIES)}", name="severity_valid"),
        CheckConstraint("char_length(event_id) BETWEEN 1 AND 200", name="event_id_len"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 160", name="title_len"),
        CheckConstraint("char_length(body) <= 600", name="body_len"),
        # In-app paths only: "/orders/…", never "//evil.example" or "https://…".
        CheckConstraint("link = '' OR link ~ '^/[A-Za-z0-9]'", name="link_relative"),
        Index("ix_notifications_company_created", "company_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_module: Mapped[str] = mapped_column(String(48), nullable=False)
    link: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    target_type: Mapped[str | None] = mapped_column(String(48))
    target_id: Mapped[str | None] = mapped_column(String(64))
    required_permission: Mapped[str | None] = mapped_column(Text)
    # True: anyone holding required_permission in any warehouse scope may see it (company-level
    # events such as website orders); False: company-wide holders, or this warehouse's.
    any_scope: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = _created()


class NotificationRecipient(Base):
    """Per-employee inbox row. `seq` is the stream cursor (SSE `id:` / Last-Event-ID)."""

    __tablename__ = "notification_recipients"
    __table_args__ = (
        UniqueConstraint("notification_id", "employee_id", name="uq_notification_recipient"),
        Index("ix_notification_recipients_inbox", "employee_id", "seq"),
        Index(
            "ix_notification_recipients_unread",
            "employee_id",
            postgresql_where=text("read_at IS NULL AND archived_at IS NULL"),
        ),
    )
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    notification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (CheckConstraint("version >= 1", name="version_pos"),)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    in_app: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    desktop: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    email: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    sound: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    grouped: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    quiet_hours: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    quiet_start: Mapped[time] = mapped_column(Time, nullable=False, server_default="22:00")
    quiet_end: Mapped[time] = mapped_column(Time, nullable=False, server_default="07:00")
    # {category: {"in_app": bool, "email": bool, "desktop": bool}}; missing = defaults.
    categories: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    updated_at: Mapped[datetime] = _created()


class NotificationDeliveryAttempt(Base):
    """Out-of-app delivery (email). One row per recipient and channel; the outbox retries it."""

    __tablename__ = "notification_delivery_attempts"
    __table_args__ = (
        UniqueConstraint("recipient_seq", "channel", name="uq_notification_delivery"),
        CheckConstraint("channel IN ('email')", name="channel_valid"),
        CheckConstraint("status IN ('PENDING','SENT','FAILED','SKIPPED')", name="status_valid"),
    )
    id: Mapped[uuid.UUID] = _pk()
    recipient_seq: Mapped[int] = mapped_column(
        ForeignKey("notification_recipients.seq", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------------ preferences


@dataclass(frozen=True)
class Prefs:
    in_app: bool = True
    desktop: bool = False
    email: bool = False
    sound: bool = False
    grouped: bool = True
    quiet_hours: bool = False
    quiet_start: time = time(22, 0)
    quiet_end: time = time(7, 0)
    categories: dict[str, dict[str, bool]] | None = None

    def cat(self, category: str, channel: str) -> bool:
        """Per-category switch; every channel defaults to on (the global switch still gates)."""
        return bool(((self.categories or {}).get(category) or {}).get(channel, True))


DEFAULT_PREFS = Prefs()


def prefs_of(row: NotificationPreference | None) -> Prefs:
    if row is None:
        return DEFAULT_PREFS
    return Prefs(
        in_app=row.in_app,
        desktop=row.desktop,
        email=row.email,
        sound=row.sound,
        grouped=row.grouped,
        quiet_hours=row.quiet_hours,
        quiet_start=row.quiet_start,
        quiet_end=row.quiet_end,
        categories=row.categories,
    )


def in_quiet_hours(p: Prefs, now_local: time) -> bool:
    """Quiet hours may cross midnight (22:00–07:00). Equal start and end means no quiet time."""
    if not p.quiet_hours or p.quiet_start == p.quiet_end:
        return False
    t = now_local.replace(tzinfo=None)
    if p.quiet_start < p.quiet_end:
        return p.quiet_start <= t < p.quiet_end
    return t >= p.quiet_start or t < p.quiet_end


def delivers_in_app(p: Prefs, category: str, severity: str) -> bool:
    """Critical alerts always reach the inbox: nobody can mute them, by any switch."""
    if severity == "critical":
        return True
    return p.in_app and p.cat(category, "in_app")


def wants_email(p: Prefs, category: str, severity: str, now_local: time) -> bool:
    if not (p.email and p.cat(category, "email")):
        return False
    return severity == "critical" or not in_quiet_hours(p, now_local)


def alert_for(p: Prefs, category: str, severity: str, now_local: time) -> dict[str, bool]:
    """Hints for an open tab: slide in an in-app banner, show a desktop notification, play a
    sound. Quiet hours hold all three back except for critical alerts; the inbox and the
    unread count are never held back."""
    quiet = severity != "critical" and in_quiet_hours(p, now_local)
    return {
        "banner": not quiet,
        "desktop": not quiet
        and p.desktop
        and (severity == "critical" or p.cat(category, "desktop")),
        "sound": not quiet and p.sound,
    }


def company_now(db: Session, company_id: uuid.UUID) -> datetime:
    tz = db.scalar(select(Company.timezone).where(Company.id == company_id)) or "UTC"
    return datetime.now(ZoneInfo(tz))


# ------------------------------------------------------------------ recipients


def recipients(
    db: Session,
    company_id: uuid.UUID,
    permission: str,
    warehouse_id: uuid.UUID | None = None,
    *,
    any_scope: bool = False,
) -> list[uuid.UUID]:
    """Active employees holding `permission` company-wide or for `warehouse_id` (a company-level
    event, warehouse_id=None, goes to company-wide holders only — the same rule as
    Principal.can). any_scope=True: every holder, whatever the warehouse."""
    scope: ColumnElement[bool]
    if any_scope:
        scope = true()
    elif warehouse_id is None:
        scope = EmployeeRole.warehouse_id.is_(None)
    else:
        scope = or_(EmployeeRole.warehouse_id.is_(None), EmployeeRole.warehouse_id == warehouse_id)
    return list(
        db.scalars(
            select(Employee.id)
            .distinct()
            .join(EmployeeRole, EmployeeRole.employee_id == Employee.id)
            .join(RolePermission, RolePermission.role_id == EmployeeRole.role_id)
            .where(
                Employee.company_id == company_id,
                Employee.active.is_(True),
                RolePermission.permission_code == permission,
                scope,
            )
        ).all()
    )


# ------------------------------------------------------------------ notify


def notify(
    db: Session,
    company_id: uuid.UUID,
    kind: str,
    *,
    title: str,
    body: str = "",
    link: str = "",
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    event_id: str | None = None,
    category: str | None = None,
    severity: str = "info",
    source_module: str | None = None,
    permission: str | None = None,
    warehouse_id: uuid.UUID | None = None,
    to: Iterable[uuid.UUID] | None = None,
    actor_id: uuid.UUID | None = None,
    include_actor: bool = False,
    any_scope: bool | None = None,
) -> int:
    """Record one event and fan it out, in the caller's transaction. Returns the number of
    inboxes it reached (0 for a duplicate event_id).

    to=None sends to every holder of `permission` (warehouse-scoped); otherwise only to the
    listed employees, who must still hold `permission` (if given) when they read it. The actor
    is left out unless include_actor (people are not notified about their own clicks)."""
    if kind in KINDS:
        category = category or KINDS[kind][0]
        source_module = source_module or KINDS[kind][1]
    permission = permission or KIND_PERMISSION.get(kind)
    if any_scope is None:
        any_scope = kind in KIND_ANY_SCOPE
    if category not in CATEGORIES:
        raise ValueError(f"unknown notification category {category!r}")
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}")
    if to is None and permission is None:
        raise ValueError("notify() needs recipients or a permission")
    if event_id is None:
        if target_id is None:
            raise ValueError("notify() needs an event_id or a target_id")
        event_id = f"{kind}:{target_id}"
    if link and not link.startswith("/"):
        raise ValueError("notification links are in-app paths")

    nid = db.scalar(
        insert(Notification)
        .values(
            company_id=company_id,
            event_id=event_id[:200],
            kind=kind[:64],
            category=category,
            severity=severity,
            title=_clip(title, 160) or "Notification",
            body=_clip(body, 600),
            source_module=(source_module or CATEGORIES[category])[:48],
            link=link,
            target_type=target_type,
            target_id=None if target_id is None else str(target_id),
            required_permission=permission,
            any_scope=any_scope,
            warehouse_id=warehouse_id,
            actor_id=actor_id,
        )
        .on_conflict_do_nothing(constraint="uq_notifications_event")
        .returning(Notification.id)
    )
    if nid is None:
        return 0  # this event was already announced

    if to is None:
        assert permission is not None
        people = recipients(db, company_id, permission, warehouse_id, any_scope=any_scope)
    else:
        wanted = set(to)
        people = (
            list(
                db.scalars(
                    select(Employee.id).where(
                        Employee.id.in_(wanted),
                        Employee.company_id == company_id,
                        Employee.active.is_(True),
                    )
                ).all()
            )
            if wanted
            else []
        )
    if actor_id is not None and not include_actor:
        people = [e for e in people if e != actor_id]
    if not people:
        return 0

    prefs = {
        r.employee_id: prefs_of(r)
        for r in db.scalars(
            select(NotificationPreference).where(NotificationPreference.employee_id.in_(people))
        )
    }
    now_local = company_now(db, company_id).time()
    reached = 0
    for emp in sorted(people):
        pr = prefs.get(emp, DEFAULT_PREFS)
        if not delivers_in_app(pr, category, severity):
            continue
        seq = db.scalar(
            insert(NotificationRecipient)
            .values(notification_id=nid, employee_id=emp, company_id=company_id)
            .on_conflict_do_nothing(constraint="uq_notification_recipient")
            .returning(NotificationRecipient.seq)
        )
        if seq is None:
            continue
        reached += 1
        _pending(db).add(emp)
        if wants_email(pr, category, severity, now_local):
            att = NotificationDeliveryAttempt(
                recipient_seq=seq, company_id=company_id, channel="email"
            )
            db.add(att)
            db.flush()
            outbox.enqueue(
                db,
                EMAIL_EVENT,
                {"attempt_id": str(att.id)},
                company_id=company_id,
                aggregate_id=str(nid),
            )
    return reached


def notify_safely(db: Session, company_id: uuid.UUID | None, kind: str, **kw: Any) -> int:
    """notify() inside a SAVEPOINT: a notification problem is logged, never allowed to undo the
    business change that triggered it."""
    if company_id is None:
        return 0
    db.flush()  # business errors surface here, to the caller, as before
    try:
        with db.begin_nested():
            return notify(db, company_id, kind, **kw)
    except Exception:  # noqa: BLE001 - logged; the business transaction carries on
        log.exception("notification failed", extra={"ctx": {"kind": kind}})
        return 0


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ------------------------------------------------------------------ publish after commit

_PENDING_KEY = "pt_notify_pending"


def _pending(db: Session) -> set[uuid.UUID]:
    return db.info.setdefault(_PENDING_KEY, set())  # type: ignore[no-any-return]


def mark_changed(db: Session, employee_id: uuid.UUID) -> None:
    """Read/archive state changed: other open tabs refresh their counts after commit."""
    db.info.setdefault(_PENDING_KEY + ":state", set()).add(employee_id)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    new = session.info.pop(_PENDING_KEY, None)
    state = session.info.pop(_PENDING_KEY + ":state", None)
    if new:
        publish(new, "new")
    if state:
        publish(state - (new or set()), "state")


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)
    session.info.pop(_PENDING_KEY + ":state", None)


def channel(employee_id: uuid.UUID) -> str:
    return f"pt:{get_settings().app_env}:{CHANNEL_PREFIX}:{employee_id}"


@lru_cache
def _redis() -> "redis.Redis":
    return redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.5)


def publish(employee_ids: Iterable[uuid.UUID], what: str) -> None:
    """Best effort: if Redis is down, open streams still pick changes up on their next poll."""
    ids = list(employee_ids)
    if not ids:
        return
    try:
        pipe = _redis().pipeline(transaction=False)
        for e in ids:
            pipe.publish(channel(e), json.dumps({"kind": what}))
        pipe.execute()
    except redis.RedisError as exc:
        log.warning("notification publish failed", extra={"ctx": {"error": type(exc).__name__}})


# ------------------------------------------------------------------ read side


def visible_to(grants: Iterable[tuple[str, uuid.UUID | None]]) -> ColumnElement[bool]:
    """SQL filter: rows the reader may see with these effective (permission, warehouse) grants."""
    wide: set[str] = set()
    scoped: set[tuple[str, uuid.UUID]] = set()
    for perm, wh in grants:
        if wh is None:
            wide.add(perm)
        else:
            scoped.add((perm, wh))
    conds: list[ColumnElement[bool]] = [Notification.required_permission.is_(None)]
    if wide:
        conds.append(Notification.required_permission.in_(sorted(wide)))
    for perm, wh in sorted(scoped, key=str):
        conds.append(
            and_(
                Notification.required_permission == perm,
                or_(Notification.warehouse_id == wh, Notification.any_scope.is_(True)),
            )
        )
    return or_(*conds) if conds else false()


def item_out(r: NotificationRecipient, n: Notification) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "seq": r.seq,
        "kind": n.kind,
        "category": n.category,
        "category_label": CATEGORIES.get(n.category, n.category),
        "severity": n.severity,
        "title": n.title,
        "body": n.body,
        "source_module": n.source_module,
        "link": n.link or None,
        "target_type": n.target_type,
        "target_id": n.target_id,
        "created_at": n.created_at.isoformat(),
        "read": r.read_at is not None,
        "read_at": r.read_at.isoformat() if r.read_at else None,
        "archived": r.archived_at is not None,
    }


def prefs_out(row: NotificationPreference | None, can_mute_security: bool) -> dict[str, Any]:
    p = prefs_of(row)
    return {
        "in_app": p.in_app,
        "desktop": p.desktop,
        "email": p.email,
        "sound": p.sound,
        "grouped": p.grouped,
        "quiet_hours": p.quiet_hours,
        "quiet_start": p.quiet_start.strftime("%H:%M"),
        "quiet_end": p.quiet_end.strftime("%H:%M"),
        "categories": {
            c: {ch: p.cat(c, ch) for ch in ("in_app", "email", "desktop")} for c in CATEGORIES
        },
        "category_labels": CATEGORIES,
        "can_mute_security": can_mute_security,
        "version": row.version if row else 0,
    }


# ------------------------------------------------------------------ email channel (outbox)


def handle_email(ev: OutboxEvent) -> None:
    """Send one notification email. Idempotent: the dedupe key makes a retry a no-op."""
    db = object_session(ev)
    assert db is not None
    att = db.get(
        NotificationDeliveryAttempt,
        uuid.UUID(ev.payload["attempt_id"]),
        with_for_update=True,
        populate_existing=True,
    )
    if att is None:
        raise LookupError("delivery attempt not found")
    if att.status != "PENDING":
        return
    rec = db.get(NotificationRecipient, att.recipient_seq)
    n = db.get(Notification, rec.notification_id) if rec else None
    emp = db.get(Employee, rec.employee_id) if rec else None
    att.attempts += 1
    if rec is None or n is None or emp is None or not emp.active or not emp.email:
        att.status = "SKIPPED"
        return
    try:
        db.execute(
            insert(EmailMessage)
            .values(
                company_id=att.company_id,
                dedupe_key=f"notification:{att.id}",
                to_addr=emp.email,
                subject=f"[Pro Trading] {n.title}",
                body=(
                    f"{n.title}\n\n{n.body}\n\nOpen Pro Trading to see it"
                    f"{' (' + n.link + ')' if n.link else ''}.\n\n"
                    "You get these emails because email alerts are on in Settings → "
                    "Notifications."
                ),
                attachment_sha256=None,
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )
    except Exception as exc:
        att.last_error = f"{type(exc).__name__}: {exc}"[:300]
        if ev.attempts >= outbox.MAX_ATTEMPTS:
            att.status = "FAILED"
        raise
    att.status, att.sent_at, att.last_error = "SENT", datetime.now(ZoneInfo("UTC")), None
