"""Builds search documents from the ERP tables and keeps them in step.

- `reindex(db, doc_type, id)`: rebuild one document from the source rows (upsert), or delete
  it when the source row is gone (deleted / moved to the Trash). Idempotent: running it twice
  gives the same row.
- `drain(db)`: called by the Celery worker every few seconds. Claims queued changes with
  SKIP LOCKED, rebuilds each document once (many changes to one record coalesce), deletes the
  queue rows it handled, and backs off / marks FAILED after repeated errors. It runs in its own
  transactions and never touches the ERP tables, so it can never block an invoice or a stock
  posting.
- `backfill` / `reconcile`: full rebuild, and a check that every source row has a document,
  no document is orphaned, and nothing is stuck in the queue.
"""

import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, delete, func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..gmail.models import GmailAttachment, GmailMessage
from ..models import (
    Customer,
    CustomerGroup,
    DeclaredLine,
    DocumentFile,
    GrnLine,
    InventoryLot,
    Invoice,
    InvoiceLine,
    LandedCost,
    OcrExtraction,
    OcrField,
    Payment,
    PaymentAllocation,
    PickingAttempt,
    Product,
    ProductAlias,
    ProductCategory,
    ProductUnitProfile,
    SalesOrder,
    SalesOrderLine,
    ScanJob,
    Shipment,
    ShipmentDocument,
    SourceDocument,
    StockCount,
    SupplierPayment,
    Vendor,
    Warehouse,
)
from .models import SearchDocument, SearchQueueItem
from .registry import INDEXED_TYPES, fold, ident_forms

log = logging.getLogger("pro_trading.search")

MAX_ATTEMPTS = 8
OCR_TEXT_LIMIT = 100_000

Doc = dict[str, Any]


def _d(v: datetime | date | None) -> date | None:
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


def _join(*parts: Any) -> str:
    return " ".join(str(p) for p in parts if p not in (None, ""))


def _dt(v: date | datetime | None) -> str:
    d = _d(v)
    return d.isoformat() if d else ""


def _wh(db: Session, wid: uuid.UUID | None) -> str:
    if wid is None:
        return ""
    return db.scalar(select(Warehouse.code).where(Warehouse.id == wid)) or ""


def _doc(
    *,
    company_id: uuid.UUID,
    title: str,
    url: str,
    identifiers: Iterable[str | None] = (),
    warehouse_id: uuid.UUID | None = None,
    subtitle: str = "",
    names: str = "",
    body: str = "",
    ocr_text: str = "",
    status: str | None = None,
    doc_date: date | None = None,
    amount: Decimal | None = None,
    currency: str | None = None,
    customer_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    product_ids: Iterable[uuid.UUID] = (),
    attrs: dict[str, Any] | None = None,
    source_updated_at: datetime | None = None,
) -> Doc:
    ids = ident_forms(*identifiers)
    return {
        "company_id": company_id,
        "warehouse_id": warehouse_id,
        "title": title,
        "subtitle": subtitle,
        "title_norm": fold(title),
        "identifiers": ids,
        "ident_text": (" " + " ".join(ids) + " ") if ids else "",
        "names": fold(names),
        "body": fold(body),
        "ocr_text": fold(ocr_text)[:OCR_TEXT_LIMIT],
        "status": status,
        "doc_date": doc_date,
        "amount": amount,
        "currency": currency,
        "customer_id": customer_id,
        "vendor_id": vendor_id,
        "product_ids": sorted(set(product_ids), key=str),
        "attrs": attrs or {},
        "url": url,
        "source_updated_at": source_updated_at,
    }


def _products(db: Session, ids: Iterable[uuid.UUID]) -> list[Product]:
    ids = list(set(ids))
    return list(db.scalars(select(Product).where(Product.id.in_(ids)))) if ids else []


# ------------------------------------------------------------------ builders (one per type)


