# Project Spider User Guide

Project Spider lets you describe a dataset in `spider.yaml`, collect information from permitted web pages or local files, assemble the information into structured records, review uncertain values, and export the result as CSV, JSON, SQLite, Excel, or a portable project pack.

This guide explains two ways to use the project:

1. **Command line:** the most complete and repeatable workflow.
2. **Local dashboard:** a visual interface for collecting, building, reviewing, searching, and exporting the same project data.

The command line and dashboard use the same `spider.yaml` file and the same SQLite database. You can switch between them without copying data.

## 1. Requirements

You need Python 3.9 or newer. Run the following commands from the project folder:

```bash
cd "/path/to/hackday 0.1"
python3 -m pip install -e ".[all]"
```

The `.[all]` installation includes the dashboard, file readers, standardization tools, export formats, and development dependencies. If you only need the dashboard, use:

```bash
python3 -m pip install -e ".[dashboard]"
```

The basic crawler requires `requests`, `beautifulsoup4`, and `PyYAML`, which are installed by the editable installation.

### Optional API keys

Create a `.env` file beside `spider.yaml` only if you want optional features:

```text
ANTHROPIC_API_KEY=your_key_here
BRAVE_API_KEY=your_key_here
```

The project still works without these keys. Without an Anthropic key, use CSS extraction rules and normal crawling; without a Brave key, do not use query-driven discovery.

Do not commit `.env` to source control.

## 2. Create or initialise a Spider project

If you are starting a new dataset project, create a separate working directory and initialise Spider:

```bash
mkdir my-data-project
cd my-data-project
spider init
```

This creates:

```text
my-data-project/
├── .spider/
│   └── spider.db       # local SQLite database
└── spider.yaml         # create or edit this project definition
```

Spider can find the project when you run commands from a subfolder, as long as the `.spider/spider.db` file remains in the project root.

If this repository already contains a `spider.yaml`, `.spider/spider.db`, or an `exports/` directory, you can work directly from the repository root instead of running `spider init` again.

## 3. Describe the data you want

You can write `spider.yaml` manually or let Spider draft it:

```bash
spider describe "tourist places in Chennai with their category, area and opening hours"
```

Review the generated file and then validate it:

```bash
spider check
```

You can also use an existing example file or schema:

```bash
spider describe --like wanted.csv --entity place
spider describe --from schema.sql
spider describe --from schema.json
```

For a schema that already exists, edit `spider.yaml` directly or use the **Schema and rules** screen in the dashboard.

### Important `spider.yaml` sections

| Section | Purpose |
|---|---|
| `project` | Name used in generated metadata and exports. |
| `sources` | Seed URLs, local files, keywords, crawl depth, page limit, and crawl policy. |
| `entities` | The records you want, such as `place`, `plant`, or `product`. |
| `identity` | Field or fields used to recognise the same record across sources. |
| `fields` | Values to extract, including their type, validation, and CSS selectors. |
| `connectors` | Optional external checks such as GBIF, Wikidata, geocoding, or elevation. |
| `derived` | Calculated fields based on other fields. |
| `output` | Export formats and output targets. |

A small example:

```yaml
project: chennai-places
mode: project
languages: [en, ta]

sources:
  seeds:
    - https://chennai.nic.in/tourism/tourist-places/
  keywords: [Chennai, attraction, museum, temple, park, beach]
  depth: 1
  max_pages: 100
  delay_seconds: 1.0
  follow_other_domains: false

entities:
  place:
    identity: [name]
    fields:
      name:
        type: text
        required: true
        extract: ["h1"]
      category:
        type: text
        extract: [".category"]
      area:
        type: text
        extract: [".area"]
      opening_hours:
        type: text
        extract: [".opening-hours"]

output:
  formats: [csv, sqlite, json]
```

### The settings that most affect the result

- **`seeds`:** URLs or source records where collection starts. Spider only visits what it can reach from these sources.
- **`keywords`:** words used to decide whether a page is relevant. If the list is too narrow, useful pages may be skipped.
- **`depth`:** how many links away from the seed Spider may follow. Start with `1`.
- **`max_pages`:** maximum number of pages to save for a run.
- **`identity`:** the field that identifies one real-world record. Incorrect identity rules can create duplicates or merge different records.
- **`extract`:** CSS selectors or extraction rules that identify where a value appears on a page.
- **`follow_other_domains`:** keep this `false` unless you intentionally want to follow links to other domains.

Before a full crawl, test the configuration with:

