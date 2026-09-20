"""Parser, relevance scorer and URL rules (FR-2 to FR-8)."""

from conftest import sample

from spider.crawl.frontier import Frontier, domain_of, is_crawlable, normalise
from spider.extract.parser import parse, score_relevance


def test_parse_reads_metadata_headings_and_structured_data():
    page = parse(sample("plant_tier1.html"), "http://a.test/p.html",
                 {"altitude_m": [".altitude"]})
    assert page.title.startswith("Saussurea obvallata")
    assert page.description == "An alpine herb"
    assert page.author == "Institute"
    assert page.lang == "en"
    assert "Saussurea obvallata" in page.headings
    assert page.structured and page.structured[0][0] == "Plant"
    assert ("altitude_m", "It grows at 3,000 to 4,500 m above sea level.") in page.fields


def test_clean_text_drops_navigation_and_footers():
    page = parse(sample("plant_tier1.html"), "http://a.test/p.html")
    assert "menu links here" not in page.text
    assert "copyright junk" not in page.text
    assert "3,000 to 4,500 m" in page.text


def test_relevance_counts_text_hits_and_title_hits():
    page = parse(sample("plant_tier1.html"), "http://a.test/p.html")
    assert score_relevance(page, []) == 1.0              # no keywords means keep
    assert score_relevance(page, ["plant"]) > 0
    assert score_relevance(page, ["submarine"]) == 0     # nothing to save


def test_regex_extraction_rule():
    # only "4,500" is followed by the unit in "3,000 to 4,500 m"
    page = parse(sample("plant_tier1.html"), "http://a.test/p.html",
                 {"altitude_m": [r"regex:(\d[\d,]*)\s*(?:m|metres)"]})
    assert ("altitude_m", "4,500") in page.fields

    both = parse(sample("plant_tier1.html"), "http://a.test/p.html",
                 {"altitude_m": [r"regex:\d[\d,]*\s*to\s*\d[\d,]*\s*m"]})
    assert ("altitude_m", "3,000 to 4,500 m") in both.fields


def test_a_regex_with_several_groups_keeps_the_whole_match():
    """`(\d+) to (\d+) m` describes one range, not two separate values - found
    against a live page where only the lower bound was being stored."""
    html = ("<html><body><p>plant</p>"
            "<p>It grows at altitudes of 3,700 to 4,600 m.</p></body></html>")
    page = parse(html, "http://a.test/p", {
        "altitude_m": [r"regex:(\d[\d,]{2,})\s*(?:to|-)\s*(\d[\d,]{2,})\s*m\b"]})
    assert ("altitude_m", "3,700 to 4,600 m") in page.fields

    from spider.standardize.units import parse_value
    assert parse_value("3,700 to 4,600 m", "m") == (3700.0, 4600.0, "m")


def test_url_rules():
    assert normalise("http://a.test/x/#top") == "http://a.test/x"
    assert domain_of("http://www.A.test/p") == "a.test"
    assert is_crawlable("http://a.test/p.html")
    assert not is_crawlable("http://a.test/photo.jpg")
    assert not is_crawlable("mailto:someone@a.test")


def test_frontier_keeps_to_seed_domains_and_depth():
    frontier = Frontier(["http://a.test/"], max_depth=1)
    frontier.add_links("http://a.test/", ["/one", "http://b.test/two", "pic.png"], 0)
    urls = [url for url, _depth, _tier in frontier.queue]
    assert "http://a.test/one" in urls
    assert "http://b.test/two" not in urls               # off the seed domain
    assert not any(u.endswith(".png") for u in urls)

    wide = Frontier(["http://a.test/"], max_depth=1, any_domain=True)
    wide.add_links("http://a.test/", ["http://b.test/two"], 0)
    assert any("b.test" in url for url, _d, _t in wide.queue)


def test_frontier_respects_the_depth_limit():
    frontier = Frontier(["http://a.test/"], max_depth=0)
    assert frontier.add_links("http://a.test/", ["/deeper"], 0) == 0
