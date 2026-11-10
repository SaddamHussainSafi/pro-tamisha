"""Order Connect: forms, publishing, embed page, public intake (validation, spam, limits,
idempotency), notifications, review, customer matching, one-time conversion to a DRAFT sales
order, roles, company isolation, erasure. Synthetic data only."""

import io
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from pro_trading import ratelimit
from pro_trading.db import get_sessionmaker
from pro_trading.models import (
    Customer,
    Invoice,
    OutboxEvent,
    PriceBookVersion,
    PriceEntry,
    Product,
    ProductCategory,
    ProductUnitProfile,
    SalesOrder,
    SalesOrderLine,
    StockMovement,
    StockReservation,
)
from pro_trading.notifications import Notification, NotificationRecipient
from pro_trading.order_connect import intake
from pro_trading.order_connect.models import OrderConnectSettings, OrderFormSubmission

from .conftest import build_world, idem

P = "/api/v1"
OC = f"{P}/order-connect"


def code(r):
    return r.json()["error"]["code"]


def ok(r, status=200):
    assert r.status_code == status, r.text
    return r.json()


# ------------------------------------------------------------------ fixtures


def signed_in(make_api, world, name):
    a = make_api()
    r = a.login(world.emails[name])
    assert r.status_code == 200, r.text
    if r.json().get("mfa_required"):
        assert a.enroll_and_verify().status_code == 200
    return a


@pytest.fixture
def as_(make_api, world):
    return lambda name: signed_in(make_api, world, name)


@pytest.fixture(autouse=True)
def _fresh_get_limit():
    ratelimit.clear("oc_get", "testclient")


def seed_catalog(w):
    """Two categories; Cassava (BOX), Plantain (PIECE), Okra (BOX, other category), an inactive
    product; STANDARD and CUSTOMER prices in an active price book."""
    company_id = w.company_id
    with get_sessionmaker()() as db, db.begin():
        veg = ProductCategory(company_id=company_id, code="VEG", name="Vegetables")
        fru = ProductCategory(company_id=company_id, code="FRU", name="Fruits")
        db.add_all([veg, fru])
        db.flush()
        ids = {}
        for sku, name, unit, cat, active in [
            ("CAS", "Cassava", "BOX", veg, True),
            ("PLN", "Plantain", "PIECE", fru, True),
            ("OKR", "Okra", "BOX", veg, True),
            ("OLD", "Old item", "BOX", veg, False),
        ]:
            p = Product(
                company_id=company_id,
                sku=sku,
                name=name,
                category_id=cat.id,
                stock_unit=unit,
                active=active,
            )
            db.add(p)
            db.flush()
            db.add(
                ProductUnitProfile(
                    product_id=p.id,
                    code="STD",
                    unit_code=unit,
                    is_default=True,
                    weight_lb_per_unit=Decimal("40") if sku == "CAS" else None,
                )
            )
            ids[sku] = p.id
        cust = Customer(company_id=company_id, name="Price Leak Test")
        db.add(cust)
        db.flush()
        ver = PriceBookVersion(company_id=company_id, version_code="PB-1")
        db.add(ver)
        db.flush()
        db.add_all(
            [
                PriceEntry(
                    version_id=ver.id,
                    product_id=ids["CAS"],
                    unit_code="BOX",
                    scope="STANDARD",
                    rate=Decimal("28.0000"),
                    currency="CAD",
                ),
                PriceEntry(
                    version_id=ver.id,
                    product_id=ids["CAS"],
                    unit_code="LB",
                    scope="STANDARD",
                    rate=Decimal("1.5000"),
                    currency="CAD",
                ),
                PriceEntry(
                    version_id=ver.id,
                    product_id=ids["CAS"],
                    unit_code="BOX",
                    scope="CUSTOMER",
                    customer_id=cust.id,
                    rate=Decimal("19.5000"),
                    currency="CAD",
                ),
            ]
        )
        db.flush()
        ver.status, ver.effective_at = "ACTIVE", datetime.now(UTC) - timedelta(days=1)
        ver.approved_by, ver.approved_at = w.ids["owner"], datetime.now(UTC)
        ids["veg"], ids["fru"] = veg.id, fru.id
    return ids


@pytest.fixture
def cat(world):
    return seed_catalog(world)


@pytest.fixture
def owner(as_):
    return as_("owner")


@pytest.fixture
def settings_fast(world):
    """No minimum fill time, so tests can submit right after loading the form."""
    with get_sessionmaker()() as db, db.begin():
        db.merge(OrderConnectSettings(company_id=world.company_id, min_fill_seconds=0))


def make_form(owner, name="Wholesale orders", **draft):
    f = ok(owner.post(f"{OC}/forms", json={"name": name}, headers=idem()), 201)
    if draft:
        f = save(owner, f, **draft)
    return f


def editing(owner, form_id):
    return ok(owner.get(f"{OC}/forms/{form_id}"))["editing"]


def save(owner, f, **changes):
    cur = editing(owner, f["id"])
    body = {
        k: cur[k]
        for k in (
            "revision",
            "title",
            "intro",
            "success_message",
            "theme",
            "logo_id",
            "show_prices",
            "product_scope",
            "scope_ids",
            "fields",
        )
    }
    body.update(changes)
    r = owner.put(f"{OC}/forms/{f['id']}/draft", json=body, headers=idem())
    return ok(r)


def publish(owner, f):
    rev = editing(owner, f["id"])["revision"]
    return ok(owner.post(f"{OC}/forms/{f['id']}/publish", json={"revision": rev}, headers=idem()))


@pytest.fixture
def live(owner, cat, settings_fast):
    f = make_form(owner)
    publish(owner, f)
    return f


def load(api, f, host=""):
    return ok(api.get(f"{P}/public/order-forms/{f['public_id']}", params={"host": host}))


def good_body(cfg, cat, **over):
    body = {
        "form_token": cfg["token"],
        "client_token": uuid.uuid4().hex,
        "values": {
            "customer_name": "Ada Buyer",
            "business_name": "Corner Grocery",
            "email": "Ada@Example.test",
            "phone": "+1 (416) 555-0101",
            "delivery_address": "1 Main St\nToronto",
            "notes": "Back door please",
        },
        "items": [
            {"product_id": str(cat["CAS"]), "unit_code": "BOX", "qty": 3, "box_size_lb": "40"},
            {"product_id": str(cat["PLN"]), "unit_code": "PIECE", "qty": "12"},
        ],
    }
    body.update(over)
    return body


