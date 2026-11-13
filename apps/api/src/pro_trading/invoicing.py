"""Module 05 invoicing service: verified pick -> numbered invoice -> stock deduction, atomically.

Issue runs in ONE transaction, taking locks in a fixed order everywhere:
  sales order row -> picking attempt row -> inventory balances (sorted) -> invoice sequence row.
A failure anywhere (stock short, audit error, crash) rolls back the number, the movements and
the order quantities together, so numbers are gapless and stock never moves without an invoice.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, object_session

from . import documents, invoice_pdf, outbox, pricing
from . import inventory as inv
from . import orders as osvc
from .config import get_settings
from .errors import ApiError
from .models import (
    Company,
    Customer,
    EmailMessage,
    Employee,
    InventoryBalance,
    InventoryLot,
    Invoice,
    InvoiceDocument,
    InvoiceLine,
    InvoiceSequence,
    OutboxEvent,
    PickingAttempt,
    PickingLine,
    Product,
    ProductUnitProfile,
    SalesOrder,
    SalesOrderLine,
    StockMovement,
    Warehouse,
)

CENT = Decimal("0.01")
ZERO = Decimal(0)
TAX_CODE = "UNASSESSED"
TAX_NOTE = "not assessed - rules pending accountant sign-off (D-03)"
EMAIL_EVENT = "invoice.email"


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


@dataclass
class DraftLine:
    pick_line: PickingLine
    order_line: SalesOrderLine
    product: Product
    unit_code: str
    cases: Decimal | None
    case_weight_lb: Decimal | None
    weight_source: str | None
    billed_qty: Decimal
    unit_rate: Decimal
    pricing_snapshot: dict[str, Any]
    tax_rate: Decimal = ZERO
    # Pounds per unit printed on the invoice (D-12 "CASE-40" / LBS / KG columns): the entered
    # case weight on weighed rows, else the product's default case size for the unit.
    pack_lb: Decimal | None = None

    @property
    def line_amount(self) -> Decimal:
        return money(self.billed_qty * self.unit_rate)

    @property
    def tax_amount(self) -> Decimal:
        return money(self.line_amount * self.tax_rate)

    def out(self) -> dict[str, Any]:
        snap = self.pricing_snapshot or {}
        return {
            "pick_line_id": str(self.pick_line.id),
            "order_line_id": str(self.order_line.id),
            "product_id": str(self.product.id),
            "sku": self.product.sku,
            "description": self.product.name,
            "unit_code": self.unit_code,
            "cases": s(self.cases),
            "case_weight_lb": s(self.case_weight_lb),
            "weight_source": self.weight_source,
            "billed_qty": str(inv.q4(self.billed_qty)),
            "unit_rate": str(self.unit_rate),
            "line_amount": str(self.line_amount),
            "tax_code": TAX_CODE,
            "tax_rate": str(self.tax_rate),
            "tax_amount": str(self.tax_amount),
            "price_version_code": snap.get("version_code"),
            "price_scope": snap.get("scope"),
            "pack_lb": s(self.pack_lb),
        }


@dataclass
class Deduction:
    pick_line: PickingLine
    product: Product
    unit_code: str
    qty: Decimal


@dataclass
class Draft:
    lines: list[DraftLine] = field(default_factory=list)
    deductions: list[Deduction] = field(default_factory=list)
    weighable: list[uuid.UUID] = field(default_factory=list)

    @property
    def subtotal(self) -> Decimal:
        return sum((ln.line_amount for ln in self.lines), ZERO).quantize(CENT)

    @property
    def tax_amount(self) -> Decimal:
        return sum((ln.tax_amount for ln in self.lines), ZERO).quantize(CENT)

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.tax_amount


def compute(
    db: Session,
    order: SalesOrder,
    attempt: PickingAttempt,
    customer: Customer,
    weights: dict[uuid.UUID, list[Any]],
) -> Draft:
    """Billable rows from the verified pick. Rates come from the order's confirmation-time
    snapshot; catch-weight rows resolve the LB rate *as of confirmation* (immutable versions),
    so a later price book can never change what this order is billed."""
    rows = db.execute(
        select(PickingLine, SalesOrderLine, Product)
        .join(SalesOrderLine, SalesOrderLine.id == PickingLine.order_line_id)
        .join(Product, Product.id == SalesOrderLine.product_id)
        .where(PickingLine.attempt_id == attempt.id)
        .order_by(SalesOrderLine.line_no)
    ).all()
    by_id = {pl.id: (pl, ol, prod) for pl, ol, prod in rows}
    packs = _pack_weights(db, {prod.id for _, _, prod in rows})
    for pid in weights:
        if pid not in by_id or not (by_id[pid][0].picked_qty or ZERO) > 0:
            raise ApiError(422, "INVALID_LINE", "Weights given for a line with nothing picked")
    d = Draft()
    for pl, ol, prod in rows:
        picked = pl.picked_qty or ZERO
        if picked <= 0:
            continue
        d.deductions.append(Deduction(pl, prod, ol.unit_code, picked))
        if ol.unit_code == "BOX":
            d.weighable.append(pl.id)
        rows_w = weights.get(pl.id)
        if rows_w:
            if ol.unit_code != "BOX":
                raise ApiError(
                    422,
                    "WEIGHTS_NOT_ALLOWED",
                    f"{prod.sku} is sold by {ol.unit_code}; case weights apply to boxes only",
                    pick_line_id=str(pl.id),
                )
            cases = sum((Decimal(r.cases) for r in rows_w), ZERO)
            if cases != picked:
                raise ApiError(
                    422,
                    "WEIGHTS_MISMATCH",
                    f"{prod.sku}: weights cover {cases} cases but {inv.q4(picked)} were picked",
                    pick_line_id=str(pl.id),
                    expected=str(inv.q4(picked)),
                    given=str(cases),
                )
            assert order.confirmed_at is not None
            try:
                quote = pricing.resolve(
                    db, order.company_id, prod.id, "LB", customer, order.confirmed_at
                )
            except pricing.NoPriceError:
                raise ApiError(
                    422,
                    "NO_PRICE",
                    f"No per-lb price for {prod.sku} on the order's confirmation date",
                    pick_line_id=str(pl.id),
                ) from None
            grouped: dict[Decimal, int] = {}
            for r in rows_w:  # same weight -> one row; first-seen order kept
                w = inv.q4(r.weight_lb)
                grouped[w] = grouped.get(w, 0) + r.cases
            snap = quote.snapshot() | {"basis": "CATCH_WEIGHT_LB"}
            for w, n in grouped.items():
                d.lines.append(
                    DraftLine(
                        pl, ol, prod, "LB", Decimal(n), w, "ENTERED", w * n, quote.rate, snap,
                        pack_lb=w,
                    )
                )  # fmt: skip
        else:
            if ol.rate is None:  # confirmed orders always carry a rate; defensive
                raise ApiError(409, "NO_PRICE", f"{prod.sku} has no confirmed rate")
            d.lines.append(
                DraftLine(
                    pl,
                    ol,
                    prod,
                    ol.unit_code,
                    picked if ol.unit_code == "BOX" else None,
                    None,
                    None,
                    picked,
                    ol.rate,
                    dict(ol.price_snapshot or {}),
                    pack_lb=_snap_box_lb(ol.price_snapshot) or packs.get((prod.id, ol.unit_code)),
                )
            )
    return d


def _snap_box_lb(snap: dict[str, Any] | None) -> Decimal | None:
    """Box weight a box rate was worked out from (per lb x box, see orders._box_from_lb)."""
    v = (snap or {}).get("box_lb")
    return Decimal(v) if v else None


def _pack_weights(db: Session, product_ids: set[uuid.UUID]) -> dict[tuple[uuid.UUID, str], Decimal]:
    """(product, unit) -> lb per unit of the active case size, the product's default first."""
    if not product_ids:
        return {}
    profs = db.scalars(
        select(ProductUnitProfile)
        .where(
            ProductUnitProfile.product_id.in_(product_ids),
            ProductUnitProfile.active.is_(True),
            ProductUnitProfile.weight_lb_per_unit.is_not(None),
        )
        .order_by(ProductUnitProfile.is_default.desc(), ProductUnitProfile.code)
    ).all()
    out: dict[tuple[uuid.UUID, str], Decimal] = {}
    for pr in profs:
        assert pr.weight_lb_per_unit is not None
        out.setdefault((pr.product_id, pr.unit_code), pr.weight_lb_per_unit)
    return out


