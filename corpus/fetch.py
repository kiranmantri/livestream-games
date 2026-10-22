"""Download pt transcripts + live chat of the last N HORIZONTAL streams using the yt-dlp CLI."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CHANNEL_STREAMS = "https://www.youtube.com/@O_Poligrafo/streams"
WATCH = "https://www.youtube.com/watch?v={}"
BASE = ["yt-dlp", "--no-update", "--ignore-no-formats-error"]
# Transcript tracks, in preference order. 'pt-orig' is the original-language auto caption.
TRANSCRIPT_LANGS = ["pt-orig", "pt"]


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, capture_output=True, text=True, **kw)


def list_recent_ids(limit: int) -> list[tuple[str, str]]:
    """Newest-first (id, title) from the /streams tab (flat, no per-video requests)."""
    r = run(BASE + ["--flat-playlist", "--playlist-end", str(limit),
                    "--print", "%(id)s\t%(title)s", CHANNEL_STREAMS])
    if r.returncode != 0:
        raise RuntimeError(f"listing failed: {r.stderr[-500:]}")
    return [tuple(line.split("\t", 1)) for line in r.stdout.splitlines() if "\t" in line]


def get_meta(video_id: str, timeout: float | None = None) -> dict:
    r = run(BASE + ["--skip-download", "-J", WATCH.format(video_id)], timeout=timeout)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"metadata failed for {video_id}: {r.stderr[-300:]}")
    d = json.loads(r.stdout)
    w, h = d.get("width"), d.get("height")
    if not (w and h):  # fall back to the best format's size
        sized = [f for f in d.get("formats", []) if f.get("width") and f.get("height")]
        if sized:
            w, h = sized[-1]["width"], sized[-1]["height"]
    orientation = "unknown" if not (w and h) else ("horizontal" if w > h else "vertical")
    subs = d.get("subtitles", {}) or {}
    auto = d.get("automatic_captions", {}) or {}
    return {
        "video_id": video_id, "title": d.get("title"), "upload_date": d.get("upload_date"),
        "duration": d.get("duration"), "live_status": d.get("live_status"),
        "width": w, "height": h, "orientation": orientation,
        "transcript_langs_available": [k for k in auto if k.startswith("pt")],
        "has_live_chat": "live_chat" in subs,
    }


def download(video_id: str, out: Path, meta: dict, timeout: float | None = None) -> dict:
    """Download transcript (vtt) and live chat (json) via yt-dlp; idempotent."""
    vdir = out / video_id
    vdir.mkdir(parents=True, exist_ok=True)
    result = {"transcript": None, "chat": None, "errors": []}

    def find(suffix: str) -> Path | None:
        hits = sorted(vdir.glob(f"{video_id}*{suffix}"))
        return hits[0] if hits else None

    if not find(".vtt"):
        lang = next((l for l in TRANSCRIPT_LANGS if l in meta["transcript_langs_available"]), None)
        if lang:
            r = run(BASE + ["--skip-download", "--write-auto-subs", "--sub-langs", lang,
                            "--sub-format", "vtt", "-o", str(vdir / "%(id)s"), WATCH.format(video_id)], timeout=timeout)
            if r.returncode != 0:
                result["errors"].append(f"transcript: {r.stderr[-300:]}")
        else:
            result["errors"].append("transcript: no pt caption track available")
    if not find(".live_chat.json"):
        if meta["has_live_chat"]:
            r = run(BASE + ["--skip-download", "--write-subs", "--sub-langs", "live_chat",
                            "-o", str(vdir / "%(id)s"), WATCH.format(video_id)], timeout=timeout)
            if r.returncode != 0:
                result["errors"].append(f"chat: {r.stderr[-300:]}")
        else:
            result["errors"].append("chat: no live_chat track available")
    t, c = find(".vtt"), find(".live_chat.json")
    result["transcript"] = str(t.relative_to(out)) if t else None
    result["chat"] = str(c.relative_to(out)) if c else None
    return result


def fetch(last: int, out: Path, scan: int = 40, dry_run: bool = False) -> int:
    out.mkdir(parents=True, exist_ok=True)
    selected, skipped = [], []
    for vid, title in list_recent_ids(scan):
        if len(selected) >= last:
            break
        meta = get_meta(vid)
        ok = meta["orientation"] == "horizontal" and meta["live_status"] in ("was_live", "not_live")
        tag = "KEEP" if ok else f"SKIP({meta['orientation']},{meta['live_status']})"
        print(f"{tag:28} {vid} {meta['width']}x{meta['height']} {title[:60]}", flush=True)
        (selected if ok else skipped).append(meta)
    if len(selected) < last:
        print(f"WARNING: only {len(selected)} horizontal streams found in the last {scan}", file=sys.stderr)
    manifest = []
    for meta in selected:
        if dry_run:
            continue
        print(f"downloading {meta['video_id']} ...", flush=True)
        res = download(meta["video_id"], out, meta)
        manifest.append({**meta, **res})
        print(f"  transcript={res['transcript']} chat={res['chat']} errors={res['errors']}", flush=True)
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0 if all(not m["errors"] for m in manifest) else 1
