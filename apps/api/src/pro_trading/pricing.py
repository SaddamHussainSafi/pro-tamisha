"""Pricing domain (Module 02): CSV parsing/validation, canonical hashing, diffs, resolution.

Rules:
- A price book version is a *complete* set of rates. Resolution uses the ACTIVE version with the
  latest ``effective_at <= at``; drafts are never used for pricing.
- Precedence inside that version: CUSTOMER > GROUP (customer's group) > STANDARD.
- Rates are Decimal (4 dp), currency is the company currency (CAD); no floats anywhere.
- The returned :class:`Quote` is the snapshot a document stores; because published versions are
  immutable, a stored snapshot can always be traced back to the exact entry it came from.
"""

import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import (
    UNIT_CODES,
    Customer,
    CustomerGroup,
    PriceBookVersion,
    PriceEntry,
    Product,
    ProductUnitProfile,
)

CURRENCY = "CAD"
MAX_CSV_BYTES = 2_000_000
MAX_ROWS = 20_000
REQUIRED_COLUMNS = ("sku", "unit", "rate")
OPTIONAL_COLUMNS = ("product_name", "currency", "customer_ref", "group_code")
TEMPLATE_COLUMNS = ("sku", "product_name", "unit", "rate", "currency", "customer_ref", "group_code")
RATE_RE = re.compile(r"^\d{1,10}(\.\d{1,4})?$")
Q4 = Decimal("0.0001")


class NoPriceError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, order=True)
class EntryKey:
    product_id: uuid.UUID
    unit_code: str
    scope: str
    target_id: uuid.UUID | None  # customer_id or group_id


@dataclass(frozen=True)
class ParsedEntry:
    key: EntryKey
    rate: Decimal
    currency: str
    row: int


@dataclass(frozen=True)
class RowError:
    row: int
    field: str | None
    code: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "field": self.field, "code": self.code, "message": self.message}


# --------------------------------------------------------------------------- unit validity


def allowed_units(db: Session, product_ids: list[uuid.UUID]) -> dict[uuid.UUID, set[str]]:
    """LB and KG are always priceable (exact conversion); BOX/PIECE only when the product has
    an active unit profile of that kind (otherwise the unit means nothing for this product)."""
    out: dict[uuid.UUID, set[str]] = {pid: {"LB", "KG"} for pid in product_ids}
    if not product_ids:
        return out
    rows = db.execute(
        select(ProductUnitProfile.product_id, ProductUnitProfile.unit_code).where(
            ProductUnitProfile.product_id.in_(product_ids), ProductUnitProfile.active.is_(True)
        )
    ).all()
    for pid, unit in rows:
        out[pid].add(unit)
    return out


# --------------------------------------------------------------------------- CSV


_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(row: dict[str, str | None], col: str) -> str:
    v = (row.get(col) or "").strip()
    # undo csv_safe() quoting from our own exports so round-trips are lossless
    if v[:1] == "'" and v[1:2] in _FORMULA:
        v = v[1:]
    return v


