"""Live speech-to-text worker (S1, app/sttworker.py). Audio capture, whisper.cpp and the clock are fakes; the database is real."""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from app import sessions as S
from app import sttworker as W
from app.db import connect
from app.sttworker import Segment
from tests.chat_fakes import wait_until
from tests.stt_fakes import (
    FakeSource,
    FakeTranscriber,
    SourceFactory,
    loud_chunk,
    make_wav,
    quiet_chunk,
    silence,
    tone,
)

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
    W.stop_all()
    yield
    W.stop_all()


@pytest.fixture
def live(conn):
    return S.start_session_as_admin(conn, game_type="falacias", video_id=VID, source_mode="live")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def worker(test_db_url, sid, source_factory, transcriber, clock=None, **kw):
    return W.SttWorker(sid, VID, source_factory, transcriber, lambda: connect(test_db_url), clock=clock or Clock(), **kw)


def rows(conn, sid):
    return conn.execute("SELECT source_key, author, text FROM stream_events WHERE session_id = %s AND kind = 'transcript' ORDER BY id",
                        (sid,)).fetchall()


# ---------------------------------------------------------------- cleaning
def test_clean_normalises_unicode_and_whitespace():
    assert W.clean_text("  a\u0301gua   é \n boa\t ") == "água é boa"


@pytest.mark.parametrize("raw", ["[Música]", "[Aplausos] ", "(Música)", "♪ ♪", "♫ la la ♫ [Música]", "...", "123", "   ", ""])
def test_clean_drops_noise_tags_music_and_text_without_letters(raw):
    assert W.clean_text(raw) == "" or W.clean_text(raw) == "la la"


def test_clean_removes_a_noise_tag_but_keeps_the_speech_around_it():
    assert W.clean_text("[Música] eu acho que sim") == "eu acho que sim"
    assert W.clean_text("ele disse (risos) que não") == "ele disse que não"
    assert W.clean_text("o argumento (ad hominem) é fraco") == "o argumento (ad hominem) é fraco"


@pytest.mark.parametrize("raw", [
    "Obrigado por assistir!", "obrigada por assistir.", "Legendas pela comunidade Amara.org", "Inscreva-se no canal",
    "Transcrição e legendas por Fulano", "Legenda Adriana Zanotto", "Subtitles by the Amara.org community",
])
def test_clean_drops_known_whisper_hallucinations(raw):
    assert W.clean_text(raw) == ""


def test_clean_keeps_a_real_single_word_and_a_sentence_that_mentions_thanks():
    assert W.clean_text("Exato.") == "Exato."
    assert W.clean_text("obrigado por assistir ao debate de ontem, pessoal") != ""      # only the WHOLE segment is a hallucination


def test_clean_drops_repetition_loops_but_not_natural_repetition():
    assert W.clean_text("obrigado obrigado obrigado obrigado") == ""
    assert W.clean_text("não sei, não sei, não sei, não sei, não sei, não sei") == ""
    assert W.clean_text("eu acho que eu acho") == "eu acho que eu acho"
    assert W.clean_text("não não") == "não não"


# ---------------------------------------------------------------- overlap between chunks
def test_trim_overlap_removes_the_repeated_words_at_the_start_of_the_new_text():
    assert W.trim_overlap("a gente precisa falar sobre isso", "falar sobre isso agora mesmo") == "agora mesmo"


def test_trim_overlap_ignores_case_and_punctuation():
    assert W.trim_overlap("vamos falar sobre isso.", "Sobre isso, agora") == "agora"


def test_trim_overlap_needs_two_words_so_a_real_repeat_survives():
    assert W.trim_overlap("eu não", "não sei") == "não sei"


def test_trim_overlap_identical_or_contained_text_becomes_empty():
    assert W.trim_overlap("isso é verdade", "isso é verdade") == ""
    assert W.trim_overlap("todo mundo sabe isso é verdade", "isso é verdade") == ""


