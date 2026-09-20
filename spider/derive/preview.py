"""Try a formula on real records before it is written down."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..spec import DerivedSpec, FORMULA_FUNCTIONS
from .engine import DeriveEngine
from .formula import FormulaError, called_names, suggest
from .statistics import Columns


@dataclass
class Preview:
    entity: str
    formula: str
    reads: list[str] = field(default_factory=list)
    unknown: list[tuple] = field(default_factory=list)      # (name, suggestion)
    syntax_error: str = ""
    samples: list[tuple] = field(default_factory=list)      # (label, value, inputs)
    scanned: int = 0
    filled: int = 0
    empty: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    missing: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.syntax_error and not self.unknown


def known_names(spec, entity: str) -> set:
    ent = spec.entities.get(entity)
    names = set(ent.fields) if ent else set()
    names |= {d.name for d in spec.derived.values() if d.on == entity}
    names |= set(spec.entities)
    return names


def preview(conn, spec, entity: str, formula: str, *, samples: int = 5,
            scan: int = 2000, unit: str | None = None) -> Preview:
    """Evaluate the formula on real records, changing nothing."""
    result = Preview(entity=entity, formula=formula)
    der = DerivedSpec.parse("__try__", {"on": entity, "method": "formula",
                                        "formula": formula, "unit": unit})
    try:
        result.reads = der.referenced_fields()
    except Exception as exc:                          # noqa: BLE001
        result.syntax_error = str(exc)
        return result
    try:
        from .formula import _compile, prepare
        _compile(prepare(formula))
    except FormulaError as exc:
        result.syntax_error = str(exc)
        return result

    # a function that does not exist is a different mistake from a field that
    # does not exist, and gets its own message
    from .catalogue import BY_NAME
    for called in called_names(formula):
        if called not in BY_NAME and called != "if_":
            hint = suggest(called, BY_NAME)
            result.syntax_error = (
                f"there is no function called '{called}'"
                + (f" - did you mean '{hint}'?" if hint else "")
                + " (`spider derive functions` lists every one)")
            return result

    known = known_names(spec, entity)
    for name in result.reads:
        if name not in known and name not in FORMULA_FUNCTIONS:
            result.unknown.append((name, suggest(name, known)))
    if result.unknown:
        return result

    engine = DeriveEngine(conn, spec)
    engine._columns = Columns(conn)                   # one read per column, not per record
    rows = conn.execute("SELECT id, canonical_name FROM entities WHERE type=? "
                        "ORDER BY id LIMIT ?", (entity, scan)).fetchall()
    for row in rows:
        result.scanned += 1
        try:
            outcome = engine.calculate(der, row["id"])
        except FormulaError as exc:
            result.failed += 1
            if str(exc) not in result.errors and len(result.errors) < 5:
                result.errors.append(str(exc))
            continue
        if outcome is None:
            result.empty += 1
            for name in engine._last_missing:
                result.missing[name] = result.missing.get(name, 0) + 1
            continue
        result.filled += 1
        if len(result.samples) < samples:
            variables, _sources, _missing = engine._variables(der, row["id"])
            shown = {n: variables.get(n) for n in result.reads if n in variables}
            result.samples.append((row["canonical_name"], outcome.value, shown))
    return result
