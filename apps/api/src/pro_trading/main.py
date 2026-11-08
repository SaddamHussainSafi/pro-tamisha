"""ASGI app: request id, JSON logs, origin check, error envelope, /api/v1 routers."""

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import errors
from .config import get_settings
from .routes import (
    admin,
    auth,
    catalog,
    customers,
    documents,
    health,
    inventory,
    invoices,
    ocr,
    orders,
    pricing,
    receiving,
    scans,
    warehouses,
)

log = logging.getLogger("pro_trading.access")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {"level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        base.update(getattr(record, "ctx", {}))
        return json.dumps(base, default=str)


def _setup_logging() -> None:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root = logging.getLogger("pro_trading")
    root.handlers[:] = [h]
    root.setLevel(logging.INFO)


def create_app() -> FastAPI:
    s = get_settings()
    _setup_logging()
    app = FastAPI(
        title="Pro Trading API", version="0.1.0", docs_url=None if s.is_production_like else "/docs"
    )
    errors.install(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[s.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "X-Request-Id"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = rid[:64]
        # Cookie-auth mutations must come from our own origin (CORS is not authentication).
        origin = request.headers.get("origin")
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and origin is not None
            and origin != s.frontend_origin
        ):
            resp: Response = JSONResponse(
                {
                    "error": {
                        "code": "ORIGIN_REJECTED",
                        "message": "Origin not allowed",
                        "request_id": rid,
                    }
                },
                403,
            )
        else:
            t0 = time.perf_counter()
            resp = await call_next(request)
            log.info(
                "request",
                extra={
                    "ctx": {
                        "request_id": rid,
                        "method": request.method,
                        "path": request.url.path,
                        "status": resp.status_code,
                        "ms": round((time.perf_counter() - t0) * 1000, 1),
                        "actor_id": getattr(request.state, "actor_id", None),
                    }
                },
            )
        resp.headers["X-Request-Id"] = rid
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    app.include_router(health.router)  # probes at /health/* (no auth, outside API version)
    for r in (
        auth.router,
        warehouses.router,
        admin.router,
        catalog.router,
        customers.router,
        pricing.router,
        inventory.router,
        orders.router,
        invoices.router,
        scans.router,
        ocr.router,
        receiving.router,
        documents.router,
    ):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
