"""Account links (invitations, password resets), recovery codes, sessions and security mail.

Rules kept here so every route applies them the same way:
- a link token is 256 random bits; only its SHA-256 is stored, it is single use and expires
  (INVITE_TTL_HOURS, PASSWORD_RESET_TTL_MINUTES); issuing a new one revokes the older ones;
- lock order is employee row, then its token rows (see `consume_token`);
- emails go to the email_messages table, which is the mailbox in dev/test. No real provider is
  configured yet (OPEN_DECISIONS D-15), so in staging/production the link is NOT written to the
  table: the message is kept without it and a warning is logged.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, delete, func, select, update
from sqlalchemy.orm import Session

from .config import get_settings
from .models import AuthSession, AuthToken, EmailMessage, Employee, MfaRecoveryCode
from .security import (
    RECOVERY_CODE_COUNT,
    new_recovery_code,
    new_token,
    recovery_code_sha,
    sha256_hex,
)

log = logging.getLogger("pro_trading.accounts")

INVITE, RESET = "INVITE", "PASSWORD_RESET"
LINK_PATHS = {INVITE: "/accept-invite", RESET: "/reset-password"}


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- link tokens


def _ttl(purpose: str) -> timedelta:
    s = get_settings()
    if purpose == INVITE:
        return timedelta(hours=s.invite_ttl_hours)
    return timedelta(minutes=s.password_reset_ttl_minutes)


def revoke_tokens(db: Session, employee_id: uuid.UUID, *purposes: str) -> None:
    db.execute(
        update(AuthToken)
        .where(
            AuthToken.employee_id == employee_id,
            AuthToken.purpose.in_(purposes or (INVITE, RESET)),
            AuthToken.used_at.is_(None),
            AuthToken.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )


def issue_token(
    db: Session, emp: Employee, purpose: str, created_by: uuid.UUID | None = None
) -> tuple[str, AuthToken]:
    """New single-use link for `emp`; older open links of the same purpose stop working.
    The caller holds the employee row lock."""
    revoke_tokens(db, emp.id, purpose)
    raw, sha = new_token()
    tok = AuthToken(
        company_id=emp.company_id,
        employee_id=emp.id,
        purpose=purpose,
        token_sha256=sha,
        created_by=created_by,
        expires_at=now() + _ttl(purpose),
    )
    db.add(tok)
    db.flush()
    return raw, tok


def peek_token(db: Session, raw: str, purpose: str) -> AuthToken | None:
    """The open, unexpired token for this raw value, without locks (for display)."""
    tok = db.scalar(select(AuthToken).where(AuthToken.token_sha256 == sha256_hex(raw)))
    if (
        tok is None
        or tok.purpose != purpose
        or tok.used_at is not None
        or tok.revoked_at is not None
        or tok.expires_at <= now()
    ):
        return None
    return tok


def consume_token(db: Session, raw: str, purpose: str) -> tuple[AuthToken, Employee] | None:
    """Lock the employee, then the token, re-check both and return them (caller marks it used).
    Employee first, like every other path that issues or revokes tokens, so they can't deadlock."""
    first = peek_token(db, raw, purpose)
    if first is None:
        return None
    emp = db.get(Employee, first.employee_id, with_for_update=True, populate_existing=True)
    tok = db.get(AuthToken, first.id, with_for_update=True, populate_existing=True)
    if emp is None or tok is None or not emp.active:
        return None
    if tok.used_at is not None or tok.revoked_at is not None or tok.expires_at <= now():
        return None
    return tok, emp


def open_invite_expiry(db: Session, employee_id: uuid.UUID) -> datetime | None:
    return db.scalar(
        select(func.max(AuthToken.expires_at)).where(
            AuthToken.employee_id == employee_id,
            AuthToken.purpose == INVITE,
            AuthToken.used_at.is_(None),
            AuthToken.revoked_at.is_(None),
        )
    )


def link(purpose: str, raw: str) -> str:
    # The token rides in the URL fragment: browsers never send it to a server or in a Referer.
    return f"{get_settings().frontend_origin}{LINK_PATHS[purpose]}#token={raw}"


# ---------------------------------------------------------------- mail


def send_mail(
    db: Session,
    emp: Employee,
    *,
    subject: str,
    body: str,
    dedupe_key: str,
    secret_link: str | None = None,
) -> None:
    """Queue a message to the employee. `{link}` in `body` is replaced by `secret_link`."""
    s = get_settings()
    if secret_link is not None and s.is_production_like:
        log.warning(
            "no email provider: account link not stored", extra={"ctx": {"dedupe": dedupe_key}}
        )
        secret_link = "[link withheld: no email provider is configured]"  # noqa: S105
    db.add(
        EmailMessage(
            company_id=emp.company_id,
            dedupe_key=dedupe_key,
            to_addr=emp.email,
            subject=subject,
            body=body.replace("{link}", secret_link or ""),
        )
    )


def send_security_notice(db: Session, emp: Employee, what: str, key: str) -> None:
    """'Your password was changed' style notice, so the real owner notices a takeover."""
    send_mail(
        db,
        emp,
        subject=f"{get_settings().app_name}: {what}",
        body=(
            f"Hello {emp.display_name},\n\n{what} on your {get_settings().app_name} account "
            f"at {now():%Y-%m-%d %H:%M} UTC.\n\nIf this wasn't you, tell your Super Admin now."
        ),
        dedupe_key=f"security:{key}:{emp.id}:{uuid.uuid4()}",
    )


# ---------------------------------------------------------------- recovery codes


def new_recovery_codes(db: Session, employee_id: uuid.UUID) -> list[str]:
    """Replace all of this person's recovery codes with a fresh set (shown once)."""
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.employee_id == employee_id))
    codes = [new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    db.add_all(
        MfaRecoveryCode(employee_id=employee_id, code_sha256=recovery_code_sha(c)) for c in codes
    )
    db.flush()
    return codes


def use_recovery_code(db: Session, employee_id: uuid.UUID, code: str) -> bool:
    """Spend one unused recovery code. The caller holds the employee row lock."""
    row = db.scalar(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.employee_id == employee_id,
            MfaRecoveryCode.code_sha256 == recovery_code_sha(code),
            MfaRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if row is None:
        return False
    row.used_at = now()
    return True


def recovery_codes_left(db: Session, employee_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count()).where(
                MfaRecoveryCode.employee_id == employee_id, MfaRecoveryCode.used_at.is_(None)
            )
        )
        or 0
    )


def clear_two_step(db: Session, emp: Employee) -> None:
    emp.mfa_secret_enc, emp.mfa_enrolled_at, emp.mfa_last_step = None, None, None
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.employee_id == emp.id))


# ---------------------------------------------------------------- sessions


def sign_out(
    db: Session, employee_id: uuid.UUID, reason: str, keep: uuid.UUID | None = None
) -> None:
    q = update(AuthSession).where(
        AuthSession.employee_id == employee_id, AuthSession.revoked_at.is_(None)
    )
    if keep is not None:
        q = q.where(AuthSession.id != keep)
    db.execute(q.values(revoked_at=func.now(), revoked_reason=reason))


def live_sessions_filter() -> list[ColumnElement[bool]]:
    """Conditions for sessions that still work (not revoked, not expired, not idle too long)."""
    s = get_settings()
    t = now()
    return [
        AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > t,
        AuthSession.last_seen_at > t - timedelta(minutes=s.session_idle_minutes),
    ]
