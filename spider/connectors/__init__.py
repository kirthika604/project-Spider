"""Optional connectors, listed in spider.yaml under `connectors:`."""

from __future__ import annotations

from .base import Connector, ConnectorResult, ConnectorValue
from .elevation import ElevationConnector
from .gbif import GBIFConnector
from .geocode import GeocodeConnector
from .wikidata import WikidataConnector

REGISTRY = {
    "gbif": GBIFConnector,
    "wikidata": WikidataConnector,
    "elevation": ElevationConnector,
    "geocode": GeocodeConnector,
}


def load(conn, spec) -> list[Connector]:
    """Build the connectors named in spider.yaml; unknown names are skipped."""
    out = []
    for entry in spec.connectors:
        if isinstance(entry, str):
            entry = {"name": entry}
        name = str(entry.get("name", "")).lower()
        if name in REGISTRY:
            out.append(REGISTRY[name](conn, entry))
    return out


def run(conn, spec, on_event=None) -> dict:
    """Ask every connector about every entity, and store what comes back."""
    from ..assemble.confidence import score
    from ..store.db import jdump, now

    on_event = on_event or (lambda *a, **k: None)
    connectors = load(conn, spec)
    summary = {"connectors": [], "values": 0, "aliases": 0, "identifiers": 0,
               "conflicts": 0, "skipped": []}
    if not connectors:
        return summary

    for connector in connectors:
        added = {"values": 0, "aliases": 0, "identifiers": 0, "conflicts": 0}
        for entity_type in spec.entities:
            if not connector.applies_to(entity_type, spec):
                summary["skipped"].append(
                    f"{connector.name}: nothing to ask about '{entity_type}'")
                continue
            for row in conn.execute(
                    "SELECT id, canonical_name FROM entities WHERE type=?",
                    (entity_type,)).fetchall():
                values = _entity_values(conn, row["id"])
                try:
                    result = connector.lookup(entity_type, row["canonical_name"], values)
                except Exception as exc:
                    summary["skipped"].append(f"{connector.name}: {exc}")
                    continue
                if not result.ok:
                    continue
                for value in result.values:
                    if value.kind == "alias":
                        conn.execute(
                            "INSERT OR IGNORE INTO entity_aliases"
                            "(entity_id,alias,language,script,source) VALUES(?,?,?,?,?)",
                            (row["id"], value.value, "", "", connector.name))
                        added["aliases"] += 1
                    elif value.kind == "identifier":
                        conn.execute(
                            "INSERT OR REPLACE INTO ref_authority_ids"
                            "(entity_type,authority,name,identifier) VALUES(?,?,?,?)",
                            (entity_type, connector.name, row["canonical_name"],
                             value.value))
                        added["identifiers"] += 1
                    elif value.kind == "check":
                        if _record_check(conn, spec, connector, row["id"], value):
                            added["conflicts"] += 1
                    else:
                        if _record_value(conn, spec, connector, entity_type,
                                         row["id"], value):
                            added["values"] += 1
                on_event("connector", name=connector.name,
                         entity=row["canonical_name"], note=result.note)
        conn.commit()
        summary["connectors"].append({"name": connector.name, "calls": connector.calls,
                                      "cache_hits": connector.cache_hits, **added})
        for key in ("values", "aliases", "identifiers", "conflicts"):
            summary[key] += added[key]
    del score, jdump, now
    return summary