def test_trim_overlap_without_overlap_leaves_the_text_alone():
    assert W.trim_overlap("primeira frase", "outra coisa") == "outra coisa"
    assert W.trim_overlap("", "qualquer coisa aqui") == "qualquer coisa aqui"


# ---------------------------------------------------------------- silence gate
def test_silence_and_very_low_noise_are_silent_speech_is_not():
    assert W.is_silent(make_wav(silence(2)))
    assert W.is_silent(make_wav(tone(2, amp=30)))
    assert not W.is_silent(make_wav(tone(2, amp=6000)))


def test_a_wav_that_cannot_be_read_counts_as_silent():
    assert W.is_silent(b"") and W.is_silent(b"not a wav at all")


# ---------------------------------------------------------------- whisper.cpp reply + HTTP client
def test_parse_whispercpp_verbose_json_segments_in_seconds_become_ms():
    segs = W.parse_whispercpp({"segments": [{"start": 0.0, "end": 2.5, "text": " olá pessoal"}, {"start": 2.5, "end": 4.0, "text": "tudo certo?"}]})
    assert segs == [Segment(0, 2500, "olá pessoal"), Segment(2500, 4000, "tudo certo?")]


def test_parse_whispercpp_plain_text_reply_is_one_segment():
    [s] = W.parse_whispercpp({"text": " só o texto "})
    assert s.text == "só o texto" and s.start_ms == 0


@pytest.mark.parametrize("payload", [None, [], "x", {}, {"segments": "nope"}, {"segments": [{"text": 5}]}, {"text": "   "}])
def test_parse_whispercpp_junk_gives_no_segments(payload):
    assert W.parse_whispercpp(payload) == []


class _Whisper(BaseHTTPRequestHandler):
    seen: ClassVar[list] = []
    status = 200
    reply: ClassVar[dict] = {"segments": [{"start": 0.0, "end": 1.0, "text": "oi"}]}

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).seen.append((self.path, self.headers["Content-Type"], body))
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(type(self).reply).encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def whisper_server():
    _Whisper.seen, _Whisper.status = [], 200
    srv = HTTPServer(("127.0.0.1", 0), _Whisper)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}", _Whisper
    srv.shutdown()
    srv.server_close()


def test_whispercpp_client_posts_the_wav_with_language_and_prompt_and_parses_the_reply(whisper_server):
    url, h = whisper_server
    wav = make_wav(tone(0.5))
    segs = W.WhisperCppTranscriber(url).transcribe(wav, prompt="ateísmo, falácia")
    assert segs == [Segment(0, 1000, "oi")]
    path, ctype, body = h.seen[0]
    assert path == "/inference" and ctype.startswith("multipart/form-data; boundary=")
    assert wav in body and b'name="language"' in body and b"pt" in body
    assert b'name="prompt"' in body and "ateísmo, falácia".encode() in body and b"verbose_json" in body


def test_whispercpp_client_raises_on_http_errors_and_on_a_dead_server(whisper_server):
    url, h = whisper_server
    h.status = 500
    with pytest.raises(W.TranscribeError):
        W.WhisperCppTranscriber(url).transcribe(make_wav(tone(0.2)))
    with pytest.raises(W.TranscribeError):
        W.WhisperCppTranscriber("http://127.0.0.1:1", timeout=1).transcribe(make_wav(tone(0.2)))


# ---------------------------------------------------------------- ingest
def test_ingest_stores_lines_in_a_live_falacias_session_and_is_idempotent(conn, test_db_url, live):
    w = connect(test_db_url)
    assert W.ingest(w, live.id, [W.Line("0:0", "primeira"), W.Line("0:1", "segunda")]) == 2
    assert W.ingest(w, live.id, [W.Line("0:0", "primeira"), W.Line("12000:0", "terceira")]) == 1     # retry of chunk 0 + a new one
    assert rows(conn, live.id) == [("0:0", None, "primeira"), ("0:1", None, "segunda"), ("12000:0", None, "terceira")]