# ------------------------------------------------------------------ stock


def _lots(db: Session, order: SalesOrder, deds: list[Deduction]) -> dict[Any, list[InventoryLot]]:
    """Candidate lots per deduction: the lot picked from, else saleable lots in the order's
    warehouse (first-expiry-first-out)."""
    out: dict[Any, list[InventoryLot]] = {}
    for dd in deds:
        q = select(InventoryLot).where(
            InventoryLot.company_id == order.company_id,
            InventoryLot.product_id == dd.product.id,
            InventoryLot.warehouse_id == order.warehouse_id,
            InventoryLot.status == "SALEABLE",
        )
        if dd.pick_line.lot_id is not None:
            q = q.where(InventoryLot.id == dd.pick_line.lot_id)
        lots = [lot for lot in db.scalars(q).all() if inv.is_sellable(lot)]
        lots.sort(key=fefo_key)
        out[dd.pick_line.id] = lots
    return out


def fefo_key(lot: InventoryLot) -> tuple[bool, datetime, datetime, str]:
    """First-expiry-first-out: earliest expiry, then no expiry; ties by receipt, then code."""
    return (
        lot.expires_at is None,
        lot.expires_at or datetime.max.replace(tzinfo=UTC),
        lot.received_at or lot.created_at,
        lot.lot_code,
    )


