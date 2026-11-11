"""Tamisha Trading's own printed order sheet: the paper form the office uses for orders and picking.

Layout (from the office scans of 2026-11-09): "TAMISHA TRADING INC  CUSTOMER NAME: ____", then
three tables side by side, each with columns ITEM | Q | PKG | WEIGHT | NOTES under shaded section
bars (VEGETABLES, PEPPERS / ROOTS, HERBS, OTHERS / FRUITS). Products are printed by name only (the
catalog keeps the sheet's spellings as aliases, see scripts/load_catalog.py); the quantity is
handwritten in Q. Sheets are often scanned sideways.

Reading rule: a printed product name plus a mark in the Q box on its row is one proposed line for
the loose item. A number in the PKG box orders the product's packaged twin (owner, 2026-11-11:
"pkg scans must use package and q column for regular loose items"), as its own line; a row with
both gives both. WEIGHT/NOTES are packing marks. A box with a mark that could not be read still
gives the line, without a quantity, so the reviewer types it from the original.
Like every scan, this only proposes lines for review; nothing here touches stock or orders.
"""

from __future__ import annotations

import io
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import packaged
from .models import Customer, Product

if TYPE_CHECKING:
    from .ocr import Catalog, Proposal, Token

TEMPLATE = "TT-PAPER-1"
SECTIONS = frozenset({"VEGETABLES", "PEPPERS", "ROOTS", "HERBS", "OTHERS", "FRUITS"})
ROW_TOL = Decimal("0.009")  # rows are ~0.02 of the page height apart
LINE_TOL = Decimal("0.006")  # words of one printed name
WORD_GAP = Decimal("0.025")
D0 = Decimal(0)


def _key(s: str) -> str:
    """Spacing and punctuation don't matter: OCR often drops spaces ("DASHEENLEAVES")."""
    return re.sub(r"[^0-9a-z]", "", s.casefold())


def _up(s: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", s.upper())


def _xc(t: Token) -> Decimal:
    return (t.x0 + t.x1) / 2


def detect(tokens: list[Token]) -> bool:
    words = {_up(t.text) for t in tokens if not t.handwritten}
    return len(words & SECTIONS) >= 2 and any(w.startswith("PKG") for w in words)


# ------------------------------------------------------------------ product names


def _within(a: str, b: str, k: int) -> bool:
    """Levenshtein distance of a and b is at most k (banded)."""
    if abs(len(a) - len(b)) > k:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [k + 1] * len(b)
        lo, hi = max(1, i - k), min(len(b), i + k)
        for j in range(lo, hi + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != b[j - 1]))
        if min(cur[max(0, lo - 1) : hi + 1]) > k:
            return False
        prev = cur
    return prev[len(b)] <= k


class Names:
    """Catalog lookup by printed name or sheet alias, tolerant of spacing and one or two OCR slips
    on long names (fuzzy matches are marked so they are reviewed)."""

    def __init__(self, cat: Catalog) -> None:
        self.exact: dict[str, Product] = {}
        for p in cat.by_id.values():
            if not packaged.is_packaged(p.sku):  # printed names are loose items; PKG finds twins
                self.exact.setdefault(_key(p.name), p)
        for alias, p in cat.by_alias.items():
            self.exact.setdefault(_key(alias), p)

    def find(self, text: str) -> tuple[Product | None, bool]:
        k = _key(text)
        if not k:
            return None, False
        if k in self.exact:
            return self.exact[k], False
        if len(k) < 5 or k.isdigit():
            return None, False
        allow = 1 if len(k) < 10 else 2
        hits = {p.id: p for c, p in self.exact.items() if _within(k, c, allow)}
        return (next(iter(hits.values())), True) if len(hits) == 1 else (None, False)


def phrases(tokens: list[Token]) -> list[list[Token]]:
    """Printed words that sit next to each other on one line (a product name, a header...)."""
    printed = sorted(
        (t for t in tokens if not t.handwritten and re.search(r"[A-Za-z]", t.text)),
        key=lambda t: (t.page, t.x0),
    )
    out: list[list[Token]] = []
    for t in printed:
        for ph in out:
            last = ph[-1]
            gap = t.x0 - last.x1
            if (
                last.page == t.page
                and abs(last.yc - t.yc) <= LINE_TOL
                and -LINE_TOL <= gap <= WORD_GAP
            ):
                ph.append(t)
                break
        else:
            out.append([t])
    return out


