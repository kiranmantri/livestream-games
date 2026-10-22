# Backlog

Open items, newest first. Remove an item when done.

## Due 2026-10-25 (5 days after the first Fly.io deploy, once things have settled)
- Delete the temporary local safety backup: `rm data/backup/livegames_*.dump` (full pg_dump of the dev DB,
  contains player names/IPs). Also delete `data/backup/fly_admin_password.txt` AFTER the admin password has been
  stored in a password manager (rotate it any time with `fly secrets set ADMIN_PASSWORD=...`).

## UX / bugs found by the adversarial test (2026-10-22, local dev; not browser-verified on touch, Safari, Firefox)
Fixed already (see AGENTS.md "Validation for the adversarial-test fixes"): non-Latin-1 names gave HTTP 500; rapid taps could leave the
board out of sync with the server; a lost cookie locked the player out of their own name (now: personal recovery link).

Bugs
- Chat shows raw `:face-green-smiling:` shortcodes (YouTube standard emoji): needs a shortcode -> emoji map, in the replay import and the chat worker.
- IP limit (MAX_USERS_PER_IP) is still an env variable; making it editable in the admin needs a DB setting (migration) - not done.
- Checked and NOT bugs: the "Início" vs "Entrou" one-hour gap (the session was started an hour before my players joined; every timestamp
  uses the same formatter on timestamptz columns), and the admin replay URL (the admin shows the SESSION's video, the entry page used to show the
  default replay; the entry page no longer shows one). The "unnamed buttons" on /admin/falacias were elements inside the closed replay <details>.

Phone layout (Bingo)
- Not done / not checked: touch devices, Safari and Firefox; the Falácias game page on phones (panes, picker) was not part of this pass.

Admin (left over after group 3)
- Phrases and fallacies lists still scroll sideways on phones (the users, leaderboard and replay tables are cards now); sort links are hidden on phones.
- The open player cards on the Bingo admin are not auto-refreshed (only the users table and the count are): an open card would collapse.
- Keyboard shortcuts ignore extra key presses while a verdict is still on its way (on purpose: never double-send).
- Not checked: touch devices, Safari, Firefox; the "Encerrar partida" confirm dialog.

## Falácias
- Live speech-to-text (auto-transcription) of the horizontal live stream. Until then spoken passages are TYPED by the players
  (max 300 chars, no context for the admin). Needs an audio source (yt-dlp audio of the live), an STT engine (cost/latency/pt-BR quality
  to be measured) and a spoiler-free delay policy. The replay transcript feed (Task 2) stays for tests/demos.

## Production (Fly.io)
- Scheduled Postgres backup: Fly keeps only 5 days of volume snapshots and this is one Machine / one volume.
  Add a periodic `pg_dump` to off-machine storage, and rehearse a restore once.
- The cloud DB now holds real games and admin edits (it is the source of truth, not `deploy/seed/phrases_seed.sql`).
  Never run `TRUNCATE` on it casually; remove only your own test rows.
- Consider `fly ips allocate-v4` (dedicated IPv4) only if the shared IPv4 causes trouble.
