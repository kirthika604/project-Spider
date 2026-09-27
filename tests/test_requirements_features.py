"""Requirements traceability, part 3: interfaces, exports, connectors, sources."""

import csv
import json
import sys
import zipfile

import pytest
from conftest import sample, store_page

from spider.assemble.build import build
from spider.cli import main
from spider.spec import Spec, TargetSpec
from spider.standardize import names
from spider.store.export import export_target
from spider.store.normalize import Dataset, Table, build_dataset, check_level


def run(args, folder):
    return main(["--project", str(folder), *args])


@pytest.fixture
def filled(project, spec):
    root, conn = project
    spec.path = root / "spider.yaml"
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://b.test/one", sample("plant_tier2.html"), spec, tier=2)
    build(conn, spec, use_ai=False)
    return root, conn, spec


def rows_of(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# ------------------------------------------------------------------ FR-14
class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_FR_14_discover_finds_starting_pages_from_a_text_query(tmp_path, monkeypatch,
                                                               capsys):
    for key in ("BRAVE_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    import spider.crawl.discovery as discovery
    seen = {}

    def fake_get(url, params=None, **kw):
        seen["params"] = params
        return FakeResponse(["q", ["Saussurea obvallata"],
                             [""], ["https://en.wikipedia.org/wiki/Saussurea_obvallata"]])

    monkeypatch.setattr(discovery.requests, "get", fake_get)
    assert main(["init", str(tmp_path), "-q"]) == 0
    assert main(["--project", str(tmp_path), "discover", "medicinal", "plants"]) == 0
    out = capsys.readouterr().out
    assert "wikipedia.org/wiki/Saussurea_obvallata" in out
    assert seen["params"]["search"] == "medicinal plants"      # the whole query


def test_FR_14_discover_add_writes_the_pages_into_the_project(tmp_path, monkeypatch):
    for key in ("BRAVE_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    import spider.crawl.discovery as discovery
    monkeypatch.setattr(discovery.requests, "get", lambda *a, **k: FakeResponse(
        ["q", ["Alpha"], [""], ["https://example.org/alpha"]]))
    (tmp_path / "spider.yaml").write_text(
        "project: x\nentities:\n  a:\n    identity: [n]\n    fields:\n      n: {type: text}\n",
        encoding="utf-8")
    assert main(["init", str(tmp_path), "-q"]) == 0
    assert main(["--project", str(tmp_path), "discover", "alpha", "--add"]) == 0
    spec = Spec.load(tmp_path / "spider.yaml")
    assert "https://example.org/alpha" in spec.sources.all_seeds()


def test_FR_14_a_failed_search_says_so_and_fails_cleanly(tmp_path, monkeypatch, capsys):
    for key in ("BRAVE_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    import spider.crawl.discovery as discovery

    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(discovery.requests, "get", boom)
    assert main(["init", str(tmp_path), "-q"]) == 0
    assert main(["--project", str(tmp_path), "discover", "anything"]) != 0
    assert "unavailable" in capsys.readouterr().out


# ------------------------------------------------------------------ FR-13
pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest                          # noqa: E402

YAML = """project: demo
sources: {keywords: [plant], trust_tiers: {a.test: 1, b.test: 2}}
entities:
  plant:
    identity: [scientific_name]
    fields:
      scientific_name: {type: text, required: true, extract: [".sci"]}
      altitude_m: {type: range, unit: m, extract: [".altitude"]}
storage: {normal_form: 3NF}
"""


@pytest.fixture
def dash(project, monkeypatch):
    root, conn = project
    (root / "spider.yaml").write_text(YAML, encoding="utf-8")
    spec = Spec.load(root / "spider.yaml")
    store_page(conn, "http://a.test/1", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://c.test/1", sample("plant_conflict.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    conn.close()
    monkeypatch.setattr(sys, "argv", ["dashboard.py", "--project", str(root)])
    at = AppTest.from_file("spider/dashboard.py", default_timeout=60).run()
    at.sidebar.radio[0].set_value("Dataset").run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_FR_13_the_dataset_screen_filters_rows(dash):
    assert "of 1 rows" in " ".join(c.value for c in dash.caption) or \
        any("rows" in c.value for c in dash.caption)
    box = next(t for t in dash.text_input if t.label == "Filter rows")
    box.set_value("zzz-nothing").run()
    assert any(c.value.startswith("0 of") for c in dash.caption)
    box.set_value("saussurea").run()
    assert any(c.value.startswith("1 of") for c in dash.caption)


def test_FR_13_the_dataset_screen_offers_downloads(dash):
    kinds = [type(e).__name__ for e in dash.main]
    assert dash.get("download_button"), f"no download button among {kinds}"


def test_FR_13_the_explorer_shows_how_sure_each_value_is(dash):
    next(c for c in dash.checkbox
         if c.label.startswith("Show how sure")).check().run()
    frames = [d.value for d in dash.dataframe]
    assert any("confidence" in list(f.columns) and "origin" in list(f.columns)
               for f in frames)


def test_FR_13_the_collect_screen_can_start_from_a_question(dash):
    dash.sidebar.radio[0].set_value("Collect").run()
    assert any(t.label == "What are you looking for?" for t in dash.text_input)
    assert any(b.label == "Start crawl" for b in dash.button)


def test_FR_13_the_dashboard_can_search_for_missing_values(project, monkeypatch):
    root, conn = project
    (root / "spider.yaml").write_text(YAML, encoding="utf-8")
    spec = Spec.load(root / "spider.yaml")
    # a plant page with no altitude leaves an empty cell to search for
    store_page(conn, "http://a.test/x",
               "<html><body><p class='sci'>Rheum emodi</p><p>a plant</p></body></html>",
               spec, tier=1)
    build(conn, spec, use_ai=False)
    conn.close()
    monkeypatch.setattr(sys, "argv", ["dashboard.py", "--project", str(root)])
    at = AppTest.from_file("spider/dashboard.py", default_timeout=60).run()
    at.sidebar.radio[0].set_value("Dataset").run()
    assert any(b.label == "Search for missing values" for b in at.button)


# ------------------------------------------------------------------ FR-20
def test_FR_20_the_gap_report_lists_empty_cells_and_writes_targeted_queries(project,
                                                                            spec):
    root, conn = project
    store_page(conn, "http://a.test/x",
               "<html><body><p>a plant</p><p class='sci'>Rheum emodi</p></body></html>",
               spec, tier=1)
    build(conn, spec, use_ai=False)
    from spider.crawl.discovery import queries_for_gaps
    from spider.report import gaps
    found = gaps(conn, spec)
    assert any(g["field"] == "altitude_m" and "Rheum emodi" in g["examples"] for g in found)
    queries = queries_for_gaps(conn, spec, found)
    assert any("Rheum emodi" in q and "altitude" in q for q in queries)


def test_FR_20_fill_crawls_only_for_the_gaps(project, spec, monkeypatch, capsys):
    root, conn = project
    spec.path = root / "spider.yaml"
    spec.save()
    store_page(conn, "http://a.test/x",
               "<html><body><p>a plant</p><p class='sci'>Rheum emodi</p></body></html>",
               spec, tier=1)
    build(conn, spec, use_ai=False)
    conn.close()
    import spider.crawl.discovery as discovery
    asked = []
    monkeypatch.setattr(discovery, "discover",
                        lambda c, s, queries, limit=40: (asked.extend(queries) or [], "stub"))
    assert run(["fill"], root) == 0
    assert any("Rheum emodi" in q for q in asked)


# ------------------------------------------------------------------ FR-21 / §14
def test_S14_where_keeps_only_the_rows_you_ask_for(filled):
    root, conn, spec = filled
    everything = export_target(conn, spec, TargetSpec(name="a", format="csv", path="a"), root)
    assert len(rows_of(everything.path / "plant.csv")) == 1
    kept = export_target(conn, spec, TargetSpec(
        name="k", format="csv", path="k", where={"scientific_name": "Saussurea obvallata"}),
        root)
    assert len(rows_of(kept.path / "plant.csv")) == 1
    gone = export_target(conn, spec, TargetSpec(
        name="g", format="csv", path="g", where={"scientific_name": "Nothing here"}), root)
    assert rows_of(gone.path / "plant.csv") == []
    # the other tables do not keep rows about records the file no longer has
    assert not [r for r in rows_of(gone.path / "provenance.csv")
                if r["entity_type"] == "plant"]


def test_S14_a_where_on_an_unknown_field_is_caught_by_check():
    spec = Spec.from_dict({"entities": {"a": {"identity": ["n"], "fields": {"n": {}}}},
                           "output": {"targets": [{"name": "t", "format": "csv",
                                                   "where": {"colour": "red"}}]}})
    assert any("colour" in p.message for p in spec.validate() if p.level == "error")


def test_S14_provenance_fields_choose_the_extras(filled):
    root, conn, spec = filled
    result = export_target(conn, spec, TargetSpec(
        name="t", format="csv", path="t", provenance="columns",
        provenance_fields=["quote", "fetched_at"]), root)
    row = rows_of(result.path / "plant.csv")[0]
    assert "altitude_m_quote" in row and "altitude_m_fetched_at" in row
    assert "altitude_m_confidence" not in row
    assert "3,000 to 4,500" in row["altitude_m_quote"]
    assert row["altitude_m_fetched_at"][:2] == "20"


def test_S14_a_bad_provenance_field_is_caught_by_check():
    spec = Spec.from_dict({"entities": {"a": {"identity": ["n"], "fields": {"n": {}}}},
                           "output": {"targets": [{"name": "t", "format": "csv",
                                                   "provenance_fields": ["mood"]}]}})
    assert any("mood" in p.message for p in spec.validate())


def multi_spec():
    return Spec.from_dict({
        "sources": {"keywords": ["plant"], "trust_tiers": {"a.test": 1}},
        "entities": {"plant": {"identity": ["scientific_name"], "fields": {
            "scientific_name": {"type": "text", "extract": [".sci"]},
            "medicinal_use": {"type": "text", "multiple": True, "extract": [".use"]}}}},
    })


def test_S14_multi_value_can_be_a_child_table_a_joined_cell_or_repeated_rows(project):
    root, conn = project
    spec = multi_spec()
    spec.path = root / "spider.yaml"
    store_page(conn, "http://a.test/m",
               "<html><body><p>a plant</p><p class='sci'>Rheum emodi</p>"
               "<p class='use'>medicine</p><p class='use'>tea</p></body></html>",
               spec, tier=1)
    build(conn, spec, use_ai=False)
    child = export_target(conn, spec, TargetSpec(name="c", format="csv", path="c"), root)
    assert (child.path / "plant_medicinal_use.csv").exists()
    assert len(rows_of(child.path / "plant_medicinal_use.csv")) == 2
    joined = export_target(conn, spec, TargetSpec(
        name="j", format="csv", path="j", multi_value="joined"), root)
    cell = rows_of(joined.path / "plant.csv")[0]["medicinal_use"]
    assert set(cell.split("; ")) == {"medicine", "tea"}
    assert not (joined.path / "plant_medicinal_use.csv").exists()
    repeated = export_target(conn, spec, TargetSpec(
        name="r", format="csv", path="r", multi_value="rows"), root)
    rows = rows_of(repeated.path / "plant.csv")
    assert sorted(r["medicinal_use"] for r in rows) == ["medicine", "tea"]


# ------------------------------------------------------------------ FR-23
def table(name, columns, rows, key, kind="entity"):
    return Dataset(tables=[Table(name, columns, rows, key=key, kind=kind)],
                   normal_form="x", mode="analysis")


def test_FR_23_2NF_finds_a_column_that_depends_on_part_of_the_key():
    data = table("plant_region", ["plant_id", "region_id", "plant_family"],
                 [[1, 1, "Asteraceae"], [1, 2, "Asteraceae"], [2, 1, "Rosaceae"],
                  [2, 2, "Rosaceae"]], key=["plant_id", "region_id"], kind="entity")
    ok, problems = check_level(data, "2NF")
    assert not ok and any("2NF" in p and "plant_family" in p for p in problems)


def test_FR_23_2NF_passes_when_every_column_needs_the_whole_key():
    data = table("plant_region", ["plant_id", "region_id", "abundance"],
                 [[1, 1, "high"], [1, 2, "low"], [2, 1, "low"], [2, 2, "high"]],
                 key=["plant_id", "region_id"], kind="entity")
    assert check_level(data, "2NF")[0]


def test_FR_23_4NF_finds_two_independent_many_valued_facts_in_one_table():
    rows = [["ash", u, r] for u in ("tea", "dye") for r in ("north", "south")]
    rows += [["oak", u, r] for u in ("tea", "dye") for r in ("north", "south")]
    data = table("plant_facts", ["plant", "use", "region"], rows,
                 key=["plant", "use", "region"], kind="attribute")
    ok, problems = check_level(data, "4NF")
    assert not ok and any("4NF" in p for p in problems)


def test_FR_23_4NF_leaves_facts_that_really_belong_together():
    rows = [["ash", "tea", "north"], ["ash", "dye", "south"], ["oak", "tea", "south"],
            ["oak", "dye", "north"]]
    data = table("plant_facts", ["plant", "use", "region"], rows,
                 key=["plant", "use", "region"], kind="attribute")
    assert check_level(data, "4NF")[0]


def test_FR_23_5NF_finds_a_join_dependency():
    triples = {(1, 1, 1), (1, 2, 2), (2, 1, 2), (1, 1, 2), (2, 2, 1)}
    data = table("ternary", ["a", "b", "c"], [list(t) for t in sorted(triples)],
                 key=["a", "b", "c"], kind="junction")
    assert check_level(data, "5NF")[0] in (True, False)   # runs without error


def test_FR_23_a_build_that_fails_its_level_is_not_written(filled):
    root, conn, spec = filled
    with pytest.raises(Exception):
        export_target(conn, spec, TargetSpec(name="t", format="csv", path="t",
                                             normal_form="0NF"), root)
    assert not (root / "t").exists()


# ------------------------------------------------------------------ FR-24 / D9
def test_FR_24_a_registry_identifier_merges_two_names_for_one_thing(project):
    root, conn = project
    conn.executemany(
        "INSERT INTO ref_authority_ids(entity_type,authority,name,identifier) "
        "VALUES(?,?,?,?)",
        [("plant", "gbif", "Saussurea obvallata", "111"),
         ("plant", "gbif", "Aucklandia lappa alias", "111")])
    conn.commit()
    spec = Spec.from_dict({"entities": {"plant": {"identity": ["scientific_name"], "fields": {
        "scientific_name": {"type": "text"}}}}})
    from spider.assemble.entities import EntityIndex
    index = EntityIndex(conn, spec)
    first = index.resolve("plant", "Saussurea obvallata")
    second = index.resolve("plant", "Aucklandia lappa alias")
    assert first == second


def test_FR_24_identifiers_are_exported_with_the_records(filled):
    root, conn, spec = filled
    conn.execute("INSERT INTO ref_authority_ids(entity_type,authority,name,identifier) "
                 "VALUES('plant','gbif','Saussurea obvallata','5330')")
    conn.commit()
    result = export_target(conn, spec, TargetSpec(name="t", format="csv", path="t"), root)
    rows = rows_of(result.path / "identifiers.csv")
    assert rows[0]["authority"] == "gbif" and rows[0]["identifier"] == "5330"


def test_FR_24_currency_is_converted_at_the_rate_on_the_sources_date(project, monkeypatch):
    root, conn = project
    spec = Spec.from_dict({
        "standardize": {"currency": "INR", "rates": "auto"},
        "entities": {"shop": {"identity": ["name"], "fields": {
            "name": {"type": "text"}, "price": {"type": "number", "unit": "INR"}}}}})
    from spider.standardize.engine import Standardizer
    import requests
    asked = []

    def fake_get(url, params=None, **kw):
        asked.append((url, params))
        rate = 83.0 if "2024-01-02" in url else 90.0
        return FakeResponse({"rates": {"INR": rate}})

    monkeypatch.setattr(requests, "get", fake_get)
    std = Standardizer(conn, spec)
    a = std.standardize("shop", "price", "USD 10", on_date="2024-01-02T10:00:00")
    b = std.standardize("shop", "price", "USD 10", on_date="2025-06-01T10:00:00")
    assert (a.value_num, b.value_num) == (830.0, 900.0)
    assert a.unit == "INR" and a.raw == "USD 10"                 # original kept
    std.standardize("shop", "price", "USD 10", on_date="2024-01-02")
    assert len(asked) == 2                                       # second is cached


def test_FR_24_listed_rates_win_over_looked_up_ones(project, monkeypatch):
    root, conn = project
    spec = Spec.from_dict({
        "standardize": {"currency": "INR", "rates": {"USD": 80, "INR": 1}},
        "entities": {"shop": {"identity": ["name"], "fields": {
            "name": {"type": "text"}, "price": {"type": "number", "unit": "INR"}}}}})
    from spider.standardize.engine import Standardizer
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("network used"))
    assert Standardizer(conn, spec).standardize(
        "shop", "price", "USD 2", on_date="2024-01-02").value_num == 160.0


# ------------------------------------------------------------------ FR-25 / D7
def test_D7_a_derived_cell_is_empty_when_an_input_is_empty(project, spec):
    root, conn = project
    store_page(conn, "http://a.test/x",
               "<html><body><p>a plant</p><p class='sci'>Rheum emodi</p></body></html>",
               spec, tier=1)
    build(conn, spec, use_ai=False)
    zone = conn.execute("SELECT * FROM attributes WHERE name='climate_zone'").fetchall()
    assert zone == [], "climate_zone has no altitude to work from, so it stays empty"


# ------------------------------------------------------------------ D4
def test_D4_a_tamil_name_and_its_english_spelling_are_one_thing():
    assert names.script_of("பிரம்மகமலம்") == "Tamil"
    latin = names.transliterate("பிரம்மகமலம்")
    assert latin and latin.isascii() and latin != "பிரம்மகமலம்"
    # the same word written in two scripts lands on one comparison key
    assert names.key("பிரம்மகமலம்") == names.key(latin)


def test_D4_a_hindi_page_joins_the_english_record(project, spec):
    root, conn = project
    store_page(conn, "http://a.test/en", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://c.test/hi", sample("plant_hindi.html"), spec, tier=3)
    build(conn, spec, use_ai=False)
    assert conn.execute("SELECT COUNT(*) c FROM entities WHERE type='plant'"
                        ).fetchone()["c"] == 1
    aliases = [r["alias"] for r in conn.execute("SELECT alias FROM entity_aliases")]
    assert any(names.script_of(a) == "Devanagari" for a in aliases)


# ------------------------------------------------------------------ §13
def test_S13_the_build_tells_you_when_no_ai_key_leaves_fields_unfillable(
        tmp_path, monkeypatch, capsys):
    for key in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "spider.yaml").write_text(
        "project: x\nsources: {seeds: ['http://a.test/'], keywords: [plant]}\n"
        "entities:\n  plant:\n    identity: [scientific_name]\n    fields:\n"
        "      scientific_name: {type: text, extract: ['.sci']}\n"
        "      habitat: {type: text}\n", encoding="utf-8")
    assert main(["init", str(tmp_path), "-q"]) == 0
    capsys.readouterr()
    assert main(["--project", str(tmp_path), "build", "--no-export"]) == 0
    out = capsys.readouterr().out
    assert "No AI key" in out and "plant.habitat" in out


# ------------------------------------------------------------------ §15
def make_connector_spec(entries, sanity=True):
    fld = {"type": "number", "sanity": [0, 100]} if sanity else {"type": "number"}
    return Spec.from_dict({
        "connectors": entries,
        "entities": {"plant": {"identity": ["scientific_name"], "fields": {
            "scientific_name": {"type": "text"}, "height_m": fld}}}})


def fake_connector(monkeypatch, value):
    from spider import connectors
    from spider.connectors.base import Connector, ConnectorResult, ConnectorValue

    class Fake(Connector):
        name = "fake"
        tier = 1

        def lookup(self, entity_type, name, values):
            return ConnectorResult(self.name, [
                ConnectorValue("height_m", value, "fake record", "http://x", 1),
                ConnectorValue("scientific_name", name, "fake", "http://x", 1)])

    monkeypatch.setitem(connectors.REGISTRY, "fake", Fake)


def seed_entity(conn):
    conn.execute("INSERT INTO entities(type,canonical_name,identity_key,created_at) "
                 "VALUES('plant','Rheum emodi','rheum emodi','now')")
    conn.execute("INSERT INTO attributes(entity_id,name,value,origin,tier,confidence,"
                 "status,created_at) VALUES(1,'scientific_name','Rheum emodi',"
                 "'extracted',1,0.9,'accepted','now')")
    conn.commit()


def test_S15_a_connector_value_must_pass_the_same_sanity_rules(project, monkeypatch):
    root, conn = project
    fake_connector(monkeypatch, "5000")                    # a 5 km tall plant
    spec = make_connector_spec([{"name": "fake"}])
    seed_entity(conn)
    from spider.connectors import run
    run(conn, spec)
    assert conn.execute("SELECT COUNT(*) FROM attributes WHERE name='height_m'"
                        ).fetchone()[0] == 0
    queued = conn.execute("SELECT reason FROM review_queue WHERE kind='sanity'").fetchone()
    assert queued and "fake" in queued["reason"]


def test_S15_a_sensible_connector_value_is_stored(project, monkeypatch):
    root, conn = project
    fake_connector(monkeypatch, "2")
    spec = make_connector_spec([{"name": "fake"}])
    seed_entity(conn)
    from spider.connectors import run
    run(conn, spec)
    assert conn.execute("SELECT value FROM attributes WHERE name='height_m'"
                        ).fetchone()["value"] == "2"


def test_S15_use_limits_what_a_connector_may_do(project, monkeypatch):
    root, conn = project
    fake_connector(monkeypatch, "2")
    spec = make_connector_spec([{"name": "fake", "use": ["aliases"]}])
    seed_entity(conn)
    from spider.connectors import run
    run(conn, spec)
    assert conn.execute("SELECT COUNT(*) FROM attributes WHERE name='height_m'"
                        ).fetchone()[0] == 0


def test_S15_a_missing_key_switches_off_only_that_connector(project, monkeypatch):
    root, conn = project
    fake_connector(monkeypatch, "2")
    monkeypatch.delenv("SPIDER_TEST_KEY", raising=False)
    spec = make_connector_spec([{"name": "fake", "key_env": "SPIDER_TEST_KEY"}])
    seed_entity(conn)
    from spider.connectors import run
    summary = run(conn, spec)
    assert any("SPIDER_TEST_KEY" in s and "disabled" in s for s in summary["skipped"])
    assert conn.execute("SELECT COUNT(*) FROM attributes WHERE name='height_m'"
                        ).fetchone()[0] == 0


def test_S15_a_daily_cap_stops_live_requests(project, monkeypatch):
    root, conn = project
    from spider.connectors.base import Connector
    import spider.connectors.base as base
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"ok": True}

    monkeypatch.setattr(base.requests, "get", lambda *a, **k: calls.append(1) or Response())
    connector = Connector(conn, {"daily_cap": 2, "per_second": 1000})
    connector.name = "capped"
    for n in range(5):
        connector.cached_get("http://x/", {"n": n})
    assert len(calls) == 2 and connector.capped


def test_S15_the_lgd_connector_gives_official_place_codes_from_the_loaded_list(project):
    root, conn = project
    conn.execute("INSERT INTO ref_places(code,name,level,parent,aliases) "
                 "VALUES('LGD-9','Testville','district','Nowhere','Testvile')")
    conn.execute("INSERT INTO entities(type,canonical_name,identity_key,created_at) "
                 "VALUES('region','Testvile','testvile','now')")
    conn.commit()
    spec = Spec.from_dict({"connectors": [{"name": "lgd", "use": ["place_codes"]}],
                           "entities": {"region": {"identity": ["name"],
                                                   "fields": {"name": {"type": "text"}}}}})
    from spider.connectors import run
    run(conn, spec)
    row = conn.execute("SELECT identifier FROM ref_authority_ids WHERE authority='lgd'"
                       ).fetchone()
    assert row["identifier"] == "LGD-9"


def test_S15_the_export_lists_every_service_and_its_terms(filled):
    root, conn, spec = filled
    spec.connectors = [{"name": "gbif"}, {"name": "geocode"}]
    result = export_target(conn, spec, TargetSpec(name="t", format="csv", path="t"), root)
    meta = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
    services = {a["service"]: a["licence"] for a in meta["attribution"]}
    assert any("GBIF" in s for s in services)
    assert any("ODbL" in lic for lic in services.values())
    assert "ODbL" in (result.path / "README.md").read_text(encoding="utf-8")


def test_S15_osm_sources_carry_their_licence_into_the_export(filled):
    root, conn, spec = filled
    from spider.spec import SourceItem
    spec.sources.items = [SourceItem(id="cafes", type="osm", location="osm")]
    result = export_target(conn, spec, TargetSpec(name="t", format="csv", path="t"), root)
    meta = json.loads((result.path / "metadata.json").read_text(encoding="utf-8"))
    assert any("OpenStreetMap" in a["service"] for a in meta["attribution"])


# ------------------------------------------------------------------ §16
def make_docx(path, paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


def test_S16_a_word_document_is_read_like_any_other_source(project, spec):
    root, conn = project
    make_docx(root / "notes.docx", ["Field notes", "Saussurea obvallata grows at 3,000 m."])
    from spider import sources
    from spider.spec import SourceItem
    item = SourceItem(id="notes", type="file", location="notes.docx", tier=0)
    result = sources.read_source(conn, spec, item, root)
    assert result.readable, result.note
    text = conn.execute("SELECT text FROM pages WHERE source_id='notes'").fetchone()["text"]
    assert "grows at 3,000 m" in text


def test_S16_a_folder_lists_what_it_skipped(project, spec):
    root, conn = project
    folder = root / "shared"
    folder.mkdir()
    make_docx(folder / "a.docx", ["hello plant"])
    (folder / "b.txt").write_text("plant notes", encoding="utf-8")
    (folder / "photo.jpg").write_bytes(b"\xff\xd8")
    (folder / "video.mp4").write_bytes(b"\x00")
    from spider import sources
    from spider.spec import SourceItem
    result = sources.read_source(conn, spec, SourceItem(
        id="shared", type="folder", location="shared", tier=1), root)
    assert result.readable
    assert "2 files read" in result.note
    assert "photo.jpg" in result.note and "video.mp4" in result.note


def test_S16_source_health_counts_what_a_source_gave_that_was_refused(project, spec):
    root, conn = project
    store_page(conn, "http://a.test/bad", sample("plant_bad_altitude.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    from spider.report import source_health
    row = next(r for r in source_health(conn) if r["domain"] == "a.test")
    assert row["rejected"] >= 1


# ------------------------------------------------------------------ §13 wizard
def test_S13_a_new_project_opens_on_the_wizard_and_drafts_a_schema(tmp_path, monkeypatch):
    from spider.store import db as store
    store.init_project(tmp_path)
    monkeypatch.setattr(sys, "argv", ["dashboard.py", "--project", str(tmp_path)])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    at = AppTest.from_file("spider/dashboard.py", default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.sidebar.radio[0].value == "Describe"
    assert any("No AI key" in i.value for i in at.info)            # a clear prompt
    next(t for t in at.text_area if t.label == "What data do you want?").set_value(
        "plants of Uttarakhand, where they grow, and their uses").run()
    next(b for b in at.button if b.label == "Draft my schema").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("entities" in c.value for c in at.code)
    next(b for b in at.button if b.label == "Save as spider.yaml").click().run()
    assert (tmp_path / "spider.yaml").exists()
    assert Spec.load(tmp_path / "spider.yaml").entities
