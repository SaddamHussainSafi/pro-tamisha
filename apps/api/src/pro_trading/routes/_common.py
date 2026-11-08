"""Shared command plumbing for module routers: idempotency + audit + commit in one transaction."""

from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, idempotency
from ..deps import Principal
from ..errors import ApiError


def esc(s: str) -> str:
    """Escape LIKE wildcards so user search text is matched literally."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def mutate(
    db: Session,
    request: Request,
    p: Principal,
    key: str,
    scope: str,
    body: BaseModel,
    status: int,
    action: str,
    fn: Callable[[], tuple[dict[str, Any], str, dict[str, Any]]],
    duplicate_msg: str = "Already exists",
) -> JSONResponse:
    """Idempotency claim -> fn() -> audit -> store response, all in one transaction.
    fn returns (response body, target id, audit detail)."""
    sc = f"{p.company_id}:{scope}"
    payload = body.model_dump(mode="json")
    if (replay := idempotency.begin(db, sc, key, payload)) is not None:
        db.commit()
        return JSONResponse(replay[1], replay[0], headers={"Idempotent-Replay": "true"})
    try:
        out, target_id, detail = fn()
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "DUPLICATE", duplicate_msg) from None
    audit.record(
        db,
        action=action,
        success=True,
        company_id=p.company_id,
        actor_id=p.employee.id,
        target_type=action.split(".")[0],
        target_id=target_id,
        request_id=request.state.request_id,
        detail=detail,
    )
    idempotency.finish(db, sc, key, status, out)
    db.commit()
    return JSONResponse(out, status)
