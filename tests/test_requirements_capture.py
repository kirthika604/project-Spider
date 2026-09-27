"""Requirements traceability, part 4: capture, derivation, reference, lineage, pack."""

import json
import sqlite3
import time

import pytest
from conftest import sample, store_page

from spider.assemble.build import build
from spider.cli import main
from spider.spec import Spec


def run(args, folder):
    return main(["--project", str(folder), *args])


# --------------------------------------------------- §14 capture: channels 1-3
def draft_spec(tmp_path, capsys, *args):
    out = tmp_path / "spider.yaml"
    assert main(["init", str(tmp_path), "-q"]) == 0
    assert main(["--project", str(tmp_path), "describe", *args, "-o", str(out),
                 "--no-ai"]) == 0
    capsys.readouterr()
    return Spec.load(out)


def test_S14_channel_1_plain_words_draft_entities_and_fields(tmp_path, capsys):
    spec = draft_spec(tmp_path, capsys, "plants of Uttarakhand and their uses")
    assert spec.entities and any(spec.entities[e].fields for e in spec.entities)


def test_S14_channel_2_an_example_csv_fixes_the_columns_and_units(tmp_path, capsys):
    example = tmp_path / "example.csv"
    example.write_text("Name,Altitude (m),Flowering month\nRheum emodi,3200,July\n"
                       "Aconitum,2800,August\n", encoding="utf-8")
    spec = draft_spec(tmp_path, capsys, "--like", str(example))
    fields = next(iter(spec.entities.values())).fields
    assert len(fields) == 3
    altitude = next(f for n, f in fields.items() if "altitude" in n)
    assert altitude.unit == "m" and altitude.type in ("number", "range")


def test_S14_channel_3_create_table_statements_keep_names_and_keys(tmp_path, capsys):
    sql = tmp_path / "schema.sql"
    sql.write_text("CREATE TABLE plant (\n  scientific_name TEXT PRIMARY KEY,\n"
                   "  altitude_m REAL,\n  family TEXT\n);\n", encoding="utf-8")
    spec = draft_spec(tmp_path, capsys, "--from", str(sql))
    plant = spec.entities["plant"]
    assert set(plant.fields) >= {"scientific_name", "altitude_m", "family"}
    assert plant.identity == ["scientific_name"]


def test_S14_channel_3_a_json_schema_is_understood(tmp_path, capsys):
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"title": "plant", "type": "object",
                                  "required": ["scientific_name"],
                                  "properties": {"scientific_name": {"type": "string"},
                                                 "altitude_m": {"type": "number"}}}),
                      encoding="utf-8")
    spec = draft_spec(tmp_path, capsys, "--from", str(schema))
    assert "altitude_m" in spec.entities["plant"].fields


def test_S14_channel_3_an_existing_sqlite_file_is_mapped(tmp_path, capsys):
    path = tmp_path / "app.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE plant (scientific_name TEXT PRIMARY KEY, height_m REAL)")
    db.commit()
    db.close()
    spec = draft_spec(tmp_path, capsys, "--from", str(path))
    assert "height_m" in spec.entities["plant"].fields


def test_S14_at_most_five_questions_are_asked(tmp_path, capsys):
    from spider import describe
    _doc, questions, _how = describe.draft("plants and their uses", use_ai=False)
    assert len(questions) <= 5


