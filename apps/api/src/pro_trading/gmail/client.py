"""Google calls for the Gmail Inbox: OAuth (authorization-code + PKCE, offline access), token
refresh and the read-only Gmail REST endpoints. Standard library only.

Every request goes through `transport`, which tests replace: no test can reach Google.
Tokens and the client secret are only ever placed in a request header or form body; they are
never logged and never put in an exception message.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import random
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ..config import DEV_GMAIL_KEY, GMAIL_READONLY_SCOPE, get_settings

log = logging.getLogger("pro_trading.gmail")

STATE_TTL_SECONDS = 600
# Refresh a little early so a call never starts with a token about to lapse.
ACCESS_SKEW = timedelta(seconds=60)


class GmailError(Exception):
    """A failed Google call. `transient` = worth trying again later (rate limit, 5xx, network)."""

    def __init__(self, code: str, detail: str, *, transient: bool = False, status: int = 0):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.transient, self.status = code, detail, transient, status


class ReauthRequired(GmailError):  # noqa: N818 - reads as a state, raised like an error
    """Google no longer accepts the stored refresh token (revoked, expired, password changed)."""

    def __init__(self, detail: str = "Google no longer accepts the saved sign-in") -> None:
        super().__init__("GMAIL_REAUTH_REQUIRED", detail, transient=False, status=400)


class HistoryExpired(GmailError):  # noqa: N818
    """The saved history cursor is older than Gmail keeps: a full resync is needed."""

    def __init__(self) -> None:
        super().__init__("GMAIL_HISTORY_EXPIRED", "history cursor is too old", status=404)


# ------------------------------------------------------------------ network (replaced in tests)


def transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: int
) -> tuple[int, dict[str, str], bytes]:
    """One HTTPS request; returns (status, headers, body). HTTP errors are returned, not raised."""
    if not url.startswith("https://"):
        raise GmailError("GMAIL_NOT_CONFIGURED", "Google endpoints must be https")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        hdrs = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
        return e.code, hdrs, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise GmailError(
            "GMAIL_UNREACHABLE", f"could not reach Google ({type(e).__name__})", transient=True
        ) from None


def sleep(seconds: float) -> None:  # replaced in tests
    time.sleep(seconds)


def _json(raw: bytes) -> dict[str, Any]:
    try:
        v = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _reason(data: dict[str, Any]) -> str:
    """Google's machine reason for an error body (never the token, never the request)."""
    err = data.get("error")
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        for e in err.get("errors") or []:
            if isinstance(e, dict) and e.get("reason"):
                return str(e["reason"])
        return str(err.get("status") or err.get("message") or "")[:120]
    return ""


RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "RESOURCE_EXHAUSTED"}


def _retry_after(headers: dict[str, str], attempt: int) -> float:
    try:
        return min(60.0, max(0.0, float(headers.get("retry-after", ""))))
    except ValueError:
        return min(32.0, 2.0**attempt) + random.uniform(0, 0.5)  # noqa: S311 - jitter


# ------------------------------------------------------------------ token encryption


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().gmail_token_key
    try:
        return Fernet(key)
    except ValueError:
        if get_settings().is_production_like or key != DEV_GMAIL_KEY:
            raise RuntimeError("GMAIL_TOKEN_KEY must be a valid Fernet key") from None
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def seal(secret: str) -> bytes:
    return _fernet().encrypt(secret.encode())


def unseal(blob: bytes) -> str:
    try:
        return _fernet().decrypt(blob).decode()
    except InvalidToken:
        raise GmailError(
            "GMAIL_TOKEN_UNREADABLE", "the stored sign-in cannot be decrypted with this key"
        ) from None


# ------------------------------------------------------------------ OAuth


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def new_state(employee_id: str, session_id: str) -> tuple[str, str]:
    """(state, code_challenge). The state is an encrypted, 10-minute note to ourselves: who
    started the connection and the PKCE verifier. Nothing has to be stored for it."""
    verifier = _b64url(secrets.token_bytes(48))
    payload = {"e": employee_id, "s": session_id, "v": verifier, "n": secrets.token_urlsafe(8)}
    state = _fernet().encrypt(json.dumps(payload).encode()).decode()
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return state, challenge


def read_state(state: str, employee_id: str, session_id: str) -> str:
    """The PKCE verifier, if this state was issued to this signed-in session and is fresh."""
    bad = GmailError("GMAIL_STATE_INVALID", "This connection attempt expired; start again")
    try:
        raw = _fernet().decrypt(state.encode(), ttl=STATE_TTL_SECONDS)
        data = json.loads(raw)
    except (InvalidToken, ValueError):
        raise bad from None
    if not isinstance(data, dict) or data.get("e") != employee_id or data.get("s") != session_id:
        raise bad
    return str(data.get("v", ""))


