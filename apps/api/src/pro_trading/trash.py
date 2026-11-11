"""Trash bin (migration 0020).

The Super Admin (permission `trash.manage`, with a verified two-step sign-in) moves a record to
the Trash. That removes the record, and the rows that belong to it, from the live tables, so no
other screen or query has to know about the Trash. Every removed row is kept in `trash_items`
until the record is restored, deleted forever, or purged automatically after 30 days.

What depends on the record goes with it (migration 0026). Every foreign key that points at a
removed row is followed (`EDGES`): rows that only exist for it are taken into the same Trash
item (a scan's OCR reads, a customer's uninvoiced orders), loose links are cleared and put back
on restore (customers of a deleted group), and anything in the protected books - invoices,
payments, the stock ledger, shipments, the audit log - refuses the delete with a 409 IN_USE
that names it and says what to do instead (deactivate, suspend, void). Keys not listed refuse
too, so a new table never gets swept along by accident. `preview` answers the same question
without changing anything, for the confirm dialog.

Kind-specific rules (only aborted picking sheets, only finished scans, never invoice PDFs, ...)
are checked here; the DB guard triggers let a DELETE (or a link being cleared) through only while
`pro_trading.trash` is on for the transaction (set and cleared by `_trash_mode`).

Other staff cannot trash anything; they file a delete request that the Super Admin approves
(the record moves to the Trash) or dismisses.
"""

import base64
import functools
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, IntegrityError
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
    # When protected records refuse the delete: (one-click action or None, what to do instead).
    instead: tuple[str | None, str] | None = None


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


def _pick_rules(db: Session, me: uuid.UUID, a: PickingAttempt) -> None:
    if a.status != "ABORTED":
        if a.status in ("CREATED", "IN_PROGRESS", "SUBMITTED", "NEEDS_CORRECTION"):
            raise _refuse(f"Packing sheet {a.pick_code} is still open. Abort it first.")
        raise _refuse(
            f"Packing sheet {a.pick_code} is {a.status.lower()} and stays with its order. "
            "Delete the order to take it with it."
        )


def _pick_refs(db: Session, a: PickingAttempt) -> list[dict[str, Any]]:
    n = db.scalar(sa.select(sa.func.count()).where(StockMovement.source_id == a.id)) or 0
    return [_ref("stock_movements", n)] if n else []


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


def _product_prepare(db: Session, p: Product) -> None:
    # The default-case-size guard (migration 0020) protects a lone case size; the product's
    # own default goes with the product. The snapshot was taken first, so a restore keeps it.
    db.execute(
        sa.update(ProductUnitProfile)
        .where(ProductUnitProfile.product_id == p.id, ProductUnitProfile.is_default.is_(True))
        .values(is_default=False)
    )


_DEACTIVATE = (
    "deactivate",
    "Deactivate it instead: it stays on past records but can't be used for anything new.",
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
            prepare=_product_prepare,
            instead=_DEACTIVATE,
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
        Kind(
            "customer",
            Customer,
            "customer",
            lambda _db, c: c.name,
            "catalog.manage",
            instead=_DEACTIVATE,
        ),
        Kind(
            "customer_group",
            CustomerGroup,
            "customer group",
            lambda _db, g: f"{g.name} ({g.code})",
            "catalog.manage",
        ),
        Kind(
            "supplier",
            Vendor,
            "supplier",
            lambda _db, v: v.name,
            "catalog.manage",
            instead=_DEACTIVATE,
        ),
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
            instead=(None, "An invoiced order stays with its invoice."),
        ),
        Kind(
            "picking_sheet",
            PickingAttempt,
            "packing sheet",
            lambda _db, a: f"Packing sheet {a.pick_code}",
            "sales_order.manage",
            children=(Child(PickingLine, "attempt_id"),),
            rules=_pick_rules,
            extra_refs=_pick_refs,
            instead=(None, "A packing sheet that was invoiced stays with its invoice."),
        ),
        Kind(
            "scan",
            ScanJob,
            "scan",
            lambda _db, j: f"Scan {j.job_no}",
            "scan.create",
            rules=_scan_rules,
            leftover_doc=lambda j: j.document_id,
            instead=(None, "A scan filed as a shipment's receiving paperwork stays with it."),
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
            instead=(
                "suspend",
                "Suspend them instead: they can't sign in, and their name stays on their work.",
            ),
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


def _generated(t: sa.Table) -> set[str]:
    """Identity columns the database always numbers itself (e.g. scan event ids)."""
    return {c.name for c in t.c if c.identity is not None and c.identity.always}


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in row.items()
        if not any(h in k for h in _SECRET_HINTS) and not (isinstance(v, str) and len(v) > 2000)
    }


