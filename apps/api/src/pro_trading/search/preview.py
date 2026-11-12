"""Preview panel for one result: a few key fields, related records (real foreign keys only)
and actions that reuse existing screens / endpoints. Read live from the ERP tables (never from
search_documents), with the same visibility rule for the record and for every related record.
Search never changes money or stock data: every action is a link."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import Principal
from ..errors import ApiError
from ..gmail.models import GmailAttachment, GmailMessage
from ..models import (
    Customer,
    DocumentFile,
    GrnLine,
    InventoryBalance,
    InventoryLot,
    Invoice,
    InvoiceDocument,
    Payment,
    PaymentAllocation,
    PickingAttempt,
    PickingLine,
    Product,
    ProductAlias,
    ProductUnitProfile,
    SalesOrder,
    SalesOrderLine,
    ScanJob,
    Shipment,
    SourceDocument,
    StockCount,
    StockMovement,
    StockReservation,
    SupplierPayment,
    Vendor,
    Warehouse,
)
from .registry import Visibility


@dataclass
class Preview:
    doc_type: str
    id: uuid.UUID
    title: str
    subtitle: str
    url: str
    fields: list[dict[str, str]] = field(default_factory=list)
    related: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)

    def f(self, label: str, value: Any) -> None:
        if value not in (None, ""):
            self.fields.append({"label": label, "value": str(value)})

    def act(self, label: str, href: str, kind: str = "link") -> None:
        self.actions.append({"label": label, "href": href, "kind": kind})


def _money(v: Any, cur: str | None) -> str:
    return f"{cur or ''} {v:,.2f}".strip()


def _qty(v: Any) -> str:
    s = f"{v:f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


class _Ctx:
    def __init__(self, db: Session, p: Principal, vis: Visibility, pv: Preview):
        self.db, self.p, self.vis, self.pv = db, p, vis, pv

    def rel(
        self,
        doc_type: str,
        rid: uuid.UUID,
        wh: uuid.UUID | None,
        title: str,
        subtitle: str,
        url: str,
    ) -> None:
        """Add a related record only when this employee may see it."""
        if self.vis.can_see(doc_type, wh) and len(self.pv.related) < 25:
            self.pv.related.append(
                {"type": doc_type, "id": str(rid), "title": title, "subtitle": subtitle, "url": url}
            )

    def money_ok(self, doc_type: str) -> bool:
        return doc_type in self.vis.money


def _wh(db: Session, wid: uuid.UUID | None) -> str:
    return (db.scalar(select(Warehouse.code).where(Warehouse.id == wid)) or "") if wid else ""


# ------------------------------------------------------------------ per type


def _invoice(c: _Ctx, inv: Invoice) -> None:
    db, pv = c.db, c.pv
    cust = db.get(Customer, inv.customer_id)
    o = db.get(SalesOrder, inv.sales_order_id)
    pv.f("Customer", cust.name if cust else "")
    pv.f("Issued", f"{inv.issued_at:%Y-%m-%d}")
    pv.f("Due", f"{inv.due_at:%Y-%m-%d}")
    pv.f("Status", inv.state.replace("_", " ").lower())
    if c.money_ok("invoice"):
        pv.f("Total", _money(inv.total, inv.currency))
        pv.f("Paid", _money(inv.paid_amount, inv.currency))
    if cust:
        c.rel(
            "customer", cust.id, None, cust.name, cust.external_ref or "", f"/customers/{cust.id}"
        )
    if o:
        c.rel("sales_order", o.id, o.warehouse_id, o.order_no, o.status.lower(), f"/orders/{o.id}")
    pays = db.execute(
        select(Payment.id, Payment.payment_no, Payment.status)
        .join(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
        .where(PaymentAllocation.invoice_id == inv.id)
        .distinct()
    ).all()
    for pid, no, st in pays:
        c.rel("payment", pid, None, no, st.lower(), f"/payments/{pid}")
    has_pdf = db.scalar(
        select(func.count())
        .select_from(InvoiceDocument)
        .where(InvoiceDocument.invoice_id == inv.id)
    )
    pv.act("Open invoice", f"/invoices/{inv.id}")
    if has_pdf:
        pv.act("Print / download PDF", f"/api/v1/invoices/{inv.id}/pdf", "download")


def _order(c: _Ctx, o: SalesOrder) -> None:
    db, pv = c.db, c.pv
    cust = db.get(Customer, o.customer_id)
    pv.f("Customer", cust.name if cust else "")
    pv.f("Status", o.status.replace("_", " ").lower())
    pv.f("Warehouse", _wh(db, o.warehouse_id))
    pv.f("Created", f"{o.created_at:%Y-%m-%d}")
    n = db.scalar(
        select(func.count()).select_from(SalesOrderLine).where(SalesOrderLine.order_id == o.id)
    )
    pv.f("Lines", n)
    if cust:
        c.rel(
            "customer", cust.id, None, cust.name, cust.external_ref or "", f"/customers/{cust.id}"
        )
    for a in db.scalars(select(PickingAttempt).where(PickingAttempt.order_id == o.id)):
        c.rel("pick", a.id, a.warehouse_id, a.pick_code, a.status.lower(), f"/packing/{a.id}")
    for i in db.scalars(select(Invoice).where(Invoice.sales_order_id == o.id)):
        c.rel("invoice", i.id, i.warehouse_id, i.invoice_no, i.state.lower(), f"/invoices/{i.id}")
    pv.act("Open order", f"/orders/{o.id}")


def _pick(c: _Ctx, a: PickingAttempt) -> None:
    db, pv = c.db, c.pv
    o = db.get(SalesOrder, a.order_id)
    pv.f("Status", a.status.replace("_", " ").lower())
    pv.f("Warehouse", _wh(db, a.warehouse_id))
    if o:
        c.rel("sales_order", o.id, o.warehouse_id, o.order_no, o.status.lower(), f"/orders/{o.id}")
    inv = db.scalar(select(Invoice).where(Invoice.picking_attempt_id == a.id))
    if inv:
        c.rel(
            "invoice",
            inv.id,
            inv.warehouse_id,
            inv.invoice_no,
            inv.state.lower(),
            f"/invoices/{inv.id}",
        )
    pv.act("Open packing sheet", f"/packing/{a.id}")
    pv.act("Print packing sheet", f"/api/v1/pick-attempts/{a.id}/sheet.pdf?copies=2", "download")


def _customer(c: _Ctx, cu: Customer) -> None:
    db, pv = c.db, c.pv
    pv.f("Code", cu.external_ref)
    pv.f("Store / ship to", cu.ship_to_name)
    pv.f("Phone", cu.phone)
    pv.f("Email", cu.email)
    pv.f("Terms", f"{cu.payment_terms_days} days")
    for o in db.scalars(
        select(SalesOrder)
        .where(SalesOrder.customer_id == cu.id)
        .order_by(SalesOrder.created_at.desc())
        .limit(5)
    ):
        c.rel("sales_order", o.id, o.warehouse_id, o.order_no, o.status.lower(), f"/orders/{o.id}")
    for i in db.scalars(
        select(Invoice)
        .where(Invoice.customer_id == cu.id)
        .order_by(Invoice.issued_at.desc())
        .limit(5)
    ):
        sub = i.state.lower() + (
            f" · {_money(i.total, i.currency)}" if c.money_ok("invoice") else ""
        )
        c.rel("invoice", i.id, i.warehouse_id, i.invoice_no, sub, f"/invoices/{i.id}")
    for pmt in db.scalars(
        select(Payment)
        .where(Payment.customer_id == cu.id)
        .order_by(Payment.received_on.desc())
        .limit(5)
    ):
        sub = pmt.status.lower() + (
            f" · {_money(pmt.amount, pmt.currency)}" if c.money_ok("payment") else ""
        )
        c.rel("payment", pmt.id, None, pmt.payment_no, sub, f"/payments/{pmt.id}")
    pv.act("Open customer", f"/customers/{cu.id}")


def _product(c: _Ctx, pr: Product) -> None:
    db, pv = c.db, c.pv
    pv.f("SKU", pr.sku)
    pv.f("Stock unit", pr.stock_unit)
    pv.f("Status", "active" if pr.active else "inactive")
    aliases = db.scalars(
        select(ProductAlias.alias).where(ProductAlias.product_id == pr.id).limit(6)
    ).all()
    pv.f("Also called", ", ".join(aliases))
    units = db.scalars(
        select(ProductUnitProfile.code).where(
            ProductUnitProfile.product_id == pr.id, ProductUnitProfile.active.is_(True)
        )
    ).all()
    pv.f("Units", ", ".join(units))
    twin_sku = pr.sku[:-1] if len(pr.sku) == 4 else pr.sku + "P"
    twin = db.scalar(
        select(Product).where(Product.company_id == pr.company_id, Product.sku == twin_sku)
    )
    if twin:
        c.rel(
            "product",
            twin.id,
            None,
            twin.name,
            twin.sku + (" · packaged" if len(twin.sku) == 4 else ""),
            f"/products/{twin.id}",
        )
    for lot in db.scalars(
        select(InventoryLot)
        .where(InventoryLot.product_id == pr.id)
        .order_by(InventoryLot.created_at.desc())
        .limit(8)
    ):
        c.rel(
            "lot",
            lot.id,
            lot.warehouse_id,
            lot.lot_code,
            lot.status.lower(),
            f"/inventory/lots/{lot.id}",
        )
    pv.act("Open product", f"/products/{pr.id}")


def _vendor(c: _Ctx, v: Vendor) -> None:
    db, pv = c.db, c.pv
    pv.f("Code", v.code)
    pv.f("Country", v.country_code)
    pv.f("Currency", v.currency)
    pv.f("Email", v.email)
    for s in db.scalars(
        select(Shipment)
        .where(Shipment.vendor_id == v.id)
        .order_by(Shipment.created_at.desc())
        .limit(6)
    ):
        c.rel(
            "shipment", s.id, s.warehouse_id, s.shipment_no, s.status.lower(), f"/receiving/{s.id}"
        )
    for sp in db.scalars(
        select(SupplierPayment)
        .where(SupplierPayment.vendor_id == v.id)
        .order_by(SupplierPayment.paid_on.desc())
        .limit(5)
    ):
        sub = f"{sp.paid_on}" + (
            f" · {_money(sp.amount, sp.currency)}" if c.money_ok("supplier_payment") else ""
        )
        c.rel(
            "supplier_payment", sp.id, None, sp.payment_no, sub, f"/suppliers/{v.id}?tab=payments"
        )
    pv.act(
        "Open supplier", f"/suppliers/{v.id}" if c.p.can("supplier.ledger.read") else "/suppliers"
    )


def _lot(c: _Ctx, lot: InventoryLot) -> None:
    db, pv = c.db, c.pv
    pr = db.get(Product, lot.product_id)
    pv.f("Product", f"{pr.sku} {pr.name}" if pr else "")
    pv.f("Batch", lot.batch_ref)
    pv.f("Warehouse", _wh(db, lot.warehouse_id))
    pv.f("Status", lot.status.lower())
    pv.f("Received", f"{lot.received_at:%Y-%m-%d}" if lot.received_at else "")
    pv.f("Expires", f"{lot.expires_at:%Y-%m-%d}" if lot.expires_at else "")
    for unit, qty, res in db.execute(
        select(
            InventoryBalance.unit_code, InventoryBalance.qty, InventoryBalance.reserved_qty
        ).where(InventoryBalance.lot_id == lot.id)
    ):
        pv.f(f"On hand ({unit})", _qty(qty) + (f", {_qty(res)} reserved" if res else ""))
    if pr:
        c.rel("product", pr.id, None, pr.name, pr.sku, f"/products/{pr.id}")
    src = db.execute(
        select(Shipment, Vendor)
        .join(GrnLine, GrnLine.shipment_id == Shipment.id)
        .join(Vendor, Vendor.id == Shipment.vendor_id)
        .where(GrnLine.lot_id == lot.id)
        .limit(1)
    ).first()
    if src:
        s, v = src
        c.rel(
            "shipment",
            s.id,
            s.warehouse_id,
            s.shipment_no,
            (s.grn_no or s.status).lower(),
            f"/receiving/{s.id}",
        )
        c.rel("vendor", v.id, None, v.name, v.code, f"/suppliers/{v.id}")
    n = db.scalar(
        select(func.count()).select_from(StockMovement).where(StockMovement.lot_id == lot.id)
    )
    pv.f("Stock movements", n)
    # Sales: orders holding a reservation on this batch (stock_reservations.source_ref is the
    # order number when source_type is SALES_ORDER) and packing lines picked from it.
    refs = db.scalars(
        select(StockReservation.source_ref)
        .where(StockReservation.lot_id == lot.id, StockReservation.source_type == "SALES_ORDER")
        .distinct()
        .limit(10)
    ).all()
    if refs:
        for o in db.scalars(
            select(SalesOrder).where(
                SalesOrder.company_id == lot.company_id, SalesOrder.order_no.in_(refs)
            )
        ):
            c.rel(
                "sales_order",
                o.id,
                o.warehouse_id,
                o.order_no,
                "reserved from this batch",
                f"/orders/{o.id}",
            )
    for a in db.scalars(
        select(PickingAttempt)
        .join(PickingLine, PickingLine.attempt_id == PickingAttempt.id)
        .where(PickingLine.lot_id == lot.id)
        .distinct()
        .limit(10)
    ):
        c.rel(
            "pick", a.id, a.warehouse_id, a.pick_code, "packed from this batch", f"/packing/{a.id}"
        )
        inv = db.scalar(select(Invoice).where(Invoice.picking_attempt_id == a.id))
        if inv:
            c.rel(
                "invoice",
                inv.id,
                inv.warehouse_id,
                inv.invoice_no,
                "sold from this batch",
                f"/invoices/{inv.id}",
            )
    pv.act("Open batch", f"/inventory/lots/{lot.id}")


def _shipment(c: _Ctx, s: Shipment) -> None:
    db, pv = c.db, c.pv
    v = db.get(Vendor, s.vendor_id)
    pv.f("Supplier", v.name if v else "")
    pv.f("Status", s.status.lower())
    pv.f("GRN", s.grn_no)
    pv.f("Airway bill", s.awb_no)
    pv.f("Supplier invoice", s.vendor_invoice_no)
    pv.f("Expected", s.expected_on)
    batches = db.scalars(
        select(GrnLine.batch_ref)
        .where(GrnLine.shipment_id == s.id, GrnLine.batch_ref.is_not(None))
        .distinct()
    ).all()
    pv.f("Batches", ", ".join(sorted(b for b in batches if b)[:8]))
    if v:
        c.rel("vendor", v.id, None, v.name, v.code, f"/suppliers/{v.id}")
    for lot in db.scalars(
        select(InventoryLot)
        .join(GrnLine, GrnLine.lot_id == InventoryLot.id)
        .where(GrnLine.shipment_id == s.id)
        .limit(10)
    ):
        c.rel(
            "lot",
            lot.id,
            lot.warehouse_id,
            lot.lot_code,
            lot.status.lower(),
            f"/inventory/lots/{lot.id}",
        )
    for sp in db.scalars(select(SupplierPayment).where(SupplierPayment.shipment_id == s.id)):
        c.rel(
            "supplier_payment",
            sp.id,
            None,
            sp.payment_no,
            str(sp.paid_on),
            f"/suppliers/{sp.vendor_id}?tab=payments",
        )
    pv.act("Open shipment", f"/receiving/{s.id}")


def _payment(c: _Ctx, pm: Payment) -> None:
    db, pv = c.db, c.pv
    cust = db.get(Customer, pm.customer_id)
    pv.f("Customer", cust.name if cust else "")
    pv.f("Method", pm.method.lower())
    pv.f("Status", pm.status.lower())
    pv.f("Received", pm.received_on)
    pv.f("Customer's reference", pm.payer_ref)
    pv.f("Bank reference", pm.verification_ref)
    if c.money_ok("payment"):
        pv.f("Amount", _money(pm.amount, pm.currency))
        pv.f("Allocated", _money(pm.allocated_amount, pm.currency))
    if cust:
        c.rel(
            "customer", cust.id, None, cust.name, cust.external_ref or "", f"/customers/{cust.id}"
        )
    inv_ids = set(
        db.scalars(
            select(PaymentAllocation.invoice_id).where(PaymentAllocation.payment_id == pm.id)
        )
    )
    if pm.reported_invoice_id:
        inv_ids.add(pm.reported_invoice_id)
    for i in db.scalars(select(Invoice).where(Invoice.id.in_(inv_ids))) if inv_ids else []:
        c.rel("invoice", i.id, i.warehouse_id, i.invoice_no, i.state.lower(), f"/invoices/{i.id}")
    pv.act("Open payment", f"/payments/{pm.id}")


def _supplier_payment(c: _Ctx, sp: SupplierPayment) -> None:
    db, pv = c.db, c.pv
    v = db.get(Vendor, sp.vendor_id)
    pv.f("Supplier", v.name if v else "")
    pv.f("Paid on", sp.paid_on)
    pv.f("Method", sp.method.lower())
    pv.f("Reference", sp.reference)
    if c.money_ok("supplier_payment"):
        pv.f("Amount", _money(sp.amount, sp.currency))
    pv.f("Voided", sp.void_reason)
    if v:
        c.rel("vendor", v.id, None, v.name, v.code, f"/suppliers/{v.id}")
    if sp.shipment_id:
        s = db.get(Shipment, sp.shipment_id)
        if s:
            c.rel(
                "shipment",
                s.id,
                s.warehouse_id,
                s.shipment_no,
                s.status.lower(),
                f"/receiving/{s.id}",
            )
    pv.act("Open supplier payments", f"/suppliers/{sp.vendor_id}?tab=payments")


def _scan(c: _Ctx, j: ScanJob) -> None:
    from ..models import OcrExtraction

    db, pv = c.db, c.pv
    pv.f("Type", j.job_type.replace("_", " ").lower())
    pv.f("Status", j.status.lower())
    pv.f("Warehouse", _wh(db, j.warehouse_id))
    if j.document_id:
        pv.f(
            "File",
            db.scalar(
                select(SourceDocument.original_filename).where(SourceDocument.id == j.document_id)
            ),
        )
    ex = db.scalar(
        select(OcrExtraction)
        .where(OcrExtraction.scan_job_id == j.id, OcrExtraction.status != "SUPERSEDED")
        .order_by(OcrExtraction.run_no.desc())
        .limit(1)
    )
    if ex:
        pv.f("Reading", ex.status.replace("_", " ").lower())
        if ex.sales_order_id:
            o = db.get(SalesOrder, ex.sales_order_id)
            if o:
                c.rel(
                    "sales_order",
                    o.id,
                    o.warehouse_id,
                    o.order_no,
                    o.status.lower(),
                    f"/orders/{o.id}",
                )
        if ex.shipment_id:
            s = db.get(Shipment, ex.shipment_id)
            if s:
                c.rel(
                    "shipment",
                    s.id,
                    s.warehouse_id,
                    s.shipment_no,
                    s.status.lower(),
                    f"/receiving/{s.id}",
                )
        if ex.customer_id:
            cu = db.get(Customer, ex.customer_id)
            if cu:
                c.rel(
                    "customer", cu.id, None, cu.name, cu.external_ref or "", f"/customers/{cu.id}"
                )
        if ex.status == "NEEDS_REVIEW":
            pv.act("Review reading", f"/scan/review/{ex.id}")
    pv.act("Open scan", f"/scan/{j.id}")


def _count(c: _Ctx, sc: StockCount) -> None:
    pv = c.pv
    pv.f("Status", sc.status.lower())
    pv.f("Warehouse", _wh(c.db, sc.warehouse_id))
    pv.f("Counted", f"{sc.created_at:%Y-%m-%d}")
    pv.f("Note", sc.note)
    pv.act("Open counts", "/inventory?tab=counts")


def _document(c: _Ctx, f: DocumentFile) -> None:
    db, pv = c.db, c.pv
    pv.f("Kind", f.kind.replace("_", " ").lower())
    pv.f("Folder", f.folder)
    pv.f("Drive", f.sync_status.replace("_", " ").lower())
    if f.invoice_id:
        i = db.get(Invoice, f.invoice_id)
        if i:
            c.rel(
                "invoice", i.id, i.warehouse_id, i.invoice_no, i.state.lower(), f"/invoices/{i.id}"
            )
    if f.shipment_id:
        s = db.get(Shipment, f.shipment_id)
        if s:
            c.rel(
                "shipment",
                s.id,
                s.warehouse_id,
                s.shipment_no,
                s.status.lower(),
                f"/receiving/{s.id}",
            )
    if f.customer_id:
        cu = db.get(Customer, f.customer_id)
        if cu:
            c.rel("customer", cu.id, None, cu.name, cu.external_ref or "", f"/customers/{cu.id}")
    pv.act("Open documents", "/documents")
    pv.act("Download", f"/api/v1/documents/{f.id}/content", "download")


def _gmail(c: _Ctx, m: GmailMessage) -> None:
    db, pv = c.db, c.pv
    pv.f("From", f"{m.from_name} <{m.from_email}>" if m.from_name else m.from_email)
    pv.f("Received", f"{m.received_at:%Y-%m-%d %H:%M}")
    pv.f("Preview", m.snippet)
    files = db.scalars(
        select(GmailAttachment.filename).where(GmailAttachment.message_id == m.id)
    ).all()
    pv.f("Attachments", ", ".join(files[:6]))
    pv.act("Open in Gmail Inbox", f"/gmail?message={m.id}")


@dataclass(frozen=True)
class _Spec:
    model: Any
    warehouse: Callable[[Any], uuid.UUID | None]
    title: Callable[[Any], str]
    url: Callable[[Any], str]
    fill: Callable[[_Ctx, Any], None]


SPECS: dict[str, _Spec] = {
    "invoice": _Spec(
        Invoice,
        lambda r: r.warehouse_id,
        lambda r: r.invoice_no,
        lambda r: f"/invoices/{r.id}",
        _invoice,
    ),
    "sales_order": _Spec(
        SalesOrder,
        lambda r: r.warehouse_id,
        lambda r: r.order_no,
        lambda r: f"/orders/{r.id}",
        _order,
    ),
    "pick": _Spec(
        PickingAttempt,
        lambda r: r.warehouse_id,
        lambda r: r.pick_code,
        lambda r: f"/packing/{r.id}",
        _pick,
    ),
    "customer": _Spec(
        Customer, lambda r: None, lambda r: r.name, lambda r: f"/customers/{r.id}", _customer
    ),
    "product": _Spec(
        Product, lambda r: None, lambda r: r.name, lambda r: f"/products/{r.id}", _product
    ),
    "vendor": _Spec(
        Vendor, lambda r: None, lambda r: r.name, lambda r: f"/suppliers/{r.id}", _vendor
    ),
    "lot": _Spec(
        InventoryLot,
        lambda r: r.warehouse_id,
        lambda r: r.lot_code,
        lambda r: f"/inventory/lots/{r.id}",
        _lot,
    ),
    "shipment": _Spec(
        Shipment,
        lambda r: r.warehouse_id,
        lambda r: r.shipment_no,
        lambda r: f"/receiving/{r.id}",
        _shipment,
    ),
    "payment": _Spec(
        Payment, lambda r: None, lambda r: r.payment_no, lambda r: f"/payments/{r.id}", _payment
    ),
    "supplier_payment": _Spec(
        SupplierPayment,
        lambda r: None,
        lambda r: r.payment_no,
        lambda r: f"/suppliers/{r.vendor_id}?tab=payments",
        _supplier_payment,
    ),
    "scan": _Spec(
        ScanJob, lambda r: r.warehouse_id, lambda r: r.job_no, lambda r: f"/scan/{r.id}", _scan
    ),
    "stock_count": _Spec(
        StockCount,
        lambda r: r.warehouse_id,
        lambda r: r.count_no,
        lambda r: "/inventory?tab=counts",
        _count,
    ),
    "document": _Spec(
        DocumentFile,
        lambda r: r.warehouse_id,
        lambda r: r.filename,
        lambda r: "/documents",
        _document,
    ),
    "gmail": _Spec(
        GmailMessage,
        lambda r: None,
        lambda r: r.subject or "(no subject)",
        lambda r: f"/gmail?message={r.id}",
        _gmail,
    ),
}


def load_visible(db: Session, p: Principal, vis: Visibility, doc_type: str, rid: uuid.UUID) -> Any:
    """The source row, or 404 when it does not exist, belongs to another company, or this
    employee may not see it (the same answer each time, so nothing is revealed)."""
    spec = SPECS.get(doc_type)
    row = db.get(spec.model, rid) if spec else None
    if (
        spec is None
        or row is None
        or row.company_id != p.company_id
        or not vis.can_see(doc_type, spec.warehouse(row))
    ):
        raise ApiError(404, "NOT_FOUND", "Not found")
    return row


def build_preview(
    db: Session, p: Principal, vis: Visibility, doc_type: str, rid: uuid.UUID
) -> Preview:
    row = load_visible(db, p, vis, doc_type, rid)
    spec = SPECS[doc_type]
    from .indexer import BUILDERS

    doc = BUILDERS[doc_type](db, rid)
    pv = Preview(doc_type, rid, spec.title(row), (doc or {}).get("subtitle", ""), spec.url(row))
    if doc_type == "vendor" and not p.can("supplier.ledger.read"):
        pv.url = "/suppliers"
    spec.fill(_Ctx(db, p, vis, pv), row)
    return pv
