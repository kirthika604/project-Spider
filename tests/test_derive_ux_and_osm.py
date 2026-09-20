"""Defining a derived column without knowing the system, and discovering places."""

import pytest
from conftest import sample, store_page

from spider import yamltext
from spider.assemble.build import build
from spider.cli import main
from spider.derive import catalogue
from spider.derive.formula import CONSTANTS, FUNCTIONS, evaluate
from spider.derive.preview import preview
from spider.derive.statistics import COLUMN_FUNCTIONS
from spider.spec import Spec, SourceItem
from spider.store import db as store


def run(args, folder):
    return main(["--project", str(folder), *args])


# ------------------------------------------------------------ the catalogue
def test_every_function_the_evaluator_accepts_is_documented():
    accepted = (set(FUNCTIONS) - {"if_"}) | set(CONSTANTS) | set(COLUMN_FUNCTIONS) \
        | {"count", "place_parent"}
    assert accepted - set(catalogue.BY_NAME) == set(), "undocumented functions"
    assert set(catalogue.BY_NAME) - accepted == set(), "documented but not real"


def test_every_documented_example_that_can_run_gives_the_result_it_claims():
    """An example that says `-> monsoon` must say what it assumed, and be true."""
    checked = 0
    for entry in catalogue.CATALOGUE:
        if not entry.result or " over " in entry.example or " via " in entry.example:
            continue
        got = evaluate(entry.example, dict(entry.given))
        try:                                     # 4 and 4.0 are the same answer
            same = abs(float(got) - float(entry.result)) < 1e-9
        except (TypeError, ValueError):
            same = str(got).lower() == entry.result.lower()
        assert same, f"{entry.example} gave {got!r}, documented as {entry.result!r}"
        checked += 1
    assert checked >= 25


def test_an_example_that_depends_on_a_field_says_which_value_it_assumed():
    import re
    for entry in catalogue.CATALOGUE:
        outside_quotes = re.sub(r"'[^']*'", "", entry.example)     # 'rating' the word is not a field
        if entry.result and any(re.search(rf"\b{name}\b", outside_quotes) for name in
                                ("price", "rating", "score", "nickname", "born",
                                 "code", "month", "altitude_m", "availability")):
            assert entry.given, f"{entry.name}: its result depends on a field but " \
                                f"does not say what the field is"


def test_derive_functions_lists_and_searches(capsys, tmp_path):
    assert main(["derive", "functions"]) == 0        # needs no project
    out = capsys.readouterr().out
    assert "zscore(field over entity)" in out and "COMMON RECIPES" in out
    assert main(["derive", "functions", "distance"]) == 0
    assert "distance_km" in capsys.readouterr().out
    assert main(["derive", "functions", "zzzzz"]) == 1


