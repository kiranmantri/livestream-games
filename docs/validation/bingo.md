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

### Validation for the Bingo admin auto-refresh (task 8, user 2026-10-26): app/admin_web.py (cards_panel, watcher, /admin/cards, /admin/watch), admin.js
    # What updates by itself on /admin (no click, no full reload):
    #   - users table + player count: every 5 s (as before);
    #   - player cards (#cards-panel): polled every 5 s with a signature (marks, admin blocks, players); 204 while nothing changed.
    #     New players' cards appear, "Ana - 1/16" follows the marks, OPEN cards stay open (admin.js reads them before the swap and reopens
    #     them after); a redraw that would land while an unmark/unblock is on its way is skipped (next poll brings it). last_seen_at is NOT
    #     in the signature (it changes on every player poll and is not shown on the cards).
    #   - the game ended or another one started: the page reloads (HX-Refresh) and shows the final result;
    #   - no game running (none yet, or the last one ended): #bingo-watch polls /admin/watch every 5 s and reloads when a new Bingo game starts
    #     (a player can start Bingo as the first visitor, without the admin).
    uv run pytest -q tests/test_admin_bingo_autorefresh.py     # expect: 11 passed (red first: 8 failed)
    uv run pytest -q                                           # expect: 1906 passed, 1 skipped
    # Mutation check (scratch copy): 6 mutants (signature without marks, always 204, cards poll ignores the session id, watcher ignores a new
    #   ended game, no watcher on the ended page, JS swaps during an action) -> every one fails the file (2 tests added for the 2 that survived).
    # Real browser (me, throwaway DB lg_qa, Chrome, Basic auth login): admin page with no game -> Ana started Bingo from her phone page ->
    #   admin reloaded by itself; opened Ana's card; Bia joined + Ana marked -> ~5 s later "Ana - 1/16 [open]", Bia's card added, no reload;
    #   game ended -> page showed "Partida encerrada / Vencedor(a): Ana" by itself; Caio started a new one -> page reloaded to it; admin
    #   unmarked a cell in Caio's open card -> "Caio - 1/16", card still open, cell blocked, later polls 204.
    # By hand: open /admin in one window, join from a private window, mark cells; open a card on /admin and watch it stay open while marks arrive.
    # Load: one extra GET every 5 s per open admin tab (players + revoked marks query); same order as the users table poll.

### Validation for the 2x hover zoom of the others' cards (user 2026-10-28): app/main.py (#cardzoom), game.js, app.css
    # Player page: with a mouse/trackpad, resting ~120 ms on another player's card shows a copy 2x as WIDE (fonts lifted to ~2x too)
    #   over a dimmed page, centred on the card and kept inside the window; it closes when the mouse leaves the card, on scroll or
    #   when the window loses focus. The overlay is click-through (pointer-events: none) and aria-hidden (a visual copy).
    #   Not on touch (pointerType !== 'mouse': phones keep the long-press tooltip) and not in the list view (no grid there).
    #   The 5 s /cards poll redraws the cards: the copy is rebuilt from the new card still under the mouse (fresh marks), or closed.
    #   Started at 1.5x, changed to 2x on request (ZOOM constant in game.js).
    uv run pytest -q tests/test_card_zoom.py      # expect: 6 passed (red first: 5 failed)
    uv run pytest -q                              # expect: 1927 passed, 1 skipped
    # Mutation check (scratch copy): 12 mutants (factor, width not scaled, touch allowed, list view zooms, no poll follow, overlay not
    #   click-through / not fixed / [hidden] rule dropped, font caps kept, overlay missing / visible / inside the polled #others) -> 12/12 killed.
    # Real browser (me, throwaway DB lg_qa, Chrome): 4 other players, Bia with marks. 1280 px: card 224.8 px -> copy 449.6 px (ratio 2.0),
    #   cell font 5.3 -> 10.9 px, 0 overflowing cells; first card clamped to x=8; 900 px: 213.5 -> 427 px, last card ends 8 px from the edge.
    #   Bia marked a cell while hovered (1.5x run) -> after the poll the copy showed the new count; mouse out -> closed; list view -> no copy.
    #   NOT checked: Safari/Firefox, a real touch device (CDP only sends mouse events), very short windows (copy taller than the window).
    # By hand: uv run uvicorn app.main:app --port 8001 against a throwaway DB, join from two browsers, mark cells in one,
    #   hover that player's card in the other: a 2x copy appears and follows the marks; move away -> it disappears. Drop the DB after.

