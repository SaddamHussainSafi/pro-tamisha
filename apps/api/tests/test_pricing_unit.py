"""Pure pricing helpers: canonical hashing and CSV formula safety (no DB)."""

import uuid
from decimal import Decimal

from pro_trading.pricing import RATE_RE, EntryKey, _cell, canonical_hash, csv_safe

A, B = uuid.UUID(int=1), uuid.UUID(int=2)


def test_hash_is_order_and_format_independent():
    x = {
        EntryKey(A, "LB", "STANDARD", None): Decimal("1.5"),
        EntryKey(B, "BOX", "STANDARD", None): Decimal("28"),
    }
    y = dict(reversed(list({k: v.quantize(Decimal("0.0001")) for k, v in x.items()}.items())))
    assert canonical_hash(x) == canonical_hash(y)


def test_hash_changes_with_rate_unit_or_scope():
    base = {EntryKey(A, "LB", "STANDARD", None): Decimal("1.5")}
    h = canonical_hash(base)
    assert canonical_hash({EntryKey(A, "LB", "STANDARD", None): Decimal("1.5001")}) != h
    assert canonical_hash({EntryKey(A, "KG", "STANDARD", None): Decimal("1.5")}) != h
    assert canonical_hash({EntryKey(A, "LB", "GROUP", B): Decimal("1.5")}) != h


def test_rate_format():
    for ok in ("0", "1", "1.5", "1234567890.1234"):
        assert RATE_RE.match(ok)
    for bad in ("-1", "1.12345", "1e3", "1,50", "", " 1", "NaN", "Infinity", "0x10"):
        assert not RATE_RE.match(bad)


def test_csv_formula_round_trip():
    for v in ("=SUM(A1)", "+1", "-1", "@x", "Cassava"):
        assert _cell({"c": csv_safe(v)}, "c") == v
    assert csv_safe("=1").startswith("'")
    assert _cell({"c": "'Quoted"}, "c") == "'Quoted"  # a real leading quote is kept
