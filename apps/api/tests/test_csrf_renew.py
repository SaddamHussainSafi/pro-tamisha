"""A browser tab whose CSRF token is missing or stale gets a fresh one for its session.

Owner report 2026-11-10: Scan Center upload said "Missing or invalid CSRF token". The token lives
in per-tab sessionStorage; signing in again (another tab, the dev auto sign-in firing twice)
starts a new session behind the same cookie, so a tab can hold another session's token."""

from pro_trading.config import get_settings

A = "/api/v1/auth"


def origin() -> dict[str, str]:
    return {"Origin": get_settings().frontend_origin}


def test_stale_tab_gets_a_fresh_token_for_the_current_session(api, world):
    api.login(world.emails["sales"])
    stale = api.csrf
    api.login(world.emails["sales"])  # signed in again: new session behind the same cookie
    other_tab = api.csrf
    api.csrf = stale
    r = api.post(f"{A}/logout")  # any mutation
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"

    r = api.c.post(f"{A}/csrf", headers=origin())
    assert r.status_code == 200, r.text
    fresh = r.json()["csrf_token"]
    assert fresh not in (stale, other_tab)
    api.csrf = other_tab  # the token it replaced no longer works
    assert api.post(f"{A}/logout").status_code == 403
    api.csrf = fresh
    assert api.post(f"{A}/logout").status_code == 204


def test_renewal_needs_our_origin_and_a_live_session(api, world):
    r = api.c.post(f"{A}/csrf", headers=origin())
    assert r.status_code == 401  # nobody signed in
    api.login(world.emails["sales"])
    r = api.c.post(f"{A}/csrf")  # no Origin header: not provably our page
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"
    r = api.c.post(f"{A}/csrf", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] in ("ORIGIN_REJECTED", "CSRF_FAILED")
    assert "csrf_token" not in r.text
    assert api.post(f"{A}/logout").status_code == 204  # the real token still works
    r = api.c.post(f"{A}/csrf", headers=origin())
    assert r.status_code == 401  # a signed-out session can't be revived
