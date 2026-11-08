"""Pure unit-conversion tests (Module 01 acceptance: Cassava 205 lb; INV-006 weight part).

205 lb for 7 boxes is not reachable with 20/30/40 lb boxes alone (every sum is a multiple
of 10), so the fixture follows INV-006: cases of 20/30/40 plus one 25 lb case.
2x20 + 2x30 + 2x40 + 1x25 = 205 lb.
"""

from dataclasses import dataclass
from decimal import Decimal as D  # noqa: N817

import pytest

from pro_trading.units import (
    InvalidQuantityError,
    UnknownConversionError,
    group_weight,
    lb_to_kg,
    total_weight,
)


@dataclass
class P:
    unit_code: str
    weight_lb_per_unit: D | None = None


BOX20, BOX30, BOX40, BOX25 = (P("BOX", D(w)) for w in ("20", "30", "40", "25"))
BOX_NO_WEIGHT = P("BOX")


def test_cassava_variable_case_groups_total_205_lb_for_7_boxes() -> None:
    t = total_weight(
        [
            group_weight(BOX20, D(2)),
            group_weight(BOX30, D(2)),
            group_weight(BOX40, D(2)),
            group_weight(BOX25, D(1)),
        ]
    )
    assert t.units == {"BOX": D(7)}
    assert t.total_lb == D("205.0000")
    assert t.total_kg == D("92.9864")
    # INV-006 pricing (Module 02) will multiply by 1.50/lb -> CAD 307.50
    assert (t.total_lb * D("1.50")).quantize(D("0.01")) == D("307.50")


def test_entered_case_weight_overrides_default_and_is_not_estimated() -> None:
    groups = [
        group_weight(BOX_NO_WEIGHT, D(2), D(20)),
        group_weight(BOX_NO_WEIGHT, D(2), D(30)),
        group_weight(BOX_NO_WEIGHT, D(2), D(40)),
        group_weight(BOX_NO_WEIGHT, D(1), D(25)),
    ]
    t = total_weight(groups)
    assert t.total_lb == D("205.0000")
    assert not t.estimated
    assert {g.weight_source for g in groups} == {"ENTERED"}


def test_profile_default_weight_is_flagged_estimated() -> None:
    t = total_weight([group_weight(BOX30, D(3)), group_weight(BOX30, D(1), D("28.5"))])
    assert t.total_lb == D("118.5000")
    assert t.estimated


def test_unknown_piece_to_weight_conversion_rejected() -> None:
    with pytest.raises(UnknownConversionError):
        group_weight(P("PIECE"), D(12))


def test_unknown_box_weight_rejected() -> None:
    with pytest.raises(UnknownConversionError):
        group_weight(BOX_NO_WEIGHT, D(1))


@pytest.mark.parametrize("count", [D(0), D(-1), D("1.5")])
def test_box_counts_must_be_positive_whole(count: D) -> None:
    with pytest.raises(InvalidQuantityError):
        group_weight(BOX20, count)


def test_entered_weight_must_be_positive() -> None:
    with pytest.raises(InvalidQuantityError):
        group_weight(BOX20, D(1), D(0))


def test_base_units() -> None:
    lb = group_weight(P("LB"), D("12.5"))
    kg = group_weight(P("KG"), D(10))
    assert lb.weight_lb == D("12.5") and lb.weight_source == "BASE_UNIT"
    assert kg.weight_lb == D("22.0462")
    with pytest.raises(InvalidQuantityError):
        group_weight(P("LB"), D(1), D(5))


def test_no_float_leaks() -> None:
    t = total_weight([group_weight(BOX20, D(3))])
    assert all(isinstance(v, D) for v in (t.total_lb, t.total_kg, *t.units.values()))
    assert lb_to_kg(D(1)) == D("0.4536")
