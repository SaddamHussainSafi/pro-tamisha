"""Customers and customer groups (minimal master data needed for price overrides).

Reads: any-scope `catalog.read` or `price.read`. Mutations: company-wide `catalog.manage`.
Customers are optimistically versioned; every mutation is idempotent and audited.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import idempotency
from ..deps import DbDep, Principal, PrincipalDep, deny, require_permission
from ..errors import ApiError
from ..models import Customer, CustomerGroup
from ..schemas import CustomerGroupIn, CustomerGroupOut, CustomerIn, CustomerOut, CustomerPatch
from ._common import esc, mutate

router = APIRouter(tags=["customers"])
Manage = Depends(require_permission("catalog.manage"))


def read_check(request: Request, p: Principal) -> None:
    if not (p.can("catalog.read", any_scope=True) or p.can("price.read", any_scope=True)):
        raise deny(request, p, "catalog.read", None)


def get_customer(db: Session, p: Principal, cid: uuid.UUID, lock: bool = False) -> Customer:
    c = db.get(Customer, cid, with_for_update=lock)
    if c is None or c.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Customer not found")
    return c


def check_group(db: Session, p: Principal, gid: uuid.UUID | None) -> None:
    if gid is None:
        return
    g = db.get(CustomerGroup, gid)
    if g is None or g.company_id != p.company_id:
        raise ApiError(422, "INVALID_GROUP", "Customer group not found")


def _out(c: Customer) -> dict[str, Any]:
    return CustomerOut.model_validate(c).model_dump(mode="json")


@router.get("/customer-groups", response_model=list[CustomerGroupOut])
def list_groups(request: Request, db: DbDep, p: PrincipalDep) -> list[CustomerGroup]:
    read_check(request, p)
    return list(
        db.scalars(
            select(CustomerGroup)
            .where(CustomerGroup.company_id == p.company_id)
            .order_by(CustomerGroup.code)
        )
    )


@router.post("/customer-groups", status_code=201, response_model=CustomerGroupOut)
def create_group(
    body: CustomerGroupIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        g = CustomerGroup(company_id=p.company_id, code=body.code, name=body.name.strip())
        db.add(g)
        db.flush()
        out = CustomerGroupOut.model_validate(g).model_dump(mode="json")
        return out, str(g.id), body.model_dump(mode="json")

    return mutate(
        db, request, p, key, "customer_group.create", body, 201, "customer_group.create", fn,
        "Group code already exists",
    )  # fmt: skip


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    q: str | None = Query(None, max_length=100),
    group_id: uuid.UUID | None = None,
    active: bool | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[Customer]:
    read_check(request, p)
    stmt = select(Customer).where(Customer.company_id == p.company_id)
    if q:
        like = f"%{esc(q.strip().lower())}%"
        stmt = stmt.where(
            or_(
                func.lower(Customer.name).like(like, escape="\\"),
                func.lower(Customer.external_ref).like(like, escape="\\"),
            )
        )
    if group_id:
        stmt = stmt.where(Customer.group_id == group_id)
    if active is not None:
        stmt = stmt.where(Customer.active.is_(active))
    return list(db.scalars(stmt.order_by(func.lower(Customer.name), Customer.id).limit(limit)))


@router.get("/customers/{customer_id}", response_model=CustomerOut)
def get_one(customer_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep) -> Customer:
    read_check(request, p)
    return get_customer(db, p, customer_id)


@router.post("/customers", status_code=201, response_model=CustomerOut)
def create_customer(
    body: CustomerIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        check_group(db, p, body.group_id)
        c = Customer(
            company_id=p.company_id,
            external_ref=body.external_ref,
            name=body.name.strip(),
            email=body.email,
            phone=body.phone,
            group_id=body.group_id,
            payment_terms_days=body.payment_terms_days,
        )
        db.add(c)
        db.flush()
        db.refresh(c)
        return _out(c), str(c.id), body.model_dump(mode="json")

    return mutate(
        db, request, p, key, "customer.create", body, 201, "customer.create", fn,
        "Customer reference already exists",
    )  # fmt: skip


@router.patch("/customers/{customer_id}", response_model=CustomerOut)
def patch_customer(
    customer_id: uuid.UUID,
    body: CustomerPatch,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        c = get_customer(db, p, customer_id, lock=True)
        if c.version != body.version:
            raise ApiError(
                409, "VERSION_CONFLICT", "Customer changed; reload and retry",
                current_version=c.version,
            )  # fmt: skip
        before = _out(c)
        if body.group_id is not None:
            check_group(db, p, body.group_id)
            c.group_id = body.group_id
        if body.clear_group:
            c.group_id = None
        for f in ("name", "email", "phone", "payment_terms_days", "active"):
            v = getattr(body, f)
            if v is not None:
                setattr(c, f, v.strip() if isinstance(v, str) else v)
        c.version += 1
        c.updated_at = datetime.now(UTC)
        db.flush()
        after = _out(c)
        return after, str(c.id), {"before": before, "after": after}

    return mutate(
        db, request, p, key, f"customer.patch:{customer_id}", body, 200, "customer.update", fn
    )