def test_ingest_refuses_ended_replay_and_bingo_sessions(conn, test_db_url, live):
    w = connect(test_db_url)
    S.end_session(conn, live.id, None)
    assert W.ingest(w, live.id, [W.Line("0:0", "x")]) == 0
    conn.execute("TRUNCATE game_sessions, users CASCADE")
    replay = S.start_session_as_admin(conn, game_type="falacias", video_id=VID, source_mode="replay")
    assert W.ingest(w, replay.id, [W.Line("0:0", "x")]) == 0
    conn.execute("TRUNCATE game_sessions, users CASCADE")
    bingo = S.start_session_as_admin(conn, game_type="bingo")
    assert W.ingest(w, bingo.id, [W.Line("0:0", "x")]) == 0
    assert conn.execute("SELECT count(1) FROM stream_events").fetchone()[0] == 0


# ---------------------------------------------------------------- worker.step()
def test_a_chunk_is_transcribed_cleaned_and_stored_with_stable_keys(conn, test_db_url, live):
    tr = FakeTranscriber([Segment(0, 3000, " olá  pessoal "), Segment(3000, 5000, "[Música]"), Segment(5000, 9000, "vamos começar")])
    w = worker(test_db_url, live.id, SourceFactory(FakeSource([loud_chunk(24000)])), tr)
    assert w.step() == "ok" and w.healthy()
    assert rows(conn, live.id) == [("24000:0", None, "olá pessoal"), ("24000:2", None, "vamos começar")]


def test_the_prompt_is_sent_to_the_transcriber(conn, test_db_url, live):
    tr = FakeTranscriber("oi")
    worker(test_db_url, live.id, SourceFactory(FakeSource([loud_chunk()])), tr, prompt="ateísmo, falácia").step()
    assert tr.calls[0][1] == "ateísmo, falácia"


def test_a_silent_chunk_is_not_sent_to_the_transcriber(conn, test_db_url, live):
    tr = FakeTranscriber("não deveria ser chamado")
    w = worker(test_db_url, live.id, SourceFactory(FakeSource([quiet_chunk()])), tr)
    assert w.step() == "ok" and tr.calls == [] and rows(conn, live.id) == []


def test_hallucinations_are_dropped_and_real_speech_kept(conn, test_db_url, live):
    tr = FakeTranscriber([Segment(0, 1000, "Obrigado por assistir!"), Segment(1000, 4000, "isso não é um argumento")])
    worker(test_db_url, live.id, SourceFactory(FakeSource([loud_chunk()])), tr).step()
    assert [r[2] for r in rows(conn, live.id)] == ["isso não é um argumento"]


def test_the_overlap_between_two_chunks_is_not_stored_twice(conn, test_db_url, live):
    tr = FakeTranscriber("a gente precisa falar sobre isso", "falar sobre isso agora mesmo")
    w = worker(test_db_url, live.id, SourceFactory(FakeSource([loud_chunk(0), loud_chunk(11000)])), tr)
    w.step()
    w.step()
    assert [r[2] for r in rows(conn, live.id)] == ["a gente precisa falar sobre isso", "agora mesmo"]


def test_the_same_chunk_seen_twice_after_a_restart_adds_no_duplicate_rows(conn, test_db_url, live):
    for _ in range(2):
        worker(test_db_url, live.id, SourceFactory(FakeSource([loud_chunk(0)])), FakeTranscriber("uma frase só")).step()
    assert len(rows(conn, live.id)) == 1


def test_a_transcriber_failure_marks_the_worker_down_drops_the_chunk_and_recovers_after_the_backoff(conn, test_db_url, live):
    clock = Clock()
    src = FakeSource([loud_chunk(0), loud_chunk(12000), loud_chunk(24000)])
    tr = FakeTranscriber(W.TranscribeError("server down"), "voltei")
    w = worker(test_db_url, live.id, SourceFactory(src), tr, clock=clock)
    assert w.step() == "down" and not w.healthy()
    assert w.step() == "down" and len(tr.calls) == 1, "inside the backoff window chunks are drained, not sent"
    clock.t += W.BACKOFF_BASE_S + 0.1
    assert w.step() == "ok" and w.healthy()
    assert [r[2] for r in rows(conn, live.id)] == ["voltei"]
    assert not src.stopped, "a transcriber outage must not restart the audio capture"


