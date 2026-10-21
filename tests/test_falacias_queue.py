"""Task 5b: the admin's live-queue read model (F.queue_items, F.pending_count)."""
from __future__ import annotations

import pytest

from app import falacias as F
from app import sessions as S
from app.db import connect


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users CASCADE")
    c.execute("DELETE FROM fallacies")
    yield c
    c.execute("TRUNCATE game_sessions, users CASCADE")
    c.execute("DELETE FROM fallacies")
    c.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "50")
    monkeypatch.setattr(F, "RATE_LIMIT_S", 0)


@pytest.fixture
def world(conn):
    fid = conn.execute("INSERT INTO fallacies (name, category, description, source, enabled) "
                       "VALUES ('Espantalho','C','d','manual',true) RETURNING id").fetchone()[0]
    fid2 = conn.execute("INSERT INTO fallacies (name, category, description, source, enabled) "
                        "VALUES ('Ad hominem','C','d','manual',true) RETURNING id").fetchone()[0]
    _, ana, _ = S.start_session(conn, "Ana", "10.0.0.1", game_type="falacias")
    _, bia, _ = S.join_session(conn, "Bia", "10.0.0.2", game_type="falacias")
    sid = S.get_active_session(conn, "falacias").id
    ids = [F.add_event(conn, sid, "transcript", f"t{i}", f"linha {i}") for i in range(10)]
    chat = F.add_event(conn, sid, "chat", "c0", "msg do chat", author="@x")
    return {"sid": sid, "ana": ana, "bia": bia, "ids": ids, "chat": chat, "f1": fid, "f2": fid2}


def mark(conn, w, who, a, b, off=(0, 3), fallacy="f1", kind="transcript"):
    ids = w["ids"]
    return F.submit_mark(conn, w[who].id, kind, ids[a], off[0], ids[b], off[1], w[fallacy])


def test_pending_items_come_oldest_first_with_names(conn, world):
    m1 = mark(conn, world, "bia", 2, 2)
    m2 = mark(conn, world, "ana", 5, 5)
    items = F.queue_items(conn, world["sid"])
    assert [i.mark.id for i in items] == [m1.id, m2.id]
    assert (items[0].name, items[0].fallacy) == ("Bia", "Espantalho")
    assert [i.created_us for i in items] == sorted(i.created_us for i in items) and items[0].created_us > 10**15


def test_reviewed_pairs_leave_the_queue(conn, world):
    m1 = mark(conn, world, "bia", 2, 2)
    m2 = mark(conn, world, "ana", 5, 5)
    F.review_mark(conn, world["sid"], m1.id, "wrong")
    assert [i.mark.id for i in F.queue_items(conn, world["sid"])] == [m2.id]
    assert F.pending_count(conn, world["sid"]) == 1


def test_after_cursor_returns_only_newer_pairs(conn, world):
    mark(conn, world, "bia", 2, 2)
    first = F.queue_items(conn, world["sid"])[0].created_us
    m2 = mark(conn, world, "ana", 5, 5)
    assert [i.mark.id for i in F.queue_items(conn, world["sid"], after_us=first)] == [m2.id]
    assert F.queue_items(conn, world["sid"], after_us=F.queue_items(conn, world["sid"])[-1].created_us) == []


def test_context_is_three_lines_each_side_inside_the_same_pane(conn, world):
    mark(conn, world, "ana", 4, 5)                          # lines 4..5 selected
    it = F.queue_items(conn, world["sid"])[0]
    assert it.before == ["linha 1", "linha 2", "linha 3"] and it.selected == ["linha 4", "linha 5"]
    assert it.after == ["linha 6", "linha 7", "linha 8"]
    assert "msg do chat" not in it.before + it.selected + it.after


def test_context_is_shorter_at_the_edges_and_per_pane(conn, world):
    mark(conn, world, "ana", 0, 0)
    it = F.queue_items(conn, world["sid"])[0]
    assert it.before == [] and it.after == ["linha 1", "linha 2", "linha 3"]
    F.submit_mark(conn, world["bia"].id, "chat", world["chat"], 0, world["chat"], 3, world["f1"])
    chat = next(i for i in F.queue_items(conn, world["sid"]) if i.mark.kind == "chat")
    assert (chat.before, chat.after, chat.selected) == ([], [], ["msg do chat"])


def test_same_counts_identical_pending_pairs_of_any_player(conn, world):
    a = mark(conn, world, "ana", 3, 3)
    mark(conn, world, "bia", 3, 3)
    mark(conn, world, "ana", 3, 3, fallacy="f2")               # same passage, other fallacy
    mark(conn, world, "bia", 3, 3, off=(1, 4))                 # other offsets
    items = {i.mark.id: i for i in F.queue_items(conn, world["sid"])}
    assert items[a.id].same == 2
    assert sorted(i.same for i in items.values()) == [1, 1, 2, 2]


def test_same_ignores_already_reviewed_twins(conn, world):
    a = mark(conn, world, "ana", 3, 3)
    b = mark(conn, world, "bia", 3, 3)
    F.review_mark(conn, world["sid"], b.id, "correct")
    assert F.queue_items(conn, world["sid"])[0].same == 1 and F.queue_items(conn, world["sid"])[0].mark.id == a.id


def test_a_single_pair_can_be_fetched_whatever_its_status(conn, world):
    a = mark(conn, world, "ana", 3, 3)
    F.review_mark(conn, world["sid"], a.id, "spam")
    [it] = F.queue_items(conn, world["sid"], mark_id=a.id)
    assert it.mark.status == "spam" and it.same == 0


def test_only_the_given_session_and_never_other_sessions_pairs(conn, world):
    mark(conn, world, "ana", 3, 3)
    S.end_session(conn, world["sid"], None)
    S.start_session(conn, "Caio", "10.0.0.3", game_type="falacias")
    sid2 = S.get_active_session(conn, "falacias").id
    assert F.queue_items(conn, sid2) == [] and F.pending_count(conn, sid2) == 0
    assert len(F.queue_items(conn, world["sid"])) == 1


def test_limit(conn, world):
    for i in range(4):
        mark(conn, world, "ana", i, i)
    assert len(F.queue_items(conn, world["sid"], limit=3)) == 3
