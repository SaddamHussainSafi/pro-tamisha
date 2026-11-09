"""Uniform error envelope: {"error": {"code", "message", "request_id"}}."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger("pro_trading.errors")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        self.status, self.code, self.message, self.extra = status, code, message, extra


def _body(request: Request, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", None),
            **extra,
        }
    }


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(request, exc.code, exc.message, **exc.extra), exc.status)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Any unexpected error: logged with its traceback, answered with the standard envelope
        and no internal detail (no exception text, SQL or paths reach the client)."""
        log.error(
            "unhandled error",
            exc_info=exc,
            extra={"ctx": {"request_id": getattr(request.state, "request_id", None)}},
        )
        resp = JSONResponse(
            _body(request, "INTERNAL", "Something went wrong. Try again or contact support."), 500
        )
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errs = [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(_body(request, "VALIDATION", "Invalid request", fields=errs), 422)
