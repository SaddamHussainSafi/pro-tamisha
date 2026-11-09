"""Order Connect public surface: the embeddable page and the anonymous form API.

No route here reads the staff session cookie, so a submission can never act with staff rights.
Protection does not rest on Referer or CORS: framing is limited by CSP frame-ancestors, every
submission needs a signed form-load token (with a minimum fill time), a honeypot, per-IP and
per-form limits, a small body cap, a client idempotency token and full server validation."""

import re
import uuid
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ratelimit
from ..config import get_settings
from ..deps import DbDep
from ..errors import ApiError
from ..order_connect import embed, forms, intake
from ..order_connect.models import OrderForm, OrderFormLogo, OrderFormVersion

pages = APIRouter(include_in_schema=False)  # /embed/... (outside /api/v1)
router = APIRouter(prefix="/public", tags=["order-connect-public"])

PUBLIC_ID = r"^[A-Za-z0-9_-]{22,32}$"
GET_LIMIT_PER_HOUR = 600


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def live_form(db: Session, public_id: str) -> OrderForm:
    form = db.scalar(select(OrderForm).where(OrderForm.public_id == public_id))
    if form is None or form.archived_at is not None or form.published_version_id is None:
        raise ApiError(404, "FORM_NOT_FOUND", "This order form is not available")
    if not form.accepting:
        raise ApiError(409, "FORM_CLOSED", "This order form is not taking orders right now")
    return form


def referer_host(request: Request) -> str:
    try:
        return intake.clean_host(urlsplit(request.headers.get("referer", "")).netloc)
    except ValueError:
        return ""


# ------------------------------------------------------------------ pages


@pages.get("/embed/assets/{name}")
def embed_asset(name: str) -> Response:
    if name not in embed.ASSETS:
        raise ApiError(404, "NOT_FOUND", "Not found")
    return embed.asset_response(name)


@pages.get("/embed/preview", response_class=HTMLResponse)
def embed_preview() -> HTMLResponse:
    return embed.preview_page()


@pages.get("/embed/{public_id}", response_class=HTMLResponse)
def embed_page(public_id: str, request: Request, db: DbDep) -> HTMLResponse:
    unavailable = (
        404,
        "This order form is not available",
        "Please contact us to place your order.",
    )
    if not re.match(PUBLIC_ID, public_id):
        return embed.message_page(*unavailable)
    form = db.scalar(select(OrderForm).where(OrderForm.public_id == public_id))
    if form is None or form.archived_at is not None or form.published_version_id is None:
        return embed.message_page(*unavailable)
    if not form.accepting:
        return embed.message_page(
            409,
            "Orders are paused",
            "This order form is not taking orders right now. Please contact us.",
        )
    # Sec-Fetch-Dest tells a framed load from a top-level visit. It is a convenience switch
    # (the form API stays reachable either way); framing itself is governed by the CSP.
    if not form.standalone_link and request.headers.get("sec-fetch-dest") == "document":
        return embed.message_page(
            403,
            "Please order on our website",
            "This order form is only available on our website.",
        )
    ver = db.get(OrderFormVersion, form.published_version_id)
    assert ver is not None
    return embed.form_page(form, ver.title, referer_host(request), embed.allowed_hosts(db, form))


# ------------------------------------------------------------------ public API


@router.get("/order-forms/{public_id}")
def public_form(
    request: Request,
    db: DbDep,
    public_id: str,
    host: str = Query("", max_length=260),
) -> dict[str, Any]:
    """The published form, its catalog slice (no stock, no customer prices) and a signed token."""
    if ratelimit.attempt("oc_get", client_ip(request), 3600) > GET_LIMIT_PER_HOUR:
        raise ApiError(429, "RATE_LIMITED", "Too many requests. Please wait a few minutes.")
    form = live_form(db, public_id)
    ver = db.get(OrderFormVersion, form.published_version_id)
    assert ver is not None
    cfg = forms.public_config(
        db, form, ver, forms.consent_of(forms.settings_row(db, form.company_id))
    )
    cfg["token"] = intake.make_token(form.public_id, ver.id, intake.clean_host(host))
    return cfg


async def small_body(request: Request) -> bytes:
    """The public form takes at most ORDER_CONNECT_MAX_BODY_BYTES (64 KiB by default)."""
    limit = get_settings().order_connect_max_body_bytes
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > limit):
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "The order is too large")
    body = await request.body()
    if len(body) > limit:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "The order is too large")
    return body


@router.post("/order-forms/{public_id}/submissions", status_code=201)
def submit(
    public_id: str,
    request: Request,
    db: DbDep,
    raw: bytes = Depends(small_body),
) -> JSONResponse:
    s = get_settings()
    origin = request.headers.get("origin")
    if origin is not None and origin not in (s.public_origin, s.frontend_origin):
        raise ApiError(403, "ORIGIN_REJECTED", "Origin not allowed")
    try:
        body = intake.PublicSubmissionIn.model_validate_json(raw)
    except ValidationError as e:
        raise ApiError(
            422,
            "VALIDATION",
            "The order could not be read. Reload the page and try again.",
            details=[
                {"loc": [str(x) for x in err["loc"]], "msg": err["msg"]} for err in e.errors()
            ][:20],
        ) from None
    form = live_form(db, public_id)
    ver = db.get(OrderFormVersion, form.published_version_id)
    assert ver is not None

    # A retry of an accepted submission (same client token) gets the same answer, uncounted.
    if (prior := intake.existing(db, form.id, body.client_token)) is not None:
        return JSONResponse(_receipt(prior, ver), 200, headers={"Idempotent-Replay": "true"})

    st = forms.settings_row(db, form.company_id)
    ip = client_ip(request)
    if (
        ratelimit.attempt("oc_ip", f"{form.id}:{intake.ip_hash(ip)}", 3600)
        > st["ip_limit_per_hour"]
    ):
        raise ApiError(429, "RATE_LIMITED", "Too many orders from this connection. Try later.")
    if ratelimit.attempt("oc_form", str(form.id), 3600) > st["form_limit_per_hour"]:
        raise ApiError(429, "RATE_LIMITED", "This form is very busy. Please try again later.")

    token = intake.read_token(body.form_token, form.public_id)
    if token.get("v") != str(form.published_version_id):
        raise ApiError(
            409,
            "FORM_UPDATED",
            "This form was just updated. Please check your order and send it again.",
        )
    sub, created = intake.receive(
        db,
        form,
        body,
        token=token,
        ip=ip,
        user_agent=request.headers.get("user-agent", ""),
        request_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    return JSONResponse(_receipt(sub, ver), 201 if created else 200)


def _receipt(sub: Any, ver: OrderFormVersion) -> dict[str, Any]:
    return {"reference": sub.reference, "message": ver.success_message}


@router.get("/logos/{logo_id}")
def logo(logo_id: uuid.UUID, db: DbDep) -> Response:
    row = db.get(OrderFormLogo, logo_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Not found")
    return Response(
        row.content,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
            "Content-Disposition": 'inline; filename="logo.png"',
            "Content-Security-Policy": "default-src 'none'",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    )
