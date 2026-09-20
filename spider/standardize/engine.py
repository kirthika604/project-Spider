"""Standardize one extracted value before it is merged (D9, FR-24).

The original text is always kept beside the standard value, so a user can
turn the level down to `raw` and rebuild without re-crawling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from ..ref import tables as ref
from . import dates, names, units


@dataclass
class StandardValue:
    value: str | None                 # the standard form, as text
    value_num: float | None = None
    value_max: float | None = None
    unit: str | None = None
    raw: str = ""
    changes: list[str] = field(default_factory=list)
    rejected: bool = False
    reason: str = ""

    @property
    def empty(self) -> bool:
        return self.value in (None, "") and self.value_num is None


class Standardizer:
    """Applies the rules in the `standardize` section of spider.yaml."""

    def __init__(self, conn, spec):
        self.conn = conn
        self.spec = spec
        self.report: dict[str, int] = {}
        self.examples: dict[str, list[str]] = {}

    def _note(self, change: str, example: str = "") -> None:
        self.report[change] = self.report.get(change, 0) + 1
        if example and len(self.examples.setdefault(change, [])) < 3:
            self.examples[change].append(example)

    # ------------------------------------------------------------------ api
    def standardize(self, entity_type: str, field_name: str, raw_value) -> StandardValue:
        fld = self.spec.entity_field(entity_type, field_name)
        level = self.spec.field_level(entity_type, field_name)
        raw = names.normalise(raw_value)
        result = StandardValue(value=raw, raw=str(raw_value or "").strip())

        if result.raw and raw != result.raw.strip():
            self._note("text normalized", f"{result.raw!r} -> {raw!r}")
        if level == "raw" or not raw:
            return result

        ftype = fld.type if fld else "text"
        if ftype in ("number", "range"):
            self._numeric(result, fld, raw)
        elif ftype == "date":
            self._date(result, raw)
        elif ftype in ("month", "year"):
            self._month_or_year(result, ftype, raw)
        elif ftype == "bool":
            low = raw.lower()
            result.value = "true" if low in ("true", "yes", "y", "1") else (
                "false" if low in ("false", "no", "n", "0") else raw)
        else:
            self._text(result, entity_type, field_name, fld, raw)

        if fld and fld.vocabulary and level in ("standard", "strict"):
            self._vocabulary(result, fld, level)
        if fld and fld.unit and result.unit is None and result.value_num is not None:
            result.unit = fld.unit
        return result

    # -------------------------------------------------------------- helpers
    def _numeric(self, result: StandardValue, fld, raw: str) -> None:
        # Money is numeric but its units are currencies rather than physical
        # dimensions.  Handle it before the generic unit parser, which quite
        # correctly knows nothing about exchange rates.
        if _is_money_field(fld, raw):
            money = _currency_value(raw, self.spec.standardize.currency)
            if money is not None:
                value, currency, changed = money
                result.value_num, result.unit = value, currency
                result.value = _trim(value)
                if changed:
                    self._note("currencies converted",
                               f"{raw} -> {result.value} {currency}")
                return
        target = fld.unit if fld else None
        low, high, unit = units.parse_value(raw, target)
        if low is None:
            result.rejected = True
            result.reason = "no number found in the value"
            self._note("values rejected: not a number", raw[:60])
            return
        result.value_num, result.value_max, result.unit = low, high, unit
        result.value = (f"{_trim(low)}-{_trim(high)}" if high is not None else _trim(low))
        if target and target.lower() not in raw.lower() and any(
                ch.isalpha() for ch in raw):
            self._note("units converted", f"{raw} -> {result.value} {unit or ''}".strip())
        if high is not None:
            self._note("ranges split into min and max", f"{raw} -> min {_trim(low)}, max {_trim(high)}")

    def _date(self, result: StandardValue, raw: str) -> None:
        iso = dates.to_iso(raw)
        if iso is None:
            result.rejected = True
            result.reason = "could not read a date"
            self._note("values rejected: bad date", raw[:60])
            return
        if iso != raw:
            self._note("dates reformatted", f"{raw} -> {iso}")
        result.value = iso

    def _month_or_year(self, result: StandardValue, ftype: str, raw: str) -> None:
        if ftype == "month":
            month = dates.to_month(raw)
            if month is None:
                result.rejected = True
                result.reason = "could not read a month"
                self._note("values rejected: bad month", raw[:60])
                return
            result.value_num = float(month)
            result.value = dates.MONTH_NAMES[month]
            if result.value.lower() != raw.lower():
                self._note("months standardized", f"{raw} -> {result.value}")
        else:
            year = dates.year_of(raw)
            if year is None:
                result.rejected = True
                result.reason = "could not read a year"
                return
            result.value_num = float(year)
            result.value = str(year)

    def _text(self, result: StandardValue, entity_type: str, field_name: str,
              fld, raw: str) -> None:
        lowered = field_name.lower()
        if names.is_scientific_field(field_name):
            cleaned = names.scientific(raw)
            if cleaned != raw:
                self._note("names cleaned", f"{raw} -> {cleaned}")
            result.value = cleaned
        elif fld and (fld.vocabulary == "places" or lowered in ("region", "district", "state", "place")):
            row = ref.place(self.conn, raw)
            if row is not None and row["name"] != raw:
                self._note("places matched to official names", f"{raw} -> {row['name']}")
                result.value = row["name"]
            elif row is not None:
                result.value = row["name"]
        else:
            result.value = raw
        if names.script_of(result.value) != "Latin":
            self._note("local-script names kept with transliteration",
                       f"{result.value} ~ {names.transliterate(result.value)}")

    def _vocabulary(self, result: StandardValue, fld, level: str) -> None:
        if fld.vocabulary == "places":
            return                               # handled in _text
        term = ref.vocab_term(self.conn, fld.vocabulary, result.value or "")
        if term:
            if term != result.value:
                self._note("categories mapped to the controlled list",
                           f"{result.value} -> {term}")
            result.value = term
        elif level == "strict":
            result.rejected = True
            result.reason = (f"'{result.value}' is not in the '{fld.vocabulary}' "
                             f"vocabulary and the level is strict")
            self._note("values rejected: not in vocabulary", str(result.value)[:60])

    def summary(self) -> dict:
        return {"changes": dict(sorted(self.report.items(), key=lambda kv: -kv[1])),
                "examples": self.examples}


def _trim(number: float) -> str:
    return units.format_number(number)


# A deliberately small offline table.  Values are explicitly a fixed demo
# snapshot, not a claim that a historical rate was looked up.  Projects that
# need accounting-grade historical conversion should retain the raw value and
# use a dated connector before accepting the result.
_CURRENCY_TO_USD = {"USD": 1.0, "INR": 1 / 83.0, "EUR": 1.08, "GBP": 1.27}
_CURRENCY_MARKERS = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR",
    "$": "USD", "usd": "USD", "us$": "USD",
    "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
}


def _is_money_field(fld, raw: str) -> bool:
    name = (getattr(fld, "name", "") or "").lower()
    unit = (getattr(fld, "unit", "") or "").upper()
    low = raw.lower()
    has_marker = any(marker in raw for marker in ("₹", "$", "€", "£")) or bool(
        re.search(r"\b(?:rs\.?|inr|usd|eur|gbp)\b", raw, re.IGNORECASE))
    return (unit in _CURRENCY_TO_USD or any(word in name for word in
            ("price", "cost", "amount", "currency", "fee", "salary")) or
            has_marker)


def _currency_value(raw: str, target: str) -> tuple[float, str, bool] | None:
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", raw)
    if not match:
        return None
    amount = float(match.group(0).replace(",", ""))
    low = raw.lower()
    source = next((code for marker, code in _CURRENCY_MARKERS.items()
                   if (marker in ("₹", "$", "€", "£") and marker in raw)
                   or (marker not in ("₹", "$", "€", "£")
                       and re.search(rf"\b{re.escape(marker)}\b", low))), None)
    target = (target or "INR").upper()
    if source is None:
        # A field explicitly declared with a currency is already in that unit.
        return amount, target, False
    if source not in _CURRENCY_TO_USD or target not in _CURRENCY_TO_USD:
        return None
    converted = amount * _CURRENCY_TO_USD[source] / _CURRENCY_TO_USD[target]
    return converted, target, source != target
