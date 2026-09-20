"""The whole assembly chain over saved pages: no network, no AI key."""

import csv
import json
import sqlite3

import pytest
from conftest import sample, store_page

from spider.assemble.build import build
from spider.report import build_report, check_gold, explain
from spider.store.export import export_all
from spider.store.normalize import NormalFormError, build_dataset


@pytest.fixture
def built(project, spec):
    root, conn = project
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://b.test/one", sample("plant_tier2.html"), spec, tier=2)
    store_page(conn, "http://c.test/one", sample("plant_hindi.html"), spec, tier=3)
    store_page(conn, "http://c.test/two", sample("plant_bad_altitude.html"), spec, tier=3)
    store_page(conn, "http://a.test/index", sample("index.html"), spec, tier=1)
    report = build(conn, spec, use_ai=False)
    return root, conn, spec, report


def value_of(conn, name, field, status="accepted"):
    row = conn.execute(
        "SELECT a.* FROM attributes a JOIN entities e ON e.id=a.entity_id "
        "WHERE e.canonical_name=? AND a.name=? AND a.status=? "
        "ORDER BY a.confidence DESC LIMIT 1", (name, field, status)).fetchone()
    return dict(row) if row else None


def test_the_same_plant_on_three_sites_becomes_one_record(built):
    _root, conn, _spec, _report = built
    rows = conn.execute("SELECT canonical_name FROM entities WHERE type='plant'").fetchall()
    assert sorted(r["canonical_name"] for r in rows) == [
        "Aconitum heterophyllum", "Saussurea obvallata"]


def test_an_index_page_does_not_become_a_record(built):
    _root, conn, _spec, _report = built
    names = [r["canonical_name"] for r in
             conn.execute("SELECT canonical_name FROM entities")]
    assert "Example Institute" not in names


def test_agreement_across_domains_raises_confidence(built):
    _root, conn, _spec, _report = built
    altitude = value_of(conn, "Saussurea obvallata", "altitude_m")
    assert altitude["value"] == "3000-4500"
    assert altitude["confidence"] >= 0.95            # three independent domains
    lineage = json.loads(altitude["lineage"])
    assert len(lineage["agreeing_domains"]) == 3


def test_a_value_outside_its_sanity_range_never_enters_the_dataset(built):
    _root, conn, _spec, report = built
    assert value_of(conn, "Aconitum heterophyllum", "altitude_m") is None
    assert report.rejected_sanity == 1
    queued = conn.execute(
        "SELECT reason FROM review_queue WHERE kind='sanity'").fetchone()
    assert "outside the allowed range" in queued["reason"]


def test_every_value_keeps_its_evidence_and_source(built):
    _root, conn, _spec, _report = built
    altitude = value_of(conn, "Saussurea obvallata", "altitude_m")
    assert "3,000 to 4,500 m" in altitude["evidence"]
    page = conn.execute("SELECT url FROM pages WHERE id=?",
                        (altitude["source_page"],)).fetchone()
    assert page["url"].startswith("http")


def test_local_names_attach_to_the_same_record(built):
    _root, conn, _spec, _report = built
    aliases = [r["alias"] for r in conn.execute(
        "SELECT a.alias FROM entity_aliases a JOIN entities e ON e.id=a.entity_id "
        "WHERE e.canonical_name='Saussurea obvallata'")]
    assert "ब्रह्मकमल" in aliases   # the Hindi name
    assert any("rahmakamal" in a or "brhmkml" in a for a in aliases)


def test_relations_link_plants_to_regions_and_uses(built):
    _root, conn, _spec, _report = built
    rows = conn.execute(
        "SELECT r.relation, e.canonical_name FROM relations r "
        "JOIN entities e ON e.id=r.to_entity "
        "WHERE r.from_entity=(SELECT id FROM entities WHERE canonical_name="
        "'Saussurea obvallata')").fetchall()
    found = {(r["relation"], r["canonical_name"]) for r in rows}
    assert ("grows_in", "Chamoli") in found
    assert ("used_for", "medicinal") in found


def test_a_derived_value_is_calculated_and_labelled(built):
    _root, conn, _spec, _report = built
    zone = value_of(conn, "Saussurea obvallata", "climate_zone")
    assert zone["value"] == "alpine" and zone["origin"] == "derived"
    lineage = json.loads(zone["lineage"])
    assert lineage["inputs"], "a derived value records the cells it came from"


