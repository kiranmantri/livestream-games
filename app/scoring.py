"""Scoring: the player with the MOST marked phrase cells wins; the admin ends the session.

Marks are honour-based. The free avatar cell of a 5x5 card is never stored: max 16 marks on a 4x4 card, 24 on a 5x5.
Tie-break: whoever reached their final count first (earliest last mark), then earliest joined.
Nobody marked anything -> no winner.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import class_row

from app import phrases
from app.db import ensure_autocommit


@dataclass(frozen=True)
class Standing:
    rank: int
    user_id: uuid.UUID
    name: str
    marked: int
    last_mark: datetime | None


@dataclass(frozen=True)
class FinishResult:
    session_id: uuid.UUID
    winner: Standing | None
    standings: list[Standing]


_LEADERBOARD_SQL = """
SELECT row_number() OVER (ORDER BY count(m.cell_index) DESC, max(m.marked_at) ASC NULLS LAST,
                                   u.created_at ASC, u.id) AS rank,
       u.id AS user_id, u.name, count(m.cell_index)::int AS marked, max(m.marked_at) AS last_mark
FROM users u
LEFT JOIN bingo_cards c ON c.user_id = u.id AND c.session_id = u.session_id
LEFT JOIN bingo_marks m ON m.card_id = c.id
WHERE u.session_id = %s
GROUP BY u.id, u.name, u.created_at
ORDER BY rank
"""


def leaderboard(conn: psycopg.Connection, session_id: uuid.UUID) -> list[Standing]:
    with conn.cursor(row_factory=class_row(Standing)) as cur:
        cur.execute(_LEADERBOARD_SQL, (session_id,))  # type: ignore[arg-type]
        return cur.fetchall()


def finish_session(conn: psycopg.Connection, session_id: uuid.UUID) -> FinishResult | None:
    """Admin action: end the active session and record the winner (most marked cells).
    Returns None if the session is not active (already finished / unknown), so a double click or two
    admins can never end it twice or overwrite the winner."""
    ensure_autocommit(conn)
    with conn.transaction():
        locked = conn.execute(
            "SELECT 1 FROM game_sessions WHERE id = %s AND status = 'active' FOR UPDATE",
            (session_id,)).fetchone()
        if locked is None:
            return None
        standings = leaderboard(conn, session_id)
        winner = standings[0] if standings and standings[0].marked > 0 else None
        conn.execute(
            "UPDATE game_sessions SET status = 'ended', ended_at = now(), winner_user_id = %s "
            "WHERE id = %s", (winner.user_id if winner else None, session_id))
        phrases.apply_session_stats(conn, session_id)     # same transaction: once per session, atomic
    return FinishResult(session_id, winner, standings)
