# Project Spider

**You describe the dataset you wish existed; Spider roams the web, gathers the
scattered pieces, links them together, and builds it for you as a clean
database with the source of every value.**

Spider is project-local, like git. Run `spider init` once in your project
folder, say what data you want in `spider.yaml`, and read the result back
instantly, offline, from a local SQLite file.

Other tools give you pages or records from one site at a time. Spider gives
you the dataset you described, joined across many sites and languages, with a
source, an exact quote and a confidence score for every cell.

---

## Install

```bash
pip install -e .
```

Only three things are required: `requests`, `beautifulsoup4` and `PyYAML`.
Everything else is optional and degrades gracefully:

```bash
pip install -e ".[all]"      # AI extraction, Excel, PDF, dashboard, fuzzy matching
```

| Extra | Adds |
| --- | --- |
| `ai` | Claude extraction, describe-to-schema, derivation suggestions |
| `standardize` | `pint`, `dateparser`, `rapidfuzz` (built-in fallbacks exist for all three) |
| `files` | Excel and PDF sources |
| `dashboard` | the Streamlit dashboard |
| `dev` | pytest |

Keys live in a `.env` file beside `spider.yaml`, never in the project file:

```
ANTHROPIC_API_KEY=...       # optional: AI extraction
BRAVE_API_KEY=...           # optional: query-driven discovery for `spider fill`
```

Without any key Spider still crawls, extracts with rules, standardizes,
merges, derives, checks and exports. The demo below needs no key at all.

---

## Try it in two minutes

The example ships with three small websites so you can see cross-source
agreement, a real conflict and a rejected value without touching the internet.

```bash
./examples/himalayan-plants/serve_demo_sites.sh    # leave running
```

In another terminal:

```bash
mkdir demo && cd demo
cp ../examples/himalayan-plants/{spider.yaml,gold.csv} .
spider init
spider ref load --preset himalayan-plants
spider check
spider crawl
spider build
spider report --gold gold.csv
```

You get a 3NF SQLite database, a flat Excel sheet and nested JSON in
`exports/`, plus a report showing coverage, conflicts, rejected values and
measured accuracy against the facts you listed in `gold.csv`.

---

## "I want places in Chennai with their coordinates"

A worked answer, because this is the shape most projects take: you have a
list of things, and the facts you want are not printed on any page.

No web page reliably states a latitude. That is what a **connector** is for -
an outside service that feeds the same verification chain as a crawl. So:
your list is the source, OpenStreetMap supplies the coordinates, and
everything else is worked out from those two numbers.

```yaml
sources:
  mode: only_listed                 # nothing is crawled; your list is the source
  items:
    - {id: my_list, type: csv, location: places.csv, tier: 0,
       map: {Place: name, Kind: kind}}

entities:
  place:
    identity: [name]
    fields:
      name:      {type: text, required: true}
      latitude:  {type: number, sanity: [12.6, 13.6]}   # Chennai's bounds
      longitude: {type: number, sanity: [79.9, 80.5]}

connectors:
  - {name: geocode, for: [place], within: "Chennai, Tamil Nadu, India", country: in}

derived:
  distance_from_centre_km:
    on: place
    formula: "distance_km(latitude, longitude, 13.0827, 80.2707)"
    round: 2
  zone:
    on: place
    method: lookup
    inputs: [distance_from_centre_km]
    bands: {cutoffs: [3, 8], labels: [central, inner, outer]}
```

```bash
spider init && spider check && spider crawl && spider build
```

and you have:

| name | latitude | longitude | distance_from_centre_km | side_of_city | zone |
| --- | --- | --- | --- | --- | --- |
| Chennai Central | 13.0826 | 80.2763 | 0.61 | east | central |
| Marina Beach | 13.0533 | 80.2833 | 3.55 | south | inner |
| Kapaleeshwarar Temple | 13.0334 | 80.2687 | 5.48 | south | inner |
| Guindy National Park | 13.0000 | 80.2280 | 10.29 | south | outer |

