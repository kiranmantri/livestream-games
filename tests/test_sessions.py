"""Task 5: session + identity services."""
from __future__ import annotations

import threading

import psycopg
import pytest

from app import sessions as S
from app.db import connect


@pytest.fixture
def conn(test_db_url):
    """Service-layer connection: autocommit, like production (overrides the conftest fixture)."""
    c = connect(test_db_url)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


def test_changes_are_durable_and_visible_from_another_connection(conn, test_db_url):
    """Regression: writes must really be COMMITTED (not just visible on the writing connection)."""
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    _, _b, _ = S.join_session(conn, "Bia", "10.0.0.2")
    S.resume(conn, S.new_token())   # no-op path must not break anything
    with psycopg.connect(test_db_url) as other:
        assert other.execute("SELECT count(*) FROM users").fetchone()[0] == 2
        assert other.execute("SELECT status FROM game_sessions").fetchone()[0] == "active"
    assert S.end_session(conn, s.id, p.id) is True
    with psycopg.connect(test_db_url) as other:
        assert other.execute("SELECT status, winner_user_id FROM game_sessions").fetchone() == ("ended", p.id)


def test_services_refuse_non_autocommit_connections(test_db_url):
    """On a non-autocommit connection conn.transaction() degrades to a savepoint that is never
    committed (silent data loss), so the services must refuse it loudly."""
    import uuid
    with psycopg.connect(test_db_url) as bad:
        assert not bad.autocommit
        for call in (lambda: S.start_session(bad, "Ana", "10.0.0.1"),
                     lambda: S.join_session(bad, "Ana", "10.0.0.1"),
                     lambda: S.touch(bad, uuid.uuid4()),
                     lambda: S.end_session(bad, uuid.uuid4())):
            with pytest.raises(RuntimeError, match="autocommit"):
                call()


@pytest.fixture(autouse=True)
def _ip_limit(monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "2")


# ---------------------------------------------------------------- names
@pytest.mark.parametrize("raw,expected", [
    ("Ana", "Ana"), ("  Ana   Maria  ", "Ana Maria"), ("José", "José"),
    ("Jose\u0301", "José"),            # decomposed accent -> NFC
    ("x" * 20, "x" * 20),
])
def test_normalize_name_ok(raw, expected):
    assert S.normalize_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "x" * 21, "bad\x00name", "tab\u200bhidden\x07", None])
def test_normalize_name_invalid(raw):
    with pytest.raises(S.InvalidName):
        S.normalize_name(raw)


# ---------------------------------------------------------------- tokens / ip
def test_token_is_random_and_only_hash_is_stored(conn):
    _s, p, token = S.start_session(conn, "ana", "10.0.0.1")
    assert len(token) >= 40 and token != S.new_token()
    stored = conn.execute("SELECT token_hash FROM users WHERE id=%s", (p.id,)).fetchone()[0]
    assert stored == S.hash_token(token) and token not in stored


def test_normalize_ip():
    assert S.normalize_ip(" 192.168.0.1 ") == "192.168.0.1"
    assert S.normalize_ip("::1") == "::1"
    with pytest.raises(ValueError):
        S.normalize_ip("not-an-ip")


PROXIES = frozenset({"10.0.0.254", "10.0.0.253"})


@pytest.mark.parametrize("peer,xff,expected", [
    ("203.0.113.5", None, "203.0.113.5"),                               # direct
    ("203.0.113.5", "1.2.3.4", "203.0.113.5"),                          # untrusted peer: header ignored (spoof)
    ("10.0.0.254", "198.51.100.7", "198.51.100.7"),                     # trusted proxy
    ("10.0.0.254", "6.6.6.6, 198.51.100.7", "198.51.100.7"),            # client-supplied junk on the left ignored
    ("10.0.0.254", "198.51.100.7, 10.0.0.253", "198.51.100.7"),         # chain of proxies
    ("10.0.0.254", "garbage", "10.0.0.254"),                            # malformed -> fall back to peer
    ("10.0.0.254", None, "10.0.0.254"),
])
def test_client_ip(peer, xff, expected):
    assert S.client_ip(peer, xff, PROXIES) == expected


# ---------------------------------------------------------------- start
def test_no_active_session_initially(conn):
    assert S.get_active_session(conn) is None


def test_first_user_starts_session(conn):
    session, player, _token = S.start_session(conn, "Ana", "10.0.0.1")
    assert session.status == "active" and session.created_by == player.id
    assert player.name == "Ana" and player.ip == "10.0.0.1" and player.session_id == session.id
    assert S.get_active_session(conn).id == session.id
    assert S.count_players(conn, session.id) == 1


def test_cannot_start_second_session(conn):
    S.start_session(conn, "Ana", "10.0.0.1")
    with pytest.raises(S.SessionAlreadyActive):
        S.start_session(conn, "Bia", "10.0.0.2")
    assert conn.execute("SELECT count(*) FROM game_sessions").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1  # failed attempt left nothing behind


