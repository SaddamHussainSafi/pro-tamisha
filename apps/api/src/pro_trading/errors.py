"""Uniform error envelope: {"error": {"code", "message", "request_id", ...}}.

Every error the API answers uses this shape, so the web app can always say what happened, whether
anything was saved and what to do next (docs/operations/ERROR_CATALOG.md). Messages are written
for the people using Pro Trading: no exception text, SQL, paths or stack traces reach the client.
Every error is logged with its request id, so a reference shown on screen finds the log line.
"""

import logging
from typing import Any

import redis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import exc as sa_exc
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("pro_trading.errors")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        self.status, self.code, self.message, self.extra = status, code, message, extra


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _body(request: Request, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": _rid(request), **extra}}


def _ctx(request: Request, status: int, code: str) -> dict[str, Any]:
    return {
        "ctx": {
            "request_id": _rid(request),
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "code": code,
            "actor_id": getattr(request.state, "actor_id", None),
        }
    }


# Framework errors (unknown route, wrong method, body too large...) in plain words.
HTTP_CODES: dict[int, tuple[str, str]] = {
    400: ("BAD_REQUEST", "The request could not be read. Reload the page and try again."),
    401: ("UNAUTHENTICATED", "Sign in to continue."),
    403: ("FORBIDDEN", "You do not have permission for this action."),
    404: ("NOT_FOUND", "This page or record does not exist."),
    405: ("METHOD_NOT_ALLOWED", "That action is not available here."),
    413: ("PAYLOAD_TOO_LARGE", "The request is too large."),
    415: ("UNSUPPORTED_MEDIA_TYPE", "That type of file or data is not accepted here."),
    429: ("RATE_LIMITED", "Too many requests at once. Wait a moment and try again."),
    503: ("SERVICE_UNAVAILABLE", "This service is not available right now. Try again shortly."),
}

INTERNAL_MESSAGE = (
    "Something unexpected went wrong on the server. Try again in a moment; if it keeps "
    "happening, give support the reference number."
)
DB_UNAVAILABLE_MESSAGE = (
    "The database is not answering right now, so nothing was saved. Try again in a minute."
)
DB_BUSY_MESSAGE = (
    "Someone else was saving the same records at the same moment, so nothing was saved. Try again."
)
DB_CONFLICT_MESSAGE = (
    "This clashes with a record that already exists, so nothing was saved. Reload the page to "
    "see the latest, then try again."
)
REDIS_UNAVAILABLE_MESSAGE = (
    "A background service is not answering right now, so nothing was saved. Try again in a minute."
)
# PostgreSQL: serialization failure, deadlock, lock not available. The transaction was rolled
# back and the same request may simply be sent again.
RETRYABLE_PGCODES = {"40001", "40P01", "55P03"}


def _pgcode(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


def _headers(request: Request, resp: JSONResponse) -> JSONResponse:
    # Exception handlers for bare Exception run outside the request middleware, so the headers
    # that middleware adds are added here too.
    rid = _rid(request)
    if rid:
        resp.headers["X-Request-Id"] = rid
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'self'"
    return resp


def classify(exc: BaseException) -> tuple[int, str, str, dict[str, str]]:
    """(status, code, message, headers) for an exception no route turned into an ApiError."""
    if isinstance(exc, sa_exc.IntegrityError):
        return 409, "CONFLICT", DB_CONFLICT_MESSAGE, {}
    if isinstance(exc, sa_exc.DBAPIError) and _pgcode(exc) in RETRYABLE_PGCODES:
        return 409, "BUSY_RETRY", DB_BUSY_MESSAGE, {}
    if isinstance(
        exc, sa_exc.OperationalError | sa_exc.InterfaceError | sa_exc.DisconnectionError
    ) or isinstance(exc, sa_exc.TimeoutError):  # pool exhausted / server down
        return 503, "DB_UNAVAILABLE", DB_UNAVAILABLE_MESSAGE, {"Retry-After": "30"}
    if isinstance(exc, redis.ConnectionError | redis.TimeoutError):
        return 503, "SERVICE_UNAVAILABLE", REDIS_UNAVAILABLE_MESSAGE, {"Retry-After": "30"}
    return 500, "INTERNAL", INTERNAL_MESSAGE, {}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError) -> JSONResponse:
        # Refusals the app expected (validation, conflicts, permissions) are routine: info.
        level = logging.WARNING if exc.status >= 500 else logging.INFO
        log.log(level, "api error", extra=_ctx(request, exc.status, exc.code))
        return JSONResponse(_body(request, exc.code, exc.message, **exc.extra), exc.status)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Any unexpected error: logged with its traceback and request id, answered with the
        standard envelope and no internal detail (no exception text, SQL or paths)."""
        status, code, message, headers = classify(exc)
        log.error("unhandled error", exc_info=exc, extra=_ctx(request, status, code))
        resp = JSONResponse(_body(request, code, message), status, headers=headers)
        return _headers(request, resp)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Framework errors (unknown route, wrong method, body too large) in the same envelope."""
        code, message = HTTP_CODES.get(
            exc.status_code, ("HTTP_ERROR", "The request could not be completed. Try again.")
        )
        log.info("http error", extra=_ctx(request, exc.status_code, code))
        return JSONResponse(_body(request, code, message), exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # `field` is the name of the input (last part of loc) so the screen can highlight it.
        errs = [
            {
                "loc": e["loc"],
                "field": next((str(p) for p in reversed(e["loc"]) if isinstance(p, str)), None),
                "msg": e["msg"],
            }
            for e in exc.errors()
        ]
        log.info("validation error", extra=_ctx(request, 422, "VALIDATION"))
        return JSONResponse(
            _body(
                request,
                "VALIDATION",
                "Some details are missing or not valid. Check the highlighted fields.",
                fields=errs,
            ),
            422,
        )
