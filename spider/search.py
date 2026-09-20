"""Ranked full-text search with highlighted snippets (FR-9)."""

from __future__ import annotations

import re


def _fts_query(text: str) -> str:
    """Turn a user phrase into an FTS5 query, quoting to avoid syntax errors."""
    text = text.strip()
    if not text:
        return '""'
    if re.search(r'[:"()*]', text) and " " not in text:
        return f'"{text}"'
    terms = [t for t in re.split(r"\s+", text) if t]
    if len(terms) > 1 and not any(t.upper() in ("AND", "OR", "NOT") for t in terms):
        return " ".join(f'"{t}"' for t in terms)
    return " ".join(f'"{t}"' if not t.isalnum() else t for t in terms)


def search(conn, query: str, limit: int = 10, domain: str | None = None,
           snippet_size: int = 20) -> list[dict]:
    """Pages matching every word; if none do, pages matching any of them."""
    results = _run(conn, _fts_query(query), query, limit, domain, snippet_size)
    terms = [t for t in re.split(r"\s+", query.strip()) if t]
    if not results and len(terms) > 1:
        results = _run(conn, " OR ".join(f'"{t}"' for t in terms), query, limit,
                       domain, snippet_size)
    return results


def _run(conn, match: str, query: str, limit: int, domain: str | None,
         snippet_size: int) -> list[dict]:
    sql = (
        "SELECT p.id, p.url, p.domain, p.title, p.relevance, p.fetched_at, "
        "  snippet(pages_fts, 3, '[', ']', ' ... ', ?) AS snippet, "
        "  bm25(pages_fts, 4.0, 2.0, 2.0, 1.0) AS rank "
        "FROM pages_fts JOIN pages p ON p.id = pages_fts.rowid "
        "WHERE pages_fts MATCH ? "
    )
    params: list = [snippet_size, match]
    if domain:
        sql += "AND p.domain LIKE ? "
        params.append(f"%{domain}%")
    sql += "ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:                       # fall back to LIKE on odd queries
        like = f"%{query}%"
        rows = conn.execute(
            "SELECT id, url, domain, title, relevance, fetched_at, "
            "substr(text,1,300) AS snippet, 0 AS rank FROM pages "
            "WHERE text LIKE ? OR title LIKE ? ORDER BY relevance DESC LIMIT ?",
            (like, like, limit)).fetchall()
    return [dict(r) for r in rows]


def get_page(conn, page_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
    if not row:
        return None
    page = dict(row)
    page["fields"] = [dict(r) for r in conn.execute(
        "SELECT name, value FROM fields WHERE page_id=?", (page_id,))]
    page["structured"] = [dict(r) for r in conn.execute(
        "SELECT type, data FROM structured WHERE page_id=?", (page_id,))]
    page["values"] = [dict(r) for r in conn.execute(
        "SELECT e.type, e.canonical_name, a.name, a.value, a.confidence "
        "FROM attributes a JOIN entities e ON e.id=a.entity_id WHERE a.source_page=?",
        (page_id,))]
    return page


def stats(conn) -> dict:
    def one(sql, *params):
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else 0
    return {
        "pages": one("SELECT COUNT(*) FROM pages"),
        "domains": one("SELECT COUNT(DISTINCT domain) FROM pages"),
        "links": one("SELECT COUNT(*) FROM links"),
        "fields": one("SELECT COUNT(*) FROM fields"),
        "structured": one("SELECT COUNT(*) FROM structured"),
        "crawls": one("SELECT COUNT(*) FROM crawls"),
        "words": one("SELECT COALESCE(SUM(word_count),0) FROM pages"),
        "entities": one("SELECT COUNT(*) FROM entities"),
        "attributes": one("SELECT COUNT(*) FROM attributes WHERE status='accepted'"),
        "relations": one("SELECT COUNT(*) FROM relations"),
        "review_open": one("SELECT COUNT(*) FROM review_queue WHERE status='open'"),
        "top_domains": [dict(r) for r in conn.execute(
            "SELECT domain, COUNT(*) pages FROM pages GROUP BY domain "
            "ORDER BY pages DESC LIMIT 10")],
        "top_fields": [dict(r) for r in conn.execute(
            "SELECT name, COUNT(*) n FROM attributes WHERE status='accepted' "
            "GROUP BY name ORDER BY n DESC LIMIT 10")],
    }
