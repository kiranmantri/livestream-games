# AGENTS.md — Live Games

Reference instructions for any agent (or human) working in this repo. Keep it current:
when we agree on a new convention, add it here in the same change.

## Project
"Live Games": FastHTML web app with games to play while a YouTube live stream runs.
First game: Bingo (5x5). Cell phrases come from transcripts + live chat of past streams of
https://www.youtube.com/@O_Poligrafo/streams (Portuguese, pt-BR). Design: `docs/design.md`.

## Stack
- Python >=3.12, managed with **uv** (`uv sync`, `uv run ...`); deps live in `pyproject.toml` only.
- **FastHTML** (+ HTMX) for the web app. **PostgreSQL 17** in Docker, **psycopg 3** with a pool,
  plain SQL migrations in `migrations/` (no ORM).
- Deployment: `docker compose` on a VM/VPS (host TBD). Secrets via env (`.env`, never committed).
  `Dockerfile` (uv, python:3.14-slim, non-root uid 10001, read-only rootfs) + `docker/entrypoint.sh`
  (migrate -> seed phrases only if table EMPTY -> uvicorn). `docker-compose.override.yml` (dev only) publishes Postgres on 127.0.0.1:5432.
- Corpus ingestion uses the **yt-dlp CLI/library**; CLI entry point: `uv run corpus ...`.

## Layout
    app/          FastHTML app (routes, views, services, db)
    corpus/       yt-dlp ingestion + phrase extraction
    migrations/   numbered SQL files (0001_*.sql ...)
    data/corpus/  raw downloads (gitignored)
    docs/         design docs
    tests/        pytest

## Working agreements
- Work **task by task** following the plan in `docs/design.md`; stop after each task for review.
- Be critical: state pros AND cons of technical choices. Never claim success without real
  command output (tests run, app exercised).
- Verify DB schema with `\d table` before building on it; migrations are the source of truth.
- Only horizontal streams are used (check `width > height` from metadata; the 📱 title marker
  is a secondary hint only).
- Phrases: anything the admin types (add OR edit, any source) is 1-10 words; the DB CHECK `phrase_word_count` is 1-10
  for every source (migration 0005). Candidates EXTRACTED from the corpus are 3-10 words (enforced in corpus/extract.py,
  not in the DB); an extracted phrase keeps its source when the admin shortens it. Never greetings ("bom dia", "tudo bem", "boa noite", ...). Sources: `transcript`, `chat`, `transcript+chat`, `manual`.

## Domain rules (decided)
- Two games ('bingo', 'falacias'), each with at most ONE active session at a time (partial unique index `one_active_session_per_game`
  on `game_type`, migration 0007); one Bingo and one Falácias session can be active together. A person playing both has two
  `users` rows (one per session, same name) and two cookies: `lg_token` (Bingo) and `lg_token_falacias`; `users.token_hash` stays
  globally unique. `app.sessions` functions take `game_type` (default 'bingo').
- First visitor starts the session, OR the admin starts it from /admin (then `created_by` is NULL and nobody is
  the starter); others join it. Nobody can create a second session of the same game while one is active.
- Phrases: the DB is the source of truth. `corpus/phrases.csv` is only the initial seed (`corpus load` seeds an
  EMPTY table; `--force` re-imports and overwrites). Admin manages them at `/admin/phrases` (add / edit / delete).
  Manual phrases start at score 99 (max) and are "frozen" (counters grow, score does not move) until the admin
  edits the score by hand; an edited score becomes the new `prior_score`. At game finish every dealt phrase is
  relearned: score = (20*prior + dealt*obs) / (20 + dealt), obs = 1 + 10*marked/dealt (cumulative over finished
  games). Card weight = 1 + score for every phrase. At least 24 enabled phrases must always remain.
- Username: unique per session, case-insensitive, max 20 chars.
- Identity for now: random cookie token (`lg_token`, HttpOnly; only its sha256 is stored) + source IP
  limit `MAX_USERS_PER_IP` (.env, currently 3). A valid cookie resumes the player; the IP never grants
  identity. `X-Forwarded-For` is used only when the direct peer is in `TRUSTED_PROXIES`.
  (To be revisited vs. fake users.)
- A card is generated randomly on join, persisted, and immutable (no re-roll).
- Card: NEW sessions are 4x4 (16 phrases, no free cell; `game_sessions.grid_size`, migration 0006). Sessions created before it stay
  5x5 (centre index 12 = channel avatar, free cell, counts as marked). Max marks 16 / 24. `ingested_videos` and `corpus/phrases.csv` are gone.
- Marking is honour-based (players mark their own cells; no phrase detection yet).
  There is NO "declare BINGO". The admin ends the session after some time; the winner is the
  player with the MOST marked phrase cells (centre free cell not counted; max 24). Tie-break: whoever
  reached their final count first (earliest last mark). Nobody marked anything -> no winner.
  Rationale: with the real corpus a full card is virtually never completable (see docs/design.md).
- Admin page `/admin`: password from `ADMIN_PASSWORD` env (HTTP Basic). Lists users and cards.

## Commands
    uv sync                         # install
    docker compose up -d db         # start Postgres (needs .env)
    uv run pytest                   # tests
    uv run ruff check .             # lint

## Task reporting: how to test and validate
At the end of EVERY task, the report must include a "How to test / validate" section that the
user can follow by hand, containing:
- exact commands to run (copy-pasteable, from the repo root), including any setup/start steps;
- the expected result for each command (what output / UI state means success);
- what to look at in files, DB (`docker compose exec db psql ...` queries) or browser URLs;
- how to reset/clean up so the check can be repeated.
Also state what the agent itself already ran and its real output; never claim success without it.

### Validation for Task 1 (scaffold)
    uv sync                                   # expect: resolves, no errors
    uv run corpus                             # expect: "corpus: not implemented yet"
    uv run ruff check .                       # expect: All checks passed!
    docker compose up -d db                   # expect: container started
    docker compose ps                         # expect: db "Up ... (healthy)"
    docker compose exec db psql -U livegames -c "select version();"   # expect: PostgreSQL 17.x
    # reset: docker compose down        (add -v to also delete the DB volume)

### Validation for Task 2 (corpus fetch)
    uv run corpus fetch --last 5 --dry-run   # expect: 5 KEEP lines (1920x1080), SKIP(vertical) lines (1080x1920)
    uv run corpus fetch --last 5             # idempotent; skips files already on disk (long on first run)
    ls -l data/corpus/*/                     # expect per video: <id>.pt-orig.vtt (+ <id>.live_chat.json)
    python3 -c "import json;[print(x['video_id'],x['orientation'],x['errors']) for x in json.load(open('data/corpus/manifest.json'))]"
    # expect: all horizontal; a stream without a chat track shows 'chat: no live_chat track available'
    # reset: rm -rf data/corpus/*/ data/corpus/manifest.json

### Validation for Task 3 (phrase extraction)
    uv run corpus suggest --show 60          # expect: "5 transcripts, 4 chats (10583 chat msgs)" + ranked table
                                             # columns: /hour = uses per stream-hour; t_str = streams (of 5) containing it
    uv run corpus suggest --write 400        # merges candidates into corpus/phrases.csv (keeps manual rows + your edits)
    uv run corpus add "minha frase aqui"     # manual phrase (3-5 words, else error exit 1)
    # CURATE: open corpus/phrases.csv, set enabled to 0 for rows you don't want (or delete them), add rows via `corpus add`.
    # re-running `suggest --write` never overwrites your enabled edits or manual rows ("+0 new").
    awk -F, 'NR>1{c[$3]++}END{for(k in c)print k,c[k]}' corpus/phrases.csv   # count by source
    # reset: rm corpus/phrases.csv

### Validation for Task 4 (DB schema + migrations)
    docker compose up -d db                  # DB must be healthy (see Task 1)
    uv run migrate                           # first run: "applied: 0001_init.sql"; second run: "up to date"
    uv run corpus load                       # expect: phrases {'upserted': N, 'enabled': E, 'disabled': D, 'rejected': 0}; videos upserted: 5
    docker compose exec db psql -U livegames -c '\dt'                 # expect 7 tables (+ schema_migrations)
    docker compose exec db psql -U livegames -c '\d users'            # expect name varchar(20), name_ci generated, UNIQUE (session_id, name_ci)
    docker compose exec db psql -U livegames -c "select source, enabled, count(*) from phrases group by 1,2"
    uv run pytest -q                         # expect: 38 passed (uses a throwaway DB; dev data untouched)
    # Mutation check (proves tests catch removed constraints; works on COPIES, never the real migration):
    #   LG_MIGRATIONS_DIR=<dir with a modified 0001_init.sql> uv run pytest -q   -> must FAIL
    # reset dev DB (destroys data): docker compose down -v && docker compose up -d db && uv run migrate

### Validation for Task 5 (session + identity services, app/sessions.py)
    uv run pytest tests/test_sessions.py -q  # expect: 48 passed (incl. 4 concurrency tests with real parallel connections)
    uv run pytest -q                         # expect: 86 passed in total
    # Try it by hand against the dev DB (creates rows; clean up below):
    uv run python -c "
    import psycopg; from app.db import connect; from app import sessions as S
    c = connect()
    s,p,tok = S.start_session(c,'Ana','10.0.0.1'); print('started', s.status, p.name)
    try: S.start_session(c,'Bia','10.0.0.2')
    except S.SessionError as e: print('2nd start refused:', e.code)          # session_already_active
    try: S.join_session(c,'ANA','10.0.0.2')
    except S.SessionError as e: print('dup name refused:', e.code)            # name_taken
    print('resume by cookie:', S.resume(c,tok).name, '| resume without cookie:', S.resume(c,None))
    print('ended:', S.end_session(c,s.id,p.id), '| ended again:', S.end_session(c,s.id,p.id))   # True | False
    "
    docker compose exec db psql -U livegames -c "TRUNCATE game_sessions, users CASCADE"    # clean up dev rows
    # Mutation check on a repo COPY (never the real code): see how it was done in the Task 5 report; every mutant must fail the suite.

### Validation for Task 6 (card generator, app/cards.py)
    uv run pytest tests/test_cards.py -q     # expect: 19 passed
    uv run pytest -q                         # expect: all passed
    # Deal real cards from the real corpus (needs `uv run migrate` + `uv run corpus load` done):
    uv run python -c "
    import psycopg; from app.db import connect; from app import sessions as S, cards as C
    c = connect()
    s,p,_ = S.start_session(c,'Ana','10.0.0.1'); _,b,_ = S.join_session(c,'Bia','10.0.0.2')
    ca, cb = C.assign_card(c,s.id,p.id), C.assign_card(c,s.id,b.id)
    for row in ca.grid(): print(' | '.join(('[ AVATAR ]' if 'type' in x else x['text']).ljust(28) for x in row))
    print('same card on 2nd call:', C.assign_card(c,s.id,p.id).id == ca.id, '| Bia differs:', ca.cells != cb.cells)
    "
    docker compose exec db psql -U livegames -c "select jsonb_array_length(cells) n, cells->12 centre from bingo_cards"   # expect n=25, centre {"type": "image"}
    docker compose exec db psql -U livegames -c "TRUNCATE game_sessions, users CASCADE"    # clean up dev rows
    # Mutation checks run on a repo COPY (never the real code); every mutant must fail the suite.

### Validation for Task 6b (winner = most marked cells, app/scoring.py)
    uv run pytest tests/test_scoring.py -q   # expect: 11 passed (ranking, tie-break, finish, idempotency, concurrency)
    uv run pytest -q                         # expect: 120 passed in total
    # Durability check against the dev DB (rows must be visible from a DIFFERENT connection, i.e. psql):
    uv run python -c "
    from app.db import connect; from app import sessions as S, cards as C, scoring as G
    c = connect()
    s,p,_ = S.start_session(c,'Ana','10.0.0.1'); _,b,_ = S.join_session(c,'Bia','10.0.0.2')
    ca, cb = C.assign_card(c,s.id,p.id), C.assign_card(c,s.id,b.id)
    for i in (0,1,2,3): c.execute('INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s,%s)', (ca.id,i))
    for i in (0,1): c.execute('INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s,%s)', (cb.id,i))
    print([(x.rank,x.name,x.marked) for x in G.leaderboard(c,s.id)])
    "
    docker compose exec db psql -U livegames -c "select count(*) cards from bingo_cards" -c "select count(*) marks from bingo_marks"   # expect 2 and 6
    uv run python -c "
    from app.db import connect; from app import sessions as S, scoring as G
    c = connect(); s = S.get_active_session(c); r = G.finish_session(c, s.id)
    print('winner:', r.winner.name, r.winner.marked, '| finish again:', G.finish_session(c, s.id))   # Ana 4 | None
    "
    docker compose exec db psql -U livegames -c "select status, u.name winner from game_sessions g join users u on u.id = g.winner_user_id"   # ended | Ana
    docker compose exec db psql -U livegames -c "TRUNCATE game_sessions, users CASCADE"    # clean up dev rows

