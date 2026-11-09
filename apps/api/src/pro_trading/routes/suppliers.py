"""Supplier ledger (billed / paid / returned) and payments we make to suppliers.

Reads need company-wide `supplier.ledger.read`; recording or voiding a payment needs company-wide
`supplier_payment.record`. Payments are append-only: a mistake is voided with a reason. The same
bank reference cannot be recorded twice for one supplier unless the first one is voided."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import idempotency
from .. import suppliers as svc
from ..deps import DbDep, Principal, require_permission
from ..errors import ApiError
from ..models import Company, Shipment, SupplierPayment, Vendor
from ..payments import local_today
from ..schemas import (
    SupplierBalanceOut,
    SupplierLedgerOut,
    SupplierPaymentIn,
    SupplierPaymentOut,
    SupplierPaymentVoidIn,
)
from ._common import mutate

router = APIRouter(tags=["suppliers"])
Read = Depends(require_permission("supplier.ledger.read"))
Record = Depends(require_permission("supplier_payment.record"))
Result = tuple[dict[str, Any], str, dict[str, Any]]


def _vendor(db: Session, p: Principal, vid: uuid.UUID) -> Vendor:
    v = db.get(Vendor, vid)
    if v is None or v.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Supplier not found")
    return v


def _json(d: dict[str, Any]) -> dict[str, Any]:
    return SupplierPaymentOut.model_validate(d).model_dump(mode="json")


@router.get("/suppliers/balances", response_model=list[SupplierBalanceOut])
def supplier_balances(db: DbDep, p: Principal = Read) -> list[dict[str, Any]]:
    return svc.balances(db, p.company_id)


@router.get("/suppliers/{vendor_id}/ledger", response_model=SupplierLedgerOut)
def supplier_ledger(vendor_id: uuid.UUID, db: DbDep, p: Principal = Read) -> dict[str, Any]:
    return svc.ledger(db, p.company_id, _vendor(db, p, vendor_id))


@router.get("/supplier-payments", response_model=list[SupplierPaymentOut])
def list_supplier_payments(
    db: DbDep,
    p: Principal = Read,
    vendor_id: uuid.UUID | None = None,
    include_voided: bool = True,
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    q = select(SupplierPayment).where(SupplierPayment.company_id == p.company_id)
    if vendor_id:
        q = q.where(SupplierPayment.vendor_id == vendor_id)
    if not include_voided:
        q = q.where(SupplierPayment.voided_at.is_(None))
    rows = db.scalars(
        q.order_by(SupplierPayment.paid_on.desc(), SupplierPayment.payment_no.desc()).limit(limit)
    )
    return [svc.payment_out(db, x) for x in rows]


@router.post("/supplier-payments", status_code=201, response_model=SupplierPaymentOut)
def record_supplier_payment(
    body: SupplierPaymentIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Record,
) -> JSONResponse:
    def fn() -> Result:
        v = _vendor(db, p, body.vendor_id)
        if body.shipment_id is not None:
            s = db.get(Shipment, body.shipment_id)
            if s is None or s.company_id != p.company_id or s.vendor_id != v.id:
                raise ApiError(422, "INVALID_SHIPMENT", "That shipment is not from this supplier")
        today = local_today(db, p.company_id)
        if body.paid_on > today + timedelta(days=1):
            raise ApiError(422, "FUTURE_DATE", "The payment date is in the future")
        ref = (body.reference or "").strip() or None
        # Serialize per company before the duplicate check, so two concurrent requests with the
        # same reference can't both pass it (next_no takes the same lock again).
        db.get(Company, p.company_id, with_for_update={"key_share": True})
        if ref is not None:
            dup = db.scalar(
                select(SupplierPayment.payment_no).where(
                    SupplierPayment.company_id == p.company_id,
                    SupplierPayment.vendor_id == v.id,
                    func.lower(SupplierPayment.reference) == ref.lower(),
                    SupplierPayment.voided_at.is_(None),
                )
            )
            if dup:
                raise ApiError(
                    409, "DUPLICATE_REFERENCE",
                    f"This reference is already recorded as {dup}", payment_no=dup,
                )  # fmt: skip
        sp = SupplierPayment(
            company_id=p.company_id,
            payment_no=svc.next_no(db, p.company_id, today.year),
            vendor_id=v.id,
            shipment_id=body.shipment_id,
            method=body.method,
            amount=body.amount,
            currency=body.currency or v.currency,
            paid_on=body.paid_on,
            reference=ref,
            note=(body.note or "").strip() or None,
            recorded_by=p.employee.id,
        )
        db.add(sp)
        db.flush()
        db.refresh(sp)
        out = _json(svc.payment_out(db, sp))
        return out, str(sp.id), {"payment_no": sp.payment_no, "vendor": v.code,
                                 "amount": str(sp.amount), "currency": sp.currency}  # fmt: skip

    return mutate(
        db, request, p, key, "supplier_payment.record", body, 201, "supplier_payment.record", fn
    )


@router.post("/supplier-payments/{payment_id}/void", response_model=SupplierPaymentOut)
def void_supplier_payment(
    payment_id: uuid.UUID,
    body: SupplierPaymentVoidIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Record,
) -> JSONResponse:
    def fn() -> Result:
        sp = db.get(SupplierPayment, payment_id, with_for_update=True, populate_existing=True)
        if sp is None or sp.company_id != p.company_id:
            raise ApiError(404, "NOT_FOUND", "Supplier payment not found")
        if sp.voided_at is not None:
            raise ApiError(409, "ALREADY_VOIDED", f"{sp.payment_no} is already voided")
        sp.voided_at, sp.voided_by = datetime.now(UTC), p.employee.id
        sp.void_reason = body.reason.strip()
        db.flush()
        return _json(svc.payment_out(db, sp)), str(sp.id), {"payment_no": sp.payment_no,
                                                            "reason": sp.void_reason}  # fmt: skip

    return mutate(
        db, request, p, key, f"supplier_payment.void:{payment_id}", body, 200,
        "supplier_payment.void", fn,
    )  # fmt: skip
