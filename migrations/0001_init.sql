-- 0001_init: core schema for Live Games (sessions, users, phrases, bingo cards/marks)
-- Conventions: uuid PKs (gen_random_uuid, built in since PG13); timestamptz everywhere.

-- ---------------------------------------------------------------- game_sessions
-- created_by / winner_user_id reference users, and users reference game_sessions:
-- a circular dependency. FKs to users are added after both tables exist and are
-- DEFERRABLE INITIALLY DEFERRED so the app can insert session + first user in ONE transaction
-- (the app generates both UUIDs up front).
CREATE TABLE game_sessions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    game_type      text        NOT NULL DEFAULT 'bingo' CHECK (game_type IN ('bingo')),
    status         text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
    created_by     uuid        NOT NULL,
    winner_user_id uuid,
    created_at     timestamptz NOT NULL DEFAULT now(),
    ended_at       timestamptz,
    -- an active session has no end time and no winner; an ended one has an end time
    CONSTRAINT session_end_consistency CHECK (
        (status = 'active' AND ended_at IS NULL AND winner_user_id IS NULL)
        OR (status = 'ended' AND ended_at IS NOT NULL)
    )
);

-- DB-enforced rule: only ONE active session at a time (across all game types).
CREATE UNIQUE INDEX one_active_session ON game_sessions (status) WHERE status = 'active';

-- ---------------------------------------------------------------- users (a player in ONE session)
CREATE TABLE users (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   uuid         NOT NULL REFERENCES game_sessions (id) ON DELETE CASCADE,
    name         varchar(20)  NOT NULL CHECK (char_length(btrim(name)) >= 1),
    -- case-insensitive comparison key of the display name (see docs/design.md)
    name_ci      text GENERATED ALWAYS AS (lower(btrim(name))) STORED,
    ip           inet         NOT NULL,
    -- sha256 hex of the random cookie token; the raw token only lives in the browser cookie
    token_hash   text         NOT NULL UNIQUE,
    created_at   timestamptz  NOT NULL DEFAULT now(),
    last_seen_at timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT users_name_unique_per_session UNIQUE (session_id, name_ci),
    -- lets child tables prove "this user belongs to this session" with a composite FK
    CONSTRAINT users_id_session_unique UNIQUE (id, session_id)
);
CREATE INDEX users_session_ip_idx ON users (session_id, ip);

ALTER TABLE game_sessions
    ADD CONSTRAINT game_sessions_created_by_fk
        FOREIGN KEY (created_by) REFERENCES users (id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT game_sessions_winner_fk
        FOREIGN KEY (winner_user_id) REFERENCES users (id) DEFERRABLE INITIALLY DEFERRED;

-- ---------------------------------------------------------------- phrases (the corpus)
CREATE TABLE phrases (
    id         bigserial PRIMARY KEY,
    text       text        NOT NULL UNIQUE,
    source     text        NOT NULL
               CHECK (source IN ('transcript', 'chat', 'transcript+chat', 'manual')),
    score      real        NOT NULL DEFAULT 0,
    enabled    boolean     NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    -- 3 to 5 words (project rule)
    CONSTRAINT phrase_word_count CHECK (
        array_length(string_to_array(btrim(text), ' '), 1) BETWEEN 3 AND 5
    )
);

-- ---------------------------------------------------------------- bingo_cards
-- One card per user per session, assigned once. cells = JSON array of 25 items (row-major 5x5);
-- index 12 (centre) = {"type":"image"}, others = {"phrase_id": n, "text": "..."}.
-- The phrase text is snapshotted so later corpus edits never change an issued card.
CREATE TABLE bingo_cards (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid        NOT NULL,
    user_id    uuid        NOT NULL,
    cells      jsonb       NOT NULL
               CHECK (jsonb_typeof(cells) = 'array' AND jsonb_array_length(cells) = 25),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT card_one_per_user_session UNIQUE (session_id, user_id),
    CONSTRAINT card_id_unique_for_marks UNIQUE (id),
    -- the user must belong to that very session
    CONSTRAINT card_user_in_session FOREIGN KEY (user_id, session_id)
        REFERENCES users (id, session_id) ON DELETE CASCADE
);

-- "Once assigned the user can't change": cards are immutable.
CREATE FUNCTION bingo_cards_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'bingo_cards rows are immutable once assigned';
END $$;
CREATE TRIGGER bingo_cards_no_update BEFORE UPDATE ON bingo_cards
    FOR EACH ROW EXECUTE FUNCTION bingo_cards_immutable();

-- ---------------------------------------------------------------- bingo_marks
-- Marked cells. The centre (12) is a free cell and is never stored, so a card has at most 24 marks.
-- Score = number of rows for the card (most marks wins when the admin ends the session).
CREATE TABLE bingo_marks (
    card_id    uuid        NOT NULL REFERENCES bingo_cards (id) ON DELETE CASCADE,
    cell_index smallint    NOT NULL CHECK (cell_index BETWEEN 0 AND 24 AND cell_index <> 12),
    marked_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (card_id, cell_index)
);

-- ---------------------------------------------------------------- ingested_videos (corpus provenance)
CREATE TABLE ingested_videos (
    video_id       text PRIMARY KEY,
    title          text        NOT NULL,
    upload_date    date,
    duration_s     integer,
    width          integer,
    height         integer,
    orientation    text        NOT NULL CHECK (orientation IN ('horizontal', 'vertical')),
    has_transcript boolean     NOT NULL,
    has_chat       boolean     NOT NULL,
    ingested_at    timestamptz NOT NULL DEFAULT now()
);