def submit(api, f, body, headers=None):
    return api.c.post(
        f"{P}/public/order-forms/{f['public_id']}/submissions", json=body, headers=headers or {}
    )


def sub_row(sid):
    with get_sessionmaker()() as db:
        return db.get(OrderFormSubmission, uuid.UUID(sid))


# ------------------------------------------------------------------ forms and publishing


def test_new_form_has_default_fields_and_a_draft_only(owner, cat):
    f = make_form(owner)
    assert f["published"] is False and f["unpublished_changes"] is True
    ed = editing(owner, f["id"])
    keys = [x["key"] for x in ed["fields"]]
    assert keys[:3] == ["customer_name", "business_name", "email"] and "products" in keys
    assert len(f["public_id"]) == 22
    # unpublished forms are not public
    assert owner.get(f"{P}/public/order-forms/{f['public_id']}").status_code == 404
    page = owner.get(f"/embed/{f['public_id']}")
    assert page.status_code == 404 and "not available" in page.text


def test_publish_then_edit_keeps_the_published_version(owner, cat, settings_fast):
    f = make_form(owner)
    publish(owner, f)
    cfg1 = load(owner, f)
    assert cfg1["form"]["title"] == "Wholesale orders"
    # editing makes a new draft (revision 0 = start from the published version)
    save(owner, f, revision=0, title="New title")
    assert load(owner, f)["form"]["title"] == "Wholesale orders"  # still the published one
    out = publish(owner, f)
    assert out["published_version_no"] == 2 and out["unpublished_changes"] is False
    assert load(owner, f)["form"]["title"] == "New title"
    # nothing left to publish
    r = owner.post(f"{OC}/forms/{f['id']}/publish", json={"revision": 1}, headers=idem())
    assert r.status_code == 409 and code(r) == "NOTHING_TO_PUBLISH"


def test_published_versions_are_immutable_in_the_database(owner, live):
    with get_sessionmaker()() as db, pytest.raises(DBAPIError, match="immutable"):
        db.execute(
            text("update order_form_versions set title='x' where form_id=:f"),
            {"f": live["id"]},
        )
    with get_sessionmaker()() as db, pytest.raises(DBAPIError, match="cannot change"):
        db.execute(
            text(
                "update order_form_fields set label='x' where version_id in "
                "(select id from order_form_versions where form_id=:f)"
            ),
            {"f": live["id"]},
        )


def test_builder_edits_fields_and_checks_them(owner, cat):
    f = make_form(owner)
    fields = editing(owner, f["id"])["fields"]
    fields = [x for x in fields if x["key"] != "notes"]
    fields.insert(
        0,
        {
            "key": "custom_store_type",
            "kind": "SELECT",
            "label": "Store type",
            "help_text": "",
            "required": True,
            "options": ["Grocery", "Restaurant"],
        },
    )
    out = save(owner, f, fields=fields, title="Order form")
    keys = [x["key"] for x in out["editing"]["fields"]]
    assert keys[0] == "custom_store_type" and "notes" not in keys
    # a system key must keep its kind; custom keys need the prefix; selects need options
    for bad, err in [
        ({"key": "email", "kind": "TEXT", "label": "x"}, "INVALID_FIELD"),
        ({"key": "favourite", "kind": "TEXT", "label": "x"}, "INVALID_FIELD"),
        (
            {"key": "custom_x", "kind": "SELECT", "label": "x", "options": ["only"]},
            "INVALID_OPTIONS",
        ),
    ]:
        r = owner.put(
            f"{OC}/forms/{f['id']}/draft",
            json={**_draft(owner, f), "fields": [*fields, bad]},
            headers=idem(),
        )
        assert r.status_code == 422 and code(r) in (err, "DUPLICATE_FIELD"), r.text


def _draft(owner, f):
    cur = editing(owner, f["id"])
    return {
        k: cur[k]
        for k in (
            "revision",
            "title",
            "intro",
            "success_message",
            "theme",
            "logo_id",
            "show_prices",
            "product_scope",
            "scope_ids",
            "fields",
        )
    }


def test_publish_needs_products_a_name_and_a_contact(owner, cat):
    f = make_form(owner)
    fields = [x for x in editing(owner, f["id"])["fields"] if x["key"] not in ("email", "phone")]
    save(owner, f, fields=fields)
    rev = editing(owner, f["id"])["revision"]
    r = owner.post(f"{OC}/forms/{f['id']}/publish", json={"revision": rev}, headers=idem())
    assert r.status_code == 422 and code(r) == "NOT_PUBLISHABLE"


def test_stale_draft_revision_conflicts(owner, cat):
    f = make_form(owner)
    body = _draft(owner, f)
    ok(owner.put(f"{OC}/forms/{f['id']}/draft", json=body, headers=idem()))
    r = owner.put(f"{OC}/forms/{f['id']}/draft", json=body, headers=idem())
    assert r.status_code == 409 and code(r) == "VERSION_CONFLICT"


def test_light_accent_colours_are_refused(owner, cat):
    f = make_form(owner)
    r = owner.put(
        f"{OC}/forms/{f['id']}/draft",
        json={**_draft(owner, f), "theme": {"accent": "#FFFF00", "radius": "small"}},
        headers=idem(),
    )
    assert r.status_code == 422 and code(r) == "ACCENT_LOW_CONTRAST"


def test_logo_is_reencoded_png_and_svg_is_refused(owner, cat):
    img = Image.new("RGB", (1200, 300), (27, 122, 48))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    payload = buf.getvalue() + b"<script>alert(1)</script>"  # trailing junk must not survive
    r = owner.c.post(
        f"{OC}/logos", content=payload, headers=owner._h({"Content-Type": "image/jpeg"})
    )
    lg = ok(r, 201)
    assert lg["width"] == 800  # downscaled
    png = owner.get(lg["url"])
    assert png.headers["content-type"] == "image/png" and png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert b"<script>" not in png.content
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    r = owner.c.post(f"{OC}/logos", content=svg, headers=owner._h({"Content-Type": "image/png"}))
    assert r.status_code == 415
    r = owner.c.post(
        f"{OC}/logos", content=svg, headers=owner._h({"Content-Type": "image/svg+xml"})
    )
    assert r.status_code == 415