## Bingo end screen = the game screen without clicks

After the admin ends the session, `/` shows: "Fim de jogo!" + winner + "your result", MY card (same `.toprow > .cardbox.mine`, same size as in the game,
no buttons, marked times and blue lines kept), the grid/list toggle, and everybody else's cards with the 2x hover zoom (game.js). No stream, chat,
leaderboard table or polling (`others_view(poll=False)`: a poll would answer HX-Refresh forever once the player is no longer in an active session).
Measured in Chrome at 756 px: my card 560 x 437 px, cells 131.5 x 88 px, identical to the running game; 3 other cards; hover zoom opens (480 px).
`uv run pytest -q` -> 1929 passed, 1 skipped. By hand: play with 2-3 browsers, mark cells, end the game from /admin, reload `/`.
Not checked in a browser: phones, Safari/Firefox.

## Falácias / Aponte o mentiroso end screens

Both end screens: ranking table, then tabs "Minhas frases | Outros participantes" (`ui.result_tabs`, `app/static/tabs.js`); others' cards are
named by player (`F.session_marks`, `C.session_claims`). Not looked at in a real browser (covered by the pytest suite only).


## Bingo celebration effects (mark / line / full card)

A tap that MARKS one of my cells (not an unmark, not a revoked cell) plays an effect drawn on a throwaway `<canvas>` (`app/static/fx.js`,
called from game.js as `window.bingoFx.tap(cell, e)`; pointer-events: none, removed when done):
- every mark: ~0.7 s sparks + a flash ring from the tap point, on a canvas laid exactly over that cell inside `.board` (box taken from the
  board's grid tracks, so it matches the cell to the sub-pixel and nothing leaves the cell; a child of the board, not of the cell, because
  the server answer replaces the cell while the sparks still fly);
- the mark completes a row / column / diagonal: bursts along each new line, cell by cell, plus confetti, on a canvas = my card's padding box
  (`.cardbox.mine` is now `position: relative`);
- the mark completes the whole card: ~5 s fireworks (9 rockets) + confetti over the whole window (`position: fixed`, z-index 60), one at a
  time; the line effect is skipped then. Unmarking and re-marking the last cell plays it again.
Detection is client-side from the cells' aria-pressed right after the optimistic flip (free avatar cell counts, revoked cells do not), so the
effect starts at the tap, not after the server answer. OS "reduce motion" -> no effects. Tunables: CELL_MS / CARD_MS / SCREEN_MS in fx.js.

    uv run pytest -q tests/test_bingo_fx.py       # expect: 5 passed (red first: 5 failed)
    uv run pytest -q                              # expect: 1935 passed, 1 skipped
    # Mutation check (scratch copy, mutate_fx.py): 9 mutants (script not loaded, fx on unmark, fx on revoked cell, layer catches taps,
    #   card layer not inset 0, card not relative, screen layer not fixed, reduced-motion ignored, full card ignored) -> 9/9 killed.
    # Real browser (me, throwaway DB lg_qa, Chrome): 1280 px: cell canvas 131.5 x 131.5 at the cell's exact left/top; card canvas 554 x 598
    #   = card 560 x 604 minus its 3 px border; full card -> screen canvas 1280 x 900, gone after ~5.6 s. 390 px (phone, dpr 3): cell canvas
    #   85 x 102.14 vs cell 85 x 102.16, buffer 255 x 306; unmark -> no canvas. newLines() checked for row, column+diagonal, diagonal.
    #   NOT checked: Safari/Firefox, a real phone, a 5x5 card (old sessions; same code path, free cell counted as marked).
    # By hand: uv run uvicorn app.main:app --port 8001 against a throwaway DB, start a Bingo game, tap a cell (sparks inside it only),
    #   complete a row (bursts + confetti inside the red card only), mark all 16 (fireworks over the window). Drop the DB after.

