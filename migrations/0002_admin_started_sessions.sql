-- A session can now also be started by the admin (from /admin), with no player attached.
-- created_by IS NULL  <=>  started by the admin. When set, it still must reference a real user
-- (the deferred FK from 0001 is unchanged: NULL passes it, any other value is checked).
ALTER TABLE game_sessions ALTER COLUMN created_by DROP NOT NULL;