### Validation for Task 7 (player web UI, app/main.py, app/marks.py)
    uv run pytest -q                         # expect: 168 passed (tests/test_marks.py, tests/test_web.py are new)
    uv run ruff check .                      # expect: All checks passed!
    uv run uvicorn app.main:app --port 8000  # then open http://127.0.0.1:8000 (phone-size window is best)
    # expect: "Começar partida" form; after entering a name you get a 5x5 board with the avatar in the centre.
    # Open a 2nd browser/private window: it shows "Entrar" (join), not start. Tap cells: green + "Marcadas: n/24", survives reload.
    docker compose exec db psql -U livegames -c "select u.name, count(m.*) from users u join bingo_cards c on c.user_id=u.id left join bingo_marks m on m.card_id=c.id group by 1"
    # end the game (until Task 8 admin exists): uv run python -c "from app.db import connect; from app import sessions as S, scoring as G; c=connect(); print(G.finish_session(c, S.get_active_session(c).id))"
    # expect: players see the results screen on next poll/reload.
    docker compose exec db psql -U livegames -c "TRUNCATE game_sessions, users CASCADE"    # clean up

### Validation for Task 8 (admin page, app/admin.py + app/admin_web.py)
    uv run pytest tests/test_admin.py -q     # expect: 20 passed (auth, csrf, listing, finish, double finish)
    uv run pytest -q                         # expect: 188 passed in total
    # ADMIN_PASSWORD in .env must be set and NOT "change-me" (otherwise /admin answers 503 "admin disabled").
    uv run uvicorn app.main:app --port 8000
    curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/admin                  # expect 401 (no credentials)
    curl -s -o /dev/null -w "%{http_code}\n" -u admin:wrong http://127.0.0.1:8000/admin   # expect 401
    # Browser: http://127.0.0.1:8000/admin -> login prompt: user "admin", password = ADMIN_PASSWORD.
    # expect: status pill, users table (rank, name, marks, IP, joined, last seen), one collapsible 5x5 card per
    # player (marked cells green, centre = star), and "Encerrar partida" (asks to confirm).
    # After finishing: pill "Partida encerrada", winner name shown, no finish button; players see "Fim de jogo!".
    # Refresh the page to update (no auto-refresh). reset: TRUNCATE game_sessions, users CASCADE (see Task 7).

### Validation for "time of each mark" (follow-up to Tasks 7/8)
    uv run pytest -q                         # expect: 193 passed
    # Play: tap a cell -> it turns green and shows HH:MM:SS under the phrase; tap again -> the time disappears.
    # /admin: every marked cell in each player's card grid shows its HH:MM:SS.
    docker compose exec db psql -U livegames -c "select cell_index, marked_at from bingo_marks order by marked_at"   # stored timestamptz
    # Displayed time = marked_at in TIMEZONE (default America/Sao_Paulo; set TIMEZONE=<IANA name> in .env to change).

### Validation for "admin can start a session" (migration 0002)
    uv run migrate                           # expect: "applied: 0002_admin_started_sessions.sql" (then "up to date")
    uv run pytest tests/test_admin.py tests/test_sessions.py -q
    # Browser /admin with NO active game: "Iniciar nova partida" -> "Partida em andamento", "Jogadores: 0".
    # A visitor on / now sees "Entrar na partida" (join), never "Começar partida". Finish the game: the button returns.
    docker compose exec db psql -U livegames -c "select status, created_by from game_sessions order by created_at desc limit 3"   # admin-started: created_by empty

### Validation for phrase CRUD + learned scores (migration 0003, app/phrases.py, app/phrases_web.py)
    uv run migrate                           # expect: "applied: 0003_phrase_stats.sql" (existing rows get prior_score = score)
    uv run pytest -q                         # expect: 294 passed (tests/test_phrases.py, tests/test_phrases_web.py are new)
    uv run uvicorn app.main:app --port 8000  # then http://127.0.0.1:8000/admin/phrases (user "admin", password ADMIN_PASSWORD)
    # expect: table of phrases (text, source, score, dealt, marked, rate, active), search/filter/sort, 50 per page.
    # Add "minha frase de teste": appears with source manual, score 99 + "fixo" badge. Edit: score "6,5" -> badge gone.
    # Bulk: tick rows (or "Selecionar todas"), then "Ativar selecionadas" / "Desativar selecionadas" / "Excluir selecionadas".
    #   Deactivate/delete is refused (nothing changes) if fewer than 24 enabled phrases would remain; activating never is.
    # Edit ANY phrase (also extracted ones) down to 1 word: allowed; 11 words or a greeting: inline error.
    # Delete: row disappears after the confirm dialog. Invalid input (2 words, greeting, score 100) shows an inline error.
    # Learning: play a game, finish it from /admin; then sort by "mais distribuídas": Dadas/Marcadas/Taxa are filled and
    #   the score of non-frozen phrases moved (marked often -> up, never marked -> down).
    docker compose exec db psql -U livegames -c "select text, source, score, prior_score, dealt, marked from phrases order by dealt desc limit 5"
    uv run corpus load                       # expect: "skipped: phrases table already has N rows"  (never overwrites edits)
    # reset: TRUNCATE game_sessions, users CASCADE (see Task 7); phrases are real data, do not truncate them.

### Validation for the game page (live stream + cards, app/livestream.py)
    # Layout: top row 50/50 = LEFT: live stream with YouTube's live chat embed BELOW it | RIGHT: MY card (RED border);
    #   stream top = card top and chat bottom = card bottom (the chat fills the remaining height). Below: all other cards, small,
    # read-only, with their marks (green). No scoreboard panel. Phones: stream, then my card, then 2 small cards per row.
    uv run pytest tests/test_live.py -q             # expect: 28 passed (cache/background lookup, yt-dlp cases, page, polls)
    uv run pytest -q                                # expect: 377 passed, 1 skipped (e2e needs SMOKE_URL)
    uv run uvicorn app.main:app --port 8000         # open http://127.0.0.1:8000, join from 2-3 browsers/private windows
    # expect: the channel's CURRENT live (auto-detected from youtube.com/@O_Poligrafo/live; first lookup takes ~15 s in the
    #   background, the page says "Procurando..." then reloads itself) - or "O canal nao esta ao vivo" with a link when offline.
    # Others' marks appear on your page within ~5 s without reload; your own board is never re-rendered by that poll.
    # LIVE_VIDEO_ID=<11-char id> forces a video (no detection); LIVE_CHANNEL_URL / LIVE_TTL (s, default 60) tune detection.
    # Tests never reach YouTube (autouse fixture in tests/conftest.py stubs the lookup).
    # Chat: iframe https://www.youtube.com/live_chat?v=<id>&embed_domain=<this page's host>; it needs a current browser and
    #   YouTube may disable chat embedding per stream; to WRITE in it the viewer must be logged in to YouTube in that browser.
    # reset: TRUNCATE game_sessions, users CASCADE (see Task 7).

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

### Validation for the corpus overhaul (20 streams, cliches, rescore, bulk delete) - YOU run these (long / touch dev DB)
    uv run migrate                                    # expect: applied 0002 ... 0005 (then "up to date")
    nohup uv run corpus fetch --last 20 --scan 80 > /tmp/fetch20.log 2>&1 &     # hours; idempotent, re-run to resume
    tail -f /tmp/fetch20.log                          # expect KEEP / SKIP(vertical) lines, then a summary
    uv run corpus suggest --show 80                   # expect: "20 transcripts", cliche authors matched (names + channel ids),
                                                      # kinds {'repeated','cliche','author'}; skips phrases already in seed/DB
    uv run corpus suggest --write 400                 # appends NEW rows to corpus/phrases.csv, all enabled=0
    uv run corpus export                              # FIRST (optional): make the seed file equal the DB (DB is the truth)
    uv run corpus load --new                          # inserts CSV rows missing from the DB as INACTIVE; never changes existing
    uv run corpus rescore --dry-run                   # expect: counts (changed / not_found_in_corpus / min / max), writes nothing
    uv run corpus rescore                             # replaces score AND prior_score of every non-manual phrase
    docker compose exec db psql -U livegames -c "select source, enabled, count(*), round(avg(score),2) from phrases group by 1,2 order by 1,2"
    # UI: /admin/phrases -> tick rows, "Selecionar todas" / "Limpar seleção" / "Excluir selecionadas" (asks to confirm;
    #   refused if fewer than 24 enabled would remain). Phrases of 1-10 words can be added manually.
    uv run pytest -q                                  # expect: 333 passed
    # Cliche sources: corpus/cliches.txt (3-10 word expressions, matched ignoring case/accents, >= 1 occurrence) and
    #   corpus/cliche_authors.txt (aliases >= 5 letters; chat display name CONTAINING an alias, '@' and punctuation ignored).

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

### Validation for 4x4 cards, admin unmark, red lines, users sort, game-screen toggles (migration 0006)
    uv run migrate                           # expect: "applied: 0006_grid4_drop_videos.sql" (drops ingested_videos)
    uv run pytest -q                         # expect: 397 passed, 1 skipped (tests/test_lines.py is new)
    # Game page: new cards are 4x4. Complete a row/column/diagonal -> blue box over it (a diagonal: one box per cell) (visual only; winner = most marks).
    #   Top button hides/shows video+chat (iframes unloaded while hidden); icons ▦/☰ switch the OTHER cards between grid
    #   (join order) and list (ranked by marks, with a bar). Choices live in localStorage key `lg_prefs`.
    # /admin: a yellow ★ appears next to a player's name (users table + card header) once they complete any row/column/
    #   diagonal; a bigger ★ + tooltip "cartela completa" when every phrase cell is marked. Hover for the line count.
    # /admin: users table has no IP and sorts via header links (?sort=name|marked|joined|seen|rank&dir=asc|desc).
    #   Each marked cell has "Desmarcar" (removes the mark AND blocks the cell, table revoked_marks); blocked cells show "Liberar".
    # /admin Desmarcar/Liberar use htmx: only that player's card + the users table (out of band) are swapped; no reload,
    #   no scroll jump (without JS it falls back to POST + redirect to /admin?open=<user>).
    # Corpus seed: `deploy/seed/phrases_seed.sql` (loaded only into an EMPTY phrases table); `suggest --write` inserts inactive rows.

### Validation for Task 7 (Falácias on Fly: entrypoint seeds, XDG_CACHE_HOME)
    # docker/entrypoint.sh now runs, after migrate + phrases: `corpus.cli fallacies-load` (only if the fallacies table is EMPTY: admin edits are never
    #   overwritten, and `fallacies-top` is NOT run at boot) and `corpus.cli replay-load` (16,260 rows, skipped if present). deploy/fly.machine.json: XDG_CACHE_HOME=/tmp/.cache.
    # Smoke (separate compose project, as in Task 9): logs show "applied: 0001..0009", "fallacies: {'inserted': 97}", "replay: {'inserted': 16260}", "Uvicorn running";
    #   a Falácias start detected the horizontal live (Ko_lIgMJudY, source_mode live) and the chat worker stored chat rows from inside the container (yt-dlp works there).
    # The Fly rootfs is writable, so tempfile.mkdtemp in /tmp works; an app without a home dir only needs XDG_CACHE_HOME for yt-dlp's cache.

