"""Activity feed: the audit log's business events as plain sentences ("Sam Sales created an
invoice — INV-2026-000041"). Read-only; nothing here writes.

Only committed, successful business actions are shown (sign-ins, permission denials, settings
and other housekeeping are left out). Everyone sees their own activity; people with
`audit.read` see their colleagues' too (company-wide, or for the warehouses their grant covers).
Objects and amounts come from the audit row or the stored record, never invented.
"""

import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import ColumnElement, and_, or_, select, true
from sqlalchemy.orm import Session

from .models import (
    AuditEvent,
    Customer,
    Employee,
    InventoryLot,
    Invoice,
    Payment,
    PickingAttempt,
    Product,
    SalesOrder,
    Shipment,
)

# Tones the web app knows: success (green), info (blue), price (purple), stock (orange),
# danger (red), neutral (grey).
Detail = dict[str, Any]


@dataclass(frozen=True)
class Row:
    action: str
    target_type: str | None
    target_id: str | None
    detail: Detail


@dataclass(frozen=True)
class Kind:
    verb: str
    tag: str
    tone: str
    icon: str
    obj: Callable[["Lookups", Row], str | None]
    link: Callable[[Row], str | None] = lambda r: None
    note: Callable[["Lookups", Row], str | None] = lambda lk, r: None


def _d(key: str) -> Callable[["Lookups", Row], str | None]:
    return lambda lk, r: _s(r.detail.get(key))


def _s(v: Any) -> str | None:
    return str(v) if v not in (None, "") else None


def _to(prefix: str, key: str | None = None) -> Callable[[Row], str | None]:
    """In-app link to the record: the audit target id, or an id stored in the detail."""

    def f(r: Row) -> str | None:
        raw = r.detail.get(key) if key else r.target_id
        try:
            return f"{prefix}/{uuid.UUID(str(raw))}"
        except (ValueError, TypeError):
            return None

    return f


def _money(lk: "Lookups", r: Row, key: str = "amount", cur_key: str = "currency") -> str | None:
    try:
        v = Decimal(str(r.detail.get(key)))
    except (InvalidOperation, ValueError):
        return None
    return f"{r.detail.get(cur_key) or 'CAD'} {v:,.2f}"


def _qty(v: Any) -> str:
    try:
        d = Decimal(str(v)).normalize()
    except (InvalidOperation, ValueError):
        return str(v)
    return f"{d:+f}" if d < 0 or str(v).startswith("+") else f"+{d:f}"


def _price_change(lk: "Lookups", r: Row) -> str | None:
    new, old, unit = r.detail.get("rate"), r.detail.get("old_rate"), r.detail.get("unit_code")
    if new is None:
        return None
    per = f" per {str(unit).lower()}" if unit else ""
    if old is None:
        return f"New price {Decimal(str(new)):,.2f}{per}"
    return f"{Decimal(str(old)):,.2f} → {Decimal(str(new)):,.2f}{per}"


def _stock_change(lk: "Lookups", r: Row) -> str | None:
    qty = r.detail.get("delta_qty", r.detail.get("qty"))
    if qty is None:
        return None
    sign = _qty(qty) if "delta_qty" in r.detail else f"-{Decimal(str(qty)).normalize():f}"
    return f"Qty {sign} {r.detail.get('unit_code', '')}".strip()


_KIND_NOUN = {
    "product": "a product",
    "case_size": "a case size",
    "customer": "a customer",
    "vendor": "a supplier",
    "employee": "a user",
    "document": "a document",
    "order_form_submission": "a website order",
}


def _trash(verb: str, tag: str, tone: str, icon: str) -> Kind:
    return Kind(
        verb=verb,
        tag=tag,
        tone=tone,
        icon=icon,
        obj=_d("label"),
    )


