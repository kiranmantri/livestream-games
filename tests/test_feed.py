"""Task 2b: the replay feed of "Falaciômetro" (app/feed.py): config, readiness, session clock, health, seed."""
from __future__ import annotations

import threading
import uuid

import psycopg
import pytest

from app import falacias as F
from app import feed
from app import sessions as S
from app.db import connect
from app.texts import FEED_UNAVAILABLE

VID = "vid1"


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users, replay_events CASCADE")
    yield c
    c.execute("TRUNCATE game_sessions, users, replay_events CASCADE")
    c.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("FALACIAS_VIDEO_ID", "FALACIAS_SPEED", "FALACIAS_START_AT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FALACIAS_VIDEO_ID", VID)
    monkeypatch.setenv("MAX_USERS_PER_IP", "50")
    feed._last_tick.clear()


def replay(conn, rows, vid=VID):
    for kind, off, text in rows:
        conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES (%s,%s,%s,%s,%s)",
                     (vid, kind, off, "@a" if kind == "chat" else None, text))


ROWS = [("transcript", 1_000, "t1"), ("chat", 2_000, "c1"), ("transcript", 3_000, "t2"), ("transcript", 3_000, "t2b"),
        ("chat", 10_000, "c2"), ("transcript", 20_000, "t3")]


def started(conn, seconds_ago: float = 0):
    s = S.start_session_as_admin(conn, game_type="falacias")
    if seconds_ago:
        conn.execute("UPDATE game_sessions SET created_at = created_at - make_interval(secs => %s) WHERE id = %s",
                     (seconds_ago, s.id))
    return s


def shown(conn, sid):
    return [(r[0], r[1]) for r in conn.execute("SELECT kind, text FROM stream_events WHERE session_id = %s ORDER BY id", (sid,))]


# ================================================================ config
def test_config_defaults(monkeypatch):
    monkeypatch.delenv("FALACIAS_VIDEO_ID")
    c = feed.feed_config()
    assert (c.video_id, c.speed, c.start_at_ms) == ("_nVWPIFCUzQ", 1.0, 0)


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("FALACIAS_SPEED", "20")
    monkeypatch.setenv("FALACIAS_START_AT", "90.5")
    c = feed.feed_config()
    assert (c.video_id, c.speed, c.start_at_ms) == (VID, 20.0, 90_500)


@pytest.mark.parametrize("name,val", [("FALACIAS_SPEED", "0"), ("FALACIAS_SPEED", "-1"), ("FALACIAS_SPEED", "abc"),
                                      ("FALACIAS_SPEED", "100000"), ("FALACIAS_START_AT", "-5"),
                                      ("FALACIAS_START_AT", "x"), ("FALACIAS_VIDEO_ID", "a b")])
def test_invalid_config_is_refused(monkeypatch, name, val):
    monkeypatch.setenv(name, val)
    with pytest.raises(ValueError):
        feed.feed_config()


# ================================================================ readiness + start
def test_the_error_text_is_the_agreed_one():
    assert FEED_UNAVAILABLE == "Vídeo offline ou vídeo sem transcrições"


def test_not_ready_without_both_tracks(conn):
    with pytest.raises(feed.FeedUnavailable) as e:
        feed.check_ready(conn)
    assert e.value.code == "feed_unavailable" and str(e.value) == FEED_UNAVAILABLE
    replay(conn, [("transcript", 1, "t")])
    with pytest.raises(feed.FeedUnavailable):
        feed.check_ready(conn)
    replay(conn, [("chat", 1, "c")], vid="other")                          # another video's chat does not count
    with pytest.raises(feed.FeedUnavailable):
        feed.check_ready(conn)
    replay(conn, [("chat", 1, "c")])
    feed.check_ready(conn)


def test_start_is_refused_without_a_feed_and_creates_no_session(conn):
    with pytest.raises(feed.FeedUnavailable):
        feed.start_falacias(conn, "Ana", "10.0.0.1")
    with pytest.raises(feed.FeedUnavailable):
        feed.start_falacias_as_admin(conn)
    assert conn.execute("SELECT count(*) FROM game_sessions").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0


def test_start_works_with_a_feed_and_only_once(conn):
    replay(conn, ROWS)
    s, p, _tok = feed.start_falacias(conn, "Ana", "10.0.0.1")
    assert s.game_type == "falacias" and p.name == "Ana"
    with pytest.raises(S.SessionAlreadyActive):
        feed.start_falacias_as_admin(conn)
    S.end_session(conn, s.id)
    assert feed.start_falacias_as_admin(conn).game_type == "falacias"


