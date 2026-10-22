"""Replay catalogue (migration 0010, app/replays.py): submit a URL, a FIFO job queue, status transitions, delete, and the hooks that
register imported / seeded videos as ready."""
from __future__ import annotations

import threading

import pytest

from app import feed as FD
from app import replays as R
from app import sessions as S
from app.db import connect

VID = "abcdefghijk"
VID2 = "lmnopqrstuv"


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users, replay_events, replays CASCADE")
    yield c
    c.execute("TRUNCATE game_sessions, users, replay_events, replays CASCADE")
    c.close()


def code_of(fn, *a, **kw):
    with pytest.raises(R.ReplayError) as e:
        fn(*a, **kw)
    return e.value.code


def put_rows(conn, vid, transcript=3, chat=2):
    for i in range(transcript):
        conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, text) VALUES (%s,'transcript',%s,%s)", (vid, i * 1000, f"t{i}"))
    for i in range(chat):
        conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES (%s,'chat',%s,'a',%s)", (vid, i * 1000, f"c{i}"))


def finish(conn, vid=VID):
    r = R.claim_next(conn)
    assert r is not None and r.video_id == vid
    put_rows(conn, vid)
    return R.mark_ready(conn, vid, title="Um debate", width=1920, height=1080, duration_s=3600)


# ------------------------------------------------------------------ submit
def test_submit_queues_a_new_video_from_an_id_or_a_url(conn):
    r = R.submit(conn, f"https://www.youtube.com/watch?v={VID}")
    assert (r.video_id, r.status, r.error) == (VID, "queued", "")
    assert r.url == f"https://www.youtube.com/watch?v={VID}"
    assert R.submit(conn, VID2).video_id == VID2
    assert [x.video_id for x in R.list_replays(conn)] == [VID2, VID]               # newest first


@pytest.mark.parametrize("raw", ["", "not a url", "https://evil.example/watch?v=abcdefghijk", "ftp://youtube.com/watch?v=abcdefghijk", "abc"])
def test_submit_refuses_anything_that_is_not_a_youtube_video(conn, raw):
    with pytest.raises(FD.InvalidVideo):
        R.submit(conn, raw)
    assert conn.execute("SELECT count(1) FROM replays").fetchone()[0] == 0


def test_submit_of_a_known_video_is_refused_with_its_state(conn):
    R.submit(conn, VID)
    assert code_of(R.submit, conn, VID) == "already_running"
    finish(conn)
    assert code_of(R.submit, conn, VID) == "already_ready"
    assert conn.execute("SELECT count(1) FROM replays").fetchone()[0] == 1


def test_submit_of_a_failed_video_requeues_it_and_clears_the_error(conn):
    R.submit(conn, VID)
    R.claim_next(conn)
    R.mark_failed(conn, VID, "sem legendas")
    r = R.submit(conn, VID)
    assert (r.status, r.error) == ("queued", "")


def test_the_queue_is_capped(conn):
    for i in range(R.MAX_QUEUED):
        R.submit(conn, f"{i:011d}")
    assert code_of(R.submit, conn, "zzzzzzzzzzz") == "queue_full"


def test_concurrent_submits_of_the_same_video_make_one_row(conn, test_db_url):
    out, barrier = [], threading.Barrier(6)

    def go():
        c = connect(test_db_url)
        try:
            barrier.wait()
            out.append(R.submit(c, VID).status)
        except R.ReplayError as e:
            out.append(e.code)
        finally:
            c.close()

    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out.count("queued") == 1 and out.count("already_running") == 5
    assert conn.execute("SELECT count(1) FROM replays").fetchone()[0] == 1


# ------------------------------------------------------------------ queue / transitions
def test_claim_takes_the_oldest_queued_job_once(conn):
    R.submit(conn, VID)
    R.submit(conn, VID2)
    a = R.claim_next(conn)
    assert (a.video_id, a.status) == (VID, "downloading")
    b = R.claim_next(conn)
    assert b.video_id == VID2
    assert R.claim_next(conn) is None


def test_parallel_claims_never_give_the_same_job_twice(conn, test_db_url):
    for i in range(3):
        R.submit(conn, f"{i:011d}")
    got, barrier = [], threading.Barrier(6)

    def go():
        c = connect(test_db_url)
        try:
            barrier.wait()
            r = R.claim_next(c)
            got.append(r.video_id if r else None)
        finally:
            c.close()

    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(g for g in got if g) == [f"{i:011d}" for i in range(3)] and got.count(None) == 3


