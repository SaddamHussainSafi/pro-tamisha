"""Password hashing, opaque session/CSRF tokens, TOTP with encrypted secrets."""

import base64
import hashlib
import hmac
import secrets
import time
from functools import lru_cache

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet

from .config import get_settings

_ph = PasswordHasher()
# Pre-computed hash so unknown-email logins spend the same time as wrong-password logins.
_DUMMY_HASH = _ph.hash("not-a-real-password-" + secrets.token_hex(8))

TOTP_STEP_SECONDS = 30


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _ph.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """Return (raw token for the client, sha256 hex stored server-side)."""
    raw = secrets.token_urlsafe(32)
    return raw, sha256_hex(raw)


def tokens_match(raw: str | None, stored_sha: str) -> bool:
    return raw is not None and hmac.compare_digest(sha256_hex(raw), stored_sha)


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().mfa_encryption_key
    try:
        return Fernet(key)
    except ValueError:
        if get_settings().is_production_like:
            raise RuntimeError("MFA_ENCRYPTION_KEY must be a valid Fernet key") from None
        # dev/test only: derive a stable key from the placeholder
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def new_totp_secret() -> tuple[str, bytes]:
    secret = pyotp.random_base32()
    return secret, _fernet().encrypt(secret.encode())


def totp_uri(secret: str, email: str) -> str:
    return str(
        pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=get_settings().mfa_issuer)
    )


def verify_totp(secret_enc: bytes, code: str, last_step: int | None) -> int | None:
    """Return the accepted time-step, or None. Rejects reuse of a step <= last_step (replay)."""
    if not (code.isdigit() and len(code) == 6):
        return None
    secret = _fernet().decrypt(secret_enc).decode()
    totp = pyotp.TOTP(secret)
    now_step = int(time.time()) // TOTP_STEP_SECONDS
    for step in (now_step - 1, now_step, now_step + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(totp.at(step * TOTP_STEP_SECONDS), code):
            return step
    return None


PASSWORD_MIN_LENGTH = 12  # schemas.Password enforces 12-128 characters


def password_problem(email: str, password: str) -> str | None:
    """Server-side password rule on top of the 12-128 length check: not the email, and not a
    few repeated characters. The web app mirrors it (lib/staff.ts passwordProblem)."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return "Use at least 12 characters"
    if password.strip().lower() == email.strip().lower() or len(set(password)) < 4:
        return "Choose a less guessable password"
    return None


# Recovery codes: 16 symbols from a 30-letter alphabet without look-alikes (~78 bits), shown
# as XXXX-XXXX-XXXX-XXXX. High entropy, so a plain SHA-256 at rest is enough.
_RC_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
RECOVERY_CODE_COUNT = 10


def new_recovery_code() -> str:
    raw = "".join(secrets.choice(_RC_ALPHABET) for _ in range(16))
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))


def normalize_recovery_code(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def recovery_code_sha(code: str) -> str:
    return sha256_hex(normalize_recovery_code(code))
