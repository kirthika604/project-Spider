"""The review queue: decisions a person makes, kept across rebuilds (stage 4)."""

import pytest
from conftest import sample, store_page

from spider import review as review_module
from spider.assemble.build import build


@pytest.fixture
def conflicted(project, spec):
    """Two sources disagree about altitude: one tier 1, one tier 3.

    The blog page never says the word "plant", so the spec's keywords grow
    to include "grows" - otherwise the crawl would never have saved it.
    """
    root, conn = project
    spec.sources.keywords = ["plant", "grows"]
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://c.test/one", sample("plant_conflict.html"), spec, tier=3)
    report = build(conn, spec, use_ai=False)
    return root, conn, spec, report


def open_conflict(conn):
    items = review_module.open_items(conn, kind="conflict")
    assert items, "the build queued the disagreement for review"
    return items[0]


def test_a_conflict_waits_for_a_person_and_both_values_stay(conflicted):
    _root, conn, _spec, report = conflicted
    item = open_conflict(conn)
    assert len(item["detail"]["options"]) == 2
    statuses = {r["status"] for r in conn.execute(
        "SELECT status FROM attributes WHERE name='altitude_m'")}
    assert statuses == {"accepted", "review"}, \
        "the loser is kept and flagged, never hidden"


def test_keeping_one_option_marks_the_others_superseded(conflicted):
    _root, conn, _spec, _report = conflicted
    item = open_conflict(conn)
    result = review_module.keep(conn, item["id"], 1)
    assert "superseded" in result["note"]
    statuses = {r["status"] for r in conn.execute(
        "SELECT status FROM attributes WHERE name='altitude_m'")}
    assert statuses == {"accepted", "superseded"}
    row = conn.execute("SELECT * FROM review_queue WHERE id=?", (item["id"],)).fetchone()
    assert row["status"] == "resolved"


def test_keeping_all_leaves_every_value_in_the_dataset(conflicted):
    _root, conn, _spec, _report = conflicted
    item = open_conflict(conn)
    review_module.keep(conn, item["id"], "all")
    statuses = {r["status"] for r in conn.execute(
        "SELECT status FROM attributes WHERE name='altitude_m'")}
    assert "superseded" not in statuses


def test_reject_takes_the_value_out_but_keeps_the_row(conflicted):
    _root, conn, _spec, _report = conflicted
    item = open_conflict(conn)
    result = review_module.reject(conn, item["id"])
    assert result["kept"] is None
    statuses = {r["status"] for r in conn.execute(
        "SELECT status FROM attributes WHERE name='altitude_m'")}
    assert statuses == {"superseded"}


def test_a_decision_survives_a_rebuild(conflicted):
    root, conn, spec, _report = conflicted
    item = open_conflict(conn)
    review_module.keep(conn, item["id"], 1)
    kept = conn.execute(
        "SELECT value FROM attributes WHERE name='altitude_m' "
        "AND status='accepted'").fetchone()["value"]

    build(conn, spec, use_ai=False)          # everything is recomputed

    now_kept = conn.execute(
        "SELECT value FROM attributes WHERE name='altitude_m' "
        "AND status='accepted'").fetchone()["value"]
    assert now_kept == kept, "the build re-applied the decision"
    assert conn.execute(
        "SELECT COUNT(*) c FROM review_queue WHERE status='open'").fetchone()["c"] == 0


def test_forgetting_lets_the_user_change_their_mind(conflicted):
    root, conn, spec, _report = conflicted
    item = open_conflict(conn)
    review_module.keep(conn, item["id"], 1)
    assert review_module.forget(conn, item["target"]) == 1
    build(conn, spec, use_ai=False)
    assert open_conflict(conn), "without a stored decision, the question is asked again"


def test_a_resolved_conflict_that_sources_now_agree_on_drops_its_decision(project, spec):
    root, conn = project
    spec.sources.keywords = ["plant", "grows"]
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://c.test/one", sample("plant_conflict.html"), spec, tier=3)
    build(conn, spec, use_ai=False)
    item = open_conflict(conn)
    review_module.keep(conn, item["id"], 1)
    page_id = conn.execute("SELECT id FROM pages WHERE url='http://c.test/one'").fetchone()[0]
    for table, column in (("links", "from_page"), ("fields", "page_id"),
                          ("structured", "page_id"), ("attributes", "source_page"),
                          ("relations", "source_page")):
        conn.execute(f"DELETE FROM {table} WHERE {column}=?", (page_id,))
    conn.execute("DELETE FROM pages WHERE id=?", (page_id,))
    build(conn, spec, use_ai=False)          # only one source left: no conflict
    assert conn.execute(
        "SELECT COUNT(*) c FROM review_decisions").fetchone()["c"] == 0


def test_low_confidence_can_be_kept_anyway_or_left_out(project, spec):
    root, conn = project
    spec.sources.keywords = ["plant", "grows"]
    spec.standardize.min_confidence = 0.9     # force the tier-3 value into review
    store_page(conn, "http://c.test/one", sample("plant_conflict.html"), spec, tier=3)
    build(conn, spec, use_ai=False)

    def altitude_item():
        items = [i for i in review_module.open_items(conn, kind="low_confidence")
                 if i["field"] == "altitude_m"]
        assert items, "the weak tier-3 value went to review"
        return items[0]

    item = altitude_item()
    review_module.reject(conn, item["id"])
    assert conn.execute(
        "SELECT COUNT(*) c FROM attributes WHERE status='accepted' "
        "AND name='altitude_m'").fetchone()["c"] == 0
    # a second chance: the user drops the decision and keeps the value instead
    review_module.forget(conn, item["target"])
    build(conn, spec, use_ai=False)
    review_module.keep(conn, altitude_item()["id"], "keep")
    assert conn.execute(
        "SELECT COUNT(*) c FROM attributes WHERE status='accepted' "
        "AND name='altitude_m'").fetchone()["c"] == 1


def test_explain_shows_who_decided(conflicted):
    from spider.report import explain
    _root, conn, spec, _report = conflicted
    item = open_conflict(conn)
    review_module.keep(conn, item["id"], 1)
    chain = explain(conn, spec, "Saussurea obvallata", "altitude_m")
    decided = [v for v in chain["values"]
               if v["origin"] == "extracted" and (v.get("lineage") or {}).get("user_decision")]
    assert decided, "the kept value records that a person chose it"


def test_the_cli_lists_and_resolves_items(conflicted, capsys):
    from spider.cli import main
    root, conn, _spec, _report = conflicted
    item = open_conflict(conn)
    assert main(["--project", str(root), "review"]) == 0
    out = capsys.readouterr().out
    assert str(item["id"]) in out and "spider review show" in out
    assert main(["--project", str(root), "review", "keep",
                 str(item["id"]), "1"]) == 0
    out = capsys.readouterr().out
    assert "superseded" in out
    conn.close()


def test_review_reject_needs_a_real_item(tmp_path):
    from spider.cli import main
    from spider.store import db as store
    store.init_project(tmp_path)
    assert main(["--project", str(tmp_path), "review", "reject", "99"]) == 1
