"""Search box syntax. Plain words search everything; `key:value` narrows it:

    invoice:TT2026-000123   order:SO-20261110-0002   batch:TFS-0001   sku:CFZ
    customer:"island grocers"   store:kingston   supplier:accra   status:issued
    date:2026-11   date:2026-11-01..2026-11-15   date:>2026-11-01
    amount:>1000   amount:500..2000   type:invoices

Values are validated here and later only ever reach SQL as bound parameters. Anything that is
not a known key (a time like 12:30, a URL) stays plain text.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

MAX_QUERY = 200
MAX_VALUE = 64

# key -> document types it narrows to (empty = no type restriction by itself)
IDENT_KEYS: dict[str, tuple[str, ...]] = {
    "invoice": ("invoice",),
    "order": ("sales_order", "pick"),
    "batch": ("lot", "shipment"),
    "lot": ("lot", "shipment"),
    "shipment": ("shipment",),
    "grn": ("shipment",),
    "awb": ("shipment",),
    "scan": ("scan",),
    "payment": ("payment", "supplier_payment"),
}
TYPE_ALIASES: dict[str, str] = {
    "invoice": "invoice", "invoices": "invoice",
    "order": "sales_order", "orders": "sales_order", "sales_order": "sales_order",
    "packing": "pick", "pick": "pick", "picking": "pick", "packing_sheet": "pick",
    "batch": "lot", "batches": "lot", "lot": "lot", "lots": "lot", "inventory": "lot",
    "product": "product", "products": "product",
    "customer": "customer", "customers": "customer", "store": "customer", "stores": "customer",
    "supplier": "vendor", "suppliers": "vendor", "vendor": "vendor", "vendors": "vendor",
    "shipment": "shipment", "shipments": "shipment", "receiving": "shipment", "grn": "shipment",
    "payment": "payment", "payments": "payment",
    "supplier_payment": "supplier_payment", "supplier_payments": "supplier_payment",
    "scan": "scan", "scans": "scan", "ocr": "scan",
    "count": "stock_count", "counts": "stock_count", "stock_count": "stock_count",
    "document": "document", "documents": "document",
    "gmail": "gmail", "email": "gmail", "emails": "gmail",
    "activity": "activity", "audit": "activity",
}  # fmt: skip
OTHER_KEYS = {"sku", "customer", "store", "supplier", "vendor", "status", "date", "amount", "type"}
KEYS = set(IDENT_KEYS) | OTHER_KEYS

_TOKEN = re.compile(r'(\w+):("([^"]*)"|\S+)|"([^"]*)"|(\S+)')
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_STATUS = re.compile(r"^[A-Za-z_ ]{2,32}$")
_AMOUNT = re.compile(r"^(>=|<=|>|<|=)?\s*(\d{1,12}(?:\.\d{1,2})?)$")
_AMOUNT_RANGE = re.compile(r"^(\d{1,12}(?:\.\d{1,2})?)\.\.(\d{1,12}(?:\.\d{1,2})?)$")


class QueryError(ValueError):
    """A filter the user typed is not valid; the message says how to write it."""


@dataclass
class ParsedQuery:
    text: str = ""  # free text (words and quoted phrases), as typed
    idents: list[str] = field(default_factory=list)  # values of invoice:/batch:/... keys
    types: set[str] | None = None  # None = every type the user may see
    sku: str | None = None
    customer: str | None = None
    store: str | None = None
    supplier: str | None = None
    statuses: list[str] = field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None  # inclusive
    amount_min: Decimal | None = None  # inclusive
    amount_max: Decimal | None = None  # inclusive
    filters: list[tuple[str, str]] = field(default_factory=list)  # as typed, for the UI chips

    @property
    def has_filters(self) -> bool:
        return bool(
            self.sku or self.customer or self.store or self.supplier or self.statuses
            or self.date_from or self.date_to or self.amount_min is not None
            or self.amount_max is not None
        )  # fmt: skip

    @property
    def needle(self) -> str:
        """What to look for: the identifier values first, then the free text."""
        return " ".join([*self.idents, self.text]).strip()

    def narrow(self, types: set[str]) -> None:
        self.types = set(types) if self.types is None else self.types & types


def _month_end(y: int, m: int) -> date:
    return (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)


def parse_date(v: str) -> tuple[date | None, date | None]:
    """2026 / 2026-11 / 2026-11-12 / a..b / >x / >=x / <x / <=x."""

    def one(s: str) -> tuple[date, date]:
        try:
            if re.fullmatch(r"\d{4}", s):
                y = int(s)
                return date(y, 1, 1), date(y, 12, 31)
            if re.fullmatch(r"\d{4}-\d{1,2}", s):
                y, m = map(int, s.split("-"))
                return date(y, m, 1), _month_end(y, m)
            if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", s):
                d = date(*map(int, s.split("-")))
                return d, d
        except ValueError:
            pass
        raise QueryError(
            f"date:{v} — write a date like 2026-11, 2026-11-12 or 2026-11-01..2026-11-15"
        )

    if ".." in v:
        a, b = v.split("..", 1)
        return one(a)[0] if a else None, one(b)[1] if b else None
    for op in (">=", "<=", ">", "<"):
        if v.startswith(op):
            lo, hi = one(v[len(op) :])
            return {
                ">=": (lo, None),
                ">": (hi + timedelta(days=1), None),
                "<=": (None, hi),
                "<": (None, lo - timedelta(days=1)),
            }[op]
    return one(v)


def parse_amount(v: str) -> tuple[Decimal | None, Decimal | None]:
    v = v.replace(",", "").replace("$", "").strip()
    m = _AMOUNT_RANGE.fullmatch(v)
    try:
        if m:
            lo, hi = Decimal(m.group(1)), Decimal(m.group(2))
            return (lo, hi) if lo <= hi else (hi, lo)
        m = _AMOUNT.fullmatch(v)
        if m:
            op, n = m.group(1) or "=", Decimal(m.group(2))
            step = Decimal("0.01")
            return {
                "=": (n, n),
                ">=": (n, None),
                ">": (n + step, None),
                "<=": (None, n),
                "<": (None, n - step),
            }[op]
    except InvalidOperation:
        pass
    raise QueryError(f"amount:{v} — write an amount like >1000, <=250.50 or 500..2000")


def parse(q: str) -> ParsedQuery:
    # Control characters (NUL can't even be stored in PostgreSQL text) become spaces.
    q = _CONTROL.sub(" ", q or "").strip()
    if len(q) > MAX_QUERY:
        raise QueryError(f"Search is limited to {MAX_QUERY} characters")
    out = ParsedQuery()
    words: list[str] = []
    for m in _TOKEN.finditer(q):
        key, raw, quoted, phrase, word = m.groups()
        if key is None:
            w = phrase if phrase is not None else word
            k = w[:-1].lower() if w.endswith(":") else ""
            if k in IDENT_KEYS:  # "invoice:" while still typing: just narrow to invoices
                out.narrow(set(IDENT_KEYS[k]))
            elif k not in KEYS:
                words.append(w)
            continue
        k = key.lower()
        val = (quoted if quoted is not None else raw).strip()
        if k not in KEYS:
            words.append(m.group(0))
            continue
        if not val:
            raise QueryError(f"{k}: needs a value, e.g. {k}:{'2026-11' if k == 'date' else '…'}")
        if len(val) > MAX_VALUE:
            raise QueryError(f"{k}: value is too long (at most {MAX_VALUE} characters)")
        if len(out.filters) >= 10:
            raise QueryError("At most 10 filters in one search")
        out.filters.append((k, val))
        if k in IDENT_KEYS:
            out.idents.append(val)
            out.narrow(set(IDENT_KEYS[k]))
        elif k == "sku":
            if not re.fullmatch(r"[A-Za-z]{1,3}[Pp]?", val):
                raise QueryError(f"sku:{val} — a SKU is 3 letters (4 with P for packaged)")
            out.sku = val.upper()
        elif k == "customer":
            out.customer = val
        elif k == "store":
            out.store = val
        elif k in ("supplier", "vendor"):
            out.supplier = val
        elif k == "status":
            for s in val.split(","):
                if not _STATUS.fullmatch(s.strip()):
                    raise QueryError(f"status:{val} — use a status name such as issued, paid, open")
                out.statuses.append(s.strip().upper().replace(" ", "_"))
        elif k == "date":
            out.date_from, out.date_to = parse_date(val)
        elif k == "amount":
            out.amount_min, out.amount_max = parse_amount(val)
        elif k == "type":
            ts = set()
            for t in val.lower().split(","):
                if t.strip() not in TYPE_ALIASES:
                    raise QueryError(
                        f"type:{t} — try invoices, orders, batches, products, customers"
                    )
                ts.add(TYPE_ALIASES[t.strip()])
            out.narrow(ts)
    out.text = " ".join(w for w in words if w).strip()
    return out


_WORD = re.compile(r"[0-9a-z]+")


def words(folded: str) -> list[str]:
    """Words safe to put in a tsquery (letters/digits only), at most 8."""
    return _WORD.findall(folded)[:8]


def tsquery(folded: str, weights: str = "") -> str | None:
    """Prefix query: every word must match the start of a word ("cass gre" -> cassava green)."""
    ws = words(folded)
    if not ws:
        return None
    return " & ".join(f"{w}:*{weights}" for w in ws)
