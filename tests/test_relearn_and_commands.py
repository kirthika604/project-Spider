"""Self-healing rules (D6), preview, the fill loop, summaries and API sources."""

import http.server
import json
import socket
import threading

import pytest
from conftest import sample, store_page

from spider.assemble.build import build
from spider.cli import main
from spider.extract import relearn as relearn_module
from spider.spec import Spec, SourceItem
from spider.store import db as store

ORIGINAL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Saussurea obvallata</title></head><body><h1>Saussurea obvallata</h1>
<p class="sci">Saussurea obvallata</p>
<p class="altitude">It grows at 3,000 to 4,500 m above sea level.</p>
<p>A plant of the alpine Himalaya.</p></body></html>"""

REDESIGNED = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Saussurea obvallata</title></head><body><h1>Saussurea obvallata</h1>
<div class="factsheet"><p class="sci">Saussurea obvallata</p>
<div class="row"><span class="label">Elevation</span>
<span class="elevation-value">It grows at 3,000 to 4,500 m above sea level.</span>
</div></div><p>A plant of the alpine Himalaya.</p></body></html>"""


@pytest.fixture
def site(tmp_path):
    """A server whose page can be redesigned mid-test."""
    state = {"html": ORIGINAL}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (b"User-agent: *\nAllow: /\n" if self.path.endswith("robots.txt")
                    else state["html"].encode())
            kind = ("text/plain" if self.path.endswith("robots.txt")
                    else "text/html; charset=utf-8")
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
    yield f"http://127.0.0.1:{port}/plant.html", state
    server.shutdown()


@pytest.fixture
def healing_project(tmp_path, site):
    url, state = site
    store.init_project(tmp_path)
    (tmp_path / "spider.yaml").write_text(f"""
project: heal
sources: {{seeds: ["{url}"], keywords: [plant], depth: 0, delay_seconds: 0.0,
           trust_tiers: {{127.0.0.1: 1}}}}
entities:
  plant:
    identity: [scientific_name]
    fields:
      scientific_name: {{type: text, extract: [".sci"]}}
      altitude_m: {{type: range, unit: m, extract: [".altitude"]}}
storage: {{normal_form: 3NF}}
output: {{formats: [csv]}}
""", encoding="utf-8")
    return tmp_path, url, state


def run(args, folder):
    return main(["--project", str(folder), *args])


def test_a_rule_that_stops_matching_is_reported_as_broken(healing_project, capsys):
    root, _url, state = healing_project
    assert run(["crawl"], root) == 0
    assert run(["build", "--no-ai", "--no-export"], root) == 0
    capsys.readouterr()

    state["html"] = REDESIGNED                     # the site is redesigned
    assert run(["crawl", "--refresh"], root) == 0
    assert run(["build", "--no-ai", "--no-export"], root) == 0
    capsys.readouterr()

    assert run(["rules", "check"], root) == 0
    output = capsys.readouterr().out
    assert "BROKEN" in output and "altitude_m" in output


def test_spider_relearns_the_rule_and_the_value_comes_back(healing_project, capsys):
    root, _url, state = healing_project
    run(["crawl"], root)
    run(["build", "--no-ai", "--no-export"], root)
    state["html"] = REDESIGNED
    run(["crawl", "--refresh"], root)
    run(["build", "--no-ai", "--no-export"], root)
    capsys.readouterr()

    conn = store.connect(root)
    assert conn.execute(
        "SELECT COUNT(*) c FROM attributes WHERE name='altitude_m'").fetchone()["c"] == 0
    # what the page used to say is remembered, which is what makes this possible
    assert conn.execute(
        "SELECT value FROM value_history WHERE field='altitude_m'").fetchone() is not None
    conn.close()

    assert run(["rules", "relearn", "altitude_m", "--apply"], root) == 0
    output = capsys.readouterr().out
    assert ".elevation-value" in output          # the specific selector, not `span`
    assert "was: .altitude" in output

    reloaded = Spec.load(root / "spider.yaml")
    rules = reloaded.entities["plant"].fields["altitude_m"].extract
    assert rules[0] == ".elevation-value"
    assert ".altitude" in rules                  # the old rule is kept as a fallback

    run(["crawl", "--refresh"], root)
    run(["build", "--no-ai", "--no-export"], root)
    conn = store.connect(root)
    row = conn.execute(
        "SELECT value FROM attributes WHERE name='altitude_m'").fetchone()
    conn.close()
    assert row["value"] == "3000-4500"


def test_a_label_is_not_mistaken_for_the_value():
    """`span` matches the label first, so it must lose to `.elevation-value`."""
    found = relearn_module.relearn(
        None, Spec.from_dict({"entities": {"plant": {"identity": ["scientific_name"],
                                                     "fields": {"altitude_m": {}}}}}),
        "plant", "altitude_m", {"http://x.test/p": REDESIGNED},
        {"http://x.test/p": "It grows at 3,000 to 4,500 m above sea level."})
    assert found is not None and found.new_rule == ".elevation-value"


