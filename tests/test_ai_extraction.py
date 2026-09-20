"""The AI route, driven by a stub model so the test needs no key or network.

What matters is not that the model is clever but that nothing it returns can
enter the dataset without a quote that is really on the page.
"""

import json

from conftest import sample, store_page

from spider.assemble.build import build
from spider.extract.ai import AIExtractor, _parse_json
from spider.extract.extractor import PageExtractor


class StubModel:
    """Stands in for the Claude client: returns whatever JSON the test sets."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def messages_create(self, **_kwargs):
        self.calls += 1

        class Block:
            type = "text"
            text = json.dumps(self.payload)

        class Message:
            content = [Block()]

        return Message()


def stub_extractor(conn, spec, payload):
    extractor = AIExtractor(conn, spec)
    model = StubModel(payload)

    class Client:
        class messages:
            @staticmethod
            def create(**kwargs):
                return model.messages_create(**kwargs)

    extractor._client = Client()
    return extractor, model


TRUE_QUOTE = "It grows at 3,000 to 4,500 m above sea level."


def payload(value, quote, month_quote="Flowering takes place in July."):
    return {"records": [{"type": "plant", "fields": {
        "scientific_name": {"value": "Saussurea obvallata", "quote":
                            "Saussurea obvallata"},
        "altitude_m": {"value": value, "unit": "m", "quote": quote},
        "flowering_month": {"value": "July", "quote": month_quote}}}],
        "relations": [{"from": "plant:Saussurea obvallata", "name": "grows_in",
                       "to": "region:Chamoli",
                       "quote": "Recorded from Chamoli in Uttarakhand."}]}


def test_the_model_json_becomes_values_with_evidence(project, spec):
    _root, conn = project
    page_id = store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec)
    page = conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()

    extractor, _model = stub_extractor(conn, spec, payload("3000-4500", TRUE_QUOTE))
    values, relations, _aliases = PageExtractor(conn, spec, ai=extractor).run(page)
    by_field = {(c.entity_type, c.field): c for c in values if c.route == "ai"}
    altitude = by_field[("plant", "altitude_m")]
    assert altitude.raw_value == "3000-4500" and altitude.quote == TRUE_QUOTE
    assert any(r.name == "grows_in" and r.to_identity == "Chamoli" for r in relations)


def test_an_invented_value_is_thrown_away(project, spec):
    """The model claims 8,000 m and supplies a quote that is not on the page."""
    _root, conn = project
    page_id = store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec)
    page = conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()

    extractor, _model = stub_extractor(
        conn, spec, payload("8000", "It grows at 8,000 m above sea level."))
    page_extractor = PageExtractor(conn, spec, ai=extractor)
    values, _relations, _aliases = page_extractor.run(page)

    assert not any(c.route == "ai" and c.field == "altitude_m" for c in values)
    assert any("quote not found" in reason for _url, _what, reason
               in page_extractor.rejected)


def test_a_real_quote_that_does_not_contain_the_value_is_thrown_away(project, spec):
    _root, conn = project
    page_id = store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec)
    page = conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()

    extractor, _model = stub_extractor(conn, spec, payload("7777", TRUE_QUOTE))
    page_extractor = PageExtractor(conn, spec, ai=extractor)
    values, _relations, _aliases = page_extractor.run(page)
    assert not any(c.route == "ai" and c.field == "altitude_m" for c in values)
    assert any("does not appear" in reason for _url, _what, reason
               in page_extractor.rejected)


def test_the_answer_is_cached_so_a_second_build_costs_nothing(project, spec):
    _root, conn = project
    page_id = store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec)
    page = conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()

    extractor, model = stub_extractor(conn, spec, payload("3000-4500", TRUE_QUOTE))
    extractor.extract(page)
    extractor.extract(page)
    assert model.calls == 1 and extractor.cache_hits == 1


def test_the_call_cap_is_honoured(project, spec):
    _root, conn = project
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec)
    store_page(conn, "http://b.test/one", sample("plant_tier2.html"), spec)
    pages = conn.execute("SELECT * FROM pages").fetchall()

    extractor, model = stub_extractor(conn, spec, payload("3000-4500", TRUE_QUOTE))
    extractor.max_calls = 1
    for page in pages:
        extractor.extract(page)
    assert model.calls == 1


def test_a_model_value_still_passes_through_standardization_and_sanity(project, spec):
    """The AI route is not a side door: 9,842 ft becomes 3000 m, 99999 m is refused."""
    _root, conn = project
    page_id = store_page(conn, "http://c.test/one", sample("plant_hindi.html"),
                         spec, tier=3)
    page = conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
    feet_quote = "Walkers find it around 9,842 ft."

    extractor, _model = stub_extractor(conn, spec, {"records": [{"type": "plant",
        "fields": {
            "scientific_name": {"value": "Saussurea obvallata",
                                "quote": "Saussurea obvallata"},
            "altitude_m": {"value": "9,842 ft", "unit": "ft", "quote": feet_quote}}}]})
    values, _relations, _aliases = PageExtractor(conn, spec, ai=extractor).run(page)
    raw = next(c for c in values if c.route == "ai" and c.field == "altitude_m")

    from spider.extract import sanity
    from spider.standardize.engine import Standardizer
    standard = Standardizer(conn, spec).standardize("plant", "altitude_m", raw.raw_value)
    assert standard.value_num == 3000 and standard.unit == "m"
    assert sanity.check(spec.entity_field("plant", "altitude_m"), standard)[0]

    impossible = Standardizer(conn, spec).standardize("plant", "altitude_m", "99999 m")
    assert not sanity.check(spec.entity_field("plant", "altitude_m"), impossible)[0]


def test_json_is_read_even_when_the_model_wraps_it_in_a_code_fence():
    fenced = '```json\n{"records": [], "relations": []}\n```'
    assert _parse_json(fenced) == {"records": [], "relations": []}
    chatty = 'Here is the result:\n{"records": []}\nHope that helps.'
    assert _parse_json(chatty) == {"records": []}
    assert _parse_json("I could not find anything.") is None
