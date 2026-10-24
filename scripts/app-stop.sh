#!/usr/bin/env bash
# Stop the MAIN app livebingo (web + Postgres Machine): the whole site goes offline and running games stop being served.
# Data is kept on the volume. Asks for confirmation (YES=1 skips it).   Usage: scripts/app-stop.sh   (DRY_RUN=1: print only)
set -euo pipefail
. "$(dirname "$0")/_fly.sh"
confirm "Stop the main app? The site goes offline and any running game is interrupted."
act stop "${APP:-livebingo}"