### Validation for the fallacy admin page (/admin/fallacies: app/fallacies_admin.py, app/fallacies_web.py)
    uv run pytest tests/test_fallacies_admin.py tests/test_fallacies_admin_web.py -q   # expect: 48 passed
    uv run uvicorn app.main:app --port 8001             # then http://127.0.0.1:8001/admin/fallacies (user admin, ADMIN_PASSWORD); 4th admin tab "Falácias"
    # List with search (name, category, description; accent/case-insensitive), filters (origin, active), uses per fallacy. Add -> source manual, active.
    # Edit inline (name, category, description, active): same row id, so marks keep pointing at it; the game picker reads the table.
    # Refused (message in the row): fewer than 5 active after the change; deleting a fallacy players already used (disable it instead).
    # `uv run corpus fallacies-top` (manual, never at boot) renames/toggles Wikipedia rows by their ORIGINAL names and WOULD overwrite online edits of them.
    uv run corpus fallacies-manual                      # adds "Deus das lacunas (God of the gaps)" and "Ônus da prova invertido (Shift the burden of proof)" if missing BY NAME
                                                        #   expect: fallacies: {'added': 2}, then {'added': 0}. Not run at boot.
    # Cloud, once after the deploy: fly ssh console --app livebingo --container app -C "sh -c 'cd /app && python -m corpus.cli fallacies-manual'"
    # Mutation check (repo COPY): 32/32 killed (mutate_fa.py). NOT checked: the page in a real browser (htmx row swaps).

### Validation for R1 (replay catalogue: migration 0010, app/replays.py)
    uv run pytest tests/test_replays.py -q              # expect: 33 passed
    uv run pytest -q                                    # expect: 1004 passed, 1 skipped
    uv run migrate                                      # YOU, dev DB: expect "applied: 0010_replays.sql"; videos already in replay_events become 'ready' (backfill)
    docker compose exec db psql -U livegames -c "select video_id, status, transcript_rows, chat_rows from replays"   # expect _nVWPIFCUzQ | ready | 13535 | 2725
    # Service only (no page, no downloader yet): submit(url) queues a video (states queued -> downloading -> importing -> ready | failed), at most 5 queued,
    #   a failed one can be submitted again (goes to the back), claim_next() is atomic (SKIP LOCKED), mark_ready needs BOTH tracks in replay_events,
    #   mark_failed drops partial rows, recover_interrupted() fails jobs left running by a restart, delete() refuses queued/running (busy) and videos
    #   followed by an ACTIVE game (in_use). corpus replay-import and the SQL seed loader register the video as ready.
    # Mutation check (repo COPY): 28/28 killed (mutate_r1.py; the first run had 5 survivors -> tests added).

### Validation for R2 (replay collector: app/replaycollector.py)
    uv run pytest tests/test_replay_collector.py -q     # expect: 32 passed (red run before the module existed: collection error)
    # One daemon thread, started by the app's lifespan (off with REPLAY_COLLECTOR=off; tests always run with it off). It takes the oldest queued
    #   video, probes it with yt-dlp (120 s) and REFUSES BEFORE DOWNLOADING: live / not started / still processing / vertical or square /
    #   longer than REPLAY_MAX_HOURS (default 12) / no pt captions yet ("podem levar horas") / no chat replay. Then it downloads (30 min limit) into
    #   a private temp dir, imports with corpus/replay.py, marks ready; ANY failure -> failed with a pt-BR message and no partial rows. Temp dir always removed.
    #   At start it fails jobs a restart left running (queued ones are kept). Polls every 15 s and is woken by the admin page.
    # Real smoke (read-only, you ran it): uv run python -c "from app import replaycollector as RC; m = RC.YtdlpDownloader().probe('_nVWPIFCUzQ'); print(m, RC._refusal(m, 12))"
    #   expect: Meta(... has_pt_captions=True, has_chat=True) and None.
    # NOT measured: a real download (time, size) and whether Fly's IP is allowed to download captions/chat. Test it after the deploy with a SHORT video.
    # Mutation check (repo COPY): 28/28 killed (mutate_r2.py; the first run had 5 survivors -> tests added).

### Validation for R3 (admin replay catalogue on /admin/falacias)
    uv run pytest tests/test_replays_admin_web.py -q    # expect: 39 passed (red run: 34 failed before the code)
    uv run pytest -q                                    # expect: 1074 passed at R3, 1073 after R4 (3 old tests in test_live_pages.py were replaced: the admin start form has no link field any more)
    uv run migrate                                      # YOU: 0010 on the dev DB
    uv run uvicorn app.main:app --port 8001             # http://127.0.0.1:8001/admin/falacias (user admin, ADMIN_PASSWORD)
    # Panel "Replays disponíveis": paste a link of a FINISHED horizontal stream -> "Coletar" -> row "Na fila" -> "Baixando" -> "Importando" -> "Pronto"
    #   (the list redraws itself every 3 s only while a job runs). Failures show the reason (live, vertical, > 12 h, no captions yet...) + "Tentar de novo".
    #   "Iniciar com este replay" (ready rows, only when no Falácias game is active) starts a game on THAT video, even if the channel is live
    #   (the admin's explicit pick wins). "Excluir" asks to confirm; refused while queued/running or while an active game uses the replay.
    #   The plain "Iniciar partida de Falácias" button is automatic: horizontal live, else a message telling you to pick a replay.
    docker compose exec db psql -U livegames -c "select video_id, status, transcript_rows, chat_rows, error from replays order by created_at"
    # Mutation check (repo COPY): 27/27 killed (mutate_r3.py; the first run had 5 survivors -> tests added). NOT checked: the page in a real browser (htmx swaps).

### Validation for R4 (players never pick a replay)
    uv run pytest tests/test_live_pages.py -q           # expect: 49 passed (red run: 7 failed before the code)
    uv run pytest -q                                    # expect: 1076 passed, 1 skipped (after the browser QA layout fixes)
    # /falacias: no "Link do vídeo" field any more. A player's start follows a HORIZONTAL live only; a posted `video` value is ignored.
    #   No horizontal live => "Não há partida disponível agora... Aguarde o administrador iniciar uma partida." and no session is created.
    #   Joining a game the admin started (live OR replay, even while a live exists) works as before. FALACIAS_VIDEO_ID (env, tests/demos) is still a fallback.
    # Mutation check (repo COPY): 6/6 killed (mutate_r4.py). NOT checked: the entry page in a real browser.

### Validation for the adversarial-test fixes (emoji names, rapid taps, recovery link: app/ui.py, app/main.py)
    uv run pytest tests/test_major_bugs.py tests/test_falacias_web.py -q   # expect: all passed (red run before the code: 7 failed)
    uv run pytest -q                                    # expect: 1089 passed, 1 skipped
    # 1. Names: the `lg_name` cookie is stored percent-encoded (quote/unquote in app/ui.py); emoji, Cyrillic and CJK names no longer give HTTP 500.
    # 2. Taps: see "Validation for backlog Group 2" (the first fix with hx-sync did NOT work: htmx's queue drops requests of replaced buttons).
    # 3. Lost cookie / other device: the player's own page has "Salvar link para voltar nesta partida" -> /r/<bingo|falacias>/<token>.
    #    Opening it sets the cookie again (same card, marks kept). SECURITY: whoever holds the link IS the player; unknown tokens do nothing.
    #    Open the link in a private window to try it. The token travels in the URL (browser history, proxy logs): acceptable for a low-stakes game.
    # NOT checked: touch devices, Safari/Firefox. All other findings are in docs/backlog.md.

### Validation for backlog Group 1 (bugs: name pre-check, IP limit redirect, admin throttle, 404, labels)
    uv run pytest -q                                    # expect: 1104 passed, 1 skipped (red run before the code: 13 failed)
    # Entry page: a visitor whose remembered name is already taken in the running game goes straight to the name form with the message
    #   (Bingo and Falacias); "trocar nome" selects the old name on focus. The entry page of Falacias no longer shows a replay line.
    # IP limit: the join POST answers 303 to /?err=ip_limit_reached (/falacias?err=...), the page says to ask the presenter. Only codes in
    #   app.ui.URL_ERRORS are read from the URL. The limit itself is still MAX_USERS_PER_IP (env).
    # Admin throttle (app/admin.py, in memory, per client IP): 5 WRONG passwords in 5 min -> 429 + Retry-After for 5 min, even with the right
    #   password. No Authorization header never counts; a success clears the counter. Try: for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w "%{http_code} " -u admin:x http://127.0.0.1:8000/admin; done
    #   expect: 401 401 401 401 401 429. Reset: restart the server. Behind Fly the IP comes from CLIENT_IP_HEADER (fly-client-ip).
    # Also: singular/plural player count, friendly 404 page, name gate titled "Jogos da Live", labels on /admin/fallacies inputs.
    # Real browser (throwaway DB): 404 page, name gate, taken-name flow checked; curl for the 429. NOT checked: phone, Safari, Firefox.

### Validation for backlog Group 2 (phone layout of the Bingo player page, per-cell taps)
    uv run pytest -q                                    # expect: 1119 passed, 1 skipped (tests/test_phone_ui.py is new; red run: collection error)
    # Phones (<= 760 px): MY board comes first with a sticky "Ana (você) / Marcadas n/16" bar; video+chat start hidden (a saved choice in
    #   localStorage `lg_prefs` wins; the toggle button shows them); with no live there is no toggle and no empty boxes, just "Canal fora do ar agora.".
    #   Cells: 13 px text, rows >= 88 px, a trailing "(falácia ...)" is drawn as a small tag under the phrase (split_kind in app/main.py).
    #   Tap targets >= 44 px (view toggles, hide-video, "trocar nome", "Ver o canal", menu, recovery summary). Desktop layout unchanged.
    # Taps: /mark/<i> answers an htmx request with ONLY that cell + out-of-band #count and #lines (blue boxes); a plain POST (tests, curl) still gets the
    #   whole board; a revoked cell answers the whole board via HX-Retarget. game.js flips the cell at once, then sends taps ONE AT A TIME in order
    #   (promise chain around htmx.ajax). htmx's own sync queue was tried first and dropped queued taps (replaced buttons), so it is NOT used.
    # Real Chrome, 390 px, throwaway DB (you can repeat: lg_qa via ~/.hermes/cache/scratch/qa_setup.py): bursts of 1-7 taps on several cells ->
    #   expected parity == screen == server (after reload) in 3 of 3 runs, no cell left "pending". Board top y: 841 px -> 177 px.
    # NOT checked: a real touch device, Safari, Firefox; the sticky bar was checked by scrolling (bar stayed at top: 0).

### Validation for backlog Group 3 (admin: grouped tabs, status bar, auto-refresh, queue filter/shortcuts, phone cards)
    uv run pytest -q                                    # expect: 1129 passed, 1 skipped (tests/test_admin_group3.py is new; red run: 10 failed)
    # Tabs: "Partidas: Bingo | Encontre as Falácias" and "Conteúdo: Frases do Bingo | Lista de falácias" (the last one used to be called "Falácias").
    # Sticky status bar (#statusbar) on both game pages: state, start time, players, elapsed time (admin.js ticks it), "Atualizar", "atualizado há N s",
    #   and the end button at the far right. On phones it is NOT sticky (3 lines tall) and hides start time / updated label.
    # Bingo admin: the users table re-reads itself every 5 s via GET /admin/live?sort=&dir= (keeps the sort, updates the player count out of band,
    #   HX-Refresh when the game ended). Player cards are NOT redrawn (an open card would collapse).
    # Falácias queue: "Apenas pendentes" (default on, remembered in localStorage lg_admin_prefs) hides reviewed items; each item shows "há N s";
    #   the OLDEST pending item is highlighted and the keys C / E / S give Correto / Errado / Spam (Shift = all identical pairs). Keys are ignored while typing
    #   and while a verdict is in flight.
    # Real Chrome (throwaway DB, 5 players, 4 pending pairs): Shift+C reviewed the identical pair, E and S the next ones, filter toggled; three fast
    #   presses sent ONE verdict. Phone (390 px): no sideways scroll on /admin, /admin/falacias, /admin/phrases, /admin/fallacies; tap targets >= 44 px
    #   except the "Apenas pendentes" checkbox itself (its label is 44 px) and text links inside paragraphs/tables were padded.
    # Existing tests adapted: users-table regexes now accept <td data-label=...>.

