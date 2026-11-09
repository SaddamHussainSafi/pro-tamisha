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
from .db import get_db, get_sessionmaker
from .errors import ApiError
from .models import (
    AuthSession,
    Employee,
    EmployeeRole,
    FeatureFlag,
    Role,
    RolePermission,
    Warehouse,
)
from .security import sha256_hex, tokens_match

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "X-CSRF-Token"

DbDep = Annotated[Session, Depends(get_db)]


TWO_STEP_FLAG = "require_two_step"


@dataclass(frozen=True)
class Grant:
    permission: str
    warehouse_id: uuid.UUID | None  # None = company-wide


@dataclass
class Principal:
    """The signed-in employee. Two-step sign-in is optional: it is asked for only when the
    employee turned it on (or the company's `require_two_step` flag is on, which is off today).
    Until that code is entered the session holds no permissions at all."""

    employee: Employee
    session: AuthSession
    grants: list[Grant] = field(default_factory=list)
    two_step_policy: bool = False  # company requires two-step sign-in for everyone

    @property
    def company_id(self) -> uuid.UUID:
        return self.employee.company_id

    @property
    def mfa_enrolled(self) -> bool:
        return self.employee.mfa_enrolled_at is not None

    @property
    def mfa_setup_required(self) -> bool:
        """Policy requires two-step sign-in but this person has not set it up yet."""
        return self.two_step_policy and not self.mfa_enrolled

    @property
    def mfa_required(self) -> bool:
        """This session still has to pass (or set up) two-step sign-in."""
        return (self.mfa_enrolled or self.two_step_policy) and not self.session.mfa_verified

    def effective(self) -> list[Grant]:
        return [] if self.mfa_required else self.grants

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
        select(RolePermission.permission_code, EmployeeRole.warehouse_id)
        .join(Role, Role.id == EmployeeRole.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .where(EmployeeRole.employee_id == employee_id)
    ).all()
    return [Grant(p, w) for p, w in rows]


def two_step_policy(db: Session, company_id: uuid.UUID) -> bool:
    return bool(
        db.scalar(
            select(FeatureFlag.enabled).where(
                FeatureFlag.company_id == company_id, FeatureFlag.key == TWO_STEP_FLAG
            )
        )
    )


def build_principal(db: Session, emp: Employee, sess: AuthSession) -> Principal:
    return Principal(emp, sess, load_grants(db, emp.id), two_step_policy(db, emp.company_id))


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
    return build_principal(db, emp, sess)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def get_verified_principal(p: PrincipalDep) -> Principal:
    """Self-service routes (My account): refused while the sign-in's two-step code is pending."""
    if p.mfa_required:
        raise ApiError(403, "MFA_REQUIRED", "Enter your two-step sign-in code first")
    return p


VerifiedPrincipalDep = Annotated[Principal, Depends(get_verified_principal)]


def _own_warehouse(company_id: uuid.UUID, warehouse_id: uuid.UUID) -> bool:
    with get_sessionmaker()() as db:
        return (
            db.scalar(
                select(Warehouse.id).where(
                    Warehouse.id == warehouse_id, Warehouse.company_id == company_id
                )
            )
            is not None
        )


def deny(
    request: Request,
    principal: Principal,
    permission: str,
    warehouse_id: uuid.UUID | None,
) -> ApiError:
    detail: dict[str, object] = {"permission": permission, "mfa_pending": principal.mfa_required}
    if warehouse_id is not None and not _own_warehouse(principal.company_id, warehouse_id):
        # A made-up or foreign id must not reach the audit row's warehouse foreign key
        # (that turned the 403 into a 500 and lost the denial record).
        detail["requested_warehouse_id"] = str(warehouse_id)
        warehouse_id = None
    audit.record_independent(
        action="permission.denied",
        success=False,
        company_id=principal.company_id,
        actor_id=principal.employee.id,
        warehouse_id=warehouse_id,
        request_id=getattr(request.state, "request_id", None),
        target_type="route",
        target_id=f"{request.method} {request.url.path}",
        detail=detail,
    )
    if principal.mfa_required:
        return ApiError(403, "MFA_REQUIRED", "Enter your two-step sign-in code first")
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
