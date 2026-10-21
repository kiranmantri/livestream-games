"""Test doubles for the live-chat worker: nothing here ever starts a real yt-dlp or reaches YouTube."""
from __future__ import annotations

import json
import time


def chat_line(key: str, text: str, author: str = "@fulano", ts_us: int | None = None, renderer: str = "liveChatTextMessageRenderer",
              runs: list | None = None, wrapped: bool = True) -> str:
    """One JSON line like the ones yt-dlp writes for a live chat (replayChatItemAction wrapper, no video offset)."""
    r = {"id": key, "authorName": {"simpleText": author}, "message": {"runs": runs if runs is not None else [{"text": text}]}}
    if ts_us is not None:
        r["timestampUsec"] = str(ts_us)
    add = {"addChatItemAction": {"item": {renderer: r}}}
    return json.dumps({"replayChatItemAction": {"actions": [add]}} if wrapped else add, ensure_ascii=False)


class FakeProcess:
    """ChatProcess double: `batches` are returned one per read_lines() call; `die_after` reads => alive() turns False."""

    def __init__(self, batches=(), die_after: int | None = None, fail_start: bool = False):
        self.batches = list(batches)
        self.die_after = die_after
        self.fail_start = fail_start
        self.started = self.stopped = False
        self.reads = 0

    def start(self):
        if self.fail_start:
            raise OSError("cannot start")
        self.started = True

    def read_lines(self) -> list[str]:
        self.reads += 1
        return list(self.batches.pop(0)) if self.batches else []

    def alive(self) -> bool:
        return self.started and not self.stopped and not (self.die_after is not None and self.reads >= self.die_after)

    def stop(self):
        self.stopped = True


class Factory:
    """Hands out the scripted processes in order (then idle ones) and records how many were requested."""

    def __init__(self, *procs: FakeProcess):
        self.procs = list(procs)
        self.made: list[FakeProcess] = []
        self.video_ids: list[str] = []

    def __call__(self, video_id: str) -> FakeProcess:
        self.video_ids.append(video_id)
        p = self.procs.pop(0) if self.procs else FakeProcess()
        self.made.append(p)
        return p


def wait_until(cond, timeout: float = 5.0, step: float = 0.02) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()
