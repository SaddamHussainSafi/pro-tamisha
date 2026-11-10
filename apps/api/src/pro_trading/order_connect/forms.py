"""Form content: defaults, draft validation, publishing, the public config and logos."""

import hashlib
import io
import re
import secrets
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import ApiError
from ..models import (
    PriceBookVersion,
    PriceEntry,
    Product,
    ProductCategory,
    ProductUnitProfile,
)
from .models import (
    OrderConnectSettings,
    OrderForm,
    OrderFormField,
    OrderFormLogo,
    OrderFormVersion,
)

# System fields: key -> kind. Their meaning is fixed (they fill the submission's columns and
# the draft order); staff may rename, reorder, require or remove them.
SYSTEM_FIELDS: dict[str, str] = {
    "customer_name": "TEXT",
    "business_name": "TEXT",
    "email": "EMAIL",
    "phone": "PHONE",
    "delivery_address": "TEXTAREA",
    "delivery_date": "DATE",
    "products": "PRODUCTS",
    "notes": "TEXTAREA",
}
CUSTOM_KINDS = ("TEXT", "TEXTAREA", "SELECT", "CHECKBOX")
CUSTOM_KEY = re.compile(r"^custom_[a-z0-9_]{1,32}$")
MAX_FIELDS = 30

DEFAULT_FIELDS: list[dict[str, Any]] = [
    {"key": "customer_name", "label": "Your name", "required": True},
    {"key": "business_name", "label": "Business or store name", "required": True},
    {"key": "email", "label": "Email", "required": True},
    {"key": "phone", "label": "Phone", "required": False},
    {"key": "delivery_address", "label": "Delivery address", "required": False},
    {"key": "delivery_date", "label": "Requested delivery date", "required": False},
    {"key": "products", "label": "Products", "required": True},
    {"key": "notes", "label": "Notes", "required": False},
]
DEFAULT_SUCCESS = (
    "Thank you. We received your order request and will contact you to confirm it. "
    "This is not an invoice."
)
# Button colours customers see. Each passes 4.5:1 with white text (checked again on save).
ACCENTS = (
    ("#1B7A30", "Tamisha green"),
    ("#125E23", "Dark green"),
    ("#18181B", "Black"),
    ("#2459C6", "Blue"),
    ("#B63838", "Red"),
    ("#7A4E0A", "Brown"),
)
RADII = ("none", "small", "large")
DEFAULT_THEME = {"accent": "#1B7A30", "radius": "large"}

DEFAULT_SETTINGS: dict[str, Any] = {
    "notify_staff": True,
    "ack_email": False,
    "min_fill_seconds": 3,
    "ip_limit_per_hour": 20,
    "form_limit_per_hour": 300,
    "retention_days": None,
    "consent_required": False,
    "consent_text": "",
    "version": 0,
}

UNIT_ORDER = {"BOX": 0, "PIECE": 1, "LB": 2, "KG": 3}


def now() -> datetime:
    return datetime.now(UTC)


def new_public_id() -> str:
    """22 url-safe characters (128 random bits): unguessable, not derived from any id."""
    return secrets.token_urlsafe(16)[:22].ljust(22, "A")


def settings_row(db: Session, company_id: uuid.UUID) -> dict[str, Any]:
    row = db.get(OrderConnectSettings, company_id)
    if row is None:
        return dict(DEFAULT_SETTINGS)
    return {k: getattr(row, k) for k in DEFAULT_SETTINGS}


# ------------------------------------------------------------------ theme