def stock_check(db: Session, order: SalesOrder, draft: Draft) -> list[dict[str, Any]]:
    """Read-only availability (preview). The issue path re-checks under row locks."""
    lots = _lots(db, order, draft.deductions)
    res = []
    for dd in draft.deductions:
        ids = [lot.id for lot in lots[dd.pick_line.id]]
        bals = (
            db.scalars(
                select(InventoryBalance).where(
                    InventoryBalance.lot_id.in_(ids), InventoryBalance.unit_code == dd.unit_code
                )
            ).all()
            if ids
            else []
        )
        free = {b.lot_id: b.qty - b.reserved_qty for b in bals}
        avail = sum(free.values(), ZERO)
        # The batches issuing would take, in the order allocate() takes them.
        plan, need = [], dd.qty
        for lot in lots[dd.pick_line.id]:
            if need <= 0:
                break
            if free.get(lot.id, ZERO) > 0:
                plan.append(lot.lot_code)
                need -= free[lot.id]
        res.append(
            {
                "pick_line_id": str(dd.pick_line.id),
                "product_id": str(dd.product.id),
                "sku": dd.product.sku,
                "unit_code": dd.unit_code,
                "deduct_qty": str(inv.q4(dd.qty)),
                "available_qty": str(inv.q4(avail)),
                "ok": avail >= dd.qty,
                "lots": plan,
            }
        )
    return res


