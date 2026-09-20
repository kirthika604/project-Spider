"""The commands themselves, driven the way a user drives them."""

import sqlite3

from conftest import sample, store_page

from spider.cli import main
from spider.spec import Spec
from spider.store import db as store


def run(args, folder):
    return main(["--project", str(folder), *args])


def test_init_creates_the_database_and_reference_data(tmp_path, capsys):
    assert main(["init", str(tmp_path)]) == 0
    assert (tmp_path / ".spider" / "spider.db").exists()
    conn = store.connect(tmp_path)
    assert conn.execute("SELECT COUNT(*) c FROM ref_units").fetchone()["c"] > 0
    assert conn.execute("SELECT COUNT(*) c FROM ref_places").fetchone()["c"] == 0
    conn.close()
    assert "Reference data loaded" in capsys.readouterr().out


def test_a_project_is_found_from_a_sub_folder(tmp_path):
    store.init_project(tmp_path)
    deep = tmp_path / "src" / "app"
    deep.mkdir(parents=True)
    assert store.find_project(deep) == tmp_path


def test_check_reports_errors_and_returns_two(tmp_path, capsys):
    store.init_project(tmp_path)
    (tmp_path / "spider.yaml").write_text(
        "mode: project\nstorage: {normal_form: 1NF}\n"
        "entities: {plant: {identity: [name], fields: {name: {type: text}}}}\n",
        encoding="utf-8")
    assert run(["check"], tmp_path) == 2
    assert "below 3NF" in capsys.readouterr().out


def test_check_passes_a_good_file(tmp_path, spec, capsys):
    store.init_project(tmp_path)
    from spider.ref import tables as ref_tables
    conn = store.connect(tmp_path)
    ref_tables.load_preset(conn, "himalayan-plants")
    conn.close()
    spec.save(tmp_path / "spider.yaml")
    assert run(["check"], tmp_path) == 0
    assert "The file is valid" in capsys.readouterr().out


def test_sql_is_read_only(tmp_path, capsys):
    store.init_project(tmp_path)
    assert run(["sql", "DELETE FROM pages"], tmp_path) == 1
    assert run(["sql", "DROP TABLE pages"], tmp_path) == 1
    assert run(["sql", "SELECT COUNT(*) FROM pages"], tmp_path) == 0
    conn = store.connect(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 0
    conn.close()


def test_search_get_and_stats(tmp_path, spec, capsys):
    store.init_project(tmp_path)
    spec.save(tmp_path / "spider.yaml")
    conn = store.connect(tmp_path)
    page_id = store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec)
    conn.close()

    assert run(["search", "alpine herb"], tmp_path) == 0
    assert "Saussurea" in capsys.readouterr().out

    assert run(["get", str(page_id)], tmp_path) == 0
    output = capsys.readouterr().out
    assert "http://a.test/one" in output and "Custom fields" in output

    assert run(["stats"], tmp_path) == 0
    assert "Pages" in capsys.readouterr().out


def test_build_report_and_export_run_from_the_command_line(tmp_path, spec, capsys):
    store.init_project(tmp_path)
    spec.save(tmp_path / "spider.yaml")
    conn = store.connect(tmp_path)
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://b.test/one", sample("plant_tier2.html"), spec, tier=2)
    conn.close()

    assert run(["build", "--no-ai"], tmp_path) == 0
    output = capsys.readouterr().out
    assert "3NF check passed" in output

    assert run(["report"], tmp_path) == 0
    assert "Coverage" in capsys.readouterr().out

    assert run(["export", "-f", "sqlite"], tmp_path) == 0
    capsys.readouterr()
    written = list((tmp_path / "exports").rglob("*.db"))
    assert written, "an export file was written"
    out = sqlite3.connect(written[0])
    assert out.execute("SELECT COUNT(*) FROM plant").fetchone()[0] == 1
    out.close()


def test_describe_drafts_a_file_that_checks_out(tmp_path, capsys):
    store.init_project(tmp_path)
    assert run(["describe", "plants of Uttarakhand and their uses", "--no-ai"],
               tmp_path) == 0
    assert (tmp_path / "spider.yaml").exists()
    drafted = Spec.load(tmp_path / "spider.yaml")
    assert [p for p in drafted.validate() if p.level == "error"] == []


def test_source_add_reads_a_csv_and_writes_it_into_the_file(tmp_path, spec, capsys):
    store.init_project(tmp_path)
    spec.save(tmp_path / "spider.yaml")
    survey = tmp_path / "survey.csv"
    survey.write_text("Species,Alt (m)\nSaussurea obvallata,3000-4500\n", encoding="utf-8")

    assert run(["source", "add", str(survey), "--tier", "0",
                "--map", "Species=scientific_name,Alt (m)=altitude_m"], tmp_path) == 0
    assert "scientific_name" in capsys.readouterr().out

    reloaded = Spec.load(tmp_path / "spider.yaml")
    assert reloaded.sources.items and reloaded.sources.items[0].tier == 0

    conn = store.connect(tmp_path)
    page = conn.execute("SELECT url, tier FROM pages").fetchone()
    assert page["tier"] == 0 and "#row=2" in page["url"]   # evidence points at the row
    conn.close()


def test_ref_list_and_edit(tmp_path, capsys):
    store.init_project(tmp_path)
    assert run(["ref", "load", "--preset", "himalayan-plants"], tmp_path) == 0
    capsys.readouterr()
    assert run(["ref", "list", "--kind", "places"], tmp_path) == 0
    assert "Uttarakhand" in capsys.readouterr().out
    assert run(["ref", "edit", "--kind", "categories",
                "--set", "vocabulary=uses,term=dye,alias=colouring"], tmp_path) == 0
    conn = store.connect(tmp_path)
    from spider.ref.tables import vocab_term
    assert vocab_term(conn, "uses", "colouring") == "dye"
    conn.close()


def test_a_missing_project_says_so_without_a_traceback(tmp_path, capsys):
    assert main(["--project", str(tmp_path / "nothing"), "stats"]) == 1
    assert "spider init" in capsys.readouterr().err
