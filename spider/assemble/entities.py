"""Entity resolution: one real-world item, however many sites mention it (FR-17)."""

from __future__ import annotations

from ..standardize import names
from ..store.db import now


class EntityIndex:
    """Finds or creates the entity a candidate belongs to, and merges aliases."""

    def __init__(self, conn, spec):
        self.conn = conn
        self.spec = spec
        self._by_key: dict[tuple[str, str], int] = {}
        self._aliases: dict[str, set[int]] = {}
        self.merges: list[str] = []
        self._load()

    def _load(self) -> None:
        for row in self.conn.execute("SELECT id, type, identity_key FROM entities"):
            if row["identity_key"]:
                self._by_key[(row["type"], row["identity_key"])] = row["id"]
        for row in self.conn.execute("SELECT entity_id, alias FROM entity_aliases"):
            self._aliases.setdefault(names.key(row["alias"]), set()).add(row["entity_id"])

    # ------------------------------------------------------------------ api
    def resolve(self, entity_type: str, identity_value: str) -> int | None:
        """Return the entity id for this name, creating or merging as needed."""
        clean = names.normalise(identity_value)
        if not clean:
            return None
        ident = self._identity_key(entity_type, clean)
        existing = self._by_key.get((entity_type, ident))
        if existing:
            self.add_alias(existing, clean)
            return existing

        matched = self._match_alias(entity_type, clean)
        if matched:
            self.add_alias(matched, clean)
            self.merges.append(f"{clean} -> entity {matched}")
            return matched

        fuzzy = self._match_fuzzy(entity_type, clean)
        if fuzzy:
            self.add_alias(fuzzy, clean)
            canonical = self.conn.execute(
                "SELECT canonical_name FROM entities WHERE id=?", (fuzzy,)).fetchone()
            self.merges.append(f"{clean} ~ {canonical['canonical_name']} (spelling variant)")
            return fuzzy

        entity_id = self.conn.execute(
            "INSERT INTO entities(type,canonical_name,identity_key,created_at) "
            "VALUES(?,?,?,?)", (entity_type, self._canonical(entity_type, clean),
                                ident, now())).lastrowid
        self._by_key[(entity_type, ident)] = entity_id
        self.add_alias(entity_id, clean)
        return entity_id

    def add_alias(self, entity_id: int, alias: str, language: str | None = None,
                  source: str = "page") -> None:
        clean = names.normalise(alias)
        if not clean:
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO entity_aliases(entity_id,alias,language,script,source) "
            "VALUES(?,?,?,?,?)",
            (entity_id, clean, language or "", names.script_of(clean), source))
        self._aliases.setdefault(names.key(clean), set()).add(entity_id)
        if names.script_of(clean) != "Latin":       # D4: local name to Latin
            latin = names.transliterate(clean)
            if latin and latin != clean:
                self.conn.execute(
                    "INSERT OR IGNORE INTO entity_aliases"
                    "(entity_id,alias,language,script,source) VALUES(?,?,?,?,?)",
                    (entity_id, latin, language or "", "Latin", "transliteration"))
                self._aliases.setdefault(names.key(latin), set()).add(entity_id)

    # -------------------------------------------------------------- helpers
    def _identity_key(self, entity_type: str, value: str) -> str:
        ent = self.spec.entities.get(entity_type)
        if ent and any(names.is_scientific_field(f) for f in (ent.identity or [])):
            return names.key(names.scientific(value))
        return names.key(value)

    def _canonical(self, entity_type: str, value: str) -> str:
        ent = self.spec.entities.get(entity_type)
        if ent and any(names.is_scientific_field(f) for f in (ent.identity or [])):
            return names.scientific(value)
        return value

    def _match_alias(self, entity_type: str, value: str) -> int | None:
        for entity_id in self._aliases.get(names.key(value), ()):  # exact alias hit
            row = self.conn.execute("SELECT type FROM entities WHERE id=?",
                                    (entity_id,)).fetchone()
            if row and row["type"] == entity_type:
                return entity_id
        return None

    def _match_fuzzy(self, entity_type: str, value: str) -> int | None:
        rows = self.conn.execute(
            "SELECT id, canonical_name FROM entities WHERE type=?", (entity_type,)
        ).fetchall()
        best, best_score = None, 0.0
        for row in rows:
            score = names.similarity(row["canonical_name"], value)
            if score > best_score:
                best, best_score = row["id"], score
        if best_score >= 0.92:
            return best
        return None

    def label(self, entity_id: int) -> str:
        row = self.conn.execute("SELECT canonical_name FROM entities WHERE id=?",
                                (entity_id,)).fetchone()
        return row["canonical_name"] if row else str(entity_id)
