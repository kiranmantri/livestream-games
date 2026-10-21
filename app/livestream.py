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
from dataclasses import dataclass

log = logging.getLogger("livestream")

DEFAULT_URL = "https://www.youtube.com/@O_Poligrafo/live"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PHONE_MARKER = "📱"
MAX_CANDIDATES = 4        # other live streams of the channel that get looked up while searching for a horizontal one
TAB_ENTRIES = 10          # newest entries read from the channel's /streams tab


class LookupFailed(Exception):
    """Network/extractor problem: the live state is UNKNOWN (keep the previous answer)."""


@dataclass(frozen=True)
class StreamInfo:
    id: str
    title: str
    width: int | None
    height: int | None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"

    @property
    def horizontal(self) -> bool:
        """Only horizontal streams are used (width > height). Without a size the phone marker in the title is the hint."""
        if self.width and self.height:
            return self.width > self.height
        return PHONE_MARKER not in (self.title or "")


def _extract(url: str, flat: bool = False) -> dict | None:
    """yt-dlp metadata of a URL (no download). None when the CHANNEL is not live; LookupFailed when unknown."""
    import yt_dlp
    from yt_dlp.utils import DownloadError

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 10,
            "cachedir": False, "noplaylist": True}
    if flat:                                  # a channel tab: just the entries, no per-video requests
        opts |= {"extract_flat": True, "playlistend": TAB_ENTRIES, "noplaylist": False}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except DownloadError as e:
        if "not currently live" in str(e).lower():
            return None
        raise LookupFailed(str(e)[:200]) from e
    except Exception as e:  # yt-dlp can raise assorted errors on network trouble
        raise LookupFailed(f"{type(e).__name__}: {e}"[:200]) from e


def _int_or_none(v) -> int | None:
    return v if isinstance(v, int) and v > 0 else None


def _as_live(info: dict | None) -> StreamInfo | None:
    if info and info.get("is_live") and VIDEO_ID_RE.match(str(info.get("id", ""))):
        return StreamInfo(info["id"], str(info.get("title") or ""), _int_or_none(info.get("width")),
                          _int_or_none(info.get("height")))
    return None


def _streams_url(live_url: str) -> str:
    return re.sub(r"/live/?$", "/streams", live_url)


def resolve_channel_info(url: str = DEFAULT_URL) -> StreamInfo | None:
    """The live stream to use: the channel runs a vertical AND a horizontal live at the same time, and /live answers only one of
    them. Only HORIZONTAL streams are used, so when /live is vertical the newest live entries of the /streams tab are checked
    (at most MAX_CANDIDATES lookups) for a horizontal one. If there is none, the vertical stream is returned (so the page can
    say it is not used). None = not live; LookupFailed = unknown (only for the first, /live lookup)."""
    first = _as_live(_extract(url))
    if first is None or first.horizontal:
        return first
    try:
        entries = (_extract(_streams_url(url), flat=True) or {}).get("entries") or []
    except LookupFailed as e:
        log.warning("streams tab lookup failed, keeping the /live answer: %s", e)
        return first
    candidates = [str(e.get("id")) for e in entries if e and e.get("live_status") == "is_live"
                  and VIDEO_ID_RE.match(str(e.get("id", ""))) and e.get("id") != first.id]
    for vid in candidates[:MAX_CANDIDATES]:
        try:
            other = _as_live(_extract(f"https://www.youtube.com/watch?v={vid}"))
        except LookupFailed as e:
            log.warning("candidate %s lookup failed: %s", vid, e)
            continue
        if other is not None and other.horizontal:
            return other
    return first


def resolve_channel(url: str = DEFAULT_URL) -> str | None:
    """-> video id if the channel is live now, None if it is not, raises LookupFailed when unknown."""
    live = _as_live(_extract(url))
    return live.id if live else None


def resolve_video_title(video_id: str) -> str | None:
    """Title of any video (e.g. the replay used by Falácias). None when unknown; raises LookupFailed on network trouble."""
    if not VIDEO_ID_RE.match(video_id or ""):
        return None
    info = _extract(f"https://www.youtube.com/watch?v={video_id}")
    return (str(info["title"]) if info and info.get("title") else None)


class LiveStream:
    def __init__(self, resolver: Callable[[], str | StreamInfo | None] | None = None, ttl: float | None = None,
                 forced_id: str | None = None, background: bool = True,
                 video_resolver: Callable[[str], str | None] | None = None):
        url = os.environ.get("LIVE_CHANNEL_URL") or DEFAULT_URL
        self._resolver = resolver or (lambda: resolve_channel_info(url))
        self._video_resolver = video_resolver or (lambda v: resolve_video_title(v))
        self._titles: dict[str, tuple[str | None, float]] = {}      # video id -> (title, looked up at)
        self._title_busy: set[str] = set()
        self.ttl = float(os.environ.get("LIVE_TTL", "60")) if ttl is None else ttl
        forced = forced_id if forced_id is not None else os.environ.get("LIVE_VIDEO_ID", "")
        self.forced = forced if VIDEO_ID_RE.match(forced or "") else None
        self.background = background
        self._lock = threading.Lock()
        self._info: StreamInfo | None = None
        self._checked_at: float | None = None      # None = never looked up yet
        self._busy = False

    def refresh(self) -> None:
        """One lookup, synchronously. A failed lookup keeps the previous answer."""
        try:
            found = self._resolver()
        except LookupFailed as e:
            log.warning("live lookup failed, keeping previous answer: %s", e)
            with self._lock:
                self._checked_at, self._busy = time.monotonic(), False
            return
        with self._lock:
            if isinstance(found, str):
                found = StreamInfo(found, "", None, None)
            self._info = found if found and VIDEO_ID_RE.match(found.id) else None
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
            vid = self._info.id if self._info else None
        if start:
            if self.background:
                self._spawn()
            else:
                self.refresh()
                with self._lock:
                    vid = self._info.id if self._info else None
        return vid

    def info(self) -> StreamInfo | None:
        """Cached id/title/size of the live stream (None = not live / not known yet). Never blocks on the network."""
        if self.forced:
            return StreamInfo(self.forced, "", None, None)
        self.current()                       # triggers the background refresh when stale
        with self._lock:
            return self._info

    def title_of(self, video_id: str) -> str | None:
        """Cached title of ANY video (the replay Falácias uses). None while unknown. Lookups run in ONE background thread
        per video; a failed or empty lookup is retried after `ttl` seconds."""
        if not VIDEO_ID_RE.match(video_id or ""):
            return None
        with self._lock:
            cached = self._titles.get(video_id)
            fresh = cached is not None and (cached[0] is not None or time.monotonic() - cached[1] < self.ttl)
            start = not fresh and video_id not in self._title_busy
            if start:
                self._title_busy.add(video_id)
        if start:
            if self.background:
                threading.Thread(target=self._lookup_title, args=(video_id,), daemon=True, name="title-lookup").start()
            else:
                self._lookup_title(video_id)
        with self._lock:
            got = self._titles.get(video_id)
        return got[0] if got else None

    def _lookup_title(self, video_id: str) -> None:
        title = None
        try:
            title = self._video_resolver(video_id)
        except LookupFailed as e:
            log.warning("title lookup failed for %s: %s", video_id, e)
        except Exception:  # never leave the id marked busy
            log.exception("title lookup crashed")
        with self._lock:
            self._titles[video_id] = (title or None, time.monotonic())
            self._title_busy.discard(video_id)
