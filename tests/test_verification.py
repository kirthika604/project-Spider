"""The verification chain: quote proof, sanity rules, confidence (section 12)."""

import pytest

from spider.assemble.confidence import base_for, inherit, score
from spider.extract import proof, sanity
from spider.spec import FieldSpec
from spider.standardize.engine import StandardValue

PAGE = ("Saussurea obvallata grows at 3,000 to 4,500 m in Uttarakhand "
        "and flowers in July.")


def test_a_quote_that_is_on_the_page_is_kept():
    kept, reason = proof.check("3000-4500", "grows at 3,000 to 4,500 m", PAGE)
    assert kept and reason == "quote verified"


def test_an_invented_quote_is_rejected():
    kept, reason = proof.check("6000", "grows at 6,000 m", PAGE)
    assert not kept and reason == "quote not found on page"


def test_a_value_missing_from_its_own_quote_is_rejected():
    kept, reason = proof.check("9999", "grows at 3,000 to 4,500 m", PAGE)
    assert not kept and "does not appear" in reason


def test_quote_matching_ignores_spacing_and_case():
    assert proof.quote_on_page("GROWS AT 3,000  to 4,500 M", PAGE)


@pytest.mark.parametrize("value,expected", [(3000, True), (99999, False), (-5, False)])
def test_sanity_range(value, expected):
    field = FieldSpec(name="altitude_m", type="range", unit="m", sanity=[0, 9000])
    passes, _reason = sanity.check(field, StandardValue(value="x", value_num=value))
    assert passes is expected


def test_sanity_allowed_list():
    field = FieldSpec(name="habit", type="text", sanity=["herb", "shrub", "tree"])
    assert sanity.check(field, StandardValue(value="herb"))[0]
    assert not sanity.check(field, StandardValue(value="submarine"))[0]


class Candidate:
    def __init__(self, tier, domain):
        self.tier, self.domain = tier, domain


def test_confidence_follows_the_agreement_table():
    assert base_for(0) == 0.95 and base_for(1) == 0.80
    assert base_for(2) == 0.60 and base_for(3) == 0.40
    assert score([Candidate(1, "a.test")]) == 0.80
    assert score([Candidate(1, "a.test"), Candidate(2, "b.test")]) == 0.95
    assert score([Candidate(1, "a.test"), Candidate(2, "b.test"),
                  Candidate(3, "c.test")]) == 0.99            # capped
    assert score([Candidate(1, "a.test")], disagreement=True) == 0.60
    assert score([Candidate(1, "a.test")], origin="inferred") == 0.45  # capped low


def test_two_pages_on_one_domain_are_not_two_sources():
    one_domain = [Candidate(1, "a.test"), Candidate(1, "a.test")]
    assert score(one_domain) == 0.80


def test_a_derived_value_takes_the_lowest_confidence_of_its_inputs():
    assert inherit([0.95, 0.60, 0.80]) == 0.60
    assert inherit([0.95], origin="inferred") == 0.45