def test_the_backoff_grows_and_is_capped(conn, test_db_url, live):
    clock = Clock()
    w = worker(test_db_url, live.id, SourceFactory(FakeSource([loud_chunk(i * 12000) for i in range(40)])),
               FakeTranscriber(*[W.TranscribeError("x")] * 40), clock=clock)
    waits = []
    for _ in range(8):
        w.step()
        waits.append(round(w.retry_in()))
        clock.t += w.retry_in() + 0.01
    assert waits[:4] == [2, 4, 8, 16] and max(waits) == W.BACKOFF_MAX_S


def test_a_dead_audio_process_is_down_stopped_and_restarted_after_the_backoff(conn, test_db_url, live):
    clock = Clock()
    first, second = FakeSource(die_after=1), FakeSource([loud_chunk(0)])
    factory = SourceFactory(first, second)
    w = worker(test_db_url, live.id, factory, FakeTranscriber("depois do reinício"), clock=clock)
    assert w.step() == "down" and first.stopped and not w.healthy()
    assert w.step() == "down" and len(factory.made) == 1
    clock.t += W.BACKOFF_BASE_S + 0.1
    assert w.step() == "ok" and len(factory.made) == 2 and w.healthy()
    assert rows(conn, live.id)[0][2] == "depois do reinício"


def test_a_source_that_cannot_start_is_down(conn, test_db_url, live):
    w = worker(test_db_url, live.id, SourceFactory(FakeSource(fail_start=True)), FakeTranscriber())
    assert w.step() == "down" and not w.healthy()


def test_no_audio_for_too_long_is_a_stall(conn, test_db_url, live):
    clock = Clock()
    src = FakeSource()
    w = worker(test_db_url, live.id, SourceFactory(src), FakeTranscriber(), clock=clock, stall_s=60)
    assert w.step() == "ok"
    clock.t += 61
    assert w.step() == "down" and src.stopped


def test_an_ended_session_stops_the_source_and_reports_ended(conn, test_db_url, live):
    src = FakeSource()
    w = worker(test_db_url, live.id, SourceFactory(src), FakeTranscriber())
    w.step()
    S.end_session(conn, live.id, None)
    assert w.step() == "ended" and src.stopped


def test_a_database_failure_is_down_not_a_crash(conn, test_db_url, live):
    def boom():
        raise RuntimeError("db gone")
    w = W.SttWorker(live.id, VID, SourceFactory(FakeSource()), FakeTranscriber(), boom, clock=Clock())
    assert w.step() == "down" and not w.healthy()


# ---------------------------------------------------------------- chunk assembly (overlap) + ffmpeg commands
def test_assembler_prepends_the_tail_of_the_previous_chunk_as_overlap():
    a = W.ChunkAssembler(chunk_s=2, overlap_s=1)
    first = a.add(0, make_wav(tone(2, amp=1000)))
    second = a.add(1, make_wav(tone(2, amp=2000)))
    assert first.start_ms == 0 and second.start_ms == 1000                     # 1 * 2 s - 1 s overlap
    assert W.wav_seconds(first.wav) == 2.0 and W.wav_seconds(second.wav) == 3.0


def test_assembler_without_overlap_keeps_chunks_as_they_are():
    a = W.ChunkAssembler(chunk_s=2, overlap_s=0)
    a.add(0, make_wav(tone(2)))
    c = a.add(1, make_wav(tone(2)))
    assert c.start_ms == 2000 and W.wav_seconds(c.wav) == 2.0