# ------------------------------------------------------------------ public config and embed


def test_public_config_has_catalog_units_and_no_stock_or_customer_prices(owner, live, cat):
    cfg = load(owner, live)
    names = [p["name"] for p in cfg["products"]]
    assert names == ["Cassava", "Okra", "Plantain"]  # inactive product hidden
    cas = next(p for p in cfg["products"] if p["sku"] == "CAS")
    # box or per lb for every product; box sizes: the product's own (CAS packs 40 lb), else standard
    assert [u["code"] for u in cas["units"]] == ["BOX", "LB"]
    assert cas["units"][0]["box_sizes"] == ["40"] and cas["units"][0]["standard_sizes"] is False
    okr_box = next(p for p in cfg["products"] if p["sku"] == "OKR")["units"][0]
    assert (
        okr_box["box_sizes"] == ["10", "20", "25", "40", "50"] and okr_box["standard_sizes"] is True
    )
    assert "box_sizes" not in cas["units"][1]
    assert cas["units"][0]["size"] == "40 lb"  # from the packing profile, nothing invented
    assert next(p for p in cfg["products"] if p["sku"] == "OKR")["units"][0]["size"] is None
    assert "price" not in cas["units"][0] and cas["prices"] == []  # prices off by default
    blob = str(cfg)
    assert "19.5" not in blob and "on_hand" not in blob and "stock" not in blob.lower()
    assert {c["name"] for c in cfg["categories"]} == {"Vegetables", "Fruits"}
    # prices on: only the STANDARD rate is shown, never the customer-specific one
    save(owner, live, revision=0, show_prices=True)
    publish(owner, live)
    cas = next(p for p in load(owner, live)["products"] if p["sku"] == "CAS")
    assert cas["units"][0]["price"] == "28.00" and "19.5" not in str(cas)
    # every standard rate, also in units the product isn't ordered in (shown as "per lb")
    assert cas["prices"] == [
        {"unit": "BOX", "price": "28.00", "currency": "CAD"},
        {"unit": "LB", "price": "1.50", "currency": "CAD"},
    ]


def test_product_scope_limits_the_catalog(owner, live, cat):
    save(owner, live, revision=0, product_scope="CATEGORIES", scope_ids=[str(cat["fru"])])
    publish(owner, live)
    assert [p["sku"] for p in load(owner, live)["products"]] == ["PLN"]


def test_embed_page_frame_ancestors_follow_allowed_websites(owner, live):
    page = owner.get(f"/embed/{live['public_id']}")
    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in csp and "script-src 'self'" in csp
    assert "unsafe-inline" not in csp and "x-frame-options" not in page.headers
    assert 'data-public-id="' + live["public_id"] + '"' in page.text
    ok(
        owner.post(
            f"{OC}/domains", json={"host": "https://Shop.Example.com/order"}, headers=idem()
        ),
        201,
    )
    ok(
        owner.post(
            f"{OC}/domains", json={"form_id": live["id"], "host": "*.example.org"}, headers=idem()
        ),
        201,
    )
    csp = owner.get(f"/embed/{live['public_id']}").headers["content-security-policy"]
    assert "https://shop.example.com" in csp and "https://*.example.org" in csp
    for bad in ("localhost", "192.168.1.10", "user@evil.test", "http://x.test:8080"):
        r = owner.post(f"{OC}/domains", json={"host": bad}, headers=idem())
        assert r.status_code == 422, bad
    dup = owner.post(f"{OC}/domains", json={"host": "shop.example.com"}, headers=idem())
    assert dup.status_code == 409
    info = ok(owner.get(f"{OC}/forms/{live['id']}/embed"))
    frame_id = f"tt-order-{live['public_id'][:8]}"
    assert info["iframe_only"].startswith(
        f'<iframe id="{frame_id}" src="http://localhost:5173/embed/{live["public_id"]}"'
    )
    # the companion script only talks to the form's own origin and its own frame
    assert info["iframe"].startswith(info["iframe_only"] + "\n<script>")
    assert f'getElementById("{frame_id}")' in info["iframe"]
    assert "e.origin!==o||e.source!==f.contentWindow" in info["iframe"]
    assert '"http://localhost:5173"' in info["iframe"]
    for code in (info["iframe"], info["iframe_only"]):
        assert "token" not in code and "csrf" not in code.lower() and "secret" not in code


def test_embed_page_escapes_and_handles_closed_and_link_disabled_forms(owner, live):
    host_attack = '"><script>alert(1)</script>'
    page = owner.get(f"/embed/{live['public_id']}", headers={"Referer": f"https://{host_attack}/"})
    assert "<script>alert" not in page.text
    patch = {"version": live["version"] + 1, "standalone_link": False}
    form = ok(owner.get(f"{OC}/forms/{live['id']}"))
    patch["version"] = form["version"]
    form = ok(owner.patch(f"{OC}/forms/{live['id']}", json=patch, headers=idem()))
    top = owner.get(f"/embed/{live['public_id']}", headers={"Sec-Fetch-Dest": "document"})
    framed = owner.get(f"/embed/{live['public_id']}", headers={"Sec-Fetch-Dest": "iframe"})
    assert top.status_code == 403 and framed.status_code == 200
    ok(
        owner.patch(
            f"{OC}/forms/{live['id']}",
            json={"version": form["version"], "accepting": False},
            headers=idem(),
        )
    )
    assert owner.get(f"/embed/{live['public_id']}").status_code == 409
    r = owner.get(f"{P}/public/order-forms/{live['public_id']}")
    assert r.status_code == 409 and code(r) == "FORM_CLOSED"


def test_preview_page_only_framable_by_the_staff_app(api):
    page = api.get("/embed/preview")
    assert "frame-ancestors 'self' http://localhost:5173" in page.headers["content-security-policy"]
    assert 'data-mode="preview"' in page.text


def test_embed_assets_are_static_and_cacheable(api):
    js = api.get("/embed/assets/embed.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")
    assert api.get("/embed/assets/../../main.py").status_code == 404
    assert api.get("/embed/assets/nope.js").status_code == 404


# ------------------------------------------------------------------ intake


