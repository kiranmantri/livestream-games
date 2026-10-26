#!/usr/bin/env bash
# One safe release to Fly: (1) look for an ACTIVE game, (2) warn about uncommitted/unpushed work, (3) stage the API keys of .env, (4) fly deploy --ha=false.
# Does NOT take a backup and does NOT run migrations by hand: a release with a migration needs the backup step of the checklist in docs/validation/deploy.md first.
# Usage: scripts/fly-deploy.sh      (YES=1: no questions; DRY_RUN=1: print the fly commands only; APP=<fly app>; ENV_FILE=<path>)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
. "$HERE/_fly.sh"
cd "$ROOT"
APP="${APP:-livebingo}"

echo "== 1. active games on $APP"
if [ -n "${DRY_RUN:-}" ]; then
  echo "[dry run] would query game_sessions over fly ssh"
elif out="$(fly ssh console --app "$APP" --container db \
      -C "psql -U livegames -d livegames -h 127.0.0.1 -At -F '|' -c \"select game_type, count(*) from game_sessions where status='active' group by 1\"" 2>&1)"; then
  games="$(printf '%s\n' "$out" | grep -E '^[a-z]+\|[0-9]+$' || true)"
  if [ -n "$games" ]; then
    echo "ACTIVE games (type|count):"; echo "$games"
    confirm "A deploy restarts the Machine for about 30 s and interrupts these games. Deploy anyway?"
  else
    echo "none"
  fi
else
  echo "could not check (is the Machine stopped?): $out"
  confirm "Deploy without knowing whether a game is running?"
fi

echo "== 2. git"
if [ -n "$(git status --porcelain)" ] || [ -n "$(git log '@{u}..HEAD' --oneline 2>/dev/null || true)" ]; then
  echo "warning: uncommitted or unpushed changes (the checklist says: commit and push before deploying)"
  confirm "Deploy anyway?"
else
  echo "clean and pushed"
fi

echo "== 3. secrets"
"$HERE/fly-secrets.sh" || confirm "No secrets were staged. Continue with the deploy?"

echo "== 4. deploy"
run fly deploy --ha=false --app "$APP"

echo "== done. Verify:"
echo "  fly status --app $APP"
echo "  curl -s -o /dev/null -w '%{http_code}\n' https://$APP.fly.dev/healthz        # 200"
echo "  fly logs --app $APP --no-tail | grep -E 'up to date|phrases:|Uvicorn|stt '"