The whole project is in [examples/chennai-places](examples/chennai-places),
ready to copy. Swap `places.csv` for your own list, or point `sources.seeds`
at a page that lists the places and let Spider read the names off it.

**The connector runs inside `spider build`.** You never have to remember an
order: build reads the pages, works out the records, asks the services, then
calculates. Its answers are cached, so a rebuild asks for nothing.

## Writing your first `spider.yaml`

Every setting has a plain-English entry in the tool itself:

```bash
spider settings              # all of them, grouped
spider settings depth        # one, with its default and an example
spider settings trust        # searches names and descriptions
```

The full reference is [docs/spider-yaml.md](docs/spider-yaml.md), generated
from the same list the command reads, so the two cannot disagree.

Here is the smallest useful file, with what each line means:

```yaml
project: himalayan-plants     # names the export files

sources:
  seeds:                      # where the crawl starts: pages you already know
    - https://institute.example.org/flora
  keywords: [plant, herb]     # a page is saved only if it contains one of these
  depth: 1                    # 0 = seeds only, 1 = also the pages they link to
  max_pages: 100              # stop after this many are saved
  delay_seconds: 1.0          # wait this long between requests to one site
  trust_tiers:                # 1 official, 2 established, 3 everything else
    institute.example.org: 1  # tier decides a value's starting confidence

entities:
  plant:                      # one entity becomes one table
    identity: [scientific_name]   # how Spider knows two pages mean one plant
    fields:
      scientific_name: {type: text, required: true, extract: [".sci"]}
      altitude_m:
        type: range           # "3,000 to 4,500 m" splits into a min and a max
        unit: m               # anything in feet or km is converted to this
        sanity: [0, 9000]     # outside this goes to review, not the dataset
        extract: [".altitude"]    # the CSS selector that holds it on the page
```

The five that matter most when you start:

| Setting | In one line | If you get it wrong |
| --- | --- | --- |
| `seeds` | The pages the crawl starts from. | Nothing is found: Spider only goes where you point it. |
| `keywords` | What makes a page worth keeping. | Too narrow saves nothing; empty saves everything. |
| `depth` | How many links away from a seed to go. | Each step multiplies the pages. Start at 1. |
| `identity` | The field that decides two records are the same thing. | The same plant becomes two rows, or two plants become one. |
| `extract` | Where the value sits on the page (a CSS selector or `regex:`). | The column stays empty unless the AI extractor fills it. |

Do not guess at any of them: run `spider preview -n 5` and look at five real
rows before spending a full crawl on the settings.

## The seven steps

| Step | Command | What happens |
| --- | --- | --- |
| 1. Initialise | `spider init` | creates `.spider/spider.db` and the reference database |
| 2. Describe | `spider describe "plants, where they grow, what they are used for"` | drafts `spider.yaml` for you to approve |
| 3. Load references | `spider ref load units.csv places.csv` | units, places, categories and aliases to check against |
| 4. Validate | `spider check` | names the field, the reason and the fix - never a traceback |
| 5. Collect | `spider crawl` | polite crawl; saves pages that match your keywords |
| 6. Build | `spider build` | extract, standardize, merge, resolve conflicts, derive, write |
| 7. Review | `spider report`, `spider fill`, `spider export` | coverage, gaps, targeted re-crawl, exports |

Repeat 5 to 7 until the report satisfies you.

### Every command

