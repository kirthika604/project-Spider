"""Query-driven source discovery (section 12, layer 2) and gap-driven fill (D3).

Spider writes search queries from its own schema and its own empty cells,
sends them to whichever search API has a key, and keeps only results from
allowed domains. With no key, it still works from the trusted seeds and the
link graph already stored.
"""

from __future__ import annotations

import os

import requests

from .. import USER_AGENT
from ..store.db import jdump, now
from .frontier import domain_of, is_crawlable

SEARCH_APIS = {
    "BRAVE_API_KEY": "brave",
    "SERPER_API_KEY": "serper",
    "TAVILY_API_KEY": "tavily",
}


def queries_for_gaps(conn, spec, gaps, limit: int = 10) -> list[str]:
    """'Brahmakamal altitude Uttarakhand' - one query per empty cell."""
    context = _context_words(spec)
    queries: list[str] = []

    def add(query: str) -> None:
        if query not in queries:          # two empty columns can ask the same thing
            queries.append(query)

    for gap in gaps:
        readable = gap["field"].replace("_", " ")
        for word in ("m", "ft", "kg", "km", "cm"):
            if readable.endswith(f" {word}"):
                readable = readable[: -(len(word) + 1)]
                break
        for name in gap["examples"]:
            add(" ".join(filter(None, [name, readable, context])))
            if len(queries) >= limit:
                return queries
        if not gap["examples"]:
            add(" ".join(filter(None, [gap["entity_type"], readable, context])))
    return queries[:limit]


def queries_for_schema(conn, spec, limit: int = 10) -> list[str]:
    context = _context_words(spec)
    queries = []
    for entity_type, ent in spec.entities.items():
        for field_name in list(ent.fields)[:3]:
            query = " ".join(filter(
                None, [entity_type, field_name.replace('_', ' '), context]))
            if query not in queries:
                queries.append(query)
            if len(queries) >= limit:
                return queries
    return queries


def _context_words(spec) -> str:
    words = [k for k in spec.sources.keywords[:2]]
    described = spec.raw.get("described_as", "")
    for candidate in str(described).split():
        if candidate.istitle() and candidate.lower() not in [w.lower() for w in words]:
            words.append(candidate)
            break
    return " ".join(words[:3])


def available_api() -> tuple[str | None, str | None]:
    for variable, name in SEARCH_APIS.items():
        if os.environ.get(variable):
            return name, os.environ[variable]
    return None, None


def discover(conn, spec, queries, limit: int = 40) -> tuple[list[str], str]:
    """Run the queries, keep allowed domains, and return URLs to crawl."""
    name, key = available_api()
    if not name:
        urls = _from_link_graph(conn, spec, limit)
        return urls, (f"No search key set, so {len(urls)} unvisited link(s) from pages "
                      f"already stored were used instead.")

    found: list[tuple[str, int]] = []
    for query in queries:
        for url in _search(name, key, query):
            if not is_crawlable(url):
                continue
            domain = domain_of(url)
            tier = spec.tier_for(url)
            if spec.sources.mode == "only_listed":
                continue
            if not spec.sources.follow_other_domains and spec.sources.all_seeds():
                seeds = {domain_of(s) for s in spec.sources.all_seeds()}
                if domain not in seeds and tier > 2:
                    continue                      # keep tier 1 and 2 finds only
            if conn.execute("SELECT 1 FROM pages WHERE url=?", (url,)).fetchone():
                continue
            found.append((url, tier))
        if len(found) >= limit:
            break

    ordered = [url for url, _tier in sorted(found, key=lambda pair: pair[1])][:limit]
    for url in ordered:
        tier = spec.tier_for(url)
        conn.execute(
            "INSERT OR IGNORE INTO ref_source_rank(domain,tier,note) VALUES(?,?,?)",
            (domain_of(url), max(tier, 3) if tier > 2 else tier,
             f"found by search on {now()[:10]}"))
    conn.commit()
    tiers = {}
    for url in ordered:
        tiers[spec.tier_for(url)] = tiers.get(spec.tier_for(url), 0) + 1
    note = (f"{name} search returned {len(ordered)} new URL(s): "
            + ", ".join(f"tier{t} {c}" for t, c in sorted(tiers.items())))
    return ordered, note


def _search(api: str, key: str, query: str, count: int = 10) -> list[str]:
    try:
        if api == "brave":
            response = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": count},
                headers={"X-Subscription-Token": key, "Accept": "application/json",
                         "User-Agent": USER_AGENT}, timeout=15)
            data = response.json()
            return [r["url"] for r in (data.get("web") or {}).get("results", [])
                    if r.get("url")]
        if api == "serper":
            response = requests.post(
                "https://google.serper.dev/search", json={"q": query, "num": count},
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                timeout=15)
            data = response.json()
            return [r["link"] for r in data.get("organic", []) if r.get("link")]
        if api == "tavily":
            response = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query, "max_results": count}, timeout=20)
            data = response.json()
            return [r["url"] for r in data.get("results", []) if r.get("url")]
    except Exception:
        return []
    return []


def _from_link_graph(conn, spec, limit: int) -> list[str]:
    """Without a search key: follow links Spider has seen but not yet visited."""
    rows = conn.execute(
        "SELECT l.to_url AS url, COUNT(*) AS seen FROM links l "
        "LEFT JOIN pages p ON p.url = l.to_url WHERE p.id IS NULL "
        "GROUP BY l.to_url ORDER BY seen DESC LIMIT ?", (limit * 4,)).fetchall()
    allowed = {domain_of(s) for s in spec.sources.all_seeds()}
    out = []
    for row in rows:
        url = row["url"]
        if not url.startswith("http") or not is_crawlable(url):
            continue
        if not spec.sources.follow_other_domains and allowed and domain_of(url) not in allowed:
            continue
        out.append(url)
        if len(out) >= limit:
            break
    return out


def record_run(conn, queries, urls) -> None:
    conn.execute(
        "INSERT INTO crawls(started_at,seeds,keywords,depth,max_pages,kind) "
        "VALUES(?,?,?,?,?,?)",
        (now(), jdump(urls), jdump(queries), 1, len(urls), "discovery"))
    conn.commit()
