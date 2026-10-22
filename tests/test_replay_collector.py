"""Replay collector (app/replaycollector.py): takes queued videos, probes them, downloads transcript + chat, imports, marks ready/failed.
Nothing here starts yt-dlp or reaches YouTube: the downloader is a fake."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app import replaycollector as RC
from app import replays as R
from app.db import connect

VID = "abcdefghijk"
VID2 = "lmnopqrstuv"

VTT = ("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nolá <00:00:01.500>mundo <00:00:02.000>aqui\n\n"
       "00:00:04.000 --> 00:00:06.000\nsegunda <00:00:04.500>linha <00:00:05.000>de teste\n\n")


def chat_json(n=3) -> str:
    out = []
    for i in range(n):
        item = {"liveChatTextMessageRenderer": {"authorName": {"simpleText": f"Ana{i}"}, "message": {"runs": [{"text": f"oi {i}"}]}}}
        out.append(json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(1000 * (i + 1)),
                                                        "actions": [{"addChatItemAction": {"item": item}}]}}))
    return "\n".join(out) + "\n"


def meta(**kw) -> RC.Meta:
    base = {"title": "Um debate", "width": 1920, "height": 1080, "duration_s": 3600, "live_status": "was_live",
            "has_pt_captions": True, "has_chat": True}
    return RC.Meta(**{**base, **kw})


class FakeDownloader:
    def __init__(self, metas=None, vtt=VTT, chat=None, probe_error=None, download_error=None, on_download=None):
        self.metas = metas or {}
        self.default = meta()
        self.vtt, self.chat = vtt, chat_json() if chat is None else chat
        self.probe_error, self.download_error, self.on_download = probe_error, download_error, on_download
        self.probed: list[str] = []
        self.downloaded: list[str] = []

    def probe(self, video_id):
        self.probed.append(video_id)
        if isinstance(self.probe_error, Exception):
            raise self.probe_error
        return self.metas.get(video_id, self.default)

    def download(self, video_id, dest):
        self.downloaded.append(video_id)
        if self.on_download:
            self.on_download(video_id)
        if isinstance(self.download_error, Exception):
            raise self.download_error
        d = Path(dest) / video_id
        d.mkdir(parents=True, exist_ok=True)
        if self.vtt is not None:
            (d / f"{video_id}.pt-orig.vtt").write_text(self.vtt, encoding="utf-8")
        if self.chat is not None:
            (d / f"{video_id}.live_chat.json").write_text(self.chat, encoding="utf-8")


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users, replay_events, replays CASCADE")
    yield c
    c.execute("TRUNCATE game_sessions, users, replay_events, replays CASCADE")
    c.close()


@pytest.fixture
def mk(conn, test_db_url, tmp_path):
    def make(dl=None, **kw):
        return RC.Collector(lambda: connect(test_db_url), dl or FakeDownloader(), workdir_root=tmp_path, **kw)
    return make


# ------------------------------------------------------------------ one job
def test_idle_when_the_queue_is_empty(mk):
    assert mk().step() == "idle"


def test_a_queued_video_becomes_ready_with_metadata_and_rows(conn, mk):
    R.submit(conn, VID)
    dl = FakeDownloader()
    assert mk(dl).step() == "ready"
    r = R.get(conn, VID)
    assert (r.status, r.title, r.width, r.height, r.duration_s) == ("ready", "Um debate", 1920, 1080, 3600)
    assert r.transcript_rows == 2 and r.chat_rows == 3 and r.error == ""
    assert conn.execute("SELECT count(1) FROM replay_events WHERE video_id=%s", (VID,)).fetchone()[0] == 5
    assert dl.probed == [VID] and dl.downloaded == [VID]


def test_the_job_is_visible_as_downloading_then_importing(conn, mk, monkeypatch):
    R.submit(conn, VID)
    seen = []
    from corpus import replay as CR
    real = CR.import_video

    def spy(*a, **kw):
        seen.append(("import", R.get(conn, VID).status))
        return real(*a, **kw)
    monkeypatch.setattr(CR, "import_video", spy)
    dl = FakeDownloader(on_download=lambda v: seen.append(("download", R.get(conn, v).status)))
    mk(dl).step()
    assert seen == [("download", "downloading"), ("import", "importing")]


def test_jobs_run_in_fifo_order_one_per_step(conn, mk):
    R.submit(conn, VID)
    R.submit(conn, VID2)
    dl = FakeDownloader()
    c = mk(dl)
    assert c.step() == "ready" and dl.downloaded == [VID]
    assert c.step() == "ready" and dl.downloaded == [VID, VID2]
    assert c.step() == "idle"


def test_the_work_directory_is_removed_after_success_and_after_failure(conn, mk, tmp_path):
    R.submit(conn, VID)
    R.submit(conn, VID2)
    dl = FakeDownloader(metas={VID2: meta(live_status="is_live")})
    c = mk(dl)
    c.step()
    c.step()
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------ refusals (checked BEFORE downloading anything)
@pytest.mark.parametrize("status, word", [("is_live", "ao vivo"), ("is_upcoming", "ainda não começou"), ("post_live", "processando")])
def test_a_video_that_is_not_a_finished_recording_is_refused(conn, mk, status, word):
    R.submit(conn, VID)
    dl = FakeDownloader(metas={VID: meta(live_status=status)})
    assert mk(dl).step() == "failed"
    r = R.get(conn, VID)
    assert r.status == "failed" and word in r.error.lower()
    assert dl.downloaded == []


def test_a_vertical_video_is_refused_and_an_unknown_size_is_accepted(conn, mk):
    R.submit(conn, VID)
    R.submit(conn, VID2)
    dl = FakeDownloader(metas={VID: meta(width=1080, height=1920), VID2: meta(width=None, height=None)})
    c = mk(dl)
    assert c.step() == "failed"
    assert "horizontal" in R.get(conn, VID).error.lower()
    assert c.step() == "ready"
    assert dl.downloaded == [VID2]


def test_a_square_video_is_refused_too(conn, mk):
    R.submit(conn, VID)
    assert mk(FakeDownloader(metas={VID: meta(width=1000, height=1000)})).step() == "failed"


def test_the_length_cap_is_inclusive(conn, mk):
    R.submit(conn, VID)
    R.submit(conn, VID2)
    dl = FakeDownloader(metas={VID: meta(duration_s=12 * 3600 + 1), VID2: meta(duration_s=12 * 3600)})
    c = mk(dl, max_hours=12)
    assert c.step() == "failed" and "12" in R.get(conn, VID).error
    assert c.step() == "ready"


def test_missing_portuguese_captions_and_missing_chat_say_so(conn, mk):
    R.submit(conn, VID)
    R.submit(conn, VID2)
    dl = FakeDownloader(metas={VID: meta(has_pt_captions=False), VID2: meta(has_chat=False)})
    c = mk(dl)
    c.step()
    c.step()
    assert "legendas" in R.get(conn, VID).error.lower() and "horas" in R.get(conn, VID).error.lower()
    assert "chat" in R.get(conn, VID2).error.lower()
    assert dl.downloaded == []


# ------------------------------------------------------------------ failures while downloading / importing
def test_a_download_error_fails_the_job_with_its_message(conn, mk):
    R.submit(conn, VID)
    dl = FakeDownloader(download_error=RC.CollectError("YouTube recusou o download"))
    assert mk(dl).step() == "failed"
    r = R.get(conn, VID)
    assert r.status == "failed" and "recusou" in r.error


def test_a_probe_error_fails_the_job(conn, mk):
    R.submit(conn, VID)
    assert mk(FakeDownloader(probe_error=RC.CollectError("vídeo indisponível"))).step() == "failed"
    assert "indisponível" in R.get(conn, VID).error


def test_an_unexpected_exception_fails_the_job_and_the_next_one_still_runs(conn, mk):
    R.submit(conn, VID)
    R.submit(conn, VID2)
    dl = FakeDownloader(probe_error=ZeroDivisionError("boom"))
    c = mk(dl)
    assert c.step() == "failed"
    assert "inesperado" in R.get(conn, VID).error.lower()
    dl.probe_error = None
    assert c.step() == "ready"


@pytest.mark.parametrize("vtt, chat", [("WEBVTT\n\n", None), (VTT, ""), (None, None)])
def test_empty_or_missing_tracks_fail_and_leave_no_rows(conn, mk, vtt, chat):
    R.submit(conn, VID)
    dl = FakeDownloader(vtt=vtt)
    dl.chat = chat
    if chat is None and vtt is not None:
        dl.chat = chat_json()
    assert mk(dl).step() == "failed"
    assert R.get(conn, VID).status == "failed"
    assert conn.execute("SELECT count(1) FROM replay_events WHERE video_id=%s", (VID,)).fetchone()[0] == 0


def test_a_failed_video_can_be_submitted_and_collected_again(conn, mk):
    R.submit(conn, VID)
    dl = FakeDownloader(download_error=RC.CollectError("x"))
    c = mk(dl)
    c.step()
    dl.download_error = None
    R.submit(conn, VID)
    assert c.step() == "ready"


def test_the_error_text_is_short_and_has_no_traceback(conn, mk):
    R.submit(conn, VID)
    mk(FakeDownloader(probe_error=RuntimeError("x" * 3000))).step()
    assert len(R.get(conn, VID).error) <= R.MAX_ERROR


# ------------------------------------------------------------------ the thread
def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_the_thread_collects_when_woken_and_stops(conn, mk):
    c = mk(FakeDownloader(), poll_s=60)
    c.start()
    time.sleep(0.3)                                                # the first (empty) loop is over; now only a wake can help
    try:
        R.submit(conn, VID)
        c.wake()
        assert wait_for(lambda: R.get(conn, VID).status == "ready")
        R.submit(conn, VID2)
        c.wake()
        assert wait_for(lambda: R.get(conn, VID2).status == "ready")
    finally:
        c.stop()
    assert not c.running()


def test_the_thread_also_polls_without_a_wake(conn, mk):
    c = mk(FakeDownloader(), poll_s=0.05)
    R.submit(conn, VID)
    c.start()
    try:
        assert wait_for(lambda: R.get(conn, VID).status == "ready")
    finally:
        c.stop()


def test_start_fails_jobs_left_running_by_a_restart_but_keeps_queued_ones(conn, mk):
    R.submit(conn, VID)
    R.claim_next(conn)                                             # a job that was running when the server died
    R.submit(conn, VID2)
    c = mk(FakeDownloader(), poll_s=0.05)
    c.start()
    try:
        assert wait_for(lambda: R.get(conn, VID2).status == "ready")
    finally:
        c.stop()
    assert R.get(conn, VID).status == "failed" and "interrompid" in R.get(conn, VID).error.lower()


def test_the_thread_survives_a_database_error(conn, test_db_url, tmp_path):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("db down")
        return connect(test_db_url)
    c = RC.Collector(flaky, FakeDownloader(), workdir_root=tmp_path, poll_s=0.05)
    R.submit(conn, VID)
    c.start()
    try:
        assert wait_for(lambda: R.get(conn, VID).status == "ready")
    finally:
        c.stop()


def test_start_twice_runs_one_thread(mk):
    c = mk()
    c.start()
    t = c._thread
    c.start()
    try:
        assert c._thread is t
    finally:
        c.stop()


# ------------------------------------------------------------------ the yt-dlp adapter (fetch functions are faked)
def test_the_ytdlp_downloader_maps_metadata_and_passes_timeouts(monkeypatch):
    from corpus import fetch as F
    seen = {}

    def get_meta(video_id, timeout=None):
        seen["meta"] = (video_id, timeout)
        return {"title": "T", "duration": 100, "live_status": "was_live", "width": 1280, "height": 720,
                "transcript_langs_available": ["pt-orig"], "has_live_chat": True}
    monkeypatch.setattr(F, "get_meta", get_meta)
    m = RC.YtdlpDownloader(probe_timeout=11, download_timeout=22).probe(VID)
    assert m == RC.Meta("T", 1280, 720, 100, "was_live", True, True) and seen["meta"] == (VID, 11)


def test_the_ytdlp_downloader_reports_missing_tracks_and_tool_errors(monkeypatch, tmp_path):
    from corpus import fetch as F
    meta_d = {"title": "T", "duration": 1, "live_status": "was_live", "width": 1, "height": 1,
              "transcript_langs_available": ["pt-orig"], "has_live_chat": True}
    monkeypatch.setattr(F, "get_meta", lambda video_id, timeout=None: meta_d)
    seen = {}

    def download(video_id, out, m, timeout=None):
        seen["args"] = (video_id, Path(out), timeout)
        return {"transcript": None, "chat": "x", "errors": ["transcript: 429 Too Many Requests"]}
    monkeypatch.setattr(F, "download", download)
    d = RC.YtdlpDownloader(probe_timeout=11, download_timeout=22)
    with pytest.raises(RC.CollectError, match="429"):
        d.download(VID, tmp_path)
    assert seen["args"] == (VID, tmp_path, 22)

    def boom(video_id, timeout=None):
        raise RuntimeError("metadata failed for x: ERROR: Video unavailable")
    monkeypatch.setattr(F, "get_meta", boom)
    with pytest.raises(RC.CollectError, match="unavailable"):
        d.probe(VID)


def test_the_module_helpers_respect_the_off_switch(monkeypatch, test_db_url):
    monkeypatch.setenv("REPLAY_COLLECTOR", "off")
    assert RC.enabled() is False
    RC.ensure_started(test_db_url)
    assert RC.current() is None
    monkeypatch.setenv("REPLAY_COLLECTOR", "on")
    assert RC.enabled() is True


# ------------------------------------------------------------------ gaps found by the mutation check
def test_empty_tracks_give_a_specific_message(conn, mk):
    R.submit(conn, VID)
    mk(FakeDownloader(vtt="WEBVTT\n\n")).step()
    assert "vazios" in R.get(conn, VID).error.lower()


def test_the_thread_drains_the_whole_queue_on_one_wake(conn, mk):
    R.submit(conn, VID)
    R.submit(conn, VID2)
    c = mk(FakeDownloader(), poll_s=60)
    c.start()
    try:
        assert wait_for(lambda: R.get(conn, VID).status == "ready" and R.get(conn, VID2).status == "ready")
    finally:
        c.stop()


def test_recovery_runs_once_per_thread_not_on_every_loop(conn, mk):
    c = mk(FakeDownloader(), poll_s=60)
    c.start()
    try:
        R.submit(conn, VID)
        c.wake()
        assert wait_for(lambda: R.get(conn, VID).status == "ready")
        R.submit(conn, VID2)
        R.claim_next(conn)                                         # a job in flight (as if run by another process)
        c.wake()
        time.sleep(0.3)
        assert R.get(conn, VID2).status == "downloading"
    finally:
        c.stop()


def test_the_ytdlp_downloader_reads_false_flags(monkeypatch):
    from corpus import fetch as F
    monkeypatch.setattr(F, "get_meta", lambda video_id, timeout=None: {
        "title": None, "duration": None, "live_status": "was_live", "width": None, "height": None,
        "transcript_langs_available": [], "has_live_chat": False})
    m = RC.YtdlpDownloader().probe(VID)
    assert (m.has_pt_captions, m.has_chat, m.title) == (False, False, "")