def build_product(db: Session, pid: uuid.UUID) -> Doc | None:
    p = db.get(Product, pid)
    if p is None:
        return None
    aliases = db.scalars(select(ProductAlias.alias).where(ProductAlias.product_id == p.id)).all()
    profiles = db.scalars(
        select(ProductUnitProfile.code).where(
            ProductUnitProfile.product_id == p.id, ProductUnitProfile.active.is_(True)
        )
    ).all()
    cat = db.scalar(select(ProductCategory.name).where(ProductCategory.id == p.category_id))
    # Packaged twin: SKU + "P" (products.sku check constraint).
    twin_sku = p.sku[:-1] if p.sku.endswith("P") and len(p.sku) == 4 else p.sku + "P"
    twin = db.scalar(
        select(Product).where(Product.company_id == p.company_id, Product.sku == twin_sku)
    )
    return _doc(
        company_id=p.company_id,
        title=p.name,
        subtitle=_join(p.sku, "·", cat or "", "·", p.stock_unit, "" if p.active else "· inactive"),
        identifiers=[p.sku],
        names=_join(*aliases),
        body=_join(cat, " ".join(profiles), twin.name if twin else "", twin.sku if twin else ""),
        status="ACTIVE" if p.active else "INACTIVE",
        product_ids=[p.id],
        attrs={"sku": p.sku, "twin_id": str(twin.id) if twin else None},
        url=f"/products/{p.id}",
        source_updated_at=p.updated_at,
    )


def build_customer(db: Session, cid: uuid.UUID) -> Doc | None:
    c = db.get(Customer, cid)
    if c is None:
        return None
    group = db.scalar(select(CustomerGroup.name).where(CustomerGroup.id == c.group_id))
    return _doc(
        company_id=c.company_id,
        title=c.name,
        subtitle=_join(
            c.external_ref, c.ship_to_name and f"· {c.ship_to_name}", c.phone and f"· {c.phone}"
        ),
        identifiers=[c.external_ref, c.vendor_no],
        names=_join(c.ship_to_name, c.email),
        body=_join(c.bill_to_address, c.ship_to_address, c.phone, c.fax, group),
        status="ACTIVE" if c.active else "INACTIVE",
        customer_id=c.id,
        attrs={"code": c.external_ref, "store": c.ship_to_name},
        url=f"/customers/{c.id}",
        source_updated_at=c.updated_at,
    )


def build_vendor(db: Session, vid: uuid.UUID) -> Doc | None:
    v = db.get(Vendor, vid)
    if v is None:
        return None
    return _doc(
        company_id=v.company_id,
        title=v.name,
        subtitle=_join(v.code, v.country_code and f"· {v.country_code}", "·", v.currency),
        identifiers=[v.code],
        names=_join(v.email),
        status="ACTIVE" if v.active else "INACTIVE",
        vendor_id=v.id,
        attrs={"code": v.code},
        url=f"/suppliers/{v.id}",
        source_updated_at=v.updated_at,
    )


def build_lot(db: Session, lid: uuid.UUID) -> Doc | None:
    lot = db.get(InventoryLot, lid)
    if lot is None:
        return None
    prod = db.get(Product, lot.product_id)
    # Where the batch came from: the GRN line that created it (real foreign key grn_lines.lot_id).
    src = db.execute(
        select(Shipment.id, Shipment.shipment_no, Shipment.grn_no, Vendor.id, Vendor.name)
        .join(GrnLine, GrnLine.shipment_id == Shipment.id)
        .outerjoin(Vendor, Vendor.id == Shipment.vendor_id)  # a quick receive may have none
        .where(GrnLine.lot_id == lot.id)
        .limit(1)
    ).first()
    return _doc(
        company_id=lot.company_id,
        warehouse_id=lot.warehouse_id,
        title=lot.lot_code,
        subtitle=_join(
            prod.sku if prod else "",
            prod.name if prod else "",
            "·",
            _wh(db, lot.warehouse_id),
            "·",
            lot.status,
        ),
        identifiers=[lot.lot_code, lot.batch_ref],
        names=prod.name if prod else "",
        body=_join(
            prod.sku if prod else "",
            src[1] if src else "",
            src[2] if src else "",
            src[4] if src else "",
        ),
        status=lot.status,
        doc_date=_d(lot.received_at or lot.created_at),
        vendor_id=src[3] if src else None,
        product_ids=[lot.product_id],
        attrs={"batch_ref": lot.batch_ref, "shipment_id": str(src[0]) if src else None},
        url=f"/inventory/lots/{lot.id}",
        source_updated_at=lot.created_at,
    )