def test_explain_walks_from_a_derived_value_back_to_the_page(built):
    _root, conn, spec, _report = built
    chain = explain(conn, spec, "Saussurea obvallata", "climate_zone")
    first = chain["values"][0]
    assert first["origin"] == "derived"
    assert first["inputs"][0]["name"] == "altitude_m"
    assert first["inputs"][0]["url"].startswith("http")


def test_the_output_passes_its_3nf_check(built):
    _root, conn, spec, _report = built
    dataset = build_dataset(conn, spec)
    assert dataset.passed, dataset.problems
    plant = dataset.table("plant")
    assert "scientific_name" in plant.columns and "climate_zone" in plant.columns


def test_project_mode_refuses_to_write_below_3nf(built):
    _root, conn, spec, _report = built
    with pytest.raises(NormalFormError, match="analysis mode"):
        build_dataset(conn, spec, normal_form="0NF")


def test_analysis_mode_writes_one_flat_table(built):
    _root, conn, spec, _report = built
    dataset = build_dataset(conn, spec, normal_form="0NF", mode="analysis")
    assert dataset.passed and len(dataset.tables) <= 2
    assert dataset.tables[0].name.endswith("_flat")


def test_6nf_gives_one_table_per_attribute(built):
    _root, conn, spec, _report = built
    dataset = build_dataset(conn, spec, normal_form="6NF", mode="analysis")
    assert dataset.passed, dataset.problems
    assert dataset.table("plant_altitude_m") is not None
    assert "valid_from" in dataset.table("plant_altitude_m").columns