def test_full_happy_path_records_counts_and_metadata(conn):
    R.submit(conn, VID)
    R.claim_next(conn)
    assert R.mark_importing(conn, VID).status == "importing"
    put_rows(conn, VID, transcript=5, chat=4)
    r = R.mark_ready(conn, VID, title="Um debate", width=1920, height=1080, duration_s=3600)
    assert (r.status, r.title, r.width, r.height, r.duration_s) == ("ready", "Um debate", 1920, 1080, 3600)
    assert (r.transcript_rows, r.chat_rows) == (5, 4)
    assert r.finished_at is not None and r.error == ""


def test_mark_ready_needs_rows_of_both_kinds(conn):
    R.submit(conn, VID)
    R.claim_next(conn)
    put_rows(conn, VID, transcript=3, chat=0)
    assert code_of(R.mark_ready, conn, VID, title="x", width=1, height=1, duration_s=1) == "incomplete"
    assert R.get(conn, VID).status == "downloading"


def test_mark_failed_keeps_the_error_and_drops_partial_rows(conn):
    R.submit(conn, VID)
    R.claim_next(conn)
    put_rows(conn, VID)
    r = R.mark_failed(conn, VID, "x" * 5000)
    assert r.status == "failed" and 0 < len(r.error) <= R.MAX_ERROR
    assert conn.execute("SELECT count(1) FROM replay_events WHERE video_id=%s", (VID,)).fetchone()[0] == 0


def test_a_ready_replay_cannot_be_failed_or_claimed_again(conn):
    R.submit(conn, VID)
    finish(conn)
    assert code_of(R.mark_failed, conn, VID, "boom") == "bad_state"
    assert R.claim_next(conn) is None
    assert R.get(conn, VID).status == "ready"


def test_transitions_on_an_unknown_video_say_not_found(conn):
    assert code_of(R.mark_importing, conn, VID) == "not_found"
    assert code_of(R.mark_failed, conn, VID, "x") == "not_found"
    assert R.get(conn, VID) is None


def test_recover_interrupted_fails_running_jobs_but_keeps_queued_and_ready_ones(conn):
    R.submit(conn, VID)
    R.submit(conn, VID2)
    R.claim_next(conn)                                         # VID downloading
    third = "qqqqqqqqqqq"
    R.submit(conn, third)
    assert R.recover_interrupted(conn) == 1
    states = {r.video_id: r.status for r in R.list_replays(conn)}
    assert states == {VID: "failed", VID2: "queued", third: "queued"}
    assert "interrompid" in R.get(conn, VID).error.lower()


# ------------------------------------------------------------------ readiness / delete
def test_is_ready_follows_the_catalogue_not_just_the_rows(conn):
    put_rows(conn, VID)                                        # rows without a catalogue entry
    assert R.is_ready(conn, VID) is False
    R.submit(conn, VID2)
    assert R.is_ready(conn, VID2) is False                     # queued
    R.claim_next(conn)
    put_rows(conn, VID2)
    R.mark_ready(conn, VID2, title="t", width=1920, height=1080, duration_s=10)
    assert R.is_ready(conn, VID2) is True
    assert [r.video_id for r in R.ready_replays(conn)] == [VID2]


def test_delete_removes_the_row_and_its_events(conn):
    R.submit(conn, VID)
    finish(conn)
    R.delete(conn, VID)
    assert R.get(conn, VID) is None
    assert conn.execute("SELECT count(1) FROM replay_events WHERE video_id=%s", (VID,)).fetchone()[0] == 0


def test_delete_is_refused_while_a_game_uses_it_but_allowed_after(conn):
    R.submit(conn, VID)
    finish(conn)
    s = FD.start_falacias_as_admin(conn, FD.Source("replay", VID))
    assert code_of(R.delete, conn, VID) == "in_use"
    assert R.get(conn, VID).status == "ready"
    S.end_session(conn, s.id, None)
    R.delete(conn, VID)
    assert R.get(conn, VID) is None


def test_delete_of_a_running_job_is_refused(conn):
    R.submit(conn, VID)
    assert code_of(R.delete, conn, VID) == "busy"
    R.claim_next(conn)
    assert code_of(R.delete, conn, VID) == "busy"


def test_delete_of_a_failed_or_unknown_video(conn):
    R.submit(conn, VID)
    R.claim_next(conn)
    R.mark_failed(conn, VID, "boom")
    R.delete(conn, VID)
    assert code_of(R.delete, conn, VID) == "not_found"