| Command | Purpose |
| --- | --- |
| `spider init` | create the project and reference databases |
| `spider describe "..."` | draft `spider.yaml` from words, `--like` an example file, or `--from` a schema (`--ask` for the questions) |
| `spider settings [name]` | what every setting means, with its default and an example |
| `spider preview` | run the whole chain on a few pages before the full crawl |
| `spider check` | validate the project file |
| `spider crawl [urls...]` | collect pages (`-k` keywords, `-d` depth, `-n` limit, `-e name=CSS`) |
| `spider build` | assemble the dataset (`--normalize 6NF`, `--mode analysis`, `--no-ai`) |
| `spider report` | coverage, conflicts, rejections, gaps, source health (`--gold gold.csv`) |
| `spider review list/keep/reject/ask/forget` | decide what the build flagged; decisions survive rebuilds |
| `spider fill` | crawl only for the empty cells (`--until-stable` repeats until it stops helping) |
| `spider derive list/approve/edit/reject/run` | review calculated fields |
| `spider explain <record> <field>` | the chain from a value back to its source pages |
| `spider export` | write every target in `output.targets` (`--target`, `--format`, `--pack`) |
| `spider summarise` | a short AI summary of each stored page |
| `spider rules check / relearn` | find extraction rules that broke, and re-learn them |
| `spider search "..."` / `get <id>` / `stats` | read the collected pages |
| `spider sql "select ..."` | read-only SQL over the project database |
| `spider ref load/list/edit` | the reference tables |
| `spider source add/test/list` | your own links, PDFs, spreadsheets and folders |
| `spider connectors` | GBIF, Wikidata and an elevation cross-check |
| `spider dashboard` | the local web dashboard |

---

## How a value earns its place

Nothing enters the dataset without evidence. Each candidate passes five gates:

1. **Keyword prefilter** - cheap, no AI, so most pages never cost a call.
2. **Evidence-locked extraction** - rules first; the AI extractor must return
   `value`, `unit` and the exact sentence that states it.
3. **Quote proof** - the sentence must really be on the page and must contain
   the value. This is what blocks invented values.
4. **Sanity rules** - a range or allowed list per field (`sanity: [0, 9000]`).
   Failures go to the review queue, not the dataset.
5. **Agreement scoring** - after standardization, values are compared across
   independent domains:

| Situation | Confidence |
| --- | --- |
| Your own data (tier 0) | 0.95 |
| One tier 1 source (government, research institute) | 0.80 |
| One tier 2 source (university, established organisation) | 0.60 |
| One tier 3 source (everything else) | 0.40 |
| Each further independent domain that agrees | +0.15, up to 0.99 |
| Domains disagree | every value kept, each -0.20, flagged for review |

Values below `min_confidence` wait in the review queue until a second source
confirms them or you approve them.

```
$ spider explain "Saussurea obvallata" altitude_m
Saussurea obvallata (plant) . altitude_m

  * 3000-4500 m   [extracted]  confidence 0.99  (accepted)
      as written on the page: It grows at 3,000 to 4,500 m above sea level.
      source:   http://localhost:8011/brahmakamal.html (tier 1)
      source:   http://localhost:8012/saussurea-obvallata.html (tier 2)
      source:   http://localhost:8013/brahmakamal-hindi.html (tier 3)
      agreed by: localhost:8011, localhost:8012, localhost:8013
```

---

## Standardization

Every source formats data differently, so values are converted to one standard
form before they are compared. The original text is always kept beside the
standard value.

| What | Becomes | Example |
| --- | --- | --- |
| Units | one base unit per column | "3 km", "3,000 m" and "9,842 ft" all become 3000 m |
| Dates | ISO 8601 | "20 Sep 26" and "20/09/2026" become 2026-09-20 |
| Ranges | minimum and maximum | "3,000 to 4,500" becomes min 3000, max 4500 |
| Places | official names | "Garwhal" and "Garhwal" match the same district |
| Categories | your controlled vocabulary | "medicine" and "ayurvedic use" become `medicinal` |
| Names | Unicode normalized and transliterated | a Hindi name links to its Latin spelling |

Set `standardize.level` to `raw` (keep the text as found), `standard` (the
default) or `strict` (only values in the vocabulary; anything else goes to
review). The level can be set per field, and because the canonical copy is
never changed you can lower it and rebuild without re-crawling.

---

## Defaults

Two different things are called defaults, so they are worth separating.

**What a setting falls back to when you say nothing.** Every one of these is
what the design document specifies, and `spider check` shows the value in
force:

