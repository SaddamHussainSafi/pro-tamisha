"""Order Connect staff API: forms, publishing, embedding, settings, the submission inbox,
review and conversion into DRAFT sales orders (never invoices, stock movements or
reservations: conversion only calls the same insert as the order form)."""

import html
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from .. import audit, idempotency
from ..config import get_settings
from ..db import get_sessionmaker
from ..deps import DbDep, Principal, PrincipalDep, authorize
from ..errors import ApiError
from ..models import Customer, Employee, SalesOrder, Warehouse
from ..notifications import Notification, NotificationRecipient, mark_changed
from ..order_connect import embed, forms, intake, review
from ..order_connect.models import (
    OrderConnectSettings,
    OrderForm,
    OrderFormAllowedDomain,
    OrderFormField,
    OrderFormLogo,
    OrderFormSubmission,
    OrderFormSubmissionEvent,
    OrderFormSubmissionItem,
    OrderFormVersion,
)
from ..order_connect.schemas import (
    ConvertIn,
    DomainIn,
    DraftIn,
    EraseIn,
    FormIn,
    FormPatch,
    NoteIn,
    PublishIn,
    SettingsIn,
    StatusIn,
)
from ..schemas import OrderIn, OrderLineIn
from . import customers as customer_routes
from . import orders as order_routes
from ._common import esc, mutate

router = APIRouter(prefix="/order-connect", tags=["order-connect"])
Result = tuple[dict[str, Any], str, dict[str, Any]]
OPEN = ("NEW", "REVIEWING", "APPROVED")
TARGET = "order_form_submission"


def iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


# ------------------------------------------------------------------ lookups


def get_form(db: Session, p: Principal, form_id: uuid.UUID, lock: bool = False) -> OrderForm:
    f = db.get(OrderForm, form_id, with_for_update=lock, populate_existing=lock)
    if f is None or f.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Form not found")
    return f


def get_sub(db: Session, p: Principal, sid: uuid.UUID, lock: bool = False) -> OrderFormSubmission:
    s = db.get(OrderFormSubmission, sid, with_for_update=lock, populate_existing=lock)
    if s is None or s.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Submission not found")
    return s


def check_version(current: int, sent: int, what: str) -> None:
    if current != sent:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            f"{what} changed since you loaded it; reload",
            current_version=current,
        )


def warehouse_ok(db: Session, p: Principal, wid: uuid.UUID | None) -> None:
    if wid is None:
        return
    wh = db.get(Warehouse, wid)
    if wh is None or wh.company_id != p.company_id:
        raise ApiError(422, "INVALID_WAREHOUSE", "Warehouse not found")


# ------------------------------------------------------------------ read models


def fields_out(db: Session, version_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        {
            "key": f.key,
            "kind": f.kind,
            "label": f.label,
            "help_text": f.help_text,
            "required": f.required,
            "options": list(f.options),
        }
        for f in forms.fields_of(db, version_id)
    ]


def content_out(db: Session, v: OrderFormVersion) -> dict[str, Any]:
    return {
        "version_id": str(v.id),
        "version_no": v.version_no,
        "status": v.status,
        "revision": v.revision if v.status == "DRAFT" else 0,
        "title": v.title,
        "intro": v.intro,
        "success_message": v.success_message,
        "theme": {**forms.DEFAULT_THEME, **(v.theme or {})},
        "logo_id": str(v.logo_id) if v.logo_id else None,
        "logo_url": f"/api/v1/public/logos/{v.logo_id}" if v.logo_id else None,
        "show_prices": v.show_prices,
        "product_scope": v.product_scope,
        "scope_ids": list(v.scope_ids),
        "fields": fields_out(db, v.id),
        "updated_at": iso(v.updated_at),
        "published_at": iso(v.published_at),
    }