def _luminance(hex_color: str) -> float:
    def ch(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_with_white(hex_color: str) -> float:
    return 1.05 / (_luminance(hex_color) + 0.05)


def clean_theme(theme: dict[str, Any]) -> dict[str, str]:
    accent = str(theme.get("accent", DEFAULT_THEME["accent"])).upper()
    if not re.fullmatch(r"#[0-9A-F]{6}", accent):
        raise ApiError(422, "INVALID_THEME", "Accent must be a colour like #1B7A30")
    if contrast_with_white(accent) < 4.5:
        raise ApiError(
            422,
            "ACCENT_LOW_CONTRAST",
            "That colour is too light for white button text (needs 4.5:1 contrast)",
        )
    radius = str(theme.get("radius", DEFAULT_THEME["radius"]))
    if radius not in RADII:
        raise ApiError(422, "INVALID_THEME", "Corner style must be none, small or large")
    return {"accent": accent, "radius": radius}


# ------------------------------------------------------------------ drafts


def create_form(
    db: Session,
    company_id: uuid.UUID,
    actor: uuid.UUID,
    name: str,
    warehouse_id: uuid.UUID | None,
) -> OrderForm:
    form = OrderForm(
        company_id=company_id,
        public_id=new_public_id(),
        name=name,
        default_warehouse_id=warehouse_id,
        created_by=actor,
    )
    db.add(form)
    db.flush()
    ver = OrderFormVersion(
        company_id=company_id,
        form_id=form.id,
        version_no=1,
        title=name,
        success_message=DEFAULT_SUCCESS,
        theme=dict(DEFAULT_THEME),
        created_by=actor,
    )
    db.add(ver)
    db.flush()
    for pos, f in enumerate(DEFAULT_FIELDS):
        db.add(
            OrderFormField(
                version_id=ver.id,
                position=pos,
                key=f["key"],
                kind=SYSTEM_FIELDS[f["key"]],
                label=f["label"],
                required=f["required"],
            )
        )
    db.flush()
    return form


def fields_of(db: Session, version_id: uuid.UUID) -> list[OrderFormField]:
    return list(
        db.scalars(
            select(OrderFormField)
            .where(OrderFormField.version_id == version_id)
            .order_by(OrderFormField.position)
        ).all()
    )


def get_draft(db: Session, form: OrderForm, actor: uuid.UUID, *, create: bool) -> OrderFormVersion:
    """The form's DRAFT; with create=True a missing draft is copied from the published one."""
    draft = db.scalar(
        select(OrderFormVersion).where(
            OrderFormVersion.form_id == form.id, OrderFormVersion.status == "DRAFT"
        )
    )
    if draft is not None or not create:
        if draft is None:
            raise ApiError(404, "NOT_FOUND", "This form has no draft")
        return draft
    src = db.get(OrderFormVersion, form.published_version_id)
    assert src is not None  # a form always has a draft or a published version
    last = db.scalar(
        select(OrderFormVersion.version_no)
        .where(OrderFormVersion.form_id == form.id)
        .order_by(OrderFormVersion.version_no.desc())
        .limit(1)
    )
    draft = OrderFormVersion(
        company_id=form.company_id,
        form_id=form.id,
        version_no=(last or 0) + 1,
        title=src.title,
        intro=src.intro,
        success_message=src.success_message,
        theme=dict(src.theme),
        logo_id=src.logo_id,
        show_prices=src.show_prices,
        product_scope=src.product_scope,
        scope_ids=list(src.scope_ids),
        created_by=actor,
    )
    db.add(draft)
    db.flush()
    for f in fields_of(db, src.id):
        db.add(
            OrderFormField(
                version_id=draft.id,
                position=f.position,
                key=f.key,
                kind=f.kind,
                label=f.label,
                help_text=f.help_text,
                required=f.required,
                options=list(f.options),
            )
        )
    db.flush()
    return draft


def check_fields(fields: list[Any]) -> None:
    """Structure rules for a field list (pydantic already checked lengths and types)."""
    if not 1 <= len(fields) <= MAX_FIELDS:
        raise ApiError(422, "INVALID_FIELDS", f"A form has 1 to {MAX_FIELDS} fields")
    seen: set[str] = set()
    for i, f in enumerate(fields, start=1):
        if f.key in seen:
            raise ApiError(422, "DUPLICATE_FIELD", f"Field {i}: {f.key} appears twice", field=i)
        seen.add(f.key)
        if f.key in SYSTEM_FIELDS:
            if f.kind != SYSTEM_FIELDS[f.key]:
                raise ApiError(
                    422, "INVALID_FIELD", f"Field {i}: {f.key} must be {SYSTEM_FIELDS[f.key]}"
                )
        elif not CUSTOM_KEY.match(f.key) or f.kind not in CUSTOM_KINDS:
            raise ApiError(
                422,
                "INVALID_FIELD",
                f"Field {i}: custom fields use a custom_… key and a text, select or checkbox type",
                field=i,
            )
        if f.kind == "SELECT":
            opts = [o.strip() for o in f.options]
            if not 2 <= len(opts) <= 50 or any(not o for o in opts) or len(set(opts)) != len(opts):
                raise ApiError(
                    422,
                    "INVALID_OPTIONS",
                    f"Field {i}: a choice list needs 2 to 50 different, non-empty options",
                    field=i,
                )
        elif f.options:
            raise ApiError(422, "INVALID_OPTIONS", f"Field {i}: only choice lists have options")


def check_publishable(fields: list[OrderFormField]) -> None:
    """Publish rules: a submission must carry products, a name to match a customer and a way
    to reach them (owner decision OC-D7 may change this)."""
    req = {f.key for f in fields if f.required}
    missing = []
    if "products" not in req:
        missing.append("a required Products field")
    if not req & {"customer_name", "business_name"}:
        missing.append("a required name or business name")
    if not req & {"email", "phone"}:
        missing.append("a required email or phone")
    if missing:
        raise ApiError(
            422, "NOT_PUBLISHABLE", "Before publishing, add " + ", ".join(missing), missing=missing
        )


def check_scope(db: Session, company_id: uuid.UUID, scope: str, ids: list[uuid.UUID]) -> None:
    if scope == "ALL":
        if ids:
            raise ApiError(422, "INVALID_SCOPE", "All products takes no category or product list")
        return
    if not ids:
        raise ApiError(422, "INVALID_SCOPE", "Pick at least one category or product")
    model = ProductCategory if scope == "CATEGORIES" else Product
    found = set(
        db.scalars(select(model.id).where(model.id.in_(ids), model.company_id == company_id)).all()
    )
    if len(found) != len(set(ids)):
        raise ApiError(422, "INVALID_SCOPE", "Some categories or products were not found")


def publish(db: Session, form: OrderForm, draft: OrderFormVersion, actor: uuid.UUID) -> None:
    fields = fields_of(db, draft.id)
    check_publishable(fields)
    if form.published_version_id is not None:
        old = db.get(OrderFormVersion, form.published_version_id)
        assert old is not None
        old.status = "RETIRED"
        db.flush()  # free the one-published-per-form index before the draft takes it
    draft.status, draft.published_by, draft.published_at = "PUBLISHED", actor, now()
    db.flush()
    form.published_version_id = draft.id
    form.version += 1
    form.updated_at = now()


# ------------------------------------------------------------------ public config


# Box sizes offered for a product with no recorded case weight yet (owner, 2026-11-09: "some
# standard sizes" until the real product list with its case sizes is imported). A product's own
# active BOX profiles with a weight replace these automatically. OPEN_DECISIONS D-32.
STANDARD_BOX_SIZES_LB: tuple[Decimal, ...] = tuple(
    Decimal(x) for x in ("10", "20", "25", "40", "50")
)


def _units(db: Session, products: list[Product]) -> dict[uuid.UUID, list[str]]:
    """Units a customer may order in (owner, 2026-11-09: box or per lb for every product): the
    product's stock unit, LB always (catch weight, as for stock), plus BOX/PIECE units with an
    active packing profile. KG only when that is how the product is stocked."""
    out: dict[uuid.UUID, set[str]] = {p.id: {p.stock_unit, "LB"} for p in products}
    if products:
        rows = db.execute(
            select(ProductUnitProfile.product_id, ProductUnitProfile.unit_code).where(
                ProductUnitProfile.product_id.in_(list(out)),
                ProductUnitProfile.active.is_(True),
                ProductUnitProfile.unit_code.in_(("BOX", "PIECE")),
            )
        ).all()
        for pid, unit in rows:
            out[pid].add(unit)
    return {pid: sorted(u, key=UNIT_ORDER.__getitem__) for pid, u in out.items()}


def products_in_scope(db: Session, company_id: uuid.UUID, ver: OrderFormVersion) -> list[Product]:
    q = select(Product).where(Product.company_id == company_id, Product.active.is_(True))
    ids = [uuid.UUID(i) for i in ver.scope_ids]
    if ver.product_scope == "CATEGORIES":
        q = q.where(Product.category_id.in_(ids))
    elif ver.product_scope == "PRODUCTS":
        q = q.where(Product.id.in_(ids))
    return list(db.scalars(q.order_by(Product.name)).all())


def _qty(v: Decimal) -> str:
    return format(v.normalize(), "f")


def box_sizes(db: Session, products: list[Product]) -> dict[uuid.UUID, tuple[list[Decimal], bool]]:
    """Box sizes (lb) a customer can pick per product, and whether they are the standard list:
    the product's own active BOX profiles with a weight, else STANDARD_BOX_SIZES_LB."""
    own: dict[uuid.UUID, set[Decimal]] = {}
    if products:
        rows = db.execute(
            select(ProductUnitProfile.product_id, ProductUnitProfile.weight_lb_per_unit).where(
                ProductUnitProfile.product_id.in_([p.id for p in products]),
                ProductUnitProfile.active.is_(True),
                ProductUnitProfile.unit_code == "BOX",
                ProductUnitProfile.weight_lb_per_unit.is_not(None),
            )
        ).all()
        for pid, w in rows:
            if w is not None:
                own.setdefault(pid, set()).add(w.normalize())
    return {
        p.id: (sorted(own[p.id]), False) if p.id in own else (list(STANDARD_BOX_SIZES_LB), True)
        for p in products
    }


def size_text(v: Decimal) -> str:
    return format(v.normalize(), "f")


def case_sizes(db: Session, products: list[Product]) -> dict[tuple[uuid.UUID, str], str]:
    """A short case description per (product, unit) from the active packing profiles, e.g.
    "40 lb" or "24 pieces"; a range when a product packs several sizes. Nothing is invented:
    a unit with no recorded weight or count gets no description."""
    if not products:
        return {}
    rows = db.execute(
        select(
            ProductUnitProfile.product_id,
            ProductUnitProfile.unit_code,
            ProductUnitProfile.weight_lb_per_unit,
            ProductUnitProfile.pieces_per_unit,
        ).where(
            ProductUnitProfile.product_id.in_([p.id for p in products]),
            ProductUnitProfile.active.is_(True),
        )
    ).all()
    weights: dict[tuple[uuid.UUID, str], set[Decimal]] = {}
    pieces: dict[tuple[uuid.UUID, str], set[Decimal]] = {}
    for pid, unit, w, n in rows:
        if w is not None:
            weights.setdefault((pid, unit), set()).add(w)
        if n is not None:
            pieces.setdefault((pid, unit), set()).add(n)
    out: dict[tuple[uuid.UUID, str], str] = {}
    for key in set(weights) | set(pieces):
        parts = []
        for vals, word in ((weights.get(key), "lb"), (pieces.get(key), "pieces")):
            if vals:
                lo, hi = min(vals), max(vals)
                parts.append(f"{_qty(lo)} {word}" if lo == hi else f"{_qty(lo)}–{_qty(hi)} {word}")
        out[key] = " · ".join(parts)
    return out


def standard_prices(
    db: Session, company_id: uuid.UUID, product_ids: list[uuid.UUID]
) -> dict[tuple[uuid.UUID, str], tuple[Decimal, str]]:
    """STANDARD-scope rates of the price book in effect now. Customer and group prices are
    never read here, so a public form can't leak them."""
    at = now()
    ver = db.scalar(
        select(PriceBookVersion)
        .where(
            PriceBookVersion.company_id == company_id,
            PriceBookVersion.status == "ACTIVE",
            PriceBookVersion.effective_at <= at,
        )
        .order_by(PriceBookVersion.effective_at.desc())
        .limit(1)
    )
    if ver is None or not product_ids:
        return {}
    rows = db.execute(
        select(
            PriceEntry.product_id, PriceEntry.unit_code, PriceEntry.rate, PriceEntry.currency
        ).where(
            PriceEntry.version_id == ver.id,
            PriceEntry.scope == "STANDARD",
            PriceEntry.product_id.in_(product_ids),
        )
    ).all()
    return {(p, u): (r, c) for p, u, r, c in rows}


def money(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.01")))


def public_config(
    db: Session, form: OrderForm, ver: OrderFormVersion, consent: dict[str, Any]
) -> dict[str, Any]:
    """Everything the embed page needs, and nothing else: no stock, costs, customer data or
    non-standard prices."""
    products = products_in_scope(db, form.company_id, ver)
    units = _units(db, products)
    sizes = case_sizes(db, products)
    boxes = box_sizes(db, products)
    prices = (
        standard_prices(db, form.company_id, [p.id for p in products]) if ver.show_prices else {}
    )
    cat_ids = {p.category_id for p in products if p.category_id}
    cats = (
        db.execute(
            select(ProductCategory.id, ProductCategory.name)
            .where(ProductCategory.id.in_(cat_ids), ProductCategory.active.is_(True))
            .order_by(ProductCategory.name)
        ).all()
        if cat_ids
        else []
    )
    active_cats = {c for c, _ in cats}
    return {
        "form": {
            "public_id": form.public_id,
            "title": ver.title,
            "intro": ver.intro,
            "success_message": ver.success_message,
            "theme": clean_theme(ver.theme or DEFAULT_THEME),
            "logo_url": f"/api/v1/public/logos/{ver.logo_id}" if ver.logo_id else None,
            "show_prices": ver.show_prices,
            "consent": consent,
        },
        "fields": [
            {
                "key": f.key,
                "kind": f.kind,
                "label": f.label,
                "help_text": f.help_text,
                "required": f.required,
                "options": list(f.options),
            }
            for f in fields_of(db, ver.id)
        ],
        "categories": [{"id": str(c), "name": n} for c, n in cats],
        "products": [
            {
                "id": str(p.id),
                "sku": p.sku,
                "name": p.name,
                "category_id": str(p.category_id) if p.category_id in active_cats else None,
                # Same convention as the staff app (web public/products/<SKU>.webp); the page
                # falls back to initials when a product has no photo.
                "image_url": f"/products/{p.sku}.webp",
                "units": [
                    {
                        "code": u,
                        "size": sizes.get((p.id, u)),
                        # Box sizes to choose from (lb); "standard" when the product has none of
                        # its own recorded yet.
                        **(
                            {
                                "box_sizes": [size_text(x) for x in boxes[p.id][0]],
                                "standard_sizes": boxes[p.id][1],
                            }
                            if u == "BOX"
                            else {}
                        ),
                        **(
                            {"price": money(prices[(p.id, u)][0]), "currency": prices[(p.id, u)][1]}
                            if (p.id, u) in prices
                            else {}
                        ),
                    }
                    for u in units[p.id]
                ],
                # Standard prices in every unit they are kept in (e.g. per lb for a product sold
                # by the box); empty unless the form shows prices.
                "prices": [
                    {"unit": u, "price": money(r), "currency": c}
                    for (pid, u), (r, c) in sorted(
                        prices.items(), key=lambda kv: UNIT_ORDER.get(kv[0][1], 9)
                    )
                    if pid == p.id
                ],
            }
            for p in products
        ],
    }


def consent_of(s: dict[str, Any]) -> dict[str, Any]:
    return {"required": bool(s["consent_required"]), "text": s["consent_text"]}


# ------------------------------------------------------------------ logos

LOGO_MAX_INPUT = 1024 * 1024
LOGO_MAX_OUTPUT = 512 * 1024
LOGO_MAX_SIDE = 800
LOGO_MAX_PIXELS = 25_000_000


def store_logo(db: Session, company_id: uuid.UUID, actor: uuid.UUID, data: bytes) -> OrderFormLogo:
    """Decode a PNG/JPEG/WebP upload and re-encode it as a fresh PNG (no metadata, no trailing
    bytes, no SVG). Anything Pillow can't fully decode is refused."""
    if not data or len(data) > LOGO_MAX_INPUT:
        raise ApiError(413, "LOGO_TOO_LARGE", "Logos must be 1 MB or smaller")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ("PNG", "JPEG", "WEBP"):
                raise ApiError(415, "LOGO_TYPE", "Use a PNG, JPEG or WebP logo")
            if probe.width * probe.height > LOGO_MAX_PIXELS:
                raise ApiError(422, "LOGO_DIMENSIONS", "That image is too large in pixels")
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            out = img.convert("RGBA")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ApiError(415, "LOGO_TYPE", "That file is not a readable PNG, JPEG or WebP") from None
    out.thumbnail((LOGO_MAX_SIDE, LOGO_MAX_SIDE))
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    png = buf.getvalue()
    if len(png) > LOGO_MAX_OUTPUT:
        raise ApiError(413, "LOGO_TOO_LARGE", "That logo is still over 512 KB as a PNG")
    digest = hashlib.sha256(png).hexdigest()
    existing = db.scalar(
        select(OrderFormLogo).where(
            OrderFormLogo.company_id == company_id, OrderFormLogo.sha256 == digest
        )
    )
    if existing is not None:
        return existing
    logo = OrderFormLogo(
        company_id=company_id,
        sha256=digest,
        mime="image/png",
        content=png,
        byte_size=len(png),
        width=out.width,
        height=out.height,
        created_by=actor,
    )
    db.add(logo)
    db.flush()
    return logo
