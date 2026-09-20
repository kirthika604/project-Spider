# Every setting in `spider.yaml`

Generated from `spider/reference.py` by `spider settings --format markdown`, so it cannot drift from the code.

## The project

### `project`

A short name for this dataset. It names the export files.

**Type** text &middot; **Default** `spider-project`

```yaml
project: himalayan-plants
```

### `mode`

How tidy the output has to be. Project mode refuses anything below 3NF so an app never gets a messy structure; analysis mode lets you ask for a single flat table.

**Type** choice &middot; **Default** `project` &middot; **One of** `project`, `analysis`

```yaml
mode: analysis
```

Analysis mode is what you want for a spreadsheet or a notebook.

### `languages`

The languages you expect to meet. Local-script names are kept and transliterated so they match their Latin spelling.

**Type** list &middot; **Default** `[en]`

```yaml
languages: [en, hi, ta]
```

### `described_as`

The sentence you started from. Spider keeps it to write search queries and to summarise pages.

**Type** text

```yaml
described_as: "plants and their uses"
```

### `derive_policy`

How much Spider may calculate without asking. `suggest` proposes and waits; `auto_safe` applies only exact conversions and counts; `off` calculates nothing you did not write.

**Type** choice &middot; **Default** `suggest` &middot; **One of** `suggest`, `auto_safe`, `off`

An AI-proposed formula always needs approval, under every policy.

## Where to look

### `sources.seeds`

The pages the crawl starts from. A seed is just a URL you already know is worth reading - a department's index, a flora checklist.

**Type** list of links &middot; **Default** `[]`

```yaml
seeds:
  - https://institute.example.org/flora
```

Spider stays on these sites unless you widen it below.

### `sources.keywords`

Words that decide whether a page is worth keeping. A page is saved only if it contains at least one; its links are followed either way.

**Type** list &middot; **Default** `[]`

```yaml
keywords: [plant, herb, medicinal]
```

Leave it empty to keep every page the crawl reaches.

### `sources.depth`

How many links away from a seed to go. 0 reads only the seeds themselves, 1 also reads the pages they link to, 2 goes one further.

**Type** number &middot; **Default** `2`

```yaml
depth: 1
```

Each step multiplies the pages, so raise it slowly.

### `sources.max_pages`

The most pages to save in one crawl. The crawl stops when it gets there, however much is left in the queue.

**Type** number &middot; **Default** `200`

```yaml
max_pages: 300
```

### `sources.delay_seconds`

How long to wait between two requests to the same site. This is the politeness setting; a site's own Crawl-delay wins if it asks for more.

**Type** number &middot; **Default** `1.0`

```yaml
delay_seconds: 1.0
```

Do not lower it for sites you do not own.

### `sources.follow_other_domains`

Whether links that leave the seed sites may be followed.

**Type** yes or no &middot; **Default** `false` &middot; **One of** `true`, `false`

```yaml
follow_other_domains: false
```

Off keeps a crawl predictable; on finds more and wanders.

### `sources.domains`

An explicit list of sites the crawl may visit, whatever the seeds are.

**Type** list &middot; **Default** `[]`

```yaml
domains: [example.org, example.gov.in]
```

### `sources.mode`

How far Spider may go beyond what you listed. `only_listed` never leaves your sources, `start_here` follows links inside them, `start_here_and_discover` also searches the web.

**Type** choice &middot; **Default** `start_here` &middot; **One of** `only_listed`, `start_here`, `start_here_and_discover`

Use only_listed for coursework or confidential data.

### `sources.trust_tiers`

How much to trust each site: 1 official, 2 established, 3 everything else. Tier decides a value's starting confidence.

**Type** map of site to tier &middot; **Default** `3 for anything unlisted`

```yaml
trust_tiers:
  forest.gov.in: 1
  someblog.com: 3
```

Government and .gov sites default to 1, .edu and .org to 2.

### `sources.trusted_order`

Which site wins when you resolve conflicts with `trusted_first`, best first.

**Type** list &middot; **Default** `[]`

```yaml
trusted_order: [institute.org, blog.com]
```

### `sources.workers`

How many pages to fetch at the same time. The per-site delay still holds, so this only helps when a crawl spans several sites - and then it helps a lot.

**Type** number &middot; **Default** `5`

```yaml
workers: 8
```