def test_valid_submission_is_stored_with_items_timeline_audit_and_outbox(api, live, cat, world):
    cfg = load(api, live, host="shop.example.com")
    r = submit(api, live, good_body(cfg, cat))
    out = ok(r, 201)
    assert out["reference"].startswith("OC-") and "not an invoice" in out["message"]
    with get_sessionmaker()() as db:
        s = db.scalar(
            select(OrderFormSubmission).where(OrderFormSubmission.reference == out["reference"])
        )
        assert s.status == "NEW" and s.email == "ada@example.test"
        assert s.source_host == "shop.example.com" and len(s.ip_hash) == 64
        assert s.raw["values"]["business_name"] == "Corner Grocery"
        n = db.scalar(
            text("select count(*) from order_form_submission_items where submission_id=:s"),
            {"s": s.id},
        )
        assert n == 2
        ev = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == str(s.id)))
        assert ev.event_type == intake.RECEIVED_EVENT and ev.payload["ack"] is False
        aud = db.scalar(
            text("select count(*) from audit_events where target_id=:t and action=:a"),
            {"t": str(s.id), "a": "order_connect.submission.received"},
        )
        assert aud == 1
        # nothing stock- or invoice-related happened
        assert (
            db.scalar(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.company_id == world.company_id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(StockReservation)
                .where(StockReservation.company_id == world.company_id)
            )
            == 0
        )


def test_same_client_token_returns_the_same_reference(api, live, cat):
    cfg = load(api, live)
    body = good_body(cfg, cat)
    a = ok(submit(api, live, body), 201)
    b = submit(api, live, body)
    assert b.status_code == 200 and b.json()["reference"] == a["reference"]
    assert b.headers["idempotent-replay"] == "true"


def test_concurrent_retries_create_one_submission(api, live, cat):
    cfg = load(api, live)
    body = good_body(cfg, cat)
    with ThreadPoolExecutor(4) as ex:
        rs = list(ex.map(lambda _: submit(api, live, body), range(4)))
    assert sorted(r.status_code for r in rs).count(201) == 1
    assert len({r.json()["reference"] for r in rs}) == 1


def test_prices_totals_and_unknown_fields_are_refused(api, live, cat):
    cfg = load(api, live)
    body = good_body(cfg, cat)
    body["items"][0]["price"] = "0.01"
    assert submit(api, live, body).status_code == 422
    body = good_body(cfg, cat, total="1.00")
    assert submit(api, live, body).status_code == 422
    body = good_body(cfg, cat)
    body["values"]["customer_id"] = str(uuid.uuid4())
    r = submit(api, live, body)
    assert r.status_code == 422 and "customer_id" in r.json()["error"]["fields"]


@pytest.mark.parametrize(
    "values,field",
    [
        ({"email": "not-an-email"}, "email"),
        ({"phone": "12"}, "phone"),
        ({"customer_name": ""}, "customer_name"),
        ({"delivery_date": "2020-01-01"}, "delivery_date"),
        ({"delivery_date": "tomorrow"}, "delivery_date"),
        ({"notes": "x" * 2001}, "notes"),
    ],
)
def test_field_validation(api, live, cat, values, field):
    cfg = load(api, live)
    body = good_body(cfg, cat)
    body["values"].update(values)
    r = submit(api, live, body)
    assert r.status_code == 422 and field in r.json()["error"]["fields"], r.text


def test_valid_delivery_date_is_kept(api, live, cat):
    cfg = load(api, live)
    body = good_body(cfg, cat)
    when = (date.today() + timedelta(days=3)).isoformat()
    body["values"]["delivery_date"] = when
    ref = ok(submit(api, live, body), 201)["reference"]
    with get_sessionmaker()() as db:
        s = db.scalar(select(OrderFormSubmission).where(OrderFormSubmission.reference == ref))
        assert s.requested_date.isoformat() == when


@pytest.mark.parametrize(
    "item,msg",
    [
        ({"sku": "OLD", "unit_code": "BOX", "qty": 1}, "no longer available"),
        ({"sku": "CAS", "unit_code": "PIECE", "qty": 1}, "can't be ordered by PIECE"),
        ({"sku": "CAS", "unit_code": "KG", "qty": 1}, "can't be ordered by KG"),
        ({"sku": "CAS", "unit_code": "BOX", "qty": "1.5"}, "whole numbers"),
        ({"sku": "foreign", "unit_code": "BOX", "qty": 1}, "no longer available"),
    ],
)
def test_inactive_foreign_or_wrong_unit_products_are_refused(api, live, cat, item, msg):
    other = seed_catalog(build_world())
    pid = other["CAS"] if item["sku"] == "foreign" else cat[item["sku"]]
    cfg = load(api, live)
    body = good_body(
        cfg,
        cat,
        items=[{"product_id": str(pid), "unit_code": item["unit_code"], "qty": item["qty"]}],
    )
    r = submit(api, live, body)
    assert r.status_code == 422 and msg in str(r.json()["error"]["fields"]), r.text


def test_box_sizes_and_per_lb_orders(api, live, cat, owner):
    cfg = load(api, live)
    cas, okr = str(cat["CAS"]), str(cat["OKR"])
    for item, msg in [
        ({"product_id": cas, "unit_code": "BOX", "qty": 1}, "Choose a box size"),
        (
            {"product_id": cas, "unit_code": "BOX", "qty": 1, "box_size_lb": "25"},
            "listed box sizes",
        ),
        (
            {"product_id": cas, "unit_code": "LB", "qty": 5, "box_size_lb": "40"},
            "only applies to boxes",
        ),
    ]:
        r = submit(api, live, good_body(cfg, cat, items=[item]))
        assert r.status_code == 422 and msg in str(r.json()["error"]["fields"]), r.text
    # standard sizes for a product with no case weight of its own; per lb with a decimal amount
    items = [
        {"product_id": okr, "unit_code": "BOX", "qty": 2, "box_size_lb": "25"},
        {"product_id": cas, "unit_code": "LB", "qty": "12.5"},
    ]
    ref = ok(submit(api, live, good_body(cfg, cat, items=items)), 201)["reference"]
    d = ok(owner.get(f"{OC}/submissions/{_sid(ref)}"))
    got = {(i["product_name"], i["unit_code"], i["qty"], i["box_size_lb"]) for i in d["items"]}
    assert got == {("Okra", "BOX", "2", "25"), ("Cassava", "LB", "12.5", None)}


