# ruff: noqa: RUF059
"""FC3: the checker worker (app/checkworker.py) — takes queued checks, asks the checker (a FakeChecker here, never a real model), stores the outcome."""
from __future__ import annotations

import logging
import threading
import time

import pytest
from starlette.testclient import TestClient

from app import checker as C
from app import checkworker as W
from app import factcheck as FC
from app import falacias as F
from app import sessions as S
from app.db import connect
from app.main import create_app


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
    monkeypatch.setattr(C, "RATE_LIMIT_S", 0)
    yield
    W.stop()


SRC = [{"title": "Fonte", "url": "https://example.org/a"}]


def good(**over):
    base = {"verdict": "false", "score": -3, "confidence": "high", "explanation": "Falso.", "sources": SRC, "model": "fake",
            "input_tokens": 10, "output_tokens": 5, "elapsed_ms": 7}
    return C.AiResult(**(base | over))


def game(conn, players=("Ana", "Bia")):
    s = S.start_session_as_admin(conn, game_type="checker")
    return s, [S.join_session(conn, n, f"10.0.0.{i + 1}", game_type="checker")[1].id for i, n in enumerate(players)]


def worker(test_db_url, checker, **kw):
    return W.CheckWorker(lambda: connect(test_db_url), checker, **kw)


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- one step
def test_step_is_idle_when_nothing_is_queued(conn, test_db_url):
    assert worker(test_db_url, FC.FakeChecker([good()])).step() == "idle"


def test_step_checks_the_oldest_queued_claim_and_stores_the_result(conn, test_db_url):
    s, (ana, bia) = game(conn)
    cl = C.submit_typed_claim(conn, ana, "A Terra é plana", -4, note="acho falso")
    fake = FC.FakeChecker([good()])
    assert worker(test_db_url, fake).step() == "done"
    assert fake.calls == [("A Terra é plana", (), "acho falso")]
    ck = C.get_check(conn, cl.check_id)
    assert (ck.status, ck.verdict, ck.ai_score, ck.model, ck.input_tokens, ck.attempts) == ("done", "false", -3, "fake", 10, 1)


def test_the_checker_gets_the_lines_around_a_selected_passage(conn, test_db_url):
    s, (ana, _) = game(conn)
    ids = [F.add_event(conn, s.id, "transcript", str(i), f"linha {i} do apresentador") for i in range(1, 6)]
    C.submit_claim(conn, ana, "transcript", ids[2], 0, ids[2], 5, guess=0)
    fake = FC.FakeChecker([good()])
    worker(test_db_url, fake).step()
    claim, context, note = fake.calls[0]
    assert claim == "linha" and context == tuple(f"linha {i} do apresentador" for i in range(1, 6))


def test_low_confidence_and_unverifiable_wait_for_the_admin(conn, test_db_url):
    s, (ana, _) = game(conn)
    a = C.submit_typed_claim(conn, ana, "um", 1)
    b = C.submit_typed_claim(conn, ana, "dois", 1)
    fake = FC.FakeChecker([good(confidence="low"), good(verdict="unverifiable", score=0, sources=[])])
    w = worker(test_db_url, fake)
    assert (w.step(), w.step()) == ("done", "done")
    assert [C.get_check(conn, x.check_id).status for x in (a, b)] == ["needs_admin", "needs_admin"]


def test_a_failed_check_goes_back_to_the_queue_with_the_reason_then_to_the_admin(conn, test_db_url):
    s, (ana, _) = game(conn)
    cl = C.submit_typed_claim(conn, ana, "A Terra é plana", 1)
    w = worker(test_db_url, FC.FakeChecker([C.CheckFailed("limite de requisições do provedor do modelo (HTTP 429)")]))
    for n in range(1, C.MAX_ATTEMPTS):
        assert w.step() == "failed"
        ck = C.get_check(conn, cl.check_id)
        assert (ck.status, ck.attempts, ck.error) == ("queued", n, "limite de requisições do provedor do modelo (HTTP 429)")
    assert w.step() == "failed"
    assert C.get_check(conn, cl.check_id).status == "needs_admin"
    assert w.step() == "idle"


def test_an_unexpected_exception_fails_only_that_check_and_never_leaks_its_message(conn, test_db_url):
    s, (ana, _) = game(conn)
    cl = C.submit_typed_claim(conn, ana, "x y", 1)
    w = worker(test_db_url, FC.FakeChecker([RuntimeError("segredo sk-or-123")]))
    assert w.step() == "failed"
    ck = C.get_check(conn, cl.check_id)
    assert ck.error == "erro inesperado (RuntimeError)" and "sk-or" not in ck.error