### Validation for the two bugs + "name [timestamp]: text" (migration 0011, corpus/emoji.py, app/feed.py, app/admin_web.py)
    uv run pytest tests/test_bugs_followup.py -q        # expect: 18 passed (red run before the code: 15 failed)
    uv run pytest -q                                    # expect: 1147 passed, 1 skipped (1 old test adapted: the author span now also holds the time)
    uv run migrate                                      # YOU, dev DB: expect "applied: 0011_app_settings.sql"
    uv run corpus replay-emoji                          # YOU, dev DB: rewrites YouTube emoji shortcodes in replay_events (":face-blue-smiling:" -> 😊); idempotent
    #   expect "replay rows rewritten: N" (N ~ 70 for _nVWPIFCUzQ), then 0. Games already running keep their old text (stored quote offsets refer to it).
    #   Then `uv run corpus replay-seed _nVWPIFCUzQ` so the Fly seed has the new text (the seed only fills an EMPTY table).
    #   New imports and the live chat worker map the emoji by themselves (corpus/replay.py _runs_text). A YouTube-style name that is not in the table
    #   (corpus/emoji.py) is dropped; a channel emoji (":canal:") stays as typed.
    # Panes: chat "@name [HH:MM:SS]: text", transcript "[HH:MM:SS]: text". The time is when the line reached the pane (stream_events.fed_at, TIMEZONE),
    #   not the video offset; name and time are outside the selectable text, so quote offsets are unchanged.
    # IP limit: /admin (bottom of the page, both with and without a running game) "Jogadores por rede (IP), nos dois jogos", 1..50, saved in table app_settings;
    #   it overrides MAX_USERS_PER_IP (env stays the default while nothing is saved). Applies from the next join, both games. Player messages show the saved number.
    #   curl check: POST /admin/settings/ip-limit with the page's csrf token: limit=4 -> 303 /admin; limit=99 -> 303 /admin?err=ip_limit (nothing saved).
    docker compose exec db psql -U livegames -c "select key, value, updated_at from app_settings"
    # Real check (throwaway DB, Chrome, replay at 60x): both panes show the new format with Unicode emoji; saved 2 -> 4 via the form, 99 refused.
    # NOT checked: the cloud (needs the deploy checklist: migration 0011 => backup first; then run replay-emoji once in the app container:
    #   fly ssh console --app livebingo --container app -C "sh -c 'cd /app && python -m corpus.cli replay-emoji'"), phones, Safari/Firefox.

### Validation for Visual refresh v1 (DESIGN.md is the target; app/ui.py, app/static/app.css, admin.js)
    uv run pytest tests/test_refresh.py -q               # expect: 16 passed (red run before the code: failures on every new feature)
    uv run pytest -q                                     # expect: 1163 passed, 1 skipped
    # Every player page: sticky app bar = brand (home) | game tabs with icon + one-line subtitle (green pulsing dot = game running) | avatar + name
    #   (click: "Trocar nome"). Phones (<= 760 px): the tabs move to a fixed BOTTOM bar. NOTE: a backdrop-filter on .appbar on phones would
    #   anchor that fixed bar to the top (found in the browser) - keep it `none` there.
    # Bingo: progress bar under "Marcadas n/16", cells pop when marked. Name gate: centred welcome. Falacias LIVE game: the left pane shows the
    #   stream (youtube-nocookie embed, muted autoplay) instead of the empty transcript pane. Picker is a right-hand drawer on desktop.
    # Admin: verdict buttons coloured (Correto green, Errado amber, Spam grey) with C/E/S key badges (hidden on phones); the browser tab title shows
    #   "(n) ..." while n pairs wait (admin.js, ~20 lines). All motion is off with prefers-reduced-motion.
    # Real Chrome (throwaway DB): 390 px and 1280 px, Bingo + Falacias live + user menu checked. NOT checked: admin queue colours, picker drawer,
    #   tab-title counter (the QA had no pending pairs), Safari/Firefox/touch.

### Validation for Visual refresh v1, part 2 (admin shell: app/admin_web.py admin_tabs, app/static/app.css)
    uv run pytest tests/test_refresh.py -q               # expect: 19 passed (3 new admin tests were red first)
    uv run pytest -q                                     # expect: 1166 passed, 1 skipped (tests/test_phone_ui.py::test_a_falacia_phrase_is_drawn_with_a_small_tag is
                                                         #   probabilistic - it deals up to 40 random cards - and failed once in ~4 full runs; rerun it if it does)
    # Every /admin page: sticky app bar "Admin" | Partidas: Bingo, Encontre as Falácias | Conteúdo: Frases do Bingo, Lista de falácias | "Ver o site" (new tab).
    #   The bar is now OUTSIDE <main> (admin_tabs() returns the <header>; pages return (Title, admin_tabs(..), Main(..))). Phones: the 4 tabs sit in a fixed bottom bar,
    #   group labels and "Ver o site" are hidden. The sticky status bar is a rounded card pinned at top: 57px (just under the app bar), with a pulsing dot while active;
    #   users table: uppercase headers, zebra rows, hover; "Encerrar partida" is outlined red and fills on hover.
    # Later tweaks: no "Atualizar"/"atualizado" in the Bingo status bar (the 5 s poll replaces it); both end buttons say "Encerrar partida"; the bar wraps instead of overflowing;
    #   All destructive buttons (row/bulk "Excluir", replay "Excluir", "Encerrar partida") share ONE soft red pill (`/* destructive buttons */` in app.css): fully rounded, tinted, solid on hover.
    #   /admin/phrases has no "Painel" button (the tabs navigate): add card, toolbar card (filters + count + bulk buttons), table card (scrolls sideways INSIDE its card on phones).
    # Real Chrome (throwaway DB): /admin and /admin/falacias at 1280 px and /admin at 390 px (no sideways scroll, bottom tabs, nothing hidden).
    #   NOT checked: /admin/phrases and /admin/fallacies visually, the coloured verdict buttons with real pending pairs (the seeder got HTTP 401 on typed pairs and the
    #   channel was live, so no replay chat to mark), Safari/Firefox/touch.

### Validation for S1 (live speech-to-text worker: app/sttworker.py) - NOT wired into the game yet
    uv run pytest tests/test_sttworker.py -q            # expect: 64 passed (red run first: collection error, module did not exist)
    uv run pytest -q                                    # expect: all passed (the replay-collector recovery test is timing-flaky: rerun it alone if it is the only failure)
    # Decided (user, 2026-10-23): self-hosted whisper.cpp `small` on a SECOND Fly Machine (not the 1 GB app Machine); typed "Frase falada" box stays as fallback;
    #   the pane will carry a "transcricao automatica, pode conter erros" label (S2). Scope of S1: the worker only; nothing calls ensure() yet and no migration.
    # What exists: pipeline yt-dlp | ffmpeg 16 kHz mono segments -> SegmentSource (+1 s overlap) -> silence gate -> WhisperCppTranscriber (HTTP POST <STT_URL>/inference)
    #   -> clean_text (noise tags, music, known hallucinations, loops) -> trim_overlap -> stream_events kind 'transcript' (key "<chunk start ms>:<segment idx>", author NULL).
    #   Same shape as chatworker: one daemon thread per live session, health ok/down, capped backoff 2..60 s, stall detection (60 s without a chunk), thread ends with the session.
    #   A transcriber outage keeps the capture running and DROPS the chunks of the outage (live audio is stale later); a capture/DB failure restarts the capture.
    # Env (all optional): STT_PROVIDER=off|whispercpp (default off; ensure() does nothing), STT_URL, STT_CHUNK_S (12), STT_OVERLAP_S (1), STT_PROMPT.
    # Real checks done: (1) mutation check on a repo COPY: 41/42 killed; the survivor (ingest without `game_type='falacias'`) is equivalent: the CHECK
    #   session_source_falacias_only already forbids a source_mode on a bingo session. (2) the REAL ffmpeg segment command, fed a generated tone instead of yt-dlp,
    #   gave 2 s segments with the 1 s overlap prepended and the work dir removed (SegmentSource + capture_commands).
    # NOT verified (spike S0 must do it; see .hermes/plans/2026-10-22_speech-to-text-falacias.md): yt-dlp live audio from a Fly IP, a real whisper.cpp server (the
    #   multipart fields /inference, verbose_json, prompt, and the reply shape are written from documentation as remembered), real-time factor of `small` on a shared
    #   CPU, ffmpeg in the Docker image (not added yet), pt-BR accuracy, the silence threshold (SILENCE_RMS=100) and the hallucination list on real audio.
    # Next: S2 wires feed.tick + the page (label, banner, typed box), S3 Fly (ffmpeg in the image, second Machine, STT_URL over Fly private network), S4 live tuning.

### Validation for S2 (STT wired into the live Falacias game: app/feed.py, app/falacias_web.py) - still OFF unless STT_PROVIDER is set
    uv run pytest tests/test_stt_pages.py -q            # expect: 15 passed (red run first: 6 failed / 8 guards passed)
    uv run pytest -q                                    # expect: all passed
    # feed.tick() of a LIVE session also calls sttworker.ensure (a no-op with STT_PROVIDER unset/off; never for replays).
    # Page with STT ON (live game only): the stream moves into a collapsible box ABOVE the two panes (<details class="vidbox" open>), the left pane is the
    #   transcript again with the label "Transcricao automatica, pode conter erros", lines show [HH:MM:SS] and no author. Own red banner #sttbanner when the worker
    #   is down ("... digite a frase na caixa abaixo"): SEPARATE from the chat banner; it never blocks pairs (selected or typed). The poll URL carries tr=ok|down;
    #   an old page without tr never shows the banner. With STT OFF the page is exactly as before (video replaces the transcript pane, no banner).
    # Real browser (Chrome, throwaway DB, fake live + fake audio source, whisper URL pointing nowhere): label, open video box, red banner after the first poll,
    #   no sideways scroll at 390 px. I did not LOOK at the screenshots in detail; check the look yourself. NOT checked: real audio, real whisper, Fly (S3).
    # Mutation check: not run for S2.

### Validation for "start Falacias from ANY link" (app/livestream.py probe_video, app/feed.py source_from_url, /admin/falacias/start-url)
    uv run pytest tests/test_start_by_url.py -q         # expect: 28 passed (red run first: collection error, VideoProbe missing)
    uv run pytest -q                                    # expect: 1283 passed, 1 skipped
    # /admin/falacias with no game: field "Link do video ou da live (qualquer canal)" + "Iniciar com este link" (the old automatic channel button stays below it).
    #   The server asks YouTube about THAT video (yt-dlp, ~5-15 s, the admin waits): horizontal LIVE -> live game (chat worker, and STT if enabled);
    #   finished video with a READY replay -> replay game; finished video NOT collected -> the replay is queued in the catalogue (cache) and the page says so;
    #   paste the link again once the row is "Pronto" (or use "Iniciar com este replay"). Same link while queued/running -> "ja esta na fila".
    #   Refused with a message: bad/foreign link, vertical, not started yet, still processing at YouTube, lookup failed, queue full (5).
    # Players: unchanged. They join the game the admin started; a player's own start still follows the channel in LIVE_CHANNEL_URL (default O_Poligrafo).
    # Real read-only check (me): probe_video on 3 finished videos of two different channels -> kind vod, 1920x1080 / 3840x2160, horizontal.
    #   NOT checked: a real LIVE link of another channel, the page in a real browser, a full queue -> collect -> start cycle on a real video.
    # Mutation check: not run.

