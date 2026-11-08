"""Warehouses. Mutations are idempotent and scope-checked against the target warehouse."""

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import audit, idempotency
from ..deps import DbDep, Principal, PrincipalDep, authorize, require_permission
from ..errors import ApiError
from ..models import Warehouse
from ..schemas import WarehouseIn, WarehouseOut, WarehousePatch

router = APIRouter(prefix="/warehouses", tags=["warehouses"])


@router.get("", response_model=list[WarehouseOut])
def list_warehouses(request: Request, db: DbDep, p: PrincipalDep) -> list[Warehouse]:
    authorize(request, p, "warehouse.read", any_scope=True)
    rows = db.scalars(
        select(Warehouse).where(Warehouse.company_id == p.company_id).order_by(Warehouse.code)
    ).all()
    return [w for w in rows if p.can("warehouse.read", w.id)]


@router.post("", status_code=201, response_model=WarehouseOut)
def create_warehouse(
    body: WarehouseIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Depends(require_permission("warehouse.manage")),
) -> JSONResponse:
    scope = f"{p.company_id}:warehouse.create"
    if (replay := idempotency.begin(db, scope, key, body.model_dump())) is not None:
        db.commit()
        return JSONResponse(replay[1], replay[0], headers={"Idempotent-Replay": "true"})
    wh = Warehouse(company_id=p.company_id, code=body.code, name=body.name)
    db.add(wh)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "DUPLICATE", "Warehouse code or name already exists") from None
    out = WarehouseOut.model_validate(wh).model_dump(mode="json")
    audit.record(
        db,
        action="warehouse.create",
        success=True,
        company_id=p.company_id,
        actor_id=p.employee.id,
        target_type="warehouse",
        target_id=str(wh.id),
        warehouse_id=wh.id,
        request_id=request.state.request_id,
        detail=body.model_dump(),
    )
    idempotency.finish(db, scope, key, 201, out)
    db.commit()
    return JSONResponse(out, 201)


@router.patch("/{warehouse_id}", response_model=WarehouseOut)
def update_warehouse(
    warehouse_id: uuid.UUID,
    body: WarehousePatch,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Depends(require_permission("warehouse.manage", warehouse_param="warehouse_id")),
) -> JSONResponse:
    scope = f"{p.company_id}:warehouse.update:{warehouse_id}"
    if (replay := idempotency.begin(db, scope, key, body.model_dump())) is not None:
        db.commit()
        return JSONResponse(replay[1], replay[0], headers={"Idempotent-Replay": "true"})
    wh = db.get(Warehouse, warehouse_id, with_for_update=True)
    if wh is None or wh.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Warehouse not found")
    if wh.version != body.version:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            "Warehouse changed; reload and retry",
            current_version=wh.version,
        )
    before = {"name": wh.name, "active": wh.active}
    wh.name, wh.active, wh.version = body.name, body.active, wh.version + 1
    db.flush()
    out = WarehouseOut.model_validate(wh).model_dump(mode="json")
    audit.record(
        db,
        action="warehouse.update",
        success=True,
        company_id=p.company_id,
        actor_id=p.employee.id,
        target_type="warehouse",
        target_id=str(wh.id),
        warehouse_id=wh.id,
        request_id=request.state.request_id,
        detail={"before": before, "after": {"name": wh.name, "active": wh.active}},
    )
    idempotency.finish(db, scope, key, 200, out)
    db.commit()
    return JSONResponse(out, 200)
