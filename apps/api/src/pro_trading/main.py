"""ASGI app: request id, JSON logs, origin check, error envelope, /api/v1 routers."""

import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import errors
from .bodylimit import BodySizeLimit
from .config import get_settings
from .routes import (
    admin,
    auth,
    catalog,
    customers,
    documents,
    employees,
    health,
    inventory,
    invoices,
    ocr,
    orders,
    payments,
    pricing,
    receiving,
    reports,
    scans,
    suppliers,
    warehouses,
)

log = logging.getLogger("pro_trading.access")
REQUEST_ID = re.compile(r"[^A-Za-z0-9._:-]")


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


# API responses never run scripts or get framed by another site. img-src/style-src let the
# browser's own image and PDF viewers show inline files (documents, invoice PDFs); routes that
# set their own CSP (scan content) keep it.
API_CSP = (
    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'; "
    "base-uri 'none'; form-action 'none'"
)


def _security_headers(resp: Response, rid: str, https: bool) -> None:
    h = resp.headers
    h["X-Request-Id"] = rid
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "same-origin"
    h["X-Frame-Options"] = "SAMEORIGIN"
    h["Cross-Origin-Opener-Policy"] = "same-origin"
    h.setdefault("Content-Security-Policy", API_CSP)
    # Signed-in data must not land in shared/browser caches unless a route opted in.
    h.setdefault("Cache-Control", "no-store")
    if https:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


def create_app() -> FastAPI:
    s = get_settings()
    _setup_logging()
    prod = s.is_production_like
    app = FastAPI(
        title="Pro Trading API",
        version="0.1.0",
        docs_url=None if prod else "/docs",
        redoc_url=None if prod else "/redoc",
        openapi_url=None if prod else "/openapi.json",
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
        # A caller may pass its own id for tracing; keep it short and plain before it is
        # echoed into headers, error bodies and logs.
        rid = REQUEST_ID.sub("", request.headers.get("x-request-id", ""))[:64] or str(uuid.uuid4())
        request.state.request_id = rid
        # Cookie-auth mutations must come from our own origin (CORS is not authentication).
        # Browsers also label every request with Sec-Fetch-Site; refuse cross-site ones even
        # when an old browser or a privacy extension drops the Origin header.
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and (
            (origin is not None and origin != s.frontend_origin)
            or request.headers.get("sec-fetch-site") == "cross-site"
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
        _security_headers(resp, rid, s.cookie_secure)
        return resp

    # Outermost, so oversized bodies are refused before anything reads them.
    app.add_middleware(BodySizeLimit, max_bytes=s.max_request_bytes)

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
        payments.router,
        reports.router,
        employees.router,
        suppliers.router,
    ):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
