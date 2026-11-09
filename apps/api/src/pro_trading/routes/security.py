"""Settings › Roles & permissions and Settings › Security & sessions (Super Admin only).

Everything here needs company-wide `employee.manage`. The role matrix is read-only: it is the
code-owned catalog in permissions.py, and access changes by assigning a role to a person."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from .. import accounts, audit
from ..config import get_settings
from ..deps import DbDep, Principal, require_permission
from ..errors import ApiError
from ..models import AuditEvent, AuthSession, Employee, EmployeeRole, Role, RolePermission
from ..permissions import DEFAULT_ROLES, PERMISSIONS
from ..schemas import (
    PermissionOut,
    RoleMatrixOut,
    RoleOut,
    SecurityEventOut,
    SecuritySettingsOut,
    SessionInfoOut,
)
from ..security import PASSWORD_MIN_LENGTH
from .account import AUTH_ACTIONS, event_out, session_info

router = APIRouter(tags=["security"])
Manage = Depends(require_permission("employee.manage"))

ACCOUNT_ACTIONS = (
    "employee.create",
    "employee.invite",
    "employee.update",
    "employee.password_reset",
    "employee.mfa_reset",
    "feature_flag.set",
)
KINDS: dict[str, tuple[str, ...]] = {
    "all": (*AUTH_ACTIONS, *ACCOUNT_ACTIONS, "permission.denied", "auth.link_throttled"),
    "sign_in": ("auth.login", "auth.dev_login", "auth.logout", "auth.mfa_verify"),
    "account": (*ACCOUNT_ACTIONS, *(a for a in AUTH_ACTIONS if "login" not in a)),
    "denied": ("permission.denied",),
}


@router.get("/roles", response_model=RoleMatrixOut)
def role_matrix(db: DbDep, p: Principal = Manage) -> RoleMatrixOut:
    roles = db.scalars(select(Role).where(Role.company_id == p.company_id)).all()
    counts = dict(
        db.execute(
            select(EmployeeRole.role_id, func.count(func.distinct(Employee.id)))
            .join(Employee, Employee.id == EmployeeRole.employee_id)
            .where(Employee.company_id == p.company_id, Employee.active.is_(True))
            .group_by(EmployeeRole.role_id)
        ).all()
    )
    order = list(DEFAULT_ROLES)
    out = []
    for r in sorted(roles, key=lambda r: order.index(r.code) if r.code in order else 99):
        perms = sorted(
            db.scalars(select(RolePermission.permission_code).where(RolePermission.role_id == r.id))
        )
        out.append(
            RoleOut(
                code=r.code,
                name=r.name,
                description=DEFAULT_ROLES[r.code][1] if r.code in DEFAULT_ROLES else "",
                permissions=perms,
                members=counts.get(r.id, 0),
            )
        )
    return RoleMatrixOut(
        roles=out,
        permissions=[PermissionOut(code=c, description=d) for c, d in PERMISSIONS.items()],
    )


@router.get("/security/settings", response_model=SecuritySettingsOut)
def security_settings(p: Principal = Manage) -> SecuritySettingsOut:
    s = get_settings()
    return SecuritySettingsOut(
        two_step_required=p.two_step_policy,
        session_ttl_minutes=s.session_ttl_minutes,
        session_idle_minutes=s.session_idle_minutes,
        login_max_failures=s.login_max_failures,
        login_lockout_minutes=s.login_lockout_minutes,
        login_ip_max_failures=s.login_ip_max_failures,
        login_ip_window_minutes=s.login_ip_window_minutes,
        invite_ttl_hours=s.invite_ttl_hours,
        password_reset_ttl_minutes=s.password_reset_ttl_minutes,
        password_min_length=PASSWORD_MIN_LENGTH,
    )


@router.get("/security/sessions", response_model=list[SessionInfoOut])
def company_sessions(db: DbDep, p: Principal = Manage) -> list[SessionInfoOut]:
    rows = db.execute(
        select(AuthSession, Employee)
        .join(Employee, Employee.id == AuthSession.employee_id)
        .where(Employee.company_id == p.company_id, *accounts.live_sessions_filter())
        .order_by(AuthSession.last_seen_at.desc())
        .limit(500)
    ).all()
    out = []
    for sess, emp in rows:
        info = session_info(sess, p.session.id)
        info.employee_id, info.employee_name, info.employee_email = (
            emp.id,
            emp.display_name,
            emp.email,
        )
        out.append(info)
    return out


@router.post("/security/sessions/{session_id}/revoke", status_code=204)
def revoke_company_session(
    session_id: uuid.UUID, request: Request, db: DbDep, p: Principal = Manage
) -> None:
    if session_id == p.session.id:
        raise ApiError(422, "CURRENT_SESSION", "Use Sign out to end your own session")
    sess = db.get(AuthSession, session_id, with_for_update=True, populate_existing=True)
    emp = db.get(Employee, sess.employee_id) if sess else None
    if sess is None or emp is None or emp.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Session not found")
    if sess.revoked_at is None:
        sess.revoked_at, sess.revoked_reason = datetime.now(UTC), "admin_revoked"
        audit.record(
            db,
            action="auth.session_revoked",
            success=True,
            company_id=p.company_id,
            actor_id=p.employee.id,
            target_type="employee",
            target_id=str(emp.id),
            request_id=getattr(request.state, "request_id", None),
            detail={"session_id": str(session_id)},
        )
    db.commit()


@router.get("/security/events", response_model=list[SecurityEventOut])
def security_events(
    db: DbDep,
    p: Principal = Manage,
    kind: Literal["all", "sign_in", "account", "denied"] = "all",
    failed_only: bool = False,
    employee_id: uuid.UUID | None = None,
    before: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> list[SecurityEventOut]:
    """Sign-ins (and failures), lockouts, two-step changes, password resets, invitations,
    role and status changes, session revocations and permission denials, newest first.
    Page with `before` = the last row's created_at."""
    q = select(AuditEvent).where(
        AuditEvent.company_id == p.company_id, AuditEvent.action.in_(KINDS[kind])
    )
    if failed_only:
        q = q.where(AuditEvent.success.is_(False))
    if employee_id is not None:
        q = q.where(
            (AuditEvent.actor_id == employee_id) | (AuditEvent.target_id == str(employee_id))
        )
    if before is not None:
        q = q.where(AuditEvent.created_at < before)
    rows = db.scalars(q.order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    names = dict(
        db.execute(
            select(Employee.id, Employee.display_name).where(Employee.company_id == p.company_id)
        ).all()
    )
    return [event_out(r, names) for r in rows]
