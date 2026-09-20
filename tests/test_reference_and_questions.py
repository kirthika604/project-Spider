"""The settings reference, and the clarifying questions with their choices."""

import pytest

from spider import reference
from spider.capture import Question, apply_answers, from_sql, questions_for
from spider.cli import main
from spider.spec import Spec
from spider.store import db as store


# ----------------------------------------------------------- the reference
def test_every_setting_explains_itself_in_plain_words():
    for setting in reference.SETTINGS:
        assert setting.what.endswith("."), f"{setting.key} has no sentence"
        assert len(setting.what) > 25, f"{setting.key} says too little"
        assert setting.kind, f"{setting.key} has no type"


def test_the_words_a_user_would_search_for_find_the_setting():
    for word, expected in [("depth", "sources.depth"),
                           ("seeds", "sources.seeds"),
                           ("delay", "sources.delay_seconds"),
                           ("normal_form", "storage.normal_form"),
                           ("min_confidence", "standardize.min_confidence"),
                           ("sanity", "entities.<name>.fields.<f>.sanity")]:
        found = [s.key for s in reference.find(word)]
        assert expected in found, f"'{word}' did not find {expected}"


def test_a_plain_english_search_still_lands_somewhere():
    assert reference.find("politeness") or reference.find("wait")
    assert any("tier" in s.key or "tier" in s.what.lower()
               for s in reference.find("trust"))


def test_the_reference_covers_what_spider_yaml_actually_reads():
    """A setting the parser understands but the reference never mentions is a
    setting nobody can discover."""
    documented = {s.key for s in reference.SETTINGS}
    leaves = {k.split(".")[-1].rstrip("]").split("[")[0] for k in documented}
    for key in ("seeds", "keywords", "depth", "max_pages", "delay_seconds",
                "follow_other_domains", "trust_tiers", "identity", "fields",
                "type", "unit", "required", "multiple", "extract", "sanity",
                "vocabulary", "default", "on", "method", "formula", "bands",
                "if_missing", "explain", "review", "level", "on_conflict",
                "min_confidence", "normal_form", "keep_provenance", "formats",
                "targets", "shape", "provenance", "nesting", "naming",
                "sort_by", "split"):
        assert key in leaves, f"spider.yaml reads `{key}` but nothing explains it"


def test_the_defaults_in_the_reference_match_the_code():
    spec = Spec.from_dict({"entities": {"a": {"identity": ["n"],
                                              "fields": {"n": {"type": "text"}}}}})
    pairs = [("mode", spec.mode),
             ("storage.normal_form", spec.storage.normal_form),
             ("standardize.level", spec.standardize.level),
             ("standardize.on_conflict", spec.standardize.on_conflict),
             ("standardize.min_confidence", str(spec.standardize.min_confidence)),
             ("derive_policy", spec.derive_policy),
             ("sources.mode", spec.sources.mode),
             ("sources.depth", str(spec.sources.depth)),
             ("sources.delay_seconds", str(spec.sources.delay_seconds))]
    for key, in_code in pairs:
        stated = reference.BY_KEY[key].default
        assert stated == in_code, f"{key}: reference says {stated}, code uses {in_code}"


def test_a_choice_setting_lists_the_choices_the_validator_accepts():
    from spider.spec import (CONFLICT_RULES, DERIVE_POLICIES, NORMAL_FORMS,
                             SOURCE_MODES, STANDARD_LEVELS)
    assert set(reference.BY_KEY["standardize.on_conflict"].choices) == CONFLICT_RULES
    assert set(reference.BY_KEY["standardize.level"].choices) == STANDARD_LEVELS
    assert set(reference.BY_KEY["derive_policy"].choices) == DERIVE_POLICIES
    assert set(reference.BY_KEY["sources.mode"].choices) == SOURCE_MODES
    assert set(reference.BY_KEY["storage.normal_form"].choices) == set(NORMAL_FORMS)


def test_settings_runs_without_a_project(tmp_path, capsys):
    assert main(["--project", str(tmp_path), "settings", "depth"]) == 0
    output = capsys.readouterr().out
    assert "links away from a seed" in output and "default: 2" in output


def test_settings_lists_everything_and_says_nothing_silly(capsys):
    assert main(["settings"]) == 0
    output = capsys.readouterr().out
    assert "WHERE TO LOOK" in output and "sources.depth" in output


def test_an_unknown_setting_points_somewhere_useful(capsys):
    assert main(["settings", "zzzznotasetting"]) == 1
    assert "spider settings" in capsys.readouterr().out