# ------------------------------------------------------------------ registration hooks
def test_migration_backfills_videos_that_already_have_both_tracks(test_db_url):
    c = connect(test_db_url)
    try:
        c.execute("TRUNCATE replay_events, replays CASCADE")
        put_rows(c, VID)
        put_rows(c, VID2, transcript=2, chat=0)                 # half a video is not backfilled
        sql = (R.MIGRATION_0010).read_text(encoding="utf-8")
        marker = "-- BACKFILL"
        assert marker in sql
        c.execute(sql.split(marker, 1)[1].split("\n", 1)[1])
        got = {r.video_id: (r.status, r.transcript_rows, r.chat_rows) for r in R.list_replays(c)}
        assert got == {VID: ("ready", 3, 2)}
    finally:
        c.execute("TRUNCATE replay_events, replays CASCADE")
        c.close()


def test_register_ready_creates_or_completes_a_catalogue_entry(conn):
    put_rows(conn, VID, transcript=4, chat=3)
    r = R.register_ready(conn, VID)
    assert (r.status, r.transcript_rows, r.chat_rows, r.url) == ("ready", 4, 3, f"https://www.youtube.com/watch?v={VID}")
    assert R.register_ready(conn, VID).status == "ready"       # idempotent
    assert conn.execute("SELECT count(1) FROM replays").fetchone()[0] == 1


def test_register_ready_refuses_a_video_without_both_tracks(conn):
    put_rows(conn, VID, transcript=2, chat=0)
    assert code_of(R.register_ready, conn, VID) == "incomplete"
    assert R.get(conn, VID) is None


def test_the_replay_seed_loader_registers_the_video(conn, tmp_path):
    seed = tmp_path / "s.sql"
    seed.write_text("INSERT INTO replay_events (video_id, kind, offset_ms, text) VALUES "
                    f"('{VID}','transcript',0,'a'),('{VID}','chat',0,'b');", encoding="utf-8")
    assert FD.load_replay_seed(conn, VID, seed) == {"inserted": 2}
    assert R.get(conn, VID).status == "ready"


def test_the_importer_registers_the_video(conn, tmp_path):
    from corpus import replay as CR
    d = tmp_path / VID
    d.mkdir()
    (d / f"{VID}.pt-orig.vtt").write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nolá <00:00:01.500>mundo <00:00:02.000>aqui\n\n", encoding="utf-8")
    (d / f"{VID}.live_chat.json").write_text(
        '{"replayChatItemAction":{"videoOffsetTimeMsec":"1000","actions":[{"addChatItemAction":{"item":{"liveChatTextMessageRenderer":'
        '{"authorName":{"simpleText":"Ana"},"message":{"runs":[{"text":"oi"}]}}}}}]}}\n', encoding="utf-8")
    CR.import_video(conn, VID, tmp_path)
    r = R.get(conn, VID)
    assert r is not None and r.status == "ready" and r.transcript_rows > 0 and r.chat_rows > 0


# ------------------------------------------------------------------ gaps found by the mutation check
def test_a_requeued_failed_video_goes_to_the_back_of_the_queue(conn):
    R.submit(conn, VID)
    R.claim_next(conn)
    R.mark_failed(conn, VID, "boom")
    R.submit(conn, VID2)
    R.submit(conn, VID)                                        # retry: now after VID2
    assert R.claim_next(conn).video_id == VID2
    assert R.claim_next(conn).video_id == VID


def test_mark_ready_only_from_downloading_or_importing(conn):
    R.submit(conn, VID)
    put_rows(conn, VID)
    assert code_of(R.mark_ready, conn, VID, title="t", width=1, height=1, duration_s=1) == "bad_state"        # still queued
    R.claim_next(conn)
    R.mark_ready(conn, VID, title="t", width=1, height=1, duration_s=1)
    assert code_of(R.mark_ready, conn, VID, title="t", width=1, height=1, duration_s=1) == "bad_state"        # already ready


def test_recover_interrupted_drops_the_partial_rows(conn):
    R.submit(conn, VID)
    R.claim_next(conn)
    put_rows(conn, VID)
    R.recover_interrupted(conn)
    assert conn.execute("SELECT count(1) FROM replay_events WHERE video_id=%s", (VID,)).fetchone()[0] == 0


def test_register_ready_leaves_a_running_job_to_its_worker(conn):
    R.submit(conn, VID)
    R.claim_next(conn)
    put_rows(conn, VID)
    r = R.register_ready(conn, VID)
    assert r.status == "downloading"


def test_the_database_refuses_a_failed_replay_without_an_error_and_a_ready_one_without_rows(conn):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO replays (video_id, url, status) VALUES ('aaaaaaaaaaa','u','failed')")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO replays (video_id, url, status, finished_at) VALUES ('bbbbbbbbbbb','u','ready', now())")
