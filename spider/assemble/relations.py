"""Relation building: values found together become links (FR-18)."""

from __future__ import annotations

from ..store.db import now


def save(conn, index, relation_candidates, spec) -> int:
    """Write relation rows, resolving both ends to entity ids."""
    allowed = {(r.from_entity, r.name, r.to_entity) for r in spec.relations}
    written = 0
    for rel in relation_candidates:
        if (rel.from_type, rel.name, rel.to_type) not in allowed:
            continue
        left = index.resolve(rel.from_type, rel.from_identity)
        right = index.resolve(rel.to_type, rel.to_identity)
        if not left or not right:
            continue
        confidence = rel.confidence or 0.6
        cursor = conn.execute(
            "INSERT OR IGNORE INTO relations"
            "(from_entity,relation,to_entity,source_page,evidence,confidence,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (left, rel.name, right, rel.page_id, rel.quote[:400], confidence, now()))
        written += cursor.rowcount
    conn.commit()
    return written


def related(conn, entity_id: int, relation: str, direction: str = "out") -> list[int]:
    if direction == "out":
        rows = conn.execute(
            "SELECT DISTINCT to_entity AS other FROM relations "
            "WHERE from_entity=? AND relation=?", (entity_id, relation))
    else:
        rows = conn.execute(
            "SELECT DISTINCT from_entity AS other FROM relations "
            "WHERE to_entity=? AND relation=?", (entity_id, relation))
    return [r["other"] for r in rows]
