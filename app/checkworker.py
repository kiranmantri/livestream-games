
"""Checker worker (FC3): takes the queued fact checks of "Quem tá metendo o loco?", asks the checker, stores the outcome.

Same shape as app/replaycollector.py: daemon threads started by the app's lifespan, woken by `wake()` (FC4 calls it after a claim is sent), polling
every POLL_S otherwise. `CHECKER_WORKERS` (default 2, max 4) checks run at the same time; the database hands each queued check to exactly one of them
(SKIP LOCKED). No checker configured (CHECKER_MODEL empty or without its key) => no thread: claims stay queued and the admin scores them by hand.

A failed run puts the check back in the queue until MAX_ATTEMPTS, then it waits for the admin (checker.fail_check). A restart requeues what was running.
Nothing in a stored error comes from a response body: factcheck.explain_error writes short reasons, anything else is just the exception's class name.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable

import psycopg

from app import checker as C
from app.db import connect

log = logging.getLogger(__name__)

POLL_S = 5.0
DEFAULT_WORKERS = 2
MAX_WORKERS = 4


class CheckWorker:
    def __init__(self, connect_fn: Callable[[], psycopg.Connection], checker, poll_s: float = POLL_S, workers: int = DEFAULT_WORKERS):
        self._connect, self.checker, self.poll_s, self.workers = connect_fn, checker, poll_s, max(1, workers)
        self._wakes = [threading.Event() for _ in range(self.workers)]
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._recovered = False

    # ---- one check
    def step(self) -> str:
        """Check the oldest queued claim. 'idle' (nothing queued) | 'done' (a result was stored) | 'failed' (requeued, or sent to the admin)."""
        conn = self._connect()
        try:
            ck = C.claim_next_check(conn)
            if ck is None:
                return "idle"
            ctx = C.context_for(conn, ck.id)
            try:
                result = asyncio.run(self.checker.check(ck.claim_text, ctx.lines, ctx.note))
                C.complete_check(conn, ck.id, result)
                return "done"
            except C.CheckNotRunning:
                log.warning("check %s was taken away while it ran (restart?)", ck.id)
                return "failed"
            except C.CheckFailed as e:
                reason = str(e) or "falha desconhecida"
            except ValueError as e:                       # complete_check refused an inconsistent result
                reason = f"resultado inválido: {e}"
            except Exception as e:
                log.exception("fact check %s crashed", ck.id)
                reason = f"erro inesperado ({type(e).__name__})"
            log.warning("fact check %s failed (attempt %s): %s", ck.id, ck.attempts, reason)
            try:
                C.fail_check(conn, ck.id, reason)
            except C.CheckNotRunning:
                pass
            return "failed"
        finally:
            conn.close()

    # ---- threads
    def _recover(self) -> None:
        with self._lock:
            if self._recovered:
                return
            conn = self._connect()
            try:
                n = C.recover_interrupted(conn)
            finally:
                conn.close()
            self._recovered = True
            if n:
                log.info("fact checks requeued after a restart: %s", n)

    def _run(self, wake: threading.Event) -> None:
        while not self._stop.is_set():
            try:
                self._recover()
                while not self._stop.is_set() and self.step() != "idle":
                    pass
            except Exception:
                log.exception("checker worker loop failed; retrying")
            wake.wait(self.poll_s)
            wake.clear()

    def start(self) -> None:
        with self._lock:
            if self.running():
                return
            self._stop.clear()
            self._threads = [threading.Thread(target=self._run, args=(w,), name=f"checker-{i}", daemon=True) for i, w in enumerate(self._wakes)]
            for t in self._threads:
                t.start()

    def wake(self) -> None:
        for w in self._wakes:
            w.set()

    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self.wake()
        for t in self._threads:
            t.join(timeout=timeout)


# ---- process-wide instance (started by the app's lifespan)
_worker: CheckWorker | None = None
_glock = threading.Lock()
_logged_off: set[str] = set()


def worker_count() -> int:
    try:
        n = int(os.environ.get("CHECKER_WORKERS", ""))
    except ValueError:
        return DEFAULT_WORKERS
    return DEFAULT_WORKERS if n < 1 else min(n, MAX_WORKERS)


def _off_reason() -> str | None:
    """Why there is no checker. The unset case is answered without importing pydantic-ai (the app starts without it when the game is off)."""
    if not (os.environ.get("CHECKER_MODEL") or "").strip():
        return C.CHECKER_MODEL_UNSET
    from app import factcheck
    return factcheck.off_reason()


def ensure_started(db_url: str, checker=None) -> CheckWorker | None:
    global _worker
    with _glock:
        if _worker is not None and _worker.running():
            return _worker
        if checker is None:
            reason = _off_reason()
            if reason:
                if reason not in _logged_off:
                    _logged_off.add(reason)
                    (log.info if reason == C.CHECKER_MODEL_UNSET else log.warning)("fact-checker is off: %s", reason)
                return None
            from app import factcheck
            checker = factcheck.from_env()
        _worker = CheckWorker(lambda: connect(db_url), checker, workers=worker_count())
        _worker.start()
        log.info("fact-checker on: %s, %s worker(s)", getattr(checker, "name", "?"), _worker.workers)
        return _worker


def current() -> CheckWorker | None:
    return _worker


def wake() -> None:
    if _worker is not None:
        _worker.wake()


def stop() -> None:
    global _worker
    with _glock:
        w, _worker = _worker, None
        _logged_off.clear()
    if w is not None:
        w.stop()