def test_the_markdown_reference_is_generated_from_the_same_list(capsys):
    assert main(["settings", "--format", "markdown"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("# Every setting in `spider.yaml`")
    for setting in reference.SETTINGS:
        assert f"`{setting.key}`" in text


# --------------------------------------------------- the clarifying questions
def sample_document():
    document, _notes = from_sql(
        "CREATE TABLE plant (name TEXT PRIMARY KEY, altitude INTEGER);"
        "CREATE TABLE region (name TEXT PRIMARY KEY);")
    return document


def test_there_are_at_most_five_questions():
    assert len(questions_for(sample_document())) <= 5


def test_each_question_carries_its_choices_and_a_suggestion():
    for question in questions_for(sample_document()):
        # a question may add a sentence of explanation after the "?"
        assert "?" in question.ask
        if not question.free_text:
            assert question.options, f"{question.key} offers no choices"
            assert question.suggested in [v for v, _m in question.options]
            for _value, meaning in question.options:
                assert meaning.strip(), "a choice should say what it means"


def test_the_choices_are_the_values_spider_yaml_accepts():
    questions = {q.key: q for q in questions_for(sample_document())}
    from spider.spec import CONFLICT_RULES
    offered = {v for v, _m in questions["standardize.on_conflict"].options}
    assert offered <= CONFLICT_RULES
    modes = {v for v, _m in questions["mode"].options}
    assert modes == {"project", "analysis"}


def test_it_asks_about_a_measurement_with_no_unit():
    questions = [q for q in questions_for(sample_document()) if q.key.endswith(".unit")]
    assert questions, "a number column with no unit is exactly what to ask about"
    assert "altitude" in questions[0].ask


def test_answers_land_where_they_belong():
    document = sample_document()
    document = apply_answers(document, {
        "mode": "analysis",
        "standardize.on_conflict": "majority",
        "sources.seeds": "https://a.test https://b.test",
        "entities.plant.fields.altitude.unit": "ft",
    })
    assert document["mode"] == "analysis"
    assert document["storage"]["normal_form"] == "0NF"   # analysis allows flat
    assert document["standardize"]["on_conflict"] == "majority"
    assert document["sources"]["seeds"] == ["https://a.test", "https://b.test"]
    assert document["entities"]["plant"]["fields"]["altitude"]["unit"] == "ft"


def test_an_unanswered_question_takes_its_suggestion(monkeypatch, tmp_path, capsys):
    store.init_project(tmp_path)
    example = tmp_path / "want.csv"
    example.write_text("Species,Alt\nSaussurea obvallata,3000-4500\n", encoding="utf-8")

    import spider.cli as cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda *_a: "")      # press Enter throughout

    assert main(["--project", str(tmp_path), "describe", "--like", str(example),
                 "--entity", "plant", "--ask", "--no-ai"]) == 0
    written = Spec.load(tmp_path / "spider.yaml")
    assert written.mode == "project"
    assert written.standardize.on_conflict == "keep_all_and_flag"
    assert [p for p in written.validate() if p.level == "error"] == []


def test_choosing_by_number_picks_that_option(monkeypatch, tmp_path):
    store.init_project(tmp_path)
    example = tmp_path / "want.csv"
    example.write_text("Species,Alt\nSaussurea obvallata,3000-4500\n", encoding="utf-8")

    import spider.cli as cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    replies = iter(["1", "2", "2", "2", "https://trusted.test"])
    monkeypatch.setattr("builtins.input", lambda *_a: next(replies, ""))

    assert main(["--project", str(tmp_path), "describe", "--like", str(example),
                 "--entity", "plant", "--ask", "--no-ai"]) == 0
    written = Spec.load(tmp_path / "spider.yaml")
    assert written.mode == "analysis"                       # choice 2
    assert written.standardize.on_conflict == "majority"    # choice 2
    assert "https://trusted.test" in written.sources.seeds
    assert written.entities["plant"].fields["alt"].unit == "ft"   # choice 2


def test_a_number_out_of_range_falls_back_to_the_suggestion(monkeypatch, tmp_path,
                                                            capsys):
    store.init_project(tmp_path)
    example = tmp_path / "want.csv"
    example.write_text("Species,Alt\nx,1\n", encoding="utf-8")
    import spider.cli as cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda *_a: "99")
    assert main(["--project", str(tmp_path), "describe", "--like", str(example),
                 "--ask", "--no-ai"]) == 0
    assert "no choice 99" in capsys.readouterr().out
