"""Politeness and crawl bookkeeping (NFR-1, NFR-4, FR-11, FR-12)."""

import http.server
import socket
import threading
from pathlib import Path

import pytest
from conftest import SAMPLES

from spider.crawl.crawler import Crawler
from spider.crawl.robots import Politeness


@pytest.fixture
def site(tmp_path):
    """A real HTTP server over the sample pages, on a free port."""
    folder = tmp_path / "site"
    folder.mkdir()
    for name in ("plant_tier1.html", "plant_tier2.html", "index.html"):
        (folder / name).write_text((SAMPLES / name).read_text(encoding="utf-8"),
                                   encoding="utf-8")
    (folder / "robots.txt").write_text(
        "User-agent: *\nDisallow: /secret/\nAllow: /\n", encoding="utf-8")
    (folder / "secret").mkdir()
    (folder / "secret" / "page.html").write_text(
        "<html><body><p>plant secret</p></body></html>", encoding="utf-8")

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(
        *a, directory=str(folder), **k)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_robots_txt_is_obeyed(site):
    politeness = Politeness(default_delay=0)
    assert politeness.allowed(f"{site}/index.html")
    assert not politeness.allowed(f"{site}/secret/page.html")
    assert "127.0.0.1" in list(politeness.blocked)[0]


def test_a_blocked_page_is_counted_and_the_crawl_continues(project, site):
    _root, conn = project
    crawler = Crawler(conn, keywords=["plant"], depth=0, max_pages=10, delay=0)
    result = crawler.run([f"{site}/secret/page.html", f"{site}/plant_tier1.html"])
    assert result.blocked == 1 and result.saved == 1


def test_a_page_already_stored_is_skipped_unless_refreshed(project, site):
    _root, conn = project
    first = Crawler(conn, keywords=["plant"], depth=0, max_pages=5, delay=0)
    assert first.run([f"{site}/plant_tier1.html"]).saved == 1

    again = Crawler(conn, keywords=["plant"], depth=0, max_pages=5, delay=0)
    second = again.run([f"{site}/plant_tier1.html"])
    assert second.saved == 0 and second.skipped_existing == 1

    refreshed = Crawler(conn, keywords=["plant"], depth=0, max_pages=5, delay=0,
                        refresh=True)
    assert refreshed.run([f"{site}/plant_tier1.html"]).saved == 1
    assert conn.execute("SELECT COUNT(*) c FROM pages").fetchone()["c"] == 1


def test_a_content_hash_is_recorded_for_change_detection(project, site):
    _root, conn = project
    Crawler(conn, keywords=["plant"], depth=0, max_pages=5, delay=0).run(
        [f"{site}/plant_tier1.html"])
    row = conn.execute("SELECT content_hash FROM pages").fetchone()
    assert row["content_hash"] and len(row["content_hash"]) == 32


def test_every_crawl_run_is_recorded(project, site):
    _root, conn = project
    Crawler(conn, keywords=["plant"], depth=1, max_pages=7, delay=0).run(
        [f"{site}/index.html"])
    run = conn.execute("SELECT * FROM crawls ORDER BY id DESC LIMIT 1").fetchone()
    assert run["seeds"] and "plant" in run["keywords"]
    assert run["depth"] == 1 and run["max_pages"] == 7
    assert run["pages_saved"] >= 1 and run["finished_at"]


def test_an_unreachable_page_does_not_stop_the_crawl(project, site):
    _root, conn = project
    crawler = Crawler(conn, keywords=["plant"], depth=0, max_pages=5, delay=0)
    result = crawler.run(["http://127.0.0.1:9/nothing", f"{site}/plant_tier1.html"])
    assert result.failed == 1 and result.saved == 1
    assert result.errors


def test_the_delay_is_respected_per_domain(site):
    import time
    politeness = Politeness(default_delay=0.4)
    politeness.wait(f"{site}/a")
    started = time.time()
    politeness.wait(f"{site}/b")
    assert time.time() - started >= 0.35


def test_a_second_crawl_still_follows_links_from_stored_pages(project, site):
    """Skipping a stored page must not hide the links on it, or a resumed
    crawl could never reach anything deeper."""
    _root, conn = project
    first = Crawler(conn, keywords=["plant"], depth=0, max_pages=5, delay=0)
    first.run([f"{site}/index.html"])
    assert conn.execute("SELECT COUNT(*) c FROM pages").fetchone()["c"] == 1

    deeper = Crawler(conn, keywords=["plant"], depth=1, max_pages=5, delay=0)
    result = deeper.run([f"{site}/index.html"])
    assert result.skipped_existing == 1
    assert result.saved >= 1, "the linked page should have been found and saved"
    urls = [r["url"] for r in conn.execute("SELECT url FROM pages")]
    assert any("plant_tier1.html" in u for u in urls)


def test_several_sites_are_crawled_at_once_without_breaking_politeness(project,
                                                                       tmp_path):
    """A one-second delay per domain should not mean one second per page when
    the pages are on different sites."""
    import time

    servers, seeds = [], []
    for index in range(4):
        folder = tmp_path / f"site{index}"
        folder.mkdir()
        (folder / "index.html").write_text(
            f"<html><body><p>plant number {index}</p></body></html>", encoding="utf-8")
        (folder / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        handler = lambda *a, d=str(folder), **k: http.server.SimpleHTTPRequestHandler(
            *a, directory=d, **k)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        seeds.append(f"http://127.0.0.1:{port}/index.html")

    _root, conn = project
    try:
        crawler = Crawler(conn, keywords=["plant"], depth=0, max_pages=8, delay=1.0,
                          any_domain=True, workers=4)
        started = time.time()
        result = crawler.run(seeds)
        elapsed = time.time() - started
    finally:
        for server in servers:
            server.shutdown()

    assert result.saved == 4
    assert elapsed < 2.5, f"four sites took {elapsed:.1f}s; they should overlap"


def test_one_site_is_still_crawled_politely_when_workers_are_allowed(project, site):
    """Parallelism must never collapse the delay for a single domain."""
    import time

    _root, conn = project
    crawler = Crawler(conn, keywords=["plant"], depth=1, max_pages=3, delay=0.6,
                      workers=4)
    started = time.time()
    result = crawler.run([f"{site}/index.html"])
    elapsed = time.time() - started
    assert result.saved >= 2
    assert elapsed >= 0.6 * (result.saved - 1), (
        f"{result.saved} pages from one site in {elapsed:.2f}s is too fast")
