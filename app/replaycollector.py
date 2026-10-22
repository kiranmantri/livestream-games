"""Replay collector: ONE background thread that takes queued videos from `replays` (app/replays.py), checks them, downloads
the pt-BR captions + chat replay with yt-dlp into a private temp dir, imports both tracks into replay_events and marks the
video ready (or failed with a readable message). Never started by tests (see tests/conftest.py)."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import psycopg

from app import replays
from app.db import connect
from corpus import fetch as corpus_fetch
from corpus import replay as corpus_replay

POLL_S = 15.0
MAX_HOURS = 12
PROBE_TIMEOUT_S = 120
DOWNLOAD_TIMEOUT_S = 1800


class CollectError(Exception):
    """A readable (pt-BR) reason why a video cannot be collected; stored as the job's error."""


@dataclass(frozen=True)
class Meta:
    title: str
    width: int | None
    height: int | None
    duration_s: int | None
    live_status: str | None
    has_pt_captions: bool
    has_chat: bool


class Downloader(Protocol):
    def probe(self, video_id: str) -> Meta: ...
    def download(self, video_id: str, dest: Path) -> None: ...


class YtdlpDownloader:
    """Adapter over corpus/fetch.py (the same yt-dlp calls as `corpus fetch`), with timeouts."""

    def __init__(self, probe_timeout: float = PROBE_TIMEOUT_S, download_timeout: float = DOWNLOAD_TIMEOUT_S):
        self.probe_timeout, self.download_timeout = probe_timeout, download_timeout
        self._raw: dict[str, dict] = {}

    def probe(self, video_id: str) -> Meta:
        try:
            d = corpus_fetch.get_meta(video_id, timeout=self.probe_timeout)
        except subprocess.TimeoutExpired as e:
            raise CollectError("O YouTube demorou demais para responder. Tente de novo.") from e
        except RuntimeError as e:
            raise CollectError(f"Não foi possível ler o vídeo: {e}") from e
        self._raw[video_id] = d
        return Meta(d.get("title") or "", d.get("width"), d.get("height"), d.get("duration"), d.get("live_status"),
                    bool(d.get("transcript_langs_available")), bool(d.get("has_live_chat")))

    def download(self, video_id: str, dest: Path) -> None:
        raw = self._raw.get(video_id)
        try:
            if raw is None:
                raw = corpus_fetch.get_meta(video_id, timeout=self.probe_timeout)
            res = corpus_fetch.download(video_id, Path(dest), raw, timeout=self.download_timeout)
        except subprocess.TimeoutExpired as e:
            raise CollectError("O download demorou demais e foi cancelado. Tente de novo.") from e
        except RuntimeError as e:
            raise CollectError(f"Não foi possível ler o vídeo: {e}") from e
        finally:
            self._raw.pop(video_id, None)
        if not res.get("transcript") or not res.get("chat"):
            raise CollectError("Falha ao baixar legendas/chat: " + "; ".join(res.get("errors") or ["arquivo ausente"]))


def _refusal(m: Meta, max_hours: float) -> str | None:
    if m.live_status == "is_live":
        return "O vídeo está ao vivo. Envie o link depois que a transmissão terminar."
    if m.live_status == "is_upcoming":
        return "A transmissão ainda não começou."
    if m.live_status == "post_live":
        return "O YouTube ainda está processando a gravação. Tente de novo em alguns minutos."
    if m.width and m.height and m.width <= m.height:
        return "Só usamos vídeos na horizontal (este é vertical ou quadrado)."
    if m.duration_s and m.duration_s > max_hours * 3600:
        return f"Vídeo longo demais (limite: {max_hours:g} horas)."
    if not m.has_pt_captions:
        return "Legendas em português ainda não disponíveis. O YouTube pode levar algumas horas após o fim da transmissão."
    if not m.has_chat:
        return "Este vídeo não tem o replay do chat."
    return None


class Collector:
    def __init__(self, connect_fn: Callable[[], psycopg.Connection], downloader: Downloader, workdir_root: Path | str | None = None,
                 poll_s: float = POLL_S, max_hours: float = MAX_HOURS):
        self._connect, self._dl = connect_fn, downloader
        self._root = Path(workdir_root) if workdir_root else Path(tempfile.gettempdir())
        self.poll_s, self.max_hours = poll_s, max_hours
        self._wake, self._stop = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._recovered = False
        self._lock = threading.Lock()

    # ---- one job
    def step(self) -> str:
        """Collect the oldest queued video. 'idle' (nothing queued) | 'ready' | 'failed'."""
        conn = self._connect()
        try:
            job = replays.claim_next(conn)
            if job is None:
                return "idle"
            vid = job.video_id
            work = Path(tempfile.mkdtemp(prefix=f"replay-{vid}-", dir=self._root))
            try:
                m = self._dl.probe(vid)
                why = _refusal(m, self.max_hours)
                if why:
                    raise CollectError(why)
                self._dl.download(vid, work)
                replays.mark_importing(conn, vid)
                try:
                    corpus_replay.import_video(conn, vid, work)
                except corpus_replay.ReplayImportError as e:
                    raise CollectError(f"Legendas ou chat vazios: {e}") from e
                replays.mark_ready(conn, vid, m.title, m.width, m.height, m.duration_s)
                return "ready"
            except CollectError as e:
                replays.mark_failed(conn, vid, str(e))
                return "failed"
            except Exception as e:  # noqa: BLE001 - any bug must fail THIS job, not kill the thread
                replays.mark_failed(conn, vid, f"Erro inesperado: {type(e).__name__}: {e}")
                return "failed"
            finally:
                shutil.rmtree(work, ignore_errors=True)
        finally:
            conn.close()

    # ---- thread
    def _recover(self) -> None:
        if self._recovered:
            return
        conn = self._connect()
        try:
            replays.recover_interrupted(conn)
        finally:
            conn.close()
        self._recovered = True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._recover()
                while not self._stop.is_set() and self.step() != "idle":
                    pass
            except Exception:  # noqa: BLE001, S110 - DB down etc.: try again after poll_s
                pass
            self._wake.wait(self.poll_s)
            self._wake.clear()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="replay-collector", daemon=True)
            self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)


# ---- process-wide instance (started by the app's lifespan)
_collector: Collector | None = None
_glock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("REPLAY_COLLECTOR", "on").strip().lower() != "off"


def ensure_started(db_url: str) -> Collector | None:
    global _collector
    if not enabled():
        return None
    with _glock:
        if _collector is None or not _collector.running():
            hours = float(os.environ.get("REPLAY_MAX_HOURS") or MAX_HOURS)
            _collector = Collector(lambda: connect(db_url), YtdlpDownloader(), max_hours=hours)
            _collector.start()
        return _collector


def current() -> Collector | None:
    return _collector


def wake() -> None:
    if _collector is not None:
        _collector.wake()


def stop() -> None:
    global _collector
    with _glock:
        c, _collector = _collector, None
    if c is not None:
        c.stop()
