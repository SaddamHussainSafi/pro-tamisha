"""Product-specific unit conversion (Module 01). Pure Decimal; no floats.

Rules (docs/modules/01-catalog.md, D-08/D-09 interim defaults):
- Conversions come only from the product's own active unit profiles; no global table.
- A group may state the actual case weight; otherwise the profile default is used and the
  result is flagged `estimated` (default case weight is only a suggestion).
- No weight known for a BOX/PIECE group -> UnknownConversionError (never guessed).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

LB_TO_KG = Decimal("0.45359237")  # exact by definition
Q4 = Decimal("0.0001")
COUNTED_UNITS = frozenset({"BOX", "PIECE"})
WeightSource = Literal["ENTERED", "PROFILE_DEFAULT", "BASE_UNIT"]


class UnknownConversionError(ValueError):
    pass


class InvalidQuantityError(ValueError):
    pass


class ProfileLike(Protocol):
    unit_code: str
    weight_lb_per_unit: Decimal | None


@dataclass(frozen=True)
class GroupResult:
    count: Decimal
    unit_code: str
    weight_lb_per_unit: Decimal
    weight_lb: Decimal
    weight_source: WeightSource


@dataclass(frozen=True)
class WeightTotal:
    groups: list[GroupResult]
    units: dict[str, Decimal]
    total_lb: Decimal
    total_kg: Decimal
    estimated: bool


def q4(v: Decimal) -> Decimal:
    return v.quantize(Q4)


def lb_to_kg(lb: Decimal) -> Decimal:
    return q4(lb * LB_TO_KG)


def kg_to_lb(kg: Decimal) -> Decimal:
    """Exact definition (1 lb = 0.45359237 kg), rounded to the 4 places stock is kept in."""
    return q4(kg / LB_TO_KG)


def group_weight(
    profile: ProfileLike, count: Decimal, entered_lb_per_unit: Decimal | None = None
) -> GroupResult:
    if count <= 0:
        raise InvalidQuantityError("count must be greater than zero")
    unit = profile.unit_code
    if unit in COUNTED_UNITS and count != count.to_integral_value():
        raise InvalidQuantityError(f"{unit} count must be a whole number")
    if unit == "LB" or unit == "KG":
        if entered_lb_per_unit is not None:
            raise InvalidQuantityError(f"{unit} is a weight unit; do not enter a case weight")
        per = Decimal(1) if unit == "LB" else q4(Decimal(1) / LB_TO_KG)
        lb = count if unit == "LB" else q4(count / LB_TO_KG)
        return GroupResult(count, unit, per, lb, "BASE_UNIT")
    if entered_lb_per_unit is not None:
        if entered_lb_per_unit <= 0:
            raise InvalidQuantityError("case weight must be greater than zero")
        per, source = entered_lb_per_unit, "ENTERED"
    elif profile.weight_lb_per_unit is not None:
        per, source = profile.weight_lb_per_unit, "PROFILE_DEFAULT"
    else:
        raise UnknownConversionError(
            f"No weight conversion defined for this product's {unit} profile"
        )
    return GroupResult(count, unit, per, q4(count * per), source)  # type: ignore[arg-type]


def total_weight(groups: list[GroupResult]) -> WeightTotal:
    units: dict[str, Decimal] = {}
    for g in groups:
        units[g.unit_code] = units.get(g.unit_code, Decimal(0)) + g.count
    total = q4(sum((g.weight_lb for g in groups), Decimal(0)))
    return WeightTotal(
        groups=groups,
        units=units,
        total_lb=total,
        total_kg=lb_to_kg(total),
        estimated=any(g.weight_source == "PROFILE_DEFAULT" for g in groups),
    )