## Bingo effects v2: longer effects, endless full-card fireworks

- Cell sparks 0.7 -> 2.8 s and line bursts 1.6 -> 6.4 s (doubled twice on request; CELL_MS / CARD_MS in fx.js). Gravity lowered so the
  sparks float inside the cell / card instead of dropping out of it early.
- Full card: the fireworks (a rocket every 0.35-0.75 s + steady confetti) NEVER stop by themselves. They stop (the ones in the air finish,
  then the canvas goes) when:
  - I unmark a cell (game.js -> `bingoFx.stop()`);
  - the admin unmarks one: while they run, fx.js re-reads my board from the new `GET /myboard` every 5 s (BOARD_POLL_MS; skipped while a tap
    is in flight) and stops once it is no longer full. The redraw also shows the revoked cell (dashed) at once. `/myboard` answers
    HX-Refresh when I am no longer in an active game, so the end of the game reloads into the end screen.
- A reload with a full card starts them again. End screen: `results_page` adds `data-fx="fireworks"` (+ loads fx.js) only when I am the
  WINNER and my card is full (ties: whoever filled it first); they run forever there. A winner without a full card gets none.

    uv run pytest -q tests/test_bingo_fx.py tests/test_bingo_fireworks.py   # expect: 15 passed (red first: 7 failed)
    uv run pytest -q                              # expect: 1944 passed, 1 skipped
    # Mutation check (mutate_fx.py): 15 mutants (fx.js not loaded, fx on revoked, unmark does not stop, layer catches taps, reduced motion
    #   ignored, full ignored, poll under a pending tap, poll never stops, end screen not started, durations, winner check dropped, full check
    #   dropped, fx.js on every end screen, /myboard without HX-Refresh, /myboard hides revoked) -> 15/15 killed (the winner-check survivor
    #   of the first run got the "full card that lost the tie" test).
    # Real browser (me, throwaway DB lg_qa, Chrome 1280 px): cell canvas lived 2.75-2.8 s, line canvas 6.9 s; full card -> fireworks still on
    #   after 12 s; unmark -> canvas gone ~4 s later; re-mark -> back; reload -> back; admin revoke (M.revoke_mark from Python) -> ~4 s later
    #   the board showed 15/16 with the dashed cell and the fireworks stopped; lift + re-mark, finish_session -> the page reloaded itself into
    #   "Fim de jogo!" with the fireworks still running 10 s later.
    #   NOT checked: Safari/Firefox, a real phone, battery/CPU cost of an endless full-window canvas on a weak phone.
    # By hand: same setup as the block above; fill the card, wait (fireworks keep going), unmark one cell (they wind down), re-mark, then
    #   in /admin unmark one of your cells (within ~5 s the board shows it and the fireworks stop), re-mark it, end the game from /admin.

## Bingo admin review of marks (Correto / Incorreto) and kick

Migration 0015: `bingo_marks.confirmed_at` + table `kicked_players`. Admin `/admin`, new panel "Marcações" between Usuários and Cartelas:
every current mark (player, phrase, time, status), pending first then newest, a "N a conferir" pill, polled every 5 s (`/admin/marks?sid&sig`,
204 while unchanged).
- ✔ Correto (`POST /admin/confirm`): `confirmed_at` set -> the player's cell gets a yellow ring + 🔒 and the title "Confirmada pelo
  administrador..."; game.js never sends a tap on it and the server refuses one (CellLocked -> board with the reason). 🔒 also on the admin's card.