def test_an_inconsistent_result_is_a_failure_not_a_stored_row(conn, test_db_url):
    s, (ana, _) = game(conn)
    cl = C.submit_typed_claim(conn, ana, "x y", 1)
    w = worker(test_db_url, FC.FakeChecker([good(verdict="true", score=-3)]))
    assert w.step() == "failed"
    ck = C.get_check(conn, cl.check_id)
    assert ck.verdict is None and ck.status == "queued" and "resultado inválido" in ck.error


def test_a_game_that_ends_while_the_model_thinks_still_keeps_the_result(conn, test_db_url):
    s, (ana, _) = game(conn)
    cl = C.submit_typed_claim(conn, ana, "x y", 1)

    def ends_the_game(claim, context, note):
        S.end_session(connect(test_db_url), s.id, None)
        return good()

    assert worker(test_db_url, FC.FakeChecker(ends_the_game)).step() == "done"
    assert C.get_check(conn, cl.check_id).status == "done"


def test_checks_of_an_ended_game_are_not_taken(conn, test_db_url):
    s, (ana, _) = game(conn)
    C.submit_typed_claim(conn, ana, "x y", 1)
    S.end_session(conn, s.id, None)
    fake = FC.FakeChecker([good()])
    assert worker(test_db_url, fake).step() == "idle" and fake.calls == []


# ---------------------------------------------------------------- the thread
def test_the_thread_processes_the_queue_and_stops(conn, test_db_url):
    s, (ana, bia) = game(conn)
    cls = [C.submit_typed_claim(conn, ana, "primeira", 1), C.submit_typed_claim(conn, bia, "segunda", 1)]
    w = worker(test_db_url, FC.FakeChecker([good()]), poll_s=0.05)
    w.start()
    assert wait_for(lambda: all(C.get_check(conn, c.check_id).status == "done" for c in cls))
    assert w.running()
    w.stop()
    assert not w.running()


def test_wake_makes_a_sleeping_worker_look_at_the_queue_now(conn, test_db_url):
    s, (ana, _) = game(conn)
    w = worker(test_db_url, FC.FakeChecker([good()]), poll_s=60)
    w.start()
    time.sleep(0.3)                                    # it has looked once and gone to sleep for 60 s
    cl = C.submit_typed_claim(conn, ana, "nova frase", 1)
    w.wake()
    assert wait_for(lambda: C.get_check(conn, cl.check_id).status == "done")
    w.stop()


def test_checks_left_running_by_a_restart_are_picked_up_again(conn, test_db_url):
    s, (ana, _) = game(conn)
    cl = C.submit_typed_claim(conn, ana, "x y", 1)
    C.claim_next_check(conn)                           # a previous process died holding it
    w = worker(test_db_url, FC.FakeChecker([good()]), poll_s=0.05)
    w.start()
    assert wait_for(lambda: C.get_check(conn, cl.check_id).status == "done")
    w.stop()


def test_two_workers_check_in_parallel(conn, test_db_url):
    s, (ana, bia) = game(conn)
    cls = [C.submit_typed_claim(conn, ana, "primeira", 1), C.submit_typed_claim(conn, bia, "segunda", 1)]
    gate = threading.Barrier(2, timeout=5)             # passes only if both checks are inside the checker at the same time

    class Slow:
        name = "slow"

        async def check(self, claim, context=(), note=""):
            gate.wait()
            return good()

    w = worker(test_db_url, Slow(), poll_s=0.05, workers=2)
    w.start()
    assert wait_for(lambda: all(C.get_check(conn, c.check_id).status == "done" for c in cls))
    w.stop()


def test_the_thread_survives_the_database_being_down_for_a_moment(conn, test_db_url):
    s, (ana, _) = game(conn)
    cl = C.submit_typed_claim(conn, ana, "x y", 1)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise OSError("connection refused")
        return connect(test_db_url)

    w = W.CheckWorker(flaky, FC.FakeChecker([good()]), poll_s=0.05)
    w.start()
    assert wait_for(lambda: C.get_check(conn, cl.check_id).status == "done")
    w.stop()


def test_stop_does_not_hang_on_a_long_sleep(conn, test_db_url):
    w = worker(test_db_url, FC.FakeChecker([good()]), poll_s=60)
    w.start()
    t0 = time.monotonic()
    w.stop()
    assert time.monotonic() - t0 < 3


