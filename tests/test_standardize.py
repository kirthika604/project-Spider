"""Units, dates, names, vocabularies and conflict rules (D9)."""

import pytest

from spider.ref import tables as ref
from spider.standardize import conflicts, dates, names, units
from spider.standardize.engine import StandardValue, Standardizer


@pytest.mark.parametrize("written,expected", [
    ("3 km", 3000), ("3,000 m", 3000), ("9,842 ft", 3000), ("3000", 3000)])
def test_every_way_of_writing_3000_m_becomes_3000(written, expected):
    low, _high, unit = units.parse_value(written, "m")
    assert low == expected and unit == "m"


def test_a_range_splits_into_minimum_and_maximum():
    assert units.parse_value("3,000 to 4,500 m", "m") == (3000.0, 4500.0, "m")
    assert units.parse_value("2800-4000 metres", "m") == (2800.0, 4000.0, "m")


def test_units_of_different_quantities_do_not_convert():
    with pytest.raises(units.UnitError):
        units.convert(5, "kg", "m")


@pytest.mark.parametrize("written", ["20 Sep 26", "20/09/2026", "2026-09-20",
                                     "September 20, 2026", "20th September 2026"])
def test_dates_become_iso_8601(written):
    assert dates.to_iso(written) == "2026-09-20"


def test_months_and_seasons():
    assert dates.to_month("July to September") == 7
    assert dates.to_month("08") == 8
    assert dates.season_of("July") == "monsoon"
    assert dates.season_of("March") == "spring"


def test_name_cleaning_and_matching():
    assert names.scientific("saussurea OBVALLATA") == "Saussurea obvallata"
    assert names.matches("Garwhal", "Garhwal")
    assert not names.matches("Chamoli", "Pithoragarh")
    assert names.script_of("ब्रह्मकमल") == "Devanagari"
    assert names.case_style("altitude_m", "camelCase") == "altitudeM"


def test_places_and_vocabularies_come_from_the_reference_tables(project):
    _root, conn = project
    assert ref.place(conn, "Garwhal")["name"] == "Pauri Garhwal"
    assert ref.vocab_term(conn, "uses", "ayurvedic use") == "medicinal"
    assert ref.vocab_term(conn, "uses", "spaceship") is None


def test_standardizer_converts_and_reports(project, spec):
    _root, conn = project
    standardizer = Standardizer(conn, spec)
    result = standardizer.standardize("plant", "altitude_m", "9,842 ft")
    assert result.value_num == 3000 and result.unit == "m"
    assert result.raw == "9,842 ft"                     # the original is kept
    assert "units converted" in standardizer.report


def test_strict_level_rejects_a_value_outside_its_vocabulary(project, spec):
    _root, conn = project
    standardizer = Standardizer(conn, spec)
    assert standardizer.standardize("use", "name", "ayurvedic use").value == "medicinal"
    rejected = standardizer.standardize("use", "name", "spaceship")
    assert rejected.rejected and "strict" in rejected.reason


def test_currency_amounts_are_converted_and_keep_their_raw_text(project, spec):
    _root, conn = project
    spec.entities["plant"].fields["price"] = type(spec.entities["plant"].fields[
        "altitude_m"])(name="price", type="number", unit="INR")
    standardizer = Standardizer(conn, spec)
    result = standardizer.standardize("plant", "price", "$10")
    assert result.value_num == 830 and result.unit == "INR"
    assert result.raw == "$10"
    assert "currencies converted" in standardizer.report


class Candidate:
    def __init__(self, value, num, tier, domain, maximum=None, fetched="2026-01-01"):
        self.value, self.value_num, self.value_max = value, num, maximum
        self.tier, self.domain, self.fetched_at = tier, domain, fetched


def test_values_within_tolerance_count_as_agreement():
    groups = conflicts.group([Candidate("3000", 3000, 1, "a.test"),
                              Candidate("3000", 3000, 2, "b.test")])
    assert len(groups) == 1


def test_a_single_reading_inside_a_range_confirms_it():
    assert conflicts.same_value(Candidate("3000-4500", 3000, 1, "a", maximum=4500),
                                Candidate("3000", 3000, 3, "c"))
    assert not conflicts.same_value(Candidate("3000-4500", 3000, 1, "a", maximum=4500),
                                    Candidate("1200", 1200, 3, "c"))


def test_conflict_rules():
    high = Candidate("3000-4500", 3000, 1, "a.test", maximum=4500)
    low = Candidate("1200-1400", 1200, 3, "c.test", maximum=1400, fetched="2026-06-01")
    candidates = [high, low]

    keep_all = conflicts.resolve(candidates, "keep_all_and_flag")
    assert len(keep_all.winners) == 2 and len(keep_all.flagged) == 2

    trusted = conflicts.resolve(candidates, "trusted_first", ["a.test", "c.test"])
    assert trusted.winners == [high]

    newest = conflicts.resolve(candidates, "newest")
    assert newest.winners == [low]

    majority = conflicts.resolve(
        [high, low, Candidate("3000-4500", 3000, 2, "b.test", maximum=4500)],
        "majority")
    assert len(majority.winners) == 2 and majority.winners[0] is high


def test_an_author_citation_is_not_part_of_the_name():
    """Two sources differ on printing the author; it must not split a record."""
    assert names.scientific("Saussurea obvallata Nakai") == "Saussurea obvallata"
    assert names.scientific("Picrorhiza kurroa Royle ex Benth.") == "Picrorhiza kurroa"
    assert names.scientific("Nardostachys jatamansi (D.Don) DC.") == "Nardostachys jatamansi"
    # an infraspecific rank is part of the name and stays
    assert names.scientific("Rhododendron arboreum var. nivea") == \
        "Rhododendron arboreum var. nivea"


def test_a_scientific_field_is_recognised_whatever_it_is_called():
    assert names.is_scientific_field("scientific_name")
    assert names.is_scientific_field("species")
    assert names.is_scientific_field("Taxon Name")
    assert not names.is_scientific_field("common_name")
    assert not names.is_scientific_field("region")