def allocate(
    db: Session, order: SalesOrder, draft: Draft, invoice_id: uuid.UUID
) -> list[inv.Posting]:
    lots = _lots(db, order, draft.deductions)
    keys = [(lot.id, dd.unit_code) for dd in draft.deductions for lot in lots[dd.pick_line.id]]
    bals = inv.lock_balances(db, keys)  # one sorted lock pass across all lines
    # Lot status read before the locks may be stale (a quarantine can commit in between; it
    # takes these balance locks too), so only lots still saleable now are used.
    ids = {k[0] for k in keys}
    fresh = (
        dict(
            db.execute(
                select(InventoryLot.id, InventoryLot.status).where(InventoryLot.id.in_(ids))
            ).all()
        )
        if ids
        else {}
    )
    lots = {k: [lot for lot in v if fresh.get(lot.id) == "SALEABLE"] for k, v in lots.items()}
    postings: list[inv.Posting] = []
    for dd in draft.deductions:
        need = dd.qty
        for lot in lots[dd.pick_line.id]:
            bal = bals.get((lot.id, dd.unit_code))
            free = (bal.qty - bal.reserved_qty) if bal else ZERO
            take = min(free, need)
            if dd.unit_code in ("BOX", "PIECE"):
                take = take.to_integral_value(rounding="ROUND_FLOOR")
            if take <= 0:
                continue
            postings.append(
                inv.Posting(
                    lot=lot,
                    unit_code=dd.unit_code,
                    delta=-take,
                    movement_type="INVOICE_DISPATCH",
                    posting_key=f"invoice:{dd.pick_line.id}:{lot.id}:{dd.unit_code}",
                    source_type="INVOICE",
                    source_id=invoice_id,
                    source_line_id=dd.pick_line.id,
                )
            )
            need -= take
            if need == 0:
                break
        if need > 0:
            have = inv.q4(dd.qty - need)
            raise ApiError(
                409,
                "INSUFFICIENT_STOCK",
                f"Only {have} {dd.unit_code} of {dd.product.sku} in stock; "
                f"{inv.q4(dd.qty)} picked. Check the shelf or count the stock.",
                sku=dd.product.sku,
                unit_code=dd.unit_code,
                available=str(have),
                requested=str(inv.q4(dd.qty)),
            )
    return postings


# ------------------------------------------------------------------ issue


