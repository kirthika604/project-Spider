"""Find derivations the user did not know were needed (section 11B, FR-25).

After a build, each requested field that is still empty is checked in order:
a conversion of a filled field, a count over a relation, a reference mapping,
and only then an AI-proposed formula - which always needs approval.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..ref import tables as ref
from ..standardize import units
from ..store.db import jdump, now
from .engine import DeriveEngine
from .formula import FormulaError

SAFE_METHODS = {"convert", "aggregate", "arithmetic", "lookup"}

UNIT_HINTS = {
    "_ft": ("ft", "length"), "_feet": ("ft", "length"), "_m": ("m", "length"),
    "_km": ("km", "length"), "_cm": ("cm", "length"), "_kg": ("kg", "mass"),
    "_g": ("g", "mass"), "_lb": ("lb", "mass"), "_c": ("c", "temperature"),
    "_f": ("f", "temperature"),
}


@dataclass
class Suggestion:
    name: str
    on: str
    method: str            # convert | aggregate | lookup | midpoint | year | ai
    formula: str
    inputs: list[str]
    explain: str
    fills: int = 0
    samples: list[str] = field(default_factory=list)
    unit: str | None = None
    bands: dict | None = None
    source: str = "system"       # system | ai
    safe: bool = False

    def to_yaml_block(self) -> dict:
        block = {"on": self.on, "method": "formula" if not self.bands else "lookup",
                 "inputs": self.inputs, "explain": self.explain,
                 "suggested_by": self.source, "approved_on": now()[:10]}
        if self.bands:
            block["bands"] = self.bands
        else:
            block["formula"] = self.formula
        if self.unit:
            block["unit"] = self.unit
        return block


def find(conn, spec, use_ai: bool = False) -> list[Suggestion]:
    """Look at every empty requested field and propose how to fill it."""
    suggestions: list[Suggestion] = []
    for entity_type, ent in spec.entities.items():
        filled = _filled_fields(conn, entity_type)
        total = conn.execute("SELECT COUNT(*) c FROM entities WHERE type=?",
                             (entity_type,)).fetchone()["c"]
        if not total:
            continue
        for field_name, fld in ent.fields.items():
            if field_name in spec.derived:
                continue
            missing = total - filled.get(field_name, 0)
            if missing <= 0:
                continue
            found = (_as_conversion(conn, spec, entity_type, field_name, fld, filled)
                     or _as_aggregate(conn, spec, entity_type, field_name, total)
                     or _as_reference(conn, spec, entity_type, field_name, fld, filled))
            if found is None and use_ai:
                found = _as_ai(conn, spec, entity_type, field_name, filled)
            if found is None:
                continue
            found.fills = missing
            found.samples = _samples(conn, spec, found)
            suggestions.append(found)
    return suggestions


def _filled_fields(conn, entity_type: str) -> dict[str, int]:
    return {r["name"]: r["c"] for r in conn.execute(
        "SELECT a.name, COUNT(DISTINCT a.entity_id) c FROM attributes a "
        "JOIN entities e ON e.id=a.entity_id WHERE e.type=? AND a.status='accepted' "
        "GROUP BY a.name", (entity_type,))}


# ------------------------------------------------------- 1. unit conversion
def _as_conversion(conn, spec, entity_type, field_name, fld, filled):
    """Is this field the same quantity as a filled one, in another unit?"""
    base = field_name
    target_unit = fld.unit
    for suffix, (unit, _quantity) in UNIT_HINTS.items():
        if field_name.endswith(suffix):
            base = field_name[: -len(suffix)]
            target_unit = target_unit or unit
            break
    if not target_unit:
        return None
    for other, count in filled.items():
        if other == field_name or not count:
            continue
        other_spec = spec.entity_field(entity_type, other)
        other_base = other
        other_unit = other_spec.unit if other_spec else None
        for suffix, (unit, _q) in UNIT_HINTS.items():
            if other.endswith(suffix):
                other_base = other[: -len(suffix)]
                other_unit = other_unit or unit
                break
        if other_base != base or not other_unit:
            continue
        if units.quantity_of(other_unit) != units.quantity_of(target_unit):
            continue
        # say which end of a range is converted, rather than leaving it implicit
        reference = f"{other}.min" if (other_spec and other_spec.type == "range") else other
        note = (f"{field_name} is the lower end of {other} converted from "
                f"{other_unit} to {target_unit}") if reference != other else (
                f"{field_name} is {other} converted from {other_unit} to {target_unit}")
        return Suggestion(
            name=field_name, on=entity_type, method="convert",
            formula=f"convert({reference}, '{other_unit}', '{target_unit}')",
            inputs=[reference], unit=target_unit, safe=True, explain=note)
    # a range's midpoint
    for other, count in filled.items():
        if not count or other == field_name:
            continue
        if field_name in (f"{other}_mid", f"{other}_midpoint", f"{other}_average"):
            return Suggestion(
                name=field_name, on=entity_type, method="convert",
                formula=f"midpoint({other}.min, {other}.max)", inputs=[other],
                unit=fld.unit, safe=True,
                explain=f"{field_name} is the midpoint of {other}")
    if field_name.endswith("_year"):
        source = field_name[:-5]
        if filled.get(source):
            return Suggestion(name=field_name, on=entity_type, method="convert",
                              formula=f"year_of({source})", inputs=[source], safe=True,
                              explain=f"the year taken from {source}")
    return None


# ------------------------------------------------ 2. counts over a relation
def _as_aggregate(conn, spec, entity_type, field_name, total):
    lowered = field_name.lower()
    if not any(word in lowered for word in ("count", "number", "per", "total", "_n")):
        return None
    for rel in spec.relations:
        other, direction = None, None
        if rel.to_entity == entity_type:
            other, direction = rel.from_entity, "in"
        elif rel.from_entity == entity_type:
            other, direction = rel.to_entity, "out"
        if not other or other not in lowered and other[:-1] not in lowered:
            continue
        links = conn.execute("SELECT COUNT(*) c FROM relations WHERE relation=?",
                             (rel.name,)).fetchone()["c"]
        if not links:
            continue
        del direction
        return Suggestion(
            name=field_name, on=entity_type, method="aggregate",
            formula=f"count({other} via {rel.name})", inputs=[other], safe=True,
            explain=f"how many {other} records link to this one through {rel.name}")
    return None


# --------------------------------------------- 3. mapping in the reference
def _as_reference(conn, spec, entity_type, field_name, fld, filled):
    """A district implies its state; a month implies its season."""
    if field_name in ("state", "parent", "region_state"):
        for other in ("name", "district", "region"):
            if filled.get(other):
                return Suggestion(
                    name=field_name, on=entity_type, method="lookup",
                    formula=f"place_parent({other})", inputs=[other], safe=True,
                    explain=f"the state that {other} belongs to, from the places reference")
    if "season" in field_name.lower():
        for other, count in filled.items():
            other_spec = spec.entity_field(entity_type, other)
            if count and other_spec and other_spec.type == "month":
                return Suggestion(
                    name=field_name, on=entity_type, method="lookup",
                    formula=f"season_of({other})", inputs=[other], safe=True,
                    explain=f"the season of {other}")
    if "zone" in field_name.lower() or "band" in field_name.lower():
        for other, count in filled.items():
            other_spec = spec.entity_field(entity_type, other)
            if count and other_spec and other_spec.type in ("range", "number") \
                    and other_spec.unit in ("m", "ft"):
                return Suggestion(
                    name=field_name, on=entity_type, method="lookup",
                    formula="", inputs=[f"{other}.min"],
                    bands={"cutoffs": [1500, 3000],
                           "labels": ["subtropical", "temperate", "alpine"]},
                    safe=True,
                    explain=f"a zone read from {other} using altitude bands")
    return None


# -------------------------------------------------- 4. AI, as a last resort
def _as_ai(conn, spec, entity_type, field_name, filled):
    from ..extract import ai as ai_module
    if not ai_module.available():
        return None
    available = ", ".join(sorted(filled)) or "(none)"
    prompt = (
        f"A dataset has entity '{entity_type}' with these filled fields: {available}.\n"
        f"The field '{field_name}' is empty on every record.\n"
        f"If it can be calculated from the filled fields, answer with JSON "
        f'{{"possible": true, "formula": "...", "inputs": ["..."], "explain": "..."}} '
        f"using only these functions: min, max, avg, sum, count, band, if, convert, "
        f"midpoint, season_of, year_of, round, concat, and arithmetic.\n"
        f'If it cannot, answer {{"possible": false}}.')
    try:
        answer = ai_module.ask_json(prompt)
    except Exception:
        return None
    if not answer.get("possible") or not answer.get("formula"):
        return None
    return Suggestion(
        name=field_name, on=entity_type, method="ai", formula=str(answer["formula"]),
        inputs=[str(i) for i in (answer.get("inputs") or [])],
        explain=str(answer.get("explain") or "proposed by AI"), source="ai", safe=False)


# --------------------------------- a derivation the user described in words
DRAFT_PROMPT = """A dataset has an entity '{entity}' with these fields:
{fields}

