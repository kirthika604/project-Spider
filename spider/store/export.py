"""Write the assembled dataset in the shape the user asked for (section 14B)."""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..spec import TargetSpec
from .db import now
from .normalize import Dataset, NormalFormError, Table, build_dataset

SQL_TYPES = {str: "TEXT", int: "INTEGER", float: "REAL", type(None): "TEXT"}


@dataclass
class ExportResult:
    target: str
    format: str
    path: Path
    tables: int
    rows: int
    normal_form: str
    files: list[Path]


def export_target(conn, spec, target: TargetSpec, root: Path) -> ExportResult:
    dataset = build_dataset(
        conn, spec, normal_form=target.normal_form, mode=target.mode,
        include_derived=target.include_derived and spec.output.include_derived,
        min_confidence=target.min_confidence, provenance=target.provenance,
        naming=target.naming, provenance_fields=target.provenance_fields or None)
    if not dataset.passed:
        raise NormalFormError(
            f"target '{target.name}' does not pass its {dataset.normal_form} check:\n  - "
            + "\n  - ".join(dataset.problems)
            + "\nNothing was written. Fix the schema or choose another level.")

    if target.where:
        _apply_where(conn, dataset, target, spec)
    if target.multi_value != "child_table":
        _apply_multi_value(dataset, target)
    dataset = _apply_design(dataset, target, spec)
    if target.shape == "long":
        dataset = _reshape_long(dataset, spec)
    _sort(dataset, target)
    path = (root / target.path).resolve()
    writer = {"csv": _write_csv, "json": _write_json, "sqlite": _write_sqlite,
              "xlsx": _write_xlsx, "sql": _write_sql, "parquet": _write_parquet,
              "template": _write_template}.get(target.format)
    if writer is None:
        raise NormalFormError(f"format '{target.format}' is not supported yet "
                              f"- use csv, json, sqlite, xlsx or sql")
    files = writer(dataset, path, target, spec)
    rows = sum(len(t.rows) for t in dataset.tables)
    _write_metadata(conn, spec, dataset, target, path, files)
    return ExportResult(target.name, target.format, path, len(dataset.tables), rows,
                        dataset.normal_form, files)


def export_all(conn, spec, root: Path, only: str | None = None) -> list[ExportResult]:
    targets = list(spec.output.targets)
    if not targets:
        targets = [TargetSpec(name=fmt, format=fmt,
                              path=f"exports/{spec.project}.{_suffix(fmt)}"
                                   if fmt != "csv" else "exports/csv")
                   for fmt in (spec.output.formats or ["csv"])]
    if only:
        targets = [t for t in targets if t.name == only or t.format == only]
        if not targets:
            raise NormalFormError(f"no export target called '{only}'")
    return [export_target(conn, spec, t, root) for t in targets]


def _matches(cell, wanted) -> bool:
    options = wanted if isinstance(wanted, list) else [wanted]
    return str(cell).strip().lower() in {str(o).strip().lower() for o in options}


def _apply_where(conn, dataset: Dataset, target: TargetSpec, spec) -> None:
    """Keep only the rows matching `where`, and only what belongs to them.

    A row filter that left the other tables alone would write junction and
    provenance rows for records the file no longer has.
    """
    survivors: dict[str, set] = {}
    for column, wanted in target.where.items():
        for table in dataset.tables:
            if table.kind != "entity" or column not in table.columns:
                continue
            index = table.columns.index(column)
            table.rows = [r for r in table.rows if _matches(r[index], wanted)]
    for table in dataset.tables:
        if table.kind == "entity" and table.key:
            survivors[table.key[0]] = {r[table.columns.index(table.key[0])]
                                       for r in table.rows}
    names: dict[str, set] = {}
    for key, ids in survivors.items():
        entity_type = key[:-3] if key.endswith("_id") else key
        marks = ",".join("?" for _ in ids) or "NULL"
        names[entity_type] = {r["canonical_name"] for r in conn.execute(
            f"SELECT canonical_name FROM entities WHERE type=? AND id IN ({marks})",
            (entity_type, *ids))}
    for table in dataset.tables:
        if table.kind == "provenance":
            index = table.columns.index("entity")
            typ = table.columns.index("entity_type")
            table.rows = [r for r in table.rows
                          if r[typ] not in names or r[index] in names[r[typ]]]
        elif table.kind in ("junction", "attribute"):
            for key, ids in survivors.items():
                if key in table.columns:
                    index = table.columns.index(key)
                    table.rows = [r for r in table.rows if r[index] in ids]


