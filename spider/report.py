"""Coverage, conflicts, source health, gaps and the gold-set accuracy test."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .standardize import names, units
from .store.db import jload


@dataclass
class Coverage:
    entity_type: str
    field: str
    filled: int
    total: int
    derived: int = 0
    defaults: int = 0

    @property
    def percent(self) -> float:
        return round(100.0 * self.filled / self.total, 1) if self.total else 0.0

    @property
    def found(self) -> int:
        """Cells a source actually filled, not counting declared defaults."""
        return max(0, self.filled - self.defaults)


@dataclass
class Report:
    entities: dict[str, int] = field(default_factory=dict)
    coverage: list[Coverage] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    low_confidence: list[dict] = field(default_factory=list)
    suggestions: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    gold: dict | None = None
    standardization: dict = field(default_factory=dict)


def coverage(conn, spec) -> list[Coverage]:
    out = []
    for entity_type, _ent in spec.entities.items():
        total = conn.execute("SELECT COUNT(*) c FROM entities WHERE type=?",
                             (entity_type,)).fetchone()["c"]
        from .store.normalize import field_names
        for field_name in field_names(spec, entity_type):
            row = conn.execute(
                "SELECT COUNT(DISTINCT a.entity_id) filled, "
                "SUM(CASE WHEN a.origin IN ('derived','inferred') THEN 1 ELSE 0 END) der, "
                "SUM(CASE WHEN a.origin = 'default' THEN 1 ELSE 0 END) def "
                "FROM attributes a JOIN entities e ON e.id=a.entity_id "
                "WHERE e.type=? AND a.name=? AND a.status='accepted'",
                (entity_type, field_name)).fetchone()
            out.append(Coverage(entity_type, field_name, row["filled"] or 0, total,
                                row["der"] or 0, row["def"] or 0))
    return out


def gaps(conn, spec) -> list[dict]:
    """Empty cells, which is what `spider fill` goes looking for (FR-20, D3)."""
    found = []
    for item in coverage(conn, spec):
        if item.total and item.filled < item.total:
            missing_rows = conn.execute(
                "SELECT e.id, e.canonical_name FROM entities e WHERE e.type=? AND e.id "
                "NOT IN (SELECT entity_id FROM attributes WHERE name=? AND status='accepted')",
                (item.entity_type, item.field)).fetchall()
            found.append({
                "entity_type": item.entity_type, "field": item.field,
                "missing": len(missing_rows), "total": item.total,
                "examples": [r["canonical_name"] for r in missing_rows[:8]],
                "entity_ids": [r["id"] for r in missing_rows],
            })
    return sorted(found, key=lambda g: -g["missing"])


def source_health(conn) -> list[dict]:
    """Which sources are actually contributing (section 16).

    A source that says the same thing as a more trusted one does not own the
    stored row, but it is the reason that row is believed - so agreement is
    counted beside the values a source gave outright.
    """
    rows = conn.execute(
        "SELECT p.domain AS domain, COUNT(DISTINCT p.id) AS pages, "
        "  COUNT(a.id) AS values_given, "
        "  SUM(CASE WHEN a.status='review' THEN 1 ELSE 0 END) AS in_review, "
        "  SUM(CASE WHEN a.status='superseded' THEN 1 ELSE 0 END) AS lost_conflicts, "
        "  MIN(p.tier) AS tier, AVG(a.confidence) AS avg_confidence "
        "FROM pages p LEFT JOIN attributes a ON a.source_page = p.id "
        "GROUP BY p.domain").fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["avg_confidence"] = round(item["avg_confidence"] or 0, 2)
        agreed = conn.execute(
            "SELECT COUNT(*) c FROM attributes a "
            "LEFT JOIN pages p ON p.id = a.source_page "
            "WHERE a.status='accepted' AND a.lineage LIKE ? "
            "AND (p.domain IS NULL OR p.domain != ?)",
            (f'%"{item["domain"]}"%', item["domain"])).fetchone()["c"]
        item["values_agreed"] = agreed
        out.append(item)
    return sorted(out, key=lambda r: (-(r["values_given"] or 0),
                                      -(r["values_agreed"] or 0)))


def review_items(conn, kind: str | None = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM review_queue WHERE status='open'"
    params: list = []
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    out = []
    for row in conn.execute(sql, params):
        item = dict(row)
        item["detail"] = jload(item.get("detail"), {})
        out.append(item)
    return out


def build_report(conn, spec, gold_path: Path | None = None) -> Report:
    report = Report()
    report.entities = {t: conn.execute(
        "SELECT COUNT(*) c FROM entities WHERE type=?", (t,)).fetchone()["c"]
        for t in spec.entities}
    report.coverage = coverage(conn, spec)
    report.conflicts = review_items(conn, "conflict")
    report.low_confidence = review_items(conn, "low_confidence")
    report.suggestions = review_items(conn, "suggestion")
    report.rejected = review_items(conn, "sanity") + review_items(conn, "strict_reject")
    report.sources = source_health(conn)
    report.gaps = gaps(conn, spec)
    row = conn.execute(
        "SELECT changes FROM build_reports ORDER BY id DESC LIMIT 1").fetchone()
    report.standardization = jload(row["changes"], {}) if row else {}
    if gold_path:
        report.gold = check_gold(conn, spec, gold_path)
    return report


# --------------------------------------------------------- accuracy testing
def check_gold(conn, spec, path: Path) -> dict:
    """Measured correctness against facts the user is sure about (section 12)."""
    path = Path(path)
    if not path.exists():
        return {"error": f"{path} not found"}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = [dict(r) for r in csv.DictReader(handle)]

    result = {"total": len(rows), "found": 0, "correct": 0, "wrong": 0, "missing": 0,
              "high_confidence_total": 0, "high_confidence_correct": 0, "details": []}
    for row in rows:
        clean = {k.strip().lower(): (v.strip() if isinstance(v, str) else v)
                 for k, v in row.items() if k}
        entity_name = clean.get("entity") or clean.get("name") or clean.get("plant")
        field_name = clean.get("field") or clean.get("column")
        expected = clean.get("value") or clean.get("expected")
        entity_type = clean.get("type") or next(iter(spec.entities), "")
        if not entity_name or not field_name:
            continue
        entity = _find_entity(conn, entity_type, entity_name)
        if not entity:
            result["missing"] += 1
            result["details"].append({"entity": entity_name, "field": field_name,
                                      "expected": expected, "got": None,
                                      "verdict": "not found"})
            continue
        attribute = conn.execute(
            "SELECT value, value_num, value_max, confidence, origin FROM attributes "
            "WHERE entity_id=? AND name=? AND status='accepted' "
            "ORDER BY confidence DESC LIMIT 1", (entity, field_name)).fetchone()
        if not attribute:
            result["missing"] += 1
            result["details"].append({"entity": entity_name, "field": field_name,
                                      "expected": expected, "got": None,
                                      "verdict": "not found"})
            continue
        result["found"] += 1
        got = attribute["value"]
        correct = _same(expected, got)
        confidence = attribute["confidence"] or 0
        if confidence >= 0.8:
            result["high_confidence_total"] += 1
            result["high_confidence_correct"] += int(correct)
        result["correct" if correct else "wrong"] += 1
        result["details"].append({"entity": entity_name, "field": field_name,
                                  "expected": expected, "got": got,
                                  "confidence": confidence,
                                  "verdict": "correct" if correct else "wrong"})
    result["coverage_percent"] = round(100 * result["found"] / result["total"], 1) \
        if result["total"] else 0.0
    result["accuracy_percent"] = round(100 * result["correct"] / result["found"], 1) \
        if result["found"] else 0.0
    result["high_confidence_accuracy"] = round(
        100 * result["high_confidence_correct"] / result["high_confidence_total"], 1) \
        if result["high_confidence_total"] else 0.0
    return result


def _find_entity(conn, entity_type: str, name: str):
    row = conn.execute(
        "SELECT id FROM entities WHERE type=? AND lower(canonical_name)=lower(?)",
        (entity_type, name)).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        "SELECT e.id FROM entities e JOIN entity_aliases a ON a.entity_id=e.id "
        "WHERE e.type=? AND lower(a.alias)=lower(?)", (entity_type, name)).fetchone()
    if row:
        return row["id"]
    best, best_score = None, 0.0
    for candidate in conn.execute("SELECT id, canonical_name FROM entities WHERE type=?",
                                  (entity_type,)):
        score = names.similarity(candidate["canonical_name"], name)
        if score > best_score:
            best, best_score = candidate["id"], score
    return best if best_score >= 0.9 else None


def _same(expected, got) -> bool:
    if expected is None or got is None:
        return False
    expected_low, got_low = str(expected).strip().lower(), str(got).strip().lower()
    if expected_low == got_low:
        return True
    expected_numbers = [units.parse_number(p) for p in str(expected).split("-")]
    got_numbers = [units.parse_number(p) for p in str(got).split("-")]
    expected_numbers = [n for n in expected_numbers if n is not None]
    got_numbers = [n for n in got_numbers if n is not None]
    if expected_numbers and len(expected_numbers) == len(got_numbers):
        return all(abs(a - b) <= max(abs(a), 1.0) * 0.05
                   for a, b in zip(expected_numbers, got_numbers))
    return names.similarity(expected_low, got_low) >= 0.92


# ------------------------------------------------------------------ explain
def explain(conn, spec, entity_name: str, field_name: str) -> dict:
    """The full chain from a value back to its source pages (FR-28)."""
    entity = None
    for entity_type in spec.entities:
        entity = _find_entity(conn, entity_type, entity_name)
        if entity:
            break
    if not entity:
        return {"error": f"no record called '{entity_name}'"}
    row = conn.execute("SELECT type, canonical_name FROM entities WHERE id=?",
                       (entity,)).fetchone()
    attributes = [dict(r) for r in conn.execute(
        "SELECT * FROM attributes WHERE entity_id=? AND name=? "
        "ORDER BY CASE status WHEN 'accepted' THEN 0 ELSE 1 END, confidence DESC",
        (entity, field_name))]
    out = {"entity": row["canonical_name"], "type": row["type"], "field": field_name,
           "values": []}
    for attribute in attributes:
        lineage = jload(attribute["lineage"], {})
        item = {
            "value": attribute["value"], "unit": attribute["unit"],
            "origin": attribute["origin"], "confidence": attribute["confidence"],
            "status": attribute["status"], "raw_value": attribute["raw_value"],
            "evidence": attribute["evidence"], "lineage": lineage, "sources": [],
        }
        if attribute["source_page"]:
            page = conn.execute(
                "SELECT url, domain, tier, fetched_at FROM pages WHERE id=?",
                (attribute["source_page"],)).fetchone()
            if page:
                item["sources"].append(dict(page))
        for source in lineage.get("sources", []):
            if source.get("url") not in [s.get("url") for s in item["sources"]]:
                item["sources"].append(source)
        if attribute["origin"] in ("derived", "inferred"):
            item["inputs"] = []
            for input_id in lineage.get("inputs", []):
                parent = conn.execute(
                    "SELECT a.name, a.value, a.unit, a.confidence, a.evidence, p.url "
                    "FROM attributes a LEFT JOIN pages p ON p.id=a.source_page "
                    "WHERE a.id=?", (input_id,)).fetchone()
                if parent:
                    item["inputs"].append(dict(parent))
        out["values"].append(item)
    return out
