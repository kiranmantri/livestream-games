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
    monkeypatch.delenv("LIVE_VIDEO_ID", raising=False)
