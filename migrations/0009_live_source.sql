-- 0009: where a Falácias session gets its video from (live or replay), and typed ("spoken") pairs.
-- Existing sessions keep NULLs: they used the FALACIAS_VIDEO_ID replay (legacy behaviour, unchanged).

ALTER TABLE game_sessions
    ADD COLUMN video_id    text,
    ADD COLUMN source_mode text,
    ADD CONSTRAINT session_source_mode CHECK (source_mode IN ('live', 'replay')),
    ADD CONSTRAINT session_source_pair CHECK ((video_id IS NULL) = (source_mode IS NULL)),
    ADD CONSTRAINT session_source_falacias_only CHECK (source_mode IS NULL OR game_type = 'falacias'),
    ADD CONSTRAINT session_video_id_format CHECK (video_id IS NULL OR video_id ~ '^[A-Za-z0-9_-]{1,32}$');

-- 'spoken': the player TYPES what was said on the stream. The pair is its own one-line event (never shown in the panes).
ALTER TABLE stream_events DROP CONSTRAINT stream_events_kind_check;
ALTER TABLE stream_events ADD CONSTRAINT stream_events_kind_check CHECK (kind IN ('transcript', 'chat', 'spoken'));

ALTER TABLE fallacy_marks DROP CONSTRAINT fallacy_marks_kind_check;
ALTER TABLE fallacy_marks ADD CONSTRAINT fallacy_marks_kind_check CHECK (kind IN ('transcript', 'chat', 'spoken'));
ALTER TABLE fallacy_marks
    ADD CONSTRAINT mark_spoken_quote CHECK (kind <> 'spoken' OR char_length(quote) <= 300),
    ADD CONSTRAINT mark_spoken_shape CHECK (kind <> 'spoken' OR (start_event_id = end_event_id AND start_offset = 0));
