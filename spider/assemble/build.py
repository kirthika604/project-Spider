"""`spider build`: turn stored pages into the assembled dataset.

Order (section 6, assembly layer, and section 12's chain): extract candidates
with evidence, standardize them, check sanity, merge entities, apply the
conflict rule, score confidence, write attributes, then derive.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..derive.engine import DeriveEngine
from ..extract import sanity
from ..extract.extractor import PageExtractor
from ..standardize import conflicts
from ..standardize.engine import Standardizer
from ..store.db import jdump, now
from . import relations as rel_store
from .confidence import score
from .entities import EntityIndex


@dataclass
class BuildReport:
    pages_read: int = 0
    candidates: int = 0
    rejected_quote: int = 0
    rejected_sanity: int = 0
    rejected_vocab: int = 0
    attributes: int = 0
    entities: int = 0
    relations: int = 0
    aliases: int = 0
    conflicts: int = 0
    low_confidence: int = 0
    derived: int = 0
    defaults: int = 0
    derive_errors: list[str] = field(default_factory=list)
    described: list[str] = field(default_factory=list)
    standardization: dict = field(default_factory=dict)
    merges: list[str] = field(default_factory=list)
    rejections: list[tuple] = field(default_factory=list)
    ai_calls: int = 0
    ai_cache_hits: int = 0
    connectors: dict = field(default_factory=dict)
    connector_errors: list[str] = field(default_factory=list)
    normal_form: str = "3NF"
    passed_check: bool = True
    check_problems: list[str] = field(default_factory=list)
    decisions_applied: int = 0


def build(conn, spec, *, use_ai: bool = True, limit: int | None = None,
          page_ids=None, use_connectors: bool = True, on_event=None) -> BuildReport:
    on_event = on_event or (lambda *a, **k: None)
    report = BuildReport(normal_form=spec.storage.normal_form)

    ai = None
    if use_ai:
        from ..extract import ai as ai_module
        if ai_module.available():
            ai = ai_module.AIExtractor(conn, spec, max_calls=spec.sources.max_ai_pages)

    # the old rows go first: the index must not cache ids that are about to
    # be deleted
    _clear_extracted(conn)

    extractor = PageExtractor(conn, spec, ai=ai)
    standardizer = Standardizer(conn, spec)
    index = EntityIndex(conn, spec)

    if page_ids:                      # a preview builds from its own sample only
        marks = ",".join("?" for _ in page_ids)
        pages = conn.execute(
            f"SELECT * FROM pages WHERE relevance > 0 AND id IN ({marks}) "
            f"ORDER BY tier, id", list(page_ids)).fetchall()
    else:
        query = "SELECT * FROM pages WHERE relevance > 0 ORDER BY tier, id"
        if limit:
            query += f" LIMIT {int(limit)}"
        pages = conn.execute(query).fetchall()

    # ------------------------------------------- 1. extract and standardize
    cells: dict[tuple[int, str], list] = {}
    all_relations, all_aliases = [], []
    for page in pages:
        report.pages_read += 1
        candidates, relation_candidates, alias_candidates = extractor.run(page)
        all_relations.extend(relation_candidates)
        all_aliases.extend(alias_candidates)
        on_event("page", url=page["url"], values=len(candidates))
        for candidate in candidates:
            report.candidates += 1
            standard = standardizer.standardize(candidate.entity_type,
                                                candidate.field, candidate.raw_value)
            if standard.rejected:
                report.rejected_vocab += 1
                report.rejections.append((candidate.url, f"{candidate.entity_type}."
                                          f"{candidate.field}", standard.reason))
                _queue(conn, "strict_reject", f"{candidate.entity_type}.{candidate.field}",
                       None, candidate.field, standard.reason,
                       {"value": candidate.raw_value, "url": candidate.url})
                continue
            field_spec = spec.entity_field(candidate.entity_type, candidate.field)
            passes, reason = sanity.check(field_spec, standard)
            if not passes:
                report.rejected_sanity += 1
                report.rejections.append((candidate.url, f"{candidate.entity_type}."
                                          f"{candidate.field}", reason))
                _queue(conn, "sanity", f"{candidate.entity_type}.{candidate.field}",
                       None, candidate.field, reason,
                       {"value": candidate.raw_value, "url": candidate.url,
                        "quote": candidate.quote})
                continue
            candidate.value = standard.value
            candidate.value_num = standard.value_num
            candidate.value_max = standard.value_max
            candidate.unit = standard.unit or candidate.unit
            entity_id = index.resolve(candidate.entity_type, candidate.identity)
            if not entity_id:
                continue
            cells.setdefault((entity_id, candidate.field), []).append(candidate)

    for alias in all_aliases:                     # local and common names (D4)
        entity_id = index.resolve(alias.entity_type, alias.identity)
        if entity_id:
            index.add_alias(entity_id, alias.alias, alias.language, alias.source)
    conn.commit()

    report.rejected_quote = extractor.stats["rejected"]
    report.standardization = standardizer.summary()
    report.merges = index.merges
    if ai is not None:
        report.ai_calls, report.ai_cache_hits = ai.calls, ai.cache_hits

    # ----------------------------------------- 2. conflicts and confidence
    for (entity_id, field_name), candidates in cells.items():
        entity_type = conn.execute("SELECT type FROM entities WHERE id=?",
                                   (entity_id,)).fetchone()["type"]
        field_spec = spec.entity_field(entity_type, field_name)
        multiple = bool(field_spec and field_spec.multiple)
        rule = spec.conflict_rule(entity_type, field_name)

        if multiple:
            for group in conflicts.group(candidates):
                _write_attribute(conn, entity_id, field_name, group, False, report, spec)
            continue

        decision = conflicts.resolve(candidates, rule, spec.sources.trusted_order)
        groups = conflicts.group(decision.winners)
        disagreement = len(conflicts.group(candidates)) > 1
        if disagreement:
            report.conflicts += 1
            _queue(conn, "conflict", f"{entity_type}.{field_name}", entity_id, field_name,
                   f"sources disagree ({decision.note})",
                   {"rule": rule,
                    "options": [{"value": g[0].value,
                                 "sources": [{"url": c.url, "domain": c.domain,
                                              "tier": c.tier, "quote": c.quote[:200]}
                                             for c in g]}
                                for g in conflicts.group(candidates)]})
        for group in groups:
            _write_attribute(conn, entity_id, field_name, group, disagreement, report, spec)
        for loser in decision.flagged:
            if rule != "keep_all_and_flag":
                _mark_superseded(conn, entity_id, field_name, loser, decision.rule)

    # --------------------------------------------------------- 3. relations
    report.relations = rel_store.save(conn, index, all_relations, spec)
    report.entities = conn.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"]
    report.aliases = conn.execute(
        "SELECT COUNT(*) c FROM entity_aliases").fetchone()["c"]

    # ------------------------------------------- 4. connectors, before deriving
    # A connector answers about a record, so it can only run once the records
    # exist - and its values must land before anything is calculated from
    # them. Putting it in the build removes an ordering nobody should have to
    # know about. Answers are cached, so a rebuild asks for nothing.
    if spec.connectors and use_connectors:
        from ..connectors import run as run_connectors
        try:
            report.connectors = run_connectors(conn, spec, on_event=on_event)
        except Exception as exc:                  # a service is never fatal
            report.connector_errors.append(f"{type(exc).__name__}: {exc}"[:200])

    # -------------------------------------------------------- 5. derivations
    if spec.derived and spec.derive_policy != "off":
        derive_report = DeriveEngine(conn, spec).run()
        report.derived = derive_report.written
        report.derive_errors = derive_report.errors
        report.described = derive_report.described

    _remember(conn)

    # ---------------------------------------------------------- 6. defaults
    report.defaults = _apply_defaults(conn, spec)

    # ---------------------------------------------------------- 7. decisions
    # A rebuild recomputes the queue from the pages, so the decisions a user
    # made in `spider review` are applied to the fresh items instead of
    # asking the same question twice (section 13, stage 4).
    from ..review import reapply
    report.decisions_applied = reapply(conn)

    report.attributes = conn.execute(
        "SELECT COUNT(*) c FROM attributes WHERE status='accepted'").fetchone()["c"]
    report.low_confidence = conn.execute(
        "SELECT COUNT(*) c FROM attributes WHERE status='review'").fetchone()["c"]

    conn.commit()
    return report


# ------------------------------------------------------------------ helpers
def _apply_defaults(conn, spec) -> int:
    """Fill a cell a field declares a `default:` for, when nothing found one.

    This runs last, so a default never displaces something a page actually
    said or a formula worked out. It is stored as its own origin with no
    confidence, it is not evidence, and nothing can be confirmed by it.
    """
    from ..standardize.units import parse_number

    written = 0
    for entity_type, ent in spec.entities.items():
        for field_name, fld in ent.fields.items():
            if fld.default is None or fld.multiple or fld.required:
                continue
            missing = conn.execute(
                "SELECT e.id FROM entities e WHERE e.type=? AND e.id NOT IN "
                "(SELECT entity_id FROM attributes WHERE name=? "
                " AND status IN ('accepted','review'))",
                (entity_type, field_name)).fetchall()
            for row in missing:
                value = fld.default
                conn.execute(
                    "INSERT INTO attributes(entity_id,name,value,value_num,unit,"
                    "raw_value,origin,evidence,tier,confidence,status,lineage,"
                    "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (row["id"], field_name, str(value), parse_number(value),
                     fld.unit, None, "default",
                     f"declared in spider.yaml as the default for {field_name}",
                     None, 0.0, "accepted",
                     jdump({"from": "spider.yaml", "field": field_name,
                            "note": "no source states this; it is not evidence"}),
                     now()))
                written += 1
    conn.commit()
    return written


def _remember(conn) -> None:
    """Keep what each page gave, so a rule that later breaks can be re-learned."""
    conn.execute(
        "INSERT OR REPLACE INTO value_history(url, field, value, raw_value, seen_at) "
        "SELECT p.url, a.name, a.value, a.raw_value, ? FROM attributes a "
        "JOIN pages p ON p.id = a.source_page "
        "WHERE a.origin = 'extracted' AND a.status IN ('accepted', 'review') "
        "AND p.url LIKE 'http%'", (now(),))
    conn.commit()


def _clear_extracted(conn) -> None:
    """A build always recomputes from the pages, so old rows go first.

    Entities go too: they are derived from the pages like everything else, and
    keeping them would leave records behind from an earlier schema.
    """
    conn.execute("DELETE FROM attributes")
    conn.execute("DELETE FROM relations")
    conn.execute("DELETE FROM entity_aliases")
    conn.execute("DELETE FROM entities")
    conn.execute("DELETE FROM review_queue WHERE kind != 'suggestion' AND status='open'")
    conn.commit()


def _write_attribute(conn, entity_id, field_name, group, disagreement, report, spec):
    best = min(group, key=lambda c: c.tier)
    label = f"{best.entity_type}.{field_name}"
    confidence = score(group, disagreement=disagreement, origin=best.origin)
    status = "accepted"
    if confidence < spec.standardize.min_confidence:
        status = "review"
        _queue(conn, "low_confidence", label, entity_id, field_name,
               f"confidence {confidence} is below min_confidence "
               f"{spec.standardize.min_confidence}",
               {"value": best.value, "sources": [c.url for c in group]})
    conn.execute(
        "INSERT INTO attributes(entity_id,name,value,value_num,value_max,unit,raw_value,"
        "origin,source_page,source_id,evidence,tier,confidence,status,lineage,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (entity_id, field_name, best.value, best.value_num, best.value_max, best.unit,
         best.raw_value, best.origin, best.page_id, best.source_id, best.quote[:400],
         best.tier, confidence, status,
         jdump({"agreeing_domains": sorted({c.domain for c in group if c.domain}),
                "route": best.route,
                "sources": [{"url": c.url, "tier": c.tier, "quote": c.quote[:200]}
                            for c in group]}),
         now()))
    if status == "accepted":
        report.attributes += 0        # counted at the end from the table


def _mark_superseded(conn, entity_id, field_name, candidate, rule) -> None:
    conn.execute(
        "INSERT INTO attributes(entity_id,name,value,value_num,unit,raw_value,origin,"
        "source_page,evidence,tier,confidence,status,lineage,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (entity_id, field_name, candidate.value, candidate.value_num, candidate.unit,
         candidate.raw_value, candidate.origin, candidate.page_id,
         candidate.quote[:400], candidate.tier, 0.0, "superseded",
         jdump({"reason": f"conflict rule '{rule}' chose another value",
                "url": candidate.url}), now()))


def _queue(conn, kind, target, entity_id, field_name, reason, detail) -> None:
    conn.execute(
        "INSERT INTO review_queue(kind,target,entity_id,field,reason,detail,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (kind, target, entity_id, field_name, reason, jdump(detail), now()))
