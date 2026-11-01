# Backlog

Open items, newest first. Remove an item when done.

## On hold (decided 2026-10-26: keep, not now)
- Delete `data/backup/fly_admin_password.txt` AFTER the admin password has been stored in a password manager
  (rotate it any time with `fly secrets set ADMIN_PASSWORD=...`). The local dev dumps and old cloud backups are already deleted.
- "Verdade ou Lorota" in the cloud with real players: a whole game with claims sent from the page, and the real cost of a ~3 h game
  (so far only the worker and direct checker calls were run on Fly; ~11-22k input tokens and 45-106 s per claim).
- Real-device QA: touch phones, Safari, Firefox on every page; the Falaciômetro and Verdade ou Lorota game pages on phones (panes, slide panel);
  the admin header while a session runs (video link + "Finalizar sessão").

## UX / bugs found by the adversarial test (2026-10-22, local dev; not browser-verified on touch, Safari, Firefox)
Fixed already (see docs/validation/ui-admin.md "Validation for the adversarial-test fixes"): non-Latin-1 names gave HTTP 500; rapid taps could leave the
board out of sync with the server; a lost cookie locked the player out of their own name (now: personal recovery link).

Bugs
- Checked and NOT bugs: the "Início" vs "Entrou" one-hour gap (the session was started an hour before my players joined; every timestamp
  uses the same formatter on timestamptz columns), and the admin replay URL (the admin shows the SESSION's video, the entry page used to show the
  default replay; the entry page no longer shows one). The "unnamed buttons" on /admin/falacias were elements inside the closed replay <details>.

Phone layout (Bingo)
- Not done / not checked: touch devices, Safari and Firefox; the Falácias game page on phones (panes, picker) was not part of this pass.

Admin (left over after group 3)
- (on hold) Phrases and fallacies lists still scroll sideways on phones (the users, leaderboard and replay tables are cards now); sort links are hidden on phones.
- Keyboard shortcuts ignore extra key presses while a verdict is still on its way (on purpose: never double-send).
- Not checked: touch devices, Safari, Firefox; the "Encerrar partida" confirm dialog.

## Falácias
- Live speech-to-text (auto-transcription) of the horizontal live stream. Until then spoken passages are TYPED by the players
  (max 300 chars, no context for the admin). Needs an audio source (yt-dlp audio of the live), an STT engine (cost/latency/pt-BR quality
  to be measured) and a spoiler-free delay policy. The replay transcript feed (Task 2) stays for tests/demos.

## Verdade ou Lorota
- Web search is the free DuckDuckGo (ddgs): bursts of searches get "no results" or DDGSException (seen in the stance evaluation). Failed checks are
  retried by the worker (3 runs, then the admin queue). If real games show it, switch to a paid search API (e.g. Brave Search / Tavily; compare price).

## Production (Fly.io)
- Scheduled Postgres backup: Fly keeps only 5 days of volume snapshots and this is one Machine / one volume.
  Add a periodic `pg_dump` to off-machine storage, and rehearse a restore once.
- The cloud DB now holds real games and admin edits (it is the source of truth, not `deploy/seed/phrases_seed.sql`).
  Never run `TRUNCATE` on it casually; remove only your own test rows.
- Parked (2026-10-26, not needed now): `fly ips allocate-v4` (dedicated IPv4), only if the shared IPv4 causes trouble.
