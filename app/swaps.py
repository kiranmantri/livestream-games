"""Bingo secret keywords and card swaps.

The admin picks up to MAX_KEYWORDS keywords among the phrases on this game's dealt cards; only the admin sees them. A mark of a
keyword confirmed by the admin (marks.confirm_mark, or a keyword chosen after the confirmation) earns its player ONE swap
(swap_rights). Until the game ends the holder may exchange their card -- cells, marks (with their times), confirmations and
blocks -- with any other player whose CURRENT card does not have that keyword marked (pending or confirmed); the other player
cannot refuse. Or decline it. An Incorreto on the earning mark takes an unused swap back (marks.reject_mark). A keyword is
frozen (cannot be removed) while a confirmed mark of it exists.

Writes take the same FOR SHARE lock on the session as marking, so nothing interleaves with scoring.finish_session.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg

from app.db import ensure_autocommit

MAX_KEYWORDS = 3


class KeywordError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code            # not_active | not_on_cards | max | frozen


class SwapError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code            # not_active | not_yours | used | not_eligible


@dataclass(frozen=True)
class KeywordOption:
    phrase_id: int
    text: str
    cards: int                      # on how many of this game's cards


@dataclass(frozen=True)
class Keyword:
    phrase_id: int
    text: str
    frozen: bool                    # a confirmed mark of it exists: cannot be removed


@dataclass(frozen=True)
class Right:
    id: int
    session_id: uuid.UUID
    user_id: uuid.UUID
    phrase_id: int


@dataclass(frozen=True)
class Target:
    user_id: uuid.UUID
    name: str
    marked: int


@dataclass(frozen=True)
class RightLog:
    holder: str
    text: str
    earned_at: datetime
    used_at: datetime | None
    declined_at: datetime | None
    target: str | None


def _active(conn: psycopg.Connection, session_id: uuid.UUID) -> bool:
    return conn.execute("SELECT 1 FROM game_sessions WHERE id = %s AND status = 'active' FOR SHARE",
                        (session_id,)).fetchone() is not None


# phrase id / text of every phrase cell of the session's cards
_CELLS = ("SELECT c.id AS card_id, c.user_id, (e.ord - 1)::int AS idx, (e.cell->>'phrase_id')::bigint AS phrase_id, "
          "e.cell->>'text' AS text FROM bingo_cards c, jsonb_array_elements(c.cells) WITH ORDINALITY AS e(cell, ord) "
          "WHERE c.session_id = %s AND e.cell ? 'phrase_id'")


def keyword_options(conn: psycopg.Connection, session_id: uuid.UUID) -> list[KeywordOption]:
    return [KeywordOption(p, t, n) for p, t, n in conn.execute(
        f"SELECT phrase_id, min(text), count(DISTINCT card_id) FROM ({_CELLS}) x GROUP BY phrase_id ORDER BY lower(min(text))",
        (session_id,))]


def keywords(conn: psycopg.Connection, session_id: uuid.UUID) -> list[Keyword]:
    return [Keyword(p, t or "", bool(f)) for p, t, f in conn.execute(
        f"""SELECT k.phrase_id,
                   (SELECT min(text) FROM ({_CELLS}) x WHERE x.phrase_id = k.phrase_id),
                   EXISTS (SELECT 1 FROM ({_CELLS}) x JOIN bingo_marks m ON m.card_id = x.card_id AND m.cell_index = x.idx
                           WHERE x.phrase_id = k.phrase_id AND m.confirmed_at IS NOT NULL)
            FROM bingo_keywords k WHERE k.session_id = %s ORDER BY k.chosen_at, k.phrase_id""",
        (session_id, session_id, session_id))]


def grant(conn: psycopg.Connection, session_id: uuid.UUID, card_id: uuid.UUID | None = None,
          cell_index: int | None = None) -> int:
    """Give a swap to the owner of every CONFIRMED keyword mark that has none yet (one per player and keyword); limited to one
    mark when card_id/cell_index are given. Run inside the caller's transaction. Returns how many were granted."""
    only, extra = ("AND x.card_id = %s AND x.idx = %s", (card_id, cell_index)) if card_id is not None else ("", ())
    return conn.execute(
        f"""INSERT INTO swap_rights (session_id, user_id, phrase_id, card_id, cell_index)
            SELECT %s, x.user_id, x.phrase_id, x.card_id, x.idx FROM ({_CELLS}) x
            JOIN bingo_keywords k ON k.session_id = %s AND k.phrase_id = x.phrase_id
            JOIN bingo_marks m ON m.card_id = x.card_id AND m.cell_index = x.idx AND m.confirmed_at IS NOT NULL
            WHERE true {only}
            ON CONFLICT ON CONSTRAINT one_swap_per_keyword DO NOTHING""",
        (session_id, session_id, session_id, *extra)).rowcount


