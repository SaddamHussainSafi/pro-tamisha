"""Module 05 invoices: preview + issue from a verified pick, list/read, stored PDF, resend email.

Issue is the only path that posts INVOICE_DISPATCH stock movements or assigns invoice numbers.
It runs inside `mutate()` so idempotency claim, stock, number, document, audit and the email
outbox row commit (or roll back) together."""

import uuid
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import idempotency
from .. import invoicing as svc
from ..deps import DbDep, Principal, PrincipalDep, authorize
from ..errors import ApiError
from ..models import Customer, Invoice, InvoiceDocument, Warehouse
from ..schemas import InvoiceIssueIn, InvoiceOut, InvoicePage, InvoicePreviewIn, InvoicePreviewOut
from ._common import esc, mutate
from .orders import _locked, check_version, get_attempt, get_order, state

router = APIRouter(tags=["invoices"])

Result = tuple[dict[str, Any], str, dict[str, Any]]


class _Empty(BaseModel):
    pass


def readable(db: Session, p: Principal) -> set[uuid.UUID]:
    ids = db.scalars(select(Warehouse.id).where(Warehouse.company_id == p.company_id)).all()
    return {w for w in ids if p.can("invoice.read", w)}


def get_invoice(db: Session, request: Request, p: Principal, iid: uuid.UUID) -> Invoice:
    inv = db.get(Invoice, iid)
    if inv is None or inv.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Invoice not found")
    authorize(request, p, "invoice.read", inv.warehouse_id)
    return inv


def _weights(body: InvoicePreviewIn) -> dict[uuid.UUID, list[Any]]:
    return {k: list(v) for k, v in body.weights.items() if v}


def _already(db: Session, pick_id: uuid.UUID) -> None:
    existing = db.scalar(select(Invoice.id).where(Invoice.picking_attempt_id == pick_id))
    if existing is not None:
        raise ApiError(
            409,
            "ALREADY_INVOICED",
            "This picking sheet has already been invoiced",
            invoice_id=str(existing),
        )