def build_stock_count(db: Session, sid: uuid.UUID) -> Doc | None:
    c = db.get(StockCount, sid)
    if c is None:
        return None
    return _doc(
        company_id=c.company_id,
        warehouse_id=c.warehouse_id,
        title=c.count_no,
        subtitle=_join("Stock count ·", _wh(db, c.warehouse_id), "·", c.status),
        identifiers=[c.count_no],
        body=c.note,
        status=c.status,
        doc_date=_d(c.created_at),
        url="/inventory?tab=counts",
        source_updated_at=c.decided_at or c.created_at,
    )


def build_sales_order(db: Session, oid: uuid.UUID) -> Doc | None:
    o = db.get(SalesOrder, oid)
    if o is None:
        return None
    cust = db.get(Customer, o.customer_id)
    lines = db.scalars(select(SalesOrderLine).where(SalesOrderLine.order_id == o.id)).all()
    prods = _products(db, (ln.product_id for ln in lines))
    return _doc(
        company_id=o.company_id,
        warehouse_id=o.warehouse_id,
        title=o.order_no,
        subtitle=_join(
            cust.name if cust else "",
            "·",
            o.status.replace("_", " ").lower(),
            "·",
            _dt(o.created_at),
        ),
        identifiers=[o.order_no],
        names=_join(cust.name if cust else "", cust.ship_to_name if cust else ""),
        body=_join(
            cust.external_ref if cust else "", o.note, *(f"{p.sku} {p.name}" for p in prods)
        ),
        ocr_text=o.source_text,
        status=o.status,
        doc_date=_d(o.created_at),
        customer_id=o.customer_id,
        product_ids=[p.id for p in prods],
        url=f"/orders/{o.id}",
        source_updated_at=o.updated_at,
    )


def build_pick(db: Session, aid: uuid.UUID) -> Doc | None:
    a = db.get(PickingAttempt, aid)
    if a is None:
        return None
    o = db.get(SalesOrder, a.order_id)
    cust = db.get(Customer, o.customer_id) if o else None
    return _doc(
        company_id=a.company_id,
        warehouse_id=a.warehouse_id,
        title=a.pick_code,
        subtitle=_join(
            "Packing sheet ·", cust.name if cust else "", "·", a.status.replace("_", " ").lower()
        ),
        identifiers=[a.pick_code],
        names=cust.name if cust else "",
        body=_join(o.order_no if o else ""),
        status=a.status,
        doc_date=_d(a.created_at),
        customer_id=o.customer_id if o else None,
        attrs={"order_id": str(a.order_id)},
        url=f"/packing/{a.id}",
        source_updated_at=a.verified_at or a.submitted_at or a.created_at,
    )