Raising it does not make Spider rude to any one site.

### `sources.max_ai_pages`

The most pages to send to the AI extractor in one build. Answers are cached, so re-runs cost nothing.

**Type** number &middot; **Default** `100`

```yaml
max_ai_pages: 50
```

### `sources.items`

Sources you name one by one: a site, a PDF, a spreadsheet, a folder, a JSON endpoint or a feed. Each carries its own tier.

**Type** list of sources &middot; **Default** `[]`

```yaml
items:
  - {id: survey, type: xlsx, location: data/survey.xlsx,
     tier: 0, map: {Species: scientific_name}}
```

### `sources.items[].tier`

How much you trust this one source. Tier 0 means you vouch for it yourself, and its values start at 0.95.

**Type** number &middot; **Default** `3` &middot; **One of** `0`, `1`, `2`, `3`

```yaml
tier: 0
```

### `sources.items[].map`

Which column or JSON key holds which field, for a spreadsheet, CSV or endpoint.

**Type** map &middot; **Default** `matched by name`

```yaml
map: {"Alt (m)": altitude_m, Species: scientific_name}
```

### `sources.items[].ai_allowed`

Whether this source's text may be sent to the AI. Off means it is read by rules and column mapping only, and never leaves the machine.

**Type** yes or no &middot; **Default** `true` &middot; **One of** `true`, `false`

```yaml
ai_allowed: false
```

### `sources.items[].pages`

Which pages of a PDF to read.

**Type** text &middot; **Default** `all`

```yaml
pages: "12-40"
```

### `sources.items[].key_env`

The environment variable holding this endpoint's API key, so the key stays in .env and out of the project file.

**Type** text

```yaml
key_env: MY_API_KEY
```

## What you want

### `entities`

The things your dataset is about. One entity becomes one table.

**Type** map

```yaml
entities:
  plant:
    identity: [scientific_name]
    fields:
      scientific_name: {type: text}
```

The first entity listed is the subject of the dataset.

### `entities.<name>.identity`

The field that says whether two records are the same thing. Two pages naming the same scientific name become one record.

**Type** list &middot; **Default** `the first field`

```yaml
identity: [scientific_name]
```

Choose something stable: a scientific name, not a common one.

### `entities.<name>.fields`

The columns you want for this entity.

**Type** map

```yaml
fields:
  altitude_m: {type: range, unit: m}
```

### `entities.<name>.fields.<f>.type`

What kind of value this is. It decides how the text is cleaned: a range splits into a minimum and a maximum, a date becomes ISO 8601.

**Type** choice &middot; **Default** `text` &middot; **One of** `text`, `number`, `range`, `date`, `month`, `year`, `bool`, `url`, `category`

```yaml
type: range
```

### `entities.<name>.fields.<f>.unit`

The unit this column is stored in. Everything found in another unit is converted to it.

**Type** text

```yaml
unit: m
```

"3 km", "3,000 m" and "9,842 ft" all become 3000 m.

### `entities.<name>.fields.<f>.required`

Whether a record is incomplete without this value.

**Type** yes or no &middot; **Default** `false` &middot; **One of** `true`, `false`

```yaml
required: true
```

### `entities.<name>.fields.<f>.multiple`

Whether this column can hold more than one value, such as several common names. Multi-value columns get their own table.

**Type** yes or no &middot; **Default** `false` &middot; **One of** `true`, `false`

```yaml
multiple: true
```

### `entities.<name>.fields.<f>.extract`

Where to find the value on a page: a CSS selector, or `regex:` and a pattern. Several may be listed; the first that matches wins.

**Type** list &middot; **Default** `[]`

```yaml
extract: [".altitude", "regex:(\\d+)\\s*m"]
```

Without a rule, the field is filled by the AI extractor or left to a vocabulary match.

### `entities.<name>.fields.<f>.sanity`

The range or list a value must fall in. Anything outside goes to the review queue instead of the dataset.

**Type** list

```yaml
sanity: [0, 9000]
```

This is what stops a page claiming 99999 m from being believed.

### `entities.<name>.fields.<f>.vocabulary`

The controlled list this column's values must map to, from the reference tables.

**Type** text

```yaml
vocabulary: uses
```

"medicine" and "ayurvedic use" both become `medicinal`.

### `entities.<name>.fields.<f>.level`

How strictly to clean this one column, overriding `standardize.level`.

