"""Catalogue of replay videos: the admin submits a URL, a background job collects the transcript and chat into replay_events, and a
game can only start from a replay whose status is 'ready'.

States: queued -> downloading -> importing -> ready | failed.  A failed video can be submitted again (it is queued again).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import class_row

from app.db import ensure_autocommit
from app.sessions import SessionError

MAX_QUEUED = 5
MAX_ERROR = 500
RUNNING = ("queued", "downloading", "importing")
MIGRATION_0010 = Path(__file__).resolve().parent.parent / "migrations" / "0010_replays.sql"
WATCH_URL = "https://www.youtube.com/watch?v={}"
INTERRUPTED = "Interrompido por reinício do servidor. Envie o link de novo."


class ReplayError(SessionError):
    code = "error"


def _err(code: str) -> ReplayError:
    e = ReplayError(code)
    e.code = code
    return e


@dataclass(frozen=True)
class Replay:
    video_id: str
    url: str
    title: str
    width: int | None
    height: int | None
    duration_s: int | None
    status: str
    error: str
    transcript_rows: int
    chat_rows: int
    created_at: datetime
    finished_at: datetime | None


_COLS = ("video_id, url, title, width, height, duration_s, status, error, transcript_rows, chat_rows, created_at, finished_at")


def _lock(conn: psycopg.Connection) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('livegames.replays'))")


def get(conn: psycopg.Connection, video_id: str) -> Replay | None:
    with conn.cursor(row_factory=class_row(Replay)) as cur:
        cur.execute(f"SELECT {_COLS} FROM replays WHERE video_id = %s", (video_id,))
        return cur.fetchone()


def list_replays(conn: psycopg.Connection) -> list[Replay]:
    """Newest first."""
    with conn.cursor(row_factory=class_row(Replay)) as cur:
        cur.execute(f"SELECT {_COLS} FROM replays ORDER BY created_at DESC, video_id")
        return cur.fetchall()


def ready_replays(conn: psycopg.Connection) -> list[Replay]:
    return [r for r in list_replays(conn) if r.status == "ready"]


def is_ready(conn: psycopg.Connection, video_id: str) -> bool:
    return conn.execute("SELECT 1 FROM replays WHERE video_id = %s AND status = 'ready'", (video_id,)).fetchone() is not None


def submit(conn: psycopg.Connection, raw: str) -> Replay:
    """Queue a video for collection. Raises feed.InvalidVideo (not a YouTube link) or ReplayError: already_ready, already_running,
    queue_full. A failed video is queued again."""
    from app.feed import parse_video_id  # lazy: feed imports this module
    ensure_autocommit(conn)
    video_id = parse_video_id(raw)
    with conn.transaction():
        _lock(conn)
        cur = conn.execute("SELECT status FROM replays WHERE video_id = %s", (video_id,)).fetchone()
        if cur is not None and cur[0] == "ready":
            raise _err("already_ready")
        if cur is not None and cur[0] != "failed":
            raise _err("already_running")
        if conn.execute("SELECT count(1) FROM replays WHERE status = 'queued'").fetchone()[0] >= MAX_QUEUED:  # type: ignore[index]
            raise _err("queue_full")
        if cur is None:
            conn.execute("INSERT INTO replays (video_id, url, status) VALUES (%s,%s,'queued')", (video_id, WATCH_URL.format(video_id)))
        else:
            conn.execute("UPDATE replays SET status = 'queued', error = '', finished_at = NULL, created_at = now(), updated_at = now() "
                         "WHERE video_id = %s", (video_id,))
    return get(conn, video_id)  # type: ignore[return-value]


def claim_next(conn: psycopg.Connection) -> Replay | None:
    """Atomically take the oldest queued video (-> downloading). None when the queue is empty."""
    ensure_autocommit(conn)
    row = conn.execute(
        "UPDATE replays SET status = 'downloading', updated_at = now() WHERE video_id = ("
        "  SELECT video_id FROM replays WHERE status = 'queued' ORDER BY created_at, video_id FOR UPDATE SKIP LOCKED LIMIT 1"
        ") RETURNING video_id").fetchone()
    return get(conn, row[0]) if row else None


def _require(conn: psycopg.Connection, video_id: str, allowed: tuple[str, ...]) -> None:
    cur = conn.execute("SELECT status FROM replays WHERE video_id = %s FOR UPDATE", (video_id,)).fetchone()
    if cur is None:
        raise _err("not_found")
    if cur[0] not in allowed:
        raise _err("bad_state")


def mark_importing(conn: psycopg.Connection, video_id: str) -> Replay:
    ensure_autocommit(conn)
    with conn.transaction():
        _lock(conn)
        _require(conn, video_id, ("downloading",))
        conn.execute("UPDATE replays SET status = 'importing', updated_at = now() WHERE video_id = %s", (video_id,))
    return get(conn, video_id)  # type: ignore[return-value]


def _counts(conn: psycopg.Connection, video_id: str) -> tuple[int, int]:
    rows = dict(conn.execute("SELECT kind, count(1) FROM replay_events WHERE video_id = %s GROUP BY 1", (video_id,)).fetchall())
    return rows.get("transcript", 0), rows.get("chat", 0)


def mark_ready(conn: psycopg.Connection, video_id: str, title: str, width: int | None, height: int | None,
               duration_s: int | None) -> Replay:
    """The rows are in replay_events: record their counts + metadata. Refused ('incomplete') unless BOTH tracks have rows."""
    ensure_autocommit(conn)
    with conn.transaction():
        _lock(conn)
        _require(conn, video_id, ("downloading", "importing"))
        t, c = _counts(conn, video_id)
        if not t or not c:
            raise _err("incomplete")
        conn.execute("UPDATE replays SET status = 'ready', title = %s, width = %s, height = %s, duration_s = %s, transcript_rows = %s, "
                     "chat_rows = %s, error = '', finished_at = now(), updated_at = now() WHERE video_id = %s",
                     ((title or "")[:300], width, height, duration_s, t, c, video_id))
    return get(conn, video_id)  # type: ignore[return-value]


def mark_failed(conn: psycopg.Connection, video_id: str, error: str) -> Replay:
    """Record why it failed and drop any partial rows."""
    ensure_autocommit(conn)
    msg = (" ".join((error or "").split()) or "Falhou.")[:MAX_ERROR]
    with conn.transaction():
        _lock(conn)
        _require(conn, video_id, ("queued", "downloading", "importing", "failed"))
        conn.execute("DELETE FROM replay_events WHERE video_id = %s", (video_id,))
        conn.execute("UPDATE replays SET status = 'failed', error = %s, finished_at = now(), updated_at = now() WHERE video_id = %s",
                     (msg, video_id))
    return get(conn, video_id)  # type: ignore[return-value]


def recover_interrupted(conn: psycopg.Connection) -> int:
    """At start-up: jobs left 'downloading'/'importing' by a restart become 'failed' (not silently retried). Returns how many."""
    ensure_autocommit(conn)
    with conn.transaction():
        _lock(conn)
        ids = [r[0] for r in conn.execute("SELECT video_id FROM replays WHERE status IN ('downloading', 'importing') FOR UPDATE")]
        for vid in ids:
            conn.execute("DELETE FROM replay_events WHERE video_id = %s", (vid,))
            conn.execute("UPDATE replays SET status = 'failed', error = %s, finished_at = now(), updated_at = now() WHERE video_id = %s",
                         (INTERRUPTED, vid))
    return len(ids)


def delete(conn: psycopg.Connection, video_id: str) -> None:
    """Remove a ready/failed replay and its events. Refused: not_found, busy (queued/running), in_use (an active game follows it)."""
    ensure_autocommit(conn)
    with conn.transaction():
        _lock(conn)
        cur = conn.execute("SELECT status FROM replays WHERE video_id = %s FOR UPDATE", (video_id,)).fetchone()
        if cur is None:
            raise _err("not_found")
        if cur[0] in RUNNING:
            raise _err("busy")
        from app.feed import feed_config
        legacy = feed_config().video_id
        if conn.execute("SELECT 1 FROM game_sessions WHERE game_type = 'falacias' AND status = 'active' "
                        "AND (video_id = %s OR (video_id IS NULL AND %s = %s)) LIMIT 1", (video_id, video_id, legacy)).fetchone():
            raise _err("in_use")
        conn.execute("DELETE FROM replay_events WHERE video_id = %s", (video_id,))
        conn.execute("DELETE FROM replays WHERE video_id = %s", (video_id,))


def register_ready(conn: psycopg.Connection, video_id: str) -> Replay:
    """Called after rows reach replay_events by another route (CLI import, SQL seed): make sure the catalogue lists the video as
    ready. A job that is queued/running is left to its own worker. Refused ('incomplete') unless BOTH tracks have rows."""
    ensure_autocommit(conn)
    with conn.transaction():
        _lock(conn)
        t, c = _counts(conn, video_id)
        if not t or not c:
            raise _err("incomplete")
        conn.execute(
            "INSERT INTO replays (video_id, url, status, transcript_rows, chat_rows, finished_at) VALUES (%s,%s,'ready',%s,%s,now()) "
            "ON CONFLICT (video_id) DO UPDATE SET status = 'ready', transcript_rows = EXCLUDED.transcript_rows, "
            "chat_rows = EXCLUDED.chat_rows, error = '', finished_at = COALESCE(replays.finished_at, now()), updated_at = now() "
            "WHERE replays.status IN ('ready', 'failed')", (video_id, WATCH_URL.format(video_id), t, c))
    return get(conn, video_id)  # type: ignore[return-value]