def drafts_of(db: Session, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    return set(
        db.scalars(
            select(OrderFormVersion.form_id).where(
                OrderFormVersion.form_id.in_(ids), OrderFormVersion.status == "DRAFT"
            )
        ).all()
    )


def form_out(db: Session, f: OrderForm, has_draft: bool | None = None) -> dict[str, Any]:
    if has_draft is None:
        has_draft = f.id in drafts_of(db, [f.id])
    counts = dict(
        db.execute(
            select(OrderFormSubmission.status, func.count())
            .where(OrderFormSubmission.form_id == f.id)
            .group_by(OrderFormSubmission.status)
        ).all()
    )
    pub = db.get(OrderFormVersion, f.published_version_id) if f.published_version_id else None
    return {
        "id": str(f.id),
        "public_id": f.public_id,
        "name": f.name,
        "accepting": f.accepting,
        "standalone_link": f.standalone_link,
        "default_warehouse_id": str(f.default_warehouse_id) if f.default_warehouse_id else None,
        "archived": f.archived_at is not None,
        "published": pub is not None,
        "published_version_no": pub.version_no if pub else None,
        "published_at": iso(pub.published_at) if pub else None,
        "unpublished_changes": has_draft,
        "submissions": sum(counts.values()),
        "open_submissions": sum(counts.get(s, 0) for s in OPEN),
        "version": f.version,
        "created_at": iso(f.created_at),
        "updated_at": iso(f.updated_at),
    }


def editing_version(db: Session, f: OrderForm) -> OrderFormVersion:
    draft = db.scalar(
        select(OrderFormVersion).where(
            OrderFormVersion.form_id == f.id, OrderFormVersion.status == "DRAFT"
        )
    )
    if draft is not None:
        return draft
    pub = db.get(OrderFormVersion, f.published_version_id)
    assert pub is not None
    return pub


def names(db: Session, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    real = {i for i in ids if i}
    if not real:
        return {}
    return dict(
        db.execute(select(Employee.id, Employee.display_name).where(Employee.id.in_(real))).all()
    )


def unread_ids(db: Session, p: Principal, ids: list[uuid.UUID]) -> set[str]:
    if not ids:
        return set()
    return {
        t
        for t in db.scalars(
            select(Notification.target_id)
            .join(NotificationRecipient, NotificationRecipient.notification_id == Notification.id)
            .where(
                NotificationRecipient.employee_id == p.employee.id,
                NotificationRecipient.read_at.is_(None),
                Notification.target_type == TARGET,
                Notification.target_id.in_([str(i) for i in ids]),
            )
        ).all()
        if t
    }


def summary_out(
    s: OrderFormSubmission, form_name: str, n_items: int, unread: bool
) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "reference": s.reference,
        "status": s.status,
        "request_type": intake.request_type(s),
        "form_id": str(s.form_id),
        "form_name": form_name,
        "customer_name": s.customer_name,
        "business_name": s.business_name,
        "email": s.email,
        "phone": s.phone,
        "requested_date": s.requested_date.isoformat() if s.requested_date else None,
        "item_count": n_items,
        "possible_duplicate": s.possible_duplicate_of is not None,
        "spam_reasons": list(s.spam_reasons),
        "sales_order_id": str(s.sales_order_id) if s.sales_order_id else None,
        "erased": s.erased_at is not None,
        "unread": unread,
        "version": s.version,
        "created_at": iso(s.created_at),
    }


def detail_out(db: Session, p: Principal, s: OrderFormSubmission) -> dict[str, Any]:
    form = db.get(OrderForm, s.form_id)
    ver = db.get(OrderFormVersion, s.version_id)
    assert form is not None and ver is not None
    items = db.scalars(
        select(OrderFormSubmissionItem)
        .where(OrderFormSubmissionItem.submission_id == s.id)
        .order_by(OrderFormSubmissionItem.line_no)
    ).all()
    events = db.scalars(
        select(OrderFormSubmissionEvent)
        .where(OrderFormSubmissionEvent.submission_id == s.id)
        .order_by(OrderFormSubmissionEvent.created_at, OrderFormSubmissionEvent.id)
    ).all()
    who = names(db, {e.actor_id for e in events} | {s.converted_by})
    dup = db.get(OrderFormSubmission, s.possible_duplicate_of) if s.possible_duplicate_of else None
    order = db.get(SalesOrder, s.sales_order_id) if s.sales_order_id else None
    cust = db.get(Customer, s.customer_id) if s.customer_id else None
    out = summary_out(s, form.name, len(items), str(s.id) in unread_ids(db, p, [s.id]))
    out.update(
        {
            "form_version_no": ver.version_no,
            "default_warehouse_id": str(form.default_warehouse_id)
            if form.default_warehouse_id
            else None,
            "delivery_address": s.delivery_address,
            "notes": s.notes,
            "custom": list(s.custom),
            "consent_at": iso(s.consent_at),
            "source_host": s.source_host,
            "possible_duplicate_of": {"id": str(dup.id), "reference": dup.reference}
            if dup
            else None,
            "items": [
                {
                    "line_no": i.line_no,
                    "product_id": str(i.product_id),
                    "sku": i.sku,
                    "product_name": i.product_name,
                    "unit_code": i.unit_code,
                    "qty": format(i.qty.normalize(), "f"),
                    "box_size_lb": intake.box_size(s, i.product_id, i.unit_code),
                }
                for i in items
            ],
            "raw": s.raw,
            "events": [
                {
                    "kind": e.kind,
                    "from_status": e.from_status,
                    "to_status": e.to_status,
                    "note": e.note,
                    "actor_name": who.get(e.actor_id) if e.actor_id else None,
                    "created_at": iso(e.created_at),
                }
                for e in events
            ],
            "customer": {"id": str(cust.id), "name": cust.name} if cust else None,
            "sales_order": {"id": str(order.id), "order_no": order.order_no, "status": order.status}
            if order
            else None,
            "converted_by_name": who.get(s.converted_by) if s.converted_by else None,
            "converted_at": iso(s.converted_at),
            "erased_at": iso(s.erased_at),
            "allowed": sorted(review.TRANSITIONS[s.status]),
        }
    )
    return out


# ------------------------------------------------------------------ overview


def my_unread(db: Session, p: Principal) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(NotificationRecipient)
            .join(Notification, Notification.id == NotificationRecipient.notification_id)
            .where(
                NotificationRecipient.employee_id == p.employee.id,
                NotificationRecipient.read_at.is_(None),
                NotificationRecipient.archived_at.is_(None),
                Notification.target_type == TARGET,
            )
        )
        or 0
    )


