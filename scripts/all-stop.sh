#!/usr/bin/env bash
# Stop BOTH apps: whisper first, then the main app (asks for confirmation; YES=1 skips it).   Usage: scripts/all-stop.sh
set -euo pipefail
. "$(dirname "$0")/_fly.sh"
"$(dirname "$0")/stt-stop.sh"
"$(dirname "$0")/app-stop.sh"