def parse_csv(
    db: Session, company_id: uuid.UUID, content: str
) -> tuple[list[ParsedEntry], list[RowError]]:
    """Validate a full price book CSV. Returns entries and row-level errors (row = file line)."""
    errors: list[RowError] = []
    if len(content.encode()) > MAX_CSV_BYTES:
        return [], [RowError(0, None, "TOO_LARGE", f"File exceeds {MAX_CSV_BYTES} bytes")]
    text = content.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    reader.fieldnames = header
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    unknown = [c for c in header if c and c not in REQUIRED_COLUMNS + OPTIONAL_COLUMNS]
    if missing or unknown:
        msg = []
        if missing:
            msg.append("missing column(s): " + ", ".join(missing))
        if unknown:
            msg.append("unknown column(s): " + ", ".join(unknown))
        return [], [RowError(1, None, "INVALID_HEADER", "; ".join(msg))]

    raw = list(reader)
    if len(raw) > MAX_ROWS:
        return [], [RowError(0, None, "TOO_MANY_ROWS", f"More than {MAX_ROWS} rows")]

    skus = {_cell(r, "sku") for r in raw}
    products = {
        p.sku: p
        for p in db.scalars(
            select(Product).where(Product.company_id == company_id, Product.sku.in_(skus))
        )
    }
    units = allowed_units(db, [p.id for p in products.values()])
    refs = {_cell(r, "customer_ref") for r in raw} - {""}
    customers = {
        c.external_ref: c
        for c in db.scalars(
            select(Customer).where(
                Customer.company_id == company_id, Customer.external_ref.in_(refs)
            )
        )
    }
    codes = {_cell(r, "group_code").upper() for r in raw} - {""}
    groups = {
        g.code: g
        for g in db.scalars(
            select(CustomerGroup).where(
                CustomerGroup.company_id == company_id, CustomerGroup.code.in_(codes)
            )
        )
    }

    entries: list[ParsedEntry] = []
    seen: dict[EntryKey, int] = {}
    for i, r in enumerate(raw):
        line = i + 2  # header is line 1
        if not any((v or "").strip() for v in r.values() if isinstance(v, str)):
            continue  # blank line
        if None in r:  # extra cells beyond the header
            errors.append(RowError(line, None, "EXTRA_CELLS", "Row has more cells than the header"))
            continue
        row_errors: list[RowError] = []
        sku = _cell(r, "sku")
        prod = products.get(sku)
        if not sku:
            row_errors.append(RowError(line, "sku", "REQUIRED", "SKU is required"))
        elif prod is None:
            row_errors.append(RowError(line, "sku", "UNKNOWN_SKU", f"Unknown SKU {sku!r}"))
        elif not prod.active:
            row_errors.append(RowError(line, "sku", "INACTIVE_PRODUCT", f"{sku} is inactive"))

        unit = _cell(r, "unit").upper()
        if unit not in UNIT_CODES:
            row_errors.append(
                RowError(
                    line, "unit", "INVALID_UNIT", f"Unit must be one of {', '.join(UNIT_CODES)}"
                )
            )
        elif prod is not None and unit not in units.get(prod.id, set()):
            row_errors.append(
                RowError(
                    line,
                    "unit",
                    "INVALID_UNIT",
                    f"{sku} has no active {unit} unit profile; add one in Products first",
                )
            )

        rate_s = _cell(r, "rate")
        rate: Decimal | None = None
        if not RATE_RE.match(rate_s):
            row_errors.append(
                RowError(
                    line, "rate", "INVALID_RATE", "Rate must be a number ≥ 0 with ≤ 4 decimals"
                )
            )
        else:
            rate = Decimal(rate_s).quantize(Q4)

        currency = (_cell(r, "currency") or CURRENCY).upper()
        if currency != CURRENCY:
            row_errors.append(
                RowError(line, "currency", "INVALID_CURRENCY", f"Only {CURRENCY} is supported")
            )

        ref, gcode = _cell(r, "customer_ref"), _cell(r, "group_code").upper()
        scope, target = "STANDARD", None
        if ref and gcode:
            row_errors.append(
                RowError(line, None, "AMBIGUOUS_SCOPE", "Use customer_ref or group_code, not both")
            )
        elif ref:
            cust = customers.get(ref)
            if cust is None:
                row_errors.append(
                    RowError(line, "customer_ref", "UNKNOWN_CUSTOMER", f"Unknown customer {ref!r}")
                )
            else:
                scope, target = "CUSTOMER", cust.id
        elif gcode:
            grp = groups.get(gcode)
            if grp is None:
                row_errors.append(
                    RowError(line, "group_code", "UNKNOWN_GROUP", f"Unknown group {gcode!r}")
                )
            else:
                scope, target = "GROUP", grp.id

        if row_errors or prod is None or rate is None:
            errors.extend(row_errors)
            continue
        key = EntryKey(prod.id, unit, scope, target)
        if key in seen:
            errors.append(
                RowError(
                    line, None, "DUPLICATE_ROW", f"Same SKU/unit/target already on line {seen[key]}"
                )
            )
            continue
        seen[key] = line
        entries.append(ParsedEntry(key, rate, currency, line))
    if not entries and not errors:
        errors.append(RowError(0, None, "EMPTY", "The file has no price rows"))
    return entries, errors


def canonical_hash(entries: dict[EntryKey, Decimal]) -> str:
    """Order- and formatting-independent fingerprint of a price book's content."""
    h = hashlib.sha256()
    for k in sorted(
        entries, key=lambda k: (str(k.product_id), k.unit_code, k.scope, str(k.target_id))
    ):
        h.update(
            f"{k.product_id}|{k.unit_code}|{k.scope}|{k.target_id or ''}|"
            f"{entries[k].quantize(Q4)}|{CURRENCY}\n".encode()
        )
    return h.hexdigest()


def version_entries(db: Session, version_id: uuid.UUID) -> dict[EntryKey, Decimal]:
    out: dict[EntryKey, Decimal] = {}
    for e in db.scalars(select(PriceEntry).where(PriceEntry.version_id == version_id)):
        out[EntryKey(e.product_id, e.unit_code, e.scope, e.customer_id or e.group_id)] = e.rate
    return out


# --------------------------------------------------------------------------- diff


