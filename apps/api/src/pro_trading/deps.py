"""Authentication, CSRF and server-side authorization dependencies."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .config import get_settings
from .db import get_db
from .errors import ApiError
from .models import AuthSession, Employee, EmployeeRole, Role, RolePermission
from .security import sha256_hex, tokens_match

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "X-CSRF-Token"

DbDep = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True)
class Grant:
    permission: str
    warehouse_id: uuid.UUID | None  # None = company-wide
    privileged: bool


@dataclass
class Principal:
    employee: Employee
    session: AuthSession
    grants: list[Grant] = field(default_factory=list)

    @property
    def company_id(self) -> uuid.UUID:
        return self.employee.company_id

    @property
    def mfa_required(self) -> bool:
        return any(g.privileged for g in self.grants) and not self.session.mfa_verified

    def effective(self) -> list[Grant]:
        """Privileged-role grants only count after MFA in this session."""
        return [g for g in self.grants if not g.privileged or self.session.mfa_verified]

    def can(
        self, permission: str, warehouse_id: uuid.UUID | None = None, *, any_scope: bool = False
    ) -> bool:
        """warehouse_id=None means a company-level action: only company-wide grants count,
        unless any_scope=True (e.g. list endpoints that filter rows per warehouse)."""
        return any(
            g.permission == permission
            and (any_scope or g.warehouse_id is None or g.warehouse_id == warehouse_id)
            for g in self.effective()
        )

    def permission_codes(self) -> list[str]:
        return sorted({g.permission for g in self.effective()})


def load_grants(db: Session, employee_id: uuid.UUID) -> list[Grant]:
    rows = db.execute(
        select(RolePermission.permission_code, EmployeeRole.warehouse_id, Role.privileged)
        .join(Role, Role.id == EmployeeRole.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .where(EmployeeRole.employee_id == employee_id)
    ).all()
    return [Grant(p, w, priv) for p, w, priv in rows]


def get_principal(request: Request, db: DbDep) -> Principal:
    s = get_settings()
    raw = request.cookies.get(s.session_cookie_name)
    if not raw:
        raise ApiError(401, "UNAUTHENTICATED", "Sign in required")

    sess = db.scalar(
        select(AuthSession).where(AuthSession.token_sha256 == sha256_hex(raw)).with_for_update()
    )
    now = datetime.now(UTC)
    if (
        sess is None
        or sess.revoked_at is not None
        or sess.expires_at <= now
        or sess.last_seen_at + timedelta(minutes=s.session_idle_minutes) <= now
    ):
        raise ApiError(401, "SESSION_INVALID", "Session expired or revoked")
    emp = db.get(Employee, sess.employee_id)
    if emp is None or not emp.active:
        raise ApiError(401, "SESSION_INVALID", "Session expired or revoked")
    if request.method not in SAFE_METHODS and not tokens_match(
        request.headers.get(CSRF_HEADER), sess.csrf_token_sha256
    ):
        raise ApiError(403, "CSRF_FAILED", "Missing or invalid CSRF token")
    sess.last_seen_at = now
    db.commit()
    request.state.actor_id = emp.id
    return Principal(emp, sess, load_grants(db, emp.id))


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def deny(
    request: Request,
    principal: Principal,
    permission: str,
    warehouse_id: uuid.UUID | None,
) -> ApiError:
    audit.record_independent(
        action="permission.denied",
        success=False,
        company_id=principal.company_id,
        actor_id=principal.employee.id,
        warehouse_id=warehouse_id,
        request_id=getattr(request.state, "request_id", None),
        target_type="route",
        target_id=f"{request.method} {request.url.path}",
        detail={"permission": permission, "mfa_pending": principal.mfa_required},
    )
    if principal.mfa_required and any(
        g.permission == permission for g in principal.grants if g.privileged
    ):
        return ApiError(403, "MFA_REQUIRED", "Verify MFA to use this permission")
    return ApiError(403, "FORBIDDEN", "You do not have permission for this action")


def authorize(
    request: Request,
    principal: Principal,
    permission: str,
    warehouse_id: uuid.UUID | None = None,
    *,
    any_scope: bool = False,
) -> None:
    """Imperative check for handlers that resolve warehouse scope from the target object."""
    if not principal.can(permission, warehouse_id, any_scope=any_scope):
        raise deny(request, principal, permission, warehouse_id)


def require_permission(
    permission: str, warehouse_param: str | None = None
) -> Callable[[Request, Principal], Principal]:
    """Route dependency. warehouse_param names a path/query param holding the warehouse id."""

    def dep(request: Request, principal: PrincipalDep) -> Principal:
        wh: uuid.UUID | None = None
        if warehouse_param:
            raw = request.path_params.get(warehouse_param) or request.query_params.get(
                warehouse_param
            )
            try:
                wh = uuid.UUID(str(raw))
            except ValueError:
                raise ApiError(422, "VALIDATION", f"{warehouse_param} must be a UUID") from None
        authorize(request, principal, permission, wh)
        return principal

    return dep