def _apply_multi_value(dataset: Dataset, target: TargetSpec) -> None:
    """`multi_value: joined | rows`: fold a many-valued field's child table
    back into its record, either as one cell or as repeated rows."""
    for child in [t for t in dataset.tables if t.kind == "attribute"]:
        key, value_column = child.columns[0], child.columns[1]
        parent = next((t for t in dataset.tables
                       if t.kind == "entity" and t.key and t.key[0] == key), None)
        if parent is None:
            continue
        values: dict = {}
        for row in child.rows:
            values.setdefault(row[0], []).append(row[1])
        index = parent.columns.index(key)
        parent.columns = parent.columns + [value_column]
        rows = []
        for row in parent.rows:
            found = values.get(row[index]) or [None]
            if target.multi_value == "joined":
                rows.append(list(row) + ["; ".join(str(v) for v in found if v is not None)
                                         or None])
            else:
                rows.extend(list(row) + [v] for v in found)
        parent.rows = rows
        dataset.tables.remove(child)


def _convert_columns(table, conversions: dict, spec) -> None:
    """A target may ask for a column in a different unit from the stored one."""
    from ..standardize.units import UnitError, convert, parse_value
    for column, (field_name, wanted) in conversions.items():
        index = table.columns.index(column)
        stored = None
        for ent in spec.entities.values():
            if field_name in ent.fields and ent.fields[field_name].unit:
                stored = ent.fields[field_name].unit
                break
        if not stored or stored.lower() == str(wanted).lower():
            continue
        for row in table.rows:
            if row[index] in (None, ""):
                continue
            low, high, _unit = parse_value(row[index], stored)
            if low is None:
                continue
            try:
                low = round(convert(low, stored, wanted), 2)
                high = round(convert(high, stored, wanted), 2) if high is not None else None
            except UnitError:
                continue
            row[index] = f"{low}-{high}" if high is not None else low


def _suffix(fmt: str) -> str:
    return {"sqlite": "db", "json": "json", "xlsx": "xlsx", "sql": "sql",
            "template": "txt"}.get(fmt, fmt)


def _apply_design(dataset: Dataset, target: TargetSpec, spec) -> Dataset:
    """Column choice, labels and order from the target's `columns:` block."""
    if not target.columns:
        return dataset
    from ..standardize.names import case_style
    for table in dataset.tables:
        if table.kind == "provenance":
            continue
        # a `columns:` block names schema fields; the table may already carry
        # the target's naming style
        keep, conversions = [], {}
        for entry in target.columns:
            if not isinstance(entry, dict) or not entry.get("field"):
                continue
            field_name = entry["field"]
            label = entry.get("label") or field_name
            for candidate in (field_name, case_style(field_name, target.naming)):
                if candidate in table.columns:
                    keep.append((candidate, label))
                    if entry.get("unit"):
                        conversions[candidate] = (field_name, entry["unit"])
                    break
        _convert_columns(table, conversions, spec)
        if not keep:
            continue
        key_columns = [c for c in table.columns if c.endswith("_id") and c in table.key]
        indexes = [table.columns.index(c) for c in key_columns] + \
                  [table.columns.index(f) for f, _ in keep]
        labels = key_columns + [l for _, l in keep]
        table.rows = [[row[i] for i in indexes] for row in table.rows]
        table.columns = labels
    return dataset


def _reshape_long(dataset: Dataset, spec) -> Dataset:
    """One row per record, field and value (section 14B, "Shape").

    Any provenance columns travel with their value instead of spreading the
    table sideways, which is the reason to ask for long in the first place.
    """
    reshaped = []
    for table in dataset.tables:
        if table.kind not in ("entity", "attribute") or not table.columns:
            reshaped.append(table)
            continue
        key = table.columns[0]
        extras = [s for s in ("confidence", "origin", "source")
                  if any(c.endswith(f"_{s}") for c in table.columns)]
        fields = [c for c in table.columns[1:]
                  if not c.endswith(("_confidence", "_origin", "_source"))]
        columns = [key, "field", "value"] + extras
        rows = []
        for record in table.as_dicts():
            for name in fields:
                value = record.get(name)
                if value is None:
                    continue
                row = [record.get(key), name, value]
                row += [record.get(f"{name}_{extra}") for extra in extras]
                rows.append(row)
        reshaped.append(Table(table.name, columns, rows, key=[key, "field"],
                              kind=table.kind, derived=table.derived))
    return Dataset(reshaped, dataset.normal_form, dataset.mode, dataset.passed,
                   dataset.problems)