def test_relearning_says_what_to_do_when_it_cannot_tell(healing_project, capsys):
    root, _url, _state = healing_project
    assert run(["rules", "relearn", "altitude_m"], root) == 1
    assert "--value" in capsys.readouterr().err


# ------------------------------------------------------------------ preview
def test_preview_shows_rows_and_leaves_the_project_as_it_was(project, spec, capsys):
    root, conn = project
    spec.save(root / "spider.yaml")
    for url, page in (("http://a.test/one", "plant_tier1.html"),
                      ("http://b.test/one", "plant_tier2.html"),
                      ("http://c.test/one", "plant_hindi.html")):
        store_page(conn, url, sample(page), spec, tier=1)
    build(conn, spec, use_ai=False)
    before = conn.execute(
        "SELECT COUNT(*) c FROM attributes WHERE status='accepted'").fetchone()["c"]
    conn.close()
    capsys.readouterr()

    assert run(["preview", "-n", "2", "--no-ai"], root) == 0
    output = capsys.readouterr().out
    assert "Preview" in output and "plant" in output

    conn = store.connect(root)
    after = conn.execute(
        "SELECT COUNT(*) c FROM attributes WHERE status='accepted'").fetchone()["c"]
    conn.close()
    assert after == before, "a preview must not leave a dataset built from 2 pages"


# ----------------------------------------------------------- summaries / AI
def test_summaries_are_stored_and_shown(project, spec, monkeypatch, capsys):
    root, conn = project
    spec.save(root / "spider.yaml")
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    conn.close()

    import spider.extract.ai as ai_module
    monkeypatch.setattr(ai_module, "available", lambda: True)
    monkeypatch.setattr(ai_module, "summarise",
                        lambda conn, page, topic, **k: "An alpine herb of Uttarakhand.")
    capsys.readouterr()
    assert run(["summarise"], root) == 0
    assert "alpine herb" in capsys.readouterr().out

    conn = store.connect(root)
    assert conn.execute("SELECT summary FROM pages").fetchone()["summary"]
    conn.close()
    assert run(["get", "1"], root) == 0
    assert "AI summary" in capsys.readouterr().out


def test_the_ai_relevance_check_can_reject_a_page(project, spec, monkeypatch):
    from spider.crawl.crawler import Crawler
    root, conn = project
    calls = []

    def check(title, text):
        calls.append(title)
        return False, "no schema values here"

    crawler = Crawler(conn, keywords=[], depth=0, max_pages=2, delay=0,
                      relevance_check=check)
    from spider.extract.parser import parse
    page = parse(sample("plant_tier1.html"), "http://a.test/one")
    # the check runs after the cheap keyword filter, in _visit; call it directly
    useful, why = check(page.title, page.text)
    assert not useful and "no schema" in why
    assert calls


# ----------------------------------------------------------- API endpoint
def test_a_json_endpoint_becomes_records(project, spec, monkeypatch):
    from spider import sources as sources_module
    root, conn = project

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"results": [
                {"scientificName": "Saussurea obvallata",
                 "measurement": {"elevation": "3000-4500"}},
                {"scientificName": "Picrorhiza kurroa",
                 "measurement": {"elevation": "3000-4300"}}]}

    monkeypatch.setattr("requests.get", lambda *a, **k: Response())
    item = SourceItem(id="api1", type="api", location="https://api.test/v1/species",
                      tier=1, map={"scientificName": "scientific_name",
                                   "measurement.elevation": "altitude_m"})
    result = sources_module.read_source(conn, spec, item, root)
    assert result.readable and result.rows == 2
    assert "altitude_m" in result.fields_found      # nested JSON was flattened

    build(conn, spec, use_ai=False)
    row = conn.execute(
        "SELECT a.value, a.tier FROM attributes a JOIN entities e ON e.id=a.entity_id "
        "WHERE e.canonical_name='Saussurea obvallata' AND a.name='altitude_m'").fetchone()
    assert row["value"] == "3000-4500" and row["tier"] == 1


def test_a_missing_api_key_is_reported_not_crashed(project, spec, monkeypatch):
    from spider import sources as sources_module
    root, conn = project
    monkeypatch.delenv("SOME_KEY", raising=False)
    item = SourceItem(id="api2", type="api", location="https://api.test/v1",
                      map={"key_env": "SOME_KEY"})
    result = sources_module.read_source(conn, spec, item, root)
    assert not result.readable and "SOME_KEY" in result.note


def test_the_fill_loop_stops_when_coverage_stops_improving(project, spec, capsys):
    """D3: repeat until it stops helping, rather than crawling to a depth limit."""
    root, conn = project
    spec.save(root / "spider.yaml")
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    conn.close()
    capsys.readouterr()

    # no search key and no unvisited links, so a round can add nothing
    assert run(["fill", "--until-stable", "--rounds", "3"], root) == 0
    output = capsys.readouterr().out
    assert "round 1" in output
    assert "stopped improving" in output or "No empty cells" in output
    assert "round 3" not in output, "it should stop early, not run every round"