```bash
spider preview -n 5
```

This uses a small sample and helps reveal incorrect selectors, missing fields, wrong units, or unexpected duplicates.

## 4. Load reference data when needed

References help Spider standardise units, places, aliases, categories, and other controlled values.

Load the default references:

```bash
spider ref load
```

Load a bundled subject-specific preset, if one is relevant:

```bash
spider ref load --preset himalayan-plants
```

Inspect loaded references:

```bash
spider ref list
spider ref list --kind places
spider ref list --kind categories
```

## 5. Add local files or known sources

Spider can use your own data as a source. This is useful when you already have a list of places, products, plants, or other records and want Spider to enrich it.

Examples:

```bash
spider source add places.csv --tier 0 \
  --map "Place=name,Kind=category"

spider source add reports/forest_plants_2024.pdf --tier 1 --pages 12-40

spider source add survey.xlsx --tier 0 \
  --map "Species=scientific_name,Alt (m)=altitude_m"
```

A **tier 0** source is data you provide or vouch for. Higher tiers describe increasingly less trusted external sources. Spider keeps the original source, evidence, and confidence with each value.

Check the configured sources with:

```bash
spider source list
```

## 6. Collect the data

First validate the project:

```bash
spider check
```

Then crawl the configured sources:

```bash
spider crawl
```

For more information about skipped, blocked, failed, or irrelevant pages:

```bash
spider crawl --verbose
```

You can also crawl URLs directly without relying on the seeds in `spider.yaml`:

```bash
spider crawl https://example.org/catalog \
  -k product,price \
  -d 1 \
  -n 100 \
  -e name="h1" \
  -e price=".price"
```

Useful crawl options include:

| Option | Meaning |
|---|---|
| `-k`, `--keyword` | Comma-separated relevance keywords. |
| `-d`, `--depth` | Link depth to follow. |
| `-n`, `--max-pages` | Maximum pages to save. |
| `--delay` | Delay between requests to the same domain. |
| `-e name=selector` | Custom field and CSS selector. |
| `--refresh` | Re-fetch pages already stored. |
| `--verbose` | Show skipped, blocked, and failed URLs. |

Spider obeys robots.txt and uses a per-domain delay by default. It does not bypass logins, paywalls, CAPTCHAs, or anti-bot protection. Only crawl sites that permit it and follow their terms and copyright rules.

## 7. Build the structured dataset

Crawling stores source pages. Building turns those pages into entities, fields, relations, standardised values, confidence scores, and provenance:

```bash
spider build
```

If you do not want optional AI extraction for a run:

```bash
spider build --no-ai
```

A build performs the following operations:

1. Reads stored pages and local source records.
2. Extracts values using the configured rules.
3. Optionally uses AI when a rule cannot fill a field and a key is configured.
4. Checks that extracted evidence really appears in the source.
5. Standardises units, dates, names, places, and categories.
6. Merges records using the configured identity fields.
7. Preserves conflicting values and flags them for review.
8. Calculates approved derived fields.
9. Writes the assembled dataset to the local database.

The canonical database keeps provenance for each value: source URL, evidence or quote, origin, fetch time, and confidence.

## 8. Review quality and missing data

Generate a report after a build:

```bash
spider report
```

The report shows coverage, conflicts, rejected values, low-confidence values, gaps, and source health. If you have a gold/reference file, compare against it with:

```bash
spider report --gold gold.csv
```

List items waiting for a decision:

```bash
spider review
```

Typical decisions are:

```bash
spider review show <id>
spider review keep <id> 1
spider review keep <id> all
spider review reject <id>
spider review ask <id>
```

If fields are empty, run a targeted fill:

```bash
spider fill
```

To repeat targeted collection until no further useful pages are found:

```bash
spider fill --until-stable
```

For a field whose extraction selector stopped matching after a website redesign:

```bash
spider rules check
spider rules relearn <field> --apply
spider crawl --refresh
spider build
```

## 9. Inspect individual records and source evidence

Search stored pages without re-crawling:

```bash
spider search "museum Chennai"
```

Open one stored page using its ID from the search result:

```bash
spider get <page-id>
```

Inspect the source chain for a particular assembled value:

```bash
spider explain "Chennai Central" category
```

The explanation shows the accepted value, origin, confidence, supporting evidence, and source URLs. This is the recommended way to verify why a value appears in the dataset.

Read-only SQL is available for custom inspection:

```bash
spider sql "SELECT id, title, url FROM pages ORDER BY id DESC LIMIT 20"
```

