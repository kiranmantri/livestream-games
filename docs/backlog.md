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
- Entry page offers "Entrar" for a name already taken in the session; the error only appears after tapping. Pre-check on the entry page.
- "trocar nome" field is prefilled but not selected (typing appends to the old name). Select it on focus.
- 4th person behind one IP (MAX_USERS_PER_IP=3) gets a dead-end message and the URL stays on the POST route (/join). Redirect after POST,
  tell them to ask the presenter, make the limit editable in the admin.
- No throttle on wrong admin passwords (30 attempts in ~10 ms). Add a delay/lockout per IP (security as much as UX).
- Admin Bingo: "Início" shows 1 h earlier than "Entrou"/"Visto por último" (looks like a timezone bug, code not checked).
- Admin Falácias replay line showed `7Y89JM-wDk4` (no title) while the player page showed `_nVWPIFCUzQ` (with title): unexplained, investigate.
- Bare "404 Not Found" page; "1 jogador(es)" wording; raw `:face-green-smiling:` shortcodes in the chat pane.
- Page title/heading says "Bingo da Live" before a game is chosen (name gate).
- /admin/fallacies: category and description inputs have no labels; /admin/falacias has unnamed links/buttons for assistive tech.

Phone layout (Bingo, 390 px)
- The player's own board starts a full screen down (y=841 px), behind the empty video and chat boxes. Show the board first with a sticky
  "Marcadas n/16" bar; collapse video+chat into one line by default; hide empty boxes.
- Cell text is 7.7-9.2 px (aim >= 13 px); move the repeated "(falácia)" into a small tag.
- Tap targets under 44 px: "trocar nome" 17, "Ver o canal" 19, view toggles 28, "Ocultar vídeo e chat" 29.
- Each tap re-renders the whole board over the network; swap only the tapped cell and give instant feedback.

Admin
- Navigation mixes game admin and content lists: group as "Partidas: Bingo | Falácias" and "Conteúdo: Frases | Lista de falácias";
  "Falácias" vs "Encontre as Falácias" is confusing. Sticky bar with game state, player count, elapsed time; "Encerrar partida" alone at the right.
- No auto-refresh on the Bingo admin and "Atualizar" is below the fold: refresh tables every 5 s with "atualizado há 3 s".
- "Desmarcar" is 9 px text on a 14 px button inside the green cell; Edit/Excluir on lists are 24 px. Put it in the player's row with a confirm.
- Falácias queue: finished items stay forever and bury the pending ones; add "Apenas pendentes" (default), bigger Correto/Errado/Spam buttons,
  C/E/S keyboard shortcuts, "há 12 s" per item. Use the phone card layout on every admin table (as done for replays).
- Not tested: the "Encerrar partida" confirm dialog, /admin/phrases beyond accessibility numbers.

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
