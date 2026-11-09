"""Feature flags and audit log read."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select

from .. import audit
from ..deps import DbDep, Principal, require_permission
from ..models import AuditEvent, FeatureFlag
from ..schemas import AuditEventOut, FeatureFlagIn, FeatureFlagOut

router = APIRouter(tags=["admin"])


@router.get("/feature-flags", response_model=list[FeatureFlagOut])
def list_flags(
    db: DbDep, p: Principal = Depends(require_permission("feature_flag.manage"))
) -> list[FeatureFlag]:
    return list(
        db.scalars(
            select(FeatureFlag)
            .where(FeatureFlag.company_id == p.company_id)
            .order_by(FeatureFlag.key)
        )
    )


@router.put("/feature-flags/{key}", response_model=FeatureFlagOut)
def set_flag(
    key: Annotated[str, Path(pattern=r"^[a-z0-9_]{1,64}$")],
    body: FeatureFlagIn,
    request: Request,
    db: DbDep,
    p: Principal = Depends(require_permission("feature_flag.manage")),
) -> FeatureFlag:
    flag = db.scalar(
        select(FeatureFlag)
        .where(FeatureFlag.company_id == p.company_id, FeatureFlag.key == key)
        .with_for_update()
    )
    before = None if flag is None else flag.enabled
    if flag is None:
        flag = FeatureFlag(company_id=p.company_id, key=key, version=0)
        db.add(flag)
    flag.enabled, flag.description = body.enabled, body.description
    flag.version, flag.updated_by, flag.updated_at = (
        flag.version + 1,
        p.employee.id,
        datetime.now(UTC),
    )
    audit.record(
        db,
        action="feature_flag.set",
        success=True,
        company_id=p.company_id,
        actor_id=p.employee.id,
        target_type="feature_flag",
        target_id=key,
        request_id=request.state.request_id,
        detail={"before": before, "after": body.enabled},
    )
    db.commit()
    return flag


@router.get("/audit-events", response_model=list[AuditEventOut])
def list_audit(
    db: DbDep,
    p: Principal = Depends(require_permission("audit.read")),
    limit: int = Query(100, ge=1, le=500),
    action: str | None = None,
) -> list[AuditEvent]:
    q = select(AuditEvent).where(AuditEvent.company_id == p.company_id)
    if action:
        q = q.where(AuditEvent.action == action)
    return list(db.scalars(q.order_by(AuditEvent.created_at.desc()).limit(limit)))
