"""Spider is for any subject, not for plants.

Every test here is a failure a non-plant project actually hit: values held back
with no explanation, a Himalayan assumption offered as fact, a price silently
turned into rupees, a record that lost its name because it contained a full stop.
"""

import pytest
from conftest import sample, store_page

from spider.assemble.build import build
from spider.cli import main
from spider.extract.extractor import sentence_with
from spider.extract.parser import parse, split_attribute_rule
from spider.ref import tables as ref_tables
from spider.spec import Spec
from spider.store import db as store


def run(args, folder):
    return main(["--project", str(folder), *args])


# ------------------------------------------------ nothing about plants preloaded
def test_a_new_project_starts_with_no_subject_matter(tmp_path):
    store.init_project(tmp_path)
    conn = store.connect(tmp_path)
    assert conn.execute("SELECT COUNT(*) c FROM ref_places").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM ref_vocab").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM ref_authority_ids").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM ref_units").fetchone()["c"] > 0  # units are universal


def test_a_subject_set_is_opt_in(tmp_path):
    store.init_project(tmp_path)
    conn = store.connect(tmp_path)
    assert "himalayan-plants" in ref_tables.presets()
    ref_tables.load_preset(conn, "himalayan-plants")
    assert conn.execute("SELECT COUNT(*) c FROM ref_places").fetchone()["c"] > 0
    with pytest.raises(ValueError, match="no reference preset"):
        ref_tables.load_preset(conn, "no-such-thing")


def test_init_can_load_a_preset(tmp_path):
    assert main(["init", str(tmp_path), "-q", "--preset", "himalayan-plants"]) == 0
    conn = store.connect(tmp_path)
    assert conn.execute("SELECT COUNT(*) c FROM ref_places").fetchone()["c"] > 0


def test_a_vocabulary_with_nothing_in_it_is_an_error_not_a_silent_no_match(tmp_path,
                                                                          capsys):
    """This used to match nothing, forever, and say nothing."""
    store.init_project(tmp_path)
    (tmp_path / "spider.yaml").write_text("""
entities:
  thing:
    identity: [name]
    fields:
      name: {type: text}
      area: {type: text, vocabulary: places}
""", encoding="utf-8")
    assert run(["check"], tmp_path) == 2
    out = capsys.readouterr().out
    assert "vocabulary 'places' has nothing in it" in out
    assert "spider ref load" in out


# ---------------------------------------------- no domain claim is made for you
def build_huts(tmp_path, extra_yaml=""):
    store.init_project(tmp_path)
    (tmp_path / "huts.csv").write_text(
        "name,height_m,opened\nRefuge A,1200,January\nRefuge B,2600,July\n"
        "Refuge C,3400,December\n", encoding="utf-8")
    (tmp_path / "spider.yaml").write_text(f"""
project: huts
{extra_yaml}
sources: {{mode: only_listed, items: [{{id: h, type: csv, location: huts.csv, tier: 0,
          map: {{name: name, height_m: height_m, opened: opened}}}}]}}
entities:
  hut:
    identity: [name]
    fields:
      name: {{type: text}}
      height_m: {{type: range, unit: m}}
      zone: {{type: text}}
      season: {{type: text}}
      opened: {{type: month}}
""", encoding="utf-8")
    assert run(["crawl"], tmp_path) == 0
    assert run(["build", "--no-export"], tmp_path) == 0
    conn = store.connect(tmp_path)
    return conn


def test_a_hut_in_the_alps_is_not_called_subtropical(tmp_path):
    """Himalayan altitude bands were offered to any field called `zone`."""
    conn = build_huts(tmp_path)
    suggested = conn.execute("SELECT * FROM derivations WHERE status='suggested'").fetchall()
    text = " ".join(str(dict(r)) for r in suggested)
    assert "subtropical" not in text and "alpine" not in text
    assert not any(r["name"] == "zone" for r in suggested)


def test_july_is_not_a_monsoon_unless_the_project_says_so(tmp_path):
    conn = build_huts(tmp_path)
    season = conn.execute(
        "SELECT samples FROM derivations WHERE name='season'").fetchone()
    assert "monsoon" not in season["samples"]
    assert "summer" in season["samples"]                  # July, northern calendar


def test_the_suggested_season_names_the_calendar_it_assumed(tmp_path):
    conn = build_huts(tmp_path)
    row = conn.execute("SELECT explain FROM derivations WHERE name='season'").fetchone()
    assert "northern" in row["explain"] and "season_scheme" in row["explain"]


def test_a_project_can_name_the_calendar_it_uses(tmp_path):
    conn = build_huts(tmp_path, "season_scheme: india")
    season = conn.execute("SELECT samples FROM derivations WHERE name='season'").fetchone()
    assert "monsoon" in season["samples"]


