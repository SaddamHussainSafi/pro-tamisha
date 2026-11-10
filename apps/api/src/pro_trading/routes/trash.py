"""Trash bin API (see `trash.py`).

- `DELETE /<record>/{item_id}` (one typed route per kind) moves a record to the Trash.
- `GET /trash`, `POST /trash/{id}/restore`, `DELETE /trash/{id}` (delete forever),
  `POST /trash/empty`. Items left 30 days are purged by the worker (`pro_trading.purge_trash`).
- `POST /delete-requests` lets staff who may edit a record ask for it to be deleted;
  `GET /delete-requests`, `.../approve` and `.../dismiss` are the Super Admin's side.

Moving, restoring and deleting forever need `trash.manage` (Super Admin only) and a session
that passed two-step sign-in with an enrolled authenticator. Reading the Trash needs
`trash.manage` only. Every mutation takes an Idempotency-Key and is audited.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .. import audit, idempotency
from .. import trash as svc
from ..deps import DbDep, Principal, PrincipalDep, authorize, deny
from ..errors import ApiError
from ..models import Employee

router = APIRouter(tags=["trash"])

KindKey = Literal[
    "product",
    "case_size",
    "customer",
    "customer_group",
    "supplier",
    "price_draft",
    "sales_order",
    "picking_sheet",
    "scan",
    "document",
    "user",
    "notification",
    "website_entry",
]

# Where each kind's DELETE lives: next to the record's own routes.
PATHS: dict[str, str] = {
    "product": "/products/{item_id}",
    "case_size": "/unit-profiles/{item_id}",
    "customer": "/customers/{item_id}",
    "customer_group": "/customer-groups/{item_id}",
    "supplier": "/vendors/{item_id}",
    "price_draft": "/price-books/versions/{item_id}",
    "sales_order": "/sales-orders/{item_id}",
    "picking_sheet": "/pick-attempts/{item_id}",
    "scan": "/scans/jobs/{item_id}",
    "document": "/documents/{item_id}",
    "user": "/employees/{item_id}",
    "notification": "/notifications/{item_id}",
    "website_entry": "/order-connect/submissions/{item_id}",
}
assert set(PATHS) == set(svc.KINDS)


# ------------------------------------------------------------------ schemas


class TrashItemOut(BaseModel):
    id: uuid.UUID
    kind: KindKey
    item_id: uuid.UUID
    label: str
    trashed_by: str | None
    trashed_at: datetime
    purge_after: datetime
    days_left: int
    already_in_trash: bool = False


class RestoredOut(BaseModel):
    kind: KindKey
    item_id: uuid.UUID
    label: str


class PurgedOut(BaseModel):
    purged: int


class DeleteRequestIn(BaseModel):
    kind: KindKey
    item_id: uuid.UUID
    reason: str = Field("", max_length=500)


class DismissIn(BaseModel):
    note: str = Field("", max_length=500)


class DeleteRequestOut(BaseModel):
    id: uuid.UUID
    kind: KindKey
    item_id: uuid.UUID
    label: str
    reason: str
    status: Literal["PENDING", "APPROVED", "DISMISSED"]
    requested_by: str | None
    created_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str


# ------------------------------------------------------------------ access


def trash_reader(request: Request, p: PrincipalDep) -> Principal:
    authorize(request, p, svc.TRASH_PERMISSION)
    return p


def trash_admin(request: Request, p: PrincipalDep) -> Principal:
    """Super Admin with two-step sign-in turned on and passed for this session."""
    authorize(request, p, svc.TRASH_PERMISSION)
    if not (p.mfa_enrolled and p.session.mfa_verified):
        audit.record_independent(
            action="permission.denied",
            success=False,
            company_id=p.company_id,
            actor_id=p.employee.id,
            request_id=getattr(request.state, "request_id", None),
            target_type="route",
            target_id=f"{request.method} {request.url.path}",
            detail={"permission": svc.TRASH_PERMISSION, "two_step": "not enrolled"},
        )
        raise ApiError(
            403,
            "TWO_STEP_REQUIRED",
            "Turn on two-step sign-in in My account before deleting or restoring records.",
        )
    return p


Reader = Depends(trash_reader)
Admin = Depends(trash_admin)


# ------------------------------------------------------------------ helpers


def _names(db: Session, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    real = {i for i in ids if i is not None}
    if not real:
        return {}
    return dict(
        db.execute(select(Employee.id, Employee.display_name).where(Employee.id.in_(real))).all()
    )


def _item_out(
    db: Session, t: svc.TrashItem, already: bool = False, names: dict[uuid.UUID, str] | None = None
) -> dict[str, Any]:
    names = names if names is not None else _names(db, {t.trashed_by})
    left = (t.purge_after - datetime.now(UTC)).total_seconds() / 86400
    return TrashItemOut(
        id=t.id,
        kind=t.kind,
        item_id=t.item_id,
        label=t.label,
        trashed_by=names.get(t.trashed_by) if t.trashed_by else None,
        trashed_at=t.trashed_at,
        purge_after=t.purge_after,
        days_left=max(0, int(left + 0.999)),
        already_in_trash=already,
    ).model_dump(mode="json")


def _request_out(
    db: Session, r: svc.DeleteRequest, names: dict[uuid.UUID, str] | None = None
) -> dict[str, Any]:
    names = names if names is not None else _names(db, {r.requested_by, r.decided_by})
    return DeleteRequestOut(
        id=r.id,
        kind=r.kind,
        item_id=r.item_id,
        label=r.label,
        reason=r.reason,
        status=r.status,
        requested_by=names.get(r.requested_by) if r.requested_by else None,
        created_at=r.created_at,
        decided_by=names.get(r.decided_by) if r.decided_by else None,
        decided_at=r.decided_at,
        decision_note=r.decision_note,
    ).model_dump(mode="json")


def _command(
    db: Session,
    p: Principal,
    key: str,
    scope: str,
    payload: dict[str, Any],
    fn: Callable[[], tuple[int, dict[str, Any]]],
) -> JSONResponse:
    """Idempotency claim -> fn() (does its own audit rows) -> store response -> commit."""
    sc = f"{p.company_id}:{scope}"
    if (replay := idempotency.begin(db, sc, key, payload)) is not None:
        db.commit()
        return JSONResponse(replay[1], replay[0], headers={"Idempotent-Replay": "true"})
    status, out = fn()
    idempotency.finish(db, sc, key, status, out)
    db.commit()
    return JSONResponse(out, status)


def _trash_item(db: Session, p: Principal, trash_id: uuid.UUID) -> svc.TrashItem:
    t = db.get(svc.TrashItem, trash_id, with_for_update=True, populate_existing=True)
    if t is None or t.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "That item is no longer in the Trash")
    return t


# ------------------------------------------------------------------ move to Trash


def _move(
    db: Session, request: Request, p: Principal, key: str, kind: svc.Kind, item_id: uuid.UUID
) -> JSONResponse:
    def run() -> tuple[int, dict[str, Any]]:
        item, already = svc.move_to_trash(
            db, kind, p.company_id, p.employee.id, item_id, request.state.request_id
        )
        return 200, _item_out(db, item, already)

    return _command(db, p, key, "trash.move", {"kind": kind.key, "item_id": str(item_id)}, run)


def _delete_route(kind: svc.Kind) -> Callable[..., JSONResponse]:
    def handler(
        item_id: uuid.UUID,
        request: Request,
        db: DbDep,
        key: idempotency.IdemKey,
        p: Principal = Admin,
    ) -> JSONResponse:
        return _move(db, request, p, key, kind, item_id)

    handler.__name__ = f"trash_{kind.key}"
    handler.__doc__ = (
        f"Move a {kind.noun} to the Trash (Super Admin, two-step sign-in). 409 IN_USE names "
        "what still references it; a repeat answers 200 with already_in_trash."
    )
    return handler


for _key, _path in PATHS.items():
    router.add_api_route(
        _path,
        _delete_route(svc.KINDS[_key]),
        methods=["DELETE"],
        response_model=TrashItemOut,
        summary=f"Move a {svc.KINDS[_key].noun} to the Trash",
    )


# ------------------------------------------------------------------ Trash bin


@router.get("/trash", response_model=list[TrashItemOut])
def list_trash(
    db: DbDep, kind: KindKey | None = Query(None), p: Principal = Reader
) -> list[dict[str, Any]]:
    q = select(svc.TrashItem).where(svc.TrashItem.company_id == p.company_id)
    if kind:
        q = q.where(svc.TrashItem.kind == kind)
    items = db.scalars(q.order_by(svc.TrashItem.trashed_at.desc()).limit(1000)).all()
    names = _names(db, {t.trashed_by for t in items})
    return [_item_out(db, t, names=names) for t in items]


@router.post("/trash/{trash_id}/restore", response_model=RestoredOut)
def restore_item(
    trash_id: uuid.UUID,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Admin,
) -> JSONResponse:
    def run() -> tuple[int, dict[str, Any]]:
        t = _trash_item(db, p, trash_id)
        out = RestoredOut(kind=t.kind, item_id=t.item_id, label=t.label)
        svc.restore(db, t, p.employee.id, request.state.request_id)
        return 200, out.model_dump(mode="json")

    return _command(db, p, key, "trash.restore", {"trash_id": str(trash_id)}, run)


@router.delete("/trash/{trash_id}", response_model=PurgedOut)
def delete_forever(
    trash_id: uuid.UUID,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Admin,
) -> JSONResponse:
    def run() -> tuple[int, dict[str, Any]]:
        svc.purge(db, _trash_item(db, p, trash_id), p.employee.id, request.state.request_id)
        return 200, {"purged": 1}

    return _command(db, p, key, "trash.purge", {"trash_id": str(trash_id)}, run)


@router.post("/trash/empty", response_model=PurgedOut)
def empty_trash(
    request: Request, db: DbDep, key: idempotency.IdemKey, p: Principal = Admin
) -> JSONResponse:
    def run() -> tuple[int, dict[str, Any]]:
        items = db.scalars(
            select(svc.TrashItem).where(svc.TrashItem.company_id == p.company_id).with_for_update()
        ).all()
        for t in items:
            svc.purge(db, t, p.employee.id, request.state.request_id)
        return 200, {"purged": len(items)}

    return _command(db, p, key, "trash.empty", {}, run)


# ------------------------------------------------------------------ delete requests


@router.get("/delete-requests", response_model=list[DeleteRequestOut])
def list_requests(
    db: DbDep,
    status: Literal["PENDING", "APPROVED", "DISMISSED"] = Query("PENDING"),
    p: Principal = Reader,
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(svc.DeleteRequest)
        .where(svc.DeleteRequest.company_id == p.company_id, svc.DeleteRequest.status == status)
        .order_by(svc.DeleteRequest.created_at.desc())
        .limit(500)
    ).all()
    names = _names(db, {r.requested_by for r in rows} | {r.decided_by for r in rows})
    return [_request_out(db, r, names) for r in rows]


@router.post("/delete-requests", status_code=201, response_model=DeleteRequestOut)
def request_delete(
    body: DeleteRequestIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Ask the Super Admin to delete a record. Needs the permission that edits that record;
    refused up front (409) when the record could not be deleted anyway."""
    kind = svc.KINDS[body.kind]
    perm = kind.permission or svc.TRASH_PERMISSION
    if not p.can(perm, any_scope=True):
        raise deny(request, p, perm, None)

    def run() -> tuple[int, dict[str, Any]]:
        obj = svc.load(db, kind, p.company_id, body.item_id)
        if obj is None:
            raise ApiError(404, "NOT_FOUND", f"That {kind.noun} was not found")
        authorize(request, p, perm, getattr(obj, "warehouse_id", None))
        svc.check(db, kind, p.employee.id, obj)
        label = kind.label(db, obj)
        new_id = db.scalar(
            insert(svc.DeleteRequest)
            .values(
                company_id=p.company_id,
                kind=kind.key,
                item_id=body.item_id,
                label=label,
                reason=body.reason.strip(),
                requested_by=p.employee.id,
            )
            .on_conflict_do_nothing(
                index_elements=["company_id", "kind", "item_id"],
                index_where=svc.DeleteRequest.status == "PENDING",
            )
            .returning(svc.DeleteRequest.id)
        )
        if new_id is None:  # someone already asked; answer with the open request
            open_req = db.scalar(
                select(svc.DeleteRequest).where(
                    svc.DeleteRequest.company_id == p.company_id,
                    svc.DeleteRequest.kind == kind.key,
                    svc.DeleteRequest.item_id == body.item_id,
                    svc.DeleteRequest.status == "PENDING",
                )
            )
            assert open_req is not None
            return 200, _request_out(db, open_req)
        audit.record(
            db,
            action="trash.delete_requested",
            success=True,
            company_id=p.company_id,
            actor_id=p.employee.id,
            target_type=kind.key,
            target_id=str(body.item_id),
            warehouse_id=getattr(obj, "warehouse_id", None),
            request_id=request.state.request_id,
            detail={
                "label": label,
                "reason": body.reason.strip(),
                "delete_request_id": str(new_id),
            },
        )
        req = db.get(svc.DeleteRequest, new_id)
        assert req is not None
        return 201, _request_out(db, req)

    return _command(db, p, key, "trash.request", body.model_dump(mode="json"), run)