def build_invoice(db: Session, iid: uuid.UUID) -> Doc | None:
    inv = db.get(Invoice, iid)
    if inv is None:
        return None
    cust = db.get(Customer, inv.customer_id)
    o = db.get(SalesOrder, inv.sales_order_id)
    lines = db.execute(
        select(InvoiceLine.product_id, InvoiceLine.sku, InvoiceLine.description).where(
            InvoiceLine.invoice_id == inv.id
        )
    ).all()
    return _doc(
        company_id=inv.company_id,
        warehouse_id=inv.warehouse_id,
        title=inv.invoice_no,
        subtitle=_join(
            cust.name if cust else "",
            "·",
            _dt(inv.issued_at),
            "·",
            inv.state.replace("_", " ").lower(),
        ),
        identifiers=[inv.invoice_no],
        names=_join(cust.name if cust else "", cust.ship_to_name if cust else ""),
        body=_join(
            o.order_no if o else "",
            cust.external_ref if cust else "",
            *(f"{s} {d}" for _, s, d in lines),
        ),
        status=inv.state,
        doc_date=_d(inv.issued_at),
        amount=inv.total,
        currency=inv.currency,
        customer_id=inv.customer_id,
        product_ids=[pid for pid, _, _ in lines],
        attrs={"order_id": str(inv.sales_order_id), "due": _dt(inv.due_at)},
        url=f"/invoices/{inv.id}",
        source_updated_at=inv.email_sent_at or inv.issued_at,
    )


def build_shipment(db: Session, sid: uuid.UUID) -> Doc | None:
    s = db.get(Shipment, sid)
    if s is None:
        return None
    v = db.get(Vendor, s.vendor_id) if s.vendor_id else None
    refs = db.scalars(
        select(ShipmentDocument.doc_ref).where(ShipmentDocument.shipment_id == s.id)
    ).all()
    grn = db.execute(
        select(GrnLine.batch_ref, GrnLine.product_id).where(GrnLine.shipment_id == s.id)
    ).all()
    declared = db.execute(
        select(DeclaredLine.raw_label, DeclaredLine.product_id).where(
            DeclaredLine.shipment_id == s.id, DeclaredLine.removed_at.is_(None)
        )
    ).all()
    costs = db.scalars(
        select(LandedCost.kind).where(
            LandedCost.shipment_id == s.id, LandedCost.removed_at.is_(None)
        )
    ).all()
    prods = _products(db, [p for _, p in grn] + [p for _, p in declared])
    return _doc(
        company_id=s.company_id,
        warehouse_id=s.warehouse_id,
        title=s.shipment_no,
        subtitle=_join(v.name if v else "", "·", s.status.lower(), s.grn_no and f"· {s.grn_no}"),
        identifiers=[
            s.shipment_no,
            s.grn_no,
            s.awb_no,
            s.vendor_invoice_no,
            *refs,
            *(b for b, _ in grn),
        ],
        names=v.name if v else "",
        body=_join(
            v.code if v else "",
            s.note,
            s.discrepancy_note,
            *(lbl for lbl, _ in declared),
            *(f"{p.sku} {p.name}" for p in prods),
            *(k.lower() for k in costs),
        ),
        status=s.status,
        doc_date=s.expected_on or _d(s.created_at),
        vendor_id=s.vendor_id,
        product_ids=[p.id for p in prods],
        attrs={"awb_no": s.awb_no, "vendor_invoice_no": s.vendor_invoice_no, "grn_no": s.grn_no},
        url=f"/receiving/{s.id}",
        source_updated_at=s.updated_at,
    )


def build_payment(db: Session, pid: uuid.UUID) -> Doc | None:
    pay = db.get(Payment, pid)
    if pay is None:
        return None
    cust = db.get(Customer, pay.customer_id)
    inv_ids = set(
        db.scalars(
            select(PaymentAllocation.invoice_id).where(PaymentAllocation.payment_id == pay.id)
        )
    )
    if pay.reported_invoice_id:
        inv_ids.add(pay.reported_invoice_id)
    inv_nos = (
        db.scalars(select(Invoice.invoice_no).where(Invoice.id.in_(inv_ids))).all()
        if inv_ids
        else []
    )
    return _doc(
        company_id=pay.company_id,
        title=pay.payment_no,
        subtitle=_join(
            cust.name if cust else "",
            "·",
            pay.method.lower(),
            "·",
            pay.status.lower(),
            "·",
            _dt(pay.received_on),
        ),
        identifiers=[pay.payment_no, pay.payer_ref, pay.verification_ref],
        names=cust.name if cust else "",
        body=_join(*inv_nos, pay.note),
        status=pay.status,
        doc_date=pay.received_on,
        amount=pay.amount,
        currency=pay.currency,
        customer_id=pay.customer_id,
        attrs={"invoice_ids": sorted(str(i) for i in inv_ids)},
        url=f"/payments/{pay.id}",
        source_updated_at=pay.verified_at or pay.created_at,
    )


