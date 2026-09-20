"""Four ways to say what data you want (section 14A).

Channel 1 (plain words) lives in describe.py. This module covers the other
two that read something you already have:

  channel 2  an example of the output you want - a CSV or Excel header, or a
             few sample rows - so the dataset comes out in that shape
  channel 3  an existing structure - SQL `CREATE TABLE`, a JSON Schema or an
             existing SQLite file - so names and keys carry over
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import json
import re
import sqlite3
from pathlib import Path

from .standardize import dates, units
from .standardize.names import case_style, normalise

# a unit written into a column heading: "Alt (m)", "weight_kg", "price in INR"
# a written date, checked before ranges: "2026-09-20" is not "2026 to 09"
DATE_LIKE = re.compile(
    r"^\s*(?:\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}"
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{2,4})\s*$")

UNIT_IN_HEADING = re.compile(
    r"[\(\[]\s*([a-zA-Z°%]{1,8})\s*[\)\]]|_([a-zA-Z]{1,4})$|\bin\s+([a-zA-Z]{1,4})\b")

SQL_TYPES = {
    "int": "number", "integer": "number", "bigint": "number", "smallint": "number",
    "real": "number", "float": "number", "double": "number", "decimal": "number",
    "numeric": "number", "date": "date", "datetime": "date", "timestamp": "date",
    "bool": "bool", "boolean": "bool",
}

JSON_TYPES = {"integer": "number", "number": "number", "boolean": "bool",
              "string": "text", "array": "text", "object": "text"}


class CaptureError(Exception):
    pass


# ------------------------------------------------------- channel 2: example
def from_example(path: str | Path, entity: str | None = None,
                 sample_rows: int = 30) -> tuple[dict, list[str]]:
    """Read a file the user already has (or wishes for) and copy its shape."""
    path = Path(path)
    if not path.exists():
        raise CaptureError(f"{path} not found")
    if path.suffix.lower() in (".csv", ".tsv"):
        header, rows = _read_csv(path, sample_rows)
    elif path.suffix.lower() in (".xlsx", ".xls"):
        header, rows = _read_xlsx(path, sample_rows)
    elif path.suffix.lower() == ".json":
        header, rows = _read_json(path, sample_rows)
    else:
        raise CaptureError(
            f"{path.suffix or 'that file'} is not an example Spider can read - "
            f"use a CSV, Excel or JSON file with the columns you want")
    if not header:
        raise CaptureError(f"{path.name} has no column headings to copy")

    name = entity or _entity_name(path)
    fields, notes = {}, []
    for column in header:
        field_name, spec, note = _field_from_column(column, [r.get(column) for r in rows])
        if not field_name:
            continue
        fields[field_name] = spec
        if note:
            notes.append(note)

    identity = _identity_for(fields, rows, header)
    document = {"entities": {name: {"identity": identity, "fields": fields}},
                "relations": []}
    notes.insert(0, f"{len(fields)} columns copied from {path.name}"
                    f" ({len(rows)} sample row(s) read)")
    if identity:
        notes.append(f"'{identity[0]}' looks like the column that identifies a row - "
                     f"change `identity:` if that is wrong")
    return document, notes


def _read_csv(path: Path, limit: int):
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        header = [h for h in (reader.fieldnames or []) if h and h.strip()]
        rows = [row for _, row in zip(range(limit), reader)]
    return header, rows


def _read_xlsx(path: Path, limit: int):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise CaptureError("reading an Excel example needs openpyxl "
                           "(pip install 'project-spider[files]')") from exc
    book = load_workbook(path, data_only=True, read_only=True)
    sheet = book.worksheets[0]
    raw = list(sheet.iter_rows(values_only=True))
    book.close()
    if not raw:
        return [], []
    header = [str(c).strip() for c in raw[0] if c is not None and str(c).strip()]
    rows = [dict(zip(header, values)) for values in raw[1:limit + 1]]
    return header, rows


def _read_json(path: Path, limit: int):
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data if isinstance(data, list) else data.get("records") or [data]
    records = [r for r in records if isinstance(r, dict)][:limit]
    header: list[str] = []
    for record in records:
        for key in record:
            if key not in header:
                header.append(key)
    return header, records


def _field_from_column(column: str, values: list) -> tuple[str, dict, str]:
    """A heading plus its sample values give a field name, type and unit."""
    heading = normalise(column)
    if not heading:
        return "", {}, ""
    unit = None
    found = UNIT_IN_HEADING.search(heading)
    if found:
        candidate = next(g for g in found.groups() if g)
        if units.quantity_of(candidate):
            unit = candidate.lower()
    field_name = case_style(re.sub(r"[\(\[].*?[\)\]]", " ", heading), "snake_case")
    field_name = re.sub(r"_+", "_", field_name).strip("_") or "column"

    samples = [v for v in values if v not in (None, "")][:20]
    spec: dict = {"type": _guess_type(samples, field_name)}
    if unit and spec["type"] in ("number", "range"):
        spec["unit"] = unit
    elif unit:
        spec["unit"] = unit
    note = ""
    if spec["type"] == "range":
        note = f"'{field_name}' holds ranges like {samples[0]!r}, so it is a range field"
    return field_name, spec, note


def _guess_type(samples: list, field_name: str) -> str:
    if not samples:
        return "text"
    texts = [str(s) for s in samples]
    if all(re.fullmatch(r"\s*-?\d[\d,]*(\.\d+)?\s*(?:[a-zA-Z°%]{1,6})?\s*", t)
           for t in texts):
        return "number"
    if all(DATE_LIKE.match(t) for t in texts):
        return "date"
    ranged = sum(1 for t in texts
                 if re.search(r"\d[\d,]*\s*(?:-|–|to)\s*\d", t, re.IGNORECASE))
    if ranged >= max(1, len(texts) // 2):
        return "range"
    # written month names are a month column whatever the heading calls it
    if all(re.fullmatch(r"[A-Za-z]{3,9}\.?", t.strip()) and dates.to_month(t)
           for t in texts):
        return "month"
    if "month" in field_name and all(dates.to_month(t) for t in texts):
        return "month"
    if sum(1 for t in texts if dates.to_iso(t)) >= max(1, int(len(texts) * 0.8)):
        if any(re.search(r"\d{4}", t) for t in texts):
            return "date"
    if all(str(s).strip().lower() in ("true", "false", "yes", "no", "0", "1")
           for s in samples):
        return "bool"
    if all(str(s).startswith("http") for s in samples):
        return "url"
    return "text"


def _identity_for(fields: dict, rows: list, header: list) -> list[str]:
    """The column whose values are unique and textual identifies a record."""
    for column in header:
        name = case_style(re.sub(r"[\(\[].*?[\)\]]", " ", normalise(column)),
                          "snake_case").strip("_")
        if name not in fields or fields[name]["type"] not in ("text", "url"):
            continue
        values = [str(r.get(column) or "").strip() for r in rows]
        values = [v for v in values if v]
        if values and len(set(values)) == len(values):
            return [name]
    return [next(iter(fields))] if fields else []


def _entity_name(path: Path) -> str:
    stem = case_style(re.sub(r"[\d_\-]+", " ", path.stem), "snake_case").strip("_")
    stem = stem or "record"
    return stem[:-1] if stem.endswith("s") and len(stem) > 4 else stem


# --------------------------------------------------- channel 3: a structure
def from_structure(path: str | Path) -> tuple[dict, list[str]]:
    """Carry an existing schema over: SQL, JSON Schema or a SQLite file."""
    path = Path(path)
    if not path.exists():
        raise CaptureError(f"{path} not found")
    suffix = path.suffix.lower()
    if suffix == ".sql":
        return from_sql(path.read_text(encoding="utf-8"))
    if suffix == ".json":
        return from_json_schema(json.loads(path.read_text(encoding="utf-8")))
    if suffix in (".db", ".sqlite", ".sqlite3"):
        return from_sqlite(path)
    raise CaptureError(
        f"{suffix or 'that file'} is not a structure Spider can import - use a "
        f".sql file, a JSON Schema (.json) or a SQLite database (.db)")


CREATE_TABLE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?[\"'`\[]?(\w+)[\"'`\]]?\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL)


def _dedupe(relations: list[dict], entities: dict) -> list[dict]:
    """A key can be found twice - by its FOREIGN KEY and by its `_id` name."""
    seen, out = set(), []
    for relation in relations:
        if relation["to"] not in entities or relation["from"] not in entities:
            continue
        marker = (relation["from"], relation["name"], relation["to"])
        if marker in seen:
            continue
        seen.add(marker)
        out.append(relation)
    return out


def from_sql(text: str) -> tuple[dict, list[str]]:
    entities, relations, notes = {}, [], []
    for table_name, body in CREATE_TABLE.findall(text):
        fields, identity = {}, []
        for line in _split_columns(body):
            line = line.strip()
            low = line.lower()
            if low.startswith(("primary key", "unique", "constraint", "check", "key ")):
                if low.startswith("primary key"):
                    identity = [c.strip(' "`[]') for c in
                                re.findall(r"\((.*?)\)", line)[0].split(",")]
                continue
            if low.startswith("foreign key"):
                match = re.search(r"references\s+[\"'`\[]?(\w+)", line, re.IGNORECASE)
                if match:
                    relations.append({"from": table_name, "name": f"has_{match.group(1)}",
                                      "to": match.group(1)})
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            column = parts[0].strip(' "`[]')
            sql_type = re.sub(r"\(.*?\)", "", parts[1]).lower()
            spec: dict = {"type": SQL_TYPES.get(sql_type, "text")}
            if "not null" in low:
                spec["required"] = True
            if "primary key" in low:
                identity = identity or [column]
            fields[column] = spec
            if column.endswith("_id") and column[:-3] != table_name:
                relations.append({"from": table_name, "name": f"has_{column[:-3]}",
                                  "to": column[:-3]})
        if fields:
            entities[table_name] = {"identity": identity or [next(iter(fields))],
                                    "fields": fields}
    if not entities:
        raise CaptureError("no CREATE TABLE statements found in that file")
    relations = _dedupe(relations, entities)
    notes.append(f"{len(entities)} table(s) imported: {', '.join(entities)}")
    if relations:
        notes.append(f"{len(relations)} relation(s) read from the keys")
    notes.append("column names and keys were kept exactly as written")
    return {"entities": entities, "relations": relations}, notes


def _split_columns(body: str) -> list[str]:
    """Split on commas that are not inside brackets."""
    out, depth, current = [], 0, ""
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            out.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        out.append(current)
    return out


def from_json_schema(schema: dict) -> tuple[dict, list[str]]:
    name = schema.get("title") or "record"
    name = case_style(name, "snake_case")
    properties = schema.get("properties") or {}
    if not properties and "items" in schema:
        properties = (schema["items"] or {}).get("properties") or {}
    if not properties:
        raise CaptureError("that JSON Schema has no `properties` to import")
    required = set(schema.get("required") or [])
    fields = {}
    for key, body in properties.items():
        body = body or {}
        json_type = body.get("type")
        if isinstance(json_type, list):
            json_type = next((t for t in json_type if t != "null"), "string")
        spec: dict = {"type": JSON_TYPES.get(json_type, "text")}
        if body.get("format") in ("date", "date-time"):
            spec["type"] = "date"
        if body.get("format") == "uri":
            spec["type"] = "url"
        if json_type == "array":
            spec["multiple"] = True
        if key in required:
            spec["required"] = True
        if body.get("enum"):
            spec["sanity"] = list(body["enum"])
        fields[key] = spec
    identity = [next((k for k in fields if k in required), next(iter(fields)))]
    return ({"entities": {name: {"identity": identity, "fields": fields}},
             "relations": []},
            [f"imported '{name}' with {len(fields)} field(s) from the JSON Schema"])


def from_sqlite(path: Path) -> tuple[dict, list[str]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    entities, relations = {}, []
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'")]
    for table_name in tables:
        fields, identity = {}, []
        for column in conn.execute(f'PRAGMA table_info("{table_name}")'):
            sql_type = re.sub(r"\(.*?\)", "", (column["type"] or "").lower())
            spec: dict = {"type": SQL_TYPES.get(sql_type, "text")}
            if column["notnull"]:
                spec["required"] = True
            if column["pk"]:
                identity.append(column["name"])
            fields[column["name"]] = spec
        for key in conn.execute(f'PRAGMA foreign_key_list("{table_name}")'):
            relations.append({"from": table_name, "name": f"has_{key['table']}",
                              "to": key["table"]})
        if fields:
            entities[table_name] = {"identity": identity or [next(iter(fields))],
                                    "fields": fields}
    conn.close()
    if not entities:
        raise CaptureError(f"{path.name} has no tables to import")
    relations = _dedupe(relations, entities)
    return ({"entities": entities, "relations": relations},
            [f"{len(entities)} table(s) read from {path.name}: {', '.join(entities)}"])


# ------------------------------------------------ clarifying questions (<=5)
@dataclass
class Question:
    """One thing Spider cannot decide safely, with an answer ready to accept."""
    key: str                       # where the answer goes in spider.yaml
    ask: str
    options: list[tuple[str, str]] = field(default_factory=list)  # (value, what it means)
    suggested: str = ""
    free_text: bool = False

    def suggestion_text(self) -> str:
        for value, meaning in self.options:
            if value == self.suggested:
                return f"{value} - {meaning}"
        return self.suggested


def questions_for(document: dict) -> list[Question]:
    """At most five, each with a suggested answer and the choices spelled out.

    Spider asks only what it cannot work out on its own: what a row is, which
    unit an unmarked measurement uses, how tidy the output must be, what to do
    when sources disagree, and which sites you already trust.
    """
    entities = document.get("entities") or {}
    primary = next(iter(entities), "record")
    fields = (entities.get(primary, {}).get("fields") or {})
    identity = (entities.get(primary, {}).get("identity") or [next(iter(fields), "?")])[0]

    out: list[Question] = []

    # 1. one row per what
    row_options = [(primary, f"one row for each {primary}, told apart by "
                             f"'{identity}'")]
    for other in list(entities)[1:3]:
        row_options.append((f"{primary}+{other}",
                            f"one row for each {primary} and {other} together"))
    out.append(Question("entities.identity", "What is one row of this dataset?",
                        row_options, primary))

    # 2. a unit for any measurement that does not carry one
    unitless = [name for name, spec in fields.items()
                if spec.get("type") in ("number", "range") and not spec.get("unit")]
    if unitless:
        out.append(Question(
            f"entities.{primary}.fields.{unitless[0]}.unit",
            f"What unit is '{unitless[0]}' in? Everything found in another unit "
            f"is converted to it.",
            [("m", "metres"), ("ft", "feet"), ("km", "kilometres"),
             ("kg", "kilograms"), ("", "leave it as written")],
            "m"))

    # 3. how tidy the output has to be
    out.append(Question("mode", "How tidy does the output have to be?",
                        [("project", "separate, linked tables (3NF) - for an app"),
                         ("analysis", "one flat table is allowed - for a "
                                      "spreadsheet or a notebook")],
                        "project"))

    # 4. what to do when sources disagree
    out.append(Question("standardize.on_conflict",
                        "When two sources disagree, what should happen?",
                        [("keep_all_and_flag", "keep every value and flag it for "
                                               "you to look at"),
                         ("majority", "take the value most sources give"),
                         ("trusted_first", "take the one from the site you trust most"),
                         ("newest", "take the most recently published")],
                        "keep_all_and_flag"))

    # 5. which sites you already trust
    out.append(Question("sources.seeds",
                        "Which sites do you already trust? Spider starts there, "
                        "and they count as more reliable than anything it finds.",
                        [], "", free_text=True))
    return out[:5]


def apply_answers(document: dict, answers: dict) -> dict:
    """Fold the answers back into the draft."""
    for key, value in answers.items():
        if not value:
            continue
        if key == "mode":
            document["mode"] = value
            if value == "analysis":
                document.setdefault("storage", {})["normal_form"] = "0NF"
        elif key == "standardize.on_conflict":
            document.setdefault("standardize", {})["on_conflict"] = value
        elif key == "sources.seeds":
            seeds = [s.strip() for s in str(value).replace(",", " ").split()
                     if s.strip()]
            if seeds:
                sources = document.setdefault("sources", {})
                sources.setdefault("seeds", []).extend(seeds)
                sources["mode"] = "start_here"
        elif key.endswith(".unit"):
            parts = key.split(".")
            entity, field_name = parts[1], parts[3]
            spec = (document.get("entities", {}).get(entity, {})
                    .get("fields", {}).get(field_name))
            if isinstance(spec, dict):
                spec["unit"] = value
        elif key == "entities.identity" and "+" in str(value):
            document.setdefault("_note_one_row_per", value)
    return document
