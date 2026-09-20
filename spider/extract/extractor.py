"""Turn a stored page into candidate values, each with its evidence.

Two routes fill the schema (FR-16): the rules route (CSS fields captured at
crawl time, regex over the text, and controlled vocabularies) and the AI
route. Every candidate from either route goes through quote proof before it
can become an attribute.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..ref import tables as ref
from ..standardize import names
from . import proof


@dataclass
class Candidate:
    entity_type: str
    identity: str                 # identifying value of the entity on this page
    field: str
    raw_value: str
    quote: str = ""
    unit: str | None = None
    page_id: int | None = None
    url: str = ""
    domain: str = ""
    tier: int = 3
    source_id: str | None = None
    fetched_at: str = ""
    route: str = "rules"          # rules | vocabulary | ai | file | connector
    dom_evidence: bool = False    # read from the page's own markup by a selector
    origin: str = "extracted"
    rejected: str = ""            # reason, when the verification chain drops it
    # filled in later by the standardizer
    value: str | None = None
    value_num: float | None = None
    value_max: float | None = None
    confidence: float = 0.0
    lineage: dict = field(default_factory=dict)


@dataclass
class AliasCandidate:
    """A local, common or alternate name for an entity found on a page (D4)."""
    entity_type: str
    identity: str
    alias: str
    language: str = ""
    source: str = "page"


@dataclass
class RelationCandidate:
    from_type: str
    from_identity: str
    name: str
    to_type: str
    to_identity: str
    quote: str = ""
    page_id: int | None = None
    confidence: float = 0.0


SENTENCE_SPLIT = re.compile(r"(?<=[.!?।])\s+|\n+")


def _tier_of(page_row) -> int:
    """Tier 0 is the user's own data - a real tier, not a missing one."""
    tier = page_row["tier"]
    return 3 if tier is None else int(tier)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text or "") if s.strip()]


_TERMINATORS = (". ", "! ", "? ", "\u0964 ", "\n")


def sentence_with(text: str, value: str) -> str:
    """The sentence on the page that states this value - its evidence quote.

    The value is found first and the sentence grown outwards from it. Splitting
    the page into sentences and then searching them cannot work for a value
    that contains a full stop - "St. Mary's Cafe", "3.5 km", "Dr. Rao" - because
    the split cuts straight through it.
    """
    target = names.normalise(value)
    if not target:
        return ""
    low, needle = text.lower(), target.lower()
    position = low.find(needle)
    if position >= 0:
        end_of_value = position + len(needle)
        start = max((low.rfind(t, 0, position) + len(t) for t in _TERMINATORS
                     if low.rfind(t, 0, position) >= 0), default=0)
        stops = [low.find(t, end_of_value) for t in _TERMINATORS]
        stops = [s for s in stops if s >= 0]
        end = min(stops) + 1 if stops else len(text)
        return text[start:end].strip()[:400]
    numbers = re.findall(r"\d[\d,.]*", needle)
    if numbers:
        for sentence in sentences(text):
            lowered = sentence.lower()
            if all(n in lowered for n in numbers):
                return sentence[:400]
    return ""


