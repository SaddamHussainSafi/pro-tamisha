"""Trash bin (migration 0020).

The Super Admin (permission `trash.manage`, with a verified two-step sign-in) moves a record to
the Trash. That removes the record, and the rows that belong to it, from the live tables, so no
other screen or query has to know about the Trash. Every removed row is kept in `trash_items`
until the record is restored, deleted forever, or purged automatically after 30 days.

A record can be trashed only while nothing else references it: every foreign key in the schema
that points at one of the removed rows is counted (cascading keys excepted), plus a few
references that are not foreign keys. Anything found turns into a 409 naming what uses it.
Kind-specific rules (only draft orders, only aborted picking sheets, never invoice PDFs, ...)
are checked here and again by the DB guard triggers, which let a DELETE through only while
`pro_trading.trash` is on for the transaction (set and cleared by `_delete_rows`).

Other staff cannot trash anything; they file a delete request that the Super Admin approves
(the record moves to the Trash) or dismisses.
"""

import base64
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import audit
from .db import Base
from .errors import ApiError
from .models import (
    AuthSession,
    AuthToken,
    Customer,
    CustomerGroup,
    DocumentFile,
    Employee,
    EmployeeRole,
    MfaRecoveryCode,
    PickingAttempt,
    PickingLine,
    PriceBookVersion,
    PriceEntry,
    Product,
    ProductAlias,
    ProductUnitProfile,
    Role,
    SalesOrder,
    SalesOrderAmendment,
    SalesOrderLine,
    ScanJob,
    SourceDocument,
    StockMovement,
    Vendor,
    _created,
    _pk,
)
from .notification_rules import AuditInfo, rule
from .notifications import (
    Notification,
    NotificationPreference,
    NotificationRecipient,
    notify_safely,
)
from .order_connect.models import (
    OrderFormSubmission,
    OrderFormSubmissionEvent,
    OrderFormSubmissionItem,
    OrderFormVersion,
)
from .permissions import SUPER_ADMIN

TRASH_DAYS = 30
TRASH_PERMISSION = "trash.manage"


