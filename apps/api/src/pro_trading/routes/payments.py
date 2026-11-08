"""Module 10 payments: record (customer says sent) -> verify (bank checked) -> apply / reverse,
or reject; receivables aging. Every command runs inside `mutate()` (idempotency + audit + one
transaction) and locks the payment row first, then the invoices it touches (sorted by id).

Payments are a company-level ledger: `payment.record` / `payment.verify` grants of any scope
allow the payment itself; applying money to an invoice also needs `payment.verify` for that
invoice's warehouse."""

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import idempotency
from .. import payments as svc
from ..deps import DbDep, Principal, PrincipalDep, authorize, deny
from ..errors import ApiError
from ..models import PAYMENT_STATUSES, Customer, Invoice, Payment, PaymentAllocation
from ..schemas import (
    AllocationIn,
    AllocationReverseIn,
    PaymentAllocateIn,
    PaymentOut,
    PaymentPage,
    PaymentRecordIn,
    PaymentRejectIn,
    PaymentVerifyIn,
    ReceivablesOut,
)
from ._common import esc, mutate
from .invoices import readable
from .orders import check_version

router = APIRouter(tags=["payments"])

Result = tuple[dict[str, Any], str, dict[str, Any]]


def can_read(p: Principal) -> bool:
    return p.can("payment.record", any_scope=True) or p.can("payment.verify", any_scope=True)


def read_guard(request: Request, p: Principal) -> None:
    if not can_read(p):
        raise deny(request, p, "payment.record", None)


def get_payment(db: Session, p: Principal, pid: uuid.UUID) -> Payment:
    pay = db.get(Payment, pid)
    if pay is None or pay.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Payment not found")
    return pay


def _scope_invoices(db: Session, request: Request, p: Principal, items: list[AllocationIn]) -> None:
    """Applying money changes the invoice: needs payment.verify in its warehouse. Warehouse is
    immutable on issued invoices, so checking before taking locks is safe."""
    ids = {a.invoice_id for a in items}
    if not ids:
        return
    rows = db.execute(
        select(Invoice.id, Invoice.warehouse_id).where(
            Invoice.id.in_(ids), Invoice.company_id == p.company_id
        )
    ).all()
    for _, wh in rows:
        authorize(request, p, "payment.verify", wh)


def _items(items: list[AllocationIn]) -> list[tuple[uuid.UUID, Decimal]]:
    return [(a.invoice_id, a.amount) for a in items]


@router.get("/payments", response_model=PaymentPage)
def list_payments(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    status: str | None = Query(None, pattern=r"^[A-Z]{6,10}$"),
    customer_id: uuid.UUID | None = None,
    method: str | None = Query(None, pattern=r"^[A-Z]{3,10}$"),
    invoice_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, pattern=r"^\d{1,7}$"),
) -> dict[str, Any]:
    read_guard(request, p)
    base = (
        select(Payment)
        .join(Customer, Customer.id == Payment.customer_id)
        .where(Payment.company_id == p.company_id)
    )
    if customer_id:
        base = base.where(Payment.customer_id == customer_id)
    if method:
        base = base.where(Payment.method == method)
    if invoice_id:
        base = base.where(Payment.reported_invoice_id == invoice_id)
    if q:
        like = f"%{esc(q.strip())}%"
        base = base.where(
            or_(
                Payment.payment_no.ilike(like, escape="\\"),
                Payment.payer_ref.ilike(like, escape="\\"),
                Payment.verification_ref.ilike(like, escape="\\"),
                Customer.name.ilike(like, escape="\\"),
                Customer.external_ref.ilike(like, escape="\\"),
            )
        )
    sq = base.subquery()
    counted = db.execute(select(sq.c.status, func.count()).group_by(sq.c.status))
    counts = dict.fromkeys(PAYMENT_STATUSES, 0) | {s: n for s, n in counted.all()}
    stmt = base.where(Payment.status == status) if status else base
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    off = int(cursor or 0)
    rows = db.scalars(
        stmt.order_by(Payment.created_at.desc(), Payment.payment_no.desc()).offset(off).limit(limit)
    ).all()
    items = svc.summaries(db, list(rows)) if rows else []
    nxt = off + len(items)
    return {
        "items": items,
        "total": total,
        "next_cursor": str(nxt) if nxt < total else None,
        "counts": counts,
    }


