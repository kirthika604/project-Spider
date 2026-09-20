"""Candidate values that outgrow memory go to a scratch file on disk.

Conflict resolution needs every candidate for a cell at once (a plant on ten
sites, a place in three files), so candidates were held in a dict until the
last page was read - about 10 KB of memory per record. That is fine for a
crawl and fatal for a million-row file.

Small runs still use the dict, untouched. Past a threshold, everything moves to
a scratch SQLite file and is read back grouped by cell, so memory stays flat
however many rows there are.
"""

from __future__ import annotations

import os
import pickle
import sqlite3
from pathlib import Path

# candidates held in memory before spilling; roughly 100 MB
DEFAULT_SPILL_AT = 100_000


def spill_threshold() -> int:
    try:
        return max(1, int(os.environ.get("SPIDER_SPILL_AT", DEFAULT_SPILL_AT)))
    except ValueError:
        return DEFAULT_SPILL_AT


class CellStore:
    """Candidates grouped by (record, field), in memory until there are too many."""

    def __init__(self, scratch_dir: Path, limit: int | None = None):
        self.limit = limit or spill_threshold()
        self._cells: dict = {}
        self._count = 0
        self._path = Path(scratch_dir) / "candidates.spool"
        self._db: sqlite3.Connection | None = None
        self.spilled = False

    # ---------------------------------------------------------------- writing
    def add(self, entity_id: int, field_name: str, candidate) -> None:
        self._cells.setdefault((entity_id, field_name), []).append(candidate)
        self._count += 1
        if self._count >= self.limit:
            self._spill()

    def _open(self) -> sqlite3.Connection:
        if self._db is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.exists():
                self._path.unlink()
            self._db = sqlite3.connect(self._path)
            self._db.execute("PRAGMA journal_mode=OFF")
            self._db.execute("PRAGMA synchronous=OFF")
            self._db.execute(
                "CREATE TABLE spool (entity_id INTEGER, field TEXT, blob BLOB)")
        return self._db

    def _spill(self) -> None:
        db = self._open()
        rows = [(entity_id, field_name, pickle.dumps(candidate, protocol=4))
                for (entity_id, field_name), group in self._cells.items()
                for candidate in group]
        db.executemany("INSERT INTO spool VALUES (?,?,?)", rows)
        db.commit()
        self._cells.clear()
        self._count = 0
        self.spilled = True

    # ---------------------------------------------------------------- reading
    def __iter__(self):
        """Yield ((record, field), [candidates]) for every cell."""
        if not self.spilled:
            yield from self._cells.items()
            return
        if self._cells:                          # what has not spilled yet
            self._spill()
        db = self._open()
        current, group = None, []
        for entity_id, field_name, blob in db.execute(
                "SELECT entity_id, field, blob FROM spool "
                "ORDER BY entity_id, field, rowid"):
            key = (entity_id, field_name)
            if key != current:
                if group:
                    yield current, group
                current, group = key, []
            group.append(pickle.loads(blob))
        if group:
            yield current, group

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
        try:
            self._path.unlink()
        except OSError:
            pass
