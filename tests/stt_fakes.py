"""Test doubles for the speech-to-text worker: nothing here ever starts yt-dlp/ffmpeg or reaches a whisper.cpp server."""
from __future__ import annotations

import io
import math
import struct
import wave

from app.sttworker import Chunk, Segment

RATE = 16000


def make_wav(samples: list[int], rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def silence(seconds: float) -> list[int]:
    return [0] * int(RATE * seconds)


def tone(seconds: float, amp: int = 8000, hz: int = 220) -> list[int]:
    return [int(amp * math.sin(2 * math.pi * hz * i / RATE)) for i in range(int(RATE * seconds))]


def loud_chunk(start_ms: int = 0, seconds: float = 1.0) -> Chunk:
    return Chunk(start_ms, make_wav(tone(seconds)))


def quiet_chunk(start_ms: int = 0, seconds: float = 1.0) -> Chunk:
    return Chunk(start_ms, make_wav(silence(seconds)))


class FakeSource:
    """AudioSource double: `chunks` are returned one per next_chunk() call; `die_after` reads => alive() turns False."""

    def __init__(self, chunks=(), die_after: int | None = None, fail_start: bool = False):
        self.chunks = list(chunks)
        self.die_after = die_after
        self.fail_start = fail_start
        self.started = self.stopped = False
        self.reads = 0

    def start(self):
        if self.fail_start:
            raise OSError("cannot start")
        self.started = True

    def next_chunk(self):
        self.reads += 1
        return self.chunks.pop(0) if self.chunks else None

    def alive(self) -> bool:
        return self.started and not self.stopped and not (self.die_after is not None and self.reads >= self.die_after)

    def stop(self):
        self.stopped = True


class SourceFactory:
    def __init__(self, *sources: FakeSource):
        self.sources = list(sources)
        self.made: list[FakeSource] = []
        self.video_ids: list[str] = []

    def __call__(self, video_id: str):
        self.video_ids.append(video_id)
        s = self.sources.pop(0) if self.sources else FakeSource()
        self.made.append(s)
        return s


class FakeTranscriber:
    """`script` items are consumed one per call: a list of Segment, a str (one segment), or an Exception to raise."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[tuple[int, str]] = []

    def transcribe(self, wav: bytes, prompt: str = ""):
        self.calls.append((len(wav), prompt))
        item = self.script.pop(0) if self.script else []
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return [Segment(0, 1000, item)]
        return list(item)