@router.post("/payments", response_model=PaymentOut, status_code=201)
def record_payment(
    body: PaymentRecordIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    authorize(request, p, "payment.record", any_scope=True)

    def fn() -> Result:
        pay = svc.record(
            db,
            p.employee,
            customer_id=body.customer_id,
            method=body.method,
            amount=body.amount,
            received_on=body.received_on,
            payer_ref=(body.payer_ref or "").strip() or None,
            invoice_id=body.invoice_id,
            note=(body.note or "").strip() or None,
        )
        out = svc.payment_out(db, pay)
        return (
            out,
            str(pay.id),
            {"payment_no": pay.payment_no, "amount": str(pay.amount), "method": pay.method},
        )

    return mutate(db, request, p, key, "payment.record", body, 201, "payment.record", fn)


@router.get("/payments/{payment_id}", response_model=PaymentOut)
def read_payment(
    payment_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    read_guard(request, p)
    return svc.payment_out(db, get_payment(db, p, payment_id))


@router.post("/payments/{payment_id}/verify", response_model=PaymentOut)
def verify_payment(
    payment_id: uuid.UUID,
    body: PaymentVerifyIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Bank / cheque / receipt checked by an authorised employee. Optional allocations are
    applied in the same transaction."""
    authorize(request, p, "payment.verify", any_scope=True)
    get_payment(db, p, payment_id)
    _scope_invoices(db, request, p, body.allocations)

    def fn() -> Result:
        pay = svc.lock(db, payment_id)
        if pay.status not in ("VERIFIED", "APPLIED"):  # an already-confirmed payment says so
            check_version(pay.version, body.version, "Payment")
        svc.verify(
            db,
            p.employee,
            pay,
            body.verification_ref.strip(),
            (body.verification_note or "").strip() or None,
        )
        if body.allocations:
            svc.allocate(db, p.employee, pay, _items(body.allocations))
        return (
            svc.payment_out(db, pay),
            str(pay.id),
            {
                "payment_no": pay.payment_no,
                "verification_ref": pay.verification_ref,
                "allocated": str(pay.allocated_amount),
            },
        )

    return mutate(
        db,
        request,
        p,
        key,
        f"payment.verify:{payment_id}",
        body,
        200,
        "payment.verify",
        fn,
        "This bank reference already confirmed another payment",
    )


@router.post("/payments/{payment_id}/allocations", response_model=PaymentOut)
def allocate_payment(
    payment_id: uuid.UUID,
    body: PaymentAllocateIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    authorize(request, p, "payment.verify", any_scope=True)
    get_payment(db, p, payment_id)
    _scope_invoices(db, request, p, body.allocations)

    def fn() -> Result:
        pay = svc.lock(db, payment_id)
        check_version(pay.version, body.version, "Payment")
        rows = svc.allocate(db, p.employee, pay, _items(body.allocations))
        return (
            svc.payment_out(db, pay),
            str(pay.id),
            {
                "payment_no": pay.payment_no,
                "allocations": [
                    {"invoice_id": str(a.invoice_id), "amount": str(a.amount)} for a in rows
                ],
            },
        )

    return mutate(
        db, request, p, key, f"payment.allocate:{payment_id}", body, 200, "payment.allocate", fn
    )


@router.post(
    "/payments/{payment_id}/allocations/{allocation_id}/reverse", response_model=PaymentOut
)
def reverse_allocation(
    payment_id: uuid.UUID,
    allocation_id: uuid.UUID,
    body: AllocationReverseIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    authorize(request, p, "payment.verify", any_scope=True)
    get_payment(db, p, payment_id)
    wh = db.scalar(
        select(Invoice.warehouse_id)
        .join_from(Invoice, PaymentAllocation, PaymentAllocation.invoice_id == Invoice.id)
        .where(PaymentAllocation.id == allocation_id)
    )
    if wh is not None:
        authorize(request, p, "payment.verify", wh)

    def fn() -> Result:
        pay = svc.lock(db, payment_id)
        check_version(pay.version, body.version, "Payment")
        r = svc.reverse(db, p.employee, pay, allocation_id, body.reason.strip())
        return (
            svc.payment_out(db, pay),
            str(pay.id),
            {
                "payment_no": pay.payment_no,
                "reversed": str(allocation_id),
                "amount": str(r.amount),
                "reason": r.reason,
            },
        )

    return mutate(
        db,
        request,
        p,
        key,
        f"payment.reverse:{allocation_id}",
        body,
        200,
        "payment.reverse",
        fn,
        "This allocation was already reversed",
    )


@router.post("/payments/{payment_id}/reject", response_model=PaymentOut)
def reject_payment(
    payment_id: uuid.UUID,
    body: PaymentRejectIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Not found at the bank (or wrong amount): the report is closed, never deleted."""
    authorize(request, p, "payment.verify", any_scope=True)
    get_payment(db, p, payment_id)

    def fn() -> Result:
        pay = svc.lock(db, payment_id)
        if pay.status == "REPORTED":
            check_version(pay.version, body.version, "Payment")
        svc.reject(db, p.employee, pay, body.reason.strip())
        return (
            svc.payment_out(db, pay),
            str(pay.id),
            {"payment_no": pay.payment_no, "reason": pay.rejected_reason},
        )

    return mutate(
        db, request, p, key, f"payment.reject:{payment_id}", body, 200, "payment.reject", fn
    )


@router.get("/receivables", response_model=ReceivablesOut)
def receivables(request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    """Aging of open invoices (warehouse-scoped by invoice.read) with confirmed-but-unapplied
    and reported-but-unconfirmed payment totals per customer."""
    authorize(request, p, "invoice.read", any_scope=True)
    read_guard(request, p)
    return svc.aging(db, p.company_id, readable(db, p))
