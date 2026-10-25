-- 0013_checker: the third game, "Quem tá metendo o loco?" (fact checking). Players GUESS a score (-5 lie ... +5 truth) for a claim they pick
-- from the live; an AI agent (and, when it is unsure, the admin) gives the real score. Claim text is shared inside a session: ONE check per
-- distinct text, many players' claims point at it.

ALTER TABLE game_sessions DROP CONSTRAINT game_sessions_game_type_check;
ALTER TABLE game_sessions ADD CONSTRAINT game_sessions_game_type_check CHECK (game_type IN ('bingo', 'falacias', 'checker'));
-- a checker session follows a video (live or replay) exactly like a falacias one
ALTER TABLE game_sessions DROP CONSTRAINT session_source_falacias_only;
ALTER TABLE game_sessions ADD CONSTRAINT session_source_video_games_only CHECK (source_mode IS NULL OR game_type IN ('falacias', 'checker'));

CREATE TABLE fact_checks (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    uuid        NOT NULL REFERENCES game_sessions (id) ON DELETE CASCADE,
    claim_key     text        NOT NULL CHECK (char_length(claim_key) BETWEEN 1 AND 500),     -- casefolded claim text: the dedup key
    claim_text    text        NOT NULL CHECK (char_length(claim_text) BETWEEN 1 AND 500),
    -- queued -> running -> done (confident AI result) | needs_admin (low confidence, unverifiable, or attempts exhausted) -> resolved (admin acted)
    status        text        NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'needs_admin', 'resolved')),
    attempts      smallint    NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    verdict       text        CHECK (verdict IN ('true', 'partial', 'false', 'unverifiable')),
    ai_score      smallint    CHECK (ai_score BETWEEN -5 AND 5),
    confidence    text        CHECK (confidence IN ('low', 'medium', 'high')),
    explanation   text        NOT NULL DEFAULT '' CHECK (char_length(explanation) <= 1000),
    sources       jsonb       NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(sources) = 'array' AND jsonb_array_length(sources) <= 3),
    admin_score   smallint    CHECK (admin_score BETWEEN -5 AND 5),                           -- NULL on a resolved check = dismissed, nobody scores
    error         text        CHECK (char_length(error) <= 500),
    model         text,
    input_tokens  integer     CHECK (input_tokens >= 0),
    output_tokens integer     CHECK (output_tokens >= 0),
    elapsed_ms    integer     CHECK (elapsed_ms >= 0),
    created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    started_at    timestamptz,
    finished_at   timestamptz,
    resolved_at   timestamptz,
    CONSTRAINT fact_check_unique_text UNIQUE (session_id, claim_key),
    CONSTRAINT fact_check_id_session UNIQUE (id, session_id),
    CONSTRAINT fact_check_result_pair CHECK ((verdict IS NULL) = (ai_score IS NULL)),
    -- the sign of the score must agree with the verdict (the agent's validator says the same; the database is the last line)
    CONSTRAINT fact_check_verdict_score CHECK (verdict IS NULL OR CASE verdict
        WHEN 'true' THEN ai_score BETWEEN 1 AND 5
        WHEN 'false' THEN ai_score BETWEEN -5 AND -1
        WHEN 'partial' THEN ai_score BETWEEN -2 AND 2
        ELSE ai_score = 0 END),
    CONSTRAINT fact_check_done_has_result CHECK (status <> 'done' OR (verdict IS NOT NULL AND verdict <> 'unverifiable' AND confidence IS NOT NULL)),
    CONSTRAINT fact_check_resolved_time CHECK ((status = 'resolved') = (resolved_at IS NOT NULL)),
    CONSTRAINT fact_check_admin_score_only_resolved CHECK (admin_score IS NULL OR status = 'resolved')
);
CREATE INDEX fact_checks_queue_idx ON fact_checks (created_at) WHERE status = 'queued';
CREATE INDEX fact_checks_session_idx ON fact_checks (session_id, status, created_at);

CREATE TABLE fact_claims (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     uuid        NOT NULL,
    user_id        uuid        NOT NULL,
    check_id       uuid        NOT NULL,
    kind           text        NOT NULL CHECK (kind IN ('transcript', 'chat', 'spoken')),
    start_event_id bigint      NOT NULL,
    start_offset   integer     NOT NULL CHECK (start_offset >= 0),
    end_event_id   bigint      NOT NULL,
    end_offset     integer     NOT NULL CHECK (end_offset >= 1),
    quote          text        NOT NULL CHECK (char_length(quote) BETWEEN 1 AND 500),      -- recomputed by the SERVER
    guess          smallint    NOT NULL CHECK (guess BETWEEN -5 AND 5),
    note           text        NOT NULL DEFAULT '' CHECK (char_length(note) <= 280),
    created_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT fact_claim_user_in_session FOREIGN KEY (user_id, session_id) REFERENCES users (id, session_id) ON DELETE CASCADE,
    CONSTRAINT fact_claim_check FOREIGN KEY (check_id, session_id) REFERENCES fact_checks (id, session_id) ON DELETE CASCADE,
    CONSTRAINT fact_claim_start_event FOREIGN KEY (start_event_id, session_id, kind) REFERENCES stream_events (id, session_id, kind),
    CONSTRAINT fact_claim_end_event FOREIGN KEY (end_event_id, session_id, kind) REFERENCES stream_events (id, session_id, kind),
    CONSTRAINT fact_claim_selection_order CHECK (start_event_id < end_event_id
                                                 OR (start_event_id = end_event_id AND start_offset < end_offset)),
    CONSTRAINT fact_claim_spoken_shape CHECK (kind <> 'spoken'
                                              OR (char_length(quote) <= 300 AND start_event_id = end_event_id AND start_offset = 0)),
    CONSTRAINT fact_claim_one_per_player_and_text UNIQUE (user_id, check_id)
);
CREATE INDEX fact_claims_user_idx ON fact_claims (user_id, created_at);
CREATE INDEX fact_claims_session_idx ON fact_claims (session_id, created_at);
CREATE INDEX fact_claims_check_idx ON fact_claims (check_id, created_at);
