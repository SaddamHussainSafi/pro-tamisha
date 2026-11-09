"""Price books (Module 02): versioned price lists, CSV import/preview/diff, activation, lookup.

Lifecycle: DRAFT (editable) -> ACTIVE (immutable; effective from `effective_at`) and, only while
still scheduled in the future, ACTIVE -> ARCHIVED (cancel). Prior versions stay ACTIVE as history;
resolution picks the ACTIVE version with the latest `effective_at <= at`, so an older document's
stored rate can always be traced and is never rewritten. DB trigger (migration 0003) enforces it.

Permissions: read `price.read` (any scope); drafts `price.draft`; activate/cancel `price.approve`
(company-wide, MFA-gated like every privileged grant).
"""

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .. import idempotency, pricing
from ..deps import DbDep, Principal, PrincipalDep, authorize, require_permission
from ..errors import ApiError
from ..models import (
    Company,
    Customer,
    CustomerGroup,
    PriceBookVersion,
    PriceEntry,
    Product,
)
from ..schemas import (
    ActivateIn,
    PriceDiffOut,
    PriceEntryIn,
    PriceEntryOut,
    PriceEntryPage,
    PriceEntryRemoveIn,
    PriceImportIn,
    PricePreviewIn,
    PricePreviewOut,
    PriceQuoteOut,
    PriceScope,
    PriceVersionIn,
    PriceVersionOut,
    UnitCode,
    VersionIn,
)
from ._common import esc, mutate
from .customers import get_customer

router = APIRouter(tags=["pricing"])
Draft = Depends(require_permission("price.draft"))
Approve = Depends(require_permission("price.approve"))
CLOCK_SKEW = timedelta(seconds=60)
MAX_ERRORS = 200


def _read(request: Request, p: Principal) -> None:
    authorize(request, p, "price.read", any_scope=True)


def _now() -> datetime:
    return datetime.now(UTC)


def _version(db: Session, p: Principal, vid: uuid.UUID, lock: bool = False) -> PriceBookVersion:
    v = db.get(PriceBookVersion, vid, with_for_update=lock, populate_existing=lock)
    if v is None or v.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Price book version not found")
    return v


def _check(v: PriceBookVersion, version: int, *, draft: bool = True) -> None:
    if draft and v.status != "DRAFT":
        raise ApiError(
            409, "INVALID_STATE", f"Version {v.version_code} is {v.status} and cannot be edited",
            current_status=v.status,
        )  # fmt: skip
    if v.version != version:
        raise ApiError(
            409, "VERSION_CONFLICT", "Price book changed; reload and retry",
            current_version=v.version,
        )  # fmt: skip


def _current_id(db: Session, company_id: uuid.UUID) -> uuid.UUID | None:
    cur = pricing.effective_version(db, company_id, _now())
    return cur.id if cur else None


def version_out(db: Session, v: PriceBookVersion, current_id: uuid.UUID | None) -> dict[str, Any]:
    now = _now()
    if v.status in ("DRAFT", "ARCHIVED"):
        state = v.status
    elif v.effective_at is not None and v.effective_at > now:
        state = "SCHEDULED"
    elif v.id == current_id:
        state = "CURRENT"
    else:
        state = "SUPERSEDED"
    return PriceVersionOut(
        id=v.id,
        version_code=v.version_code,
        status=v.status,
        state=state,
        notes=v.notes,
        effective_at=v.effective_at,
        source_hash=v.source_hash,
        based_on_id=v.based_on_id,
        stale=v.status == "DRAFT" and current_id is not None and v.based_on_id != current_id,
        current_version_id=current_id,
        created_by=v.created_by,
        approved_by=v.approved_by,
        approved_at=v.approved_at,
        entry_count=pricing.entry_count(db, v.id),
        version=v.version,
        created_at=v.created_at,
    ).model_dump(mode="json")


def _touch(v: PriceBookVersion) -> None:
    v.version += 1
    v.source_hash = None  # content no longer equals any uploaded file


# ---------------------------------------------------------------- versions


@router.get("/price-books/versions", response_model=list[PriceVersionOut])
def list_versions(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    status: str | None = Query(None, pattern="^(DRAFT|ACTIVE|ARCHIVED)$"),
) -> list[dict[str, Any]]:
    _read(request, p)
    q = select(PriceBookVersion).where(PriceBookVersion.company_id == p.company_id)
    if status:
        q = q.where(PriceBookVersion.status == status)
    q = q.order_by(
        PriceBookVersion.effective_at.desc().nulls_first(), PriceBookVersion.created_at.desc()
    )
    cur = _current_id(db, p.company_id)
    return [version_out(db, v, cur) for v in db.scalars(q.limit(200))]


