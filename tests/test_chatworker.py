"""L3 / step 2: the live-chat worker (app/chatworker.py). yt-dlp is never started: processes are scripted fakes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import chatworker as CW
from app import falacias as F
from app import feed
from app import sessions as S
from app.db import connect
from tests.chat_fakes import Factory, FakeProcess, chat_line, wait_until

VID = "abcdefghijk"


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users, fallacies, replay_events CASCADE")
    yield c
    c.execute("TRUNCATE game_sessions, users, fallacies, replay_events CASCADE")
    c.close()


@pytest.fixture(autouse=True)
def _clean():
    CW.stop_all()
    feed._last_tick.clear()
    yield
    CW.stop_all()


def now_us(conn, delta_s=0.0):
    return int(float(conn.execute("SELECT extract(epoch FROM now())").fetchone()[0] + delta_s) * 1_000_000)


@pytest.fixture
def live(conn):
    return S.start_session_as_admin(conn, game_type="falacias", video_id=VID, source_mode="live")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def worker(conn, test_db_url, sid, factory, clock=None, **kw):
    return CW.LiveChatWorker(sid, VID, factory, lambda: connect(test_db_url), clock=clock or Clock(), **kw)


def events(conn, sid, kind="chat"):
    return conn.execute("SELECT source_key, author, text FROM stream_events WHERE session_id = %s AND kind = %s ORDER BY id",
                        (sid, kind)).fetchall()


# ---------------------------------------------------------------- parsing
def test_parse_reads_text_messages_from_the_yt_dlp_wrapper():
    [m] = CW.parse_lines([chat_line("k1", "oi pessoal", "@ana", ts_us=5)])
    assert (m.key, m.author, m.text, m.ts_us) == ("k1", "@ana", "oi pessoal", 5)


def test_parse_accepts_a_bare_add_action_and_paid_messages():
    out = CW.parse_lines([chat_line("a", "x1", ts_us=1, wrapped=False),
                          chat_line("b", "x2", ts_us=2, renderer="liveChatPaidMessageRenderer")])
    assert [m.key for m in out] == ["a", "b"]


def test_parse_skips_junk_gifts_system_empty_and_keyless_messages():
    gift = json.dumps({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {
        "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer": {"id": "g", "timestampUsec": "1"}}}}]}})
    out = CW.parse_lines(["", "   ", "not json", "[1,2]", "{}", gift, chat_line("e", "   ", ts_us=1),
                          chat_line("", "sem chave", ts_us=1), chat_line("ok", "bom", ts_us=1)])
    assert [m.key for m in out] == ["ok"]


def test_parse_cleans_text_like_the_replay_does():
    [m] = CW.parse_lines([chat_line("k", "", ts_us=1, runs=[{"text": "a &amp;  b\n"}, {"emoji": {"emojiId": "🙂"}}])])
    assert m.text == "a & b 🙂"


def test_parse_message_without_timestamp_has_none():
    [m] = CW.parse_lines([chat_line("k", "oi")])
    assert m.ts_us is None


def test_parse_returns_messages_sorted_by_time():
    out = CW.parse_lines([chat_line("b", "2", ts_us=20), chat_line("a", "1", ts_us=10)])
    assert [m.key for m in out] == ["a", "b"]


# ---------------------------------------------------------------- ingest
def test_ingest_stores_chat_events_with_author_and_youtube_id(conn, live):
    n = CW.ingest(conn, live.id, CW.parse_lines([chat_line("Y1", "olá", "@bia", ts_us=now_us(conn, 5))]))
    assert n == 1 and events(conn, live.id) == [("Y1", "@bia", "olá")]


def test_ingest_drops_messages_from_before_the_session_started(conn, live):
    old = chat_line("old", "antigo", ts_us=now_us(conn, -30))
    new = chat_line("new", "novo", ts_us=now_us(conn, 1))
    assert CW.ingest(conn, live.id, CW.parse_lines([old, new])) == 1
    assert [e[0] for e in events(conn, live.id)] == ["new"]


def test_ingest_drops_messages_without_timestamp(conn, live):
    assert CW.ingest(conn, live.id, CW.parse_lines([chat_line("k", "sem hora")])) == 0


def test_ingest_is_idempotent_by_youtube_id(conn, live):
    msgs = CW.parse_lines([chat_line("Y1", "um", ts_us=now_us(conn, 1))])
    assert CW.ingest(conn, live.id, msgs) == 1 and CW.ingest(conn, live.id, msgs) == 0
    assert len(events(conn, live.id)) == 1


def test_ingest_keeps_time_order_inside_a_batch(conn, live):
    t = now_us(conn, 1)
    CW.ingest(conn, live.id, CW.parse_lines([chat_line("b", "segundo", ts_us=t + 10), chat_line("a", "primeiro", ts_us=t)]))
    assert [e[2] for e in events(conn, live.id)] == ["primeiro", "segundo"]


def test_ingest_ignores_an_ended_or_unknown_or_replay_session(conn, live):
    msg = CW.parse_lines([chat_line("k", "x", ts_us=now_us(conn, 1))])
    S.end_session(conn, live.id, None)
    assert CW.ingest(conn, live.id, msg) == 0
    import uuid
    assert CW.ingest(conn, uuid.uuid4(), msg) == 0


def test_ingest_never_writes_transcript_events(conn, live):
    CW.ingest(conn, live.id, CW.parse_lines([chat_line("k", "x", ts_us=now_us(conn, 1))]))
    assert events(conn, live.id, "transcript") == []


def test_ingested_chat_shows_in_the_pane_feed(conn, live):
    CW.ingest(conn, live.id, CW.parse_lines([chat_line("k", "frase do chat", "@c", ts_us=now_us(conn, 1))]))
    assert [(e.kind, e.text) for e in feed.recent_events(conn, live.id)] == [("chat", "frase do chat")]


def test_a_player_can_mark_a_live_chat_line(conn, live, monkeypatch):
    monkeypatch.setattr(F, "RATE_LIMIT_S", 0)
    me = S.join_session(conn, "Ana", "10.0.0.1", game_type="falacias")[1]
    fid = conn.execute("INSERT INTO fallacies (name, category, source) VALUES ('Ad hominem','C','manual') RETURNING id").fetchone()[0]
    CW.ingest(conn, live.id, CW.parse_lines([chat_line("k", "você é burro então está errado", ts_us=now_us(conn, 1))]))
    eid = feed.last_event_id(conn, live.id)
    m = F.submit_mark(conn, me.id, 'chat', eid, 0, eid, len("você é burro então está errado"), fid)
    assert m.quote == "você é burro então está errado"


# ---------------------------------------------------------------- worker steps (no thread, fake clock)
def test_step_starts_the_process_for_the_session_video(conn, test_db_url, live):
    f = Factory(FakeProcess())
    w = worker(conn, test_db_url, live.id, f)
    w.step()
    assert f.video_ids == [VID] and f.made[0].started and w.healthy()


def test_step_ingests_what_the_process_prints(conn, test_db_url, live):
    t = now_us(conn, 1)
    f = Factory(FakeProcess([[chat_line("a", "um", ts_us=t), chat_line("b", "dois", ts_us=t + 1)]]))
    w = worker(conn, test_db_url, live.id, f)
    w.step()
    assert [e[2] for e in events(conn, live.id)] == ["um", "dois"]


def test_restart_after_death_dedupes_the_backlog_the_new_process_replays(conn, test_db_url, live):
    t = now_us(conn, 1)
    first = FakeProcess([[chat_line("a", "um", ts_us=t)]], die_after=2)
    second = FakeProcess([[chat_line("a", "um", ts_us=t), chat_line("b", "dois", ts_us=t + 1)]])
    clock = Clock()
    w = worker(conn, test_db_url, live.id, Factory(first, second), clock)
    w.step(); w.step()                       # 2nd read kills it
    assert not w.healthy() or first.stopped
    clock.t += 100
    w.step(); w.step()
    assert [e[2] for e in events(conn, live.id)] == ["um", "dois"] and second.started


def test_dead_process_makes_the_worker_unhealthy_and_it_is_restarted_after_a_backoff(conn, test_db_url, live):
    clock = Clock()
    f = Factory(FakeProcess(die_after=1), FakeProcess())
    w = worker(conn, test_db_url, live.id, f, clock)
    w.step()
    w.step()                                  # sees the death
    assert not w.healthy() and len(f.made) == 1
    clock.t += 0.5
    w.step()
    assert len(f.made) == 1                   # still backing off
    clock.t += CW.BACKOFF_MAX_S
    w.step()
    assert len(f.made) == 2 and w.healthy()


def test_silent_process_is_a_stall_after_stall_seconds(conn, test_db_url, live):
    clock = Clock()
    p = FakeProcess()
    w = worker(conn, test_db_url, live.id, Factory(p, FakeProcess()), clock, stall_s=60)
    w.step()
    clock.t += 59
    w.step()
    assert w.healthy() and not p.stopped
    clock.t += 2
    w.step()
    assert p.stopped and not w.healthy()


def test_any_output_line_counts_as_activity_even_if_it_is_not_a_chat_text(conn, test_db_url, live):
    clock = Clock()
    x = json.dumps({"replayChatItemAction": {"actions": []}})
    p = FakeProcess([[x], [], [x]])
    w = worker(conn, test_db_url, live.id, Factory(p), clock, stall_s=60)
    w.step()
    clock.t += 50
    w.step()
    clock.t += 50                             # 100 s since start, but only 50 since the last line
    w.step()
    assert w.healthy() and not p.stopped


def test_start_failure_is_unhealthy_and_retried_with_growing_backoff(conn, test_db_url, live):
    clock = Clock()
    f = Factory(*[FakeProcess(fail_start=True) for _ in range(3)], FakeProcess())
    w = worker(conn, test_db_url, live.id, f, clock)
    gaps = []
    for _ in range(3):
        w.step()
        assert not w.healthy()
        gaps.append(w.retry_in())
        clock.t += w.retry_in() + 0.01
    assert gaps[0] < gaps[1] <= gaps[2] <= CW.BACKOFF_MAX_S
    w.step()
    assert w.healthy()


def test_backoff_resets_after_a_healthy_run(conn, test_db_url, live):
    clock = Clock()
    t = now_us(conn, 1)
    f = Factory(FakeProcess(fail_start=True), FakeProcess([[chat_line("a", "x", ts_us=t)]], die_after=3), FakeProcess())
    w = worker(conn, test_db_url, live.id, f, clock)
    w.step(); first = w.retry_in()
    clock.t += first + 0.01
    w.step(); w.step(); w.step()              # healthy run, then it dies
    w.step()
    assert w.retry_in() <= first


def test_worker_stops_its_process_when_the_session_ends(conn, test_db_url, live):
    p = FakeProcess()
    w = worker(conn, test_db_url, live.id, Factory(p))
    w.step()
    S.end_session(conn, live.id, None)
    assert w.step() == "ended" and p.stopped


def test_worker_with_unknown_session_ends_without_starting_anything(conn, test_db_url):
    import uuid
    f = Factory(FakeProcess())
    w = worker(conn, test_db_url, uuid.uuid4(), f)
    assert w.step() == "ended" and f.made == []


def test_a_read_error_does_not_kill_the_worker(conn, test_db_url, live):
    class Boom(FakeProcess):
        def read_lines(self):
            raise OSError("disk")
    f = Factory(Boom(), FakeProcess())
    w = worker(conn, test_db_url, live.id, f)
    w.step()
    assert not w.healthy() or f.made[0].stopped


def test_a_database_error_while_ingesting_is_unhealthy_not_fatal(conn, test_db_url, live):
    def bad():
        raise OSError("db down")
    w = CW.LiveChatWorker(live.id, VID, Factory(FakeProcess()), bad, clock=Clock())
    assert w.step() != "ended" and not w.healthy()


# ---------------------------------------------------------------- the registry used by the pages
def test_ensure_starts_one_thread_per_session_and_ingests(conn, test_db_url, live, monkeypatch):
    t = now_us(conn, 1)
    f = Factory(FakeProcess([[chat_line("a", "pelo thread", ts_us=t)]]))
    monkeypatch.setattr(CW, "process_factory", f)
    monkeypatch.setattr(CW, "connect_fn", lambda: connect(test_db_url))
    w1 = CW.ensure(live.id, VID)
    w2 = CW.ensure(live.id, VID)
    assert w1 is w2
    assert wait_until(lambda: len(events(conn, live.id)) == 1)
    assert len(f.made) == 1


def test_ensure_thread_exits_and_unregisters_when_the_session_ends(conn, test_db_url, live, monkeypatch):
    monkeypatch.setattr(CW, "process_factory", Factory(FakeProcess()))
    monkeypatch.setattr(CW, "connect_fn", lambda: connect(test_db_url))
    monkeypatch.setattr(CW, "POLL_S", 0.02)
    CW.ensure(live.id, VID)
    S.end_session(conn, live.id, None)
    assert wait_until(lambda: CW.get(live.id) is None)


def test_health_of_unknown_session_is_ok_and_of_a_failing_one_is_down(conn, test_db_url, live, monkeypatch):
    assert CW.health(live.id) == "ok"          # nothing runs yet: the next tick starts it
    monkeypatch.setattr(CW, "process_factory", Factory(FakeProcess(fail_start=True)))
    monkeypatch.setattr(CW, "connect_fn", lambda: connect(test_db_url))
    CW.ensure(live.id, VID)
    assert wait_until(lambda: CW.health(live.id) == "down")


# ---------------------------------------------------------------- wiring into feed.tick / feed.status
def test_tick_of_a_live_session_starts_the_chat_worker(conn, test_db_url, live, monkeypatch):
    f = Factory(FakeProcess())
    monkeypatch.setattr(CW, "process_factory", f)
    monkeypatch.setattr(CW, "connect_fn", lambda: connect(test_db_url))
    feed.tick(conn, live.id)
    assert wait_until(lambda: f.video_ids == [VID])


def test_tick_of_a_replay_session_never_starts_a_chat_worker(conn, test_db_url, monkeypatch):
    f = Factory(FakeProcess())
    monkeypatch.setattr(CW, "process_factory", f)
    conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, text) VALUES (%s,'transcript',1,'a'),(%s,'chat',2,'b')",
                 (VID, VID))
    s = feed.start_falacias_as_admin(conn, feed.Source("replay", VID))
    feed.tick(conn, s.id)
    assert f.made == [] and CW.get(s.id) is None


def test_tick_of_an_ended_live_session_starts_nothing(conn, test_db_url, live, monkeypatch):
    f = Factory(FakeProcess())
    monkeypatch.setattr(CW, "process_factory", f)
    S.end_session(conn, live.id, None)
    feed.tick(conn, live.id)
    assert f.made == [] and CW.get(live.id) is None


def test_feed_status_of_a_live_session_follows_the_worker_health(conn, test_db_url, live, monkeypatch):
    monkeypatch.setattr(CW, "process_factory", Factory(FakeProcess(fail_start=True)))
    monkeypatch.setattr(CW, "connect_fn", lambda: connect(test_db_url))
    assert feed.status(conn, live.id) == "ok"
    CW.ensure(live.id, VID)
    assert wait_until(lambda: feed.status(conn, live.id) == "down")


def test_ensure_up_refuses_marks_when_the_chat_is_down(conn, test_db_url, live, monkeypatch):
    monkeypatch.setattr(CW, "process_factory", Factory(FakeProcess(fail_start=True)))
    monkeypatch.setattr(CW, "connect_fn", lambda: connect(test_db_url))
    CW.ensure(live.id, VID)
    assert wait_until(lambda: CW.health(live.id) == "down")
    with pytest.raises(feed.FeedDown):
        feed.ensure_up(conn, live.id)


# ---------------------------------------------------------------- the real yt-dlp process (no network: a fake command)
def test_ytdlp_process_tails_the_part_file_and_keeps_partial_lines(tmp_path, monkeypatch):
    p = CW.YtdlpChat(VID, workdir=tmp_path, command=["true"])
    part = tmp_path / "chat.live_chat.json.part"
    part.write_text('{"a": 1}\n{"b"')
    assert p.read_lines() == ['{"a": 1}']
    with part.open("a") as fh:
        fh.write(': 2}\n{"c": 3}\n')
    assert p.read_lines() == ['{"b": 2}', '{"c": 3}']
    assert p.read_lines() == []


def test_ytdlp_process_reads_the_final_file_when_the_part_is_gone(tmp_path):
    p = CW.YtdlpChat(VID, workdir=tmp_path, command=["true"])
    (tmp_path / "chat.live_chat.json").write_text('{"z": 1}\n')
    assert p.read_lines() == ['{"z": 1}']


def test_ytdlp_process_start_runs_the_command_in_its_workdir_and_stop_cleans_up(tmp_path):
    marker = tmp_path / "cwd.txt"
    p = CW.YtdlpChat(VID, command=["sh", "-c", f"pwd > {marker}; sleep 30"])
    p.start()
    assert wait_until(marker.exists)
    assert Path(marker.read_text().strip()).samefile(p.workdir) and p.alive()
    p.stop()
    assert not p.alive() and not p.workdir.exists()


def test_ytdlp_default_command_targets_the_watch_url_with_live_chat_only():
    cmd = CW.ytdlp_command(VID)
    assert cmd[-1] == f"https://www.youtube.com/watch?v={VID}"
    assert "--skip-download" in cmd and cmd[cmd.index("--sub-langs") + 1] == "live_chat"
    assert "--write-subs" in cmd and cmd[cmd.index("-o") + 1] == "chat"


def test_ytdlp_rejects_an_id_that_is_not_a_youtube_id():
    with pytest.raises(ValueError):
        CW.ytdlp_command("x; rm -rf /")


def test_stop_is_safe_before_start_and_twice(tmp_path):
    p = CW.YtdlpChat(VID, workdir=tmp_path / "w", command=["true"])
    p.stop(); p.stop()


def test_ingest_ignores_a_replay_session(conn):
    conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, text) VALUES (%s,'transcript',1,'a'),(%s,'chat',2,'b')", (VID, VID))
    s = feed.start_falacias_as_admin(conn, feed.Source("replay", VID))
    assert CW.ingest(conn, s.id, CW.parse_lines([chat_line("k", "x", ts_us=now_us(conn, 1))])) == 0


def test_stop_really_terminates_the_child_process(tmp_path):
    p = CW.YtdlpChat(VID, command=["sh", "-c", "sleep 30"])
    p.start()
    child = p._proc
    assert child.poll() is None
    p.stop()
    assert child.poll() is not None


def test_backoff_is_capped_after_many_failures(conn, test_db_url, live):
    clock = Clock()
    f = Factory(*[FakeProcess(fail_start=True) for _ in range(12)])
    w = worker(conn, test_db_url, live.id, f, clock)
    for _ in range(12):
        clock.t += 1000
        w.step()
    assert w.retry_in() == CW.BACKOFF_MAX_S