- ✕ Incorreto (`POST /admin/reject`, also on a confirmed mark): the mark is deleted, NOT blocked; the player may mark it again (pending again).
- Expulsar (users table, confirm dialog, `POST /admin/kick`): user + card + marks deleted (cascade), `created_by` cleared if it was the starter,
  cookie hash stored in kicked_players. The kicked player's page reloads at its next 5 s poll (HX-Refresh) into the join screen with
  "O administrador removeu você desta partida..."; joining again gives a NEW card (same name allowed).
- Player page: game.js polls `GET /myboard?v=m=..;l=..;r=..` (what the cells show) every 5 s, skipped while a tap is in flight; 204 while the
  DB agrees, else the board is redrawn (also shows the old "Desmarcar" block at once now). The full-card fireworks stop on a redraw that
  leaves the card not full (fx.js htmx:afterSettle) instead of fx.js's own poll.
- All marks still count at the end (reviewed or not); csrf = the per-session admin token, like unmark/unblock.

    uv run pytest -q tests/test_bingo_review.py   # expect: 17 passed (red first: 17 failed)
    uv run pytest -q                              # expect: 1961 passed, 1 skipped
    # tests/test_lines.py users-table sort test now cuts the page at id="marks-panel" (the new table lists names too).
    # Mutation check (mutate_review.py, scratch copy): 16 mutants (lock ignored on toggle, re-confirm, reject also blocks, list order, kick in an
    #   ended game, no kick notice row, kick across sessions, no locked class, signature ignores locks, /myboard always 204, no kicked notice,
    #   locked tap sent, confirm / kick without csrf, marks poll never 204, no 🔒 on the admin card) -> 16/16 killed.
    # Real browser (me, throwaway DB lg_qa, Chrome 1280 px): Bia (curl, 3 marks) + QA (browser, 2 marks) -> "5 a conferir"; Correto on QA's row ->
    #   🔒 confirmada; Incorreto on QA's other row -> gone, QA 1 mark; QA's page: locked cell with title, tap on it -> still marked; re-marked
    #   the rejected cell -> allowed; confirm from Python -> lock appeared ~1 s later without reload; reject from Python -> cleared without reload;
    #   Expulsar QA from the users table -> QA gone from users + marks; QA's `/` -> join screen with the notice; rejoined -> new card 0/16;
    #   kicked Bia (the starter) -> created_by NULL, her /myboard -> HX-Refresh.
    #   NOT checked: phones (the 5-column marks table on 390 px), Safari/Firefox, two admins acting on the same mark at once.
    # By hand: start a throwaway-DB server, join from two browsers, mark cells, open http://admin:<pw>@127.0.0.1:8001/admin and use
    #   Correto / Incorreto / Expulsar; watch the player pages change within ~5 s. Reset: DROP the throwaway DB.



## Marcações grouped per phrase (review applies to every player)

What changed: /admin "Marcações" shows one row per phrase ("Frase | Quem marcou | Situação"); "Quem marcou" lists one
player per line with the time they marked it (no count). Correto confirms the phrase for ALL players (their cells lock) and any later mark of it is confirmed at
once; Incorreto clears the phrase from every card (not blocked). Table `confirmed_phrases` (migration 0016).
Code: app/marks.py (confirm_phrase / reject_phrase / phrase_marks; toggle_mark auto-confirms), app/admin_web.py marks_panel.

    uv run pytest -q tests/test_bingo_review.py      # expected: 22 passed
    uv run ruff check .                              # expected: All checks passed!

