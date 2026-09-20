"""Build the output tables at the level the user chose, and check them (D8).

The canonical copy (entities / attributes / relations) stays in 6NF, so any
level can be rebuilt from it without re-crawling. Every table set is checked
against its level and a build that fails its check is not written.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..spec import NORMAL_FORMS, PROJECT_MIN
from ..standardize.names import case_style


@dataclass
class Table:
    name: str
    columns: list[str]
    rows: list[list]
    key: list[str] = field(default_factory=list)
    kind: str = "entity"        # entity | junction | attribute | lookup | provenance
    derived: list[str] = field(default_factory=list)
    """Columns calculated by a derivation - a computed projection, not stored
    redundancy, so the 3NF check leaves them out of its dependency search."""

    def as_dicts(self) -> list[dict]:
        return [dict(zip(self.columns, row)) for row in self.rows]


@dataclass
class Dataset:
    tables: list[Table]
    normal_form: str
    mode: str
    passed: bool = True
    problems: list[str] = field(default_factory=list)

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name == name), None)


class NormalFormError(Exception):
    pass


# columns that describe a value rather than being data of their own; 6NF
# tables carry them on purpose (D8, "role of 6NF")
VALUE_METADATA = {"unit", "origin", "confidence", "valid_from", "valid_to",
                  "source_url", "evidence", "fetched_at"}
METADATA_SUFFIXES = ("_confidence", "_origin", "_source", "_evidence")


def is_metadata(column: str) -> bool:
    return column in VALUE_METADATA or column.endswith(METADATA_SUFFIXES)


# --------------------------------------------------------------- read canon
def _entities(conn, entity_type: str):
    """Records with at least one accepted value.

    A record whose every value is still in the review queue is not part of
    the dataset yet: it stays in `spider report` until a value is accepted,
    rather than appearing as an empty row.
    """
    return conn.execute(
        "SELECT id, canonical_name FROM entities WHERE type=? AND id IN "
        "(SELECT entity_id FROM attributes WHERE status='accepted') "
        "ORDER BY canonical_name", (entity_type,)).fetchall()


def _page_urls(conn) -> dict:
    return {r["id"]: r["url"] for r in conn.execute("SELECT id, url FROM pages")}


def _accepted_ids(conn) -> set:
    return {r["entity_id"] for r in conn.execute(
        "SELECT DISTINCT entity_id FROM attributes WHERE status='accepted'")}


def _values(conn, entity_id: int, include_derived=True, min_confidence=None):
    query = ("SELECT name, value, value_num, value_max, unit, origin, confidence, "
             "tier, source_page, evidence FROM attributes "
             "WHERE entity_id=? AND status='accepted'")
    rows = conn.execute(query, (entity_id,)).fetchall()
    out: dict[str, list] = {}
    for row in rows:
        if not include_derived and row["origin"] in ("derived", "inferred", "default"):
            continue
        if min_confidence is not None and (row["confidence"] or 0) < min_confidence:
            continue
        out.setdefault(row["name"], []).append(row)
    return out


def _display(row, spec, entity_type: str):
    """One cell as text: ranges keep both ends, numbers stay numbers."""
    fld = spec.entity_field(entity_type, row["name"])
    if row["value_num"] is not None and row["value_max"] is not None:
        return f"{_num(row['value_num'])}-{_num(row['value_max'])}"
    if row["value_num"] is not None and (fld and fld.type in ("number", "range", "year")):
        return _num(row["value_num"])
    return row["value"]


def _num(value):
    """Keep a number a number for a table, but never print a float's noise."""
    if value is None:
        return None
    number = float(value)
    size = abs(number)
    from ..standardize.units import BIG, SMALL, format_number
    if size != 0 and (size >= BIG or size < SMALL):
        return format_number(number)          # text, so the exponent survives
    return int(number) if number.is_integer() else round(number, 6)