def diff(
    db: Session, old: dict[EntryKey, Decimal], new: dict[EntryKey, Decimal], limit: int = 500
) -> dict[str, Any]:
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [k for k in new if k in old and new[k] != old[k]]
    unchanged = len(new) - len(added) - len(changed)
    keys = added + changed + removed
    pids = {k.product_id for k in keys}
    tids = {k.target_id for k in keys if k.target_id}
    prods = (
        {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(pids)))} if pids else {}
    )
    custs = (
        {c.id: c.name for c in db.scalars(select(Customer).where(Customer.id.in_(tids)))}
        if tids
        else {}
    )
    grps = (
        {g.id: g.code for g in db.scalars(select(CustomerGroup).where(CustomerGroup.id.in_(tids)))}
        if tids
        else {}
    )

    def row(k: EntryKey, change: str) -> dict[str, Any]:
        p = prods[k.product_id]
        target = None
        if k.scope == "CUSTOMER" and k.target_id:
            target = custs.get(k.target_id)
        elif k.scope == "GROUP" and k.target_id:
            target = grps.get(k.target_id)
        o, n = old.get(k), new.get(k)
        return {
            "product_id": str(p.id),
            "sku": p.sku,
            "product_name": p.name,
            "unit_code": k.unit_code,
            "scope": k.scope,
            "target": target,
            "old_rate": None if o is None else str(o.quantize(Q4)),
            "new_rate": None if n is None else str(n.quantize(Q4)),
            "change": change,
        }

    rows = (
        [row(k, "ADDED") for k in added]
        + [row(k, "CHANGED") for k in changed]
        + [row(k, "REMOVED") for k in removed]
    )
    rows.sort(key=lambda r: (r["product_name"].lower(), r["sku"], r["unit_code"], r["scope"]))
    return {
        "added": len(added),
        "changed": len(changed),
        "removed": len(removed),
        "unchanged": unchanged,
        "rows": rows[:limit],
        "truncated": len(rows) > limit,
    }


# --------------------------------------------------------------------------- resolution


def effective_version(db: Session, company_id: uuid.UUID, at: datetime) -> PriceBookVersion | None:
    return db.scalars(
        select(PriceBookVersion)
        .where(
            PriceBookVersion.company_id == company_id,
            PriceBookVersion.status == "ACTIVE",
            PriceBookVersion.effective_at <= at,
        )
        .order_by(PriceBookVersion.effective_at.desc())
        .limit(1)
    ).first()


@dataclass(frozen=True)
class Quote:
    product_id: uuid.UUID
    unit_code: str
    customer_id: uuid.UUID | None
    rate: Decimal
    currency: str
    scope: str
    entry_id: uuid.UUID
    version_id: uuid.UUID
    version_code: str
    effective_at: datetime
    resolved_at: datetime

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe snapshot for invoice/order lines (Decimal as string)."""
        return {
            "product_id": str(self.product_id),
            "unit_code": self.unit_code,
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "rate": str(self.rate),
            "currency": self.currency,
            "scope": self.scope,
            "entry_id": str(self.entry_id),
            "version_id": str(self.version_id),
            "version_code": self.version_code,
            "effective_at": self.effective_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat(),
        }


_PRECEDENCE = {"CUSTOMER": 0, "GROUP": 1, "STANDARD": 2}


def resolve(
    db: Session,
    company_id: uuid.UUID,
    product_id: uuid.UUID,
    unit_code: str,
    customer: Customer | None,
    at: datetime,
) -> Quote:
    ver = effective_version(db, company_id, at)
    if ver is None or ver.effective_at is None:
        raise NoPriceError("No active price book at that time")
    conds = [PriceEntry.scope == "STANDARD"]
    if customer is not None:
        conds.append((PriceEntry.scope == "CUSTOMER") & (PriceEntry.customer_id == customer.id))
        if customer.group_id is not None:
            conds.append((PriceEntry.scope == "GROUP") & (PriceEntry.group_id == customer.group_id))
    cands = db.scalars(
        select(PriceEntry).where(
            PriceEntry.version_id == ver.id,
            PriceEntry.product_id == product_id,
            PriceEntry.unit_code == unit_code,
            or_(*conds),
        )
    ).all()
    if not cands:
        raise NoPriceError(f"No {unit_code} price for this product in {ver.version_code}")
    best = min(cands, key=lambda e: _PRECEDENCE[e.scope])
    return Quote(
        product_id=product_id,
        unit_code=unit_code,
        customer_id=customer.id if customer else None,
        rate=best.rate,
        currency=best.currency,
        scope=best.scope,
        entry_id=best.id,
        version_id=ver.id,
        version_code=ver.version_code,
        effective_at=ver.effective_at,
        resolved_at=at,
    )


def entry_count(db: Session, version_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(PriceEntry).where(PriceEntry.version_id == version_id)
        )
        or 0
    )


def csv_safe(value: str) -> str:
    """Neutralise spreadsheet formula injection in exported cells."""
    return "'" + value if value[:1] in _FORMULA else value
