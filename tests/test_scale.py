"""Things that only go wrong with a lot of data: memory, speed and scale-only bugs."""

import csv
import time

import pytest
from conftest import sample, store_page

from spider.assemble.build import build
from spider.assemble.entities import EntityIndex
from spider.assemble.spool import CellStore
from spider.spec import Spec
from spider.store import db as store


def snapshot(conn):
    return conn.execute(
        "SELECT e.type, e.canonical_name, a.name, a.value, a.origin, a.confidence, "
        "a.status FROM attributes a JOIN entities e ON e.id = a.entity_id "
        "ORDER BY 1, 2, 3, 4, 5").fetchall()


def test_spilling_candidates_to_disk_changes_nothing(project, spec, monkeypatch):
    """Agreement and conflict scoring need every candidate for a cell together.
    A run that spills must give exactly the answer a run that does not."""
    root, conn = project
    for url, page, tier in [("http://a.test/1", "plant_tier1.html", 1),
                            ("http://b.test/1", "plant_tier2.html", 2),
                            ("http://c.test/1", "plant_conflict.html", 3),
                            ("http://c.test/2", "plant_hindi.html", 3)]:
        store_page(conn, url, sample(page), spec, tier=tier)

    monkeypatch.setenv("SPIDER_SPILL_AT", "100000")
    build(conn, spec, use_ai=False)
    in_memory = [tuple(r) for r in snapshot(conn)]

    monkeypatch.setenv("SPIDER_SPILL_AT", "3")            # spill constantly
    build(conn, spec, use_ai=False)
    spilled = [tuple(r) for r in snapshot(conn)]

    assert spilled == in_memory and len(in_memory) >= 8
    assert not (root / ".spider" / "candidates.spool").exists(), "scratch file left behind"


def test_the_spool_groups_a_cell_that_is_split_across_spills(tmp_path):
    from spider.extract.extractor import Candidate
    store_ = CellStore(tmp_path, limit=2)
    for tier, domain in [(1, "a.test"), (2, "b.test"), (3, "c.test"), (1, "d.test")]:
        store_.add(7, "altitude_m", Candidate(
            entity_type="plant", identity="x", field="altitude_m", raw_value="1",
            tier=tier, domain=domain))
    store_.add(8, "altitude_m", Candidate(
        entity_type="plant", identity="y", field="altitude_m", raw_value="2",
        tier=1, domain="a.test"))
    cells = {key: [c.domain for c in group] for key, group in store_}
    store_.close()
    assert cells[(7, "altitude_m")] == ["a.test", "b.test", "c.test", "d.test"]
    assert cells[(8, "altitude_m")] == ["a.test"]


# ---- the two n-squared bugs, guarded by behaviour rather than by a timer ----
def test_entity_matching_does_not_compare_a_record_with_every_other(project, spec,
                                                                    monkeypatch):
    """The first version compared each new name against all existing ones:
    2,000 records took 187 seconds. Names are now compared only within a block."""
    _root, conn = project
    from spider.standardize import names
    calls = {"n": 0}
    real = names.similarity_of_keys

    def counting(a, b):
        calls["n"] += 1
        return real(a, b)

    monkeypatch.setattr(names, "similarity_of_keys", counting)
    spec.entities["region"].fields["name"].vocabulary = None
    index = EntityIndex(conn, spec)
    total = 600
    for i in range(total):
        index.resolve("region", f"Settlement Number {i:05d}")
    assert calls["n"] < total * 5, (
        f"{calls['n']} comparisons for {total} records - that is n-squared "
        f"({total * (total - 1) // 2} would be)")


def test_two_records_that_differ_by_a_digit_are_never_merged(project, spec):
    """'Model 100' and 'Model 101' are 92% similar and completely different."""
    _root, conn = project
    index = EntityIndex(conn, spec)
    first = index.resolve("region", "Blue Cafe 100")
    second = index.resolve("region", "Blue Cafe 101")
    assert first != second


def test_a_genuine_spelling_variant_still_merges(project, spec):
    _root, conn = project
    index = EntityIndex(conn, spec)
    assert index.resolve("region", "Pithoragarh") == index.resolve("region", "Pithoragarh ")
    assert index.resolve("region", "Nainital") == index.resolve("region", "Nainitall")


def test_an_identifier_is_never_fuzzy_matched(tmp_path):
    spec = Spec.from_dict({"entities": {"shop": {"identity": ["shop_id"], "fields": {
        "shop_id": {"type": "number"}}}}})
    store.init_project(tmp_path)
    conn = store.connect(tmp_path)
    index = EntityIndex(conn, spec)
    assert index.resolve("shop", "100000") != index.resolve("shop", "100001")


