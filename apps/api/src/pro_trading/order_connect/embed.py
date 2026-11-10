"""The page a website frames: plain HTML + one script + one stylesheet, all same-origin.

Framing is enforced by CSP `frame-ancestors` built from the allowed websites (HTTPS only; plain
http://localhost and 127.0.0.1 are added outside staging/production for local testing). The page
carries no secret: the form token it later fetches is a signed timestamp, not a credential."""

import hashlib
import html
import json
from pathlib import Path

from fastapi.responses import HTMLResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from .models import OrderForm, OrderFormAllowedDomain

STATIC = Path(__file__).parent / "static"
ASSETS = {"embed.js": "text/javascript; charset=utf-8", "embed.css": "text/css; charset=utf-8"}


def asset(name: str) -> tuple[bytes, str]:
    """Read per request (a few KB): an edited asset is served at once with a new ?v= hash."""
    data = (STATIC / name).read_bytes()
    return data, hashlib.sha256(data).hexdigest()[:12]


def page_csp(ancestors: str) -> str:
    return (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'none'; base-uri 'none'; "
        f"frame-ancestors {ancestors}"
    )


def allowed_hosts(db: Session, form: OrderForm) -> list[str]:
    return sorted(
        db.scalars(
            select(OrderFormAllowedDomain.host).where(
                OrderFormAllowedDomain.company_id == form.company_id,
                or_(
                    OrderFormAllowedDomain.form_id.is_(None),
                    OrderFormAllowedDomain.form_id == form.id,
                ),
            )
        ).all()
    )


def frame_ancestors(hosts: list[str]) -> str:
    sources = ["'self'", *(f"https://{h}" for h in hosts)]
    if not get_settings().is_production_like:
        sources += ["http://localhost:*", "http://127.0.0.1:*"]
    return " ".join(sources)


def _headers(csp: str) -> dict[str, str]:
    return {
        "Content-Security-Policy": csp,
        "Cache-Control": "no-store",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    }


def render(
    *,
    title: str,
    public_id: str = "",
    host: str = "",
    mode: str = "live",
    staff_origin: str = "",
) -> str:
    js_v, css_v = asset("embed.js")[1], asset("embed.css")[1]
    e = html.escape
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="stylesheet" href="/embed/assets/embed.css?v={css_v}">
</head>
<body>
<main id="oc-app" data-public-id="{e(public_id)}" data-host="{e(host)}" data-mode="{e(mode)}"
  data-staff-origin="{e(staff_origin)}" aria-busy="true">
  <div class="oc-state" role="status" aria-live="polite">
    <span class="oc-spinner" aria-hidden="true"></span><span>Loading products…</span>
  </div>
</main>
<noscript><p class="oc-state">This order form needs JavaScript. Please turn it on, or contact
us to order.</p></noscript>
<script src="/embed/assets/embed.js?v={js_v}" defer></script>
</body>
</html>
"""


def form_page(form: OrderForm, title: str, host: str, hosts: list[str]) -> HTMLResponse:
    return HTMLResponse(
        render(title=title, public_id=form.public_id, host=host),
        headers=_headers(page_csp(frame_ancestors(hosts))),
    )


def preview_page() -> HTMLResponse:
    """Builder preview: renders a draft sent by the staff app over postMessage. Only the staff
    app's origin may frame it, and the script accepts messages from that origin only."""
    s = get_settings()
    return HTMLResponse(
        render(title="Order form preview", mode="preview", staff_origin=s.frontend_origin),
        headers=_headers(page_csp(f"'self' {s.frontend_origin}")),
    )


def message_page(status: int, heading: str, text: str) -> HTMLResponse:
    """A plain, script-free notice (unknown, closed or link-disabled form). It may be framed
    anywhere: it has no actions, so framing it can't trick anyone into one."""
    e = html.escape
    css_v = asset("embed.css")[1]
    body = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(heading)}</title><link rel="stylesheet" href="/embed/assets/embed.css?v={css_v}"></head>
<body><main class="oc-shell"><div class="oc-state oc-state-error" role="alert">
<h1>{e(heading)}</h1><p>{e(text)}</p></div></main></body></html>
"""
    return HTMLResponse(body, status, headers=_headers(page_csp("*")))


def asset_response(name: str) -> Response:
    data, _ = asset(name)
    return Response(
        data,
        media_type=ASSETS[name],
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Security-Policy": "default-src 'none'",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    )


# Optional companion of the iframe on the website (an Elementor "HTML" widget runs it). It only
# exchanges numbers with the form: the form reports its height (the frame grows, so the page
# scrolls instead of a box inside it) and the page reports which part of the frame is on screen
# (so the cart can stay in view). Messages are checked against the form's own origin and frame.
RESIZE_JS = (
    "<script>(function(){var f=document.getElementById(%(id)s),o=%(origin)s;if(!f)return;"
    "function v(){if(!f.contentWindow)return;var r=f.getBoundingClientRect();"
    'f.contentWindow.postMessage({type:"oc-viewport",top:Math.round(-r.top),'
    "height:window.innerHeight},o)}"
    'window.addEventListener("message",function(e){if(e.origin!==o||e.source!==f.contentWindow'
    '||!e.data)return;if(e.data.type==="oc-height"){var h=Number(e.data.height)||0;'
    'f.style.height=Math.max(400,Math.min(30000,h))+"px";v()}'
    'else if(e.data.type==="oc-top"){var t=f.getBoundingClientRect().top;'
    'if(t<0)window.scrollBy({top:t-16,behavior:"smooth"})}});'
    'window.addEventListener("scroll",v,{passive:true});window.addEventListener("resize",v);'
    'f.addEventListener("load",v)})();</script>'
)


def resize_script(frame_id: str, origin: str) -> str:
    return RESIZE_JS % {"id": json.dumps(frame_id), "origin": json.dumps(origin)}