def authorization_url(state: str, challenge: str) -> str:
    s = get_settings()
    q = {
        "client_id": s.gmail_client_id,
        "redirect_uri": s.gmail_redirect,
        "response_type": "code",
        "scope": GMAIL_READONLY_SCOPE,
        "access_type": "offline",  # a refresh token, so the worker runs with nobody signed in
        "prompt": "consent",  # Google only returns a refresh token on a fresh consent
        "include_granted_scopes": "false",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if s.gmail_allowed_address:
        q["login_hint"] = s.gmail_allowed_address
    return f"{s.gmail_oauth_auth_url}?{urllib.parse.urlencode(q)}"


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    expires_at: datetime
    refresh_token: str | None
    scopes: str


def _token_request(form: dict[str, str]) -> TokenSet:
    s = get_settings()
    if not s.gmail_configured:
        raise GmailError("GMAIL_NOT_CONFIGURED", "Gmail is not set up on this server")
    body = urllib.parse.urlencode(
        {
            **form,
            "client_id": s.gmail_client_id,
            "client_secret": s.gmail_client_secret.get_secret_value(),
        }
    ).encode()
    status, _, raw = transport(
        "POST",
        s.gmail_oauth_token_url,
        {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        body,
        s.gmail_timeout_seconds,
    )
    data = _json(raw)
    if status == 200 and data.get("access_token"):
        try:
            ttl = int(data.get("expires_in") or 3600)
        except (TypeError, ValueError):
            ttl = 3600
        return TokenSet(
            access_token=str(data["access_token"]),
            expires_at=datetime.now(UTC) + timedelta(seconds=max(60, ttl)),
            refresh_token=str(data["refresh_token"]) if data.get("refresh_token") else None,
            scopes=str(data.get("scope") or ""),
        )
    reason = _reason(data)
    if reason == "invalid_grant":
        raise ReauthRequired()
    if status == 429 or status >= 500:
        raise GmailError("GMAIL_OAUTH_FAILED", f"Google answered {status}", transient=True)
    raise GmailError("GMAIL_OAUTH_FAILED", f"Google refused ({reason or status})", status=status)


def exchange_code(code: str, verifier: str) -> TokenSet:
    return _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": get_settings().gmail_redirect,
        }
    )


def refresh(refresh_token: str) -> TokenSet:
    return _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})


def revoke(token: str) -> bool:
    """Withdraw our access at Google (best effort; the local tokens are wiped either way)."""
    s = get_settings()
    try:
        status, _, _ = transport(
            "POST",
            s.gmail_oauth_revoke_url,
            {"Content-Type": "application/x-www-form-urlencoded"},
            urllib.parse.urlencode({"token": token}).encode(),
            s.gmail_timeout_seconds,
        )
    except GmailError:
        return False
    return status == 200


# ------------------------------------------------------------------ Gmail REST


class Mailbox:
    """Read-only Gmail calls for one access token. `on_unauthorized` (if given) returns a fresh
    access token once when Google answers 401, so a token that lapsed mid-sync is replaced."""

    def __init__(self, access_token: str, on_unauthorized: Any = None) -> None:
        self._token = access_token
        self._renew = on_unauthorized

    def _call(
        self, method: str, path: str, query: list[tuple[str, str]] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        s = get_settings()
        url = f"{s.gmail_api_base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        payload = json.dumps(body).encode() if body is not None else None
        renewed = False
        attempt = 0
        while True:
            headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            try:
                status, hdrs, raw = transport(
                    method, url, headers, payload, s.gmail_timeout_seconds
                )
            except GmailError as e:
                if not e.transient or attempt >= s.gmail_max_retries:
                    raise
                attempt += 1
                sleep(_retry_after({}, attempt))
                continue
            if 200 <= status < 300:
                return _json(raw)
            data = _json(raw)
            reason = _reason(data)
            if status == 401 and self._renew is not None and not renewed:
                self._token = self._renew()
                renewed = True
                continue
            if status == 401:
                raise ReauthRequired("Google refused the saved sign-in")
            limited = status == 429 or (status == 403 and reason in RATE_REASONS)
            if limited or status >= 500:
                if attempt >= s.gmail_max_retries:
                    raise GmailError(
                        "GMAIL_RATE_LIMITED" if limited else "GMAIL_UNAVAILABLE",
                        f"Google answered {status} after {attempt + 1} tries",
                        transient=True,
                        status=status,
                    )
                attempt += 1
                sleep(_retry_after(hdrs, attempt))
                continue
            if status == 404:
                raise GmailError("GMAIL_NOT_FOUND", "not found in the mailbox", status=404)
            if status == 403:
                raise GmailError(
                    "GMAIL_FORBIDDEN", f"Google refused ({reason or 'forbidden'})", status=403
                )
            raise GmailError("GMAIL_ERROR", f"Google answered {status} ({reason})", status=status)

    def profile(self) -> dict[str, Any]:
        return self._call("GET", "/users/me/profile")

    def list_messages(self, page_token: str | None, limit: int, q: str = "") -> dict[str, Any]:
        query = [("maxResults", str(limit))]
        if page_token:
            query.append(("pageToken", page_token))
        if q:
            query.append(("q", q))
        return self._call("GET", "/users/me/messages", query)

    def get_message(self, message_id: str) -> dict[str, Any]:
        mid = urllib.parse.quote(message_id, safe="")
        return self._call("GET", f"/users/me/messages/{mid}", [("format", "full")])

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        mid = urllib.parse.quote(message_id, safe="")
        aid = urllib.parse.quote(attachment_id, safe="")
        data = self._call("GET", f"/users/me/messages/{mid}/attachments/{aid}")
        return b64url_decode(str(data.get("data") or ""))

    def history(self, start_history_id: int, page_token: str | None) -> dict[str, Any]:
        query = [
            ("startHistoryId", str(start_history_id)),
            ("maxResults", "500"),
            ("historyTypes", "messageAdded"),
            ("historyTypes", "messageDeleted"),
            ("historyTypes", "labelAdded"),
            ("historyTypes", "labelRemoved"),
        ]
        if page_token:
            query.append(("pageToken", page_token))
        try:
            return self._call("GET", "/users/me/history", query)
        except GmailError as e:
            if e.status == 404:
                raise HistoryExpired() from None
            raise

    def watch(self, topic: str) -> dict[str, Any]:
        return self._call("POST", "/users/me/watch", body={"topicName": topic})

    def stop(self) -> None:
        self._call("POST", "/users/me/stop")


def b64url_decode(data: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (ValueError, TypeError):
        return b""
