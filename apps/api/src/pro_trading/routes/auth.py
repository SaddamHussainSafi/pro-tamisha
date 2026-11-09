"""Login/logout/session, MFA enrollment & verification, session revocation."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import audit, ratelimit
from ..config import LOCAL_HOSTS, Settings, get_settings
from ..deps import DbDep, Principal, PrincipalDep, load_grants, require_permission
from ..errors import ApiError
from ..models import AuditEvent, AuthSession, Employee
from ..schemas import (
    EmployeeOut,
    GrantOut,
    LoginIn,
    LoginOut,
    MfaEnrollOut,
    MfaVerifyIn,
    SessionOut,
)
from ..security import (
    new_token,
    new_totp_secret,
    sha256_hex,
    tokens_match,
    totp_uri,
    verify_password,
    verify_totp,
)

router = APIRouter(prefix="/auth", tags=["auth"])
BAD_LOGIN = ApiError(401, "INVALID_CREDENTIALS", "Email or password is incorrect")


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _login_failed(request: Request, s: Settings) -> None:
    ratelimit.record_failure("login_ip", _client_ip(request), s.login_ip_window_minutes * 60)


def _session_out(p: Principal) -> SessionOut:
    return SessionOut(
        employee=EmployeeOut.model_validate(p.employee),
        mfa_required=p.mfa_required,
        mfa_enrolled=p.employee.mfa_enrolled_at is not None,
        permissions=p.permission_codes(),
        grants=[
            GrantOut(permission=g.permission, warehouse_id=g.warehouse_id) for g in p.effective()
        ],
        expires_at=p.session.expires_at,
    )


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, request: Request, response: Response, db: DbDep) -> LoginOut:
    s = get_settings()
    now = datetime.now(UTC)
    if ratelimit.failures("login_ip", _client_ip(request)) >= s.login_ip_max_failures:
        audit.record_independent(
            action="auth.login",
            success=False,
            request_id=_rid(request),
            detail={"reason": "ip_throttled"},
        )
        raise ApiError(
            429, "TOO_MANY_ATTEMPTS", "Too many failed sign-ins. Wait a few minutes and try again."
        )
    emp = db.scalar(
        select(Employee).where(func.lower(Employee.email) == body.email.lower()).with_for_update()
    )
    if emp is None:
        verify_password(None, body.password)  # equalize timing
        _login_failed(request, s)
        audit.record_independent(
            action="auth.login",
            success=False,
            request_id=_rid(request),
            detail={"reason": "unknown_email"},
        )
        raise BAD_LOGIN
    base: dict[str, Any] = {
        "company_id": emp.company_id,
        "actor_id": emp.id,
        "request_id": _rid(request),
    }
    if emp.locked_until and emp.locked_until > now:
        db.rollback()
        _login_failed(request, s)
        audit.record_independent(
            action="auth.login", success=False, detail={"reason": "locked"}, **base
        )
        raise ApiError(423, "ACCOUNT_LOCKED", "Too many failed attempts. Try again later.")
    if not emp.active or not verify_password(emp.password_hash, body.password):
        emp.failed_login_count += 1
        reason = "inactive" if not emp.active else "bad_password"
        if emp.failed_login_count >= s.login_max_failures:
            emp.locked_until = now + timedelta(minutes=s.login_lockout_minutes)
            emp.failed_login_count = 0
            reason = "locked_now"
        audit.record(db, action="auth.login", success=False, detail={"reason": reason}, **base)
        db.commit()
        _login_failed(request, s)
        raise BAD_LOGIN

    emp.failed_login_count, emp.locked_until = 0, None
    return _start_session(db, emp, request, response, action="auth.login", base=base)


@router.post("/dev-login", response_model=LoginOut, include_in_schema=False)
def dev_login(request: Request, response: Response, db: DbDep) -> LoginOut:
    """Development only: sign in as DEV_AUTO_LOGIN_EMAIL without password or MFA.

    Answers only when all hold: the setting is non-empty, APP_ENV is development/test, the
    configured FRONTEND_ORIGIN is localhost, and this request came from a loopback address with a
    localhost Host header (so a LAN client or a DNS-rebinding page cannot use it)."""
    s = get_settings()
    client = request.client.host if request.client else ""
    if (
        not s.dev_auto_login_email
        or s.is_production_like
        or not s.frontend_is_local
        or client not in LOCAL_HOSTS
        or request.url.hostname not in LOCAL_HOSTS
    ):
        raise ApiError(404, "NOT_FOUND", "Not found")
    emp = db.scalar(
        select(Employee).where(func.lower(Employee.email) == s.dev_auto_login_email.lower())
    )
    if emp is None or not emp.active:
        raise ApiError(404, "NOT_FOUND", "Dev auto-login employee not found or inactive")
    base: dict[str, Any] = {
        "company_id": emp.company_id,
        "actor_id": emp.id,
        "request_id": _rid(request),
    }
    return _start_session(
        db, emp, request, response, action="auth.dev_login", base=base, mfa_verified=True
    )


def _start_session(
    db: Session,
    emp: Employee,
    request: Request,
    response: Response,
    *,
    action: str,
    base: dict[str, Any],
    mfa_verified: bool = False,
) -> LoginOut:
    s = get_settings()
    now = datetime.now(UTC)
    raw, token_sha = new_token()
    csrf_raw, csrf_sha = new_token()
    sess = AuthSession(
        employee_id=emp.id,
        token_sha256=token_sha,
        csrf_token_sha256=csrf_sha,
        expires_at=now + timedelta(minutes=s.session_ttl_minutes),
        last_seen_at=now,
        user_agent=(request.headers.get("user-agent") or "")[:300],
        mfa_verified=mfa_verified,
    )
    db.add(sess)
    audit.record(db, action=action, success=True, target_type="session", **base)
    db.commit()
    response.set_cookie(
        s.session_cookie_name,
        raw,
        max_age=s.session_ttl_minutes * 60,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
        path="/",
    )
    p = Principal(emp, sess, load_grants(db, emp.id))
    return LoginOut(**_session_out(p).model_dump(), csrf_token=csrf_raw)


@router.get("/session", response_model=SessionOut)
def get_session(p: PrincipalDep) -> SessionOut:
    return _session_out(p)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: DbDep) -> None:
    """Revokes the session and always clears the cookie, even when the session already expired.

    A tab that lost its CSRF token (it lives in per-tab sessionStorage) can still sign out: the
    browser's same-origin Origin header is accepted instead. Cross-site requests are stopped by
    the Origin check here and in main.py, and by the SameSite=Strict cookie."""
    s = get_settings()
    raw = request.cookies.get(s.session_cookie_name)
    sess = (
        db.scalar(select(AuthSession).where(AuthSession.token_sha256 == sha256_hex(raw)))
        if raw
        else None
    )
    if sess is not None and sess.revoked_at is None:
        same_origin = request.headers.get("origin") == s.frontend_origin
        if not (
            same_origin or tokens_match(request.headers.get("X-CSRF-Token"), sess.csrf_token_sha256)
        ):
            raise ApiError(403, "CSRF_FAILED", "Missing or invalid CSRF token")
        emp = db.get(Employee, sess.employee_id)
        sess.revoked_at, sess.revoked_reason = datetime.now(UTC), "logout"
        audit.record(
            db,
            action="auth.logout",
            success=True,
            company_id=emp.company_id if emp else None,
            actor_id=sess.employee_id,
            request_id=_rid(request),
        )
        db.commit()
    response.delete_cookie(
        s.session_cookie_name, path="/", secure=s.cookie_secure, httponly=True, samesite="strict"
    )


@router.post("/mfa/enroll", response_model=MfaEnrollOut)
def mfa_enroll(request: Request, p: PrincipalDep, db: DbDep) -> MfaEnrollOut:
    emp = db.merge(p.employee)
    if emp.mfa_enrolled_at is not None:
        raise ApiError(409, "MFA_ALREADY_ENROLLED", "MFA already enrolled; ask an admin to reset")
    secret, enc = new_totp_secret()
    emp.mfa_secret_enc, emp.mfa_last_step = enc, None
    audit.record(
        db,
        action="auth.mfa_enroll_started",
        success=True,
        company_id=p.company_id,
        actor_id=emp.id,
        request_id=_rid(request),
    )
    db.commit()
    return MfaEnrollOut(otpauth_uri=totp_uri(secret, emp.email))


@router.post("/mfa/verify", response_model=SessionOut)
def mfa_verify(body: MfaVerifyIn, request: Request, p: PrincipalDep, db: DbDep) -> SessionOut:
    emp = db.get(Employee, p.employee.id, with_for_update=True)
    assert emp is not None
    base: dict[str, Any] = {
        "company_id": p.company_id,
        "actor_id": emp.id,
        "request_id": _rid(request),
    }
    if _mfa_locked(db, emp):
        audit.record(db, action="auth.mfa_locked", success=False, **base)
        db.commit()
        raise ApiError(
            429, "TOO_MANY_ATTEMPTS", "Too many wrong codes. Wait a few minutes and try again."
        )
    step = (
        verify_totp(emp.mfa_secret_enc, body.code, emp.mfa_last_step)
        if emp.mfa_secret_enc
        else None
    )
    if step is None:
        audit.record(db, action="auth.mfa_verify", success=False, **base)
        db.commit()
        raise ApiError(400, "MFA_INVALID", "Invalid or reused code")
    emp.mfa_last_step = step
    emp.mfa_enrolled_at = emp.mfa_enrolled_at or datetime.now(UTC)
    sess = db.merge(p.session)
    sess.mfa_verified = True
    audit.record(db, action="auth.mfa_verify", success=True, **base)
    db.commit()
    return _session_out(Principal(emp, sess, p.grants))


@router.post("/employees/{employee_id}/revoke-sessions", status_code=204)
def revoke_sessions(
    employee_id: uuid.UUID,
    request: Request,
    db: DbDep,
    p: Principal = Depends(require_permission("employee.manage")),
) -> None:
    target = db.get(Employee, employee_id)
    if target is None or target.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Employee not found")
    _revoke_all(db, employee_id, "admin_revoked")
    audit.record(
        db,
        action="auth.sessions_revoked",
        success=True,
        company_id=p.company_id,
        actor_id=p.employee.id,
        target_type="employee",
        target_id=str(employee_id),
        request_id=_rid(request),
    )
    db.commit()


def _mfa_locked(db: Session, emp: Employee) -> bool:
    """True when the last MFA_MAX_FAILURES verifications in the window all failed. The caller
    holds the employee row lock, so concurrent guesses are counted one after another."""
    s = get_settings()
    since = datetime.now(UTC) - timedelta(minutes=s.mfa_window_minutes)
    recent = db.scalars(
        select(AuditEvent.success)
        .where(
            AuditEvent.company_id == emp.company_id,
            AuditEvent.created_at > since,
            AuditEvent.actor_id == emp.id,
            AuditEvent.action == "auth.mfa_verify",
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(s.mfa_max_failures)
    ).all()
    return len(recent) >= s.mfa_max_failures and not any(recent)


def _revoke_all(db: Session, employee_id: uuid.UUID, reason: str) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.employee_id == employee_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=func.now(), revoked_reason=reason)
    )
