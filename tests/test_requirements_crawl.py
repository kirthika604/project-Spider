"""Requirements traceability, part 1: the crawler and the store.

One test per requirement in the design document, named after its ID, checking
what a user would see rather than what the code happens to contain.
"""

import json
import sqlite3
import time

import pytest

from spider.cli import main
from spider.crawl.crawler import Crawler
from spider.store import db as store


def urls(conn):
    return [r["url"] for r in conn.execute("SELECT url FROM pages ORDER BY id")]


def run(args, folder):
    return main(["--project", str(folder), *args])


def clear(conn):
    for table in ("links", "fields", "structured", "pages_fts"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM pages")
    conn.commit()


# ---------------------------------------------------------------------- FR-1
def test_FR_1_init_creates_the_database_and_is_found_from_subfolders(tmp_path):
    assert main(["init", str(tmp_path), "-q"]) == 0
    assert (tmp_path / ".spider" / "spider.db").exists()
    deep = tmp_path / "src" / "app" / "core"
    deep.mkdir(parents=True)
    assert store.find_project(deep) == tmp_path


# ---------------------------------------------------------------------- FR-2
def test_FR_2_several_seeds_are_traversed_breadth_first(project, multisite):
    _root, conn = project
    order = []
    crawler = Crawler(conn, keywords=["plant"], depth=3, max_pages=20, delay=0,
                      workers=1, on_event=lambda kind, **i: order.append(i["url"])
                      if kind in ("saved", "irrelevant") else None)
    crawler.run([f"{multisite['a']}/index.html", f"{multisite['a']}/c.html"])
    depth_of = {"index": 0, "c": 0, "a": 1, "b": 1, "d": 2, "e": 1}   # from the seeds
    seen = [u.rsplit("/", 1)[1].split(".")[0] for u in order]
    levels = [depth_of.get(s, 9) for s in seen]
    assert levels == sorted(levels), f"not breadth first: {seen}"


# ---------------------------------------------------------------------- FR-3
def test_FR_3_depth_max_pages_and_delay_are_honoured(project, multisite):
    _root, conn = project
    shallow = Crawler(conn, keywords=["plant"], depth=1, max_pages=50, delay=0)
    shallow.run([f"{multisite['a']}/index.html"])
    assert not any(u.endswith("/c.html") for u in urls(conn))    # depth 2, beyond the limit

    clear(conn)
    capped = Crawler(conn, keywords=[], depth=3, max_pages=2, delay=0)
    result = capped.run([f"{multisite['a']}/index.html"])
    assert result.saved == 2

    slow = Crawler(conn, keywords=["plant"], depth=1, max_pages=50, delay=0.5,
                   refresh=True, workers=1)
    started = time.time()
    slow.run([f"{multisite['a']}/a.html", f"{multisite['a']}/c.html"])
    assert time.time() - started >= 0.45


def test_FR_3_the_flags_exist_on_the_command_line(tmp_path):
    main(["init", str(tmp_path), "-q"])
    from spider.cli import build_parser
    parsed = build_parser().parse_args(["crawl", "http://x.test", "-d", "3", "-n", "7",
                                        "--delay", "2.5"])
    assert (parsed.depth, parsed.max_pages, parsed.delay) == (3, 7, 2.5)


# ---------------------------------------------------------------------- FR-4
def test_FR_4_only_relevant_pages_are_saved_but_links_are_followed_anyway(project,
                                                                          multisite):
    _root, conn = project
    Crawler(conn, keywords=["plant"], depth=2, max_pages=20, delay=0).run(
        [f"{multisite['a']}/index.html"])
    saved = urls(conn)
    assert not any(u.endswith("/b.html") for u in saved), "b.html has no keyword"
    assert any(u.endswith("/d.html") for u in saved), "but its link must still be followed"
    assert all(r["relevance"] > 0 for r in conn.execute("SELECT relevance FROM pages"))


# ---------------------------------------------------------------------- FR-5
def test_FR_5_stays_on_seed_domains_unless_widened(project, multisite):
    _root, conn = project
    # the index links to localhost only via absolute URLs we add for this test
    base = Crawler(conn, keywords=["plant"], depth=1, max_pages=20, delay=0)
    base.run([f"{multisite['a']}/a.html"])
    assert all(multisite["a"] in u for u in urls(conn))

    from spider.crawl.frontier import Frontier
    tight = Frontier([f"{multisite['a']}/a.html"], max_depth=1)
    tight.add_links(f"{multisite['a']}/a.html", [f"{multisite['b']}/c.html"], 0)
    assert len(tight) == 1                                   # the other host was refused
    wide = Frontier([f"{multisite['a']}/a.html"], max_depth=1, any_domain=True)
    wide.add_links(f"{multisite['a']}/a.html", [f"{multisite['b']}/c.html"], 0)
    assert len(wide) == 2
    listed = Frontier([f"{multisite['a']}/a.html"], max_depth=1,
                      allowed_domains=["localhost"])
    listed.add_links(f"{multisite['a']}/a.html", [f"{multisite['b']}/c.html"], 0)
    assert len(listed) == 2


def test_FR_5_the_flags_exist_on_the_command_line():
    from spider.cli import build_parser
    parsed = build_parser().parse_args(["crawl", "http://x.test", "--any-domain",
                                        "--domains", "a.test,b.test"])
    assert parsed.any_domain and parsed.domains == "a.test,b.test"


# ---------------------------------------------------------------------- FR-6
def test_FR_6_every_listed_page_property_is_extracted(project, multisite):
    _root, conn = project
    Crawler(conn, keywords=["plant"], depth=0, max_pages=5, delay=0).run(
        [f"{multisite['a']}/a.html"])
    row = conn.execute("SELECT * FROM pages").fetchone()
    assert row["title"] == "Alpha plant page"
    assert row["description"] == "About the alpha plant"
    assert row["author"] == "Dr Rao"
    assert row["published"] == "2025-06-14"
    assert row["lang"] == "en"
    assert "Alpha" in row["headings"] and "Habitat" in row["headings"]
    assert "menu" not in row["text"] and "footer junk" not in row["text"]
    assert row["word_count"] == len(row["text"].split()) > 5


# ---------------------------------------------------------------------- FR-7
def test_FR_7_custom_rules_are_stored_as_name_value_rows(tmp_path, multisite):
    main(["init", str(tmp_path), "-q"])
    assert run(["crawl", f"{multisite['a']}/a.html", "-d", "0", "--delay", "0",
                "-e", "altitude=p.alt", "-e", "heading=h1"], tmp_path) == 0
    conn = store.connect(tmp_path)
    fields = {(r["name"], r["value"]) for r in conn.execute("SELECT * FROM fields")}
    assert ("altitude", "Grows at 3,000 to 4,500 m.") in fields
    assert ("heading", "Alpha") in fields


# ---------------------------------------------------------------------- FR-8
def test_FR_8_json_ld_is_captured_as_structured_data(project, multisite):
    _root, conn = project
    Crawler(conn, keywords=["plant"], depth=0, max_pages=5, delay=0).run(
        [f"{multisite['a']}/a.html"])
    row = conn.execute("SELECT * FROM structured").fetchone()
    assert row["type"] == "Plant" and json.loads(row["data"])["name"] == "Alpha"


# ---------------------------------------------------------------------- FR-9
def test_FR_9_search_is_ranked_and_highlights_snippets(tmp_path, multisite, capsys):
    main(["init", str(tmp_path), "-q"])
    run(["crawl", f"{multisite['a']}/index.html", "-k", "plant", "-d", "3",
         "--delay", "0"], tmp_path)
    capsys.readouterr()
    assert run(["search", "plant"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "[plant]" in out.lower() or "[Plant]" in out, "matches are highlighted"
    from spider.search import search
    conn = store.connect(tmp_path)
    ranked = search(conn, "plant rare")
    assert ranked and ranked[0]["rank"] <= ranked[-1]["rank"], "best match first"
    assert "alpha" in ranked[0]["title"].lower()


# --------------------------------------------------------------------- FR-10
def test_FR_10_get_export_and_readonly_sql(tmp_path, multisite, capsys):
    main(["init", str(tmp_path), "-q"])
    run(["crawl", f"{multisite['a']}/a.html", "-k", "plant", "-d", "0", "--delay", "0"],
        tmp_path)
    capsys.readouterr()
    assert run(["get", "1"], tmp_path) == 0
    assert "Alpha plant page" in capsys.readouterr().out

    for form in ("text", "json", "csv"):                    # the documented formats
        assert run(["export", "--pages", "-f", form], tmp_path) == 0, form
        out = capsys.readouterr().out
        assert "Alpha" in out, f"{form} export lost the page"
    assert json.loads(_last_json(capsys, tmp_path))[0]["title"] == "Alpha plant page"

    assert run(["sql", "select count(*) n from pages"], tmp_path) == 0
    assert run(["sql", "delete from pages"], tmp_path) == 1
    conn = store.connect(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 1


def _last_json(capsys, tmp_path):
    run(["export", "--pages", "-f", "json"], tmp_path)
    return capsys.readouterr().out


def test_FR_10_the_documented_export_command_works_on_a_plain_project(tmp_path,
                                                                     multisite, capsys):
    """`spider export -f csv > data.csv` with no spider.yaml is the MVP command."""
    main(["init", str(tmp_path), "-q"])
    run(["crawl", f"{multisite['a']}/a.html", "-k", "plant", "-d", "0", "--delay", "0"],
        tmp_path)
    capsys.readouterr()
    assert run(["export", "-f", "csv"], tmp_path) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("id,") and "Alpha" in out


# --------------------------------------------------------------------- FR-11
def test_FR_11_stored_urls_are_skipped_refresh_refetches_hash_is_recorded(project,
                                                                          multisite):
    _root, conn = project
    make = lambda **k: Crawler(conn, keywords=["plant"], depth=0, max_pages=5,
                               delay=0, **k)
    make().run([f"{multisite['a']}/a.html"])
    before = len(multisite["hits"])
    second = make().run([f"{multisite['a']}/a.html"])
    assert second.skipped_existing == 1 and len(multisite["hits"]) == before
    make(refresh=True).run([f"{multisite['a']}/a.html"])
    assert len(multisite["hits"]) > before
    assert len(conn.execute("SELECT content_hash FROM pages").fetchone()[0]) >= 16


# --------------------------------------------------------------------- FR-12
def test_FR_12_every_crawl_run_is_recorded(tmp_path, multisite):
    main(["init", str(tmp_path), "-q"])
    run(["crawl", f"{multisite['a']}/index.html", "-k", "plant", "-d", "2", "-n", "9",
         "--delay", "0"], tmp_path)
    run(["crawl", f"{multisite['a']}/c.html", "-d", "0", "--delay", "0"], tmp_path)
    conn = store.connect(tmp_path)
    rows = conn.execute("SELECT * FROM crawls ORDER BY id").fetchall()
    assert len(rows) == 2
    first = rows[0]
    assert "index.html" in first["seeds"] and "plant" in first["keywords"]
    assert first["depth"] == 2 and first["max_pages"] == 9 and first["pages_saved"] >= 1


# ---------------------------------------------------------------------- NFR-1
def test_NFR_1_robots_txt_crawl_delay_default_and_user_agent(project, multisite):
    from spider import USER_AGENT
    from spider.crawl.robots import Politeness
    _root, conn = project
    assert Politeness().default_delay == 1.0
    assert USER_AGENT.startswith("ProjectSpider/") and len(USER_AGENT) > 10
    Crawler(conn, keywords=[], depth=2, max_pages=20, delay=0).run(
        [f"{multisite['a']}/index.html"])
    assert not any("/private/" in u for u in urls(conn))
    assert "/private/x.html" not in multisite["hits"], "a disallowed page was requested"


# ---------------------------------------------------------------------- NFR-2
def test_NFR_2_search_over_10000_pages_returns_in_under_a_second(tmp_path):
    store.init_project(tmp_path)
    conn = store.connect(tmp_path)
    conn.execute("INSERT INTO crawls(id,started_at) VALUES(1,'now')")
    words = ["plant", "herb", "river", "stone", "flora", "moss", "peak", "trail"]
    rows, fts = [], []
    for i in range(10000):
        text = " ".join(words[(i + k) % 8] for k in range(60)) + f" unique{i}"
        rows.append((i + 1, 1, f"http://x.test/{i}", "x.test", f"Page {i}", text, 60, 1.0))
        fts.append((i + 1, f"Page {i}", "", "", text))
    conn.executemany("INSERT INTO pages(id,crawl_id,url,domain,title,text,word_count,"
                     "relevance) VALUES(?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO pages_fts(rowid,title,description,headings,text) "
                     "VALUES(?,?,?,?,?)", fts)
    conn.commit()
    from spider.search import search
    started = time.time()
    results = search(conn, "plant herb", limit=10)
    elapsed = time.time() - started
    assert len(results) == 10 and elapsed < 1.0, f"{elapsed:.2f}s"


# ---------------------------------------------------------------------- NFR-3
def test_NFR_3_the_code_uses_nothing_newer_than_python_3_9():
    """Runs on Python 3.9+: no `match`, no runtime `X | Y`, no 3.10 dataclass args."""
    import ast
    import pathlib
    problems = []
    for path in pathlib.Path("spider").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        annotation_nodes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Match if hasattr(ast, "Match") else ()):
                problems.append(f"{path}: match statement")
            for attr in ("annotation", "returns"):
                anno = getattr(node, attr, None)
                if anno is not None:
                    annotation_nodes.update(id(n) for n in ast.walk(anno))
        if "from __future__ import annotations" not in text:
            # without the future import annotations are evaluated: `X | Y` breaks 3.9
            for node in ast.walk(tree):
                if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
                        and id(node) in annotation_nodes):
                    problems.append(f"{path}:{node.lineno}: X | Y in an annotation")
        for node in ast.walk(tree):
            if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
                    and id(node) not in annotation_nodes):
                # a runtime union like `str | None` outside an annotation
                sides = [getattr(s, "id", getattr(s, "value", None)) for s in
                         (node.left, node.right)]
                if any(s in ("None", None) and isinstance(getattr(x, "value", 0), type(None))
                       for s, x in ((sides[0], node.left), (sides[1], node.right))):
                    problems.append(f"{path}:{node.lineno}: runtime union with None")
        if "slots=True" in text or "kw_only=True" in text:
            problems.append(f"{path}: 3.10-only dataclass argument")
    assert not problems, problems


def test_NFR_3_the_required_dependencies_are_few_and_the_rest_are_optional():
    import pathlib
    import re
    text = pathlib.Path("pyproject.toml").read_text()
    required = re.search(r"dependencies = \[(.*?)\]", text, re.S).group(1)
    names = re.findall(r'"([A-Za-z0-9_.-]+)', required)
    assert set(names) <= {"requests", "beautifulsoup4", "PyYAML"}, names
    assert "python_requires" not in text and 'requires-python = ">=3.9"' in text


# ---------------------------------------------------------------------- NFR-4
def test_NFR_4_a_failed_page_never_stops_a_crawl_and_data_is_committed_per_page(
        project, multisite):
    root, conn = project
    seeds = ["http://127.0.0.1:9/nothing", f"{multisite['a']}/a.html",
             f"{multisite['a']}/missing.html", f"{multisite['a']}/c.html"]
    saved_when_stopped = []

    def stop_after_two(kind, **info):
        if kind == "saved":
            saved_when_stopped.append(info["url"])
            if len(saved_when_stopped) == 2:
                raise KeyboardInterrupt          # the user hits Ctrl-C mid-crawl

    crawler = Crawler(conn, keywords=["plant"], depth=0, max_pages=10, delay=0,
                      workers=1, on_event=stop_after_two)
    with pytest.raises(KeyboardInterrupt):
        crawler.run(seeds)
    fresh = sqlite3.connect(root / ".spider" / "spider.db")      # what is on disk
    assert fresh.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 2


# ---------------------------------------------------------------------- NFR-5
def test_NFR_5_nothing_is_sent_anywhere_the_user_did_not_configure(project, multisite,
                                                                   monkeypatch):
    """A crawl talks to the sites it was pointed at and to nobody else: no
    telemetry, no analytics, no update check."""
    import requests
    _root, conn = project
    contacted = set()
    original = requests.Session.request

    def spy(self, method, url, *args, **kwargs):
        contacted.add(url.split("/")[2])
        return original(self, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", spy)
    Crawler(conn, keywords=["plant"], depth=1, max_pages=5, delay=0).run(
        [f"{multisite['a']}/a.html"])
    assert contacted == {f"127.0.0.1:{multisite['port']}"}, contacted


def test_NFR_5_the_dashboard_is_configured_with_telemetry_off():
    import pathlib
    assert "gatherUsageStats = false" in pathlib.Path(".streamlit/config.toml").read_text()
    from spider import cli
    import inspect
    assert "GATHER_USAGE_STATS" in inspect.getsource(cli.cmd_dashboard)


# ---------------------------------------------------------------------- NFR-7
def test_NFR_7_sql_is_read_only_and_pages_are_not_touched_outside_crawling(
        tmp_path, multisite, capsys):
    main(["init", str(tmp_path), "-q"])
    run(["crawl", f"{multisite['a']}/a.html", "-k", "plant", "-d", "0", "--delay", "0"],
        tmp_path)
    for statement in ("delete from pages", "drop table pages", "update pages set title='x'",
                      "insert into pages(url) values('x')", "pragma writable_schema=1",
                      "with x as (select 1) delete from pages"):
        assert run(["sql", statement], tmp_path) != 0, statement
    conn = store.connect(tmp_path)
    assert conn.execute("SELECT title FROM pages").fetchone()[0] == "Alpha plant page"
    # a build recomputes derived tables but must leave the collected pages alone
    (tmp_path / "spider.yaml").write_text(
        "entities: {p: {identity: [n], fields: {n: {type: text, extract: ['h1']}}}}\n")
    before = conn.execute("SELECT COUNT(*), SUM(word_count) FROM pages").fetchone()
    run(["build", "--no-export", "--no-ai"], tmp_path)
    assert tuple(conn.execute("SELECT COUNT(*), SUM(word_count) FROM pages").fetchone()) \
        == tuple(before)
