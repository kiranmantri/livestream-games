# Validation: Shared UI and admin shell

Cross-game UI: adversarial-test fixes, backlog groups 1-3, visual refresh, personal link, admin shell v2 (one session).
Split out of AGENTS.md (see its "Validation docs" index). Blocks are in the order they were written;
later blocks supersede earlier ones where they disagree. Expected test counts are as of when each block was written.

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

### Validation for the personal link in the header (app/ui.py: CookieContext, personal_link, GET /r)
    uv run pytest tests/test_major_bugs.py tests/test_falacias_web.py -q    # expect: all passed (red run first: 3 failed)
    uv run pytest -q                                    # expect: 1338 passed, 1 skipped
    # The "Salvar link para voltar nesta partida" section is gone from the bottom of both game pages. Instead the header has a link icon
    #   (class mylink) LEFT of the profile button, on every page, once the visitor has a game cookie. ONE link for all games: /r?b=<bingo token>&f=<falacias token>
    #   (only the games the player is in). Opening it elsewhere sets every valid cookie and the name. Old /r/<game>/<token> links still work.
    #   SECURITY: whoever holds the link IS the player. The tokens are in the URL (history, logs): accepted for a low-stakes game.
    # Try: play in one browser, right-click the icon > copy link, open it in a private window: same cards and marks.
    # NOT checked in a real browser (icon size/alignment on desktop and phones).

### Validation for admin shell v2 (one session for the three games; app/globalsession.py, app/admin_web.py)
    uv run pytest tests/test_admin_shell.py -q         # expect: 43 passed (red run first: 42 failed). Full suite: uv run pytest -q -> 1763+ passed, 1 skipped
    # Names: Bingo | Quem é Falacioso? | Aponte o mentiroso! (admin tabs AND the player menu/titles; texts.py). Admin bar: row 1 = brand "Admin" (far left),
    #   the three games, session controls (right), "Ver o site"; row 2 = Jogo | Conteúdo of the active game (Bingo: Frases do Bingo; Falacioso: Lista de falácias;
    #   Aponte o mentiroso!: only Jogo, with the cards of every check, pending or decided).
    # ONE session: "Iniciar sessão" (header, every admin page) opens a dialog: a collected replay (select) OR any YouTube link (a typed link wins). It starts Bingo,
    #   Falacioso and Aponte o mentiroso! together; the two video games follow the same video (Bingo has none). A game already running is kept. A SOURCE IS REQUIRED
    #   (empty = message): there is no "Bingo alone / no video" start any more. A finished video not collected yet is queued in the replay catalogue and the dialog says so.
    #   While anything runs the header shows the video link and "Finalizar sessão" (asks to confirm), which ends all running games (each game's own winner rules).
    #   No-JS: /admin/session shows the dialog open. Routes: POST /admin/session/start|finish (token A.session_token), GET /admin/session.
    # Per-game start/finish buttons were REMOVED from the pages. The old routes /admin/start, /admin/finish, /admin/falacias/{start,start-url,finish,replays/*} still exist
    #   (tested, nothing links to them); /admin/checker/{start,finish} were deleted.
    # Real Chrome (throwaway DB, replay 'QaReplay001'): dialog, start, header with video + end button at 1280 and 390 px; no sideways scroll; phones: games in the bottom bar.
    #   NOT checked: ending from the header in the browser, a real live link, Safari/Firefox.
    # Slide panel, Quem é Falacioso? (done): ONE text box (name=note, 500 chars). Clicking/selecting lines writes the passage into it (falacias.js putText; untouched text is
    #   replaced by a new selection, edited text is kept and the new passage appended). POST /falacias/mark compares the box with the passage it rebuilds from the offsets:
    #   equal or empty = anchored pair (context + speed bonus); EDITED = typed text (kind 'spoken', no context, <= 280 chars); no fallacy + no selection = message to the host.
    #   A selection without a fallacy is still refused. Tests: tests/test_unified_panel.py (+5). Full suite 1768 passed, 1 skipped.
    #   NOT checked in a browser (box filling on click, extending a selection, phones). Aponte o mentiroso! gets the same panel with FC4 (claim form).

### Validation for "Jogo | Conteúdo start under the selected game" (app/admin_web.py admin_tabs, app/static/app.css "admin shell v2")
    uv run pytest tests/test_admin_shell.py -q          # expect: 49 passed (red run first: the 6 new tests failed, 42 old passed)
    uv run pytest -q                                    # expect: 1873 passed, 1 skipped
    # Desktop (> 760 px): the header row is a CSS grid (1 brand | 2-4 the three games | 5 spacer | 6 session | 7 "Ver o site"); the games nav is a subgrid.
    #   The sub menu is row 2 of the same grid; its links start at the selected game's column, written by the server as style="--sub-col: 2|3|4".
    #   No JavaScript, so no jump on load. Both subgrids use column-gap: normal (a different gap shifts subgrid items by half the difference: 1 px).
    #   Three games are hard-coded in the CSS (repeat(3, auto), 2 / 5): a 4th game must change both.
    # Phones (<= 760 px): unchanged; the games are in the bottom bar and Jogo | Conteúdo is a full-width line at the top.
    ADMIN_PASSWORD=qa-admin-1 REPLAY_COLLECTOR=off uv run uvicorn app.main:app --port 8001     # then /admin, /admin/phrases, /admin/falacias, /admin/fallacies, /admin/checker
    # expect: the left edge of "Jogo" = the left edge of the highlighted game tab; the game tabs do not move between pages.
    # Real Chrome (me, dev DB, read-only pages): left edges equal (sub-pixel) on all 5 pages at 1280 and 900 px (1 px off before the gap fix; also checked 1000/800),
    #   no sideways scroll; 390 px phone layout as before. NOT checked: the header WITH a running session (video link + "Finalizar sessão" in column 6), Safari/Firefox.