def match_phrase(names: Names, ph: list[Token]) -> tuple[Product, list[Token], bool] | None:
    """Longest run of words in the phrase that names a product (exact first, then fuzzy)."""
    n = len(ph)
    spans = sorted(
        ((i, j) for i in range(n) for j in range(i + 1, min(n, i + 6) + 1)),
        key=lambda s: (s[0] - s[1], s[0]),
    )
    for fuzzy_ok in (False, True):
        for i, j in spans:
            text = " ".join(t.text for t in ph[i:j])
            prod, fuzzy = names.find(text)
            if prod is not None and (fuzzy_ok or not fuzzy):
                return prod, ph[i:j], fuzzy
    return None


# ------------------------------------------------------------------ the Q column


@dataclass
class Column:
    page: int
    y: Decimal  # header row
    q0: Decimal  # Q box, left/right edges
    q1: Decimal
    k0: Decimal = D0  # PKG box, left/right edges (0 = not found)
    k1: Decimal = D0


def columns(tokens: list[Token]) -> list[Column]:
    """Each table's Q box from its header row: the "Q" before "PKG" (or one column left of PKG)."""
    printed = [t for t in tokens if not t.handwritten]
    out: list[Column] = []
    for p in printed:
        if not _up(p.text).startswith("PKG"):
            continue
        row = [
            t
            for t in printed
            if t is not p and t.page == p.page and abs(t.yc - p.yc) <= 2 * LINE_TOL
        ]
        qs = [
            t for t in row
            if _up(t.text) in {"Q", "O", "0", "QTY"} and t.x1 <= p.x0 + Decimal("0.003")
            and p.x0 - t.x1 <= Decimal("0.03")
        ]  # fmt: skip
        nxt = [t.x0 for t in row if t.x0 > p.x1 and t.x0 - p.x0 <= Decimal("0.06")]
        width = (min(nxt) - p.x0) if nxt else Decimal("0.032")
        if qs:
            q = max(qs, key=lambda t: t.x0)
            q0 = q.x0 - (p.x0 - q.x0) / 2
        else:
            q0 = p.x0 - width
        k1 = (min(nxt) - Decimal("0.002")) if nxt else p.x0 + width
        out.append(Column(p.page, p.yc, q0, p.x0 - Decimal("0.002"), p.x0 - Decimal("0.002"), k1))
    return out


def _column_for(cols: list[Column], page: int, x0: Decimal, yc: Decimal) -> Column | None:
    right = [c for c in cols if c.page == page and c.q0 >= x0 + Decimal("0.02") and c.y < yc]
    return min(right, key=lambda c: c.q0, default=None)


def _in_q(c: Column, t: Token) -> bool:
    return t.page == c.page and c.q0 - Decimal("0.008") <= _xc(t) <= c.q1


def _in_pkg(c: Column, t: Token) -> bool:
    return t.page == c.page and c.k1 > c.k0 and c.k0 < _xc(t) <= c.k1


# ------------------------------------------------------------------ reading