def revoke_for_mark(conn: psycopg.Connection, card_id: uuid.UUID, cell_index: int) -> int:
    """The mark that earned a swap was removed (Incorreto): an UNUSED swap goes away. Inside the caller's transaction."""
    return conn.execute("DELETE FROM swap_rights WHERE card_id = %s AND cell_index = %s AND used_at IS NULL "
                        "AND declined_at IS NULL", (card_id, cell_index)).rowcount


def add_keyword(conn: psycopg.Connection, session_id: uuid.UUID, phrase_id: int) -> None:
    ensure_autocommit(conn)
    with conn.transaction():
        if not _active(conn, session_id):
            raise KeywordError("not_active")
        if conn.execute(f"SELECT 1 FROM ({_CELLS}) x WHERE x.phrase_id = %s LIMIT 1", (session_id, phrase_id)).fetchone() is None:
            raise KeywordError("not_on_cards")
        if conn.execute("SELECT 1 FROM bingo_keywords WHERE session_id = %s AND phrase_id = %s",
                        (session_id, phrase_id)).fetchone():
            return
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"kw:{session_id}",))   # the count below
        n = conn.execute("SELECT count(*) FROM bingo_keywords WHERE session_id = %s", (session_id,)).fetchone()[0]
        if n >= MAX_KEYWORDS:
            raise KeywordError("max")
        conn.execute("INSERT INTO bingo_keywords (session_id, phrase_id) VALUES (%s, %s)", (session_id, phrase_id))
        grant(conn, session_id)                         # marks of it confirmed before it was chosen earn too


def remove_keyword(conn: psycopg.Connection, session_id: uuid.UUID, phrase_id: int) -> None:
    ensure_autocommit(conn)
    with conn.transaction():
        if not _active(conn, session_id):
            raise KeywordError("not_active")
        if any(k.frozen for k in keywords(conn, session_id) if k.phrase_id == phrase_id):
            raise KeywordError("frozen")
        conn.execute("DELETE FROM bingo_keywords WHERE session_id = %s AND phrase_id = %s", (session_id, phrase_id))


def open_right(conn: psycopg.Connection, user_id: uuid.UUID) -> Right | None:
    """The player's oldest swap not used nor declined, in a running game."""
    row = conn.execute(
        "SELECT r.id, r.session_id, r.user_id, r.phrase_id FROM swap_rights r JOIN game_sessions s ON s.id = r.session_id "
        "WHERE r.user_id = %s AND r.used_at IS NULL AND r.declined_at IS NULL AND s.status = 'active' "
        "ORDER BY r.earned_at, r.id LIMIT 1", (user_id,)).fetchone()
    return Right(*row) if row else None


_TARGETS = """SELECT u.id, u.name, (SELECT count(*) FROM bingo_marks m WHERE m.card_id = c.id)
    FROM users u JOIN bingo_cards c ON c.user_id = u.id AND c.session_id = u.session_id
    WHERE u.session_id = %s AND u.id <> %s
      AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(c.cells) WITH ORDINALITY AS e(cell, ord)
                      JOIN bingo_marks m ON m.card_id = c.id AND m.cell_index = e.ord - 1
                      WHERE (e.cell->>'phrase_id')::bigint = %s)"""


def targets(conn: psycopg.Connection, right: Right) -> list[Target]:
    """Who the holder may swap with: every other player whose current card does not have the keyword marked."""
    return [Target(*r) for r in conn.execute(_TARGETS + " ORDER BY u.created_at, u.id",
                                             (right.session_id, right.user_id, right.phrase_id))]


def _move_rows(conn: psycopg.Connection, table: str, cols: str, a: uuid.UUID, b: uuid.UUID) -> None:
    """Exchange the rows of `table` between cards a and b (PK card_id + cell_index: read, delete, re-insert)."""
    rows = conn.execute(f"SELECT card_id, cell_index, {cols} FROM {table} WHERE card_id IN (%s, %s)", (a, b)).fetchall()
    conn.execute(f"DELETE FROM {table} WHERE card_id IN (%s, %s)", (a, b))
    n = len(cols.split(","))
    for row in rows:
        conn.execute(f"INSERT INTO {table} (card_id, cell_index, {cols}) VALUES (%s, %s{', %s' * n})",
                     (b if row[0] == a else a, *row[1:]))