def test_start_validates_input(conn):
    with pytest.raises(S.InvalidName):
        S.start_session(conn, "", "10.0.0.1")
    with pytest.raises(ValueError):
        S.start_session(conn, "ana", "bogus")
    assert S.get_active_session(conn) is None


# ---------------------------------------------------------------- join
def test_join_requires_active_session(conn):
    with pytest.raises(S.NoActiveSession):
        S.join_session(conn, "Bia", "10.0.0.2")


def test_join_existing_session(conn):
    s, _, _ = S.start_session(conn, "Ana", "10.0.0.1")
    s2, bia, _token = S.join_session(conn, "Bia", "10.0.0.2")
    assert s2.id == s.id and bia.session_id == s.id and S.count_players(conn, s.id) == 2


@pytest.mark.parametrize("dup", ["ana", "ANA", "  Ana ", "aNa"])
def test_join_name_must_be_unique_case_insensitive(conn, dup):
    S.start_session(conn, "Ana", "10.0.0.1")
    with pytest.raises(S.NameTaken):
        S.join_session(conn, dup, "10.0.0.2")


def test_join_after_session_ended_fails_and_new_session_possible(conn):
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    assert S.end_session(conn, s.id, p.id) is True
    with pytest.raises(S.NoActiveSession):
        S.join_session(conn, "Bia", "10.0.0.2")
    s2, _p2, _ = S.start_session(conn, "Ana", "10.0.0.1")   # same name/IP reusable in a NEW session
    assert s2.id != s.id


# ---------------------------------------------------------------- IP limit
def test_ip_limit(conn):
    S.start_session(conn, "A", "10.0.0.1")
    S.join_session(conn, "B", "10.0.0.1")             # 2nd from same IP: allowed (limit 2)
    with pytest.raises(S.IpLimitReached):
        S.join_session(conn, "C", "10.0.0.1")          # 3rd: refused
    S.join_session(conn, "C", "10.0.0.2")              # other IP fine
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 3


def test_ip_limit_is_configurable(conn, monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "1")
    S.start_session(conn, "A", "10.0.0.1")
    with pytest.raises(S.IpLimitReached):
        S.join_session(conn, "B", "10.0.0.1")


def test_ip_limit_not_bypassed_by_ipv6_spelling(conn, monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "1")
    S.start_session(conn, "A", "2001:db8::1")
    with pytest.raises(S.IpLimitReached):
        S.join_session(conn, "B", "2001:0db8:0000:0000:0000:0000:0000:0001")


# ---------------------------------------------------------------- cookie resume
def test_resume_with_valid_token(conn):
    _s, p, token = S.start_session(conn, "Ana", "10.0.0.1")
    before = conn.execute("SELECT last_seen_at FROM users WHERE id=%s", (p.id,)).fetchone()[0]
    again = S.resume(conn, token)
    assert again.id == p.id
    after = conn.execute("SELECT last_seen_at FROM users WHERE id=%s", (p.id,)).fetchone()[0]
    assert after >= before


@pytest.mark.parametrize("bad", [None, "", "garbage", "x" * 43])
def test_resume_with_bad_token(conn, bad):
    S.start_session(conn, "Ana", "10.0.0.1")
    assert S.resume(conn, bad) is None


def test_join_with_valid_cookie_returns_same_player_not_new_one(conn):
    s, p, token = S.start_session(conn, "Ana", "10.0.0.1")
    _s2, p2, token2 = S.join_session(conn, "Another Name", "10.0.0.9", existing_token=token)
    assert p2.id == p.id and token2 == token and S.count_players(conn, s.id) == 1


def test_cookie_from_ended_session_is_ignored(conn):
    s, p, token = S.start_session(conn, "Ana", "10.0.0.1")
    S.end_session(conn, s.id)
    assert S.resume(conn, token) is None
    _s2, p2, _ = S.start_session(conn, "Ana", "10.0.0.1")
    assert p2.id != p.id


def test_stolen_ip_does_not_grant_identity(conn):
    """Another browser on the same IP without the cookie must NOT become the existing player."""
    _s, ana, _ = S.start_session(conn, "Ana", "10.0.0.1")
    assert S.resume(conn, None) is None
    _, other, _ = S.join_session(conn, "Other", "10.0.0.1")
    assert other.id != ana.id


# ---------------------------------------------------------------- end_session
def test_end_session_records_winner_and_is_idempotent(conn):
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    assert S.end_session(conn, s.id, p.id) is True
    assert S.end_session(conn, s.id, p.id) is False       # second declare loses
    row = conn.execute("SELECT status, winner_user_id, ended_at IS NOT NULL FROM game_sessions "
                       "WHERE id=%s", (s.id,)).fetchone()
    assert row == ("ended", p.id, True)
    assert S.get_active_session(conn) is None