def test_starting_bingo_is_unaffected_by_the_feed(conn):
    S.start_session(conn, "Ana", "10.0.0.1")


# ================================================================ session clock
def test_nothing_is_due_at_second_zero(conn):
    replay(conn, ROWS)
    s = started(conn)
    assert feed.tick(conn, s.id) == 0 and shown(conn, s.id) == []


def test_tick_releases_only_what_is_due_in_offset_order_across_both_panes(conn):
    replay(conn, ROWS)
    s = started(conn, seconds_ago=3.5)
    assert feed.tick(conn, s.id) == 4
    assert shown(conn, s.id) == [("transcript", "t1"), ("chat", "c1"), ("transcript", "t2"), ("transcript", "t2b")]


def test_tick_is_idempotent_and_continues_where_it_stopped(conn):
    replay(conn, ROWS)
    s = started(conn, seconds_ago=3.5)
    feed.tick(conn, s.id)
    assert feed.tick(conn, s.id) == 0
    conn.execute("UPDATE game_sessions SET created_at = created_at - interval '30 seconds' WHERE id = %s", (s.id,))
    assert feed.tick(conn, s.id) == 2
    assert [t for _, t in shown(conn, s.id)][-2:] == ["c2", "t3"]
    assert feed.tick(conn, s.id) == 0


def test_a_released_line_is_never_fed_again(conn):
    replay(conn, ROWS)
    s = started(conn, seconds_ago=3.5)
    feed.tick(conn, s.id)
    conn.execute("DELETE FROM stream_events WHERE id = (SELECT min(id) FROM stream_events)")
    assert feed.tick(conn, s.id) == 0 and len(shown(conn, s.id)) == 3        # the cursor is the last released offset


def test_event_ids_follow_offsets_even_if_the_rows_were_stored_in_another_order(conn):
    replay(conn, list(reversed(ROWS)))
    s = started(conn, seconds_ago=60)
    feed.tick(conn, s.id)
    assert [t for _, t in shown(conn, s.id)] == ["t1", "c1", "t2b", "t2", "c2", "t3"]       # equal offsets: stored order


def test_event_ids_follow_replay_offsets(conn):
    replay(conn, ROWS)
    s = started(conn, seconds_ago=60)
    feed.tick(conn, s.id)
    offs = [r[0] for r in conn.execute(
        "SELECT r.offset_ms FROM stream_events e JOIN replay_events r ON r.id = e.source_key::bigint "
        "WHERE e.session_id = %s ORDER BY e.id", (s.id,))]
    assert offs == sorted(offs) and len(offs) == 6


def test_fed_at_is_the_scheduled_time_not_the_catch_up_time(conn):
    replay(conn, ROWS)
    s = started(conn, seconds_ago=60)                      # the tick happens 60 s late: fed_at must still be start + offset
    feed.tick(conn, s.id)
    bad = conn.execute(
        "SELECT count(*) FROM stream_events e JOIN replay_events r ON r.id = e.source_key::bigint "
        "JOIN game_sessions g ON g.id = e.session_id "
        "WHERE abs(extract(epoch FROM e.fed_at - (g.created_at + r.offset_ms * interval '1 millisecond'))) > 0.001").fetchone()[0]
    assert bad == 0


def test_speed_scales_the_clock_and_fed_at(conn, monkeypatch):
    monkeypatch.setenv("FALACIAS_SPEED", "10")
    replay(conn, ROWS)
    s = started(conn, seconds_ago=1.5)                     # 1.5 s x 10 = 15 s of video: everything up to c2
    assert feed.tick(conn, s.id) == 5
    gap = conn.execute("SELECT extract(epoch FROM e.fed_at - g.created_at)::float8 FROM stream_events e JOIN game_sessions g "
                       "ON g.id = e.session_id WHERE e.session_id = %s ORDER BY e.id LIMIT 1", (s.id,)).fetchone()[0]
    assert abs(gap - 0.1) < 0.001                          # offset 1 000 ms / 10


def test_start_at_skips_earlier_rows_and_shifts_the_clock(conn, monkeypatch):
    monkeypatch.setenv("FALACIAS_START_AT", "5")
    replay(conn, ROWS)
    s = started(conn, seconds_ago=0.5)
    assert feed.tick(conn, s.id) == 0                      # 5 s + 0.5 s: c2 (10 s) is not due yet
    conn.execute("UPDATE game_sessions SET created_at = created_at - interval '6 seconds' WHERE id = %s", (s.id,))
    assert feed.tick(conn, s.id) == 1 and shown(conn, s.id) == [("chat", "c2")]        # t1..t2b are before the start point
    gap = conn.execute("SELECT extract(epoch FROM e.fed_at - g.created_at)::float8 FROM stream_events e JOIN game_sessions g "
                       "ON g.id = e.session_id").fetchone()[0]
    assert abs(gap - 5.0) < 0.001                          # (10 000 - 5 000) ms after the session start