By hand (docker compose up -d db; uv run uvicorn app.main:app --reload):
1. Start a game, join as two players in two browsers (one private window). Mark the SAME phrase on both cards
   (pick a phrase that appears on both; the admin's cards panel shows them).
2. /admin -> Marcações: ONE row for that phrase, "Quem marcou" lists both names with times, status "a conferir".
3. Click Correto: both players' cells show the lock within 5 s; clicking them gives the "confirmada" message.
4. A third player who has the phrase marks it now: their cell is locked immediately, and they appear in the same row.
5. Click Incorreto on that row: the phrase disappears from every card; players can mark it again (pending).
DB: docker compose exec db psql -U livegames -d livegames -c "SELECT * FROM confirmed_phrases;"
   expected: one row (session_id, phrase_id) after step 3, gone after step 5.
Reset: finish the game from /admin and start a new one (confirmed_phrases is per session; old rows are harmless).


## Secret keywords + card swaps (Bingo)

What changed: /admin has a "🔑 Palavras-chave (secretas)" panel above Marcações: pick up to 3 phrases from this game's cards
(Adicionar / Remover; "travada" once a mark of it is confirmed) and a "Trocas" log (who earned a swap with which keyword,
and: disponível / trocou com X às HH:MM:SS / recusou). Keyword rows in Marcações carry a 🔑 (admin only). When the admin
confirms a keyword phrase, every player who marked it earns ONE card swap: their board shows "🎁 Você ganhou uma troca de
cartela!" with a "Trocar com" list (everyone except players whose current card has the keyword marked) and
"Não quero trocar". The swap exchanges the whole card with its marks/locks/blocks; the other player sees
"🔄 X trocou de cartela com você às ..." within 5 s. Code: app/swaps.py, app/main.py (/swap, /swap/decline, my_state),
app/admin_web.py (keyword_panel, /admin/keywords/add|remove), migrations/0016_keywords_swaps.sql.

    uv run pytest -q tests/test_bingo_swap.py         # expected: 25 passed
    uv run pytest -q                                  # expected: all passed (1 skipped)
    uv run ruff check .                               # expected: All checks passed!

By hand, on a throwaway DB (never the dev DB with a live game):
    docker compose exec -T db psql -U livegames -c "CREATE DATABASE lg_qa"
    DATABASE_URL="$(grep '^DATABASE_URL=' .env | cut -d= -f2- | sed 's#/livegames$#/lg_qa#')" uv run migrate
    docker compose exec -T db sh -c "pg_dump -U livegames -a -t phrases livegames | psql -q -U livegames -d lg_qa"
    DATABASE_URL="$(grep '^DATABASE_URL=' .env | cut -d= -f2- | sed 's#/livegames$#/lg_qa#')" MAX_USERS_PER_IP=10 \
      REPLAY_COLLECTOR=off uv run uvicorn app.main:app --port 8001
1. Three players (three browsers / private windows): Ana starts at http://127.0.0.1:8001/, Bia and Caio join.
2. /admin (port 8001): in the keyword panel pick a phrase that is on Ana's and Bia's cards but not Caio's (the option shows
   "N cartela(s)"; open the cards below to check), Adicionar. Ana and Bia mark it.
3. Marcações: the row has 🔑, "Quem marcou" = Ana / Bia. Click Correto. Expected without reload: keyword "travada", Trocas
   log "Ana ganhou uma troca ... — disponível" and the same for Bia; within 5 s Ana's and Bia's boards show the 🎁 box,
   with only Caio in "Trocar com" (Bia is excluded because she marked the keyword). Players never see 🔑 or the keyword.
4. Ana: Trocar de cartela -> confirm. Expected: Ana has Caio's former card + marks and "🔄 Você trocou de cartela com Caio";
   within 5 s Caio's board changes to Ana's former card with its marks (keyword locked) and "🔄 Ana trocou de cartela com você".
   Bia's box now lists only Ana (Caio holds the card with the keyword marked).
5. Bia: "Não quero trocar" -> the box disappears; Trocas log says "recusou".
6. Incorreto on a keyword row before a swap is used -> the unused swaps vanish from the boards and the log.
DB: docker compose exec db psql -U livegames -d lg_qa -c "SELECT user_id, phrase_id, used_at, declined_at, target_id FROM swap_rights;"
Cleanup: pkill -f "uvicorn app.main:app --port 8001"; docker compose exec -T db psql -U livegames -c "DROP DATABASE lg_qa"