@router.get("/badge")
def badge(request: Request, db: DbDep, p: PrincipalDep) -> dict[str, int]:
    """Sidebar count: this employee's unread website-order alerts."""
    authorize(request, p, "order_connect.read")
    return {"unread": my_unread(db, p)}


@router.get("/overview")
def overview(request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    authorize(request, p, "order_connect.read")
    cid = p.company_id
    by_status = dict(
        db.execute(
            select(OrderFormSubmission.status, func.count())
            .where(OrderFormSubmission.company_id == cid)
            .group_by(OrderFormSubmission.status)
        ).all()
    )
    since = forms.now() - timedelta(days=30)
    recent = dict(
        db.execute(
            select(OrderFormSubmission.status, func.count())
            .where(OrderFormSubmission.company_id == cid, OrderFormSubmission.created_at >= since)
            .group_by(OrderFormSubmission.status)
        ).all()
    )
    received_30 = sum(v for k, v in recent.items() if k != "SPAM")
    converted_30 = recent.get("CONVERTED", 0)
    day = forms.now() - timedelta(days=1)
    today = db.scalar(
        select(func.count()).where(
            OrderFormSubmission.company_id == cid,
            OrderFormSubmission.created_at >= day,
            OrderFormSubmission.status != "SPAM",
        )
    )
    all_forms = db.scalars(
        select(OrderForm).where(OrderForm.company_id == cid, OrderForm.archived_at.is_(None))
    ).all()
    latest = db.execute(
        select(OrderFormSubmission, OrderForm.name)
        .join(OrderForm, OrderForm.id == OrderFormSubmission.form_id)
        .where(OrderFormSubmission.company_id == cid, OrderFormSubmission.status != "SPAM")
        .order_by(OrderFormSubmission.created_at.desc())
        .limit(6)
    ).all()
    n_items = _item_counts(db, [s.id for s, _ in latest])
    unread = unread_ids(db, p, [s.id for s, _ in latest])
    return {
        "by_status": {k: by_status.get(k, 0) for k in review.TRANSITIONS},
        "open": sum(by_status.get(k, 0) for k in OPEN),
        "new": by_status.get("NEW", 0),
        "unread_for_me": my_unread(db, p),
        "received_24h": today or 0,
        "received_30d": received_30,
        "converted_30d": converted_30,
        # Share of the last 30 days' real (non-spam) submissions already converted; None when
        # nothing came in, so the page shows "—" instead of a made-up 0 %.
        "conversion_rate_30d": round(converted_30 / received_30, 4) if received_30 else None,
        "forms_total": len(all_forms),
        "forms_live": sum(1 for f in all_forms if f.published_version_id and f.accepting),
        "latest": [
            summary_out(s, name, n_items.get(s.id, 0), str(s.id) in unread) for s, name in latest
        ],
    }


def _item_counts(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    return dict(
        db.execute(
            select(OrderFormSubmissionItem.submission_id, func.count())
            .where(OrderFormSubmissionItem.submission_id.in_(ids))
            .group_by(OrderFormSubmissionItem.submission_id)
        ).all()
    )


# ------------------------------------------------------------------ forms


@router.get("/forms")
def list_forms(
    request: Request, db: DbDep, p: PrincipalDep, archived: bool = False
) -> list[dict[str, Any]]:
    authorize(request, p, "order_connect.read")
    q = select(OrderForm).where(OrderForm.company_id == p.company_id)
    q = q.where(OrderForm.archived_at.is_not(None) if archived else OrderForm.archived_at.is_(None))
    rows = db.scalars(q.order_by(OrderForm.created_at)).all()
    drafts = drafts_of(db, [f.id for f in rows])
    return [form_out(db, f, f.id in drafts) for f in rows]


@router.post("/forms", status_code=201)
def create_form(
    body: FormIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    authorize(request, p, "order_connect.manage")

    def fn() -> Result:
        warehouse_ok(db, p, body.default_warehouse_id)
        f = forms.create_form(
            db, p.company_id, p.employee.id, body.name.strip(), body.default_warehouse_id
        )
        return form_out(db, f, True), str(f.id), {"name": f.name}

    return mutate(
        db, request, p, key, "order_connect.form.create", body, 201, "order_connect.form.create", fn
    )


@router.get("/forms/{form_id}")
def read_form(form_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    authorize(request, p, "order_connect.read")
    f = get_form(db, p, form_id)
    out = form_out(db, f)
    out["editing"] = content_out(db, editing_version(db, f))
    # Suggested button colours (each passes 4.5:1 with white text; any such colour is accepted).
    out["accents"] = [{"value": v, "label": label} for v, label in forms.ACCENTS]
    return out


@router.patch("/forms/{form_id}")
def patch_form(
    form_id: uuid.UUID,
    body: FormPatch,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    authorize(request, p, "order_connect.manage")

    def fn() -> Result:
        f = get_form(db, p, form_id, lock=True)
        check_version(f.version, body.version, "Form")
        changes = body.model_dump(exclude_unset=True, exclude={"version"})
        if "name" in changes and body.name is not None:
            f.name = body.name.strip()
        if body.accepting is not None:
            f.accepting = body.accepting
        if body.standalone_link is not None:
            f.standalone_link = body.standalone_link
        if "default_warehouse_id" in changes:
            warehouse_ok(db, p, body.default_warehouse_id)
            f.default_warehouse_id = body.default_warehouse_id
        if body.archived is not None:
            f.archived_at = forms.now() if body.archived else None
        f.version += 1
        f.updated_at = forms.now()
        db.flush()
        return form_out(db, f), str(f.id), changes

    return mutate(
        db, request, p, key, "order_connect.form.update", body, 200, "order_connect.form.update", fn
    )


@router.put("/forms/{form_id}/draft")
def save_draft(
    form_id: uuid.UUID,
    body: DraftIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """Replace the draft's content (revision 0 = start a draft from the published version)."""
    authorize(request, p, "order_connect.manage")

    def fn() -> Result:
        f = get_form(db, p, form_id, lock=True)
        has_draft = f.id in drafts_of(db, [f.id])
        if not has_draft and body.revision != 0:
            check_version(0, body.revision, "Form draft")
        d = forms.get_draft(db, f, p.employee.id, create=True)
        if has_draft:
            check_version(d.revision, body.revision, "Form draft")
        forms.check_fields(body.fields)
        forms.check_scope(db, p.company_id, body.product_scope, body.scope_ids)
        theme = forms.clean_theme(body.theme.model_dump())
        if body.logo_id is not None:
            lg = db.get(OrderFormLogo, body.logo_id)
            if lg is None or lg.company_id != p.company_id:
                raise ApiError(422, "INVALID_LOGO", "Logo not found; upload it again")
        d.title, d.intro = body.title.strip(), body.intro.strip()
        d.success_message = body.success_message.strip()
        d.theme, d.logo_id, d.show_prices = theme, body.logo_id, body.show_prices
        d.product_scope, d.scope_ids = body.product_scope, [str(i) for i in body.scope_ids]
        d.revision += 1 if has_draft else 0
        d.updated_at = forms.now()
        db.execute(delete(OrderFormField).where(OrderFormField.version_id == d.id))
        for pos, fl in enumerate(body.fields):
            db.add(
                OrderFormField(
                    version_id=d.id,
                    position=pos,
                    key=fl.key,
                    kind=fl.kind,
                    label=fl.label.strip(),
                    help_text=fl.help_text.strip(),
                    required=fl.required,
                    options=[o.strip() for o in fl.options],
                )
            )
        f.updated_at = forms.now()
        db.flush()
        out = form_out(db, f, True)
        out["editing"] = content_out(db, d)
        return out, str(f.id), {"version_no": d.version_no, "revision": d.revision}

    return mutate(
        db, request, p, key, "order_connect.form.draft", body, 200, "order_connect.form.draft", fn
    )


@router.post("/forms/{form_id}/publish")
def publish_form(
    form_id: uuid.UUID,
    body: PublishIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    authorize(request, p, "order_connect.manage")

    def fn() -> Result:
        f = get_form(db, p, form_id, lock=True)
        d = db.scalar(
            select(OrderFormVersion).where(
                OrderFormVersion.form_id == f.id, OrderFormVersion.status == "DRAFT"
            )
        )
        if d is None:
            raise ApiError(409, "NOTHING_TO_PUBLISH", "There are no unpublished changes")
        check_version(d.revision, body.revision, "Form draft")
        forms.publish(db, f, d, p.employee.id)
        db.flush()
        out = form_out(db, f, False)
        out["editing"] = content_out(db, d)
        return out, str(f.id), {"version_no": d.version_no}

    return mutate(
        db,
        request,
        p,
        key,
        "order_connect.form.publish",
        body,
        200,
        "order_connect.form.publish",
        fn,
    )


@router.get("/forms/{form_id}/preview-config")
def preview_config(
    form_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    """The draft as customers would see it (same builder as the public config, no token)."""
    authorize(request, p, "order_connect.read")
    f = get_form(db, p, form_id)
    cfg = forms.public_config(
        db, f, editing_version(db, f), forms.consent_of(forms.settings_row(db, p.company_id))
    )
    cfg["preview"] = True
    return cfg


@router.get("/forms/{form_id}/embed")
def embed_info(form_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    authorize(request, p, "order_connect.read")
    f = get_form(db, p, form_id)
    origin = get_settings().public_origin
    url = f"{origin}/embed/{f.public_id}"
    title = html.escape(f.name, quote=True)
    frame_id = f"tt-order-{f.public_id[:8]}"
    plain = (
        f'<iframe id="{frame_id}" src="{url}" title="{title}" width="100%" height="900" '
        'loading="lazy" style="display:block;border:0;width:100%;max-width:100%;min-height:600px" '
        'referrerpolicy="strict-origin-when-cross-origin"></iframe>'
    )
    snippet = plain + "\n" + embed.resize_script(frame_id, origin)
    hosts = embed.allowed_hosts(db, f)
    return {
        "public_url": url,
        "iframe": snippet,
        "iframe_only": plain,
        "published": f.published_version_id is not None,
        "accepting": f.accepting,
        "standalone_link": f.standalone_link,
        "hosts": hosts,
        "frame_ancestors": embed.frame_ancestors(hosts),
        "https_only": get_settings().is_production_like,
    }


@router.post("/logos", status_code=201)
async def upload_logo(request: Request, p: PrincipalDep) -> JSONResponse:
    """Raw PNG/JPEG/WebP body (≤ 1 MB). Stored re-encoded as PNG."""
    authorize(request, p, "order_connect.manage")
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in ("image/png", "image/jpeg", "image/webp"):
        raise ApiError(415, "LOGO_TYPE", "Use a PNG, JPEG or WebP logo")
    data = await request.body()

    def store() -> dict[str, Any]:
        with get_sessionmaker()() as db:
            lg = forms.store_logo(db, p.company_id, p.employee.id, data)
            audit.record(
                db,
                action="order_connect.logo.upload",
                success=True,
                company_id=p.company_id,
                actor_id=p.employee.id,
                target_type="order_form_logo",
                target_id=str(lg.id),
                request_id=request.state.request_id,
                detail={"bytes": lg.byte_size, "width": lg.width, "height": lg.height},
            )
            db.commit()
            return {
                "id": str(lg.id),
                "url": f"/api/v1/public/logos/{lg.id}",
                "width": lg.width,
                "height": lg.height,
            }

    return JSONResponse(await run_in_threadpool(store), 201)


# ------------------------------------------------------------------ allowed websites


def normalize_host(raw: str) -> str:
    """'https://www.Example.com/shop' -> 'www.example.com'; '*.example.com' allowed."""
    h = raw.strip().lower()
    for prefix in ("https://", "http://"):
        if h.startswith(prefix):
            h = h[len(prefix) :]
    h = h.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in h or ":" in h:
        raise ApiError(422, "INVALID_HOST", "Enter a website address like shop.example.com")
    wild = h.startswith("*.")
    core = h[2:] if wild else h
    try:
        core = core.encode("idna").decode("ascii")
    except UnicodeError:
        raise ApiError(422, "INVALID_HOST", "That website address is not valid") from None
    labels = core.split(".")
    if len(labels) < 2 or labels[-1].isdigit() or core in ("localhost",):
        raise ApiError(
            422, "INVALID_HOST", "Use a public website name (no IP addresses or localhost)"
        )
    if wild and len(labels) < 2:
        raise ApiError(422, "INVALID_HOST", "A wildcard needs a domain, like *.example.com")
    return ("*." if wild else "") + core


def domain_out(d: OrderFormAllowedDomain) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "form_id": str(d.form_id) if d.form_id else None,
        "host": d.host,
        "created_at": iso(d.created_at),
    }


@router.get("/domains")
def list_domains(
    request: Request, db: DbDep, p: PrincipalDep, form_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    authorize(request, p, "order_connect.read")
    q = select(OrderFormAllowedDomain).where(OrderFormAllowedDomain.company_id == p.company_id)
    if form_id is not None:
        get_form(db, p, form_id)
        q = q.where(
            or_(OrderFormAllowedDomain.form_id.is_(None), OrderFormAllowedDomain.form_id == form_id)
        )
    else:
        q = q.where(OrderFormAllowedDomain.form_id.is_(None))
    return [domain_out(d) for d in db.scalars(q.order_by(OrderFormAllowedDomain.host)).all()]


@router.post("/domains", status_code=201)
def add_domain(
    body: DomainIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    authorize(request, p, "order_connect.manage")

    def fn() -> Result:
        if body.form_id is not None:
            get_form(db, p, body.form_id)
        d = OrderFormAllowedDomain(
            company_id=p.company_id,
            form_id=body.form_id,
            host=normalize_host(body.host),
            created_by=p.employee.id,
        )
        db.add(d)
        db.flush()
        return domain_out(d), str(d.id), {"host": d.host, "form_id": str(body.form_id or "")}

    return mutate(
        db,
        request,
        p,
        key,
        "order_connect.domain.add",
        body,
        201,
        "order_connect.domain.add",
        fn,
        "That website is already allowed",
    )


@router.delete("/domains/{domain_id}", status_code=204)
def remove_domain(
    domain_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> JSONResponse:
    authorize(request, p, "order_connect.manage")
    d = db.get(OrderFormAllowedDomain, domain_id)
    if d is None or d.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Website not found")
    host = d.host
    db.delete(d)
    audit.record(
        db,
        action="order_connect.domain.remove",
        success=True,
        company_id=p.company_id,
        actor_id=p.employee.id,
        target_type="order_form_domain",
        target_id=str(domain_id),
        request_id=request.state.request_id,
        detail={"host": host},
    )
    db.commit()
    return JSONResponse(None, 204)


# ------------------------------------------------------------------ settings


@router.get("/settings")
def get_settings_route(request: Request, db: DbDep, p: PrincipalDep) -> dict[str, Any]:
    authorize(request, p, "order_connect.read")
    return forms.settings_row(db, p.company_id)


@router.put("/settings")
def put_settings(
    body: SettingsIn, request: Request, db: DbDep, key: idempotency.IdemKey, p: PrincipalDep
) -> JSONResponse:
    authorize(request, p, "order_connect.manage")

    def fn() -> Result:
        row = db.get(
            OrderConnectSettings, p.company_id, with_for_update=True, populate_existing=True
        )
        check_version(row.version if row else 0, body.version, "Settings")
        if body.consent_required and not body.consent_text.strip():
            raise ApiError(422, "CONSENT_TEXT", "Write the privacy notice customers agree to")
        vals = body.model_dump(exclude={"version"})
        vals["consent_text"] = vals["consent_text"].strip()
        if row is None:
            row = OrderConnectSettings(company_id=p.company_id, **vals, updated_by=p.employee.id)
            db.add(row)
        else:
            for k, v in vals.items():
                setattr(row, k, v)
            row.version += 1
            row.updated_by, row.updated_at = p.employee.id, forms.now()
        db.flush()
        return forms.settings_row(db, p.company_id), str(p.company_id), vals

    return mutate(
        db, request, p, key, "order_connect.settings", body, 200, "order_connect.settings", fn
    )


# ------------------------------------------------------------------ submissions


@router.get("/submissions")
def list_submissions(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    status: str | None = Query(
        None, pattern=r"^(OPEN|ALL|NEW|REVIEWING|APPROVED|CONVERTED|REJECTED|DUPLICATE|SPAM)$"
    ),
    q: str = Query("", max_length=100),
    form_id: uuid.UUID | None = None,
    request_type: str | None = Query(None, alias="type", pattern=r"^(ORDER|QUOTE)$"),
    before: datetime | None = None,
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, Any]:
    authorize(request, p, "order_connect.read")
    cond = [OrderFormSubmission.company_id == p.company_id]
    if request_type is not None:
        kind = func.coalesce(OrderFormSubmission.raw["request_type"].astext, "ORDER")
        cond.append(kind == request_type)
    if status in (None, "OPEN"):
        cond.append(OrderFormSubmission.status.in_(OPEN))
    elif status != "ALL":
        cond.append(OrderFormSubmission.status == status)
    if form_id is not None:
        cond.append(OrderFormSubmission.form_id == form_id)
    if before is not None:
        cond.append(OrderFormSubmission.created_at < before)
    if q.strip():
        like = f"%{esc(q.strip().lower())}%"
        cond.append(
            or_(
                *(
                    func.lower(c).like(like, escape="\\")
                    for c in (
                        OrderFormSubmission.reference,
                        OrderFormSubmission.customer_name,
                        OrderFormSubmission.business_name,
                        OrderFormSubmission.email,
                        OrderFormSubmission.phone,
                    )
                )
            )
        )
    rows = db.execute(
        select(OrderFormSubmission, OrderForm.name)
        .join(OrderForm, OrderForm.id == OrderFormSubmission.form_id)
        .where(and_(*cond))
        .order_by(OrderFormSubmission.created_at.desc())
        .limit(limit + 1)
    ).all()
    more = len(rows) > limit
    rows = rows[:limit]
    ids = [s.id for s, _ in rows]
    counts = _item_counts(db, ids)
    unread = unread_ids(db, p, ids)
    items = [summary_out(s, n, counts.get(s.id, 0), str(s.id) in unread) for s, n in rows]
    return {
        "items": items,
        "next_before": items[-1]["created_at"] if more and items else None,
    }


@router.get("/submissions/{submission_id}")
def read_submission(
    submission_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    authorize(request, p, "order_connect.read")
    return detail_out(db, p, get_sub(db, p, submission_id))


@router.get("/submissions/{submission_id}/matches")
def matches(
    submission_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> list[dict[str, Any]]:
    authorize(request, p, "order_connect.read")
    return review.customer_matches(db, get_sub(db, p, submission_id))


@router.post("/submissions/{submission_id}/read", status_code=204)
def mark_read(
    submission_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> JSONResponse:
    """Opening a submission clears this employee's alerts for it (others keep theirs)."""
    authorize(request, p, "order_connect.read")
    get_sub(db, p, submission_id)
    nids = select(Notification.id).where(
        Notification.company_id == p.company_id,
        Notification.target_type == TARGET,
        Notification.target_id == str(submission_id),
    )
    res = db.execute(
        update(NotificationRecipient)
        .where(
            NotificationRecipient.employee_id == p.employee.id,
            NotificationRecipient.read_at.is_(None),
            NotificationRecipient.notification_id.in_(nids),
        )
        .values(read_at=forms.now())
    )
    if getattr(res, "rowcount", 0):
        mark_changed(db, p.employee.id)
    db.commit()
    return JSONResponse(None, 204)


@router.post("/submissions/{submission_id}/status")
def change_status(
    submission_id: uuid.UUID,
    body: StatusIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    authorize(request, p, "order_connect.review")

    def fn() -> Result:
        s = get_sub(db, p, submission_id, lock=True)
        authorize(request, p, review.permission_for(s.status, body.status))
        check_version(s.version, body.version, "Submission")
        old = s.status
        review.set_status(db, s, body.status, p.employee.id, body.note.strip())
        db.flush()
        return detail_out(db, p, s), str(s.id), {"from": old, "to": body.status}

    return mutate(
        db, request, p, key, "order_connect.status", body, 200, "order_connect.status", fn
    )


@router.post("/submissions/{submission_id}/notes", status_code=201)
def add_note(
    submission_id: uuid.UUID,
    body: NoteIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    authorize(request, p, "order_connect.review")

    def fn() -> Result:
        s = get_sub(db, p, submission_id)
        review.event(db, s, "NOTE", p.employee.id, note=body.note.strip())
        db.flush()
        return detail_out(db, p, s), str(s.id), {}

    return mutate(db, request, p, key, "order_connect.note", body, 201, "order_connect.note", fn)


@router.post("/submissions/{submission_id}/convert", status_code=201)
def convert(
    submission_id: uuid.UUID,
    body: ConvertIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    """APPROVED submission -> one DRAFT sales order. Locked, idempotent, and the database
    refuses a second order for the same submission (unique sales_order_id)."""
    authorize(request, p, "order_connect.approve")
    authorize(request, p, "sales_order.manage", body.warehouse_id)
    if (body.customer_id is None) == (body.new_customer is None):
        raise ApiError(422, "CUSTOMER_CHOICE", "Pick an existing customer or create a new one")
    if body.new_customer is not None:
        authorize(request, p, "catalog.manage")

    def fn() -> Result:
        s = get_sub(db, p, submission_id, lock=True)
        if s.status == "CONVERTED":
            raise ApiError(
                409,
                "ALREADY_CONVERTED",
                "This submission is already a sales order",
                sales_order_id=str(s.sales_order_id),
            )
        check_version(s.version, body.version, "Submission")
        if s.status != "APPROVED":
            raise ApiError(409, "NOT_APPROVED", "Approve the submission before converting it")
        created_customer = False
        if body.new_customer is not None:
            nc = body.new_customer
            customer_routes.check_group(db, p, nc.group_id)
            c = Customer(
                company_id=p.company_id,
                external_ref=nc.external_ref,
                name=nc.name.strip(),
                email=customer_routes._blank(nc.email),
                phone=customer_routes._blank(nc.phone),
                group_id=nc.group_id,
                payment_terms_days=nc.payment_terms_days,
            )
            db.add(c)
            db.flush()
            customer_id, created_customer = c.id, True
            audit.record(
                db,
                action="customer.create",
                success=True,
                company_id=p.company_id,
                actor_id=p.employee.id,
                target_type="customer",
                target_id=str(c.id),
                request_id=request.state.request_id,
                detail={"from_submission": s.reference},
            )
        else:
            assert body.customer_id is not None
            customer_id = body.customer_id
        what = "quote request" if intake.request_type(s) == "QUOTE" else "order"
        items = {
            i.line_no: i
            for i in db.scalars(
                select(OrderFormSubmissionItem).where(OrderFormSubmissionItem.submission_id == s.id)
            ).all()
        }
        lines = []
        for ln in body.lines:
            src = items.get(ln.item_line_no) if ln.item_line_no else None
            if ln.item_line_no and src is None:
                raise ApiError(422, "INVALID_ITEM", f"Submission has no line {ln.item_line_no}")
            size = intake.box_size(s, src.product_id, src.unit_code) if src else None
            text = (
                f"Online {what} {s.reference} line {src.line_no}: {src.qty.normalize()} "
                f"{src.unit_code} {src.product_name} ({src.sku})"
                + (f", {size} lb boxes" if size else "")
                if src
                else f"Added by staff to online {what} {s.reference}"
            )
            # The draft order keeps the requested box size as the line note; the order line has
            # no case-size field (packing is chosen when the order is picked and invoiced).
            note = ln.note or (
                f"Box size requested: {size} lb" if size and ln.unit_code == "BOX" else ""
            )
            lines.append(ln.model_copy(update={"source_line_text": text[:500], "note": note[:500]}))
        requested = body.requested_for
        if requested is None and s.requested_date is not None:
            requested = datetime.combine(s.requested_date, time(12, 0), tzinfo=UTC)
        who = s.business_name or s.customer_name
        order_in = OrderIn(
            customer_id=customer_id,
            warehouse_id=body.warehouse_id,
            source="WEB",
            source_text=(
                f"Online {what} {s.reference} from {who}".strip()
                + (f"\nDeliver to: {s.delivery_address}" if s.delivery_address else "")
                + (f"\nCustomer notes: {s.notes}" if s.notes else "")
            )[:10_000],
            requested_for=requested,
            note=body.note.strip(),
            lines=[OrderLineIn(**ln.model_dump(exclude={"item_line_no"})) for ln in lines],
        )
        order = order_routes.insert_order(db, p, order_in)
        old = s.status
        s.status, s.sales_order_id, s.customer_id = "CONVERTED", order.id, customer_id
        s.converted_by, s.converted_at = p.employee.id, forms.now()
        s.version += 1
        s.updated_at = s.converted_at
        review.event(
            db,
            s,
            "CONVERTED",
            p.employee.id,
            from_status=old,
            to_status="CONVERTED",
            note=f"Draft sales order {order.order_no}",
        )
        db.flush()
        return (
            detail_out(db, p, s),
            str(s.id),
            {
                "order_no": order.order_no,
                "sales_order_id": str(order.id),
                "customer_created": created_customer,
                "lines": len(lines),
            },
        )

    return mutate(
        db,
        request,
        p,
        key,
        "order_connect.convert",
        body,
        201,
        "order_connect.convert",
        fn,
        "This submission is already a sales order",
    )


@router.post("/submissions/{submission_id}/erase")
def erase_personal_data(
    submission_id: uuid.UUID,
    body: EraseIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: PrincipalDep,
) -> JSONResponse:
    authorize(request, p, "order_connect.manage")

    def fn() -> Result:
        s = get_sub(db, p, submission_id, lock=True)
        check_version(s.version, body.version, "Submission")
        review.erase(db, s, p.employee.id, body.reason.strip())
        db.flush()
        return detail_out(db, p, s), str(s.id), {"reason": body.reason.strip()}

    return mutate(db, request, p, key, "order_connect.erase", body, 200, "order_connect.erase", fn)
