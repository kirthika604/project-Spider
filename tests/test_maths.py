"""Quantitative data: scientific notation, maths functions, chained derivations.

The numbers here are the published ones for the planets, so a formula that is
subtly wrong fails against physics rather than against itself.
"""

import math

import pytest
from conftest import store_page

from spider.assemble.build import build
from spider.derive.formula import FormulaError, evaluate
from spider.spec import DerivedSpec, FieldSpec, Spec
from spider.standardize.units import format_number, parse_number, parse_value

G = 6.674e-11
EARTH = {"mass": 5.972e24, "radius": 6.371e6}


# ------------------------------------------------------- scientific notation
@pytest.mark.parametrize("written,expected", [
    ("5.972e24", 5.972e24), ("6.371E6", 6.371e6), ("1.2e-5", 1.2e-5),
    ("-3.4e2", -340.0), ("1.898e27 kg", 1.898e27), ("3,000", 3000.0),
])
def test_scientific_notation_is_read_as_written(written, expected):
    """Reading 5.972e24 as 5.972 would be wrong by a factor of 10^24."""
    assert parse_number(written) == expected


def test_a_range_in_scientific_notation_keeps_both_ends_and_its_unit():
    low, high, unit = parse_value("2.3e5 to 4.1e5 m", "m")
    assert (low, high, unit) == (2.3e5, 4.1e5, "m")


def test_the_word_between_two_numbers_is_not_mistaken_for_a_unit():
    assert parse_value("3,000 to 4,500 m")[2] == "m"     # not "to"


def test_yaml_scientific_notation_is_read_as_a_number():
    """YAML 1.1 hands `1.0e20` over as text because it wants `1.0e+20`.

    Left as text a sanity range becomes a list of allowed values, and every
    mass is rejected.
    """
    spec = Spec.from_dict({"entities": {"p": {"identity": ["n"], "fields": {
        "n": {"type": "text"},
        "mass": {"type": "number", "sanity": ["1.0e20", "1.0e30"]}}}},
        "derived": {"size": {"on": "p", "method": "lookup", "inputs": ["mass"],
                             "bands": {"cutoffs": ["3.0e6", "1.0e7"],
                                       "labels": ["a", "b", "c"]}}}})
    assert spec.entities["p"].fields["mass"].sanity == [1e20, 1e30]
    assert spec.derived["size"].bands["cutoffs"] == [3e6, 1e7]
    assert [p for p in spec.validate() if p.level == "error"] == []


def test_a_mass_inside_its_range_passes_sanity():
    from spider.extract import sanity
    from spider.standardize.engine import StandardValue
    field = FieldSpec(name="mass_kg", type="number", sanity=[1e20, 1e30])
    assert sanity.check(field, StandardValue(value="5.972e24", value_num=5.972e24))[0]
    assert not sanity.check(field, StandardValue(value="5", value_num=5.0))[0]


def test_a_big_number_keeps_its_exponent_instead_of_inventing_digits():
    assert format_number(5.972e24) == "5.972e+24"
    assert format_number(1.2e-7) == "1.2e-07"
    assert format_number(6371000.0) == "6371000"
    assert format_number(11.19) == "11.19"
    assert "0000000" not in format_number(5.972e24)


# ------------------------------------------------------------ the maths
def test_the_maths_functions_do_what_they_say():
    assert evaluate("sqrt(16)", {}) == 4
    assert evaluate("log10(1000)", {}) == 3
    assert round(evaluate("log(e)", {}), 9) == 1
    assert evaluate("floor(3.7)", {}) == 3 and evaluate("ceil(3.2)", {}) == 4
    assert evaluate("pow(2, 10)", {}) == 1024
    assert evaluate("clamp(15, 0, 10)", {}) == 10
    assert evaluate("sign(-4)", {}) == -1
    assert round(evaluate("pi", {}), 6) == 3.141593


def test_maths_on_impossible_input_gives_nothing_rather_than_raising():
    assert evaluate("sqrt(-1)", {}) is None
    assert evaluate("log(0)", {}) is None
    assert evaluate("sqrt(mass)", {"mass": None}) is None


def test_the_physics_a_reader_could_check_by_hand():
    values = {"mass_kg": EARTH["mass"], "radius_m": EARTH["radius"]}
    volume = evaluate("(4 / 3) * pi * radius_m ** 3", values)
    assert volume == pytest.approx(4 / 3 * math.pi * EARTH["radius"] ** 3)

    density = evaluate("mass_kg / ((4 / 3) * pi * radius_m ** 3)", values)
    assert density == pytest.approx(5514, rel=0.01)          # published 5514

    gravity = evaluate("6.674e-11 * mass_kg / radius_m ** 2", values)
    assert gravity == pytest.approx(9.81, rel=0.01)          # published 9.81

    escape = evaluate("sqrt(2 * 6.674e-11 * mass_kg / radius_m) / 1000", values)
    assert escape == pytest.approx(11.19, rel=0.01)          # published 11.19


