"""Deterministic sales invoice PDF renderer (no third-party dependency).

Layout: the owner's official sales invoice (OPEN_DECISIONS D-12, settled 2026-11-09), in black
and white only: black-and-white logo, grey fills at most, one receiver signature, no S/N column.
US Letter, the paper size of the owner's printed invoice.

The renderer is a pure function of the immutable invoice snapshot: same snapshot -> byte-identical
PDF (the logo is a fixed JPEG in `assets/`; there are no timestamps). The bytes are rendered once
at issue time, hashed and stored; reprints return the stored bytes, so a template change only
affects invoices issued after it.

`_Page`, `_assemble`, `_clip`, `MARGIN`, `PAGE_W` and `PAGE_H` are also used to draw the synthetic
order sheets and supplier documents for OCR tests; their output must stay unchanged.
"""

import hashlib
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Any

from .units import LB_TO_KG

TEMPLATE_VERSION = "sales-invoice-1"
PAGE_W, PAGE_H = 612, 792  # US Letter, points
MARGIN = 48  # OCR test sheets; the invoice uses its own frame below
LOGO_PATH = Path(__file__).parent / "assets" / "invoice_logo.jpg"


def _txt(s: Any) -> str:
    """PDF literal string in WinAnsi; unsupported characters become '?'."""
    raw = str(s).encode("cp1252", errors="replace").decode("cp1252")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _clip(v: Any, n: int) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "~"


# Advance widths (1/1000 em) of WinAnsi codes 32..255 for the standard 14 fonts used here.
# Helvetica-BoldOblique has the same widths as Helvetica-Bold.
_W_REGULAR = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 "
    "556 556 556 556 278 278 584 584 584 556 1015 667 667 722 722 667 611 778 722 278 500 667 "
    "556 833 722 778 667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556 333 556 "
    "556 500 556 556 278 556 556 222 222 500 222 833 556 556 556 556 333 500 278 556 500 722 "
    "500 500 500 334 260 334 584 761 556 0 222 556 333 1000 556 556 333 1000 667 333 1000 0 611 "
    "0 0 222 222 333 333 350 556 1000 333 1000 500 333 944 0 500 667 278 333 556 556 556 556 "
    "260 556 333 737 370 556 584 333 737 333 400 584 333 333 333 556 537 278 333 333 365 556 "
    "834 834 834 611 667 667 667 667 667 667 1000 722 667 667 667 667 278 278 278 278 722 722 "
    "778 778 778 778 778 584 778 722 722 722 722 667 667 611 556 556 556 556 556 556 889 500 "
    "556 556 556 556 278 278 278 278 556 556 556 556 556 556 556 584 611 556 556 556 556 500 "
    "556 500"
)
_W_BOLD = (
    "278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 556 556 556 556 556 556 "
    "556 556 556 556 333 333 584 584 584 611 975 722 722 722 722 667 611 778 722 278 556 722 "
    "611 833 722 778 667 778 722 667 611 722 667 944 667 667 611 333 278 333 584 556 333 556 "
    "611 556 611 556 333 611 611 278 278 556 278 889 611 611 611 611 389 556 333 611 556 778 "
    "556 556 500 389 280 389 584 761 556 0 278 556 500 1000 556 556 333 1000 667 333 1000 0 611 "
    "0 0 278 278 500 500 350 556 1000 333 1000 556 333 944 0 500 667 278 333 556 556 556 556 "
    "280 556 333 737 370 556 584 333 737 333 400 584 333 333 333 611 556 278 333 333 365 556 "
    "834 834 834 611 722 722 722 722 722 722 1000 722 667 667 667 667 278 278 278 278 722 722 "
    "778 778 778 778 778 584 778 722 722 722 722 667 667 611 556 556 556 556 556 556 889 556 "
    "556 556 556 556 278 278 278 278 611 611 611 611 611 611 611 584 611 611 611 611 611 556 "
    "611 556"
)
_WIDTHS = {
    "F1": tuple(int(w) for w in _W_REGULAR.split()),
    "F2": tuple(int(w) for w in _W_BOLD.split()),
}
_WIDTHS["F4"] = _WIDTHS["F2"]


def _clean(s: Any) -> str:
    """One printable line: control characters (newlines, tabs) become spaces."""
    return "".join(" " if ord(c) < 32 else c for c in str(s))