def test_commands_capture_live_audio_to_16k_mono_segments_and_reject_bad_ids(tmp_path):
    ytdlp, ffmpeg = W.capture_commands(VID, tmp_path, chunk_s=12)
    assert ytdlp[:3] == [sys.executable, "-m", "yt_dlp"] and f"https://www.youtube.com/watch?v={VID}" in ytdlp and "-" in ytdlp
    assert "--live-from-start" not in ytdlp, "the live edge is wanted, not the beginning"
    assert ffmpeg[0] == "ffmpeg" and "segment" in ffmpeg and ffmpeg[ffmpeg.index("-segment_time") + 1] == "12"
    assert ffmpeg[ffmpeg.index("-ar") + 1] == "16000" and ffmpeg[ffmpeg.index("-ac") + 1] == "1" and "-vn" in ffmpeg
    assert str(tmp_path) in ffmpeg[-1]
    for bad in ("", "short", "has space!!!", "../../etc/pas"):
        with pytest.raises(ValueError):
            W.capture_commands(bad, tmp_path, chunk_s=12)


def test_segment_files_are_released_only_when_the_next_one_exists_or_the_process_ended(tmp_path):
    src = W.SegmentSource(VID, chunk_s=2, overlap_s=0, workdir=tmp_path, commands=([sys.executable, "-c", "import time; time.sleep(30)"],))
    src.start()
    try:
        (tmp_path / "seg00000.wav").write_bytes(make_wav(tone(2)))
        assert src.next_chunk() is None, "segment 0 may still be growing"
        (tmp_path / "seg00001.wav").write_bytes(make_wav(tone(2)))
        c = src.next_chunk()
        assert c is not None and c.start_ms == 0 and not (tmp_path / "seg00000.wav").exists(), "consumed files are deleted"
        assert src.next_chunk() is None
    finally:
        src.stop()
    assert not tmp_path.exists(), "stop() removes the private work dir"


def test_the_last_segment_is_released_once_the_audio_process_has_ended(tmp_path):
    src = W.SegmentSource(VID, chunk_s=2, overlap_s=0, workdir=tmp_path, commands=([sys.executable, "-c", "pass"],))
    src.start()
    try:
        (tmp_path / "seg00000.wav").write_bytes(make_wav(tone(2)))
        assert wait_until(lambda: not src.alive())
        assert src.next_chunk() is not None
    finally:
        src.stop()


# ---------------------------------------------------------------- config + registry
def test_config_defaults_to_off_and_reads_the_environment(monkeypatch):
    for k in ("STT_PROVIDER", "STT_URL", "STT_CHUNK_S", "STT_OVERLAP_S", "STT_PROMPT"):
        monkeypatch.delenv(k, raising=False)
    c = W.config()
    assert c.provider == "off" and not W.enabled() and c.chunk_s == 12 and c.overlap_s == 1
    monkeypatch.setenv("STT_PROVIDER", "whispercpp")
    monkeypatch.setenv("STT_URL", "http://stt.internal:8080")
    monkeypatch.setenv("STT_CHUNK_S", "8")
    monkeypatch.setenv("STT_OVERLAP_S", "2")
    monkeypatch.setenv("STT_PROMPT", "ateísmo")
    c = W.config()
    assert (c.provider, c.url, c.chunk_s, c.overlap_s, c.prompt) == ("whispercpp", "http://stt.internal:8080", 8, 2, "ateísmo") and W.enabled()


def test_config_ignores_unknown_providers_bad_numbers_and_a_missing_url(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "magic")
    assert not W.enabled()
    monkeypatch.setenv("STT_PROVIDER", "whispercpp")
    monkeypatch.delenv("STT_URL", raising=False)
    assert not W.enabled(), "whispercpp without STT_URL is not usable"
    monkeypatch.setenv("STT_URL", "http://x")
    monkeypatch.setenv("STT_CHUNK_S", "abc")
    monkeypatch.setenv("STT_OVERLAP_S", "99")
    c = W.config()
    assert c.chunk_s == 12 and 0 <= c.overlap_s < c.chunk_s


def test_ensure_does_nothing_while_the_provider_is_off(live, monkeypatch):
    monkeypatch.delenv("STT_PROVIDER", raising=False)
    assert W.ensure(live.id, VID) is None and W.get(live.id) is None and W.health(live.id) == "ok"


