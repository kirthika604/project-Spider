"""SQLite access: project discovery, connections and schema creation."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SPIDER_DIR = ".spider"
DB_NAME = "spider.db"


class ProjectNotFound(Exception):
    """Raised when no .spider folder exists in this folder or above it."""


def find_project(start: Path | None = None) -> Path:
    """Walk up from `start` looking for a .spider folder (FR-1)."""
    here = (start or Path.cwd()).resolve()
    for folder in [here, *here.parents]:
        if (folder / SPIDER_DIR / DB_NAME).exists():
            return folder
    raise ProjectNotFound(
        "no Spider project here or in any parent folder - run `spider init` first"
    )


def db_path(root: Path) -> Path:
    return root / SPIDER_DIR / DB_NAME


def connect(root: Path | None = None,
            check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the project database.

    `check_same_thread=False` is for the dashboard, which runs its script on a
    different thread each time it reruns.
    """
    root = root or find_project()
    conn = sqlite3.connect(db_path(root), timeout=30,
                           check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    migrate(conn)
    return conn


def init_project(root: Path, with_reference: bool = True) -> Path:
    """Create .spider/spider.db and the reference database, and return the root."""
    (root / SPIDER_DIR).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path(root))
    conn.executescript((Path(__file__).parent / "schema.sql").read_text())
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('created_at',?)",
        (now(),),
    )
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('fts_version',?)",
                 (FTS_VERSION,))
    conn.commit()
    conn.close()
    if with_reference:
        conn = connect(root)
        from ..ref.tables import load_seeds
        load_seeds(conn)
        conn.close()
    return root


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply the schema to an existing connection (safe to repeat)."""
    conn.executescript((Path(__file__).parent / "schema.sql").read_text())
    conn.commit()


FTS_VERSION = "4"


def migrate(conn: sqlite3.Connection) -> None:
    """Bring a database made by an earlier version up to date."""
    # cheap, idempotent additions land here so every existing project gets
    # them on the next connect without a version bump
    conn.execute(
        "CREATE TABLE IF NOT EXISTS review_decisions ("
        "target TEXT, kind TEXT, choice TEXT, decided_at TEXT,"
        "PRIMARY KEY (target, kind))")
    if get_meta(conn, "fts_version") == FTS_VERSION:
        conn.commit()
        return
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(pages)")}
    if "summary" not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN summary TEXT")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS value_history (url TEXT, field TEXT, "
        "value TEXT, raw_value TEXT, seen_at TEXT, PRIMARY KEY (url, field))")
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='pages_fts'").fetchone()
    if row and "content=''" in (row["sql"] or ""):
        conn.execute("DROP TABLE pages_fts")
        conn.execute("CREATE VIRTUAL TABLE pages_fts USING fts5("
                     "title, description, headings, text)")
        conn.execute(
            "INSERT INTO pages_fts(rowid,title,description,headings,text) "
            "SELECT id, title, description, headings, text FROM pages")
    set_meta(conn, "fts_version", FTS_VERSION)
    conn.commit()


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def jdump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def jload(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value)))