def build_supplier_payment(db: Session, pid: uuid.UUID) -> Doc | None:
    sp = db.get(SupplierPayment, pid)
    if sp is None:
        return None
    v = db.get(Vendor, sp.vendor_id)
    shp = db.scalar(select(Shipment.shipment_no).where(Shipment.id == sp.shipment_id))
    return _doc(
        company_id=sp.company_id,
        title=sp.payment_no,
        subtitle=_join(
            v.name if v else "",
            "·",
            sp.method.lower(),
            "·",
            _dt(sp.paid_on),
            "· voided" if sp.voided_at else "",
        ),
        identifiers=[sp.payment_no, sp.reference],
        names=v.name if v else "",
        body=_join(shp, sp.note),
        status="VOIDED" if sp.voided_at else "RECORDED",
        doc_date=sp.paid_on,
        amount=sp.amount,
        currency=sp.currency,
        vendor_id=sp.vendor_id,
        attrs={"shipment_id": str(sp.shipment_id) if sp.shipment_id else None},
        url=f"/suppliers/{sp.vendor_id}?tab=payments",
        source_updated_at=sp.voided_at or sp.created_at,
    )


def _ocr_text(ex: OcrExtraction, fields: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    for b in ex.text_blocks or []:
        if isinstance(b, dict) and isinstance(b.get("text"), str):
            parts.append(b["text"])
    for v in (ex.header or {}).values():
        if isinstance(v, dict) and v.get("value") not in (None, ""):
            parts.append(str(v["value"]))
    for label, raw in fields:
        parts.append(label)
        parts.append(raw)
    return "\n".join(p for p in parts if p)


def build_scan(db: Session, jid: uuid.UUID) -> Doc | None:
    j = db.get(ScanJob, jid)
    if j is None:
        return None
    filename = (
        db.scalar(
            select(SourceDocument.original_filename).where(SourceDocument.id == j.document_id)
        )
        if j.document_id
        else None
    )
    ex = db.scalar(
        select(OcrExtraction)
        .where(OcrExtraction.scan_job_id == j.id, OcrExtraction.status != "SUPERSEDED")
        .order_by(OcrExtraction.run_no.desc())
        .limit(1)
    )
    ocr = ""
    cust_id = vendor_id = None
    if ex is not None:
        fields = db.execute(
            select(OcrField.label, OcrField.raw_text).where(OcrField.extraction_id == ex.id)
        ).all()
        ocr = _ocr_text(ex, [(a, b) for a, b in fields])
        cust_id, vendor_id = ex.customer_id, ex.vendor_id
    cust = db.scalar(select(Customer.name).where(Customer.id == cust_id)) if cust_id else None
    vend = db.scalar(select(Vendor.name).where(Vendor.id == vendor_id)) if vendor_id else None
    return _doc(
        company_id=j.company_id,
        warehouse_id=j.warehouse_id,
        title=j.job_no,
        subtitle=_join(
            j.job_type.replace("_", " ").lower(),
            filename and f"· {filename}",
            "·",
            (ex.status if ex else j.status).replace("_", " ").lower(),
        ),
        identifiers=[j.job_no],
        names=_join(filename, cust, vend),
        body=_join(j.note),
        ocr_text=ocr,
        status=ex.status if ex else j.status,
        doc_date=_d(j.created_at),
        customer_id=cust_id,
        vendor_id=vendor_id,
        attrs={"extraction_id": str(ex.id) if ex else None, "job_type": j.job_type},
        url=f"/scan/{j.id}",
        source_updated_at=(ex.completed_at if ex else None) or j.completed_at or j.created_at,
    )


def build_document(db: Session, did: uuid.UUID) -> Doc | None:
    f = db.get(DocumentFile, did)
    if f is None:
        return None
    return _doc(
        company_id=f.company_id,
        warehouse_id=f.warehouse_id,
        title=f.filename,
        subtitle=_join(f.kind.replace("_", " ").lower(), "·", f.folder),
        identifiers=[],
        names=f.folder.replace("/", " "),
        body=_join(f.note),
        status=f.sync_status,
        doc_date=_d(f.created_at),
        customer_id=f.customer_id,
        vendor_id=f.vendor_id,
        attrs={"invoice_id": str(f.invoice_id) if f.invoice_id else None, "kind": f.kind},
        url="/documents",
        source_updated_at=f.synced_at or f.created_at,
    )


def build_gmail(db: Session, mid: uuid.UUID) -> Doc | None:
    m = db.get(GmailMessage, mid)
    if m is None:
        return None
    files = db.scalars(
        select(GmailAttachment.filename).where(GmailAttachment.message_id == m.id)
    ).all()
    # The email body needs gmail.body.read: it is never indexed, only subject / sender /
    # Gmail's short preview / attachment names (all visible with gmail.read).
    return _doc(
        company_id=m.company_id,
        title=m.subject or "(no subject)",
        subtitle=_join(
            m.from_name or m.from_email,
            "·",
            _dt(m.received_at),
            "· removed from Gmail" if m.removed_at else "",
        ),
        identifiers=[],
        names=_join(m.from_name, m.from_email, *files),
        body=m.snippet,
        status="REMOVED" if m.removed_at else ("READ" if m.read_at else "UNREAD"),
        doc_date=_d(m.received_at),
        attrs={"attachments": len(files)},
        url=f"/gmail?message={m.id}",
        source_updated_at=m.synced_at,
    )


BUILDERS: dict[str, Callable[[Session, uuid.UUID], Doc | None]] = {
    "product": build_product,
    "customer": build_customer,
    "vendor": build_vendor,
    "lot": build_lot,
    "stock_count": build_stock_count,
    "sales_order": build_sales_order,
    "pick": build_pick,
    "invoice": build_invoice,
    "shipment": build_shipment,
    "payment": build_payment,
    "supplier_payment": build_supplier_payment,
    "scan": build_scan,
    "document": build_document,
    "gmail": build_gmail,
}
assert set(BUILDERS) == set(INDEXED_TYPES)

SOURCES: dict[str, Any] = {
    "product": Product,
    "customer": Customer,
    "vendor": Vendor,
    "lot": InventoryLot,
    "stock_count": StockCount,
    "sales_order": SalesOrder,
    "pick": PickingAttempt,
    "invoice": Invoice,
    "shipment": Shipment,
    "payment": Payment,
    "supplier_payment": SupplierPayment,
    "scan": ScanJob,
    "document": DocumentFile,
    "gmail": GmailMessage,
}

# A rename shows up on other records' documents (a customer's name on its invoices).
FANOUT: dict[str, list[tuple[str, Any, Any]]] = {
    "customer": [
        ("sales_order", SalesOrder.id, SalesOrder.customer_id),
        ("invoice", Invoice.id, Invoice.customer_id),
        ("payment", Payment.id, Payment.customer_id),
    ],
    "vendor": [
        ("shipment", Shipment.id, Shipment.vendor_id),
        ("supplier_payment", SupplierPayment.id, SupplierPayment.vendor_id),
    ],
    "product": [("lot", InventoryLot.id, InventoryLot.product_id)],
    "shipment": [("lot", GrnLine.lot_id, GrnLine.shipment_id)],
    "sales_order": [("pick", PickingAttempt.id, PickingAttempt.order_id)],
}


# ------------------------------------------------------------------ write path


def upsert(db: Session, doc_type: str, source_id: uuid.UUID, doc: Doc) -> None:
    values = {"doc_type": doc_type, "source_id": source_id, **doc, "indexed_at": func.now()}
    stmt = insert(SearchDocument).values(**values)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["doc_type", "source_id"],
            set_={k: stmt.excluded[k] for k in values if k not in ("doc_type", "source_id")},
        )
    )


