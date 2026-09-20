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
            money = _currency_value(raw, fld.unit if fld else None,
                                    self.spec.standardize.currency,
                                    self.spec.standardize.rates)
            if money is not None:
                value, currency, changed, problem = money
                if problem:
                    result.rejected = True
                    result.reason = problem
                    self._note("values rejected: currency has no rate", raw[:60])
                    return
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
# Currency codes Spider recognises when it reads an amount. This is a list of
# names, not of prices: it says "GBP" is a currency, and nothing about how many
# of anything a pound is worth.
_CURRENCY_CODES = {
    "USD", "EUR", "GBP", "INR", "JPY", "CNY", "AUD", "CAD", "CHF", "NZD", "SGD",
    "HKD", "SEK", "NOK", "DKK", "KRW", "MXN", "BRL", "ZAR", "AED", "SAR", "LKR",
    "PKR", "BDT", "NPR", "IDR", "MYR", "THB", "TRY", "RUB", "PLN",
}
_CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_CURRENCY_WORDS = {"rs": "INR", "rs.": "INR"}


def _currency_of(raw: str) -> str | None:
    """The currency an amount is written in, or None if it does not say."""
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in raw:
            return code
    for word in re.findall(r"\b[A-Za-z]{2,3}\.?(?=\W|$)", raw):
        low = word.lower()
        if low in _CURRENCY_WORDS:
            return _CURRENCY_WORDS[low]
        if word.upper().rstrip(".") in _CURRENCY_CODES:
            return word.upper().rstrip(".")
    return None


def _is_money_field(fld, raw: str) -> bool:
    unit = (getattr(fld, "unit", "") or "").upper()
    name = (getattr(fld, "name", "") or "").lower()
    if unit in _CURRENCY_CODES:
        return True
    if unit:                      # a declared physical unit: not money
        return False
    return (_currency_of(raw) is not None or any(
        word in name for word in ("price", "cost", "amount", "fee", "salary",
                                  "revenue", "budget", "wage")))


def _currency_value(raw: str, field_unit: str, project_currency: str,
                    rates: dict) -> tuple:
    """(amount, currency, converted, problem) for a written amount.

    What an amount is stored in comes from, in order: the field's own declared
    unit, then the project's `standardize.currency`, then - if neither is set -
    whatever the source wrote, unchanged. Conversion needs a rate the user gave
    in `standardize.rates`; Spider has no exchange rates of its own, so without
    one a mismatch is reported instead of guessed at.
    """
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", raw)
    if not match:
        return None
    amount = float(match.group(0).replace(",", ""))
    source = _currency_of(raw)
    field_code = (field_unit or "").upper()
    target = (field_code if field_code in _CURRENCY_CODES
              else (project_currency or "").upper() or None)

    if source is None:                    # no marker: it is in the target already
        return amount, target or None, False, None
    if target is None or source == target:
        return amount, source, False, None

    have = {k.upper(): float(v) for k, v in (rates or {}).items()}
    if source not in have or target not in have:
        missing = source if source not in have else target
        return None, source, False, (
            f"written in {source} but this field is in {target}, and there is no "
            f"rate for {missing} - add it under standardize.rates")
    return amount * have[source] / have[target], target, True, None