class TrashItem(Base):
    """A trashed record and every row removed with it (`payload`, in insert order)."""

    __tablename__ = "trash_items"
    __table_args__ = (
        UniqueConstraint("company_id", "kind", "item_id", name="uq_trash_item"),
        CheckConstraint("purge_after > trashed_at", name="purge_after_later"),
        Index("ix_trash_purge_after", "purge_after"),
        Index("ix_trash_company_trashed", "company_id", "trashed_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    trashed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    trashed_at: Mapped[datetime] = _created()
    purge_after: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class DeleteRequest(Base):
    """A staff member asked for a record to be deleted. One PENDING request per record."""

    __tablename__ = "delete_requests"
    __table_args__ = (
        Index(
            "uq_delete_request_pending",
            "company_id",
            "kind",
            "item_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_delete_requests_company_status", "company_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="PENDING")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = _created()
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    decided_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


# ------------------------------------------------------------------ kinds


def _refuse(message: str, **extra: Any) -> ApiError:
    return ApiError(409, "NOT_DELETABLE", message, **extra)


@dataclass(frozen=True)
class Child:
    """Rows removed (and, unless `keep` is False, restored) together with the record."""

    model: Any
    column: str
    where: Any = None
    keep: bool = True


@dataclass(frozen=True)
class Kind:
    key: str
    model: Any
    noun: str
    label: Callable[[Session, Any], str]
    permission: str | None  # who may ask for a delete; None = Super Admin only
    children: tuple[Child, ...] = ()
    rules: Callable[[Session, uuid.UUID, Any], None] | None = None
    extra_refs: Callable[[Session, Any], list[dict[str, Any]]] | None = None
    company: Callable[[Session, Any], uuid.UUID] = lambda _db, o: o.company_id  # noqa: E731
    leftover_doc: Callable[[Any], uuid.UUID | None] | None = None  # source document to take
    prepare: Callable[[Session, Any], None] | None = None  # just before the rows are deleted


def _product_label(db: Session, p: Product) -> str:
    return f"{p.sku} — {p.name}"


def _case_label(db: Session, u: ProductUnitProfile) -> str:
    p = db.get(Product, u.product_id)
    return f"{u.code} ({p.sku if p else '?'})"


def _case_company(db: Session, u: ProductUnitProfile) -> uuid.UUID:
    p = db.get(Product, u.product_id)
    assert p is not None
    return p.company_id


def _case_rules(db: Session, me: uuid.UUID, u: ProductUnitProfile) -> None:
    if u.is_default:
        raise _refuse(
            f"{_case_label(db, u)} is the product's default case size. "
            "Make another case size the default first."
        )


def _price_rules(db: Session, me: uuid.UUID, v: PriceBookVersion) -> None:
    if v.status != "DRAFT":
        raise _refuse(
            f"Price book {v.version_code} is {v.status.lower()}. Only drafts can be deleted; "
            "archive a scheduled price book instead."
        )


def _order_rules(db: Session, me: uuid.UUID, o: SalesOrder) -> None:
    if o.status != "DRAFT":
        raise _refuse(
            f"Order {o.order_no} is {o.status.lower().replace('_', ' ')}. Only draft orders can "
            "be deleted; cancel or close it instead."
        )


def _pick_rules(db: Session, me: uuid.UUID, a: PickingAttempt) -> None:
    if a.status != "ABORTED":
        if a.status in ("CREATED", "IN_PROGRESS", "SUBMITTED", "NEEDS_CORRECTION"):
            raise _refuse(f"Picking sheet {a.pick_code} is still open. Abort it first.")
        raise _refuse(f"Picking sheet {a.pick_code} is {a.status.lower()} and is kept.")


def _pick_refs(db: Session, a: PickingAttempt) -> list[dict[str, Any]]:
    out = []
    n = db.scalar(sa.select(sa.func.count()).where(StockMovement.source_id == a.id)) or 0
    if n:
        out.append(_ref("stock_movements", n))
    n = (
        db.scalar(sa.select(sa.func.count()).where(SalesOrderAmendment.pick_attempt_id == a.id))
        or 0
    )
    if n:
        out.append(_ref("sales_order_amendments", n))
    return out


def _scan_rules(db: Session, me: uuid.UUID, j: ScanJob) -> None:
    if j.status in ("REQUESTED", "SCANNING"):
        raise _refuse(f"Scan {j.job_no} is still open. Cancel it first.")


def _doc_rules(db: Session, me: uuid.UUID, f: DocumentFile) -> None:
    if f.kind == "INVOICE_PDF":
        raise _refuse(f"{f.filename} is an issued invoice's PDF and is kept with the invoice.")


def _user_label(db: Session, e: Employee) -> str:
    return f"{e.display_name} <{e.email}>"


def _user_rules(db: Session, me: uuid.UUID, e: Employee) -> None:
    if e.id == me:
        raise _refuse("You can't delete your own account.")
    is_owner = db.scalar(
        sa.select(sa.func.count())
        .select_from(EmployeeRole)
        .join(Role, Role.id == EmployeeRole.role_id)
        .where(EmployeeRole.employee_id == e.id, Role.code == SUPER_ADMIN)
    )
    if is_owner and e.active and e.password_hash is not None:
        others = db.scalar(
            sa.select(sa.func.count(sa.func.distinct(Employee.id)))
            .join(EmployeeRole, EmployeeRole.employee_id == Employee.id)
            .join(Role, Role.id == EmployeeRole.role_id)
            .where(
                Employee.company_id == e.company_id,
                Employee.id != e.id,
                Employee.active.is_(True),
                Employee.password_hash.is_not(None),
                Role.code == SUPER_ADMIN,
            )
        )
        if not others:
            raise _refuse("The company must keep at least one Super Admin.")


def _entry_label(db: Session, s: OrderFormSubmission) -> str:
    who = s.business_name or s.customer_name
    return f"{s.reference} — {who}" if who else s.reference


def _entry_rules(db: Session, me: uuid.UUID, s: OrderFormSubmission) -> None:
    if s.status == "CONVERTED":
        o = db.get(SalesOrder, s.sales_order_id) if s.sales_order_id else None
        raise _refuse(
            f"{s.reference} became sales order {o.order_no if o else ''}".rstrip()
            + ". Erase its personal data instead."
        )


def _product_refs(db: Session, p: Product) -> list[dict[str, Any]]:
    rows = db.scalars(
        sa.select(OrderFormVersion.title).where(
            OrderFormVersion.company_id == p.company_id,
            OrderFormVersion.product_scope == "PRODUCTS",
            OrderFormVersion.scope_ids.contains([str(p.id)]),
        )
    ).all()
    return [_ref("order_form_versions", len(rows), list(rows[:3]))] if rows else []


def _product_prepare(db: Session, p: Product) -> None:
    # The default-case-size guard (migration 0020) protects a lone case size; the product's
    # own default goes with the product. The snapshot was taken first, so a restore keeps it.
    db.execute(
        sa.update(ProductUnitProfile)
        .where(ProductUnitProfile.product_id == p.id, ProductUnitProfile.is_default.is_(True))
        .values(is_default=False)
    )


KINDS: dict[str, Kind] = {
    k.key: k
    for k in (
        Kind(
            "product",
            Product,
            "product",
            _product_label,
            "catalog.manage",
            children=(
                Child(ProductAlias, "product_id"),
                Child(ProductUnitProfile, "product_id"),
            ),
            extra_refs=_product_refs,
            prepare=_product_prepare,
        ),
        Kind(
            "case_size",
            ProductUnitProfile,
            "case size",
            _case_label,
            "catalog.manage",
            rules=_case_rules,
            company=_case_company,
        ),
        Kind("customer", Customer, "customer", lambda _db, c: c.name, "catalog.manage"),
        Kind(
            "customer_group",
            CustomerGroup,
            "customer group",
            lambda _db, g: f"{g.name} ({g.code})",
            "catalog.manage",
        ),
        Kind("supplier", Vendor, "supplier", lambda _db, v: v.name, "catalog.manage"),
        Kind(
            "price_draft",
            PriceBookVersion,
            "price book draft",
            lambda _db, v: f"Price book {v.version_code}",
            "price.draft",
            children=(Child(PriceEntry, "version_id"),),
            rules=_price_rules,
        ),
        Kind(
            "sales_order",
            SalesOrder,
            "sales order",
            lambda _db, o: f"Order {o.order_no}",
            "sales_order.manage",
            children=(Child(SalesOrderLine, "order_id"),),
            rules=_order_rules,
        ),
        Kind(
            "picking_sheet",
            PickingAttempt,
            "picking sheet",
            lambda _db, a: f"Picking sheet {a.pick_code}",
            "sales_order.manage",
            children=(Child(PickingLine, "attempt_id"),),
            rules=_pick_rules,
            extra_refs=_pick_refs,
        ),
        Kind(
            "scan",
            ScanJob,
            "scan",
            lambda _db, j: f"Scan {j.job_no}",
            "scan.create",
            children=(Child(DocumentFile, "scan_job_id", DocumentFile.kind == "SCAN"),),
            rules=_scan_rules,
            leftover_doc=lambda j: j.document_id,
        ),
        Kind(
            "document",
            DocumentFile,
            "document",
            lambda _db, f: f.filename,
            "document.manage",
            rules=_doc_rules,
            leftover_doc=lambda f: f.source_document_id,
        ),
        Kind(
            "user",
            Employee,
            "user",
            _user_label,
            "employee.manage",
            children=(
                Child(EmployeeRole, "employee_id"),
                Child(MfaRecoveryCode, "employee_id"),
                Child(NotificationPreference, "employee_id"),
                Child(AuthSession, "employee_id", keep=False),
                Child(AuthToken, "employee_id", keep=False),
                Child(NotificationRecipient, "employee_id", keep=False),
            ),
            rules=_user_rules,
        ),
        Kind(
            "notification",
            Notification,
            "notification",
            lambda _db, n: n.title,
            None,
            children=(Child(NotificationRecipient, "notification_id"),),
        ),
        Kind(
            "website_entry",
            OrderFormSubmission,
            "website order entry",
            _entry_label,
            "order_connect.review",
            children=(
                Child(OrderFormSubmissionItem, "submission_id"),
                Child(OrderFormSubmissionEvent, "submission_id"),
            ),
            rules=_entry_rules,
        ),
    )
}

# ------------------------------------------------------------------ rows <-> JSON


def _enc(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime | date | time):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, bytes | memoryview):
        return base64.b64encode(bytes(v)).decode()
    return v


def _dec(col: sa.Column[Any], v: Any) -> Any:
    if v is None:
        return None
    t = col.type
    if isinstance(t, sa.Uuid):
        return uuid.UUID(v)
    if isinstance(t, sa.DateTime):
        return datetime.fromisoformat(v)
    if isinstance(t, sa.Date):
        return date.fromisoformat(v)
    if isinstance(t, sa.Time):
        return time.fromisoformat(v)
    if isinstance(t, sa.Float):
        return float(v)
    if isinstance(t, sa.Numeric):
        return Decimal(v)
    if isinstance(t, sa.LargeBinary):
        return base64.b64decode(v)
    return v


# Columns a restore must not write (generated by the database).
RESTORE_SKIP = {"notification_recipients": {"seq"}}
# Never copied into the audit snapshot or shown in the Trash.
_SECRET_HINTS = ("password", "secret", "token", "_hash", "nonce", "content")


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in row.items()
        if not any(h in k for h in _SECRET_HINTS) and not (isinstance(v, str) and len(v) > 2000)
    }


@dataclass
class Removal:
    """Rows to remove, in insert (restore) order, plus rows dropped without being kept."""

    rows: list[tuple[sa.Table, dict[str, Any]]] = field(default_factory=list)
    dropped: list[tuple[sa.Table, dict[str, Any]]] = field(default_factory=list)

    def by_table(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for t, r in [*self.rows, *self.dropped]:
            out.setdefault(t.name, []).append(r)
        return out


def _rows(db: Session, table: sa.Table, where: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(sa.select(table).where(where)).mappings()]


def _collect(db: Session, kind: Kind, obj: Any) -> Removal:
    table: sa.Table = kind.model.__table__
    rm = Removal()
    rm.rows += [(table, r) for r in _rows(db, table, table.c.id == obj.id)]
    for ch in kind.children:
        t: sa.Table = ch.model.__table__
        cond = t.c[ch.column] == obj.id
        if ch.where is not None:
            cond = sa.and_(cond, ch.where)
        (rm.rows if ch.keep else rm.dropped).extend((t, r) for r in _rows(db, t, cond))
    return rm


# ------------------------------------------------------------------ references

NOUNS = {
    "audit_events": ("audit log entry", None),
    "auth_tokens": ("invitation or reset link they sent", None),
    "customers": ("customer", "name"),
    "document_files": ("filed document", "filename"),
    "grn_lines": ("receiving line", None),
    "inventory_balances": ("stock balance", None),
    "inventory_lots": ("stock lot", "lot_code"),
    "invoice_lines": ("invoice line", None),
    "invoices": ("invoice", "invoice_no"),
    "ocr_extractions": ("OCR read", None),
    "ocr_fields": ("OCR field", None),
    "order_form_submission_items": ("website order item", None),
    "order_form_submissions": ("website order entry", "reference"),
    "order_form_versions": ("order form", "title"),
    "payments": ("payment", "reference"),
    "picking_attempts": ("picking sheet", "pick_code"),
    "picking_lines": ("picking line", None),
    "price_book_versions": ("price book", "version_code"),
    "price_entries": ("price", None),
    "products": ("product", "sku"),
    "sales_order_amendments": ("order change", None),
    "sales_order_lines": ("sales order line", None),
    "sales_orders": ("sales order", "order_no"),
    "scan_jobs": ("scan", "job_no"),
    "shipment_declared_lines": ("declared shipment line", None),
    "shipment_documents": ("shipment document", None),
    "shipments": ("shipment", "shipment_no"),
    "stock_movements": ("stock movement", None),
    "supplier_payments": ("supplier payment", "reference"),
    "trash_items": ("item they moved to the Trash", None),
    "delete_requests": ("delete request", None),
}


def _plural(word: str, n: int) -> str:
    if n == 1:
        return word
    if word.endswith("y") and not word.endswith(("ay", "ey", "oy")):
        return word[:-1] + "ies"
    return word + "s"


def _ref(table: str, count: int, examples: list[str] | None = None) -> dict[str, Any]:
    noun = NOUNS.get(table, (table.replace("_", " ").rstrip("s"), None))[0]
    return {"table": table, "noun": noun, "count": count, "examples": examples or []}


def _references(db: Session, rm: Removal) -> list[dict[str, Any]]:
    """Every non-cascading foreign key that points at a removed row from a row that stays."""
    removed = rm.by_table()
    tables = {t.name: t for t, _ in [*rm.rows, *rm.dropped]}
    out: list[dict[str, Any]] = []
    for name, rows in removed.items():
        target = tables[name]
        for other in Base.metadata.sorted_tables:
            for fk in other.foreign_keys:
                if fk.column.table is not target or fk.ondelete == "CASCADE":
                    continue
                vals = {r[fk.column.name] for r in rows if r[fk.column.name] is not None}
                if not vals:
                    continue
                cond: Any = fk.parent.in_(vals)
                pk = list(other.primary_key.columns)
                if other.name in removed and len(pk) == 1:
                    gone = {r[pk[0].name] for r in removed[other.name]}
                    cond = sa.and_(cond, pk[0].not_in(gone))
                n = db.scalar(sa.select(sa.func.count()).select_from(other).where(cond)) or 0
                if not n:
                    continue
                col = NOUNS.get(other.name, ("", None))[1]
                ex: list[str] = []
                if col and col in other.c:
                    ex = [
                        str(v)
                        for v in db.scalars(sa.select(other.c[col]).where(cond).distinct().limit(3))
                        if v is not None
                    ]
                out.append(_ref(other.name, n, ex))
    # The same table can point at us through several columns (e.g. created_by, approved_by).
    merged: dict[str, dict[str, Any]] = {}
    for r in out:
        m = merged.setdefault(r["table"], {**r, "count": 0, "examples": []})
        m["count"] += r["count"]
        m["examples"] = list(dict.fromkeys([*m["examples"], *r["examples"]]))[:3]
    return list(merged.values())


def _in_use_error(label: str, refs: list[dict[str, Any]]) -> ApiError:
    parts = []
    for r in refs:
        s = f"{r['count']} {_plural(r['noun'], r['count'])}"
        if r["examples"]:
            s += f" ({', '.join(r['examples'])})"
        parts.append(s)
    return ApiError(
        409,
        "IN_USE",
        f"{label} can't be deleted: it is used by {'; '.join(parts)}.",
        references=refs,
    )


def _take_leftover_doc(db: Session, rm: Removal, doc_id: uuid.UUID | None) -> None:
    """A scanned or uploaded file goes with the record when nothing else uses it."""
    if doc_id is None:
        return
    t: sa.Table = SourceDocument.__table__  # type: ignore[assignment]
    rows = _rows(db, t, t.c.id == doc_id)
    if not rows:
        return
    trial = Removal([(t, rows[0]), *rm.rows], rm.dropped)
    if _references(db, trial):
        return
    if db.scalar(sa.select(sa.func.count()).where(SalesOrder.source_document_id == doc_id)):
        return
    rm.rows.insert(0, (t, rows[0]))


# ------------------------------------------------------------------ service


def next_number(db: Session, column: Any, company_id: uuid.UUID, kind: str) -> int:
    """1 + the highest trailing number used by a live record or one waiting in the Trash.
    Numbering by row count would hand out a number again once a record left the table."""

    def last(col: Any) -> Any:
        return sa.func.max(sa.cast(sa.func.substring(col, r"(\d+)$"), sa.Integer))

    model = column.class_
    live = db.scalar(sa.select(last(column)).where(model.company_id == company_id)) or 0
    trashed = (
        db.scalar(
            sa.select(last(TrashItem.snapshot[column.key].astext)).where(
                TrashItem.company_id == company_id, TrashItem.kind == kind
            )
        )
        or 0
    )
    return int(max(live, trashed)) + 1


def _now() -> datetime:
    return datetime.now(UTC)


def load(db: Session, kind: Kind, company_id: uuid.UUID, item_id: uuid.UUID) -> Any:
    obj = db.get(kind.model, item_id, with_for_update=True, populate_existing=True)
    if obj is None or kind.company(db, obj) != company_id:
        return None
    return obj


def check(db: Session, kind: Kind, me: uuid.UUID, obj: Any) -> Removal:
    """Raise 409 when the record can't go to the Trash; else return what would be removed."""
    if kind.rules:
        kind.rules(db, me, obj)
    rm = _collect(db, kind, obj)
    refs = _references(db, rm) + (kind.extra_refs(db, obj) if kind.extra_refs else [])
    if refs:
        raise _in_use_error(kind.label(db, obj), refs)
    if kind.leftover_doc:
        _take_leftover_doc(db, rm, kind.leftover_doc(obj))
    return rm


def _delete_rows(db: Session, rm: Removal) -> None:
    db.execute(sa.text("SELECT set_config('pro_trading.trash', 'on', true)"))
    try:
        for t, r in [*rm.dropped, *reversed(rm.rows)]:
            pk = list(t.primary_key.columns)
            db.execute(sa.delete(t).where(*[c == r[c.name] for c in pk]))
    finally:
        db.execute(sa.text("SELECT set_config('pro_trading.trash', 'off', true)"))


def existing(db: Session, company_id: uuid.UUID, kind: str, item_id: uuid.UUID) -> TrashItem | None:
    return db.scalar(
        sa.select(TrashItem).where(
            TrashItem.company_id == company_id,
            TrashItem.kind == kind,
            TrashItem.item_id == item_id,
        )
    )


def move_to_trash(
    db: Session,
    kind: Kind,
    company_id: uuid.UUID,
    actor_id: uuid.UUID,
    item_id: uuid.UUID,
    request_id: str | None,
) -> tuple[TrashItem, bool]:
    """Returns (trash item, already_in_trash)."""
    obj = load(db, kind, company_id, item_id)
    if obj is None:
        prior = existing(db, company_id, kind.key, item_id)
        if prior is not None:
            return prior, True
        raise ApiError(404, "NOT_FOUND", f"That {kind.noun} was not found")
    rm = check(db, kind, actor_id, obj)
    label = kind.label(db, obj)
    table = kind.model.__table__
    main = next(r for t, r in rm.rows if t is table and r["id"] == obj.id)
    snapshot = {k: _enc(v) for k, v in _public(main).items()}
    payload = [{"t": t.name, "r": {k: _enc(v) for k, v in r.items()}} for t, r in rm.rows]
    db.expunge(obj)
    if kind.prepare:
        kind.prepare(db, obj)
    _delete_rows(db, rm)
    now = _now()
    item = TrashItem(
        company_id=company_id,
        kind=kind.key,
        item_id=item_id,
        label=label,
        snapshot=snapshot,
        payload=payload,
        trashed_by=actor_id,
        trashed_at=now,
        purge_after=now + timedelta(days=TRASH_DAYS),
    )
    db.add(item)
    db.execute(
        sa.update(DeleteRequest)
        .where(
            DeleteRequest.company_id == company_id,
            DeleteRequest.kind == kind.key,
            DeleteRequest.item_id == item_id,
            DeleteRequest.status == "PENDING",
        )
        .values(status="APPROVED", decided_by=actor_id, decided_at=now)
    )
    db.flush()
    counts: dict[str, int] = {}
    for t, r in [*rm.rows, *rm.dropped]:
        if r is not main:
            counts[t.name] = counts.get(t.name, 0) + 1
    audit.record(
        db,
        action="trash.moved",
        success=True,
        company_id=company_id,
        actor_id=actor_id,
        target_type=kind.key,
        target_id=str(item_id),
        warehouse_id=_warehouse(snapshot),
        request_id=request_id,
        detail={
            "label": label,
            "trash_id": str(item.id),
            "snapshot": snapshot,
            "removed_with_it": counts,
            "purge_after": item.purge_after.isoformat(),
        },
    )
    return item, False


def _warehouse(snapshot: dict[str, Any]) -> uuid.UUID | None:
    w = snapshot.get("warehouse_id")
    return uuid.UUID(w) if w else None


def _restore_error(item: TrashItem, e: IntegrityError) -> ApiError:
    diag = getattr(e.orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None) or ""
    detail = getattr(diag, "message_detail", None) or ""
    sqlstate = getattr(e.orig, "sqlstate", None)
    if sqlstate == "23503":  # foreign key: something it belongs to is gone
        return ApiError(
            409,
            "RESTORE_CONFLICT",
            f"{item.label} can't be restored: something it belongs to no longer exists "
            "(restore that first if it is in the Trash).",
            constraint=constraint,
        )
    if sqlstate == "23505":
        return ApiError(
            409,
            "RESTORE_CONFLICT",
            f"{item.label} can't be restored: another record now uses the same "
            f"{detail.split('=')[0].removeprefix('Key ').strip('()') or 'name or code'}.",
            constraint=constraint,
        )
    return ApiError(
        409, "RESTORE_CONFLICT", f"{item.label} can't be restored.", constraint=constraint
    )


def restore(db: Session, item: TrashItem, actor_id: uuid.UUID, request_id: str | None) -> None:
    tables = Base.metadata.tables
    try:
        with db.begin_nested():
            for entry in item.payload:
                t = tables[entry["t"]]
                skip = RESTORE_SKIP.get(t.name, set())
                values = {k: _dec(t.c[k], v) for k, v in entry["r"].items() if k not in skip}
                db.execute(sa.insert(t).values(values))
    except IntegrityError as e:
        raise _restore_error(item, e) from None
    audit.record(
        db,
        action="trash.restored",
        success=True,
        company_id=item.company_id,
        actor_id=actor_id,
        target_type=item.kind,
        target_id=str(item.item_id),
        warehouse_id=_warehouse(item.snapshot),
        request_id=request_id,
        detail={"label": item.label, "trash_id": str(item.id)},
    )
    db.delete(item)
    db.flush()


def purge(db: Session, item: TrashItem, actor_id: uuid.UUID | None, request_id: str | None) -> None:
    """Delete forever: the record already left the live tables; drop what was kept."""
    audit.record(
        db,
        action="trash.purged" if actor_id else "trash.auto_purged",
        success=True,
        company_id=item.company_id,
        actor_id=actor_id,
        target_type=item.kind,
        target_id=str(item.item_id),
        warehouse_id=_warehouse(item.snapshot),
        request_id=request_id,
        detail={
            "label": item.label,
            "trash_id": str(item.id),
            "trashed_at": item.trashed_at.isoformat(),
        },
    )
    db.delete(item)
    db.flush()


def purge_expired(db: Session, now: datetime | None = None, batch: int = 200) -> int:
    """Celery beat (hourly): delete forever whatever has been in the Trash for 30 days."""
    total = 0
    while True:
        items = db.scalars(
            sa.select(TrashItem)
            .where(TrashItem.purge_after <= (now or _now()))
            .order_by(TrashItem.purge_after)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for it in items:
            purge(db, it, None, None)
        db.commit()
        total += len(items)
        if len(items) < batch:
            return total


# ------------------------------------------------------------------ delete requests


@rule("trash.delete_requested")
def _notify_request(db: Session, ev: AuditInfo) -> None:
    req = db.get(DeleteRequest, uuid.UUID(str(ev.detail["delete_request_id"])))
    if req is None or ev.company_id is None:
        return
    who = db.get(Employee, req.requested_by) if req.requested_by else None
    notify_safely(
        db,
        ev.company_id,
        "trash.delete_requested",
        target_type="delete_request",
        target_id=req.id,
        category="system",
        severity="info",
        source_module="Trash",
        title=f"Delete requested: {req.label}",
        body=f"{who.display_name if who else 'Someone'}: {req.reason or 'no reason given'}",
        link="/trash?tab=requests",
        permission=TRASH_PERMISSION,
        actor_id=ev.actor_id,
    )