| Setting | Default |
| --- | --- |
| `mode` | `project` (refuses anything below 3NF) |
| `storage.normal_form` | `3NF` |
| `storage.keep_provenance` | `true` |
| `standardize.level` | `standard` |
| `standardize.on_conflict` | `keep_all_and_flag` |
| `standardize.min_confidence` | `0.5` |
| `standardize.dates` | `iso8601` |
| `derive_policy` | `suggest` (nothing is derived without your approval) |
| `derived.*.if_missing` | `leave_empty` |
| `sources.mode` | `start_here` |
| `sources.delay_seconds` | `1.0` per domain |
| `sources.follow_other_domains` | `false` |
| `sources.max_ai_pages` | `100` |

**A value for a column when nothing found one.** A field can declare a
`default:`, and it is the one value in the dataset with no source:

```yaml
entities:
  plant:
    fields:
      conservation_status: {type: text, default: "not assessed"}
```

Because Spider's whole promise is that every value has a source, a default is
fenced off rather than blended in:

- it is applied **last**, so it can never displace something a page stated or
  a formula worked out;
- it is stored with its own origin, `default`, and a confidence of **0**;
- it is never evidence: it cannot confirm another value, it does not count
  towards source agreement, and a connector that "agrees" with a default
  replaces it rather than endorsing it;
- it is kept out of the history that feeds rule re-learning, so a broken
  extractor is never taught to reproduce a default;
- `spider report` counts it apart from what was found, and `spider check`
  warns you that you wrote a value with no source;
- exports label it, and `include_derived: false` leaves it out with the
  calculated values.

```
$ spider report
  plant.conservation_status        [====================] 100.0%  5/5  (5 by default)

$ spider explain "Saussurea obvallata" conservation_status
  * not assessed    [default]  confidence 0.0  (accepted)
      evidence: "declared in spider.yaml as the default for conservation_status"
```

A `required: true` field cannot have a default, and neither can a `multiple`
one; `spider check` rejects both.

## Statistics, for analysts

`count(moon via has_moon)` looks along a relation. These look down a whole
column - every accepted value of one field, across every record - so a row
can say where it sits in the dataset as a whole:

```yaml
derived:
  further_than_typical:
    on: place
    formula: "zscore(distance_from_centre_km over place)"
    round: 2
    explain: "Standard deviations from the average distance"
  distance_rank:
    on: place
    formula: "rank(distance_from_centre_km over place, 'asc')"
  median_distance_km:
    on: place
    formula: "median(distance_from_centre_km over place)"
```

| Written | Gives |
| --- | --- |
| `mean(f over e)`, `median`, `stdev`, `variance` | the usual summaries of a column |
| `smallest`, `largest`, `spread`, `total`, `records` | its range, sum and count |
| `percentile(f over e, 90)` | the value 90% of records fall below |
| `zscore(f over e)` | how far **this** record is from the mean, in standard deviations |
| `rank(f over e)`, `rank(f over e, 'asc')` | this record's place in the column |
| `normalize(f over e)` | where this record falls between smallest and largest, 0 to 1 |
| `share(f over e)` | this record's value as a fraction of the total |
| `count_distinct(f over e)`, `mode(f over e)` | for text columns |
| `correlation(a over e, b over e)` | how strongly two columns move together |

A column statistic is the same on every row, which is what makes it useful
beside a per-record value: you can see the median and this record's distance
from it in the same table, and export it straight to a notebook.

## You choose the structure

Spider always keeps one canonical copy internally - `entities`, `attributes`
and `relations`, in 6NF, one value per row with its source and confidence -
and builds the output at the level you ask for.

| Level | Output | Available |
| --- | --- | --- |
| 0NF | one wide table, repeats joined in the cell | analysis mode only |
| 1NF | repeats split into rows | analysis mode only |
| 2NF | related attributes moved out | analysis mode only |
| 3NF | entity tables plus junction tables | **default**, both modes |
| BCNF, 4NF, 5NF | stricter splits | both modes |
| 6NF | one table per attribute, with validity columns | both modes |

