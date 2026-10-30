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

from app import swaps
from app.cards import MAX_CELLS, PHRASE_CELLS, is_phrase_cell, max_marks
from app.db import ensure_autocommit


class MarkError(Exception):
    code = "error"


class InvalidCell(MarkError):
    code = "invalid_cell"


class SessionNotActive(MarkError):
    code = "session_not_active"


class CellLocked(MarkError):
    """The admin confirmed this mark as correct: the player cannot unmark it any more."""
    code = "cell_locked"


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
        mark = conn.execute("SELECT confirmed_at FROM bingo_marks WHERE card_id = %s AND cell_index = %s",
                            (card_id, cell_index)).fetchone()
        if mark is not None:
            if mark[0] is not None:
                raise CellLocked(f"cell {cell_index!r} was confirmed by the admin")
            conn.execute("DELETE FROM bingo_marks WHERE card_id = %s AND cell_index = %s", (card_id, cell_index))
            return False
        # a phrase the admin already confirmed: the new mark is confirmed (locked) at once, and may earn a card swap
        said = conn.execute("SELECT 1 FROM confirmed_phrases p JOIN bingo_cards c ON c.session_id = p.session_id "
                            "WHERE c.id = %s AND p.phrase_id = %s", (card_id, cells[cell_index]["phrase_id"])).fetchone()
        conn.execute(
            "INSERT INTO bingo_marks (card_id, cell_index, confirmed_at) VALUES (%s, %s, CASE WHEN %s THEN now() END) "
            "ON CONFLICT DO NOTHING", (card_id, cell_index, said is not None))
        if said:
            swaps.grant(conn, conn.execute("SELECT session_id FROM bingo_cards WHERE id = %s", (card_id,)).fetchone()[0],
                        card_id, cell_index)
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


# every mark of a session with the phrase of its cell
_MARKS = ("SELECT m.card_id, m.cell_index, (c.cells->m.cell_index->>'phrase_id')::bigint AS phrase_id, "
          "c.cells->m.cell_index->>'text' AS text, c.user_id, m.marked_at, m.confirmed_at "
          "FROM bingo_marks m JOIN bingo_cards c ON c.id = m.card_id WHERE c.session_id = %s")


def _lock_active(conn: psycopg.Connection, session_id: uuid.UUID) -> bool:
    return conn.execute("SELECT 1 FROM game_sessions WHERE id = %s AND status = 'active' FOR SHARE",
                        (session_id,)).fetchone() is not None


