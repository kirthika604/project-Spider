"""Agreement scoring (section 12, step 5).

One tier 1 source is 0.80, tier 2 is 0.60, tier 3 is 0.40 and the user's own
data (tier 0) is 0.95. Each further independent domain that agrees adds 0.15
up to 0.99; when domains disagree every value drops 0.20 and is flagged.
"""

from __future__ import annotations

BASE = {0: 0.95, 1: 0.80, 2: 0.60, 3: 0.40}
AGREEMENT_BONUS = 0.15
DISAGREEMENT_PENALTY = 0.20
CEILING = 0.99
INFERRED_CAP = 0.45
# A default came from the project file, not from the world: it fills a cell
# so a table is usable, and it is never evidence for anything.
DEFAULT_CONFIDENCE = 0.0


def base_for(tier: int) -> float:
    return BASE.get(int(tier), 0.40)


def score(candidates, *, disagreement: bool = False, origin: str = "extracted") -> float:
    """Confidence for one group of candidates that carry the same value."""
    if not candidates:
        return 0.0
    best_tier = min(int(c.tier) for c in candidates)
    value = base_for(best_tier)
    domains = {c.domain for c in candidates if c.domain}
    value += AGREEMENT_BONUS * max(0, len(domains) - 1)
    if disagreement:
        value -= DISAGREEMENT_PENALTY
    value = max(0.0, min(CEILING, value))
    if origin == "inferred":
        value = min(value, INFERRED_CAP)
    if origin == "default":
        return DEFAULT_CONFIDENCE
    return round(value, 3)


def pick_best(rows):
    """The one value to show when several are accepted for the same cell.

    Every value stays in the store; this only decides which one a single cell
    shows, and it must be the same answer everywhere - the table, the export
    and the inputs of a derivation - or one row could mix two sources.
    Most confident first, then the most trusted tier, then the first stored.
    """
    def rank(row):
        tier = row["tier"] if row["tier"] is not None else 3   # tier 0 is real
        return (row["confidence"] or 0.0, -int(tier))

    return max(rows, key=rank) if rows else None


def inherit(input_confidences, origin: str = "derived") -> float:
    """A derived value takes the lowest confidence among its inputs (D7)."""
    values = [c for c in input_confidences if c is not None]
    if not values:
        return 0.0
    lowest = min(values)
    return round(min(lowest, INFERRED_CAP) if origin == "inferred" else lowest, 3)
