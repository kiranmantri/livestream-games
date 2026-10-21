#!/bin/sh
# Start-up: apply pending SQL migrations, seed the phrase table if (and only if) it is EMPTY, then run the app.
# Both steps are idempotent, so restarting the container is safe.
set -eu
python -m app.db
if [ -f deploy/seed/phrases_seed.sql ]; then
  python -m corpus.cli load          # SQL seed (made from the cloud DB); only runs if the phrases table is EMPTY
fi
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-server-header
