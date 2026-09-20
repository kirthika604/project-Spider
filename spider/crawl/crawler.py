"""The crawl loop: the nine steps of the pipeline in section 6."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..extract.parser import parse, score_relevance
from ..store.db import jdump, now
from .fetch import fetch
from .frontier import Frontier, domain_of
from .robots import Politeness


@dataclass
class CrawlResult:
    crawl_id: int
    seen: int = 0
    saved: int = 0
    skipped_existing: int = 0
    blocked: int = 0
    failed: int = 0
    irrelevant: int = 0
    page_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Crawler:
    def __init__(self, conn, *, keywords=None, depth=2, max_pages=100, delay=1.0,
                 any_domain=False, domains=None, extract_rules=None, refresh=False,
                 respect_robots=True, tier_of=None, source_id=None, on_event=None,
                 relevance_check=None, workers=5):
        self.conn = conn
        self.keywords = list(keywords or [])
        self.depth = depth
        self.max_pages = max_pages
        self.extract_rules = extract_rules or {}
        self.refresh = refresh
        self.any_domain = any_domain
        self.domains = domains or []
        self.tier_of = tier_of or (lambda _u: 3)
        self.source_id = source_id
        self.politeness = Politeness(default_delay=delay, respect_robots=respect_robots)
        self.on_event = on_event or (lambda *a, **k: None)
        # layer 3 (section 12): given (title, text), says whether the page
        # holds facts the schema wants. None means "keyword score only".
        self.relevance_check = relevance_check
        self.ai_rejected = 0
        # How many pages may be in flight at once. The per-domain delay still
        # holds, so this only helps when a crawl spans several sites - which
        # is the case Spider is for.
        self.workers = max(1, int(workers))

    # ------------------------------------------------------------------ run
    def run(self, seeds, kind: str = "crawl") -> CrawlResult:
        cursor = self.conn.execute(
            "INSERT INTO crawls(started_at,seeds,keywords,depth,max_pages,kind) "
            "VALUES(?,?,?,?,?,?)",
            (now(), jdump(list(seeds)), jdump(self.keywords), self.depth,
             self.max_pages, kind))
        result = CrawlResult(crawl_id=cursor.lastrowid)
        self.conn.commit()

        frontier = Frontier(seeds, max_depth=self.depth, any_domain=self.any_domain,
                            allowed_domains=self.domains, tier_of=self.tier_of)
        if self.workers == 1:
            while len(frontier) and result.saved < self.max_pages:
                url, depth, tier = frontier.pop()
                result.seen += 1
                self._visit(url, depth, tier, frontier, result)
        else:
            self._run_in_parallel(frontier, result)

        self.conn.execute(
            "UPDATE crawls SET finished_at=?, pages_saved=?, pages_seen=? WHERE id=?",
            (now(), result.saved, result.seen, result.crawl_id))
        self.conn.commit()
        return result

    def _run_in_parallel(self, frontier, result: CrawlResult) -> None:
        """Fetch several pages at once, but parse and store on one thread.

        Only the network wait is worth parallelising; SQLite stays on the
        calling thread, so there is no shared-connection problem to solve.
        """
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            while len(frontier) and result.saved < self.max_pages:
                batch = self._next_batch(frontier, result)
                if not batch:
                    break
                fetched = list(pool.map(lambda job: (job, self._fetch_one(job[0])),
                                        batch))
                for (url, depth, tier), got in fetched:
                    if result.saved >= self.max_pages:
                        break
                    self._store_result(url, depth, tier, got, frontier, result)

    def _next_batch(self, frontier, result: CrawlResult) -> list:
        """Take the next few URLs, at most one per domain, so the batch never
        waits on itself."""
        batch, seen_domains, held = [], set(), []
        room = min(self.workers, max(1, self.max_pages - result.saved))
        while len(frontier) and len(batch) < room:
            job = frontier.pop()
            url, depth, _tier = job
            existing = self.conn.execute(
                "SELECT id FROM pages WHERE url=?", (url,)).fetchone()
            if existing and not self.refresh:
                result.skipped_existing += 1
                if depth < self.depth:
                    stored = [r["to_url"] for r in self.conn.execute(
                        "SELECT to_url FROM links WHERE from_page=?",
                        (existing["id"],))]
                    frontier.add_links(url, stored, depth)
                self.on_event("skip", url=url, reason="already stored")
                continue
            domain = domain_of(url)
            if domain in seen_domains:
                held.append(job)
                continue
            seen_domains.add(domain)
            batch.append(job)
        for job in held:                        # put the rest back, in order
            frontier.queue.appendleft(job)
        result.seen += len(batch)
        return batch

    def _fetch_one(self, url: str):
        """The part that is worth doing in parallel: robots, wait, fetch."""
        if not self.politeness.allowed(url):
            return None
        self.politeness.wait(url)
        return fetch(url)

    def _store_result(self, url, depth, tier, got, frontier, result) -> None:
        if got is None:
            result.blocked += 1
            self.on_event("blocked", url=url, reason="robots.txt")
            return
        if not got.ok:
            result.failed += 1
            reason = got.error or f"HTTP {got.status}"
            result.errors.append(f"{url}: {reason}")
            self.on_event("failed", url=url, reason=reason)
            return
        page = parse(got.html, url, self.extract_rules)
        relevance = score_relevance(page, self.keywords)
        if depth < self.depth:
            frontier.add_links(url, page.links, depth)
        if relevance <= 0:
            result.irrelevant += 1
            self.on_event("irrelevant", url=url, reason="no keyword match")
            return
        if self.relevance_check is not None:
            useful, why = self.relevance_check(page.title, page.text)
            if not useful:
                result.irrelevant += 1
                self.ai_rejected += 1
                self.on_event("irrelevant", url=url, reason=f"AI check: {why}")
                return
        page_id = self.save_page(page, result.crawl_id, relevance, got.status,
                                 depth, tier)
        result.saved += 1
        result.page_ids.append(page_id)
        self.on_event("saved", url=url, page_id=page_id, relevance=relevance,
                      title=page.title, tier=tier)

    # ---------------------------------------------------------------- steps
    def _visit(self, url, depth, tier, frontier, result: CrawlResult) -> None:
        existing = self.conn.execute(
            "SELECT id FROM pages WHERE url=?", (url,)).fetchone()
        if existing and not self.refresh:                      # step 2
            result.skipped_existing += 1
            # its links still belong in the queue, or a second crawl could
            # never reach anything deeper than what is already stored
            if depth < self.depth:
                stored = [r["to_url"] for r in self.conn.execute(
                    "SELECT to_url FROM links WHERE from_page=?", (existing["id"],))]
                frontier.add_links(url, stored, depth)
            self.on_event("skip", url=url, reason="already stored")
            return
        if not self.politeness.allowed(url):
            result.blocked += 1
            self.on_event("blocked", url=url, reason="robots.txt")
            return

        self.politeness.wait(url)                              # step 3
        got = fetch(url)
        if not got.ok:                                         # step 4
            result.failed += 1
            reason = got.error or f"HTTP {got.status}"
            result.errors.append(f"{url}: {reason}")
            self.on_event("failed", url=url, reason=reason)
            return

        page = parse(got.html, url, self.extract_rules)        # step 5
        relevance = score_relevance(page, self.keywords)       # step 6

        if depth < self.depth:                                 # step 8
            frontier.add_links(url, page.links, depth)

        if relevance <= 0:                                     # step 7
            result.irrelevant += 1
            self.on_event("irrelevant", url=url, reason="no keyword match")
            return

        if self.relevance_check is not None:       # layer 3, after the cheap filter
            useful, why = self.relevance_check(page.title, page.text)
            if not useful:
                result.irrelevant += 1
                self.ai_rejected += 1
                self.on_event("irrelevant", url=url, reason=f"AI check: {why}")
                return

        page_id = self.save_page(page, result.crawl_id, relevance, got.status,
                                 depth, tier)
        result.saved += 1
        result.page_ids.append(page_id)
        self.on_event("saved", url=url, page_id=page_id, relevance=relevance,
                      title=page.title, tier=tier)

    # ---------------------------------------------------------------- store
    def save_page(self, page, crawl_id, relevance, status, depth, tier) -> int:
        conn = self.conn
        row = conn.execute("SELECT id FROM pages WHERE url=?", (page.url,)).fetchone()
        values = (crawl_id, page.url, domain_of(page.url), page.title, page.description,
                  page.author, page.published, page.lang, page.headings_text, page.text,
                  page.word_count, relevance, status, page.content_hash, depth, tier,
                  self.source_id, now())
        if row:
            page_id = row["id"]
            conn.execute(
                "UPDATE pages SET crawl_id=?,url=?,domain=?,title=?,description=?,"
                "author=?,published=?,lang=?,headings=?,text=?,word_count=?,relevance=?,"
                "status=?,content_hash=?,depth=?,tier=?,source_id=?,fetched_at=? WHERE id=?",
                (*values, page_id))
            for table in ("links", "fields", "structured"):
                key = "from_page" if table == "links" else "page_id"
                conn.execute(f"DELETE FROM {table} WHERE {key}=?", (page_id,))
            conn.execute("DELETE FROM pages_fts WHERE rowid=?", (page_id,))
        else:
            page_id = conn.execute(
                "INSERT INTO pages(crawl_id,url,domain,title,description,author,"
                "published,lang,headings,text,word_count,relevance,status,content_hash,"
                "depth,tier,source_id,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values).lastrowid

        conn.executemany("INSERT OR IGNORE INTO links(from_page,to_url) VALUES(?,?)",
                         [(page_id, link) for link in dict.fromkeys(page.links)])
        conn.executemany("INSERT INTO fields(page_id,name,value) VALUES(?,?,?)",
                         [(page_id, n, v) for n, v in page.fields])
        conn.executemany("INSERT INTO structured(page_id,type,data) VALUES(?,?,?)",
                         [(page_id, t, d) for t, d in page.structured])
        conn.execute(
            "INSERT INTO pages_fts(rowid,title,description,headings,text) VALUES(?,?,?,?,?)",
            (page_id, page.title, page.description, page.headings_text, page.text))
        conn.commit()                                # step 7: commit per page (NFR-4)
        return page_id


def crawl_from_spec(conn, spec, *, max_pages=None, depth=None, refresh=False,
                    seeds=None, kind="crawl", on_event=None, use_ai_relevance=False):
    """Run a crawl using the settings in spider.yaml (section 11, step 5)."""
    src = spec.sources
    rules: dict[str, list[str]] = {}
    for ent in spec.entities.values():
        for fname, fld in ent.fields.items():
            if fld.extract:
                rules.setdefault(fname, []).extend(fld.extract)
    relevance_check = None
    if use_ai_relevance:
        from ..extract import ai as ai_module
        if ai_module.available():
            schema = ai_module.schema_prompt(spec)

            def relevance_check(title, text):
                try:
                    return ai_module.is_useful(conn, title, text, schema)
                except Exception:
                    return True, "check unavailable"   # never lose a page to an outage

    crawler = Crawler(
        conn, workers=getattr(src, "workers", 5),
        keywords=src.keywords, depth=depth if depth is not None else src.depth,
        max_pages=max_pages or src.max_pages, delay=src.delay_seconds,
        any_domain=src.follow_other_domains, domains=src.domains,
        extract_rules=rules, refresh=refresh, tier_of=spec.tier_for, on_event=on_event,
        relevance_check=relevance_check)
    if src.mode == "only_listed":
        crawler.depth = 0
    return crawler.run(seeds if seeds is not None else src.all_seeds(), kind=kind)
