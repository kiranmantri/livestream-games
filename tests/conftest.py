"""Test fixtures: a throwaway PostgreSQL database per test session (dev DB is never touched)."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.config import database_url
from app.db import MIGRATIONS_DIR, migrate

# LG_MIGRATIONS_DIR lets us run the suite against a mutated COPY of the migrations
# (mutation check: prove the tests fail when a constraint is removed).
MIGRATIONS = Path(os.environ.get("LG_MIGRATIONS_DIR", MIGRATIONS_DIR))


@pytest.fixture(scope="session")
def test_db_url():
    base = database_url()
    name = f"livegames_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    params = conninfo_to_dict(base)
    params["dbname"] = name
    url = make_conninfo(**params)
    with psycopg.connect(url) as conn:
        migrate(conn, MIGRATIONS)
    yield url
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def conn(test_db_url):
    """Fresh connection per test; all tables emptied afterwards."""
    c = psycopg.connect(test_db_url)
    yield c
    c.rollback()
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.commit()
    c.close()


def new_session(conn, name="ana", ip="10.0.0.1", end=False):
    """Create a session + its first user in ONE transaction (deferred circular FKs)."""
    sid, uid = uuid.uuid4(), uuid.uuid4()
    conn.execute("INSERT INTO game_sessions (id, created_by) VALUES (%s, %s)", (sid, uid))
    conn.execute(
        "INSERT INTO users (id, session_id, name, ip, token_hash) VALUES (%s,%s,%s,%s,%s)",
        (uid, sid, name, ip, uuid.uuid4().hex),
    )
    conn.commit()
    if end:
        conn.execute("UPDATE game_sessions SET status='ended', ended_at=now() WHERE id=%s", (sid,))
        conn.commit()
    return sid, uid


def add_user(conn, sid, name, ip="10.0.0.2"):
    uid = uuid.uuid4()
    conn.execute(
        "INSERT INTO users (id, session_id, name, ip, token_hash) VALUES (%s,%s,%s,%s,%s)",
        (uid, sid, name, ip, uuid.uuid4().hex),
    )
    conn.commit()
    return uid


@pytest.fixture(autouse=True)
def no_youtube_lookups(monkeypatch):
    """Tests must never reach YouTube: the live-stream lookup answers 'not live' unless a test injects its own."""
    from app import livestream
    monkeypatch.setattr(livestream, "resolve_channel", lambda url=livestream.DEFAULT_URL: None)
    monkeypatch.setattr(livestream, "resolve_channel_info", lambda url=livestream.DEFAULT_URL: None)
    monkeypatch.setattr(livestream, "resolve_video_title", lambda video_id: None)
    monkeypatch.delenv("LIVE_VIDEO_ID", raising=False)


@pytest.fixture(autouse=True)
def no_real_chat_process(monkeypatch, request):
    """Tests must never start yt-dlp: live sessions get an idle fake chat process; the worker thread uses the test DB if the
    test has a DB at all, else every DB attempt fails (the worker just stays 'down')."""
    from app import chatworker
    from tests.chat_fakes import FakeProcess
    monkeypatch.setattr(chatworker, "process_factory", lambda video_id: FakeProcess())
    if "conn" in request.fixturenames or "test_db_url" in request.fixturenames:
        from app.db import connect
        url = request.getfixturevalue("test_db_url")
        monkeypatch.setattr(chatworker, "connect_fn", lambda: connect(url))
    else:
        def _no_db():
            raise RuntimeError("no database in this test")
        monkeypatch.setattr(chatworker, "connect_fn", _no_db)
    yield
    chatworker.stop_all()


@pytest.fixture(autouse=True)
def no_replay_collector(monkeypatch):
    """The replay collector thread (yt-dlp) never starts in tests unless a test turns it on explicitly."""
    monkeypatch.setenv("REPLAY_COLLECTOR", "off")
    yield
    from app import replaycollector
    replaycollector.stop()


@pytest.fixture(autouse=True)
def no_checker_worker():
    """The AI-check worker thread never outlives a test."""
    yield
    from app import checkworker
    checkworker.stop()


@pytest.fixture(autouse=True)
def _admin_throttle_reset():
    """The admin brute-force throttle is process-wide state: every test starts with nobody locked."""
    from app import admin
    admin.reset_throttle()
    yield
    admin.reset_throttle()


# app.config loads <repo>/.env when it is imported, so a developer's real STT settings and API keys would leak into every test
# (a page rendered with live transcription ON, a worker with a real key). Tests that need one set it themselves with monkeypatch.
_DEV_ONLY_ENV = ("STT_PROVIDER", "STT_LANGUAGE", "STT_URL", "STT_API_URL", "STT_PROMPT", "STT_CHUNK_S", "STT_OVERLAP_S",
                 "GROQ_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY", "CHECKER_MODEL", "GROQ_BASE_URL", "OPENROUTER_BASE_URL")


@pytest.fixture(autouse=True)
def _no_developer_stt_or_keys(monkeypatch, tmp_path_factory):
    for name in _DEV_ONLY_ENV:
        monkeypatch.delenv(name, raising=False)
    # code that calls config.load_env() again (factcheck.off_reason) must not bring the developer's .env back in
    monkeypatch.setattr("app.config.ROOT", tmp_path_factory.mktemp("emptyroot"))
