"""Load the curated phrase list (corpus/phrases.csv) and video provenance into PostgreSQL."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import psycopg

from corpus.extract import MANUAL_MAX_WORDS, MANUAL_MIN_WORDS

MANUAL_SCORE = 99.0     # keep equal to app.phrases.MANUAL_SCORE (corpus must not import app internals)


def _word_ok(text: str, source: str) -> bool:
    n = len(text.split())
    return MANUAL_MIN_WORDS <= n <= MANUAL_MAX_WORDS     # same 1-10 as the DB: admin edits may shorten any source


def load_phrases(conn: psycopg.Connection, csv_path: Path, force: bool = False,
                 new_only: bool = False) -> dict[str, int | str]:
    """SEED the phrase table from the CSV. The DB is the source of truth (admin edits it), so by default this
    only runs when the table is EMPTY; otherwise nothing is touched.
      new_only=True : insert ONLY rows whose text is missing from the DB, always INACTIVE; existing rows are
                      never changed (a phrase deleted in the UI comes back, inactive).
      force=True    : upsert every row again: OVERWRITES source/score/enabled of matching rows and re-adds deleted
                      ones (play counters are kept).
    Word limit: 1-10 for every source (the 3-10 rule for extracted candidates is applied by corpus/extract.py). Manual rows without a score get 99."""
    stats: dict[str, int | str] = {"upserted": 0, "enabled": 0, "disabled": 0, "rejected": 0, "added": 0}
    with csv_path.open(newline="", encoding="utf-8") as f, conn.transaction():
        existing = conn.execute("SELECT count(*) FROM phrases").fetchone()[0]  # type: ignore[index]
        if existing and not force and not new_only:
            return {**stats, "skipped": f"phrases table already has {existing} rows "
                                        "(use --new to add missing ones as inactive, --force to overwrite)"}
        for r in csv.DictReader(f):
            text = " ".join(r["text"].split()).lower()
            if not _word_ok(text, r["source"]):
                stats["rejected"] += 1  # type: ignore[operator]
                continue
            score = float(r.get("score") or 0)
            if r["source"] == "manual" and score <= 0:
                score = MANUAL_SCORE
            if new_only:
                added = conn.execute(
                    "INSERT INTO phrases (text, source, score, prior_score, enabled) VALUES (%s,%s,%s,%s,false) "
                    "ON CONFLICT (text) DO NOTHING", (text, r["source"], score, score)).rowcount
                stats["added"] += added  # type: ignore[operator]
                continue
            enabled = r.get("enabled", "1").strip() == "1"
            conn.execute(
                "INSERT INTO phrases (text, source, score, prior_score, enabled) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (text) DO UPDATE SET source = EXCLUDED.source, score = EXCLUDED.score, "
                "prior_score = EXCLUDED.prior_score, enabled = EXCLUDED.enabled",
                (text, r["source"], score, score, enabled),
            )
            stats["upserted"] += 1  # type: ignore[operator]
            stats["enabled" if enabled else "disabled"] += 1  # type: ignore[operator]
    return stats


def load_sql_seed(conn: psycopg.Connection, sql_path: Path) -> dict[str, int | str]:
    """Seed from a SQL file made by deploy/make_seed.py (data only, ON CONFLICT DO NOTHING). Like the CSV seed it
    runs ONLY when the phrases table is EMPTY: re-applying it on every start would bring back phrases the admin
    deleted. The file carries its own BEGIN/COMMIT, so the connection must be autocommit."""
    if not conn.autocommit:
        raise RuntimeError("load_sql_seed needs an autocommit connection (the file has its own BEGIN/COMMIT)")
    existing = conn.execute("SELECT count(*) FROM phrases").fetchone()[0]  # type: ignore[index]
    if existing:
        return {"skipped": f"phrases table already has {existing} rows"}
    conn.execute(sql_path.read_text(encoding="utf-8"))  # type: ignore[arg-type]  # multi-statement, no params
    return {"phrases": conn.execute("SELECT count(*) FROM phrases").fetchone()[0],  # type: ignore[index]
            "enabled": conn.execute("SELECT count(*) FROM phrases WHERE enabled").fetchone()[0],  # type: ignore[index]
            "videos": conn.execute("SELECT count(*) FROM ingested_videos").fetchone()[0]}  # type: ignore[index]


def load_videos(conn: psycopg.Connection, manifest_path: Path) -> int:
    n = 0
    with conn.transaction():
        for m in json.loads(manifest_path.read_text()):
            if m["orientation"] not in ("horizontal", "vertical"):
                continue
            up = datetime.strptime(m["upload_date"], "%Y%m%d").date() if m.get("upload_date") else None
            conn.execute(
                "INSERT INTO ingested_videos (video_id, title, upload_date, duration_s, width, height,"
                " orientation, has_transcript, has_chat) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (video_id) DO UPDATE SET title = EXCLUDED.title, "
                "has_transcript = EXCLUDED.has_transcript, has_chat = EXCLUDED.has_chat",
                (m["video_id"], m["title"], up, m.get("duration"), m.get("width"), m.get("height"),
                 m["orientation"], bool(m.get("transcript")), bool(m.get("chat"))),
            )
            n += 1
    return n
