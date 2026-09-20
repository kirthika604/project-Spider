"""spider.yaml validation (`check`) and derived fields (D7)."""

import pytest

from spider.derive.formula import FormulaError, evaluate
from spider.spec import Spec


def problems_of(document) -> list[str]:
    return [f"{p.level}:{p.where}:{p.message}" for p in Spec.from_dict(document).validate()]


def test_a_good_file_has_no_errors(spec):
    assert [p for p in spec.validate() if p.level == "error"] == []


def test_project_mode_refuses_a_level_below_3nf():
    found = problems_of({"entities": {"a": {"identity": ["n"],
                                            "fields": {"n": {"type": "text"}}}},
                         "mode": "project", "storage": {"normal_form": "1NF"}})
    assert any("below 3NF" in p and p.startswith("error") for p in found)


def test_analysis_mode_allows_a_flat_table():
    found = problems_of({"entities": {"a": {"identity": ["n"],
                                            "fields": {"n": {"type": "text"}}}},
                         "mode": "analysis", "storage": {"normal_form": "0NF"}})
    assert not any(p.startswith("error") for p in found)


def test_circular_derivations_are_rejected():
    found = problems_of({
        "entities": {"a": {"identity": ["n"], "fields": {"n": {"type": "text"}}}},
        "derived": {"x": {"on": "a", "formula": "y + 1"},
                    "y": {"on": "a", "formula": "x * 2"}}})
    assert any("circular derivation" in p for p in found)


def test_unknown_entity_in_a_relation_is_an_error():
    found = problems_of({
        "entities": {"a": {"identity": ["n"], "fields": {"n": {"type": "text"}}}},
        "relations": [{"from": "a", "name": "links", "to": "ghost"}]})
    assert any("ghost" in p and p.startswith("error") for p in found)


def test_identity_field_must_exist():
    found = problems_of({"entities": {"a": {"identity": ["missing"],
                                            "fields": {"n": {"type": "text"}}}}})
    assert any("identity field 'missing'" in p for p in found)


def test_bands_need_one_more_label_than_cutoffs():
    found = problems_of({
        "entities": {"a": {"identity": ["n"], "fields": {"n": {"type": "text"}}}},
        "derived": {"z": {"on": "a", "method": "lookup",
                          "bands": {"cutoffs": [1, 2], "labels": ["low", "high"]}}}})
    assert any("labels" in p and p.startswith("error") for p in found)


def test_yaml_reads_on_as_a_key_not_a_boolean(tmp_path):
    path = tmp_path / "spider.yaml"
    path.write_text("""
entities:
  plant:
    identity: [name]
    fields:
      name: {type: text}
      alt: {type: number, unit: m}
derived:
  doubled:
    on: plant
    formula: "alt * 2"
""", encoding="utf-8")
    loaded = Spec.load(path)
    assert loaded.derived["doubled"].on == "plant"
    assert [p for p in loaded.validate() if p.level == "error"] == []


# ------------------------------------------------------------------ formulas
def test_the_allowed_functions_work():
    values = {"altitude_m__min": 3000.0, "altitude_m__max": 4500.0,
              "flowering_month": "July"}
    assert evaluate("band(altitude_m.min, [1500, 3000], ['low','mid','alpine'])",
                    values) == "alpine"
    assert evaluate("season_of(flowering_month, 'india')", values) == "monsoon"
    # with no calendar named, July is not assumed to be a monsoon
    assert evaluate("season_of(flowering_month)", values) == "summer"
    assert evaluate("midpoint(altitude_m.min, altitude_m.max)", values) == 3750
    assert evaluate("convert(altitude_m.min, 'm', 'ft')", values) == 9842.52
    assert evaluate("if(altitude_m.min > 3500, 'rare', 'common')", values) == "common"


def test_a_missing_input_gives_an_empty_result_not_an_error():
    assert evaluate("alt * 2", {"alt": None}) is None


@pytest.mark.parametrize("attack", [
    "__import__('os').system('ls')",
    "open('/etc/passwd').read()",
    "(lambda: 1)()",
    "[x for x in range(3)]",
    "().__class__.__bases__",
])
def test_formulas_cannot_run_arbitrary_code(attack):
    with pytest.raises(FormulaError):
        evaluate(attack, {})


def test_unknown_field_names_are_named_in_the_error():
    with pytest.raises(FormulaError, match="unknown field"):
        evaluate("not_a_field + 1", {})
