"""Minimal QR Code encoder (ISO/IEC 18004) for short codes printed on our own sheets.

Byte mode, error correction level M, versions 1-6 (up to 106 bytes), mask chosen by the standard
penalty rules. Deterministic and dependency-free, like the PDF renderers that draw it. Structure
follows Project Nayuki's reference implementation (MIT).
"""

from __future__ import annotations

# Level M, indexed by version (index 0 unused).
_ECC_PER_BLOCK = (-1, 10, 16, 26, 18, 24, 16)
_BLOCKS = (-1, 1, 1, 1, 2, 2, 4)
MAX_VERSION = 6
_FORMAT_M = 0  # format bits of level M


def _raw_modules(ver: int) -> int:
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        k = ver // 7 + 2
        n -= (25 * k - 10) * k - 55
    return n


def _data_codewords(ver: int) -> int:
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[ver] * _BLOCKS[ver]


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    out = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            out[j] = _gf_mul(out[j], root)
            if j + 1 < degree:
                out[j] ^= out[j + 1]
        root = _gf_mul(root, 0x02)
    return out


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    out = [0] * len(divisor)
    for b in data:
        factor = b ^ out.pop(0)
        out.append(0)
        for i, coef in enumerate(divisor):
            out[i] ^= _gf_mul(coef, factor)
    return out


def _codewords(data: bytes) -> tuple[int, list[int]]:
    """(version, final codeword sequence with error correction, interleaved)."""
    ver = next((v for v in range(1, MAX_VERSION + 1) if len(data) + 2 <= _data_codewords(v)), 0)
    if not ver:
        raise ValueError(f"QR payload too long ({len(data)} bytes)")
    cap = _data_codewords(ver) * 8
    bits: list[int] = []

    def put(val: int, n: int) -> None:
        bits.extend((val >> i) & 1 for i in reversed(range(n)))

    put(0b0100, 4)  # byte mode
    put(len(data), 8)
    for b in data:
        put(b, 8)
    put(0, min(4, cap - len(bits)))
    put(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < cap:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    words = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]

    nblocks, ecc = _BLOCKS[ver], _ECC_PER_BLOCK[ver]
    raw = _raw_modules(ver) // 8
    nshort = nblocks - raw % nblocks
    short_len = raw // nblocks
    div = _rs_divisor(ecc)
    blocks: list[list[int]] = []
    k = 0
    for i in range(nblocks):
        dat = words[k : k + short_len - ecc + (0 if i < nshort else 1)]
        k += len(dat)
        rem = _rs_remainder(dat, div)
        if i < nshort:
            dat = [*dat, 0]
        blocks.append(dat + rem)
    out: list[int] = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short_len - ecc or j >= nshort:
                out.append(blk[i])
    return ver, out


class _Grid:
    def __init__(self, ver: int) -> None:
        self.size = ver * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]
        self._functions(ver)

    def set(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.fixed[y][x] = True

    def _functions(self, ver: int) -> None:
        n = self.size
        for i in range(n):
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        if ver >= 2:
            k = ver // 7 + 2
            step = (ver * 8 + k * 3 + 5) // (k * 4 - 4) * 2
            pos = sorted([n - 7 - i * step for i in range(k - 1)] + [6])
            last = len(pos) - 1
            for i, ax in enumerate(pos):
                for j, ay in enumerate(pos):
                    if (i, j) in ((0, 0), (0, last), (last, 0)):
                        continue  # finder corners
                    for dy in range(-2, 3):
                        for dx in range(-2, 3):
                            self.set(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0)  # reserve; real bits after masking

    def format_bits(self, mask: int) -> None:
        data = _FORMAT_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        n = self.size

        def bit(i: int) -> bool:
            return (bits >> i) & 1 != 0

        for i in range(6):
            self.set(8, i, bit(i))
        self.set(8, 7, bit(6))
        self.set(8, 8, bit(7))
        self.set(7, 8, bit(8))
        for i in range(9, 15):
            self.set(14 - i, 8, bit(i))
        for i in range(8):
            self.set(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set(8, n - 15 + i, bit(i))
        self.set(8, n - 8, True)  # the dark module

    def place(self, words: list[int]) -> None:
        n, i, total = self.size, 0, len(words) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self.fixed[y][x] and i < total:
                        self.dark[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 != 0
                        i += 1
            right -= 2

    def mask(self, m: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if self.fixed[y][x]:
                    continue
                flip = (
                    (x + y) % 2 == 0,
                    y % 2 == 0,
                    x % 3 == 0,
                    (x + y) % 3 == 0,
                    (x // 3 + y // 2) % 2 == 0,
                    x * y % 2 + x * y % 3 == 0,
                    (x * y % 2 + x * y % 3) % 2 == 0,
                    ((x + y) % 2 + x * y % 3) % 2 == 0,
                )[m]
                if flip:
                    self.dark[y][x] = not self.dark[y][x]

    def penalty(self) -> int:
        n, g, score = self.size, self.dark, 0
        lines = [g[y] for y in range(n)] + [[g[y][x] for y in range(n)] for x in range(n)]
        finder = ([1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1])
        for ln in lines:
            run = 1
            for i in range(1, n + 1):
                if i < n and ln[i] == ln[i - 1]:
                    run += 1
                    continue
                if run >= 5:
                    score += run - 2
                run = 1
            vals = [int(v) for v in ln]
            for i in range(n - 10):
                if vals[i : i + 11] in finder:
                    score += 40
        for y in range(n - 1):
            for x in range(n - 1):
                if g[y][x] == g[y][x + 1] == g[y + 1][x] == g[y + 1][x + 1]:
                    score += 3
        dark = sum(v for row in g for v in row)
        total = n * n
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1  # each 5% away from half
        return score + max(0, k) * 10


def encode(text: str) -> list[list[bool]]:
    """Module matrix (True = dark), without the quiet zone. Same text -> same matrix."""
    ver, words = _codewords(text.encode("utf-8"))
    best: tuple[int, int] | None = None
    for m in range(8):
        g = _Grid(ver)
        g.place(words)
        g.mask(m)
        g.format_bits(m)
        p = g.penalty()
        if best is None or p < best[0]:
            best = (p, m)
    assert best is not None
    g = _Grid(ver)
    g.place(words)
    g.mask(best[1])
    g.format_bits(best[1])
    return g.dark
