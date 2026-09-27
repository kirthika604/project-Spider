"""Requirements traceability, part 2: trust, privacy, review and the build."""

import json

import pytest
from conftest import sample, store_page

from spider.assemble.build import build
from spider.cli import main
from spider.spec import DerivedSpec, Spec
from spider.store import db as store


def run(args, folder):
    return main(["--project", str(folder), *args])


BASE = {
    "sources": {"keywords": ["plant", "saussurea", "altitude", "grows"], "trust_tiers": {"a.test": 1, "b.test": 2,
                                                       "c.test": 3}},
    "entities": {"plant": {"identity": ["scientific_name"], "fields": {
        "scientific_name": {"type": "text", "required": True, "extract": [".sci"]},
        "altitude_m": {"type": "range", "unit": "m", "sanity": [0, 9000],
                       "extract": [".altitude"]}}}},
}


def spec_with(**over):
    import copy
    d = copy.deepcopy(BASE)
    for key, value in over.items():
        d[key] = value if not isinstance(value, dict) else {**d.get(key, {}), **value}
    return Spec.from_dict(d)


def altitude(conn, status="accepted"):
    return [dict(r) for r in conn.execute(
        "SELECT a.value, a.confidence, a.status, a.tier FROM attributes a "
        "WHERE a.name='altitude_m' AND a.status=? ORDER BY a.confidence DESC", (status,))]


# ------------------------------------------- section 12: `check` rejects a bad tier
def test_S12_check_rejects_an_unknown_trust_tier():
    spec = Spec.from_dict({"sources": {"trust_tiers": {"a.test": 7}},
                           "entities": {"a": {"identity": ["n"], "fields": {"n": {}}}}})
    errors = [p for p in spec.validate() if p.level == "error"]
    assert any("tier" in e.message and "7" in e.message for e in errors)
    good = Spec.from_dict({"sources": {"trust_tiers": {"a.test": 2}},
                           "entities": {"a": {"identity": ["n"], "fields": {"n": {}}}}})
    assert not [p for p in good.validate() if "tier" in p.message]


# ------------------------------------------- section 16: ai_allowed: false is private
class Recorder:
    """Stands in for the AI client and remembers everything it was sent."""

    def __init__(self):
        self.sent = []

    class messages:                                       # noqa: N801
        pass


def stub_ai(monkeypatch, sent):
    import spider.extract.ai as ai_module

    def fake_extract(self, page_row):
        sent.append(page_row["url"])
        return None

    monkeypatch.setattr(ai_module, "available", lambda: True)
    monkeypatch.setattr(ai_module.AIExtractor, "extract", fake_extract)


def test_S16_a_source_marked_ai_allowed_false_is_never_sent_to_the_model(
        project, monkeypatch):
    root, conn = project
    spec = spec_with(sources={"items": [
        {"id": "private_web", "type": "website", "location": "http://a.test/",
         "tier": 1, "ai_allowed": False},
        {"id": "open_web", "type": "website", "location": "http://b.test/", "tier": 2}]})
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://b.test/one", sample("plant_tier2.html"), spec, tier=2)
    sent = []
    stub_ai(monkeypatch, sent)
    build(conn, spec, use_ai=True)
    assert "http://a.test/one" not in sent, "text from an ai_allowed:false source was sent"
    assert "http://b.test/one" in sent


def test_S16_a_file_source_marked_ai_allowed_false_is_never_sent(project, monkeypatch):
    root, conn = project
    (root / "notes.txt").write_text("The plant Saussurea obvallata grows at 3,000 m.",
                                    encoding="utf-8")
    spec = spec_with(sources={"items": [{"id": "notes", "type": "text",
                                         "location": "notes.txt", "tier": 0,
                                         "ai_allowed": False}]})
    spec.path = root / "spider.yaml"
    from spider import sources
    sources.read_source(conn, spec, spec.sources.items[0], root)
    sent = []
    stub_ai(monkeypatch, sent)
    build(conn, spec, use_ai=True)
    assert sent == []


