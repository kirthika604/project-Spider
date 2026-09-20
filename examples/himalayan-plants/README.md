# Demo: Himalayan medicinal plants

Three small websites that run on your own machine, so the whole pipeline can
be shown without the internet, without an API key, and without depending on
anyone's site staying up.

| Site | Port | Tier | What it shows |
| --- | --- | --- | --- |
| Botany institute | 8011 | 1 | four plants with altitude, flowering month, region and uses |
| University flora | 8012 | 2 | overlapping records: one agrees, one disagrees |
| Local blog | 8013 | 3 | a Hindi page, an altitude in feet, and an impossible 99999 m |

## Run it

```bash
./serve_demo_sites.sh          # leave this running
```

```bash
mkdir /tmp/demo && cd /tmp/demo
cp <this folder>/spider.yaml <this folder>/gold.csv .
spider init --preset himalayan-plants && spider check && spider crawl && spider build
spider report --gold gold.csv
```

## What to look for

- **Merging** - "Brahmakamal", "ब्रह्मकमल" and "Saussurea obvallata" become one
  record, because all three pages state the scientific name.
- **Agreement** - three independent domains give the same altitude, so it
  scores 0.99. The blog writes it as "9,842 ft", which standardizes to 3000 m
  and counts as agreement, not conflict.
- **Conflict** - the institute says Jatamansi grows at 3,000 to 5,000 m, the
  university says 2,800 to 4,000 m. Both are kept, both are flagged, and the
  output table shows the more confident one.
- **Rejection** - "99999 m" fails the sanity range and never enters the
  dataset; it appears in the report with its reason.
- **Derivation** - `climate_zone` and `season` are calculated, labelled
  `derived`, and inherit the confidence of their inputs. `altitude_ft` is
  suggested, not assumed: approve it with `spider derive approve altitude_ft`.
- **Three shapes, one dataset** - a 3NF SQLite file for an app, a flat Excel
  sheet for an analyst, nested camelCase JSON for a web feed.

## Add your own sources

```bash
spider source add field_survey_2025.xlsx --tier 0 \
    --map "Species=scientific_name,Alt (m)=altitude_m,Flowering=flowering_month,District=region"
spider build
```

The spreadsheet is tier 0 - you vouch for it - so its values start at 0.95 and
fill the one plant the websites never give an altitude for. Its evidence is
the sheet and cell it came from.