**Type** choice &middot; **Default** `the project level` &middot; **One of** `raw`, `standard`, `strict`

```yaml
level: strict
```

### `entities.<name>.fields.<f>.on_conflict`

What to do when sources disagree about this one column.

**Type** choice &middot; **Default** `the project rule` &middot; **One of** `majority`, `trusted_first`, `newest`, `keep_all_and_flag`

```yaml
on_conflict: newest
```

### `entities.<name>.fields.<f>.default`

A value to use when nothing found one. It is the only value with no source: it is stored as origin `default`, carries no confidence, and never counts as agreement.

**Type** any &middot; **Default** `none`

```yaml
default: "not assessed"
```

Not allowed on a required or multiple field.

### `entities.<name>.fields.<f>.infer`

Set to `ai` to let the model estimate this field where no rule exists. Values are capped low and always flagged.

**Type** choice &middot; **Default** `off` &middot; **One of** `ai`

```yaml
infer: ai
```

### `entities.<name>.fields.<f>.languages`

The languages this text field may appear in.

**Type** list &middot; **Default** `the project languages`

```yaml
languages: [en, hi]
```

### `relations`

How entities link to each other. Each becomes a junction table.

**Type** list &middot; **Default** `[]`

```yaml
relations:
  - {from: plant, name: grows_in, to: region}
```

You can also write it as `plant grows_in region`.

## Calculated columns

### `derived`

Columns that are calculated rather than found, each with a rule you wrote or approved.

**Type** map &middot; **Default** `{}`

```yaml
derived:
  climate_zone: {...}
```

### `derived.<name>.on`

Which entity the calculated column belongs to.

**Type** text

```yaml
on: plant
```

Quote it as `'on'` if your editor turns it into true.

### `derived.<name>.method`

How it is worked out: a formula, a lookup in bands, an if-then list, an aggregate over related records, or your own Python file.

**Type** choice &middot; **Default** `formula` &middot; **One of** `formula`, `lookup`, `rules`, `aggregate`, `code`, `describe`

```yaml
method: lookup
```

### `derived.<name>.formula`

The expression, using only the allowed functions. It is read as a syntax tree, never executed, so a project file cannot run code.

**Type** text

```yaml
formula: "convert(altitude_m.min, 'm', 'ft')"
```

### `derived.<name>.bands`

Cutoffs and the label for each band, for `method: lookup`.

**Type** map

```yaml
bands: {cutoffs: [1500, 3000], labels: [low, mid, alpine]}
```

One more label than cutoffs.

### `derived.<name>.code`

A Python file in your project that calculates the value. This is the one thing Spider runs as written, so it warns you about it.

**Type** text

```yaml
code: rarity.py
```

The file defines `def compute(values): ...`.

### `derived.<name>.if_missing`

What to do when an input is empty: leave the cell empty, use a fallback, or calculate from what exists and lower the confidence.

**Type** choice &middot; **Default** `leave_empty` &middot; **One of** `leave_empty`, `use_fallback`, `partial`

```yaml
if_missing: partial
```

### `derived.<name>.explain`

One plain sentence saying how the value is obtained. It shows in reports and exports.

**Type** text

```yaml
explain: "Zone from minimum altitude"
```

### `derived.<name>.review`

Set to `required` to hold results in the review queue until you approve them.

**Type** choice &middot; **Default** `not required` &middot; **One of** `required`

```yaml
review: required
```

### `derived.<name>.describe`

Say in words what you want instead of writing a formula. Spider drafts one for your approval and never applies it unasked.

**Type** text

```yaml
describe: "how wide its altitude band is"
```

## Cleaning values

### `standardize.level`

How hard to clean values before they are compared. `raw` keeps the text as found, `standard` unifies units, dates and spellings, `strict` also rejects anything outside a vocabulary.

**Type** choice &middot; **Default** `standard` &middot; **One of** `raw`, `standard`, `strict`

The original text is kept either way, so you can change your mind and rebuild without crawling again.

### `standardize.dates`

The form dates are stored in.

**Type** text &middot; **Default** `iso8601`

```yaml
dates: iso8601
```

### `standardize.currency`

The currency amounts are converted to.

**Type** text &middot; **Default** `INR`

```yaml
currency: INR
```

### `standardize.on_conflict`