def _next_number(db: Session, company_id: uuid.UUID, year: int) -> tuple[int, str]:
    db.execute(
        insert(InvoiceSequence)
        .values(company_id=company_id, fiscal_year=year, last_no=0)
        .on_conflict_do_nothing(index_elements=["company_id", "fiscal_year"])
    )
    seq = db.scalar(
        select(InvoiceSequence)
        .where(InvoiceSequence.company_id == company_id, InvoiceSequence.fiscal_year == year)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert seq is not None
    seq.last_no += 1
    return seq.last_no, invoice_number(get_settings().invoice_prefix, year, seq.last_no)


def invoice_number(prefix: str, year: int, seq_no: int) -> str:
    """`TT2026-000001` (D-11/D-12): prefix + fiscal year + 6-digit yearly sequence, room for
    999,999 invoices a year (~2,700 a day). The sequence carries on from numbers already
    issued in the year, so `(fiscal_year, seq_no)` stays unique and gapless."""
    return f"{prefix}{year}-{seq_no:06d}"


def issue(
    db: Session,
    actor: Employee,
    order: SalesOrder,
    attempt: PickingAttempt,
    weights: dict[uuid.UUID, list[Any]],
    note: str,
    salesperson: str = "",
    customer_po: str = "",
) -> Invoice:
    """Caller holds the order + attempt locks and has checked state/version/permission."""
    st = get_settings()
    if st.is_production_like and not st.invoice_tax_verified:
        raise ApiError(
            409,
            "TAX_RULES_UNVERIFIED",
            "Invoices cannot be issued until tax rules are verified by the accountant (D-03)",
        )
    customer = db.get(Customer, order.customer_id)
    company = db.get(Company, order.company_id)
    wh = db.get(Warehouse, order.warehouse_id)
    assert customer and company and wh
    draft = compute(db, order, attempt, customer, weights)
    if not draft.lines:
        raise ApiError(
            422, "NOTHING_TO_INVOICE", "Nothing was packed on this sheet; there is nothing to bill"
        )
    invoice_id = uuid.uuid4()
    inv.post(db, actor.id, allocate(db, order, draft, invoice_id))

    issued_at = datetime.now(UTC)
    tz = ZoneInfo(company.timezone)
    year = issued_at.astimezone(tz).year
    seq_no, number = _next_number(db, company.id, year)
    due_at = issued_at + timedelta(days=customer.payment_terms_days)
    lines_out = [ln.out() | {"line_no": i} for i, ln in enumerate(draft.lines, start=1)]
    snapshot: dict[str, Any] = {
        "invoice_no": number,
        "issued_at": issued_at.isoformat(),
        "issued_date": issued_at.astimezone(tz).date().isoformat(),
        "due_date": due_at.astimezone(tz).date().isoformat(),
        "currency": "CAD",
        "company": {
            "id": str(company.id),
            "legal_name": company.legal_name,
            "address": company.address,
            "phone": company.phone,
            "fax": company.fax,
            "email": company.email,
            "business_no": company.business_no,
        },
        "customer": {
            "id": str(customer.id),
            "external_ref": customer.external_ref,
            "name": customer.name,
            "email": customer.email,
            "phone": customer.phone,
            "fax": customer.fax,
            "bill_to_address": customer.bill_to_address,
            "ship_to_name": customer.ship_to_name,
            "ship_to_address": customer.ship_to_address,
            "vendor_no": customer.vendor_no,
            "payment_terms_days": customer.payment_terms_days,
        },
        "salesperson": salesperson.strip() or company.default_salesperson,
        "customer_po": customer_po.strip(),
        "order": {"id": str(order.id), "order_no": order.order_no},
        "pick_code": attempt.pick_code,
        "warehouse_code": wh.code,
        "lines": lines_out,
        "subtotal": str(draft.subtotal),
        "tax_amount": str(draft.tax_amount),
        "total": str(draft.total),
        "tax_note": TAX_NOTE,
        "note": note,
        "template_version": invoice_pdf.TEMPLATE_VERSION,
    }
    pdf = invoice_pdf.render(snapshot)
    digest = invoice_pdf.sha256(pdf)
    invoice = Invoice(
        id=invoice_id,
        company_id=company.id,
        invoice_no=number,
        fiscal_year=year,
        seq_no=seq_no,
        sales_order_id=order.id,
        picking_attempt_id=attempt.id,
        customer_id=customer.id,
        warehouse_id=order.warehouse_id,
        state="PAID" if draft.total == 0 else "ISSUED",  # nothing to collect on a $0 invoice
        currency="CAD",
        subtotal=draft.subtotal,
        tax_amount=draft.tax_amount,
        total=draft.total,
        issued_at=issued_at,
        due_at=due_at,
        issued_by=actor.id,
        snapshot=snapshot,
        pdf_sha256=digest,
        template_version=invoice_pdf.TEMPLATE_VERSION,
        email_to=customer.email,
        email_status="QUEUED" if customer.email else "NO_EMAIL",
        email_sends=1 if customer.email else 0,
    )
    db.add(invoice)
    db.flush()
    for i, ln in enumerate(draft.lines, start=1):
        db.add(
            InvoiceLine(
                invoice_id=invoice_id,
                line_no=i,
                order_line_id=ln.order_line.id,
                pick_line_id=ln.pick_line.id,
                product_id=ln.product.id,
                sku=ln.product.sku,
                description=ln.product.name,
                unit_code=ln.unit_code,
                cases=ln.cases,
                case_weight_lb=ln.case_weight_lb,
                weight_source=ln.weight_source,
                billed_qty=inv.q4(ln.billed_qty),
                unit_rate=ln.unit_rate,
                line_amount=ln.line_amount,
                tax_code=TAX_CODE,
                tax_rate=ln.tax_rate,
                tax_amount=ln.tax_amount,
                pricing_snapshot=ln.pricing_snapshot,
            )
        )
    issued_doc = InvoiceDocument(
        company_id=company.id,
        invoice_id=invoice_id,
        kind="ISSUED",
        content=pdf,
        sha256=digest,
        byte_size=len(pdf),
    )
    db.add(issued_doc)
    for dd in draft.deductions:
        ol = db.get(SalesOrderLine, dd.pick_line.order_line_id)
        assert ol is not None
        ol.dispatched_qty = inv.q4(ol.dispatched_qty + dd.qty)
    attempt.status = "INVOICED"
    attempt.version += 1
    order.version += 1
    order.updated_at = osvc.now()
    db.flush()
    osvc.refresh_status(db, order)
    # Archive entry + Drive copy (outbox) in the same transaction; Drive IO never blocks issue.
    documents.file_invoice(db, invoice, issued_doc, customer, company)
    if customer.email:
        _queue_email(db, invoice, 1)
    return invoice


def _queue_email(db: Session, invoice: Invoice, n: int) -> None:
    outbox.enqueue(
        db,
        EMAIL_EVENT,
        {"invoice_id": str(invoice.id), "dedupe_key": f"invoice-email:{invoice.id}:{n}"},
        company_id=invoice.company_id,
        aggregate_id=str(invoice.id),
    )


def resend(db: Session, invoice: Invoice) -> None:
    if invoice.email_status == "QUEUED":
        raise ApiError(409, "EMAIL_QUEUED", "This invoice is already waiting to be emailed")
    customer = db.get(Customer, invoice.customer_id)
    if customer is None or not customer.email:
        raise ApiError(422, "NO_EMAIL_ADDRESS", "The customer has no email address; add one first")
    invoice.email_to = customer.email
    invoice.email_status = "QUEUED"
    invoice.email_error = None
    invoice.email_sends += 1
    invoice.version += 1
    _queue_email(db, invoice, invoice.email_sends)


# ------------------------------------------------------------------ email (outbox handler)


def deliver(db: Session, msg: EmailMessage) -> None:
    """Provider adapter. Dev/test: the email_messages table is the mailbox. A real provider
    (pending D-15 Google Workspace decision) must pass `dedupe_key` as its idempotency token."""
    db.execute(
        insert(EmailMessage)
        .values(
            company_id=msg.company_id,
            dedupe_key=msg.dedupe_key,
            invoice_id=msg.invoice_id,
            to_addr=msg.to_addr,
            subject=msg.subject,
            body=msg.body,
            attachment_sha256=msg.attachment_sha256,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )


def handle_email(ev: OutboxEvent) -> None:
    db = object_session(ev)
    assert db is not None
    invoice = db.get(Invoice, uuid.UUID(ev.payload["invoice_id"]))
    if invoice is None:
        raise LookupError("invoice not found")
    if invoice.email_status != "QUEUED" or not invoice.email_to:
        return  # superseded (e.g. a later resend) or nothing to send
    snap = invoice.snapshot
    msg = EmailMessage(
        company_id=invoice.company_id,
        dedupe_key=ev.payload["dedupe_key"],
        invoice_id=invoice.id,
        to_addr=invoice.email_to,
        subject=f"Invoice {invoice.invoice_no} from {snap['company']['legal_name']}",
        body=(
            f"Hello {snap['customer']['name']},\n\nPlease find invoice {invoice.invoice_no} "
            f"for order {snap['order']['order_no']} attached.\nAmount due: CAD {invoice.total}"
            f"\nDue date: {snap['due_date']}\n\nThank you."
        ),
        attachment_sha256=invoice.pdf_sha256,
    )
    try:
        deliver(db, msg)
    except Exception as exc:
        if ev.attempts >= outbox.MAX_ATTEMPTS:
            invoice.email_status = "FAILED"
            invoice.email_error = f"{type(exc).__name__}: {exc}"[:300]
            from .notifications import notify_safely

            notify_safely(
                db,
                invoice.company_id,
                "invoice.email_failed",
                event_id=f"invoice.email_failed:{invoice.id}:{invoice.email_sends}",
                target_type="invoice",
                target_id=invoice.id,
                category="integration",
                severity="warning",
                source_module="Invoices",
                title=f"Invoice {invoice.invoice_no} wasn't emailed",
                body=f"Sending to {invoice.email_to} failed after {ev.attempts} tries. "
                "Check the address and send it again.",
                link=f"/invoices/{invoice.id}",
                permission="invoice.issue",
                warehouse_id=invoice.warehouse_id,
            )
        raise
    invoice.email_status = "SENT"
    invoice.email_sent_at = datetime.now(UTC)
    invoice.email_error = None


# ------------------------------------------------------------------ read models


def invoice_movements(db: Session, invoice_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        select(StockMovement, InventoryLot.lot_code, Product.sku)
        .join(InventoryLot, InventoryLot.id == StockMovement.lot_id)
        .join(Product, Product.id == StockMovement.product_id)
        .where(StockMovement.source_type == "INVOICE", StockMovement.source_id == invoice_id)
        .order_by(StockMovement.created_at, Product.sku)
    ).all()
    return [
        {
            "id": str(m.id),
            "lot_id": str(m.lot_id),
            "lot_code": code,
            "sku": sku,
            "unit_code": m.unit_code,
            "delta_qty": str(m.delta_qty),
            "qty_after": str(m.qty_after),
        }
        for m, code, sku in rows
    ]


def summary(invoice: Invoice) -> dict[str, Any]:
    snap = invoice.snapshot
    return {
        "id": str(invoice.id),
        "invoice_no": invoice.invoice_no,
        "state": invoice.state,
        "customer_id": str(invoice.customer_id),
        "customer_name": snap["customer"]["name"],
        "customer_ref": snap["customer"]["external_ref"],
        "sales_order_id": str(invoice.sales_order_id),
        "order_no": snap["order"]["order_no"],
        "pick_code": snap["pick_code"],
        "warehouse_code": snap["warehouse_code"],
        "issued_at": invoice.issued_at.isoformat(),
        "due_at": invoice.due_at.isoformat(),
        "total": str(invoice.total),
        "paid_amount": str(invoice.paid_amount),
        "balance": str(invoice.total - invoice.paid_amount),
        "currency": invoice.currency,
        "email_status": invoice.email_status,
    }


def invoice_out(db: Session, invoice: Invoice) -> dict[str, Any]:
    lines = db.scalars(
        select(InvoiceLine)
        .where(InvoiceLine.invoice_id == invoice.id)
        .order_by(InvoiceLine.line_no)
    ).all()
    by_no = {ln["line_no"]: ln for ln in invoice.snapshot["lines"]}
    issuer = db.get(Employee, invoice.issued_by) if invoice.issued_by else None
    return summary(invoice) | {
        "warehouse_id": str(invoice.warehouse_id),
        "picking_attempt_id": str(invoice.picking_attempt_id),
        "subtotal": str(invoice.subtotal),
        "tax_amount": str(invoice.tax_amount),
        "tax_note": invoice.snapshot["tax_note"],
        "issued_by_name": issuer.display_name if issuer else None,
        "pdf_sha256": invoice.pdf_sha256,
        "template_version": invoice.template_version,
        "email_to": invoice.email_to,
        "email_sent_at": invoice.email_sent_at.isoformat() if invoice.email_sent_at else None,
        "email_error": invoice.email_error,
        "email_sends": invoice.email_sends,
        "version": invoice.version,
        "lines": [by_no[ln.line_no] | {"id": str(ln.id)} for ln in lines],
        "movements": invoice_movements(db, invoice.id),
    }