def test_maths_still_cannot_reach_outside_the_formula():
    for attack in ["__import__('math').pi", "sqrt.__class__", "pi.__class__"]:
        with pytest.raises(FormulaError):
            evaluate(attack, {})


# --------------------------------------------------- chained derivations
PLANET_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{name}</title></head><body><h1>{name}</h1>
<p class="body">{name}</p>
<p class="mass">Mass: {mass} kg</p>
<p class="radius">Mean radius: {radius} m</p>
<p>A planet of the solar system.</p></body></html>"""


@pytest.fixture
def planets(project):
    root, conn = project
    spec = Spec.from_dict({
        "project": "planets",
        "sources": {"keywords": ["planet"], "trust_tiers": {"a.test": 1}},
        "entities": {"planet": {"identity": ["body"], "fields": {
            "body": {"type": "text", "required": True, "extract": [".body"]},
            "mass_kg": {"type": "number", "unit": "kg", "sanity": ["1.0e20", "1.0e30"],
                        "extract": [r"regex:Mass:\s*([\d.,]+(?:[eE][-+]?\d+)?)"]},
            "radius_m": {"type": "number", "unit": "m", "sanity": ["1.0e5", "1.0e9"],
                         "extract": [r"regex:Mean radius:\s*([\d.,]+(?:[eE][-+]?\d+)?)"]},
        }}},
        "derived": {
            "volume_m3": {"on": "planet", "inputs": ["radius_m"],
                          "formula": "(4 / 3) * pi * radius_m ** 3"},
            # depends on volume_m3, so the order matters
            "density_kg_m3": {"on": "planet", "inputs": ["mass_kg", "volume_m3"],
                              "formula": "mass_kg / volume_m3", "round": 0},
            "escape_velocity_kms": {
                "on": "planet", "inputs": ["mass_kg", "radius_m"],
                "formula": "sqrt(2 * 6.674e-11 * mass_kg / radius_m) / 1000",
                "round": 2},
        },
        "storage": {"normal_form": "3NF"},
    })
    spec.path = root / "spider.yaml"
    for name, mass, radius in [("Earth", "5.972e24", "6.371e6"),
                               ("Mars", "6.417e23", "3.3895e6"),
                               ("Jupiter", "1.898e27", "6.9911e7")]:
        store_page(conn, f"http://a.test/{name}",
                   PLANET_PAGE.format(name=name, mass=mass, radius=radius),
                   spec, tier=1)
    build(conn, spec, use_ai=False)
    return conn, spec


def value_of(conn, planet, field):
    row = conn.execute(
        "SELECT value, value_num, origin FROM attributes a JOIN entities e "
        "ON e.id=a.entity_id WHERE e.canonical_name=? AND a.name=? "
        "AND a.status='accepted'", (planet, field)).fetchone()
    return row


def test_a_mass_in_scientific_notation_survives_the_whole_chain(planets):
    conn, _spec = planets
    row = value_of(conn, "Earth", "mass_kg")
    assert row["value_num"] == pytest.approx(5.972e24)
    assert row["value"] == "5.972e+24", "it should not print 24 digits of noise"


def test_a_derivation_that_needs_another_derivation_is_calculated_after_it(planets):
    conn, _spec = planets
    volume = value_of(conn, "Earth", "volume_m3")
    density = value_of(conn, "Earth", "density_kg_m3")
    assert volume is not None, "volume must exist for density to use it"
    assert density["value_num"] == pytest.approx(5514, rel=0.01)


@pytest.mark.parametrize("planet,published", [
    ("Earth", 5514), ("Mars", 3933), ("Jupiter", 1326)])
def test_the_computed_density_matches_the_published_figure(planets, planet, published):
    conn, _spec = planets
    assert value_of(conn, planet, "density_kg_m3")["value_num"] == pytest.approx(
        published, rel=0.01)


@pytest.mark.parametrize("planet,published", [
    ("Earth", 11.19), ("Mars", 5.03), ("Jupiter", 60.2)])
def test_the_computed_escape_velocity_matches_physics(planets, planet, published):
    conn, _spec = planets
    assert value_of(conn, planet, "escape_velocity_kms")["value_num"] == pytest.approx(
        published, rel=0.02)


def test_a_derived_number_is_labelled_and_traceable(planets):
    conn, spec = planets
    from spider.report import explain
    chain = explain(conn, spec, "Earth", "density_kg_m3")
    value = chain["values"][0]
    assert value["origin"] == "derived"
    inputs = {i["name"] for i in value["inputs"]}
    assert {"mass_kg", "volume_m3"} <= inputs, "its lineage names both inputs"


def test_the_gold_set_checks_derived_numbers_against_published_values(planets, tmp_path):
    conn, spec = planets
    gold = tmp_path / "gold.csv"
    gold.write_text("type,entity,field,value\n"
                    "planet,Earth,density_kg_m3,5514\n"
                    "planet,Earth,escape_velocity_kms,11.19\n"
                    "planet,Mars,density_kg_m3,3933\n"
                    "planet,Jupiter,escape_velocity_kms,59.5\n", encoding="utf-8")
    from spider.report import check_gold
    result = check_gold(conn, spec, gold)
    assert result["found"] == 4
    assert result["correct"] == 4, [d for d in result["details"]
                                    if d["verdict"] != "correct"]


# ------------------------------------------------- statistics across a column
@pytest.fixture
def stats_project(planets):
    """The planets, plus derivations that look down a whole column."""
    conn, spec = planets
    from spider.derive.engine import DeriveEngine
    for name, formula in [
        ("mass_zscore", "zscore(mass_kg over planet)"),
        ("density_rank", "rank(density_kg_m3 over planet)"),
        ("mass_share", "share(mass_kg over planet)"),
        ("median_density", "median(density_kg_m3 over planet)"),
        ("mean_density", "mean(density_kg_m3 over planet)"),
        ("density_spread", "spread(density_kg_m3 over planet)"),
        ("radius_p90", "percentile(radius_m over planet, 90)"),
        ("how_many", "records(mass_kg over planet)"),
        ("size_position", "normalize(radius_m over planet)"),
        ("mass_radius_link",
         "correlation(mass_kg over planet, radius_m over planet)"),
    ]:
        block = {"on": "planet", "method": "formula", "formula": formula}
        spec.raw.setdefault("derived", {})[name] = block
        spec.derived[name] = DerivedSpec.parse(name, block)
    DeriveEngine(conn, spec).run()
    return conn, spec


def stat(conn, planet, field):
    row = conn.execute(
        "SELECT value_num, value FROM attributes a JOIN entities e "
        "ON e.id = a.entity_id WHERE e.canonical_name = ? AND a.name = ?",
        (planet, field)).fetchone()
    return row["value_num"] if row else None


def test_a_column_statistic_is_the_same_on_every_row(stats_project):
    conn, _spec = stats_project
    medians = {stat(conn, p, "median_density") for p in ("Earth", "Mars", "Jupiter")}
    assert len(medians) == 1, "the median of a column does not vary by row"
    assert stat(conn, "Earth", "how_many") == 3


def test_the_statistics_agree_with_working_them_out_by_hand(stats_project):
    import statistics as stats_module
    conn, _spec = stats_project
    densities = [stat(conn, p, "density_kg_m3") for p in ("Earth", "Mars", "Jupiter")]
    assert stat(conn, "Earth", "mean_density") == pytest.approx(
        stats_module.fmean(densities), rel=1e-3)
    assert stat(conn, "Earth", "median_density") == pytest.approx(
        stats_module.median(densities), rel=1e-3)
    assert stat(conn, "Earth", "density_spread") == pytest.approx(
        max(densities) - min(densities), rel=1e-3)


def test_a_z_score_places_this_record_against_the_rest(stats_project):
    conn, _spec = stats_project
    # Jupiter is far heavier than the other two, so it sits well above the mean
    assert stat(conn, "Jupiter", "mass_zscore") > 1
    assert stat(conn, "Earth", "mass_zscore") < 0
    shares = [stat(conn, p, "mass_share") for p in ("Earth", "Mars", "Jupiter")]
    assert sum(shares) == pytest.approx(1.0, abs=1e-4)


def test_rank_and_position_read_the_way_they_are_described(stats_project):
    conn, _spec = stats_project
    assert stat(conn, "Earth", "density_rank") == 1        # the densest of the three
    assert stat(conn, "Jupiter", "size_position") == 1.0   # the largest radius
    assert stat(conn, "Mars", "size_position") == 0.0      # the smallest


def test_a_percentile_falls_inside_the_column(stats_project):
    conn, _spec = stats_project
    radii = [stat(conn, p, "radius_m") for p in ("Earth", "Mars", "Jupiter")]
    p90 = stat(conn, "Earth", "radius_p90")
    assert min(radii) <= p90 <= max(radii)


def test_correlation_comes_out_between_minus_one_and_one(stats_project):
    conn, _spec = stats_project
    link = stat(conn, "Earth", "mass_radius_link")
    assert link is not None and -1.0 <= link <= 1.0


def test_a_statistic_over_an_empty_column_is_empty_not_an_error(planets):
    conn, spec = planets
    from spider.derive.engine import DeriveEngine
    block = {"on": "planet", "method": "formula",
             "formula": "mean(nothing_here over planet)"}
    spec.raw.setdefault("derived", {})["empty_stat"] = block
    spec.derived["empty_stat"] = DerivedSpec.parse("empty_stat", block)
    report = DeriveEngine(conn, spec).run(["empty_stat"])
    assert report.errors == []
    assert conn.execute(
        "SELECT COUNT(*) c FROM attributes WHERE name='empty_stat'").fetchone()["c"] == 0


def test_the_distance_between_two_points_is_the_real_distance():
    # Chennai Central to Bengaluru City, published as about 290 km
    assert evaluate("distance_km(13.0827, 80.2707, 12.9716, 77.5946)", {}) == \
        pytest.approx(290, rel=0.02)
    assert evaluate("distance_km(13.0827, 80.2707, 13.0827, 80.2707)", {}) == 0
    assert evaluate("distance_km(lat, lon, 0, 0)", {"lat": None, "lon": 1}) is None
