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
