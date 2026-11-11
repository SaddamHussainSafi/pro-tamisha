"""Route-wide authorization sweep (docs/SECURITY_REVIEW.md). Walks every operation in the
OpenAPI schema, so a new route that forgets authentication, CSRF or a permission check fails
here even if nobody wrote a test for it."""

import re
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from pro_trading.db import get_sessionmaker
from pro_trading.models import Employee
from pro_trading.security import hash_password

from .conftest import PASSWORD

UNSAFE = {"post", "put", "patch", "delete"}

# Reachable without a staff session, by design.
ANONYMOUS_OK = {
    ("post", "/api/v1/auth/login"),  # 401/422 without valid credentials
    ("post", "/api/v1/auth/logout"),  # 204: clears the cookie, there is nothing to revoke
    # Account links: the emailed single-use token is the credential (generic 202 / 400).
    ("post", "/api/v1/auth/password/forgot"),
    ("post", "/api/v1/auth/password/reset"),
    ("post", "/api/v1/auth/invitation/accept"),
    ("post", "/api/v1/auth/token/inspect"),
}
# Scanner bridge routes authenticate the device (Authorization: Device ...), not a session.
BRIDGE = re.compile(r"^/api/v1/scan-bridge/")
# Order Connect public form API: anonymous by design, never reads the staff cookie
# (tests/test_order_connect.py covers its own protections).
PUBLIC = re.compile(r"^/api/v1/public/")
# Gmail Pub/Sub push: called by Google, never with a staff session. It answers 401 unless the
# caller proves it is our subscription (tests/test_gmail.py covers that), so a session cookie
# alone gets 401 here, not the CSRF refusal.
WEBHOOKS = {("post", "/api/v1/gmail/push")}

# A signed-in employee with no role may only use their own account.
SELF_SERVICE = {
    ("get", "/api/v1/auth/session"),
    ("post", "/api/v1/auth/logout"),
    ("post", "/api/v1/auth/mfa/enroll"),
    ("post", "/api/v1/auth/mfa/verify"),
    ("patch", "/api/v1/auth/me"),
    ("post", "/api/v1/auth/password"),
    # Two-step sign-in, my sessions and my sign-in history: only ever the caller's own account.
    ("post", "/api/v1/auth/mfa/enroll/confirm"),
    ("post", "/api/v1/auth/mfa/disable"),
    ("post", "/api/v1/auth/mfa/recovery-codes"),
    ("get", "/api/v1/auth/sessions"),
    ("post", "/api/v1/auth/sessions/{session_id}/revoke"),
    ("post", "/api/v1/auth/sessions/revoke-others"),
    ("get", "/api/v1/auth/activity"),
    # Activity feed: own actions for everyone; colleagues' only with audit.read.
    ("get", "/api/v1/activity"),
    # Notification Center: only ever the caller's own inbox and preferences.
    ("get", "/api/v1/notifications"),
    ("get", "/api/v1/notifications/summary"),
    ("get", "/api/v1/notifications/stream"),
    ("get", "/api/v1/notifications/preferences"),
    ("put", "/api/v1/notifications/preferences"),
    ("post", "/api/v1/notifications/read-all"),
    ("post", "/api/v1/notifications/archive"),
    ("post", "/api/v1/notifications/{notification_id}/read"),
    ("post", "/api/v1/notifications/{notification_id}/unread"),
    ("post", "/api/v1/notifications/{notification_id}/archive"),
    ("post", "/api/v1/notifications/{notification_id}/unarchive"),
}


def operations(app: Any) -> list[tuple[str, str, str]]:
    """(method, template, concrete url) for every documented API operation."""
    out = []
    for path, item in app.openapi()["paths"].items():
        if not path.startswith("/api/v1/") or path.startswith("/api/v1/_probe"):
            continue
        for method, op in item.items():
            if method not in {"get", *UNSAFE}:
                continue
            url = path
            for prm in op.get("parameters", []):
                if prm["in"] != "path":
                    continue
                schema = prm.get("schema", {})
                if schema.get("type") == "integer":
                    value = "1"
                elif schema.get("format") == "uuid":
                    value = str(uuid.uuid4())
                else:
                    value = "demo_key"
                url = url.replace("{" + prm["name"] + "}", value)
            out.append((method, path, url))
    assert len(out) > 100  # the walk itself works
    return out


def call(client: Any, method: str, url: str, headers: dict[str, str] | None = None) -> Any:
    h = {"Idempotency-Key": f"sweep-{uuid.uuid4()}", **(headers or {})}
    if method in UNSAFE:
        return client.request(method.upper(), url, json={}, headers=h)
    return client.request("GET", url, headers=h)


def test_every_route_needs_a_session(api):
    bad = []
    for method, path, url in operations(api.c.app):
        if (method, path) in ANONYMOUS_OK:
            continue
        r = call(api.c, method, url)
        want = (
            {404, 409, 422, 429}
            if PUBLIC.match(path)
            else {401}
            if not BRIDGE.match(path)
            else {401, 403, 404, 409, 422}
        )
        if r.status_code not in want:
            bad.append(f"{method.upper()} {path} -> {r.status_code}")
    assert bad == [], "\n".join(bad)


def test_every_mutation_needs_the_csrf_token(make_api, world):
    owner = make_api()
    owner.login(world.emails["owner"])
    assert owner.enroll_and_verify().status_code == 200
    bad = []
    for method, path, url in operations(owner.c.app):
        if (
            method not in UNSAFE
            or (method, path) in ANONYMOUS_OK
            or BRIDGE.match(path)
            or PUBLIC.match(path)
            or (method, path) in WEBHOOKS
        ):
            continue
        r = call(owner.c, method, url)  # cookie only: no X-CSRF-Token, no Origin
        if r.status_code != 403 or r.json()["error"]["code"] != "CSRF_FAILED":
            bad.append(f"{method.upper()} {path} -> {r.status_code} {r.text[:80]}")
    assert bad == [], "\n".join(bad)


@pytest.fixture
def nobody(make_api, world):
    """An active employee of the company with no role at all."""
    email = f"norole.{uuid.uuid4().hex[:6]}@example.test"
    with get_sessionmaker()() as db, db.begin():
        db.add(
            Employee(
                company_id=world.company_id,
                email=email,
                display_name="No role",
                password_hash=hash_password(PASSWORD),
            )
        )
    a = make_api()
    assert a.login(email).status_code == 200
    yield a
    with get_sessionmaker()() as db, db.begin():
        e = db.scalar(select(Employee).where(Employee.email == email))
        if e is not None:
            e.active = False


def test_an_employee_without_a_role_can_reach_only_their_own_account(nobody):
    leaks = []
    for method, path, url in operations(nobody.c.app):
        if (method, path) in SELF_SERVICE or BRIDGE.match(path) or PUBLIC.match(path):
            continue
        r = call(nobody.c, method, url, headers=nobody._h())
        # 403 = refused; 404 = the (random) id is not theirs; 422 = refused before any work.
        if r.status_code < 400 or r.status_code >= 500:
            leaks.append(f"{method.upper()} {path} -> {r.status_code} {r.text[:80]}")
    assert leaks == [], "\n".join(leaks)