def text_width(s: Any, size: float, font: str = "F1") -> float:
    table = _WIDTHS[font]
    raw = _clean(s).encode("cp1252", errors="replace")
    return sum(table[b - 32] or 556 for b in raw) * size / 1000


def _fit(s: Any, width: float, size: float, font: str = "F1") -> str:
    """Text cut with an ellipsis so it fits `width` points."""
    t = _clean(s)
    if text_width(t, size, font) <= width:
        return t
    while t and text_width(t + "…", size, font) > width:
        t = t[:-1]
    return t.rstrip() + "…"


class _Page:
    def __init__(self) -> None:
        self.ops: list[str] = []

    def text(self, x: float, y: float, s: Any, size: float = 9, bold: bool = False) -> None:
        font = "F2" if bold else "F1"
        self.ops.append(f"BT /{font} {size} Tf {x:.1f} {y:.1f} Td ({_txt(s)}) Tj ET")

    def hand(self, x: float, y: float, s: Any, size: float = 14) -> None:
        """Synthetic 'handwriting' (Courier-Oblique, /F3) - used only to build OCR test sheets."""
        self.ops.append(f"BT /F3 {size} Tf {x:.1f} {y:.1f} Td ({_txt(s)}) Tj ET")

    def right(self, x: float, y: float, s: Any, size: float = 9, bold: bool = False) -> None:
        # Helvetica digits are 0.556 em; good enough for right-aligned numeric columns.
        w = len(str(s)) * size * 0.556
        self.text(x - w, y, s, size, bold)

    def line(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.ops.append(f"{x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S")

    # ---------------------------------------------------------------- invoice drawing
    # Measured text, grey-only fills and an image. Text keeps the `BT /Fn size Tf x y Td (..)
    # Tj ET` form the fake OCR provider reads.

    def put(
        self,
        x: float,
        y: float,
        s: Any,
        size: float = 9,
        font: str = "F1",
        align: str = "left",
        white: bool = False,
    ) -> None:
        t = _clean(s)
        if not t:
            return
        w = text_width(t, size, font)
        x0 = x - w if align == "right" else x - w / 2 if align == "center" else x
        op = f"BT /{font} {size} Tf {x0:.2f} {y:.2f} Td ({_txt(t)}) Tj ET"
        self.ops.append(f"q 1 g {op} Q" if white else op)

    def rule(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.5) -> None:
        self.ops.append(f"q {width} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S Q")

    def box(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        r: float = 0,
        fill: float | None = None,
        stroke: float | None = 0.6,
    ) -> None:
        """Rectangle (rounded when r > 0). fill = grey level (0 black .. 1 white)."""
        if r > 0:
            k = 0.5523 * r
            x1, y1 = x + w, y + h
            c = (  # (operator, coordinates): four straight sides joined by quarter arcs
                ("m", (x + r, y)),
                ("l", (x1 - r, y)),
                ("c", (x1 - r + k, y, x1, y + r - k, x1, y + r)),
                ("l", (x1, y1 - r)),
                ("c", (x1, y1 - r + k, x1 - r + k, y1, x1 - r, y1)),
                ("l", (x + r, y1)),
                ("c", (x + r - k, y1, x, y1 - r + k, x, y1 - r)),
                ("l", (x, y + r)),
                ("c", (x, y + r - k, x + r - k, y, x + r, y)),
            )
            pts = [" ".join(f"{v:.2f}" for v in vs) + f" {op}" for op, vs in c] + ["h"]
            path = " ".join(pts)
        else:
            path = f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re"
        self._paint(path, fill, stroke)

    def polygon(self, pts: list[tuple[float, float]], fill: float) -> None:
        path = " ".join(
            f"{px:.2f} {py:.2f} {'m' if i == 0 else 'l'}" for i, (px, py) in enumerate(pts)
        )
        self._paint(path + " h", fill, None)

    def _paint(self, path: str, fill: float | None, stroke: float | None) -> None:
        parts = ["q"]
        if fill is not None:
            parts.append(f"{fill} g")
        if stroke is not None:
            parts.append(f"{stroke} w")
        op = "B" if fill is not None and stroke is not None else "f" if fill is not None else "S"
        self.ops.append(" ".join([*parts, path, op, "Q"]))

    def image(self, name: str, x: float, y: float, w: float, h: float) -> None:
        self.ops.append(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm /{name} Do Q")

    def stream(self) -> bytes:
        return ("0.5 w\n" + "\n".join(self.ops)).encode("cp1252")  # WinAnsiEncoding


# ------------------------------------------------------------------ logo


def _jpeg_info(data: bytes) -> tuple[int, int, int]:
    """(width, height, components) from a baseline/progressive JPEG's SOF marker."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            raise ValueError("not a JPEG")
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h = int.from_bytes(data[i + 5 : i + 7], "big")
            w = int.from_bytes(data[i + 7 : i + 9], "big")
            return w, h, data[i + 9]
        i += 2 + int.from_bytes(data[i + 2 : i + 4], "big")
    raise ValueError("JPEG size not found")


@cache
def logo() -> tuple[bytes, int, int]:
    data = LOGO_PATH.read_bytes()
    w, h, comps = _jpeg_info(data)
    if comps != 1:
        raise ValueError("invoice logo must be a greyscale JPEG (black and white print)")
    return data, w, h


# ------------------------------------------------------------------ formatting

CENT = Decimal("0.01")
NOTES = (
    "No claims after 24 hours on fruits and vegetables.",
    "Please verify all items and quantities upon receipt.",
    "Thank you for your business.",
)
DASH = "—"


def _dec(v: Any) -> Decimal | None:
    return None if v in (None, "") else Decimal(str(v))


def _plain(d: Decimal) -> str:
    """8, 22.5 (no trailing zeros)."""
    s = f"{d:f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _two(d: Decimal) -> str:
    """1,522.00"""
    return f"{d.quantize(CENT, rounding=ROUND_HALF_UP):,.2f}"


def _rate(d: Decimal) -> str:
    """Unit price: 2 decimals, more only when the rate has them (0.4375)."""
    return _two(d) if d == d.quantize(CENT) else f"{d.normalize():,f}"


def _money(v: Any) -> str:
    d = _dec(v) or Decimal(0)
    return f"-${_two(-d)}" if d < 0 else f"${_two(d)}"


def _date(iso: str) -> str:
    """2026-09-24 -> 24-09-2026 (the owner's printed invoice)."""
    parts = (iso or "").split("-")
    return f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else iso or ""


def _terms(days: Any) -> str:
    n = int(days or 0)
    return "Due on receipt" if n == 0 else f"Net {n} Day" + ("" if n == 1 else "s")


def _lines(v: Any) -> list[str]:
    return [x.strip() for x in str(v or "").splitlines() if x.strip()]


def row_values(ln: dict[str, Any]) -> dict[str, Any]:
    """What one snapshot line prints: Qty, Units, LBS, KG, Unit Price, Amount.

    Boxes print as cases ("CASE-40" = the case size's lb), with LBS = cases x lb per case.
    Weighed boxes (billed per lb) print the cases and their entered weight; the price is per lb.
    Weights use the same Decimal conversion as the catalog (units.LB_TO_KG)."""
    unit = ln.get("unit_code", "")
    billed = _dec(ln.get("billed_qty")) or Decimal(0)
    cases = _dec(ln.get("cases"))
    pack = _dec(ln.get("pack_lb")) or _dec(ln.get("case_weight_lb"))
    rate = _dec(ln.get("unit_rate")) or Decimal(0)
    lbs: Decimal | None = None
    kg: Decimal | None = None
    price = _rate(rate)
    counted = True
    if unit == "BOX":
        qty, units = billed, f"CASE-{_plain(pack)}" if pack else "CASE"
        lbs = billed * pack if pack else None
    elif unit == "LB" and cases:
        qty, units, lbs, price = (
            cases,
            f"CASE-{_plain(pack)}" if pack else "CASE",
            billed,
            f"{price}/lb",
        )
    elif unit == "PIECE":
        qty, units = billed, "PIECE"
        lbs = billed * pack if pack else None
    elif unit == "KG":
        qty, units, counted = billed, "KG", False
        kg = billed
        lbs = (billed / LB_TO_KG).quantize(Decimal("0.0001"))
    else:  # LB sold by weight
        qty, units, lbs, counted = billed, unit or "LB", billed, False
    if lbs is not None and kg is None:
        kg = lbs * LB_TO_KG
    return {
        "qty": qty,
        "qty_text": _plain(qty),
        "counted": counted,
        "units": units,
        "lbs": lbs,
        "kg": kg,
        "price": price,
        "amount": _two(_dec(ln.get("line_amount")) or Decimal(0)),
        "description": ln.get("description", ""),
    }


# ------------------------------------------------------------------ page frame

L, R = 30.0, 582.0  # invoice frame
ROW_H = 12.5
ROWS_PER_PAGE = 25
TABLE_TOP = 512.0
HEAD_H = 20.0
TOTAL_H = 18.0
ROWS_BOTTOM = TABLE_TOP - HEAD_H - ROWS_PER_PAGE * ROW_H
TABLE_BOTTOM = ROWS_BOTTOM - TOTAL_H
# (header, left x, right x, align)
COLUMNS = (
    ("Qty", 30.0, 68.0, "center"),
    ("Units", 68.0, 136.0, "center"),
    ("Description", 136.0, 338.0, "left"),
    ("LBS", 338.0, 394.0, "right"),
    ("KG", 394.0, 450.0, "right"),
    ("Unit Price", 450.0, 516.0, "right"),
    ("Amount", 516.0, 582.0, "right"),
)
HEADER_GREY, BAND_GREY, TOTAL_GREY = 0.86, 0.9, 0.93


def _cell_x(col: int, align: str) -> float:
    _, x0, x1, _ = COLUMNS[col]
    return x0 + 6 if align == "left" else x1 - 5 if align == "right" else (x0 + x1) / 2


def _header(pg: _Page, s: dict[str, Any], page: int, pages: int) -> None:
    co = s.get("company") or {}
    cust = s.get("customer") or {}
    pg.put(R, 774, f"Page: {page} of {pages}", 8.5, "F2", "right")

    # Logo + company block (left)
    _, lw, lh = logo()
    w = 222.0
    h = w * lh / lw
    pg.image("Im1", L, 772 - h, w, h)
    y = 772 - h - 14
    for ln in _lines(co.get("address"))[:2]:
        pg.put(L + 2, y, ln, 9.5)
        y -= 12
    contact = []
    if co.get("phone"):
        contact.append(("Tel:", co["phone"]))
    if co.get("fax"):
        contact.append(("Fax:", co["fax"]))
    x = L + 2
    for label, value in contact:
        pg.put(x, y, label, 9.5, "F2")
        x += text_width(label, 9.5, "F2") + 4
        pg.put(x, y, value, 9.5)
        x += text_width(value, 9.5) + 16
    if contact:
        y -= 12
    if co.get("email"):
        pg.put(L + 2, y, "Email:", 9.5, "F2")
        pg.put(L + 2 + text_width("Email:", 9.5, "F2") + 4, y, co["email"], 9.5)

    # Title banner + number box (right)
    bx0, bx1 = 352.0, R
    pg.polygon([(bx0, 740), (bx1 - 10, 740), (bx1, 766), (bx0 + 10, 766)], 0)
    pg.put((bx0 + bx1) / 2, 746.5, "SALES INVOICE", 18, "F4", "center", white=True)
    rows = (
        ("Invoice No.", s.get("invoice_no", "")),
        ("Date", _date(s.get("issued_date", ""))),
        ("Bus #", co.get("business_no") or DASH),
        ("Vendor #", cust.get("vendor_no") or DASH),
    )
    top, rh = 732.0, 16.5
    pg.box(bx0, top - rh * len(rows), bx1 - bx0, rh * len(rows), r=4)
    for i, (label, value) in enumerate(rows):
        yy = top - rh * (i + 1)
        if i:
            pg.rule(bx0, yy + rh, bx1, yy + rh, 0.4)
        pg.put(bx0 + 10, yy + 5, label, 9.5, "F2")
        pg.put(bx0 + 88, yy + 5, ":", 9.5, "F2")
        pg.put(bx0 + 98, yy + 5, _fit(value, bx1 - bx0 - 104, 9.5), 9.5)

    # Bill To / Ship To
    bill_addr = _lines(cust.get("bill_to_address"))
    ship_addr = _lines(cust.get("ship_to_address")) or bill_addr
    name = cust.get("name", "")
    boxes = (
        ("Bill To", L, name, bill_addr, True),
        ("Ship To", 312.0, cust.get("ship_to_name") or name, ship_addr, False),
    )
    btop, bh, bw = 646.0, 88.0, 270.0
    for title, x0, who, addr, tel in boxes:
        pg.box(x0, btop - bh, bw, bh, r=5, fill=BAND_GREY, stroke=None)
        pg.box(x0 + 0.3, btop - bh + 0.3, bw - 0.6, bh - 18.3, fill=1, stroke=None)
        pg.box(x0, btop - bh, bw, bh, r=5)
        pg.rule(x0, btop - 18, x0 + bw, btop - 18, 0.5)
        pg.put(x0 + 10, btop - 13, title, 10.5, "F2")
        yy = btop - 32
        for t in [who, *addr[:3]]:
            pg.put(x0 + 10, yy, _fit(t, bw - 20, 9.5), 9.5)
            yy -= 12
        if tel:
            ty = btop - bh + 8
            pg.put(x0 + 10, ty, "Tel:", 9.5, "F2")
            pg.put(x0 + 34, ty, cust.get("phone") or DASH, 9.5)
            pg.put(x0 + 150, ty, "Fax:", 9.5, "F2")
            pg.put(x0 + 174, ty, cust.get("fax") or DASH, 9.5)

    # Salesperson / Customer PO No. / Terms of Payment
    stop, sh = 550.0, 30.0
    cells = (
        ("Salesperson", s.get("salesperson") or DASH, L, 212.0),
        ("Customer PO No.", s.get("customer_po") or DASH, 212.0, 400.0),
        ("Terms of Payment", _terms(cust.get("payment_terms_days")), 400.0, R),
    )
    pg.box(L, stop - sh, R - L, sh, r=4)
    for i, (label, value, x0, x1) in enumerate(cells):
        if i:
            pg.rule(x0, stop - sh, x0, stop, 0.5)
        pg.put((x0 + x1) / 2, stop - 11, label, 9, "F2", "center")
        pg.put((x0 + x1) / 2, stop - 24, _fit(value, x1 - x0 - 10, 9), 9, "F1", "center")


def _table(pg: _Page, rows: list[dict[str, Any]], last: bool, totals: dict[str, Any]) -> None:
    head_bottom = TABLE_TOP - HEAD_H
    pg.box(L, TABLE_BOTTOM, R - L, TABLE_TOP - TABLE_BOTTOM, r=4, fill=1, stroke=None)
    pg.box(L, head_bottom, R - L, HEAD_H, fill=HEADER_GREY, stroke=None)
    pg.box(L, TABLE_BOTTOM, R - L, TOTAL_H, fill=TOTAL_GREY, stroke=None)
    for i, (title, _, _, align) in enumerate(COLUMNS):
        pg.put(_cell_x(i, align), head_bottom + 6.5, title, 9.5, "F2", align)
    # ruled rows (blank ones fill the page)
    for r in range(ROWS_PER_PAGE + 1):
        y = head_bottom - r * ROW_H
        pg.rule(L, y, R, y, 0.6 if r in (0, ROWS_PER_PAGE) else 0.3)
    for i, (_, x0, _, _) in enumerate(COLUMNS[1:], start=1):
        bottom = TABLE_BOTTOM if i in (1, 2) else ROWS_BOTTOM
        pg.rule(x0, bottom, x0, TABLE_TOP, 0.4)
    pg.box(L, TABLE_BOTTOM, R - L, TABLE_TOP - TABLE_BOTTOM, r=4, stroke=0.8)
    for n, v in enumerate(rows):
        y = head_bottom - (n + 1) * ROW_H + 3.6
        cells = (
            v["qty_text"],
            v["units"],
            v["description"],
            _two(v["lbs"]) if v["lbs"] is not None else "",
            _two(v["kg"]) if v["kg"] is not None else "",
            v["price"],
            v["amount"],
        )
        for i, value in enumerate(cells):
            _, x0, x1, align = COLUMNS[i]
            pg.put(_cell_x(i, align), y, _fit(value, x1 - x0 - 10, 9), 9, "F1", align)
    ty = TABLE_BOTTOM + 5.5
    if last:
        pg.put(_cell_x(0, "center"), ty, totals["qty"], 9.5, "F2", "center")
        pg.put(_cell_x(1, "center"), ty, "Total", 9.5, "F2", "center")
        label = "Total Weight:"
        x = COLUMNS[2][1] + 6
        pg.put(x, ty, label, 9.5, "F2")
        pg.put(x + text_width(label, 9.5, "F2") + 14, ty, totals["weight"], 9.5, "F2")
    else:
        pg.put(COLUMNS[2][1] + 6, ty, "Continued on the next page", 9, "F1")


def _footer(pg: _Page, s: dict[str, Any], last: bool, page: int, pages: int) -> None:
    top = TABLE_BOTTOM - 14
    if not last:
        pg.put(R, top - 6, f"Continued on page {page + 1} of {pages}", 9, "F2", "right")
        return
    # Notes (left)
    pg.put(L + 2, top - 6, "Notes:", 10.5, "F2")
    y = top - 19
    for t in NOTES:
        pg.put(L + 8, y, "•", 9)
        pg.put(L + 17, y, t, 9)
        y -= 11
    note = _clean(s.get("note") or "").strip()
    if note:
        pg.put(L + 8, y, "Note:", 9, "F2")
        pg.put(L + 36, y, _fit(note, 300, 9), 9)
    # Gross / Tax / Net (right)
    x0, x1 = 352.0, R
    rows = (
        ("Gross Amount", s.get("subtotal")),
        ("Tax Amount", s.get("tax_amount")),
    )
    y = top - 8
    for label, value in rows:
        pg.put(x0 + 10, y, label, 10.5, "F2")
        pg.put(x0 + 128, y, ":", 10.5, "F2")
        pg.put(x1 - 10, y, _money(value), 11, "F2", "right")
        y -= 17
    pg.box(x0, y - 7, x1 - x0, 21, r=3, fill=0, stroke=None)
    pg.put(x0 + 10, y, "Net Amount", 11, "F2", white=True)
    pg.put(x0 + 128, y, ":", 11, "F2", white=True)
    pg.put(x1 - 10, y, _money(s.get("total")), 11.5, "F2", "right", white=True)
    # One signature: the customer's receiver (D-12)
    sx0, sx1 = 352.0, R
    pg.rule(sx0, 54, sx1, 54, 0.7)
    pg.put((sx0 + sx1) / 2, 42, "Received By (Name & Signature)", 9.5, "F2", "center")


def render(snapshot: dict[str, Any]) -> bytes:
    rows = [row_values(ln) for ln in snapshot.get("lines", [])]
    total_qty = sum((v["qty"] for v in rows if v["counted"]), Decimal(0))
    total_lb = sum((v["lbs"] for v in rows if v["lbs"] is not None), Decimal(0))
    total_kg = sum((v["kg"] for v in rows if v["kg"] is not None), Decimal(0))
    totals = {"qty": _plain(total_qty), "weight": f"{_two(total_lb)} LBS / {_two(total_kg)} KG"}
    chunks = [rows[i : i + ROWS_PER_PAGE] for i in range(0, len(rows), ROWS_PER_PAGE)] or [[]]
    pages: list[_Page] = []
    for n, chunk in enumerate(chunks, start=1):
        last = n == len(chunks)
        pg = _Page()
        _header(pg, snapshot, n, len(chunks))
        _table(pg, chunk, last, totals)
        _footer(pg, snapshot, last, n, len(chunks))
        pages.append(pg)
    return _assemble(pages, snapshot.get("invoice_no", ""))


def _assemble(pages: list[_Page], title: str, producer: str = "") -> bytes:
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    def uses(token: str) -> bool:
        return any(token in op for pg in pages for op in pg.ops)

    add(b"")  # 1 catalog (filled below)
    add(b"")  # 2 pages
    f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )
    fonts = f"/F1 {f1} 0 R /F2 {f2} 0 R"
    if uses("/F3 "):
        f3 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Oblique >>")
        fonts += f" /F3 {f3} 0 R"
    if uses("/F4 "):
        f4 = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-BoldOblique "
            b"/Encoding /WinAnsiEncoding >>"
        )
        fonts += f" /F4 {f4} 0 R"
    xobjects = ""
    if uses("/Im1 Do"):
        data, w, h = logo()
        im = add(
            (
                f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /DCTDecode "
                f"/Length {len(data)} >>\nstream\n"
            ).encode()
            + data
            + b"\nendstream"
        )
        xobjects = f" /XObject << /Im1 {im} 0 R >>"
    kids: list[int] = []
    for pg in pages:
        data = pg.stream()
        c = add(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        kids.append(
            add(
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                    f"/Resources << /Font << {fonts} >>{xobjects} >> /Contents {c} 0 R >>"
                ).encode()
            )
        )
    producer = producer or f"Pro Trading {TEMPLATE_VERSION}"
    info = add(f"<< /Title ({_txt(title)}) /Producer ({_txt(producer)}) >>".encode())
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    ).encode()
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    # Deterministic /ID derived from content (no timestamps anywhere in the file).
    digest = hashlib.md5(bytes(out), usedforsecurity=False).hexdigest()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {info} 0 R "
        f"/ID [<{digest}> <{digest}>] >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