@router.get("/price-books/versions/{version_id}", response_model=PriceVersionOut)
def get_version(
    version_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    _read(request, p)
    return version_out(db, _version(db, p, version_id), _current_id(db, p.company_id))


@router.post("/price-books/versions", status_code=201, response_model=PriceVersionOut)
def create_version(
    body: PriceVersionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Draft,
) -> JSONResponse:
    """New draft, empty or cloned from `based_on_id` (default: the current price book)."""

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        cur = _current_id(db, p.company_id)
        base_id = body.based_on_id or cur
        if body.based_on_id is not None:
            _version(db, p, body.based_on_id)
        v = PriceBookVersion(
            company_id=p.company_id,
            version_code=body.version_code,
            notes=body.notes,
            based_on_id=base_id,
            created_by=p.employee.id,
        )
        db.add(v)
        db.flush()
        if base_id is not None:
            for e in db.scalars(select(PriceEntry).where(PriceEntry.version_id == base_id)):
                db.add(
                    PriceEntry(
                        version_id=v.id,
                        product_id=e.product_id,
                        scope=e.scope,
                        customer_id=e.customer_id,
                        group_id=e.group_id,
                        unit_code=e.unit_code,
                        rate=e.rate,
                        currency=e.currency,
                    )
                )
        db.flush()
        db.refresh(v)
        return version_out(db, v, cur), str(v.id), body.model_dump(mode="json")

    return mutate(
        db, request, p, key, "price_version.create", body, 201, "price_version.create", fn,
        "Version code already exists",
    )  # fmt: skip


def _bad_csv(errors: list[pricing.RowError]) -> ApiError:
    return ApiError(
        422,
        "CSV_INVALID",
        f"{len(errors)} row error(s); nothing was saved",
        errors=[e.as_dict() for e in errors[:MAX_ERRORS]],
        error_count=len(errors),
    )


def _duplicate_of(db: Session, company_id: uuid.UUID, h: str) -> PriceBookVersion | None:
    return db.scalars(
        select(PriceBookVersion).where(
            PriceBookVersion.company_id == company_id, PriceBookVersion.source_hash == h
        )
    ).first()


@router.post("/price-books/imports/preview", response_model=PricePreviewOut)
def preview_import(
    body: PricePreviewIn, request: Request, db: DbDep, p: Principal = Draft
) -> dict[str, Any]:
    """Validate a CSV and diff it against a version (default: current). Saves nothing."""
    entries, errors = pricing.parse_csv(db, p.company_id, body.csv)
    if errors:
        return PricePreviewOut(
            valid=False,
            entry_count=len(entries),
            source_hash=None,
            duplicate_of=None,
            errors=[e.as_dict() for e in errors[:MAX_ERRORS]],
            diff=None,
        ).model_dump(mode="json")
    new = {e.key: e.rate for e in entries}
    h = pricing.canonical_hash(new)
    dup = _duplicate_of(db, p.company_id, h)
    against = body.against_version_id or _current_id(db, p.company_id)
    old = pricing.version_entries(db, _version(db, p, against).id) if against else {}
    return PricePreviewOut(
        valid=True,
        entry_count=len(entries),
        source_hash=h,
        duplicate_of=dup.id if dup else None,
        errors=[],
        diff=PriceDiffOut(against_version_id=against, **pricing.diff(db, old, new)),
    ).model_dump(mode="json")


@router.post("/price-books/versions/import", status_code=201, response_model=PriceVersionOut)
def import_version(
    body: PriceImportIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Draft,
) -> JSONResponse:
    """Create a DRAFT from a full price book CSV. All-or-nothing; the same content uploaded
    twice (any formatting/order) is rejected with 409 DUPLICATE_UPLOAD."""

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        entries, errors = pricing.parse_csv(db, p.company_id, body.csv)
        if errors:
            raise _bad_csv(errors)
        h = pricing.canonical_hash({e.key: e.rate for e in entries})
        if (dup := _duplicate_of(db, p.company_id, h)) is not None:
            raise ApiError(
                409, "DUPLICATE_UPLOAD", "Already uploaded as " + dup.version_code,
                existing_version_id=str(dup.id),
            )  # fmt: skip
        cur = _current_id(db, p.company_id)
        v = PriceBookVersion(
            company_id=p.company_id,
            version_code=body.version_code,
            notes=body.notes,
            source_hash=h,
            based_on_id=cur,
            created_by=p.employee.id,
        )
        db.add(v)
        db.flush()
        for e in entries:
            db.add(
                PriceEntry(
                    version_id=v.id,
                    product_id=e.key.product_id,
                    scope=e.key.scope,
                    customer_id=e.key.target_id if e.key.scope == "CUSTOMER" else None,
                    group_id=e.key.target_id if e.key.scope == "GROUP" else None,
                    unit_code=e.key.unit_code,
                    rate=e.rate,
                    currency=e.currency,
                )
            )
        db.flush()
        db.refresh(v)
        detail = {"version_code": body.version_code, "rows": len(entries), "source_hash": h}
        return version_out(db, v, cur), str(v.id), detail

    return mutate(
        db, request, p, key, "price_version.import", body, 201, "price_version.import", fn,
        "Version code already exists or this file was already uploaded",
    )  # fmt: skip


@router.post("/price-books/versions/{version_id}/discard")
def discard_draft(
    version_id: uuid.UUID,
    body: VersionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Draft,
) -> JSONResponse:
    """Delete a DRAFT and its entries (published versions can never be deleted)."""

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        v = _version(db, p, version_id, lock=True)
        _check(v, body.version)
        code = v.version_code
        db.execute(delete(PriceEntry).where(PriceEntry.version_id == v.id))
        db.delete(v)
        db.flush()
        out = {"id": str(version_id), "discarded": True}
        return out, str(version_id), {"version_code": code}

    return mutate(
        db, request, p, key, f"price_version.discard:{version_id}", body, 200,
        "price_version.discard", fn,
    )  # fmt: skip


@router.post("/price-books/versions/{version_id}/activate", response_model=PriceVersionOut)
def activate(
    version_id: uuid.UUID,
    body: ActivateIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Approve,
) -> JSONResponse:
    """Approve a draft. It becomes immutable and takes effect at `effective_at` (default now).
    Only new documents priced after that moment use it; issued documents keep their snapshot."""

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        # serialise activations per company so effective timestamps/NO_CHANGES checks are stable
        db.get(Company, p.company_id, with_for_update={"key_share": True})
        v = _version(db, p, version_id, lock=True)
        _check(v, body.version)
        now = _now()
        eff = body.effective_at or now
        if eff.tzinfo is None:
            raise ApiError(422, "VALIDATION", "effective_at must include a timezone")
        if eff < now - CLOCK_SKEW:
            raise ApiError(
                422, "EFFECTIVE_IN_PAST", "Effective time cannot be in the past; issued documents "
                "must keep the price they were issued with",
            )  # fmt: skip
        content = pricing.version_entries(db, v.id)
        if not content:
            raise ApiError(422, "EMPTY_VERSION", "Add at least one price before activating")
        allowed = pricing.allowed_units(db, list({k.product_id for k in content}))
        bad = sorted(
            {
                db.get(Product, k.product_id).sku  # type: ignore[union-attr]
                for k in content
                if k.unit_code not in allowed[k.product_id]
            }
        )
        if bad:
            raise ApiError(
                422, "INVALID_UNIT", "Some prices use a unit the product no longer has: "
                + ", ".join(bad[:20]), skus=bad,
            )  # fmt: skip
        latest = db.scalars(
            select(PriceBookVersion)
            .where(PriceBookVersion.company_id == p.company_id, PriceBookVersion.status == "ACTIVE")
            .order_by(PriceBookVersion.effective_at.desc())
            .limit(1)
        ).first()
        if latest is not None and pricing.version_entries(db, latest.id) == content:
            raise ApiError(
                422, "NO_CHANGES",
                f"Prices are identical to {latest.version_code}; nothing to publish",
                existing_version_id=str(latest.id),
            )  # fmt: skip
        v.status = "ACTIVE"
        v.effective_at = eff
        v.approved_by = p.employee.id
        v.approved_at = now
        v.version += 1
        db.flush()
        out = version_out(db, v, _current_id(db, p.company_id))
        return out, str(v.id), {"version_code": v.version_code, "effective_at": eff.isoformat()}

    return mutate(
        db, request, p, key, f"price_version.activate:{version_id}", body, 200,
        "price_version.activate", fn, "Another price book is already effective at that exact time",
    )  # fmt: skip


@router.post("/price-books/versions/{version_id}/archive", response_model=PriceVersionOut)
def cancel_scheduled(
    version_id: uuid.UUID,
    body: VersionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Approve,
) -> JSONResponse:
    """Cancel a scheduled (not yet effective) version. Effective history can't be archived."""

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        v = _version(db, p, version_id, lock=True)
        _check(v, body.version, draft=False)
        if v.status != "ACTIVE" or v.effective_at is None or v.effective_at <= _now():
            raise ApiError(
                409, "INVALID_STATE", "Only a scheduled price book can be cancelled",
                current_status=v.status,
            )  # fmt: skip
        v.status = "ARCHIVED"
        v.version += 1
        db.flush()
        return version_out(db, v, _current_id(db, p.company_id)), str(v.id), {}

    return mutate(
        db, request, p, key, f"price_version.archive:{version_id}", body, 200,
        "price_version.archive", fn,
    )  # fmt: skip


@router.get("/price-books/versions/{version_id}/diff", response_model=PriceDiffOut)
def version_diff(
    version_id: uuid.UUID,
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    against_version_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Diff `version_id` against another version (default: current, else what it was based on)."""
    _read(request, p)
    v = _version(db, p, version_id)
    against = against_version_id or _current_id(db, p.company_id)
    if against == v.id:
        against = v.based_on_id
    old = pricing.version_entries(db, _version(db, p, against).id) if against else {}
    d = pricing.diff(db, old, pricing.version_entries(db, v.id))
    return PriceDiffOut(against_version_id=against, **d).model_dump(mode="json")


# ---------------------------------------------------------------- entries


def _targets(db: Session, entries: list[PriceEntry]) -> dict[uuid.UUID, str]:
    cids = {e.customer_id for e in entries if e.customer_id}
    gids = {e.group_id for e in entries if e.group_id}
    out: dict[uuid.UUID, str] = {}
    if cids:
        out |= {c.id: c.name for c in db.scalars(select(Customer).where(Customer.id.in_(cids)))}
    if gids:
        out |= {
            g.id: g.code
            for g in db.scalars(select(CustomerGroup).where(CustomerGroup.id.in_(gids)))
        }
    return out


def _entry_out(e: PriceEntry, prod: Product, targets: dict[uuid.UUID, str]) -> dict[str, Any]:
    t = e.customer_id or e.group_id
    return PriceEntryOut(
        id=e.id,
        product_id=e.product_id,
        sku=prod.sku,
        product_name=prod.name,
        unit_code=e.unit_code,
        scope=e.scope,
        customer_id=e.customer_id,
        group_id=e.group_id,
        target=targets.get(t) if t else None,
        rate=e.rate,
        currency=e.currency,
    ).model_dump(mode="json")


@router.get("/price-books/versions/{version_id}/entries", response_model=PriceEntryPage)
def list_entries(
    version_id: uuid.UUID,
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    q: str | None = Query(None, max_length=100, description="SKU or product name contains"),
    unit_code: UnitCode | None = None,
    scope: PriceScope | None = None,
    category_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    cursor: str | None = Query(None, pattern=r"^\d{1,7}$", description="Opaque offset"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Alphabetical (product name, SKU, unit, scope) price grid with filters."""
    _read(request, p)
    v = _version(db, p, version_id)
    stmt = (
        select(PriceEntry, Product)
        .join(Product, Product.id == PriceEntry.product_id)
        .where(PriceEntry.version_id == v.id)
    )
    if q:
        like = f"%{esc(q.strip().lower())}%"
        stmt = stmt.where(
            or_(
                func.lower(Product.name).like(like, escape="\\"),
                func.lower(Product.sku).like(like, escape="\\"),
            )
        )
    if unit_code:
        stmt = stmt.where(PriceEntry.unit_code == unit_code)
    if scope:
        stmt = stmt.where(PriceEntry.scope == scope)
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if customer_id:
        stmt = stmt.where(PriceEntry.customer_id == customer_id)
    if group_id:
        stmt = stmt.where(PriceEntry.group_id == group_id)
    if product_id:
        stmt = stmt.where(PriceEntry.product_id == product_id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    off = int(cursor or 0)
    rows = db.execute(
        stmt.order_by(
            func.lower(Product.name), Product.sku, PriceEntry.unit_code, PriceEntry.scope,
            PriceEntry.id,
        ).offset(off).limit(limit + 1)
    ).all()  # fmt: skip
    more = len(rows) > limit
    rows = rows[:limit]
    targets = _targets(db, [e for e, _ in rows])
    return {
        "items": [_entry_out(e, prod, targets) for e, prod in rows],
        "next_cursor": str(off + limit) if more else None,
        "total": total,
    }


def _validate_entry(db: Session, p: Principal, body: PriceEntryIn) -> Product:
    prod = db.get(Product, body.product_id)
    if prod is None or prod.company_id != p.company_id:
        raise ApiError(422, "UNKNOWN_PRODUCT", "Product not found")
    if not prod.active:
        raise ApiError(422, "INACTIVE_PRODUCT", f"{prod.sku} is inactive")
    if body.unit_code not in pricing.allowed_units(db, [prod.id])[prod.id]:
        raise ApiError(
            422, "INVALID_UNIT", f"{prod.sku} has no active {body.unit_code} unit profile",
        )  # fmt: skip
    if body.scope == "STANDARD" and (body.customer_id or body.group_id):
        raise ApiError(422, "INVALID_SCOPE", "Standard prices have no customer or group")
    if body.scope == "GROUP":
        if not body.group_id or body.customer_id:
            raise ApiError(422, "INVALID_SCOPE", "Group prices need group_id only")
        g = db.get(CustomerGroup, body.group_id)
        if g is None or g.company_id != p.company_id:
            raise ApiError(422, "INVALID_GROUP", "Customer group not found")
    if body.scope == "CUSTOMER":
        if not body.customer_id or body.group_id:
            raise ApiError(422, "INVALID_SCOPE", "Customer prices need customer_id only")
        c = db.get(Customer, body.customer_id)
        if c is None or c.company_id != p.company_id:
            raise ApiError(422, "INVALID_CUSTOMER", "Customer not found")
        if not c.external_ref:
            raise ApiError(
                422, "CUSTOMER_REF_REQUIRED",
                "Give the customer a reference code first (needed for CSV import/export)",
            )  # fmt: skip
    return prod


@router.put("/price-books/versions/{version_id}/entries", response_model=PriceVersionOut)
def upsert_entry(
    version_id: uuid.UUID,
    body: PriceEntryIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Draft,
) -> JSONResponse:
    """Set one rate in a DRAFT (insert or update by product/unit/scope/target)."""

    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        v = _version(db, p, version_id, lock=True)
        _check(v, body.version)
        _validate_entry(db, p, body)
        rate = Decimal(body.rate).quantize(pricing.Q4)
        existing = db.scalars(
            select(PriceEntry).where(
                PriceEntry.version_id == v.id,
                PriceEntry.product_id == body.product_id,
                PriceEntry.unit_code == body.unit_code,
                PriceEntry.scope == body.scope,
                PriceEntry.customer_id.is_(None)
                if body.customer_id is None
                else PriceEntry.customer_id == body.customer_id,
                PriceEntry.group_id.is_(None)
                if body.group_id is None
                else PriceEntry.group_id == body.group_id,
            )
        ).first()
        old = None
        if existing is not None:
            old = str(existing.rate)
            existing.rate = rate
        else:
            db.add(
                PriceEntry(
                    version_id=v.id,
                    product_id=body.product_id,
                    scope=body.scope,
                    customer_id=body.customer_id,
                    group_id=body.group_id,
                    unit_code=body.unit_code,
                    rate=rate,
                    currency=pricing.CURRENCY,
                )
            )
        _touch(v)
        db.flush()
        detail = {**body.model_dump(mode="json"), "old_rate": old}
        return version_out(db, v, _current_id(db, p.company_id)), str(v.id), detail

    return mutate(
        db, request, p, key, f"price_entry.set:{version_id}", body, 200, "price_entry.set", fn
    )


@router.post("/price-books/versions/{version_id}/entries/remove", response_model=PriceVersionOut)
def remove_entry(
    version_id: uuid.UUID,
    body: PriceEntryRemoveIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Draft,
) -> JSONResponse:
    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        v = _version(db, p, version_id, lock=True)
        _check(v, body.version)
        e = db.get(PriceEntry, body.entry_id)
        if e is None or e.version_id != v.id:
            raise ApiError(404, "NOT_FOUND", "Price entry not found in this version")
        detail = {"entry_id": str(e.id), "product_id": str(e.product_id), "rate": str(e.rate)}
        db.delete(e)
        _touch(v)
        db.flush()
        return version_out(db, v, _current_id(db, p.company_id)), str(v.id), detail

    return mutate(
        db, request, p, key, f"price_entry.remove:{version_id}", body, 200, "price_entry.remove",
        fn,
    )  # fmt: skip


# ---------------------------------------------------------------- CSV download


def _csv_response(rows: list[list[str]], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(pricing.TEMPLATE_COLUMNS)
    for r in rows:
        w.writerow([pricing.csv_safe(c) for c in r])
    return Response(
        "﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/price-books/template.csv", response_class=Response)
def template_csv(request: Request, db: DbDep, p: PrincipalDep) -> Response:
    """Header plus one example row per active product/unit with the current rate (if any)."""
    _read(request, p)
    cur = _current_id(db, p.company_id)
    current = pricing.version_entries(db, cur) if cur else {}
    prods = list(
        db.scalars(
            select(Product)
            .where(Product.company_id == p.company_id, Product.active.is_(True))
            .order_by(func.lower(Product.name), Product.sku)
        )
    )
    units = pricing.allowed_units(db, [x.id for x in prods])
    rows = []
    for prod in prods:
        for u in sorted(units[prod.id]):
            rate = current.get(pricing.EntryKey(prod.id, u, "STANDARD", None))
            if rate is None and u not in ("BOX", "LB"):
                continue  # keep the template short: KG/PIECE only when already priced
            rows.append(
                [
                    prod.sku,
                    prod.name,
                    u,
                    "" if rate is None else str(rate),
                    pricing.CURRENCY,
                    "",
                    "",
                ]
            )
    return _csv_response(rows, "price-template.csv")


@router.get("/price-books/versions/{version_id}/export.csv", response_class=Response)
def export_csv(version_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep) -> Response:
    """Full version as an importable CSV (round-trips to the same content hash)."""
    _read(request, p)
    v = _version(db, p, version_id)
    rows_db = db.execute(
        select(PriceEntry, Product)
        .join(Product, Product.id == PriceEntry.product_id)
        .where(PriceEntry.version_id == v.id)
        .order_by(func.lower(Product.name), Product.sku, PriceEntry.unit_code, PriceEntry.scope)
    ).all()
    cids = {e.customer_id for e, _ in rows_db if e.customer_id}
    gids = {e.group_id for e, _ in rows_db if e.group_id}
    refs = (
        {
            c.id: c.external_ref or ""
            for c in db.scalars(select(Customer).where(Customer.id.in_(cids)))
        }
        if cids
        else {}
    )
    codes = (
        {g.id: g.code for g in db.scalars(select(CustomerGroup).where(CustomerGroup.id.in_(gids)))}
        if gids
        else {}
    )
    rows = [
        [
            prod.sku,
            prod.name,
            e.unit_code,
            str(e.rate),
            e.currency,
            refs.get(e.customer_id, "") if e.customer_id else "",
            codes.get(e.group_id, "") if e.group_id else "",
        ]
        for e, prod in rows_db
    ]
    return _csv_response(rows, f"price-book-{v.version_code}.csv")


# ---------------------------------------------------------------- lookup


@router.get("/prices/resolve", response_model=PriceQuoteOut)
def resolve_price(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    product_id: uuid.UUID,
    unit_code: UnitCode,
    customer_id: uuid.UUID | None = None,
    at: datetime | None = Query(None, description="Defaults to now"),
) -> dict[str, Any]:
    """Price a product for a customer at a moment: customer > group > standard."""
    _read(request, p)
    prod = db.get(Product, product_id)
    if prod is None or prod.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Product not found")
    cust = get_customer(db, p, customer_id) if customer_id else None
    when = at or _now()
    if when.tzinfo is None:
        raise ApiError(422, "VALIDATION", "at must include a timezone")
    try:
        quote = pricing.resolve(db, p.company_id, product_id, unit_code, cust, when)
    except pricing.NoPriceError as e:
        raise ApiError(404, "NO_PRICE", e.reason) from None
    return quote.snapshot()
