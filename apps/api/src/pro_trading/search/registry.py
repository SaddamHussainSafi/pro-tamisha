"""What can be searched, who may see it, and the text helpers shared by indexer and engine.

Visibility mirrors each module's own read endpoints (routes/*.py):
- "warehouse": the permission must cover the record's warehouse (company-wide or that one).
- "any": the permission held in any scope (the module's list endpoints use any_scope=True).
- "company": only a company-wide grant counts (require_permission without a warehouse).
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Literal

from ..deps import Principal

Scope = Literal["warehouse", "any", "company"]


@dataclass(frozen=True)
class DocType:
    key: str
    label: str  # group heading in the palette
    perms: tuple[str, ...]  # any one of these lets you see it
    scope: Scope
    rank: int  # tie-break inside one score tier (lower first)
    money: tuple[str, ...] = ()  # amount shown only with one of these


DOC_TYPES: dict[str, DocType] = {
    d.key: d
    for d in (
        DocType("invoice", "Invoices", ("invoice.read",), "warehouse", 1, ("invoice.read",)),
        DocType("lot", "Batches", ("inventory.read",), "warehouse", 2),
        DocType("sales_order", "Orders", ("sales_order.read",), "warehouse", 3),
        DocType("shipment", "Receiving", ("grn.record", "grn.approve"), "warehouse", 4),
        DocType("customer", "Customers", ("catalog.read", "price.read"), "any", 5),
        DocType("product", "Products", ("catalog.read",), "any", 6),
        DocType("vendor", "Suppliers", ("catalog.read", "grn.record", "grn.approve"), "any", 7),
        DocType(
            "payment",
            "Payments",
            ("payment.record", "payment.verify"),
            "any",
            8,
            ("payment.record", "payment.verify"),
        ),
        DocType(
            "supplier_payment",
            "Supplier payments",
            ("supplier.ledger.read",),
            "company",
            9,
            ("supplier.ledger.read",),
        ),
        DocType("pick", "Packing sheets", ("sales_order.read",), "warehouse", 10),
        DocType("scan", "Scans", ("scan.create", "scan.review"), "warehouse", 11),
        DocType("stock_count", "Stock counts", ("inventory.read",), "warehouse", 12),
        DocType("document", "Documents", ("document.read",), "warehouse", 13),
        DocType("gmail", "Gmail", ("gmail.read",), "company", 14),
        # Not stored in search_documents: looked up live in audit_events (admins only).
        DocType("activity", "Activity", ("audit.read",), "company", 15),
        # Not stored in search_documents either: looked up live in the Email module's own
        # index, and only in the mailboxes this person was given (mail_access).
        DocType("email", "Email", ("mail.use",), "company", 16),
    )
}
LIVE_ONLY = frozenset({"activity", "email"})
INDEXED_TYPES = tuple(k for k in DOC_TYPES if k not in LIVE_ONLY)

# Modules the owner listed that have no table yet. A future table only needs a builder in
# indexer.BUILDERS, a DocType here and a trigger in a migration.
MISSING_MODULES = {
    "returns": "No returns document table; returns exist only as RETURN stock movements.",
    "adjustments": "No adjustment document table; adjustments are ADJUSTMENT stock movements.",
    "stores": "No separate store table; a customer's ship-to name is searched as its store.",
}


@dataclass(frozen=True)
class Visibility:
    full: tuple[str, ...]  # types visible in every warehouse
    pairs: tuple[str, ...]  # "type:warehouse_id" for warehouse-limited types
    money: frozenset[str]  # types whose amounts may be shown / filtered

    def can_see(self, doc_type: str, warehouse_id: uuid.UUID | None) -> bool:
        if doc_type in self.full:
            return True
        return warehouse_id is not None and f"{doc_type}:{warehouse_id}" in self.pairs

    @property
    def types(self) -> tuple[str, ...]:
        seen = list(self.full)
        for p in self.pairs:
            t = p.split(":", 1)[0]
            if t not in seen:
                seen.append(t)
        return tuple(t for t in DOC_TYPES if t in seen)


def visibility(p: Principal) -> Visibility:
    full: list[str] = []
    pairs: list[str] = []
    grants = p.effective()
    for d in DOC_TYPES.values():
        mine = [g for g in grants if g.permission in d.perms]
        if not mine:
            continue
        if d.scope == "any" or any(g.warehouse_id is None for g in mine):
            full.append(d.key)
        elif d.scope == "warehouse":
            pairs.extend(f"{d.key}:{g.warehouse_id}" for g in mine)
        # scope "company" with only warehouse grants: not visible
    money = frozenset(
        d.key
        for d in DOC_TYPES.values()
        if d.money and any(p.can(m, any_scope=True) for m in d.money)
    )
    return Visibility(tuple(full), tuple(sorted(set(pairs))), money)


# ------------------------------------------------------------------ text normalization

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^0-9A-Za-z]")


def fold(s: str | None) -> str:
    """Lower case, accents removed, single spaces: "Café  Ñame" -> "cafe name"."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return _WS.sub(" ", s).strip().lower()


def ident_forms(*values: str | None) -> list[str]:
    """Upper-cased identifiers as written plus a compact form without spaces / dashes,
    so "176 55512345", "176-55512345" and "17655512345" all match the same airway bill."""
    out: list[str] = []
    for v in values:
        if not v:
            continue
        u = _WS.sub(" ", v).strip().upper()
        if not u:
            continue
        for f in (u, _NON_ALNUM.sub("", u)):
            if f and f not in out:
                out.append(f)
    return out


def compact(s: str) -> str:
    return _NON_ALNUM.sub("", s).upper()