class PageExtractor:
    """Runs both routes over one page and returns verified candidates."""

    def __init__(self, conn, spec, ai=None):
        self.conn = conn
        self.spec = spec
        self.ai = ai
        self.rejected: list[tuple[str, str, str]] = []   # a few examples
        self.reject_counts: dict = {}                    # every one, by kind
        self.stats = {"rules": 0, "vocabulary": 0, "ai": 0, "rejected": 0}

    # ------------------------------------------------------------------ run
    def run(self, page_row):
        """Returns (values, relations, aliases) for one page."""
        text = page_row["text"] or ""
        stored_fields = self._stored_fields(page_row["id"])
        primary_type = self._primary_type()
        identity = self._identity_value(page_row, primary_type, stored_fields)

        # A page is only *about* an entity when it states a value for that
        # entity itself. Otherwise the heading is just a site name (an index
        # or a list page), and naming it as a record would invent one - a
        # region or a use found in the page's prose does not make it a
        # subject.
        primary_values = (self._from_rules(page_row, primary_type, identity,
                                           stored_fields, text) if identity else [])
        # `required` means a record is incomplete without the value. A page
        # that lacks one is not a page about this entity at all - it is a
        # listing, an index or a search result - so it yields no record. This
        # is what lets one crawl walk a site's list pages to reach its detail
        # pages without inventing a record from every list.
        if primary_values and not self._has_required(primary_type, primary_values):
            primary_values = []
        candidates: list[Candidate] = list(primary_values)
        relations: list[RelationCandidate] = []
        subject = identity if primary_values else ""
        vocab_candidates, vocab_relations = self._from_vocabularies(
            page_row, primary_type, subject, text)
        candidates += vocab_candidates
        relations += vocab_relations

        if self.ai is not None:
            ai_candidates, ai_relations = self._from_ai(page_row, text)
            candidates += ai_candidates
            relations += ai_relations

        aliases = self._aliases(page_row, primary_type, identity) if primary_values else []
        verified = [c for c in (self._verify(c, text) for c in candidates) if c]
        return verified, relations, aliases

    # -------------------------------------------------------------- helpers
    def _tier(self, page_row) -> int:
        """How much this page's site is trusted, as spider.yaml says *now*.

        The tier is a judgement about a source, not a fact about a page, so it
        is not read back from when the page was crawled. Changing
        `trust_tiers` should take effect on the next build, not force a
        re-crawl. Files and endpoints carry the tier you gave them.
        """
        url = str(page_row["url"] or "")
        if page_row["source_id"] is None and url.startswith(("http://", "https://")):
            return self.spec.tier_for(url)
        return _tier_of(page_row)

    def _has_required(self, entity_type: str, values) -> bool:
        ent = self.spec.entities.get(entity_type)
        if not ent:
            return True
        found = {c.field for c in values}
        missing = [name for name, fld in ent.fields.items()
                   if fld.required and name not in found]
        if missing:
            self.stats["skipped_incomplete"] = self.stats.get("skipped_incomplete", 0) + 1
        return not missing

    def _primary_type(self) -> str:
        return next(iter(self.spec.entities), "")

    def _stored_fields(self, page_id: int) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for row in self.conn.execute(
                "SELECT name, value FROM fields WHERE page_id=?", (page_id,)):
            out.setdefault(row["name"], []).append(row["value"])
        return out

    def _identity_value(self, page_row, entity_type: str, stored_fields) -> str:
        ent = self.spec.entities.get(entity_type)
        if not ent:
            return ""
        for key in (ent.identity or list(ent.fields)[:1]):
            values = stored_fields.get(key)
            if values:
                return names.normalise(values[0])
            fld = ent.fields.get(key)
            if fld:
                found = self._by_rules(fld, page_row["text"] or "")
                if found:
                    return names.normalise(found[0])
        title = (page_row["title"] or "").split("|")[0].split(" - ")[0]
        heading = (page_row["headings"] or "").split("|")[0]
        return names.normalise(heading or title)

    def _by_rules(self, fld, text: str) -> list[str]:
        """Regex rules run over the stored text (CSS rules ran at crawl time)."""
        found = []
        for rule in fld.extract:
            if not str(rule).startswith("regex:"):
                continue
            for match in re.finditer(str(rule)[6:], text, re.IGNORECASE):
                found.append(match.group(0).strip())
                if not fld.multiple:
                    break
        return found

    def _from_rules(self, page_row, entity_type, identity, stored_fields, text):
        out = []
        ent = self.spec.entities[entity_type]
        for fname, fld in ent.fields.items():
            values = list(stored_fields.get(fname, []))
            values += self._by_rules(fld, text)
            if not values:
                continue
            for value in (values if fld.multiple else values[:1]):
                quote = sentence_with(text, value)
                dom = False
                if not quote:
                    # Not a sentence on the page: an attribute (a rating held in
                    # a class, a price in `content`), or text the cleaner drops.
                    # A selector read it from the markup, so the selector is the
                    # evidence - and it is shown as one, not passed off as prose.
                    selector = next((r for r in fld.extract
                                     if not str(r).startswith("regex:")), None)
                    if selector and value in stored_fields.get(fname, []):
                        quote = f"[{selector}] {value}"
                        dom = True
                out.append(Candidate(
                    entity_type=entity_type, identity=identity, field=fname,
                    raw_value=value, quote=quote, dom_evidence=dom,
                    page_id=page_row["id"], url=page_row["url"],
                    domain=page_row["domain"], tier=self._tier(page_row),
                    source_id=page_row["source_id"], fetched_at=page_row["fetched_at"],
                    route="rules"))
                self.stats["rules"] += 1
        return out

    def _from_vocabularies(self, page_row, primary_type, primary_identity, text):
        """Find related entities (regions, uses) by their controlled vocabulary."""
        candidates, relations = [], []
        low_text = text.lower()
        for ename, ent in self.spec.entities.items():
            if ename == primary_type:
                continue
            for fname, fld in ent.fields.items():
                if not fld.vocabulary or fname not in (ent.identity or [fname]):
                    continue
                for term, alias in self._vocabulary_terms(fld.vocabulary):
                    if not re.search(rf"\b{re.escape(alias.lower())}\b", low_text):
                        continue
                    quote = sentence_with(text, alias)
                    if not quote:
                        continue
                    candidates.append(Candidate(
                        entity_type=ename, identity=term, field=fname,
                        raw_value=term, quote=quote, page_id=page_row["id"],
                        url=page_row["url"], domain=page_row["domain"],
                        tier=self._tier(page_row), source_id=page_row["source_id"],
                        fetched_at=page_row["fetched_at"], route="vocabulary"))
                    self.stats["vocabulary"] += 1
                    if primary_identity:
                        for rel in self.spec.relations:
                            if rel.from_entity == primary_type and rel.to_entity == ename:
                                relations.append(RelationCandidate(
                                    from_type=primary_type, from_identity=primary_identity,
                                    name=rel.name, to_type=ename, to_identity=term,
                                    quote=quote, page_id=page_row["id"]))
        return candidates, relations

    def _vocabulary_terms(self, vocabulary: str) -> list[tuple[str, str]]:
        if vocabulary == "places":
            rows = self.conn.execute("SELECT name, aliases FROM ref_places").fetchall()
            pairs = []
            for row in rows:
                pairs.append((row["name"], row["name"]))
                for alias in (row["aliases"] or "").split(";"):
                    if alias.strip():
                        pairs.append((row["name"], alias.strip()))
            return pairs
        return [(r["term"], r["alias"]) for r in self.conn.execute(
            "SELECT term, alias FROM ref_vocab WHERE vocabulary=?", (vocabulary,))] + \
            [(t, t) for t in ref.vocab_terms(self.conn, vocabulary)]

    def _from_ai(self, page_row, text):
        data = self.ai.extract(page_row)
        if not data:
            return [], []
        candidates, relations = [], []
        for record in data.get("records") or []:
            etype = str(record.get("type", "")).strip()
            if etype not in self.spec.entities:
                continue
            ent = self.spec.entities[etype]
            fields = record.get("fields") or {}
            identity = ""
            for key in (ent.identity or list(ent.fields)[:1]):
                cell = fields.get(key) or {}
                if isinstance(cell, dict) and cell.get("value"):
                    identity = names.normalise(cell["value"])
                    break
            if not identity:
                continue
            for fname, cell in fields.items():
                if fname not in ent.fields or not isinstance(cell, dict):
                    continue
                value = cell.get("value")
                if value in (None, "", []):
                    continue
                for single in (value if isinstance(value, list) else [value]):
                    candidates.append(Candidate(
                        entity_type=etype, identity=identity, field=fname,
                        raw_value=str(single), quote=str(cell.get("quote") or ""),
                        unit=cell.get("unit"), page_id=page_row["id"],
                        url=page_row["url"], domain=page_row["domain"],
                        tier=self._tier(page_row), source_id=page_row["source_id"],
                        fetched_at=page_row["fetched_at"], route="ai"))
                    self.stats["ai"] += 1
        for rel in data.get("relations") or []:
            try:
                from_type, from_id = str(rel["from"]).split(":", 1)
                to_type, to_id = str(rel["to"]).split(":", 1)
            except (KeyError, ValueError):
                continue
            if from_type in self.spec.entities and to_type in self.spec.entities:
                relations.append(RelationCandidate(
                    from_type=from_type, from_identity=names.normalise(from_id),
                    name=str(rel.get("name", "related_to")), to_type=to_type,
                    to_identity=names.normalise(to_id),
                    quote=str(rel.get("quote") or ""), page_id=page_row["id"]))
        return candidates, relations

    def _aliases(self, page_row, entity_type: str, identity: str) -> list[AliasCandidate]:
        """Other names the page gives this entity: headings, local names, JSON-LD."""
        out: list[AliasCandidate] = []
        language = (page_row["lang"] or "").split("-")[0]
        seen = {names.key(identity)}

        def add(text: str, source: str = "page") -> None:
            clean = names.normalise(text)
            if not clean or len(clean) > 80:
                return
            marker = names.key(clean)
            if not marker or marker in seen:
                return
            seen.add(marker)
            out.append(AliasCandidate(entity_type, identity, clean, language, source))

        heading = (page_row["headings"] or "").split("|")[0]
        title = (page_row["title"] or "").split("|")[0].split(" - ")[0]
        for text in (heading, title):
            plain = re.sub(r"\(([^)]*)\)", "", text)
            add(plain)
            for inside in re.findall(r"\(([^)]*)\)", text):
                add(inside)

        ent = self.spec.entities.get(entity_type)
        alias_fields = set(ent.aliases_from) if ent else set()
        for field_name in (ent.fields if ent else {}):
            if any(word in field_name for word in ("common_name", "local_name",
                                                   "alias", "other_name")):
                alias_fields.add(field_name)
        for field_name in alias_fields:
            for row in self.conn.execute(
                    "SELECT value FROM fields WHERE page_id=? AND name=?",
                    (page_row["id"], field_name)):
                add(row["value"], "field")

        for row in self.conn.execute(
                "SELECT data FROM structured WHERE page_id=?", (page_row["id"],)):
            try:
                data = json.loads(row["data"])
            except (ValueError, TypeError):
                continue
            for key_name in ("alternateName", "name"):
                value = data.get(key_name)
                for single in (value if isinstance(value, list) else [value]):
                    if isinstance(single, str):
                        add(single, "json-ld")
        return out

    def _verify(self, candidate: Candidate, text: str) -> Candidate | None:
        """Quote proof (section 12, step 3). No quote, no value.

        The proof exists to stop a model inventing a value, so it is applied to
        anything a model or a loose text match produced. A value a CSS selector
        read straight out of the page's markup is deterministic and carries its
        selector as evidence instead.
        """
        if candidate.dom_evidence and candidate.route == "rules":
            return candidate
        kept, reason = proof.check(candidate.raw_value, candidate.quote, text)
        if kept:
            return candidate
        if candidate.route != "ai":
            # a rules hit with no readable sentence still has the page as evidence
            if candidate.quote and proof.quote_on_page(candidate.quote, text):
                return candidate
            if not candidate.quote:
                fallback = sentence_with(text, candidate.raw_value)
                if fallback:
                    candidate.quote = fallback
                    return candidate
        candidate.rejected = reason
        what = f"{candidate.entity_type}.{candidate.field}"
        key = (what, reason[:70])
        self.reject_counts[key] = self.reject_counts.get(key, 0) + 1
        if len(self.rejected) < 500:
            self.rejected.append((candidate.url, f"{what} = {candidate.raw_value[:40]}",
                                  reason))
        self.stats["rejected"] += 1
        return None