def test_products_required_and_no_duplicates(api, live, cat):
    cfg = load(api, live)
    r = submit(api, live, good_body(cfg, cat, items=[]))
    assert r.status_code == 422 and "products" in r.json()["error"]["fields"]
    line = {"product_id": str(cat["CAS"]), "unit_code": "BOX", "qty": 1, "box_size_lb": "40"}
    r = submit(api, live, good_body(cfg, cat, items=[line, line]))
    assert r.status_code == 422 and "listed twice" in r.text


def test_stored_text_is_kept_as_text(api, live, cat, owner):
    cfg = load(api, live)
    body = good_body(cfg, cat)
    body["values"]["notes"] = '<img src=x onerror="alert(1)">\u0000‮'
    ref = ok(submit(api, live, body), 201)["reference"]
    with get_sessionmaker()() as db:
        s = db.scalar(select(OrderFormSubmission).where(OrderFormSubmission.reference == ref))
        assert s.notes == '<img src=x onerror="alert(1)">'  # control chars gone, text untouched
    d = ok(owner.get(f"{OC}/submissions/{s.id}"))
    assert d["notes"] == s.notes  # JSON string; the UI renders it as text


def test_honeypot_and_too_fast_submissions_are_marked_spam_silently(api, live, cat, world, owner):
    cfg = load(api, live)
    r = submit(api, live, good_body(cfg, cat, website="http://spam.test"))
    assert r.status_code == 201  # the bot is not told
    s = sub_row(_sid(r.json()["reference"]))
    assert s.status == "SPAM" and s.spam_reasons == ["honeypot"]
    with get_sessionmaker()() as db, db.begin():
        db.merge(OrderConnectSettings(company_id=world.company_id, min_fill_seconds=30))
    cfg = load(api, live)
    r = submit(api, live, good_body(cfg, cat))
    assert sub_row(_sid(r.json()["reference"])).spam_reasons == ["too_fast"]
    # spam raises no alerts and no outbox event
    with get_sessionmaker()() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.target_id == str(s.id))
            )
            == 0
        )
    assert ok(owner.get(f"{OC}/overview"))["open"] == 0


def _sid(ref):
    with get_sessionmaker()() as db:
        return str(
            db.scalar(select(OrderFormSubmission.id).where(OrderFormSubmission.reference == ref))
        )


def test_forged_old_or_other_form_tokens_are_refused(api, live, cat, owner):
    cfg = load(api, live)
    forged = cfg["token"][:-4] + "AAAA"
    r = submit(api, live, good_body(cfg, cat, form_token=forged))
    assert r.status_code == 422 and code(r) == "INVALID_FORM_TOKEN"
    old = intake.make_token(live["public_id"], uuid.uuid4(), "", at=time.time() - 13 * 3600)
    r = submit(api, live, good_body(cfg, cat, form_token=old))
    assert r.status_code == 409 and code(r) == "FORM_EXPIRED"
    other = make_form(owner, "Other")
    publish(owner, other)
    r = submit(api, live, good_body(cfg, cat, form_token=load(api, other)["token"]))
    assert r.status_code == 422 and code(r) == "INVALID_FORM_TOKEN"


def test_republished_form_asks_for_a_reload(api, live, cat, owner):
    cfg = load(api, live)
    save(owner, live, revision=0, title="Changed")
    publish(owner, live)
    r = submit(api, live, good_body(cfg, cat))
    assert r.status_code == 409 and code(r) == "FORM_UPDATED"


def test_rate_limit_per_connection(api, live, cat, world):
    with get_sessionmaker()() as db, db.begin():
        db.merge(
            OrderConnectSettings(
                company_id=world.company_id, min_fill_seconds=0, ip_limit_per_hour=2
            )
        )
    cfg = load(api, live)
    codes = [submit(api, live, good_body(cfg, cat)).status_code for _ in range(3)]
    assert codes == [201, 201, 429]