### Validation for the start dialog (replaces the "Replays disponiveis" panel and the link panel on /admin/falacias)
    uv run pytest tests/test_start_by_url.py -q         # expect: all passed (red run first: 13 failed)
    uv run pytest -q                                    # expect: 1294 passed, 1 skipped (the replay-collector recovery test is the known timing flake)
    # /admin/falacias with no game or after one ended: the title line has "Iniciar nova partida" (right). It opens a MODAL (startdlg.js, <dialog>) with
    #   (1) a select of the collected replays (ready = pickable, queued/failed/... shown disabled with their status), (2) a text box for ANY YouTube link
    #   (a typed link wins over the select), "Cancelar" / "Iniciar". Esc or a click on the backdrop closes it. Messages (refused link, "queued for
    #   collection") come back inside the dialog, which reopens by itself. Without JS: the button is a link to ?start=1 which shows the dialog inline.
    #   Link: horizontal live -> live game; finished + collected -> replay game; finished + not collected -> queued in the catalogue (cache), try again when ready.
    #   Picked replay: starts at once, no YouTube lookup. Both empty: "Escolha um replay na lista ou cole um link".
    # REMOVED from the page: the catalogue table, its "retry"/"delete" buttons and the automatic "channel" start button. The routes
    #   (/admin/falacias/replays/{add,retry,delete,start} and /admin/falacias/start) still exist and are tested but nothing links to them.
    #   A failed collection is retried by pasting the link again; there is NO UI to delete a collected replay now.
    # Real Chrome (throwaway DB): modal opens centred (560 px; 352 px at 390 px width), no sideways scroll, option list as above, Esc closes,
    #   picking a replay without rows came back with the message INSIDE the reopened dialog. NOT checked: the full start of a real game from the dialog.

### Validation for "STT down on a real live" (diagnosis + fixes: logs with the reason, STT_LANGUAGE, prompt-echo filter) and the status panel / entry page tidy-up
    uv run pytest -q                                    # expect: 1314 passed, 1 skipped
    # Run it locally (YOU): whisper.cpp from Homebrew + a ggml model, then the app with STT on. Put Homebrew's ffmpeg FIRST in PATH:
    #   a static ffmpeg without CA certificates (the Hermes tools one) fails on a live's HLS manifest with "certificate verify failed".
    whisper-server -m ~/whisper-models/ggml-medium.bin -l pt --host 127.0.0.1 --port 8080 -t 4
    PATH=/opt/homebrew/bin:$PATH STT_PROVIDER=whispercpp STT_URL=http://127.0.0.1:8080 STT_LANGUAGE=pt uv run uvicorn app.main:app --port 8000 --reload
    #   STT_LANGUAGE = pt (default) | en | any 2-3 letter code | auto. With a non-pt language the Portuguese default prompt is NOT sent (STT_PROMPT still is).
    #   Language pt on an English stream makes whisper TRANSLATE into Portuguese: set the language of the stream you start.
    # The banner "A transcricao automatica esta fora do ar" now has a reason in the server log: grep "stt " (first failure, then #2, #4, #8...; "stt recovered" once).
    #   Causes seen/expected: whisper-server not running (connection refused), ffmpeg missing or without certificates, yt-dlp blocked/403.
    # Real check (me, this Mac, live pxEG3l8yNtg, medium model): yt-dlp | ffmpeg -> 12 s WAV segments -> whisper-server /inference worked end to end (6 chunks,
    #   0.4-0.8 s per 13 s of audio); the request fields (/inference, language, prompt, verbose_json) are CONFIRMED against a real whisper-server.
    #   Whisper answered one quiet chunk with its own prompt; such segments are now dropped (tests were written together with that code: no red run for it).
    # NOT checked: the whole thing through the web app with a game (only the worker's pieces), Fly, other models.
    # Status panel on /admin/falacias: the source link ("Transmissao ao vivo da partida: ..." / "Video da partida (replay): ...") is a row INSIDE the status panel
    #   (also when ended). "Ninguem pontuou desta vez." was removed (admin and player end screens). The entry page of Falacias no longer says
    #   "O canal nao esta ao vivo agora." / "So transmissoes na horizontal sao usadas.".

### Validation for the unified slide panel (replaces "Frase falada" and "Mensagem para o apresentador"; migration 0012)
    uv run pytest tests/test_unified_panel.py -q        # expect: 26 passed (written first; red run: 20+ failed)
    uv run pytest -q                                    # expect: 1331 passed, 1 skipped (+ the known replay-collector timing flake)
    uv run migrate                                      # YOU, dev DB: expect "applied: 0012_mark_note.sql" (adds fallacy_marks.note, <= 280 chars)
    # /falacias: the two boxes below the panes are gone. The slide panel (#picker) opens on ANY click in the chat or transcript pane, with or
    #   without selected text, and ends with a "Mensagem ou frase falada" textarea (280 chars) right before "Enviar". One POST /falacias/mark:
    #   passage + fallacy (+ message)  -> pair; the message is stored as `note` and shown to the admin in the queue ("Mensagem do jogador")
    #   fallacy + message, no passage  -> typed fallacy (kind 'spoken', the old Frase falada)
    #   message only                   -> host message (host_messages; same 30 s / max 5 limits)
    #   passage without fallacy, fallacy without text, or nothing -> inline error. Fallacy radios are no longer `required`.
    # REMOVED: routes /falacias/spoken and /falacias/message, texts FAL_SPOKEN_*, HM_HEADING/HELP/SEND/COUNT, JS events fal:spoken-sent / fal:msg-sent.
    #   The typed quote limit is now 280 (was 300); the host-message counter "Enviadas n/5" is gone (the error appears when the cap is hit).
    # NOT checked: the panel in a real browser (opening on pane click, textarea layout, phones), no mutation check.
    # Fly: migration 0012 -> follow the deploy checklist (backup first).

### Validation for the personal link in the header (app/ui.py: CookieContext, personal_link, GET /r)
    uv run pytest tests/test_major_bugs.py tests/test_falacias_web.py -q    # expect: all passed (red run first: 3 failed)
    uv run pytest -q                                    # expect: 1338 passed, 1 skipped
    # The "Salvar link para voltar nesta partida" section is gone from the bottom of both game pages. Instead the header has a link icon
    #   (class mylink) LEFT of the profile button, on every page, once the visitor has a game cookie. ONE link for all games: /r?b=<bingo token>&f=<falacias token>
    #   (only the games the player is in). Opening it elsewhere sets every valid cookie and the name. Old /r/<game>/<token> links still work.
    #   SECURITY: whoever holds the link IS the player. The tokens are in the URL (history, logs): accepted for a low-stakes game.
    # Try: play in one browser, right-click the icon > copy link, open it in a private window: same cards and marks.
    # NOT checked in a real browser (icon size/alignment on desktop and phones).

### Validation for the three-panel Falácias live page (app/falacias_web.py game_view/video_pane, falacias.js, app.css .panes.three)
    uv run pytest tests/test_three_panels.py -q         # expect: 6 passed (red run first)
    uv run pytest -q                                    # expect: 1345 passed, 1 skipped
    # LIVE game: video | chat | transcript side by side (1.25fr 1fr 1fr). The video panel's title "Transmissão ao vivo" has "Ocultar vídeo" on its right;
    #   clicking it unloads the iframe (sound stops), hides the panel and the other two share the width 50/50; a "Mostrar vídeo" button appears above them.
    #   The transcript panel is always there in a live game (with STT off it only holds the "no transcript" note). The old collapsible box above the panes is gone.
    #   Replay games are unchanged (transcript | chat, no video). Phones (<= 760 px): one column, video first.
    # NOT checked in a real browser (widths, iframe size in a 1/3 column, the hide/show script, phones).

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

### Validation for FC0 (fact-checker spike: spikes/checker.py) - YOU run it (model pulls of GBs, minutes per claim)
    uv run pytest tests/test_spike_checker.py -q       # expect: 39 passed (scripted FunctionModel, no Ollama, no network; first 35 written with the code, last 4 red first)
    # Group `checker` (pydantic-ai-slim[openai,duckduckgo], httpx) is a default uv group but is NOT installed in the Docker image (--no-group checker).
    brew services start ollama   # or: OLLAMA_CONTEXT_LENGTH=32768 ollama serve   (default 4096 is too small; with brew set it in the service env)
    ollama pull gemma4:e4b; ollama pull gemma4:26b; ollama pull qwen3:14b      # + gemma4:12b / :31b if you want all of them; qwen3 = a second family    # tags NOT verified: if one fails, `ollama search gemma4` / ollama.com/library/gemma4
    uv run python -m spikes.checker --claim "A Terra tem 6 mil anos" --expected false --models gemma4:e4b   # one claim first (expect: verdict false, score < 0, sources from the search)
    uv run python -m spikes.checker --models gemma4:e4b,gemma4:26b,qwen3:14b                    # 33 claims (categories: pseudo-history incl. "os Annunakis existem", opinion, prediction, vague, noisy-transcript, recent-news, half-truth, person, science, history, numbers) x models (long); --limit 5 = a varied smoke set; --no-fetch = snippets only
    # Summary adds refused_ok (opinion/prediction/vague correctly answered "unverifiable"), refused_wrong (checkable claims it gave up on) and by_category.
    # Output: data/spike/results.jsonl (one row per model x claim: time, requests, tool calls, searches, fetched pages, tokens, verdict/score/confidence/sources, error) + a summary table.
    # Rules proven offline: sources must come from the search results; sign of score agrees with verdict; unverifiable => score 0; max 3 page fetches, only of URLs the search returned
    #   and only public http(s); search count capped. NOT checked: any real model, Ollama's tool-calling with Gemma 4, DuckDuckGo from this network.
    # Groq contender (added): `--models groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b` uses Groq's OpenAI-compatible API with OUR tools (web_search + fetch_page) and
    #   validators (Groq's built-in browser search is NOT used: it does not work with structured outputs). Key from the shell: GROQ_API_KEY=... (missing => clear exit, key never printed).
    #   The summary adds cost_usd_total / cost_usd_per_claim (prices in GROQ_PRICES, console.groq.com/docs/models; unknown model => no cost). Local tags cost 0. 44 tests (5 new, red first).
    #   Keyless wire check: a fake key reaches Groq (HTTP 401, not Cloudflare 403).
    #   REAL RUNS FAILED (user), fixed in three rounds; ALL are Groq/gpt-oss behaviours that a fake server did not show:
    #     (1) answer as a forced final_result tool call => 400 tool_use_failed ("Tool choice is required, but model did not call a tool");
    #     (2) response_format (json_schema OR json_object, incl. pydantic-ai's `prompted`) next to tools => 400 "json mode cannot be combined with tool/function calling";
    #     (3) JSON as plain text with tools present => gpt-oss calls a made-up tool named 'json' => 400 tool_use_failed (failed_generation holds its answer).
    #   Fix: `--output-mode twostep` (DEFAULT for groq:; Ollama keeps `tool`): step 1 researches WITH tools and ends in plain-text notes; step 2 has NO tools, gets the notes + the URLs
    #   web_search returned, and answers with a JSON schema (native, strict false); the same output validators run on step 2. Cost: one extra request per claim. Usage is summed.
    #   tests/test_spike_checker.py: the fake HTTP server now ENFORCES the rule (tools + response_format => 400) and checks that step 1 has tools/no response_format and step 2 the opposite (49 passed).
    #   NOT verified with a real key: that step 2's json_schema is accepted and that gpt-oss writes useful notes. Lesson: only a real call proves the wire.
    # Next: send me the summary table; I decide hosting (Fly CPU vs Mac+tunnel vs rented GPU) from it. reset: rm -rf data/spike

