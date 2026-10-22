#!/bin/sh
# Start-up: apply pending SQL migrations, seed phrases / fallacies if (and only if) their tables are EMPTY, load the replay rows, then run the app.
# Both steps are idempotent, so restarting the container is safe.
set -eu
python -m app.db
if [ -f deploy/seed/phrases_seed.sql ]; then
  python -m corpus.cli load          # SQL seed (made from the cloud DB); only runs if the phrases table is EMPTY
fi
if [ -f deploy/seed/fallacies_seed.sql ]; then
  python -m corpus.cli fallacies-load   # fallacy list; only runs if the fallacies table is EMPTY (admin edits are never overwritten)
fi
if ls deploy/seed/replay_*.sql >/dev/null 2>&1; then
  python -m corpus.cli replay-load      # test/demo replay of the Falácias feed; skipped if its rows already exist
fi
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-server-header