def _entity_values(conn, entity_id: int) -> dict:
    out = {}
    for row in conn.execute(
            "SELECT name, value, value_num, value_max FROM attributes "
            "WHERE entity_id=? AND status='accepted'", (entity_id,)):
        out[row["name"]] = row["value"]
        if row["value_num"] is not None:
            out[f"{row['name']}__min"] = row["value_num"]
            out[f"{row['name']}__max"] = row["value_max"]
    # A record that carries its own coordinates - a place you listed, or one a
    # geocoder just found - is the best source for them. The places reference
    # is only a fallback, and it is empty unless the project loaded one.
    lat = next((out[k] for k in ("latitude", "lat") if out.get(k) not in (None, "")),
               None)
    lon = next((out[k] for k in ("longitude", "lon", "lng", "long")
                if out.get(k) not in (None, "")), None)
    if lat is None or lon is None:
        place = conn.execute(
            "SELECT lat, lon FROM ref_places p JOIN entities e ON "
            "lower(e.canonical_name)=lower(p.name) WHERE e.id=?",
            (entity_id,)).fetchone()
        if place and place["lat"] is not None:
            lat, lon = place["lat"], place["lon"]
    try:
        if lat is not None and lon is not None:
            out["lat"], out["lon"] = float(lat), float(lon)
    except (TypeError, ValueError):
        pass
    return out


def _record_value(conn, spec, connector, entity_type, entity_id, value) -> bool:
    """A connector value joins the dataset like any other source."""
    from ..assemble.confidence import base_for
    from ..store.db import jdump, now
    if not spec.entity_field(entity_type, value.field):
        return False
    existing = conn.execute(
        "SELECT id, value, origin FROM attributes WHERE entity_id=? AND name=? "
        "AND status='accepted'", (entity_id, value.field)).fetchone()
    if existing and existing["origin"] == "default":
        # a service agreeing with a value we invented proves nothing; replace it
        conn.execute("DELETE FROM attributes WHERE id=?", (existing["id"],))
        existing = None
    if existing and str(existing["value"]).lower() == str(value.value).lower():
        conn.execute(
            "UPDATE attributes SET confidence=MIN(0.99, confidence + 0.15), "
            "lineage=? WHERE id=?",
            (jdump({"confirmed_by": connector.name, "url": value.url,
                    "evidence": value.evidence}), existing["id"]))
        return True
    if existing:
        return False                       # a conflict is handled by the build rules
    conn.execute(
        "INSERT INTO attributes(entity_id,name,value,unit,raw_value,origin,evidence,"
        "tier,confidence,status,source_id,lineage,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (entity_id, value.field, value.value, value.unit, value.value, "extracted",
         value.evidence, connector.tier, base_for(connector.tier), "accepted",
         connector.name, jdump({"connector": connector.name, "url": value.url}), now()))
    return True


def _record_check(conn, spec, connector, entity_id, value) -> bool:
    """A cross-check changes confidence, never the value itself."""
    from ..store.db import jdump, now
    wanted = getattr(connector, "config", {}).get("compare")
    if wanted:
        altitude = conn.execute(
            "SELECT id, name, value, value_num, value_max, confidence FROM attributes "
            "WHERE entity_id=? AND name=? AND status='accepted' LIMIT 1",
            (entity_id, wanted)).fetchone()
    else:
        altitude = conn.execute(
            "SELECT id, name, value, value_num, value_max, confidence FROM attributes "
            "WHERE entity_id=? AND (name LIKE '%altitude%' OR name LIKE '%elevation%') "
            "AND status='accepted' AND value_num IS NOT NULL LIMIT 1",
            (entity_id,)).fetchone()
    if not altitude:
        return False
    measured = float(value.value)
    disagrees = getattr(connector, "disagrees", lambda *a: False)(
        altitude["value_num"], altitude["value_max"], measured)
    if disagrees:
        conn.execute("UPDATE attributes SET confidence=MAX(0.0, confidence - 0.2) "
                     "WHERE id=?", (altitude["id"],))
        conn.execute(
            "INSERT INTO review_queue(kind,target,entity_id,field,reason,detail,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("conflict", f"{altitude['name']} vs elevation API", entity_id,
             altitude["name"],
             f"the elevation service says {measured:.0f} m but the sources say "
             f"{altitude['value']}", jdump({"measured": measured,
                                            "claimed": altitude["value"],
                                            "evidence": value.evidence,
                                            "url": value.url}), now()))
        return True
    conn.execute("UPDATE attributes SET confidence=MIN(0.99, confidence + 0.1) WHERE id=?",
                 (altitude["id"],))
    return False