# ------------------------------------------- section 16: authority per field
def test_S16_authoritative_for_decides_which_source_wins_a_conflict(project):
    root, conn = project
    spec = spec_with(
        sources={"items": [
            {"id": "forest_dept", "type": "website", "location": "http://a.test/",
             "tier": 1},
            {"id": "botany_inst", "type": "website", "location": "http://c.test/",
             "tier": 3, "authoritative_for": ["altitude_m"]}]},
        standardize={"on_conflict": "trusted_first"})
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://c.test/one", sample("plant_conflict.html"), spec, tier=3)
    build(conn, spec, use_ai=False)
    winner = altitude(conn)[0]
    assert winner["value"] == "1200-1400", (
        "the source declared authoritative for altitude_m should win, "
        "whatever its tier")


# ------------------------------------------- section 16: tier 0 vs two tier 1 sources
def test_S16_tier_0_that_disagrees_with_two_tier_1_sources_is_flagged_not_silently_used(
        project):
    root, conn = project
    spec = spec_with(
        sources={"trust_tiers": {"a.test": 1, "b.test": 1},
                 "items": [{"id": "mine", "type": "text", "location": "x.txt",
                            "tier": 0}]},
        standardize={"on_conflict": "trusted_first"})
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://b.test/one", sample("plant_tier2.html"), spec, tier=1)
    # a tier 0 page that disagrees with both (stored like a file source's row)
    page_id = store_page(conn, "file:///mine.csv#row=2", sample("plant_conflict.html"),
                         spec, tier=0)
    conn.execute("UPDATE pages SET source_id='mine' WHERE id=?", (page_id,))
    conn.commit()
    build(conn, spec, use_ai=False)
    flagged = conn.execute(
        "SELECT reason FROM review_queue WHERE kind='conflict' AND target LIKE '%altitude%'"
    ).fetchall()
    assert flagged and any("tier 0" in r["reason"].lower() for r in flagged)


# ------------------------------------------- section 6/15: source ranking in the reference
def test_FR_26_the_reference_source_ranking_decides_a_sites_tier(project):
    root, conn = project
    conn.execute("INSERT INTO ref_source_rank(domain,tier,note) VALUES('c.test',1,'listed')")
    conn.commit()
    spec = spec_with(sources={"trust_tiers": {}})           # nothing set in the project file
    store_page(conn, "http://c.test/one", sample("plant_tier1.html"), spec, tier=3)
    build(conn, spec, use_ai=False)
    row = conn.execute("SELECT tier FROM attributes WHERE name='altitude_m' "
                       "AND status IN ('accepted','review')").fetchone()
    assert row["tier"] == 1, "ref_source_rank said tier 1 and nothing overrode it"


def test_FR_26_the_project_file_still_beats_the_reference_ranking(project):
    root, conn = project
    conn.execute("INSERT INTO ref_source_rank(domain,tier,note) VALUES('c.test',1,'listed')")
    conn.commit()
    spec = spec_with(sources={"trust_tiers": {"c.test": 3}})
    store_page(conn, "http://c.test/one", sample("plant_tier1.html"), spec, tier=3)
    build(conn, spec, use_ai=False)
    assert conn.execute("SELECT tier FROM attributes WHERE name='altitude_m'"
                        ).fetchone()["tier"] == 3


# ------------------------------------------- FR-28: the standardization report
def test_FR_28_the_report_shows_what_standardization_changed(project, capsys):
    root, conn = project
    spec = spec_with()
    spec.save(root / "spider.yaml")
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://c.test/one", sample("plant_hindi.html"), spec, tier=3)
    conn.close()
    assert run(["build", "--no-export", "--no-ai"], root) == 0
    capsys.readouterr()
    assert run(["report"], root) == 0
    out = capsys.readouterr().out
    assert "Standardization" in out
    assert "units converted" in out and "9,842 ft" in out, \
        "each change is listed with a count and an example row"
