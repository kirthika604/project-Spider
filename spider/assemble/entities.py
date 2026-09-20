"""Entity resolution: one real-world item, however many sites mention it (FR-17)."""

from __future__ import annotations

from ..standardize import names
from ..store.db import now


class EntityIndex:
    """Finds or creates the entity a candidate belongs to, and merges aliases.

    Matching a new name to an existing record used to compare it against every
    record, which is n-squared: 2,000 records took 187 seconds, 20,000 would
    take hours. Names are now compared only with plausible neighbours - those
    that share a block - so the cost grows with how alike records are, not with
    how many there are.
    """

    FUZZY_THRESHOLD = 0.92
    # A block this crowded says nothing about who is a variant of whom (every
    # "John ..." in a phone book), so it is skipped rather than scanned.
    MAX_BLOCK = 400

    def __init__(self, conn, spec):
        self.conn = conn
        self.spec = spec
        self._by_key: dict[tuple[str, str], int] = {}
        self._aliases: dict[str, set[int]] = {}
        self._type_of: dict[int, str] = {}
        self._blocks: dict[tuple[str, str], list] = {}
        self._known_aliases: set = set()
        self.merges: list[str] = []
        self.skipped_dense = 0
        self._load()

    def _load(self) -> None:
        for row in self.conn.execute(
                "SELECT id, type, canonical_name, identity_key FROM entities"):
            self._type_of[row["id"]] = row["type"]
            if row["identity_key"]:
                self._by_key[(row["type"], row["identity_key"])] = row["id"]
                self._add_to_blocks(row["type"], row["id"], row["identity_key"])
        for row in self.conn.execute(
                "SELECT entity_id, alias FROM entity_aliases"):
            self._aliases.setdefault(names.key(row["alias"]), set()).add(row["entity_id"])
            self._known_aliases.add((row["entity_id"], row["alias"]))

    @staticmethod
    def _block_keys(key: str) -> list[str]:
        """Two records can only be spelling variants if they share every digit
        and either their first or their last three letters."""
        digits = "".join(ch for ch in key if ch.isdigit())
        compact = key.replace(" ", "")
        return [f"{digits}|p:{compact[:3]}", f"{digits}|s:{compact[-3:]}"]

    def _add_to_blocks(self, entity_type: str, entity_id: int, key: str) -> None:
        for block in self._block_keys(key):
            self._blocks.setdefault((entity_type, block), []).append((entity_id, key))

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
            if len(self.merges) < 200:
                self.merges.append(f"{clean} -> entity {matched}")
            return matched

        fuzzy = self._match_fuzzy(entity_type, clean)
        if fuzzy:
            self.add_alias(fuzzy, clean)
            canonical = self.conn.execute(
                "SELECT canonical_name FROM entities WHERE id=?", (fuzzy,)).fetchone()
            if len(self.merges) < 200:
                self.merges.append(
                    f"{clean} ~ {canonical['canonical_name']} (spelling variant)")
            return fuzzy

        entity_id = self.conn.execute(
            "INSERT INTO entities(type,canonical_name,identity_key,created_at) "
            "VALUES(?,?,?,?)", (entity_type, self._canonical(entity_type, clean),
                                ident, now())).lastrowid
        self._by_key[(entity_type, ident)] = entity_id
        self._type_of[entity_id] = entity_type
        self._add_to_blocks(entity_type, entity_id, ident)
        self.add_alias(entity_id, clean)
        return entity_id

    def add_alias(self, entity_id: int, alias: str, language: str | None = None,
                  source: str = "page") -> None:
        clean = names.normalise(alias)
        if not clean or (entity_id, clean) in self._known_aliases:
            return
        self._known_aliases.add((entity_id, clean))
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
            if self._type_of.get(entity_id) == entity_type:
                return entity_id
        return None

    def _fuzzy_allowed(self, entity_type: str) -> bool:
        """Spelling-variant matching suits names. It must never run on an
        identifier, where "S000123" and "S000124" are simply two shops."""
        ent = self.spec.entities.get(entity_type)
        if ent is None:
            return True
        if getattr(ent, "match", "fuzzy") == "exact":
            return False
        identity = ent.identity or list(ent.fields)[:1]
        return all(ent.fields[f].type == "text" for f in identity if f in ent.fields)

    def _match_fuzzy(self, entity_type: str, value: str) -> int | None:
        if not self._fuzzy_allowed(entity_type):
            return None
        key = names.key(value)
        if not key:
            return None
        seen, best, best_score = set(), None, 0.0
        for block in self._block_keys(key):
            members = self._blocks.get((entity_type, block), ())
            if len(members) > self.MAX_BLOCK:
                self.skipped_dense += 1
                continue
            for entity_id, other in members:
                if entity_id in seen:
                    continue
                seen.add(entity_id)
                if abs(len(other) - len(key)) > max(2, 0.25 * max(len(other), len(key))):
                    continue
                score = names.similarity_of_keys(other, key)
                if score > best_score:
                    best, best_score = entity_id, score
        return best if best_score >= self.FUZZY_THRESHOLD else None

    def type_of(self, entity_id: int) -> str:
        """What kind of record this is, without a query."""
        kind = self._type_of.get(entity_id)
        if kind is None:
            row = self.conn.execute("SELECT type FROM entities WHERE id=?",
                                    (entity_id,)).fetchone()
            kind = row["type"] if row else ""
            self._type_of[entity_id] = kind
        return kind

    def label(self, entity_id: int) -> str:
        row = self.conn.execute("SELECT canonical_name FROM entities WHERE id=?",
                                (entity_id,)).fetchone()
        return row["canonical_name"] if row else str(entity_id)
