"""The reference database: units, places, vocabularies, authority ids, ranks."""

from __future__ import annotations

import csv
from pathlib import Path

from ..standardize.names import key, normalise

# Loaded into every project: units are the same everywhere.
SEED_DIR = Path(__file__).parent / "seed"
# Bundled domain packs.  They are opt-in: a project about planets, transport
# or anything else must never inherit plant-specific locations or categories.
PRESET_DIR = Path(__file__).parent / "presets"

LOADERS = {
    "units": ("ref_units", ["quantity", "unit", "base_unit", "factor", "offset"]),
    "places": ("ref_places", ["code", "name", "level", "parent", "aliases", "lat", "lon"]),
    "categories": ("ref_vocab", ["vocabulary", "term", "alias"]),
    "vocab": ("ref_vocab", ["vocabulary", "term", "alias"]),
    "aliases": ("ref_authority_ids", ["entity_type", "authority", "name", "identifier"]),
    "authority": ("ref_authority_ids", ["entity_type", "authority", "name", "identifier"]),
    "sources": ("ref_source_rank", ["domain", "tier", "note"]),
}


def guess_kind(path: Path, header: list[str]) -> str | None:
    stem = path.stem.lower()
    for kind in LOADERS:
        if kind in stem:
            return kind
    for kind, (_table, columns) in LOADERS.items():
        if set(columns[:3]).issubset({h.strip().lower() for h in header}):
            return kind
    return None


def load_csv(conn, path: Path, kind: str | None = None) -> tuple[str, int]:
    """Load one reference CSV. Returns (kind, rows loaded)."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        header = reader.fieldnames or []
    kind = kind or guess_kind(path, header)
    if kind not in LOADERS:
        raise ValueError(
            f"cannot tell what {path.name} holds - name it units.csv, places.csv, "
            f"categories.csv, aliases.csv or sources.csv, or pass --kind")
    table, columns = LOADERS[kind]
    placeholders = ",".join("?" for _ in columns)
    count = 0
    for row in rows:
        clean = {k.strip().lower(): (v.strip() if isinstance(v, str) else v)
                 for k, v in row.items() if k}
        values = [clean.get(c) for c in columns]
        if all(v in (None, "") for v in values):
            continue
        conn.execute(f"INSERT OR REPLACE INTO {table}({','.join(columns)}) "
                     f"VALUES({placeholders})", values)
        count += 1
        if kind in ("places",) and clean.get("aliases"):
            pass                                   # aliases are read from the column
    conn.commit()
    return kind, count


def load_seeds(conn) -> dict[str, int]:
    """Load only universal reference data for a new project.

    Subject-specific places, vocabularies and aliases are intentionally not
    loaded here.  Select a bundled pack with ``spider ref load --preset`` or
    load the project's own CSVs, so extraction is not biased toward one demo
    domain.
    """
    loaded = {}
    for path in sorted(SEED_DIR.glob("*.csv")):
        kind, count = load_csv(conn, path)
        loaded[kind] = loaded.get(kind, 0) + count
    return loaded


def presets() -> dict[str, Path]:
    """The reference sets a project can ask for by name."""
    if not PRESET_DIR.exists():
        return {}
    return {folder.name: folder for folder in sorted(PRESET_DIR.iterdir())
            if folder.is_dir()}


def load_preset(conn, name: str) -> dict[str, int]:
    """Load one named set of reference data into this project."""
    folder = presets().get(name)
    if folder is None:
        available = ", ".join(presets()) or "none are bundled"
        raise ValueError(f"no reference preset called '{name}' ({available})")
    loaded = {}
    for path in sorted(folder.glob("*.csv")):
        kind, count = load_csv(conn, path)
        loaded[kind] = loaded.get(kind, 0) + count
    return loaded


def vocabulary_is_empty(conn, name: str) -> bool:
    """Whether a vocabulary a project refers to has anything in it."""
    if name == "places":
        return not conn.execute(
            "SELECT 1 FROM ref_places LIMIT 1").fetchone()
    return not conn.execute(
        "SELECT 1 FROM ref_vocab WHERE vocabulary=? LIMIT 1", (name,)).fetchone()


def counts(conn) -> dict[str, int]:
    out = {}
    for table in ("ref_units", "ref_places", "ref_vocab", "ref_authority_ids",
                  "ref_source_rank"):
        out[table] = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    return out


# ------------------------------------------------------------------ lookups
def place(conn, name: str):
    """Match a place name or spelling variant to its official row."""
    clean = normalise(name)
    if not clean:
        return None
    row = conn.execute("SELECT * FROM ref_places WHERE lower(name)=lower(?)",
                       (clean,)).fetchone()
    if row:
        return row
    target = key(clean)
    for candidate in conn.execute("SELECT * FROM ref_places").fetchall():
        names = [candidate["name"]] + [a for a in (candidate["aliases"] or "").split(";") if a]
        for option in names:
            if key(option) == target:
                return candidate
    from ..standardize.names import similarity
    best, best_score = None, 0.0
    for candidate in conn.execute("SELECT * FROM ref_places").fetchall():
        for option in [candidate["name"]] + [a for a in (candidate["aliases"] or "").split(";") if a]:
            score = similarity(option, clean)
            if score > best_score:
                best, best_score = candidate, score
    return best if best_score >= 0.85 else None


def vocab_term(conn, vocabulary: str, value: str):
    """Map a written value to a controlled term, or None if it is not allowed."""
    clean = normalise(value)
    if not clean:
        return None
    row = conn.execute(
        "SELECT term FROM ref_vocab WHERE vocabulary=? AND lower(alias)=lower(?)",
        (vocabulary, clean)).fetchone()
    if row:
        return row["term"]
    row = conn.execute(
        "SELECT DISTINCT term FROM ref_vocab WHERE vocabulary=? AND lower(term)=lower(?)",
        (vocabulary, clean)).fetchone()
    if row:
        return row["term"]
    target = key(clean)
    for candidate in conn.execute(
            "SELECT term, alias FROM ref_vocab WHERE vocabulary=?", (vocabulary,)):
        if key(candidate["alias"]) == target or target in key(candidate["alias"]).split():
            return candidate["term"]
    return None


def vocab_terms(conn, vocabulary: str) -> list[str]:
    return [r["term"] for r in conn.execute(
        "SELECT DISTINCT term FROM ref_vocab WHERE vocabulary=? ORDER BY term",
        (vocabulary,))]


def authority_id(conn, entity_type: str, name: str):
    row = conn.execute(
        "SELECT authority, identifier FROM ref_authority_ids "
        "WHERE entity_type=? AND lower(name)=lower(?)", (entity_type, normalise(name))
    ).fetchone()
    return (row["authority"], row["identifier"]) if row else None


def source_tier(conn, domain: str):
    row = conn.execute("SELECT tier FROM ref_source_rank WHERE domain=?",
                       (domain,)).fetchone()
    return row["tier"] if row else None