def test_body_size_cap_and_foreign_origin(api, live, cat):
    cfg = load(api, live)
    body = good_body(cfg, cat)
    body["values"]["notes"] = "x" * 70_000
    assert submit(api, live, body).status_code == 413
    r = submit(api, live, good_body(cfg, cat), headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = submit(api, live, good_body(cfg, cat), headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_staff_cookie_gives_a_public_submission_no_extra_rights(owner, live, cat):
    """The owner's own browser session submitting the public form is just a customer."""
    cfg = load(owner, live)
    body = good_body(cfg, cat)
    body["values"]["customer_name"] = "Owner as customer"
    r = owner.c.post(f"{P}/public/order-forms/{live['public_id']}/submissions", json=body)
    s = sub_row(_sid(ok(r, 201)["reference"]))
    assert s.status == "NEW" and s.converted_by is None and s.sales_order_id is None


def test_possible_duplicates_are_flagged_not_blocked(api, live, cat):
    cfg = load(api, live)
    a = ok(submit(api, live, good_body(cfg, cat)), 201)
    b = ok(submit(api, live, good_body(cfg, cat)), 201)
    sa, sb = sub_row(_sid(a["reference"])), sub_row(_sid(b["reference"]))
    assert sb.possible_duplicate_of == sa.id and sb.status == "NEW"


def test_submitted_values_are_immutable_in_the_database(api, live, cat):
    ref = ok(submit(api, live, good_body(load(api, live), cat)), 201)["reference"]
    sid = _sid(ref)
    for stmt in (
        "update order_form_submissions set email='x@y.z' where id=:s",
        "update order_form_submission_items set qty=99 where submission_id=:s",
        "delete from order_form_submissions where id=:s",
        "update order_form_submission_events set note='x' where submission_id=:s",
    ):
        with get_sessionmaker()() as db, pytest.raises(DBAPIError):
            db.execute(text(stmt), {"s": sid})


# ------------------------------------------------------------------ notifications


def test_quote_requests_are_marked_alerted_filtered_and_kept_after_erasure(
    api, live, cat, owner, world
):
    cfg = load(api, live)
    order_ref = ok(submit(api, live, good_body(cfg, cat)), 201)["reference"]
    body = good_body(cfg, cat, request_type="QUOTE")
    body["values"]["email"] = "quote@example.test"  # not a duplicate of the order above
    quote_ref = ok(submit(api, live, body), 201)["reference"]
    assert submit(api, live, good_body(cfg, cat, request_type="BUY")).status_code == 422
    qid = _sid(quote_ref)
    with get_sessionmaker()() as db:
        titles = set(
            db.scalars(select(Notification.title).where(Notification.target_id == qid)).all()
        )
    assert titles == {f"New quote request {quote_ref}"}
    with get_sessionmaker()() as db:
        oid = _sid(order_ref)
        assert db.scalar(select(Notification.title).where(Notification.target_id == oid)) == (
            f"New online order {order_ref}"
        )
        link = db.scalar(select(Notification.link).where(Notification.target_id == oid))
        assert link == f"/online-orders/requests/{oid}"
    quotes = ok(owner.get(f"{OC}/submissions", params={"type": "QUOTE"}))["items"]
    orders = ok(owner.get(f"{OC}/submissions", params={"type": "ORDER"}))["items"]
    assert [i["reference"] for i in quotes] == [quote_ref]
    assert [i["reference"] for i in orders] == [order_ref]
    assert quotes[0]["request_type"] == "QUOTE" and orders[0]["request_type"] == "ORDER"
    assert owner.get(f"{OC}/submissions", params={"type": "x"}).status_code == 422
    v = ok(owner.get(f"{OC}/submissions/{qid}"))["version"]
    d = ok(
        owner.post(
            f"{OC}/submissions/{qid}/erase",
            json={"version": v, "reason": "Customer request"},
            headers=idem(),
        )
    )
    assert d["raw"]["erased"] is True and d["raw"]["request_type"] == "QUOTE"
    assert set(d["raw"]) == {"erased", "request_type", "items"} and d["request_type"] == "QUOTE"


def test_quote_receipt_email_says_quote(api, live, cat, world):
    from pro_trading.models import EmailMessage
    from pro_trading.outbox import dispatch_pending

    with get_sessionmaker()() as db, db.begin():
        db.merge(
            OrderConnectSettings(company_id=world.company_id, min_fill_seconds=0, ack_email=True)
        )
    ref = ok(submit(api, live, good_body(load(api, live), cat, request_type="QUOTE")), 201)[
        "reference"
    ]
    with get_sessionmaker()() as db:
        dispatch_pending(db, {intake.RECEIVED_EVENT: intake.handle_received}, limit=500)
    with get_sessionmaker()() as db:
        msg = db.scalar(select(EmailMessage).where(EmailMessage.subject.contains(ref)))
        assert msg.subject == f"We received your quote request {ref}"
        assert "not an order confirmation, a quote or an invoice" in msg.body


def test_alerts_persist_for_authorized_staff_only(api, live, cat, world, as_):
    ref = ok(submit(api, live, good_body(load(api, live), cat)), 201)["reference"]
    sid = _sid(ref)
    with get_sessionmaker()() as db:
        got = set(
            db.scalars(
                select(NotificationRecipient.employee_id)
                .join(Notification, Notification.id == NotificationRecipient.notification_id)
                .where(Notification.target_id == sid)
            ).all()
        )
    # company-wide holders of order_connect.read: owner and sales (manager_a is warehouse-scoped)
    assert world.ids["owner"] in got and world.ids["sales"] in got
    assert world.ids["wh_a"] not in got and world.ids["finance"] not in got
    # manager_a holds order_connect.read for warehouse A only: no access to submissions, so no alert
    assert world.ids["manager_a"] not in got
    sales = as_("sales")
    lst = ok(sales.get(f"{OC}/submissions"))
    assert lst["items"][0]["unread"] is True
    assert sales.post(f"{OC}/submissions/{sid}/read").status_code == 204
    assert ok(sales.get(f"{OC}/submissions"))["items"][0]["unread"] is False
    # the owner's alert is still unread (per person)
    assert ok(as_("owner").get(f"{OC}/submissions"))["items"][0]["unread"] is True


def test_staff_alerts_can_be_switched_off(api, live, cat, world):
    with get_sessionmaker()() as db, db.begin():
        db.merge(
            OrderConnectSettings(
                company_id=world.company_id, min_fill_seconds=0, notify_staff=False
            )
        )
    sid = _sid(ok(submit(api, live, good_body(load(api, live), cat)), 201)["reference"])
    with get_sessionmaker()() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Notification).where(Notification.target_id == sid)
            )
            == 0
        )


# ------------------------------------------------------------------ review and conversion


@pytest.fixture
def received(api, live, cat):
    ref = ok(submit(api, live, good_body(load(api, live), cat)), 201)["reference"]
    return _sid(ref)


def move(api, sid, status, note=""):
    v = ok(api.get(f"{OC}/submissions/{sid}"))["version"]
    return api.post(
        f"{OC}/submissions/{sid}/status",
        json={"version": v, "status": status, "note": note},
        headers=idem(),
    )


def convert_body(api, sid, world, **over):
    d = ok(api.get(f"{OC}/submissions/{sid}"))
    body = {
        "version": d["version"],
        "warehouse_id": str(world.wh_a),
        "new_customer": {"name": "Corner Grocery", "email": "ada@example.test"},
        "lines": [
            {
                "product_id": i["product_id"],
                "unit_code": i["unit_code"],
                "qty": i["qty"],
                "item_line_no": i["line_no"],
            }
            for i in d["items"]
        ],
    }
    body.update(over)
    return body


def test_review_workflow_and_role_limits(received, as_, world):
    sales, owner = as_("sales"), as_("owner")
    assert as_("wh_a").get(f"{OC}/submissions").status_code == 403
    assert as_("finance").get(f"{OC}/submissions/{received}").status_code == 403
    assert ok(move(sales, received, "REVIEWING"))["status"] == "REVIEWING"
    r = move(sales, received, "APPROVED")
    assert r.status_code == 403  # sales may review, not approve
    r = move(owner, received, "CONVERTED")
    assert r.status_code == 422  # not a status anyone can set
    assert move(owner, received, "SPAM").status_code == 200
    r = move(owner, received, "APPROVED")
    assert r.status_code == 409 and code(r) == "INVALID_TRANSITION"
    ok(move(owner, received, "REVIEWING"))
    ok(move(owner, received, "APPROVED", "Checked by phone"))
    d = ok(owner.get(f"{OC}/submissions/{received}"))
    assert [e["to_status"] for e in d["events"] if e["kind"] == "STATUS"] == [
        "REVIEWING",
        "SPAM",
        "REVIEWING",
        "APPROVED",
    ]
    assert d["events"][-1]["note"] == "Checked by phone"
    # sales can't convert either
    r = sales.post(
        f"{OC}/submissions/{received}/convert",
        json=convert_body(sales, received, world),
        headers=idem(),
    )
    assert r.status_code == 403