### Validation for hosted STT (STT_PROVIDER=groq|grok: app/sttworker.py ApiTranscriber) - not deployed yet
    uv run pytest tests/test_stt_api.py -q             # expect: 22 passed (red run first: 18 failed / 2 passed; the User-Agent test was red first too; fake HTTP server, no network, no keys)
    docker compose up -d db && uv run pytest -q         # YOU: the DB-backed worker tests need Postgres (the Docker daemon was OFF when I ran them: 55 passed, 39 errors, all DB fixtures)
    # Env: STT_PROVIDER=groq|grok, GROQ_API_KEY | XAI_API_KEY (a provider without its key is OFF, like STT_PROVIDER unset), STT_LANGUAGE (pt; auto = no language field),
    #   STT_API_URL (optional: overrides the vendor URL, tests/proxies; STT_URL is IGNORED by groq/grok: Fly merges env, a stale STT_URL stayed on the live Machine). whispercpp still works (STT_URL required). The key is not in repr(config()) nor in any error message.
    # Requests: Groq whisper-large-v3-turbo (verbose_json, temperature 0, prompt = STT_PROMPT/default); xAI grok-voice-transcribe-2.0 (format=true + language; xAI has no free-text prompt).
    #   Multipart, file LAST. Errors (401/429/5xx/network) -> TranscribeError "<provider>: HTTP <code>" and the existing backoff; an EMPTY answer is asked once more (Grok dropped a 12 s chunk in the spike).
    # Local try (YOU, real audio from a live; keys from your shell, never in the repo):
    #   PATH=/opt/homebrew/bin:$PATH STT_PROVIDER=groq GROQ_API_KEY=... uv run uvicorn app.main:app --port 8000 --reload     (or grok + XAI_API_KEY)
    #   start a Falacias game on a horizontal live: the transcript pane fills every ~12-15 s; `grep "stt "` in the server log shows failures with the reason.
    # Fly (NOT done): set the secret(s), add them to the app container's "secrets" in deploy/fly.machine.json ({"env_var": "GROQ_API_KEY", "name": "GROQ_API_KEY"}; secrets are NOT
    #   injected unless listed), change env STT_PROVIDER, drop STT_URL, deploy, then delete the whisper app (fly apps destroy livebingo-stt). Never put a key in a file or in chat:
    #   fly secrets set GROQ_API_KEY="$(pbpaste)" --app livebingo --stage     (copy the key first; --stage = no restart until the deploy)
    # GOTCHA (found by the user, local run): "groq: HTTP 403" = Cloudflare error 1010 for urllib's default User-Agent. The worker sends `User-Agent: livegames-stt/1.0`.
    #   Keyless proof: default UA -> 403 "error code: 1010"; custom UA -> 401 "Invalid API Key". xAI answers a WRONG KEY with HTTP 400 (not 401): "Incorrect API key provided".
    # NOT checked: a real Groq/xAI call from the worker (only the spike called them), the whole path with a game, Fly's IP with yt-dlp live audio, Groq free-tier rate limits for 3 h.

### Deploying to Fly.io - checklist (every release)
    # 0. Local: tests green and everything committed + pushed.
    uv run ruff check . && uv run pytest -q                    # expect: All checks passed! / N passed
    uv run migrate                                             # applies new migrations to the DEV DB first (cloud applies its own on boot)
    # 1. NO active game in the cloud (the deploy restarts the Machine for ~30 s; data is safe, players lose the page briefly).
    fly ssh console --app livebingo --container db -C "psql -U livegames -d livegames -h 127.0.0.1 -At -c \"select status, count(*) from game_sessions group by 1\""
    #    expect: no "active|N" line. If there is one: finish the game in /admin first (or agree on the interruption).
    # 2. Backup (plain SQL; tested: restores cleanly into a scratch DB). Mandatory when the release has a migration, esp. DROP/ALTER.
    fly ssh console --app livebingo --container db -C "pg_dump -U livegames -d livegames -h 127.0.0.1 --no-owner" 2>/dev/null | grep -v '^Connecting' > data/backup/cloud_full_$(date +%Y%m%d_%H%M%S).sql
    #    expect: ~100+ KB file, 7+ "COPY" lines (grep -c '^COPY'). Files in data/backup/ are gitignored; delete when no longer needed.
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
    #   step-2 backup into an empty DB instead (psql -f <file>), after stopping writes. Never TRUNCATE the cloud DB.
    # Phrases are edited in the cloud (/admin/phrases); the seed file only fills an EMPTY table. To bring the cloud phrases to
    #   local: dump `COPY (SELECT * FROM phrases ORDER BY 1) TO STDOUT WITH (FORMAT csv, HEADER, NULL '\N')` via fly ssh into
    #   data/backup/cloud_phrases.csv, then `uv run python -m deploy.make_seed --from-dump data/backup` (see the Fly section above).

### Validation for Task 1c (one active session per game, migration 0007, app/sessions.py)
    uv run pytest tests/test_sessions_per_game.py -q   # expect: 23 passed
    uv run pytest -q                                   # expect: 424 passed, 1 skipped (Bingo behaviour unchanged; 3 old tests were adapted:
                                                       #   migration-name list, one lambda taking game_type, none changed in meaning)
    uv run migrate                                     # YOU run it on the dev DB: expect "applied: 0007_sessions_per_game.sql" (then "up to date")
    docker compose exec db psql -U livegames -c '\d game_sessions'   # expect index one_active_session_per_game (game_type) WHERE status='active'
                                                                     #   and CHECK game_type IN ('bingo','falacias')
    uv run python -c "
    from app.db import connect; from app import sessions as S
    c = connect()
    b,p,tb = S.start_session(c,'Ana','10.0.0.1'); f = S.start_session_as_admin(c,'falacias')
    print('both active:', S.get_active_session(c).game_type, S.get_active_session(c,'falacias').game_type)
    s,q,tf = S.join_session(c, 'Ana', '10.0.0.1', game_type='falacias'); print('same name:', q.name, '| tokens differ:', tb != tf)
    try: S.start_session_as_admin(c,'falacias')
    except S.SessionAlreadyActive as e: print('2nd falacias refused:', e.code)
    "
    docker compose exec db psql -U livegames -c "TRUNCATE game_sessions, users CASCADE"    # clean up dev rows
    # Mutation check (repo COPY, never the real code): 20/20 mutants killed (script kept in the scratch dir, mutate_1c.py).
    # Fly: schema change -> follow "Deploying to Fly.io - checklist" (no active game, backup first). Nothing is deployed yet.

### Validation for Task 1a (fallacy seed from pt.wikipedia, deploy/make_fallacies_seed.py)
    uv run pytest tests/test_fallacies_seed.py -q      # expect: 18 passed (offline: parses a SAVED copy, tests/fixtures/lista_de_falacias_70610117.json)
    uv run pytest -q                                   # expect: 442 passed, 1 skipped
    uv run python -m deploy.make_fallacies_seed --from-file tests/fixtures/lista_de_falacias_70610117.json
                                                       # expect: "97 fallacies -> deploy/seed/fallacies_seed.sql (20 enabled)", counts per
                                                       #   category, "without article link: 33", missing must-haves: deus das lacunas, onus da prova invertido
    uv run python -m deploy.make_fallacies_seed        # same, but fetches revision 70610117 from the MediaWiki API (needs network)
    head -c 600 deploy/seed/fallacies_seed.sql         # expect: INSERT INTO fallacies (name, category, description, wikipedia_url, source, enabled) VALUES ...
    # The `fallacies` table does not exist yet (migration 0008, Task 1b): the seed is tested against a TEMP table with the planned layout.
    # ONLY the curated 21 (top 20 + "falácia da falácia", app/falacias.py TOP_FALLACIES) are enabled; the other 76 rows stay in the table, disabled. Missing must-haves
    #   (deus das lacunas, onus da prova invertido) are added later as source='manual' (manual rows are never touched by the sync).
    # DB seeded before the cut: `uv run corpus fallacies-top` -> fallacies: {'renamed': R, 'enabled': N, 'disabled': M, 'missing': []}; verify with the psql line above (21 | 97).
    # DISPLAY NAMES: the 21 enabled fallacies show "<pt name> (<English name>)" or "<pt name> (<English name> - <Latin name>)" (app/falacias.py FALLACY_NAMES,
    #   keyed by the pt.wikipedia name; long/complicated names are simplified, no Latin name is invented). E.g. "Pista Falsa (Red Herring)",
    #   "Ladeira escorregadia (Slippery Slope)", "Ataque pessoal (Personal Attack - Ad hominem)", "Falácia da falácia (Fallacy Fallacy - Argumentum ad logicam)".
    #   The names live in the DB table `fallacies` (the code dict only seeds/renames). `uv run corpus fallacies-top` renames an existing DB (same row id,
    #   marks unaffected; skipped if the new name is taken; manual rows untouched): prints renamed: 21 on a DB with the wikipedia names, then 0.
    #   psql check: select name from fallacies where enabled order by 1  -> 21 names.
    #   Change a name later = edit it in the DB (no admin page for fallacies yet) or edit FALLACY_NAMES and regenerate the seed.
    # Text (c) Wikipedia contributors, CC BY-SA 4.0: show the attribution wherever the list appears.
    # Mutation check (repo COPY): 16/16 killed (mutate_1a.py in the scratch dir). reset: rm deploy/seed/fallacies_seed.sql

### Validation for Task 1b (Falácias schema + services, migration 0008, app/falacias.py)
    uv run pytest tests/test_falacias.py -q            # expect: 65 passed (constraints, seed, quote building, submit, review, leaderboard, races)
    uv run pytest -q                                   # expect: 507 passed, 1 skipped
    uv run migrate                                     # YOU run it on the dev DB (applies 0007 too if pending): expect "applied: 0007_..." and "applied: 0008_falacias.sql"
    docker compose exec db psql -U livegames -c '\dt'                       # expect fallacies, replay_events, stream_events, fallacy_marks, host_messages
    docker compose exec db psql -U livegames -c '\d fallacy_marks'          # expect FKs mark_user_in_session, mark_start_event, mark_end_event, unique fallacy_marks_unique_pair
    uv run python -c "
    from app.db import connect; from app import falacias as F
    c = connect(); print(F.load_fallacy_seed(c)); print(F.load_fallacy_seed(c))
    "                                                  # expect {'inserted': 97} then {'skipped': 'fallacies table already has 97 rows'}
    docker compose exec db psql -U livegames -c "select count(*) filter (where enabled) on_, count(*) from fallacies"   # expect 20 | 97
    # Rules implemented (all derived, nothing about points is stored): base +5 (first 20 unreviewed pairs of a player; flag `counts` stored at submit),
    #   Correto +5 and speed 0,1 x max(0, 60 - s) (s = server clock from the LAST selected line's fed_at to submit), Errado = base only, Spam = 0.
    #   Rate limit 1 pair / 10 s per player, no identical pair twice per player (others may repeat it and ALL score), quote recomputed by the server
    #   (max 500 chars, max 30 lines, offsets are UTF-16 units = browser offsets), no review after the session ended, review_same_pairs for identical pending pairs.
    # Not built yet: the feed worker (Task 2), pages (Tasks 3-5), host-message service (Task 5), fallacy admin UI. `host_messages` exists but has no service yet.
    # Mutation check (repo COPY): 57/57 killed (mutate_1b.py in the scratch dir). Clean up dev rows: TRUNCATE game_sessions, users CASCADE (cascades marks/events).
    # Fly: migration 0008 -> follow "Deploying to Fly.io - checklist" (no active game, backup first). Nothing is deployed yet.

### Validation for Task 2 (replay feed: corpus/replay.py, app/feed.py, deploy/seed/replay__nVWPIFCUzQ.sql)
    uv run pytest tests/test_replay.py tests/test_feed.py -q   # expect: 56 passed (parsers, import, seed round trip, session clock, health, CLI)
    uv run pytest -q                                           # expect: 563 passed, 1 skipped
    uv run migrate                                             # YOU: dev DB must have 0008 (see Task 1b)
    uv run corpus replay-import _nVWPIFCUzQ                    # expect: replay rows: {'chat': 2725, 'transcript': 13535}  (again: "skipped"; --force replaces)
    docker compose exec db psql -U livegames -c "select kind, count(*), min(offset_ms), max(offset_ms) from replay_events group by 1"
    uv run corpus replay-seed _nVWPIFCUzQ                      # rewrites deploy/seed/replay__nVWPIFCUzQ.sql (16260 rows, ~1.4 MB; tracked: the Fly image needs it)
    uv run corpus replay-load                                  # expect: replay: {'skipped': '_nVWPIFCUzQ already has 16260 replay rows'}
    # Rules: the session clock starts at game_sessions.created_at ("start from zero"); a line with video offset o appears at created_at + (o - START_AT)/SPEED.
    #   Env: FALACIAS_VIDEO_ID (default _nVWPIFCUzQ), FALACIAS_SPEED (default 1; >1 = demos only, the speed bonus becomes meaningless),
    #   FALACIAS_START_AT (seconds into the video). Lines are released LAZILY by feed.tick_throttled() (called by the feed poll in Task 3), idempotent,
    #   fed_at = scheduled time. No Falácias session can START unless BOTH tracks exist: error "Vídeo offline ou vídeo sem transcrições", no row created.
    #   feed.status(): ok | ended (video over: marks stay allowed) | down (a track vanished: feed.ensure_up raises FeedDown -> the pair route must refuse).
    #   Do NOT run `replay-import --force` during a game (running sessions point at the old row ids and would show lines twice).
    # Real data (scratch DB, dropped after): import 0.3 s; tick after 10 h of video releases 14,740 rows in 0.09 s; status() ~1.7 ms.
    # Cleaning: [annotations] removed, HTML entities decoded, paid-message text kept, gifts/system/empty dropped, channel emoji -> :shortcut:.
    # The first transcript line is at 59 s, the first chat line at 0 s; the longest transcript gap is 22 s.
    # Mutation check (repo COPY): 57/58 killed. The survivor (advisory lock in tick) is redundant protection: the UNIQUE (session_id, kind, source_key)
    #   index already makes concurrent releasers wait for each other, so no test can observe its absence.
    # Not built yet: the pages that call tick (Task 3), the pair route that calls ensure_up (Task 4), the entrypoint/seed wiring (Task 7).