# ---------------------------------------------------------------- concurrency
def test_join_rejected_if_session_ended_between_check_and_insert(conn, monkeypatch):
    """Simulates the race: join saw an active session, but it ended before the INSERT."""
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    stale = S.get_active_session(conn)
    S.end_session(conn, s.id, p.id)
    monkeypatch.setattr(S, "get_active_session", lambda c, game_type="bingo": stale)   # join still believes it's active
    with pytest.raises(S.NoActiveSession):
        S.join_session(conn, "Bia", "10.0.0.2")
    assert S.count_players(conn, s.id) == 1                          # nobody was added to the ended session


def _run_parallel(url, fns):
    barrier, results = threading.Barrier(len(fns)), [None] * len(fns)

    def worker(i, fn):
        with connect(url) as c:
            barrier.wait()
            try:
                results[i] = ("ok", fn(c))
            except S.SessionError as e:
                results[i] = ("err", e.code)

    ts = [threading.Thread(target=worker, args=(i, f)) for i, f in enumerate(fns)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    return results


def test_concurrent_start_exactly_one_wins(conn, test_db_url):
    res = _run_parallel(test_db_url, [lambda c, n=n: S.start_session(c, f"u{n}", f"10.0.1.{n}")
                                      for n in range(8)])
    assert [r[0] for r in res].count("ok") == 1
    assert all(r[1] == "session_already_active" for r in res if r[0] == "err")
    assert conn.execute("SELECT count(*) FROM game_sessions").fetchone()[0] == 1


def test_concurrent_same_name_exactly_one_wins(conn, test_db_url):
    S.start_session(conn, "host", "10.0.0.1")
    res = _run_parallel(test_db_url, [lambda c, n=n: S.join_session(c, "Same", f"10.0.2.{n}")
                                      for n in range(6)])
    assert [r[0] for r in res].count("ok") == 1
    assert all(r[1] == "name_taken" for r in res if r[0] == "err")


def test_concurrent_ip_limit_cannot_be_raced(conn, test_db_url):
    S.start_session(conn, "host", "10.0.0.1")
    res = _run_parallel(test_db_url, [lambda c, n=n: S.join_session(c, f"p{n}", "10.0.3.3")
                                      for n in range(6)])
    assert [r[0] for r in res].count("ok") == 2          # limit = 2
    assert all(r[1] == "ip_limit_reached" for r in res if r[0] == "err")


def test_concurrent_end_exactly_one_declares_win(conn, test_db_url):
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    res = _run_parallel(test_db_url, [lambda c: S.end_session(c, s.id, p.id) for _ in range(6)])
    assert sorted(r[1] for r in res) == [False] * 5 + [True]


# ---------------------------------------------------------------- started by the admin
def test_admin_start_creates_empty_active_session(conn, test_db_url):
    import psycopg
    s = S.start_session_as_admin(conn)
    assert s.status == "active" and s.created_by is None and S.count_players(conn, s.id) == 0
    assert S.get_active_session(conn).id == s.id
    with psycopg.connect(test_db_url) as other:                      # committed, visible elsewhere
        assert other.execute("SELECT count(*) FROM game_sessions WHERE created_by IS NULL").fetchone()[0] == 1


def test_admin_start_refused_while_one_is_active(conn):
    S.start_session(conn, "Ana", "10.0.0.1")
    with pytest.raises(S.SessionAlreadyActive):
        S.start_session_as_admin(conn)
    S.end_session(conn, S.get_active_session(conn).id, S.get_active_session(conn).created_by)
    S.start_session_as_admin(conn)                                   # fine once ended


def test_player_cannot_start_while_admin_session_active_but_can_join(conn):
    S.start_session_as_admin(conn)
    with pytest.raises(S.SessionAlreadyActive):
        S.start_session(conn, "Ana", "10.0.0.1")
    _, p, _ = S.join_session(conn, "Ana", "10.0.0.1")
    assert p.name == "Ana" and S.count_players(conn, p.session_id) == 1


def test_admin_start_race_exactly_one_wins(conn, test_db_url):
    import threading

    from app.db import connect
    results, barrier = [], threading.Barrier(4)

    def go():
        with connect(test_db_url) as c:
            barrier.wait()
            try:
                S.start_session_as_admin(c)
                results.append("ok")
            except S.SessionAlreadyActive:
                results.append("refused")

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert sorted(results) == ["ok", "refused", "refused", "refused"]


@pytest.mark.parametrize("header,expected", [
    ("198.51.100.7", "198.51.100.7"),            # platform header wins over the (internal) proxy peer
    (" 2001:db8::1 ", "2001:db8::1"),
    ("garbage", "10.9.9.9"),                       # invalid -> normal logic (peer)
    (None, "10.9.9.9"),                            # header absent / CLIENT_IP_HEADER unset
])
def test_client_ip_header(header, expected):
    assert S.client_ip("10.9.9.9", "1.2.3.4", PROXIES, client_ip_header=header) == expected
