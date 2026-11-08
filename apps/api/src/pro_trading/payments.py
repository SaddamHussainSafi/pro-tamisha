"""Module 10 payments: manual ledger, receivables aging and due reminders. No bank integration.

A customer saying "sent" only creates a REPORTED payment. An employee with `payment.verify`
checks the bank (or cheque / cash receipt) and records its reference (VERIFIED); only then can
the money be allocated to open invoices. Allocations are append-only; a mistake is undone by a
reversal row. Lock order (no cycles with other modules): payment row -> invoices sorted by id.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, object_session

from . import outbox
from .config import get_settings
from .errors import ApiError
from .invoicing import deliver
from .models import (
    Company,
    Customer,
    EmailMessage,
    Employee,
    Invoice,
    OutboxEvent,
    Payment,
    PaymentAllocation,
    PaymentReminder,
)

REMINDER_EVENT = "payment.reminder"
OPEN_STATES = ("ISSUED", "PARTIALLY_PAID")
CENT = Decimal("0.01")
BUCKETS = ("current", "d1_30", "d31_60", "d61_90", "d90_plus")


def now() -> datetime:
    return datetime.now(UTC)


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT)


def company_tz(db: Session, company_id: uuid.UUID) -> ZoneInfo:
    co = db.get(Company, company_id)
    assert co is not None
    return ZoneInfo(co.timezone)


def local_today(db: Session, company_id: uuid.UUID) -> date:
    return now().astimezone(company_tz(db, company_id)).date()


def balance(inv: Invoice) -> Decimal:
    return money(inv.total - inv.paid_amount)


def reminder_offsets() -> list[int]:
    return sorted({int(x) for x in get_settings().payment_reminder_offsets.split(",")})


# ------------------------------------------------------------------ record / verify / reject


def next_payment_no(db: Session, company_id: uuid.UUID, year: int) -> str:
    db.get(Company, company_id, with_for_update=True)  # serialize numbering per company
    n = db.scalar(select(func.count()).where(Payment.company_id == company_id)) or 0
    return f"PAY-{year}-{n + 1:06d}"


def record(
    db: Session,
    actor: Employee,
    *,
    customer_id: uuid.UUID,
    method: str,
    amount: Decimal,
    received_on: date,
    payer_ref: str | None,
    invoice_id: uuid.UUID | None,
    note: str | None,
) -> Payment:
    company_id = actor.company_id
    cust = db.get(Customer, customer_id)
    if cust is None or cust.company_id != company_id:
        raise ApiError(422, "INVALID_CUSTOMER", "Customer not found")
    today = local_today(db, company_id)
    if received_on > today:
        raise ApiError(422, "FUTURE_DATE", "The payment date cannot be in the future")
    if invoice_id is not None:
        inv = db.get(Invoice, invoice_id)
        if inv is None or inv.company_id != company_id:
            raise ApiError(422, "INVALID_INVOICE", "Invoice not found")
        if inv.customer_id != customer_id:
            raise ApiError(
                422, "INVOICE_CUSTOMER_MISMATCH", "The invoice belongs to another customer"
            )
        if inv.state not in OPEN_STATES:
            raise ApiError(
                409,
                "INVOICE_NOT_OPEN",
                f"Invoice {inv.invoice_no} is {inv.state.lower().replace('_', ' ')}",
                current_state=inv.state,
            )
    pay = Payment(
        company_id=company_id,
        payment_no=next_payment_no(db, company_id, today.year),
        customer_id=customer_id,
        method=method,
        status="REPORTED",
        amount=money(amount),
        received_on=received_on,
        payer_ref=payer_ref,
        reported_invoice_id=invoice_id,
        note=note,
        recorded_by=actor.id,
        allocated_amount=Decimal("0"),
    )
    db.add(pay)
    db.flush()
    return pay


def lock(db: Session, payment_id: uuid.UUID) -> Payment:
    pay = db.scalar(
        select(Payment)
        .where(Payment.id == payment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert pay is not None
    return pay


def _bump(obj: Payment | Invoice) -> None:
    obj.version += 1


def verify(db: Session, actor: Employee, pay: Payment, ref: str, note: str | None) -> None:
    if pay.status in ("VERIFIED", "APPLIED"):
        raise ApiError(
            409,
            "ALREADY_VERIFIED",
            f"Payment {pay.payment_no} is already confirmed",
            current_status=pay.status,
        )
    if pay.status != "REPORTED":
        raise ApiError(
            409, "INVALID_STATE", f"Payment is {pay.status.lower()}", current_status=pay.status
        )
    dup = db.scalar(
        select(Payment.payment_no).where(
            Payment.company_id == pay.company_id,
            Payment.method == pay.method,
            func.lower(Payment.verification_ref) == ref.lower(),
            Payment.status.in_(("VERIFIED", "APPLIED")),
        )
    )
    if dup is not None:
        raise ApiError(
            409,
            "DUPLICATE_CONFIRMATION",
            f"This bank reference already confirmed payment {dup}",
            payment_no=dup,
        )
    pay.status = "VERIFIED"
    pay.verification_ref = ref
    pay.verification_note = note
    pay.verified_by = actor.id
    pay.verified_at = now()
    _bump(pay)
    db.flush()  # allocation trigger reads the stored status


def reject(db: Session, actor: Employee, pay: Payment, reason: str) -> None:
    if pay.status != "REPORTED":
        raise ApiError(
            409,
            "INVALID_STATE",
            f"Only a reported payment can be rejected; this one is {pay.status.lower()}",
            current_status=pay.status,
        )
    pay.status = "REJECTED"
    pay.rejected_reason = reason
    pay.rejected_by = actor.id
    pay.rejected_at = now()
    _bump(pay)


# ------------------------------------------------------------------ allocate / reverse


def _invoice_state(inv: Invoice) -> None:
    if inv.state in ("ISSUED", "PARTIALLY_PAID", "PAID"):
        if inv.paid_amount == inv.total:
            inv.state = "PAID"
        elif inv.paid_amount > 0:
            inv.state = "PARTIALLY_PAID"
        else:
            inv.state = "ISSUED"


def _payment_state(pay: Payment) -> None:
    pay.status = "APPLIED" if pay.allocated_amount == pay.amount else "VERIFIED"


def _lock_invoices(db: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Invoice]:
    rows = db.scalars(
        select(Invoice)
        .where(Invoice.id.in_(sorted(set(ids))))
        .order_by(Invoice.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    return {i.id: i for i in rows}


def allocate(
    db: Session, actor: Employee, pay: Payment, items: list[tuple[uuid.UUID, Decimal]]
) -> list[PaymentAllocation]:
    if pay.status == "APPLIED":
        raise ApiError(409, "FULLY_APPLIED", f"Payment {pay.payment_no} is fully applied")
    if pay.status != "VERIFIED":
        raise ApiError(
            409,
            "NOT_VERIFIED",
            "Only a verified payment can be applied to invoices",
            current_status=pay.status,
        )
    ids = [i for i, _ in items]
    if len(set(ids)) != len(ids):
        raise ApiError(422, "DUPLICATE_INVOICE", "Each invoice may appear only once")
    invoices = _lock_invoices(db, ids)
    total = Decimal("0")
    for iid, amt in items:
        inv = invoices.get(iid)
        if inv is None or inv.company_id != pay.company_id:
            raise ApiError(422, "INVALID_INVOICE", "Invoice not found", invoice_id=str(iid))
        if inv.customer_id != pay.customer_id:
            raise ApiError(
                422,
                "INVOICE_CUSTOMER_MISMATCH",
                f"Invoice {inv.invoice_no} belongs to another customer",
            )
        if inv.state not in OPEN_STATES:
            raise ApiError(
                409,
                "INVOICE_NOT_OPEN",
                f"Invoice {inv.invoice_no} is {inv.state.lower().replace('_', ' ')}",
                invoice_no=inv.invoice_no,
                current_state=inv.state,
            )
        if money(amt) > balance(inv):
            raise ApiError(
                409,
                "EXCEEDS_INVOICE_BALANCE",
                f"Invoice {inv.invoice_no} has only CAD {balance(inv)} open",
                invoice_no=inv.invoice_no,
                balance=str(balance(inv)),
            )
        total += money(amt)
    unallocated = money(pay.amount - pay.allocated_amount)
    if total > unallocated:
        raise ApiError(
            409,
            "EXCEEDS_PAYMENT",
            f"Only CAD {unallocated} of this payment is unapplied",
            unallocated=str(unallocated),
        )
    out = []
    for iid, amt in items:
        inv = invoices[iid]
        a = PaymentAllocation(
            company_id=pay.company_id,
            payment_id=pay.id,
            invoice_id=iid,
            amount=money(amt),
            created_by=actor.id,
        )
        db.add(a)
        out.append(a)
        inv.paid_amount = money(inv.paid_amount + amt)
        _invoice_state(inv)
        _bump(inv)
    pay.allocated_amount = money(pay.allocated_amount + total)
    _payment_state(pay)
    _bump(pay)
    db.flush()
    return out


def reverse(
    db: Session, actor: Employee, pay: Payment, allocation_id: uuid.UUID, reason: str
) -> PaymentAllocation:
    a = db.get(PaymentAllocation, allocation_id)
    if a is None or a.payment_id != pay.id:
        raise ApiError(404, "NOT_FOUND", "Allocation not found")
    if a.reverses_id is not None:
        raise ApiError(409, "NOT_REVERSIBLE", "A reversal cannot itself be reversed")
    done = db.scalar(select(PaymentAllocation.id).where(PaymentAllocation.reverses_id == a.id))
    if done is not None:
        raise ApiError(409, "ALREADY_REVERSED", "This allocation was already reversed")
    inv = _lock_invoices(db, [a.invoice_id])[a.invoice_id]
    r = PaymentAllocation(
        company_id=pay.company_id,
        payment_id=pay.id,
        invoice_id=a.invoice_id,
        amount=-a.amount,
        reverses_id=a.id,
        reason=reason,
        created_by=actor.id,
    )
    db.add(r)
    inv.paid_amount = money(inv.paid_amount - a.amount)
    _invoice_state(inv)
    _bump(inv)
    pay.allocated_amount = money(pay.allocated_amount - a.amount)
    _payment_state(pay)
    _bump(pay)
    db.flush()
    return r


# ------------------------------------------------------------------ read models


def _names(db: Session, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    want = {i for i in ids if i}
    if not want:
        return {}
    rows = db.execute(select(Employee.id, Employee.display_name).where(Employee.id.in_(want)))
    return {i: n for i, n in rows.all()}


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def summaries(db: Session, pays: list[Payment]) -> list[dict[str, Any]]:
    cust = {
        c.id: c
        for c in db.scalars(select(Customer).where(Customer.id.in_({p.customer_id for p in pays})))
    }
    inv_ids = {p.reported_invoice_id for p in pays if p.reported_invoice_id}
    inv_no = dict(
        db.execute(select(Invoice.id, Invoice.invoice_no).where(Invoice.id.in_(inv_ids))).all()
        if inv_ids
        else []
    )
    names = _names(db, [x for p in pays for x in (p.recorded_by, p.verified_by, p.rejected_by)])
    out = []
    for p in pays:
        c = cust[p.customer_id]
        out.append(
            {
                "id": str(p.id),
                "payment_no": p.payment_no,
                "status": p.status,
                "customer_id": str(p.customer_id),
                "customer_name": c.name,
                "customer_ref": c.external_ref,
                "method": p.method,
                "amount": str(p.amount),
                "allocated_amount": str(p.allocated_amount),
                "unallocated_amount": str(money(p.amount - p.allocated_amount)),
                "currency": p.currency,
                "received_on": p.received_on.isoformat(),
                "payer_ref": p.payer_ref,
                "reported_invoice_id": str(p.reported_invoice_id)
                if p.reported_invoice_id
                else None,
                "reported_invoice_no": inv_no.get(p.reported_invoice_id)
                if p.reported_invoice_id
                else None,
                "note": p.note,
                "recorded_by_name": names.get(p.recorded_by),
                "recorded_at": p.created_at.isoformat(),
                "verification_ref": p.verification_ref,
                "verification_note": p.verification_note,
                "verified_by_name": names.get(p.verified_by) if p.verified_by else None,
                "verified_at": _iso(p.verified_at),
                "rejected_reason": p.rejected_reason,
                "rejected_by_name": names.get(p.rejected_by) if p.rejected_by else None,
                "rejected_at": _iso(p.rejected_at),
                "version": p.version,
            }
        )
    return out


def payment_out(db: Session, pay: Payment) -> dict[str, Any]:
    db.flush()
    db.refresh(pay)
    out = summaries(db, [pay])[0]
    rows = db.scalars(
        select(PaymentAllocation)
        .where(PaymentAllocation.payment_id == pay.id)
        .order_by(PaymentAllocation.created_at, PaymentAllocation.id)
    ).all()
    reversed_ids = {r.reverses_id for r in rows if r.reverses_id}
    invs = {
        i.id: i
        for i in db.scalars(select(Invoice).where(Invoice.id.in_({r.invoice_id for r in rows})))
    }
    names = _names(db, [r.created_by for r in rows])
    out["allocations"] = [
        {
            "id": str(r.id),
            "invoice_id": str(r.invoice_id),
            "invoice_no": invs[r.invoice_id].invoice_no,
            "invoice_state": invs[r.invoice_id].state,
            "invoice_balance": str(balance(invs[r.invoice_id])),
            "amount": str(r.amount),
            "reverses_id": str(r.reverses_id) if r.reverses_id else None,
            "reversed": r.id in reversed_ids,
            "reason": r.reason,
            "created_by_name": names.get(r.created_by),
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return out


@dataclass
class _Row:
    customer: Customer
    buckets: dict[str, Decimal]
    open_invoices: int = 0
    oldest_due: date | None = None
    unapplied: Decimal = Decimal("0")
    reported: Decimal = Decimal("0")


def _bucket(days_past_due: int) -> str:
    if days_past_due <= 0:
        return "current"
    if days_past_due <= 30:
        return "d1_30"
    if days_past_due <= 60:
        return "d31_60"
    if days_past_due <= 90:
        return "d61_90"
    return "d90_plus"


def aging(db: Session, company_id: uuid.UUID, warehouse_ids: set[uuid.UUID]) -> dict[str, Any]:
    """Open balance per customer by days past due (company-local dates), plus verified money
    not yet applied and money the customer reported that nobody has confirmed yet."""
    tz = company_tz(db, company_id)
    today = now().astimezone(tz).date()
    rows: dict[uuid.UUID, _Row] = {}

    def row(c: Customer) -> _Row:
        if c.id not in rows:
            rows[c.id] = _Row(c, dict.fromkeys(BUCKETS, Decimal("0")))
        return rows[c.id]

    invs = db.execute(
        select(Invoice, Customer)
        .join(Customer, Customer.id == Invoice.customer_id)
        .where(
            Invoice.company_id == company_id,
            Invoice.state.in_(OPEN_STATES),
            Invoice.warehouse_id.in_(warehouse_ids),
        )
    ).all()
    for inv, c in invs:
        due = inv.due_at.astimezone(tz).date()
        r = row(c)
        r.buckets[_bucket((today - due).days)] += balance(inv)
        r.open_invoices += 1
        r.oldest_due = min(r.oldest_due or due, due)
    pays = db.execute(
        select(Payment, Customer)
        .join(Customer, Customer.id == Payment.customer_id)
        .where(Payment.company_id == company_id, Payment.status.in_(("REPORTED", "VERIFIED")))
    ).all()
    for p, c in pays:
        r = row(c)
        if p.status == "VERIFIED":
            r.unapplied += p.amount - p.allocated_amount
        else:
            r.reported += p.amount
    totals = dict.fromkeys(BUCKETS, Decimal("0"))
    customers = []
    for r in sorted(rows.values(), key=lambda x: (-sum(x.buckets.values()), x.customer.name)):
        for b in BUCKETS:
            totals[b] += r.buckets[b]
        customers.append(
            {
                "customer_id": str(r.customer.id),
                "customer_name": r.customer.name,
                "customer_ref": r.customer.external_ref,
                "email": r.customer.email,
                **{b: str(money(r.buckets[b])) for b in BUCKETS},
                "total": str(money(sum(r.buckets.values(), Decimal("0")))),
                "open_invoices": r.open_invoices,
                "oldest_due": r.oldest_due.isoformat() if r.oldest_due else None,
                "unapplied_credit": str(money(r.unapplied)),
                "reported_pending": str(money(r.reported)),
            }
        )
    return {
        "as_of": today.isoformat(),
        "currency": "CAD",
        **{b: str(money(totals[b])) for b in BUCKETS},
        "total": str(money(sum(totals.values(), Decimal("0")))),
        "unapplied_credit": str(money(sum((r.unapplied for r in rows.values()), Decimal("0")))),
        "reported_pending": str(money(sum((r.reported for r in rows.values()), Decimal("0")))),
        "customers": customers,
        "payment_instructions": get_settings().payment_instructions,
        "reminder_offsets": reminder_offsets(),
    }


# ------------------------------------------------------------------ reminders


def schedule_reminders(db: Session, at: datetime | None = None) -> int:
    """Queue at most one reminder per (invoice, offset). Run hourly by the worker; safe to run
    concurrently and repeatedly. When several offsets have passed (e.g. the worker was down),
    only the latest is emailed and the earlier ones are recorded as SKIPPED."""
    at = at or now()
    offsets = reminder_offsets()
    queued = 0
    rows = db.execute(
        select(Invoice, Company.timezone)
        .join(Company, Company.id == Invoice.company_id)
        .where(Invoice.state.in_(OPEN_STATES), Invoice.paid_amount < Invoice.total)
    ).all()
    for inv, tzname in rows:
        today = at.astimezone(ZoneInfo(tzname)).date()
        due = inv.due_at.astimezone(ZoneInfo(tzname)).date()
        passed = [o for o in offsets if (today - due).days >= o]
        if not passed:
            continue
        latest = passed[-1]
        for o in passed:
            rid = db.scalar(
                insert(PaymentReminder)
                .values(
                    company_id=inv.company_id,
                    invoice_id=inv.id,
                    offset_days=o,
                    status="QUEUED" if o == latest else "SKIPPED",
                    balance=balance(inv),
                )
                .on_conflict_do_nothing(index_elements=["invoice_id", "offset_days"])
                .returning(PaymentReminder.id)
            )
            if rid is not None and o == latest:
                outbox.enqueue(
                    db,
                    REMINDER_EVENT,
                    {"reminder_id": str(rid)},
                    company_id=inv.company_id,
                    aggregate_id=str(inv.id),
                )
                queued += 1
    db.commit()
    return queued


def handle_reminder(ev: OutboxEvent) -> None:
    db = object_session(ev)
    assert db is not None
    rem = db.get(PaymentReminder, uuid.UUID(ev.payload["reminder_id"]))
    if rem is None:
        raise LookupError("reminder not found")
    if rem.status != "QUEUED":
        return
    inv = db.get(Invoice, rem.invoice_id)
    assert inv is not None
    cust = db.get(Customer, inv.customer_id)
    assert cust is not None
    to = cust.email or inv.email_to
    open_ = balance(inv)
    if inv.state not in OPEN_STATES or open_ <= 0 or not to:
        rem.status = "SKIPPED"  # paid since queueing, or no address on file
        return
    snap = inv.snapshot
    when = "is due on" if rem.offset_days < 0 else "was due on"
    deliver(
        db,
        EmailMessage(
            company_id=inv.company_id,
            dedupe_key=f"payment-reminder:{rem.id}",
            invoice_id=inv.id,
            to_addr=to,
            subject=f"Payment reminder: invoice {inv.invoice_no}",
            body=(
                f"Hello {cust.name},\n\nInvoice {inv.invoice_no} {when} {snap['due_date']}."
                f"\nOpen balance: CAD {open_}\n\n{get_settings().payment_instructions}"
                "\n\nIf you have already paid, thank you; we will confirm it shortly."
            ),
            attachment_sha256=None,
        ),
    )
    rem.status, rem.sent_at, rem.balance = "SENT", now(), open_
