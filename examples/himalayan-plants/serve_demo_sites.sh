#!/usr/bin/env bash
# Serve the three demo sites on ports 8011, 8012 and 8013.
# Different ports mean different domains, so agreement across sources is real.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
trap 'kill 0' EXIT
python3 -m http.server 8011 --directory "$here/demo_sites/site1_botany_institute" >/dev/null 2>&1 &
python3 -m http.server 8012 --directory "$here/demo_sites/site2_university_flora"  >/dev/null 2>&1 &
python3 -m http.server 8013 --directory "$here/demo_sites/site3_local_blog"        >/dev/null 2>&1 &
echo "Demo sites running on http://localhost:8011, :8012 and :8013 - press Ctrl-C to stop."
wait
