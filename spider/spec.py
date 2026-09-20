"""The `spider.yaml` project file: models, defaults and validation.

Two kinds of default live here: the value a setting takes when the file says
nothing (each one matches the design document), and a field's own `default:`,
which fills a cell nothing found and is stored as origin `default` with no
confidence, because it is the one value in the dataset with no source.

`spider check` (section 11, step 4) runs `Spec.validate()` and prints every
problem with the field that caused it and a suggested fix - never a traceback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

NORMAL_FORMS = ["0NF", "1NF", "2NF", "3NF", "BCNF", "4NF", "5NF", "6NF"]
PROJECT_MIN = NORMAL_FORMS.index("3NF")
FIELD_TYPES = {"text", "number", "range", "date", "month", "year", "bool", "url", "category"}
STANDARD_LEVELS = {"raw", "standard", "strict"}
CONFLICT_RULES = {"majority", "trusted_first", "newest", "keep_all_and_flag"}
DERIVE_POLICIES = {"suggest", "auto_safe", "off"}
SOURCE_MODES = {"only_listed", "start_here", "start_here_and_discover"}
IF_MISSING = {"leave_empty", "use_fallback", "partial"}
DERIVE_METHODS = {"formula", "lookup", "rules", "aggregate", "code", "describe"}

# units understood without the optional `pint` dependency
KNOWN_UNITS = {
    "m", "km", "cm", "mm", "ft", "feet", "in", "mi",
    "kg", "g", "mg", "lb", "t",
    "c", "f", "k",
    "d", "day", "days", "h", "hour", "min", "s", "year", "years",
    "inr", "usd", "eur", "gbp", "%",
}


class SpecError(Exception):
    pass


@dataclass
class Problem:
    level: str      # "error" | "warning"
    where: str
    message: str
    fix: str = ""

    def __str__(self) -> str:
        head = f"{self.level.upper():7} {self.where}: {self.message}"
        return f"{head}\n{'':8}fix: {self.fix}" if self.fix else head


@dataclass
class FieldSpec:
    name: str
    type: str = "text"
    unit: str | None = None
    multiple: bool = False
    required: bool = False
    languages: list[str] = field(default_factory=list)
    vocabulary: str | None = None
    extract: list[str] = field(default_factory=list)
    sanity: list[Any] | None = None       # [min, max] or a list of allowed values
    level: str | None = None              # per-field standardize level
    infer: str | None = None              # "ai" enables AI inference
    on_conflict: str | None = None
    display: str | None = None
    default: Any = None                   # used when nothing was found (origin: default)

    @classmethod
    def parse(cls, name: str, raw: Any) -> "FieldSpec":
        if raw is None:
            return cls(name=name)
        if isinstance(raw, str):                      # shorthand: `name: text`
            return cls(name=name, type=raw)
        extract = raw.get("extract") or []
        if isinstance(extract, str):
            extract = [extract]
        langs = raw.get("languages") or []
        if isinstance(langs, str):
            langs = [langs]
        return cls(
            name=name,
            type=str(raw.get("type", "text")),
            unit=raw.get("unit"),
            multiple=bool(raw.get("multiple", False)),
            required=bool(raw.get("required", False)),
            languages=list(langs),
            vocabulary=raw.get("vocabulary"),
            extract=list(extract),
            sanity=_numbers(raw.get("sanity")),
            level=raw.get("level"),
            infer=raw.get("infer"),
            on_conflict=raw.get("on_conflict"),
            display=raw.get("display") or raw.get("label"),
            default=raw.get("default"),
        )


@dataclass
class EntitySpec:
    name: str
    identity: list[str] = field(default_factory=list)
    fields: dict[str, FieldSpec] = field(default_factory=dict)
    aliases_from: list[str] = field(default_factory=list)
    label: str | None = None          # the field a person would call this record by
    match: str = "fuzzy"              # fuzzy | exact: whether spelling variants merge

    @classmethod
    def parse(cls, name: str, raw: Any) -> "EntitySpec":
        if isinstance(raw, list):                     # shorthand: `plant: [a, b]`
            return cls(name=name, identity=[raw[0]] if raw else [],
                       fields={f: FieldSpec(name=f) for f in raw})
        raw = raw or {}
        identity = raw.get("identity") or []
        if isinstance(identity, str):
            identity = [identity]
        fields_raw = raw.get("fields") or {}
        fields = {k: FieldSpec.parse(k, v) for k, v in fields_raw.items()}
        alias_from = raw.get("aliases_from") or []
        if isinstance(alias_from, str):
            alias_from = [alias_from]
        return cls(name=name, identity=list(identity), fields=fields,
                   aliases_from=list(alias_from), label=raw.get("label"),
                   match=str(raw.get("match", "fuzzy")).lower())


@dataclass
class RelationSpec:
    from_entity: str
    name: str
    to_entity: str

    @classmethod
    def parse(cls, raw: Any) -> "RelationSpec":
        if isinstance(raw, str):                      # "plant grows_in region"
            parts = raw.split()
            if len(parts) != 3:
                raise SpecError(f"relation '{raw}' must read 'from name to'")
            return cls(parts[0], parts[1], parts[2])
        return cls(str(raw.get("from")), str(raw.get("name")), str(raw.get("to")))


def _fix_bands(bands):
    if not isinstance(bands, dict):
        return bands
    out = dict(bands)
    if "cutoffs" in out:
        out["cutoffs"] = _numbers(out["cutoffs"])
    return out


@dataclass
class DerivedSpec:
    name: str
    on: str
    method: str = "formula"
    formula: str | None = None
    describe: str | None = None
    inputs: list[str] = field(default_factory=list)
    bands: dict | None = None
    rules: list | None = None
    code: str | None = None          # a Python file, for `method: code`
    function: str = "compute"
    unit: str | None = None
    round: int | None = None
    if_missing: str = "leave_empty"
    fallback: Any = None
    explain: str = ""
    review: str | None = None
    status: str = "active"
    suggested_by: str | None = None

    @classmethod
    def parse(cls, name: str, raw: Any) -> "DerivedSpec":
        raw = raw or {}
        if isinstance(raw, str):
            raw = {"formula": raw, "on": ""}
        raw = _fix_yaml_booleans(raw)
        inputs = raw.get("inputs") or []
        if isinstance(inputs, str):
            inputs = [inputs]
        method = raw.get("method")
        if not method:
            method = "lookup" if raw.get("bands") else (
                "rules" if raw.get("rules") else (
                    "code" if raw.get("code") else (
                        "describe" if raw.get("describe") and not raw.get("formula")
                        else "formula")))
        return cls(
            name=name, on=str(raw.get("on", "")), method=method,
            formula=raw.get("formula"), describe=raw.get("describe"),
            inputs=list(inputs), bands=_fix_bands(raw.get("bands")),
            rules=raw.get("rules"),
            code=raw.get("code"), function=str(raw.get("function", "compute")),
            unit=raw.get("unit"), round=raw.get("round"),
            if_missing=str(raw.get("if_missing", "leave_empty")),
            fallback=raw.get("fallback"), explain=str(raw.get("explain", "")),
            review=raw.get("review"), status=str(raw.get("status", "active")),
            suggested_by=raw.get("suggested_by"),
        )

    def referenced_fields(self) -> list[str]:
        """The fields this derivation reads, from `inputs` or from the formula.

        The formula is read as a syntax tree, so a unit written as text - the
        'm' in convert(x, 'm', 'ft') - is not mistaken for a field called m,
        which a search through the raw text cannot tell apart.
        """
        if self.inputs:
            return [i.split(".")[0].strip() for i in self.inputs]
        if not self.formula:
            return []
        try:
            from .derive.formula import referenced_names
            names = referenced_names(self.formula)
        except Exception:
            names = []
        out = []
        for name in names:
            for suffix in ("__min", "__max", "__value", "__count"):
                if name.endswith(suffix):
                    name = name[: -len(suffix)]
                    break
            if name not in FORMULA_FUNCTIONS and name not in out:
                out.append(name)
        return out


NUMERIC_TEXT = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


def as_number(value):
    """YAML 1.1 reads `3.0e6` as text, because it wants `3.0e+6`.

    Scientific notation is how anyone writes a mass or a distance, so a
    numeric-looking string is read as the number it plainly is. Left alone it
    would sort as text and turn a sanity range into a list of allowed values.
    """
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and NUMERIC_TEXT.match(value.strip()):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _numbers(values):
    return [as_number(v) for v in values] if isinstance(values, list) else values


def _fix_yaml_booleans(raw: dict) -> dict:
    """YAML 1.1 reads `on:` as True and `off:`/`no:` as False - undo that."""
    if not isinstance(raw, dict):
        return raw
    replacements = {True: "on", False: "off"}
    return {replacements.get(k, k) if isinstance(k, bool) else k: v
            for k, v in raw.items()}


FORMULA_FUNCTIONS = {
    "min", "max", "avg", "count", "sum", "band", "season_of", "place_parent", "if",
    "convert",
    "round", "abs", "len", "lower", "upper", "concat", "midpoint", "year_of",
    # maths
    "sqrt", "pow", "log", "log10", "exp", "floor", "ceil", "sign", "clamp",
    "pi", "e", "tau",
    # geography and trigonometry
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "hypot",
    "radians", "degrees", "distance_km",
    # text
    "contains", "word", "replace", "number", "startswith", "endswith", "trim",
    "substr", "coalesce", "is_empty",
    # dates
    "days_between", "years_between", "month_of", "day_of", "weekday_of",
    # statistics across a column
    "mean", "median", "stdev", "variance", "total", "spread", "smallest",
    "largest", "records", "percentile", "zscore", "normalize", "rank", "share",
    "count_distinct", "mode", "correlation",
}


@dataclass
class SourceItem:
    id: str
    type: str = "website"
    location: str = ""
    tier: int = 3
    authoritative_for: list[str] = field(default_factory=list)
    follow_links: bool = True
    depth: int | None = None
    language: str | None = None
    pages: str | None = None
    map: dict = field(default_factory=dict)
    ai_allowed: bool = True
    key_env: str | None = None       # environment variable holding an API key
    license: str | None = None
    area: str | None = None          # type osm: a place name, resolved to a bounding box
    bbox: list = field(default_factory=list)   # type osm: south, west, north, east
    tags: Any = None                 # type osm: {amenity: cafe} or ["amenity=cafe"]
    limit: int | None = None         # type osm: the most features to fetch

    @classmethod
    def parse(cls, raw: Any, index: int = 0) -> "SourceItem":
        if isinstance(raw, str):
            return cls(id=f"source_{index+1}", type=guess_source_type(raw), location=raw)
        auth = raw.get("authoritative_for") or []
        if isinstance(auth, str):
            auth = [auth]
        loc = str(raw.get("location", ""))
        return cls(
            id=str(raw.get("id") or f"source_{index+1}"),
            type=str(raw.get("type") or guess_source_type(loc)),
            location=loc, tier=int(raw.get("tier", 3)),
            authoritative_for=list(auth),
            follow_links=bool(raw.get("follow_links", True)),
            depth=raw.get("depth"), language=raw.get("language"),
            pages=str(raw["pages"]) if raw.get("pages") is not None else None,
            map=raw.get("map") or {}, ai_allowed=bool(raw.get("ai_allowed", True)),
            key_env=raw.get("key_env"), license=raw.get("license"),
            area=raw.get("area"), bbox=list(raw.get("bbox") or []),
            tags=raw.get("tags"),
            limit=int(raw["limit"]) if raw.get("limit") is not None else None,
        )


def guess_source_type(location: str) -> str:
    low = location.lower()
    if low.startswith("http"):
        if any(word in low for word in ("/rss", "/feed", ".rss", ".atom", "feed.xml")):
            return "feed"
        from urllib.parse import urlparse
        parsed = urlparse(low)
        host, path, query = parsed.netloc, parsed.path, parsed.query
        if (host.startswith("api.") or host.endswith(".api") or "/api/" in path
                or path.endswith(("/api", ".json")) or "format=json" in query):
            return "api"
        return "website"
    if low.startswith("osm:"):
        return "osm"
    for ext, kind in ((".pdf", "pdf"), (".csv", "csv"), (".tsv", "csv"),
                      (".xlsx", "xlsx"), (".xls", "xlsx"), (".json", "json"),
                      (".txt", "text"), (".md", "text"), (".docx", "text")):
        if low.endswith(ext):
            return kind
    return "folder" if location and not Path(location).suffix else "file"


@dataclass
class SourcesSpec:
    seeds: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    depth: int = 2
    max_pages: int = 200
    delay_seconds: float = 1.0
    follow_other_domains: bool = False
    domains: list[str] = field(default_factory=list)
    trusted_order: list[str] = field(default_factory=list)
    trust_tiers: dict[str, int] = field(default_factory=dict)
    mode: str = "start_here"
    items: list[SourceItem] = field(default_factory=list)
    max_ai_pages: int = 100
    workers: int = 5

    @classmethod
    def parse(cls, raw: Any) -> "SourcesSpec":
        raw = raw or {}
        seeds = raw.get("seeds") or []
        if isinstance(seeds, str):
            seeds = [seeds]
        items = [SourceItem.parse(s, i) for i, s in enumerate(raw.get("items") or [])]
        tiers: dict[str, int] = {}
        for key, value in (raw.get("trust_tiers") or {}).items():
            # accepts {domain: tier} or {tier: [domains]}
            if isinstance(value, list):
                for dom in value:
                    tiers[str(dom)] = int(str(key).replace("tier", "") or 3)
            else:
                tiers[str(key)] = int(value)
        for item in items:
            if item.type == "website" and item.location:
                tiers.setdefault(domain_of(item.location), item.tier)
        return cls(
            seeds=list(seeds), keywords=list(raw.get("keywords") or []),
            depth=int(raw.get("depth", 2)), max_pages=int(raw.get("max_pages", 200)),
            delay_seconds=float(raw.get("delay_seconds", 1.0)),
            follow_other_domains=bool(raw.get("follow_other_domains", False)),
            domains=list(raw.get("domains") or []),
            trusted_order=list(raw.get("trusted_order") or []),
            trust_tiers=tiers, mode=str(raw.get("mode", "start_here")),
            items=items, max_ai_pages=int(raw.get("max_ai_pages", 100)),
            workers=max(1, int(raw.get("workers", 5))),
        )

    def all_seeds(self) -> list[str]:
        urls = list(self.seeds)
        urls += [i.location for i in self.items
                 if i.type == "website" and i.location not in urls]
        return urls


def domain_of(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url).netloc or "").lower().lstrip("www.")


@dataclass
class StandardizeSpec:
    level: str = "standard"
    dates: str = "iso8601"
    currency: str = ""                    # empty: keep the currency as written
    rates: dict = field(default_factory=dict)
    on_conflict: str = "keep_all_and_flag"
    min_confidence: float = 0.5

    @classmethod
    def parse(cls, raw: Any) -> "StandardizeSpec":
        raw = raw or {}
        return cls(
            level=str(raw.get("level", "standard")),
            dates=str(raw.get("dates", "iso8601")),
            currency=str(raw.get("currency") or "").upper(),
            rates={str(k).upper(): float(v)
                   for k, v in (raw.get("rates") or {}).items()},
            on_conflict=str(raw.get("on_conflict", "keep_all_and_flag")),
            min_confidence=float(raw.get("min_confidence", 0.5)),
        )


@dataclass
class StorageSpec:
    normal_form: str = "3NF"
    database: str = ".spider/dataset.db"
    keep_provenance: bool = True

    @classmethod
    def parse(cls, raw: Any) -> "StorageSpec":
        raw = raw or {}
        return cls(
            normal_form=str(raw.get("normal_form", "3NF")).upper(),
            database=str(raw.get("database", ".spider/dataset.db")),
            keep_provenance=bool(raw.get("keep_provenance", True)),
        )


@dataclass
class TargetSpec:
    name: str
    format: str = "csv"
    path: str = "exports"
    mode: str | None = None
    normal_form: str | None = None
    shape: str = "wide"
    nesting: str = "flat"
    naming: str = "snake_case"
    provenance: str = "separate_table"
    columns: list[dict] = field(default_factory=list)
    min_confidence: float | None = None
    include_derived: bool = True
    sort_by: str | None = None
    descending: bool = False
    split: str = "per_table"          # per_table | single
    template: str | None = None       # a Jinja file, for `format: template`

    @classmethod
    def parse(cls, raw: Any, index: int = 0) -> "TargetSpec":
        if isinstance(raw, str):
            return cls(name=raw, format=raw, path=f"exports/{raw}")
        return cls(
            name=str(raw.get("name") or f"target_{index+1}"),
            format=str(raw.get("format", "csv")),
            path=str(raw.get("path") or "exports"),
            mode=raw.get("mode"),
            normal_form=(str(raw["normal_form"]).upper() if raw.get("normal_form") else None),
            shape=str(raw.get("shape", "wide")),
            nesting=str(raw.get("nesting", "flat")),
            naming=str(raw.get("naming", "snake_case")),
            provenance=str(raw.get("provenance", "separate_table")),
            columns=list(raw.get("columns") or []),
            min_confidence=(float(raw["min_confidence"]) if raw.get("min_confidence") is not None else None),
            include_derived=bool(raw.get("include_derived", True)),
            sort_by=raw.get("sort_by"),
            descending=bool(raw.get("descending", False)),
            split=str(raw.get("split", "per_table")),
            template=raw.get("template"),
        )


@dataclass
class OutputSpec:
    formats: list[str] = field(default_factory=lambda: ["csv"])
    include_derived: bool = True
    label_origin: bool = True
    include_origin_label: bool = True
    targets: list[TargetSpec] = field(default_factory=list)

    @classmethod
    def parse(cls, raw: Any) -> "OutputSpec":
        raw = raw or {}
        targets = [TargetSpec.parse(t, i) for i, t in enumerate(raw.get("targets") or [])]
        formats = raw.get("formats") or (["csv"] if not targets else [])
        if isinstance(formats, str):
            formats = [formats]
        return cls(
            formats=list(formats),
            include_derived=bool(raw.get("include_derived", True)),
            label_origin=bool(raw.get("label_origin", True)),
            include_origin_label=bool(raw.get("include_origin_label", True)),
            targets=targets,
        )


@dataclass
class Spec:
    project: str = "spider-project"
    mode: str = "project"
    languages: list[str] = field(default_factory=lambda: ["en"])
    sources: SourcesSpec = field(default_factory=SourcesSpec)
    entities: dict[str, EntitySpec] = field(default_factory=dict)
    relations: list[RelationSpec] = field(default_factory=list)
    derived: dict[str, DerivedSpec] = field(default_factory=dict)
    standardize: StandardizeSpec = field(default_factory=StandardizeSpec)
    storage: StorageSpec = field(default_factory=StorageSpec)
    output: OutputSpec = field(default_factory=OutputSpec)
    connectors: list[dict] = field(default_factory=list)
    derive_policy: str = "suggest"
    season_scheme: str = "northern"
    ai: dict = field(default_factory=dict)
    path: Path | None = None
    raw: dict = field(default_factory=dict)

    # ------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: str | Path) -> "Spec":
        path = Path(path)
        if not path.exists():
            raise SpecError(f"{path} not found - run `spider describe \"...\"` or copy the example")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise SpecError(f"{path} is not valid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise SpecError(f"{path} must contain a mapping at the top level")
        spec = cls.from_dict(raw)
        spec.path = path
        return spec

    @classmethod
    def from_dict(cls, raw: dict) -> "Spec":
        langs = raw.get("languages") or ["en"]
        if isinstance(langs, str):
            langs = [langs]
        entities = {k: EntitySpec.parse(k, v) for k, v in (raw.get("entities") or {}).items()}
        relations = [RelationSpec.parse(r) for r in (raw.get("relations") or [])]
        derived = {}
        for name, body in (raw.get("derived") or {}).items():
            derived[name] = DerivedSpec.parse(name, body)
        return cls(
            project=str(raw.get("project", "spider-project")),
            mode=str(raw.get("mode", "project")),
            languages=list(langs),
            sources=SourcesSpec.parse(raw.get("sources")),
            entities=entities, relations=relations, derived=derived,
            standardize=StandardizeSpec.parse(raw.get("standardize")),
            storage=StorageSpec.parse(raw.get("storage")),
            output=OutputSpec.parse(raw.get("output")),
            connectors=list(raw.get("connectors") or []),
            derive_policy=str(raw.get("derive_policy", "suggest")),
            season_scheme=str(raw.get("season_scheme", "northern")).lower(),
            ai=raw.get("ai") or {},
            raw=raw,
        )

    @classmethod
    def find(cls, root: Path) -> "Spec":
        for candidate in (root / "spider.yaml", root / "spider.yml",
                          root / ".spider" / "spider.yaml"):
            if candidate.exists():
                return cls.load(candidate)
        raise SpecError(
            "no spider.yaml in this project - write one or run "
            "`spider describe \"what data you want\"`"
        )

    # ---------------------------------------------------------- validation
    def validate(self, conn=None) -> list[Problem]:
        """Check the file. With a project database, also check that what the
        file refers to actually exists in it."""
        problems: list[Problem] = []
        add = problems.append

        if self.mode not in ("project", "analysis"):
            add(Problem("error", "mode", f"unknown mode '{self.mode}'",
                        "use `mode: project` or `mode: analysis`"))

        nf = self.storage.normal_form
        if nf not in NORMAL_FORMS:
            add(Problem("error", "storage.normal_form", f"unknown normal form '{nf}'",
                        f"choose one of {', '.join(NORMAL_FORMS)}"))
        elif self.mode == "project" and NORMAL_FORMS.index(nf) < PROJECT_MIN:
            add(Problem("error", "storage.normal_form",
                        f"{nf} is below 3NF and mode is project",
                        "use 3NF or higher, or set `mode: analysis`"))

        if self.standardize.level not in STANDARD_LEVELS:
            add(Problem("error", "standardize.level",
                        f"unknown level '{self.standardize.level}'",
                        "use raw, standard or strict"))
        if self.standardize.on_conflict not in CONFLICT_RULES:
            add(Problem("error", "standardize.on_conflict",
                        f"unknown rule '{self.standardize.on_conflict}'",
                        f"use one of {', '.join(sorted(CONFLICT_RULES))}"))
        if not 0 <= self.standardize.min_confidence <= 1:
            add(Problem("error", "standardize.min_confidence",
                        "must be between 0 and 1", "try 0.5"))
        from .standardize.dates import SEASON_SCHEMES
        if self.season_scheme not in SEASON_SCHEMES:
            add(Problem("error", "season_scheme",
                        f"unknown season scheme '{self.season_scheme}'",
                        f"use one of {', '.join(SEASON_SCHEMES)}"))
        if self.derive_policy not in DERIVE_POLICIES:
            add(Problem("error", "derive_policy", f"unknown policy '{self.derive_policy}'",
                        "use suggest, auto_safe or off"))
        if self.sources.mode not in SOURCE_MODES:
            add(Problem("error", "sources.mode", f"unknown mode '{self.sources.mode}'",
                        f"use one of {', '.join(sorted(SOURCE_MODES))}"))

        if not self.entities:
            add(Problem("error", "entities", "no entities defined",
                        "add at least one entity with its fields"))

        for ename, ent in self.entities.items():
            if not ent.fields:
                add(Problem("error", f"entities.{ename}", "has no fields",
                            "list the columns you want under `fields:`"))
            if not ent.identity:
                add(Problem("warning", f"entities.{ename}", "no identity key",
                            "add `identity: [field]` so records can be merged"))
            if ent.match not in ("fuzzy", "exact"):
                add(Problem("error", f"entities.{ename}.match",
                            f"unknown match '{ent.match}'",
                            "use fuzzy (merge spelling variants) or exact"))
            if ent.label and ent.label not in ent.fields:
                add(Problem("error", f"entities.{ename}.label",
                            f"label field '{ent.label}' is not defined",
                            f"add '{ent.label}' under entities.{ename}.fields"))
            for key in ent.identity:
                if key not in ent.fields:
                    add(Problem("error", f"entities.{ename}.identity",
                                f"identity field '{key}' is not defined",
                                f"add '{key}' under entities.{ename}.fields"))
            for fname, fld in ent.fields.items():
                where = f"entities.{ename}.fields.{fname}"
                if fld.type not in FIELD_TYPES:
                    add(Problem("error", where, f"unknown type '{fld.type}'",
                                f"use one of {', '.join(sorted(FIELD_TYPES))}"))
                if fld.unit and fld.unit.lower() not in KNOWN_UNITS:
                    add(Problem("warning", where, f"unknown unit '{fld.unit}'",
                                "add it to ref_units with `spider ref load units.csv`"))
                if fld.level and fld.level not in STANDARD_LEVELS:
                    add(Problem("error", where, f"unknown standardize level '{fld.level}'",
                                "use raw, standard or strict"))
                if fld.level == "strict" and not fld.vocabulary:
                    add(Problem("warning", where, "strict level without a vocabulary",
                                "set `vocabulary: <name>` so values can be checked"))
                if fld.vocabulary and conn is not None:
                    from .ref.tables import presets, vocabulary_is_empty
                    if vocabulary_is_empty(conn, fld.vocabulary):
                        add(Problem(
                            "error", where,
                            f"vocabulary '{fld.vocabulary}' has nothing in it, so "
                            f"this field can never match anything",
                            f"load a list for it: `spider ref load my-{fld.vocabulary}"
                            f".csv`, or one of the bundled sets: "
                            f"{', '.join(presets()) or 'none'}"))
                if fld.on_conflict and fld.on_conflict not in CONFLICT_RULES:
                    add(Problem("error", where, f"unknown on_conflict '{fld.on_conflict}'",
                                f"use one of {', '.join(sorted(CONFLICT_RULES))}"))
                if fld.infer and fld.infer != "ai":
                    add(Problem("error", where, f"unknown infer '{fld.infer}'",
                                "the only value is `infer: ai`"))
                if fld.default is not None:
                    add(Problem("warning", where,
                                f"`default: {fld.default}` is a value with no source",
                                "it fills the cell as origin `default`, is never "
                                "counted as agreement, and is labelled in exports"))
                    if fld.required:
                        add(Problem("error", where,
                                    "a required field cannot have a default",
                                    "a default would hide that the value was never "
                                    "found; drop one of them"))
                    if fld.multiple:
                        add(Problem("error", where,
                                    "a `multiple` field cannot have a default",
                                    "a default fills one cell, not a list"))
                if fld.sanity is not None and not isinstance(fld.sanity, list):
                    add(Problem("error", where, "sanity must be a list",
                                "use [min, max] for numbers or a list of allowed values"))
                for rule in fld.extract:
                    if rule.startswith("regex:"):
                        try:
                            re.compile(rule[6:])
                        except re.error as exc:
                            add(Problem("error", where, f"bad regex: {exc}",
                                        "escape backslashes or use single quotes in YAML"))

        for rel in self.relations:
            if rel.from_entity not in self.entities:
                add(Problem("error", "relations",
                            f"'{rel.from_entity}' in relation '{rel.name}' is not an entity",
                            f"define entities.{rel.from_entity} or fix the name"))
            if rel.to_entity not in self.entities:
                add(Problem("error", "relations",
                            f"'{rel.to_entity}' in relation '{rel.name}' is not an entity",
                            f"define entities.{rel.to_entity} or fix the name"))

        problems.extend(self._validate_derivations())

        if self.sources.mode != "only_listed" and not self.sources.all_seeds():
            add(Problem("warning", "sources.seeds", "no seed sources given",
                        "add seed URLs, or `spider source add <link-or-file>`"))
        for item in self.sources.items:
            if item.tier not in (0, 1, 2, 3):
                add(Problem("error", f"sources.items.{item.id}",
                            f"tier {item.tier} is not 0, 1, 2 or 3",
                            "tier 0 = your own data, 1 = official, 2 = established, 3 = other"))
            if item.type in ("file", "pdf", "csv", "xlsx", "text", "folder"):
                base = self.path.parent if self.path else Path.cwd()
                if not (base / item.location).exists() and not Path(item.location).exists():
                    add(Problem("warning", f"sources.items.{item.id}",
                                f"file not found: {item.location}",
                                "check the path, relative to the project folder"))

        for target in self.output.targets:
            tmode = target.mode or self.mode
            tnf = target.normal_form or self.storage.normal_form
            if tnf not in NORMAL_FORMS:
                add(Problem("error", f"output.targets.{target.name}",
                            f"unknown normal form '{tnf}'", f"use one of {NORMAL_FORMS}"))
            elif tmode == "project" and NORMAL_FORMS.index(tnf) < PROJECT_MIN:
                add(Problem("error", f"output.targets.{target.name}",
                            f"{tnf} needs analysis mode",
                            f"set `mode: analysis` on target {target.name} or raise the level"))
            if target.shape not in ("wide", "long"):
                add(Problem("error", f"output.targets.{target.name}",
                            f"unknown shape '{target.shape}'", "use wide or long"))
            if target.provenance not in ("none", "columns", "separate_table"):
                add(Problem("error", f"output.targets.{target.name}",
                            f"unknown provenance '{target.provenance}'",
                            "use none, columns or separate_table"))
            if target.split not in ("per_table", "single"):
                add(Problem("error", f"output.targets.{target.name}",
                            f"unknown split '{target.split}'",
                            "use per_table (a file per table) or single"))
            if target.format == "template" and not target.template:
                add(Problem("error", f"output.targets.{target.name}",
                            "format `template` without a `template:` file",
                            "point it at a Jinja file in the project"))
            if target.format not in ("csv", "json", "sqlite", "xlsx", "sql", "parquet",
                                     "template"):
                add(Problem("warning", f"output.targets.{target.name}",
                            f"format '{target.format}' is not built in",
                            "use csv, json, sqlite or xlsx"))
        return problems

    def _validate_derivations(self) -> list[Problem]:
        problems: list[Problem] = []
        for name, der in self.derived.items():
            where = f"derived.{name}"
            if der.status == "rejected":
                continue
            if not der.on:
                problems.append(Problem("error", where, "missing `on:` entity",
                                        "say which entity the field belongs to"))
                continue
            if der.on not in self.entities:
                problems.append(Problem("error", where, f"entity '{der.on}' is not defined",
                                        f"add entities.{der.on} or fix `on:`"))
                continue
            if der.method not in DERIVE_METHODS:
                problems.append(Problem("error", where, f"unknown method '{der.method}'",
                                        f"use one of {', '.join(sorted(DERIVE_METHODS))}"))
            if der.if_missing not in IF_MISSING:
                problems.append(Problem("error", where, f"unknown if_missing '{der.if_missing}'",
                                        "use leave_empty, use_fallback or partial"))
            if der.if_missing == "use_fallback" and der.fallback is None:
                problems.append(Problem("error", where, "use_fallback without a `fallback:` value",
                                        "add the value to use when an input is empty"))
            if (not der.formula and not der.describe and not der.bands
                    and not der.rules and not der.code):
                problems.append(Problem("error", where, "nothing to calculate",
                                        "add a `formula:`, `bands:`, `rules:`, "
                                        "`code:` or `describe:`"))
            if der.method == "code":
                if not der.code:
                    problems.append(Problem("error", where,
                                            "method `code` without a `code:` file",
                                            "point `code:` at a Python file in this project"))
                else:
                    base = self.path.parent if self.path else Path.cwd()
                    script = (base / der.code)
                    if not script.exists() and not Path(der.code).exists():
                        problems.append(Problem("error", where,
                                                f"code file not found: {der.code}",
                                                "check the path, relative to the project folder"))
                    else:
                        problems.append(Problem(
                            "warning", where,
                            f"`{der.code}` is your own Python and Spider runs it as written",
                            "formulas are sandboxed; `method: code` is not, so only "
                            "point it at a file you wrote"))
            if der.method == "lookup" and not der.bands:
                problems.append(Problem("error", where, "lookup method without `bands:`",
                                        "add bands: {cutoffs: [...], labels: [...]}"))
            if der.bands:
                cut = der.bands.get("cutoffs") or []
                lab = der.bands.get("labels") or []
                if len(lab) != len(cut) + 1:
                    problems.append(Problem("error", where,
                                            f"{len(cut)} cutoffs need {len(cut)+1} labels, found {len(lab)}",
                                            "one more label than cutoffs"))
                if list(cut) != sorted(cut):
                    problems.append(Problem("error", where, "cutoffs are not in ascending order",
                                            "sort them low to high"))
            if der.describe and not der.formula and der.review != "required":
                problems.append(Problem("warning", where,
                                        "a described derivation needs your approval",
                                        "add `review: required` - AI formulas are never applied automatically"))
            ent = self.entities[der.on]
            known = set(ent.fields) | set(self.derived) | {r.to_entity for r in self.relations}
            known |= {e for e in self.entities}
            import difflib
            for ref in der.referenced_fields():
                if ref not in known and not ref.isdigit():
                    from .derive.formula import suggest
                    close = suggest(ref, known)
                    hint = (f"did you mean '{close}'?" if close else
                            f"the fields of {der.on} are: "
                            f"{', '.join(list(ent.fields)[:8])}")
                    problems.append(Problem(
                        "error", where, f"'{ref}' is not a field of {der.on}",
                        f"{hint} - a misspelt field gives empty cells and no error "
                        f"at build time"))
        problems.extend(self._find_cycles())
        return problems

    def _find_cycles(self) -> list[Problem]:
        """Reject circular derivations (section 11, rule 3)."""
        graph = {n: [r for r in d.referenced_fields() if r in self.derived]
                 for n, d in self.derived.items()}
        problems, seen_cycles = [], set()
        state: dict[str, int] = {}

        def walk(node: str, trail: list[str]) -> None:
            state[node] = 1
            for nxt in graph.get(node, []):
                if state.get(nxt) == 1:
                    cycle = trail[trail.index(nxt):] + [nxt] if nxt in trail else [node, nxt]
                    key = tuple(sorted(set(cycle)))
                    if key not in seen_cycles:
                        seen_cycles.add(key)
                        problems.append(Problem(
                            "error", "derived",
                            "circular derivation: " + " -> ".join(cycle),
                            "break the loop so each derived field depends only on earlier ones"))
                elif state.get(nxt, 0) == 0:
                    walk(nxt, trail + [nxt])
            state[node] = 2

        for node in graph:
            if state.get(node, 0) == 0:
                walk(node, [node])
        return problems

    # ------------------------------------------------------------- helpers
    def entity_field(self, entity: str, fieldname: str) -> FieldSpec | None:
        ent = self.entities.get(entity)
        return ent.fields.get(fieldname) if ent else None

    def field_level(self, entity: str, fieldname: str) -> str:
        fld = self.entity_field(entity, fieldname)
        return (fld.level if fld and fld.level else self.standardize.level)

    def conflict_rule(self, entity: str, fieldname: str) -> str:
        fld = self.entity_field(entity, fieldname)
        return (fld.on_conflict if fld and fld.on_conflict else self.standardize.on_conflict)

    def own_domains(self) -> set:
        """The sites the user named themselves: seeds and website sources."""
        names = {domain_of(u) for u in self.sources.all_seeds()}
        names |= {domain_of(i.location) for i in self.sources.items
                  if i.type == "website" and i.location}
        return {n for n in names if n}

    def tier_for(self, url_or_domain: str) -> int:
        """How much to trust a site, from most specific to least.

        1. what `trust_tiers` says about it
        2. a guess from the address: government 1, universities and .org 2
        3. a site you pointed Spider at yourself is at least tier 2. You chose
           it, so it is not a stranger - and treating it as one would hold back
           every value from a one-site project, which is what tier 3's 0.40
           does against the default `min_confidence` of 0.5.
        4. anything else Spider wanders onto is tier 3.
        """
        dom = domain_of(url_or_domain) or url_or_domain.lower()
        if dom in self.sources.trust_tiers:
            return self.sources.trust_tiers[dom]
        for known, tier in self.sources.trust_tiers.items():
            if dom.endswith(known):
                return tier
        guess = 3
        if dom.endswith(".gov.in") or dom.endswith(".gov") or dom.endswith(".nic.in"):
            guess = 1
        elif dom.endswith(".edu") or dom.endswith(".ac.in") or dom.endswith(".org"):
            guess = 2
        if guess > 2 and any(dom == own or dom.endswith("." + own)
                             for own in self.own_domains()):
            return 2
        return guess

    def dump(self) -> str:
        return yaml.safe_dump(self.raw, sort_keys=False, allow_unicode=True)

    def save(self, path: Path | None = None) -> Path:
        target = Path(path or self.path or "spider.yaml")
        target.write_text(self.dump(), encoding="utf-8")
        return target
