"""Task 6b: winner = player with the most marked cells; admin finishes the session."""
from __future__ import annotations

import random
import threading
import uuid

import psycopg
import pytest

from app import cards as C
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




def mark(conn, card, idxs, secs=0):
    """Insert marks; `secs` offsets marked_at so tie-break order is deterministic."""
    for k, i in enumerate(idxs):
        conn.execute("INSERT INTO bingo_marks (card_id, cell_index, marked_at) "
                     "VALUES (%s, %s, timestamptz '2026-01-01 12:00:00+00' + make_interval(secs => %s))",
                     (card.id, i, secs + k))


CELLS = list(range(16))            # every cell of a 4x4 card is a phrase cell


@pytest.fixture
def game(conn):
    load_phrases(conn)
    s, ana, _ = S.start_session(conn, "Ana", "10.0.0.1")
    ca = C.assign_card(conn, s.id, ana.id, random.Random(1))
    _, bia, _ = S.join_session(conn, "Bia", "10.0.0.2")
    cb = C.assign_card(conn, s.id, bia.id, random.Random(2))
    _, caio, _ = S.join_session(conn, "Caio", "10.0.0.3")
    cc = C.assign_card(conn, s.id, caio.id, random.Random(3))
    return s, (ana, ca), (bia, cb), (caio, cc)


def test_ranking_by_marked_count(conn, game):
    s, (_ana, ca), (_bia, cb), (_caio, cc) = game
    mark(conn, ca, CELLS[:3])
    mark(conn, cb, CELLS[:10])
    mark(conn, cc, CELLS[:7])
    lb = G.leaderboard(conn, s.id)
    assert [(x.rank, x.name, x.marked) for x in lb] == [(1, "Bia", 10), (2, "Caio", 7), (3, "Ana", 3)]


def test_tie_goes_to_whoever_reached_the_count_first(conn, game):
    s, (_ana, ca), (_bia, cb), (_caio, cc) = game
    mark(conn, ca, CELLS[:5], secs=100)     # Ana's 5th mark at t+104
    mark(conn, cb, CELLS[:5], secs=10)      # Bia's 5th mark at t+14  -> reached 5 first
    mark(conn, cc, CELLS[:2])
    lb = G.leaderboard(conn, s.id)
    assert [x.name for x in lb] == ["Bia", "Ana", "Caio"]


def test_player_without_marks_or_card_counts_zero_and_is_listed(conn):
    load_phrases(conn)
    s, _ana, _ = S.start_session(conn, "Ana", "10.0.0.1")          # no card assigned at all
    lb = G.leaderboard(conn, s.id)
    assert [(x.name, x.marked, x.last_mark) for x in lb] == [("Ana", 0, None)]


def test_unmarking_lowers_the_score(conn, game):
    s, (_ana, ca), (_bia, cb), _ = game
    mark(conn, ca, CELLS[:6])
    mark(conn, cb, CELLS[:5])
    assert G.leaderboard(conn, s.id)[0].name == "Ana"
    conn.execute("DELETE FROM bingo_marks WHERE card_id=%s AND cell_index=%s", (ca.id, CELLS[0]))
    conn.execute("DELETE FROM bingo_marks WHERE card_id=%s AND cell_index=%s", (ca.id, CELLS[1]))
    assert G.leaderboard(conn, s.id)[0].name == "Bia"


def test_marks_of_a_previous_session_do_not_count(conn):
    load_phrases(conn)
    s1, ana1, _ = S.start_session(conn, "Ana", "10.0.0.1")
    c1 = C.assign_card(conn, s1.id, ana1.id, random.Random(1))
    mark(conn, c1, CELLS[:9])
    G.finish_session(conn, s1.id)
    s2, ana2, _ = S.start_session(conn, "Ana", "10.0.0.1")
    C.assign_card(conn, s2.id, ana2.id, random.Random(1))
    assert [(x.name, x.marked) for x in G.leaderboard(conn, s2.id)] == [("Ana", 0)]


def test_finish_session_records_winner_and_ends(conn, test_db_url, game):
    s, (_ana, ca), (bia, cb), (_caio, _cc) = game
    mark(conn, ca, CELLS[:4])
    mark(conn, cb, CELLS[:12])
    res = G.finish_session(conn, s.id)
    assert res.winner.name == "Bia" and res.winner.marked == 12
    assert [x.name for x in res.standings] == ["Bia", "Ana", "Caio"]
    with psycopg.connect(test_db_url) as other:                      # really committed
        assert other.execute("SELECT status, winner_user_id, ended_at IS NOT NULL FROM game_sessions"
                             ).fetchone() == ("ended", bia.id, True)
    assert S.get_active_session(conn) is None


def test_finish_with_nobody_marking_has_no_winner(conn, game):
    s, *_ = game
    res = G.finish_session(conn, s.id)
    assert res.winner is None and len(res.standings) == 3
    row = conn.execute("SELECT status, winner_user_id FROM game_sessions WHERE id=%s", (s.id,)).fetchone()
    assert row == ("ended", None)


def test_finish_twice_is_a_noop_and_never_overwrites_the_winner(conn, game):
    s, (ana, ca), (_bia, cb), _ = game
    mark(conn, ca, CELLS[:3])
    first = G.finish_session(conn, s.id)
    mark(conn, cb, CELLS[:20])                                       # late marks must not change the result
    assert G.finish_session(conn, s.id) is None
    assert conn.execute("SELECT winner_user_id FROM game_sessions WHERE id=%s", (s.id,)).fetchone()[0] \
        == first.winner.user_id == ana.id


def test_finish_unknown_session_returns_none(conn):
    assert G.finish_session(conn, uuid.uuid4()) is None


def test_finish_refuses_non_autocommit_connection(test_db_url):
    with psycopg.connect(test_db_url) as bad, pytest.raises(RuntimeError, match="autocommit"):
        G.finish_session(bad, uuid.uuid4())


def test_concurrent_finish_exactly_one_wins(conn, test_db_url, game):
    s, (_ana, ca), *_ = game
    mark(conn, ca, CELLS[:3])
    barrier, out = threading.Barrier(6), []

    def worker():
        with connect(test_db_url) as c:
            barrier.wait()
            out.append(G.finish_session(c, s.id))

    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert len(out) == 6 and sum(r is not None for r in out) == 1
