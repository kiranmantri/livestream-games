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

from app.cards import MAX_CELLS, PHRASE_CELLS, is_phrase_cell, max_marks
from app.db import ensure_autocommit


class MarkError(Exception):
    code = "error"


class InvalidCell(MarkError):
    code = "invalid_cell"


class SessionNotActive(MarkError):
    code = "session_not_active"


class CellRevoked(MarkError):
    """The admin removed this mark (illegal marking): the player cannot mark the cell again."""
    code = "cell_revoked"


def toggle_mark(conn: psycopg.Connection, user_id: uuid.UUID, cell_index: int) -> bool:
    """Toggle a cell for the user's card in the ACTIVE session. Returns True if it is now marked."""
    ensure_autocommit(conn)
    if (not isinstance(cell_index, int) or isinstance(cell_index, bool)
            or not 0 <= cell_index < MAX_CELLS):
        raise InvalidCell(f"cell {cell_index!r} cannot be marked")
    with conn.transaction():
        row = conn.execute(
            "SELECT c.id, c.cells FROM bingo_cards c JOIN game_sessions s ON s.id = c.session_id "
            "WHERE c.user_id = %s AND s.status = 'active' FOR SHARE OF s", (user_id,)).fetchone()
        if row is None:
            raise SessionNotActive("no active session / card for this player")
        card_id, cells = row
        if not is_phrase_cell(cells, cell_index):          # beyond this card's size, or the avatar free cell
            raise InvalidCell(f"cell {cell_index!r} cannot be marked")
        if conn.execute("SELECT 1 FROM revoked_marks WHERE card_id = %s AND cell_index = %s",
                        (card_id, cell_index)).fetchone():
            raise CellRevoked(f"cell {cell_index!r} was unmarked by the admin")
        removed = conn.execute(
            "DELETE FROM bingo_marks WHERE card_id = %s AND cell_index = %s",
            (card_id, cell_index)).rowcount
        if removed:
            return False
        conn.execute(
            "INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING", (card_id, cell_index))
        return True


def _admin_card(conn: psycopg.Connection, session_id: uuid.UUID, user_id: uuid.UUID, cell_index: int):
    """(card_id) of the player's card when the session is ACTIVE and the cell is a phrase cell, else None.
    Takes the same FOR SHARE lock on the session as toggle_mark, so it cannot interleave with finish_session."""
    if not isinstance(cell_index, int) or isinstance(cell_index, bool) or not 0 <= cell_index < MAX_CELLS:
        return None
    row = conn.execute(
        "SELECT c.id, c.cells FROM bingo_cards c JOIN game_sessions s ON s.id = c.session_id "
        "WHERE c.user_id = %s AND s.id = %s AND s.status = 'active' FOR SHARE OF s", (user_id, session_id)).fetchone()
    return row[0] if row and is_phrase_cell(row[1], cell_index) else None


def revoke_mark(conn: psycopg.Connection, session_id: uuid.UUID, user_id: uuid.UUID, cell_index: int) -> bool:
    """ADMIN: remove the player's mark on a cell and block it from being marked again. Only while the session is
    active (a finished game's result is final). Returns True if the cell was marked (and is now unmarked)."""
    ensure_autocommit(conn)
    with conn.transaction():
        card_id = _admin_card(conn, session_id, user_id, cell_index)
        if card_id is None:
            return False
        conn.execute("INSERT INTO revoked_marks (card_id, cell_index) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                     (card_id, cell_index))
        return conn.execute("DELETE FROM bingo_marks WHERE card_id = %s AND cell_index = %s",
                            (card_id, cell_index)).rowcount > 0


def lift_revoke(conn: psycopg.Connection, session_id: uuid.UUID, user_id: uuid.UUID, cell_index: int) -> bool:
    """ADMIN: allow the player to mark the cell again. Returns True if a block was removed."""
    ensure_autocommit(conn)
    with conn.transaction():
        card_id = _admin_card(conn, session_id, user_id, cell_index)
        if card_id is None:
            return False
        return conn.execute("DELETE FROM revoked_marks WHERE card_id = %s AND cell_index = %s",
                            (card_id, cell_index)).rowcount > 0


def revoked_cells(conn: psycopg.Connection, card_id: uuid.UUID) -> frozenset[int]:
    return frozenset(r[0] for r in conn.execute(
        "SELECT cell_index FROM revoked_marks WHERE card_id = %s", (card_id,)))


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

    @property
    def max_marks(self) -> int:
        return max_marks(self.cells)


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


__all__ = ["PHRASE_CELLS", "CellRevoked", "InvalidCell", "MarkError", "PlayerBoard", "SessionNotActive", "lift_revoke", "marked_cells", "marked_times", "revoke_mark", "revoked_cells", "session_boards", "toggle_mark"]