The user wants a new field called '{name}', described in their own words as:
"{description}"

Write a formula for it. Answer with JSON:
{{"formula": "...", "inputs": ["..."], "explain": "one plain sentence"}}

You may use only these functions: min, max, avg, sum, count, band, if,
convert, midpoint, season_of, year_of, round, concat, lower, upper, abs, len,
and arithmetic. Refer to a range's ends as field.min and field.max, and to
related records as count(<entity> via <relation>). Use only the fields listed
above. If it cannot be calculated from them, answer {{"formula": null}}."""


def draft_from_description(conn, spec, der) -> Suggestion | None:
    """Turn `describe: "..."` into a formula the user can approve (D7).

    The result is never applied: it is stored as a suggestion marked
    `suggested_by: ai`, and approving it writes `review: required`.
    """
    from ..extract import ai as ai_module
    ent = spec.entities.get(der.on)
    if not ent:
        return None

    lines = []
    for name, fld in ent.fields.items():
        bits = [fld.type]
        if fld.unit:
            bits.append(f"in {fld.unit}")
        lines.append(f"  {name}: {', '.join(bits)}")
    for other in spec.derived.values():
        if other.on == der.on and other.name != der.name and other.formula:
            lines.append(f"  {other.name}: derived")
    for rel in spec.relations:
        if rel.from_entity == der.on:
            lines.append(f"  relation: {rel.name} -> {rel.to_entity}")

    prompt = DRAFT_PROMPT.format(entity=der.on, fields="\n".join(lines),
                                 name=der.name, description=der.describe)
    try:
        answer = ai_module.ask_json(prompt, "You write short, safe formulas. JSON only.")
    except Exception:
        return None
    formula = answer.get("formula")
    if not formula:
        return None

    # a drafted formula must survive the same evaluator as a hand-written one
    from .formula import FormulaError, referenced_names
    known = set(ent.fields) | set(spec.derived) | set(spec.entities)
    try:
        unknown = [n for n in referenced_names(formula)
                   if n.split("__")[0] not in known]
    except FormulaError:
        return None
    if unknown:
        return None

    return Suggestion(
        name=der.name, on=der.on, method="ai", formula=str(formula),
        inputs=[str(i) for i in (answer.get("inputs") or [])],
        explain=str(answer.get("explain") or der.describe), source="ai", safe=False)


# ------------------------------------------------------------ store / apply
def _samples(conn, spec, suggestion: Suggestion) -> list[str]:
    """Run the proposal on a few records so the user sees real results."""
    from ..spec import DerivedSpec
    block = suggestion.to_yaml_block()
    der = DerivedSpec.parse(suggestion.name, block)
    engine = DeriveEngine(conn, spec)
    out = []
    rows = conn.execute("SELECT id, canonical_name FROM entities WHERE type=? LIMIT 5",
                        (suggestion.on,)).fetchall()
    for row in rows:
        try:
            result = engine.calculate(der, row["id"])
        except FormulaError as exc:
            out.append(f"{row['canonical_name']}: cannot calculate ({exc})")
            continue
        if result is not None:
            out.append(f"{row['canonical_name']}: {result.value}")
    return out[:3]


def save(conn, suggestions: list[Suggestion]) -> int:
    conn.execute("DELETE FROM review_queue WHERE kind='suggestion' AND status='open'")
    for suggestion in suggestions:
        conn.execute(
            "INSERT OR REPLACE INTO derivations(name,entity_type,method,inputs,formula,"
            "if_missing,unit,explain,status,suggested_by,fills,samples) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (suggestion.name, suggestion.on, suggestion.method, jdump(suggestion.inputs),
             suggestion.formula or jdump(suggestion.bands), "leave_empty", suggestion.unit,
             suggestion.explain, "suggested", suggestion.source, suggestion.fills,
             jdump(suggestion.samples)))
        conn.execute(
            "INSERT INTO review_queue(kind,target,field,reason,detail,created_at) "
            "VALUES(?,?,?,?,?,?)",
            ("suggestion", f"{suggestion.on}.{suggestion.name}", suggestion.name,
             f"would fill {suggestion.fills} empty cells", jdump(asdict(suggestion)),
             now()))
    conn.commit()
    return len(suggestions)


def pending(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM derivations WHERE status='suggested' ORDER BY fills DESC")]


def approve(conn, spec, name: str, edit_formula: str | None = None) -> dict:
    """Write an approved suggestion into spider.yaml, with its approval date."""
    row = conn.execute("SELECT * FROM derivations WHERE name=? AND status='suggested'",
                       (name,)).fetchone()
    if not row:
        raise KeyError(f"no suggested derivation called '{name}'")
    from ..store.db import jload
    inputs = jload(row["inputs"], []) or []
    formula = edit_formula or row["formula"]
    block: dict = {"on": row["entity_type"], "inputs": inputs,
                   "explain": row["explain"], "suggested_by": row["suggested_by"],
                   "approved_on": now()[:10]}
    bands = jload(formula, None) if str(formula).startswith("{") else None
    if bands:
        block["method"], block["bands"] = "lookup", bands
    else:
        block["method"], block["formula"] = "formula", formula
    if row["unit"]:
        block["unit"] = row["unit"]
    if row["suggested_by"] == "ai":
        block["review"] = "required"

    spec.raw.setdefault("derived", {})[name] = block
    from ..spec import DerivedSpec
    spec.derived[name] = DerivedSpec.parse(name, block)
    conn.execute("UPDATE derivations SET status='active', approved_on=? WHERE name=?",
                 (now(), name))
    conn.execute("UPDATE review_queue SET status='resolved' "
                 "WHERE kind='suggestion' AND field=?", (name,))
    conn.commit()
    return block


def reject(conn, name: str) -> None:
    conn.execute("UPDATE derivations SET status='rejected' WHERE name=?", (name,))
    conn.execute("UPDATE review_queue SET status='dismissed' "
                 "WHERE kind='suggestion' AND field=?", (name,))
    conn.commit()