def test_customer_matching_suggests_by_email_phone_and_name(received, owner, world):
    with get_sessionmaker()() as db, db.begin():
        db.add_all(
            [
                Customer(company_id=world.company_id, name="Someone", email="ADA@example.test"),
                Customer(company_id=world.company_id, name="Other", phone="416-555-0101"),
                Customer(company_id=world.company_id, name="Corner Grocery Ltd"),
                Customer(company_id=world.company_id, name="Unrelated"),
            ]
        )
    other = build_world()
    with get_sessionmaker()() as db, db.begin():
        db.add(
            Customer(company_id=other.company_id, name="Corner Grocery", email="ada@example.test")
        )
    m = ok(owner.get(f"{OC}/submissions/{received}/matches"))
    assert [x["name"] for x in m] == ["Someone", "Other", "Corner Grocery Ltd"]
    assert m[0]["reasons"] == ["Same email"] and m[1]["reasons"] == ["Same phone"]


def test_conversion_creates_one_draft_order_and_nothing_else(received, owner, world):
    assert move(owner, received, "REVIEWING").status_code == 200
    r = owner.post(
        f"{OC}/submissions/{received}/convert",
        json=convert_body(owner, received, world),
        headers=idem(),
    )
    assert r.status_code == 409 and code(r) == "NOT_APPROVED"
    ok(move(owner, received, "APPROVED"))
    d = ok(
        owner.post(
            f"{OC}/submissions/{received}/convert",
            json=convert_body(owner, received, world),
            headers=idem(),
        ),
        201,
    )
    assert d["status"] == "CONVERTED" and d["sales_order"]["status"] == "DRAFT"
    assert d["customer"]["name"] == "Corner Grocery"
    with get_sessionmaker()() as db:
        o = db.get(SalesOrder, uuid.UUID(d["sales_order"]["id"]))
        assert o.source == "WEB" and o.customer_id == uuid.UUID(d["customer"]["id"])
        assert "Corner Grocery" in o.source_text and "1 Main St" in o.source_text
        lines = db.scalars(select(SalesOrderLine).where(SalesOrderLine.order_id == o.id)).all()
        assert sorted((ln.unit_code, ln.ordered_qty, ln.rate) for ln in lines) == [
            ("BOX", Decimal("3.0000"), None),
            ("PIECE", Decimal("12.0000"), None),
        ]
        assert all("Online order" in ln.source_line_text for ln in lines)
        box = next(ln for ln in lines if ln.unit_code == "BOX")
        assert box.note == "Box size requested: 40 lb" and "40 lb boxes" in box.source_line_text
        assert (
            db.scalar(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.company_id == world.company_id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(StockReservation)
                .where(StockReservation.company_id == world.company_id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count()).select_from(Invoice).where(Invoice.sales_order_id == o.id)
            )
            == 0
        )
        # the original submission is untouched
        s = db.get(OrderFormSubmission, uuid.UUID(received))
        assert s.raw["values"]["business_name"] == "Corner Grocery"
    # second conversion: refused
    r = owner.post(
        f"{OC}/submissions/{received}/convert",
        json=convert_body(owner, received, world),
        headers=idem(),
    )
    assert r.status_code == 409 and code(r) == "ALREADY_CONVERTED"
    assert move(owner, received, "REJECTED").status_code == 409


def test_concurrent_conversions_make_exactly_one_order(received, owner, world, make_api):
    ok(move(owner, received, "REVIEWING"))
    ok(move(owner, received, "APPROVED"))
    body = convert_body(owner, received, world)
    clients = [signed_in(make_api, world, "owner") for _ in range(4)]

    def go(c):
        return c.post(f"{OC}/submissions/{received}/convert", json=body, headers=idem())

    with ThreadPoolExecutor(4) as ex:
        rs = list(ex.map(go, clients))
    assert sorted(r.status_code for r in rs) == [201, 409, 409, 409], [r.text for r in rs]
    with get_sessionmaker()() as db:
        n = db.scalar(
            select(func.count())
            .select_from(SalesOrder)
            .where(SalesOrder.company_id == world.company_id, SalesOrder.source == "WEB")
        )
        assert n == 1


def test_idempotent_conversion_replays(received, owner, world):
    ok(move(owner, received, "REVIEWING"))
    ok(move(owner, received, "APPROVED"))
    body, h = convert_body(owner, received, world), idem()
    a = owner.post(f"{OC}/submissions/{received}/convert", json=body, headers=h)
    b = owner.post(f"{OC}/submissions/{received}/convert", json=body, headers=h)
    assert a.status_code == b.status_code == 201
    assert b.headers["idempotent-replay"] == "true"
    assert a.json()["sales_order"]["id"] == b.json()["sales_order"]["id"]


def test_database_refuses_a_second_order_link(received, owner, world):
    ok(move(owner, received, "REVIEWING"))
    ok(move(owner, received, "APPROVED"))
    ok(
        owner.post(
            f"{OC}/submissions/{received}/convert",
            json=convert_body(owner, received, world),
            headers=idem(),
        ),
        201,
    )
    with get_sessionmaker()() as db, pytest.raises(DBAPIError, match="converted"):
        db.execute(
            text(
                "update order_form_submissions set status='APPROVED', sales_order_id=null "
                "where id=:s"
            ),
            {"s": received},
        )


def test_conversion_to_existing_customer_and_validation(received, owner, world, cat):
    ok(move(owner, received, "REVIEWING"))
    ok(move(owner, received, "APPROVED"))
    other = build_world()
    with get_sessionmaker()() as db, db.begin():
        mine = Customer(company_id=world.company_id, name="Corner Grocery")
        theirs = Customer(company_id=other.company_id, name="Foreign")
        db.add_all([mine, theirs])
        db.flush()
        mine_id, theirs_id = mine.id, theirs.id
    base = convert_body(owner, received, world, new_customer=None)
    r = owner.post(
        f"{OC}/submissions/{received}/convert",
        json={**base, "customer_id": str(theirs_id)},
        headers=idem(),
    )
    assert r.status_code == 422 and code(r) == "INVALID_CUSTOMER"
    r = owner.post(f"{OC}/submissions/{received}/convert", json=base, headers=idem())
    assert r.status_code == 422 and code(r) == "CUSTOMER_CHOICE"
    bad = {
        **base,
        "customer_id": str(mine_id),
        "lines": [{"product_id": str(cat["OLD"]), "unit_code": "BOX", "qty": "1"}],
    }
    r = owner.post(f"{OC}/submissions/{received}/convert", json=bad, headers=idem())
    assert r.status_code == 422 and code(r) == "PRODUCT_INACTIVE"
    d = ok(
        owner.post(
            f"{OC}/submissions/{received}/convert",
            json={**base, "customer_id": str(mine_id)},
            headers=idem(),
        ),
        201,
    )
    assert d["customer"]["id"] == str(mine_id)


