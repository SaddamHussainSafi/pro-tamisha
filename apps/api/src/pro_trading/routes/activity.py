"""Activity feed (read-only): who did what, newest first. See pro_trading/activity.py."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from ..activity import Scope, after_cursor, describe, parse_cursor, visible
from ..deps import DbDep, Principal, PrincipalDep
from ..errors import ApiError
from ..models import AuditEvent

router = APIRouter(prefix="/activity", tags=["activity"])


class ActivityActor(BaseModel):
    id: uuid.UUID | None
    name: str


class ActivityItem(BaseModel):
    id: uuid.UUID
    at: datetime
    action: str
    actor: ActivityActor
    verb: str
    object: str | None
    extra: str | None
    link: str | None
    tag: str
    tone: Literal["success", "info", "price", "stock", "danger", "neutral"]
    icon: str


class ActivityPage(BaseModel):
    items: list[ActivityItem]
    total: int
    next_before: str | None
    can_see_others: bool


def scope_of(p: Principal) -> Scope:
    grants = [g for g in p.effective() if g.permission == "audit.read"]
    return Scope(
        employee_id=p.employee.id,
        company_id=p.company_id,
        all_company=any(g.warehouse_id is None for g in grants),
        warehouses=frozenset(g.warehouse_id for g in grants if g.warehouse_id is not None),
    )


@router.get("", response_model=ActivityPage)
def list_activity(
    db: DbDep,
    p: PrincipalDep,
    mine: bool = False,
    actor_id: uuid.UUID | None = None,
    before: Annotated[
        str | None, Query(max_length=80, description="next_before of the previous page")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> dict[str, Any]:
    """Everyone sees their own activity; `audit.read` also shows colleagues' (company-wide, or
    for the warehouses the grant covers). `mine=true` narrows to the caller's own."""
    sc = scope_of(p)
    if actor_id is not None and actor_id != p.employee.id and not sc.can_see_others:
        raise ApiError(403, "FORBIDDEN", "You can only see your own activity")
    cond = visible(sc, mine=mine, actor=actor_id)
    total = db.scalar(select(func.count()).select_from(AuditEvent).where(cond)) or 0
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(cond, after_cursor(parse_cursor(before)))
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(limit + 1)
        )
    )
    more = len(events) > limit
    events = events[:limit]
    last = events[-1] if events else None
    return {
        "items": describe(db, p.company_id, events),
        "total": total,
        "next_before": f"{last.created_at.isoformat()}|{last.id}" if more and last else None,
        "can_see_others": sc.can_see_others,
    }