@router.post("/pick-attempts/{pick_id}/invoice-preview", response_model=InvoicePreviewOut)
def preview_invoice(
    pick_id: uuid.UUID, body: InvoicePreviewIn, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    """Read-only draft: billed lines, totals and stock check. Nothing is numbered or posted."""
    a = get_attempt(db, p, pick_id)
    if not (p.can("invoice.prepare", a.warehouse_id) or p.can("invoice.issue", a.warehouse_id)):
        authorize(request, p, "invoice.prepare", a.warehouse_id)
    _already(db, a.id)
    state(a, ("VERIFIED",), "Picking sheet")
    o = get_order(db, p, a.order_id)
    customer = db.get(Customer, o.customer_id)
    assert customer is not None
    draft = svc.compute(db, o, a, customer, _weights(body))
    stock = svc.stock_check(db, o, draft)
    return {
        "pick_id": str(a.id),
        "pick_code": a.pick_code,
        "pick_version": a.version,
        "order_id": str(o.id),
        "order_no": o.order_no,
        "customer_name": customer.name,
        "email_to": customer.email,
        "payment_terms_days": customer.payment_terms_days,
        "currency": "CAD",
        "lines": [ln.out() for ln in draft.lines],
        "stock": stock,
        "weighable_pick_line_ids": [str(i) for i in draft.weighable],
        "subtotal": str(draft.subtotal),
        "tax_amount": str(draft.tax_amount),
        "total": str(draft.total),
        "tax_note": svc.TAX_NOTE,
        "can_issue": bool(draft.lines) and all(s["ok"] for s in stock),
    }


@router.post("/pick-attempts/{pick_id}/issue-invoice", response_model=InvoiceOut, status_code=201)
def issue_invoice(
    pick_id: uuid.UUID,
    body: InvoiceIssueIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    a0 = get_attempt(db, p, pick_id)
    authorize(request, p, "invoice.issue", a0.warehouse_id)
    if not body.dispatch_confirmed:
        raise ApiError(
            422,
            "DISPATCH_NOT_CONFIRMED",
            "Confirm the goods were dispatched before issuing the invoice (D-01)",
        )

    def fn() -> Result:
        o, a = _locked(db, p, pick_id)
        if a.status == "INVOICED":
            _already(db, a.id)
        state(a, ("VERIFIED",), "Picking sheet")
        check_version(a.version, body.version, "Picking sheet")
        invoice = svc.issue(db, p.employee, o, a, _weights(body), body.note.strip())
        db.flush()
        out = svc.invoice_out(db, invoice)
        return (
            out,
            str(invoice.id),
            {"invoice_no": invoice.invoice_no, "total": str(invoice.total), "order": o.order_no},
        )

    return mutate(
        db,
        request,
        p,
        key,
        f"invoice.issue:{pick_id}",
        body,
        201,
        "invoice.issue",
        fn,
        "This picking sheet has already been invoiced",
    )


@router.get("/invoices", response_model=InvoicePage)
def list_invoices(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    state_: str | None = Query(None, alias="state", pattern=r"^[A-Z_]{4,24}$"),
    customer_id: uuid.UUID | None = None,
    sales_order_id: uuid.UUID | None = None,
    picking_attempt_id: uuid.UUID | None = None,
    email_status: str | None = Query(None, pattern=r"^[A-Z_]{4,24}$"),
    open_: bool | None = Query(None, alias="open"),
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, pattern=r"^\d{1,7}$"),
) -> dict[str, Any]:
    authorize(request, p, "invoice.read", any_scope=True)
    stmt = (
        select(Invoice)
        .join(Customer, Customer.id == Invoice.customer_id)
        .where(Invoice.company_id == p.company_id, Invoice.warehouse_id.in_(readable(db, p)))
    )
    if state_:
        stmt = stmt.where(Invoice.state == state_)
    if customer_id:
        stmt = stmt.where(Invoice.customer_id == customer_id)
    if sales_order_id:
        stmt = stmt.where(Invoice.sales_order_id == sales_order_id)
    if picking_attempt_id:
        stmt = stmt.where(Invoice.picking_attempt_id == picking_attempt_id)
    if email_status:
        stmt = stmt.where(Invoice.email_status == email_status)
    if open_ is not None:
        is_open = Invoice.state.in_(("ISSUED", "PARTIALLY_PAID"))
        stmt = stmt.where(is_open if open_ else ~is_open)
    if q:
        like = f"%{esc(q.strip())}%"
        stmt = stmt.where(
            or_(
                Invoice.invoice_no.ilike(like, escape="\\"),
                Customer.name.ilike(like, escape="\\"),
                Customer.external_ref.ilike(like, escape="\\"),
                Invoice.snapshot["order"]["order_no"].astext.ilike(like, escape="\\"),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    off = int(cursor or 0)
    rows = db.scalars(
        stmt.order_by(Invoice.issued_at.desc(), Invoice.invoice_no.desc()).offset(off).limit(limit)
    ).all()
    items = [svc.summary(i) for i in rows]
    nxt = off + len(items)
    return {"items": items, "total": total, "next_cursor": str(nxt) if nxt < total else None}


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def read_invoice(
    invoice_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    return svc.invoice_out(db, get_invoice(db, request, p, invoice_id))


@router.get(
    "/invoices/{invoice_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def invoice_pdf(invoice_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep) -> Response:
    """The exact bytes stored at issue (never re-rendered), so the hash always matches."""
    inv = get_invoice(db, request, p, invoice_id)
    doc = db.scalar(
        select(InvoiceDocument).where(
            InvoiceDocument.invoice_id == inv.id, InvoiceDocument.kind == "ISSUED"
        )
    )
    if doc is None:
        raise ApiError(404, "NOT_FOUND", "Invoice document not found")
    etag = f'"{doc.sha256}"'
    headers = {
        "ETag": etag,
        "Cache-Control": "private, max-age=0, must-revalidate",
        "Content-Disposition": f'inline; filename="{inv.invoice_no}.pdf"',
        "X-Content-SHA256": doc.sha256,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(bytes(doc.content), media_type="application/pdf", headers=headers)


@router.post("/invoices/{invoice_id}/email", response_model=InvoiceOut)
def resend_email(
    invoice_id: uuid.UUID,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    inv0 = get_invoice(db, request, p, invoice_id)
    if not (
        p.can("invoice.issue", inv0.warehouse_id) or p.can("invoice.prepare", inv0.warehouse_id)
    ):
        authorize(request, p, "invoice.issue", inv0.warehouse_id)

    def fn() -> Result:
        inv = db.get(Invoice, invoice_id, with_for_update=True)
        assert inv is not None
        db.refresh(inv)
        svc.resend(db, inv)
        db.flush()
        return svc.invoice_out(db, inv), str(inv.id), {"send": inv.email_sends}

    return mutate(
        db, request, p, key, f"invoice.email:{invoice_id}", _Empty(), 200, "invoice.email", fn
    )
