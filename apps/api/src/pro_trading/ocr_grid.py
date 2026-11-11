"""Pixel helpers for reading printed forms with Tesseract (Pillow only).

Table rulings confuse Tesseract: words get glued to "|" and "[", and anything written in a narrow
box is lost. `find_grid` finds the long straight ink runs (the printed rulings), paints them white
and returns the boxes they form. `marked_cells` lists the narrow boxes that hold ink but no printed
word, i.e. what someone wrote or ticked. `read_cells` reads those boxes in one Tesseract call
(digits only). Tesseract has no handwriting model, so callers must never trust these reads alone.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

INK = 160  # gray level (0-255) below which a pixel counts as ink
MAX_CELLS = 300  # cells read per page; more than this is not a form we know


@dataclass
class Grid:
    clean: Image.Image  # grayscale page with the rulings painted white
    ink: Image.Image  # 255 where there is ink (rulings removed), else 0
    cells: list[tuple[int, int, int, int]]  # x0, y0, x1, y1 in pixels


def _runs(img: Image.Image, minlen: int) -> list[tuple[int, int, int]]:
    """(row, start, end) of every horizontal run of at least `minlen` ink pixels."""
    w, h = img.size
    data = img.tobytes()
    pat = re.compile(rb"\xff{%d,}" % minlen)
    out: list[tuple[int, int, int]] = []
    for y in range(h):
        base = y * w
        out.extend(
            (y, m.start() - base, m.end() - base) for m in pat.finditer(data, base, base + w)
        )
    return out


def _clusters(values: list[int], gap: int) -> list[list[int]]:
    out: list[list[int]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= gap:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def find_grid(page: Image.Image) -> Grid:
    """Remove the rulings of a grayscale page and find the boxes they draw."""
    w, h = page.size
    ink = page.point(lambda v: 255 if v < INK else 0)
    minlen = max(40, max(w, h) // 30)  # ~1 inch at 300 dpi; letters and digits are shorter
    hr = _runs(ink, minlen)
    vr = _runs(ink.transpose(Image.Transpose.TRANSPOSE), minlen)  # (x, y0, y1)
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    for y, x0, x1 in hr:
        d.rectangle((x0, y - 1, x1 - 1, y + 1), fill=255)
    for x, y0, y1 in vr:
        d.rectangle((x - 1, y0, x + 1, y1 - 1), fill=255)
    clean = page.copy()
    clean.paste(255, mask=mask)
    ink.paste(0, mask=mask)

    # vertical rulings: x positions whose runs add up to a real column line
    by_x: dict[int, int] = {}
    for x, y0, y1 in vr:
        by_x[x] = by_x.get(x, 0) + (y1 - y0)
    xs = [
        round(sum(c) / len(c))
        for c in _clusters(list(by_x), 6)
        if sum(by_x[x] for x in c) >= h * 0.12
    ]
    cells: list[tuple[int, int, int, int]] = []
    for xa, xb in zip(xs, xs[1:], strict=False):
        bw = xb - xa
        if bw < w * 0.008:
            continue  # double ruling
        ys = [y for y, x0, x1 in hr if min(x1, xb) - max(x0, xa) >= 0.6 * bw]
        rows = [round(sum(c) / len(c)) for c in _clusters(ys, 6)]
        for ya, yb in zip(rows, rows[1:], strict=False):
            if h * 0.008 <= yb - ya <= h * 0.06:
                cells.append((xa, ya, xb, yb))
    return Grid(clean, ink, cells)


def marked_cells(
    grid: Grid, printed: list[tuple[int, int, int, int]], max_width: float = 0.08
) -> list[tuple[int, int, int, int]]:
    """Narrow boxes with ink in them and no printed word (`printed` = word boxes in pixels)."""
    w = grid.ink.size[0]
    centres = [((a + c) / 2, (b + d) / 2) for a, b, c, d in printed]
    out: list[tuple[int, int, int, int]] = []
    for x0, y0, x1, y1 in grid.cells:
        if x1 - x0 > w * max_width:
            continue
        if any(x0 <= cx <= x1 and y0 <= cy <= y1 for cx, cy in centres):
            continue
        ix, iy = max(4, (x1 - x0) // 10), max(4, (y1 - y0) // 6)
        box = grid.ink.crop((x0 + ix, y0 + iy, x1 - ix, y1 - iy))
        area = box.size[0] * box.size[1]
        dark = box.histogram()[255]
        # specks are not writing, nor is a solid fill (shaded header bar)
        if area and dark >= 30 and 0.025 <= dark / area <= 0.42:
            out.append((x0, y0, x1, y1))
        if len(out) >= MAX_CELLS:
            break
    return out


def read_cells(
    clean: Image.Image,
    cells: list[tuple[int, int, int, int]],
    cmd: str,
    workdir: Path,
    timeout: int,
) -> list[tuple[str, float]]:
    """(digits read, confidence 0..1) per cell, ("", 0.0) when nothing could be read. The cells
    are stacked into one image so Tesseract runs once per page."""
    if not cells:
        return []
    pad = 30
    crops = [clean.crop((x0 + 3, y0 + 3, x1 - 3, y1 - 3)) for x0, y0, x1, y1 in cells]
    sheet = Image.new(
        "L",
        (max(c.size[0] for c in crops) + 2 * pad, sum(c.size[1] + pad for c in crops) + pad),
        255,
    )
    spans: list[tuple[int, int]] = []
    y = pad
    for c in crops:
        sheet.paste(c, (pad, y))
        spans.append((y, y + c.size[1]))
        y += c.size[1] + pad
    path = workdir / "cells.png"
    sheet.save(path)
    args = [cmd, str(path), "-", "--psm", "6", "-c", "tessedit_char_whitelist=0123456789.", "tsv"]
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return [("", 0.0)] * len(cells)
    words: list[list[tuple[int, str, float]]] = [[] for _ in cells]
    for line in r.stdout.decode("utf-8", "replace").splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 12 or f[0] != "5" or not f[11].strip() or float(f[10]) < 0:
            continue
        mid = int(f[7]) + int(f[9]) // 2
        for i, (a, b) in enumerate(spans):
            if a - pad // 2 <= mid <= b + pad // 2:
                words[i].append((int(f[6]), f[11].strip(), float(f[10]) / 100))
                break
    out: list[tuple[str, float]] = []
    for ws in words:
        ws.sort()
        text = "".join(t for _, t, _ in ws)
        out.append((text, min(c for _, _, c in ws)) if ws else ("", 0.0))
    return out
