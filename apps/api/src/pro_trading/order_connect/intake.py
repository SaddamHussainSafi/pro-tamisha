"""Public submission intake. Nothing here trusts the browser: prices and totals are not even
accepted, every value is re-validated against the published form version, products and units
come from the catalog, and the whole intake (submission, items, timeline, notifications, outbox
event, audit row) commits in one PostgreSQL transaction."""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import unicodedata
import uuid
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from .. import audit, outbox
from ..config import get_settings
from ..errors import ApiError
from ..invoicing import deliver
from ..models import Company, EmailMessage, OutboxEvent, Product
from ..notifications import notify_safely
from . import forms
from .models import (
    OrderForm,
    OrderFormField,
    OrderFormSubmission,
    OrderFormSubmissionEvent,
    OrderFormSubmissionItem,
    OrderFormVersion,
)

TOKEN_MAX_AGE = 12 * 3600
RECEIVED_EVENT = "order_connect.submission_received"
KIND_WORDS = {"ORDER": "online order", "QUOTE": "quote request"}
REF_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32: no I, L, O, U
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
PHONE_RE = re.compile(r"^[0-9+()\-.\s]{7,32}$")
HOST_RE = re.compile(r"^[a-z0-9.-]{1,253}(:\d{1,5})?$")
MAX_LEN = {"TEXT": 200, "TEXTAREA": 2000, "EMAIL": 254, "PHONE": 32, "SELECT": 200}
MAX_ITEMS = 100
MAX_QTY = Decimal(9999)
DUPLICATE_WINDOW = timedelta(hours=24)


class PublicItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: uuid.UUID
    unit_code: Literal["BOX", "PIECE", "LB", "KG"]
    qty: Decimal = Field(gt=0, le=MAX_QTY, max_digits=8, decimal_places=4)
    # Chosen box size in lb (BOX lines only; one of the sizes the form offers for the product).
    box_size_lb: Decimal | None = Field(default=None, gt=0, le=1000, max_digits=7, decimal_places=2)


class PublicSubmissionIn(BaseModel):
    """No price, total or customer id can be sent: unknown keys are refused."""

    model_config = ConfigDict(extra="forbid")
    form_token: str = Field(min_length=10, max_length=1000)
    client_token: str = Field(pattern=r"^[A-Za-z0-9_-]{16,64}$")
    values: dict[str, str | bool] = Field(default_factory=dict, max_length=forms.MAX_FIELDS)
    items: list[PublicItemIn] = Field(default_factory=list, max_length=MAX_ITEMS)
    consent: bool = False
    # What the customer asks for: an order, or prices first (a quote). Same review flow for staff.
    request_type: Literal["ORDER", "QUOTE"] = "ORDER"
    website: str = Field(default="", max_length=500)  # honeypot: people never see it


# ------------------------------------------------------------------ signed form-load token


def _key() -> bytes:
    return get_settings().order_connect_key.encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def clean_host(host: str | None) -> str:
    h = (host or "").strip().lower()[:260]
    return h if HOST_RE.match(h) else ""


def make_token(public_id: str, version_id: uuid.UUID, host: str, at: float | None = None) -> str:
    body = json.dumps(
        {"p": public_id, "v": str(version_id), "t": int(at or time.time()), "h": host},
        separators=(",", ":"),
    ).encode()
    sig = hmac.new(_key(), body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(sig)}"


def read_token(token: str, public_id: str) -> dict[str, Any]:
    try:
        body_s, sig_s = token.split(".", 1)
        body, sig = _unb64(body_s), _unb64(sig_s)
        ok = hmac.compare_digest(sig, hmac.new(_key(), body, hashlib.sha256).digest())
        data = json.loads(body) if ok else None
    except (ValueError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict) or data.get("p") != public_id:
        raise ApiError(422, "INVALID_FORM_TOKEN", "Reload the page and try again")
    age = time.time() - int(data.get("t", 0))
    if age > TOKEN_MAX_AGE or age < -60:
        raise ApiError(409, "FORM_EXPIRED", "This page was open too long. Reload it to continue.")
    data["age"] = age
    return data


