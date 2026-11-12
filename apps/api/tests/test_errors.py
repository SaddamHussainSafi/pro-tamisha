"""Error envelope, unexpected-error handling and request ids (docs/operations/ERROR_CATALOG.md)."""

import json
import logging
from typing import Any

import pytest
import redis
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import exc as sa_exc

from pro_trading import errors
from pro_trading.errors import ApiError
from pro_trading.main import JsonFormatter, create_app


class _PgOrigError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("db said no")
        self.sqlstate = sqlstate


def _app_raising(exc: BaseException) -> TestClient:
    app = create_app()
    r = APIRouter()

    @r.post("/api/v1/_raise")
    def _raise() -> None:
        raise exc

    app.include_router(r)
    return TestClient(app, raise_server_exceptions=False)


def _envelope(resp: Any) -> dict[str, Any]:
    body = resp.json()
    assert set(body) == {"error"}
    err = body["error"]
    assert err["code"] and err["message"] and err["request_id"]
    assert resp.headers["x-request-id"] == err["request_id"]
    return err


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (RuntimeError("password=hunter2 /Users/x/secret.py"), 500, "INTERNAL"),
        (sa_exc.OperationalError("SELECT 1", {}, Exception("conn refused")), 503, "DB_UNAVAILABLE"),
        (sa_exc.TimeoutError("QueuePool limit reached"), 503, "DB_UNAVAILABLE"),
        (sa_exc.OperationalError("UPDATE x", {}, _PgOrigError("40P01")), 409, "BUSY_RETRY"),
        (sa_exc.DBAPIError("UPDATE x", {}, _PgOrigError("40001")), 409, "BUSY_RETRY"),
        (sa_exc.IntegrityError("INSERT x", {}, Exception("dup key")), 409, "CONFLICT"),
        (redis.ConnectionError("redis down"), 503, "SERVICE_UNAVAILABLE"),
    ],
)
def test_unexpected_errors_are_classified_and_hide_details(exc, status, code):
    resp = _app_raising(exc).post("/api/v1/_raise", headers={"X-Request-Id": "ref-42"})
    assert resp.status_code == status
    err = _envelope(resp)
    assert err["code"] == code and err["request_id"] == "ref-42"
    for secret in ("hunter2", "/Users/", "SELECT", "INSERT", "conn refused", "dup key", "Pool"):
        assert secret not in resp.text
    if status == 503:
        assert resp.headers["retry-after"] == "30"
    # plain words: says whether anything was saved
    assert "saved" in err["message"] or code == "INTERNAL"


def test_unexpected_error_is_logged_with_request_id_and_traceback(caplog):
    with caplog.at_level(logging.ERROR, logger="pro_trading.errors"):
        _app_raising(RuntimeError("kaboom")).post(
            "/api/v1/_raise", headers={"X-Request-Id": "trace-abc"}
        )
    rec = next(r for r in caplog.records if r.getMessage() == "unhandled error")
    assert rec.ctx["request_id"] == "trace-abc" and rec.ctx["code"] == "INTERNAL"
    line = json.loads(JsonFormatter().format(rec))
    assert line["request_id"] == "trace-abc" and "kaboom" in line["exc"]


def test_api_errors_are_logged_with_request_id(caplog):
    with caplog.at_level(logging.INFO, logger="pro_trading.errors"):
        resp = _app_raising(ApiError(409, "VERSION_CONFLICT", "Changed")).post(
            "/api/v1/_raise", headers={"X-Request-Id": "rid-7"}
        )
    assert resp.status_code == 409 and _envelope(resp)["code"] == "VERSION_CONFLICT"
    rec = next(r for r in caplog.records if r.getMessage() == "api error")
    assert rec.ctx == {**rec.ctx, "request_id": "rid-7", "status": 409, "code": "VERSION_CONFLICT"}


def test_api_error_extra_fields_stay_in_the_envelope():
    resp = _app_raising(ApiError(422, "CSV_INVALID", "2 row errors", error_count=2)).post(
        "/api/v1/_raise"
    )
    err = _envelope(resp)
    assert err["error_count"] == 2 and err["message"] == "2 row errors"


def test_framework_errors_use_plain_words(api):
    r = api.get("/api/v1/no-such-thing")
    assert r.status_code == 404
    err = _envelope(r)
    assert err["code"] == "NOT_FOUND" and err["message"] != "Not Found"
    r = api.c.put("/health/live")
    assert r.status_code == 405 and _envelope(r)["code"] == "METHOD_NOT_ALLOWED"


def test_validation_errors_name_the_fields(api):
    r = api.post("/api/v1/auth/login", json={"email": "x@example.test"})
    assert r.status_code == 422
    err = _envelope(r)
    assert err["code"] == "VALIDATION" and "highlighted" in err["message"]
    assert any(f["field"] == "password" for f in err["fields"])


def test_too_large_bodies_get_a_request_id(api):
    r = api.c.post(
        "/api/v1/auth/login",
        content=b"x" * (10 * 1024 * 1024),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    err = _envelope(r)
    assert err["code"] == "PAYLOAD_TOO_LARGE" and "Nothing was saved" in err["message"]


def test_every_api_error_code_is_plain_upper_snake():
    import ast
    from pathlib import Path

    src = Path(errors.__file__).parent
    bad = []
    for p in src.rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "ApiError":
                if len(n.args) > 2 and isinstance(n.args[1], ast.Constant):
                    code = n.args[1].value
                    if not (isinstance(code, str) and code.isupper() and " " not in code):
                        bad.append(f"{p.name}:{n.lineno}")
    assert not bad


# ------------------------------------------------------------------ screen crash reports


def test_screen_crash_reports_are_logged_for_signed_in_staff(api, world, caplog):
    body = {"reference": "web-1a2b3c", "kind": "render", "path": "/orders", "name": "TypeError"}
    assert api.post("/api/v1/client-errors", json=body).status_code == 401
    api.login(world.emails["sales"])
    with caplog.at_level(logging.ERROR, logger="pro_trading.errors"):
        r = api.post("/api/v1/client-errors", json={**body, "message": "x is undefined"})
    assert r.status_code == 204
    rec = next(r for r in caplog.records if r.getMessage() == "client error")
    assert rec.ctx["reference"] == "web-1a2b3c" and rec.ctx["screen"] == "/orders"
    bad = api.post("/api/v1/client-errors", json={**body, "reference": "<script>"})
    assert bad.status_code == 422