KINDS: dict[str, Kind] = {
    # catalog & prices
    "product.create": Kind(
        "added a new product", "Product Added", "success", "plus",
        lambda lk, r: _s(r.detail.get("name")) or lk.product(r.target_id), _to("/products"),
    ),
    "product.update": Kind(
        "updated a product", "Product Updated", "info", "products",
        lambda lk, r: lk.product(r.target_id), _to("/products"),
    ),
    "price_entry.set": Kind(
        "updated pricing", "Price Update", "price", "pricing",
        lambda lk, r: lk.product(r.detail.get("product_id")), _to("/products", "product_id"),
        _price_change,
    ),
    "price_entry.remove": Kind(
        "removed a price", "Price Update", "price", "pricing",
        lambda lk, r: lk.product(r.detail.get("product_id")), _to("/products", "product_id"),
    ),
    "price_version.import": Kind(
        "imported a price list", "Price Update", "price", "pricing", _d("version_code"),
        _to("/products/prices"),
    ),
    "price_version.activate": Kind(
        "activated prices", "Price Update", "price", "pricing", _d("version_code"),
        _to("/products/prices"),
    ),
    "customer.create": Kind(
        "added a customer", "Customer Added", "success", "users",
        lambda lk, r: _s(r.detail.get("name")) or lk.customer(r.target_id), _to("/customers"),
    ),
    "customer.update": Kind(
        "updated a customer", "Customer Updated", "info", "users",
        lambda lk, r: _s((r.detail.get("after") or {}).get("name")) or lk.customer(r.target_id),
        _to("/customers"),
    ),
    "vendor.create": Kind(
        "added a supplier", "Supplier Added", "success", "store", _d("name"), _to("/suppliers"),
    ),
    # sales
    "sales_order.create": Kind(
        "created an order", "Order Created", "info", "cart",
        lambda lk, r: _s(r.detail.get("order_no")) or lk.order(r.target_id), _to("/orders"),
    ),
    "sales_order.confirm": Kind(
        "confirmed an order", "Order Confirmed", "info", "check",
        lambda lk, r: _s(r.detail.get("order_no")) or lk.order(r.target_id), _to("/orders"),
    ),
    "sales_order.cancel": Kind(
        "cancelled an order", "Order Cancelled", "danger", "x",
        lambda lk, r: lk.order(r.target_id), _to("/orders"),
    ),
    "sales_order.close": Kind(
        "closed an order", "Order Closed", "neutral", "check",
        lambda lk, r: lk.order(r.target_id), _to("/orders"),
    ),
    "pick.submit": Kind(
        "packed an order", "Packing", "stock", "boxes",
        lambda lk, r: lk.pick(r.target_id), _to("/packing"),
    ),
    "pick.verify": Kind(
        "checked a packing sheet", "Packing", "success", "check",
        lambda lk, r: lk.pick(r.target_id), _to("/packing"),
    ),
    "invoice.issue": Kind(
        "created an invoice", "Invoice Created", "info", "invoices",
        lambda lk, r: _s(r.detail.get("invoice_no")) or lk.invoice(r.target_id),
        _to("/invoices"),
        lambda lk, r: _money(lk, r, "total"),
    ),
    "invoice.pod": Kind(
        "filed proof of delivery", "Delivery Proof", "success", "file",
        _d("invoice_no"), _to("/invoices"),
    ),
    "payment.record": Kind(
        "recorded a payment", "Payment Recorded", "info", "payments",
        _d("payment_no"), _to("/payments"), _money,
    ),
    "payment.verify": Kind(
        "confirmed a payment", "Payment Confirmed", "success", "check",
        lambda lk, r: _s(r.detail.get("payment_no")) or lk.payment(r.target_id),
        _to("/payments"),
    ),
    "payment.reject": Kind(
        "rejected a payment", "Payment Rejected", "danger", "x",
        lambda lk, r: _s(r.detail.get("payment_no")) or lk.payment(r.target_id),
        _to("/payments"),
    ),
    "supplier_payment.record": Kind(
        "paid a supplier", "Supplier Payment", "info", "coins", _d("payment_no"),
        lambda r: "/suppliers/payments", _money,
    ),
    "supplier_payment.void": Kind(
        "voided a supplier payment", "Supplier Payment", "danger", "x", _d("payment_no"),
        lambda r: "/suppliers/payments",
    ),
    # stock & receiving
    "inventory.adjust": Kind(
        "adjusted inventory", "Inventory Update", "stock", "inventory",
        lambda lk, r: lk.lot_product(r.detail.get("lot_id")),
        lambda r: "/inventory?tab=movements", _stock_change,
    ),
    "inventory.damage": Kind(
        "recorded damaged stock", "Inventory Update", "stock", "inventory",
        lambda lk, r: lk.lot_product(r.detail.get("lot_id")),
        lambda r: "/inventory?tab=movements", _stock_change,
    ),
    "inventory.count_post": Kind(
        "posted a stock count", "Inventory Update", "stock", "inventory", _d("count_no"),
        lambda r: "/inventory?tab=counts",
    ),
    "shipment.create": Kind(
        "logged a delivery", "Receiving", "info", "receiving",
        lambda lk, r: _s(r.detail.get("shipment_no")) or lk.shipment(r.target_id),
        _to("/receiving"),
    ),
    "shipment.post": Kind(
        "received stock", "Stock Received", "success", "receiving",
        lambda lk, r: _s(r.detail.get("grn_no")) or lk.shipment(r.target_id), _to("/receiving"),
    ),
    "shipment.purchase": Kind(
        "saved a purchase (stock added)", "Purchase", "success", "receiving",
        lambda lk, r: _s(r.detail.get("shipment_no")) or lk.shipment(r.target_id),
        _to("/purchases"),
    ),
    "shipment.add_lines": Kind(
        "added lines to a purchase (stock added)", "Purchase", "success", "receiving",
        _d("shipment_no"), _to("/purchases"),
    ),
    "shipment.stock": Kind(
        "added an old shipment to stock", "Purchase", "success", "receiving",
        _d("shipment_no"), _to("/purchases"),
    ),
    "shipment.quick_receive": Kind(
        "quick-received goods found in the warehouse", "Quick receive", "warning", "receiving",
        lambda lk, r: " ".join(
            x for x in (_s(r.detail.get("entered")), _s(r.detail.get("product"))) if x
        ) or None,
        _to("/purchases"), lambda lk, r: _s(r.detail.get("reason")),
    ),
    "shipment.complete": Kind(
        "matched a quick receive to its invoice", "Purchase", "success", "receiving",
        _d("shipment_no"), _to("/purchases"),
    ),
    "shipment.verify": Kind(
        "verified an airport shipment", "Purchase", "info", "receiving",
        _d("shipment_no"), _to("/purchases"), lambda lk, r: _s(r.detail.get("reason")),
    ),
    "shipment.correction": Kind(
        "recorded a correction on a purchase", "Purchase", "warning", "receiving",
        _d("shipment_no"), _to("/purchases"),
    ),
    "shipment.correction_approve": Kind(
        "approved a purchase correction", "Purchase", "warning", "receiving",
        _d("shipment_no"), _to("/purchases"),
    ),
    "shipment.correction_reject": Kind(
        "refused a purchase correction", "Purchase", "info", "receiving",
        _d("shipment_no"), _to("/purchases"), lambda lk, r: _s(r.detail.get("note")),
    ),
    "shipment.correction_reviewed": Kind(
        "reviewed a flagged shortage", "Purchase", "info", "receiving",
        _d("shipment_no"), _to("/purchases"), lambda lk, r: _s(r.detail.get("note")),
    ),
    # scans & website orders
    "ocr_extraction.accept": Kind(
        "approved a scanned order", "Scan Approved", "success", "scan", _d("order_no"),
        _to("/orders", "sales_order_id"),
    ),
    "order_connect.convert": Kind(
        "converted a website order", "Website Order", "success", "store", _d("order_no"),
        _to("/orders", "sales_order_id"),
    ),
    # people
    "employee.create": Kind(
        "invited a user", "User Invited", "info", "user", _d("display_name"),
        lambda r: "/settings/users",
    ),
    # trash (moving to the bin is reversible; purging is not)
    "trash.moved": _trash("removed", "Removed", "danger", "trash"),
    "trash.restored": _trash("restored", "Restored", "success", "refresh"),
    "trash.purged": _trash("permanently deleted", "Deleted", "danger", "trash"),
}  # fmt: skip