# ------------------------------------------------- a formula is checked first
@pytest.fixture
def priced(project):
    root, conn = project
    spec = Spec.from_dict({
        "sources": {"keywords": ["plant"], "trust_tiers": {"a.test": 1}},
        "entities": {"plant": {"identity": ["scientific_name"], "fields": {
            "scientific_name": {"type": "text", "required": True, "extract": [".sci"]},
            "altitude_m": {"type": "range", "unit": "m", "extract": [".altitude"]}}}}})
    spec.save(root / "spider.yaml")
    store_page(conn, "http://a.test/1", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://a.test/2", sample("plant_tier2.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    return root, conn, spec


def test_try_shows_real_values_for_real_records(priced):
    _root, conn, spec = priced
    outcome = preview(conn, spec, "plant", "altitude_m.max - altitude_m.min")
    assert outcome.ok and outcome.filled >= 1
    assert outcome.samples[0][1] == 1500


def test_a_misspelt_field_is_caught_with_a_suggestion(priced):
    _root, conn, spec = priced
    outcome = preview(conn, spec, "plant", "altitude_mm * 2")
    assert not outcome.ok
    assert outcome.unknown == [("altitude_mm", "altitude_m")]


def test_a_formula_that_cannot_be_read_says_so(priced):
    _root, conn, spec = priced
    assert preview(conn, spec, "plant", "altitude_m +* 2").syntax_error
    message = preview(conn, spec, "plant", "__import__('os')").syntax_error
    assert "no function called '__import__'" in message
    assert "did you mean 'sqrt'" in preview(conn, spec, "plant", "sqroot(4)").syntax_error


def test_check_treats_a_misspelt_field_as_an_error_not_a_warning():
    spec = Spec.from_dict({"entities": {"book": {"identity": ["t"], "fields": {
        "t": {"type": "text"}, "price": {"type": "number"}}}},
        "derived": {"x": {"on": "book", "formula": "prise * 2"}}})
    errors = [p for p in spec.validate() if p.level == "error"]
    assert any("prise" in e.message and "did you mean 'price'" in e.fix for e in errors)


def test_a_unit_written_as_text_is_not_mistaken_for_a_field():
    spec = Spec.from_dict({"entities": {"p": {"identity": ["n"], "fields": {
        "n": {"type": "text"}, "alt": {"type": "number", "unit": "m"}}}},
        "derived": {"ft": {"on": "p", "formula": "convert(alt, 'm', 'ft')"}}})
    assert spec.derived["ft"].referenced_fields() == ["alt"]
    assert [p for p in spec.validate() if p.level == "error"] == []


def test_a_derivation_that_fills_nothing_is_reported_with_the_reason(priced, capsys):
    root, conn, spec = priced
    spec.raw["derived"] = {"never": {"on": "plant", "formula": "altitude_m.min * 2",
                                     "inputs": ["altitude_m", "no_such_input"]}}
    spec.derived["never"] = __import__("spider").spec.DerivedSpec.parse(
        "never", spec.raw["derived"]["never"])
    report = build(conn, spec, use_ai=False)
    filled, total, missing = report.derive_detail["never"]
    assert filled == 0 and total >= 1 and "no_such_input" in missing


# ---------------------------------------------------------- add, via the CLI
def make_project(tmp_path):
    store.init_project(tmp_path)
    (tmp_path / "spider.yaml").write_text("""# my notes: keep this comment
project: demo   # and this one

entities:
  plant:
    identity: [name]
    fields:
      name: {type: text}
      alt: {type: number, unit: m}

# a comment inside the file
storage: {normal_form: 3NF}
""", encoding="utf-8")
    return tmp_path / "spider.yaml"


def test_add_writes_one_entry_and_leaves_everything_else_alone(tmp_path, capsys):
    path = make_project(tmp_path)
    before = path.read_text()
    assert run(["derive", "add", "alt_ft", "convert(alt, 'm', 'ft')", "--on", "plant",
                "--explain", "the same in feet", "--round", "1"], tmp_path) == 0
    after = path.read_text()
    assert "# my notes: keep this comment" in after
    assert "# and this one" in after and "# a comment inside the file" in after
    added = [l for l in after.splitlines() if l not in before.splitlines()]
    assert any("alt_ft:" in l for l in added) and len(added) <= 8
    assert Spec.load(path).derived["alt_ft"].round == 1
    assert (tmp_path / "spider.yaml.bak").read_text() == before


def test_add_refuses_a_formula_with_a_typo_and_writes_nothing(tmp_path, capsys):
    path = make_project(tmp_path)
    before = path.read_text()
    assert run(["derive", "add", "bad", "alt_typo * 2", "--on", "plant"], tmp_path) == 2
    assert path.read_text() == before
    assert "did you mean" in capsys.readouterr().out


def test_add_refuses_a_name_that_is_already_a_column(tmp_path, capsys):
    make_project(tmp_path)
    assert run(["derive", "add", "alt", "alt * 2", "--on", "plant"], tmp_path) == 1
    assert "already exists" in capsys.readouterr().err


def test_try_changes_nothing(tmp_path, capsys):
    path = make_project(tmp_path)
    before = path.read_text()
    assert run(["derive", "try", "alt * 2", "--on", "plant"], tmp_path) == 0
    assert path.read_text() == before


# ------------------------------------------------------- editing spider.yaml
def test_yamltext_adds_to_an_existing_section_without_touching_the_rest():
    text = "a: 1\nderived:\n  one:\n    on: x\n# note\nb: 2\n"
    out = yamltext.add_entry(text, "derived", "two", {"on": "y", "formula": "1 + 1"})
    assert out.index("  one:") < out.index("  two:") < out.index("# note") < out.index("b: 2")
    assert "# note" in out


def test_yamltext_creates_a_missing_section():
    out = yamltext.add_entry("a: 1\n", "derived", "two", {"on": "y"})
    assert out.startswith("a: 1\n") and "derived:\n  two:" in out


def test_yamltext_fills_an_empty_inline_section():
    out = yamltext.add_entry("derived: {}\nb: 2\n", "derived", "two", {"on": "y"})
    assert "derived:\n  two:" in out and "{}" not in out and out.rstrip().endswith("b: 2")


def test_yamltext_will_not_guess_at_an_inline_mapping_with_content():
    assert yamltext.add_entry("derived: {one: {on: x}}\n", "derived", "two", {}) is None


def test_yamltext_knows_when_an_entry_exists():
    text = "derived:\n  one:\n    on: x\n"
    assert yamltext.has_entry(text, "derived", "one")
    assert not yamltext.has_entry(text, "derived", "two")


# ------------------------------------------------------------- OpenStreetMap
class FakeResponse:
    def __init__(self, status=200, payload=None, content=b"x"):
        self.status_code = status
        self._payload = payload
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


CAFES = {"elements": [
    {"type": "node", "id": 11, "lat": 13.05, "lon": 80.27,
     "tags": {"name": "Blue Cafe", "amenity": "cafe", "cuisine": "coffee"}},
    {"type": "way", "id": 12, "center": {"lat": 13.06, "lon": 80.28},
     "tags": {"name": "St. Mary's Cafe", "amenity": "cafe"}},
    {"type": "node", "id": 13, "lat": 13.07, "lon": 80.29, "tags": {"amenity": "cafe"}},
    {"type": "node", "id": 14, "tags": {"name": "No Coordinates"}},
]}


@pytest.fixture
def osm_project(tmp_path):
    store.init_project(tmp_path)
    (tmp_path / "spider.yaml").write_text("""
project: cafes
sources:
  mode: only_listed
  items:
    - {id: osm, type: osm, bbox: [12.8, 80.1, 13.25, 80.35], tags: {amenity: cafe}, tier: 2}
entities:
  cafe:
    identity: [osm_id]
    label: name
    fields:
      osm_id: {type: text, required: true}
      name: {type: text}
      latitude: {type: number, sanity: [12.7, 13.4]}
      longitude: {type: number, sanity: [79.9, 80.4]}
      cuisine: {type: text}
""", encoding="utf-8")
    return tmp_path


def test_places_are_discovered_from_openstreetmap_without_a_list(osm_project, monkeypatch):
    import requests
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append((url, headers["User-Agent"], data["data"]))
        return FakeResponse(200, CAFES, b"x" * 100)

    monkeypatch.setattr(requests, "post", fake_post)
    conn = store.connect(osm_project)
    spec = Spec.load(osm_project / "spider.yaml")
    from spider import sources
    result = sources.read_source(conn, spec, spec.sources.items[0], osm_project)

    assert result.readable and result.rows == 3        # the one with no coordinates is skipped
    assert calls[0][1] == "ProjectSpider/0.1.0"        # the only agent overpass-api.de accepts
    assert '[bbox:12.8,80.1,13.25,80.35]' in calls[0][2] and '"amenity"="cafe"' in calls[0][2]
    assert "cuisine" in result.fields_found            # a tag first seen on the first feature
    urls = [r["url"] for r in conn.execute("SELECT url FROM pages")]
    assert "https://www.openstreetmap.org/node/11" in urls   # evidence you can open


def test_the_query_result_is_cached_so_a_rebuild_asks_nothing(osm_project, monkeypatch):
    import requests
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: (calls.append(1),
                        FakeResponse(200, CAFES, b"x"))[1])
    conn = store.connect(osm_project)
    spec = Spec.load(osm_project / "spider.yaml")
    from spider import sources
    sources.read_source(conn, spec, spec.sources.items[0], osm_project)
    sources.read_source(conn, spec, spec.sources.items[0], osm_project)
    assert len(calls) == 1


