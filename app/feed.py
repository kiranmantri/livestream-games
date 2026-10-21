"""The feed of "Encontre as Falácias": what the two panes (transcript, chat) show, and when.

A StreamSource decides which lines a session has "heard" so far. The only source for now is a REPLAY of a recorded
video (rows in `replay_events`, see corpus/replay.py): the session clock starts at the session's creation time and a line
with video offset `o` becomes visible at `created_at + (o - START_AT) / SPEED`. Releasing is LAZY: `tick()` is called by
the page's feed poll (throttled), inserts everything that is due into `stream_events` and is idempotent, so no background
thread is needed and any number of app workers can call it. `fed_at` is the SCHEDULED time, not the moment tick ran, so
the latency clock of the speed bonus is exact even if nobody polled for a while.

Env: FALACIAS_VIDEO_ID (default _nVWPIFCUzQ), FALACIAS_SPEED (default 1; > 1 is for demos/tests only: it makes the speed
bonus meaningless), FALACIAS_START_AT (seconds into the video, default 0).
A live source (YouTube captions + speech-to-text) would be another source behind the same functions; see docs/backlog.md.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import psycopg

from app import sessions as S
from app.config import ROOT
from app.db import ensure_autocommit
from app.texts import FEED_UNAVAILABLE

if TYPE_CHECKING:
    from app.livestream import StreamInfo

DEFAULT_VIDEO_ID = "_nVWPIFCUzQ"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
MAX_SPEED = 1000.0
TICK_INTERVAL_S = 1.0


class FeedError(S.SessionError):
    code = "feed_error"

    def __init__(self, message: str = FEED_UNAVAILABLE):
        super().__init__(message)


class FeedUnavailable(FeedError):
    """At session start: the video's transcript and/or chat are missing -> no session is created."""
    code = "feed_unavailable"


class ReplayNotImported(FeedUnavailable):
    """The replay video has no rows (both tracks) in replay_events: `corpus replay-import <id>` first. Same error code as its
    parent (pages show the same message); `video_id` tells the page WHICH video to name."""

    def __init__(self, video_id: str):
        super().__init__()
        self.video_id = video_id


class InvalidVideo(FeedError):
    code = "invalid_video"


class NoSource(FeedError):
    """No horizontal live stream and no replay video given: the player must be asked for a video URL."""
    code = "no_source"


class FeedDown(FeedError):
    """During a game: the source stopped working -> new pairs are refused until it recovers (the admin may end the game)."""
    code = "feed_down"


@dataclass(frozen=True)
class FeedConfig:
    video_id: str
    speed: float
    start_at_ms: int


def _check_video_id(video_id: str) -> str:
    if not VIDEO_ID_RE.match(video_id or ""):
        raise ValueError(f"invalid video id {video_id!r}")
    return video_id


def feed_config() -> FeedConfig:
    video_id = _check_video_id(os.environ.get("FALACIAS_VIDEO_ID") or DEFAULT_VIDEO_ID)
    try:
        speed = float(os.environ.get("FALACIAS_SPEED") or 1)
        start_at = float(os.environ.get("FALACIAS_START_AT") or 0)
    except ValueError as e:
        raise ValueError("FALACIAS_SPEED / FALACIAS_START_AT must be numbers") from e
    if not 0 < speed <= MAX_SPEED:
        raise ValueError(f"FALACIAS_SPEED must be in (0, {MAX_SPEED:g}]")
    if start_at < 0:
        raise ValueError("FALACIAS_START_AT must be >= 0")
    return FeedConfig(video_id, speed, round(start_at * 1000))


# ------------------------------------------------------------------ readiness
def check_video_ready(conn: psycopg.Connection, video_id: str) -> None:
    """Both tracks of this replay video must have rows, else ReplayNotImported (a FeedUnavailable)."""
    kinds = {r[0] for r in conn.execute("SELECT DISTINCT kind FROM replay_events WHERE video_id = %s", (video_id,))}
    if kinds != {"transcript", "chat"}:
        raise ReplayNotImported(video_id)


def check_ready(conn: psycopg.Connection, cfg: FeedConfig | None = None) -> None:
    """Both tracks of the configured video must have rows, else FeedUnavailable."""
    check_video_ready(conn, (cfg or feed_config()).video_id)


# ------------------------------------------------------------------ which video a session follows
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


@dataclass(frozen=True)
class Source:
    mode: str            # 'live' | 'replay'
    video_id: str