The `spider sql` command is intended for read-only queries. Do not use it for `DELETE`, `UPDATE`, `DROP`, or other database modifications.

## 10. Export and use the data

Export all targets configured in `spider.yaml`:

```bash
spider export
```

Export a specific format when no output targets are configured:

```bash
spider export --format csv
spider export --format json
spider export --format sqlite
```

Create one portable dataset package:

```bash
spider export --pack --with-pages
```

Typical generated files are placed in `exports/`:

- **CSV:** useful for spreadsheets, analysis, or importing into another system.
- **JSON:** useful for APIs and application code.
- **SQLite:** useful for an application that needs related tables and keys.
- **XLSX:** useful for users who work in Excel, when the optional file dependencies are installed.
- **`provenance`:** maps each value back to its source and evidence.
- **`metadata.json`:** records the project, build time, structure, cleaning settings, and sources.
- **`README.md`:** describes the generated export and its tables.

The exported SQLite database and provenance files are the safest outputs for an application that needs traceability. The repository's current example export may contain zero rows if the example has not yet been crawled and built; run the collection workflow first.

## 11. Start the dashboard

From the Spider project root, install the dashboard extra if it is not installed:

```bash
python3 -m pip install -e ".[dashboard]"
```

Start the dashboard:

```bash
spider dashboard
```

The terminal prints a local address, normally:

```text
http://localhost:8501
```

Open that address in a browser. To use a different port:

```bash
spider dashboard --port 8502
```

To listen only on the local computer rather than the local network:

```bash
spider dashboard --local-only
```

Keep the terminal running while using the dashboard. Stop the dashboard with `Ctrl+C` in that terminal.

If the dashboard does not start, install Streamlit explicitly:

```bash
python3 -m pip install streamlit
```

Then retry `spider dashboard`.

## 12. How to use each dashboard screen

The dashboard sidebar contains five screens: **Collect**, **Review**, **Dataset**, **Schema and rules**, and **Search**. It also shows summary counts for pages, records, values, and items in review.

### 12.1 Collect

Use this screen to collect pages and build the dataset.

1. Confirm or edit the **Seed URLs** box. Put one URL per line.
2. Edit **Keywords** as comma-separated words. Leave them broad enough to find relevant pages.
3. Set **Depth**. Start with `1`.
4. Set **Max pages** to a small number for testing, such as `10` or `20`.
5. Set the per-domain **Delay**. The default protects websites from excessive requests.
6. Click **Start crawl**.
7. Watch the progress bar and event log. Saved pages show relevance and source tier; rejected or blocked pages show a reason.
8. After the crawl finishes, click **Run build**.
9. Keep **Use the AI extractor when a key is set** enabled only when you want optional AI extraction and have configured the relevant key.
10. Review the build metrics: pages read, values, records, conflicts, and rejected values.
11. Scroll down to **Pages collected so far** to inspect saved pages.

If you change the source configuration or selectors, use **Schema and rules** to save the file, then return to **Collect** and run the crawl again. Use the CLI `--refresh` option when you need to re-fetch pages already stored.

### 12.2 Review

Use **Review** for values that require a person rather than automatic acceptance.

The screen contains four tabs:

- **Conflicts:** different sources gave different values. View the options and evidence, then choose **Keep first**, **Keep second**, **Keep both**, or **Ask for more sources**.
- **Low confidence:** a value did not meet the confidence floor. Choose **Keep it anyway** or **Leave it out**.
- **Suggested:** formulas or derived fields proposed by Spider. Choose **Approve**, edit the formula and save it, or **Reject** it. AI-proposed formulas always require approval.
- **Rejected:** values rejected by sanity rules, strict standardisation, or evidence checks. Use the reason and source evidence to decide whether to change the schema or source rules.

After asking for more sources or changing a decision, return to **Collect** and run **Run build** again so the accepted information is folded into the dataset.

### 12.3 Dataset

Use **Dataset** to inspect assembled records rather than raw pages.

1. Select an entity table from **Table**, such as `place`.
2. Select a structure from **Structure**. `3NF` is the normal project default; `0NF` and `1NF` are intended for analysis mode.
3. Inspect the table shown below the selectors.
4. Check the **Coverage** progress bars for each field. They show how many records have values and how many values are derived.
5. Under **Where a value came from**, choose a record and field. The dashboard shows the value, origin, confidence, evidence, source URL, and any inputs used by a calculation.
6. Click **Write every target from spider.yaml** to generate the configured exports.
7. If **Empty cells** appear, use the information as a prompt to run `spider fill` or improve the extraction rules.