def ip_hash(ip: str) -> str:
    return hmac.new(_key(), f"ip:{ip}".encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ value validation


def clean_text(value: str, multiline: bool) -> str:
    """NFC, no control characters (newlines kept for multi-line fields), trimmed. The text is
    stored as typed and always rendered as text, never as HTML."""
    v = unicodedata.normalize("NFC", value)
    keep = "\n" if multiline else ""
    v = "".join(
        (" " if c == "\t" else c)
        for c in v
        if c in keep or c == "\t" or unicodedata.category(c)[0] != "C"
    )
    v = v.replace("\r", "")
    if multiline:
        v = re.sub(r"\n{3,}", "\n\n", v)
    return v.strip()


def _today(db: Session, company_id: uuid.UUID) -> date:
    tz = db.scalar(select(Company.timezone).where(Company.id == company_id)) or "UTC"
    try:
        return datetime.now(ZoneInfo(tz)).date()
    except (KeyError, ValueError):
        return datetime.now(ZoneInfo("UTC")).date()


def validate_values(
    fields: list[OrderFormField], values: dict[str, str | bool], today: date
) -> dict[str, Any]:
    """Returns {key: cleaned value}. Raises 422 VALIDATION_FAILED with a message per field."""
    by_key = {f.key: f for f in fields}
    errors: dict[str, str] = {}
    for k in values:
        if k not in by_key or by_key[k].kind == "PRODUCTS":
            errors[k[:40]] = "This field is not on the form"
    out: dict[str, Any] = {}
    for f in fields:
        if f.kind == "PRODUCTS":
            continue
        raw = values.get(f.key)
        if f.kind == "CHECKBOX":
            if raw is not None and not isinstance(raw, bool):
                errors[f.key] = "Tick or untick this box"
                continue
            out[f.key] = bool(raw)
            if f.required and not raw:
                errors[f.key] = "Please tick this box"
            continue
        if raw is not None and not isinstance(raw, str):
            errors[f.key] = "Enter text"
            continue
        v = clean_text(raw or "", f.kind == "TEXTAREA")
        if not v:
            if f.required:
                errors[f.key] = "This field is required"
            out[f.key] = "" if f.kind != "DATE" else None
            continue
        if len(v) > MAX_LEN.get(f.kind, 200):
            errors[f.key] = f"Use at most {MAX_LEN.get(f.kind, 200)} characters"
        elif f.kind == "EMAIL" and not EMAIL_RE.match(v):
            errors[f.key] = "Enter a valid email address"
        elif f.kind == "PHONE" and (not PHONE_RE.match(v) or sum(c.isdigit() for c in v) < 7):
            errors[f.key] = "Enter a valid phone number"
        elif f.kind == "SELECT" and v not in f.options:
            errors[f.key] = "Choose one of the listed options"
        elif f.kind == "DATE":
            try:
                d = date.fromisoformat(v)
            except ValueError:
                errors[f.key] = "Enter a date like 2026-12-01"
                continue
            if d < today:
                errors[f.key] = "Choose today or a later date"
            elif d > today + timedelta(days=366):
                errors[f.key] = "Choose a date within the next year"
            out[f.key] = d
            continue
        out[f.key] = v.lower() if f.kind == "EMAIL" else v
    if errors:
        raise ApiError(
            422, "VALIDATION_FAILED", "Please check the highlighted fields", fields=errors
        )
    return out


def validate_items(
    db: Session,
    form: OrderForm,
    ver: OrderFormVersion,
    items: list[PublicItemIn],
    required: bool,
) -> list[tuple[PublicItemIn, Product]]:
    if not items:
        if required:
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "Add at least one product",
                fields={"products": "Add at least one product"},
            )
        return []
    allowed = {p.id: p for p in forms.products_in_scope(db, form.company_id, ver)}
    units = forms._units(db, list(allowed.values()))
    boxes = forms.box_sizes(db, list(allowed.values()))
    errors: dict[str, str] = {}
    seen: set[tuple[uuid.UUID, str]] = set()
    out = []
    for i, it in enumerate(items, start=1):
        prod = allowed.get(it.product_id)
        if prod is None:
            errors[f"items.{i}"] = "This product is no longer available"
            continue
        if it.unit_code not in units[prod.id]:
            errors[f"items.{i}"] = f"{prod.name} can't be ordered by {it.unit_code}"
            continue
        if it.unit_code in ("BOX", "PIECE") and it.qty != it.qty.to_integral_value():
            errors[f"items.{i}"] = f"{it.unit_code} quantities must be whole numbers"
            continue
        if it.unit_code == "BOX":
            offered = boxes[prod.id][0]
            if it.box_size_lb is None and offered:
                errors[f"items.{i}"] = f"Choose a box size for {prod.name}"
                continue
            if it.box_size_lb is not None and it.box_size_lb.normalize() not in offered:
                errors[f"items.{i}"] = f"Choose one of the listed box sizes for {prod.name}"
                continue
        elif it.box_size_lb is not None:
            errors[f"items.{i}"] = f"A box size only applies to boxes of {prod.name}"
            continue
        if (prod.id, it.unit_code) in seen:
            errors[f"items.{i}"] = f"{prod.name} by {it.unit_code} is listed twice"
            continue
        seen.add((prod.id, it.unit_code))
        out.append((it, prod))
    if errors:
        raise ApiError(422, "VALIDATION_FAILED", "Some products need attention", fields=errors)
    return out


