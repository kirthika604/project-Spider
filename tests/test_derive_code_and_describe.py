"""`method: code` and `describe:` - the two settings in the D7 table that go
beyond a written formula."""

import pytest
from conftest import sample, store_page

from spider.assemble.build import build
from spider.derive.code import CodeError, forget, run as run_code
from spider.derive.engine import DeriveEngine
from spider.spec import DerivedSpec, Spec


@pytest.fixture(autouse=True)
def fresh_imports():
    forget()
    yield
    forget()


def add_derivation(spec, name, block):
    spec.raw.setdefault("derived", {})[name] = block
    spec.derived[name] = DerivedSpec.parse(name, block)
    return spec.derived[name]


# ------------------------------------------------------------- method: code
def test_a_python_file_can_calculate_a_field(project, spec, tmp_path):
    root, conn = project
    (root / "rarity.py").write_text(
        "def compute(values):\n"
        "    low = values.get('altitude_m__min') or 0\n"
        "    return 'rare' if low >= 3000 else 'common'\n", encoding="utf-8")
    spec.path = root / "spider.yaml"
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)

    add_derivation(spec, "rarity", {"on": "plant", "method": "code",
                                    "code": "rarity.py", "inputs": ["altitude_m.min"],
                                    "explain": "rare above 3000 m"})
    report = DeriveEngine(conn, spec, root=root).run(["rarity"])
    assert report.written == 1 and not report.errors
    row = conn.execute(
        "SELECT value, origin, confidence FROM attributes WHERE name='rarity'").fetchone()
    assert row["value"] == "rare" and row["origin"] == "derived"
    assert row["confidence"] > 0            # inherits from its inputs


def test_the_function_name_can_be_chosen(project, spec):
    root, _conn = project
    (root / "calc.py").write_text("def my_rule(values):\n    return 42\n",
                                  encoding="utf-8")
    assert run_code(root, "calc.py", "my_rule", {}) == 42


def test_a_missing_function_says_what_to_write(project):
    root, _conn = project
    (root / "calc.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(CodeError, match="def compute"):
        run_code(root, "calc.py", "compute", {})


def test_code_outside_the_project_is_refused(project, tmp_path):
    root, _conn = project
    outside = tmp_path.parent / "elsewhere.py"
    outside.write_text("def compute(values):\n    return 1\n", encoding="utf-8")
    with pytest.raises(CodeError, match="outside the project"):
        run_code(root, str(outside), "compute", {})


def test_a_file_that_raises_is_reported_against_that_record_only(project, spec):
    root, conn = project
    (root / "boom.py").write_text(
        "def compute(values):\n    raise ValueError('bad input')\n", encoding="utf-8")
    spec.path = root / "spider.yaml"
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)

    add_derivation(spec, "boom", {"on": "plant", "method": "code", "code": "boom.py"})
    report = DeriveEngine(conn, spec, root=root).run(["boom"])
    assert report.written == 0
    assert any("bad input" in e for e in report.errors)
    # the rest of the dataset is untouched
    assert conn.execute(
        "SELECT COUNT(*) c FROM attributes WHERE status='accepted'").fetchone()["c"] > 0


def test_check_warns_that_code_is_not_sandboxed(project, spec):
    root, _conn = project
    (root / "rarity.py").write_text("def compute(values):\n    return 1\n",
                                    encoding="utf-8")
    spec.path = root / "spider.yaml"
    add_derivation(spec, "rarity", {"on": "plant", "method": "code",
                                    "code": "rarity.py"})
    warnings = [p for p in spec.validate() if p.level == "warning"]
    assert any("runs it as written" in w.message for w in warnings)


def test_check_rejects_a_code_file_that_is_not_there(project, spec):
    root, _conn = project
    spec.path = root / "spider.yaml"
    add_derivation(spec, "rarity", {"on": "plant", "method": "code",
                                    "code": "missing.py"})
    errors = [p for p in spec.validate() if p.level == "error"]
    assert any("code file not found" in e.message for e in errors)


# --------------------------------------------------------------- describe:
def test_a_described_derivation_is_drafted_and_waits_for_approval(
        project, spec, monkeypatch):
    root, conn = project
    spec.path = root / "spider.yaml"
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)

    import spider.extract.ai as ai_module
    from spider.derive import suggest as suggest_module
    monkeypatch.setattr(ai_module, "ask_json",
                        lambda *a, **k: {"formula": "altitude_m.max - altitude_m.min",
                                         "inputs": ["altitude_m"],
                                         "explain": "how wide its altitude band is"})

    add_derivation(spec, "altitude_span", {
        "on": "plant", "describe": "how wide the altitude band is",
        "review": "required"})
    report = DeriveEngine(conn, spec, root=root).run(["altitude_span"])

    # nothing was written into the dataset
    assert report.written == 0
    assert conn.execute(
        "SELECT COUNT(*) c FROM attributes WHERE name='altitude_span'").fetchone()["c"] == 0

    # it is waiting as a suggestion, marked as coming from the model
    pending = suggest_module.pending(conn)
    assert [p["name"] for p in pending] == ["altitude_span"]
    assert pending[0]["suggested_by"] == "ai"
    assert pending[0]["formula"] == "altitude_m.max - altitude_m.min"

    # approving it writes `review: required` into the project file
    block = suggest_module.approve(conn, spec, "altitude_span")
    assert block["review"] == "required"
    written = DeriveEngine(conn, spec, root=root).run(["altitude_span"])
    assert written.written == 1 and written.queued_for_review == 1
    row = conn.execute(
        "SELECT value, status FROM attributes WHERE name='altitude_span'").fetchone()
    assert float(row["value"]) == 1500.0 and row["status"] == "review"


def test_a_drafted_formula_that_names_unknown_fields_is_thrown_away(
        project, spec, monkeypatch):
    root, conn = project
    spec.path = root / "spider.yaml"
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)

    import spider.extract.ai as ai_module
    monkeypatch.setattr(ai_module, "ask_json",
                        lambda *a, **k: {"formula": "rainfall_mm * 2",
                                         "inputs": ["rainfall_mm"]})
    from spider.derive.suggest import draft_from_description
    der = add_derivation(spec, "wetness", {"on": "plant", "describe": "how wet it is"})
    assert draft_from_description(conn, spec, der) is None


def test_without_a_key_the_user_is_told_what_to_do(project, spec, monkeypatch):
    root, conn = project
    spec.path = root / "spider.yaml"
    store_page(conn, "http://a.test/one", sample("plant_tier1.html"), spec, tier=1)
    build(conn, spec, use_ai=False)

    import spider.extract.ai as ai_module
    monkeypatch.setattr(ai_module, "ask_json",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ai_module.AIUnavailable("no key")))
    add_derivation(spec, "rarity", {"on": "plant", "describe": "how rare it is"})
    report = DeriveEngine(conn, spec, root=root).run(["rarity"])
    assert report.written == 0
    assert any("ANTHROPIC_API_KEY" in e and "write `formula:`" in e
               for e in report.errors)