# ------------------------------------------------------------------ builders
def build_dataset(conn, spec, *, normal_form=None, mode=None, include_derived=True,
                  min_confidence=None, provenance="separate_table",
                  naming="snake_case") -> Dataset:
    level = (normal_form or spec.storage.normal_form).upper()
    mode = mode or spec.mode
    if level not in NORMAL_FORMS:
        raise NormalFormError(f"unknown normal form '{level}'")
    if mode == "project" and NORMAL_FORMS.index(level) < PROJECT_MIN:
        raise NormalFormError(
            f"{level} needs analysis mode - project mode stores 3NF or higher. "
            f"Set `mode: analysis` on the target, or raise the level.")

    builder = {
        "0NF": _build_flat, "1NF": _build_1nf, "2NF": _build_2nf, "3NF": _build_3nf,
        "BCNF": _build_3nf, "4NF": _build_4nf, "5NF": _build_4nf, "6NF": _build_6nf,
    }[level]
    with_columns = (provenance == "columns")
    tables = builder(conn, spec, include_derived, min_confidence,
                     provenance_columns=with_columns)

    if spec.storage.keep_provenance and provenance == "separate_table":
        tables.append(_provenance_table(conn, spec, include_derived))

    dataset = Dataset(tables=tables, normal_form=level, mode=mode)
    # the structure is checked first: a display name must never change the
    # answer to "is this 3NF?"
    dataset.passed, dataset.problems = check_level(dataset, level)

    if naming != "snake_case":
        for table in dataset.tables:
            table.columns = [case_style(c, naming) for c in table.columns]
            table.key = [case_style(c, naming) for c in table.key]
            table.derived = [case_style(c, naming) for c in table.derived]
    return dataset


def field_names(spec, entity_type: str, include_derived: bool = True) -> list[str]:
    """Every column of an entity, in schema order, each appearing once.

    A field can be declared in the schema and also filled by a derivation
    (that is how an approved suggestion works), so the two lists overlap.
    """
    ent = spec.entities.get(entity_type)
    names = list(ent.fields) if ent else []
    if include_derived:
        names += [d.name for d in spec.derived.values()
                  if d.on == entity_type and d.name not in names]
    return names


def derived_names(spec, entity_type: str, include_derived: bool = True) -> list[str]:
    return ([d.name for d in spec.derived.values() if d.on == entity_type]
            if include_derived else [])


def _multi_fields(spec, entity_type: str) -> list[str]:
    ent = spec.entities.get(entity_type)
    return [n for n, f in ent.fields.items() if f.multiple] if ent else []


def _best(rows):
    """The value a table shows when sources disagree (see `pick_best`).

    Nothing is lost: every value stays in `attributes` and in the provenance
    table, and the conflict stays in the review queue.
    """
    from ..assemble.confidence import pick_best
    return pick_best(rows)


def _single_row(conn, spec, entity_type, entity, include_derived, min_confidence,
                allow_multi_cell=False, provenance_columns=False, urls=None):
    """One output row.

    `allow_multi_cell` is for 0NF, where repeats may share a cell.
    `provenance_columns` adds `<field>_confidence`, `<field>_origin` and
    `<field>_source` beside each value (section 14B, "Extras").
    """
    values = _values(conn, entity["id"], include_derived, min_confidence)
    fields = field_names(spec, entity_type, include_derived)
    columns, row = [f"{entity_type}_id"], [entity["id"]]
    for name in fields:
        rows = values.get(name, [])
        columns.append(name)
        if not rows:
            row.append(None)
            chosen = None
        elif allow_multi_cell and len(rows) > 1:
            row.append("; ".join(str(_display(r, spec, entity_type)) for r in rows))
            chosen = _best(rows)
        else:
            chosen = rows[0] if len(rows) == 1 else _best(rows)
            row.append(_display(chosen, spec, entity_type))
        if provenance_columns:
            columns += [f"{name}_confidence", f"{name}_origin", f"{name}_source"]
            row += [chosen["confidence"] if chosen else None,
                    chosen["origin"] if chosen else None,
                    (urls or {}).get(chosen["source_page"]) if chosen else None]
    return columns[1:], row


