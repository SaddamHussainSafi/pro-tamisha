"""Catalog (Module 01): categories, products, aliases, unit profiles, weight calculation.

Products are company-level master data: reads accept any-scope `catalog.read`; mutations need a
company-wide `catalog.manage` grant. Every mutation is idempotent, audited and (for products)
optimistically versioned. Product id/sku/stock_unit and profile conversion values are immutable
(DB trigger in migration 0002). No external IO happens here, so there is no outbox event.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import idempotency, packaged, units
from ..deps import DbDep, Principal, PrincipalDep, authorize, require_permission
from ..errors import ApiError
from ..models import Product, ProductAlias, ProductCategory, ProductUnitProfile
from ..schemas import (
    AliasIn,
    AliasMatch,
    AliasOut,
    CaseSizeOut,
    CategoryIn,
    CategoryOut,
    ProductIn,
    ProductOut,
    ProductPage,
    ProductPatch,
    ProductSummary,
    UnitCode,
    UnitProfileIn,
    UnitProfileOut,
    VersionIn,
    WeightCalcIn,
    WeightCalcOut,
    WeightGroupOut,
)
from ._common import esc as _esc
from ._common import mutate as _mutate

router = APIRouter(tags=["catalog"])


class _NoBody(BaseModel):
    pass


Manage = Depends(require_permission("catalog.manage"))


def norm(alias: str) -> str:
    return " ".join(alias.casefold().split())


def _read(request: Request, p: Principal) -> None:
    authorize(request, p, "catalog.read", any_scope=True)


def _product(db: Session, p: Principal, product_id: uuid.UUID, lock: bool = False) -> Product:
    prod = db.get(Product, product_id, with_for_update=lock, populate_existing=lock)
    if prod is None or prod.company_id != p.company_id:
        raise ApiError(404, "NOT_FOUND", "Product not found")
    return prod


def _check_version(prod: Product, version: int) -> None:
    if prod.version != version:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            "Product changed; reload and retry",
            current_version=prod.version,
        )


def _bump(prod: Product) -> None:
    prod.version += 1
    prod.updated_at = datetime.now(UTC)


def _check_category(db: Session, p: Principal, category_id: uuid.UUID | None) -> None:
    if category_id is None:
        return
    cat = db.get(ProductCategory, category_id)
    if cat is None or cat.company_id != p.company_id:
        raise ApiError(422, "INVALID_CATEGORY", "Category not found")


def _validate_profile(pr: UnitProfileIn) -> None:
    if pr.unit_code in ("LB", "KG") and (pr.pieces_per_unit or pr.weight_lb_per_unit):
        raise ApiError(
            422, "INVALID_PROFILE", f"{pr.unit_code} is a base unit and takes no conversion factors"
        )


def _new_profile(prod: Product, pr: UnitProfileIn) -> ProductUnitProfile:
    _validate_profile(pr)
    return ProductUnitProfile(
        product_id=prod.id,
        code=pr.code,
        unit_code=pr.unit_code,
        pieces_per_unit=pr.pieces_per_unit,
        weight_lb_per_unit=pr.weight_lb_per_unit,
        is_default=pr.is_default,
    )


def product_out(db: Session, prod: Product) -> dict[str, Any]:
    profiles = db.scalars(
        select(ProductUnitProfile)
        .where(ProductUnitProfile.product_id == prod.id)
        .order_by(ProductUnitProfile.active.desc(), ProductUnitProfile.code)
    ).all()
    aliases = db.scalars(
        select(ProductAlias).where(ProductAlias.product_id == prod.id).order_by(ProductAlias.alias)
    ).all()
    return ProductOut(
        **ProductSummary.model_validate(prod).model_dump(),
        created_at=prod.created_at,
        updated_at=prod.updated_at,
        unit_profiles=[UnitProfileOut.model_validate(x) for x in profiles],
        aliases=[AliasOut.model_validate(x) for x in aliases],
    ).model_dump(mode="json")


# ---------------------------------------------------------------- categories


@router.get("/product-categories", response_model=list[CategoryOut])
def list_categories(request: Request, db: DbDep, p: PrincipalDep) -> list[ProductCategory]:
    _read(request, p)
    return list(
        db.scalars(
            select(ProductCategory)
            .where(ProductCategory.company_id == p.company_id)
            # Packaged is listed last (owner, 2026-11-11)
            .order_by(ProductCategory.code == packaged.CATEGORY_CODE, ProductCategory.name)
        ).all()
    )


@router.post("/product-categories", status_code=201, response_model=CategoryOut)
def create_category(
    body: CategoryIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        cat = ProductCategory(company_id=p.company_id, code=body.code, name=body.name)
        db.add(cat)
        db.flush()
        return (
            CategoryOut.model_validate(cat).model_dump(mode="json"),
            str(cat.id),
            body.model_dump(),
        )

    return _mutate(
        db, request, p, key, "category.create", body, 201, "category.create", fn,
        "Category code or name already exists",
    )  # fmt: skip


# ---------------------------------------------------------------- products


@router.get("/products", response_model=ProductPage)
def list_products(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    search: str | None = Query(None, max_length=200),
    category_id: uuid.UUID | None = None,
    active: bool | None = None,
    stock_unit: UnitCode | None = None,
    cursor: str | None = Query(None, max_length=64, description="Last SKU of previous page"),
    limit: int = Query(50, ge=1, le=1000),
) -> ProductPage:
    _read(request, p)
    q = select(Product).where(Product.company_id == p.company_id)
    if search:
        like = f"%{_esc(search.strip())}%"
        alias_hit = select(ProductAlias.product_id).where(
            ProductAlias.company_id == p.company_id,
            ProductAlias.alias_norm.ilike(f"%{_esc(norm(search))}%", escape="\\"),
        )
        q = q.where(
            or_(
                Product.sku.ilike(like, escape="\\"),
                Product.name.ilike(like, escape="\\"),
                Product.id.in_(alias_hit),
            )
        )
    if category_id is not None:
        q = q.where(Product.category_id == category_id)
    if active is not None:
        q = q.where(Product.active == active)
    if stock_unit is not None:
        q = q.where(Product.stock_unit == stock_unit)
    if cursor:  # loose items first, then the packaged ones (longer SKUs), each by SKU
        n = func.char_length(Product.sku)
        q = q.where(or_(n > len(cursor), (n == len(cursor)) & (Product.sku > cursor)))
    rows = db.scalars(q.order_by(*packaged.sku_order()).limit(limit + 1)).all()
    more = len(rows) > limit
    items = [ProductSummary.model_validate(r) for r in rows[:limit]]
    return ProductPage(items=items, next_cursor=items[-1].sku if more else None)


@router.get("/product-lookup", response_model=list[ProductOut])
def product_lookup(request: Request, db: DbDep, p: PrincipalDep) -> list[dict[str, Any]]:
    """Every active product with its case sizes and aliases, in one call, so the entry grid
    can autocomplete by name, SKU or alias and fill units/case weights without a round trip."""
    _read(request, p)
    prods = db.scalars(
        select(Product)
        .where(Product.company_id == p.company_id, Product.active.is_(True))
        .order_by(*packaged.sku_order())
        .limit(2000)
    ).all()
    ids = [x.id for x in prods]
    profiles: dict[uuid.UUID, list[ProductUnitProfile]] = {i: [] for i in ids}
    aliases: dict[uuid.UUID, list[ProductAlias]] = {i: [] for i in ids}
    if ids:
        for pr in db.scalars(
            select(ProductUnitProfile)
            .where(ProductUnitProfile.product_id.in_(ids))
            .order_by(ProductUnitProfile.active.desc(), ProductUnitProfile.code)
        ):
            profiles[pr.product_id].append(pr)
        for a in db.scalars(
            select(ProductAlias)
            .where(ProductAlias.product_id.in_(ids))
            .order_by(ProductAlias.alias)
        ):
            aliases[a.product_id].append(a)
    return [
        ProductOut(
            **ProductSummary.model_validate(x).model_dump(),
            created_at=x.created_at,
            updated_at=x.updated_at,
            unit_profiles=[UnitProfileOut.model_validate(u) for u in profiles[x.id]],
            aliases=[AliasOut.model_validate(a) for a in aliases[x.id]],
        ).model_dump(mode="json")
        for x in prods
    ]


@router.get("/products/{product_id}", response_model=ProductOut)
def get_product(
    product_id: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> dict[str, Any]:
    _read(request, p)
    return product_out(db, _product(db, p, product_id))


@router.post("/products", status_code=201, response_model=ProductOut)
def create_product(
    body: ProductIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        if sum(pr.is_default for pr in body.unit_profiles) > 1:
            raise ApiError(422, "INVALID_PROFILE", "Only one default unit profile is allowed")
        _check_category(db, p, body.category_id)
        prod = Product(
            company_id=p.company_id,
            sku=body.sku,
            name=body.name,
            category_id=body.category_id,
            stock_unit=body.stock_unit,
            tracks_expiry=body.tracks_expiry,
        )
        db.add(prod)
        db.flush()
        db.add_all([_new_profile(prod, pr) for pr in body.unit_profiles])
        db.add_all(
            ProductAlias(
                company_id=p.company_id,
                product_id=prod.id,
                alias=a.alias.strip(),
                alias_norm=norm(a.alias),
                source_label=a.source_label.strip(),
            )
            for a in body.aliases
        )
        db.flush()
        # Every product has a packaged twin (owner, 2026-11-11), created with it.
        twins = packaged.ensure_twins(db, p.company_id, [prod])
        audit_data = body.model_dump(mode="json") | {"packaged_twin": [t.sku for t in twins]}
        return product_out(db, prod), str(prod.id), audit_data

    return _mutate(
        db, request, p, key, "product.create", body, 201, "product.create", fn,
        "SKU, profile code or alias already exists",
    )  # fmt: skip


@router.patch("/products/{product_id}", response_model=ProductOut)
def update_product(
    product_id: uuid.UUID,
    body: ProductPatch,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        prod = _product(db, p, product_id, lock=True)
        _check_version(prod, body.version)
        _check_category(db, p, body.category_id)
        fields = ("name", "category_id", "tracks_expiry", "active")
        before = {f: getattr(prod, f) for f in fields}
        for f in fields:
            setattr(prod, f, getattr(body, f))
        _bump(prod)
        db.flush()
        after = {f: getattr(prod, f) for f in fields}
        detail = {"before": before, "after": after}
        return product_out(db, prod), str(prod.id), {k: _js(v) for k, v in detail.items()}

    return _mutate(
        db, request, p, key, f"product.update:{product_id}", body, 200, "product.update", fn
    )


def _js(d: dict[str, Any]) -> dict[str, Any]:
    return {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in d.items()}


# ---------------------------------------------------------------- aliases


@router.post("/products/{product_id}/aliases", status_code=201, response_model=AliasOut)
def add_alias(
    product_id: uuid.UUID,
    body: AliasIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        prod = _product(db, p, product_id)
        a = ProductAlias(
            company_id=p.company_id,
            product_id=prod.id,
            alias=body.alias.strip(),
            alias_norm=norm(body.alias),
            source_label=body.source_label.strip(),
        )
        db.add(a)
        db.flush()
        detail = {"product_id": str(prod.id), **body.model_dump()}
        return AliasOut.model_validate(a).model_dump(mode="json"), str(a.id), detail

    return _mutate(
        db, request, p, key, f"alias.create:{product_id}", body, 201, "alias.create", fn,
        "This alias is already mapped for that source",
    )  # fmt: skip


@router.delete("/products/{product_id}/aliases/{alias_id}", response_model=AliasOut)
def remove_alias(
    product_id: uuid.UUID,
    alias_id: uuid.UUID,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        _product(db, p, product_id)
        a = db.get(ProductAlias, alias_id)
        if a is None or a.product_id != product_id:
            raise ApiError(404, "NOT_FOUND", "Alias not found")
        out = AliasOut.model_validate(a).model_dump(mode="json")
        db.delete(a)
        return out, str(alias_id), {"product_id": str(product_id), **out}

    return _mutate(
        db, request, p, key, f"alias.delete:{alias_id}", _NoBody(), 200,
        "alias.delete", fn,
    )  # fmt: skip


@router.get("/product-aliases/resolve", response_model=AliasMatch)
def resolve_alias(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    name: str = Query(min_length=1, max_length=200),
    source: str = Query("", max_length=100),
) -> AliasMatch:
    """Exact normalized match; a source-specific alias wins over a generic one."""
    _read(request, p)
    labels = {source.strip(), ""}
    rows = db.execute(
        select(ProductAlias, Product)
        .join(Product, Product.id == ProductAlias.product_id)
        .where(
            ProductAlias.company_id == p.company_id,
            ProductAlias.alias_norm == norm(name),
            ProductAlias.source_label.in_(labels),
        )
        .order_by(func.char_length(ProductAlias.source_label).desc())
    ).first()
    if rows is None:
        raise ApiError(404, "NOT_FOUND", "No product mapped to this name")
    a, prod = rows
    return AliasMatch(product=ProductSummary.model_validate(prod), alias=AliasOut.model_validate(a))


# ---------------------------------------------------------------- unit profiles


@router.get("/unit-profiles", response_model=list[CaseSizeOut])
def list_case_sizes(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    q: str | None = Query(None, max_length=100, description="SKU or product name contains"),
    include_inactive: bool = False,
    limit: int = Query(500, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Every case size (unit profile) across products, by product name then default first."""
    _read(request, p)
    stmt = (
        select(ProductUnitProfile, Product)
        .join(Product, Product.id == ProductUnitProfile.product_id)
        .where(Product.company_id == p.company_id)
    )
    if q:
        like = f"%{_esc(q.strip().lower())}%"
        stmt = stmt.where(
            or_(
                func.lower(Product.name).like(like, escape="\\"),
                func.lower(Product.sku).like(like, escape="\\"),
            )
        )
    if not include_inactive:
        stmt = stmt.where(ProductUnitProfile.active.is_(True))
    rows = db.execute(
        stmt.order_by(
            func.lower(Product.name),
            ProductUnitProfile.is_default.desc(),
            ProductUnitProfile.code,
        ).limit(limit)
    ).all()
    return [
        {
            **UnitProfileOut.model_validate(prof).model_dump(),
            "product_id": prod.id,
            "sku": prod.sku,
            "product_name": prod.name,
            "product_active": prod.active,
        }
        for prof, prod in rows
    ]


