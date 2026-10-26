# Validation: Docker and Fly.io deployment

Release checklist FIRST, then the history of the Docker/Fly setup and its gotchas.
Split out of AGENTS.md (see its "Validation docs" index). Blocks are in the order they were written;
later blocks supersede earlier ones where they disagree. Expected test counts are as of when each block was written.

### Deploying to Fly.io - checklist (every release)
    # 0. Local: tests green and everything committed + pushed.
    uv run ruff check . && uv run pytest -q                    # expect: All checks passed! / N passed
    uv run migrate                                             # applies new migrations to the DEV DB first (cloud applies its own on boot)
    # 1. NO active game in the cloud (the deploy restarts the Machine for ~30 s; data is safe, players lose the page briefly).
    fly ssh console --app livebingo --container db -C "psql -U livegames -d livegames -h 127.0.0.1 -At -c \"select status, count(*) from game_sessions group by 1\""
    #    expect: no "active|N" line. If there is one: finish the game in /admin first (or agree on the interruption).
    # 2. Backup (plain SQL; tested: restores cleanly into a scratch DB). Mandatory when the release has a migration, esp. DROP/ALTER.
    scripts/fly-db-backup.sh                                   # -> data/backup/cloud_full_<ts>.sql (docs/operations.md)
    #    expect: "ok: 4.3M | tables with data: 16 | newest migration: ..."; it refuses to keep an incomplete dump. Gitignored; delete old ones.
    # 3. Deploy (ALWAYS --ha=false):
    fly deploy --ha=false                                      # expect: "Machine ... is now in a good state"
    # 4. Verify:
    fly logs --app livebingo --no-tail | grep -E "applied|up to date|phrases:|Uvicorn|rror"    # expect "applied: 000N_*.sql" (or "up to date"), "phrases: {'skipped': ...}", "Uvicorn running"
    curl -s -o /dev/null -w "%{http_code}\n" https://livebingo.fly.dev/healthz               # expect 200
    curl -s -o /dev/null -w "%{http_code}\n" https://livebingo.fly.dev/admin                 # expect 401
    fly ssh console --app livebingo --container db -C "psql -U livegames -d livegames -h 127.0.0.1 -At -c \"select name from schema_migrations order by 1 desc limit 1\" -c \"select count(*) from phrases\""
    #    expect: the newest migration file name; the phrase count unchanged. Then play/open /admin once in a browser.
    # Rollback (code only; NOT tested): `fly releases --app livebingo` -> `fly deploy --ha=false --image <registry.fly.io/livebingo:deployment-...>`
    #   of the previous release. Migrations are NOT undone (0006 dropped ingested_videos for good): for a bad migration restore the
    #   step-2 backup with scripts/fly-db-restore.sh <file> (side DB + rename swap; docs/operations.md). Never TRUNCATE the cloud DB.
    # Phrases are edited in the cloud (/admin/phrases); the seed file only fills an EMPTY table. To bring the cloud phrases to
    #   local: dump `COPY (SELECT * FROM phrases ORDER BY 1) TO STDOUT WITH (FORMAT csv, HEADER, NULL '\N')` via fly ssh into
    #   data/backup/cloud_phrases.csv, then `uv run python -m deploy.make_seed --from-dump data/backup` (see the Fly section above).

### Validation for Task 9 (Docker image, full compose, e2e smoke test)
    # Production-like run in a SEPARATE project (does not touch your dev DB / port 8000):
    export COMPOSE_PROJECT_NAME=lgsmoke POSTGRES_PASSWORD=smokepw1 ADMIN_PASSWORD=smokeadmin1 SESSION_SECRET=smokesecret1 APP_PORT=8088
    docker compose -f docker-compose.yml up -d --build   # -f without the override: Postgres NOT published to the host
    docker compose -f docker-compose.yml ps              # expect: app and db "Up ... (healthy)"
    docker compose -f docker-compose.yml logs app        # expect: "applied: 0001..0004", "phrases: {'upserted': N ...}", "Uvicorn running"
    SMOKE_URL=http://127.0.0.1:8088 SMOKE_ADMIN_PASSWORD=smokeadmin1 uv run pytest tests/e2e -q   # expect: 1 passed
    docker compose -f docker-compose.yml restart app     # expect logs: "up to date" + phrases "skipped: ... already has N rows"
    docker compose -f docker-compose.yml down -v         # reset (-v deletes the DB volume of THIS project only)
    # Real deployment: set POSTGRES_PASSWORD (URL-safe), ADMIN_PASSWORD (not change-me), SESSION_SECRET in .env, then
    #   docker compose -f docker-compose.yml up -d --build
    # Put a TLS reverse proxy in front: keep APP_BIND=127.0.0.1, set COOKIE_SECURE=1 and TRUSTED_PROXIES=<proxy IP>.
    # The e2e test needs a stack with NO active game; it plays one game and leaves an ended session.