def test_only_active_falacias_sessions_are_fed(conn):
    replay(conn, ROWS)
    bingo = S.start_session_as_admin(conn)
    conn.execute("UPDATE game_sessions SET created_at = created_at - interval '60 seconds' WHERE id = %s", (bingo.id,))
    ended = started(conn, seconds_ago=60)
    S.end_session(conn, ended.id)
    assert feed.tick(conn, bingo.id) == 0 and feed.tick(conn, ended.id) == 0
    assert conn.execute("SELECT count(*) FROM stream_events").fetchone()[0] == 0
    assert feed.tick(conn, uuid.uuid4()) == 0


def test_two_sessions_do_not_share_events(conn):
    replay(conn, ROWS)
    a = started(conn, seconds_ago=60)
    S.end_session(conn, a.id)
    b = started(conn, seconds_ago=3.5)
    feed.tick(conn, b.id)
    assert len(shown(conn, b.id)) == 4 and shown(conn, a.id) == []


def test_concurrent_ticks_never_duplicate_or_reorder(conn, test_db_url):
    replay(conn, [("transcript", i * 100, f"t{i}") for i in range(300)])
    s = started(conn, seconds_ago=3600)
    barrier, out = threading.Barrier(6), []

    def worker():
        with connect(test_db_url) as c:
            barrier.wait()
            out.append(feed.tick(c, s.id))

    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert sum(out) == 300
    texts = [t for _, t in shown(conn, s.id)]
    assert texts == [f"t{i}" for i in range(300)]


def test_tick_is_durable_from_another_connection(conn, test_db_url):
    replay(conn, ROWS)
    s = started(conn, seconds_ago=3.5)
    feed.tick(conn, s.id)
    with psycopg.connect(test_db_url) as other:
        assert other.execute("SELECT count(*) FROM stream_events").fetchone()[0] == 4


def test_throttled_tick_runs_at_most_once_per_interval(conn, monkeypatch):
    replay(conn, ROWS)
    s = started(conn, seconds_ago=3.5)
    now = [100.0]
    monkeypatch.setattr(feed.time, "monotonic", lambda: now[0])
    assert feed.tick_throttled(conn, s.id) == 4
    conn.execute("UPDATE game_sessions SET created_at = created_at - interval '60 seconds' WHERE id = %s", (s.id,))
    now[0] = 100.5
    assert feed.tick_throttled(conn, s.id) == 0                 # too soon: nothing is queried or released
    assert len(shown(conn, s.id)) == 4
    now[0] = 101.2
    assert feed.tick_throttled(conn, s.id) == 2
    S.end_session(conn, s.id)
    s2 = started(conn, seconds_ago=60)                           # another session has its own timer
    assert feed.tick_throttled(conn, s2.id) == 6


# ================================================================ health
def test_status_ok_then_ended_when_the_video_is_over(conn):
    replay(conn, ROWS)
    s = started(conn, seconds_ago=3.5)
    feed.tick(conn, s.id)
    assert feed.status(conn, s.id) == "ok"
    conn.execute("UPDATE game_sessions SET created_at = created_at - interval '60 seconds' WHERE id = %s", (s.id,))
    feed.tick(conn, s.id)
    assert feed.status(conn, s.id) == "ended"
    feed.ensure_up(conn, s.id)                                     # the video ending is not a failure: marks stay allowed


def test_a_start_point_after_the_last_line_means_the_video_is_over_at_once(conn, monkeypatch):
    monkeypatch.setenv("FALACIAS_START_AT", "100")
    replay(conn, ROWS)
    s = started(conn, seconds_ago=5)
    assert feed.tick(conn, s.id) == 0 and feed.status(conn, s.id) == "ended"


def test_status_is_down_when_the_replay_rows_disappear_and_ensure_up_refuses(conn):
    replay(conn, ROWS)
    s = started(conn, seconds_ago=3.5)
    feed.tick(conn, s.id)
    conn.execute("DELETE FROM replay_events WHERE kind = 'chat'")
    assert feed.status(conn, s.id) == "down"
    with pytest.raises(feed.FeedDown) as e:
        feed.ensure_up(conn, s.id)
    assert e.value.code == "feed_down" and str(e.value) == FEED_UNAVAILABLE
    replay(conn, [("chat", 2_000, "c1"), ("chat", 10_000, "c2")])
    assert feed.status(conn, s.id) == "ok"                         # recovers by itself


