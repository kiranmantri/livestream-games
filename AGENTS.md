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
    # ONLY the curated top 20 (app/falacias.py TOP_FALLACIES) are enabled; the other 77 rows stay in the table, disabled. Missing must-haves
    #   (deus das lacunas, onus da prova invertido) are added later as source='manual' (manual rows are never touched by the sync).
    # DB seeded before the cut: `uv run corpus fallacies-top` -> fallacies: {'renamed': R, 'enabled': N, 'disabled': M, 'missing': []}; verify with the psql line above (20 | 97).
    # DISPLAY NAMES: the 20 enabled fallacies show "<pt name> (<English name>)" (app/falacias.py FALLACY_NAMES, keyed by the pt.wikipedia name, e.g.
    #   "Pista Falsa (Red Herring)", "Ladeira escorregadia (Slippery Slope)", "Equívoco (Equivocation)", "Apelo à incredulidade (Argument from Incredulity)",
    #   "Falácia post hoc (Post Hoc Ergo Propter Hoc)"). The seed file carries the display names; `fallacies-top` renames an existing DB (same row id, marks
    #   unaffected; skipped if the new name is taken; manual rows untouched): first run on a DB with the old names prints renamed: 20, then 0.
    #   psql check: select name from fallacies where enabled order by 1  -> 20 names, each ending in (English name). Change a name = edit FALLACY_NAMES,
    #   `uv run python -m deploy.make_fallacies_seed --from-file tests/fixtures/lista_de_falacias_70610117.json`, then `fallacies-top` (a name already
    #   renamed cannot be renamed again by the sync: edit it in the DB with UPDATE fallacies SET name=... or add the previous display name as a key).
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
