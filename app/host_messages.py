"""Host messages of "Encontre as Falácias": a player sends a short text to the live host; the admin relays it by hand.

Framework-free functions over a psycopg AUTOCOMMIT connection (see app.db.connect). Text is plain text, never HTML
(views escape it). Anti-spam: 1 message per 30 s and at most 5 per player and session (hidden/handled ones still count).
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import class_row

from app.db import ensure_autocommit
from app.falacias import NotAFalaciasPlayer, SessionNotActive
from app.sessions import SessionError

MAX_LEN = 280
RATE_LIMIT_S = 30
MAX_PER_PLAYER = 5
STATUSES = ("new", "done", "hidden")


class HostMessageError(SessionError):
    code = "host_message_error"


class EmptyMessage(HostMessageError):
    code = "empty_message"


class BadMessage(HostMessageError):
    code = "bad_message"


class MessageTooLong(HostMessageError):
    code = "message_too_long"


class MessageRateLimited(HostMessageError):
    code = "message_rate_limited"


class MessageLimitReached(HostMessageError):
    code = "message_limit_reached"


class MessageNotFound(HostMessageError):
    code = "message_not_found"


@dataclass(frozen=True)
class HostMessage:
    id: UUID
    session_id: UUID
    user_id: UUID
    text: str
    status: str
    created_at: datetime
    handled_at: datetime | None


@dataclass(frozen=True)
class MessageRow:
    id: UUID
    user_id: UUID
    name: str
    text: str
    status: str
    created_at: datetime
    handled_at: datetime | None


_COLS = "id, session_id, user_id, text, status, created_at, handled_at"


def normalize(raw: str) -> str:
    """NFC, all whitespace runs -> one space, trimmed. Control characters are refused."""
    text = " ".join(unicodedata.normalize("NFC", raw or "").split())
    if not text:
        raise EmptyMessage("empty message")
    if any(unicodedata.category(c).startswith("C") for c in text):
        raise BadMessage("message contains invalid characters")
    if len(text) > MAX_LEN:
        raise MessageTooLong(f"at most {MAX_LEN} characters")
    return text


def send(conn: psycopg.Connection, user_id: UUID, raw_text: str) -> HostMessage:
    ensure_autocommit(conn)
    text = normalize(raw_text)
    row = conn.execute("SELECT u.session_id, s.status, s.game_type FROM users u JOIN game_sessions s ON s.id = u.session_id "
                       "WHERE u.id = %s", (user_id,)).fetchone()
    if row is None or row[2] != "falacias":
        raise NotAFalaciasPlayer("not a player of a falacias session")
    session_id = row[0]
    if row[1] != "active":
        raise SessionNotActive("the session has ended")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"host-message:{user_id}",))   # one player at a time
        status = conn.execute("SELECT status FROM game_sessions WHERE id = %s FOR SHARE", (session_id,)).fetchone()
        if status is None or status[0] != "active":
            raise SessionNotActive("the session has ended")
        if RATE_LIMIT_S > 0 and conn.execute(
                "SELECT 1 FROM host_messages WHERE user_id = %s AND created_at > clock_timestamp() - make_interval(secs => %s)",
                (user_id, RATE_LIMIT_S)).fetchone():
            raise MessageRateLimited(f"wait {RATE_LIMIT_S} s between messages")
        if conn.execute("SELECT count(*) FROM host_messages WHERE user_id = %s", (user_id,)).fetchone()[0] >= MAX_PER_PLAYER:
            raise MessageLimitReached(f"at most {MAX_PER_PLAYER} messages per game")
        with conn.cursor(row_factory=class_row(HostMessage)) as cur:
            cur.execute(f"INSERT INTO host_messages (session_id, user_id, text, created_at) "
                        f"VALUES (%s, %s, %s, clock_timestamp()) RETURNING {_COLS}", (session_id, user_id, text))
            return cur.fetchone()


def sent_count(conn: psycopg.Connection, user_id: UUID) -> int:
    return conn.execute("SELECT count(*) FROM host_messages WHERE user_id = %s", (user_id,)).fetchone()[0]


def list_messages(conn: psycopg.Connection, session_id: UUID, status: str | None = None) -> list[MessageRow]:
    """Oldest first (the admin answers in order). `status` filters; None = all."""
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with conn.cursor(row_factory=class_row(MessageRow)) as cur:
        cur.execute("SELECT m.id, m.user_id, u.name, m.text, m.status, m.created_at, m.handled_at FROM host_messages m "
                    "JOIN users u ON u.id = m.user_id WHERE m.session_id = %s AND (%s::text IS NULL OR m.status = %s) "
                    "ORDER BY m.created_at, m.id", (session_id, status, status))
        return cur.fetchall()


def count_new(conn: psycopg.Connection, session_id: UUID) -> int:
    return conn.execute("SELECT count(*) FROM host_messages WHERE session_id = %s AND status = 'new'",
                        (session_id,)).fetchone()[0]


def handle(conn: psycopg.Connection, session_id: UUID, message_id: UUID, status: str) -> HostMessage:
    """'done' (relayed to the host), 'hidden', or back to 'new'. Allowed after the session ended too."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    ensure_autocommit(conn)
    with conn.cursor(row_factory=class_row(HostMessage)) as cur:
        cur.execute(f"UPDATE host_messages SET status = %s, handled_at = CASE WHEN %s = 'new' THEN NULL ELSE now() END "
                    f"WHERE id = %s AND session_id = %s RETURNING {_COLS}", (status, status, message_id, session_id))
        m = cur.fetchone()
    if m is None:
        raise MessageNotFound("no such message in this session")
    return m
