"""Account links and self-service: forgot / reset password, invitation acceptance, my sessions
and my sign-in activity.

The link routes are public (no session). They never say whether an email has an account:
"forgot password" always answers 202 with the same message, and every bad, used or expired
link gets the same 400 TOKEN_INVALID. Failed link attempts and reset requests are throttled per
client IP; reset emails are also capped per account (PASSWORD_RESET_MAX_PER_HOUR)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from .. import accounts, audit, ratelimit
from ..config import get_settings
from ..deps import DbDep, VerifiedPrincipalDep
from ..errors import ApiError
from ..models import AuditEvent, AuthSession, AuthToken, Employee
from ..schemas import (
    ForgotPasswordIn,
    MessageOut,
    SecurityEventOut,
    SessionInfoOut,
    TokenInspectIn,
    TokenInspectOut,
    TokenPasswordIn,
)
from ..security import hash_password, password_problem
from .auth import client_info

router = APIRouter(prefix="/auth", tags=["auth"])

FORGOT_MESSAGE = (
    "If that email belongs to an account, a link to set a new password is on its way. "
    "It works once and expires soon."
)
BAD_LINK = ApiError(
    400, "TOKEN_INVALID", "This link is invalid, already used or expired. Ask for a new one."
)

# Sign-in and account events shown in login history and the Security page.
AUTH_ACTIONS = (
    "auth.login",
    "auth.dev_login",
    "auth.logout",
    "auth.mfa_verify",
    "auth.mfa_locked",
    "auth.mfa_enabled",
    "auth.mfa_disabled",
    "auth.mfa_recovery_used",
    "auth.mfa_recovery_regenerated",
    "auth.reauth",
    "auth.password_reset_requested",
    "auth.password_reset",
    "auth.invitation_accepted",
    "auth.session_revoked",
    "auth.sessions_revoked",
    "employee.password_change",
)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _throttle(request: Request, bucket: str) -> str:
    """Per-IP limit on failed link attempts and reset requests (same limits as sign-in)."""
    s = get_settings()
    ip = client_info(request)["ip"]
    tries = ratelimit.attempt(bucket, ip, s.login_ip_window_minutes * 60)
    if tries > s.login_ip_max_failures:
        if tries == s.login_ip_max_failures + 1:
            audit.record_independent(
                action="auth.link_throttled",
                success=False,
                request_id=_rid(request),
                detail={"bucket": bucket, "ip": ip},
            )
        raise ApiError(429, "TOO_MANY_ATTEMPTS", "Too many attempts. Wait a few minutes.")
    return ip


# ---------------------------------------------------------------- public: links


@router.post("/password/forgot", status_code=202, response_model=MessageOut)
def forgot_password(body: ForgotPasswordIn, request: Request, db: DbDep) -> MessageOut:
    s = get_settings()
    _throttle(request, "forgot_ip")
    where = client_info(request)
    emp = db.scalar(
        select(Employee)
        .where(func.lower(Employee.email) == body.email.strip().lower())
        .with_for_update()
    )
    if emp is None or not emp.active:
        audit.record(
            db,
            action="auth.password_reset_requested",
            success=False,
            company_id=emp.company_id if emp else None,
            actor_id=emp.id if emp else None,
            request_id=_rid(request),
            detail={"reason": "unknown_email" if emp is None else "inactive", **where},
        )
        db.commit()
        return MessageOut(message=FORGOT_MESSAGE)
    base: dict[str, Any] = {
        "company_id": emp.company_id,
        "actor_id": emp.id,
        "request_id": _rid(request),
    }
    recent = db.scalar(
        select(func.count()).where(
            AuthToken.employee_id == emp.id,
            AuthToken.purpose == accounts.RESET,
            AuthToken.created_at > datetime.now(UTC) - timedelta(hours=1),
        )
    )
    if (recent or 0) >= s.password_reset_max_per_hour:
        audit.record(
            db,
            action="auth.password_reset_requested",
            success=False,
            detail={"reason": "per_account_limit", **where},
            **base,
        )
        db.commit()
        return MessageOut(message=FORGOT_MESSAGE)
    raw, tok = accounts.issue_token(db, emp, accounts.RESET)
    accounts.send_mail(
        db,
        emp,
        subject=f"{s.app_name}: set a new password",
        body=(
            f"Hello {emp.display_name},\n\nSomeone asked to reset the password for this account. "
            f"Open this link to choose a new one (it works once and expires in "
            f"{s.password_reset_ttl_minutes} minutes):\n\n{{link}}\n\n"
            "If you didn't ask for this, ignore this email; your password stays the same."
        ),
        dedupe_key=f"auth:reset:{tok.id}",
        secret_link=accounts.link(accounts.RESET, raw),
    )
    audit.record(
        db,
        action="auth.password_reset_requested",
        success=True,
        target_type="employee",
        target_id=str(emp.id),
        detail=where,
        **base,
    )
    db.commit()
    # Every request counts toward the IP limit, known email or not, so the limit itself can't
    # be used to tell which emails have accounts.
    return MessageOut(message=FORGOT_MESSAGE)


@router.post("/token/inspect", response_model=TokenInspectOut)
def inspect_link(body: TokenInspectIn, request: Request, db: DbDep) -> TokenInspectOut:
    """What a link is for (who, until when), so the page can greet the person. POST keeps the
    token out of URLs and logs."""
    ip = _throttle(request, "link_ip")
    tok = accounts.peek_token(db, body.token, body.purpose)
    emp = db.get(Employee, tok.employee_id) if tok else None
    if tok is None or emp is None or not emp.active:
        raise BAD_LINK
    ratelimit.undo("link_ip", ip)
    return TokenInspectOut(
        email=emp.email,
        display_name=emp.display_name,
        purpose=body.purpose,
        expires_at=tok.expires_at,
    )


def _set_password_from_link(
    body: TokenPasswordIn, request: Request, db: DbDep, purpose: str, action: str
) -> None:
    ip = _throttle(request, "link_ip")
    found = accounts.consume_token(db, body.token, purpose)
    if found is None:
        db.rollback()
        audit.record_independent(
            action=action,
            success=False,
            request_id=_rid(request),
            detail={"reason": "bad_link", **client_info(request)},
        )
        raise BAD_LINK
    tok, emp = found
    if purpose == accounts.INVITE and emp.password_hash is not None:
        db.rollback()
        raise BAD_LINK  # already set up (e.g. a Super Admin set a password meanwhile)
    if problem := password_problem(emp.email, body.password):
        db.rollback()
        raise ApiError(422, "WEAK_PASSWORD", problem)
    now = datetime.now(UTC)
    emp.password_hash = hash_password(body.password)
    emp.password_changed_at = now
    emp.failed_login_count, emp.locked_until = 0, None
    tok.used_at = now
    accounts.revoke_tokens(db, emp.id)  # every other open invite / reset link
    accounts.sign_out(db, emp.id, "password_reset")
    audit.record(
        db,
        action=action,
        success=True,
        company_id=emp.company_id,
        actor_id=emp.id,
        target_type="employee",
        target_id=str(emp.id),
        request_id=_rid(request),
        detail=client_info(request),
    )
    if purpose == accounts.RESET:
        accounts.send_security_notice(db, emp, "Your password was changed", "pw-reset")
    db.commit()
    ratelimit.undo("link_ip", ip)


@router.post("/password/reset", status_code=204)
def reset_password(body: TokenPasswordIn, request: Request, db: DbDep) -> None:
    """Set a new password from an emailed link. Signs the account out everywhere."""
    _set_password_from_link(body, request, db, accounts.RESET, "auth.password_reset")


@router.post("/invitation/accept", status_code=204)
def accept_invitation(body: TokenPasswordIn, request: Request, db: DbDep) -> None:
    """A new person chooses their password; they then sign in normally."""
    _set_password_from_link(body, request, db, accounts.INVITE, "auth.invitation_accepted")


# ---------------------------------------------------------------- my sessions and activity


def session_info(sess: AuthSession, current: uuid.UUID | None) -> SessionInfoOut:
    return SessionInfoOut(
        id=sess.id,
        created_at=sess.created_at,
        last_seen_at=sess.last_seen_at,
        expires_at=sess.expires_at,
        user_agent=sess.user_agent,
        ip_address=sess.ip_address,
        current=sess.id == current,
    )


@router.get("/sessions", response_model=list[SessionInfoOut])
def my_sessions(p: VerifiedPrincipalDep, db: DbDep) -> list[SessionInfoOut]:
    rows = db.scalars(
        select(AuthSession)
        .where(AuthSession.employee_id == p.employee.id, *accounts.live_sessions_filter())
        .order_by(AuthSession.last_seen_at.desc())
    ).all()
    return [session_info(r, p.session.id) for r in rows]


@router.post("/sessions/{session_id}/revoke", status_code=204)
def revoke_my_session(
    session_id: uuid.UUID, request: Request, p: VerifiedPrincipalDep, db: DbDep
) -> None:
    if session_id == p.session.id:
        raise ApiError(422, "CURRENT_SESSION", "Use Sign out to end this session")
    sess = db.get(AuthSession, session_id, with_for_update=True, populate_existing=True)
    if sess is None or sess.employee_id != p.employee.id:
        raise ApiError(404, "NOT_FOUND", "Session not found")
    if sess.revoked_at is None:
        sess.revoked_at, sess.revoked_reason = datetime.now(UTC), "self_revoked"
        audit.record(
            db,
            action="auth.session_revoked",
            success=True,
            company_id=p.company_id,
            actor_id=p.employee.id,
            target_type="session",
            target_id=str(session_id),
            request_id=_rid(request),
        )
    db.commit()


@router.post("/sessions/revoke-others", status_code=204)
def revoke_my_other_sessions(request: Request, p: VerifiedPrincipalDep, db: DbDep) -> None:
    accounts.sign_out(db, p.employee.id, "self_revoked", keep=p.session.id)
    audit.record(
        db,
        action="auth.sessions_revoked",
        success=True,
        company_id=p.company_id,
        actor_id=p.employee.id,
        target_type="employee",
        target_id=str(p.employee.id),
        request_id=_rid(request),
        detail={"scope": "others"},
    )
    db.commit()


def event_out(ev: AuditEvent, names: dict[uuid.UUID, str]) -> SecurityEventOut:
    d = ev.detail_redacted or {}
    target_name = None
    if ev.target_type == "employee" and ev.target_id:
        try:
            target_name = names.get(uuid.UUID(ev.target_id))
        except ValueError:
            target_name = None
    return SecurityEventOut(
        id=ev.id,
        action=ev.action,
        success=ev.success,
        created_at=ev.created_at,
        actor_id=ev.actor_id,
        actor_name=names.get(ev.actor_id) if ev.actor_id else None,
        target_type=ev.target_type,
        target_id=ev.target_id,
        target_name=target_name,
        reason=str(d["reason"]) if d.get("reason") else None,
        ip_address=str(d["ip"]) if d.get("ip") else None,
        user_agent=str(d["user_agent"]) if d.get("user_agent") else None,
    )


@router.get("/activity", response_model=list[SecurityEventOut])
def my_activity(
    p: VerifiedPrincipalDep, db: DbDep, limit: int = Query(30, ge=1, le=100)
) -> list[SecurityEventOut]:
    """My recent sign-ins (including failed ones) and account changes."""
    rows = db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.company_id == p.company_id,
            AuditEvent.actor_id == p.employee.id,
            AuditEvent.action.in_(AUTH_ACTIONS),
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    ).all()
    names = {p.employee.id: p.employee.display_name}
    return [event_out(r, names) for r in rows]
