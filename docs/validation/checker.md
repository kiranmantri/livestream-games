# Validation: Aponte o mentiroso! (fact-checker)

Fact-checking game: spike FC0, schema FC1, agent FC2, worker FC3, player FC4, admin FC5. Plan: docs/fact-checker-plan.md.
Split out of AGENTS.md (see its "Validation docs" index). Blocks are in the order they were written;
later blocks supersede earlier ones where they disagree. Expected test counts are as of when each block was written.

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

### Validation for FC5 + the player's tab (admin page /admin/checker: app/checker_admin_web.py; player tab: app/checker_web.py)
    uv run pytest tests/test_checker_admin_web.py -q   # expect: 58 passed (red run first: 55 failed / 3 passed)
    uv run pytest -q                                   # expect: 1742 passed, 1 skipped (3 tests in test_refresh.py now count 3 player tabs / 5 admin tabs)
    uv run migrate                                     # YOU, dev DB: 0012 + 0013 (the checker tables)
    uv run uvicorn app.main:app --port 8001            # then http://127.0.0.1:8001/admin/checker (user admin, ADMIN_PASSWORD)
    # Admin: 3rd tab in "Partidas". "Iniciar nova partida" opens the dialog: empty link = game without video (scores by hand work the same); a horizontal live link
    #   follows the live; a finished video needs its replay already collected (do it on the Falacias tab; this page does NOT queue collections).
    #   Panel: "Verificacoes" (pending = queued / running / waiting for you; "Mostrar todas" lists decided ones too) and the board. Each check shows the claim, status,
    #   the guesses ("Ana: -4"), the AI answer (verdict, score, confidence, explanation, sources opening in a new tab) and, while the game runs, "Definir nota" (-5..5)
    #   and "Ninguem pontua". A score replaces the AI's even when it was confident; points are computed on read, so the board follows at once.
    #   The panel polls every 3 s with a signature and answers 204 while nothing changed. NOTE: when it DOES change it is redrawn whole, which drops a half-typed score.
    #   The page says whether the automatic check is on ("Verificacao automatica ligada: <model>") or why not (CHECKER_MODEL unset ...).
    # Player: 3rd tab "Quem ta metendo o loco?" (/checker) on every page, name gate first. It shows "Nenhuma partida agora" or "Tem uma partida rolando!" (players, video link)
    #   and follows the game every 10 s. There is NO join / claim form yet (FC4). The personal link (/r) still covers only Bingo and Falacias.
    # Hand check without a model: start a game here, run the snippet of the FC3 block to insert a claim, score it in the page, then "Encerrar partida".
    # Mutation check: not run. NOT checked: any of it in a real browser (htmx swaps, dialog, phones: the 3rd player tab label is long for the bottom bar).

