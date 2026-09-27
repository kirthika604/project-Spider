"""Shared fixtures: an in-memory project with a small schema and sample pages."""

from pathlib import Path

import pytest

from spider.ref import tables as ref_tables
from spider.spec import Spec
from spider.store import db as store

SAMPLES = Path(__file__).parent / "samples"


@pytest.fixture
def project(tmp_path):
    store.init_project(tmp_path)
    conn = store.connect(tmp_path)
    # Assembly tests use the Himalayan demo schema deliberately.  Real
    # projects opt into this pack instead of receiving it during `init`.
    ref_tables.load_preset(conn, "himalayan-plants")
    yield tmp_path, conn
    conn.close()


@pytest.fixture
def spec():
    return Spec.from_dict({
        "project": "test-plants",
        "mode": "project",
        "sources": {"seeds": ["http://a.test/"], "keywords": ["plant"], "depth": 1,
                    "trust_tiers": {"a.test": 1, "b.test": 2, "c.test": 3}},
        "entities": {
            "plant": {"identity": ["scientific_name"], "fields": {
                "scientific_name": {"type": "text", "required": True,
                                    "extract": [".sci"]},
                "altitude_m": {"type": "range", "unit": "m", "sanity": [0, 9000],
                               "extract": [".altitude"]},
                "flowering_month": {"type": "month", "sanity": [1, 12],
                                    "extract": [".flowering"]},
            }},
            "region": {"identity": ["name"],
                       "fields": {"name": {"type": "text", "vocabulary": "places"}}},
            "use": {"identity": ["name"],
                    "fields": {"name": {"type": "text", "vocabulary": "uses",
                                        "level": "strict"}}},
        },
        "relations": [{"from": "plant", "name": "grows_in", "to": "region"},
                      {"from": "plant", "name": "used_for", "to": "use"}],
        "derived": {"climate_zone": {
            "on": "plant", "method": "lookup", "inputs": ["altitude_m.min"],
            "bands": {"cutoffs": [1500, 3000],
                      "labels": ["subtropical", "temperate", "alpine"]},
            "explain": "Zone from minimum altitude"}},
        "standardize": {"level": "standard", "min_confidence": 0.5},
        "storage": {"normal_form": "3NF"},
    })


def sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


def store_page(conn, url, html, spec, tier=1, crawl_id=1):
    """Parse and store a sample page the way the crawler would."""
    from spider.crawl.crawler import Crawler
    rules = {}
    for ent in spec.entities.values():
        for name, fld in ent.fields.items():
            if fld.extract:
                rules.setdefault(name, []).extend(fld.extract)
    conn.execute("INSERT OR IGNORE INTO crawls(id,started_at) VALUES(?,?)",
                 (crawl_id, store.now()))
    crawler = Crawler(conn, extract_rules=rules)
    from spider.extract.parser import parse, score_relevance
    page = parse(html, url, rules)
    # score against the spec's own keywords, not a hard-coded one, or a page
    # about a different subject silently scores zero and is never built
    relevance = score_relevance(page, spec.sources.keywords or ["plant"])
    return crawler.save_page(page, crawl_id, relevance, 200, 0, tier)


# --------------------------------------------------------------------------
# A small real website with structure, for requirement tests. It is served over
# HTTP so the crawler, robots.txt and rate limiting are all genuinely exercised.
# --------------------------------------------------------------------------
import http.server
import socket
import threading


PAGES = {
    "/index.html": """<html lang="en"><head><title>Index of nothing</title></head><body>
        <h1>Home</h1><p>welcome</p>
        <a href="/a.html">A</a> <a href="/b.html">B</a> <a href="/private/x.html">P</a>
        <a href="http://offsite.invalid/z.html">off</a></body></html>""",
    # relevant, rich metadata, JSON-LD and a custom-rule field
    "/a.html": """<html lang="en"><head><title>Alpha plant page</title>
        <meta name="description" content="About the alpha plant">
        <meta name="author" content="Dr Rao">
        <meta property="article:published_time" content="2025-06-14">
        <script type="application/ld+json">{"@type":"Plant","name":"Alpha"}</script></head>
        <body><nav>menu</nav><h1>Alpha</h1><h2>Habitat</h2>
        <p class="alt">Grows at 3,000 to 4,500 m.</p>
        <p>The plant is a herb. The plant likes cold. The plant is rare.</p>
        <a href="/c.html">C</a><footer>footer junk</footer></body></html>""",
    # irrelevant (no keyword) but it links to a relevant page
    "/b.html": """<html><head><title>Bee</title></head><body><p>nothing here</p>
        <a href="/d.html">D</a></body></html>""",
    "/c.html": """<html><head><title>Charlie plant</title></head><body>
        <h1>Charlie</h1><p>a plant of depth two</p><a href="/e.html">E</a></body></html>""",
    "/d.html": """<html><head><title>Delta plant</title></head><body>
        <h1>Delta</h1><p>a plant reached through an irrelevant page</p></body></html>""",
    "/e.html": """<html><head><title>Echo plant</title></head><body>
        <h1>Echo</h1><p>a plant of depth three</p></body></html>""",
    "/private/x.html": "<html><body><p>plant secret</p></body></html>",
}
ROBOTS = "User-agent: *\nDisallow: /private/\nCrawl-delay: 0\n"


@pytest.fixture
def multisite():
    """Yields base URLs for one server reachable under two host names, so 'same
    domain' and 'other domain' are both real."""
    class Handler(http.server.BaseHTTPRequestHandler):
        hits = []

        def do_GET(self):
            Handler.hits.append(self.path)
            if self.path == "/robots.txt":
                body, kind = ROBOTS.encode(), "text/plain"
            elif self.path in PAGES:
                body, kind = PAGES[self.path].encode(), "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Handler.hits = []
    yield {"a": f"http://127.0.0.1:{port}", "b": f"http://localhost:{port}",
           "hits": Handler.hits, "port": port}
    server.shutdown()