def test_match_exact_turns_spelling_variants_off(tmp_path):
    spec = Spec.from_dict({"entities": {"place": {
        "identity": ["name"], "match": "exact",
        "fields": {"name": {"type": "text"}}}}})
    store.init_project(tmp_path)
    conn = store.connect(tmp_path)
    index = EntityIndex(conn, spec)
    assert index.resolve("place", "Chennai") != index.resolve("place", "Chenai")


def test_lookups_by_page_and_record_use_an_index(project):
    """Without these each lookup scans the whole table, and a build is
    quadratic in the number of pages."""
    _root, conn = project
    for sql in ["SELECT name, value FROM fields WHERE page_id=1",
                "SELECT data FROM structured WHERE page_id=1",
                "SELECT id FROM attributes WHERE source_page=1",
                "SELECT id FROM attributes INDEXED BY idx_attr_entity "
                "WHERE entity_id=1 AND status='accepted'"]:
        plan = conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()[0]["detail"]
        assert "SCAN" not in plan, f"'{sql}' scans the table: {plan}"


def test_an_old_project_gets_the_indexes_when_it_is_next_opened(tmp_path):
    store.init_project(tmp_path)
    conn = store.connect(tmp_path)
    conn.execute("DROP INDEX idx_fields_page")
    conn.execute("DELETE FROM meta WHERE key='fts_version'")
    conn.commit()
    conn.close()
    conn = store.connect(tmp_path)                       # migration runs on open
    plan = conn.execute("EXPLAIN QUERY PLAN SELECT * FROM fields WHERE page_id=1"
                        ).fetchall()[0]["detail"]
    assert "idx_fields_page" in plan


# ---- reading a big file ------------------------------------------------------
def test_a_large_csv_is_read_in_bulk_not_row_by_row(tmp_path):
    """52 seconds for 20,000 rows when it committed after every one."""
    store.init_project(tmp_path)
    (tmp_path / "spider.yaml").write_text("""
project: bulk
sources: {mode: only_listed, items: [{id: big, type: csv, location: big.csv, tier: 0,
          map: {id: shop_id, name: name}}]}
entities: {shop: {identity: [shop_id], fields: {shop_id: {type: text}, name: {type: text}}}}
""", encoding="utf-8")
    with open(tmp_path / "big.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "name"])
        for i in range(6000):
            writer.writerow([f"S{i:05d}", f"Shop {i}"])

    from spider import sources
    conn = store.connect(tmp_path)
    spec = Spec.load(tmp_path / "spider.yaml")
    started = time.time()
    result = sources.read_source(conn, spec, spec.sources.items[0], tmp_path)
    elapsed = time.time() - started
    assert result.rows == 6000
    assert elapsed < 8, f"6,000 rows took {elapsed:.1f}s"
    assert conn.execute("SELECT COUNT(*) c FROM pages").fetchone()["c"] == 6000


def test_reading_a_large_file_skips_the_search_index(tmp_path):
    """A full-text index over a million spreadsheet rows costs more than
    everything else about reading them, and nobody searches them."""
    from spider.sources import SEARCH_INDEX_ROW_LIMIT, _Rows
    assert _Rows(100).index_for_search
    assert not _Rows(SEARCH_INDEX_ROW_LIMIT + 1).index_for_search


# ---- the sanity check must not block a valid build ---------------------------
def test_a_coincidence_in_the_data_does_not_fail_the_normal_form_check(project, spec):
    """Two prices that happen to repeat with the same rating is not a
    dependency; refusing to write a valid dataset over it was a real failure."""
    from spider.store.normalize import Table, check_level
    rows = [[i, f"item{i}", price, rating] for i, (price, rating) in enumerate(
        [(10, 3), (10, 3), (25, 5), (25, 5), (31, 1), (47, 2), (52, 4), (63, 3),
         (71, 5), (88, 1), (91, 2), (12, 4), (15, 1), (18, 2), (22, 3)])]
    table = Table("book", ["book_id", "title", "price", "rating"], rows,
                  key=["book_id"], kind="entity")
    from spider.store.normalize import Dataset
    passed, problems = check_level(Dataset([table], "3NF", "project"), "3NF")
    assert passed, problems


def test_a_real_dependency_is_still_caught():
    """A low-cardinality column that determines another IS a lookup table."""
    from spider.store.normalize import Dataset, Table, check_level
    zones = {"a": "north", "b": "south", "c": "east"}
    rows = [[i, f"place{i}", key, zones[key]] for i, key in
            enumerate(["a", "b", "c"] * 10)]
    table = Table("place", ["place_id", "name", "zone_code", "zone_name"], rows,
                  key=["place_id"], kind="entity")
    passed, problems = check_level(Dataset([table], "3NF", "project"), "3NF")
    assert not passed and any("zone_name" in p and "zone_code" in p for p in problems)