def reindex(db: Session, doc_type: str, source_id: uuid.UUID, *, fanout: bool = True) -> bool:
    """Rebuild one document. Returns True when a document exists afterwards."""
    builder = BUILDERS.get(doc_type)
    if builder is None:
        raise LookupError(f"unknown search document type {doc_type}")
    doc = builder(db, source_id)
    old_title = db.scalar(
        select(SearchDocument.title).where(
            SearchDocument.doc_type == doc_type, SearchDocument.source_id == source_id
        )
    )
    if doc is None:
        db.execute(
            delete(SearchDocument).where(
                SearchDocument.doc_type == doc_type, SearchDocument.source_id == source_id
            )
        )
        return False
    upsert(db, doc_type, source_id, doc)
    if fanout and old_title is not None and old_title != doc["title"]:
        enqueue_related(db, doc_type, source_id)
    elif fanout and doc_type in ("shipment", "sales_order"):
        enqueue_related(db, doc_type, source_id)  # their numbers/batches show on children
    return True


def enqueue(db: Session, doc_type: str, ids: Iterable[uuid.UUID]) -> int:
    rows = [{"doc_type": doc_type, "source_id": i} for i in ids if i is not None]
    if rows:
        db.execute(insert(SearchQueueItem).values(rows))
    return len(rows)