# ---------------------------------------------------------------- the process-wide worker
def test_without_checker_model_nothing_starts_and_the_reason_is_logged_once(test_db_url, caplog):
    with caplog.at_level(logging.INFO, logger="app.checkworker"):
        assert W.ensure_started(test_db_url) is None
        assert W.ensure_started(test_db_url) is None
    msgs = [r.getMessage() for r in caplog.records if "fact-checker is off" in r.getMessage()]
    assert len(msgs) == 1 and "CHECKER_MODEL is not set" in msgs[0]


def test_a_model_without_its_key_names_the_missing_variable(test_db_url, monkeypatch, caplog):
    monkeypatch.setenv("CHECKER_MODEL", "groq:openai/gpt-oss-120b")
    with caplog.at_level(logging.INFO, logger="app.checkworker"):
        assert W.ensure_started(test_db_url) is None
    assert any("GROQ_API_KEY" in r.getMessage() for r in caplog.records)


def test_with_a_model_and_a_key_the_worker_starts_once_and_stops(test_db_url, monkeypatch):
    monkeypatch.setenv("CHECKER_MODEL", "groq:openai/gpt-oss-120b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    w = W.ensure_started(test_db_url)
    assert w is not None and w.running() and w.checker.name == "groq:openai/gpt-oss-120b"
    assert W.ensure_started(test_db_url) is w and W.current() is w
    W.stop()
    assert not w.running() and W.current() is None


def test_an_explicit_checker_is_used_as_given(test_db_url):
    fake = FC.FakeChecker([good()])
    assert W.ensure_started(test_db_url, checker=fake).checker is fake


@pytest.mark.parametrize(("raw", "expected"), [(None, 2), ("", 2), ("3", 3), ("1", 1), ("0", 2), ("-1", 2), ("abc", 2), ("99", 4)])
def test_worker_count_setting(raw, expected, monkeypatch):
    if raw is None:
        monkeypatch.delenv("CHECKER_WORKERS", raising=False)
    else:
        monkeypatch.setenv("CHECKER_WORKERS", raw)
    assert W.worker_count() == expected


def test_wake_without_a_worker_is_harmless():
    W.wake()


def test_the_app_starts_the_worker_with_its_lifespan_and_stops_it(test_db_url, monkeypatch):
    monkeypatch.setenv("CHECKER_MODEL", "groq:openai/gpt-oss-120b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    with TestClient(create_app(test_db_url)):
        w = W.current()
        assert w is not None and w.running()
    assert W.current() is None and not w.running()


def test_the_app_runs_fine_without_a_checker(test_db_url):
    with TestClient(create_app(test_db_url)) as c:
        assert c.get("/healthz").status_code == 200
        assert W.current() is None


def test_a_busy_check_is_not_requeued_by_the_other_threads_while_it_runs(conn, test_db_url):
    s, (ana, _) = game(conn)
    cl = C.submit_typed_claim(conn, ana, "x y", 1)
    entered, release, calls = threading.Event(), threading.Event(), []

    class Slow:
        name = "slow"

        async def check(self, claim, context=(), note=""):
            calls.append(claim)
            entered.set()
            release.wait(5)
            return good()

    w = worker(test_db_url, Slow(), poll_s=0.02, workers=2)
    w.start()
    assert entered.wait(5)
    time.sleep(0.5)                                    # many loop iterations of the idle thread pass while the check runs
    assert C.get_check(conn, cl.check_id).status == "running" and calls == ["x y"]
    release.set()
    assert wait_for(lambda: C.get_check(conn, cl.check_id).status == "done")
    assert C.get_check(conn, cl.check_id).attempts == 1
    w.stop()


def test_the_thread_drains_the_whole_queue_without_being_woken(conn, test_db_url):
    s, (ana, _) = game(conn)
    cls = [C.submit_typed_claim(conn, ana, f"frase {i}", 1) for i in range(4)]
    w = worker(test_db_url, FC.FakeChecker([good()]), poll_s=60, workers=1)
    w.start()
    assert wait_for(lambda: all(C.get_check(conn, c.check_id).status == "done" for c in cls))
    w.stop()


def test_every_step_closes_its_database_connection(conn, test_db_url):
    s, (ana, _) = game(conn)
    C.submit_typed_claim(conn, ana, "x y", 1)
    opened = []

    def tracking():
        c = connect(test_db_url)
        opened.append(c)
        return c

    w = W.CheckWorker(tracking, FC.FakeChecker([good()]))
    assert (w.step(), w.step()) == ("done", "idle")
    assert len(opened) == 2 and all(c.closed for c in opened)
