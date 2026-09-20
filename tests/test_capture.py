"""Saying what you want with an example or an existing structure (section 14A)."""

import json
import sqlite3

import pytest

from spider.capture import (CaptureError, from_example, from_json_schema, from_sql,
                            from_sqlite, from_structure, questions_for)
from spider.describe import draft
from spider.spec import Spec


def write_csv(tmp_path, text):
    path = tmp_path / "wanted.csv"
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------- channel 2: example
def test_a_csv_header_becomes_a_schema(tmp_path):
    path = write_csv(tmp_path, "Species,Alt (m),Flowering,District\n"
                               "Saussurea obvallata,3000-4500,July,Chamoli\n"
                               "Picrorhiza kurroa,3000-4300,June,Chamoli\n")
    document, notes = from_example(path, entity="plant")
    fields = document["entities"]["plant"]["fields"]
    assert fields["species"]["type"] == "text"
    assert fields["alt"] == {"type": "range", "unit": "m"}   # unit read from "(m)"
    assert fields["flowering"]["type"] == "month"            # from the values
    assert document["entities"]["plant"]["identity"] == ["species"]
    assert any("copied from" in n for n in notes)


def test_the_identity_is_the_column_whose_values_are_unique(tmp_path):
    path = write_csv(tmp_path, "district,species\nChamoli,Saussurea obvallata\n"
                               "Chamoli,Picrorhiza kurroa\n")
    document, _notes = from_example(path, entity="plant")
    assert document["entities"]["plant"]["identity"] == ["species"]


def test_types_are_read_from_the_sample_values(tmp_path):
    path = write_csv(tmp_path, "name,count,when,site,ok\n"
                               "a,12,2026-09-20,https://x.test,yes\n"
                               "b,8,2025-01-02,https://y.test,no\n")
    fields = from_example(path, entity="thing")[0]["entities"]["thing"]["fields"]
    assert fields["count"]["type"] == "number"
    assert fields["when"]["type"] == "date"
    assert fields["site"]["type"] == "url"
    assert fields["ok"]["type"] == "bool"


def test_a_json_example_works_too(tmp_path):
    path = tmp_path / "wanted.json"
    path.write_text(json.dumps([{"name": "a", "height_m": 12},
                                {"name": "b", "height_m": 9}]), encoding="utf-8")
    fields = from_example(path, entity="tree")[0]["entities"]["tree"]["fields"]
    assert fields["height_m"]["type"] == "number"


def test_an_unreadable_example_says_what_to_use(tmp_path):
    path = tmp_path / "notes.docx"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(CaptureError, match="CSV, Excel or JSON"):
        from_example(path)


# ----------------------------------------------------- channel 3: structure
SQL = """
CREATE TABLE plant (
  scientific_name TEXT NOT NULL PRIMARY KEY,
  altitude_m      INTEGER,
  first_seen      DATE,
  protected       BOOLEAN
);
CREATE TABLE region (name TEXT PRIMARY KEY, state TEXT);
CREATE TABLE plant_region (
  plant_id TEXT, region_id TEXT,
  FOREIGN KEY (region_id) REFERENCES region(name)
);
"""


def test_create_table_becomes_entities_with_their_keys():
    document, notes = from_sql(SQL)
    plant = document["entities"]["plant"]
    assert plant["identity"] == ["scientific_name"]
    assert plant["fields"]["altitude_m"]["type"] == "number"
    assert plant["fields"]["first_seen"]["type"] == "date"
    assert plant["fields"]["protected"]["type"] == "bool"
    assert plant["fields"]["scientific_name"]["required"] is True
    assert any("kept exactly as written" in n for n in notes)


def test_keys_become_relations_and_are_not_repeated():
    document, _notes = from_sql(SQL)
    pairs = [(r["from"], r["to"]) for r in document["relations"]]
    assert ("plant_region", "region") in pairs
    assert len(pairs) == len(set(pairs)), "a key found twice is still one relation"


def test_a_json_schema_is_imported():
    document, _notes = from_json_schema({
        "title": "Plant",
        "required": ["scientific_name"],
        "properties": {
            "scientific_name": {"type": "string"},
            "altitude_m": {"type": "integer"},
            "uses": {"type": "array"},
            "recorded": {"type": "string", "format": "date"},
            "habit": {"type": "string", "enum": ["herb", "shrub", "tree"]},
        }})
    fields = document["entities"]["plant"]["fields"]
    assert fields["scientific_name"]["required"] is True
    assert fields["altitude_m"]["type"] == "number"
    assert fields["uses"]["multiple"] is True
    assert fields["recorded"]["type"] == "date"
    assert fields["habit"]["sanity"] == ["herb", "shrub", "tree"]


def test_an_existing_sqlite_file_is_imported(tmp_path):
    path = tmp_path / "existing.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE plant (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
        "CREATE TABLE note (id INTEGER PRIMARY KEY, plant_id INTEGER, "
        "  FOREIGN KEY (plant_id) REFERENCES plant(id));")
    conn.commit()
    conn.close()
    document, _notes = from_structure(path)
    assert set(document["entities"]) == {"plant", "note"}
    assert document["entities"]["plant"]["identity"] == ["id"]
    assert any(r["to"] == "plant" for r in document["relations"])


def test_an_imported_schema_passes_check(tmp_path):
    document, _notes = from_sql(SQL)
    full, _questions, _how = draft("plants", structure=None)
    full["entities"] = document["entities"]
    full["relations"] = document["relations"]
    spec = Spec.from_dict(full)
    assert [p for p in spec.validate() if p.level == "error"] == []


def test_at_most_five_questions_each_with_a_suggestion():
    document, _notes = from_sql(SQL)
    questions = questions_for(document)
    assert 1 <= len(questions) <= 5
    assert all("?" in q.ask for q in questions)
    assert any("one row" in q.ask.lower() for q in questions)
    for question in questions:
        assert question.suggested or question.free_text