def _sort(dataset: Dataset, target) -> None:
    """`sort_by` names a column; tables without it keep their own order."""
    if not target.sort_by:
        return
    for table in dataset.tables:
        if target.sort_by not in table.columns:
            continue
        index = table.columns.index(target.sort_by)

        def key(row, index=index):
            value = row[index]
            return (value is None, str(value) if not isinstance(value, (int, float))
                    else f"{value:020.4f}")

        table.rows.sort(key=key, reverse=target.descending)


# ------------------------------------------------------------------ writers
def _write_csv(dataset: Dataset, path: Path, target, spec) -> list[Path]:
    if target.split == "single":              # one file for everything
        return _write_single_csv(dataset, path, target, spec)
    path.mkdir(parents=True, exist_ok=True)
    files = []
    for table in dataset.tables:
        file_path = path / f"{table.name}.csv"
        with open(file_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(table.columns)
            writer.writerows(table.rows)
        files.append(file_path)
    return files


def _write_single_csv(dataset: Dataset, path: Path, target, spec) -> list[Path]:
    """Every table stacked into one file, with a `table` column to tell them apart."""
    if path.is_dir() or not path.suffix:
        path = path / f"{spec.project}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    columns: list[str] = ["table"]
    for table in dataset.tables:
        for column in table.columns:
            if column not in columns:
                columns.append(column)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for table in dataset.tables:
            for record in table.as_dicts():
                writer.writerow({"table": table.name, **record})
    return [path]


def _write_parquet(dataset: Dataset, path: Path, target, spec) -> list[Path]:
    """Columnar files, one per table, for large datasets and data science."""
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet as parquet
        from pyarrow import Table as ArrowTable
    except ImportError as exc:
        raise NormalFormError(
            "Parquet export needs pyarrow (pip install pyarrow)") from exc
    path.mkdir(parents=True, exist_ok=True)
    files = []
    for table in dataset.tables:
        rows = table.as_dicts()
        data = {column: [row.get(column) for row in rows] for column in table.columns}
        for column, values in data.items():       # one type per column
            if any(v is not None and not isinstance(v, (int, float)) for v in values):
                data[column] = [None if v is None else str(v) for v in values]
        file_path = path / f"{table.name}.parquet"
        parquet.write_table(ArrowTable.from_pydict(data), file_path)
        files.append(file_path)
    return files


def _write_template(dataset: Dataset, path: Path, target, spec) -> list[Path]:
    """A user-written Jinja template, for Markdown, XML or any other text."""
    try:
        from jinja2 import Environment, StrictUndefined
    except ImportError as exc:
        raise NormalFormError(
            "template export needs Jinja2 (pip install jinja2)") from exc
    root = spec.path.parent if spec.path else Path.cwd()
    template_path = Path(target.template)
    if not template_path.is_absolute():
        template_path = root / template_path
    if not template_path.exists():
        raise NormalFormError(f"template not found: {target.template}")

    environment = Environment(undefined=StrictUndefined, autoescape=False,
                              trim_blocks=True, lstrip_blocks=True)
    template = environment.from_string(template_path.read_text(encoding="utf-8"))
    rendered = template.render(
        project=spec.project, built_at=now(), normal_form=dataset.normal_form,
        mode=dataset.mode,
        tables={t.name: t.as_dicts() for t in dataset.tables},
        columns={t.name: t.columns for t in dataset.tables})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return [path]


def _write_json(dataset: Dataset, path: Path, target, spec) -> list[Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if target.nesting == "nested":
        payload = _nest(dataset, spec, target)
    else:
        payload = {t.name: t.as_dicts() for t in dataset.tables}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8")
    return [path]


def _nest(dataset: Dataset, spec, target) -> dict:
    """Nested JSON: a plant carries its regions and uses inside it."""
    from ..standardize.names import case_style

    def column(table, name: str) -> str:
        """The column as this target spells it (targets may rename columns)."""
        if table is None:
            return name
        styled = case_style(name, target.naming)
        return styled if styled in table.columns else name

    primary = next(iter(spec.entities))
    main = dataset.table(primary)
    if main is None:
        return {t.name: t.as_dicts() for t in dataset.tables}
    id_column = column(main, f"{primary}_id")
    records = []
    for record in main.as_dicts():
        entity_id = record.get(id_column)
        for rel in spec.relations:
            if rel.from_entity != primary:
                continue
            junction = dataset.table(f"{primary}_{rel.name}_{rel.to_entity}")
            other = dataset.table(rel.to_entity)
            if junction is None or other is None:
                continue
            other_id = column(other, f"{rel.to_entity}_id")
            junction_other = column(junction, f"{rel.to_entity}_id")
            junction_primary = column(junction, f"{primary}_id")
            other_by_id = {r[other_id]: r for r in other.as_dicts()}
            linked = [other_by_id[j[junction_other]]
                      for j in junction.as_dicts()
                      if j.get(junction_primary) == entity_id
                      and j.get(junction_other) in other_by_id]
            record[case_style(rel.name, target.naming)] = linked
        records.append(record)
    payload = {case_style(primary, target.naming): records}
    provenance = dataset.table("provenance")
    if provenance is not None:
        payload["provenance"] = provenance.as_dicts()
    return payload


def _write_sqlite(dataset: Dataset, path: Path, target, spec) -> list[Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    out = sqlite3.connect(path)
    entity_tables = {t.name: t for t in dataset.tables if t.kind == "entity"}
    for table in dataset.tables:
        columns = ", ".join(f'"{c}" {_sql_type(table, i)}'
                            for i, c in enumerate(table.columns))
        key = (f', PRIMARY KEY ({", ".join(chr(34)+k+chr(34) for k in table.key)})'
               if table.key and table.kind in ("junction", "entity") else "")
        # real foreign keys, so the file is a database an app can trust and
        # another Spider project can import with `describe --from`
        foreign = ""
        if table.kind == "junction":
            for column in table.columns:
                if not column.endswith("_id"):
                    continue
                parent = column[:-3]
                if parent in entity_tables and entity_tables[parent].key:
                    foreign += (f', FOREIGN KEY ("{column}") REFERENCES '
                                f'"{parent}"("{entity_tables[parent].key[0]}")')
        out.execute(f'CREATE TABLE "{table.name}" ({columns}{key}{foreign})')
        marks = ",".join("?" for _ in table.columns)
        out.executemany(f'INSERT OR REPLACE INTO "{table.name}" VALUES({marks})',
                        table.rows)
        for column in table.columns:
            if column.endswith("_id") and column not in table.key:
                out.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table.name}_{column}" '
                            f'ON "{table.name}"("{column}")')
    out.commit()
    out.close()
    return [path]


def _sql_type(table, index: int) -> str:
    for row in table.rows:
        value = row[index]
        if value is not None:
            return SQL_TYPES.get(type(value), "TEXT")
    return "TEXT"


def _write_xlsx(dataset: Dataset, path: Path, target, spec) -> list[Path]:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:
        raise NormalFormError("Excel export needs openpyxl "
                              "(pip install 'project-spider[files]')") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    book.remove(book.active)
    for table in dataset.tables:
        sheet = book.create_sheet(table.name[:31])
        sheet.append(table.columns)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in table.rows:
            sheet.append(["" if v is None else v for v in row])
        sheet.freeze_panes = "A2"
        for index, column in enumerate(table.columns, start=1):
            width = max([len(str(column))] +
                        [len(str(r[index - 1] or "")) for r in table.rows[:200]] or [10])
            sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = \
                min(max(width + 2, 10), 60)
    book.save(path)
    return [path]


def _write_sql(dataset: Dataset, path: Path, target, spec) -> list[Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"-- {spec.project}: {dataset.normal_form}, written {now()}"]
    for table in dataset.tables:
        columns = ",\n  ".join(f'"{c}" TEXT' for c in table.columns)
        lines.append(f'CREATE TABLE "{table.name}" (\n  {columns}\n);')
        for row in table.rows:
            values = ", ".join("NULL" if v is None else "'" + str(v).replace("'", "''") + "'"
                               for v in row)
            lines.append(f'INSERT INTO "{table.name}" VALUES ({values});')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [path]


# ----------------------------------------------------------------- metadata
# What each outside service's data is published under. Spider states it; it
# does not decide it - check the service's own terms before you rely on it.
SERVICE_TERMS = {
    "gbif": ("GBIF Species API", "https://www.gbif.org/terms", "CC0 / CC BY, per dataset"),
    "wikidata": ("Wikidata", "https://www.wikidata.org/wiki/Wikidata:Licensing", "CC0"),
    "elevation": ("Open-Elevation / Open-Meteo elevation", "https://open-meteo.com/en/terms",
                  "CC BY 4.0 (Open-Meteo) - attribution required"),
    "geocode": ("OpenStreetMap Nominatim", "https://osm.org/copyright",
                "ODbL - (c) OpenStreetMap contributors"),
    "lgd": ("Local Government Directory (data.gov.in)", "https://data.gov.in/",
            "Government Open Data License - India"),
}
SOURCE_TERMS = {"osm": ("OpenStreetMap (Overpass)", "https://osm.org/copyright",
                        "ODbL - (c) OpenStreetMap contributors")}


def _attribution(conn, spec) -> list[dict]:
    """Every outside service used, with the terms that apply to its data (section 15)."""
    out, seen = [], set()

    def add(name, url, terms, used_for):
        if name not in seen:
            seen.add(name)
            out.append({"service": name, "terms_url": url, "licence": terms,
                        "used_for": used_for})

    for entry in spec.connectors:
        key = str((entry.get("name") if isinstance(entry, dict) else entry) or "").lower()
        if key in SERVICE_TERMS:
            add(*SERVICE_TERMS[key], "connector")
    for item in spec.sources.items:
        if item.type in SOURCE_TERMS:
            add(*SOURCE_TERMS[item.type], f"source {item.id}")
        if item.license:
            add(item.id, item.location, item.license, f"source {item.id}")
    return out
def _write_metadata(conn, spec, dataset, target, path, files) -> None:
    folder = path if path.is_dir() else path.parent
    folder.mkdir(parents=True, exist_ok=True)
    domains = [dict(r) for r in conn.execute(
        "SELECT domain, COUNT(*) pages FROM pages GROUP BY domain ORDER BY pages DESC")]
    metadata = {
        "project": spec.project,
        "built_at": now(),
        "target": target.name,
        "format": target.format,
        "mode": dataset.mode,
        "normal_form": dataset.normal_form,
        "passed_normal_form_check": dataset.passed,
        "standardize": {"level": spec.standardize.level,
                        "dates": spec.standardize.dates,
                        "currency": spec.standardize.currency,
                        "on_conflict": spec.standardize.on_conflict,
                        "min_confidence": spec.standardize.min_confidence},
        "include_derived": target.include_derived,
        "origin_labels": spec.output.label_origin,
        "tables": {t.name: len(t.rows) for t in dataset.tables},
        "sources": domains,
        "connectors": [c.get("name") for c in spec.connectors if isinstance(c, dict)],
        "attribution": _attribution(conn, spec),
        "files": [str(f.relative_to(folder)) if f.is_relative_to(folder) else str(f)
                  for f in files],
        "note": ("Values are labelled extracted, derived or inferred. Every extracted "
                 "value carries its source URL and the sentence that states it."),
    }
    (folder / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    readme = [
        f"# {spec.project} - dataset export",
        "",
        f"Built by Project Spider on {metadata['built_at']}.",
        f"Mode: {dataset.mode}. Structure: {dataset.normal_form} "
        f"(checked and passed: {dataset.passed}).",
        f"Values cleaned at level '{spec.standardize.level}'; conflicts resolved by "
        f"'{spec.standardize.on_conflict}'.",
        "",
        "## Tables",
        "",
    ]
    readme += [f"- `{t.name}` - {len(t.rows)} rows, columns: {', '.join(t.columns)}"
               for t in dataset.tables]
    readme += [
        "",
        "## How to read it",
        "",
        "- Every value has a row in `provenance` with its source URL and the exact "
        "sentence it came from.",
        "- `origin` says whether a value was extracted from a page, derived by a "
        "formula, or inferred by AI (inferred values are capped low and flagged).",
        "- `confidence` follows source agreement: more independent sites agreeing "
        "means a higher score.",
        "",
        "## Sources",
        "",
    ]
    readme += [f"- {d['domain']} ({d['pages']} pages)" for d in domains[:25]]
    if metadata["attribution"]:
        readme += ["", "## Services used and their terms", ""]
        readme += [f"- {a['service']}: {a['licence']} ({a['terms_url']})"
                   for a in metadata["attribution"]]
    readme += ["", "Check each source's terms before republishing its content."]
    (folder / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
