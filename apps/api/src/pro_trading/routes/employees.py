"""Settings › Users (staff accounts) and Settings › My account.

Staff management needs company-wide `employee.manage` (OWNER_ADMIN, privileged ⇒ MFA). Each
person gets one company-wide role: the business runs from one warehouse. Safety rules:
- passwords are never logged, audited or kept in idempotency records;
- nobody disables, re-roles or resets themselves here (My account covers name and password);
- the company always keeps at least one active Owner / Admin;
- disabling someone, or changing their role, password or two-step sign-in, signs them out
  everywhere.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .. import audit, idempotency
from ..config import get_settings
from ..deps import DbDep, Principal, PrincipalDep, require_permission
from ..errors import ApiError
from ..models import AuthSession, Company, Employee, EmployeeRole, Role
from ..schemas import (
    MyAccountIn,
    PasswordChangeIn,
    PasswordSetIn,
    StaffCreateIn,
    StaffOut,
    StaffPatchIn,
)
from ..security import hash_password, verify_password
from ._common import mutate

router = APIRouter(tags=["users"])
Manage = Depends(require_permission("employee.manage"))
OWNER = "OWNER_ADMIN"


class _Redacted(BaseModel):
    """Request body minus the password, for idempotency hashing and audit."""

    model_config = ConfigDict(extra="allow")


def _roles(db: Session, emp_id: uuid.UUID) -> list[str]:
    return sorted(
        set(
            db.scalars(
                select(Role.code)
                .join(EmployeeRole, EmployeeRole.role_id == Role.id)
                .where(EmployeeRole.employee_id == emp_id)
            )
        )
    )


def _out(db: Session, e: Employee) -> dict[str, Any]:
    seen = db.scalar(
        select(func.max(AuthSession.last_seen_at)).where(AuthSession.employee_id == e.id)
    )
    return StaffOut(
        id=e.id,
        email=e.email,
        display_name=e.display_name,
        active=e.active,
        roles=_roles(db, e.id),
        mfa_enrolled=e.mfa_enrolled_at is not None,
        locked=bool(e.locked_until and e.locked_until > datetime.now(UTC)),
        last_seen_at=seen,
        created_at=e.created_at,
    ).model_dump(mode="json")


def _target(db: Session, p: Principal, emp_id: uuid.UUID, lock: bool = False) -> Employee:
    e = db.get(Employee, emp_id, with_for_update=lock)
    if e is None or e.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "User not found")
    return e


def _not_self(p: Principal, e: Employee, what: str) -> None:
    if e.id == p.employee.id:
        raise ApiError(422, "SELF_CHANGE", f"You can't {what} your own account here")


def _role(db: Session, p: Principal, code: str) -> Role:
    r = db.scalar(select(Role).where(Role.company_id == p.company_id, Role.code == code))
    if r is None:
        raise ApiError(422, "INVALID_ROLE", "Unknown role")
    return r


def _other_owners(db: Session, p: Principal, exclude: uuid.UUID) -> int:
    """Active Owner / Admin accounts other than `exclude`. Callers hold the company row lock
    (taken before any employee row, see patch_staff), so two Owners demoting or disabling each
    other at once can't both pass the check."""
    return (
        db.scalar(
            select(func.count(func.distinct(Employee.id)))
            .join(EmployeeRole, EmployeeRole.employee_id == Employee.id)
            .join(Role, Role.id == EmployeeRole.role_id)
            .where(
                Employee.company_id == p.company_id,
                Employee.active.is_(True),
                Employee.id != exclude,
                Role.code == OWNER,
            )
        )
        or 0
    )


def _weak(email: str, password: str) -> None:
    if password.strip().lower() == email.strip().lower() or len(set(password)) < 4:
        raise ApiError(422, "WEAK_PASSWORD", "Choose a less guessable password")


def _sign_out(db: Session, emp_id: uuid.UUID, reason: str, keep: uuid.UUID | None = None) -> None:
    q = update(AuthSession).where(
        AuthSession.employee_id == emp_id, AuthSession.revoked_at.is_(None)
    )
    if keep is not None:
        q = q.where(AuthSession.id != keep)
    db.execute(q.values(revoked_at=func.now(), revoked_reason=reason))


# ---------------------------------------------------------------- staff


@router.get("/employees", response_model=list[StaffOut])
def list_staff(db: DbDep, p: Principal = Manage) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Employee)
        .where(Employee.company_id == p.company_id)
        .order_by(Employee.active.desc(), func.lower(Employee.display_name))
    ).all()
    return [_out(db, e) for e in rows]