def read(
    db: Session,
    company_id: uuid.UUID,
    cat: Catalog,
    tokens: list[Token],
    threshold: Decimal,
    make: type[Proposal],
    status: Callable[..., str],
    parse_qty: Callable[[str], Decimal | None],
    bbox: Callable[[list[Token]], dict[str, Any]],
) -> tuple[list[Proposal], list[str], uuid.UUID | None]:
    names = Names(cat)
    cols = columns(tokens)
    warnings: list[str] = [] if cols else ["NO_COLUMNS"]
    hand = [t for t in tokens if t.handwritten]
    used: set[int] = set()
    found: list[tuple[int, Decimal, Decimal, Proposal]] = []  # page, table, row, line

    for ph in phrases(tokens):
        m = match_phrase(names, ph)
        if m is None:
            continue
        prod, words, fuzzy = m
        page = words[0].page
        yc = sum((t.yc for t in words), D0) / len(words)
        x0, x1 = min(t.x0 for t in words), max(t.x1 for t in words)
        col = _column_for(cols, page, x0, yc)
        twin = cat.packaged(prod)
        pkgs: list[Token] = []
        if col is not None:
            marks = [h for h in hand if _in_q(col, h) and abs(h.yc - yc) <= ROW_TOL]
            if twin is not None:
                pkgs = [h for h in hand if _in_pkg(col, h) and abs(h.yc - yc) <= ROW_TOL]
        else:  # no header found: the first mark right of the name, never trusted
            lo, hi = x1 - Decimal("0.01"), x1 + Decimal("0.12")
            near = [
                h for h in hand
                if h.page == page and abs(h.yc - yc) <= ROW_TOL and lo <= h.x0 <= hi
            ]  # fmt: skip
            marks = sorted(near, key=lambda h: h.x0)[:1]
        marks = [h for h in marks if id(h) not in used]
        pkgs = [h for h in pkgs if id(h) not in used and id(h) not in {id(m) for m in marks}]
        if not re.search(r"\d", "".join(h.text for h in pkgs)):
            pkgs = []  # a tick or a unit word in PKG is a packing mark, not a packaged count
        if not marks and not pkgs:
            continue  # empty Q and PKG boxes: not ordered
        # Q orders the loose item, PKG the packaged twin: one line each
        for item, box in ((prod, marks), (twin, pkgs)):
            if not box or item is None:
                continue
            used.update(id(h) for h in box)
            box.sort(key=lambda h: h.x0)
            raw = "".join(h.text for h in box).strip()
            qty = parse_qty(raw)
            conf = min(h.confidence for h in box)
            if fuzzy or col is None:
                conf = min(conf, threshold - Decimal("0.001"))
            p = make(
                kind="LINE",
                label=f"{item.sku} {item.name}",
                raw_text=(raw if item is prod else f"PKG: {raw}")[:500],
                product_id=item.id,
                unit_code=item.stock_unit,
                qty=qty,
                confidence=conf,
                status=status(cat, item, item.stock_unit, qty, conf, threshold),
                bbox=bbox([*words, *box]),
            )
            found.append(
                (page, col.q0 if col else x0, yc + (D0 if item is prod else ROW_TOL / 10), p)
            )

    # A mark in a Q box whose product name could not be matched: show it, with the printed text
    printed = [t for t in tokens if not t.handwritten]
    for c in cols:
        for h in hand:
            if id(h) in used or not _in_q(c, h) or h.yc <= c.y + ROW_TOL:
                continue
            row = sorted(
                (
                    t for t in printed
                    if t.page == h.page and abs(t.yc - h.yc) <= ROW_TOL
                    and c.q0 - Decimal("0.2") <= t.x0 and t.x1 <= c.q0 + Decimal("0.004")
                ),
                key=lambda t: t.x0,
            )  # fmt: skip
            label = " ".join(t.text for t in row if re.search(r"[A-Za-z]", t.text))
            if not re.search(r"[A-Za-z]{3,}", label) or _up(label) in SECTIONS:
                continue  # shaded bar, blank row or specks
            used.add(id(h))
            qty = parse_qty(h.text)
            p = make(
                kind="EXTRA",
                label=label[:200],
                raw_text=f"{label} {h.text}"[:500],
                product_id=None,
                unit_code=None,
                qty=qty,
                confidence=h.confidence,
                status=status(cat, None, None, qty, h.confidence, threshold),
                bbox=bbox([*row, h]),
            )
            found.append((h.page, c.q0, h.yc, p))
    # sheet order: page, then table left to right, then top to bottom
    found.sort(key=lambda f: (f[0], f[1], f[2]))
    out = [f[3] for f in found]

    customer_id, w = _customer(db, company_id, tokens)
    return out, warnings + w, customer_id


