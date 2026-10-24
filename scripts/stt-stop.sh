#!/usr/bin/env bash
# Stop the whisper (speech-to-text) app livebingo-stt so it stops costing money between lives. The game keeps working: the transcript
# shows the "fora do ar" banner and players can type phrases in the slide panel.   Usage: scripts/stt-stop.sh   (DRY_RUN=1: print only)
set -euo pipefail
. "$(dirname "$0")/_fly.sh"
act stop "${STT_APP:-livebingo-stt}"
