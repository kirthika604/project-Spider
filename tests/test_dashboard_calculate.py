"""The dashboard's Calculate screen, driven headlessly with Streamlit's own harness."""

import sys

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest                      # noqa: E402

from conftest import sample, store_page                       # noqa: E402
from spider.assemble.build import build                       # noqa: E402
from spider.spec import Spec                                  # noqa: E402

APP = "spider/dashboard.py"

YAML = """# my notes: this comment must survive
project: demo
sources: {keywords: [plant], trust_tiers: {a.test: 1}}
entities:
  plant:
    identity: [scientific_name]
    fields:
      scientific_name: {type: text, required: true, extract: [".sci"]}
      altitude_m: {type: range, unit: m, extract: [".altitude"]}
storage: {normal_form: 3NF}
"""


@pytest.fixture
def screen(project, monkeypatch):
    root, conn = project
    (root / "spider.yaml").write_text(YAML, encoding="utf-8")
    spec = Spec.load(root / "spider.yaml")
    store_page(conn, "http://a.test/1", sample("plant_tier1.html"), spec, tier=1)
    store_page(conn, "http://a.test/2", sample("plant_tier2.html"), spec, tier=1)
    build(conn, spec, use_ai=False)
    conn.close()

    monkeypatch.setattr(sys, "argv", ["dashboard.py", "--project", str(root)])
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Calculate").run()
    assert not at.exception, [e.value for e in at.exception]
    return at, root


def click(at, label):
    next(b for b in at.button if b.label == label).click().run()


def test_the_screen_lists_the_columns_you_can_use(screen):
    at, _root = screen
    assert at.header[0].value == "Calculate"
    assert "altitude_m" in " ".join(m.value for m in at.markdown)


def test_a_misspelt_column_is_named_with_a_suggestion(screen):
    at, _root = screen
    at.text_area(key="formula").set_value("altitude_mm * 2").run()
    click(at, "Try it")
    assert any("altitude_mm" in e.value and "did you mean 'altitude_m'" in e.value
               for e in at.error)


def test_a_good_formula_shows_real_results(screen):
    at, _root = screen
    at.text_area(key="formula").set_value("altitude_m.max - altitude_m.min").run()
    click(at, "Try it")
    assert not at.exception and not at.error
    assert any("Tried on" in s.value and "filled" in s.value for s in at.success)
    assert len(at.dataframe) >= 1


def test_a_formula_that_is_not_allowed_is_refused_in_plain_words(screen):
    at, _root = screen
    at.text_area(key="formula").set_value("__import__('os').getcwd()").run()
    click(at, "Try it")
    assert any("no function called" in e.value or "not allowed" in e.value
               for e in at.error)


def test_adding_a_column_edits_the_file_and_keeps_comments(screen):
    at, root = screen
    at.text_area(key="formula").set_value("altitude_m.max - altitude_m.min").run()
    at.text_input[0].set_value("altitude_span").run()
    click(at, "Add to spider.yaml")
    assert any("Added 'altitude_span'" in s.value for s in at.success)
    written = (root / "spider.yaml").read_text(encoding="utf-8")
    assert "# my notes: this comment must survive" in written
    assert "altitude_span:" in written
    assert Spec.load(root / "spider.yaml").derived["altitude_span"].on == "plant"


def test_adding_a_column_that_already_exists_is_refused(screen):
    at, root = screen
    before = (root / "spider.yaml").read_text(encoding="utf-8")
    at.text_area(key="formula").set_value("altitude_m.min * 2").run()
    at.text_input[0].set_value("altitude_m").run()
    click(at, "Add to spider.yaml")
    assert any("already exists" in e.value for e in at.error)
    assert (root / "spider.yaml").read_text(encoding="utf-8") == before


def test_a_recipe_can_be_used_as_a_starting_formula(screen):
    at, _root = screen
    click(at, "Use")
    assert at.text_area(key="formula").value.strip() != ""