# ---------------------------------------------------------------- FR-22 / D7
def test_FR_22_a_derived_field_stores_its_formula_inputs_and_explanation(project, spec):
    root, conn = project
    store_page(conn, "http://a.test/1", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    row = conn.execute("SELECT origin, lineage, confidence FROM attributes "
                       "WHERE name='climate_zone'").fetchone()
    lineage = json.loads(row["lineage"])
    assert row["origin"] == "derived"
    assert lineage["inputs"] and lineage["explain"] and lineage["method"] == "lookup"
    assert row["confidence"] <= 0.95


def test_FR_22_a_derived_value_is_never_more_certain_than_its_inputs(project, spec):
    root, conn = project
    store_page(conn, "http://b.test/1", sample("plant_tier2.html"), spec, tier=2)
    build(conn, spec, use_ai=False)
    inputs = conn.execute("SELECT MIN(confidence) c FROM attributes "
                          "WHERE name='altitude_m' AND status='accepted'").fetchone()["c"]
    derived = conn.execute("SELECT confidence FROM attributes "
                           "WHERE name='climate_zone'").fetchone()["confidence"]
    assert derived <= inputs


def test_FR_22_review_required_holds_a_derived_value_until_approved(project, spec):
    root, conn = project
    spec.derived["climate_zone"].review = "required"
    store_page(conn, "http://a.test/1", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    row = conn.execute("SELECT status FROM attributes WHERE name='climate_zone'").fetchone()
    assert row["status"] != "accepted"


# ---------------------------------------------------------------------- FR-25
def test_FR_25_policy_suggest_proposes_and_off_proposes_nothing(project, spec):
    root, conn = project
    spec.derived.clear()
    from spider.spec import FieldSpec
    spec.entities["plant"].fields["altitude_ft"] = FieldSpec(name="altitude_ft",
                                                              type="range", unit="ft")
    store_page(conn, "http://a.test/1", sample("plant_tier1.html"), spec, tier=1)
    spec.derive_policy = "suggest"
    build(conn, spec, use_ai=False)
    suggested = conn.execute(
        "SELECT COUNT(*) FROM review_queue WHERE kind='suggestion'").fetchone()[0]
    assert suggested >= 1
    conn.execute("DELETE FROM review_queue")
    spec.derive_policy = "off"
    build(conn, spec, use_ai=False)
    assert conn.execute(
        "SELECT COUNT(*) FROM review_queue WHERE kind='suggestion'").fetchone()[0] == 0


def test_FR_25_an_ai_proposed_formula_always_needs_approval(project, spec):
    root, conn = project
    spec.derived["climate_zone"].suggested_by = "ai"
    spec.derived["climate_zone"].review = "none"
    store_page(conn, "http://a.test/1", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    assert conn.execute("SELECT status FROM attributes WHERE name='climate_zone'"
                        ).fetchone()["status"] != "accepted"


# ---------------------------------------------------------------------- FR-26
def test_FR_26_the_reference_can_be_loaded_listed_and_edited(tmp_path, capsys):
    assert main(["init", str(tmp_path), "-q"]) == 0
    csv_file = tmp_path / "cats.csv"
    csv_file.write_text("vocabulary,term,alias\nuses,tea,chai\n", encoding="utf-8")
    assert run(["ref", "load", str(csv_file), "--kind", "vocab"], tmp_path) == 0
    capsys.readouterr()
    assert run(["ref", "list", "--kind", "vocab"], tmp_path) == 0
    assert "chai" in capsys.readouterr().out
    assert run(["ref", "list", "--kind", "counts"], tmp_path) == 0


def test_FR_26_the_reference_covers_every_table_the_document_names(tmp_path):
    from spider.store import db as store
    store.init_project(tmp_path)
    conn = store.connect(tmp_path)
    for table in ("ref_units", "ref_places", "ref_vocab", "ref_authority_ids",
                  "ref_source_rank"):
        conn.execute(f"SELECT * FROM {table}")
    conn.close()


# ---------------------------------------------------------------------- FR-27
def test_FR_27_every_command_the_document_names_exists():
    from spider.cli import build_parser
    parser = build_parser()
    commands = set(parser._subparsers._group_actions[0].choices)
    assert {"describe", "check", "build", "report", "fill", "derive", "explain",
            "export", "review", "discover", "dashboard", "ref"} <= commands


# ---------------------------------------------------------------------- FR-28
def test_FR_28_a_value_traces_back_to_its_page_and_sentence(project, spec):
    root, conn = project
    store_page(conn, "http://a.test/1", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    from spider.report import explain
    out = explain(conn, spec, "Saussurea obvallata", "altitude_m")
    value = out["values"][0]
    assert value["sources"][0]["url"] == "http://a.test/1"
    assert "3,000 to 4,500" in value["evidence"]


def test_FR_28_a_derived_value_traces_to_the_values_it_came_from(project, spec):
    root, conn = project
    store_page(conn, "http://a.test/1", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    from spider.report import explain
    zone = explain(conn, spec, "Saussurea obvallata", "climate_zone")["values"][0]
    assert zone["origin"] == "derived"
    assert zone["inputs"][0]["name"] == "altitude_m"
    assert zone["inputs"][0]["url"] == "http://a.test/1"


# ------------------------------------------------------------------ D3 / D5 / D6
def test_D5_a_dataset_pack_can_be_opened_and_reverified_offline(project, spec, tmp_path):
    root, conn = project
    spec.path = root / "spider.yaml"
    store_page(conn, "http://a.test/1", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    from spider.pack import read, write
    packed = write(conn, spec, root, tmp_path / "out.zip")
    contents = read(packed.path)
    assert contents["files"] and packed.values > 0


def test_D3_gap_driven_crawling_names_the_empty_cells_it_is_after(project, spec):
    root, conn = project
    store_page(conn, "http://a.test/x",
               "<html><body><p>a plant</p><p class='sci'>Rheum emodi</p></body></html>",
               spec, tier=1)
    build(conn, spec, use_ai=False)
    from spider.crawl.discovery import queries_for_gaps
    from spider.report import gaps
    assert queries_for_gaps(conn, spec, gaps(conn, spec))


# ---------------------------------------------------------------------- NFR-6
def test_NFR_6_the_readme_quick_start_commands_all_exist():
    import re
    from pathlib import Path
    from spider.cli import build_parser
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    commands = set(build_parser()._subparsers._group_actions[0].choices)
    quick = readme.split("\n## ", 2)[1:]
    used = set(re.findall(r"^\s*spider ([a-z]+)", "\n".join(quick), re.M))
    assert used and used <= commands | {"--help"}, used - commands


def test_NFR_6_first_results_arrive_in_seconds_not_minutes(multisite, tmp_path):
    started = time.time()
    root = tmp_path
    assert main(["init", str(root), "-q"]) == 0
    assert run(["crawl", f"{multisite['a']}/index.html", "-d", "1",
                "-n", "5", "--delay", "0", "-k", "plant"], root) == 0
    assert run(["search", "alpha"], root) == 0
    assert time.time() - started < 60