### Validation for the Fly.io deployment (app `livebingo`, region gru, ONE Machine with two containers)
    # Files: fly.toml, deploy/fly.machine.json (containers db=postgres:17 + app; each lists its own `secrets`),
    #   deploy/make_seed.py (dev DB -> deploy/seed/phrases_seed.sql), docker/entrypoint.sh (migrate -> seed if EMPTY -> uvicorn).
    uv run python -m deploy.make_seed         # regenerate the seed from the dev DB (prior scores, no play data)
    fly deploy --ha=false                     # ALWAYS --ha=false: a volume belongs to one Machine
    fly status --app livebingo                # expect: 1 machine "started" in gru
    fly logs --app livebingo --no-tail        # expect: "applied: 0001..0005" (first boot), "phrases: {...}", "Uvicorn running"
    curl -s -o /dev/null -w "%{http_code}\n" https://livebingo.fly.dev/healthz        # expect 200
    curl -s -o /dev/null -w "%{http_code}\n" https://livebingo.fly.dev/admin          # expect 401
    fly ssh console --app livebingo --container db -C "psql -U livegames -d livegames -h 127.0.0.1 -c 'select count(*) from phrases'"
    # Persistence check: fly machine restart <id>; logs must say "phrases: {'skipped': ...}" and games must still be there.
    # Gotchas learned: (1) secrets are NOT injected into containers unless listed under the container's "secrets";
    #   (2) the postgres image's ENV PGDATA overrides the container "env", so PGDATA is exported in the db entrypoint
    #   (data lives in /data/pgdata on the volume, which has lost+found at its root); (3) CLIENT_IP_HEADER=fly-client-ip
    #   makes MAX_USERS_PER_IP use the real client IP (only safe behind a proxy that overwrites that header);
    #   (4) the SQL seed runs only when `phrases` is EMPTY, so a restart never resurrects deleted phrases.
    # NEVER truncate the cloud DB: it holds real games and admin edits. See docs/backlog.md for follow-ups.

### Validation for Task 7 (Falácias on Fly: entrypoint seeds, XDG_CACHE_HOME)
    # docker/entrypoint.sh now runs, after migrate + phrases: `corpus.cli fallacies-load` (only if the fallacies table is EMPTY: admin edits are never
    #   overwritten, and `fallacies-top` is NOT run at boot) and `corpus.cli replay-load` (16,260 rows, skipped if present). deploy/fly.machine.json: XDG_CACHE_HOME=/tmp/.cache.
    # Smoke (separate compose project, as in Task 9): logs show "applied: 0001..0009", "fallacies: {'inserted': 97}", "replay: {'inserted': 16260}", "Uvicorn running";
    #   a Falácias start detected the horizontal live (Ko_lIgMJudY, source_mode live) and the chat worker stored chat rows from inside the container (yt-dlp works there).
    # The Fly rootfs is writable, so tempfile.mkdtemp in /tmp works; an app without a home dir only needs XDG_CACHE_HOME for yt-dlp's cache.