@router.post("/employees", status_code=201, response_model=StaffOut)
def create_staff(
    body: StaffCreateIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    safe = _Redacted(**body.model_dump(exclude={"password"}))
    _weak(body.email, body.password)

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        email = body.email.strip()
        taken = db.scalar(select(Employee.id).where(func.lower(Employee.email) == email.lower()))
        if taken is not None:
            raise ApiError(409, "EMAIL_TAKEN", "Someone already signs in with this email")
        e = Employee(
            company_id=p.company_id,
            email=email,
            display_name=body.display_name.strip(),
            password_hash=hash_password(body.password),
        )
        db.add(e)
        db.flush()
        db.add(EmployeeRole(employee_id=e.id, role_id=_role(db, p, body.role_code).id))
        db.flush()
        db.refresh(e)
        return _out(db, e), str(e.id), safe.model_dump(mode="json")

    return mutate(db, request, p, key, "employee.create", safe, 201, "employee.create", fn)


@router.patch("/employees/{employee_id}", response_model=StaffOut)
def patch_staff(
    employee_id: uuid.UUID,
    body: StaffPatchIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        if body.role_code is not None or body.active is not None:
            # Serialize Owner changes per company. Lock order company -> employee, so two
            # Owners editing each other queue instead of deadlocking.
            db.get(Company, p.company_id, with_for_update=True)
        e = _target(db, p, employee_id, lock=True)
        before = _out(db, e)
        if body.display_name is not None:
            e.display_name = body.display_name.strip()
        revoke = False
        if body.role_code is not None and _roles(db, e.id) != [body.role_code]:
            _not_self(p, e, "change the role of")
            if (
                OWNER in _roles(db, e.id)
                and body.role_code != OWNER
                and not _other_owners(db, p, e.id)
            ):
                raise ApiError(422, "LAST_OWNER", "Keep at least one active Owner / Admin")
            db.execute(delete(EmployeeRole).where(EmployeeRole.employee_id == e.id))
            db.add(EmployeeRole(employee_id=e.id, role_id=_role(db, p, body.role_code).id))
            revoke = True
        if body.active is not None and body.active != e.active:
            _not_self(p, e, "disable")
            if not body.active and OWNER in _roles(db, e.id) and not _other_owners(db, p, e.id):
                raise ApiError(422, "LAST_OWNER", "Keep at least one active Owner / Admin")
            e.active = body.active
            revoke = revoke or not body.active
        if revoke:
            _sign_out(db, e.id, "admin_changed")
        db.flush()
        after = _out(db, e)
        return after, str(e.id), {"before": before, "after": after}

    return mutate(
        db, request, p, key, f"employee.patch:{employee_id}", body, 200, "employee.update", fn
    )


@router.post("/employees/{employee_id}/reset-password", response_model=StaffOut)
def reset_password(
    employee_id: uuid.UUID, body: PasswordSetIn, request: Request, db: DbDep, p: Principal = Manage
) -> dict[str, Any]:
    """Set a temporary password (share it in person). Clears a lockout; signs them out."""
    e = _target(db, p, employee_id, lock=True)
    _not_self(p, e, "reset the password of")
    _weak(e.email, body.password)
    e.password_hash = hash_password(body.password)
    e.failed_login_count, e.locked_until = 0, None
    _sign_out(db, e.id, "password_reset")
    audit.record(
        db, action="employee.password_reset", success=True, company_id=p.company_id,
        actor_id=p.employee.id, target_type="employee", target_id=str(e.id),
        request_id=request.state.request_id,
    )  # fmt: skip
    db.commit()
    return _out(db, e)


@router.post("/employees/{employee_id}/reset-mfa", response_model=StaffOut)
def reset_mfa(
    employee_id: uuid.UUID, request: Request, db: DbDep, p: Principal = Manage
) -> dict[str, Any]:
    """Lost phone: clear two-step sign-in; they set it up again at their next sign-in."""
    e = _target(db, p, employee_id, lock=True)
    _not_self(p, e, "reset two-step sign-in of")
    e.mfa_secret_enc, e.mfa_enrolled_at, e.mfa_last_step = None, None, None
    _sign_out(db, e.id, "mfa_reset")
    audit.record(
        db, action="employee.mfa_reset", success=True, company_id=p.company_id,
        actor_id=p.employee.id, target_type="employee", target_id=str(e.id),
        request_id=request.state.request_id,
    )  # fmt: skip
    db.commit()
    return _out(db, e)


# ---------------------------------------------------------------- my account


@router.patch("/auth/me", response_model=StaffOut)
def update_me(body: MyAccountIn, request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    e = _target(db, p, p.employee.id, lock=True)
    before = e.display_name
    e.display_name = body.display_name.strip()
    audit.record(
        db, action="employee.update_self", success=True, company_id=p.company_id,
        actor_id=e.id, target_type="employee", target_id=str(e.id),
        request_id=request.state.request_id,
        detail={"before": {"display_name": before}, "after": {"display_name": e.display_name}},
    )  # fmt: skip
    db.commit()
    return _out(db, e)


@router.post("/auth/password", status_code=204)
def change_password(body: PasswordChangeIn, request: Request, db: DbDep, p: PrincipalDep) -> None:
    """Change your own password. Other devices are signed out; this one stays signed in.

    A privileged account must have verified MFA in this session (a stolen password alone must
    not be enough to lock the real owner out). Wrong current passwords count toward the same
    lockout as sign-in; reaching it signs the account out everywhere."""
    if p.mfa_required:
        raise ApiError(403, "MFA_REQUIRED", "Verify MFA before changing your password")
    s = get_settings()
    e = _target(db, p, p.employee.id, lock=True)
    if not verify_password(e.password_hash, body.current_password):
        e.failed_login_count += 1
        locked = e.failed_login_count >= s.login_max_failures
        if locked:
            e.locked_until = datetime.now(UTC) + timedelta(minutes=s.login_lockout_minutes)
            e.failed_login_count = 0
            _sign_out(db, e.id, "password_guessing")
        audit.record(
            db, action="employee.password_change", success=False, company_id=p.company_id,
            actor_id=e.id, target_type="employee", target_id=str(e.id),
            request_id=request.state.request_id,
            detail={"reason": "wrong_current", "locked": locked},
        )  # fmt: skip
        db.commit()
        raise ApiError(422, "WRONG_PASSWORD", "Your current password is not right")
    if body.new_password == body.current_password:
        raise ApiError(422, "SAME_PASSWORD", "Choose a new password")
    _weak(e.email, body.new_password)
    e.password_hash = hash_password(body.new_password)
    _sign_out(db, e.id, "password_changed", keep=p.session.id)
    audit.record(
        db, action="employee.password_change", success=True, company_id=p.company_id,
        actor_id=e.id, target_type="employee", target_id=str(e.id),
        request_id=request.state.request_id,
    )  # fmt: skip
    db.commit()
