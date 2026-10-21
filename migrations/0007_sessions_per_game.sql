-- 0007_sessions_per_game: one active session PER GAME (bingo, falacias) instead of one active session overall.
-- A player is still a row of `users` in ONE session; a person who plays both games has two rows (one per session) and
-- two cookies (one per game), so users.token_hash stays globally unique and nothing about identity changes for Bingo.

ALTER TABLE game_sessions DROP CONSTRAINT game_sessions_game_type_check;
ALTER TABLE game_sessions ADD CONSTRAINT game_sessions_game_type_check CHECK (game_type IN ('bingo', 'falacias'));

DROP INDEX one_active_session;
CREATE UNIQUE INDEX one_active_session_per_game ON game_sessions (game_type) WHERE status = 'active';
