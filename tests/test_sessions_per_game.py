"""Task 1c: one active session PER GAME (bingo, falacias); one cookie per game."""
from __future__ import annotations

import threading

import psycopg
import pytest
from psycopg import errors

from app import admin as A
from app import sessions as S
from app.db import connect


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


@pytest.fixture(autouse=True)
def _ip_limit(monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "3")


# ---------------------------------------------------------------- the rule in the DB
def test_game_type_only_accepts_known_games(conn):
    with pytest.raises(errors.CheckViolation):
        conn.execute("INSERT INTO game_sessions (game_type, created_by) VALUES ('poker', NULL)")


def test_db_allows_one_active_session_per_game_but_not_two_of_the_same(conn):
    conn.execute("INSERT INTO game_sessions (game_type, created_by) VALUES ('bingo', NULL)")
    conn.execute("INSERT INTO game_sessions (game_type, created_by) VALUES ('falacias', NULL)")   # different game: fine
    for game in ("bingo", "falacias"):
        with pytest.raises(errors.UniqueViolation) as e:
            conn.execute("INSERT INTO game_sessions (game_type, created_by) VALUES (%s, NULL)", (game,))
        assert e.value.diag.constraint_name == "one_active_session_per_game"


def test_a_game_can_start_again_after_its_session_ended(conn):
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1", game_type="falacias")
    S.end_session(conn, s.id, p.id)
    S.start_session(conn, "Bia", "10.0.0.2", game_type="falacias")          # must not raise


# ---------------------------------------------------------------- services
def test_both_games_can_be_active_at_the_same_time(conn):
    b, _, _ = S.start_session(conn, "Ana", "10.0.0.1")                      # default game = bingo
    f, _, _ = S.start_session(conn, "Bia", "10.0.0.2", game_type="falacias")
    assert (b.game_type, f.game_type) == ("bingo", "falacias") and b.id != f.id
    assert S.get_active_session(conn).id == b.id                            # default = bingo (existing callers unchanged)
    assert S.get_active_session(conn, "bingo").id == b.id
    assert S.get_active_session(conn, "falacias").id == f.id


def test_second_start_of_the_same_game_is_refused_but_the_other_game_is_unaffected(conn):
    S.start_session(conn, "Ana", "10.0.0.1", game_type="falacias")
    with pytest.raises(S.SessionAlreadyActive):
        S.start_session(conn, "Bia", "10.0.0.2", game_type="falacias")
    with pytest.raises(S.SessionAlreadyActive):
        S.start_session_as_admin(conn, game_type="falacias")
    S.start_session(conn, "Caio", "10.0.0.3")                               # bingo is free
    with pytest.raises(S.SessionAlreadyActive):
        S.start_session_as_admin(conn)


def test_admin_start_per_game_has_no_creator(conn):
    f = S.start_session_as_admin(conn, game_type="falacias")
    assert f.game_type == "falacias" and f.created_by is None and f.status == "active"
    assert S.get_active_session(conn) is None                                # bingo untouched


def test_unknown_game_is_rejected_by_every_entry_point(conn):
    for call in (lambda: S.start_session(conn, "Ana", "10.0.0.1", game_type="poker"),
                 lambda: S.start_session_as_admin(conn, game_type="poker"),
                 lambda: S.get_active_session(conn, "poker"),
                 lambda: S.join_session(conn, "Ana", "10.0.0.1", game_type="poker"),
                 lambda: S.resume(conn, "x", "poker"),
                 lambda: S.cookie_name("poker")):
        with pytest.raises(ValueError, match="unknown game"):
            call()


def test_join_goes_to_the_requested_game_only(conn):
    S.start_session(conn, "Ana", "10.0.0.1")                                 # only bingo is running
    with pytest.raises(S.NoActiveSession):
        S.join_session(conn, "Bia", "10.0.0.2", game_type="falacias")
    S.start_session_as_admin(conn, game_type="falacias")
    s, _p, _ = S.join_session(conn, "Bia", "10.0.0.2", game_type="falacias")
    assert s.game_type == "falacias" and S.count_players(conn, s.id) == 1
    assert S.count_players(conn, S.get_active_session(conn).id) == 1


