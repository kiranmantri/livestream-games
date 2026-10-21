"""Task 1b: schema (migration 0008) + services of "Encontre as Falácias" (app/falacias.py)."""
from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import errors

from app import falacias as F
from app import sessions as S
from app.db import connect

LONG = "Se Deus existe então por que existe sofrimento no mundo inteiro?"        # 64 chars, one chat/transcript line


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks, fallacies, replay_events CASCADE")
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks, fallacies, replay_events CASCADE")
    c.close()


@pytest.fixture(autouse=True)
def _ip_limit(monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "50")


def fallacy(conn, name="Ad hominem", enabled=True, category="Relevância"):
    return conn.execute("INSERT INTO fallacies (name, category, source, enabled) VALUES (%s,%s,'manual',%s) RETURNING id",
                        (name, category, enabled)).fetchone()[0]


def game(conn, players=("Ana",)):
    """An active falacias session with players; returns (session, [player ids])."""
    s = S.start_session_as_admin(conn, game_type="falacias")
    ids = [S.join_session(conn, n, f"10.0.0.{i + 1}", game_type="falacias")[1].id for i, n in enumerate(players)]
    return s, ids


def ev(conn, sid, text=LONG, kind="transcript", key=None, ago=0):
    key = key or str(conn.execute("SELECT count(*) FROM stream_events WHERE session_id = %s", (sid,)).fetchone()[0])
    return F.add_event(conn, sid, kind, key, text, fed_at=datetime.now(UTC) - timedelta(seconds=ago))


def age(conn, user_id, seconds=60):
    conn.execute("UPDATE fallacy_marks SET created_at = created_at - make_interval(secs => %s) WHERE user_id = %s",
                 (seconds, user_id))


def mark(conn, uid, e1, fid, s=0, e=10, e2=None):
    return F.submit_mark(conn, uid, "transcript", e1, s, e2 or e1, e, fid)


# ================================================================ scoring (pure)
@pytest.mark.parametrize("status,latency_ms,counts,expected", [
    ("pending", 0, True, 500), ("pending", 0, False, 0),
    ("wrong", 1000, True, 500), ("wrong", 1000, False, 0),
    ("spam", 0, True, 0), ("spam", 0, False, 0),
    ("correct", 0, True, 1600), ("correct", 10_000, True, 1500), ("correct", 30_000, True, 1300),
    ("correct", 60_000, True, 1000), ("correct", 90_000, True, 1000), ("correct", 12_345, True, 1477),
    ("correct", 0, False, 1100),                       # verdict + speed still count when the pair was over the cap
])
def test_points_table(status, latency_ms, counts, expected):
    assert F.points(status, latency_ms, counts) == expected


def test_points_rejects_unknown_status_and_negative_latency():
    with pytest.raises(ValueError):
        F.points("maybe", 0, True)
    with pytest.raises(ValueError):
        F.points("correct", -1, True)


def test_fmt_points_is_one_decimal_with_comma():
    assert F.fmt_points(1600) == "16,0" and F.fmt_points(977) == "9,8" and F.fmt_points(0) == "0,0"


# ================================================================ schema constraints
def test_migration_created_the_tables(conn):
    names = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert {"fallacies", "replay_events", "stream_events", "fallacy_marks", "host_messages"} <= names


def test_fallacy_rules(conn):
    fallacy(conn, "A")
    with pytest.raises(errors.UniqueViolation):
        fallacy(conn, "A")
    with pytest.raises(errors.CheckViolation):
        conn.execute("INSERT INTO fallacies (name, category, source) VALUES ('x','c','invented')")
    with pytest.raises(errors.CheckViolation):
        conn.execute("INSERT INTO fallacies (name, category, source) VALUES ('  ','c','manual')")


def test_stream_event_source_key_is_unique_per_session_and_kind(conn):
    s, _ = game(conn)
    conn.execute("INSERT INTO stream_events (session_id, kind, source_key, text) VALUES (%s,'chat','k','a')", (s.id,))
    with pytest.raises(errors.UniqueViolation):
        conn.execute("INSERT INTO stream_events (session_id, kind, source_key, text) VALUES (%s,'chat','k','b')", (s.id,))
    conn.execute("INSERT INTO stream_events (session_id, kind, source_key, text) VALUES (%s,'transcript','k','b')", (s.id,))


