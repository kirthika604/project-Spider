"""Sanity rules: a range or allowed list per field (section 12, step 4)."""

from __future__ import annotations

from ..standardize import units


def check(field_spec, standard_value) -> tuple[bool, str]:
    """Return (passes, reason). Failures go to the review queue, not the dataset."""
    if field_spec is None or field_spec.sanity is None:
        return True, ""
    rule = field_spec.sanity
    number = standard_value.value_num
    top = standard_value.value_max

    numeric_rule = (len(rule) == 2 and all(isinstance(x, (int, float)) for x in rule))
    if numeric_rule:
        low, high = float(rule[0]), float(rule[1])
        for candidate in (number, top):
            if candidate is None:
                continue
            if not low <= candidate <= high:
                return False, (f"{_show(candidate)} is outside the allowed range "
                               f"{_show(low)} to {_show(high)}"
                               f"{' ' + field_spec.unit if field_spec.unit else ''}")
        if number is None and standard_value.value:
            return True, ""
        if number is not None and top is not None and top < number:
            return False, "range maximum is below its minimum"
        return True, ""

    allowed = {str(x).strip().lower() for x in rule}
    value = str(standard_value.value or "").strip().lower()
    if value and value not in allowed:
        return False, f"'{standard_value.value}' is not one of {', '.join(map(str, rule))}"
    return True, ""


def _show(number) -> str:
    if number is None:
        return ""
    return str(int(number)) if float(number).is_integer() else str(round(number, 3))