ACTIONS = tuple(KINDS)


def _trash_verb(kind: Kind, r: Row) -> str:
    noun = _KIND_NOUN.get(r.target_type or "", "an item")
    return f"{kind.verb} {noun}"


# ------------------------------------------------------------------ batched lookups

_LINK = re.compile(
    r"^/(products|orders|packing|picking|invoices|payments|receiving|customers)/([0-9a-f-]{36})$"
)
_LINK_TABLE = {
    "products": "product",
    "orders": "order",
    "packing": "pick",
    "picking": "pick",  # links saved before the rename to "Packing"
    "invoices": "invoice",
    "payments": "payment",
    "receiving": "shipment",
    "customers": "customer",
}


class Lookups:
    """Names for ids referenced by one page of rows, fetched with one query per table."""

    def __init__(self, db: Session, company_id: uuid.UUID, rows: Iterable[Row]) -> None:
        self.db, self.company_id = db, company_id
        want: dict[str, set[uuid.UUID]] = {}

        def add(kind: str, raw: Any) -> None:
            try:
                want.setdefault(kind, set()).add(uuid.UUID(str(raw)))
            except (ValueError, TypeError):
                pass

        for r in rows:
            k = KINDS.get(r.action)
            if k is None:
                continue
            m = _LINK.match(k.link(r) or "")
            if m:
                add(_LINK_TABLE[m.group(1)], m.group(2))
            if r.action.startswith(("product.",)):
                add("product", r.target_id)
            if r.action.startswith("price_entry."):
                add("product", r.detail.get("product_id"))
            if r.action.startswith("sales_order."):
                add("order", r.target_id)
            if r.action.startswith("pick."):
                add("pick", r.target_id)
            if r.action.startswith("invoice."):
                add("invoice", r.target_id)
            if r.action.startswith("payment."):
                add("payment", r.target_id)
            if r.action.startswith("shipment."):
                add("shipment", r.target_id)
            if r.action.startswith("customer."):
                add("customer", r.target_id)
            if r.action.startswith("inventory.") and r.detail.get("lot_id"):
                add("lot", r.detail.get("lot_id"))
        c = company_id
        self._products = self._names(
            Product, want.get("product"), lambda p: f"{p.name}", Product.company_id == c
        )
        lots = want.get("lot") or set()
        self._lots: dict[uuid.UUID, str] = {}
        if lots:
            for lot_id, name in db.execute(
                select(InventoryLot.id, Product.name)
                .join(Product, Product.id == InventoryLot.product_id)
                .where(InventoryLot.id.in_(lots), InventoryLot.company_id == c)
            ):
                self._lots[lot_id] = name
        self._orders = self._names(
            SalesOrder, want.get("order"), lambda o: o.order_no, SalesOrder.company_id == c
        )
        self._picks = self._names(
            PickingAttempt, want.get("pick"), lambda a: a.pick_code, PickingAttempt.company_id == c
        )
        self._invoices = self._names(
            Invoice, want.get("invoice"), lambda i: i.invoice_no, Invoice.company_id == c
        )
        self._payments = self._names(
            Payment, want.get("payment"), lambda p: p.payment_no, Payment.company_id == c
        )
        self._shipments = self._names(
            Shipment, want.get("shipment"), lambda s: s.shipment_no, Shipment.company_id == c
        )
        self._customers = self._names(
            Customer, want.get("customer"), lambda x: x.name, Customer.company_id == c
        )

    def _names(
        self,
        model: Any,
        ids: set[uuid.UUID] | None,
        label: Callable[[Any], str],
        scope: ColumnElement[bool],
    ) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        return {
            o.id: label(o) for o in self.db.scalars(select(model).where(model.id.in_(ids), scope))
        }

    @staticmethod
    def _get(table: dict[uuid.UUID, str], raw: Any) -> str | None:
        try:
            return table.get(uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            return None

    def exists(self, link: str | None) -> bool:
        """False for a link to a record that is gone (e.g. cleared demo data)."""
        m = _LINK.match(link or "")
        if not m:
            return True
        table = {
            "product": self._products,
            "order": self._orders,
            "pick": self._picks,
            "invoice": self._invoices,
            "payment": self._payments,
            "shipment": self._shipments,
            "customer": self._customers,
        }[_LINK_TABLE[m.group(1)]]
        return self._get(table, m.group(2)) is not None

    def product(self, raw: Any) -> str | None:
        return self._get(self._products, raw)

    def lot_product(self, raw: Any) -> str | None:
        return self._get(self._lots, raw)

    def order(self, raw: Any) -> str | None:
        return self._get(self._orders, raw)

    def pick(self, raw: Any) -> str | None:
        return self._get(self._picks, raw)

    def invoice(self, raw: Any) -> str | None:
        return self._get(self._invoices, raw)

    def payment(self, raw: Any) -> str | None:
        return self._get(self._payments, raw)

    def shipment(self, raw: Any) -> str | None:
        return self._get(self._shipments, raw)

    def customer(self, raw: Any) -> str | None:
        return self._get(self._customers, raw)


# ------------------------------------------------------------------ query


@dataclass(frozen=True)
class Scope:
    """What the reader may see: their own rows always; others' with audit.read."""

    employee_id: uuid.UUID
    company_id: uuid.UUID
    all_company: bool  # company-wide audit.read
    warehouses: frozenset[uuid.UUID]  # warehouse-scoped audit.read

    @property
    def can_see_others(self) -> bool:
        return self.all_company or bool(self.warehouses)


def visible(scope: Scope, *, mine: bool, actor: uuid.UUID | None) -> ColumnElement[bool]:
    base = and_(
        AuditEvent.company_id == scope.company_id,
        AuditEvent.success.is_(True),
        AuditEvent.action.in_(ACTIONS),
    )
    own = AuditEvent.actor_id == scope.employee_id
    if mine or not scope.can_see_others:
        who: ColumnElement[bool] = own
    elif scope.all_company:
        who = true()
    else:
        who = or_(own, AuditEvent.warehouse_id.in_(scope.warehouses))
    by = AuditEvent.actor_id == actor if actor is not None else true()
    return and_(base, who, by)


def parse_cursor(raw: str | None) -> tuple[datetime, uuid.UUID] | None:
    if not raw:
        return None
    at, _, rid = raw.partition("|")
    try:
        return datetime.fromisoformat(at), uuid.UUID(rid)
    except ValueError:
        return None


def after_cursor(cur: tuple[datetime, uuid.UUID] | None) -> ColumnElement[bool]:
    if cur is None:
        return true()
    at, rid = cur
    return or_(AuditEvent.created_at < at, and_(AuditEvent.created_at == at, AuditEvent.id < rid))


def describe(db: Session, company_id: uuid.UUID, events: list[AuditEvent]) -> list[dict[str, Any]]:
    rows = [Row(e.action, e.target_type, e.target_id, e.detail_redacted or {}) for e in events]
    lk = Lookups(db, company_id, rows)
    actor_ids = {e.actor_id for e in events if e.actor_id}
    names = (
        dict(
            db.execute(
                select(Employee.id, Employee.display_name).where(
                    Employee.id.in_(actor_ids), Employee.company_id == company_id
                )
            ).all()
        )
        if actor_ids
        else {}
    )
    out = []
    for e, r in zip(events, rows, strict=True):
        k = KINDS[e.action]
        verb = _trash_verb(k, r) if e.action.startswith("trash.") else k.verb
        try:
            extra = k.note(lk, r)
        except (InvalidOperation, ValueError, TypeError):
            extra = None
        out.append(
            {
                "id": str(e.id),
                "at": e.created_at.isoformat(),
                "action": e.action,
                "actor": {
                    "id": str(e.actor_id) if e.actor_id else None,
                    "name": names.get(e.actor_id, "Someone") if e.actor_id else "Website",
                },
                "verb": verb,
                "object": k.obj(lk, r),
                "extra": extra,
                "link": link if lk.exists(link := k.link(r)) else None,
                "tag": k.tag,
                "tone": k.tone,
                "icon": k.icon,
            }
        )
    return out
