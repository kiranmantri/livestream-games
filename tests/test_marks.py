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
    M.toggle_mark(conn, p.id, 15)
    with psycopg.connect(test_db_url) as other:
        rows = other.execute("SELECT cell_index FROM bingo_marks ORDER BY 1").fetchall()
    assert rows == [(0,), (15,)]


@pytest.mark.parametrize("bad", [-1, 16, 25, 99, True, "3", None, 3.0])     # a 4x4 card has cells 0..15
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
            for idx in range(16):
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


def test_cell_12_is_a_normal_cell_on_4x4(conn, me):
    _s, p, card = me
    assert M.toggle_mark(conn, p.id, 12) is True and M.marked_cells(conn, card.id) == {12}


def test_cell_12_is_the_avatar_on_a_legacy_5x5_card_and_is_never_markable(conn):
    load_phrases(conn)
    s, p, _ = S.start_session(conn, "Bia", "10.0.0.2")
    conn.execute("UPDATE game_sessions SET grid_size = 5 WHERE id=%s", (s.id,))
    card = C.assign_card(conn, s.id, p.id, random.Random(1))
    assert len(card.cells) == 25 and card.max_marks == 24
    for bad in (12, 25):
        with pytest.raises(M.InvalidCell):
            M.toggle_mark(conn, p.id, bad)
    assert M.toggle_mark(conn, p.id, 24) is True and M.toggle_mark(conn, p.id, 11) is True
    assert M.marked_cells(conn, card.id) == {11, 24}


def test_admin_revoke_removes_the_mark_and_blocks_the_cell_until_lifted(conn, me):
    s, p, card = me
    M.toggle_mark(conn, p.id, 3)
    M.toggle_mark(conn, p.id, 4)
    assert M.revoke_mark(conn, s.id, p.id, 3) is True                    # was marked -> now unmarked
    assert M.marked_cells(conn, card.id) == {4} and M.revoked_cells(conn, card.id) == {3}
    with pytest.raises(M.CellRevoked):
        M.toggle_mark(conn, p.id, 3)                                     # the player cannot just tap it again
    assert M.marked_cells(conn, card.id) == {4}
    assert M.revoke_mark(conn, s.id, p.id, 3) is False                   # already unmarked: nothing to remove
    assert M.lift_revoke(conn, s.id, p.id, 3) is True and M.lift_revoke(conn, s.id, p.id, 3) is False
    assert M.toggle_mark(conn, p.id, 3) is True                          # allowed again


def test_admin_revoke_can_block_an_unmarked_cell_and_ignores_bad_cells(conn, me):
    s, p, card = me
    assert M.revoke_mark(conn, s.id, p.id, 7) is False and M.revoked_cells(conn, card.id) == {7}
    for bad in (-1, 16, 99, True, None):
        assert M.revoke_mark(conn, s.id, p.id, bad) is False
    assert M.revoked_cells(conn, card.id) == {7}


def test_admin_cannot_revoke_after_the_game_ended_or_for_another_session(conn, me):
    import uuid
    s, p, card = me
    M.toggle_mark(conn, p.id, 3)
    assert M.revoke_mark(conn, uuid.uuid4(), p.id, 3) is False           # wrong session
    G.finish_session(conn, s.id)
    assert M.revoke_mark(conn, s.id, p.id, 3) is False                   # result is final
    assert M.marked_cells(conn, card.id) == {3}


def test_revoked_marks_are_durable_and_die_with_the_card(conn, me, test_db_url):
    s, p, _card = me
    M.revoke_mark(conn, s.id, p.id, 5)
    with psycopg.connect(test_db_url) as other:
        assert other.execute("SELECT cell_index FROM revoked_marks").fetchall() == [(5,)]
    conn.execute("DELETE FROM game_sessions WHERE id=%s", (s.id,))
    assert conn.execute("SELECT count(*) FROM revoked_marks").fetchone() == (0,)