def _open_request(db: Session, p: Principal, request_id: uuid.UUID) -> svc.DeleteRequest:
    r = db.get(svc.DeleteRequest, request_id, with_for_update=True, populate_existing=True)
    if r is None or r.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Delete request not found")
    if r.status != "PENDING":
        raise ApiError(409, "ALREADY_DECIDED", f"This request was already {r.status.lower()}")
    return r


@router.post("/delete-requests/{request_id}/approve", response_model=TrashItemOut)
def approve_request(
    request_id: uuid.UUID,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Admin,
) -> JSONResponse:
    def run() -> tuple[int, dict[str, Any]]:
        r = _open_request(db, p, request_id)
        item, already = svc.move_to_trash(
            db, svc.KINDS[r.kind], p.company_id, p.employee.id, r.item_id, request.state.request_id
        )
        if r.status == "PENDING":  # it was already in the Trash
            r.status, r.decided_by, r.decided_at = "APPROVED", p.employee.id, datetime.now(UTC)
        db.flush()
        return 200, _item_out(db, item, already)

    return _command(db, p, key, "trash.request.approve", {"request_id": str(request_id)}, run)


@router.post("/delete-requests/{request_id}/dismiss", response_model=DeleteRequestOut)
def dismiss_request(
    request_id: uuid.UUID,
    body: DismissIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Admin,
) -> JSONResponse:
    def run() -> tuple[int, dict[str, Any]]:
        r = _open_request(db, p, request_id)
        r.status, r.decided_by, r.decided_at = "DISMISSED", p.employee.id, datetime.now(UTC)
        r.decision_note = body.note.strip()
        audit.record(
            db,
            action="trash.request_dismissed",
            success=True,
            company_id=p.company_id,
            actor_id=p.employee.id,
            target_type=r.kind,
            target_id=str(r.item_id),
            request_id=request.state.request_id,
            detail={"label": r.label, "delete_request_id": str(r.id), "note": r.decision_note},
        )
        db.flush()
        return 200, _request_out(db, r)

    payload = {"request_id": str(request_id), **body.model_dump(mode="json")}
    return _command(db, p, key, "trash.request.dismiss", payload, run)
