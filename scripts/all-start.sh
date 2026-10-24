#!/usr/bin/env bash
# Start BOTH apps: the main app first, then whisper.   Usage: scripts/all-start.sh
set -euo pipefail
. "$(dirname "$0")/_fly.sh"
"$(dirname "$0")/app-start.sh"
"$(dirname "$0")/stt-start.sh"
