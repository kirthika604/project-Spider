"""Quote proof: an AI value is kept only if its quote is really on the page.

This is the step that blocks invented values (section 12, step 3).
"""

from __future__ import annotations

import re
import unicodedata


def _flatten(text: str) -> str:
    """Ignore case, spacing and punctuation width when comparing."""
    clean = unicodedata.normalize("NFKC", str(text or "")).lower()
    clean = clean.replace("–", "-").replace("—", "-").replace("’", "'")
    return re.sub(r"[\s ]+", " ", clean).strip()


def _squeeze(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _flatten(text))


def quote_on_page(quote: str, page_text: str) -> bool:
    if not quote or not page_text:
        return False
    if _flatten(quote) in _flatten(page_text):
        return True
    squeezed = _squeeze(quote)
    return len(squeezed) > 8 and squeezed in _squeeze(page_text)


def value_in_quote(value, quote: str) -> bool:
    """The value itself must appear inside its supporting sentence."""
    if value is None or not quote:
        return False
    flat_quote, flat_value = _flatten(value), _flatten(quote)
    if flat_quote and flat_quote in flat_value:
        return True
    numbers_in_value = re.findall(r"\d[\d,.]*", str(value))
    if not numbers_in_value:
        return _squeeze(value) in _squeeze(quote)
    quote_numbers = {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,.]*", quote)}
    return all(n.replace(",", "").rstrip(".") in quote_numbers for n in numbers_in_value)


def check(value, quote: str, page_text: str) -> tuple[bool, str]:
    """Return (kept, reason). A rejected value never enters the dataset."""
    if not quote:
        return False, "no quote given for the value"
    if not quote_on_page(quote, page_text):
        return False, "quote not found on page"
    if not value_in_quote(value, quote):
        return False, "value does not appear in its own quote"
    return True, "quote verified"
