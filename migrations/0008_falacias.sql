-- 0008_falacias: tables for the game "Encontre as Falácias" (find the fallacies in a transcript + chat feed).
-- Sessions/users are the existing ones (game_type 'falacias', migration 0007). Points are NOT stored: they are derived
-- from (status, latency_ms, counts) by app/falacias.py, so changing a constant never needs a data fix.

-- ---------------------------------------------------------------- the fallacy list (seeded from pt.wikipedia, then admin-edited)
CREATE TABLE fallacies (
    id            smallserial PRIMARY KEY,
    name          text        NOT NULL UNIQUE CHECK (char_length(btrim(name)) BETWEEN 1 AND 120),
    category      text        NOT NULL CHECK (char_length(btrim(category)) >= 1),
    description   text        NOT NULL DEFAULT '',
    wikipedia_url text,
    source        text        NOT NULL CHECK (source IN ('wikipedia', 'manual')),
    enabled       boolean     NOT NULL DEFAULT true
);

-- ---------------------------------------------------------------- replay source (cleaned text rows of a recorded video)
CREATE TABLE replay_events (
    id        bigserial PRIMARY KEY,
    video_id  text   NOT NULL,
    kind      text   NOT NULL CHECK (kind IN ('transcript', 'chat')),
    offset_ms integer NOT NULL CHECK (offset_ms >= 0),
    author    text,
    text      text   NOT NULL CHECK (char_length(btrim(text)) >= 1)
);
CREATE UNIQUE INDEX replay_events_unique ON replay_events (video_id, kind, offset_ms, md5(text));
CREATE INDEX replay_events_clock ON replay_events (video_id, offset_ms);

-- ---------------------------------------------------------------- what a session has shown in its two panes
CREATE TABLE stream_events (
    id         bigserial PRIMARY KEY,
    session_id uuid        NOT NULL REFERENCES game_sessions (id) ON DELETE CASCADE,
    kind       text        NOT NULL CHECK (kind IN ('transcript', 'chat')),
    source_key text        NOT NULL,                          -- id in the source (replay row id): feeding twice is a no-op
    author     text,
    text       text        NOT NULL CHECK (char_length(btrim(text)) >= 1),
    fed_at     timestamptz NOT NULL DEFAULT now(),            -- when the line reached the panes (the latency clock)
    CONSTRAINT stream_events_source_unique UNIQUE (session_id, kind, source_key),
    -- lets fallacy_marks prove "this event belongs to this session AND to this pane"
    CONSTRAINT stream_events_id_session_kind UNIQUE (id, session_id, kind)
);
CREATE INDEX stream_events_session_idx ON stream_events (session_id, id);

-- ---------------------------------------------------------------- pairs: (selected passage, fallacy) by a player
CREATE TABLE fallacy_marks (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     uuid        NOT NULL,
    user_id        uuid        NOT NULL,
    kind           text        NOT NULL CHECK (kind IN ('transcript', 'chat')),
    start_event_id bigint      NOT NULL,
    start_offset   integer     NOT NULL CHECK (start_offset >= 0),
    end_event_id   bigint      NOT NULL,
    end_offset     integer     NOT NULL CHECK (end_offset >= 1),
    quote          text        NOT NULL CHECK (char_length(quote) BETWEEN 1 AND 500),   -- recomputed by the SERVER
    fallacy_id     smallint    NOT NULL REFERENCES fallacies (id),                       -- disable, never delete, a used one
    latency_ms     integer     NOT NULL CHECK (latency_ms >= 0),
    counts         boolean     NOT NULL,                      -- earns the +5 base (first 20 unreviewed pairs of the player)
    status         text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'correct', 'wrong', 'spam')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    reviewed_at    timestamptz,
    CONSTRAINT mark_user_in_session FOREIGN KEY (user_id, session_id) REFERENCES users (id, session_id) ON DELETE CASCADE,
    CONSTRAINT mark_start_event FOREIGN KEY (start_event_id, session_id, kind) REFERENCES stream_events (id, session_id, kind),
    CONSTRAINT mark_end_event FOREIGN KEY (end_event_id, session_id, kind) REFERENCES stream_events (id, session_id, kind),
    CONSTRAINT mark_selection_order CHECK (start_event_id < end_event_id
                                           OR (start_event_id = end_event_id AND start_offset < end_offset)),
    CONSTRAINT mark_review_consistency CHECK ((status = 'pending') = (reviewed_at IS NULL)),
    CONSTRAINT fallacy_marks_unique_pair UNIQUE (user_id, kind, start_event_id, start_offset, end_event_id, end_offset, fallacy_id)
);
CREATE INDEX fallacy_marks_queue_idx ON fallacy_marks (session_id, status, created_at);
CREATE INDEX fallacy_marks_user_idx ON fallacy_marks (user_id, created_at);

-- ---------------------------------------------------------------- short texts from players to the live host (admin relays by hand)
CREATE TABLE host_messages (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid        NOT NULL,
    user_id    uuid        NOT NULL,
    text       text        NOT NULL CHECK (char_length(btrim(text)) BETWEEN 1 AND 280),
    status     text        NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'done', 'hidden')),
    created_at timestamptz NOT NULL DEFAULT now(),
    handled_at timestamptz,
    CONSTRAINT host_message_user_in_session FOREIGN KEY (user_id, session_id) REFERENCES users (id, session_id) ON DELETE CASCADE,
    CONSTRAINT host_message_handled_consistency CHECK ((status = 'new') = (handled_at IS NULL))
);
CREATE INDEX host_messages_queue_idx ON host_messages (session_id, status, created_at);