def _build_flat(conn, spec, include_derived, min_confidence,
                provenance_columns=False) -> list[Table]:
    """0NF: one wide table, repeated values joined into the cell."""
    primary = next(iter(spec.entities))
    ent_rows = _entities(conn, primary)
    related_types = [r.to_entity for r in spec.relations if r.from_entity == primary]
    columns, rows = None, []
    for entity in ent_rows:
        names, row = _single_row(conn, spec, primary, entity, include_derived,
                                 min_confidence, allow_multi_cell=True,
                                 provenance_columns=provenance_columns,
                                 urls=_page_urls(conn) if provenance_columns else {})
        extra_names, extra = [], []
        for rel in spec.relations:
            if rel.from_entity != primary:
                continue
            linked = conn.execute(
                "SELECT e.canonical_name n FROM relations r JOIN entities e "
                "ON e.id=r.to_entity WHERE r.from_entity=? AND r.relation=? "
                "AND e.id IN (SELECT entity_id FROM attributes WHERE status='accepted')",
                (entity["id"], rel.name)).fetchall()
            extra_names.append(rel.to_entity if rel.to_entity not in extra_names
                               else f"{rel.to_entity}_{rel.name}")
            extra.append("; ".join(sorted({r["n"] for r in linked})) or None)
        columns = [f"{primary}_id"] + names + extra_names
        rows.append(row + extra)
    del related_types
    return [Table(f"{primary}_flat", columns or [f"{primary}_id"], rows,
                  key=[f"{primary}_id"], kind="entity",
                  derived=derived_names(spec, primary, include_derived))]


def _build_1nf(conn, spec, include_derived, min_confidence,
               provenance_columns=False) -> list[Table]:
    """1NF: every cell holds one value, so repeats become extra rows."""
    primary = next(iter(spec.entities))
    tables = _build_flat(conn, spec, include_derived, min_confidence,
                         provenance_columns=provenance_columns)
    flat = tables[0]
    multi_columns = [c for c in flat.columns
                     if any(isinstance(r[flat.columns.index(c)], str)
                            and "; " in str(r[flat.columns.index(c)]) for r in flat.rows)]
    rows = []
    for row in flat.rows:
        expanded = [row]
        for column in multi_columns:
            index = flat.columns.index(column)
            next_rows = []
            for current in expanded:
                cell = current[index]
                parts = str(cell).split("; ") if isinstance(cell, str) and "; " in cell else [cell]
                for part in parts:
                    copy = list(current)
                    copy[index] = part
                    next_rows.append(copy)
            expanded = next_rows
        rows.extend(expanded)
    return [Table(f"{primary}_1nf", flat.columns, rows, key=flat.key, kind="entity",
                  derived=flat.derived)]


def _build_2nf(conn, spec, include_derived, min_confidence,
               provenance_columns=False) -> list[Table]:
    """2NF: related entities move to their own tables, keeping a joined key."""
    tables = _entity_tables(conn, spec, include_derived, min_confidence,
                            provenance_columns=provenance_columns)
    primary = next(iter(spec.entities))
    for rel in spec.relations:
        if rel.from_entity != primary:
            continue
        known = _accepted_ids(conn)
        rows = []
        for link in conn.execute(
                "SELECT r.from_entity a, r.to_entity id, e.canonical_name b "
                "FROM relations r JOIN entities e ON e.id=r.to_entity "
                "WHERE r.relation=?", (rel.name,)):
            if link["a"] in known and link["id"] in known:
                rows.append([link["a"], link["b"]])
        tables.append(Table(f"{primary}_{rel.name}", [f"{primary}_id", rel.to_entity],
                            rows, key=[f"{primary}_id", rel.to_entity], kind="junction"))
    return tables