def enqueue_related(db: Session, doc_type: str, source_id: uuid.UUID) -> int:
    n = 0
    for child, id_col, fk_col in FANOUT.get(doc_type, []):
        ids = db.scalars(select(id_col).where(fk_col == source_id, id_col.is_not(None))).all()
        n += enqueue(db, child, ids)
    return n


@dataclass
class DrainResult:
    claimed: int = 0
    documents: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def drain(db: Session, limit: int = 500) -> DrainResult:
    """Process queued changes once. Safe with several workers (SKIP LOCKED)."""
    res = DrainResult()
    rows = db.execute(
        select(SearchQueueItem.id, SearchQueueItem.doc_type, SearchQueueItem.source_id)
        .where(SearchQueueItem.status == "PENDING", SearchQueueItem.next_attempt_at <= func.now())
        .order_by(SearchQueueItem.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    res.claimed = len(rows)
    groups: dict[tuple[str, uuid.UUID], list[int]] = {}
    for qid, t, sid in rows:
        groups.setdefault((t, sid), []).append(qid)
    for (t, sid), qids in groups.items():
        try:
            with db.begin_nested():
                reindex(db, t, sid)
            db.execute(delete(SearchQueueItem).where(SearchQueueItem.id.in_(qids)))
            res.documents += 1
        except Exception as exc:  # noqa: BLE001 - recorded on the queue rows, retried with backoff
            msg = f"{type(exc).__name__}: {exc}"[:500]
            log.warning(
                "search reindex failed", extra={"ctx": {"type": t, "id": str(sid), "error": msg}}
            )
            res.failed += 1
            res.errors.append(msg)
            db.execute(
                update(SearchQueueItem)
                .where(SearchQueueItem.id.in_(qids))
                .values(
                    attempts=SearchQueueItem.attempts + 1,
                    last_error=msg,
                    # 10 s, 20 s, 40 s, ... then FAILED (reconcile reports it; retry_failed re-arms)
                    next_attempt_at=literal_column(
                        "now() + interval '5 seconds' * power(2, search_index_queue.attempts + 1)"
                    ),
                    status=case(
                        (SearchQueueItem.attempts + 1 >= MAX_ATTEMPTS, "FAILED"), else_="PENDING"
                    ),
                )
            )
    db.commit()
    return res


def drain_all(db: Session, max_rounds: int = 1000) -> int:
    """Drain until the queue has nothing due (tests, scripts)."""
    total = 0
    for _ in range(max_rounds):
        r = drain(db)
        total += r.documents
        if r.claimed == 0:
            break
    return total


# ------------------------------------------------------------------ backfill / reconcile


def backfill(
    db: Session,
    types: Iterable[str] = INDEXED_TYPES,
    company_id: uuid.UUID | None = None,
    batch: int = 500,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, int]:
    """Rebuild every document of the given types (idempotent; safe while the app runs) and
    remove documents whose source row no longer exists."""
    out: dict[str, int] = {}
    for t in types:
        model = SOURCES[t]
        q = select(model.id).order_by(model.id)
        if company_id is not None:
            q = q.where(model.company_id == company_id)
        ids = list(db.scalars(q))
        for i in range(0, len(ids), batch):
            for sid in ids[i : i + batch]:
                reindex(db, t, sid, fanout=False)
            db.commit()
            if progress:
                progress(t, min(i + batch, len(ids)))
        orphans = _orphans(db, t, company_id)
        if orphans:
            db.execute(
                delete(SearchDocument).where(
                    SearchDocument.doc_type == t, SearchDocument.source_id.in_(orphans)
                )
            )
            db.commit()
        out[t] = len(ids)
    return out


def _orphans(db: Session, t: str, company_id: uuid.UUID | None) -> list[uuid.UUID]:
    model = SOURCES[t]
    q = select(SearchDocument.source_id).where(
        SearchDocument.doc_type == t,
        ~select(model.id).where(model.id == SearchDocument.source_id).exists(),
    )
    if company_id is not None:
        q = q.where(SearchDocument.company_id == company_id)
    return list(db.scalars(q))


def reconcile(db: Session, company_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Compare source tables with search_documents. Read-only."""
    per_type: dict[str, dict[str, int]] = {}
    ok = True
    for t in INDEXED_TYPES:
        model = SOURCES[t]
        src_q = select(func.count()).select_from(model)
        missing_q = (
            select(func.count())
            .select_from(model)
            .where(
                ~select(SearchDocument.source_id)
                .where(SearchDocument.doc_type == t, SearchDocument.source_id == model.id)
                .exists()
            )
        )
        doc_q = select(func.count()).select_from(SearchDocument).where(SearchDocument.doc_type == t)
        if company_id is not None:
            src_q = src_q.where(model.company_id == company_id)
            missing_q = missing_q.where(model.company_id == company_id)
            doc_q = doc_q.where(SearchDocument.company_id == company_id)
        row = {
            "source": db.scalar(src_q) or 0,
            "documents": db.scalar(doc_q) or 0,
            "missing": db.scalar(missing_q) or 0,
            "orphaned": len(_orphans(db, t, company_id)),
        }
        ok = ok and row["missing"] == 0 and row["orphaned"] == 0
        per_type[t] = row
    q = db.execute(
        text(
            "SELECT count(*) FILTER (WHERE status='PENDING'),"
            " count(*) FILTER (WHERE status='FAILED'),"
            " extract(epoch FROM now() - min(created_at) FILTER (WHERE status='PENDING'))"
            " FROM search_index_queue"
        )
    ).one()
    return {
        "ok": ok and (q[1] or 0) == 0,
        "types": per_type,
        "queue_pending": q[0] or 0,
        "queue_failed": q[1] or 0,
        "oldest_pending_seconds": float(q[2]) if q[2] is not None else None,
    }


def retry_failed(db: Session) -> int:
    res = db.execute(
        update(SearchQueueItem)
        .where(SearchQueueItem.status == "FAILED")
        .values(
            status="PENDING", attempts=0, next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)
        )
    )
    db.commit()
    return int(getattr(res, "rowcount", 0) or 0)