def test_replay_events_reject_exact_duplicates(conn):
    q = "INSERT INTO replay_events (video_id, kind, offset_ms, text) VALUES ('v','chat',5,'oi')"
    conn.execute(q)
    with pytest.raises(errors.UniqueViolation):
        conn.execute(q)
    conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, text) VALUES ('v','chat',5,'outra')")


RAW = ("INSERT INTO fallacy_marks (session_id, user_id, kind, start_event_id, start_offset, end_event_id, end_offset, "
       "quote, fallacy_id, latency_ms, counts) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)")


def test_mark_constraints_in_the_db(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a, b = ev(conn, s.id), ev(conn, s.id)
    c = ev(conn, s.id, kind="chat")

    def raw(**kw):
        d = {"session": s.id, "user": u, "kind": "transcript", "e1": a, "o1": 0, "e2": a, "o2": 5, "quote": "Se Deu", "fid": fid, "lat": 0}
        d.update(kw)
        conn.execute(RAW, (d["session"], d["user"], d["kind"], d["e1"], d["o1"], d["e2"], d["o2"], d["quote"],
                           d["fid"], d["lat"]))

    raw()                                                                           # the baseline row is valid
    for bad in ({"e1": b, "e2": a},                                                    # start after end
                {"o1": 5, "o2": 5},                                                    # empty range in one event
                {"o1": 6, "o2": 5},
                {"quote": ""}, {"quote": "x" * 501}, {"lat": -1}, {"o1": -1},
                {"e1": a, "e2": c, "o2": 3},                                          # start ok, END is a chat event
                {"e1": c, "e2": c},                                                    # a chat event used as 'transcript'
                {"kind": "chat"},                                                   # kind does not match the events
                {"fid": 9999}):
        with pytest.raises((errors.CheckViolation, errors.ForeignKeyViolation)):
            raw(**bad)
    with pytest.raises(errors.UniqueViolation):                                      # same pair twice
        raw()
    S.start_session_as_admin(conn)                                                   # another session (bingo)
    u2 = S.join_session(conn, "Bia", "10.9.9.9")[1].id
    with pytest.raises(errors.ForeignKeyViolation):                                  # user from another session
        raw(user=u2)


def test_review_columns_must_agree(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a = ev(conn, s.id)
    conn.execute(RAW, (s.id, u, "transcript", a, 0, a, 5, "Se Deu", fid, 0))
    with pytest.raises(errors.CheckViolation):
        conn.execute("UPDATE fallacy_marks SET status = 'correct'")                  # verdict without reviewed_at
    with pytest.raises(errors.CheckViolation):
        conn.execute("UPDATE fallacy_marks SET reviewed_at = now()")                 # reviewed_at while pending
    conn.execute("UPDATE fallacy_marks SET status = 'correct', reviewed_at = now()")


def test_used_fallacy_cannot_be_deleted_and_session_delete_cascades(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a = ev(conn, s.id)
    conn.execute(RAW, (s.id, u, "transcript", a, 0, a, 5, "Se Deu", fid, 0))
    with pytest.raises(errors.ForeignKeyViolation):
        conn.execute("DELETE FROM fallacies WHERE id = %s", (fid,))
    conn.execute("DELETE FROM game_sessions WHERE id = %s", (s.id,))
    assert conn.execute("SELECT (SELECT count(*) FROM fallacy_marks) + (SELECT count(*) FROM stream_events)").fetchone()[0] == 0


def test_host_message_rules(conn):
    s, (u,) = game(conn)
    ins = "INSERT INTO host_messages (session_id, user_id, text) VALUES (%s,%s,%s)"
    conn.execute(ins, (s.id, u, "x" * 280))
    for bad in ("", "   ", "x" * 281):
        with pytest.raises(errors.CheckViolation):
            conn.execute(ins, (s.id, u, bad))
    with pytest.raises(errors.CheckViolation):
        conn.execute("UPDATE host_messages SET status = 'done'")                     # handled without handled_at
    conn.execute("UPDATE host_messages SET status = 'done', handled_at = now()")


# ================================================================ fallacy list + seed
def test_list_fallacies_filters_disabled_and_keeps_id_order(conn):
    a, b, c = fallacy(conn, "A"), fallacy(conn, "B", enabled=False), fallacy(conn, "C")
    assert [f.id for f in F.list_fallacies(conn)] == [a, c]
    assert [f.id for f in F.list_fallacies(conn, enabled_only=False)] == [a, b, c]
    assert F.list_fallacies(conn)[0].name == "A"


def test_seed_loads_the_real_file_once_and_never_resurrects(conn):
    first = F.load_fallacy_seed(conn)
    assert first == {"inserted": 97}
    assert conn.execute("SELECT count(*) FILTER (WHERE enabled), count(*) FILTER (WHERE source = 'wikipedia') FROM fallacies").fetchone() == (77, 97)
    conn.execute("DELETE FROM fallacies WHERE name LIKE 'Falácia do espantalho%'")
    fallacy(conn, "Deus das lacunas")
    again = F.load_fallacy_seed(conn)
    assert again["skipped"] and conn.execute("SELECT count(*) FROM fallacies").fetchone()[0] == 97


def test_seed_missing_file_is_reported_not_raised(conn, tmp_path):
    assert "skipped" in F.load_fallacy_seed(conn, tmp_path / "nope.sql")


# ================================================================ feed rows
def test_add_event_is_idempotent_per_source_key(conn):
    s, _ = game(conn)
    first = F.add_event(conn, s.id, "chat", "k1", "oi", author="Zé")
    assert isinstance(first, int)
    assert F.add_event(conn, s.id, "chat", "k1", "tanto faz") is None
    assert conn.execute("SELECT text, author FROM stream_events WHERE id = %s", (first,)).fetchone() == ("oi", "Zé")


def test_add_event_validates_kind_and_text(conn):
    s, _ = game(conn)
    with pytest.raises(ValueError):
        F.add_event(conn, s.id, "video", "k", "oi")
    with pytest.raises(ValueError):
        F.add_event(conn, s.id, "chat", "k", "   ")


# ================================================================ selection -> quote (server side)
def test_quote_inside_one_line(conn):
    s, _ = game(conn)
    a = ev(conn, s.id)
    assert F.build_quote(conn, s.id, "transcript", a, 3, a, 8) == LONG[3:7]


def test_quote_across_lines_joins_the_pieces_with_one_space(conn):
    s, _ = game(conn)
    a, b, c = ev(conn, s.id, "um dois três"), ev(conn, s.id, "quatro cinco"), ev(conn, s.id, "seis sete oito")
    assert F.build_quote(conn, s.id, "transcript", a, 3, c, 9) == "dois três quatro cinco seis sete"
    assert F.build_quote(conn, s.id, "transcript", a, 0, b, 6) == "um dois três quatro"


def test_quote_ignores_the_other_panes_events_in_between(conn):
    s, _ = game(conn)
    a = ev(conn, s.id, "primeira linha")
    ev(conn, s.id, "mensagem do chat", kind="chat")
    b = ev(conn, s.id, "segunda linha")
    assert F.build_quote(conn, s.id, "transcript", a, 0, b, 7) == "primeira linha segunda"


def test_offsets_are_utf16_units_like_the_browser_and_cannot_split_an_emoji(conn):
    s, _ = game(conn)
    a = ev(conn, s.id, "oi 😀 ok")                       # the emoji is 2 UTF-16 units: positions 3-4
    assert F.build_quote(conn, s.id, "transcript", a, 3, a, 5) == "😀"
    assert F.build_quote(conn, s.id, "transcript", a, 6, a, 8) == "ok"
    for o1, o2 in ((4, 8), (0, 4)):                      # a bound between the two halves of the emoji
        with pytest.raises(F.BadSelection):
            F.build_quote(conn, s.id, "transcript", a, o1, a, o2)
    with pytest.raises(F.BadSelection):
        F.build_quote(conn, s.id, "transcript", a, 0, a, 9)    # 8 units long: 9 is out of range


def test_quote_whitespace_is_collapsed_and_trimmed(conn):
    s, _ = game(conn)
    a = ev(conn, s.id, "aaa   bbb    ccc")
    assert F.build_quote(conn, s.id, "transcript", a, 0, a, 16) == "aaa bbb ccc"


@pytest.mark.parametrize("o1,o2", [(5, 5), (6, 5), (-1, 4), (0, 99), (70, 80)])
def test_bad_offsets_in_one_line_are_rejected(conn, o1, o2):
    s, _ = game(conn)
    a = ev(conn, s.id)
    with pytest.raises(F.BadSelection):
        F.build_quote(conn, s.id, "transcript", a, o1, a, o2)


def test_multi_line_offsets_must_fall_inside_the_first_and_last_line(conn):
    s, _ = game(conn)
    a, b = ev(conn, s.id, "um dois"), ev(conn, s.id, "três quatro")
    assert F.build_quote(conn, s.id, "transcript", a, 6, b, 4) == "s três"
    for o1, o2 in ((-1, 4), (7, 4), (99, 4), (0, 12), (0, 99)):   # first line has 7 units, last has 11
        with pytest.raises(F.BadSelection):
            F.build_quote(conn, s.id, "transcript", a, o1, b, o2)


def test_bad_selection_cases(conn):
    s, _ = game(conn)
    a, b = ev(conn, s.id), ev(conn, s.id)
    chat = ev(conn, s.id, kind="chat")
    with pytest.raises(F.BadSelection):                                              # start after end
        F.build_quote(conn, s.id, "transcript", b, 0, a, 5)
    with pytest.raises(F.BadSelection):                                              # unknown event
        F.build_quote(conn, s.id, "transcript", 10**9, 0, 10**9 + 1, 5)
    with pytest.raises(F.BadSelection):                                              # an event of the other pane
        F.build_quote(conn, s.id, "transcript", a, 0, chat, 5)
    with pytest.raises(F.BadSelection):                                              # unknown pane
        F.build_quote(conn, s.id, "video", a, 0, b, 5)
    with pytest.raises(F.BadSelection):                                              # whitespace only
        w = ev(conn, s.id, "   x   ")
        F.build_quote(conn, s.id, "transcript", w, 0, w, 3)


def test_events_of_another_session_are_not_selectable(conn):
    s, _ = game(conn)
    bingo = S.start_session_as_admin(conn)
    foreign = F.add_event(conn, bingo.id, "transcript", "k", LONG)
    with pytest.raises(F.BadSelection):
        F.build_quote(conn, s.id, "transcript", foreign, 0, foreign, 5)


def test_quote_longer_than_500_chars_is_refused(conn):
    s, _ = game(conn)
    ids = [ev(conn, s.id, "palavra " * 10) for _ in range(8)]                        # 8 x 79 chars
    assert len(F.build_quote(conn, s.id, "transcript", ids[0], 0, ids[5], 79)) <= 500
    with pytest.raises(F.SelectionTooLong):
        F.build_quote(conn, s.id, "transcript", ids[0], 0, ids[7], 79)


def test_a_huge_span_is_refused_without_loading_it(conn):
    s, _ = game(conn)
    a = ev(conn, s.id, "x")
    for _ in range(F.MAX_LINES + 2):
        ev(conn, s.id, "x")
    z = ev(conn, s.id, "x")
    with pytest.raises(F.SelectionTooLong):
        F.build_quote(conn, s.id, "transcript", a, 0, z, 1)


# ================================================================ submit
def test_submit_stores_the_server_side_facts(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a = ev(conn, s.id, ago=10)
    m = F.submit_mark(conn, u, "transcript", a, 3, a, 8, fid)
    assert (m.quote, m.status, m.counts, m.fallacy_id, m.user_id, m.session_id) == (LONG[3:8].strip(), "pending", True, fid, u, s.id)
    assert 10_000 <= m.latency_ms < 11_500 and m.reviewed_at is None
    assert F.points_of(m) == 500


def test_latency_is_measured_from_the_last_selected_line(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a, b = ev(conn, s.id, ago=100), ev(conn, s.id, ago=20)
    m = F.submit_mark(conn, u, "transcript", a, 0, b, 5, fid)
    assert 20_000 <= m.latency_ms < 21_500


def test_latency_never_goes_negative(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a = ev(conn, s.id, ago=-30)                                                      # clock skew: line "fed" in the future
    assert mark(conn, u, a, fid).latency_ms == 0


def test_submit_is_durable_from_another_connection(conn, test_db_url):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a = ev(conn, s.id)
    mark(conn, u, a, fid)
    with psycopg.connect(test_db_url) as other:
        assert other.execute("SELECT count(*) FROM fallacy_marks").fetchone()[0] == 1


def test_unknown_or_disabled_fallacy_is_refused(conn):
    s, (u,) = game(conn)
    a = ev(conn, s.id)
    off = fallacy(conn, "Off", enabled=False)
    for fid in (off, 12345):
        with pytest.raises(F.UnknownFallacy):
            mark(conn, u, a, fid)


def test_only_falacias_players_of_an_active_session_can_submit(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a = ev(conn, s.id)
    bingo_player = S.start_session(conn, "Bia", "10.7.7.7")[1].id
    with pytest.raises(F.NotAFalaciasPlayer):
        mark(conn, bingo_player, a, fid)
    with pytest.raises(F.NotAFalaciasPlayer):
        mark(conn, uuid.uuid4(), a, fid)
    S.end_session(conn, s.id)
    with pytest.raises(F.SessionNotActive):
        mark(conn, u, a, fid)


def test_an_ended_session_is_reported_before_anything_else(conn):
    s, (u,) = game(conn)
    a = ev(conn, s.id)
    S.end_session(conn, s.id)
    with pytest.raises(F.SessionNotActive):
        F.submit_mark(conn, u, "transcript", a, 0, a, 5, 9999)         # unknown fallacy AND ended: the ended session wins


def test_a_session_that_ends_while_the_pair_is_being_stored_is_refused(conn, monkeypatch):
    """The pre-check passed, then the admin ended the game: the check INSIDE the transaction must still refuse."""
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a = ev(conn, s.id)
    real = F.build_quote

    def end_then_quote(*args, **kw):
        out = real(*args, **kw)
        S.end_session(conn, s.id)
        return out

    monkeypatch.setattr(F, "build_quote", end_then_quote)
    with pytest.raises(F.SessionNotActive):
        mark(conn, u, a, fid)
    assert conn.execute("SELECT count(*) FROM fallacy_marks").fetchone()[0] == 0


def test_rate_limit_one_mark_per_10_seconds(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a = ev(conn, s.id)
    mark(conn, u, a, fid, 0, 5)
    with pytest.raises(F.RateLimited):
        mark(conn, u, a, fid, 6, 10)
    age(conn, u, 9)
    with pytest.raises(F.RateLimited):
        mark(conn, u, a, fid, 6, 10)
    age(conn, u, 2)                                                                  # now 11 s ago
    mark(conn, u, a, fid, 6, 10)


def test_rate_limit_is_per_player(conn):
    s, (u, v) = game(conn, ("Ana", "Bia"))
    fid = fallacy(conn)
    a = ev(conn, s.id)
    mark(conn, u, a, fid)
    mark(conn, v, a, fid)


def test_exact_duplicate_by_the_same_player_is_refused_but_others_may_repeat_it(conn):
    s, (u, v) = game(conn, ("Ana", "Bia"))
    fid, fid2 = fallacy(conn), fallacy(conn, "Espantalho")
    a = ev(conn, s.id)
    mark(conn, u, a, fid)
    age(conn, u)
    with pytest.raises(F.DuplicateMark):
        mark(conn, u, a, fid)
    mark(conn, u, a, fid2)                                                           # same passage, other fallacy: fine
    m = mark(conn, v, a, fid)                                                        # another player, same pair: scores too
    assert m.counts


def test_cap_of_20_unreviewed_pairs_that_score(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a = ev(conn, s.id)
    results = []
    for i in range(22):
        results.append(F.submit_mark(conn, u, "transcript", a, i, a, i + 3, fid).counts)
        age(conn, u)
    assert results == [True] * 20 + [False] * 2
    # a reviewed pair frees a slot for the NEXT one; stored flags of old pairs never change
    first = conn.execute("SELECT id FROM fallacy_marks ORDER BY created_at LIMIT 1").fetchone()[0]
    F.review_mark(conn, s.id, first, "wrong")
    assert F.submit_mark(conn, u, "transcript", a, 30, a, 33, fid).counts is True
    assert conn.execute("SELECT count(*) FROM fallacy_marks WHERE NOT counts").fetchone()[0] == 2


def test_concurrent_submits_by_one_player_exactly_one_wins(conn, test_db_url):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a = ev(conn, s.id)
    barrier, out = threading.Barrier(6), []

    def worker(i):
        with connect(test_db_url) as c:
            barrier.wait()
            try:
                F.submit_mark(c, u, "transcript", a, i, a, i + 3, fid)
                out.append("ok")
            except F.RateLimited:
                out.append("rate")

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert sorted(out) == ["ok"] + ["rate"] * 5
    assert conn.execute("SELECT count(*) FROM fallacy_marks").fetchone()[0] == 1


def test_concurrent_cap_is_not_exceeded(conn, test_db_url, monkeypatch):
    monkeypatch.setattr(F, "RATE_LIMIT_S", 0)
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a = ev(conn, s.id)
    barrier = threading.Barrier(25)

    def worker(i):
        with connect(test_db_url) as c:
            barrier.wait()
            F.submit_mark(c, u, "transcript", a, i, a, i + 3, fid)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert conn.execute("SELECT count(*) FILTER (WHERE counts), count(*) FROM fallacy_marks").fetchone() == (20, 25)


def test_user_marks_newest_first(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    a = ev(conn, s.id)
    m1 = mark(conn, u, a, fid, 0, 5)
    age(conn, u)
    m2 = mark(conn, u, a, fid, 6, 10)
    assert [m.id for m in F.user_marks(conn, u)] == [m2.id, m1.id]


# ================================================================ review
def test_review_sets_status_and_time_and_can_be_corrected(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    m = mark(conn, u, ev(conn, s.id), fid)
    r = F.review_mark(conn, s.id, m.id, "correct")
    assert r.status == "correct" and r.reviewed_at is not None
    assert F.review_mark(conn, s.id, m.id, "spam").status == "spam"
    assert F.get_mark(conn, m.id).status == "spam"


def test_review_refuses_bad_verdicts_unknown_marks_and_other_sessions(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    m = mark(conn, u, ev(conn, s.id), fid)
    with pytest.raises(ValueError):
        F.review_mark(conn, s.id, m.id, "pending")
    with pytest.raises(F.MarkNotFound):
        F.review_mark(conn, s.id, uuid.uuid4(), "correct")
    other = S.start_session_as_admin(conn)
    with pytest.raises(F.MarkNotFound):
        F.review_mark(conn, other.id, m.id, "correct")


def test_no_late_reviews(conn):
    s, (u,) = game(conn)
    fid = fallacy(conn)
    m = mark(conn, u, ev(conn, s.id), fid)
    S.end_session(conn, s.id)
    with pytest.raises(F.SessionNotActive):
        F.review_mark(conn, s.id, m.id, "correct")
    with pytest.raises(F.SessionNotActive):
        F.review_same_pairs(conn, s.id, m.id, "correct")
    assert F.get_mark(conn, m.id).status == "pending"


def test_review_same_pairs_touches_only_identical_pending_pairs(conn):
    s, (u, v, w) = game(conn, ("Ana", "Bia", "Caio"))
    f1, f2 = fallacy(conn), fallacy(conn, "Espantalho")
    a = ev(conn, s.id)
    mu, mv = mark(conn, u, a, f1), mark(conn, v, a, f1)
    other_fallacy = mark(conn, w, a, f2)
    age(conn, w)
    other_range = F.submit_mark(conn, w, "transcript", a, 20, a, 25, f1)
    age(conn, w)
    age(conn, w)
    longer = F.submit_mark(conn, w, "transcript", a, 0, a, 12, f1)                   # same start, longer end: NOT identical
    b = ev(conn, s.id)
    age(conn, w)
    later_line = F.submit_mark(conn, w, "transcript", a, 0, b, 10, f1)               # same start, other end line: NOT identical
    age(conn, w)
    already = F.submit_mark(conn, w, "transcript", a, 0, a, 10, f1)                  # identical pair, reviewed before
    F.review_mark(conn, s.id, already.id, "wrong")
    assert F.review_same_pairs(conn, s.id, mu.id, "correct") == 2
    status = {m.id: F.get_mark(conn, m.id).status for m in (mu, mv, other_fallacy, other_range, longer, later_line, already)}
    assert status == {mu.id: "correct", mv.id: "correct", other_fallacy.id: "pending", other_range.id: "pending",
                      longer.id: "pending", later_line.id: "pending", already.id: "wrong"}


# ================================================================ leaderboard
def test_leaderboard_sums_derived_points_and_spam_retracts(conn):
    s, (u, v, _w) = game(conn, ("Ana", "Bia", "Caio"))
    fid = fallacy(conn)
    a = ev(conn, s.id, ago=0)
    m1 = mark(conn, u, a, fid, 0, 5)
    age(conn, u)
    m2 = mark(conn, u, a, fid, 6, 10)
    F.review_mark(conn, s.id, m1.id, "correct")                                      # 500 + 500 + speed(~6.0)
    F.review_mark(conn, s.id, m2.id, "spam")                                         # 0
    mv = mark(conn, v, a, fid, 0, 5)
    F.review_mark(conn, s.id, mv.id, "wrong")                                        # 500
    rows = F.leaderboard(conn, s.id)
    by = {r.name: r for r in rows}
    assert [r.name for r in rows] == ["Ana", "Bia", "Caio"]
    assert 1500 < by["Ana"].points <= 1600 and by["Bia"].points == 500 and by["Caio"].points == 0
    assert (by["Ana"].correct, by["Ana"].spam, by["Ana"].pairs) == (1, 1, 2)
    assert (by["Bia"].wrong, by["Caio"].pairs) == (1, 0)
    assert [r.rank for r in rows] == [1, 2, 3]


def test_leaderboard_ties_break_on_correct_pairs_then_earliest_last_submission(conn):
    s, (u, v, w) = game(conn, ("Ana", "Bia", "Caio"))
    fid = fallacy(conn)
    a = ev(conn, s.id, ago=70)                                                       # latency > 60 s: no speed bonus, exact numbers
    mu = mark(conn, u, a, fid, 0, 5)
    mv = mark(conn, v, a, fid, 0, 5)
    mw = mark(conn, w, a, fid, 0, 5)
    F.review_mark(conn, s.id, mu.id, "correct")                                      # 10.0 (500 + 500)
    F.review_mark(conn, s.id, mv.id, "correct")                                      # 10.0
    F.review_mark(conn, s.id, mw.id, "wrong")                                        # 5.0, plus a 2nd pending pair below
    conn.execute("UPDATE fallacy_marks SET created_at = now() - interval '2 minutes' WHERE id = %s", (mv.id,))  # v submitted first
    age(conn, w)
    F.submit_mark(conn, w, "transcript", a, 6, a, 10, fid)                           # w: 5.0 + 5.0 = 10.0 with 0 correct
    conn.execute("UPDATE fallacy_marks SET created_at = created_at - interval '1 hour' WHERE user_id = %s", (w,))  # w was FIRST
    rows = F.leaderboard(conn, s.id)
    assert [(r.name, r.points) for r in rows] == [("Bia", 1000), ("Ana", 1000), ("Caio", 1000)]


def test_leaderboard_is_per_session(conn):
    s, (u,) = game(conn)
    other = S.start_session(conn, "Zé", "10.5.5.5")[0]                               # a bingo session: never listed
    fid = fallacy(conn)
    mark(conn, u, ev(conn, s.id), fid)
    assert [r.name for r in F.leaderboard(conn, s.id)] == ["Ana"] and F.leaderboard(conn, other.id) == []