### Validation for S3 (STT on Fly) - SUPERSEDED: Groq is the default; the self-hosted whisper app is gone
    # The whisper.cpp `small` app `livebingo-stt` (shared-cpu-4x) could not keep up: 13 s chunks took > 60 s, requests timed out ("failed to encode / client disconnected").
    #   Provider spike (spikes/stt_compare.py, 5 min of pt-BR): Groq whisper-large-v3-turbo 19% (WER vs YouTube captions), 0.9 s per chunk, US$0.04/h, 0 empty chunks;
    #   Grok 16%, 0.6 s, US$0.10/h, 1 empty chunk; local small models 27-28%. Decision (user): Groq is the default. ffmpeg stays in the app image.
    # Files now: deploy/fly.machine.json app env STT_PROVIDER=groq, STT_LANGUAGE=pt (no STT_URL), secret GROQ_API_KEY listed under the app container's "secrets".
    # Removed: deploy/stt/ and scripts/stt-*.sh, all-*.sh (scripts/app-start.sh / app-stop.sh remain). Whisper support stays in the code (STT_PROVIDER=whispercpp + STT_URL), unused.
    # Cloud steps (YOU; the key never goes in a file or in the chat):
    #   1. copy the Groq key to the clipboard, then:   fly secrets set GROQ_API_KEY="$(pbpaste)" --app livebingo --stage
    #   2. fly secrets list --app livebingo            # expect GROQ_API_KEY "Staged"
    #   3. deploy (checklist below), then:   fly ssh console --app livebingo --container app -C "printenv STT_PROVIDER STT_LANGUAGE"   # expect groq / pt (never print the key)
    #   4. start a Falacias game on a horizontal live; the pane fills in ~15 s;  fly logs --app livebingo --no-tail | grep "stt "   # none = working
    #   5. only after it works:  fly apps destroy livebingo-stt   (stops the whisper Machine for good; or `fly machine stop 827944b79991e8 --app livebingo-stt` first to keep a way back)
    # GOTCHA: Fly MERGES a container's env on deploy: deleting STT_URL from deploy/fly.machine.json did NOT remove it from the running Machine (printenv still showed it).
    #   Deleted env needs "NAME": "" in the file or a machine update; the code now ignores STT_URL for groq/grok instead.
    # Rollback: remove GROQ_API_KEY/STT_PROVIDER from the config (the page falls back to the typed phrase box).

### Validation for .env, OpenRouter and the Fly secret scripts (app/config.py, deploy/env_secrets.py, scripts/fly-secrets.sh, scripts/fly-deploy.sh)
    uv run pytest tests/test_config_env.py tests/test_env_and_deploy.py tests/test_spike_checker.py -q     # expect: 84 passed (red run first: 14 failed + 1 error + a collection error)
    # .env: `cp .env.example .env`, fill it in, never commit it (.gitignore + .dockerignore). app/config.py `load_env()` uses python-dotenv (now a declared dependency; the Docker
    #   image skips the `checker` group): real env vars win, no ${VAR} interpolation, a missing file is fine. The spike (spikes/checker.py) reads GROQ_API_KEY / OPENROUTER_API_KEY from it too.
    #   New in .env.example: STT_PROVIDER, STT_LANGUAGE, GROQ_API_KEY, XAI_API_KEY, OPENROUTER_API_KEY. Your existing .env is NOT touched: append those lines yourself.
    # OpenRouter (spike only): `--models openrouter:openai/gpt-oss-120b` (OpenAI-compatible, https://openrouter.ai/api/v1, OPENROUTER_API_KEY). Hosted models default to --output-mode twostep.
    #   Cost per claim comes from OpenRouter's public price list (GET /models, fetched once; offline or unknown model => no cost shown, never a guess). ":free" models cost 0 but have tight rate limits (not measured).
    #   NOT verified with a real key: that your chosen OpenRouter model/provider handles tools and the step-2 json_schema; try `--output-mode tool` or pick another model if it 400s.
    # docker compose: reads .env by itself; the app container now gets STT_PROVIDER, STT_LANGUAGE, GROQ_API_KEY, XAI_API_KEY AT RUN TIME. Secrets are never build args/ENV (they would stay in image layers).
    #   Check: docker compose --env-file .env -f docker-compose.yml config | grep -E "STT_|GROQ"      (prints the key: do not paste the output anywhere)
    # Fly: scripts/fly-secrets.sh stages ONLY GROQ_API_KEY / XAI_API_KEY / OPENROUTER_API_KEY that a container of deploy/fly.machine.json receives (today only GROQ_API_KEY).
    #   DATABASE_URL, POSTGRES_PASSWORD, ADMIN_PASSWORD, SESSION_SECRET are NEVER synced: the local values would lock the cloud app out of its own database. To let the app receive XAI or
    #   OPENROUTER keys, list them under the app container's "secrets" in deploy/fly.machine.json first.
    #   DRY_RUN=1 scripts/fly-secrets.sh      # names only; DRY_RUN=1 scripts/fly-deploy.sh   # the whole flow without touching Fly
    #   scripts/fly-deploy.sh                 # 1 active-game check (asks), 2 git clean/pushed check (asks), 3 stage keys, 4 `fly deploy --ha=false`; YES=1 skips the questions
    #   It does NOT take a backup: a release with a migration still needs the backup step of the deploy checklist first.
    # STT silently OFF was a trap (a .env with GROQ_API_KEY but no STT_PROVIDER => typed-phrase box, nothing in the log). sttworker.off_reason() now names the cause and ensure()
    #   logs it ONCE per cause at WARNING ("stt is off, the page shows the typed-phrase box: STT_PROVIDER is not set ..."). tests/test_stt_off_reason.py (10, red first). After editing .env RESTART
    #   uvicorn (--reload watches .py only) and start a NEW game (the message is rendered at page load).
    # Real checks done (me): the scripts against a STUB fly (order, stdin, no value printed, active-game refusal), DRY_RUN against the real .env (names only), compose rendered with a throwaway env file.
    #   NOT run: a real `fly secrets import` / `fly deploy` with these scripts.