# ------------------------------------------------------------------ intake


def new_reference() -> str:
    return "OC-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(6))


def content_hash(
    form_id: uuid.UUID, vals: dict[str, Any], lines: Sequence[tuple[str, str, str]]
) -> str:
    who = "|".join(
        str(vals.get(k) or "").strip().lower()
        for k in ("email", "phone", "business_name", "customer_name")
    )
    return hashlib.sha256(f"{form_id}|{who}|{sorted(lines)}".encode()).hexdigest()


def existing(db: Session, form_id: uuid.UUID, client_token: str) -> OrderFormSubmission | None:
    return db.scalar(
        select(OrderFormSubmission).where(
            OrderFormSubmission.form_id == form_id,
            OrderFormSubmission.client_token == client_token,
        )
    )


def receive(
    db: Session,
    form: OrderForm,
    body: PublicSubmissionIn,
    *,
    token: dict[str, Any],
    ip: str,
    user_agent: str,
    request_id: str | None,
) -> tuple[OrderFormSubmission, bool]:
    """Validate and store one submission; (submission, created). Caller commits."""
    ver = db.get(OrderFormVersion, form.published_version_id)
    assert ver is not None
    s = forms.settings_row(db, form.company_id)
    fields = forms.fields_of(db, ver.id)

    spam: list[str] = []
    if body.website.strip():
        spam.append("honeypot")
    if token["age"] < s["min_fill_seconds"]:
        spam.append("too_fast")

    vals: dict[str, Any] = {}
    lines: list[tuple[PublicItemIn, Product]] = []
    if not spam:
        vals = validate_values(fields, body.values, _today(db, form.company_id))
        req_products = any(f.kind == "PRODUCTS" and f.required for f in fields)
        has_products = any(f.kind == "PRODUCTS" for f in fields)
        if body.items and not has_products:
            raise ApiError(422, "VALIDATION_FAILED", "This form does not take products")
        lines = validate_items(db, form, ver, body.items, req_products)
        if s["consent_required"] and not body.consent:
            raise ApiError(
                422,
                "VALIDATION_FAILED",
                "Please agree to the privacy notice",
                fields={"consent": "Please agree to the privacy notice"},
            )

    raw = {
        "values": {
            k[:40]: (clean_text(v, True)[:2000] if isinstance(v, str) else v)
            for k, v in body.values.items()
        },
        "items": [
            {
                "product_id": str(i.product_id),
                "unit_code": i.unit_code,
                "qty": str(i.qty),
                **({"box_size_lb": forms.size_text(i.box_size_lb)} if i.box_size_lb else {}),
            }
            for i in body.items
        ],
        "consent": body.consent,
        "request_type": body.request_type,
    }
    line_keys = [
        (
            str(p.id),
            it.unit_code + (f"@{it.box_size_lb.normalize()}" if it.box_size_lb else ""),
            str(it.qty),
        )
        for it, p in lines
    ]
    chash = content_hash(form.id, vals, line_keys)
    dup = None
    if not spam:
        dup = db.scalar(
            select(OrderFormSubmission.id)
            .where(
                OrderFormSubmission.form_id == form.id,
                OrderFormSubmission.content_hash == chash,
                OrderFormSubmission.status != "SPAM",
                OrderFormSubmission.created_at >= forms.now() - DUPLICATE_WINDOW,
            )
            .order_by(OrderFormSubmission.created_at)
            .limit(1)
        )

    custom = [
        {"key": f.key, "label": f.label, "value": vals[f.key]}
        for f in fields
        if f.key.startswith("custom_") and f.key in vals
    ]
    sub: OrderFormSubmission | None = None
    for _ in range(5):
        cand = OrderFormSubmission(
            company_id=form.company_id,
            form_id=form.id,
            version_id=ver.id,
            reference=new_reference(),
            client_token=body.client_token,
            status="SPAM" if spam else "NEW",
            customer_name=vals.get("customer_name") or "",
            business_name=vals.get("business_name") or "",
            email=vals.get("email") or "",
            phone=vals.get("phone") or "",
            delivery_address=vals.get("delivery_address") or "",
            requested_date=vals.get("delivery_date"),
            notes=vals.get("notes") or "",
            custom=custom,
            raw=raw,
            consent_at=forms.now() if body.consent else None,
            source_host=clean_host(token.get("h")),
            ip_hash=ip_hash(ip),
            user_agent=user_agent[:300],
            content_hash=chash,
            spam_reasons=spam,
            possible_duplicate_of=dup,
        )
        try:
            with db.begin_nested():
                db.add(cand)
                db.flush()
        except IntegrityError:
            if (prior := existing(db, form.id, body.client_token)) is not None:
                return prior, False  # a concurrent retry of the same submission won the race
            continue  # reference collision: draw another
        sub = cand
        break
    if sub is None:
        raise ApiError(503, "TRY_AGAIN", "Please try again in a moment")

    for n, (it, prod) in enumerate(lines, start=1):
        db.add(
            OrderFormSubmissionItem(
                submission_id=sub.id,
                line_no=n,
                product_id=prod.id,
                unit_code=it.unit_code,
                qty=it.qty.quantize(Decimal("0.0001")),
                sku=prod.sku,
                product_name=prod.name,
            )
        )
    db.add(
        OrderFormSubmissionEvent(
            company_id=form.company_id,
            submission_id=sub.id,
            kind="RECEIVED",
            to_status=sub.status,
            note=("Possible spam: " + ", ".join(spam)) if spam else "",
        )
    )
    if not spam:
        who = sub.business_name or sub.customer_name or "A customer"
        if s["notify_staff"]:
            notify_safely(
                db,
                form.company_id,
                "order_connect.submission",
                title=f"New {KIND_WORDS[body.request_type]} {sub.reference}",
                body=f"{who} · {len(lines)} product{'s' if len(lines) != 1 else ''} · {form.name}",
                link=f"/online-orders/requests/{sub.id}",
                target_type="order_form_submission",
                target_id=sub.id,
                # Only company-wide holders may open a submission (routes/order_connect.py), so
                # only they get its alert: a warehouse-scoped grant must not see customer names.
                any_scope=False,
            )
        outbox.enqueue(
            db,
            RECEIVED_EVENT,
            {"submission_id": str(sub.id), "ack": bool(s["ack_email"] and sub.email)},
            company_id=form.company_id,
            aggregate_id=str(sub.id),
        )
    audit.record(
        db,
        action="order_connect.submission.received",
        success=True,
        company_id=form.company_id,
        target_type="order_form_submission",
        target_id=str(sub.id),
        request_id=request_id,
        detail={
            "reference": sub.reference,
            "form_id": str(form.id),
            "status": sub.status,
            "items": len(lines),
            "spam_reasons": spam,
        },
    )
    db.flush()
    return sub, True