def test_a_suggestion_that_stops_being_true_is_removed_on_the_next_build(tmp_path):
    conn = build_huts(tmp_path)
    conn.execute("INSERT INTO derivations(name,entity_type,method,formula,status) "
                 "VALUES('stale','hut','lookup','x','suggested')")
    conn.commit()
    conn.close()
    assert run(["build", "--no-export"], tmp_path) == 0
    conn = store.connect(tmp_path)
    assert conn.execute("SELECT COUNT(*) c FROM derivations WHERE name='stale'"
                        ).fetchone()["c"] == 0


# ---------------------------------------------------------- trust, and holding back
def test_a_site_you_name_yourself_is_not_a_stranger():
    spec = Spec.from_dict({"sources": {"seeds": ["https://books.example.com/start"]},
                           "entities": {"a": {"identity": ["n"], "fields": {"n": {}}}}})
    assert spec.tier_for("https://books.example.com/page/2") == 2
    assert spec.tier_for("https://books.example.com") == 2
    assert spec.tier_for("https://elsewhere.example.net/x") == 3      # a stranger
    assert spec.tier_for("https://data.example.gov") == 1


def test_an_explicit_tier_beats_the_guess():
    spec = Spec.from_dict({"sources": {"seeds": ["https://a.example.com/"],
                                       "trust_tiers": {"a.example.com": 3}},
                           "entities": {"a": {"identity": ["n"], "fields": {"n": {}}}}})
    assert spec.tier_for("https://a.example.com/x") == 3


def test_a_one_site_project_is_not_held_back_by_default(project):
    """With defaults, 600 values used to sit in review and the build reported
    success on zero rows."""
    root, conn = project
    spec = Spec.from_dict({
        "sources": {"seeds": ["http://a.test/"], "keywords": ["plant"]},
        "entities": {"plant": {"identity": ["scientific_name"], "fields": {
            "scientific_name": {"type": "text", "required": True, "extract": [".sci"]},
            "altitude_m": {"type": "range", "unit": "m", "extract": [".altitude"]}}}}})
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=3)
    report = build(conn, spec, use_ai=False)
    assert report.attributes > 0, "nothing reached the dataset with default settings"


def test_when_values_are_held_back_the_build_says_why_and_what_to_do(project, capsys):
    root, conn = project
    spec = Spec.from_dict({
        "sources": {"seeds": ["http://a.test/"], "keywords": ["plant"],
                    "trust_tiers": {"a.test": 3}},
        "entities": {"plant": {"identity": ["scientific_name"], "fields": {
            "scientific_name": {"type": "text", "required": True, "extract": [".sci"]},
            "altitude_m": {"type": "range", "unit": "m", "extract": [".altitude"]}}}}})
    spec.save(root / "spider.yaml")
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=3)
    conn.close()
    assert run(["build", "--no-export", "--no-ai"], root) == 0
    out = capsys.readouterr().out
    assert "NOTHING REACHED THE DATASET" in out
    assert "sources.trust_tiers" in out and "min_confidence" in out


def test_a_trust_change_takes_effect_without_crawling_again(project):
    """Trust is a judgement about a source, not a fact about a page."""
    root, conn = project
    base = {"sources": {"seeds": ["http://a.test/"], "keywords": ["plant"],
                        "trust_tiers": {"a.test": 3}},
            "entities": {"plant": {"identity": ["scientific_name"], "fields": {
                "scientific_name": {"type": "text", "required": True, "extract": [".sci"]},
                "altitude_m": {"type": "range", "unit": "m", "extract": [".altitude"]}}}}}
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"),
               Spec.from_dict(base), tier=3)

    build(conn, Spec.from_dict(base), use_ai=False)
    low = conn.execute("SELECT confidence FROM attributes WHERE name='altitude_m'"
                       ).fetchone()["confidence"]

    base["sources"]["trust_tiers"] = {"a.test": 1}
    build(conn, Spec.from_dict(base), use_ai=False)              # no new crawl
    high = conn.execute("SELECT confidence FROM attributes WHERE name='altitude_m' "
                        "AND status='accepted'").fetchone()["confidence"]
    assert high > low and high == pytest.approx(0.8)


# ---------------------------------------------------------------------- money
def test_a_pound_is_not_silently_turned_into_rupees(project):
    from spider.standardize.engine import Standardizer
    _root, conn = project
    spec = Spec.from_dict({"entities": {"book": {"identity": ["t"], "fields": {
        "t": {"type": "text"}, "price_gbp": {"type": "number", "unit": "gbp",
                                            "sanity": [0, 1000]}}}}})
    result = Standardizer(conn, spec).standardize("book", "price_gbp", "£22.50")
    assert result.value_num == 22.5 and result.unit == "GBP"


