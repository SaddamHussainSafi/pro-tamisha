"""Proving that a POST to /gmail/push really comes from our Pub/Sub subscription.

Two checks, either or both, set in the environment:
- OIDC: the push subscription is created with "Enable authentication"; Google then signs each
  push with a JWT (RS256) in `Authorization: Bearer`. It is accepted when Google's public keys
  verify the signature, it has not expired, its audience is GMAIL_PUSH_AUDIENCE and it was
  issued for GMAIL_PUSH_SERVICE_ACCOUNT (verified email).
- Shared token: the push endpoint URL carries `?token=<GMAIL_PUSH_TOKEN>`.
When both are configured, both must pass. With neither configured every push is refused.

The body of a push is only a hint ("this mailbox changed, around this history id"). It is
never trusted for content: the worker asks Gmail what changed, from its own saved cursor.
"""

from __future__ import annotations

import base64
import hmac
import json
import threading
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ..config import get_settings
from . import client as g

ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
CERTS_TTL = 3600
LEEWAY = 60

_certs: dict[str, rsa.RSAPublicKey] = {}
_certs_at = 0.0
_lock = threading.Lock()


class PushRejected(Exception):  # noqa: N818
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _load_certs(force: bool = False) -> dict[str, rsa.RSAPublicKey]:
    global _certs, _certs_at
    with _lock:
        if _certs and not force and time.time() - _certs_at < CERTS_TTL:
            return _certs
        s = get_settings()
        status, _, raw = g.transport(
            "GET", s.gmail_oauth_certs_url, {"Accept": "application/json"}, None, 10
        )
        if status != 200:
            raise PushRejected("certs_unavailable")
        keys: dict[str, rsa.RSAPublicKey] = {}
        for k in json.loads(raw).get("keys", []):
            if k.get("kty") != "RSA" or not k.get("kid"):
                continue
            n = int.from_bytes(_unb64(k["n"]), "big")
            e = int.from_bytes(_unb64(k["e"]), "big")
            keys[str(k["kid"])] = rsa.RSAPublicNumbers(e, n).public_key()
        _certs, _certs_at = keys, time.time()
        return _certs


def reset_cache() -> None:
    global _certs, _certs_at
    with _lock:
        _certs, _certs_at = {}, 0.0


def verify_oidc(bearer: str) -> dict[str, Any]:
    s = get_settings()
    try:
        head_s, body_s, sig_s = bearer.split(".")
        head = json.loads(_unb64(head_s))
        claims = json.loads(_unb64(body_s))
        sig = _unb64(sig_s)
    except (ValueError, TypeError):
        raise PushRejected("malformed_token") from None
    if not isinstance(head, dict) or not isinstance(claims, dict) or head.get("alg") != "RS256":
        raise PushRejected("wrong_algorithm")
    kid = str(head.get("kid") or "")
    try:
        key = _load_certs().get(kid) or _load_certs(force=True).get(kid)
    except (g.GmailError, ValueError, KeyError, TypeError):
        raise PushRejected("certs_unavailable") from None
    if key is None:
        raise PushRejected("unknown_key")
    try:
        key.verify(sig, f"{head_s}.{body_s}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise PushRejected("bad_signature") from None
    t = time.time()
    try:
        exp, iat = float(claims.get("exp", 0)), float(claims.get("iat", 0))
    except (TypeError, ValueError):
        raise PushRejected("bad_times") from None
    if exp < t - LEEWAY or iat > t + LEEWAY:
        raise PushRejected("expired")
    if claims.get("iss") not in ISSUERS:
        raise PushRejected("wrong_issuer")
    if claims.get("aud") != s.gmail_push_audience:
        raise PushRejected("wrong_audience")
    if (
        claims.get("email") != s.gmail_push_service_account
        or claims.get("email_verified") is not True
    ):
        raise PushRejected("wrong_identity")
    return claims


def verify_push(authorization: str | None, token: str | None) -> None:
    """Raise PushRejected unless the caller passes every configured check."""
    s = get_settings()
    shared = s.gmail_push_token.get_secret_value()
    oidc = bool(s.gmail_push_audience and s.gmail_push_service_account)
    if not shared and not oidc:
        raise PushRejected("not_configured")
    if shared and not (token and hmac.compare_digest(token.encode(), shared.encode())):
        raise PushRejected("bad_shared_token")
    if oidc:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise PushRejected("missing_token")
        verify_oidc(authorization[7:].strip())


def decode_envelope(body: dict[str, Any]) -> tuple[str, int | None]:
    """(mailbox address, history id) from a Pub/Sub push body; raises ValueError if unusable."""
    msg = body.get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("data"), str):
        raise ValueError("no message data")
    data = json.loads(_unb64(msg["data"]))
    if not isinstance(data, dict) or not isinstance(data.get("emailAddress"), str):
        raise ValueError("no mailbox address")
    try:
        hid: int | None = int(data.get("historyId") or 0) or None
    except (TypeError, ValueError):
        hid = None
    return data["emailAddress"].strip().lower(), hid