def test_ensure_runs_one_thread_per_session_stores_lines_and_stops_with_the_session(conn, test_db_url, live, monkeypatch):
    monkeypatch.setattr(W, "source_factory", SourceFactory(FakeSource([loud_chunk(0)])))
    monkeypatch.setattr(W, "make_transcriber", lambda cfg: FakeTranscriber("linha da thread"))
    monkeypatch.setattr(W, "connect_fn", lambda: connect(test_db_url))
    monkeypatch.setattr(W, "POLL_S", 0.02)
    monkeypatch.setenv("STT_PROVIDER", "whispercpp")
    monkeypatch.setenv("STT_URL", "http://127.0.0.1:1")
    w = W.ensure(live.id, VID)
    assert w is not None and W.ensure(live.id, VID) is w, "second call returns the running worker"
    assert wait_until(lambda: len(rows(conn, live.id)) == 1) and rows(conn, live.id)[0][2] == "linha da thread"
    assert W.health(live.id) == "ok"
    S.end_session(conn, live.id, None)
    assert wait_until(lambda: W.get(live.id) is None), "the thread ends and unregisters when the session ends"


def test_health_is_down_while_the_transcriber_keeps_failing(conn, test_db_url, live, monkeypatch):
    monkeypatch.setattr(W, "source_factory", SourceFactory(FakeSource([loud_chunk(0)])))
    monkeypatch.setattr(W, "make_transcriber", lambda cfg: FakeTranscriber(W.TranscribeError("x")))
    monkeypatch.setattr(W, "connect_fn", lambda: connect(test_db_url))
    monkeypatch.setattr(W, "POLL_S", 0.02)
    monkeypatch.setenv("STT_PROVIDER", "whispercpp")
    monkeypatch.setenv("STT_URL", "http://127.0.0.1:1")
    W.ensure(live.id, VID)
    assert wait_until(lambda: W.health(live.id) == "down")


def test_the_module_never_imports_a_new_third_party_dependency():
    src = Path(W.__file__).read_text()
    for banned in ("import requests", "import httpx", "import numpy", "import audioop", "import openai", "import groq"):
        assert banned not in src
    assert time  # keep the import used by the sleep-free tests above


# ---------------------------------------------------------------- the reason of a failure must be visible (found testing a real live)
def logged(caplog):
    return " | ".join(r.getMessage() for r in caplog.records if r.name == "app.sttworker")


def test_a_transcriber_failure_is_logged_with_its_reason(conn, test_db_url, live, caplog):
    caplog.set_level("WARNING", logger="app.sttworker")
    w = worker(test_db_url, live.id, SourceFactory(FakeSource([loud_chunk(0)])), FakeTranscriber(W.TranscribeError("connection refused")))
    w.step()
    assert "connection refused" in logged(caplog) and VID in logged(caplog)


def test_a_source_that_cannot_start_is_logged_with_its_reason(conn, test_db_url, live, caplog):
    caplog.set_level("WARNING", logger="app.sttworker")
    worker(test_db_url, live.id, SourceFactory(FakeSource(fail_start=True)), FakeTranscriber()).step()
    assert "cannot start" in logged(caplog)


def test_a_dead_capture_logs_what_the_process_printed(conn, test_db_url, live, caplog):
    caplog.set_level("WARNING", logger="app.sttworker")
    src = FakeSource(die_after=1)
    src.error_tail = lambda: "certificate verify failed"
    worker(test_db_url, live.id, SourceFactory(src), FakeTranscriber()).step()
    assert "certificate verify failed" in logged(caplog)


def test_the_same_failure_is_not_logged_on_every_retry(conn, test_db_url, live, caplog):
    caplog.set_level("WARNING", logger="app.sttworker")
    clock = Clock()
    w = worker(test_db_url, live.id, SourceFactory(FakeSource([loud_chunk(i * 12000) for i in range(20)])),
               FakeTranscriber(*[W.TranscribeError("connection refused")] * 20), clock=clock)
    for _ in range(6):
        w.step()
        clock.t += w.retry_in() + 0.01
    assert logged(caplog).count("connection refused") <= 3, "log the first failure, then only now and then"