def test_ending_one_game_does_not_end_the_other(conn):
    b, pb, _ = S.start_session(conn, "Ana", "10.0.0.1")
    f, _, _ = S.start_session(conn, "Bia", "10.0.0.2", game_type="falacias")
    assert S.end_session(conn, f.id) is True
    assert S.get_active_session(conn, "falacias") is None and S.get_active_session(conn).id == b.id
    assert S.end_session(conn, b.id, pb.id) is True


def test_a_cookie_only_identifies_the_player_inside_its_own_game(conn):
    _, pb, tb = S.start_session(conn, "Ana", "10.0.0.1")
    S.start_session_as_admin(conn, game_type="falacias")
    assert S.resume(conn, tb).id == pb.id
    assert S.resume(conn, tb, "bingo").id == pb.id
    assert S.resume(conn, tb, "falacias") is None                            # a bingo token is not a falacias player
    assert S.get_player_by_token(conn, tb, "falacias") is None


def test_cookie_names_are_one_per_game_and_bingo_keeps_the_old_name(conn):
    assert S.cookie_name("bingo") == S.COOKIE_NAME == "lg_token"
    assert S.cookie_name("falacias") == "lg_token_falacias"


def test_joining_with_a_valid_cookie_resumes_instead_of_creating_a_second_player(conn):
    S.start_session_as_admin(conn, game_type="falacias")
    s, p, t = S.join_session(conn, "Ana", "10.0.0.1", game_type="falacias")
    s2, p2, t2 = S.join_session(conn, "Other", "10.0.0.1", existing_token=t, game_type="falacias")
    assert (s2.id, p2.id, t2) == (s.id, p.id, t) and S.count_players(conn, s.id) == 1


# ---------------------------------------------------------------- admin view per game
def test_admin_current_session_is_per_game(conn):
    assert A.current_session(conn) is None and A.current_session(conn, "falacias") is None
    b, pb, _ = S.start_session(conn, "Ana", "10.0.0.1")
    f, _, _ = S.start_session(conn, "Bia", "10.0.0.2", game_type="falacias")
    assert A.current_session(conn).id == b.id and A.current_session(conn, "falacias").id == f.id
    S.end_session(conn, b.id, pb.id)
    assert A.current_session(conn).id == b.id and A.current_session(conn).status == "ended"   # ended one stays visible
    assert A.current_session(conn, "falacias").status == "active"


# ---------------------------------------------------------------- races
def _parallel(url, fns):
    barrier, out = threading.Barrier(len(fns)), [None] * len(fns)

    def worker(i, fn):
        with connect(url) as c:
            barrier.wait()
            try:
                out[i] = ("ok", fn(c))
            except S.SessionError as e:
                out[i] = ("err", e.code)

    ts = [threading.Thread(target=worker, args=(i, f)) for i, f in enumerate(fns)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    return out


def test_concurrent_start_of_the_same_game_exactly_one_wins_each_game(conn, test_db_url):
    fns = ([lambda c, n=n: S.start_session(c, f"b{n}", f"10.0.1.{n}") for n in range(5)]
           + [lambda c, n=n: S.start_session(c, f"f{n}", f"10.0.2.{n}", game_type="falacias") for n in range(5)])
    res = _parallel(test_db_url, fns)
    assert [r[0] for r in res[:5]].count("ok") == 1 and [r[0] for r in res[5:]].count("ok") == 1
    assert all(r[1] == "session_already_active" for r in res if r[0] == "err")
    assert sorted(r[0] for r in conn.execute("SELECT game_type FROM game_sessions")) == ["bingo", "falacias"]


def test_rows_are_durable_from_another_connection(conn, test_db_url):
    S.start_session(conn, "Ana", "10.0.0.1")
    S.start_session(conn, "Bia", "10.0.0.2", game_type="falacias")
    with psycopg.connect(test_db_url) as other:
        assert sorted(r[0] for r in other.execute("SELECT game_type FROM game_sessions")) == ["bingo", "falacias"]