def handle_received(ev: OutboxEvent) -> None:
    """Worker side of a new submission: the optional acknowledgment email (dedupe key makes a
    retry harmless). Staff notifications were already written in the intake transaction."""
    if not ev.payload.get("ack"):
        return
    db = object_session(ev)
    assert db is not None
    sub = db.get(OrderFormSubmission, uuid.UUID(ev.payload["submission_id"]))
    if sub is None or sub.erased_at is not None or not sub.email or sub.status == "SPAM":
        return
    company = db.get(Company, sub.company_id)
    assert company is not None
    name = sub.customer_name or sub.business_name or "there"
    what = "quote request" if request_type(sub) == "QUOTE" else "order request"
    deliver(
        db,
        EmailMessage(
            company_id=sub.company_id,
            dedupe_key=f"order-connect-ack:{sub.id}",
            to_addr=sub.email,
            subject=f"We received your {what} {sub.reference}",
            body=(
                f"Hello {name},\n\nThank you. {company.legal_name} received your {what} "
                f"{sub.reference}. Our team will review it and contact you to confirm.\n\n"
                "This email is not an order confirmation, a quote or an invoice."
            ),
        ),
    )


def request_type(sub: OrderFormSubmission) -> str:
    """ORDER or QUOTE, as the customer chose (kept in the submitted values, also after erasure)."""
    return "QUOTE" if (sub.raw or {}).get("request_type") == "QUOTE" else "ORDER"


def box_size(sub: OrderFormSubmission, product_id: uuid.UUID, unit_code: str) -> str | None:
    """The box size (lb) the customer chose for this product/unit, from the submitted values."""
    for it in (sub.raw or {}).get("items") or []:
        if it.get("product_id") == str(product_id) and it.get("unit_code") == unit_code:
            v = it.get("box_size_lb")
            return str(v) if v else None
    return None
