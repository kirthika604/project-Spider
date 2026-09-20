"""Add a calculated column to spider.yaml: checked first, written surgically.

Shared by `spider derive add` and the dashboard, so the two cannot disagree
about what is allowed or how the file is edited.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import yamltext
from ..spec import Spec


@dataclass
class AddResult:
    ok: bool
    message: str
    block: dict | None = None
    problems: list = None          # spec Problems that blocked it


def add_derived(spec: Spec, name: str, entity: str, formula: str, *,
                explain: str | None = None, round_to: int | None = None,
                unit: str | None = None, review: bool = False) -> AddResult:
    """Validate a new derived column and write it into spider.yaml."""
    name = (name or "").strip()
    if not name or not name.replace("_", "").isalnum() or name[0].isdigit():
        return AddResult(False, f"'{name}' is not a usable column name - use letters, "
                                f"digits and underscores, not starting with a digit")
    if entity not in spec.entities:
        return AddResult(False, f"'{entity}' is not an entity in spider.yaml "
                                f"({', '.join(spec.entities)})")
    if name in spec.entities[entity].fields or name in spec.derived:
        return AddResult(False, f"'{name}' already exists on {entity}. Pick another "
                                f"name, or edit it in spider.yaml.")
    if not (formula or "").strip():
        return AddResult(False, "the formula is empty")

    block = {"on": entity, "method": "formula", "formula": formula.strip()}
    if explain:
        block["explain"] = explain
    if round_to is not None:
        block["round"] = round_to
    if unit:
        block["unit"] = unit
    if review:
        block["review"] = "required"

    candidate = dict(spec.raw)
    candidate["derived"] = {**(spec.raw.get("derived") or {}), name: block}
    blockers = [p for p in Spec.from_dict(candidate).validate()
                if p.level == "error" and f"derived.{name}" in p.where]
    if blockers:
        return AddResult(False, "; ".join(f"{p.message} ({p.fix})" for p in blockers),
                         problems=blockers)

    if spec.path is None:
        return AddResult(False, "this spec has no file to write to")
    original = spec.path.read_text(encoding="utf-8")
    edited = yamltext.add_entry(original, "derived", name, block)
    if edited is None:
        return AddResult(False, f"the `derived:` section of {spec.path.name} is written "
                                f"inline, which will not be rewritten for you. Add "
                                f"`{name}: {block}` under it by hand.")
    backup = spec.path.with_suffix(spec.path.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")
    spec.path.write_text(edited, encoding="utf-8")
    return AddResult(True, f"Added '{name}' to {spec.path.name} (your comments are "
                           f"untouched; the old file is {backup.name}).", block)
