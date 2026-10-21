"""Detect the channel's CURRENT live stream (for the embedded player).

Detection uses yt-dlp on https://www.youtube.com/@O_Poligrafo/live. A lookup takes ~10-15 s, so it NEVER runs
inside a page request: `LiveStream.current()` returns the cached answer immediately and, when the cache is
older than the TTL, refreshes it in ONE background thread.

  LIVE_CHANNEL_URL   override the channel /live URL
  LIVE_VIDEO_ID      force a video id (no detection) - handy for tests/demos
  LIVE_TTL           seconds between lookups (default 60)
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Callable

log = logging.getLogger("livestream")

DEFAULT_URL = "https://www.youtube.com/@O_Poligrafo/live"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class LookupFailed(Exception):
    """Network/extractor problem: the live state is UNKNOWN (keep the previous answer)."""


def resolve_channel(url: str = DEFAULT_URL) -> str | None:
    """-> video id if the channel is live now, None if it is not, raises LookupFailed when unknown."""
    import yt_dlp
    from yt_dlp.utils import DownloadError

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 10,
            "cachedir": False, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as e:
        if "not currently live" in str(e).lower():
            return None
        raise LookupFailed(str(e)[:200]) from e
    except Exception as e:  # yt-dlp can raise assorted errors on network trouble
        raise LookupFailed(f"{type(e).__name__}: {e}"[:200]) from e
    if info and info.get("is_live") and VIDEO_ID_RE.match(str(info.get("id", ""))):
        return info["id"]
    return None


class LiveStream:
    def __init__(self, resolver: Callable[[], str | None] | None = None, ttl: float | None = None,
                 forced_id: str | None = None, background: bool = True):
        url = os.environ.get("LIVE_CHANNEL_URL") or DEFAULT_URL
        self._resolver = resolver or (lambda: resolve_channel(url))
        self.ttl = float(os.environ.get("LIVE_TTL", "60")) if ttl is None else ttl
        forced = forced_id if forced_id is not None else os.environ.get("LIVE_VIDEO_ID", "")
        self.forced = forced if VIDEO_ID_RE.match(forced or "") else None
        self.background = background
        self._lock = threading.Lock()
        self._id: str | None = None
        self._checked_at: float | None = None      # None = never looked up yet
        self._busy = False

    def refresh(self) -> None:
        """One lookup, synchronously. A failed lookup keeps the previous answer."""
        try:
            vid = self._resolver()
        except LookupFailed as e:
            log.warning("live lookup failed, keeping previous answer: %s", e)
            with self._lock:
                self._checked_at, self._busy = time.monotonic(), False
            return
        with self._lock:
            self._id = vid if vid and VIDEO_ID_RE.match(vid) else None
            self._checked_at, self._busy = time.monotonic(), False

    def _spawn(self) -> None:
        def run():
            try:
                self.refresh()
            except Exception:  # never leave _busy stuck
                log.exception("live lookup crashed")
                with self._lock:
                    self._checked_at, self._busy = time.monotonic(), False
        threading.Thread(target=run, daemon=True, name="live-lookup").start()

    @property
    def known(self) -> bool:
        """True once a lookup finished (so "not live" is a real answer, not "still searching")."""
        with self._lock:
            return self.forced is not None or self._checked_at is not None

    def current(self) -> str | None:
        """Cached video id of the live stream (None = not live / not known yet). Never blocks on the network."""
        if self.forced:
            return self.forced
        with self._lock:
            stale = self._checked_at is None or time.monotonic() - self._checked_at >= self.ttl
            start = stale and not self._busy
            if start:
                self._busy = True
            vid = self._id
        if start:
            if self.background:
                self._spawn()
            else:
                self.refresh()
                with self._lock:
                    vid = self._id
        return vid