def test_recovery_is_logged_once(conn, test_db_url, live, caplog):
    caplog.set_level("INFO", logger="app.sttworker")
    clock = Clock()
    w = worker(test_db_url, live.id, SourceFactory(FakeSource([loud_chunk(0), loud_chunk(12000)])),
               FakeTranscriber(W.TranscribeError("x"), "ok agora"), clock=clock)
    w.step()
    clock.t += W.BACKOFF_BASE_S + 0.1
    w.step()
    assert logged(caplog).count("recovered") == 1


def test_the_real_capture_keeps_the_stderr_of_its_processes(tmp_path):
    src = W.SegmentSource("x" * 11, 12, 1, workdir=tmp_path / "w",
                          commands=([sys.executable, "-c", "import sys; sys.stderr.write('boom: certificate verify failed'); sys.exit(3)"],))
    src.start()
    time.sleep(0.6)
    assert not src.alive() and "certificate verify failed" in src.error_tail()
    src.stop()


# ---------------------------------------------------------------- language: a stream is not always Portuguese
def test_language_defaults_to_portuguese_and_can_be_changed(monkeypatch):
    for k in ("STT_LANGUAGE", "STT_PROMPT"):
        monkeypatch.delenv(k, raising=False)
    assert W.config().language == "pt" and W.config().prompt == W.DEFAULT_PROMPT
    monkeypatch.setenv("STT_LANGUAGE", "EN")
    c = W.config()
    assert c.language == "en" and c.prompt == "", "the Portuguese default prompt must not bias another language"
    monkeypatch.setenv("STT_PROMPT", "atheism, fallacy")
    assert W.config().prompt == "atheism, fallacy"
    monkeypatch.setenv("STT_LANGUAGE", "auto")
    assert W.config().language == "auto"


@pytest.mark.parametrize("bad", ["", "p t", "portuguese-brazil-long", "pt;rm", "123"])
def test_a_bad_language_falls_back_to_portuguese(monkeypatch, bad):
    monkeypatch.setenv("STT_LANGUAGE", bad)
    monkeypatch.delenv("STT_PROMPT", raising=False)
    assert W.config().language == "pt"


def test_the_request_carries_the_configured_language():
    body, _ = W.WhisperCppTranscriber("http://x", language="en")._body(make_wav(tone(0.2)), "")
    assert b'name="language"\r\n\r\nen\r\n' in body
    assert isinstance(W._default_transcriber(W.SttConfig("whispercpp", "http://x", 12, 1, "", "en")), W.WhisperCppTranscriber)
    assert W._default_transcriber(W.SttConfig("whispercpp", "http://x", 12, 1, "", "auto")).language == "auto"


# ---------------------------------------------------------------- whisper sometimes answers a quiet chunk with its own prompt (seen on a real live)
def test_a_segment_that_just_repeats_the_prompt_is_dropped(conn, test_db_url, live):
    prompt = "Debate em português do Brasil sobre religião, ateísmo, política e falácias lógicas."
    echo = "Em português do Brasil sobre religião, ateísmo, política e falácias lógicas."
    w = worker(test_db_url, live.id, SourceFactory(FakeSource([loud_chunk(0), loud_chunk(12000)])),
               FakeTranscriber(echo, "o ateísmo não é uma religião"), prompt=prompt)
    w.step()
    w.step()
    assert [r[2] for r in rows(conn, live.id)] == ["o ateísmo não é uma religião"]


def test_a_real_sentence_that_shares_words_with_the_prompt_is_kept(conn, test_db_url, live):
    w = worker(test_db_url, live.id, SourceFactory(FakeSource([loud_chunk(0)])),
               FakeTranscriber("a religião no Brasil mudou muito"), prompt="Debate em português do Brasil sobre religião, ateísmo.")
    w.step()
    assert len(rows(conn, live.id)) == 1