What to do when sources still disagree after cleaning: take the value most sources give, trust your ranked list, take the newest, or keep them all and flag them.

**Type** choice &middot; **Default** `keep_all_and_flag` &middot; **One of** `majority`, `trusted_first`, `newest`, `keep_all_and_flag`

Keeping all hides nothing; the table still shows the most confident one.

### `standardize.min_confidence`

The score a value must reach to enter the dataset. Below it, the value waits in the review queue for a second source or your approval.

**Type** number &middot; **Default** `0.5`

```yaml
min_confidence: 0.5
```

One tier 3 source scores 0.40, so the default holds back anything a single blog claims.

## How it is stored

### `storage.normal_form`

How the output tables are shaped, from one wide table to one table per attribute. Every build is checked against the level and refused if it fails.

**Type** choice &middot; **Default** `3NF` &middot; **One of** `0NF`, `1NF`, `2NF`, `3NF`, `BCNF`, `4NF`, `5NF`, `6NF`

0NF to 2NF need `mode: analysis`.

### `storage.database`

Where the assembled dataset is written.

**Type** path &middot; **Default** `.spider/dataset.db`

```yaml
database: .spider/dataset.db
```

### `storage.keep_provenance`

Whether to keep the source of every value. Turning it off makes a smaller dataset and loses the audit trail.

**Type** yes or no &middot; **Default** `true` &middot; **One of** `true`, `false`

## What comes out

### `output.formats`

The simple way to ask for output: one folder or file per format.

**Type** list &middot; **Default** `[csv]`

```yaml
formats: [csv, sqlite]
```

### `output.targets`

The full way: each target picks its own format, structure and design from the same data.

**Type** list &middot; **Default** `[]`

```yaml
targets:
  - {name: app, format: sqlite, path: exports/db.sqlite}
```

### `output.targets[].format`

What kind of file to write.

**Type** choice &middot; **Default** `csv` &middot; **One of** `csv`, `json`, `sqlite`, `xlsx`, `sql`, `parquet`, `template`

### `output.targets[].shape`

`wide` gives a column per field; `long` gives a row per field and value, with its confidence and source beside it.

**Type** choice &middot; **Default** `wide` &middot; **One of** `wide`, `long`

### `output.targets[].provenance`

Where the evidence goes: its own table, extra columns beside each value, or nowhere.

**Type** choice &middot; **Default** `separate_table` &middot; **One of** `separate_table`, `columns`, `none`

### `output.targets[].nesting`

`nested` puts related records inside their parent in JSON.

**Type** choice &middot; **Default** `flat` &middot; **One of** `flat`, `nested`

### `output.targets[].naming`

How column names are written in this file.

**Type** choice &middot; **Default** `snake_case` &middot; **One of** `snake_case`, `camelCase`, `Title Case`

### `output.targets[].columns`

The columns to include, their order, their labels and their units.

**Type** list &middot; **Default** `all`

```yaml
columns:
  - {field: altitude_m, label: "Altitude (ft)", unit: ft}
```

### `output.targets[].min_confidence`

Leave out any value below this score in this file.

**Type** number

```yaml
min_confidence: 0.8
```

### `output.targets[].sort_by`

The column to sort on, with `descending: true` to reverse it.

**Type** text

```yaml
sort_by: altitude_m
```

### `output.targets[].split`

`per_table` writes a file per table; `single` stacks them into one.

**Type** choice &middot; **Default** `per_table` &middot; **One of** `per_table`, `single`

### `output.include_derived`

Whether calculated and defaulted values are written out at all.

**Type** yes or no &middot; **Default** `true` &middot; **One of** `true`, `false`

### `output.label_origin`

Whether each value is labelled extracted, derived, inferred or default.

**Type** yes or no &middot; **Default** `true` &middot; **One of** `true`, `false`

## Outside services

### `connectors`

Outside services that feed the same verification chain: a species registry, a name database, an independent measurement.

**Type** list &middot; **Default** `[]`

```yaml
connectors:
  - {name: gbif, use: [identifier]}
```

### `connectors[].for`

Which entities to ask this service about.

**Type** list &middot; **Default** `the service decides`

```yaml
for: [plant]
```

### `ai.model`

Which Claude model to use for extraction and drafting.

**Type** text &middot; **Default** `a small fast model`

```yaml
model: claude-haiku-4-5-20251001
```