def test_a_busy_server_fails_over_to_a_mirror(osm_project, monkeypatch):
    import requests
    seen = []

    def fake_post(url, data=None, headers=None, timeout=None):
        seen.append(url)
        return FakeResponse(504) if len(seen) == 1 else FakeResponse(200, CAFES, b"x")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    conn = store.connect(osm_project)
    spec = Spec.load(osm_project / "spider.yaml")
    from spider import sources
    result = sources.read_source(conn, spec, spec.sources.items[0], osm_project)
    assert result.readable and len(set(seen)) >= 2


def test_when_every_server_is_down_the_reason_is_plain(osm_project, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(504))
    monkeypatch.setattr("time.sleep", lambda *_: None)
    conn = store.connect(osm_project)
    spec = Spec.load(osm_project / "spider.yaml")
    from spider import sources
    result = sources.read_source(conn, spec, spec.sources.items[0], osm_project)
    assert not result.readable
    assert "no OpenStreetMap server answered" in result.note and "try again" in result.note


def test_an_osm_source_without_tags_refuses_rather_than_fetching_everything(osm_project):
    from spider import sources
    conn = store.connect(osm_project)
    spec = Spec.load(osm_project / "spider.yaml")
    item = SourceItem(id="x", type="osm", bbox=[1, 2, 3, 4])
    result = sources.read_source(conn, spec, item, osm_project)
    assert not result.readable and "tags" in result.note


def test_tag_filters_accept_every_way_of_writing_them():
    from spider.sources import _osm_tag_filters
    assert _osm_tag_filters({"amenity": "cafe"}) == ['["amenity"="cafe"]']
    assert _osm_tag_filters(["shop", "cuisine=indian"]) == ['["shop"]', '["cuisine"="indian"]']
    assert _osm_tag_filters("amenity=cafe,leisure=park") == \
        ['["amenity"="cafe"]', '["leisure"="park"]']
    assert _osm_tag_filters(None) == []
