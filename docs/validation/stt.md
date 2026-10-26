# Validation: Live speech-to-text (STT)

Speech-to-text worker for live streams: whisper.cpp (unused), hosted Groq/xAI (default Groq), diagnosis.
Split out of AGENTS.md (see its "Validation docs" index). Blocks are in the order they were written;
later blocks supersede earlier ones where they disagree. Expected test counts are as of when each block was written.

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
    #   (ONLY while the game runs since the "source link only while running" change, see ui-admin.md). "Ninguem pontuou desta vez." was removed (admin and player end screens). The entry page of Falacias no longer says
    #   "O canal nao esta ao vivo agora." / "So transmissoes na horizontal sao usadas.".

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