### Validation for FC4 (player side of Aponte o mentiroso!: app/checker_web.py, feed mirror in app/feed.py)
    uv run pytest tests/test_checker_play.py -q        # expect: 43 passed (the first 38 were written before the code but not run red; the 5 card tests were run red first). Full suite: uv run pytest -q -> 1811 passed, 1 skipped
    # /checker: no game = "Nenhuma partida agora"; a game running = status + "Entrar na partida" (POST /checker/join, cookie lg_token_checker, same name as the other games).
    #   A player NEVER starts a game here (the admin's session does). Joined: video | chat | transcript (live; replay = transcript | chat, no video), the slide panel of
    #   Falacioso (same falacias.js: a click writes the passage into ONE text box) + a rating -5..+5 (radio buttons), "Minhas frases" and the leaderboard (polled every 5 s).
    #   POST /checker/mark: rating required; text box untouched/empty with a selection = anchored claim (kind chat|transcript); edited or no selection = typed claim (kind spoken,
    #   <= 280 chars). Errors in pt-BR (T.CK_ERRORS). After a claim checkworker.wake() runs. "My claims": Verificando... / Aguardando o apresentador / Descartada, then the verdict,
    #   explanation, sources (http/https only, rel=noopener noreferrer), my guess, the final score (admin's replaces the AI's) and points. End screen: winner, table, my claims.
    # Claim cards: "Minhas frases" (and the end screen) draw each claim as a small <details class="ckcard" data-id=claim id>. COLLAPSED: the phrase (2 lines max),
    #   state/verdict badge, my guess icons -> final icons, points. EXPANDED: guess/final with numbers, explanation, sources. app/static/checker.js (~15 lines) keeps cards
    #   open across the 5 s poll and after a send (htmx:beforeSwap/afterSwap). Tests: 5 new (red first). Real Chrome: a card opened by hand stayed open after a poll. Not checked on a phone.
    # Score line + modal (BOTH video games, app/scorebar.py, board.js): the "Placar" panel is gone from the player page. Under the title: "Kiran (você) · 5 pontos · Placar atual"
    #   (re-read every 5 s from /<game>/score). The link opens a modal (<dialog id=scoredlg>) whose body is loaded from /<game>/board (always fresh; the dialog lives outside the polled
    #   element, so the redraw never closes it); without JS the link opens /<game>/board as a page. Falacioso shows points as "5,0 pontos", Aponte o mentiroso! as "7 pontos".
    #   The end screens keep their ranking table. "My pairs" / "My claims" are now small cards in a 3-column grid (2 under 1000 px, 1 under 760 px) in BOTH games (ol.pairs in app.css).
    #   Tests: tests/test_scorebar.py (11, red first); 4 old tests that read the leaderboard from /<game>/side now read /<game>/board. Full suite 1822 passed, 1 skipped.
    #   Real Chrome (throwaway DB, checker): bar, modal with 2 players, 3-column cards, an expanded card does not stretch its neighbours. NOT checked: the Falacioso page in a browser, phones.
    # SHARED FEED (important): a LIVE checker session has no chat/STT worker of its own. feed.tick mirrors the new lines of the active Falácias session with the SAME video
    #   (feed._mirror_live, same source_key) and starts the workers on THAT session: one chat poll and one Groq bill per live. Chat health = the sibling's. No sibling = nothing
    #   arrives and the page says the feed is down (the global session always starts both). A replay checker session releases the replay lines on its own clock.
    #   Also: falacias.js now tolerates a panel without the fallacy filter and closes the panel on the `ck:sent` event.
    # Real Chrome (throwaway DB, replay session): join, click a chat line -> text appears in the box, rating -4, send -> "Verificando...", then the AI answer (set by a script)
    #   shows verdict, explanation, 2 sources, 7 pts; phone width 390 px: no sideways scroll. A selected claim is refused while the feed is "down" (no replay rows): correct.
    #   NOT checked: a real live (video panel + mirrored chat), the real model through the page, the panel on a phone, Safari/Firefox. No mutation check yet.

### Validation for the /admin/checker table (app/checker_admin_web.py, app/static/ckadmin.js)
    uv run pytest tests/test_checker_admin_table.py tests/test_checker_admin_search.py -q   # expect: 19 + 14 passed (red run first for each feature)
    uv run pytest -q                                   # expect: all passed
    uv run uvicorn app.main:app --port 8001            # http://127.0.0.1:8001/admin/checker (user admin, ADMIN_PASSWORD), with a checker game and a claim
    # Columns: Jogador | Alegacao | Pontos | Verificacao da IA | Acoes. One <tbody> per check, one row per player; claim, AI check and actions span the rows.
    #   Pontos = 3 one-line rows: "Palpite: / IA: / Host:" as poop (negative) or stars (positive), 0 = neutral face, none = dash. Click "Host" (or the Editar button)
    #   -> a palette of -5..+5 opens (position: fixed, so the scrolling table never clips it); a click saves via htmx and swaps ONLY that tbody. Esc / outside click closes.
    #   Alegacao and the AI check are folded to one line (arrow on the same line); the open ones stay open across the 3 s poll.
    # Search box (right) is outside the polled panel; it filters on the server (case/accent-insensitive: player, claim, explanation, verdict, state). Header links sort
    #   (player, claim, points = host score else AI score, check); empty values always last. "Mostrar so pendentes" (left) keeps the search text.
    # "Verificar" (Acoes) shows only when the AI call failed (needs_admin WITH an error): POST /admin/checker/recheck re-queues it (runs reset, error cleared) and wakes the worker.
    # Verdict labels are feminine: Verdadeira / Falsa / Parcial / Nao verificavel.
    # Out-of-band pieces of a <tbody> reply sit in a <template>, else the browser pulls the board's own table into the page (seen with the score field).
    # Real Chrome (throwaway DB): Editar opens the palette (it was clipped by overflow before), a click saved +3 and showed 3 stars. NOT checked: phones, the Verificar button in a browser,
    #   "Confianca alta" wrapping with a real AI result, Safari/Firefox. No mutation check.

