"""Calculate derived fields and record their lineage (D7, FR-22)."""

from __future__ import annotations

from dataclasses import dataclass, field

from pathlib import Path

from ..assemble import relations as rel_store
from ..assemble.confidence import inherit, pick_best
from ..standardize import units
from ..store.db import jdump, now
from .formula import FormulaError, evaluate


@dataclass
class DerivedValue:
    entity_id: int
    name: str
    value: object
    confidence: float
    lineage: dict
    origin: str = "derived"
    unit: str | None = None
    needs_review: bool = False
    note: str = ""


@dataclass
class DeriveReport:
    written: int = 0
    skipped_missing: int = 0
    errors: list[str] = field(default_factory=list)
    queued_for_review: int = 0
    described: list[str] = field(default_factory=list)


class DeriveEngine:
    def __init__(self, conn, spec, root=None):
        self.conn = conn
        self.spec = spec
        self.root = Path(root) if root else (
            spec.path.parent if spec.path else Path.cwd())

    # ------------------------------------------------------------------ run
    def run(self, only: list[str] | None = None) -> DeriveReport:
        report = DeriveReport()
        order = self._order()
        for name in order:
            der = self.spec.derived[name]
            if only and name not in only:
                continue
            if der.status != "active":
                continue
            if (der.describe and not der.formula and not der.bands
                    and not der.rules and not der.code):
                report.described.append(self._draft(der, report))
                continue
            self._run_one(der, report)
        self.conn.commit()
        return report

    def _draft(self, der, report: DeriveReport) -> str:
        """`describe:` asks Spider for a formula; the user still approves it."""
        from .suggest import draft_from_description, save
        drafted = draft_from_description(self.conn, self.spec, der)
        if drafted is None:
            report.errors.append(
                f"{der.name}: described in words but no formula yet. Spider drafts "
                f"one when ANTHROPIC_API_KEY is set; otherwise write `formula:` "
                f"yourself, then run `spider build` again")
            return der.name
        drafted.fills = self.conn.execute(
            "SELECT COUNT(*) c FROM entities WHERE type=?", (der.on,)).fetchone()["c"]
        from .suggest import _samples
        drafted.samples = _samples(self.conn, self.spec, drafted)
        save(self.conn, [drafted])
        return der.name

    def _order(self) -> list[str]:
        """Derivations that feed others are calculated first."""
        pending = dict(self.spec.derived)
        done, order = set(), []
        while pending:
            progressed = False
            for name, der in list(pending.items()):
                needs = {r for r in der.referenced_fields() if r in self.spec.derived}
                if needs <= done:
                    order.append(name)
                    done.add(name)
                    del pending[name]
                    progressed = True
            if not progressed:                       # a cycle: `check` reports it
                order.extend(pending)
                break
        return order

    def _run_one(self, der, report: DeriveReport) -> None:
        entity_rows = self.conn.execute(
            "SELECT id, canonical_name FROM entities WHERE type=?", (der.on,)).fetchall()
        for row in entity_rows:
            try:
                result = self.calculate(der, row["id"])
            except FormulaError as exc:
                report.errors.append(f"{der.name} on {row['canonical_name']}: {exc}")
                continue
            if result is None:
                report.skipped_missing += 1
                continue
            self._write(result, der)
            report.written += 1
            if result.needs_review:
                report.queued_for_review += 1

    # ------------------------------------------------------------ calculate
    def calculate(self, der, entity_id: int) -> DerivedValue | None:
        variables, sources, missing = self._variables(der, entity_id)
        if missing and der.if_missing == "leave_empty":
            return None
        if missing and der.if_missing == "use_fallback":
            for name in missing:
                variables[name] = der.fallback
                variables[f"{name}__min"] = der.fallback
                variables[f"{name}__max"] = der.fallback

        value = self._apply(der, variables, entity_id)
        if value is None or value == "":
            return None
        if der.unit and isinstance(value, (int, float)):
            pass
        if der.round is not None:
            number = units.parse_number(value)
            if number is not None:
                value = round(number, int(der.round))

        confidence = inherit([c for _, c in sources] or [0.5],
                             origin="inferred" if der.method == "describe" else "derived")
        if missing and der.if_missing == "partial":
            confidence = round(max(0.0, confidence - 0.2), 3)
        needs_review = (der.review == "required") or bool(der.suggested_by == "ai")
        lineage = {
            "formula": der.formula or (f"bands {der.bands}" if der.bands else der.method),
            "method": der.method,
            "inputs": [attr_id for attr_id, _ in sources],
            "input_fields": der.referenced_fields(),
            "explain": der.explain,
            "if_missing_applied": sorted(missing) if missing else [],
        }
        return DerivedValue(entity_id=entity_id, name=der.name, value=value,
                            confidence=confidence, lineage=lineage, unit=der.unit,
                            needs_review=needs_review,
                            origin="inferred" if der.method == "describe" else "derived")

    def _variables(self, der, entity_id: int):
        """Bind this entity's values, plus `name.min` / `name.max` for ranges."""
        variables: dict[str, object] = {}
        sources: list[tuple[int, float]] = []
        rows = self.conn.execute(
            "SELECT id, name, value, value_num, value_max, confidence, tier, origin "
            "FROM attributes WHERE entity_id=? AND status='accepted'",
            (entity_id,)).fetchall()
        by_name: dict[str, list] = {}
        for row in rows:
            by_name.setdefault(row["name"], []).append(row)

        wanted = set(der.referenced_fields()) | set(
            i.split(".")[0] for i in der.inputs)
        for name in wanted:
            picks = by_name.get(name)
            if not picks:
                continue
            # a formula may read a default, but the result inherits its zero
            # confidence, so nothing calculated from one looks certain
            best = pick_best(picks)      # the same value the table shows
            scalar = best["value_num"] if best["value_num"] is not None else best["value"]
            variables[name] = scalar
            variables[f"{name}__value"] = scalar
            variables[f"{name}__min"] = best["value_num"]
            variables[f"{name}__max"] = (best["value_max"] if best["value_max"] is not None
                                         else best["value_num"])
            sources.append((best["id"], best["confidence"] or 0.5))

        missing = {name for name in wanted
                   if name not in variables and name not in self.spec.entities}
        return variables, sources, missing

    def _apply(self, der, variables, entity_id: int):
        if der.method == "code" or (der.code and not der.formula and not der.bands):
            from .code import CodeError, run as run_code
            try:
                return run_code(self.root, der.code, der.function, variables)
            except CodeError as exc:
                raise FormulaError(str(exc)) from exc
        if der.bands:
            source_field = (der.inputs[0] if der.inputs else
                            (der.referenced_fields() or [None])[0])
            if not source_field:
                raise FormulaError(f"{der.name}: no input given for the bands")
            key = source_field.replace(".", "__")
            value = variables.get(key, variables.get(source_field.split(".")[0]))
            from .formula import f_band
            return f_band(value, der.bands.get("cutoffs") or [],
                          der.bands.get("labels") or [])
        if der.rules:
            for rule in der.rules:
                condition = rule.get("if") or rule.get("when")
                if condition is None or evaluate(condition, variables, self._functions(entity_id)):
                    return rule.get("then", rule.get("value"))
            return None
        if der.formula:
            return evaluate(der.formula, variables, self._functions(entity_id))
        return None

    def _functions(self, entity_id: int):
        """`count(plant via grows_in)` counts related records (D7 aggregate)."""
        def count_related(entity_type: str, relation: str):
            out = set(rel_store.related(self.conn, entity_id, relation, "out"))
            out |= set(rel_store.related(self.conn, entity_id, relation, "in"))
            if not out:
                return 0
            marks = ",".join("?" for _ in out)
            rows = self.conn.execute(
                f"SELECT COUNT(*) c FROM entities WHERE id IN ({marks}) AND type=?",
                (*out, entity_type)).fetchone()
            return rows["c"]

        def sum_related(entity_type: str, relation: str, field_name: str = ""):
            ids = set(rel_store.related(self.conn, entity_id, relation, "out"))
            ids |= set(rel_store.related(self.conn, entity_id, relation, "in"))
            if not ids or not field_name:
                return 0
            marks = ",".join("?" for _ in ids)
            row = self.conn.execute(
                f"SELECT SUM(value_num) s FROM attributes WHERE entity_id IN ({marks}) "
                f"AND name=? AND status='accepted'", (*ids, field_name)).fetchone()
            return row["s"] or 0
        from .statistics import build as build_statistics
        functions = {"count": count_related, "sum_related": sum_related}
        functions.update(build_statistics(self.conn, entity_id))
        return functions

    # ---------------------------------------------------------------- write
    def _write(self, result: DerivedValue, der) -> None:
        self.conn.execute(
            "DELETE FROM attributes WHERE entity_id=? AND name=? AND origin IN "
            "('derived','inferred')", (result.entity_id, result.name))
        number = units.parse_number(result.value)
        # a calculated number is written the way a reader would write it,
        # not as a float's full repr
        shown = (units.format_number(number)
                 if number is not None and not isinstance(result.value, str)
                 else str(result.value))
        status = "review" if result.needs_review else "accepted"
        attribute_id = self.conn.execute(
            "INSERT INTO attributes(entity_id,name,value,value_num,unit,raw_value,"
            "origin,evidence,confidence,status,lineage,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (result.entity_id, result.name, shown, number, result.unit,
             None, result.origin, der.explain or f"calculated: {result.lineage['formula']}",
             result.confidence, status, jdump(result.lineage), now())).lastrowid
        if result.needs_review:
            self.conn.execute(
                "INSERT INTO review_queue(kind,target,entity_id,field,reason,detail,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("derived_review", f"{der.on}.{der.name}", result.entity_id, der.name,
                 "this derivation is marked `review: required`",
                 jdump({"value": result.value, "attribute_id": attribute_id,
                        "formula": result.lineage["formula"]}), now()))