def use_swap(conn: psycopg.Connection, right_id: int, holder_id: uuid.UUID, target_id: uuid.UUID) -> None:
    """Exchange the holder's card with the target's: cells, marks (times kept), confirmations and blocks; the card rows (ids,
    owners) stay, their content moves. Unused swaps keep pointing at their earning mark (they follow it to the other card)."""
    ensure_autocommit(conn)
    with conn.transaction():
        row = conn.execute("SELECT session_id, user_id, phrase_id, used_at, declined_at FROM swap_rights WHERE id = %s",
                           (right_id,)).fetchone()
        if row is None or row[1] != holder_id:
            raise SwapError("not_yours")
        session_id, _, phrase_id, used, declined = row
        if not _active(conn, session_id):
            raise SwapError("not_active")
        if used or declined:
            raise SwapError("used")
        cards = dict(conn.execute("SELECT user_id, id FROM bingo_cards WHERE session_id = %s AND user_id IN (%s, %s) "
                                  "ORDER BY id FOR UPDATE", (session_id, holder_id, target_id)).fetchall())
        if target_id == holder_id or len(cards) != 2 or conn.execute(
                _TARGETS + " AND u.id = %s", (session_id, holder_id, phrase_id, target_id)).fetchone() is None:
            raise SwapError("not_eligible")
        a, b = cards[holder_id], cards[target_id]
        conn.execute("SET LOCAL livegames.card_swap = 'on'")
        conn.execute("UPDATE bingo_cards c SET cells = o.cells FROM bingo_cards o "
                     "WHERE (c.id, o.id) IN ((%s, %s), (%s, %s))", (a, b, b, a))
        conn.execute("SET LOCAL livegames.card_swap = 'off'")
        _move_rows(conn, "bingo_marks", "marked_at, confirmed_at", a, b)
        _move_rows(conn, "revoked_marks", "revoked_at", a, b)
        conn.execute("UPDATE swap_rights SET card_id = CASE card_id WHEN %s THEN %s::uuid ELSE %s::uuid END "
                     "WHERE card_id IN (%s, %s)", (a, b, a, a, b))
        conn.execute("UPDATE swap_rights SET used_at = now(), target_id = %s WHERE id = %s", (target_id, right_id))


def decline(conn: psycopg.Connection, right_id: int, holder_id: uuid.UUID) -> None:
    ensure_autocommit(conn)
    with conn.transaction():
        if conn.execute("UPDATE swap_rights SET declined_at = now() WHERE id = %s AND user_id = %s AND used_at IS NULL "
                        "AND declined_at IS NULL", (right_id, holder_id)).rowcount == 0:
            raise SwapError("used")


def last_swap(conn: psycopg.Connection, user_id: uuid.UUID) -> tuple[str, str, datetime] | None:
    """My latest swap, either side: ("made" | "received", the other player's name, when)."""
    row = conn.execute(
        "SELECT CASE WHEN r.user_id = %s THEN 'made' ELSE 'received' END, "
        "       (SELECT name FROM users WHERE id = CASE WHEN r.user_id = %s THEN r.target_id ELSE r.user_id END), r.used_at "
        "FROM swap_rights r WHERE r.used_at IS NOT NULL AND (r.user_id = %s OR r.target_id = %s) "
        "ORDER BY r.used_at DESC LIMIT 1", (user_id, user_id, user_id, user_id)).fetchone()
    return (row[0], row[1] or "?", row[2]) if row else None


def session_rights(conn: psycopg.Connection, session_id: uuid.UUID) -> list[RightLog]:
    """Every swap of the game, for the admin: who earned it with which keyword, and what became of it."""
    return [RightLog(*r) for r in conn.execute(
        f"""SELECT u.name, coalesce((SELECT min(text) FROM ({_CELLS}) x WHERE x.phrase_id = r.phrase_id), ''),
                   r.earned_at, r.used_at, r.declined_at, t.name
            FROM swap_rights r JOIN users u ON u.id = r.user_id LEFT JOIN users t ON t.id = r.target_id
            WHERE r.session_id = %s ORDER BY r.earned_at, r.id""", (session_id, session_id))]
