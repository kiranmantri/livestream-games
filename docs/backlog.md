# Backlog

Open items, newest first. Remove an item when done.

## Due 2026-10-25 (5 days after the first Fly.io deploy, once things have settled)
- Delete the temporary local safety backup: `rm data/backup/livegames_*.dump` (full pg_dump of the dev DB,
  contains player names/IPs). Also delete `data/backup/fly_admin_password.txt` AFTER the admin password has been
  stored in a password manager (rotate it any time with `fly secrets set ADMIN_PASSWORD=...`).

## Production (Fly.io)
- Scheduled Postgres backup: Fly keeps only 5 days of volume snapshots and this is one Machine / one volume.
  Add a periodic `pg_dump` to off-machine storage, and rehearse a restore once.
- The cloud DB now holds real games and admin edits (it is the source of truth, not `deploy/seed/phrases_seed.sql`).
  Never run `TRUNCATE` on it casually; remove only your own test rows.
- Consider `fly ips allocate-v4` (dedicated IPv4) only if the shared IPv4 causes trouble.