def confirm_phrase(conn: psycopg.Connection, session_id: uuid.UUID, phrase_id: int) -> int:
    """ADMIN: the phrase WAS said -> every player's mark of it is confirmed (locked: they can no longer unmark it), and so is
    every later mark of it (toggle_mark). Confirmed keyword marks earn their players a card swap. Only while the session is
    active. Returns how many marks became confirmed."""
    ensure_autocommit(conn)
    with conn.transaction():
        if not _lock_active(conn, session_id):
            return 0
        conn.execute("INSERT INTO confirmed_phrases (session_id, phrase_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                     (session_id, phrase_id))
        n = conn.execute(
            f"UPDATE bingo_marks b SET confirmed_at = now() FROM ({_MARKS}) x "
            "WHERE b.card_id = x.card_id AND b.cell_index = x.cell_index AND x.phrase_id = %s AND b.confirmed_at IS NULL",
            (session_id, phrase_id)).rowcount
        swaps.grant(conn, session_id)                             # a secret keyword: its players earn a card swap
        return n


def reject_phrase(conn: psycopg.Connection, session_id: uuid.UUID, phrase_id: int) -> int:
    """ADMIN: the phrase was NOT said -> every player's mark of it is cleared (also confirmed ones; the phrase is no longer
    confirmed). NOT blocked: players may mark it again later (pending again). Unused swaps earned by those marks go away.
    Returns how many marks were removed."""
    ensure_autocommit(conn)
    with conn.transaction():
        if not _lock_active(conn, session_id):
            return 0
        conn.execute("DELETE FROM confirmed_phrases WHERE session_id = %s AND phrase_id = %s", (session_id, phrase_id))
        gone = conn.execute(f"SELECT card_id, cell_index FROM ({_MARKS}) x WHERE x.phrase_id = %s",
                            (session_id, phrase_id)).fetchall()
        for card_id, idx in gone:
            swaps.revoke_for_mark(conn, card_id, idx)
            conn.execute("DELETE FROM bingo_marks WHERE card_id = %s AND cell_index = %s", (card_id, idx))
        return len(gone)


def _marked_phrase(conn: psycopg.Connection, session_id: uuid.UUID, user_id: uuid.UUID, cell_index: int) -> int | None:
    """The phrase of that player's cell when the cell is MARKED in the active session, else None."""
    card_id = _admin_card(conn, session_id, user_id, cell_index)
    if card_id is None:
        return None
    row = conn.execute("SELECT (c.cells->%s->>'phrase_id')::bigint FROM bingo_marks m JOIN bingo_cards c ON c.id = m.card_id "
                       "WHERE m.card_id = %s AND m.cell_index = %s", (cell_index, card_id, cell_index)).fetchone()
    return row[0] if row else None


def confirm_mark(conn: psycopg.Connection, session_id: uuid.UUID, user_id: uuid.UUID, cell_index: int) -> bool:
    """ADMIN, from one player's marked cell: confirm its PHRASE for everybody (confirm_phrase). True if any mark changed."""
    with conn.transaction():
        phrase = _marked_phrase(conn, session_id, user_id, cell_index)
    return phrase is not None and confirm_phrase(conn, session_id, phrase) > 0


def reject_mark(conn: psycopg.Connection, session_id: uuid.UUID, user_id: uuid.UUID, cell_index: int) -> bool:
    """ADMIN, from one player's marked cell: reject its PHRASE for everybody (reject_phrase). True if any mark was removed."""
    with conn.transaction():
        phrase = _marked_phrase(conn, session_id, user_id, cell_index)
    return phrase is not None and reject_phrase(conn, session_id, phrase) > 0


def confirmed_cells(conn: psycopg.Connection, card_id: uuid.UUID) -> frozenset[int]:
    return frozenset(r[0] for r in conn.execute(
        "SELECT cell_index FROM bingo_marks WHERE card_id = %s AND confirmed_at IS NOT NULL", (card_id,)))


@dataclass(frozen=True)
class Marker:
    user_id: uuid.UUID
    name: str
    cell_index: int
    marked_at: datetime


@dataclass(frozen=True)
class PhraseMarks:
    """One marked phrase of the session, for the admin's review list (one row per phrase, every player who marked it)."""
    phrase_id: int
    text: str
    confirmed: bool
    last_at: datetime
    who: list[Marker]


def phrase_marks(conn: psycopg.Connection, session_id: uuid.UUID) -> list[PhraseMarks]:
    """Every phrase with at least one mark: pending ones first, then confirmed; the most recently marked first inside each
    group. Players in the order they marked it."""
    rows = conn.execute(
        f"SELECT x.phrase_id, x.text, u.id, u.name, x.cell_index, x.marked_at, x.confirmed_at IS NOT NULL FROM ({_MARKS}) x "
        "JOIN users u ON u.id = x.user_id ORDER BY x.marked_at, u.name", (session_id,)).fetchall()
    by: dict[int, list] = {}
    for pid, text, uid, name, idx, at, ok in rows:
        by.setdefault(pid, [text, True, []])
        by[pid][1] = by[pid][1] and ok
        by[pid][2].append(Marker(uid, name, idx, at))
    out = [PhraseMarks(pid, text, ok, max(m.marked_at for m in who), who) for pid, (text, ok, who) in by.items()]
    return sorted(out, key=lambda p: (p.confirmed, -p.last_at.timestamp(), p.text))


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


__all__ = ["PHRASE_CELLS", "CellLocked", "CellRevoked", "InvalidCell", "MarkError", "Marker", "PhraseMarks", "PlayerBoard", "SessionNotActive", "confirm_mark", "confirm_phrase", "confirmed_cells", "lift_revoke", "marked_cells", "marked_times", "phrase_marks", "reject_mark", "reject_phrase", "revoke_mark", "revoked_cells", "session_boards", "toggle_mark"]