### Validation for Task 3 (menu + the two panes: app/ui.py, app/falacias_web.py, app/static/falacias.js)
    uv run pytest tests/test_falacias_web.py -q   # expect: 24 passed (entry, panes, poll, name flow, menu)
    uv run pytest -q                              # expect: 589 passed, 1 skipped
    uv run migrate && uv run corpus replay-import _nVWPIFCUzQ    # YOU: dev DB needs 0007, 0008 and the replay rows
    uv run uvicorn app.main:app --port 8001       # (8000 is your --reload server) open http://127.0.0.1:8001/falacias
    # expect: menu "Bingo | Encontre as Falácias" on both pages; /falacias shows "Começar partida" if nothing runs, else "Entrar".
    #   After starting: two panes, transcript (left) and chat (right), both EMPTY for the first seconds (the first chat line is at 0 s,
    #   the first transcript line at 59 s); new lines appear by themselves (poll every 2 s) and the pane stays glued to the newest line.
    #   Scroll a pane up: it stops following and shows a "N novas · voltar ao vivo" button; click it to go back to the live edge.
    #   FALACIAS_SPEED=60 uv run uvicorn ... makes a minute of video pass per second (demo only).
    # One name for both games: the first page asks "Qual é o seu nome?" (no tabs yet); the name is remembered (cookie lg_name) and
    #   every page then shows the tabs Bingo | Encontre as Falácias. A game tab shows ONE button ("Começar partida" / "Entrar na partida")
    #   with the remembered name and a "trocar nome" link; nothing joins by itself. A taken or invalid name returns to the name page.
    # Two cookies: lg_token (Bingo) and lg_token_falacias (browser devtools). A Bingo cookie is NOT a Falácias identity.
    # Menu dot: a green dot on a game that has a running session (refreshes every 30 s).
    # End the game: uv run python -c "from app.db import connect; from app import sessions as S; c=connect(); print(S.end_session(c, S.get_active_session(c,'falacias').id, None))"
    #   -> panes poll once, the page reloads and shows "Fim da partida de Falácias!" with the leaderboard.
    # reset: TRUNCATE game_sessions, users CASCADE (stream_events go with it); replay_events stay.
    # Not built yet: selecting text / flagging pairs (Task 4), admin tab (Task 5). Panes keep every line in the DOM (no trimming).
    # Mutation check (repo COPY): 33/33 killed (mutate_3.py in the scratch dir). Not checked: falacias.js in a real browser.

### Validation for the shared nickname + tabs (app/ui.py: name gate, cookie lg_name; follow-up to Task 3)
    # Flow: FIRST page = "Qual é o seu nome?" (no tabs). POST /name stores it (cookie lg_name, NOT an identity) and returns to the tab you
    #   came from (/ or /falacias, whitelisted). Then every page has the tabs "Bingo | Encontre as Falácias" (green dot = a game is running)
    #   and a game tab shows ONE button (Começar partida / Entrar na partida) with "Jogando como <nome> · trocar nome". Nothing joins by itself.
    #   A taken/invalid name sends you back to the name page with the message. POST /start|/join still accept an explicit `name` field.
    #   `join_as_same_name` was REMOVED (nothing used it). An invalid/oversized lg_name cookie is ignored (gate shown).
    uv run pytest -q                              # expect: 633 passed, 1 skipped (adapted: 3 tests in test_web.py/test_admin.py + e2e now set a name first)

### Validation for Task 4 (select, choose a fallacy, submit; my pairs + leaderboard)
    uv run pytest tests/test_falacias_marks_web.py -q   # expect: 39 passed
    uv run pytest -q                                    # expect: 633 passed, 1 skipped
    uv run migrate && uv run corpus replay-import _nVWPIFCUzQ    # YOU, on the dev DB (needs 0007/0008); fallacies: see Task 1b
    FALACIAS_SPEED=20 uv run uvicorn app.main:app --port 8001     # demo speed: 20 s of video per second (speed bonus meaningless)
    # Browser http://127.0.0.1:8001/falacias: give a name, start. SELECT text with the mouse in the transcript or the chat (it may span
    #   several lines of ONE pane): a panel opens with the quote. Type in "Buscar falácia..." (accent/case-insensitive), tick one, "Enviar".
    #   expect: "Enviado! ...", the panel closes, "Meus envios" shows the quote + "Aguardando" + 5,0, the "Placar" shows you with 5,0.
    #   While you hold a selection (or scrolled up) the pane does NOT jump; a "N novas · voltar ao vivo" button appears.
    #   Errors (inline): <10 s since your last send, same passage+fallacy twice, bad/too long selection (30 lines / 500 chars), feed down.
    #   Verdicts (Correto/Errado/Spam) come from the admin tab (Task 5, not built): meanwhile test with
    #   uv run python -c "from app.db import connect; from app import falacias as F; c=connect(); m=F.user_marks(c, <user uuid>)[0]; F.review_mark(c, m.session_id, m.id, 'correct')"
    #   and watch "Meus envios" change within 5 s. CC BY-SA attribution link is under the picker.
    # QA done in a real browser (Chrome, throwaway DB, real video): click + shift-click selection across 2 lines, filter, submit, polls.
    #   Found there (no test could): the Task 3 pane/menu CSS had never been written (a failed `sed` skipped the `cat >> app.css`), the pane
    #   class clashed with Bingo's `.line` (renamed `.pline`), and /menu re-requested itself forever (`load` trigger inside the swapped fragment).
    #   NOT checked: touch selection on a phone, Safari/Firefox, drag with mouseMoved (the QA tool hangs on it), picker height on small screens.
    # Mutation check (repo COPY): 41/41 killed (mutate_4.py in the scratch dir; covers Task 4 routes/views and the name gate).
    # reset: TRUNCATE game_sessions, users CASCADE (stream_events and fallacy_marks go with it).

### Validation for Task 5 (admin Falácias tab, live queue, host messages: app/falacias_admin_web.py, app/host_messages.py)
    uv run pytest tests/test_host_messages.py tests/test_falacias_queue.py tests/test_admin_falacias.py -q   # expect: all passed
    uv run pytest -q                                    # expect: 705 passed, 1 skipped
    uv run migrate && uv run corpus replay-import _nVWPIFCUzQ    # YOU, dev DB (0007/0008); fallacies seed: see Task 1b
    FALACIAS_SPEED=20 uv run uvicorn app.main:app --port 8001
    # Admin (user "admin", ADMIN_PASSWORD): tabs "Bingo | Encontre as Falácias | Frases" on every admin page.
    #   http://127.0.0.1:8001/admin/falacias -> "Iniciar partida" (or start from /falacias as a player).
    #   Players mark pairs: they appear at the BOTTOM of the queue within ~3 s (oldest first; never inserted above, so a click cannot hit
    #   the wrong pair), with 3 lines of context before/after, the quote, and Correto / Errado / Spam. Clicking redraws only that item.
    #   Identical pending pairs show "Todas iguais (n)" buttons: one verdict for the whole group. A verdict can be changed later.
    #   Players type in "Mensagem para o apresentador" (280 chars, 1 per 30 s, max 5): it shows in the admin side panel; "Passada"/"Ocultar" move it
    #   to "Tratadas" (Desfazer brings it back). The side panel also has the leaderboard. "Encerrar partida" ends it; no review afterwards.
    # reset: TRUNCATE game_sessions, users CASCADE.
    # NOT done for Task 5: mutation check, real-browser QA (tests were written AFTER the web code, so no red run for tests/test_admin_falacias.py;
    #   tests/test_host_messages.py and test_falacias_queue.py were written first). htmx swaps (oob beforeend, group redraw) are only checked as HTML.

### Validation for Task 6 (Falácias results: F.finish_session, end screens)
    uv run pytest tests/test_falacias_results.py -q    # expect: 14 passed (red run before the code: 12 failed / 2 passed)
    uv run pytest -q                                   # expect: 720 passed, 1 skipped
    # Play a Falácias game (see Task 5), review some pairs in /admin/falacias, click "Encerrar partida".
    # expect: players see "Fim da partida de Falácias!", "Vencedor(a): <nome> com <pts> pontos" (green), a table (pts, Corretos, Errados, Spam,
    #   Sem veredito; your row highlighted), the note about pairs without a verdict (only if YOU have some) and "Meus envios" with verdicts.
    #   /admin/falacias shows the winner too. Nobody with points > 0 -> "Ninguém pontuou desta vez." and no winner.
    docker compose exec db psql -U livegames -c "select g.status, u.name winner from game_sessions g left join users u on u.id = g.winner_user_id where g.game_type='falacias'"
    # Winner = most points; ties: more Correto pairs, then whoever submitted their last pair first. Stored once (a 2nd finish returns None, keeps it).
    # Not done: mutation check, real-browser look at the end screen.

### Validation for L2 (live-source step 1: migration 0009, typed pairs, per-session video source)
    uv run pytest tests/test_spoken.py -q               # expect: 65 passed (collection error before the code existed: Source/submit_spoken missing)
    uv run pytest -q                                    # expect: 819 passed, 1 skipped
    uv run migrate                                      # YOU, dev DB: expect "applied: 0009_live_source.sql" (then "up to date")
    docker compose exec db psql -U livegames -c '\d game_sessions'    # expect video_id, source_mode + 4 session_* checks
    docker compose exec db psql -U livegames -c '\d fallacy_marks'    # expect kind check with 'spoken', mark_spoken_quote, mark_spoken_shape
    # Nothing in the UI changes yet (pages = step 3, chat worker = step 2). What exists now (services only):
    #   * kind 'spoken' = the player TYPES the passage: falacias.submit_spoken(conn, user_id, text, fallacy_id). It creates its own one-line
    #     stream_event (author = player, never shown in the panes: feed reads filter kind IN transcript/chat) + a pending mark. 1..300 chars AFTER
    #     cleaning (NFC, control chars dropped, whitespace collapsed); same rate limit (10 s), cap (20) and per-player lock as selected pairs;
    #     duplicate = same player + same text (case-insensitive) + same fallacy; no speed bonus (latency fixed at 60 s => correct = 5 + 5).
    #   * Admin queue: a spoken item has NO context; identical typed pairs (same text, case-insensitive, same fallacy, any player) group for one verdict.
    #   * game_sessions.video_id + source_mode ('live'|'replay'); NULL = legacy session = the FALACIAS_VIDEO_ID replay (old rows unchanged).
    #   * feed.resolve_source(conn, live_info, requested): a HORIZONTAL live ALWAYS wins (a replay is never used while one exists); a vertical live
    #     is ignored; no live => the requested replay (youtube id/URL, both tracks imported) else NoSource (the page must ask for a video URL).
    #   * feed.start_falacias(conn, name, ip, source=None) stores the source; tick() and status() follow the SESSION's source
    #     (live => no replay clock, status ok; the chat worker's health arrives with step 2).
    # Mutation check (repo COPY): 41/41 killed (mutate_L2.py in the scratch dir; the first run had 1 survivor -> test added).
    # reset: TRUNCATE game_sessions, users CASCADE.