def _entity_tables(conn, spec, include_derived, min_confidence,
                   skip_multi=False, provenance_columns=False) -> list[Table]:
    tables = []
    urls = _page_urls(conn) if provenance_columns else {}
    for entity_type in spec.entities:
        columns, rows = None, []
        multi = set(_multi_fields(spec, entity_type)) if skip_multi else set()
        for entity in _entities(conn, entity_type):
            names, row = _single_row(conn, spec, entity_type, entity, include_derived,
                                     min_confidence,
                                     provenance_columns=provenance_columns, urls=urls)
            keep = [i for i, n in enumerate(names)
                    if n.split("_confidence")[0].split("_origin")[0]
                    .split("_source")[0] not in multi]
            columns = [f"{entity_type}_id"] + [names[i] for i in keep]
            rows.append([row[0]] + [row[i + 1] for i in keep])
        if columns is None:
            columns = [f"{entity_type}_id"] + [
                n for n in field_names(spec, entity_type, include_derived)
                if n not in multi]
        tables.append(Table(entity_type, columns, rows, key=[f"{entity_type}_id"],
                            kind="entity",
                            derived=derived_names(spec, entity_type, include_derived)))
    return tables


def _junction_tables(conn, spec) -> list[Table]:
    tables = []
    known = _accepted_ids(conn)
    for rel in spec.relations:
        rows = [[r["a"], r["b"]] for r in conn.execute(
            "SELECT from_entity a, to_entity b FROM relations WHERE relation=? "
            "GROUP BY from_entity, to_entity", (rel.name,))
            if r["a"] in known and r["b"] in known]
        name = f"{rel.from_entity}_{rel.name}_{rel.to_entity}"
        tables.append(Table(name, [f"{rel.from_entity}_id", f"{rel.to_entity}_id"],
                            rows, key=[f"{rel.from_entity}_id", f"{rel.to_entity}_id"],
                            kind="junction"))
    return tables


def _build_3nf(conn, spec, include_derived, min_confidence,
               provenance_columns=False) -> list[Table]:
    """3NF/BCNF: one table per entity, plus junction tables for relations.

    A field marked `multiple` gets its own child table even at 3NF, because
    1NF already forbids two values sharing a cell.
    """
    return _build_4nf(conn, spec, include_derived, min_confidence,
                      provenance_columns=provenance_columns)


def _build_4nf(conn, spec, include_derived, min_confidence,
               provenance_columns=False) -> list[Table]:
    """4NF/5NF: independent many-valued facts get a table of their own."""
    tables = _entity_tables(conn, spec, include_derived, min_confidence,
                            skip_multi=True, provenance_columns=provenance_columns)
    for entity_type in spec.entities:
        for field_name in _multi_fields(spec, entity_type):
            rows = [[r["entity_id"], r["value"]] for r in conn.execute(
                "SELECT a.entity_id, a.value FROM attributes a JOIN entities e "
                "ON e.id=a.entity_id WHERE e.type=? AND a.name=? AND a.status='accepted' "
                "GROUP BY a.entity_id, a.value", (entity_type, field_name))]
            tables.append(Table(f"{entity_type}_{field_name}",
                                [f"{entity_type}_id", field_name], rows,
                                key=[f"{entity_type}_id", field_name], kind="attribute"))
    return tables + _junction_tables(conn, spec)


def _build_6nf(conn, spec, include_derived, min_confidence,
               provenance_columns=False) -> list[Table]:
    """6NF: one table per attribute, each value with its validity span."""
    tables = []
    for entity_type in spec.entities:
        ids = [r["id"] for r in _entities(conn, entity_type)]
        tables.append(Table(entity_type, [f"{entity_type}_id", "canonical_name"],
                            [[r["id"], r["canonical_name"]]
                             for r in _entities(conn, entity_type)],
                            key=[f"{entity_type}_id"], kind="entity"))
        for field_name in field_names(spec, entity_type, include_derived):
            rows = []
            for row in conn.execute(
                    "SELECT entity_id, value, value_num, value_max, unit, origin, "
                    "confidence, valid_from, valid_to FROM attributes "
                    "WHERE name=? AND status='accepted'", (field_name,)):
                if row["entity_id"] not in ids:
                    continue
                if min_confidence is not None and (row["confidence"] or 0) < min_confidence:
                    continue
                value = (f"{_num(row['value_num'])}-{_num(row['value_max'])}"
                         if row["value_max"] is not None else row["value"])
                rows.append([row["entity_id"], value, row["unit"], row["origin"],
                             row["confidence"], row["valid_from"], row["valid_to"]])
            tables.append(Table(
                f"{entity_type}_{field_name}",
                [f"{entity_type}_id", field_name, "unit", "origin", "confidence",
                 "valid_from", "valid_to"], rows,
                key=[f"{entity_type}_id", "valid_from"], kind="attribute"))
    return tables + _junction_tables(conn, spec)


