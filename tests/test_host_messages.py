"""Task 5a: tests of the host-message service (app/host_messages.py): players send short texts to the live host."""
from __future__ import annotations

import threading
import uuid

import pytest

from app import falacias as F
from app import host_messages as H
from app import sessions as S
from app.db import connect


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users CASCADE")
    yield c
    c.execute("TRUNCATE game_sessions, users CASCADE")
    c.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "50")


@pytest.fixture
def free(monkeypatch):
    monkeypatch.setattr(H, "RATE_LIMIT_S", 0)


def one(conn, name="Ana"):
    _, p, _tok = S.start_session(conn, name, "10.0.0.1", game_type="falacias")
    return p


# ================================================================ send
def test_send_stores_a_trimmed_message_with_status_new(conn):
    p = one(conn)
    m = H.send(conn, p.id, "  Fala  do   convidado\n sobre a ciência ")
    assert m.text == "Fala do convidado sobre a ciência" and m.status == "new" and m.handled_at is None
    row = connect_row(conn)
    assert row == ("Fala do convidado sobre a ciência", "new")


def connect_row(conn):
    return conn.execute("SELECT text, status FROM host_messages").fetchone()


@pytest.mark.parametrize("bad", ["", "   ", "\n\t ", "\x00", "ok\x07bell"])
def test_empty_or_control_text_is_refused(conn, bad):
    p = one(conn)
    with pytest.raises(H.EmptyMessage if not bad.strip() else H.BadMessage):
        H.send(conn, p.id, bad)
    assert conn.execute("SELECT count(*) FROM host_messages").fetchone()[0] == 0


def test_length_limit_is_280_characters(conn):
    p = one(conn)
    assert H.send(conn, p.id, "a" * 280).text == "a" * 280
    with pytest.raises(H.MessageTooLong):
        H.send(conn, p.id, "a" * 281)


def test_the_limit_counts_characters_after_collapsing_whitespace(conn):
    p = one(conn)
    assert len(H.send(conn, p.id, "a " + " " * 400 + "b").text) == 3


def test_html_is_stored_as_plain_text(conn):
    p = one(conn)
    assert H.send(conn, p.id, "<script>alert(1)</script>").text == "<script>alert(1)</script>"


def test_rate_limit_one_message_per_30_seconds_per_player(conn):
    p = one(conn)
    H.send(conn, p.id, "primeira")
    with pytest.raises(H.MessageRateLimited):
        H.send(conn, p.id, "segunda")
    assert conn.execute("SELECT count(*) FROM host_messages").fetchone()[0] == 1
    conn.execute("UPDATE host_messages SET created_at = created_at - interval '31 seconds'")
    assert H.send(conn, p.id, "segunda").text == "segunda"


def test_rate_limit_is_per_player(conn):
    a = one(conn)
    _, b, _ = S.join_session(conn, "Bia", "10.0.0.2", game_type="falacias")
    H.send(conn, a.id, "da Ana")
    assert H.send(conn, b.id, "da Bia").text == "da Bia"


def test_at_most_five_messages_per_player_even_hidden_or_done(conn, free):
    p = one(conn)
    for i in range(5):
        H.send(conn, p.id, f"m{i}")
    with pytest.raises(H.MessageLimitReached):
        H.send(conn, p.id, "m5")
    sid = conn.execute("SELECT session_id FROM users WHERE id = %s", (p.id,)).fetchone()[0]
    mid = H.list_messages(conn, sid)[0].id
    H.handle(conn, sid, mid, "hidden")
    with pytest.raises(H.MessageLimitReached):
        H.send(conn, p.id, "m5")                      # hiding does not free a slot (anti-spam)


def test_limit_is_per_session(conn, free):
    p = one(conn)
    for i in range(5):
        H.send(conn, p.id, f"m{i}")
    S.end_session(conn, S.get_active_session(conn, "falacias").id, None)
    _, p2, _ = S.start_session(conn, "Ana", "10.0.0.1", game_type="falacias")
    assert H.send(conn, p2.id, "nova").text == "nova"


