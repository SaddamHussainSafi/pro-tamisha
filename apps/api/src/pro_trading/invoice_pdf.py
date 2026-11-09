"""Deterministic invoice PDF renderer (no third-party dependency).

The real customer template is not supplied yet (OPEN_DECISIONS D-12), so this is a clearly
labelled *provisional* layout — it never claims to match the official form. The renderer is a
pure function of the immutable invoice snapshot: same snapshot -> byte-identical PDF. The bytes
are rendered once at issue time, hashed and stored; reprints return the stored bytes.
"""

import hashlib
from typing import Any

TEMPLATE_VERSION = "provisional-1"
PAGE_W, PAGE_H = 612, 792  # US Letter, points (paper size pending D-12)
MARGIN = 48
ROWS_PER_PAGE = 30


def _txt(s: Any) -> str:
    """PDF literal string in WinAnsi; unsupported characters become '?'."""
    raw = str(s).encode("cp1252", errors="replace").decode("cp1252")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _clip(v: Any, n: int) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "~"


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

    def stream(self) -> bytes:
        return ("0.5 w\n" + "\n".join(self.ops)).encode("cp1252")  # WinAnsiEncoding


COLS = [  # (header, x, right-aligned)
    ("#", MARGIN, False),
    ("Item", MARGIN + 22, False),
    ("Cases", 330, True),
    ("Lb/case", 380, True),
    ("Qty", 432, True),
    ("Unit", 440, False),
    ("Rate", 510, True),
    ("Amount", PAGE_W - MARGIN, True),
]


def _header(pg: _Page, s: dict[str, Any], page: int, pages: int) -> float:
    y = PAGE_H - MARGIN
    pg.text(MARGIN, y, s["company"]["legal_name"], 14, True)
    pg.right(PAGE_W - MARGIN, y, "INVOICE", 14, True)
    y -= 16
    pg.text(MARGIN, y, "PROVISIONAL LAYOUT - official template pending (D-12)", 8)
    pg.right(PAGE_W - MARGIN, y, f"No. {s['invoice_no']}", 10, True)
    y -= 22
    cust = s["customer"]
    pg.text(MARGIN, y, "Bill to", 8, True)
    pg.text(330, y, "Issued", 8, True)
    pg.right(PAGE_W - MARGIN, y, s["issued_date"], 9)
    y -= 12
    pg.text(MARGIN, y, _clip(cust["name"], 50), 10)
    pg.text(330, y, "Due", 8, True)
    pg.right(PAGE_W - MARGIN, y, s["due_date"], 9)
    y -= 12
    if cust.get("external_ref"):
        pg.text(MARGIN, y, f"Account {cust['external_ref']}", 9)
    pg.text(330, y, "Order / pick", 8, True)
    pg.right(PAGE_W - MARGIN, y, f"{s['order']['order_no']} / {s['pick_code']}", 9)
    y -= 12
    pg.text(330, y, "Terms", 8, True)
    pg.right(PAGE_W - MARGIN, y, f"Net {cust['payment_terms_days']} days", 9)
    y -= 22
    for name, x, right in COLS:
        (pg.right if right else pg.text)(x, y, name, 8, True)
    y -= 5
    pg.line(MARGIN, y, PAGE_W - MARGIN, y)
    pg.text(MARGIN, MARGIN - 20, f"Currency {s['currency']}. Tax: {s['tax_note']}", 7)
    pg.right(PAGE_W - MARGIN, MARGIN - 20, f"Page {page} of {pages}", 7)
    return y - 13


def render(snapshot: dict[str, Any]) -> bytes:
    lines: list[dict[str, Any]] = snapshot["lines"]
    chunks = [lines[i : i + ROWS_PER_PAGE] for i in range(0, len(lines), ROWS_PER_PAGE)] or [[]]
    pages: list[_Page] = []
    for n, chunk in enumerate(chunks, start=1):
        pg = _Page()
        y = _header(pg, snapshot, n, len(chunks))
        for ln in chunk:
            pg.text(MARGIN, y, ln["line_no"])
            pg.text(MARGIN + 22, y, _clip(f"{ln['sku']} {ln['description']}", 48))
            if ln.get("cases"):
                pg.right(330, y, ln["cases"])
            if ln.get("case_weight_lb"):
                pg.right(380, y, ln["case_weight_lb"])
            pg.right(432, y, ln["billed_qty"])
            pg.text(440, y, ln["unit_code"])
            pg.right(510, y, ln["unit_rate"])
            pg.right(PAGE_W - MARGIN, y, ln["line_amount"])
            y -= 14
        if n == len(chunks):
            y -= 4
            pg.line(400, y + 10, PAGE_W - MARGIN, y + 10)
            for label, key in (("Subtotal", "subtotal"), ("Tax", "tax_amount"), ("Total", "total")):
                bold = key == "total"
                pg.text(400, y, label, 10 if bold else 9, bold)
                pg.right(PAGE_W - MARGIN, y, f"{snapshot['currency']} {snapshot[key]}", 10, bold)
                y -= 14
        pages.append(pg)
    return _assemble(pages, snapshot["invoice_no"])


def _assemble(pages: list[_Page], title: str) -> bytes:
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    add(b"")  # 1 catalog (filled below)
    add(b"")  # 2 pages
    f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )
    fonts = f"/F1 {f1} 0 R /F2 {f2} 0 R"
    if any("/F3 " in op for pg in pages for op in pg.ops):
        f3 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Oblique >>")
        fonts += f" /F3 {f3} 0 R"
    kids: list[int] = []
    for pg in pages:
        data = pg.stream()
        c = add(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        kids.append(
            add(
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                    f"/Resources << /Font << {fonts} >> >> /Contents {c} 0 R >>"
                ).encode()
            )
        )
    info = add(f"<< /Title ({_txt(title)}) /Producer (Pro Trading {TEMPLATE_VERSION}) >>".encode())
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