# ----------------------------------------------------- reading real page markup
def test_a_value_held_in_an_attribute_can_be_extracted():
    html = ('<html><body><h1>A Book</h1><p class="star-rating Three">x</p>'
            '<a href="mailto:a@b.co">mail</a></body></html>')
    page = parse(html, "http://x.test/b", {"rating": "p.star-rating@class",
                                           "mail": 'a[href^="mailto:"]@href'})
    assert ("rating", "star-rating Three") in page.fields
    assert ("mail", "mailto:a@b.co") in page.fields        # an @ inside the selector


def test_attribute_syntax_leaves_ordinary_selectors_alone():
    assert split_attribute_rule("p.price") == ("p.price", None)
    assert split_attribute_rule("img.cover@alt") == ("img.cover", "alt")
    assert split_attribute_rule('a[href$="x@y"]') == ('a[href$="x@y"]', None)


def test_a_value_read_by_a_selector_is_accepted_without_a_sentence(project):
    """A rating held in a class never appears in the page's text, so quote proof
    rejected it - although a selector read it straight from the markup."""
    root, conn = project
    spec = Spec.from_dict({
        "sources": {"keywords": ["upc"], "trust_tiers": {"a.test": 1}},
        "entities": {"book": {"identity": ["upc"], "fields": {
            "upc": {"type": "text", "required": True, "extract": ["td.upc"]},
            "rating_class": {"type": "text", "extract": ["p.star-rating@class"]}}}}})
    html = ('<html><body><h1>B</h1><table><tr><th>UPC</th><td class="upc">u123</td>'
            '</tr></table><p class="star-rating Four">rating</p></body></html>')
    store_page(conn, "http://a.test/b", html, spec, tier=1)
    build(conn, spec, use_ai=False)
    row = conn.execute("SELECT value, evidence FROM attributes WHERE name='rating_class'"
                       ).fetchone()
    assert row["value"] == "star-rating Four"
    assert "[p.star-rating@class]" in row["evidence"]      # the selector is the evidence


def test_a_page_without_a_required_field_is_not_a_record(project):
    """A listing page has a heading and prices too; only a detail page has a UPC."""
    root, conn = project
    spec = Spec.from_dict({
        "sources": {"keywords": ["book"], "trust_tiers": {"a.test": 1}},
        "entities": {"book": {"identity": ["title"], "fields": {
            "title": {"type": "text", "extract": ["h1"]},
            "upc": {"type": "text", "required": True, "extract": ["td.upc"]}}}}})
    store_page(conn, "http://a.test/list",
               "<html><body><h1>All books</h1><p>book book</p></body></html>", spec, tier=1)
    store_page(conn, "http://a.test/one",
               '<html><body><h1>Real Book</h1><p>book</p><table><tr>'
               '<td class="upc">u9</td></tr></table></body></html>', spec, tier=1)
    build(conn, spec, use_ai=False)
    names = [r["canonical_name"] for r in conn.execute(
        "SELECT canonical_name FROM entities WHERE type='book'")]
    assert names == ["Real Book"]


def test_a_record_is_named_by_its_label_not_its_id(project):
    root, conn = project
    spec = Spec.from_dict({
        "sources": {"keywords": ["book"], "trust_tiers": {"a.test": 1}},
        "entities": {"book": {"identity": ["upc"], "label": "title", "fields": {
            "upc": {"type": "text", "required": True, "extract": ["td.upc"]},
            "title": {"type": "text", "extract": ["h1"]}}}}})
    store_page(conn, "http://a.test/one",
               '<html><body><h1>Real Book</h1><p>book</p><table><tr>'
               '<td class="upc">u9</td></tr></table></body></html>', spec, tier=1)
    build(conn, spec, use_ai=False)
    assert conn.execute("SELECT canonical_name FROM entities").fetchone()[0] == "Real Book"
    alias = conn.execute("SELECT alias FROM entity_aliases WHERE alias='u9'").fetchone()
    assert alias is not None, "the id must stay findable"


def test_evidence_is_found_for_a_value_containing_a_full_stop():
    text = "OpenStreetMap node 5. name: St. Mary's Cafe, latitude: 13.05. cuisine: coffee."
    assert "St. Mary's Cafe" in sentence_with(text, "St. Mary's Cafe")
    assert "13.05" in sentence_with(text, "13.05")
    assert sentence_with(text, "nowhere") == ""


def test_the_user_agent_does_not_invent_a_contact():
    from spider import USER_AGENT
    assert "github.com" not in USER_AGENT and "http" not in USER_AGENT
    assert USER_AGENT.startswith("ProjectSpider/")
