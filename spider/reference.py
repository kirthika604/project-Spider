"""What every setting in `spider.yaml` means, in plain words.

One list, read by three things: `spider settings`, the generated
`docs/spider-yaml.md`, and the clarifying questions in `spider describe
--ask`. Keeping them on one source is why the help cannot drift from the code.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Setting:
    key: str
    what: str                                  # one sentence, plain
    kind: str = "text"
    default: str = ""
    choices: tuple = ()
    example: str = ""
    note: str = ""                             # what changes if you change it

    @property
    def section(self) -> str:
        return self.key.split(".")[0].split("[")[0]


SETTINGS: list[Setting] = [
    # ------------------------------------------------------------ the project
    Setting("project", "A short name for this dataset. It names the export files.",
            "text", "spider-project", example="project: himalayan-plants"),
    Setting("mode",
            "How tidy the output has to be. Project mode refuses anything below "
            "3NF so an app never gets a messy structure; analysis mode lets you "
            "ask for a single flat table.",
            "choice", "project", ("project", "analysis"),
            example="mode: analysis",
            note="Analysis mode is what you want for a spreadsheet or a notebook."),
    Setting("languages",
            "The languages you expect to meet. Local-script names are kept and "
            "transliterated so they match their Latin spelling.",
            "list", "[en]", example="languages: [en, hi, ta]"),
    Setting("described_as",
            "The sentence you started from. Spider keeps it to write search "
            "queries and to summarise pages.",
            "text", "", example='described_as: "plants and their uses"'),
    Setting("derive_policy",
            "How much Spider may calculate without asking. `suggest` proposes and "
            "waits; `auto_safe` applies only exact conversions and counts; `off` "
            "calculates nothing you did not write.",
            "choice", "suggest", ("suggest", "auto_safe", "off"),
            note="An AI-proposed formula always needs approval, under every policy."),

    # ----------------------------------------------------------------- sources
    Setting("sources.seeds",
            "The pages the crawl starts from. A seed is just a URL you already "
            "know is worth reading - a department's index, a flora checklist.",
            "list of links", "[]",
            example="seeds:\n  - https://institute.example.org/flora",
            note="Spider stays on these sites unless you widen it below."),
    Setting("sources.keywords",
            "Words that decide whether a page is worth keeping. A page is saved "
            "only if it contains at least one; its links are followed either way.",
            "list", "[]", example="keywords: [plant, herb, medicinal]",
            note="Leave it empty to keep every page the crawl reaches."),
    Setting("sources.depth",
            "How many links away from a seed to go. 0 reads only the seeds "
            "themselves, 1 also reads the pages they link to, 2 goes one further.",
            "number", "2", example="depth: 1",
            note="Each step multiplies the pages, so raise it slowly."),
    Setting("sources.max_pages",
            "The most pages to save in one crawl. The crawl stops when it gets "
            "there, however much is left in the queue.",
            "number", "200", example="max_pages: 300"),
    Setting("sources.delay_seconds",
            "How long to wait between two requests to the same site. This is the "
            "politeness setting; a site's own Crawl-delay wins if it asks for more.",
            "number", "1.0", example="delay_seconds: 1.0",
            note="Do not lower it for sites you do not own."),
    Setting("sources.follow_other_domains",
            "Whether links that leave the seed sites may be followed.",
            "yes or no", "false", ("true", "false"),
            example="follow_other_domains: false",
            note="Off keeps a crawl predictable; on finds more and wanders."),
    Setting("sources.domains",
            "An explicit list of sites the crawl may visit, whatever the seeds are.",
            "list", "[]", example="domains: [example.org, example.gov.in]"),
    Setting("sources.mode",
            "How far Spider may go beyond what you listed. `only_listed` never "
            "leaves your sources, `start_here` follows links inside them, "
            "`start_here_and_discover` also searches the web.",
            "choice", "start_here",
            ("only_listed", "start_here", "start_here_and_discover"),
            note="Use only_listed for coursework or confidential data."),
    Setting("sources.trust_tiers",
            "How much to trust each site: 1 official, 2 established, 3 everything "
            "else. Tier decides a value's starting confidence.",
            "map of site to tier", "3 for anything unlisted",
            example="trust_tiers:\n  forest.gov.in: 1\n  someblog.com: 3",
            note="Government and .gov sites default to 1, .edu and .org to 2."),
    Setting("sources.trusted_order",
            "Which site wins when you resolve conflicts with `trusted_first`, "
            "best first.",
            "list", "[]", example="trusted_order: [institute.org, blog.com]"),
    Setting("sources.workers",
            "How many pages to fetch at the same time. The per-site delay still "
            "holds, so this only helps when a crawl spans several sites - and "
            "then it helps a lot.",
            "number", "5", example="workers: 8",
            note="Raising it does not make Spider rude to any one site."),
    Setting("sources.max_ai_pages",
            "The most pages to send to the AI extractor in one build. Answers are "
            "cached, so re-runs cost nothing.",
            "number", "100", example="max_ai_pages: 50"),
    Setting("sources.items",
            "Sources you name one by one: a site, a PDF, a spreadsheet, a folder, "
            "a JSON endpoint or a feed. Each carries its own tier.",
            "list of sources", "[]",
            example=("items:\n  - {id: survey, type: xlsx, location: data/survey.xlsx,"
                     "\n     tier: 0, map: {Species: scientific_name}}")),
    Setting("sources.items[].tier",
            "How much you trust this one source. Tier 0 means you vouch for it "
            "yourself, and its values start at 0.95.",
            "number", "3", ("0", "1", "2", "3"), example="tier: 0"),
    Setting("sources.items[].map",
            "Which column or JSON key holds which field, for a spreadsheet, CSV "
            "or endpoint.",
            "map", "matched by name",
            example='map: {"Alt (m)": altitude_m, Species: scientific_name}'),
    Setting("sources.items[].ai_allowed",
            "Whether this source's text may be sent to the AI. Off means it is "
            "read by rules and column mapping only, and never leaves the machine.",
            "yes or no", "true", ("true", "false"), example="ai_allowed: false"),
    Setting("sources.items[].pages",
            "Which pages of a PDF to read.", "text", "all",
            example='pages: "12-40"'),
    Setting("sources.items[].key_env",
            "The environment variable holding this endpoint's API key, so the key "
            "stays in .env and out of the project file.",
            "text", "", example="key_env: MY_API_KEY"),

    # ---------------------------------------------------------------- entities
    Setting("entities",
            "The things your dataset is about. One entity becomes one table.",
            "map", "",
            example="entities:\n  plant:\n    identity: [scientific_name]\n"
                    "    fields:\n      scientific_name: {type: text}",
            note="The first entity listed is the subject of the dataset."),
    Setting("entities.<name>.identity",
            "The field that says whether two records are the same thing. Two pages "
            "naming the same scientific name become one record.",
            "list", "the first field",
            example="identity: [scientific_name]",
            note="Choose something stable: a scientific name, not a common one."),
    Setting("entities.<name>.fields",
            "The columns you want for this entity.", "map", "",
            example="fields:\n  altitude_m: {type: range, unit: m}"),
    Setting("entities.<name>.fields.<f>.type",
            "What kind of value this is. It decides how the text is cleaned: a "
            "range splits into a minimum and a maximum, a date becomes ISO 8601.",
            "choice", "text",
            ("text", "number", "range", "date", "month", "year", "bool", "url",
             "category"),
            example="type: range"),
    Setting("entities.<name>.fields.<f>.unit",
            "The unit this column is stored in. Everything found in another unit "
            "is converted to it.",
            "text", "", example="unit: m",
            note='"3 km", "3,000 m" and "9,842 ft" all become 3000 m.'),
    Setting("entities.<name>.fields.<f>.required",
            "Whether a record is incomplete without this value.",
            "yes or no", "false", ("true", "false"), example="required: true"),
    Setting("entities.<name>.fields.<f>.multiple",
            "Whether this column can hold more than one value, such as several "
            "common names. Multi-value columns get their own table.",
            "yes or no", "false", ("true", "false"), example="multiple: true"),
    Setting("entities.<name>.fields.<f>.extract",
            "Where to find the value on a page: a CSS selector, or `regex:` and a "
            "pattern. Several may be listed; the first that matches wins.",
            "list", "[]",
            example='extract: [".altitude", "regex:(\\\\d+)\\\\s*m"]',
            note="Without a rule, the field is filled by the AI extractor or left "
                 "to a vocabulary match."),
    Setting("entities.<name>.fields.<f>.sanity",
            "The range or list a value must fall in. Anything outside goes to the "
            "review queue instead of the dataset.",
            "list", "", example="sanity: [0, 9000]",
            note="This is what stops a page claiming 99999 m from being believed."),
    Setting("entities.<name>.fields.<f>.vocabulary",
            "The controlled list this column's values must map to, from the "
            "reference tables.",
            "text", "", example="vocabulary: uses",
            note='"medicine" and "ayurvedic use" both become `medicinal`.'),
    Setting("entities.<name>.fields.<f>.level",
            "How strictly to clean this one column, overriding `standardize.level`.",
            "choice", "the project level", ("raw", "standard", "strict"),
            example="level: strict"),
    Setting("entities.<name>.fields.<f>.on_conflict",
            "What to do when sources disagree about this one column.",
            "choice", "the project rule",
            ("majority", "trusted_first", "newest", "keep_all_and_flag"),
            example="on_conflict: newest"),
    Setting("entities.<name>.fields.<f>.default",
            "A value to use when nothing found one. It is the only value with no "
            "source: it is stored as origin `default`, carries no confidence, and "
            "never counts as agreement.",
            "any", "none", example='default: "not assessed"',
            note="Not allowed on a required or multiple field."),
    Setting("entities.<name>.fields.<f>.infer",
            "Set to `ai` to let the model estimate this field where no rule "
            "exists. Values are capped low and always flagged.",
            "choice", "off", ("ai",), example="infer: ai"),
    Setting("entities.<name>.fields.<f>.languages",
            "The languages this text field may appear in.",
            "list", "the project languages", example="languages: [en, hi]"),

    # --------------------------------------------------------------- relations
    Setting("relations",
            "How entities link to each other. Each becomes a junction table.",
            "list", "[]",
            example="relations:\n  - {from: plant, name: grows_in, to: region}",
            note="You can also write it as `plant grows_in region`."),

    # ----------------------------------------------------------------- derived
    Setting("derived",
            "Columns that are calculated rather than found, each with a rule you "
            "wrote or approved.",
            "map", "{}", example="derived:\n  climate_zone: {...}"),
    Setting("derived.<name>.on",
            "Which entity the calculated column belongs to.", "text", "",
            example="on: plant",
            note="Quote it as `'on'` if your editor turns it into true."),
    Setting("derived.<name>.method",
            "How it is worked out: a formula, a lookup in bands, an if-then list, "
            "an aggregate over related records, or your own Python file.",
            "choice", "formula",
            ("formula", "lookup", "rules", "aggregate", "code", "describe"),
            example="method: lookup"),
    Setting("derived.<name>.formula",
            "The expression, using only the allowed functions. It is read as a "
            "syntax tree, never executed, so a project file cannot run code.",
            "text", "",
            example="formula: \"convert(altitude_m.min, 'm', 'ft')\""),
    Setting("derived.<name>.bands",
            "Cutoffs and the label for each band, for `method: lookup`.",
            "map", "",
            example="bands: {cutoffs: [1500, 3000], labels: [low, mid, alpine]}",
            note="One more label than cutoffs."),
    Setting("derived.<name>.code",
            "A Python file in your project that calculates the value. This is the "
            "one thing Spider runs as written, so it warns you about it.",
            "text", "", example="code: rarity.py",
            note="The file defines `def compute(values): ...`."),
    Setting("derived.<name>.if_missing",
            "What to do when an input is empty: leave the cell empty, use a "
            "fallback, or calculate from what exists and lower the confidence.",
            "choice", "leave_empty", ("leave_empty", "use_fallback", "partial"),
            example="if_missing: partial"),
    Setting("derived.<name>.explain",
            "One plain sentence saying how the value is obtained. It shows in "
            "reports and exports.",
            "text", "", example='explain: "Zone from minimum altitude"'),
    Setting("derived.<name>.review",
            "Set to `required` to hold results in the review queue until you "
            "approve them.",
            "choice", "not required", ("required",), example="review: required"),
    Setting("derived.<name>.describe",
            "Say in words what you want instead of writing a formula. Spider "
            "drafts one for your approval and never applies it unasked.",
            "text", "", example='describe: "how wide its altitude band is"'),

    # ------------------------------------------------------------ standardize
    Setting("standardize.level",
            "How hard to clean values before they are compared. `raw` keeps the "
            "text as found, `standard` unifies units, dates and spellings, "
            "`strict` also rejects anything outside a vocabulary.",
            "choice", "standard", ("raw", "standard", "strict"),
            note="The original text is kept either way, so you can change your "
                 "mind and rebuild without crawling again."),
    Setting("standardize.dates", "The form dates are stored in.",
            "text", "iso8601", example="dates: iso8601"),
    Setting("standardize.currency", "The currency amounts are converted to.",
            "text", "INR", example="currency: INR"),
    Setting("standardize.on_conflict",
            "What to do when sources still disagree after cleaning: take the value "
            "most sources give, trust your ranked list, take the newest, or keep "
            "them all and flag them.",
            "choice", "keep_all_and_flag",
            ("majority", "trusted_first", "newest", "keep_all_and_flag"),
            note="Keeping all hides nothing; the table still shows the most "
                 "confident one."),
    Setting("standardize.min_confidence",
            "The score a value must reach to enter the dataset. Below it, the "
            "value waits in the review queue for a second source or your approval.",
            "number", "0.5", example="min_confidence: 0.5",
            note="One tier 3 source scores 0.40, so the default holds back "
                 "anything a single blog claims."),

    # ---------------------------------------------------------------- storage
    Setting("storage.normal_form",
            "How the output tables are shaped, from one wide table to one table "
            "per attribute. Every build is checked against the level and refused "
            "if it fails.",
            "choice", "3NF",
            ("0NF", "1NF", "2NF", "3NF", "BCNF", "4NF", "5NF", "6NF"),
            note="0NF to 2NF need `mode: analysis`."),
    Setting("storage.database", "Where the assembled dataset is written.",
            "path", ".spider/dataset.db", example="database: .spider/dataset.db"),
    Setting("storage.keep_provenance",
            "Whether to keep the source of every value. Turning it off makes a "
            "smaller dataset and loses the audit trail.",
            "yes or no", "true", ("true", "false")),

    # ----------------------------------------------------------------- output
    Setting("output.formats",
            "The simple way to ask for output: one folder or file per format.",
            "list", "[csv]", example="formats: [csv, sqlite]"),
    Setting("output.targets",
            "The full way: each target picks its own format, structure and design "
            "from the same data.",
            "list", "[]",
            example=("targets:\n  - {name: app, format: sqlite, path: exports/db.sqlite}")),
    Setting("output.targets[].format",
            "What kind of file to write.", "choice", "csv",
            ("csv", "json", "sqlite", "xlsx", "sql", "parquet", "template")),
    Setting("output.targets[].shape",
            "`wide` gives a column per field; `long` gives a row per field and "
            "value, with its confidence and source beside it.",
            "choice", "wide", ("wide", "long")),
    Setting("output.targets[].provenance",
            "Where the evidence goes: its own table, extra columns beside each "
            "value, or nowhere.",
            "choice", "separate_table", ("separate_table", "columns", "none")),
    Setting("output.targets[].nesting",
            "`nested` puts related records inside their parent in JSON.",
            "choice", "flat", ("flat", "nested")),
    Setting("output.targets[].naming",
            "How column names are written in this file.",
            "choice", "snake_case", ("snake_case", "camelCase", "Title Case")),
    Setting("output.targets[].columns",
            "The columns to include, their order, their labels and their units.",
            "list", "all",
            example='columns:\n  - {field: altitude_m, label: "Altitude (ft)", unit: ft}'),
    Setting("output.targets[].min_confidence",
            "Leave out any value below this score in this file.",
            "number", "", example="min_confidence: 0.8"),
    Setting("output.targets[].sort_by",
            "The column to sort on, with `descending: true` to reverse it.",
            "text", "", example="sort_by: altitude_m"),
    Setting("output.targets[].split",
            "`per_table` writes a file per table; `single` stacks them into one.",
            "choice", "per_table", ("per_table", "single")),
    Setting("output.include_derived",
            "Whether calculated and defaulted values are written out at all.",
            "yes or no", "true", ("true", "false")),
    Setting("output.label_origin",
            "Whether each value is labelled extracted, derived, inferred or default.",
            "yes or no", "true", ("true", "false")),

    # ------------------------------------------------------------- connectors
    Setting("connectors",
            "Outside services that feed the same verification chain: a species "
            "registry, a name database, an independent measurement.",
            "list", "[]",
            example="connectors:\n  - {name: gbif, use: [identifier]}"),
    Setting("connectors[].for",
            "Which entities to ask this service about.",
            "list", "the service decides", example="for: [plant]"),
    Setting("ai.model",
            "Which Claude model to use for extraction and drafting.",
            "text", "a small fast model", example="model: claude-haiku-4-5-20251001"),
]

BY_KEY = {s.key: s for s in SETTINGS}

SECTIONS = {
    "project": "The project",
    "mode": "The project",
    "languages": "The project",
    "described_as": "The project",
    "derive_policy": "The project",
    "sources": "Where to look",
    "entities": "What you want",
    "relations": "What you want",
    "derived": "Calculated columns",
    "standardize": "Cleaning values",
    "storage": "How it is stored",
    "output": "What comes out",
    "connectors": "Outside services",
    "ai": "Outside services",
}

SECTION_ORDER = ["The project", "Where to look", "What you want",
                 "Calculated columns", "Cleaning values", "How it is stored",
                 "What comes out", "Outside services"]


def find(query: str) -> list[Setting]:
    """Settings matching a key, a part of one, or a word in the description."""
    query = query.strip().lower().lstrip(".")
    if not query:
        return list(SETTINGS)
    exact = [s for s in SETTINGS if s.key.lower() == query]
    if exact:
        return exact
    leaf = [s for s in SETTINGS if s.key.lower().split(".")[-1] == query]
    if leaf:
        return leaf
    partial = [s for s in SETTINGS if query in s.key.lower()]
    if partial:
        return partial
    return [s for s in SETTINGS if query in s.what.lower() or query in s.note.lower()]


def grouped() -> dict:
    out: dict = {name: [] for name in SECTION_ORDER}
    for setting in SETTINGS:
        out[SECTIONS.get(setting.section, "The project")].append(setting)
    return {k: v for k, v in out.items() if v}
