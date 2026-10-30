-- Bingo: the admin reviews the marks. A confirmed mark is locked (the player can no longer unmark it); an incorrect one is
-- simply deleted (the player may mark the cell again later). A kicked player's user row (card, marks) is deleted; the
-- hash of their cookie is kept here so the join screen can tell them why, and they may join again with a new card.
ALTER TABLE bingo_marks ADD COLUMN confirmed_at timestamptz;

CREATE TABLE kicked_players (
    session_id uuid        NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    token_hash text        NOT NULL,
    name       varchar(20) NOT NULL,
    kicked_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, token_hash)
);
