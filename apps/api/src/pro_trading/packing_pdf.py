"""Packing sheet PDF ("PACKING LIST"): the warehouse copy of a picking attempt.

Layout: the office's paper PACKING LIST (owner, 2026-11-11): "TAMISHA TRADING INC." / "PACKING
LIST" with a Sales Order Number + Date box; CUSTOMER NAME, EMPLOYEE NAME and a MISSING GOODS box;
then CHK | QTY | UNITS | PRODUCT | Batch Number | REMARKS and a Total row. Black and white, ruled.
The QR code and the pick code stay on every page: scanning the QR opens the sheet in the app.

Every page repeats a header and the column titles. The file holds `copies` identical copies of the
sheet, each starting on a new page with its own "Page n of m" (a web page cannot set the print
dialog's copy count, so the document itself carries the copies).

Pure function of the sheet data, no timestamps: same data -> byte-identical PDF.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from . import qr
from .invoice_pdf import _assemble, _date, _fit, _Page, _plain, text_width

TEMPLATE_VERSION = "packing-list-1"
COPIES = 2
MAX_COPIES = 4
L, R = 36.0, 576.0
ROW_H = 16.0
HEAD_H = 20.0
BOTTOM = 48.0  # lowest row edge; the footer sits below it
FIRST_TOP = 556.0  # table top on page 1 (under the customer block)
NEXT_TOP = 690.0  # table top on later pages (compact header)
HEADER_GREY = 0.82
# (title, x0, x1, align)
COLUMNS = (
    ("CHK", L, 80.0, "center"),
    ("QTY", 80.0, 116.0, "right"),
    ("UNITS", 116.0, 196.0, "center"),
    ("PRODUCT", 196.0, 376.0, "left"),
    ("Batch Number", 376.0, 470.0, "left"),
    ("REMARKS", 470.0, R, "left"),
)
COUNTED = ("BOX", "PIECE")  # whole units: they add up in the Total row


def units_label(unit_code: str, pack_lb: Decimal | None) -> str:
    """UNITS column: boxes as CASE-40 (lb per case, the invoice's rule), pieces as PIECE-50
    when the piece size has a weight, weights as LB / KG."""
    if unit_code == "BOX":
        return f"CASE-{_plain(pack_lb)}" if pack_lb else "CASE"
    if unit_code == "PIECE":
        return f"PIECE-{_plain(pack_lb)}" if pack_lb else "PIECE"
    return unit_code


def _dec(v: Any) -> Decimal:
    return Decimal(str(v)) if v not in (None, "") else Decimal(0)


def rows_per_page(first: bool) -> int:
    top = (FIRST_TOP if first else NEXT_TOP) - HEAD_H
    return int((top - BOTTOM) // ROW_H)


def paginate(n_lines: int) -> list[tuple[int, int]]:
    """(start, end) line ranges per page; the Total row takes one slot on the last page."""
    pages: list[tuple[int, int]] = []
    start, slots = 0, n_lines + 1
    while slots > 0:
        cap = rows_per_page(not pages)
        take = min(cap, slots)
        end = min(start + take, n_lines)
        pages.append((start, end))
        start, slots = end, slots - take
    return pages


def _qr(pg: _Page, code: str, x: float, y: float, size: float) -> None:
    """QR code with its quiet zone inside the `size` square at (x, y) (bottom-left)."""
    m = qr.encode(code)
    n = len(m)
    cell = size / (n + 8)
    x0, top = x + 4 * cell, y + size - 4 * cell
    rects = [
        f"{x0 + c * cell:.3f} {top - (r + 1) * cell:.3f} {cell:.3f} {cell:.3f} re"
        for r in range(n)
        for c in range(n)
        if m[r][c]
    ]
    pg.ops.append("q 0 g " + " ".join(rects) + " f Q")


def _so_box(pg: _Page, s: dict[str, Any], top: float, label_h: float, value_h: float,
            size: float) -> None:  # fmt: skip
    x0, mid, x1 = 312.0, 432.0, 516.0
    bottom = top - label_h - value_h
    pg.box(x0, bottom, x1 - x0, label_h + value_h, stroke=0.8)
    pg.rule(x0, top - label_h, x1, top - label_h, 0.6)
    pg.rule(mid, bottom, mid, top, 0.6)
    ly = top - label_h + (label_h - size * 0.75) / 2 + 1
    pg.put((x0 + mid) / 2, ly, "Sales Order Number", size * 0.75, "F2", "center")
    pg.put((mid + x1) / 2, ly, "Date", size * 0.75, "F2", "center")
    vy = bottom + (value_h - size) / 2 + 2
    pg.put((x0 + mid) / 2, vy, _fit(s.get("order_no", ""), mid - x0 - 8, size, "F2"), size,
           "F2", "center")  # fmt: skip
    pg.put((mid + x1) / 2, vy, _date(s.get("order_date") or ""), size, "F2", "center")


def _first_header(pg: _Page, s: dict[str, Any]) -> None:
    name = "TAMISHA TRADING INC."
    pg.put(L, 728, name, 22)
    pg.put(L + text_width(name, 22) / 2, 694, "PACKING LIST", 18, "F2", "center")
    _so_box(pg, s, 756, 20, 30, 12)
    _qr(pg, s.get("pick_code", ""), 520, 700, 56)
    pg.put(548, 692, _fit(s.get("pick_code", ""), 74, 6.5, "F2"), 6.5, "F2", "center")

    top, bottom = 680.0, 568.0
    pg.box(L, bottom, R - L, top - bottom, stroke=0.8)
    for y in (656.0, 632.0):
        pg.rule(L, y, R, y, 0.5)
    fields = (
        ("CUSTOMER NAME :", s.get("customer_name", ""), 663.0),
        ("EMPLOYEE NAME :", s.get("picked_by_name") or "", 639.0),
        ("MISSING GOODS :", "", 615.0),
    )
    for label, value, y in fields:
        pg.put(L + 8, y, label, 11, "F2")
        if value:
            pg.put(L + 150, y, _fit(value, R - L - 160, 12), 12)


def _next_header(pg: _Page, s: dict[str, Any]) -> None:
    pg.put(L, 738, "TAMISHA TRADING INC.", 14)
    pg.put(L, 720, "PACKING LIST (continued)", 12, "F2")
    _so_box(pg, s, 756, 14, 22, 10)
    _qr(pg, s.get("pick_code", ""), 532, 712, 44)
    pg.put(554, 704, _fit(s.get("pick_code", ""), 64, 6, "F2"), 6, "F2", "center")
    label = "CUSTOMER NAME :"
    pg.put(L, 702, label, 9.5, "F2")
    x = L + text_width(label, 9.5, "F2") + 8
    pg.put(x, 702, _fit(s.get("customer_name", ""), 312 - x - 6, 10), 10)


def _cell(pg: _Page, col: int, y: float, text: str, size: float, font: str = "F1") -> None:
    _, x0, x1, align = COLUMNS[col]
    t = _fit(text, x1 - x0 - 8, size, font)
    x = x0 + 4 if align == "left" else x1 - 5 if align == "right" else (x0 + x1) / 2
    pg.put(x, y, t, size, font, align)


def _table(pg: _Page, top: float, rows: list[dict[str, Any]], total: dict[str, Any] | None) -> None:
    n = len(rows) + (1 if total else 0)
    bottom = top - HEAD_H - n * ROW_H
    pg.box(L, top - HEAD_H, R - L, HEAD_H, fill=HEADER_GREY, stroke=None)
    for i, (title, x0, x1, _) in enumerate(COLUMNS):
        pg.put((x0 + x1) / 2 if i < 3 else x0 + 4, top - 14, title, 10, "F2",
               "center" if i < 3 else "left")  # fmt: skip
    for i in range(n + 1):
        y = top - HEAD_H - i * ROW_H
        pg.rule(L, y, R, y, 0.6 if i == 0 else 0.4)
    for _, x0, _, _ in COLUMNS[1:]:
        pg.rule(x0, bottom, x0, top, 0.5)
    pg.box(L, bottom, R - L, top - bottom, stroke=0.9)
    y = top - HEAD_H - ROW_H + 4.6
    for r in rows:
        _cell(pg, 1, y, r["qty"], 10, "F2")
        _cell(pg, 2, y, r["units"], 9.5)
        _cell(pg, 3, y, r["product"], 10, "F2")
        _cell(pg, 4, y, r["batch"], 9)
        _cell(pg, 5, y, r["remarks"], 9)
        y -= ROW_H
    if total:
        _cell(pg, 1, y, total["qty"], 10, "F2")
        _cell(pg, 3, y, "Total", 10, "F2")
        _cell(pg, 5, y, total["extra"], 9)


def sheet_rows(s: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, count = [], Decimal(0)
    weights: dict[str, Decimal] = {}
    for ln in s.get("lines", []):
        qty, unit = _dec(ln.get("requested_qty")), ln.get("unit_code", "")
        if unit in COUNTED:
            count += qty
        else:
            weights[unit] = weights.get(unit, Decimal(0)) + qty
        rows.append(
            {
                "qty": _plain(qty),
                "units": ln.get("units") or units_label(unit, None),
                "product": ln.get("product_name", ""),
                "batch": ln.get("lot_code") or "",
                "remarks": ln.get("remarks") or "",
            }
        )
    extra = " + ".join(f"{_plain(v)} {u}" for u, v in sorted(weights.items()))
    return rows, {"qty": _plain(count), "extra": f"+ {extra} by weight" if extra else ""}


def render(s: dict[str, Any], copies: int = COPIES) -> bytes:
    """`s` = the pick as orders.pick_out returns it."""
    if not 1 <= copies <= MAX_COPIES:
        raise ValueError("copies out of range")
    rows, total = sheet_rows(s)
    ranges = paginate(len(rows))
    one: list[_Page] = []
    for i, (a, b) in enumerate(ranges):
        pg = _Page()
        last = i == len(ranges) - 1
        if i == 0:
            _first_header(pg, s)
        else:
            _next_header(pg, s)
        _table(pg, FIRST_TOP if i == 0 else NEXT_TOP, rows[a:b], total if last else None)
        pg.put(L, 26, f"Packing sheet {s.get('pick_code', '')}", 8)
        pg.put(R, 26, f"Page {i + 1} of {len(ranges)}", 8, "F2", "right")
        one.append(pg)
    title = f"Packing sheet {s.get('pick_code', '')}"
    return _assemble(one * copies, title, producer=f"Pro Trading {TEMPLATE_VERSION}")
