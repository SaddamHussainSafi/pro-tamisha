"""Air-freight customs packet: the papers that come with a delivery collected at the airport,
scanned together. Typically a CBSA B3 Commercial Accounting Declaration, the vendor's commercial
invoice and a CFIA Confirmation of Sale (sample: This Fruta SAS, Colombia, AWB 014-…, 2026-11).

- Product lines come from the CFIA Confirmation of Sale table (one line per commodity with tariff
  code, origin, net weight, number of packages, price per package and total, US-style numbers).
- Without that page, the vendor invoice table is read instead. Latin-American vendors write
  numbers European style ("1.936" = 1,936; "2,0" = 2.0); the reading that makes
  boxes × price = amount is used.
- The B3 values sit in shaded boxes that OCR can't read reliably; the page only identifies the
  packet. Customs totals stay a person's check.
Nothing here changes stock: the result is reviewed and then sent to Receiving as declared lines.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Company, Vendor

if TYPE_CHECKING:
    from .ocr import Catalog, Token

CFIA = re.compile(r"confirmation\s+(of\s+sale|de\s+vente)", re.I)
B3 = re.compile(r"commercial\s+accounting\s+declaration|d[ée]claration\s+en\s+d[ée]tail", re.I)
CENT = Decimal("0.01")
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1
)}  # fmt: skip
MONTHS.update({"ene": 1, "abr": 4, "ago": 8, "dic": 12})  # Spanish abbreviations
# Air waybill: 3-digit airline prefix + 8-digit serial, often printed "014 - 4275 7002".
AWB = re.compile(r"\b(\d{3})\s*-\s*(\d{4})\s*(\d{4})\b|\b(\d{3})\s*-?\s*(\d{8})\b")
AIRLINES = {
    "001": "American Airlines",
    "006": "Delta",
    "014": "Air Canada",
    "016": "United",
    "045": "LATAM",
}
# CFIA line: [tariff code] description [noise] origin net-weight unit packages price total
CFIA_LINE = re.compile(
    r"(?P<hs>\b\d{4}\.?\d{2}\.?\d{2}\.?\d{2}\b)(?P<desc>.*?)\s+(?P<origin>[A-Za-z]{2})\s+"
    r"(?P<net>\d[\d.,]*)\s*(?P<wu>KGS?|LBS?)\b\W*\s+(?P<pkgs>\d{1,6})\s+"
    r"(?P<price>\d[\d,]*\.\d{2})\s+(?P<total>\d[\d,]*\.\d{2})\s*$",
    re.I,
)
INVOICE_NO = re.compile(
    r"invoice\s*(?:number|no\.?|n[°º])\s*[:#.]?\s*([A-Z]{1,6}[\s-]?\d{2,8})", re.I
)
CODE = re.compile(r"\b([A-Z]{2,6}[\s-]?\d{3,8})\b")
INVOICE_DATE = re.compile(
    r"invoice\s+date\s*[:.]?\s*(\d{1,2})[-/ .]([A-Za-z]{3})[a-z]*[-/ .](\d{2,4})", re.I
)
ISO = re.compile(r"\b(20\d{2})\s*-\s*(\d)\s?(\d)\s*-\s*(\d)\s?(\d)\b")
CREDIT_DAYS = re.compile(
    r"credit\s*\(?\s*(\d{1,3})\s*d[ií]as?|credit\s*\(?\s*(\d{1,3})\s*days?", re.I
)
CURRENCIES = ("CAD", "USD", "EUR", "GBP", "COP")


def detect(texts: list[str]) -> bool:
    return any(CFIA.search(t) or B3.search(t) for t in texts)


def awb_number(text: str) -> str | None:
    m = AWB.search(text)
    if not m:
        return None
    if m[1]:
        return f"{m[1]}-{m[2]}{m[3]}"
    return f"{m[4]}-{m[5]}"


def numbers(token: str) -> list[Decimal]:
    """Possible values of a printed number. '1.936' is 1,936 European style or 1.936 US style;
    '2,0' is 2.0; '1,936.00' is US style. Unreadable text gives []."""
    s = token.strip().lstrip("$").rstrip(".,;:")
    try:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            return [Decimal(s.replace(".", "")), Decimal(s)]
        if re.fullmatch(r"\d{1,3}(\.\d{3})*,\d{1,2}", s):
            return [Decimal(s.replace(".", "").replace(",", "."))]
        if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):
            return [Decimal(s.replace(",", ""))]
        if re.fullmatch(r"\d+(\.\d+)?", s):
            return [Decimal(s)]
    except InvalidOperation:
        pass
    return []


def _clean_desc(desc: str) -> str:
    words = re.sub(r"[^A-Za-z0-9()/&' -]+", " ", desc).split()

    # table rules and marks that OCR reads as numbers or single letters ("|:" -> "I")
    def stray(w: str) -> bool:
        return bool(re.fullmatch(r"\d+", w)) or len(w) == 1

    while words and stray(words[-1]):
        words.pop()
    while words and stray(words[0]):
        words.pop(0)
    return " ".join(words)


def _variants(desc: str) -> list[str]:
    """The description and its singular forms ('SUGAR MANGOES' -> 'SUGAR MANGO')."""
    out = [desc]
    w = desc.split()
    if w:
        last = w[-1]
        for cut in ("ES", "S"):
            if last.upper().endswith(cut) and len(last) > len(cut) + 2:
                out.append(" ".join([*w[:-1], last[: -len(cut)]]))
    return out


def _match(cat: Catalog, desc: str) -> Any:
    for v in _variants(desc):
        if p := cat.match(v):
            return p

    # OCR often drops the space between words ("SUGARMANGOES"): compare without spaces
    def squash(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.casefold())

    known = {**getattr(cat, "by_name", {}), **getattr(cat, "by_alias", {})}
    flat = {squash(k): p for k, p in known.items()}
    return next((flat[squash(v)] for v in _variants(desc) if squash(v) in flat), None)


def _iso(text: str) -> date | None:
    m = ISO.search(text)
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2] + m[3]), int(m[4] + m[5]))
    except ValueError:
        return None


def _after(rows: list[str], label: re.Pattern[str], look: int = 3) -> list[str]:
    """The rows right after the first row matching `label` (form values sit under labels)."""
    for i, t in enumerate(rows):
        if label.search(t):
            return rows[i + 1 : i + 1 + look]
    return []


def read(
    db: Session,
    company_id: uuid.UUID,
    rows: list[list[Token]],
    cat: Catalog,
    threshold: Decimal,
    make: Any,
    val: Any,
    box: Any,
) -> tuple[dict[str, dict[str, Any]], uuid.UUID | None, list[Any], list[str]]:
    """(header, vendor_id, proposals, warnings). `val`/`box`/`make` are supplier_ocr helpers."""
    texts = [" ".join(t.text for t in r) for r in rows]
    pages: dict[int, str] = {}
    for r, t in zip(rows, texts, strict=True):
        p = r[0].page
        if CFIA.search(t):
            pages[p] = "CFIA"
        elif B3.search(t):
            pages.setdefault(p, "B3")
    kind = {r[0].page: pages.get(r[0].page, "INVOICE") for r in rows}
    by = {k: [(r, t) for r, t in zip(rows, texts, strict=True) if kind[r[0].page] == k]
          for k in ("CFIA", "INVOICE", "B3")}  # fmt: skip
    cfia_t = [t for _, t in by["CFIA"]]
    inv_t = [t for _, t in by["INVOICE"]]
    warnings: list[str] = []
    header: dict[str, dict[str, Any]] = {}

    def first(pairs: list[tuple[list[Token], str]], fn: Any) -> tuple[Any, list[Token]]:
        for r, t in pairs:
            if (v := fn(t)) is not None:
                return v, r
        return None, []

    # supplier: one of yours by name ("S.A.S." and "SAS" alike), else the CFIA vendor line
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", s.casefold().replace(".", "")).strip()

    vendors = db.scalars(select(Vendor).where(Vendor.company_id == company_id)).all()
    vendor = None
    for r, t in list(zip(rows, texts, strict=True)):
        low = f" {norm(t)} "
        vendor = next((v for v in vendors if f" {norm(v.name)} " in low), None)
        if vendor:
            header["supplier"] = val(vendor.name, r, matched=True)
            break
    if vendor is None:
        ours = db.scalar(select(Company.legal_name).where(Company.id == company_id)) or ""
        own = norm(ours).split()[0] if ours else ""
        guess = None
        # CFIA: "VENDOR NAME  OUR NAME" on one row under the Vendor/Purchaser labels
        for t in _after(cfia_t, re.compile(r"\bvendor\b", re.I), 4):
            if own and re.search(rf"\b{re.escape(own)}\b", t, re.I):
                cut = re.split(rf"\b{re.escape(own)}\b", t, maxsplit=1, flags=re.I)[0]
                if re.search(r"[A-Za-z]{3}", cut):
                    guess = cut.strip(" |:-")
                    break
        # invoice: the letterhead, the first plain name row
        skip = re.compile(r"invoic|electronic|authori|exporter|effective|nit\b|phone|\d{4}", re.I)
        for t in inv_t[:4] if guess is None else []:
            if re.search(r"[A-Za-z]{3}", t) and not skip.search(t):
                guess = t.strip(" |:-")
                break
        header["supplier"] = val(guess, [], matched=False)
        warnings.append("SUPPLIER_NOT_FOUND")

    # document number and date: the vendor invoice first, then the CFIA form
    no, r = first(by["INVOICE"], lambda t: (m := INVOICE_NO.search(t)) and m[1].upper())
    if no is None:
        tail = _after(cfia_t, re.compile(r"commercial\s+invoice", re.I), 3)
        codes = [c for t in tail for c in CODE.findall(t)]
        no = codes[-1].upper() if codes else None
    header["document_no"] = val(re.sub(r"\s+", " ", no) if no else None, r)

    def inv_date(t: str) -> date | None:
        m = INVOICE_DATE.search(t)
        if not m or m[2][:3].lower() not in MONTHS:
            return None
        y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        try:
            return date(y, MONTHS[m[2][:3].lower()], int(m[1]))
        except ValueError:
            return None

    d, r = first(by["INVOICE"], inv_date)
    shipped = next((x for t in _after(cfia_t, re.compile(r"date\s+of\s+direct\s+shipment", re.I), 4)
                    if (x := _iso(t))), None)  # fmt: skip
    doc_date = d or shipped
    header["document_date"] = val(doc_date.isoformat() if doc_date else None, r)
    days, _ = first(by["INVOICE"], lambda t: (m := CREDIT_DAYS.search(t)) and int(m[1] or m[2]))
    due = doc_date + timedelta(days=days) if doc_date and days else None
    header["due_date"] = val(due.isoformat() if due else None, [], terms_days=days)

    cur = None
    for t in _after(cfia_t, re.compile(r"currency", re.I), 3) + [
        t for t in cfia_t + inv_t if "transaction" not in t.lower()
    ]:
        cur = next((c for c in CURRENCIES if re.search(rf"\b{c}\b", t)), None)
        if cur:
            break
    header["currency"] = val(cur, [])

    awb, r = first(by["INVOICE"] + by["CFIA"], lambda t: awb_number(t)
                   if re.search(r"a\.?\s*w\.?\s*b|waybill|conveyance|^\W*\d{3}\s*-", t, re.I)
                   else None)  # fmt: skip
    if awb is None:
        for t in _after(cfia_t, re.compile(r"conveyance", re.I), 3):
            if awb := awb_number(t):
                break
    header["awb_no"] = val(awb, r)
    header["carrier"] = val(AIRLINES.get(awb[:3]) if awb else None, [])
    arrived = next((x for t in _after(cfia_t, re.compile(r"date\s+of\s+delivery", re.I), 3) +
                    [t for t in cfia_t if re.search(r"date\s+of\s+delivery", t, re.I)]
                    if (x := _iso(t))), None)  # fmt: skip
    header["arrival_date"] = val(arrived.isoformat() if arrived else None, [])

    # product lines
    props: list[Any] = []
    line_sum = Decimal(0)

    def add(r: list[Token], desc: str, pkgs: Decimal, price: Decimal, amount: Decimal,
            raw: str, math_ok: bool, extra: str = "") -> None:  # fmt: skip
        nonlocal line_sum
        prod = _match(cat, desc)
        unit = "BOX"
        # table rules and stray marks read with low confidence don't make the line unsure
        real = [t for t in r if len(t.text) > 1 and re.search(r"[A-Za-z0-9]{2}", t.text)]
        conf = min(t.confidence for t in (real or r))
        status = "OK"
        if prod is None or not cat.unit_ok(prod, unit):
            status = "UNMAPPED"
        elif conf < threshold or not math_ok:
            status = "LOW_CONFIDENCE"
        if not math_ok:
            warnings.append(f"LINE_MATH:{len(props) + 1}")
        line_sum += amount
        props.append(
            make(
                kind="LINE",
                label=f"{prod.sku} {prod.name}" if prod else desc[:200],
                raw_text=(raw + extra)[:500],
                product_id=prod.id if prod else None,
                unit_code=unit,
                qty=pkgs,
                confidence=conf,
                status=status,
                bbox=box(r),
                unit_price=price,
                amount=amount.quantize(CENT),
            )
        )

    for r, t in by["CFIA"]:
        m = CFIA_LINE.search(t)
        if not m:
            continue
        desc = _clean_desc(m["desc"])
        if not re.search(r"[A-Za-z]{3}", desc):
            continue
        pkgs, price, total = (
            Decimal(m["pkgs"]),
            Decimal(m["price"].replace(",", "")),
            Decimal(m["total"].replace(",", "")),
        )
        note = f" [net {m['net']} {m['wu'].upper()}, origin {m['origin'].upper()}, HS {m['hs']}]"
        add(r, desc, pkgs, price, total, t, (pkgs * price).quantize(CENT) == total, note)

    inv_lines: list[tuple[list[Token], str, Decimal, Decimal, Decimal]] = []
    for r, t in by["INVOICE"]:
        words = [w for w in re.split(r"[\s|]+", t) if w]
        nums: list[tuple[int, list[Decimal]]] = [
            (i, v) for i, w in enumerate(words) if (v := numbers(w))
        ]
        if len(nums) < 5 or not words or numbers(words[0]):
            continue
        name = " ".join(w for w in words[: nums[0][0]])
        if not re.search(r"[A-Za-z]{3}", name) or re.search(r"total|gross|net|weight", name, re.I):
            continue
        # ... boxes, kg/box, kg, price, amount: pick readings where boxes × price = amount
        last5 = [v for _, v in nums[-5:]]
        found: tuple[Decimal, Decimal, Decimal] | None = None
        for qb in last5[0]:
            for qp in last5[3]:
                for qa in last5[4]:
                    if qb > 0 and (qb * qp).quantize(CENT) == qa.quantize(CENT):
                        found = (qb, qp, qa)
        if found:
            inv_lines.append((r, name, *found))

    if not props:
        for row, name, qb, qp, qa in inv_lines:
            add(row, _clean_desc(name), qb, qp, qa, " ".join(tok.text for tok in row), True)
    elif inv_lines:
        inv_total = sum((x[4] for x in inv_lines), Decimal(0))
        if inv_total.quantize(CENT) != line_sum.quantize(CENT):
            warnings.append("PACKET_INVOICE_MISMATCH")

    if props:
        total = line_sum.quantize(CENT)
        header["subtotal"] = val(str(total), [])
        header["total"] = val(str(total), [])
        # any total printed on the CFIA form or the invoice ("Total cost all commodities",
        # "Coût total tous produits > 1936.00", "TOTAL VALUE 1.936")
        printed = [
            x for t in cfia_t + inv_t if re.search(r"total", t, re.I)
            for w in t.split() for x in numbers(w)
        ]  # fmt: skip
        if printed and total not in [p.quantize(CENT) for p in printed]:
            warnings.append("PACKET_TOTAL_MISMATCH")
    else:
        header["subtotal"] = val(None, [])
        header["total"] = val(None, [])
        warnings.append("NO_LINES")
    header["tax"] = val(None, [])
    header["kind"] = {"value": "AIR_PACKET", "confidence": None}
    return header, vendor.id if vendor else None, props, warnings