`mode: project` (the default) refuses anything below 3NF, so an app never gets
a messy structure. `mode: analysis` allows a flat sheet for a spreadsheet or a
notebook. **Every output is checked against its level, and a build that fails
its check is not written.**

```bash
spider build --normalize 6NF --mode analysis
spider build --normalize 0NF               # refused in project mode, with the reason
```

---

## Derived values

Some columns never appear on any page because they are calculated. Spider
never invents one silently: every derived value follows a rule you wrote or
approved, and carries its formula, its inputs and its origin label.

```yaml
derived:
  climate_zone:
    on: plant
    method: lookup
    inputs: [altitude_m.min]
    bands: {cutoffs: [1500, 3000], labels: [subtropical, temperate, alpine]}
    explain: "Zone from minimum altitude"
  rarity_score:
    on: plant
    describe: "higher when the plant grows high and in few regions"
    review: required          # Spider drafts the formula; you approve it
```

Each derivation takes the settings from the design document: `method`
(`formula`, `lookup`, `rules`, `aggregate` or `code`), `inputs`, `unit` and
`round`, `if_missing` (`leave_empty`, `use_fallback`, `partial`), `explain`,
`review` and `describe`.

Allowed in formulas:

| | |
| --- | --- |
| arithmetic | `+ - * / // % **`, comparisons, `and` / `or` / `not` |
| numbers | `min` `max` `avg` `sum` `count` `abs` `round` `floor` `ceil` `sign` `clamp` |
| maths | `sqrt` `pow` `log` `log10` `exp`, and the constants `pi` `e` `tau` |
| values | `band()` `if()` `convert()` `midpoint()` `season_of()` `year_of()` |
| text | `concat()` `lower()` `upper()` `len()` |
| across records | `count(<entity> via <relation>)` |

and nothing else. Formulas are walked as a syntax tree, never `eval`'d, so a
project file cannot run code. A derivation may read another derivation, and
the engine orders them so inputs are ready first.

Quantitative data works the way you would write it: `5.972e24` is read as
5.972 × 10²⁴ (not 5.972), a sanity range of `[1.0e20, 1.0e30]` is a range and
not a list of two allowed values, and a large number keeps its exponent
instead of printing nineteen digits it never measured.

```yaml
derived:
  volume_m3:
    on: planet
    formula: "(4 / 3) * pi * radius_m ** 3"
  density_kg_m3:                       # reads the derivation above
    on: planet
    formula: "mass_kg / volume_m3"
    round: 0
  escape_velocity_kms:
    on: planet
    formula: "sqrt(2 * 6.674e-11 * mass_kg / radius_m) / 1000"
    round: 2
```

`examples/planets` builds exactly this from two web pages and checks the
results against published NASA figures - Earth comes out at 5513 kg/m3
(published 5514) and 11.19 km/s escape velocity, from nothing but a mass and
a radius.

### When a formula is not enough

`method: code` runs a small Python file you wrote, for the cases a formula
cannot express. It is the one deliberate exception to the sandbox, so it is
limited to files inside your project, `spider check` warns you about it, and
an error is reported against that record instead of stopping the build.

```yaml
derived:
  rarity_score:
    on: plant
    method: code
    code: rarity.py          # def compute(values): ...
    inputs: [altitude_m.min, altitude_m.max]
    round: 1
    explain: "higher when it grows high and in a narrow band"
```

```python
# rarity.py - called once per record, with that record's values
def compute(values):
    low, high = values.get("altitude_m__min"), values.get("altitude_m__max")
    if low is None:
        return None
    span = (high - low) if high is not None else 0
    return (low / 1000) + (2 if span and span < 1500 else 0)
```

### When you cannot write the rule either

`describe:` lets you say what you want in words. Spider drafts a formula,
checks it against your own fields, and puts it in the review queue -
**never into the dataset** - and approving it writes `review: required`:

```yaml
derived:
  altitude_span:
    on: plant
    describe: "how wide the altitude band it grows in is"
    review: required
```