def _provenance_table(conn, spec, include_derived) -> Table:
    columns = ["entity_type", "entity", "field", "value", "origin", "confidence",
               "source_url", "evidence", "fetched_at"]
    rows = []
    for row in conn.execute(
            "SELECT e.type t, e.canonical_name n, a.name f, a.value v, a.origin o, "
            "a.confidence c, a.evidence q, p.url u, p.fetched_at d "
            "FROM attributes a JOIN entities e ON e.id=a.entity_id "
            "LEFT JOIN pages p ON p.id=a.source_page WHERE a.status='accepted' "
            "ORDER BY e.type, e.canonical_name, a.name"):
        if not include_derived and row["o"] in ("derived", "inferred", "default"):
            continue
        rows.append([row["t"], row["n"], row["f"], row["v"], row["o"], row["c"],
                     row["u"], (row["q"] or "")[:300], row["d"]])
    return Table("provenance", columns, rows, kind="provenance")


# -------------------------------------------------------------- level checks
def check_level(dataset: Dataset, level: str) -> tuple[bool, list[str]]:
    """The automatic check each output must pass before it is written (D8)."""
    problems: list[str] = []
    tables = [t for t in dataset.tables if t.kind != "provenance"]

    def rows_of(table):
        return [dict(zip(table.columns, row)) for row in table.rows]

    if level in ("1NF", "2NF", "3NF", "BCNF", "4NF", "5NF", "6NF"):
        for table in tables:                       # 1NF: one value per cell
            for record in rows_of(table):
                for column, value in record.items():
                    if isinstance(value, str) and "; " in value:
                        problems.append(
                            f"{table.name}.{column} holds more than one value "
                            f"('{value[:40]}') - not 1NF")
                        break
                else:
                    continue
                break
        for table in tables:                       # 1NF: rows are unique
            seen = set()
            for row in table.rows:
                marker = tuple(str(v) for v in row)
                if marker in seen:
                    problems.append(f"{table.name} has duplicate rows - not 1NF")
                    break
                seen.add(marker)

    if level in ("2NF", "3NF", "BCNF", "4NF", "5NF", "6NF"):
        for table in tables:                       # 2NF: no partial key dependency
            if len(table.key) > 1:
                non_key = [c for c in table.columns if c not in table.key]
                if non_key and table.kind == "junction":
                    problems.append(
                        f"{table.name} keeps {non_key} beside a composite key - not 2NF")

    if level in ("3NF", "BCNF", "4NF", "5NF", "6NF"):
        for table in tables:                       # 3NF: no transitive dependency
            problems.extend(_transitive_problems(table))

    if level in ("BCNF", "4NF", "5NF", "6NF"):
        for table in tables:
            problems.extend(_bcnf_problems(table))

    if level in ("4NF", "5NF", "6NF"):
        for table in tables:                       # 4NF: independent multi-valued facts
            multi = [c for c in table.columns if c.endswith("_id") and c not in table.key]
            if table.kind == "entity" and len(multi) > 1:
                problems.append(
                    f"{table.name} mixes independent many-valued facts {multi} - not 4NF")

    if level in ("5NF", "6NF"):
        for table in tables:
            problems.extend(_join_dependency_problems(table))

    if level == "6NF":
        for table in tables:
            payload = [c for c in table.columns
                       if c not in table.key and not is_metadata(c)]
            if table.kind == "entity" and len(payload) > 1:
                problems.append(
                    f"{table.name} has {len(payload)} attributes; 6NF allows one")
    return (not problems), problems