### Validation for the cloud DB backup / restore scripts (task 6, user 2026-10-26): scripts/fly-db-backup.sh, scripts/fly-db-restore.sh, scripts/_flydb.sh
    # Guide: docs/operations.md, "Backup and restore of the cloud database".
    bash -n scripts/fly-db-backup.sh scripts/fly-db-restore.sh scripts/_flydb.sh             # syntax
    scripts/fly-db-backup.sh                                     # expect: "ok: <size> | tables with data: 16 | newest migration: 0014_..."
    VERIFY_ONLY=1 scripts/fly-db-restore.sh data/backup/<that file>   # expect: a table of "current -> restored" counts, then
    #   "VERIFY_ONLY: the backup restores cleanly ... livegames was not touched"; afterwards the cloud database list is only
    #   postgres livegames template0 template1 (the side database is gone).
    # Done by me:
    #   - errors propagate: `printf 'select 1/0;' | psql_in livegames` exits non-zero over fly ssh (1) and locally (3).
    #   - cloud VERIFY_ONLY with the 16:49 backup: 19.5 s; counts differ only by play since the backup (+5 users, +11 marks) and the
    #     0014 row in schema_migrations; side database dropped (checked with pg_database).
    #   - full rehearsal on the LOCAL docker Postgres (TARGET=local DB_NAME=lgr): dev copy as "live", the cloud backup as the file.
    #     Swap done, lgr = cloud data (200 users), lgr_before_<ts> = the old data (58 users), safety backup written.
    #   - swap under load: one connection held in pg_sleep(30) + a client reconnecting every 0.3 s -> 2 connections closed, swap OK.
    #   - truncated file -> "REFUSED", exit 1; complete file with a bad COPY -> load error, "FAILED to load ... side database dropped",
    #     live data unchanged (200 users), exit 1; the printed rollback SQL (rename back) works.
    # NOT done: a real swap on Fly (it replaces the live data; run it only when you need it). The Machine-restart + healthz part of
    #   step 6 is therefore only exercised by the deploys, not by this script.

### Validation for the docs refresh + physical architecture diagram (task 10, user 2026-10-26)
    python3 docs/diagrams/make_physical.py          # expect: prints the .drawio path (the diagram is generated, edit the script, not the XML)
    python3 ~/.hermes/skills/drawio-skill/scripts/validate.py docs/diagrams/physical-architecture.drawio   # expect: 0 error(s), 0 warning(s)
    open docs/diagrams/physical-architecture.drawio.png    # one Machine (app + db containers), volume, secrets, registry, Fly proxy, people, 5 external services
    open -a draw.io docs/diagrams/physical-architecture.drawio   # editable; the PNG also embeds the diagram
    # Read docs/architecture.md: workers table, code map, 16 tables, env table, known limits. Linked from README.md, AGENTS.md,
    #   docs/operations.md; docs/design.md and docs/fact-checker-plan.md are marked historical (plan "Open questions" answered).
    # Agent ran: generate + validate (0/0); PNG 3285x2039 with a valid IEND; zoomed visual QA per region (no line through a box,
    #   no label on a line/border, no clipped text) after 3 fix rounds; every module, table (dev DB: 16 incl. schema_migrations),
    #   env var, poll interval, Fly size/region and volume (3GB, encrypted, gru) in the doc was checked against code / fly CLI.
    # NOT done: logical / sequence diagrams (one physical diagram only); docs/design.md body not rewritten (kept as history).
