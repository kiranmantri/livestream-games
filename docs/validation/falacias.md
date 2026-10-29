# Validation: Quem é Falacioso? (Falácias)

Fallacy game: schema, replay feed, live chat worker, replay catalogue/collector, start dialog, slide panel, admin.
Split out of AGENTS.md (see its "Validation docs" index). Blocks are in the order they were written;
later blocks supersede earlier ones where they disagree. Expected test counts are as of when each block was written.

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

### Validation for the three-panel Falácias live page (app/falacias_web.py game_view/video_pane, falacias.js, app.css .panes.three)
    uv run pytest tests/test_three_panels.py -q         # expect: 6 passed (red run first)
    uv run pytest -q                                    # expect: 1345 passed, 1 skipped
    # LIVE game: video | chat | transcript side by side (1.25fr 1fr 1fr). The video panel's title "Transmissão ao vivo" has "Ocultar vídeo" on its right;
    #   clicking it unloads the iframe (sound stops), hides the panel and the other two share the width 50/50; a "Mostrar vídeo" button appears above them.
    #   The transcript panel is always there in a live game (with STT off it only holds the "no transcript" note). The old collapsible box above the panes is gone.
    #   Replay games are unchanged (transcript | chat, no video). Phones (<= 760 px): one column, video first.
    # NOT checked in a real browser (widths, iframe size in a 1/3 column, the hide/show script, phones).

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

## /falacias: tabs "Minhas frases | Outros participantes" during the game

`#side` (polled every 5 s, also returned after a send) now holds the tablist (`ui.result_tabs`, `tabs.js`): my pairs, and every other player's pairs
with the player's name (`F.session_marks`, `falacias_web.pair_tabs`; the end screen uses the same function). "Meus envios" (FAL_MINE) is gone.
`uv run pytest -q` -> 1930 passed, 1 skipped. By hand: two browsers join /falacias, each sends a pair; "Outros participantes" shows the other's pair
with its name; the tab survives the 5 s poll. Not checked in a real browser.