```
$ spider report
Suggested derivations (1)
  altitude_span on plant: altitude_m.max - altitude_m.min
    how wide its altitude band is
    [ spider derive approve altitude_span | edit | reject ]
```

Drafting needs `ANTHROPIC_API_KEY`; without it Spider says so and asks you to
write the `formula:` yourself.

### Derivations Spider proposes on its own

After a build, Spider looks at every requested field that is still empty and
proposes how to fill it - a unit conversion, a count over a relation, a
reference lookup, or (last) an AI-proposed formula, which always needs
approval:

```
$ spider report
Suggested derivations (1)
  altitude_ft on plant: convert(altitude_m.min, 'm', 'ft')
    would fill 5 empty cells; samples: Picrorhiza kurroa: 9842.52
    [ spider derive approve altitude_ft | edit | reject ]

$ spider derive approve altitude_ft
```

Approved suggestions are written back into `spider.yaml` with
`suggested_by: system` and the approval date, so the build stays repeatable.
`derive_policy` chooses how much happens without asking: `suggest` (default),
`auto_safe` (exact conversions and counts applied automatically) or `off`.

---

## Four ways to say what you want

The document calls these channels; they combine freely.

```bash
# 1. plain words
spider describe "plants of Uttarakhand, where they grow and their uses"

# 2. an example of the output you want - the columns and units are copied
spider describe --like wanted.csv --entity plant

# 3. a structure you already have - names and keys carry over
spider describe --from schema.sql
spider describe --from existing.db
spider describe --from schema.json

# 4. write spider.yaml yourself, or edit it in the dashboard
```

Add `--ask` for the clarifying questions. There are at most five, Spider asks
only what it cannot decide safely, and each one lists its choices with the
suggested answer marked - pressing Enter is an answer, not a skip:

```
  3. How tidy does the output have to be?
       1) project              separate, linked tables (3NF) - for an app (suggested)
       2) analysis             one flat table is allowed - for a spreadsheet
     > 2

  4. When two sources disagree, what should happen?
       1) keep_all_and_flag    keep every value and flag it for you (suggested)
       2) majority             take the value most sources give
       3) trusted_first        take the one from the site you trust most
       4) newest               take the most recently published
     >
```

Run it where there is no terminal and Spider prints the answers it assumed
instead of hanging. Then look before you leap:

```bash
spider preview -n 5
```

`preview` fetches a handful of pages, runs the whole chain on them - extract,
verify, standardize, merge, derive - and shows the real rows in your chosen
structure, along with any field nothing filled and why. It uses few AI calls,
catches wrong field names and wrong units early, and rebuilds the full dataset
afterwards so it leaves nothing behind.

## Your own sources

The best source is often one you already know. Files stay on your machine, and
a source marked `ai_allowed: false` is never sent to the AI.

```bash
spider source add https://institute.example.org/flora --tier 1
spider source add reports/forest_plants_2024.pdf --tier 1 --pages 12-40
spider source add data/field_survey.xlsx --tier 0 \
    --map "Species=scientific_name,Alt (m)=altitude_m"
```

Tier 0 means you vouch for it: those values start at 0.95. Evidence for a file
is its sheet and cell or its PDF page, so every value is still traceable:

```
evidence: "Species (survey_2025!A2): Rhododendron arboreum, Alt (m) (survey_2025!B2): 1500-3300"
source:   file:///.../field_survey_2025.xlsx#survey_2025!2
```

A JSON endpoint and an RSS or Atom feed work too; an API key is named, not
written, so it stays in `.env`:

```yaml
sources:
  items:
    - {id: gbif_api, type: api, tier: 1,
       location: "https://api.gbif.org/v1/species/search?q=Saussurea",
       map: {scientificName: scientific_name, "measurement.elevation": altitude_m}}
    - {id: dept_news, type: feed, tier: 2,
       location: "https://forest.example.gov.in/feed.xml"}
```

Nested JSON is flattened, so `measurement.elevation` reaches a nested field.

