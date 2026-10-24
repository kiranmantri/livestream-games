#!/usr/bin/env bash
# Start the whisper (speech-to-text) app livebingo-stt. Do it a minute or two BEFORE a live game (the model needs some seconds to load;
# chunks that arrive earlier are dropped).   Usage: scripts/stt-start.sh   (DRY_RUN=1: print only)
set -euo pipefail
. "$(dirname "$0")/_fly.sh"
act start "${STT_APP:-livebingo-stt}"