### Validation for L3 (live-source step 2: chat worker, app/chatworker.py)
    uv run pytest tests/test_chatworker.py -q           # expect: 44 passed (collection error before the module existed)
    uv run pytest -q                                    # expect: 863 passed, 1 skipped
    # What exists (no page uses it directly yet): feed.tick() of a LIVE falacias session starts ONE daemon thread (chatworker.ensure) that runs
    #   `python -m yt_dlp --skip-download --write-subs --sub-langs live_chat -o chat <watch url>` in a private temp dir, tails
    #   chat.live_chat.json.part, and stores text/paid messages as stream_events kind 'chat' (source_key = YouTube message id, so a restart's
    #   ~2 MB backlog replay is deduped; messages older than the session or without timestamp are dropped). Pages show them like replay chat.
    # Health: 'down' when the process died / printed nothing for 90 s / start failed / DB error; restart with backoff 2,4,8..60 s.
    #   feed.status() of a live session returns that health, so feed.ensure_up() refuses new pairs while the chat is down (replay sessions unaffected).
    #   The thread ends by itself when the session ends. Tests never start yt-dlp (autouse fixture in tests/conftest.py uses a fake process).
    # Real smoke (read-only, YOU can repeat; ~50 s; needs a live video id):
    uv run python -c "
    import time; from app import chatworker as C
    p = C.YtdlpChat('Ko_lIgMJudY'); p.start(); time.sleep(40); L = p.read_lines(); m = C.parse_lines(L)
    print('alive', p.alive(), 'lines', len(L), 'text msgs', len(m), 'with ts', sum(x.ts_us is not None for x in m)); p.stop()
    "                                                   # expect (my run): alive True, 77 lines, 72 text msgs, 72 with ts (first output after ~13 s)
    # Mutation check (repo COPY): 35/37 killed (mutate_L3.py). Survivors are equivalent: "ts ignored" (SQL to_timestamp(NULL) already rejects) and
    #   "no SIGTERM" (the 5 s wait then SIGKILLs the child). Not checked: the thread against a real live session end to end (needs step 3 pages).
    # Fly: yt-dlp needs a WRITABLE temp dir (root fs is read-only): configure in Task 7. Risks: `.part` is a yt-dlp internal; YouTube may block it.

### Validation for L4 (live-source step 3: pages — source choice, typed quotes, chat-down banner, admin)
    uv run pytest tests/test_live_pages.py -q           # expect: 51 passed (collection error before the texts/code existed)
    uv run pytest -q                                    # expect: 914 passed, 1 skipped (2 old tests adapted: the "feed refused" message now names the video + import command)
    uv run migrate                                      # YOU: dev DB needs 0009
    uv run uvicorn app.main:app --port 8001             # open http://127.0.0.1:8001/falacias (and /admin/falacias, user admin)
    # Start (player OR admin): a HORIZONTAL live of the channel always wins (a replay is never used while one exists; a typed URL is ignored).
    #   No horizontal live => a "Link do vídeo gravado (replay)" field appears; empty => "O canal não está ao vivo na horizontal. Cole o link...";
    #   not imported => "O vídeo <id> ainda não foi importado. No servidor, rode: uv run corpus replay-import <id>"; bad/foreign URL => "Link inválido".
    #   FALACIAS_VIDEO_ID set in the env = default replay when nothing else is given (tests/demos). The first start waits for the live lookup (~15 s).
    # Live game page: source line "Transmissão ao vivo da partida" (title + URL), chat pane fed by the chat worker, transcript pane says there is none,
    #   box "Frase falada" (<=300 chars + fallacy <select>) -> POST /falacias/spoken (same limits as picks; works while the chat is down).
    #   Chat worker down -> red banner "Chat ao vivo fora do ar..." and selected pairs are refused (typed ones are not).
    # /admin/falacias: shows the session source; typed pairs carry a "digitada" tag, no context, no timing; identical typed pairs group.
    # Mutation check (repo COPY): 35/35 killed (mutate_L4.py). NOT checked: any of this in a real browser (htmx swaps, box clearing via the
    #   fal:spoken-sent event in falacias.js, mobile layout), nor against the real live end to end. Do that before the deploy.
    # reset: TRUNCATE game_sessions, users CASCADE.

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

### Validation for FC1 (fact-checker game: migration 0013, app/checker.py) - services only, no AI, no page yet
    uv run pytest tests/test_checker.py -q             # expect: 82 passed (red run first: collection error, app.checker did not exist)
    uv run pytest -q                                   # expect: all passed (test_schema's migration list now ends in 0013_checker.sql)
    uv run migrate                                     # YOU, dev DB: expect "applied: 0013_checker.sql". CLOUD: backup first (deploy checklist); nothing uses it until FC4.
    docker compose exec db psql -U livegames -c '\d fact_checks' -c '\d fact_claims'    # expect the CHECKs: verdict/score sign, guess -5..5, one claim per player+text
    # Third game_type 'checker' ("Quem ta metendo o loco?"), one active session like the others, cookie lg_token_checker. Players GUESS -5..+5; an AI check
    #   (FC2/FC3) or the admin gives the real score. ONE check per distinct text per session (casefolded), many claims point at it.
    # Points (computed on demand, so an admin override re-scores everybody): max(0, 5 - |guess - final|) + 2 if the sign is right (0 has no sign);
    #   a guess of 0 earns nothing when |final| >= 3; none while pending / dismissed / unverifiable. Final = admin score, else the AI score of a `done` check.
    # Check states: queued -> running -> done | needs_admin (low confidence, unverifiable, 3 failed runs) -> resolved (admin score, or dismissed = admin_score NULL).
    #   A needs_admin result is still shown to players but earns no points until the admin acts.
    # Limits: 10 claims per player (MAX_CLAIMS_PER_PLAYER), one per 10 s, same player+text refused, 1..300 chars typed, message <= 280.
    # Winner: most points; ties: more exact guesses, then whoever sent the last claim first; nobody above 0 -> no winner.
    # Mutation check (repo COPY, mutate_fc1.py): 30/31 killed (first run: 5 survivors -> tests added). Survivor: dropping SKIP LOCKED from claim_next_check;
    #   Postgres re-checks the row after the lock wait, so with the test's tiny jobs the result is the same. Kept because it prevents workers from queueing behind each other.
    # NOT built: the agent (FC2), the worker thread (FC3), pages (FC4), admin page (FC5), end screen (FC6), CHECKER_MODEL setting.

### Validation for FC2 (fact-checking agent: app/factcheck.py) - no database, no page, nothing calls it yet
    uv run pytest tests/test_factcheck.py -q           # expect: 75 passed (red run first: collection error, app.factcheck did not exist)
    uv run pytest -q                                   # expect: all passed (the fake HTTP/ddgs/model are all local: no test touches the network)
    uv run ruff check .                                # expect: All checks passed!
    # What it is: PydanticChecker.check(claim, context_lines, note) -> checker.AiResult, or raises CheckFailed("short pt-BR reason"). The worker (FC3) will store
    #   the result with checker.complete_check or the reason with checker.fail_check. FakeChecker(script) is the stand-in for worker tests and demos.
    # Same two-step agent as the FC0 spike (research WITH tools -> plain notes; answer with NO tools and a JSON schema), validators unchanged (sources must be URLs the
    #   search returned, score sign agrees with the verdict, unverifiable => 0), limits: 4 searches, 3 page fetches (SSRF-safe, every redirect hop re-checked),
    #   240 s for the whole check. Errors become short reasons (429 / key refused / provider down / timeout / search blocked / invalid answer); a response body is never copied.
    # Setting: CHECKER_MODEL=groq:<id> | openrouter:<id> (keys GROQ_API_KEY / OPENROUTER_API_KEY). Empty/other = off; factcheck.off_reason() says why (never a key).
    #   Local Ollama is NOT supported here (dropped for cost); the spike still compares models: spikes/checker.py.
    # Dependencies: pydantic-ai-slim[duckduckgo,openai] and httpx moved from the `checker` group into the main dependencies (the group is gone; the Dockerfile no longer
    #   passes --no-group checker). NOT verified: `docker build` with them (do it before FC7's deploy), Python 3.14 in the image.
    # tests/conftest.py also scrubs CHECKER_MODEL, GROQ_BASE_URL, OPENROUTER_BASE_URL from the environment.
    # Mutation check (repo COPY, mutate_fc2.py): 41/42 killed (first run: 6 survivors -> 5 tests added/fixed, 1 redundant `except CancelledError` removed:
    #   CancelledError is a BaseException, so `except Exception` never swallowed it; test_cancellation_is_not_swallowed guards it).
    # NOT checked: a real model call through this module (only the spike called the real APIs), DuckDuckGo from Fly's IP.
    # Real smoke (YOU, one claim, costs ~US$0.003; keys come from .env):
    #   uv run python -c "import asyncio; from app import factcheck as F; c = F.from_env(); print(F.off_reason() or asyncio.run(c.check('os Annunakis existem')))"
    #   expect (with CHECKER_MODEL=openrouter:openai/gpt-oss-120b): AiResult(verdict='false', score=-3 or -4, confidence='high', ..., 1-3 sources, tokens, elapsed_ms ~ 30-70 s)

### Validation for FC3 (checker worker: app/checkworker.py, started by app/main.py's lifespan) - no page yet
    uv run pytest tests/test_checkworker.py -q         # expect: 33 passed (red run first: collection error, app.checkworker did not exist)
    uv run pytest -q                                   # expect: all passed
    # What it does: daemon threads (CHECKER_WORKERS, default 2, max 4) take queued fact checks of ACTIVE checker games (checker.claim_next_check, SKIP LOCKED), call
    #   the checker with the claim + the lines around the passage + the player's message, and store the answer (complete_check) or the reason (fail_check):
    #   failure => back to the queue with the reason, after 3 runs => admin queue (needs_admin). Inconsistent answer => "resultado invalido" failure. A restart requeues
    #   checks left running. wake() (FC4 will call it after a claim) skips the 5 s poll. No checker configured => NO thread; claims stay queued (the admin scores by hand, FC5).
    # Off/on is logged once per cause: "fact-checker is off: CHECKER_MODEL is not set ..." (INFO) or "... needs GROQ_API_KEY, which is not set" (WARNING);
    #   "fact-checker on: <model>, N worker(s)". Failures log "fact check <id> failed (attempt n): <reason>". grep the app log for "fact".
    # Try it (YOU; needs CHECKER_MODEL + key in .env, costs ~US$0.003 per claim, there is no page to send a claim yet, so insert one):
    #   uv run uvicorn app.main:app --port 8000 ; expect the log line "fact-checker on: ..."
    #   uv run python -c "
    #   from app.db import connect; from app import sessions as S, checker as C
    #   c = connect(); S.start_session_as_admin(c, game_type='checker'); _, p, _ = S.join_session(c, 'Ana', '10.0.0.1', game_type='checker')
    #   print(C.submit_typed_claim(c, p.id, 'os Annunakis existem', -4).check_id)"
    #   docker compose exec db psql -U livegames -c "select status, verdict, ai_score, confidence, model, elapsed_ms, error from fact_checks"   # queued -> running -> done in ~30-70 s
    #   (the worker polls every 5 s; there is no wake yet because nothing calls it from a page). reset: TRUNCATE game_sessions, users CASCADE (see Task 7).
    # CheckFailed moved to app/checker.py (factcheck re-exports it) so the worker does not import pydantic-ai when the game is off: the 1 GB Machine starts as before.
    # tests/conftest.py now ALSO points app.config.ROOT at an empty dir: factcheck.off_reason() calls load_env() again, which brought the developer's .env keys back into a test.
    # Mutation check (repo COPY, mutate_fc3.py): 26/26 killed (first run: 3 survivors -> 3 tests added: requeue by another thread, drain without wake, closed connections).
    # NOT checked: the worker against a real model and a real game; the app's startup time/memory with pydantic-ai loaded on Fly (it loads only when CHECKER_MODEL is set).

## Conventions log
(Append new conventions below as they are agreed.)
- DB access from services: ALWAYS use an autocommit connection (`app.db.connect()` / pool with
  autocommit=True). On a normal connection `conn.transaction()` silently becomes a savepoint that is
  never committed; service write functions call `ensure_autocommit()` and raise otherwise.
- Tests of services must check durability from a SECOND connection (reading through the writing
  connection hides uncommitted data).
- Mutation-check new logic on a COPY of the repo in the scratch dir (never edit real code/migrations
  to break them): every mutant must make the suite fail.
- Azure resources, if ever created: tag `owner=<owner-email>`.