`sources.mode` decides how far Spider may wander: `only_listed` (never leaves
your list), `start_here` (follows links within those sites) or
`start_here_and_discover` (also searches, and new sites enter at tier 3).

---

## Connectors

Optional, cached, and each one feeds the same verification chain. A connector
that has no key or is unreachable disables itself and nothing else breaks.

```yaml
connectors:
  - {name: gbif,      use: [validate_scientific_name, identifier]}
  - {name: wikidata,  use: [aliases], languages: [en, hi, ta], for: [plant]}
  - {name: elevation, use: [check_altitude], tolerance_m: 300}
```

`for:` limits a connector to certain entity types. Without it each connector
uses its own rule - GBIF, for instance, only asks about entities identified by
a scientific name, so it is never asked about a district. Every response is
cached in the project database, so a second run costs nothing and works
offline.

```
$ spider connectors
  gbif       5 calls, 5 values, 5 identifiers      # Saussurea obvallata -> GBIF 5404479
  wikidata   9 calls, 96 names, 5 identifiers      # जटामांसी, அதிவிடயம், burans
  skipped: gbif: nothing to ask about 'region'
```

A cross-check changes confidence, never the value: if the elevation service
disagrees with the pages by more than the tolerance, both are kept and the
disagreement is flagged.

---

## Saving it the way you need it

Each target in `output.targets` chooses its own format, structure and design,
and `spider export` writes them all from one canonical copy:

```yaml
output:
  targets:
    - {name: app_database, format: sqlite, path: exports/plants.db,
       normal_form: 3NF, provenance: separate_table}
    - {name: audit, format: csv, path: exports/audit, mode: analysis,
       normal_form: 0NF, shape: long, provenance: columns, sort_by: field}
    - {name: analyst, format: xlsx, path: exports/plants.xlsx, mode: analysis,
       normal_form: 0NF, sort_by: altitude_m, descending: true,
       columns: [{field: scientific_name, label: "Scientific name"},
                 {field: altitude_m, label: "Altitude (ft)", unit: ft}]}
    - {name: feed, format: json, path: exports/plants.json,
       nesting: nested, naming: camelCase}
    - {name: everything, format: csv, path: exports/all.csv, split: single}
    - {name: columnar, format: parquet, path: exports/parquet}
    - {name: handout, format: template, path: exports/report.md,
       template: report.md.j2}
```

| Choice | Options |
| --- | --- |
| Format | `csv`, `json`, `sqlite`, `xlsx`, `sql`, `parquet`, `template` (Jinja) |
| Shape | `wide` (a column per field) or `long` (a row per field and value) |
| Nesting | flat tables, or nested JSON with related records inside |
| Splitting | a file per table, or `split: single` for one file |
| Provenance | `separate_table`, `columns` (beside each value), or `none` |
| Names | per-column `label`, and `naming: snake_case / camelCase / Title Case` |
| Units | per-column `unit:`, converted on the way out |
| Filters | `min_confidence`, `include_derived` |
| Sorting | `sort_by` with `descending` |

The exported SQLite file carries real primary and foreign keys, so an app can
rely on it - and another Spider project can read it back with
`spider describe --from plants.db`.

### One portable file

```bash
spider export --pack --with-pages
```

writes a `.spiderpack.zip`: the SQLite dataset, CSV copies, the provenance for
every value, the `spider.yaml` that produced it, a manifest and a README -
and, with `--with-pages`, the collected pages themselves. Someone else can
open it with any zip tool, query it offline and check any value back to the
sentence it came from.

## When a site changes its layout

A CSS rule stops matching when a site is redesigned. Spider keeps what each
page said last time, so it can notice and repair that:

```
$ spider rules check
  FIELD             RULE        MATCHED  STATE
  plant.altitude_m  .altitude   0/1      BROKEN

$ spider rules relearn altitude_m --apply
Re-reading 1 page(s) to see how they look now ...
  was: .altitude
  now: .elevation-value
  works on 1 of 1 sample page(s) (confidence 1.0)
```

