-- 0010: catalogue of replay videos (a recorded stream's transcript + chat, kept in replay_events).
-- The admin submits a URL; a background job collects both tracks; a game can only start from a 'ready' replay.

CREATE TABLE replays (
    video_id        text        PRIMARY KEY CHECK (video_id ~ '^[A-Za-z0-9_-]{1,32}$'),
    url             text        NOT NULL,
    title           text        NOT NULL DEFAULT '',
    width           integer,
    height          integer,
    duration_s      integer     CHECK (duration_s IS NULL OR duration_s >= 0),
    status          text        NOT NULL CHECK (status IN ('queued', 'downloading', 'importing', 'ready', 'failed')),
    error           text        NOT NULL DEFAULT '',
    transcript_rows integer     NOT NULL DEFAULT 0 CHECK (transcript_rows >= 0),
    chat_rows       integer     NOT NULL DEFAULT 0 CHECK (chat_rows >= 0),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    CONSTRAINT replay_failed_has_error CHECK (status <> 'failed' OR error <> ''),
    CONSTRAINT replay_ready_is_complete CHECK (status <> 'ready' OR (transcript_rows > 0 AND chat_rows > 0 AND finished_at IS NOT NULL))
);
CREATE INDEX replays_queue ON replays (created_at) WHERE status = 'queued';

-- BACKFILL: videos already imported before this migration (both tracks present) are ready.
INSERT INTO replays (video_id, url, status, transcript_rows, chat_rows, finished_at)
SELECT video_id, 'https://www.youtube.com/watch?v=' || video_id, 'ready',
       count(*) FILTER (WHERE kind = 'transcript'), count(*) FILTER (WHERE kind = 'chat'), now()
FROM replay_events
GROUP BY video_id
HAVING count(*) FILTER (WHERE kind = 'transcript') > 0 AND count(*) FILTER (WHERE kind = 'chat') > 0
ON CONFLICT (video_id) DO NOTHING;
