"""Encryption at rest for mailbox app passwords (Fernet, LARK_MAIL_KEY). Decrypted values live
only in the worker / request that opens the connection; they are never logged or returned."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from ..config import DEV_LARK_MAIL_KEY, get_settings


class SecretUnavailable(RuntimeError):  # noqa: N818
    pass


def _fernet() -> Fernet:
    s = get_settings()
    key = s.lark_mail_key
    if key == DEV_LARK_MAIL_KEY:
        if s.is_production_like:
            raise SecretUnavailable("LARK_MAIL_KEY is not set on this server")
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))
    try:
        return Fernet(key)
    except ValueError:
        raise SecretUnavailable("LARK_MAIL_KEY is not a valid Fernet key") from None


def seal(secret: str) -> bytes:
    return _fernet().encrypt(secret.encode())


def unseal(blob: bytes) -> str:
    try:
        return _fernet().decrypt(blob).decode()
    except InvalidToken:
        raise SecretUnavailable("The saved password can't be decrypted (key changed?)") from None