It re-reads the pages where it knows the answer, finds where that value lives
now, prefers a specific selector over a bare tag, and keeps the old rule as a
fallback. Nothing is applied without `--apply`.

## The look

The dashboard wears the web: a midnight field with a faint web tiled behind
it, arachnid red for anything that acts or warns, web-silver blue for links
and evidence, and Archivo Black for the display voice. The mark is an
original drawing - eight arched legs, each ending on a node, because a crawl
and the graph it builds are the same shape. It comes in three forms:

| File | Used for |
| --- | --- |
| `spider/assets/logo.svg` | the masthead; inherits `currentColor` for its legs |
| `spider/assets/mark.svg` | the browser tab, and anywhere small |
| `spider/theme.py` `TERMINAL_MARK` | the terminal, on `spider init` |

The palette lives in `spider/assets/theme.css` as CSS variables, and Streamlit's
own widgets read `.streamlit/config.toml`, so both stay in step. Every text
pair clears WCAG AA (the primary button uses a deeper red than the accent so
white on it reaches 4.9:1), the browser surfaces are themed rather than left
default - selection, caret, focus ring, scrollbar - and the one animated
moment is the webbing settling behind the title, which honours
`prefers-reduced-motion`.

The look is original work: a spider and a web, not any character's insignia.

## How much can it collect?

Be honest with yourself about the arithmetic before planning a large run.
Spider fetches politely: one request per site per second by default. It
fetches several **sites** at once (`sources.workers`, 5 by default), so
throughput scales with the number of sites, not with the limit on any one of
them.

| Shape of the job | Roughly |
| --- | --- |
| 1 site, 1s delay | ~3,600 pages an hour |
| 10 sites, 5 workers | ~18,000 pages an hour |
| a CSV, Excel or JSON endpoint | as fast as the file reads - thousands a second |

So: tens of thousands of pages is an afternoon. **A million pages by crawling
is not a Spider job** - at one request a second per site it is arithmetic, not
software, and any tool that promises otherwise is either ignoring robots.txt
or using bulk dumps. For datasets that size, feed it bulk sources instead:
an API endpoint, an open-data CSV, a database extract (`spider source add`
takes all three), where the row count is limited by your disk rather than by
politeness.

The store is SQLite, which is comfortable into the low millions of rows.
What Spider is for is the shape of the problem, not the size: facts scattered
over many sites that have to be matched, cleaned, cross-checked and joined.
If the data is already in one place and one format, you do not need a crawler.

## Politeness and good behaviour

- `robots.txt` and `Crawl-delay` are obeyed; the default delay is 1 second per
  domain, and Spider identifies itself in its User-Agent.
- A failed page never stops a crawl, and data is committed after every saved
  page, so an interrupted crawl loses nothing.
- Everything stays on your machine. No telemetry.
- `spider sql` is read-only.
- Spider will not bypass logins, paywalls or CAPTCHAs.

You are responsible for crawling only sites that allow it and for respecting
each site's terms and copyright. Stored text is for analysis and citation, not
for republishing wholesale; exclude pages that may hold personal information.

---

## Layout

```
spider/
  cli.py            commands
  spec.py           spider.yaml models and validation
  describe.py       plain words to a draft schema
  crawl/            fetch, robots, queue, discovery
  extract/          parser, AI extractor, quote proof, sanity rules
  standardize/      units, dates, names, vocabularies, conflicts
  assemble/         entity merging, relations, confidence, build
  derive/           formulas, suggestions, lineage
  store/            SQLite schema, normal-form builders, export
  ref/              reference tables and seed CSVs
  capture.py        schema from an example file or an existing structure
  reference.py      what every setting means (read by `settings` and the docs)
  pack.py           the portable dataset pack
docs/spider-yaml.md generated settings reference
  connectors/       GBIF, Wikidata, elevation
  dashboard.py      the local dashboard
tests/              parser, chain, formulas, normal forms, CLI
examples/himalayan-plants/
```

Run the tests with `pytest` - they use saved HTML samples and need no network.