def parse_video_id(raw: str | None) -> str:
    """An 11-character YouTube id, or a youtube.com / youtu.be URL carrying one (watch?v=, /live/, /embed/, youtu.be/). Anything
    else (other hosts, look-alike hosts, odd schemes, extra path pieces) is refused: the id ends up in queries and URLs."""
    text = (raw or "").strip()
    if YT_ID.match(text):
        return text
    try:
        u = urlparse(text)
    except ValueError as e:
        raise InvalidVideo("not a YouTube link") from e
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https"):
        raise InvalidVideo("not a YouTube link")
    cand = None
    if host in YT_HOSTS:
        parts = [p for p in u.path.split("/") if p]
        if u.path.rstrip("/") == "/watch":
            cand = (parse_qs(u.query).get("v") or [None])[0]
        elif len(parts) == 2 and parts[0] in ("live", "embed"):
            cand = parts[1]
    elif host == "youtu.be":
        parts = [p for p in u.path.split("/") if p]
        cand = parts[0] if len(parts) == 1 else None
    if not cand or not YT_ID.match(cand):
        raise InvalidVideo("not a YouTube video link")
    return cand


def resolve_source(conn: psycopg.Connection, live: StreamInfo | None, requested: str | None = None) -> Source:
    """What a new game follows. A HORIZONTAL live stream always wins (a replay is never used while one exists); a vertical live
    stream is ignored. Without a live: the replay the player asked for (a YouTube id/URL whose transcript + chat were imported),
    else NoSource: the page must ask for a video URL."""
    if live is not None and live.horizontal:
        return Source("live", live.id)
    if not (requested or "").strip():
        raise NoSource("no horizontal live stream and no video given")
    video_id = parse_video_id(requested)
    check_video_ready(conn, video_id)
    return Source("replay", video_id)


def start_falacias(conn: psycopg.Connection, name: str, ip: str, source: Source | None = None):
    """First visitor starts the Falácias session: refused (no session row) when the replay is not ready. `source` None = the
    legacy env replay (FALACIAS_VIDEO_ID). A live source needs no replay rows."""
    source = source or Source("replay", feed_config().video_id)
    if source.mode == "replay":
        check_video_ready(conn, source.video_id)
    return S.start_session(conn, name, ip, game_type="falacias", video_id=source.video_id, source_mode=source.mode)


def start_falacias_as_admin(conn: psycopg.Connection, source: Source | None = None):
    source = source or Source("replay", feed_config().video_id)
    if source.mode == "replay":
        check_video_ready(conn, source.video_id)
    return S.start_session_as_admin(conn, game_type="falacias", video_id=source.video_id, source_mode=source.mode)


def session_source(conn: psycopg.Connection, session_id: UUID) -> tuple[str | None, str | None]:
    """(video_id, mode) stored on the session; (None, None) = legacy session or unknown session."""
    row = conn.execute("SELECT video_id, source_mode FROM game_sessions WHERE id = %s", (session_id,)).fetchone()
    return (row[0], row[1]) if row else (None, None)


# ------------------------------------------------------------------ session clock
def tick(conn: psycopg.Connection, session_id: UUID, cfg: FeedConfig | None = None) -> int:
    """Show in the panes every replay line that is due by now. Returns how many were released (idempotent)."""
    ensure_autocommit(conn)
    cfg = cfg or feed_config()
    with conn.transaction():
        # one releaser per session at a time: stream_events ids must follow the replay offsets
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"falacias-feed:{session_id}",))
        row = conn.execute("SELECT extract(epoch FROM clock_timestamp() - created_at) * 1000, video_id, source_mode "
                           "FROM game_sessions WHERE id = %s AND status = 'active' AND game_type = 'falacias'",
                           (session_id,)).fetchone()
        if row is None or row[2] == "live":          # a live session has no replay clock (its chat arrives from the chat worker)
            return 0
        if row[1]:
            cfg = replace(cfg, video_id=row[1])
        limit_ms = cfg.start_at_ms + int(float(row[0]) * cfg.speed)
        return conn.execute(
            "INSERT INTO stream_events (session_id, kind, source_key, author, text, fed_at) "
            "SELECT g.id, r.kind, r.id::text, r.author, r.text, "
            "       g.created_at + make_interval(secs => (r.offset_ms - %(start)s)::float8 / %(speed)s / 1000.0) "
            "FROM replay_events r, game_sessions g "
            "WHERE g.id = %(sid)s AND r.video_id = %(vid)s AND r.offset_ms >= %(start)s AND r.offset_ms <= %(limit)s "
            "  AND r.offset_ms > COALESCE((SELECT r2.offset_ms FROM stream_events e JOIN replay_events r2 "
            "                                ON r2.id = e.source_key::bigint "
            "                              WHERE e.session_id = %(sid)s AND e.source_key ~ '^[0-9]+$' "
            "                              ORDER BY e.id DESC LIMIT 1), -1) "
            "ORDER BY r.offset_ms, r.id ON CONFLICT DO NOTHING",
            {"sid": session_id, "vid": cfg.video_id, "start": cfg.start_at_ms, "speed": cfg.speed, "limit": limit_ms},
        ).rowcount


