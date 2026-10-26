#!/usr/bin/env bash
# Back up the cloud database (Fly app livebingo, container db) into a plain SQL file on THIS computer.
# Safe at any time, also during a game: pg_dump reads one consistent snapshot and writes nothing in the cloud.
# Usage: scripts/fly-db-backup.sh                 -> data/backup/cloud_full_<YYYYmmdd_HHMMSS>.sql (gitignored)
#        OUT=<file> scripts/fly-db-backup.sh      -> that file;  APP=<fly app>;  DRY_RUN=1 prints the commands only
#        TARGET=local DB_NAME=<db>                -> the local docker compose Postgres (rehearsals)
# Restore: scripts/fly-db-restore.sh <file>        (docs/operations.md, "Backup and restore of the cloud database")
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
. "$HERE/_fly.sh"
. "$HERE/_flydb.sh"
cd "$ROOT"

OUT="${OUT:-data/backup/cloud_full_$(date +%Y%m%d_%H%M%S).sql}"
mkdir -p "$(dirname "$OUT")"
if [ -n "${DRY_RUN:-}" ]; then
  echo "[dry run] fly ssh console --app $APP --container db -C \"pg_dump -U $DB_USER -d $DB_NAME -h 127.0.0.1 --no-owner\" > $OUT"
  exit 0
fi

echo "== backing up $DB_NAME of $([ "$TARGET" = local ] && echo "the LOCAL docker db" || echo "$APP") -> $OUT"
PART="$OUT.part"
trap 'rm -f "$PART"' EXIT
pg_dump_to_stdout > "$PART"
if ! dump_ok "$PART"; then
  echo "FAILED: the dump is empty or incomplete (no 'dump complete' line). Nothing was written to $OUT." >&2
  exit 1
fi
mv "$PART" "$OUT"
trap - EXIT
echo "ok: $(du -h "$OUT" | cut -f1) | tables with data: $(grep -c '^COPY ' "$OUT") | newest migration: $(grep -oE '^[0-9]{4}_[a-z0-9_]+\.sql' "$OUT" | sort | tail -1)"
echo "Keep it off the repo (data/ is gitignored); it contains player names and IPs."
