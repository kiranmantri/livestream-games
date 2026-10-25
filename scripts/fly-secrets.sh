#!/usr/bin/env bash
# Stage the API keys of the local .env as Fly secrets (applied by the next deploy; nothing restarts now).
# Only GROQ_API_KEY / XAI_API_KEY / OPENROUTER_API_KEY that a container of deploy/fly.machine.json receives; DB/admin/session secrets are NEVER synced
# (see deploy/env_secrets.py). Values are piped to fly and never printed.
# Usage: scripts/fly-secrets.sh      (DRY_RUN=1: list the names only; ENV_FILE=<path> to use another file; APP=<fly app>)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
. "$HERE/_fly.sh"
cd "$ROOT"
APP="${APP:-livebingo}"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
PY="${PYTHON:-uv run python}"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE not found: copy .env.example to .env and fill in the keys" >&2; exit 1; }
if [ -n "${DRY_RUN:-}" ]; then
  echo "[dry run] would stage these secrets on app $APP:"
  $PY -m deploy.env_secrets --env "$ENV_FILE" --names
  exit 0
fi
$PY -m deploy.env_secrets --env "$ENV_FILE" | fly secrets import --stage --app "$APP"
echo "staged. They are applied by the next deploy (scripts/fly-deploy.sh). Check: fly secrets list --app $APP"