def _customer(
    db: Session, company_id: uuid.UUID, tokens: list[Token]
) -> tuple[uuid.UUID | None, list[str]]:
    """The name written after "CUSTOMER NAME:", matched exactly (case-insensitive) to a customer's
    name or code. Pixel OCR can't read it; then the reviewer picks the customer."""
    printed = [t for t in tokens if not t.handwritten]
    label = next(
        (
            t for t in printed
            if _up(t.text).startswith("NAME")
            and any(_up(u.text) == "CUSTOMER" and abs(u.yc - t.yc) <= LINE_TOL for u in printed)
        ),
        None,
    )  # fmt: skip
    if label is None:
        return None, []
    written = sorted(
        (h for h in tokens if h.handwritten and h.page == label.page and h.x0 > label.x1
         and abs(h.yc - label.yc) <= Decimal("0.02")),
        key=lambda h: h.x0,
    )  # fmt: skip
    text = " ".join(h.text for h in written).strip()
    if not text or "?" in text:
        return None, []
    cust = db.scalar(
        select(Customer).where(
            Customer.company_id == company_id,
            or_(
                func.lower(Customer.name) == text.casefold(),
                func.lower(Customer.external_ref) == text.casefold(),
            ),
        )
    )
    return (cust.id, []) if cust else (None, ["CUSTOMER_NOT_FOUND"])


# ------------------------------------------------------------------ synthetic sheet (tests only)


def synthetic_scan(
    blocks: list[list[tuple[str, list[str]]]],
    fills: dict[str, str],
    customer: str = "",
    rotate: int = 90,
    dpi: int = 200,
) -> bytes:
    """A SYNTHETIC scan of the paper form for tests and demos (never a real customer's sheet).

    `blocks` = the three tables, each a list of (section title, printed product names). `fills`
    maps a printed name to what is "handwritten" in its Q box. The page is drawn landscape,
    turned `rotate` degrees clockwise (the office scanner feeds it sideways) and saved as an
    image-only PDF, so only pixel OCR can read it."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = int(11 * dpi), int(8.5 * dpi)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    s = dpi / 200
    font = ImageFont.load_default(size=round(20 * s))
    bold = ImageFont.load_default(size=round(22 * s))
    pen = ImageFont.load_default(size=round(28 * s))
    lw = max(2, round(2 * s))
    d.text((0.05 * w, 0.025 * h), "TAMISHA TRADING INC", font=bold, fill=0)
    d.text((0.26 * w, 0.025 * h), "CUSTOMER NAME:", font=bold, fill=0)
    d.line((0.37 * w, 0.05 * h, 0.62 * w, 0.05 * h), fill=0, width=lw)
    if customer:
        d.text((0.40 * w, 0.02 * h), customer, font=pen, fill=40)
    rows = max(sum(1 + len(n) for _, n in b) + 1 for b in blocks)
    rh = 0.85 * h / rows
    widths = (0.54, 0.11, 0.11, 0.12, 0.12)
    heads = ("ITEM", "Q", "PKG", "WEIGHT", "NOTES")
    for bi, block in enumerate(blocks):
        left, bw = (0.05 + 0.3 * bi) * w, 0.295 * w
        xs = [left]
        for f in widths:
            xs.append(xs[-1] + f * bw)
        y = 0.07 * h
        lines: list[tuple[str, str | None]] = []
        for si, (title, prods) in enumerate(block):
            lines.append(("bar", title))
            if si == 0:
                lines.append(("head", None))
            lines.extend(("item", p) for p in prods)
        for kind, text in lines:
            if kind == "bar":
                d.rectangle((xs[0], y, xs[-1], y + rh), fill=205, outline=0, width=lw)
                tw = d.textlength(text or "", font=bold)
                d.text((xs[0] + (bw - tw) / 2, y + rh * 0.15), text or "", font=bold, fill=0)
            else:
                for i in range(5):
                    d.rectangle((xs[i], y, xs[i + 1], y + rh), outline=0, width=lw)
                if kind == "head":
                    for i, t in enumerate(heads):
                        tw = d.textlength(t, font=font)
                        d.text(
                            (xs[i] + (xs[i + 1] - xs[i] - tw) / 2, y + rh * 0.15),
                            t,
                            font=font,
                            fill=0,
                        )
                else:
                    d.text((xs[0] + 6 * s, y + rh * 0.15), (text or "").upper(), font=font, fill=0)
                    if text in fills:
                        d.text((xs[1] + 8 * s, y - rh * 0.05), fills[text], font=pen, fill=40)
            y += rh
    if rotate:
        img = img.rotate(-rotate, expand=True, fillcolor=255)
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=float(dpi))
    return buf.getvalue()