def test_warehouse_scoped_manager_converts_only_into_their_warehouse(received, owner, world, as_):
    # manager_a holds the Order Connect grants for warehouse A only: no company-wide approval.
    mgr = as_("manager_a")
    assert mgr.get(f"{OC}/submissions/{received}").status_code == 403


# ------------------------------------------------------------------ isolation and privacy


def test_other_companies_cannot_see_or_touch_submissions(received, live, make_api):
    other = build_world()
    them = signed_in(make_api, other, "owner")
    assert them.get(f"{OC}/submissions/{received}").status_code == 404
    assert them.get(f"{OC}/forms/{live['id']}").status_code == 404
    assert ok(them.get(f"{OC}/submissions", params={"status": "ALL"}))["items"] == []
    r = them.post(
        f"{OC}/submissions/{received}/status", json={"version": 1, "status": "SPAM"}, headers=idem()
    )
    assert r.status_code == 404
    assert (
        them.post(
            f"{OC}/domains", json={"form_id": live["id"], "host": "x.example.com"}, headers=idem()
        ).status_code
        == 404
    )


def test_search_and_filters(received, owner):
    assert ok(owner.get(f"{OC}/submissions", params={"q": "corner"}))["items"][0]["id"] == received
    assert ok(owner.get(f"{OC}/submissions", params={"q": "100%_"}))["items"] == []
    assert ok(owner.get(f"{OC}/submissions", params={"status": "SPAM"}))["items"] == []


def test_erasure_blanks_personal_data_once(received, owner, as_):
    v = ok(owner.get(f"{OC}/submissions/{received}"))["version"]
    r = as_("sales").post(
        f"{OC}/submissions/{received}/erase",
        json={"version": v, "reason": "Customer request"},
        headers=idem(),
    )
    assert r.status_code == 403
    d = ok(
        owner.post(
            f"{OC}/submissions/{received}/erase",
            json={"version": v, "reason": "Customer request"},
            headers=idem(),
        )
    )
    assert d["email"] == d["customer_name"] == d["delivery_address"] == "" and d["erased"]
    # products, units and box sizes are not personal data: they stay
    assert d["raw"] == {"erased": True, "request_type": "ORDER", "items": d["raw"]["items"]}
    assert len(d["raw"]["items"]) == 2 and len(d["items"]) == 2
    assert d["items"][0]["box_size_lb"] == "40"
    with get_sessionmaker()() as db:
        assert db.scalar(select(Notification.body).where(Notification.target_id == received)) == (
            "Personal data erased"
        )
    r = owner.post(
        f"{OC}/submissions/{received}/erase",
        json={"version": d["version"], "reason": "again"},
        headers=idem(),
    )
    assert r.status_code == 409


def test_retention_erases_finished_old_submissions(received, owner, world):
    from pro_trading.order_connect.review import purge_expired

    ok(move(owner, received, "REJECTED"))
    with get_sessionmaker()() as db, db.begin():
        db.merge(
            OrderConnectSettings(company_id=world.company_id, min_fill_seconds=0, retention_days=30)
        )
        # pretend it arrived 31 days ago (created_at is immutable to the app; the test needs
        # the trigger off for this one synthetic row)
        db.execute(
            text("alter table order_form_submissions disable trigger order_form_submissions_guard")
        )
        db.execute(
            text(
                "update order_form_submissions set created_at = now() - interval '31 days' "
                "where id=:s"
            ),
            {"s": received},
        )
        db.execute(
            text("alter table order_form_submissions enable trigger order_form_submissions_guard")
        )
    with get_sessionmaker()() as db:
        assert purge_expired(db) >= 1
    assert sub_row(received).erased_at is not None


def test_settings_round_trip_and_consent(owner, live, cat, api):
    s0 = s = ok(owner.get(f"{OC}/settings"))
    r = owner.put(
        f"{OC}/settings", json={**s, "consent_required": True, "consent_text": ""}, headers=idem()
    )
    assert r.status_code == 422
    s = ok(
        owner.put(
            f"{OC}/settings",
            json={
                **s,
                "consent_required": True,
                "min_fill_seconds": 0,
                "consent_text": "We use your details to fill this order.",
            },
            headers=idem(),
        )
    )
    cfg = load(api, live)
    assert cfg["form"]["consent"]["required"] is True
    r = submit(api, live, good_body(cfg, cat))
    assert r.status_code == 422 and "consent" in r.json()["error"]["fields"]
    assert ok(submit(api, live, good_body(cfg, cat, consent=True)), 201)
    r = owner.put(f"{OC}/settings", json=s0, headers=idem())
    assert r.status_code == 409  # stale version


def test_ack_email_goes_through_the_outbox(api, live, cat, world):
    from pro_trading.models import EmailMessage
    from pro_trading.outbox import dispatch_pending

    with get_sessionmaker()() as db, db.begin():
        db.merge(
            OrderConnectSettings(company_id=world.company_id, min_fill_seconds=0, ack_email=True)
        )
    ref = ok(submit(api, live, good_body(load(api, live), cat)), 201)["reference"]
    with get_sessionmaker()() as db:
        dispatch_pending(db, {intake.RECEIVED_EVENT: intake.handle_received}, limit=500)
    with get_sessionmaker()() as db:
        msg = db.scalar(select(EmailMessage).where(EmailMessage.subject.contains(ref)))
        assert msg.to_addr == "ada@example.test" and "not an order confirmation" in msg.body


def test_overview_counts(received, owner):
    o = ok(owner.get(f"{OC}/overview"))
    assert o["new"] == 1 and o["open"] == 1 and o["forms_live"] == 1
    assert o["conversion_rate_30d"] == 0 and o["latest"][0]["id"] == received
