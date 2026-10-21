"""Replay rows for "Encontre as Falácias": the cleaned transcript + chat of a recorded video, stored in `replay_events`.

    uv run corpus replay-import <video_id> [--force]      # data/corpus/<id>/*.vtt + *.live_chat.json -> replay_events
    uv run corpus replay-seed <video_id>                   # replay_events -> deploy/seed/replay_<id>.sql (for the cloud)

Only TEXT is kept (a 9 h stream is ~16k lines, about 1 MB as SQL; the raw VTT/JSON are 9 MB).
Cleaning: caption annotations like [Música] are removed, HTML entities decoded, whitespace collapsed, blank lines dropped.
Chat: text messages and the TEXT of paid messages are kept; gifts, stickers, system notices and empty messages are not.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

import psycopg

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
CUE_RE = re.compile(r"^(\d+):(\d\d):(\d\d)\.(\d{3}) -->")
TIMED_RE = re.compile(r"<\d\d:\d\d:\d\d\.\d+>")
TAG_RE = re.compile(r"<[^>]+>")
BRACKET_RE = re.compile(r"\[[^\]]*\]")
BATCH = 500
TEXT_RENDERERS = ("liveChatTextMessageRenderer", "liveChatPaidMessageRenderer")


class ReplayImportError(Exception):
    pass


def check_video_id(video_id: str) -> str:
    if not VIDEO_ID_RE.match(video_id or ""):
        raise ValueError(f"invalid video id {video_id!r}")
    return video_id


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def read_transcript(path: Path) -> list[tuple[int, str]]:
    """YouTube rolling captions: only the line carrying inline <time> tags is new text; its time is the cue start."""
    out: list[tuple[int, str]] = []
    start_ms = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        m = CUE_RE.match(line)
        if m:
            h, mi, s, ms = (int(x) for x in m.groups())
            start_ms = ((h * 60 + mi) * 60 + s) * 1000 + ms
        elif TIMED_RE.search(line):
            text = clean(BRACKET_RE.sub(" ", TAG_RE.sub("", line)))
            if text:
                out.append((start_ms, text))
    return out


def _runs_text(runs: list[dict]) -> str:
    parts = []
    for run in runs:
        if "text" in run:
            parts.append(run["text"])
        elif "emoji" in run:
            e = run["emoji"]
            if e.get("isCustomEmoji") or "/" in e.get("emojiId", ""):
                parts.append((e.get("shortcuts") or [""])[0])        # channel emoji: its :shortcut:, else nothing
            else:
                parts.append(e.get("emojiId", ""))
    return clean("".join(parts))


def read_chat(path: Path) -> list[tuple[int, str | None, str]]:
    """-> [(offset_ms, author, text)] sorted by offset (stable)."""
    out: list[tuple[int, str | None, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        action = d.get("replayChatItemAction", {})
        offset = max(0, int(action.get("videoOffsetTimeMsec", 0) or 0))
        for a in action.get("actions", []):
            item = a.get("addChatItemAction", {}).get("item", {})
            for kind in TEXT_RENDERERS:
                r = item.get(kind)
                if not r:
                    continue
                text = _runs_text(r.get("message", {}).get("runs", []))
                if text:
                    author = clean(r.get("authorName", {}).get("simpleText", "")) or None
                    out.append((offset, author, text))
    out.sort(key=lambda x: x[0])
    return out


def import_video(conn: psycopg.Connection, video_id: str, corpus_dir: Path, force: bool = False) -> dict:
    """Store both tracks of data/corpus/<video_id>/ in replay_events. Nothing is stored unless BOTH tracks have text."""
    check_video_id(video_id)
    d = Path(corpus_dir) / video_id
    vtt = next(iter(sorted(d.glob("*.vtt"))), None) if d.is_dir() else None
    chat = next(iter(sorted(d.glob("*.live_chat.json"))), None) if d.is_dir() else None
    if vtt is None or chat is None:
        raise ReplayImportError(f"{d}: needs a .vtt transcript AND a .live_chat.json (run `corpus fetch` first)")
    transcript, messages = read_transcript(vtt), read_chat(chat)
    if not transcript or not messages:
        raise ReplayImportError(f"{video_id}: transcript has {len(transcript)} lines, chat has {len(messages)} messages")
    if not conn.autocommit:
        raise RuntimeError("import_video needs an autocommit connection")
    have = conn.execute("SELECT count(*) FROM replay_events WHERE video_id = %s", (video_id,)).fetchone()[0]
    if have and not force:
        return {"skipped": f"{video_id} already has {have} replay rows (use --force to replace them)"}
    rows = [(video_id, "transcript", ms, None, t) for ms, t in transcript] + \
           [(video_id, "chat", ms, a, t) for ms, a, t in messages]
    with conn.transaction():
        conn.execute("DELETE FROM replay_events WHERE video_id = %s", (video_id,))
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES (%s,%s,%s,%s,%s) "
                            "ON CONFLICT DO NOTHING", rows)
    return dict(conn.execute("SELECT kind, count(*) FROM replay_events WHERE video_id = %s GROUP BY 1 ORDER BY 1",
                             (video_id,)).fetchall())


def lit(v: object) -> str:
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


def write_seed(conn: psycopg.Connection, video_id: str, out: Path) -> int:
    """replay_events of one video -> an idempotent SQL file (INSERT ... ON CONFLICT DO NOTHING)."""
    check_video_id(video_id)
    rows = conn.execute("SELECT kind, offset_ms, author, text FROM replay_events WHERE video_id = %s "
                        "ORDER BY offset_ms, kind, id", (video_id,)).fetchall()
    if not rows:
        raise ReplayImportError(f"no replay rows for {video_id}: run `corpus replay-import {video_id}` first")
    head = f"-- Replay rows of video {video_id} (cleaned transcript + chat). Generated by `corpus replay-seed`.\n"
    head += "-- Applied by the app only when this video has NO rows yet; ON CONFLICT DO NOTHING makes re-applying harmless.\n"
    parts = [head]
    for i in range(0, len(rows), BATCH):
        vals = ",\n".join(f"({lit(video_id)}, {lit(k)}, {int(o)}, {lit(a)}, {lit(t)})" for k, o, a, t in rows[i:i + BATCH])
        parts.append(f"INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES\n{vals}\nON CONFLICT DO NOTHING;\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(parts), encoding="utf-8")
    return len(rows)
