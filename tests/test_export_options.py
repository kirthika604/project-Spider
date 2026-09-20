"""Every structure and design option the field reference documents (14B)."""

import csv
import json
import zipfile

import pytest
from conftest import sample, store_page

from spider.assemble.build import build
from spider.pack import read as read_pack, write as write_pack
from spider.spec import TargetSpec
from spider.store.export import export_target
from spider.store.normalize import build_dataset


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


def test_provenance_as_columns_puts_the_source_beside_the_value(filled):
    root, conn, spec = filled
    result = export_target(conn, spec, TargetSpec(
        name="t", format="csv", path="out", provenance="columns"), root)
    rows = rows_of(result.path / "plant.csv")
    assert rows[0]["altitude_m"] == "3000-4500"
    assert float(rows[0]["altitude_m_confidence"]) >= 0.9
    assert rows[0]["altitude_m_origin"] == "extracted"
    assert rows[0]["altitude_m_source"].startswith("http")
    assert not (result.path / "provenance.csv").exists()   # not both


def test_provenance_none_leaves_the_evidence_out(filled):
    root, conn, spec = filled
    result = export_target(conn, spec, TargetSpec(
        name="t", format="csv", path="out", provenance="none"), root)
    rows = rows_of(result.path / "plant.csv")
    assert "altitude_m_source" not in rows[0]
    assert not (result.path / "provenance.csv").exists()


def test_shape_long_gives_one_row_per_field(filled):
    root, conn, spec = filled
    result = export_target(conn, spec, TargetSpec(
        name="t", format="csv", path="out", shape="long", provenance="columns"), root)
    rows = rows_of(result.path / "plant.csv")
    assert {"plant_id", "field", "value", "confidence", "origin", "source"} <= set(rows[0])
    fields = {r["field"] for r in rows}
    assert {"scientific_name", "altitude_m", "climate_zone"} <= fields
    altitude = next(r for r in rows if r["field"] == "altitude_m")
    assert altitude["value"] == "3000-4500" and altitude["source"].startswith("http")


def test_sort_by_orders_the_rows(filled):
    root, conn, spec = filled
    result = export_target(conn, spec, TargetSpec(
        name="t", format="csv", path="out", shape="long", sort_by="field"), root)
    fields = [r["field"] for r in rows_of(result.path / "plant.csv")]
    assert fields == sorted(fields)

    descending = export_target(conn, spec, TargetSpec(
        name="t2", format="csv", path="out2", shape="long", sort_by="field",
        descending=True), root)
    fields = [r["field"] for r in rows_of(descending.path / "plant.csv")]
    assert fields == sorted(fields, reverse=True)


def test_a_column_can_be_asked_for_in_another_unit(filled):
    root, conn, spec = filled
    result = export_target(conn, spec, TargetSpec(
        name="t", format="csv", path="out",
        columns=[{"field": "scientific_name", "label": "Name"},
                 {"field": "altitude_m", "label": "Altitude (ft)", "unit": "ft"}]), root)
    rows = rows_of(result.path / "plant.csv")
    assert "Altitude (ft)" in rows[0] and "altitude_m" not in rows[0]
    low = float(rows[0]["Altitude (ft)"].split("-")[0])
    assert 9800 < low < 9900             # 3000 m in feet


def test_split_single_stacks_every_table_into_one_file(filled):
    root, conn, spec = filled
    result = export_target(conn, spec, TargetSpec(
        name="t", format="csv", path="out/all.csv", split="single"), root)
    rows = rows_of(result.path)
    assert result.path.name == "all.csv"
    assert {"plant", "region", "use"} <= {r["table"] for r in rows}


def test_nested_json_keeps_related_records_inside(filled):
    root, conn, spec = filled
    result = export_target(conn, spec, TargetSpec(
        name="t", format="json", path="out/feed.json", nesting="nested",
        naming="camelCase"), root)
    payload = json.loads(result.path.read_text())
    plant = payload["plant"][0]
    assert "scientificName" in plant
    assert isinstance(plant["growsIn"], list)


def test_parquet_when_pyarrow_is_installed(filled):
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as parquet
    root, conn, spec = filled
    result = export_target(conn, spec, TargetSpec(
        name="t", format="parquet", path="out"), root)
    table = parquet.read_table(result.path / "plant.parquet")
    assert "scientific_name" in table.column_names and table.num_rows >= 1


def test_a_jinja_template_renders_any_text_format(filled):
    pytest.importorskip("jinja2")
    root, conn, spec = filled
    (root / "note.j2").write_text(
        "{{ project }}: {% for p in tables['plant'] %}{{ p.scientific_name }}"
        "={{ p.altitude_m }};{% endfor %}", encoding="utf-8")
    result = export_target(conn, spec, TargetSpec(
        name="t", format="template", path="out/note.txt", template="note.j2"), root)
    text = result.path.read_text()
    assert "Saussurea obvallata=3000-4500" in text


def test_min_confidence_filters_what_is_written(filled):
    root, conn, spec = filled
    result = export_target(conn, spec, TargetSpec(
        name="t", format="csv", path="out", min_confidence=0.99), root)
    rows = rows_of(result.path / "plant.csv")
    kept = [r for r in rows if r.get("altitude_m")]
    assert not kept or all(float(r.get("altitude_m_confidence", 1)) >= 0.99
                           for r in kept if "altitude_m_confidence" in r)


# ------------------------------------------------------------- dataset pack
def test_a_pack_holds_the_dataset_and_its_evidence(filled):
    root, conn, spec = filled
    made = write_pack(conn, spec, root)
    assert made.path.exists() and made.values > 0

    manifest = read_pack(made.path)
    assert manifest["project"] == spec.project
    assert manifest["counts"]["values"] == made.values
    assert "how_to_verify" in manifest

    with zipfile.ZipFile(made.path) as archive:
        names = archive.namelist()
        assert "dataset.db" in names and "README.md" in names
        assert any(n.startswith("csv/") for n in names)
        readme = archive.read("README.md").decode()
    assert "provenance" in readme


def test_a_pack_can_carry_the_pages_so_a_value_is_checkable_offline(filled):
    root, conn, spec = filled
    made = write_pack(conn, spec, root, include_pages=True)
    with zipfile.ZipFile(made.path) as archive:
        assert "pages.db" in archive.namelist()


def test_a_file_that_is_not_a_pack_is_refused(tmp_path):
    path = tmp_path / "other.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("hello.txt", "hi")
    with pytest.raises(ValueError, match="not a Spider pack"):
        read_pack(path)
