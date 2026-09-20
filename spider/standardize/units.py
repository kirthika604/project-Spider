"""Unit conversion. Uses pint when installed, a built-in table otherwise."""

from __future__ import annotations

import re

# factor converts the unit to the base unit of its quantity
TABLE = {
    "length": {"base": "m", "units": {
        "m": 1.0, "metre": 1.0, "metres": 1.0, "meter": 1.0, "meters": 1.0,
        "km": 1000.0, "kilometre": 1000.0, "kilometres": 1000.0, "kilometer": 1000.0,
        "cm": 0.01, "mm": 0.001,
        "ft": 0.3048, "feet": 0.3048, "foot": 0.3048, "'": 0.3048,
        "in": 0.0254, "inch": 0.0254, "inches": 0.0254,
        "mi": 1609.344, "mile": 1609.344, "miles": 1609.344,
    }},
    "mass": {"base": "kg", "units": {
        "kg": 1.0, "g": 0.001, "mg": 1e-6, "t": 1000.0,
        "lb": 0.45359237, "lbs": 0.45359237, "pound": 0.45359237, "oz": 0.0283495,
    }},
    "time": {"base": "d", "units": {
        "d": 1.0, "day": 1.0, "days": 1.0, "h": 1 / 24, "hour": 1 / 24, "hours": 1 / 24,
        "min": 1 / 1440, "s": 1 / 86400, "week": 7.0, "weeks": 7.0,
        "month": 30.4375, "months": 30.4375, "year": 365.25, "years": 365.25,
    }},
    "temperature": {"base": "c", "units": {"c": 1.0, "f": 1.0, "k": 1.0}},
}

ALIASES = {}
for _quantity, _spec in TABLE.items():
    for _unit in _spec["units"]:
        ALIASES[_unit] = (_quantity, _unit)


class UnitError(Exception):
    pass


def quantity_of(unit: str) -> str | None:
    entry = ALIASES.get((unit or "").strip().lower())
    return entry[0] if entry else None


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a number between units of the same quantity."""
    src, dst = (from_unit or "").strip().lower(), (to_unit or "").strip().lower()
    if not src or src == dst:
        return float(value)
    try:                                       # prefer pint when available
        import pint
        registry = _pint_registry()
        return float((float(value) * registry(src)).to(dst).magnitude)
    except ImportError:
        pass
    except Exception as exc:
        raise UnitError(f"cannot convert {from_unit} to {to_unit}: {exc}") from exc

    q_src, q_dst = quantity_of(src), quantity_of(dst)
    if q_src is None or q_dst is None:
        raise UnitError(f"unknown unit: {from_unit if q_src is None else to_unit}")
    if q_src != q_dst:
        raise UnitError(f"{from_unit} ({q_src}) and {to_unit} ({q_dst}) measure different things")
    if q_src == "temperature":
        return _temperature(float(value), src, dst)
    table = TABLE[q_src]["units"]
    return float(value) * table[src] / table[dst]


_REGISTRY = None


def _pint_registry():
    global _REGISTRY
    if _REGISTRY is None:
        import pint
        _REGISTRY = pint.UnitRegistry()
    return _REGISTRY


def _temperature(value: float, src: str, dst: str) -> float:
    celsius = {"c": value, "f": (value - 32) * 5 / 9, "k": value - 273.15}[src]
    return {"c": celsius, "f": celsius * 9 / 5 + 32, "k": celsius + 273.15}[dst]


# scientific notation included: a page that writes 5.972e24 means 5.972e24,
# and reading it as 5.972 would be silently, enormously wrong
NUMBER = r"[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?"
RANGE_SEPARATORS = r"(?:\s*(?:-|–|—|to|and|until|up to)\s*)"
UNIT_WORD = r"[a-zA-Z°'\"%]+"


def parse_number(text) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    match = re.search(NUMBER, str(text or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


BIG = 1e15
SMALL = 1e-4


def format_number(value) -> str:
    """Write a number the way a reader would.

    A float carrying 5.972e24 is not the integer 5972000000000000327155712:
    printing it that way invents 19 digits of precision that were never
    measured, so anything very large or very small keeps its exponent.
    """
    if value is None:
        return ""
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return str(value)
    size = abs(number)
    if size != 0 and (size >= BIG or size < SMALL):
        return f"{number:.6g}"
    if number.is_integer():
        return str(int(number))
    return str(round(number, 6))


def parse_value(text, target_unit: str | None = None):
    """Read '3,000 to 4,500 m' into (min, max, unit) in the target unit.

    Returns (minimum, maximum, unit) where maximum is None for a single value.
    """
    if text is None:
        return (None, None, target_unit)
    raw = str(text).strip()
    if isinstance(text, (int, float)):
        return (float(text), None, target_unit)

    # the first word after a number is not always the unit: in "2,300 to
    # 4,100 m" it is "to", so keep looking until a real unit turns up
    unit = None
    for candidate in re.finditer(rf"({NUMBER})\s*({UNIT_WORD})", raw):
        if quantity_of(candidate.group(2)):
            unit = candidate.group(2).lower()
            break

    range_match = re.search(rf"({NUMBER}){RANGE_SEPARATORS}({NUMBER})", raw, re.IGNORECASE)
    if range_match:
        low = float(range_match.group(1).replace(",", ""))
        high = float(range_match.group(2).replace(",", ""))
    else:
        low, high = parse_number(raw), None
    if low is None:
        return (None, None, unit or target_unit)

    if target_unit and unit and unit != target_unit.lower():
        try:
            whole = float(low).is_integer() and (high is None or float(high).is_integer())
            low = convert(low, unit, target_unit)
            high = convert(high, unit, target_unit) if high is not None else None
            if whole:
                # a source that wrote a whole number meant a whole number:
                # 9,842 ft is 3000 m, not 2999.8416 m
                low = round(low)
                high = round(high) if high is not None else None
            unit = target_unit
        except UnitError:
            pass
    elif target_unit and not unit:
        unit = target_unit
    return (round(low, 6), round(high, 6) if high is not None else None, unit)