@router.post("/products/{product_id}/unit-profiles", status_code=201, response_model=ProductOut)
def add_unit_profile(
    product_id: uuid.UUID,
    body: UnitProfileIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        prod = _product(db, p, product_id, lock=True)
        if body.is_default:
            _clear_default(db, prod.id)
        db.add(_new_profile(prod, body))
        _bump(prod)
        db.flush()
        # Every product has a packaged twin (owner, 2026-11-11), created with it.
        twins = packaged.ensure_twins(db, p.company_id, [prod])
        audit_data = body.model_dump(mode="json") | {"packaged_twin": [t.sku for t in twins]}
        return product_out(db, prod), str(prod.id), audit_data

    return _mutate(
        db, request, p, key, f"profile.create:{product_id}", body, 201, "product.profile_add",
        fn, "Profile code already exists for this product",
    )  # fmt: skip


def _clear_default(db: Session, product_id: uuid.UUID) -> None:
    for x in db.scalars(
        select(ProductUnitProfile).where(
            ProductUnitProfile.product_id == product_id, ProductUnitProfile.is_default
        )
    ):
        x.is_default = False
    db.flush()


def _profile_command(
    product_id: uuid.UUID,
    profile_id: uuid.UUID,
    body: VersionIn,
    request: Request,
    db: Session,
    key: str,
    p: Principal,
    action: str,
    apply: Callable[[ProductUnitProfile], None],
) -> JSONResponse:
    def fn() -> tuple[dict[str, Any], str, dict[str, Any]]:
        prod = _product(db, p, product_id, lock=True)
        _check_version(prod, body.version)
        prof = db.get(ProductUnitProfile, profile_id)
        if prof is None or prof.product_id != prod.id:
            raise ApiError(404, "NOT_FOUND", "Unit profile not found")
        apply(prof)
        _bump(prod)
        db.flush()
        return product_out(db, prod), str(prod.id), {"profile_id": str(prof.id), "code": prof.code}

    return _mutate(db, request, p, key, f"{action}:{profile_id}", body, 200, action, fn)


@router.post(
    "/products/{product_id}/unit-profiles/{profile_id}/deactivate", response_model=ProductOut
)
def deactivate_unit_profile(
    product_id: uuid.UUID,
    profile_id: uuid.UUID,
    body: VersionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def apply(prof: ProductUnitProfile) -> None:
        prof.is_default = False
        prof.active = False
        prof.version += 1

    return _profile_command(
        product_id, profile_id, body, request, db, key, p, "product.profile_deactivate", apply
    )


@router.post(
    "/products/{product_id}/unit-profiles/{profile_id}/make-default", response_model=ProductOut
)
def make_default_unit_profile(
    product_id: uuid.UUID,
    profile_id: uuid.UUID,
    body: VersionIn,
    request: Request,
    db: DbDep,
    key: idempotency.IdemKey,
    p: Principal = Manage,
) -> JSONResponse:
    def apply(prof: ProductUnitProfile) -> None:
        if not prof.active:
            raise ApiError(422, "INVALID_PROFILE", "Inactive profiles cannot be the default")
        _clear_default(db, prof.product_id)
        prof.is_default = True
        prof.version += 1

    return _profile_command(
        product_id, profile_id, body, request, db, key, p, "product.profile_default", apply
    )


# ---------------------------------------------------------------- weight calculation


@router.post("/products/{product_id}/weight-calc", response_model=WeightCalcOut)
def weight_calc(
    product_id: uuid.UUID, body: WeightCalcIn, request: Request, db: DbDep, p: PrincipalDep
) -> WeightCalcOut:
    """Read-only computation (no idempotency needed). Conversions come only from this
    product's active profiles; anything else is UNKNOWN_CONVERSION, never guessed."""
    _read(request, p)
    prod = _product(db, p, product_id)
    profiles = {
        x.id: x
        for x in db.scalars(
            select(ProductUnitProfile).where(
                ProductUnitProfile.product_id == prod.id, ProductUnitProfile.active
            )
        )
    }
    results: list[units.GroupResult] = []
    for i, g in enumerate(body.groups):
        prof = profiles.get(g.profile_id)
        try:
            if prof is None:
                raise units.UnknownConversionError(
                    "Unit profile is not an active profile of this product"
                )
            results.append(units.group_weight(prof, g.count, g.case_weight_lb))
        except units.UnknownConversionError as e:
            raise ApiError(422, "UNKNOWN_CONVERSION", str(e), group=i) from None
        except units.InvalidQuantityError as e:
            raise ApiError(422, "INVALID_QUANTITY", str(e), group=i) from None
    t = units.total_weight(results)
    return WeightCalcOut(
        groups=[
            WeightGroupOut(profile_id=g.profile_id, **vars(r))
            for g, r in zip(body.groups, t.groups, strict=True)
        ],
        units=t.units,
        total_lb=t.total_lb,
        total_kg=t.total_kg,
        estimated=t.estimated,
    )