### Validation for FC7 prep (image, compose, two worker bugs found by a real smoke run) - deploy itself NOT done yet
    uv run pytest tests/test_factcheck_loops.py tests/test_app_logging.py -q   # expect: 6 passed (red run first: 3 failed / 1 passed, then 2 failed)
    uv run pytest -q                                    # expect: 1867 passed, 1 skipped
    # Bug 1 (blocker): the worker builds the checker ONCE and runs each check in a NEW event loop (asyncio.run). The model's HTTP client stayed bound to
    #   the first loop: check 2 of the same checker failed "erro inesperado (RuntimeError)" = "Event loop is closed" (3 runs -> admin queue). Fix: from_env
    #   gives PydanticChecker a model_factory; every check builds its own model + AsyncOpenAI client and closes it in that loop. Groq now also gets its own
    #   AsyncOpenAI client (pydantic-ai's OpenAIProvider(base_url=...) shares one cached httpx client per process). Tests: a local OpenAI-compatible server
    #   with keep-alive, 3 checks in a row and 2 threads x 3 checks, for groq and openrouter.
    # Bug 2: nothing configured logging, so under uvicorn only WARNING+ reached the log ("fact-checker on", "stt recovered", "requeued" never showed).
    #   app/main.py gives the "app" logger an INFO stderr handler. Expect now at boot: "INFO app.checkworker: fact-checker on: <model>, 2 worker(s)".
    # docker-compose.yml passes CHECKER_MODEL and OPENROUTER_API_KEY to the app (runtime only). tests/test_phone_ui.py tag test: the phrase gets score 99
    #   (it is a rendering test; a low-scored phrase could miss all 40 dealt cards and failed 2 of 2 full runs).
    # Real checks (me): `docker build` OK (python 3.14.9, pydantic-ai 2.61.0, image 265 MB; imports fine with --read-only); compose smoke in project lgsmoke
    #   (applied 0001..0013, seeds, healthy); DuckDuckGo from the container: 6 results. Real model via the worker (OpenRouter gpt-oss-120b, keys from .env):
    #   "A Terra tem 6 mil anos" false -5 (3 sources), "O Brasil tem 26 estados e o DF" true +5 (2), "A Grande Muralha ... visivel da Lua" false -3 (3);
    #   45-106 s each, 11-16k input tokens; no failures. Reset: COMPOSE_PROJECT_NAME=lgsmoke docker compose -f docker-compose.yml down -v
    # OPEN (product decision): "os Annunakis existem" -> unverifiable / 0 in 3 of 3 runs (once without searching at all). The prompts treat beliefs as
    #   uncheckable ("Deus existe"); the model files mythological beings there. FC0 expected false. Tune RESEARCH/ANSWER_INSTRUCTIONS only if you want that.
    # NOT checked: the cloud deploy (blocked: an active Falacias game, OPENROUTER_API_KEY not set on Fly, migration 0013 => backup first), DuckDuckGo from Fly's IP.
