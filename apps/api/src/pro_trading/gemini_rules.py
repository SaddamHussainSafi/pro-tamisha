"""What the Gemini reader may answer, and the rules its answer is held to.

Pure code: no database, no network. The model only reports what is written, as text; everything
that decides what a line means is here, so each rule has a test:

- a quantity is parsed from the literal text ("1", "1/2", "1.5", "5", "15" stay five different
  things); fractions and decimals always go to review;
- a row nobody wrote on is not an order; a row named TOTAL is a total, never a line;
- NO, a checkmark, a crossed-out number, supplied / unavailable are facts about their own row,
  kept apart from the quantity and never turned into a number (unknown stays None, not 0);
- the model's own "unsure" can only lower a line; a line is trusted only when a second,
  independent look (ink found in that row's box, the local digit read) agrees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

DOC_TYPES = (
    "CUSTOMER_ORDER",
    "VENDOR_RECEIVING",
    "AIRWAY_BILL",
    "SALES_INVOICE",
    "UNKNOWN_DOCUMENT",
)
DocType = Literal[
    "CUSTOMER_ORDER", "VENDOR_RECEIVING", "AIRWAY_BILL", "SALES_INVOICE", "UNKNOWN_DOCUMENT"
]
MAX_ROWS = 400
MAX_TEXT = 300
OWN_NAME = "TAMISHA"  # the company itself: supplier on its own sheets, never the customer

C_OK = Decimal("0.950")  # written, read plainly, and the local check agrees
C_UNCONFIRMED = Decimal("0.850")  # read plainly, but nothing here could confirm it
C_CHECK = Decimal("0.700")
C_DOUBT = Decimal("0.500")
C_CONFLICT = Decimal("0.400")


# ------------------------------------------------------------------ answer schema

_NS = {"type": ["string", "null"]}
_ROW_PROPS: dict[str, Any] = {
    "t": {"type": "integer", "description": "table number, left to right from 1; 0 if no tables"},
    "item": {"type": "string", "description": "item name exactly as printed or written"},
    "w": {"type": "boolean", "description": "true if anything is handwritten on this row"},
    "hand": {"type": "boolean", "description": "true if the item name itself is handwritten"},
    "qty": {
        **_NS,
        "description": "quantity exactly as it appears, e.g. 1, 1/2, 1.5, 15; null if none",
    },
    "old": {
        **_NS,
        "description": "a crossed-out quantity on this row, exactly as written; else null",
    },
    "pkg": {**_NS, "description": "package / unit column text"},
    "wt": {**_NS, "description": "weight column text"},
    "size": {**_NS, "description": "case size or count per case, if shown"},
    "notes": {**_NS, "description": "notes or remarks written on this row"},
    "no": {
        "type": "boolean",
        "description": "true only if the word NO is written on this very row",
    },
    "chk": {"type": "boolean", "description": "true if a checkmark or tick is on this row"},
    "sup": {**_NS, "description": "supplied / packed / received quantity if written separately"},
    "unav": {**_NS, "description": "quantity marked short or unavailable, if a number is written"},
    "price": {**_NS, "description": "unit price as printed"},
    "amt": {**_NS, "description": "line amount as printed"},
    "unsure": {"type": "boolean", "description": "true if any part of this row is hard to read"},
    "img": {"type": "integer", "description": "number of the IMAGE where you read this row best"},
    "box": {
        "type": "array",
        "items": {"type": "integer"},
        "description": "[y0, x0, y1, x1] of the row in that image, each 0-1000",
    },
}
PAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": list(DOC_TYPES)},
        "issuer": {**_NS, "description": "company whose letterhead or form this is"},
        "customer": {**_NS, "description": "customer or store name as written, verbatim"},
        "cust_unsure": {"type": "boolean"},
        "order_ref": {
            **_NS,
            "description": "order / sales order reference as written, e.g. SO#080778",
        },
        "doc_no": {**_NS, "description": "invoice or document number (supplier documents)"},
        "doc_date": _NS,
        "awb": {**_NS, "description": "air waybill number"},
        "currency": _NS,
        "total": {**_NS, "description": "printed document total (supplier documents)"},
        "layout": {"type": "string", "description": "one line: tables and their columns"},
        "totals": {
            "type": "array",
            "items": {"type": "string"},
            "description": "handwritten totals, counts or circled numbers not on an item row",
        },
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _ROW_PROPS,
                "required": list(_ROW_PROPS),
                "additionalProperties": False,
            },
        },  # fmt: skip
        "problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "doc_type",
        "issuer",
        "customer",
        "cust_unsure",
        "order_ref",
        "doc_no",
        "doc_date",
        "awb",
        "currency",
        "total",
        "layout",
        "totals",
        "rows",
        "problems",
    ],  # fmt: skip
    "additionalProperties": False,
}

DOC_SCHEMA: dict[str, Any] = {  # stage A when the allowance lets it be a separate request
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "doc_type": {"type": "string", "enum": list(DOC_TYPES)},
                    "customer": _NS,
                    "order_ref": _NS,
                    "layout": {"type": "string"},
                },
                "required": ["page", "doc_type", "customer", "order_ref", "layout"],
                "additionalProperties": False,
            },
        }
    },  # fmt: skip
    "required": ["pages"],
    "additionalProperties": False,
}

CHECK_SCHEMA: dict[str, Any] = {  # stage C: a second look at single rows, cut out and enlarged
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer", "description": "the row number given with the image"},
                    "item": {"type": "string"},
                    "qty": _NS,
                    "old": _NS,
                    "no": {"type": "boolean"},
                    "unsure": {"type": "boolean"},
                },
                "required": ["i", "item", "qty", "old", "no", "unsure"],
                "additionalProperties": False,
            },
        }
    },  # fmt: skip
    "required": ["checks"],
    "additionalProperties": False,
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _clip(v: str | None) -> str | None:
    if v is None:
        return None
    v = " ".join(v.split())
    return v[:MAX_TEXT] or None  # over-long text is cut, not a reason to lose the page


class RowOut(_Strict):
    t: int
    item: str
    w: bool
    hand: bool
    qty: str | None
    old: str | None
    pkg: str | None
    wt: str | None
    size: str | None
    notes: str | None
    no: bool
    chk: bool
    sup: str | None
    unav: str | None
    price: str | None
    amt: str | None
    unsure: bool
    img: int
    box: list[int]

    _c = field_validator(
        "item", "qty", "old", "pkg", "wt", "size", "notes", "sup", "unav", "price", "amt"
    )(_clip)

    @field_validator("box")
    @classmethod
    def _box(cls, v: list[int]) -> list[int]:
        # the box only says where to highlight: one that makes no sense is dropped, not fatal
        return v if len(v) == 4 and all(0 <= n <= 1000 for n in v) else []


class PageOut(_Strict):
    doc_type: DocType
    issuer: str | None
    customer: str | None
    cust_unsure: bool
    order_ref: str | None
    doc_no: str | None
    doc_date: str | None
    awb: str | None
    currency: str | None
    total: str | None
    layout: str
    totals: list[str]
    rows: list[RowOut]
    problems: list[str]

    _c = field_validator(
        "issuer", "customer", "order_ref", "doc_no", "doc_date", "awb", "currency", "total"
    )(_clip)

    @field_validator("rows")
    @classmethod
    def _rows(cls, v: list[RowOut]) -> list[RowOut]:
        if len(v) > MAX_ROWS:
            raise ValueError("too many rows")
        return v


class DocPage(_Strict):
    page: int
    doc_type: DocType
    customer: str | None
    order_ref: str | None
    layout: str


class DocOut(_Strict):
    pages: list[DocPage]


class CheckRow(_Strict):
    i: int
    item: str
    qty: str | None
    old: str | None
    no: bool
    unsure: bool


class CheckOut(_Strict):
    checks: list[CheckRow]


def validate_page(obj: dict[str, Any]) -> PageOut:
    return PageOut.model_validate(obj)


def validate_doc(obj: dict[str, Any]) -> DocOut:
    return DocOut.model_validate(obj)


def validate_check(obj: dict[str, Any]) -> CheckOut:
    return CheckOut.model_validate(obj)


# ------------------------------------------------------------------ quantities

_FRACTIONS = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3"}
_INT = re.compile(r"^\d{1,5}$")
_DEC = re.compile(r"^\d{1,5}\.\d{1,3}$")
_FRAC = re.compile(r"^(\d{1,2})/(\d{1,2})$")
_MIXED = re.compile(r"^(\d{1,4})[ \-](\d{1,2})/(\d{1,2})$")
_UNIT_TAIL = re.compile(r"^(.*?\d)\s*([A-Za-z]{1,6})\.?$")


def hand_qty(raw: str | None) -> tuple[Decimal | None, str | None, list[str]]:
    """(quantity, unit words written with it, flags) from the literal text. Nothing is rounded,
    repaired or guessed: text that is not plainly a number gives None."""
    if raw is None:
        return None, None, []
    s = raw.strip()
    for k, v in _FRACTIONS.items():
        s = s.replace(k, f" {v}" if s and s[0] != k else v)
    s = (
        " ".join(s.replace(",", ".").split())
        if re.fullmatch(r"\d+,\d{1,2}", s)
        else " ".join(s.split())
    )
    unit: str | None = None
    if (m := _UNIT_TAIL.match(s)) and not _INT.match(s):
        s, unit = m.group(1).strip(), m.group(2)
    flags: list[str] = []
    q: Decimal | None = None
    try:
        if _INT.match(s):
            q = Decimal(s)
        elif _DEC.match(s):
            q, flags = Decimal(s), ["FRACTION"]
        elif m := _FRAC.match(s):
            n, d = int(m.group(1)), int(m.group(2))
            q, flags = (Decimal(n) / Decimal(d) if d else None), ["FRACTION"]
        elif m := _MIXED.match(s):
            n, d = int(m.group(2)), int(m.group(3))
            q = Decimal(m.group(1)) + Decimal(n) / Decimal(d) if d and n < d else None
            flags = ["FRACTION"]
    except (InvalidOperation, ZeroDivisionError):
        q = None
    if q is None or q <= 0:
        return None, unit, ["QTY_UNREADABLE"]
    return q.quantize(Decimal("0.0001")).normalize() if q != q.to_integral() else q, unit, flags


def ref_key(ref: str | None) -> str | None:
    """Order references compare by their number ("SO#080778" = "SO 080778" = "50#080778")."""
    if not ref:
        return None
    runs = re.findall(r"\d+", ref.split("#", 1)[1] if "#" in ref else ref)  # "50#080781": after #
    d = max(runs, key=len, default="")
    return d if len(d) >= 3 else None


def name_key(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def is_own_company(name: str | None) -> bool:
    return OWN_NAME in name_key(name)


_TOTAL_ROW = re.compile(r"^\s*(sub\s*-?\s*total|grand\s+total|total|totals)\b", re.I)


# ------------------------------------------------------------------ one row -> one line


@dataclass
class Evidence:
    """What the local (non-Gemini) look knows about a printed row. `known=False`: the row could
    not be located on the page, so nothing can be confirmed or contradicted."""

    known: bool = False
    ink: bool = False  # writing found in this row's quantity box
    hint: str | None = None  # digits the local reader made of it, when it was fairly sure
    neighbour_ink: bool = False  # an adjacent row has writing and Gemini read nothing there


@dataclass
class Line:
    item: str
    qty: Decimal | None
    confidence: Decimal
    flags: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


def is_untouched(r: RowOut) -> bool:
    """A printed row nobody wrote on."""
    marks = (r.qty, r.old, r.pkg, r.wt, r.notes, r.sup, r.unav)
    return not r.w and not r.hand and not r.no and not r.chk and not any(marks)


def is_total_row(r: RowOut) -> bool:
    return bool(_TOTAL_ROW.match(r.item))


def judge(r: RowOut, ev: Evidence, *, handwritten_form: bool) -> Line:
    """Turn one reported row into a line with its own, code-made confidence."""
    qty, unit_txt, flags = hand_qty(r.qty)
    if r.qty is None:
        flags = ["NO_QTY"]
    conf = C_UNCONFIRMED
    if handwritten_form and ev.known:
        if ev.ink:
            conf = C_OK
            if ev.hint is not None and qty is not None:
                hq, _, _ = hand_qty(ev.hint)
                if hq is not None and hq != qty and "FRACTION" not in flags:
                    flags.append(f"LOCAL_READ_DIFFERS:{ev.hint}")
        elif r.qty is not None:
            # Gemini read a number where the local look sees an empty box
            flags.append("POSSIBLE_SHIFT" if ev.neighbour_ink else "NO_INK_SEEN")
    if r.unsure:
        flags.append("UNSURE")
    if r.old:
        flags.append("CROSSED_OUT")
    if r.no:
        flags.append("NO_MARK")
    if unit_txt and not r.pkg:
        flags.append("UNIT_IN_QTY")
    caps = {
        "POSSIBLE_SHIFT": C_CONFLICT, "NO_INK_SEEN": C_DOUBT, "UNSURE": C_DOUBT,
        "LOCAL_READ_DIFFERS": C_CHECK, "FRACTION": C_CHECK, "CROSSED_OUT": C_CHECK,
        "NO_MARK": C_CHECK, "UNIT_IN_QTY": C_CHECK,
    }  # fmt: skip
    for f in flags:
        conf = min(conf, caps.get(f.split(":")[0], conf))
    sup, _, _ = hand_qty(r.sup)
    unav, _, _ = hand_qty(r.unav)
    detail: dict[str, Any] = {
        "item_raw": r.item,
        "qty_raw": r.qty,
        "crossed_out": r.old,  # kept as written; never means "unavailable"
        "pkg": r.pkg or unit_txt,
        "weight": r.wt,
        "case_size": r.size,
        "notes": r.notes,
        "no_mark": r.no,
        "checkmark": r.chk,  # a tick only: not proof of packing or delivery
        "supplied": str(sup) if sup is not None else None,  # None = not written, not zero
        "supplied_raw": r.sup,
        "unavailable": str(unav) if unav is not None else None,
        "unavailable_raw": r.unav,
        "written_item": r.hand,
        "flags": flags,
        "engine": "gemini",
    }
    return Line(r.item, qty, conf, flags, detail)


def compare_totals(written: list[str], lines: list[Line]) -> list[str]:
    """Numbers written apart from the rows are only compared with what the code adds up (all
    lines, each table, the number of lines); they never change a line. One warning lists the
    written numbers that match none of these. References such as "SI#087622" are not totals."""
    nums: list[Decimal] = []
    for w in written[:12]:
        t = w.strip().strip("()[] ")
        if re.search(r"[A-Za-z#]", t):
            continue
        q, _, _ = hand_qty(t)
        if q is not None:
            nums.append(q)
    qtys = [(ln.detail.get("table"), ln.qty) for ln in lines if ln.qty is not None]
    if not nums or not qtys:
        return []
    total = sum((q for _, q in qtys), Decimal(0))
    tables: dict[Any, Decimal] = {}
    for tbl, q in qtys:
        tables[tbl] = tables.get(tbl, Decimal(0)) + q
    explained = {total, Decimal(len(qtys)), *tables.values()}
    odd = list(dict.fromkeys(n for n in nums if n not in explained))
    if not odd:
        return []
    shown = "/".join(f"{n.normalize():f}" for n in odd[:4])
    return [f"TOTAL_DIFFERS:written={shown},lines={len(qtys)},sum={total.normalize():f}"]


def recheck(line: Line, c: CheckRow) -> None:
    """Fold a second look at the cut-out row into the line. Agreement can clear only the doubt a
    second reading settles (a fraction read the same twice); a difference is flagged and both
    readings are kept. The quantity itself is never replaced."""
    q2, _, _ = hand_qty(c.qty)
    same = q2 == line.qty and bool(c.no) == bool(line.detail.get("no_mark"))
    line.detail["recheck"] = {"qty_raw": c.qty, "crossed_out": c.old, "no_mark": c.no, "same": same}
    if not same:
        line.flags.append(f"RECHECK_DIFFERS:{c.qty or '-'}")
        line.confidence = min(line.confidence, C_CONFLICT)
    elif {f.split(":")[0] for f in line.flags} <= {"FRACTION"} and not c.unsure:
        # read the same twice: settled if the local look also found the writing
        line.confidence = C_OK if line.detail.get("local_ink") else C_UNCONFIRMED
        line.flags = []
    line.detail["flags"] = line.flags
