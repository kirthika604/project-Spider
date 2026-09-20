#!/usr/bin/env bash
# Serve the two demo sites on ports 8021 and 8022.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
trap 'kill 0' EXIT
python3 -m http.server 8021 --directory "$here/demo_sites/site1_observatory" >/dev/null 2>&1 &
python3 -m http.server 8022 --directory "$here/demo_sites/site2_university"  >/dev/null 2>&1 &
echo "Demo sites on http://localhost:8021 and :8022 - Ctrl-C to stop."
wait
