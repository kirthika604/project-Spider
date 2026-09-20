"""What to do when standardized values still disagree (D9 conflict policy)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Decision:
    winners: list           # attribute candidates that enter the dataset
    flagged: list           # candidates kept but marked for review
    rule: str
    note: str = ""


def same_value(a, b) -> bool:
    """Two standardized values agree if their numbers or texts match.

    A single reading that falls inside another source's range confirms it
    rather than contradicting it: "around 9,842 ft" and "3,000 to 4,500 m"
    are one source agreeing with another, not two sources disagreeing.
    """
    if a.value_num is not None and b.value_num is not None:
        ranged_a, ranged_b = a.value_max is not None, b.value_max is not None
        if ranged_a != ranged_b:
            point, spread = (b, a) if ranged_a else (a, b)
            margin = max(abs(spread.value_num), abs(spread.value_max), 1.0) * 0.02
            return (spread.value_num - margin <= point.value_num
                    <= spread.value_max + margin)
        tolerance = max(abs(a.value_num), abs(b.value_num), 1.0) * 0.02
        if abs(a.value_num - b.value_num) > tolerance:
            return False
        if ranged_a and ranged_b:
            span = max(abs(a.value_max), abs(b.value_max), 1.0) * 0.02
            return abs(a.value_max - b.value_max) <= span
        return True
    return str(a.value or "").strip().lower() == str(b.value or "").strip().lower()


def group(candidates) -> list[list]:
    """Group candidates that carry the same value."""
    groups: list[list] = []
    for candidate in candidates:
        for existing in groups:
            if same_value(existing[0], candidate):
                existing.append(candidate)
                break
        else:
            groups.append([candidate])
    return groups


def independent_domains(candidates) -> set:
    return {c.domain for c in candidates if c.domain}


def resolve(candidates, rule: str, trusted_order=None) -> Decision:
    """Apply the user's conflict rule to the candidates for one cell."""
    if not candidates:
        return Decision([], [], rule)
    groups = group(candidates)
    if len(groups) == 1:
        return Decision(groups[0], [], rule, "all sources agree")

    trusted_order = [t.lower() for t in (trusted_order or [])]

    def rank(candidate) -> int:
        domain = (candidate.domain or "").lower()
        for index, trusted in enumerate(trusted_order):
            if domain.endswith(trusted):
                return index
        return len(trusted_order) + candidate.tier

    if rule == "majority":
        best = max(groups, key=lambda g: (len(independent_domains(g)), -min(rank(c) for c in g)))
        losers = [c for g in groups if g is not best for c in g]
        return Decision(best, losers, rule, f"{len(independent_domains(best))} domains agree")

    if rule == "trusted_first":
        best = min(groups, key=lambda g: min(rank(c) for c in g))
        losers = [c for g in groups if g is not best for c in g]
        top = min(best, key=rank)
        return Decision(best, losers, rule, f"most trusted source: {top.domain}")

    if rule == "newest":
        best = max(groups, key=lambda g: max((c.fetched_at or "") for c in g))
        losers = [c for g in groups if g is not best for c in g]
        return Decision(best, losers, rule, "newest source wins")

    # keep_all_and_flag (the default): nothing is hidden
    flat = [c for g in groups for c in g]
    return Decision(flat, flat, "keep_all_and_flag",
                    f"{len(groups)} different values kept and flagged")
