"""Name cleaning, transliteration and fuzzy matching (D4, D9)."""

from __future__ import annotations

import difflib
import re
import unicodedata

TITLE_WORDS = {"var", "subsp", "ssp", "f", "cv"}
RANK_MARKERS = {"var.", "subsp.", "ssp.", "f.", "cv.", "var", "subsp", "ssp", "cv"}


def normalise(text: str) -> str:
    """Unicode NFKC, collapse spaces, strip stray punctuation."""
    if text is None:
        return ""
    clean = unicodedata.normalize("NFKC", str(text))
    clean = clean.replace("​", "").replace("\xa0", " ")
    clean = re.sub(r"[\s\n\t]+", " ", clean).strip(" \t\n.,;:|-")
    return clean


def transliterate(text: str) -> str:
    """Devanagari/Tamil to Latin so local names match English spellings."""
    clean = normalise(text)
    try:
        from unidecode import unidecode
        return unidecode(clean)
    except ImportError:
        pass
    try:
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate as indic
        return indic(clean, sanscript.DEVANAGARI, sanscript.ITRANS)
    except Exception:
        return clean


def script_of(text: str) -> str:
    for char in str(text or ""):
        code = ord(char)
        if 0x0900 <= code <= 0x097F:
            return "Devanagari"
        if 0x0B80 <= code <= 0x0BFF:
            return "Tamil"
    return "Latin"


def key(text: str) -> str:
    """A comparison key: lowercase, transliterated, letters and digits only."""
    return re.sub(r"[^a-z0-9]+", " ", transliterate(text).lower()).strip()


SCIENTIFIC_FIELD_WORDS = ("scientific", "species", "binomial", "taxon", "latin")


def is_scientific_field(field_name: str) -> bool:
    """Whether a field holds a scientific name, whatever the schema calls it."""
    low = str(field_name or "").lower()
    return any(word in low for word in SCIENTIFIC_FIELD_WORDS)


def scientific(text: str) -> str:
    """Clean a binomial: 'saussurea OBVALLATA Nakai' -> 'Saussurea obvallata'.

    The author citation that follows a species name ("Nakai", "L.", "(DC.)
    Hook.f.") is part of the citation, not the name, and two sources will
    often differ on whether they print it - so it is dropped before records
    are matched. An infraspecific rank (var., subsp.) is kept.
    """
    parts = normalise(text).split()
    if not parts:
        return ""
    out = [parts[0].capitalize()]
    index = 1
    while index < len(parts):
        word = parts[index]
        low = word.lower()
        if low in RANK_MARKERS:                 # keep "var. nivea" and the like
            out.append(low if low.endswith(".") else low + ".")
            if index + 1 < len(parts):
                out.append(parts[index + 1].lower())
                index += 1
        elif index <= 1 or low.rstrip(".") in TITLE_WORDS:
            out.append(low)
        else:
            break                               # an author citation starts here
        index += 1
    return " ".join(out)


def title_case(text: str) -> str:
    return " ".join(word.capitalize() for word in normalise(text).split())


def similarity_of_keys(left: str, right: str) -> float:
    """Similarity of two strings that are already comparison keys.

    `similarity` recomputes the key - transliteration included - on every call,
    which is most of what it costs when a record is compared against thousands.
    """
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    try:
        from rapidfuzz import fuzz
        return max(fuzz.token_sort_ratio(left, right),
                   fuzz.partial_ratio(left, right)) / 100.0
    except ImportError:
        return difflib.SequenceMatcher(None, left, right).ratio()


def similarity(a: str, b: str) -> float:
    """0 to 1 similarity, using rapidfuzz when installed."""
    left, right = key(a), key(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    try:
        from rapidfuzz import fuzz
        return max(fuzz.token_sort_ratio(left, right),
                   fuzz.partial_ratio(left, right)) / 100.0
    except ImportError:
        return difflib.SequenceMatcher(None, left, right).ratio()


def matches(a: str, b: str, threshold: float = 0.85) -> bool:
    return similarity(a, b) >= threshold


def case_style(text: str, style: str) -> str:
    """Rename a field for export: snake_case, camelCase or Title Case."""
    words = re.split(r"[\s_-]+", normalise(text)) or [""]
    if style == "camelCase":
        return words[0].lower() + "".join(w.capitalize() for w in words[1:])
    if style in ("Title Case", "title"):
        return " ".join(w.capitalize() for w in words)
    if style == "PascalCase":
        return "".join(w.capitalize() for w in words)
    return "_".join(w.lower() for w in words)
