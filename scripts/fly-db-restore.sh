#!/usr/bin/env bash
# Restore a backup made by scripts/fly-db-backup.sh into the cloud database (Fly app livebingo).
# The live database is NEVER overwritten in place:
#   1. the file is checked (complete dump), active games are reported;
#   2. a SAFETY backup of the current cloud data is taken first (scripts/fly-db-backup.sh);
#   3. the file is loaded into a side database "livegames_restore" (one transaction: all or nothing);
#   4. row counts: current vs. the restored copy, side by side; you confirm by typing the app name;
#   5. swap by RENAME: livegames -> livegames_before_<ts> (kept for rollback), livegames_restore -> livegames;
#   6. the Machine restarts: the app reconnects and applies any newer migration to the restored data; /healthz is checked.
# Usage: scripts/fly-db-restore.sh <backup.sql>
#   VERIFY_ONLY=1   steps 1, 3, 4 only, then drops the side database: proves a backup restores, live data untouched
#   YES=1           no questions (still refuses an incomplete file);  APP=<fly app>;  SKIP_SAFETY_BACKUP=1 (not recommended)
#   TARGET=local DB_NAME=<scratch db>   rehearse the whole thing (swap included) on the local docker compose Postgres; no restart
# Rollback after a swap: see the commands printed at the end (they swap the names back). Docs: docs/operations.md
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
. "$HERE/_fly.sh"
. "$HERE/_flydb.sh"
cd "$ROOT"

FILE="${1:-}"
[ -n "$FILE" ] || { echo "usage: $0 <backup.sql>   (VERIFY_ONLY=1 to only test the file)" >&2; exit 2; }
[ -n "${DRY_RUN:-}" ] && { echo "DRY_RUN is not supported here: use VERIFY_ONLY=1 (it changes nothing live)" >&2; exit 2; }
SIDE="${DB_NAME}_restore"
TS="$(date +%Y%m%d_%H%M%S)"
OLD="${DB_NAME}_before_${TS}"

echo "== 1. checking $FILE"
dump_ok "$FILE" || { echo "REFUSED: $FILE is not a complete plain pg_dump (missing 'dump complete' or COPY lines)" >&2; exit 1; }
echo "ok: $(du -h "$FILE" | cut -f1), $(grep -c '^COPY ' "$FILE") tables with data, newest migration in it: $(grep -oE '^[0-9]{4}_[a-z0-9_]+\.sql' "$FILE" | sort | tail -1)"

if [ -z "${VERIFY_ONLY:-}" ]; then
  echo "== active games on $APP"
  games="$(printf '%s\n' "SELECT game_type || '|' || count(*) FROM game_sessions WHERE status = 'active' GROUP BY game_type;" | psql_in "$DB_NAME" -At || true)"
  if [ -n "$games" ]; then
    echo "ACTIVE (type|count):"; echo "$games"
    echo "Restoring replaces them with what the backup has (their marks/claims since the backup are lost, except in the safety backup)."
    confirm "Continue anyway?"
  else
    echo "none"
  fi
  if [ -z "${SKIP_SAFETY_BACKUP:-}" ]; then
    echo "== 2. safety backup of the CURRENT data"
    OUT="data/backup/cloud_full_${TS}_before_restore.sql" "$HERE/fly-db-backup.sh"
  fi
fi

echo "== 3. loading the file into the side database $SIDE"
printf '%s\n' "DROP DATABASE IF EXISTS $SIDE WITH (FORCE);" "CREATE DATABASE $SIDE;" | psql_in postgres
if ! psql_in "$SIDE" --single-transaction -o /dev/null < "$FILE"; then
  echo "FAILED to load the file: nothing changed in $DB_NAME. The side database $SIDE is dropped." >&2
  printf '%s\n' "DROP DATABASE IF EXISTS $SIDE WITH (FORCE);" | psql_in postgres || true
  exit 1
fi

