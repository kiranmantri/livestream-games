# Operations: the cloud app (Fly.io app `livebingo`)

Everyday commands for running the production app. Architecture: `docs/architecture.md`. Release checklist:
`docs/validation/deploy.md` (top). All commands run from the repo root on your computer (needs the `fly` CLI, logged in).

## Scripts

| Script | What it does | Safe during a game? |
|---|---|---|
| `scripts/fly-db-backup.sh` | Dumps the cloud database to `data/backup/cloud_full_<timestamp>.sql` (plain SQL) | yes (read-only snapshot) |
| `scripts/fly-db-restore.sh <file>` | Puts a backup back into the cloud (side database + rename swap, see below) | no: it replaces the live data |
| `VERIFY_ONLY=1 scripts/fly-db-restore.sh <file>` | Proves a backup restores (row counts), then throws the copy away | yes (live data untouched) |
| `scripts/fly-deploy.sh` | Active-game check, git check, stages API keys, `fly deploy --ha=false` (no backup!) | warns and asks |
| `scripts/fly-secrets.sh` | Stages GROQ / XAI / OPENROUTER keys from `.env` as Fly secrets (names only printed) | yes (applied on the next deploy) |
| `scripts/app-stop.sh` / `scripts/app-start.sh` | Takes the whole site offline / back online (data stays on the volume) | stop: no |

Common switches: `DRY_RUN=1` (print only), `YES=1` (no questions), `APP=<fly app>`.

## Backup and restore of the cloud database

### Take a backup (any time, about 10 s)
    scripts/fly-db-backup.sh
    # ok: 4.3M | tables with data: 16 | newest migration: 0014_host_and_ai_independent.sql
The file has a `.part` name until pg_dump has finished, and it is only kept when the last line pg_dump writes
("dump complete") is there, so a broken connection never leaves a file that looks like a backup.
Files in `data/backup/` are gitignored. They contain player names and IP addresses: keep them off shared drives and delete old ones.
Take one before every release that has a migration (the deploy checklist says so) and before any manual change to cloud data.

### Check that a backup restores (does not touch the live data)
    VERIFY_ONLY=1 scripts/fly-db-restore.sh data/backup/cloud_full_<timestamp>.sql
Loads the file into a side database `livegames_restore` in the cloud, prints the row counts (live vs. backup) per table and
drops the side database. About 20 s for a 4 MB backup.

### Restore (replaces the live data)
    scripts/fly-db-restore.sh data/backup/cloud_full_<timestamp>.sql
What it does, in order:
1. Refuses a file that is not a complete pg_dump. Lists active games and asks before going on: their marks and claims since
   the backup will be gone (they are still in the safety backup of step 2).
2. Safety backup of the CURRENT data: `data/backup/cloud_full_<ts>_before_restore.sql`.
3. Loads the file into the side database `livegames_restore` in ONE transaction (an error leaves nothing behind; live data untouched).
4. Prints the row counts side by side and asks you to type the app name (`livebingo`).
5. Swap by rename (a second or two): new connections to `livegames` are refused, open ones closed, `livegames` becomes
   `livegames_before_<ts>` and `livegames_restore` becomes `livegames`.
6. Restarts the Machine. On boot the app applies any migration newer than the backup to the restored data, then `/healthz`
   is checked (up to ~90 s). The players' pages come back by themselves; their cookies only work if they exist in the backup.

The previous data is NOT deleted: it stays in the cloud as the database `livegames_before_<ts>`. The script prints two commands at the end:
- **Rollback** (swap the names back + restart) if the restore was a mistake.
- **Drop** `livegames_before_<ts>` once you are happy (the volume is 3 GB; each copy is a few MB today).

### Rehearse a restore locally
`TARGET=local` runs the same scripts against the local docker compose Postgres. Use a scratch database name:

    docker compose exec -T db psql -U livegames -c "CREATE DATABASE lgr"
    TARGET=local DB_NAME=lgr YES=1 scripts/fly-db-restore.sh data/backup/cloud_full_<timestamp>.sql
    docker compose exec -T db psql -U livegames -c "DROP DATABASE lgr WITH (FORCE)" -c "DROP DATABASE lgr_before_<ts> WITH (FORCE)"

### Limits (be aware)
- These backups are MANUAL and live on your computer only (no schedule). Fly also takes DAILY snapshots of the volume
  (kept 5 days): `fly volumes list --app livebingo` -> `fly volumes snapshots list <vol id>`. They are a second safety net, but
  restoring one means a NEW volume (and Machine) from the snapshot, i.e. the whole Postgres data directory, not one database.
- A restore loses everything written after the backup (games, marks, phrase edits). Take a fresh backup right before restoring.
- Migrations are never undone: a backup taken BEFORE a migration is migrated forward on restart. To go back to old code, restore and deploy
  the old image together (`fly releases --app livebingo`).
- The scripts assume the layout in `deploy/fly.machine.json` (container `db`, user and database `livegames`). Override with
  `DB_USER` / `DB_NAME` / `APP` if that changes.

## Other everyday checks
    fly status --app livebingo
    fly logs --app livebingo --no-tail | grep -E "applied|up to date|phrases:|Uvicorn|fact-checker|stt |rror"
    curl -s -o /dev/null -w "%{http_code}\n" https://livebingo.fly.dev/healthz            # 200
    fly ssh console --app livebingo --container db -C "psql -U livegames -d livegames -h 127.0.0.1 -c \"select game_type, status, created_at from game_sessions where status='active'\""
Live feed problems (chat, speech-to-text, fact-checker): `docs/validation/stt.md` ("STT down" diagnosis) and `docs/validation/checker.md`.