def _transitive_problems(table: Table) -> list[str]:
    """A non-key column that determines another non-key column breaks 3NF.

    A column whose values are unique is a candidate key, and a candidate key
    determining the rest of the row is exactly what 3NF asks for - so only
    columns that actually repeat can produce a transitive dependency.
    """
    problems = []
    non_key = [c for c in table.columns
               if c not in table.key and not c.endswith("_id")
               and c not in table.derived and not is_metadata(c)]
    if len(table.rows) < 3 or len(non_key) < 2:
        return problems
    index = {c: table.columns.index(c) for c in table.columns}

    def values_of(column):
        return [row[index[column]] for row in table.rows
                if row[index[column]] not in (None, "")]

    for left in non_key:
        left_values = values_of(left)
        distinct_left = set(left_values)
        if len(distinct_left) < 2 or len(distinct_left) == len(left_values):
            continue                      # unique, so it is a candidate key
        for right in non_key:
            if left == right:
                continue
            mapping, broken = {}, False
            for row in table.rows:
                a, b = row[index[left]], row[index[right]]
                if a in (None, "") or b in (None, ""):
                    continue
                if a in mapping and mapping[a] != b:
                    broken = True
                    break
                mapping[a] = b
            if not broken and len(mapping) >= 2 and len(set(mapping.values())) > 1:
                problems.append(
                    f"{table.name}: '{right}' looks determined by non-key '{left}' "
                    f"- not 3NF (move the pair to a lookup table)")
    return problems


def _bcnf_problems(table: Table) -> list[str]:
    """Detect observed non-superkey determinants in the emitted relation.

    Functional dependencies cannot be inferred perfectly from a finite data
    sample, but a repeated value that consistently determines another column
    is concrete evidence of a BCNF violation.  Unique values are candidate
    keys and therefore valid determinants.
    """
    if len(table.rows) < 3:
        return []
    payload = [c for c in table.columns if not is_metadata(c)]
    index = {c: table.columns.index(c) for c in table.columns}
    problems = []
    for determinant in payload:
        groups: dict[object, list[list]] = {}
        for row in table.rows:
            value = row[index[determinant]]
            if value not in (None, ""):
                groups.setdefault(value, []).append(row)
        # A determinant that is unique in the observed output is a candidate
        # key.  Only repeated values can demonstrate that it is not one.
        repeated = [rows for rows in groups.values() if len(rows) > 1]
        if not repeated:
            continue
        for dependent in payload:
            if dependent == determinant:
                continue
            pos = index[dependent]
            if all(len({row[pos] for row in rows}) == 1 for rows in repeated):
                problems.append(
                    f"{table.name}: '{determinant}' determines '{dependent}' "
                    "but is not a superkey - not BCNF")
    return problems


def _join_dependency_problems(table: Table) -> list[str]:
    """Conservatively check three-key junctions for a lossy 5NF split.

    A relation with three independent keys can be decomposed into its pair
    projections only when rejoining them recreates exactly the original
    tuples.  If those projections create a tuple that never existed, keeping
    the combined relation would hide a join dependency and it is not 5NF.
    Binary junctions are already irreducible for this purpose.
    """
    if table.kind != "junction" or len(table.key) != 3 or not table.rows:
        return []
    positions = [table.columns.index(key) for key in table.key]
    triples = {tuple(row[pos] for pos in positions) for row in table.rows}
    pairs = [{(triple[i], triple[j]) for triple in triples}
             for i, j in ((0, 1), (0, 2), (1, 2))]
    rebuilt = {(a, b, c) for a, b in pairs[0] for x, c in pairs[1]
               if x == a for y, z in pairs[2] if y == b and z == c}
    if rebuilt != triples:
        extras = len(rebuilt - triples)
        return [f"{table.name}: pairwise join creates {extras} spurious tuple(s) - not 5NF"]
    return []
