# ruff: noqa: BLE001, S110  -- the worker thread must survive ANY failure of yt-dlp, the DB or a parse: it records "down" and retries
"""Live-chat worker (L3): reads the CURRENT live stream's chat into `stream_events` (kind 'chat') while a live Falácias session runs.

How: a yt-dlp subprocess (`--write-subs --sub-langs live_chat`) appends JSON lines to `chat.live_chat.json.part`; this module tails
that file, parses the text messages and inserts them, deduplicated by YouTube's message id (a restarted process replays ~2 MB of
backlog: the unique index (session, kind, source_key) and the "not before the session started" rule make that harmless).
One daemon thread per live session, started lazily by feed.tick(). No new dependency (yt-dlp is already a runtime dependency).

Weaknesses (accepted): the `.part` file is a yt-dlp internal, not a public interface; YouTube may change or block the chat page;
health is inferred from "the process is alive and printed something in the last STALL_S seconds".
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

import psycopg

from app.db import connect
from corpus.replay import TEXT_RENDERERS, _runs_text, clean

POLL_S = 1.0                  # worker loop period
STALL_S = 90.0                # no output at all for this long => restart (yt-dlp prints a chunk about every 10 s)
BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 60.0
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PART_NAME = "chat.live_chat.json.part"
FINAL_NAME = "chat.live_chat.json"


# ------------------------------------------------------------------ parsing + storing
@dataclass(frozen=True)
class ChatMsg:
    key: str
    author: str | None
    text: str
    ts_us: int | None


def parse_lines(lines: list[str]) -> list[ChatMsg]:
    """Text messages (plain and paid) from yt-dlp live-chat JSON lines, oldest first. Junk, gifts, system items, empty text and
    items without an id are dropped."""
    out: list[ChatMsg] = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        wrapped = d.get("replayChatItemAction")
        actions = wrapped.get("actions", []) if isinstance(wrapped, dict) else [d]
        for a in actions:
            item = (a.get("addChatItemAction") or {}).get("item", {}) if isinstance(a, dict) else {}
            for kind in TEXT_RENDERERS:
                r = item.get(kind)
                if not isinstance(r, dict):
                    continue
                key = str(r.get("id") or "")
                text = _runs_text(r.get("message", {}).get("runs", []))
                if not key or not text:
                    continue
                try:
                    ts = int(r["timestampUsec"]) if "timestampUsec" in r else None
                except (TypeError, ValueError):
                    ts = None
                author = clean(r.get("authorName", {}).get("simpleText", "")) or None
                out.append(ChatMsg(key, author, text, ts))
    out.sort(key=lambda m: (m.ts_us is None, m.ts_us or 0))
    return out


def ingest(conn: psycopg.Connection, session_id: UUID, msgs: list[ChatMsg]) -> int:
    """Insert new chat lines into an ACTIVE live Falácias session. Messages without a timestamp or older than the session are
    dropped (backlog); a message already stored (same YouTube id) is skipped. Returns how many rows were added."""
    added = 0
    for m in msgs:
        if m.ts_us is None:
            continue
        added += conn.execute(
            "INSERT INTO stream_events (session_id, kind, source_key, author, text) "
            "SELECT id, 'chat', %s, %s, %s FROM game_sessions "
            "WHERE id = %s AND status = 'active' AND game_type = 'falacias' AND source_mode = 'live' "
            "  AND created_at <= to_timestamp(%s / 1000000.0) "
            "ON CONFLICT DO NOTHING",
            (m.key, m.author, m.text, session_id, m.ts_us)).rowcount
    return added


# ------------------------------------------------------------------ the yt-dlp process
def ytdlp_command(video_id: str) -> list[str]:
    if not YT_ID.match(video_id or ""):
        raise ValueError("not a YouTube video id")
    return [sys.executable, "-m", "yt_dlp", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
            "--no-warnings", "--no-playlist", "-o", "chat", f"https://www.youtube.com/watch?v={video_id}"]


class YtdlpChat:
    """ChatProcess: start() / read_lines() / alive() / stop(). Files live in a private temp dir, removed by stop()."""

    def __init__(self, video_id: str, workdir: Path | None = None, command: list[str] | None = None):
        self.command = command if command is not None else ytdlp_command(video_id)
        self.workdir = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="lgchat-"))
        self._proc: subprocess.Popen | None = None
        self._offset = 0
        self._buf = b""

    def start(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._proc = subprocess.Popen(self.command, cwd=self.workdir, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True)

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def read_lines(self) -> list[str]:
        """Complete new lines since the last call (a half-written last line waits for the next call)."""
        path = next((p for p in (self.workdir / PART_NAME, self.workdir / FINAL_NAME) if p.exists()), None)
        if path is None:
            return []
        with path.open("rb") as fh:
            size = fh.seek(0, 2)
            if size < self._offset:                       # file replaced by a shorter one: start over
                self._offset, self._buf = 0, b""
            fh.seek(self._offset)
            data = fh.read()
        self._offset += len(data)
        parts = (self._buf + data).split(b"\n")
        self._buf = parts.pop()
        return [p.decode("utf-8", errors="replace") for p in parts if p.strip()]

    def stop(self) -> None:
        p, self._proc = self._proc, None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        shutil.rmtree(self.workdir, ignore_errors=True)


# ------------------------------------------------------------------ the worker
class ChatProcess(Protocol):
    def start(self) -> None: ...
    def read_lines(self) -> list[str]: ...
    def alive(self) -> bool: ...
    def stop(self) -> None: ...


def _default_process(video_id: str) -> YtdlpChat:
    return YtdlpChat(video_id)


process_factory: Callable[[str], ChatProcess] = _default_process
connect_fn: Callable[[], psycopg.Connection] = connect


class LiveChatWorker:
    """One live session. step() is one deterministic iteration (the thread just calls it); the clock is injectable for tests."""

    def __init__(self, session_id: UUID, video_id: str, factory: Callable[[str], ChatProcess],
                 connect_fn: Callable[[], psycopg.Connection], clock: Callable[[], float] = time.monotonic,
                 stall_s: float = STALL_S):
        self.session_id, self.video_id = session_id, video_id
        self._factory, self._connect, self._clock, self.stall_s = factory, connect_fn, clock, stall_s
        self._conn: psycopg.Connection | None = None
        self._proc: ChatProcess | None = None
        self._ok = True
        self._failures = 0
        self._next_try = 0.0
        self._last_activity = 0.0
        self.stop_event = threading.Event()

    def healthy(self) -> bool:
        return self._ok

    def retry_in(self) -> float:
        return max(0.0, self._next_try - self._clock())

    # -- internals
    def _drop_proc(self) -> None:
        p, self._proc = self._proc, None
        if p is not None:
            try:
                p.stop()
            except Exception:
                pass

    def _fail(self, now: float) -> None:
        self._ok = False
        self._failures += 1
        self._next_try = now + min(BACKOFF_BASE_S * 2 ** (self._failures - 1), BACKOFF_MAX_S)
        self._drop_proc()

    def _db(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = self._connect()
        return self._conn

    def close(self) -> None:
        self._drop_proc()
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def step(self) -> str:
        now = self._clock()
        try:
            conn = self._db()
            active = conn.execute("SELECT 1 FROM game_sessions WHERE id = %s AND status = 'active'",
                                  (self.session_id,)).fetchone()
        except Exception:
            self._conn = None
            self._fail(now)
            return "down"
        if not active:
            self._drop_proc()
            return "ended"
        if self._proc is None:
            if now < self._next_try:
                return "down"
            try:
                proc = self._factory(self.video_id)
                proc.start()
            except Exception:
                self._fail(now)
                return "down"
            self._proc, self._last_activity, self._ok = proc, now, True
        try:
            lines = self._proc.read_lines()
            if lines:
                self._last_activity = now
                self._failures = 0
                ingest(conn, self.session_id, parse_lines(lines))
            if not self._proc.alive() or now - self._last_activity > self.stall_s:
                self._fail(now)
                return "down"
        except Exception:
            self._conn = None
            self._fail(now)
            return "down"
        return "ok"


# ------------------------------------------------------------------ registry (one thread per live session)
_workers: dict[UUID, tuple[LiveChatWorker, threading.Thread]] = {}
_lock = threading.Lock()


def _run(w: LiveChatWorker) -> None:
    try:
        while not w.stop_event.is_set():
            try:
                if w.step() == "ended":
                    break
            except Exception:
                w._fail(w._clock())
            w.stop_event.wait(POLL_S)
    finally:
        w.close()
        with _lock:
            cur = _workers.get(w.session_id)
            if cur is not None and cur[0] is w:
                del _workers[w.session_id]


def ensure(session_id: UUID, video_id: str) -> LiveChatWorker:
    """Start (once) the chat worker of a live session; returns the running one on later calls."""
    with _lock:
        cur = _workers.get(session_id)
        if cur is not None and cur[1].is_alive():
            return cur[0]
        w = LiveChatWorker(session_id, video_id, process_factory, connect_fn)
        t = threading.Thread(target=_run, args=(w,), name=f"chat-{session_id}", daemon=True)
        _workers[session_id] = (w, t)
        t.start()
        return w


def get(session_id: UUID) -> LiveChatWorker | None:
    with _lock:
        cur = _workers.get(session_id)
    return cur[0] if cur else None


def health(session_id: UUID) -> str:
    """'ok' | 'down'. No worker yet = 'ok' (the next tick starts it)."""
    w = get(session_id)
    return "ok" if w is None or w.healthy() else "down"


def stop_all() -> None:
    with _lock:
        items = list(_workers.values())
    for w, _ in items:
        w.stop_event.set()
    for _, t in items:
        t.join(timeout=5)
    with _lock:
        _workers.clear()
