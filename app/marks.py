"""Marking: players tap phrase cells on their card (honour-based). Toggle on/off.

Marks are only accepted while the player's session is ACTIVE. The session row is locked FOR SHARE
during the write, so it cannot interleave with `scoring.finish_session` (which takes FOR UPDATE):
every mark is either fully counted in the final ranking or rejected.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg

from app.cards import CENTER_INDEX, PHRASE_CELLS
from app.db import ensure_autocommit


class MarkError(Exception):
    code = "error"


class InvalidCell(MarkError):
    code = "invalid_cell"


class SessionNotActive(MarkError):
    code = "session_not_active"


def toggle_mark(conn: psycopg.Connection, user_id: uuid.UUID, cell_index: int) -> bool:
    """Toggle a cell for the user's card in the ACTIVE session. Returns True if it is now marked."""
    ensure_autocommit(conn)
    if (not isinstance(cell_index, int) or isinstance(cell_index, bool)
            or not 0 <= cell_index <= 24 or cell_index == CENTER_INDEX):
        raise InvalidCell(f"cell {cell_index!r} cannot be marked")
    with conn.transaction():
        row = conn.execute(
            "SELECT c.id FROM bingo_cards c JOIN game_sessions s ON s.id = c.session_id "
            "WHERE c.user_id = %s AND s.status = 'active' FOR SHARE OF s", (user_id,)).fetchone()
        if row is None:
            raise SessionNotActive("no active session / card for this player")
        card_id = row[0]
        removed = conn.execute(
            "DELETE FROM bingo_marks WHERE card_id = %s AND cell_index = %s",
            (card_id, cell_index)).rowcount
        if removed:
            return False
        conn.execute(
            "INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING", (card_id, cell_index))
        return True


def marked_times(conn: psycopg.Connection, card_id: uuid.UUID) -> dict[int, datetime]:
    """cell_index -> when it was marked (set by the DB clock at insert; unmarking deletes the row)."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT cell_index, marked_at FROM bingo_marks WHERE card_id = %s", (card_id,))}


def marked_cells(conn: psycopg.Connection, card_id: uuid.UUID) -> set[int]:
    return {r[0] for r in conn.execute(
        "SELECT cell_index FROM bingo_marks WHERE card_id = %s", (card_id,))}


@dataclass(frozen=True)
class PlayerBoard:
    user_id: uuid.UUID
    name: str
    cells: list[dict]
    marked: dict[int, datetime]          # cell_index -> marked_at


def session_boards(conn: psycopg.Connection, session_id: uuid.UUID) -> list[PlayerBoard]:
    """Every dealt card of a session with its owner's name and marks, in join order (2 queries)."""
    rows = conn.execute(
        "SELECT u.id, u.name, c.cells FROM bingo_cards c JOIN users u ON u.id = c.user_id "
        "WHERE c.session_id = %s ORDER BY u.created_at, u.id", (session_id,)).fetchall()
    marks: dict[uuid.UUID, dict[int, datetime]] = {}
    for uid, idx, at in conn.execute(
            "SELECT c.user_id, m.cell_index, m.marked_at FROM bingo_marks m JOIN bingo_cards c ON c.id = m.card_id "
            "WHERE c.session_id = %s", (session_id,)):
        marks.setdefault(uid, {})[idx] = at
    return [PlayerBoard(uid, name, cells, marks.get(uid, {})) for uid, name, cells in rows]


__all__ = ["PHRASE_CELLS", "InvalidCell", "MarkError", "PlayerBoard", "SessionNotActive", "marked_cells", "marked_times", "session_boards", "toggle_mark"]