def test_status_down_when_the_configured_video_changes_to_one_without_rows(conn, monkeypatch):
    replay(conn, ROWS)
    s = started(conn, seconds_ago=3.5)
    monkeypatch.setenv("FALACIAS_VIDEO_ID", "other")
    assert feed.status(conn, s.id) == "down"


# ================================================================ cloud seed
def test_replay_seed_loads_only_when_the_video_is_absent(conn, tmp_path):
    seed = tmp_path / "replay_vid1.sql"
    seed.write_text("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES "
                    "('vid1','transcript',1,NULL,'a'),('vid1','chat',2,'@x','b') ON CONFLICT DO NOTHING;", encoding="utf-8")
    assert feed.load_replay_seed(conn, VID, seed) == {"inserted": 2}
    conn.execute("DELETE FROM replay_events WHERE kind = 'chat'")
    assert "skipped" in feed.load_replay_seed(conn, VID, seed)             # the video has rows: never re-applied
    assert conn.execute("SELECT count(*) FROM replay_events").fetchone()[0] == 1


def test_replay_seed_missing_file_is_a_skip_not_a_crash(conn, tmp_path):
    assert "skipped" in feed.load_replay_seed(conn, VID, tmp_path / "nope.sql")
    assert "skipped" in feed.load_replay_seed(conn, VID)                     # default path of a video with no seed shipped


def test_default_seed_path_is_per_video():
    assert feed.seed_path("_nVWPIFCUzQ").name == "replay__nVWPIFCUzQ.sql"
    with pytest.raises(ValueError):
        feed.seed_path("../x")


# ================================================================ the whole chain
def test_end_to_end_feed_to_a_scored_pair(conn):
    """start -> tick -> select a fed line -> submit -> admin review -> leaderboard."""
    replay(conn, ROWS)
    conn.execute("INSERT INTO fallacies (name, category, source) VALUES ('Ad hominem','x','manual')")
    fid = conn.execute("SELECT id FROM fallacies").fetchone()[0]
    s, p, _ = feed.start_falacias(conn, "Ana", "10.0.0.1")
    conn.execute("UPDATE game_sessions SET created_at = created_at - interval '5 seconds' WHERE id = %s", (s.id,))
    feed.tick(conn, s.id)
    feed.ensure_up(conn, s.id)
    ev = conn.execute("SELECT id FROM stream_events WHERE session_id = %s AND text = 't2'", (s.id,)).fetchone()[0]
    m = F.submit_mark(conn, p.id, "transcript", ev, 0, ev, 2, fid)
    assert m.quote == "t2" and 1_900 <= m.latency_ms < 3_500                 # fed at +3 s, submitted at +5 s
    F.review_mark(conn, s.id, m.id, "correct")
    assert F.leaderboard(conn, s.id)[0].points > 1_000


# ================================================================ read model (Task 3)
def test_recent_events_window_is_per_pane_oldest_first_and_per_session(conn):
    replay(conn, ROWS)
    s1 = started(conn, 100)
    feed.tick(conn, s1.id)
    S.end_session(conn, s1.id, None)
    s2 = started(conn, 100)
    feed._last_tick.clear()
    feed.tick(conn, s2.id)
    got = feed.recent_events(conn, s2.id, per_kind=1)
    assert [(e.kind, e.text) for e in got] == [("chat", "c2"), ("transcript", "t3")]      # 1 per pane, oldest first (c2 is at 10 s, t3 at 20 s)
    assert all(e.id in {r[0] for r in conn.execute("SELECT id FROM stream_events WHERE session_id = %s", (s2.id,))} for e in got)
    assert len(feed.recent_events(conn, s2.id, per_kind=50)) == 6


def test_events_after_is_exclusive_ordered_and_per_session(conn):
    replay(conn, ROWS)
    s1 = started(conn, 100)
    feed.tick(conn, s1.id)
    S.end_session(conn, s1.id, None)
    s2 = started(conn, 100)
    feed._last_tick.clear()
    feed.tick(conn, s2.id)
    ids = [e.id for e in feed.events_after(conn, s2.id, 0)]
    assert len(ids) == 6 and ids == sorted(ids)
    assert [e.id for e in feed.events_after(conn, s2.id, ids[2])] == ids[3:]
    assert feed.events_after(conn, s2.id, ids[-1]) == []
    assert feed.last_event_id(conn, s2.id) == ids[-1] and feed.last_event_id(conn, uuid.uuid4()) == 0