def test_only_falacias_players_can_send(conn):
    _, p, _ = S.start_session(conn, "Ana", "10.0.0.1")           # bingo
    with pytest.raises(F.NotAFalaciasPlayer):
        H.send(conn, p.id, "oi")
    with pytest.raises(F.NotAFalaciasPlayer):
        H.send(conn, uuid.uuid4(), "oi")


def test_no_messages_after_the_session_ended(conn):
    p = one(conn)
    S.end_session(conn, S.get_active_session(conn, "falacias").id, None)
    with pytest.raises(F.SessionNotActive):
        H.send(conn, p.id, "tarde demais")
    assert conn.execute("SELECT count(*) FROM host_messages").fetchone()[0] == 0


def test_concurrent_sends_of_one_player_store_exactly_one(conn, test_db_url):
    p = one(conn)
    results = []

    def go():
        c = connect(test_db_url)
        try:
            H.send(c, p.id, "corrida")
            results.append("ok")
        except H.MessageRateLimited:
            results.append("limited")
        finally:
            c.close()
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["limited"] * 5 + ["ok"]
    assert conn.execute("SELECT count(*) FROM host_messages").fetchone()[0] == 1


def test_concurrent_sends_never_exceed_the_cap(conn, test_db_url, free):
    p = one(conn)
    results = []

    def go(i):
        c = connect(test_db_url)
        try:
            H.send(c, p.id, f"m{i}")
            results.append("ok")
        except H.MessageLimitReached:
            results.append("cap")
        finally:
            c.close()
    ts = [threading.Thread(target=go, args=(i,)) for i in range(9)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count("ok") == 5 and results.count("cap") == 4


# ================================================================ read + handle
def test_list_is_oldest_first_with_author_and_can_filter_by_status(conn, free):
    a = one(conn)
    _, b, _ = S.join_session(conn, "Bia", "10.0.0.2", game_type="falacias")
    sid = S.get_active_session(conn, "falacias").id
    H.send(conn, a.id, "um")
    H.send(conn, b.id, "dois")
    H.send(conn, a.id, "tres")
    msgs = H.list_messages(conn, sid)
    assert [(m.text, m.name) for m in msgs] == [("um", "Ana"), ("dois", "Bia"), ("tres", "Ana")]
    H.handle(conn, sid, msgs[1].id, "done")
    assert [m.text for m in H.list_messages(conn, sid, "new")] == ["um", "tres"]
    assert [m.text for m in H.list_messages(conn, sid, "done")] == ["dois"]
    assert H.count_new(conn, sid) == 2


def test_list_is_per_session(conn, free):
    a = one(conn)
    H.send(conn, a.id, "da sessao 1")
    s1 = S.get_active_session(conn, "falacias").id
    S.end_session(conn, s1, None)
    S.start_session(conn, "Bia", "10.0.0.2", game_type="falacias")
    s2 = S.get_active_session(conn, "falacias").id
    assert H.list_messages(conn, s2) == [] and len(H.list_messages(conn, s1)) == 1


def test_handle_sets_status_and_time_and_can_be_undone(conn):
    p = one(conn)
    sid = S.get_active_session(conn, "falacias").id
    m = H.send(conn, p.id, "oi")
    done = H.handle(conn, sid, m.id, "done")
    assert done.status == "done" and done.handled_at is not None
    assert H.handle(conn, sid, m.id, "hidden").status == "hidden"
    back = H.handle(conn, sid, m.id, "new")
    assert back.status == "new" and back.handled_at is None


def test_handle_rejects_unknown_status_and_foreign_messages(conn):
    p = one(conn)
    sid = S.get_active_session(conn, "falacias").id
    m = H.send(conn, p.id, "oi")
    with pytest.raises(ValueError):
        H.handle(conn, sid, m.id, "deleted")
    with pytest.raises(H.MessageNotFound):
        H.handle(conn, uuid.uuid4(), m.id, "done")             # another session id
    with pytest.raises(H.MessageNotFound):
        H.handle(conn, sid, uuid.uuid4(), "done")
    assert conn.execute("SELECT status FROM host_messages").fetchone()[0] == "new"


def test_sent_count_of_a_player(conn, free):
    p = one(conn)
    assert H.sent_count(conn, p.id) == 0
    H.send(conn, p.id, "a")
    H.send(conn, p.id, "b")
    assert H.sent_count(conn, p.id) == 2
