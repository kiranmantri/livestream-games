# Validation: Bingo

Bingo game: schema, sessions/identity, cards, scoring, player and admin pages, phrases, 4x4 cards.
Split out of AGENTS.md (see its "Validation docs" index). Blocks are in the order they were written;
later blocks supersede earlier ones where they disagree. Expected test counts are as of when each block was written.

### Validation for Task 1 (scaffold)
    uv sync                                   # expect: resolves, no errors
    uv run corpus                             # expect: "corpus: not implemented yet"
    uv run ruff check .                       # expect: All checks passed!
    docker compose up -d db                   # expect: container started
    docker compose ps                         # expect: db "Up ... (healthy)"
    docker compose exec db psql -U livegames -c "select version();"   # expect: PostgreSQL 17.x
    # reset: docker compose down        (add -v to also delete the DB volume)

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
