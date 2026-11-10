"""Read supplier documents (invoice, purchase order, delivery note, packing list, receipt) into
header values and line proposals. Nothing is guessed: a value that isn't on the page stays None,
and anything uncertain is sent for review. Confidence is the OCR engine's (min over the words
that make the value). Arithmetic that doesn't add up is flagged, never "fixed".
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import airway_packet
from .models import OcrExtraction, Shipment, Vendor

if TYPE_CHECKING:
    from .ocr import Catalog, Proposal, Token

SUPPLIER_TYPES = ("VENDOR_DOCUMENT", "PURCHASE_ORDER", "DELIVERY_NOTE", "PACKING_LIST", "RECEIPT")
HEADER_FIELDS = (
    "supplier", "document_no", "document_date", "due_date", "currency", "subtotal", "tax", "total",
    "awb_no", "carrier", "arrival_date",
)  # fmt: skip
CURRENCIES = ("USD", "CAD", "EUR", "GBP", "GHS", "JMD", "NGN", "TTD", "XCD")
CENT = Decimal("0.01")
MONEY = re.compile(
    r"^\(?-?[$€£]?\d{1,3}(?:[,]\d{3}){0,3}(?:\.\d{1,4})?\)?$|^\(?-?[$€£]?\d{1,12}(?:\.\d{1,4})?\)?$"
)
MONEY_MAX = Decimal("1e12")  # anything bigger is a misread (and would overflow Numeric(18,4))
DOC_NO = re.compile(
    r"\b(?:invoice|inv|bill|order|po|p\.o\.|delivery|dn|packing|receipt|document|doc)\b\.?\s*"
    r"(?:no\.?|number|num|#)?\s*[:#.]?\s*([A-Z0-9/\-]*\d[A-Z0-9/\-]*)\b",
    re.I,
)
DATE_PATTERNS = (
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b"), "dmy?"),
    (re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b"), "d mon y"),
    (re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b"), "mon d y"),
)
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1
)}  # fmt: skip
UNIT_WORDS = {
    "box": "BOX", "boxes": "BOX", "bx": "BOX", "case": "BOX", "cases": "BOX", "ctn": "BOX",
    "carton": "BOX", "cartons": "BOX", "pc": "PIECE", "pcs": "PIECE", "piece": "PIECE",
    "pieces": "PIECE", "ea": "PIECE", "each": "PIECE", "lb": "LB", "lbs": "LB", "kg": "KG",
    "kgs": "KG",
}  # fmt: skip
STOP_ROWS = re.compile(r"^(sub\s*-?total|total|tax|gst|hst|vat|amount due|balance|freight)\b", re.I)


def money(s: str) -> Decimal | None:
    t = s.strip()
    if not MONEY.match(t):
        return None
    neg = t.startswith("(") and t.endswith(")")
    try:
        v = Decimal(re.sub(r"[^\d.\-]", "", t))
    except InvalidOperation:
        return None
    if abs(v) >= MONEY_MAX:
        return None
    return -v if neg else v


def parse_date(text: str) -> tuple[date | None, bool]:
    """(date, ambiguous). dd/mm vs mm/dd is ambiguous when both parts are ≤ 12."""
    for rx, kind in DATE_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        try:
            if kind == "ymd":
                return date(int(m[1]), int(m[2]), int(m[3])), False
            if kind == "dmy?":
                a, b, y = int(m[1]), int(m[2]), int(m[3])
                if a > 12:
                    return date(y, b, a), False
                if b > 12:
                    return date(y, a, b), False
                return date(y, a, b), True  # assume month/day (North America); flagged
            if kind == "d mon y":
                mon = MONTHS.get(m[2][:3].lower())
                return (date(int(m[3]), mon, int(m[1])), False) if mon else (None, False)
            mon = MONTHS.get(m[1][:3].lower())
            return (date(int(m[3]), mon, int(m[2])), False) if mon else (None, False)
        except ValueError:
            continue
    return None, False


@dataclass
class SupplierReading:
    header: dict[str, dict[str, Any]]
    vendor_id: uuid.UUID | None
    proposals: list[Proposal]
    warnings: list[str]


def _val(value: Any, toks: list[Token], **extra: Any) -> dict[str, Any]:
    conf = min((t.confidence for t in toks), default=None)
    return {"value": value, "confidence": str(conf) if conf is not None else None, **extra}


def _box(r: list[Token]) -> dict[str, Any]:
    return {
        "page": r[0].page,
        "x0": str(min(x.x0 for x in r)),
        "y0": str(min(x.y0 for x in r)),
        "x1": str(max(x.x1 for x in r)),
        "y1": str(max(x.y1 for x in r)),
    }


def _row_text(r: list[Token]) -> str:
    return " ".join(t.text for t in r)


def read(
    db: Session,
    company_id: uuid.UUID,
    rows: list[list[Token]],
    cat: Catalog,
    threshold: Decimal,
    make: Any,
) -> SupplierReading:
    """`make(**kw)` builds a Proposal (passed in to avoid an import cycle)."""
    texts = [_row_text(r) for r in rows]
    if airway_packet.detect(texts):  # airport delivery: customs packet with its own layout
        h, vendor_id, lines, warns = airway_packet.read(
            db, company_id, rows, cat, threshold, make, _val, _box
        )
        packet = {f: h.get(f, _val(None, [])) for f in HEADER_FIELDS} | {"kind": h["kind"]}
        return SupplierReading(packet, vendor_id, lines, warns)
    header: dict[str, dict[str, Any]] = {f: _val(None, []) for f in HEADER_FIELDS}
    warnings: list[str] = []

    # supplier: a known supplier's name or code anywhere in the first rows
    vendors = db.scalars(select(Vendor).where(Vendor.company_id == company_id)).all()
    vendor: Vendor | None = None
    for r, t in list(zip(rows, texts, strict=True))[:15]:
        low = t.casefold()
        for v in vendors:
            name = re.sub(r"\s*\(.*?\)\s*", " ", v.name).strip().casefold()
            if (name and name in low) or re.search(rf"\b{re.escape(v.code.casefold())}\b", low):
                vendor = v
                header["supplier"] = _val(v.name, r, matched=True)
                break
        if vendor:
            break
    if vendor is None and rows:
        header["supplier"] = _val(texts[0][:120], rows[0], matched=False)
        warnings.append("SUPPLIER_NOT_FOUND")

    for r, t in zip(rows, texts, strict=True):
        low = t.casefold()
        if header["document_no"]["value"] is None and (m := DOC_NO.search(t)):
            if not re.fullmatch(r"(date|no|number|total|amount)", m[1], re.I):
                header["document_no"] = _val(m[1].upper(), r)
        if "due" in low and header["due_date"]["value"] is None:
            d, amb = parse_date(t)
            if d:
                header["due_date"] = _val(d.isoformat(), r, ambiguous=amb)
        elif "date" in low and header["document_date"]["value"] is None:
            d, amb = parse_date(t)
            if d:
                header["document_date"] = _val(d.isoformat(), r, ambiguous=amb)
        if header["awb_no"]["value"] is None and re.search(r"a\.?\s*w\.?\s*b\b|waybill", t, re.I):
            if awb := airway_packet.awb_number(t):
                header["awb_no"] = _val(awb, r)
                header["carrier"] = _val(airway_packet.AIRLINES.get(awb[:3]), [])
        if header["currency"]["value"] is None:
            cur = next((c for c in CURRENCIES if re.search(rf"\b{c}\b", t)), None)
            if cur:
                header["currency"] = _val(cur, r)
        lead = STOP_ROWS.match(t.strip())
        if lead:
            amounts = [(a, tok) for tok in r if (a := money(tok.text)) is not None]
            if amounts:
                a, tok = amounts[-1]
                key = lead[1].casefold().replace(" ", "").replace("-", "")
                field = (
                    "subtotal" if key.startswith("sub")
                    else "tax" if key in ("tax", "gst", "hst", "vat")
                    else "total" if key in ("total", "amountdue", "balance")
                    else None
                )  # fmt: skip
                if field and header[field]["value"] is None:
                    header[field] = _val(str(a.quantize(CENT)), [tok])
    if vendor is None and header["currency"]["value"] is None:
        pass
    elif header["currency"]["value"] is None and vendor is not None:
        header["currency"] = _val(None, [], supplier_default=vendor.currency)

    # line items: description … qty [unit] price amount, above the totals
    props: list[Proposal] = []
    line_sum = Decimal(0)
    for r, t in zip(rows, texts, strict=True):
        if STOP_ROWS.match(t.strip()):
            break
        nums = [(i, money(tok.text)) for i, tok in enumerate(r)]
        trailing: list[Decimal] = []
        cut = len(r)
        for i, num in reversed(nums):
            if num is None:
                break
            trailing.insert(0, num)
            cut = i
        unit = None
        if cut > 0 and r[cut - 1].text.casefold().strip(".") in UNIT_WORDS:
            unit = UNIT_WORDS[r[cut - 1].text.casefold().strip(".")]
            cut -= 1
            # "4 box 12.50 50.00": the qty sits before the unit
            if cut > 0 and money(r[cut - 1].text) is not None:
                trailing.insert(0, money(r[cut - 1].text) or Decimal(0))
                cut -= 1
        desc_toks = r[:cut]
        desc = " ".join(tok.text for tok in desc_toks)
        if len(trailing) < 2 or len(desc) < 3 or not re.search(r"[A-Za-z]{3}", desc):
            continue
        qty, price, amount = (
            (trailing + [None])[:3] if len(trailing) >= 3 else (trailing[0], trailing[1], None)
        )
        if len(trailing) > 3:
            qty, price, amount = trailing[-3], trailing[-2], trailing[-1]
        prod = cat.match(desc) or cat.match(desc_toks[0].text if desc_toks else "")
        if prod is None and vendor is not None:
            prod = cat.by_alias.get(re.sub(r"\s+", " ", desc.casefold()).strip())
        if prod and unit is None:
            unit = prod.stock_unit
        conf = min(tok.confidence for tok in r)
        status = "OK"
        if prod is None:
            status = "UNMAPPED"
        elif conf < threshold:
            status = "LOW_CONFIDENCE"
        # A delivered line can't have a zero/negative quantity or a negative price: treat such
        # readings as unread so a person types them, instead of the database refusing later.
        if (qty is not None and qty <= 0) or (price is not None and price < 0):
            qty = qty if qty is not None and qty > 0 else None
            price = price if price is not None and price >= 0 else None
            amount = None
            status = "LOW_CONFIDENCE" if status == "OK" else status
            warnings.append(f"LINE_NEGATIVE:{len(props) + 1}")
        if qty is not None and price is not None and amount is not None:
            if (qty * price).quantize(CENT) != amount.quantize(CENT):
                status = "LOW_CONFIDENCE" if status == "OK" else status
                warnings.append(f"LINE_MATH:{len(props) + 1}")
            line_sum += amount
        props.append(
            make(
                kind="LINE",
                label=f"{prod.sku} {prod.name}" if prod else desc[:200],
                raw_text=t[:500],
                product_id=prod.id if prod else None,
                unit_code=unit,
                qty=qty,
                confidence=conf,
                status=status,
                bbox=_box(r),
                unit_price=price,
                amount=amount.quantize(CENT) if amount is not None else None,
            )
        )  # fmt: skip
    sub = header["subtotal"]["value"] or (
        header["total"]["value"] if header["tax"]["value"] is None else None
    )
    if props and sub is not None and line_sum and Decimal(sub) != line_sum.quantize(CENT):
        warnings.append("LINES_NOT_EQUAL_SUBTOTAL")
    s, tx, tot = (header[k]["value"] for k in ("subtotal", "tax", "total"))
    if (
        s is not None
        and tx is not None
        and tot is not None
        and Decimal(s) + Decimal(tx) != Decimal(tot)
    ):
        warnings.append("TOTALS_DONT_ADD_UP")
    if not props:
        warnings.append("NO_LINES")
    return SupplierReading(header, vendor.id if vendor else None, props, warnings)


def duplicate_of(db: Session, ex: OcrExtraction) -> str | None:
    """Another live reading or a shipment with the same supplier + document number."""
    no = (ex.header or {}).get("document_no", {}).get("value")
    if not no or ex.vendor_id is None:
        return None
    other = db.scalar(
        select(OcrExtraction.id).where(
            OcrExtraction.company_id == ex.company_id,
            OcrExtraction.id != ex.id,
            OcrExtraction.vendor_id == ex.vendor_id,
            OcrExtraction.status.notin_(("REJECTED", "SUPERSEDED", "FAILED")),
            OcrExtraction.header["document_no"]["value"].astext == no,
        )
    )
    if other:
        return f"reading {other}"
    shp = db.scalar(
        select(Shipment.shipment_no).where(
            Shipment.company_id == ex.company_id,
            Shipment.vendor_id == ex.vendor_id,
            func.upper(Shipment.vendor_invoice_no) == no.upper(),
            Shipment.status != "CANCELLED",
        )
    )
    return f"shipment {shp}" if shp else None
