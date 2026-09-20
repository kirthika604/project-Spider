"""Self-healing extractors (D6).

A CSS rule stops matching when a site changes its layout. Spider notices
which rules have gone quiet, then re-learns a selector from pages where it
already knows the answer - the value it stored last time, or one the user
supplies - and offers the new rule for approval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from ..standardize.names import key, normalise
from .parser import _soup


@dataclass
class BrokenRule:
    entity_type: str
    field: str
    rule: str
    pages_tried: int
    pages_matched: int
    last_seen: str | None = None

    @property
    def dead(self) -> bool:
        return self.pages_matched == 0


@dataclass
class Relearned:
    entity_type: str
    field: str
    old_rule: str
    new_rule: str
    matched: int
    tried: int
    samples: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        return round(self.matched / self.tried, 2) if self.tried else 0.0


def health(conn, spec) -> list[BrokenRule]:
    """Which CSS rules matched nothing on the pages that were crawled."""
    out = []
    for entity_type, ent in spec.entities.items():
        for field_name, fld in ent.fields.items():
            for rule in fld.extract:
                if str(rule).startswith("regex:"):
                    continue
                tried = conn.execute(
                    "SELECT COUNT(*) c FROM pages WHERE relevance > 0").fetchone()["c"]
                matched = conn.execute(
                    "SELECT COUNT(DISTINCT page_id) c FROM fields WHERE name=?",
                    (field_name,)).fetchone()["c"]
                last = conn.execute(
                    "SELECT MAX(p.fetched_at) d FROM fields f JOIN pages p "
                    "ON p.id=f.page_id WHERE f.name=?", (field_name,)).fetchone()["d"]
                out.append(BrokenRule(entity_type, field_name, str(rule),
                                      tried, matched, last))
    return out


def _candidate_selectors(node) -> list[str]:
    """Selectors that could identify this element, most specific first."""
    out = []
    classes = [c for c in (node.get("class") or []) if not c.isdigit()]
    if node.get("id"):
        out.append(f"#{node['id']}")
    for single in classes:
        out.append(f"{node.name}.{single}")
        out.append(f".{single}")
    if classes:
        out.append("." + ".".join(classes))
    if node.get("itemprop"):
        out.append(f"[itemprop='{node['itemprop']}']")
    if node.get("data-field"):
        out.append(f"[data-field='{node['data-field']}']")

    parent = node.parent
    if parent is not None and getattr(parent, "get", None):
        parent_classes = [c for c in (parent.get("class") or []) if not c.isdigit()]
        for single in parent_classes[:2]:
            out.append(f".{single} {node.name}")
        if parent.get("id"):
            out.append(f"#{parent['id']} {node.name}")
    out.append(node.name)
    seen, unique = set(), []
    for selector in out:
        if selector not in seen:
            seen.add(selector)
            unique.append(selector)
    return unique


def _specificity(selector: str) -> int:
    """An id beats a class, a class beats a bare tag name."""
    if "#" in selector:
        return 3
    if "[" in selector:
        return 2
    if "." in selector:
        return 1
    return 0


def _holds(node, wanted: str) -> bool:
    text = normalise(node.get_text(" ", strip=True))
    if not text or len(text) > 400:
        return False
    if key(wanted) and key(wanted) in key(text):
        return True
    numbers = re.findall(r"\d[\d,.]*", wanted)
    return bool(numbers) and all(n in text for n in numbers)


def relearn(conn, spec, entity_type: str, field_name: str, html_by_url: dict,
            known: dict) -> Relearned | None:
    """Find a selector that picks the known value out of each sample page.

    `html_by_url` is the saved HTML per page, `known` the value each page
    should yield (from what Spider stored earlier, or from the user).
    """
    scores: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    for url, html in html_by_url.items():
        wanted = known.get(url)
        if not wanted or not html:
            continue
        soup = _soup(html)
        for node in soup.find_all(True):
            if not _holds(node, str(wanted)):
                continue
            if node.find(True) and any(_holds(child, str(wanted))
                                       for child in node.find_all(True)):
                continue                       # prefer the innermost element
            for selector in _candidate_selectors(node):
                try:
                    picked = soup.select(selector)
                except Exception:
                    continue
                if not picked or len(picked) > 5:
                    continue
                # a rule yields its first match, so that is what must hold the
                # value - `span` matching a label first is no use
                if not _holds(picked[0], str(wanted)):
                    continue
                scores[selector] = scores.get(selector, 0) + 1
                examples.setdefault(selector, []).append(
                    normalise(picked[0].get_text(" ", strip=True))[:60])
    if not scores:
        return None
    tried = len([u for u in html_by_url if known.get(u)])
    best = max(scores, key=lambda s: (scores[s], _specificity(s), -len(s)))
    old = ""
    ent = spec.entities.get(entity_type)
    if ent and field_name in ent.fields and ent.fields[field_name].extract:
        old = str(ent.fields[field_name].extract[0])
    if best == old:
        return None
    return Relearned(entity_type, field_name, old, best, scores[best], tried,
                     examples.get(best, [])[:3])


def known_values(conn, field_name: str, limit: int = 5) -> dict:
    """Pages where this field once had a value, and the value it had.

    Spider stores cleaned text, not markup, and a selector can only be learned
    from markup - so these URLs are fetched again. That is the right thing
    anyway: the layout to learn is the one the site has now.
    """
    rows = conn.execute(
        "SELECT p.url, a.value, a.raw_value FROM attributes a JOIN pages p "
        "ON p.id=a.source_page WHERE a.name=? AND p.url LIKE 'http%' "
        "ORDER BY a.confidence DESC LIMIT ?", (field_name, limit)).fetchall()
    known = {r["url"]: (r["raw_value"] or r["value"]) for r in rows}
    if known:
        return known
    # the rule already stopped working, so fall back to what these pages said
    # the last time it did
    history = conn.execute(
        "SELECT url, value, raw_value FROM value_history WHERE field=? "
        "ORDER BY seen_at DESC LIMIT ?", (field_name, limit)).fetchall()
    return {r["url"]: (r["raw_value"] or r["value"]) for r in history}


def fetch_samples(urls, delay: float = 1.0, on_event=None) -> dict:
    """Fetch each sample page as it is today, politely."""
    from ..crawl.fetch import fetch
    from ..crawl.robots import Politeness
    politeness = Politeness(default_delay=delay)
    out = {}
    for url in urls:
        if not politeness.allowed(url):
            if on_event:
                on_event(url, "blocked by robots.txt")
            continue
        politeness.wait(url)
        got = fetch(url)
        if got.ok:
            out[url] = got.html
        elif on_event:
            on_event(url, got.error or f"HTTP {got.status}")
    return out


def apply_to_spec(spec, relearned: Relearned) -> dict:
    """Write the new rule into spider.yaml, keeping the old one as a fallback."""
    entities = spec.raw.setdefault("entities", {})
    entity = entities.setdefault(relearned.entity_type, {})
    fields = entity.setdefault("fields", {})
    body = fields.setdefault(relearned.field, {})
    if isinstance(body, str):
        body = {"type": body}
        fields[relearned.field] = body
    rules = body.get("extract") or []
    if isinstance(rules, str):
        rules = [rules]
    rules = [relearned.new_rule] + [r for r in rules if r != relearned.new_rule]
    body["extract"] = rules
    body["relearned_on"] = __import__("time").strftime("%Y-%m-%d")

    from ..spec import FieldSpec
    spec.entities[relearned.entity_type].fields[relearned.field] = FieldSpec.parse(
        relearned.field, body)
    return body
