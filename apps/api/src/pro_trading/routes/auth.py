"""Login/logout/session, optional two-step sign-in (TOTP + recovery codes), session revocation.

Two-step sign-in is optional for everyone (owner, 2026-11-09). A person who turns it on is asked
for a code (or a recovery code) at every sign-in; until then the session holds no permissions.
The company flag `require_two_step` (off) would make everyone set it up at their next sign-in."""

import ipaddress
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import accounts, audit, ratelimit
from ..config import LOCAL_HOSTS, get_settings
from ..deps import (
    DbDep,
    Principal,
    PrincipalDep,
    VerifiedPrincipalDep,
    build_principal,
    require_permission,
)
from ..errors import ApiError
from ..models import AuditEvent, AuthSession, Employee, EmployeeRole, Role
from ..schemas import (
    EmployeeOut,
    GrantOut,
    LoginIn,
    LoginOut,
    MfaChangeIn,
    MfaConfirmIn,
    MfaConfirmOut,
    MfaEnrollIn,
    MfaEnrollOut,
    MfaVerifyIn,
    RecoveryCodesOut,
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


def _loopback(host: str) -> bool:
    """127.0.0.0/8, ::1 and IPv4-mapped loopback (::ffff:127.0.0.1, how Node reports v4)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or (mapped is not None and mapped.is_loopback)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def client_info(request: Request) -> dict[str, str]:
    """Where a sign-in came from, for login history (audit detail and session row)."""
    return {
        "ip": _client_ip(request)[:64],
        "user_agent": (request.headers.get("user-agent") or "")[:300],
    }


def _role_codes(db: Session, emp_id: uuid.UUID) -> list[str]:
    return sorted(
        set(
            db.scalars(
                select(Role.code)
                .join(EmployeeRole, EmployeeRole.role_id == Role.id)
                .where(EmployeeRole.employee_id == emp_id)
            )
        )
    )


def session_out(db: Session, p: Principal) -> SessionOut:
    return SessionOut(
        employee=EmployeeOut.model_validate(p.employee),
        mfa_required=p.mfa_required,
        mfa_enrolled=p.mfa_enrolled,
        mfa_setup_required=p.mfa_setup_required,
        roles=_role_codes(db, p.employee.id),
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
    ip = _client_ip(request)
    # Counted as a failure up front; a successful sign-in takes it back (see below).
    tries = ratelimit.attempt("login_ip", ip, s.login_ip_window_minutes * 60)
    if tries > s.login_ip_max_failures:
        if tries == s.login_ip_max_failures + 1:  # audit once per window, not per request
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
    where = client_info(request)
    if emp is None:
        verify_password(None, body.password)  # equalize timing
        audit.record_independent(
            action="auth.login",
            success=False,
            request_id=_rid(request),
            detail={"reason": "unknown_email", **where},
        )
        raise BAD_LOGIN
    base: dict[str, Any] = {
        "company_id": emp.company_id,
        "actor_id": emp.id,
        "request_id": _rid(request),
    }
    if emp.locked_until and emp.locked_until > now:
        db.rollback()
        audit.record_independent(
            action="auth.login", success=False, detail={"reason": "locked", **where}, **base
        )
        raise ApiError(423, "ACCOUNT_LOCKED", "Too many failed attempts. Try again later.")
    if not emp.active or not verify_password(emp.password_hash, body.password):
        emp.failed_login_count += 1
        reason = (
            "inactive"
            if not emp.active
            else "invitation_pending"
            if emp.password_hash is None
            else "bad_password"
        )
        if emp.failed_login_count >= s.login_max_failures:
            emp.locked_until = now + timedelta(minutes=s.login_lockout_minutes)
            emp.failed_login_count = 0
            reason = "locked_now"
        audit.record(
            db, action="auth.login", success=False, detail={"reason": reason, **where}, **base
        )
        db.commit()
        raise BAD_LOGIN

    emp.failed_login_count, emp.locked_until = 0, None
    ratelimit.undo("login_ip", ip)
    return _start_session(db, emp, request, response, action="auth.login", base=base)


@router.post("/dev-login", response_model=LoginOut, include_in_schema=False)
def dev_login(request: Request, response: Response, db: DbDep) -> LoginOut:
    """Development only: sign in as DEV_AUTO_LOGIN_EMAIL without password or MFA.

    Answers only when all hold: the setting is non-empty, APP_ENV is development/test, the
    configured FRONTEND_ORIGIN is localhost, and this request came from a loopback address with a
    localhost Host header (so a LAN client or a DNS-rebinding page cannot use it)."""
    s = get_settings()
    client = request.client.host if request.client else ""
    forwarded = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h]
    if (
        not s.dev_auto_login_email
        or s.is_production_like
        or not s.frontend_is_local
        or not _loopback(client)
        or not all(_loopback(h) for h in forwarded)  # a proxy passing a remote client on
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
    """mfa_verified=False means: if this person uses two-step sign-in (or the company requires
    it) the new session holds no permissions until /auth/mfa/verify succeeds."""
    s = get_settings()
    now = datetime.now(UTC)
    raw, token_sha = new_token()
    csrf_raw, csrf_sha = new_token()
    where = client_info(request)
    sess = AuthSession(
        employee_id=emp.id,
        token_sha256=token_sha,
        csrf_token_sha256=csrf_sha,
        expires_at=now + timedelta(minutes=s.session_ttl_minutes),
        last_seen_at=now,
        user_agent=where["user_agent"],
        ip_address=where["ip"],
        mfa_verified=mfa_verified,
    )
    db.add(sess)
    db.flush()
    p = build_principal(db, emp, sess)
    if not p.mfa_required:
        emp.last_login_at = now
    audit.record(
        db,
        action=action,
        success=True,
        target_type="session",
        target_id=str(sess.id),
        detail={**where, "two_step_pending": p.mfa_required},
        **base,
    )
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
    return LoginOut(**session_out(db, p).model_dump(), csrf_token=csrf_raw)


@router.get("/session", response_model=SessionOut)
def get_session(p: PrincipalDep, db: DbDep) -> SessionOut:
    return session_out(db, p)


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


def _base(p: Principal, request: Request) -> dict[str, Any]:
    return {"company_id": p.company_id, "actor_id": p.employee.id, "request_id": _rid(request)}


def _lock_me(db: Session, p: Principal) -> Employee:
    emp = db.get(Employee, p.employee.id, with_for_update=True, populate_existing=True)
    assert emp is not None
    return emp


def _check_password(db: Session, emp: Employee, password: str, base: dict[str, Any]) -> None:
    """Re-authentication for sensitive self-service changes. Wrong passwords count toward the
    sign-in lockout; reaching it locks the account and signs it out everywhere."""
    s = get_settings()
    if verify_password(emp.password_hash, password):
        return
    emp.failed_login_count += 1
    locked = emp.failed_login_count >= s.login_max_failures
    if locked:
        emp.locked_until = datetime.now(UTC) + timedelta(minutes=s.login_lockout_minutes)
        emp.failed_login_count = 0
        accounts.sign_out(db, emp.id, "password_guessing")
    audit.record(db, action="auth.reauth", success=False, detail={"locked": locked}, **base)
    db.commit()
    raise ApiError(422, "WRONG_PASSWORD", "Your password is not right")


def _second_factor(
    db: Session, emp: Employee, body: MfaVerifyIn, base: dict[str, Any], *, action: str
) -> str:
    """Check a TOTP code or spend a recovery code. Returns "totp" or "recovery_code".
    Wrong answers are limited per person (MFA_MAX_FAILURES per MFA_WINDOW_MINUTES, then 429).
    The caller holds the employee row lock, so parallel guesses are counted one by one."""
    if (body.code is None) == (body.recovery_code is None):
        raise ApiError(422, "VALIDATION", "Enter the 6-digit code or one recovery code")
    if emp.mfa_enrolled_at is None or emp.mfa_secret_enc is None:
        raise ApiError(409, "MFA_NOT_ENROLLED", "Two-step sign-in is not turned on")
    if _mfa_locked(db, emp):
        audit.record(db, action="auth.mfa_locked", success=False, **base)
        db.commit()
        raise ApiError(
            429, "TOO_MANY_ATTEMPTS", "Too many wrong codes. Wait a few minutes and try again."
        )
    if body.code is not None:
        step = verify_totp(emp.mfa_secret_enc, body.code, emp.mfa_last_step)
        if step is not None:
            emp.mfa_last_step = step
            audit.record(db, action="auth.mfa_verify", success=True, detail={"via": action}, **base)
            return "totp"
    elif body.recovery_code and accounts.use_recovery_code(db, emp.id, body.recovery_code):
        audit.record(db, action="auth.mfa_verify", success=True, detail={"via": action}, **base)
        audit.record(
            db,
            action="auth.mfa_recovery_used",
            success=True,
            detail={"left": accounts.recovery_codes_left(db, emp.id)},
            **base,
        )
        accounts.send_security_notice(db, emp, "A recovery code was used to sign in", "rc")
        return "recovery_code"
    audit.record(db, action="auth.mfa_verify", success=False, detail={"via": action}, **base)
    db.commit()
    raise ApiError(400, "MFA_INVALID", "That code is wrong or was already used")


@router.post("/mfa/verify", response_model=SessionOut)
def mfa_verify(body: MfaVerifyIn, request: Request, p: PrincipalDep, db: DbDep) -> SessionOut:
    """Second step of sign-in for people who turned two-step sign-in on."""
    emp = _lock_me(db, p)
    sess = db.get(AuthSession, p.session.id, with_for_update=True, populate_existing=True)
    assert sess is not None
    if sess.mfa_verified:
        return session_out(db, build_principal(db, emp, sess))
    _second_factor(db, emp, body, _base(p, request), action="sign_in")
    sess.mfa_verified = True
    emp.last_login_at = datetime.now(UTC)
    db.commit()
    return session_out(db, build_principal(db, emp, sess))


@router.post("/mfa/enroll", response_model=MfaEnrollOut)
def mfa_enroll(
    request: Request, p: PrincipalDep, db: DbDep, body: MfaEnrollIn | None = None
) -> MfaEnrollOut:
    """Start setting up an authenticator app. From My account the password is asked again;
    during a sign-in that the company's policy holds for setup it was just entered."""
    emp = _lock_me(db, p)
    base = _base(p, request)
    if emp.mfa_enrolled_at is not None:
        raise ApiError(409, "MFA_ALREADY_ENROLLED", "Two-step sign-in is already on")
    if p.mfa_required and not p.mfa_setup_required:
        raise ApiError(403, "MFA_REQUIRED", "Enter your two-step sign-in code first")
    if not p.mfa_required:
        if body is None or body.password is None:
            raise ApiError(422, "PASSWORD_REQUIRED", "Enter your password to turn this on")
        _check_password(db, emp, body.password, base)
    secret, enc = new_totp_secret()
    emp.mfa_secret_enc, emp.mfa_last_step = enc, None
    audit.record(db, action="auth.mfa_enroll_started", success=True, **base)
    db.commit()
    return MfaEnrollOut(otpauth_uri=totp_uri(secret, emp.email))


@router.post("/mfa/enroll/confirm", response_model=MfaConfirmOut)
def mfa_enroll_confirm(
    body: MfaConfirmIn, request: Request, p: PrincipalDep, db: DbDep
) -> MfaConfirmOut:
    """Finish setup with a code from the app. Turns two-step sign-in on, returns 10 one-time
    recovery codes (shown once) and signs out every other session (they had no second step)."""
    emp = _lock_me(db, p)
    base = _base(p, request)
    if emp.mfa_enrolled_at is not None:
        raise ApiError(409, "MFA_ALREADY_ENROLLED", "Two-step sign-in is already on")
    if emp.mfa_secret_enc is None:
        raise ApiError(409, "MFA_NOT_STARTED", "Start the setup first")
    if p.mfa_required and not p.mfa_setup_required:
        raise ApiError(403, "MFA_REQUIRED", "Enter your two-step sign-in code first")
    if _mfa_locked(db, emp):
        audit.record(db, action="auth.mfa_locked", success=False, **base)
        db.commit()
        raise ApiError(
            429, "TOO_MANY_ATTEMPTS", "Too many wrong codes. Wait a few minutes and try again."
        )
    step = verify_totp(emp.mfa_secret_enc, body.code, emp.mfa_last_step)
    if step is None:
        audit.record(db, action="auth.mfa_verify", success=False, detail={"via": "setup"}, **base)
        db.commit()
        raise ApiError(400, "MFA_INVALID", "That code is wrong. Check the time on your phone.")
    now = datetime.now(UTC)
    emp.mfa_last_step, emp.mfa_enrolled_at = step, now
    codes = accounts.new_recovery_codes(db, emp.id)
    sess = db.get(AuthSession, p.session.id, with_for_update=True, populate_existing=True)
    assert sess is not None
    if p.mfa_required:  # policy setup during sign-in: this completes the sign-in
        emp.last_login_at = now
    sess.mfa_verified = True
    accounts.sign_out(db, emp.id, "mfa_enabled", keep=sess.id)
    audit.record(db, action="auth.mfa_verify", success=True, detail={"via": "setup"}, **base)
    audit.record(db, action="auth.mfa_enabled", success=True, **base)
    accounts.send_security_notice(db, emp, "Two-step sign-in was turned on", "mfa-on")
    db.commit()
    return MfaConfirmOut(
        session=session_out(db, build_principal(db, emp, sess)), recovery_codes=codes
    )


@router.post("/mfa/disable", response_model=SessionOut)
def mfa_disable(
    body: MfaChangeIn, request: Request, p: VerifiedPrincipalDep, db: DbDep
) -> SessionOut:
    """Turn two-step sign-in off: password plus a current code or a recovery code."""
    emp = _lock_me(db, p)
    base = _base(p, request)
    if p.two_step_policy:
        raise ApiError(409, "TWO_STEP_REQUIRED", "Your company requires two-step sign-in")
    _check_password(db, emp, body.password, base)
    _second_factor(db, emp, body, base, action="disable")
    accounts.clear_two_step(db, emp)
    audit.record(db, action="auth.mfa_disabled", success=True, **base)
    accounts.send_security_notice(db, emp, "Two-step sign-in was turned off", "mfa-off")
    db.commit()
    return session_out(db, build_principal(db, emp, p.session))


@router.post("/mfa/recovery-codes", response_model=RecoveryCodesOut)
def mfa_new_recovery_codes(
    body: MfaChangeIn, request: Request, p: VerifiedPrincipalDep, db: DbDep
) -> RecoveryCodesOut:
    """Replace every recovery code with 10 new ones (the old ones stop working)."""
    emp = _lock_me(db, p)
    base = _base(p, request)
    _check_password(db, emp, body.password, base)
    _second_factor(db, emp, body, base, action="recovery_codes")
    codes = accounts.new_recovery_codes(db, emp.id)
    audit.record(db, action="auth.mfa_recovery_regenerated", success=True, **base)
    db.commit()
    return RecoveryCodesOut(recovery_codes=codes)


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
    accounts.sign_out(db, employee_id, "admin_revoked")
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
