#!/usr/bin/env bash
# Start the MAIN app livebingo again after scripts/app-stop.sh (about 30 s until /healthz answers).
# Usage: scripts/app-start.sh   (DRY_RUN=1: print only)
set -euo pipefail
. "$(dirname "$0")/_fly.sh"
act start "${APP:-livebingo}"
[ -n "${DRY_RUN:-}" ] || echo "check: curl -s -o /dev/null -w \"%{http_code}\\n\" https://livebingo.fly.dev/healthz   (expect 200 after ~30 s)"
