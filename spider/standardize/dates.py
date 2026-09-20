"""Dates to ISO 8601, and months to numbers (D9)."""

from __future__ import annotations

import re
from datetime import datetime

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]

FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d %b %y", "%d %B, %Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m", "%Y",
]


def to_iso(text) -> str | None:
    """Parse many written date forms into YYYY-MM-DD (or YYYY-MM / YYYY)."""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bSept\b", "Sep", cleaned).strip(" .,")
    for fmt in FORMATS:
        try:
            parsed = datetime.strptime(cleaned[:len(datetime.now().strftime(fmt)) + 8]
                                       if len(cleaned) > 30 else cleaned, fmt)
        except ValueError:
            continue
        if fmt == "%Y":
            return parsed.strftime("%Y")
        if fmt == "%Y-%m":
            return parsed.strftime("%Y-%m")
        return parsed.strftime("%Y-%m-%d")
    # two-digit years: 20 Sep 26
    match = re.fullmatch(r"(\d{1,2})[ /-]([A-Za-z]{3,9})[ /-](\d{2})", cleaned)
    if match and match.group(2).lower()[:3] in MONTHS:
        day, month, year = int(match.group(1)), MONTHS[match.group(2).lower()[:3]], int(match.group(3))
        return f"{2000 + year:04d}-{month:02d}-{day:02d}"
    try:                                        # dateparser handles the rest
        import dateparser
        parsed = dateparser.parse(raw)
        if parsed:
            return parsed.strftime("%Y-%m-%d")
    except ImportError:
        pass
    match = re.search(r"\b(1[89]\d{2}|20\d{2})\b", raw)
    return match.group(1) if match else None


def to_month(text):
    """Return a month number 1-12 from a name, number or 'July to September'."""
    if text is None:
        return None
    if isinstance(text, (int, float)) and 1 <= int(text) <= 12:
        return int(text)
    raw = str(text).strip().lower()
    if raw.isdigit() and 1 <= int(raw) <= 12:
        return int(raw)
    for name, number in MONTHS.items():
        if re.search(rf"\b{name}", raw):
            return number
    iso = to_iso(raw)
    if iso and len(iso) >= 7:
        return int(iso[5:7])
    return None


def season_of(month) -> str | None:
    """Indian seasons, used by the `season_of()` formula function."""
    number = to_month(month)
    if not number:
        return None
    return {12: "winter", 1: "winter", 2: "winter",
            3: "spring", 4: "spring",
            5: "summer", 6: "summer",
            7: "monsoon", 8: "monsoon", 9: "monsoon",
            10: "autumn", 11: "autumn"}[number]


def year_of(text):
    iso = to_iso(text)
    return int(iso[:4]) if iso else None