echo "== 4. row counts (table: current -> restored)"
CUR="$(row_counts "$DB_NAME")"
NEW="$(row_counts "$SIDE")"
python3 - "$CUR" "$NEW" <<'PY'
import sys
cur = dict(l.split("|") for l in sys.argv[1].splitlines() if "|" in l)
new = dict(l.split("|") for l in sys.argv[2].splitlines() if "|" in l)
for t in sorted(set(cur) | set(new)):
    a, b = cur.get(t, "-"), new.get(t, "-")
    print(f"  {t:<22} {a:>8} -> {b:>8}" + ("" if a == b else "   *"))
PY

if [ -n "${VERIFY_ONLY:-}" ]; then
  printf '%s\n' "DROP DATABASE IF EXISTS $SIDE WITH (FORCE);" | psql_in postgres
  echo "VERIFY_ONLY: the backup restores cleanly. The side database was dropped; $DB_NAME was not touched."
  exit 0
fi

if [ -z "${YES:-}" ]; then
  read -r -p "Type the app name ($APP) to REPLACE the live database with the restored copy: " answer
  [ "$answer" = "$APP" ] || { echo "cancelled. The side database $SIDE is left for inspection (drop it with the command below)."; \
    echo "  printf 'DROP DATABASE $SIDE WITH (FORCE);\\n' | fly ssh console --app $APP --container db -C \"psql -U $DB_USER -h 127.0.0.1 -d postgres -f -\""; exit 1; }
fi

echo "== 5. swap: $DB_NAME -> $OLD, $SIDE -> $DB_NAME"
# connections are refused first, so the app cannot reconnect between the terminate and the rename
if ! printf '%s\n' \
    "ALTER DATABASE $DB_NAME ALLOW_CONNECTIONS false;" \
    "SELECT count(pg_terminate_backend(pid)) AS closed FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();" \
    "SELECT pg_sleep(1);" \
    "SELECT count(pg_terminate_backend(pid)) AS closed FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();" \
    "ALTER DATABASE $DB_NAME RENAME TO $OLD;" \
    "ALTER DATABASE $SIDE RENAME TO $DB_NAME;" \
    "ALTER DATABASE $OLD ALLOW_CONNECTIONS true;" | psql_in postgres -At; then
  echo "SWAP FAILED: re-opening $DB_NAME for connections. Check the database list below." >&2
  printf '%s\n' "ALTER DATABASE $DB_NAME ALLOW_CONNECTIONS true;" | psql_in postgres || true
  printf '%s\n' "SELECT datname, datallowconn FROM pg_database ORDER BY 1;" | psql_in postgres -At >&2 || true
  exit 1
fi

if [ "$TARGET" = "local" ]; then
  echo "== 6. TARGET=local: no Machine restart (restart your local app yourself if it uses $DB_NAME)"
else
  echo "== 6. restarting the Machine (the app reconnects and migrates the restored data forward if needed)"
  for id in $(machines_of "$APP"); do fly machine restart "$id" --app "$APP"; done
  ok=""
  for _ in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "https://$APP.fly.dev/healthz" || true)"
    [ "$code" = "200" ] && { ok=1; break; }
    sleep 3
  done
  [ -n "$ok" ] && echo "healthz: 200" || echo "WARNING: /healthz is not 200 after ~90 s: check 'fly logs --app $APP'" >&2
fi

cat <<EOF
== done. The previous live data is kept as database $OLD (plus the safety backup file in data/backup/).
Rollback (put the previous data back):
  printf 'ALTER DATABASE $DB_NAME ALLOW_CONNECTIONS false;\\nSELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '\\''$DB_NAME'\\'' AND pid <> pg_backend_pid();\\nALTER DATABASE $DB_NAME RENAME TO ${DB_NAME}_restored_$TS;\\nALTER DATABASE $OLD RENAME TO $DB_NAME;\\n' \\
    | fly ssh console --app $APP --container db -C "psql -U $DB_USER -h 127.0.0.1 -d postgres -f -" && fly machine restart <id> --app $APP
When you are happy with the restore, free the space:
  printf 'DROP DATABASE $OLD;\\n' | fly ssh console --app $APP --container db -C "psql -U $DB_USER -h 127.0.0.1 -d postgres -f -"
EOF
