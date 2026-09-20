#!/usr/bin/env bash
# The five-minute demo from the design document, section 9.
# Start ./serve_demo_sites.sh in another terminal first.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
work="${1:-/tmp/spider-demo}"

step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; sleep 1; }

rm -rf "$work" && mkdir -p "$work" && cd "$work"
cp "$here/spider.yaml" "$here/gold.csv" "$here/field_survey_2025.xlsx" .

step "1. One command per project, like git"
spider init

step "2. The project file says what dataset you want"
spider check

step "3. Collect: polite, keyword-filtered, robots.txt obeyed"
spider crawl

step "4. Build: extract, verify, standardize, merge, derive"
spider build

step "5. Every value can be traced back to the sentence that states it"
spider explain "Saussurea obvallata" altitude_m

step "6. Add a source you vouch for - a spreadsheet, read by rules only"
spider source add field_survey_2025.xlsx --tier 0 \
  --map "Species=scientific_name,Alt (m)=altitude_m,Flowering=flowering_month,District=region"
spider build > /dev/null

step "7. Spider notices a column no website will ever print, and asks"
spider derive list
spider derive approve altitude_ft

step "8. Coverage, conflicts, rejected values, source health, measured accuracy"
spider report --gold gold.csv

step "9. One dataset, three shapes: app database, analyst sheet, web feed"
ls -la exports/
sqlite3 -header -column exports/plants.db "select scientific_name, altitude_m, climate_zone, season from plant" 2>/dev/null || true

printf '\nEverything above ran offline, with no API key.\n'
