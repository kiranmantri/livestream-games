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
    docs/         architecture, operations, validation/, diagrams/ (drawio + PNG), backlog
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
  A third game 'checker' (Aponte o mentiroso!, cookie `lg_token_checker`, migration 0013) follows the same rule; the admin's
  ONE global session starts all three together (docs/validation/ui-admin.md, "admin shell v2").
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
- A card is generated randomly on join, persisted, and immutable (no re-roll). The ONLY exception is a card swap
  (app/swaps.py, migration 0016): inside `use_swap` (`SET LOCAL livegames.card_swap = 'on'`) two cards exchange their
  cells; marks, confirmations and blocks move with them. Card rows (ids, owners) stay.
- Secret keywords (Bingo, migration 0016): the admin picks up to 3 phrases from THIS game's dealt cards (`bingo_keywords`);
  only the admin sees them (🔑 on /admin). A keyword is frozen (cannot be removed) once a mark of it is confirmed. Every
  player whose keyword mark gets confirmed earns ONE swap (`swap_rights`, one per player and keyword), usable or declinable
  until the game ends: they pick another player, who cannot refuse, except players whose CURRENT card has that keyword marked
  (pending or confirmed). Incorreto on the earning phrase takes an unused swap back; kicking the player holding the earning
  card removes it too. Players see the offer / the "trocou com você" notice through the 5 s `/myboard` poll
  (board sig `m=;l=;r=;c=<card version>;s=<open swap id>`).
- Card: NEW sessions are 4x4 (16 phrases, no free cell; `game_sessions.grid_size`, migration 0006). Sessions created before it stay
  5x5 (centre index 12 = channel avatar, free cell, counts as marked). Max marks 16 / 24. `ingested_videos` and `corpus/phrases.csv` are gone.
- Marking is honour-based (players mark their own cells; no phrase detection yet), reviewed by the admin on /admin ("Marcações",
  ONE ROW PER PHRASE with everybody who marked it, pending first). Review is per phrase, for all players: Correto stores it in
  `confirmed_phrases` and sets `bingo_marks.confirmed_at` on every mark of it -> those cells are LOCKED; a LATER mark of a
  confirmed phrase is confirmed (locked) at once. Incorreto deletes every mark of the phrase and un-confirms it (NOT blocked:
  players may mark it again later, pending again). Unreviewed marks still count
  at the end. The older "✕ Desmarcar" on the admin's cards still unmarks AND blocks (revoked_marks). The player's page polls
  `GET /myboard?v=<board_sig>` every 5 s, so admin changes show up without a reload (migration 0015).
- The admin can KICK a player of the running Bingo game: the users row is deleted (card, marks cascade; `created_by` cleared
  if it was the starter), the cookie hash goes to `kicked_players` so `/` shows the join screen with a notice; the player may
  join again (same name allowed) with a NEW card. Only that game's row: their Falácias / Checker rows are untouched.
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

### Where the validation blocks live (split by area; append new blocks to the matching file)
Every task's "How to test / validate" block goes into ONE of these files (newest block at the end of the file;
create a new area file only for a genuinely new area and add it to this list):

    docs/validation/deploy.md     Fly.io release CHECKLIST (top), Docker/compose smoke, Fly setup gotchas, secrets scripts, STT on Fly
    docs/validation/bingo.md      Bingo: schema/migrations, sessions + identity, cards, scoring, player + admin pages, phrases CRUD, 4x4
    docs/validation/corpus.md     yt-dlp fetch, phrase extraction, cliches, rescore
    docs/validation/falacias.md   Quem é Falacioso?: schema, replay feed, chat worker, replays R1-R4, start dialog, slide panel, fallacy admin
    docs/validation/stt.md        speech-to-text worker (S1/S2, hosted Groq/xAI, "STT down" diagnosis)
    docs/validation/checker.md    Aponte o mentiroso!: FC0 spike .. FC5, /admin/checker table (plan: docs/fact-checker-plan.md)
    docs/validation/ui-admin.md   shared UI: adversarial fixes, backlog groups 1-3, visual refresh, personal link, admin shell v2

Architecture (diagram, workers, tables, env): docs/architecture.md. Operations (backup, restore, deploy): docs/operations.md.
Open work: docs/backlog.md. Plans: docs/fact-checker-plan.md, .hermes/plans/.

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
