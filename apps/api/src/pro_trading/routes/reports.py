"""Module 12 reports: read-only analytics endpoints.

Every report needs `report.read` and only counts rows from warehouses where the caller holds
it (the same per-warehouse filtering `invoice.read` uses for invoice lists). Receivables are
not repeated here: the Reports page reads `GET /receivables` (payments.aging)."""

import re
import uuid
from datetime import date, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import reports as svc
from ..deps import DbDep, Principal, PrincipalDep, authorize
from ..errors import ApiError
from ..models import Company, Warehouse
from ..payments import now
from ..schemas import (
    CustomerSalesOut,
    ProductSalesOut,
    ReceivingReportOut,
    SalesReportOut,
    StockFlowOut,
)

router = APIRouter(tags=["reports"])

MAX_DAYS = 731  # longest range one request may cover (about two years)
DEFAULT_DAYS = 30
_TZ = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")


def readable(db: Session, p: Principal) -> set[uuid.UUID]:
    ids = db.scalars(select(Warehouse.id).where(Warehouse.company_id == p.company_id)).all()
    return {w for w in ids if p.can("report.read", w)}


def report_scope(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    date_from: date | None = Query(None, description="First day (company timezone)"),
    date_to: date | None = Query(None, description="Last day, inclusive; default today"),
    warehouse_id: uuid.UUID | None = None,
) -> svc.Scope:
    authorize(request, p, "report.read", any_scope=True)
    co = db.get(Company, p.company_id)
    assert co is not None
    tz = co.timezone if _TZ.match(co.timezone) else "UTC"
    to = date_to or now().astimezone(ZoneInfo(tz)).date()
    frm = date_from or to - timedelta(days=DEFAULT_DAYS - 1)
    if frm > to:
        raise ApiError(422, "INVALID_RANGE", "The start date is after the end date")
    if (to - frm).days >= MAX_DAYS:
        raise ApiError(422, "RANGE_TOO_LONG", f"Choose a range of at most {MAX_DAYS} days")
    whs = readable(db, p)
    if warehouse_id is not None:
        authorize(request, p, "report.read", warehouse_id)
        whs &= {warehouse_id}
    return svc.Scope(p.company_id, frozenset(whs), tz, frm, to)


ScopeDep = Annotated[svc.Scope, Depends(report_scope)]
Limit = Annotated[int, Query(ge=1, le=500)]


@router.get("/reports/sales", response_model=SalesReportOut)
def sales_report(
    db: DbDep,
    sc: ScopeDep,
    customer_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    bucket: svc.Bucket = "day",
) -> dict[str, Any]:
    """Totals of issued invoices in the period, quantities per unit and a per-day or per-month
    series (every period present, zero when nothing was invoiced)."""
    return svc.sales_summary(db, sc, customer_id, product_id, bucket)


@router.get("/reports/sales-by-product", response_model=ProductSalesOut)
def sales_by_product(
    db: DbDep,
    sc: ScopeDep,
    customer_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    sort: svc.Sort = "top",
    limit: Limit = 50,
) -> dict[str, Any]:
    """One row per product and unit. With `customer_id` it is that customer's volume and
    average realised rate per product."""
    return svc.sales_by_product(db, sc, customer_id, product_id, sort, limit)


@router.get("/reports/sales-by-customer", response_model=CustomerSalesOut)
def sales_by_customer(
    db: DbDep,
    sc: ScopeDep,
    customer_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    sort: svc.Sort = "top",
    limit: Limit = 50,
) -> dict[str, Any]:
    return svc.sales_by_customer(db, sc, customer_id, product_id, sort, limit)


@router.get("/reports/stock-flow", response_model=StockFlowOut)
def stock_flow(db: DbDep, sc: ScopeDep, product_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Opening + received + dispatched + other = closing, per product and unit, from the
    stock ledger. Closing includes damaged and quarantined lots."""
    return svc.stock_flow(db, sc, product_id)


@router.get("/reports/receiving", response_model=ReceivingReportOut)
def receiving_report(
    db: DbDep,
    sc: ScopeDep,
    vendor_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Posted GRNs per vendor and unit: declared vs accepted / damaged / rejected."""
    return svc.receiving(db, sc, vendor_id, product_id)