@dataclass(frozen=True)
class Unlink:
    """A row that stays but pointed at a removed row: the link is cleared, and put back on
    restore (when nothing else took its place)."""

    table: sa.Table
    row: dict[str, Any]
    column: str

    def pk(self) -> dict[str, Any]:
        return {c.name: self.row[c.name] for c in self.table.primary_key.columns}


@dataclass
class Removal:
    """Rows to remove, in insert (restore) order, rows dropped without being kept, links to
    clear, and what refuses the delete (protected records pointing at it)."""

    rows: list[tuple[sa.Table, dict[str, Any]]] = field(default_factory=list)
    dropped: list[tuple[sa.Table, dict[str, Any]]] = field(default_factory=list)
    unlinks: list[Unlink] = field(default_factory=list)
    blocked: list[dict[str, Any]] = field(default_factory=list)
    found: list[str] = field(default_factory=list)  # tables in the order they were reached

    def by_table(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for t, r in [*self.rows, *self.dropped]:
            out.setdefault(t.name, []).append(r)
        return out


def _rows(db: Session, table: sa.Table, where: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(sa.select(table).where(where)).mappings()]


def _key(t: sa.Table, r: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    return t.name, tuple(r[c.name] for c in t.primary_key.columns)


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


# ------------------------------------------------------------------ what goes with it

CASCADE, UNLINK, PROTECT = "cascade", "unlink", "protect"
Split = Callable[[Session, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]


@dataclass(frozen=True)
class Edge:
    policy: str
    split: Split | None = None  # per-row policy, e.g. only lines on draft orders go
    hint: str = ""  # what to do instead, when the split protects some rows


def _no_invoice_pdf(db: Session, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        PROTECT: [r for r in rows if r["kind"] == "INVOICE_PDF"],
        CASCADE: [r for r in rows if r["kind"] != "INVOICE_PDF"],
    }


def _draft_lines(db: Session, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    drafts = set(
        db.scalars(
            sa.select(SalesOrder.id).where(
                SalesOrder.id.in_({r["order_id"] for r in rows}), SalesOrder.status == "DRAFT"
            )
        )
    )
    return {
        CASCADE: [r for r in rows if r["order_id"] in drafts],
        PROTECT: [r for r in rows if r["order_id"] not in drafts],
    }


_FILED = Edge(CASCADE, _no_invoice_pdf)

# (referencing table, column) -> what happens to its rows when the row they point at goes.
# A foreign key not listed here refuses the delete (or, for ON DELETE CASCADE keys, is left to
# the database). The protected books - invoices, payments, allocations, supplier payments,
# stock movements/lots/balances, receiving, shipments, the audit log - are never listed.
EDGES: dict[tuple[str, str], Edge] = {
    # a scan: its OCR reads (fields, corrections), Gmail imports, events and filed copies
    ("ocr_extractions", "scan_job_id"): Edge(CASCADE),
    ("ocr_fields", "extraction_id"): Edge(CASCADE),
    ("ocr_corrections", "field_id"): Edge(CASCADE),
    ("gmail_processing_jobs", "scan_job_id"): Edge(CASCADE),
    ("scan_events", "scan_job_id"): Edge(CASCADE),
    ("document_files", "scan_job_id"): _FILED,
    # a customer: its orders (with picking sheets and changes), website entries, filed
    # documents and prices; a supplier: its filed documents
    ("sales_orders", "customer_id"): Edge(CASCADE),
    ("sales_order_lines", "order_id"): Edge(CASCADE),
    ("sales_order_amendments", "order_id"): Edge(CASCADE),
    ("sales_order_amendments", "line_id"): Edge(CASCADE),
    ("picking_attempts", "order_id"): Edge(CASCADE),
    ("picking_lines", "attempt_id"): Edge(CASCADE),
    ("picking_lines", "order_line_id"): Edge(CASCADE),
    ("order_form_submissions", "sales_order_id"): Edge(CASCADE),
    ("order_form_submissions", "customer_id"): Edge(CASCADE),
    ("order_form_submission_items", "submission_id"): Edge(CASCADE),
    ("order_form_submission_events", "submission_id"): Edge(CASCADE),
    ("document_files", "customer_id"): _FILED,
    ("document_files", "vendor_id"): _FILED,
    ("price_entries", "customer_id"): Edge(CASCADE),
    ("price_entries", "group_id"): Edge(CASCADE),
    ("price_entries", "product_id"): Edge(CASCADE),
    ("price_entries", "version_id"): Edge(CASCADE),
    # a product: aliases, case sizes, prices and its lines on draft orders
    ("product_aliases", "product_id"): Edge(CASCADE),
    ("product_unit_profiles", "product_id"): Edge(CASCADE),
    ("sales_order_lines", "product_id"): Edge(CASCADE, _draft_lines, "Delete those orders first."),
    # loose links: the row stays, the link is cleared and put back on restore
    ("ocr_extractions", "customer_id"): Edge(UNLINK),
    ("ocr_extractions", "vendor_id"): Edge(UNLINK),
    ("ocr_extractions", "sales_order_id"): Edge(UNLINK),
    ("ocr_fields", "read_product_id"): Edge(UNLINK),
    ("ocr_fields", "final_product_id"): Edge(UNLINK),
    ("gmail_processing_jobs", "extraction_id"): Edge(UNLINK),
    ("order_form_submission_items", "product_id"): Edge(UNLINK),
    ("order_form_submissions", "possible_duplicate_of"): Edge(UNLINK),
    ("customers", "group_id"): Edge(UNLINK),
    ("price_book_versions", "based_on_id"): Edge(UNLINK),
}

# References that are not foreign keys: target table -> (table, column, edge).
SOFT_EDGES: dict[str, list[tuple[Any, str, Edge]]] = {
    "picking_attempts": [(SalesOrderAmendment, "pick_attempt_id", Edge(UNLINK))],
}


@functools.cache
def _incoming() -> dict[str, list[tuple[sa.Table, sa.ForeignKey]]]:
    out: dict[str, list[tuple[sa.Table, sa.ForeignKey]]] = {}
    for t in Base.metadata.sorted_tables:
        for fk in t.foreign_keys:
            out.setdefault(fk.column.table.name, []).append((t, fk))
    return out


@functools.cache
def _table_order() -> dict[str, int]:
    return {t.name: i for i, t in enumerate(Base.metadata.sorted_tables)}


def _self_fks(t: sa.Table) -> list[sa.ForeignKey]:
    return [fk for fk in t.foreign_keys if fk.column.table is t]


def _insert_order(rows: list[tuple[sa.Table, dict[str, Any]]]) -> list[tuple[sa.Table, Any]]:
    """Parents before children: tables in dependency order, and within a table that points
    at itself (website entries marked as duplicates) the original before its duplicates."""
    order = _table_order()
    out: list[tuple[sa.Table, dict[str, Any]]] = []
    by: dict[str, list[tuple[sa.Table, dict[str, Any]]]] = {}
    for t, r in rows:
        by.setdefault(t.name, []).append((t, r))
    for name in sorted(by, key=lambda n: order.get(n, -1)):
        group = by[name]
        fks = _self_fks(group[0][0])
        if not fks:
            out += group
            continue
        placed: set[Any] = set()
        ids = {r[fk.column.name] for _, r in group for fk in fks}
        while group:
            ready = [
                (t, r)
                for t, r in group
                if all(
                    r[fk.parent.name] is None
                    or r[fk.parent.name] in placed
                    or not any(x[fk.column.name] == r[fk.parent.name] for _, x in group)
                    for fk in fks
                )
            ] or group  # a loop can't happen with these keys; never spin
            for _, r in ready:
                placed |= {r[fk.column.name] for fk in fks if r[fk.column.name] in ids}
            out += ready
            group = [x for x in group if x not in ready]
    return out


def _expand(db: Session, rm: Removal) -> None:
    """Follow every reference to the removed rows: take, unlink or refuse (see `EDGES`)."""
    seen = {_key(t, r) for t, r in [*rm.rows, *rm.dropped]}
    unlinks: dict[tuple[Any, ...], Unlink] = {}
    blocked: list[tuple[sa.Table, list[dict[str, Any]], str]] = []
    pending = rm.by_table()

    def handle(
        other: sa.Table, column: str, edge: Edge | None, found: list[dict[str, Any]]
    ) -> None:
        found = [r for r in found if _key(other, r) not in seen]
        if not found:
            return
        if edge is None:
            blocked.append((other, found, ""))
            return
        parts = edge.split(db, found) if edge.split else {edge.policy: found}
        for policy, part in parts.items():
            if not part:
                continue
            if policy == CASCADE:
                for r in part:
                    seen.add(_key(other, r))
                rm.rows.extend((other, r) for r in part)
                pending.setdefault(other.name, []).extend(part)
            elif policy == UNLINK:
                for r in part:
                    u = Unlink(other, r, column)
                    unlinks[(*_key(other, r), column)] = u
            else:
                blocked.append((other, part, edge.hint))

    while pending:
        name, rows = pending.popitem()
        for other, fk in _incoming().get(name, []):
            vals = {r[fk.column.name] for r in rows if r[fk.column.name] is not None}
            if not vals:
                continue
            edge = EDGES.get((other.name, fk.parent.name))
            if edge is None and fk.ondelete == "CASCADE":
                continue
            handle(other, fk.parent.name, edge, _rows(db, other, fk.parent.in_(vals)))
        for model, column, edge in SOFT_EDGES.get(name, []):
            other = model.__table__
            ids = {r["id"] for r in rows}
            handle(other, column, edge, _rows(db, other, other.c[column].in_(ids)))

    # A row taken through one key keeps all its links; one refused earlier but taken later
    # (a confirmed order's lines, when the whole order goes) is no longer in the way.
    rm.unlinks = [u for u in unlinks.values() if _key(u.table, u.row) not in seen]
    rm.found = list(dict.fromkeys(t.name for t, _ in rm.rows))
    rm.rows = _insert_order(rm.rows)
    merged: dict[str, dict[str, Any]] = {}
    for other, part, hint in blocked:
        part = [r for r in part if _key(other, r) not in seen]
        if not part:
            continue
        ref = _summarise(db, other, part)
        m = merged.setdefault(other.name, {**ref, "count": 0, "examples": [], "hint": hint})
        m["count"] += ref["count"]
        m["examples"] = list(dict.fromkeys([*m["examples"], *ref["examples"]]))[:3]
        m["hint"] = m["hint"] or hint
    rm.blocked = list(merged.values())


def _live_refs(db: Session, table: sa.Table, rows: list[dict[str, Any]], rm: Removal) -> bool:
    """Does any row that stays point at these rows?"""
    gone = {_key(t, r) for t, r in [*rm.rows, *rm.dropped]}
    for other, fk in _incoming().get(table.name, []):
        if fk.ondelete == "CASCADE":
            continue
        vals = {r[fk.column.name] for r in rows if r[fk.column.name] is not None}
        if vals and any(_key(other, r) not in gone for r in _rows(db, other, fk.parent.in_(vals))):
            return True
    return False


# ------------------------------------------------------------------ naming what goes

NOUNS = {
    "audit_events": ("audit log entry", None),
    "auth_tokens": ("invitation or reset link they sent", None),
    "customers": ("customer", "name"),
    "document_files": ("filed document", "filename"),
    "email_messages": ("invoice email", None),
    "gmail_processing_jobs": ("Gmail import", None),
    "grn_lines": ("receiving line", None),
    "inventory_balances": ("stock balance", None),
    "inventory_lots": ("stock lot", "lot_code"),
    "invoice_documents": ("invoice PDF", None),
    "invoice_lines": ("invoice line", None),
    "invoices": ("invoice", "invoice_no"),
    "ocr_corrections": ("OCR correction", None),
    "ocr_extractions": ("OCR read", None),
    "ocr_fields": ("OCR line", None),
    "order_form_submission_events": ("website entry event", None),
    "order_form_submission_items": ("website order item", None),
    "order_form_submissions": ("website order entry", "reference"),
    "order_form_versions": ("order form", "title"),
    "payment_allocations": ("payment allocation", None),
    "payment_reminders": ("payment reminder", None),
    "payments": ("payment", "reference"),
    "picking_attempts": ("packing sheet", "pick_code"),
    "picking_lines": ("packing sheet line", None),
    "price_book_versions": ("price book", "version_code"),
    "price_entries": ("price", None),
    "product_aliases": ("alias", "alias"),
    "product_unit_profiles": ("case size", "code"),
    "products": ("product", "sku"),
    "sales_order_amendments": ("order change", None),
    "sales_order_lines": ("order line", None),
    "sales_orders": ("sales order", "order_no"),
    "scan_events": ("scan event", None),
    "scan_jobs": ("scan", "job_no"),
    "shipment_declared_lines": ("declared shipment line", None),
    "shipment_documents": ("shipment document", None),
    "shipments": ("shipment", "shipment_no"),
    "source_documents": ("scanned file", "filename"),
    "stock_movements": ("stock movement", None),
    "supplier_payments": ("supplier payment", "reference"),
    "trash_items": ("item they moved to the Trash", None),
    "delete_requests": ("delete request", None),
}
# Rows without a name of their own are shown by the record they belong to.
VIA = {
    "sales_order_lines": ("order_id", SalesOrder.__table__, "order_no"),
    "sales_order_amendments": ("order_id", SalesOrder.__table__, "order_no"),
    "picking_lines": ("attempt_id", PickingAttempt.__table__, "pick_code"),
    "ocr_extractions": ("scan_job_id", ScanJob.__table__, "job_no"),
    "gmail_processing_jobs": ("scan_job_id", ScanJob.__table__, "job_no"),
    "order_form_submission_items": ("submission_id", OrderFormSubmission.__table__, "reference"),
    "price_entries": ("version_id", PriceBookVersion.__table__, "version_code"),
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


def _summarise(db: Session, t: sa.Table, rows: list[dict[str, Any]]) -> dict[str, Any]:
    col = NOUNS.get(t.name, ("", None))[1]
    examples: list[str] = []
    if col and col in t.c:
        examples = [str(r[col]) for r in rows if r.get(col) is not None]
    elif t.name in VIA:
        fk_col, parent, label = VIA[t.name]
        ids = list(dict.fromkeys(r[fk_col] for r in rows if r.get(fk_col) is not None))[:10]
        if ids:
            q = sa.select(parent.c.id, parent.c[label]).where(parent.c.id.in_(ids))
            names = dict(db.execute(q).all())
            examples = [str(names[i]) for i in ids if names.get(i) is not None]
    return _ref(t.name, len(rows), list(dict.fromkeys(examples))[:3])


def _group(db: Session, pairs: list[tuple[sa.Table, dict[str, Any]]]) -> list[dict[str, Any]]:
    by: dict[str, tuple[sa.Table, list[dict[str, Any]]]] = {}
    for t, r in pairs:
        by.setdefault(t.name, (t, []))[1].append(r)
    return [_summarise(db, t, rows) for t, rows in by.values()]


def _ref_text(refs: list[dict[str, Any]]) -> str:
    parts = []
    for r in refs:
        s = f"{r['count']} {_plural(r['noun'], r['count'])}"
        if r["examples"]:
            s += f" ({', '.join(r['examples'])})"
        parts.append(s)
    return "; ".join(parts)


def _instead(kind: Kind, refs: list[dict[str, Any]]) -> tuple[str | None, str]:
    hints = [r["hint"] for r in refs if r.get("hint")]
    action, text_ = kind.instead or (None, "")
    return action, " ".join(dict.fromkeys([*hints, text_])).strip()


def _in_use_error(kind: Kind, label: str, refs: list[dict[str, Any]]) -> ApiError:
    action, hint = _instead(kind, refs)
    return ApiError(
        409,
        "IN_USE",
        f"{label} can't be deleted: it is used by {_ref_text(refs)}. {hint}".strip(),
        references=refs,
        hint=hint,
        instead=action,
    )


def _take_leftover_doc(db: Session, rm: Removal, doc_id: uuid.UUID | None) -> None:
    """A scanned or uploaded file goes with the record when nothing else uses it."""
    if doc_id is None:
        return
    t: sa.Table = SourceDocument.__table__  # type: ignore[assignment]
    rows = _rows(db, t, t.c.id == doc_id)
    if not rows or _live_refs(db, t, rows, rm):
        return
    if db.scalar(sa.select(sa.func.count()).where(SalesOrder.source_document_id == doc_id)):
        return
    rm.rows = _insert_order([(t, rows[0]), *rm.rows])


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


def plan(db: Session, kind: Kind, me: uuid.UUID, obj: Any) -> Removal:
    """What moving the record to the Trash takes with it, unlinks, and is refused by. Changes
    nothing; raises 409 NOT_DELETABLE when the record's own state rules it out."""
    if kind.rules:
        kind.rules(db, me, obj)
    rm = _collect(db, kind, obj)
    _expand(db, rm)
    if kind.extra_refs:
        rm.blocked += kind.extra_refs(db, obj)
    if not rm.blocked and kind.leftover_doc:
        _take_leftover_doc(db, rm, kind.leftover_doc(obj))
    return rm


def check(db: Session, kind: Kind, me: uuid.UUID, obj: Any) -> Removal:
    """Raise 409 when the record can't go to the Trash; else return what would be removed."""
    rm = plan(db, kind, me, obj)
    if rm.blocked:
        raise _in_use_error(kind, kind.label(db, obj), rm.blocked)
    return rm


def _unlinked_rows(rm: Removal) -> list[tuple[sa.Table, dict[str, Any]]]:
    rows: dict[Any, tuple[sa.Table, dict[str, Any]]] = {}
    for u in rm.unlinks:
        rows.setdefault(_key(u.table, u.row), (u.table, u.row))
    return list(rows.values())


def preview(db: Session, kind: Kind, me: uuid.UUID, obj: Any) -> dict[str, Any]:
    """For the confirm dialog: everything that goes with the record, the links that are
    cleared, or what refuses it and what to do instead."""
    label = kind.label(db, obj)
    out: dict[str, Any] = {
        "label": label,
        "deletable": True,
        "message": None,
        "goes_with": [],
        "unlinked": [],
        "blocked": [],
        "instead": None,
    }
    try:
        rm = plan(db, kind, me, obj)
    except ApiError as e:
        return {**out, "deletable": False, "message": e.message}
    if rm.blocked:
        err = _in_use_error(kind, label, rm.blocked)
        return {
            **out,
            "deletable": False,
            "message": err.message,
            "blocked": rm.blocked,
            "instead": err.extra["instead"],
            "hint": err.extra["hint"],
        }
    main = _key(kind.model.__table__, {"id": obj.id})
    goes = _group(db, [(t, r) for t, r in rm.rows if _key(t, r) != main])
    rank = {name: i for i, name in enumerate(rm.found)}  # direct dependants first
    out["goes_with"] = sorted(goes, key=lambda g: rank.get(g["table"], len(rank)))
    out["unlinked"] = _group(db, _unlinked_rows(rm))
    return out


@contextmanager
def _trash_mode(db: Session) -> Iterator[None]:
    """The DB guard triggers let a delete, or a link being cleared or put back, through only
    while this is on (migrations 0020 and 0026)."""
    db.execute(sa.text("SELECT set_config('pro_trading.trash', 'on', true)"))
    try:
        yield
    finally:
        db.execute(sa.text("SELECT set_config('pro_trading.trash', 'off', true)"))


def _set_link(db: Session, t: sa.Table, pk: dict[str, Any], column: str, value: Any) -> int:
    # Plain SQL: an ORM/Core update would also stamp onupdate columns (updated_at, version).
    # Table and column names come from the schema metadata, never from a request.
    assert column in t.c and all(k in t.c for k in pk)
    where = " AND ".join(f'"{k}" = :pk_{k}' for k in pk)
    guard = "" if value is None else f' AND "{column}" IS NULL'  # never undo a newer link
    res = db.execute(
        sa.text(f'UPDATE "{t.name}" SET "{column}" = :v WHERE {where}{guard}'),  # noqa: S608
        {"v": value, **{f"pk_{k}": v for k, v in pk.items()}},
    )
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


def _delete_rows(db: Session, rm: Removal) -> None:
    try:
        with _trash_mode(db), db.begin_nested():
            for u in rm.unlinks:
                _set_link(db, u.table, u.pk(), u.column, None)
            for t, r in [*rm.dropped, *reversed(rm.rows)]:
                pk = list(t.primary_key.columns)
                db.execute(sa.delete(t).where(*[c == r[c.name] for c in pk]))
    except IntegrityError:
        raise ApiError(
            409,
            "IN_USE",
            "Something new started using this record while it was being deleted. Try again.",
            references=[],
        ) from None


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
    payload: list[dict[str, Any]] = [
        {"t": t.name, "r": {k: _enc(v) for k, v in r.items()}} for t, r in rm.rows
    ]
    payload += [
        {
            "t": u.table.name,
            "u": {
                "pk": {k: _enc(v) for k, v in u.pk().items()},
                "c": u.column,
                "v": _enc(u.row[u.column]),
            },
        }
        for u in rm.unlinks
    ]
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
    unlinked: dict[str, int] = {}
    for t, _ in _unlinked_rows(rm):
        unlinked[t.name] = unlinked.get(t.name, 0) + 1
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
            "unlinked": unlinked,
            "purge_after": item.purge_after.isoformat(),
        },
    )
    return item, False


def _warehouse(snapshot: dict[str, Any]) -> uuid.UUID | None:
    w = snapshot.get("warehouse_id")
    return uuid.UUID(w) if w else None


def _restore_error(item: TrashItem, e: DBAPIError) -> ApiError:
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
    """Put every kept row back, in the order it was taken, then the cleared links."""
    tables = Base.metadata.tables
    try:
        with _trash_mode(db), db.begin_nested():
            for entry in item.payload:
                if "r" not in entry:
                    continue
                t = tables[entry["t"]]
                skip = RESTORE_SKIP.get(t.name, set()) | _generated(t)
                values = {k: _dec(t.c[k], v) for k, v in entry["r"].items() if k not in skip}
                db.execute(sa.insert(t).values(values))
            for entry in item.payload:
                if "u" not in entry:
                    continue
                t, u = tables[entry["t"]], entry["u"]
                pk = {k: _dec(t.c[k], v) for k, v in u["pk"].items()}
                _set_link(db, t, pk, u["c"], _dec(t.c[u["c"]], u["v"]))
    except DBAPIError as e:
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
