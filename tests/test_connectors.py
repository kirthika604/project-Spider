"""Connectors: scoping, caching and the rule that a check cannot overwrite."""

import json

from conftest import sample, store_page

from spider.assemble.build import build
from spider.connectors import REGISTRY, load, run
from spider.connectors.base import Connector, ConnectorResult, ConnectorValue
from spider.spec import Spec


def test_a_species_registry_is_never_asked_about_a_district(project, spec):
    _root, conn = project
    gbif = REGISTRY["gbif"](conn, {})
    assert gbif.applies_to("plant", spec)            # identified by scientific_name
    assert not gbif.applies_to("region", spec)
    assert not gbif.applies_to("use", spec)


def test_for_in_the_project_file_overrides_the_default_scope(project, spec):
    _root, conn = project
    gbif = REGISTRY["gbif"](conn, {"for": ["region"]})
    assert gbif.applies_to("region", spec) and not gbif.applies_to("plant", spec)


def test_an_unknown_connector_name_is_skipped_not_fatal(project):
    _root, conn = project
    spec = Spec.from_dict({"entities": {"a": {"identity": ["n"],
                                              "fields": {"n": {"type": "text"}}}},
                           "connectors": [{"name": "not_a_service"}]})
    assert load(conn, spec) == []


class FakeRegistry(Connector):
    """Stands in for a live service so the test needs no network."""
    name = "fake"
    tier = 1
    calls_made = 0

    def lookup(self, entity_type, name, values):
        FakeRegistry.calls_made += 1
        return ConnectorResult(self.name, [
            ConnectorValue("scientific_name", "Saussurea obvallata",
                           "fake registry record 1", "http://fake.test/1", 1),
            ConnectorValue("alias", "Brahma Kamal", "fake registry record 1",
                           "http://fake.test/1", 1, kind="alias"),
            ConnectorValue("fake_id", "X1", "fake registry record 1",
                           "http://fake.test/1", 1, kind="identifier"),
        ])


def test_a_connector_confirms_a_value_and_raises_its_confidence(project, spec,
                                                                monkeypatch):
    _root, conn = project
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    before = conn.execute(
        "SELECT confidence FROM attributes WHERE name='scientific_name' "
        "AND status='accepted'").fetchone()["confidence"]

    monkeypatch.setitem(REGISTRY, "fake", FakeRegistry)
    spec.connectors = [{"name": "fake", "for": ["plant"]}]
    summary = run(conn, spec)

    after = conn.execute(
        "SELECT confidence FROM attributes WHERE name='scientific_name' "
        "AND status='accepted'").fetchone()["confidence"]
    assert after > before
    assert summary["aliases"] >= 1 and summary["identifiers"] >= 1
    aliases = [r["alias"] for r in conn.execute("SELECT alias FROM entity_aliases")]
    assert "Brahma Kamal" in aliases


def test_a_cross_check_that_disagrees_flags_instead_of_overwriting(project, spec):
    _root, conn = project
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)

    class Elevation(Connector):
        name = "elevation"
        tier = 1

        def lookup(self, entity_type, name, values):
            return ConnectorResult(self.name, [
                ConnectorValue("elevation_m", "120", "measured 120 m",
                               "http://elev.test", 1, unit="m", kind="check")])

        def disagrees(self, low, high, measured):
            return True

    from spider.connectors import _record_check
    entity_id = conn.execute(
        "SELECT id FROM entities WHERE type='plant'").fetchone()["id"]
    before = conn.execute(
        "SELECT value, confidence FROM attributes WHERE entity_id=? AND "
        "name='altitude_m' AND status='accepted'", (entity_id,)).fetchone()

    flagged = _record_check(conn, spec, Elevation(conn, {}), entity_id,
                            ConnectorValue("elevation_m", "120", "measured 120 m",
                                           "http://elev.test", 1, kind="check"))
    after = conn.execute(
        "SELECT value, confidence FROM attributes WHERE entity_id=? AND "
        "name='altitude_m' AND status='accepted'", (entity_id,)).fetchone()

    assert flagged
    assert after["value"] == before["value"]          # the value is untouched
    assert after["confidence"] < before["confidence"]  # only the trust moved
    queued = conn.execute(
        "SELECT reason, detail FROM review_queue WHERE kind='conflict'").fetchone()
    assert "elevation service" in queued["reason"]
    assert json.loads(queued["detail"])["measured"] == 120


def test_responses_are_cached_so_a_rerun_makes_no_calls(project, spec, monkeypatch):
    _root, conn = project
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)

    class Counting(Connector):
        name = "counting"
        tier = 1
        hits = 0

        def lookup(self, entity_type, name, values):
            data = self.cached_get("http://cache.test/api", {"name": name})
            return ConnectorResult(self.name, [], "cached" if data else "miss",
                                   ok=bool(data))

    conn.execute(
        "INSERT INTO ai_cache(key,kind,response,created_at) VALUES(?,?,?,?)",
        ('counting:http://cache.test/api:{"name": "Saussurea obvallata"}'[:200],
         "connector:counting", json.dumps({"ok": True}), "2026-01-01"))
    conn.commit()

    monkeypatch.setitem(REGISTRY, "counting", Counting)
    spec.connectors = [{"name": "counting", "for": ["plant"]}]
    summary = run(conn, spec)
    assert summary["connectors"][0]["calls"] == 0
    assert summary["connectors"][0]["cache_hits"] == 1