def test_export_writes_csv_sqlite_and_json_with_metadata(built, tmp_path):
    root, conn, spec, _report = built
    spec.output.formats = ["csv", "sqlite", "json"]
    spec.output.targets = []
    results = export_all(conn, spec, root)
    assert {r.format for r in results} == {"csv", "sqlite", "json"}

    csv_folder = next(r.path for r in results if r.format == "csv")
    with open(csv_folder / "plant.csv", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert any(r["scientific_name"] == "Saussurea obvallata" for r in rows)

    database = next(r.path for r in results if r.format == "sqlite")
    out = sqlite3.connect(database)
    names = [r[0] for r in out.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "plant" in names and "provenance" in names
    out.close()

    metadata = json.loads((csv_folder / "metadata.json").read_text())
    assert metadata["normal_form"] == "3NF" and metadata["passed_normal_form_check"]
    assert (csv_folder / "README.md").exists()


def test_the_report_counts_coverage_conflicts_and_gaps(built):
    _root, conn, spec, _report = built
    report = build_report(conn, spec)
    coverage = {f"{c.entity_type}.{c.field}": c for c in report.coverage}
    # the second plant is named by one tier 3 page only, which scores 0.40 and
    # stays in review, so it counts as an empty cell until another source agrees
    assert coverage["plant.scientific_name"].filled == 1
    assert coverage["plant.altitude_m"].filled == 1
    assert any(g["field"] == "altitude_m" for g in report.gaps)
    assert report.sources, "source health lists the domains that contributed"


def test_a_record_with_nothing_accepted_stays_out_of_the_tables(built):
    """It is not lost: it waits in the review queue and in the gap report."""
    _root, conn, spec, _report = built
    plants = build_dataset(conn, spec).table("plant").as_dicts()
    assert [p["scientific_name"] for p in plants] == ["Saussurea obvallata"]
    waiting = conn.execute(
        "SELECT COUNT(*) c FROM review_queue WHERE status='open'").fetchone()["c"]
    assert waiting > 0


def test_the_gold_set_measures_accuracy(built, tmp_path):
    _root, conn, spec, _report = built
    gold = tmp_path / "gold.csv"
    gold.write_text("type,entity,field,value\n"
                    "plant,Saussurea obvallata,altitude_m,3000-4500\n"
                    "plant,Saussurea obvallata,flowering_month,July\n"
                    "plant,Saussurea obvallata,climate_zone,alpine\n", encoding="utf-8")
    result = check_gold(conn, spec, gold)
    assert result["found"] == 3 and result["correct"] == 3
    assert result["high_confidence_accuracy"] == 100.0


def test_rebuilding_twice_gives_the_same_dataset(built):
    _root, conn, spec, _report = built
    first = build_dataset(conn, spec).table("plant").rows
    build(conn, spec, use_ai=False)
    assert build_dataset(conn, spec).table("plant").rows == first


def test_a_tie_is_broken_by_trust_not_by_row_order(project, spec):
    """Tier 0 is the user's own data and must not be read as a missing tier."""
    from spider.assemble.confidence import pick_best

    class Row(dict):
        def keys(self):
            return super().keys()

    rows = [Row(confidence=0.75, tier=1, value="3000-5000"),
            Row(confidence=0.75, tier=0, value="3200-4800")]
    assert pick_best(rows)["value"] == "3200-4800"
    assert pick_best([Row(confidence=0.9, tier=3, value="high"),
                      Row(confidence=0.4, tier=0, value="low")])["value"] == "high"


def test_a_derived_value_uses_the_same_input_the_table_shows(project, spec):
    """Otherwise one row could mix two sources: an altitude from A, its
    conversion from B."""
    root, conn = project
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://c.test/one", sample("plant_conflict.html"), spec, tier=3)
    spec.raw.setdefault("derived", {})["altitude_ft"] = {
        "on": "plant", "method": "formula", "inputs": ["altitude_m.min"],
        "formula": "convert(altitude_m.min, 'm', 'ft')", "unit": "ft"}
    from spider.spec import DerivedSpec
    spec.derived["altitude_ft"] = DerivedSpec.parse("altitude_ft",
                                                    spec.raw["derived"]["altitude_ft"])
    spec.entities["plant"].fields["altitude_ft"] = spec.entities["plant"].fields[
        "altitude_m"].__class__(name="altitude_ft", type="number", unit="ft")
    build(conn, spec, use_ai=False)

    plant = build_dataset(conn, spec).table("plant").as_dicts()[0]
    from spider.standardize.units import convert
    shown_min = float(str(plant["altitude_m"]).split("-")[0])
    assert plant["altitude_ft"] == round(convert(shown_min, "m", "ft"), 2)


def test_a_declared_default_fills_only_what_nothing_else_could(project, spec):
    """`default:` is the one value with no source, so it goes in last and is
    labelled as its own origin."""
    root, conn = project
    spec.entities["plant"].fields["habit"] = spec.entities["plant"].fields[
        "scientific_name"].__class__(name="habit", type="text", default="unknown")
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    report = build(conn, spec, use_ai=False)

    assert report.defaults == 1
    row = conn.execute(
        "SELECT value, origin, confidence, evidence, source_page, tier "
        "FROM attributes WHERE name='habit'").fetchone()
    assert row["value"] == "unknown"
    assert row["origin"] == "default"
    assert row["confidence"] == 0.0          # it is not evidence for anything
    assert row["source_page"] is None and row["tier"] is None
    assert "spider.yaml" in row["evidence"]

    # the value a page did state is untouched
    altitude = conn.execute(
        "SELECT origin FROM attributes WHERE name='altitude_m' "
        "AND status='accepted'").fetchone()
    assert altitude["origin"] == "extracted"


def test_a_default_never_displaces_something_a_page_said(project, spec):
    root, conn = project
    field = spec.entities["plant"].fields["altitude_m"]
    field.default = "0-0"
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    row = conn.execute(
        "SELECT value, origin FROM attributes WHERE name='altitude_m' "
        "AND status='accepted'").fetchone()
    assert row["value"] == "3000-4500" and row["origin"] == "extracted"


def test_a_default_is_not_remembered_as_something_a_page_said(project, spec):
    """value_history feeds rule re-learning, so a default must stay out of it."""
    root, conn = project
    spec.entities["plant"].fields["habit"] = spec.entities["plant"].fields[
        "scientific_name"].__class__(name="habit", type="text", default="unknown")
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    assert conn.execute(
        "SELECT COUNT(*) c FROM value_history WHERE field='habit'").fetchone()["c"] == 0


def test_coverage_separates_what_was_found_from_what_was_defaulted(project, spec):
    root, conn = project
    spec.entities["plant"].fields["habit"] = spec.entities["plant"].fields[
        "scientific_name"].__class__(name="habit", type="text", default="unknown")
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)

    from spider.report import coverage
    habit = next(c for c in coverage(conn, spec) if c.field == "habit")
    assert habit.filled == 1 and habit.defaults == 1
    assert habit.found == 0, "nothing was actually found for this column"


def test_an_export_can_leave_defaults_out_with_the_derived_values(project, spec, tmp_path):
    root, conn = project
    spec.entities["plant"].fields["habit"] = spec.entities["plant"].fields[
        "scientific_name"].__class__(name="habit", type="text", default="unknown")
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)

    from spider.spec import TargetSpec
    from spider.store.export import export_target
    result = export_target(conn, spec, TargetSpec(
        name="found_only", format="csv", path="out", include_derived=False), root)
    with open(result.path / "plant.csv", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert not rows[0].get("habit"), "only what a source gave should be exported"