_last_tick: dict[UUID, float] = {}
_tick_lock = threading.Lock()


def tick_throttled(conn: psycopg.Connection, session_id: UUID, min_interval: float = TICK_INTERVAL_S) -> int:
    """tick() at most once per `min_interval` seconds per session in this process (50 pollers must not mean 50 queries/s)."""
    now = time.monotonic()
    with _tick_lock:
        if now - _last_tick.get(session_id, -1e9) < min_interval:
            return 0
        _last_tick[session_id] = now
    return tick(conn, session_id)


# ------------------------------------------------------------------ what the panes show (read model)
@dataclass(frozen=True)
class Event:
    id: int
    kind: str
    author: str | None
    text: str


def recent_events(conn: psycopg.Connection, session_id: UUID, per_kind: int = 120) -> list[Event]:
    """The last `per_kind` lines of EACH pane, oldest first (what a player sees when opening the page)."""
    rows = conn.execute(
        "SELECT id, kind, author, text FROM (SELECT *, row_number() OVER (PARTITION BY kind ORDER BY id DESC) AS rn "
        "FROM stream_events WHERE session_id = %s AND kind IN ('transcript', 'chat')) t WHERE rn <= %s ORDER BY id",
        (session_id, per_kind)).fetchall()
    return [Event(*r) for r in rows]


def events_after(conn: psycopg.Connection, session_id: UUID, after_id: int, limit: int = 300) -> list[Event]:
    rows = conn.execute("SELECT id, kind, author, text FROM stream_events WHERE session_id = %s AND id > %s "
                        "AND kind IN ('transcript', 'chat') ORDER BY id LIMIT %s", (session_id, after_id, limit)).fetchall()
    return [Event(*r) for r in rows]


def last_event_id(conn: psycopg.Connection, session_id: UUID) -> int:
    return conn.execute("SELECT COALESCE(max(id), 0) FROM stream_events WHERE session_id = %s AND kind IN ('transcript', 'chat')", (session_id,)).fetchone()[0]


# ------------------------------------------------------------------ health
def status(conn: psycopg.Connection, session_id: UUID, cfg: FeedConfig | None = None) -> str:
    """'down' (a track vanished), 'ended' (every replay line was shown: "fim da transmissão"), else 'ok'.
    A replay cannot go silent by itself (the real video's longest transcript gap is 22 s); the "silent for 60 s" rule
    belongs to a future live source."""
    cfg = cfg or feed_config()
    video_id, mode = session_source(conn, session_id)
    if mode == "live":
        return "ok"                  # the chat worker's own health arrives with L3
    if video_id:
        cfg = replace(cfg, video_id=video_id)
    try:
        check_ready(conn, cfg)
    except FeedUnavailable:
        return "down"
    left = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM replay_events WHERE video_id = %(vid)s AND offset_ms >= %(start)s AND offset_ms > "
        "COALESCE((SELECT r2.offset_ms FROM stream_events e JOIN replay_events r2 ON r2.id = e.source_key::bigint "
        "         WHERE e.session_id = %(sid)s AND e.source_key ~ '^[0-9]+$' ORDER BY e.id DESC LIMIT 1), -1))",
        {"vid": cfg.video_id, "start": cfg.start_at_ms, "sid": session_id}).fetchone()[0]
    return "ok" if left else "ended"


def ensure_up(conn: psycopg.Connection, session_id: UUID) -> None:
    """Call before accepting a new pair: FeedDown while a track is missing. The end of the video is NOT a failure."""
    if status(conn, session_id) == "down":
        raise FeedDown()


# ------------------------------------------------------------------ cloud seed
def seed_path(video_id: str) -> Path:
    return ROOT / "deploy" / "seed" / f"replay_{_check_video_id(video_id)}.sql"


def load_replay_seed(conn: psycopg.Connection, video_id: str | None = None, path: Path | None = None) -> dict:
    """Apply the video's SQL seed ONLY when the video has no rows (a restart never undoes admin/maintenance changes)."""
    ensure_autocommit(conn)
    video_id = _check_video_id(video_id or feed_config().video_id)
    path = path or seed_path(video_id)
    if not Path(path).exists():
        return {"skipped": f"no seed file at {path}"}
    sql = Path(path).read_text(encoding="utf-8")
    with conn.transaction():
        conn.execute("LOCK TABLE replay_events IN EXCLUSIVE MODE")
        have = conn.execute("SELECT count(*) FROM replay_events WHERE video_id = %s", (video_id,)).fetchone()[0]
        if have:
            return {"skipped": f"{video_id} already has {have} replay rows"}
        conn.execute(sql)
        return {"inserted": conn.execute("SELECT count(*) FROM replay_events WHERE video_id = %s", (video_id,)).fetchone()[0]}