A structure that fails its normal-form check is displayed with a warning and is not written as a valid project export.

### 12.4 Schema and rules

Use this screen to edit `spider.yaml` without leaving the browser.

1. Edit the YAML in the text area.
2. Click **Check** to parse and validate the candidate configuration. Read all errors and warnings.
3. Click **Save** only after the YAML is valid.
4. Click **Reload** to discard unsaved text and reload the file from disk.

Recommended practice is to save a copy or commit the working YAML before making major changes. After saving, validate again and run a small crawl or preview before a large crawl.

### 12.5 Search

Use **Search** to find stored pages without visiting the web again.

1. Enter a word or phrase in **Query**.
2. Review the matching page title, URL, and highlighted snippet.
3. Use the URL or page ID to inspect the source further with the CLI command `spider get <page-id>`.

This screen searches collected pages, not necessarily only the final assembled records.

## 13. Recommended end-to-end workflow

For a first run, use this sequence:

```bash
# 1. Install and enter the project
python3 -m pip install -e ".[all]"
cd "/path/to/your/project"

# 2. Create or review the project definition
spider init
spider describe "the records and fields I need"
# edit spider.yaml if necessary

# 3. Validate and test cheaply
spider check
spider preview -n 5

# 4. Collect and assemble
spider crawl --verbose
spider build --no-ai

# 5. Inspect quality
spider report
spider review
spider search "important term"

# 6. Fill gaps and rebuild if needed
spider fill
spider build --no-ai

# 7. Export
spider export

# 8. Optionally use the dashboard
spider dashboard
```

For most projects, begin with a low `max_pages` value and a depth of `1`. Increase the limits only after the preview shows the right records and fields.

## 14. Troubleshooting

### `spider: command not found`

The package is not installed in the active Python environment. Run:

```bash
python3 -m pip install -e "."
```

If you use a virtual environment, activate it before running `spider`.

### `This is not a Spider project`

Run the command from the project root or specify it explicitly:

```bash
spider --project "/path/to/project" check
```

If it is a new folder, initialise it with `spider init`.

### `spider.yaml has errors`

Run:

```bash
spider check
```

Fix the listed line or field, then run the check again. Use the dashboard's **Schema and rules > Check** for the same validation.

### No pages are saved

Check the following:

- Seed URLs are reachable and include `https://` where appropriate.
- Keywords are not too restrictive.
- The requested domain is allowed by the source policy.
- The website permits crawling through robots.txt and its terms.
- The page is HTML and returns a successful response.
- The crawl depth and page limit are not too small.

Run `spider crawl --verbose` to see whether pages were skipped as irrelevant, blocked, or failed.

### Pages are saved but fields are empty

The CSS selector may not match the page layout. Use a browser's developer tools to inspect the HTML, update the field's `extract` selector in `spider.yaml`, and test with:

```bash
spider preview -n 5
```

For a changed website layout, use `spider rules check` and `spider rules relearn <field> --apply`.

### Dashboard says Streamlit is missing

Install the dashboard dependency:

```bash
python3 -m pip install -e ".[dashboard]"
```

### Dashboard opens the wrong project

Start it from the project root, or use the dashboard's project option through the CLI project selector:

```bash
spider --project "/path/to/project" dashboard
```

### The export has zero rows

A crawl alone stores pages but does not create assembled records. Run:

```bash
spider build
spider report
spider export
```

Also check that the configured entity identity fields and extraction selectors are correct.

## 15. Data protection and responsible use

Spider stores data locally in SQLite and does not send telemetry. However, optional AI extraction may send eligible page content to the configured AI provider. Mark sensitive sources as not AI-allowed where supported, and do not collect personal information unnecessarily.

Only crawl websites that permit automated access. Respect robots.txt, rate limits, terms of service, and copyright. Use stored page text for analysis and citation rather than republishing it wholesale.

## 16. Useful command reference

```bash
spider init
spider describe "..."
spider settings
spider preview -n 5
spider check
spider crawl --verbose
spider build
spider report
spider fill
spider review
spider search "..."
spider get <page-id>
spider explain "<record>" <field>
spider sql "SELECT ..."
spider export
spider export --pack --with-pages
spider ref load
spider source list
spider rules check
spider connectors
spider dashboard
```

The project README contains the deeper design explanation and the full YAML reference. This guide is intended as the practical operating procedure for collecting data and using the dashboard.
