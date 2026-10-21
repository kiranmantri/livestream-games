"""Task 7: marking service (toggle, only while the session is active)."""
from __future__ import annotations

import random
import threading

import psycopg
import pytest

from app import cards as C
from app import marks as M
from app import scoring as G
from app import sessions as S
from app.db import connect
from tests.test_cards import load_phrases


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


@pytest.fixture
def me(conn):
    load_phrases(conn)
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    card = C.assign_card(conn, s.id, p.id, random.Random(1))
    return s, p, card


def test_toggle_marks_then_unmarks(conn, me):
    _s, p, card = me
    assert M.toggle_mark(conn, p.id, 3) is True
    assert M.marked_cells(conn, card.id) == {3}
    assert M.toggle_mark(conn, p.id, 3) is False
    assert M.marked_cells(conn, card.id) == set()


def test_marks_are_committed_and_visible_elsewhere(conn, me, test_db_url):
    _, p, _card = me
    M.toggle_mark(conn, p.id, 0)
    M.toggle_mark(conn, p.id, 24)
    with psycopg.connect(test_db_url) as other:
        rows = other.execute("SELECT cell_index FROM bingo_marks ORDER BY 1").fetchall()
    assert rows == [(0,), (24,)]


@pytest.mark.parametrize("bad", [12, -1, 25, 99, True, "3", None, 3.0])
def test_invalid_cells_rejected(conn, me, bad):
    _, p, card = me
    with pytest.raises(M.InvalidCell):
        M.toggle_mark(conn, p.id, bad)
    assert M.marked_cells(conn, card.id) == set()


def test_cannot_mark_after_session_ended(conn, me):
    s, p, card = me
    M.toggle_mark(conn, p.id, 1)
    G.finish_session(conn, s.id)
    with pytest.raises(M.SessionNotActive):
        M.toggle_mark(conn, p.id, 2)
    with pytest.raises(M.SessionNotActive):
        M.toggle_mark(conn, p.id, 1)            # unmarking is frozen too
    assert M.marked_cells(conn, card.id) == {1}


def test_player_without_card_cannot_mark(conn):
    load_phrases(conn)
    _, p, _ = S.start_session(conn, "Ana", "10.0.0.1")        # no assign_card
    with pytest.raises(M.SessionNotActive):
        M.toggle_mark(conn, p.id, 1)


def test_marks_only_affect_own_card(conn, me):
    s, ana, card_a = me
    _, bia, _ = S.join_session(conn, "Bia", "10.0.0.2")
    card_b = C.assign_card(conn, s.id, bia.id, random.Random(2))
    M.toggle_mark(conn, ana.id, 5)
    assert M.marked_cells(conn, card_a.id) == {5} and M.marked_cells(conn, card_b.id) == set()


def test_requires_autocommit(test_db_url):
    import uuid
    with psycopg.connect(test_db_url) as bad, pytest.raises(RuntimeError, match="autocommit"):
        M.toggle_mark(bad, uuid.uuid4(), 1)


def test_marks_racing_finish_are_either_counted_or_rejected(conn, me, test_db_url):
    """No mark may sneak in after the ranking was computed: final score == marks in the DB."""
    s, p, card = me
    outcomes, barrier = [], threading.Barrier(2)

    def marker():
        with connect(test_db_url) as c:
            barrier.wait()
            for i in range(24):
                idx = [x for x in range(25) if x != 12][i]
                try:
                    M.toggle_mark(c, p.id, idx)
                    outcomes.append("ok")
                except M.SessionNotActive:
                    outcomes.append("rejected")

    def finisher():
        with connect(test_db_url) as c:
            barrier.wait()
            outcomes.append(G.finish_session(c, s.id))

    ts = [threading.Thread(target=marker), threading.Thread(target=finisher)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    result = next(o for o in outcomes if not isinstance(o, str))
    in_db = conn.execute("SELECT count(*) FROM bingo_marks WHERE card_id=%s", (card.id,)).fetchone()[0]
    assert result.standings[0].marked == in_db == outcomes.count("ok")
