# ruff: noqa: BLE001, S110  -- the worker thread must survive ANY failure of the audio process, whisper.cpp or the DB: it records "down" and retries
"""Live speech-to-text worker (S1): transcribes the audio of the CURRENT live stream into `stream_events` (kind 'transcript').

Pipeline: yt-dlp (live audio) | ffmpeg (16 kHz mono ~12 s WAV segments) -> SegmentSource (adds ~1 s overlap) -> silence gate
-> whisper.cpp HTTP server (self-hosted, its own Machine) -> clean (noise tags, known hallucinations, loops) -> trim the overlap
-> insert. One daemon thread per live session, same shape as app/chatworker.py. No new dependency: urllib, wave, array.

STATUS: built and tested against FAKES only (audio source, transcriber, clock) plus a local fake HTTP server. NOT verified against a
real yt-dlp|ffmpeg live capture from a datacenter IP, nor against a real whisper.cpp server: the exact whisper.cpp request fields
(/inference, verbose_json, prompt) and the ffmpeg segment flags follow its documentation as remembered and MUST be checked in spike S0.
Nothing starts this worker yet (feed.tick wiring is S2); with STT_PROVIDER unset/off, ensure() does nothing.

Weaknesses (accepted): a transcript trails the video by YouTube latency + chunk length + STT time; a chunk lost during an STT outage is
dropped (the stream is live, a late line is stale); no speaker labels; the hallucination list is hand-made and will need tuning (S4).
"""
from __future__ import annotations

import array
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID

import psycopg

from app.db import connect

log = logging.getLogger(__name__)

POLL_S = 1.0
STALL_S = 60.0                # no audio chunk for this long (segments arrive every ~12 s) => restart the capture
BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 60.0
SILENCE_RMS = 100             # of 32768 (about -50 dBFS); tune in S4 against the real stream
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
RATE = 16000
DEFAULT_PROMPT = "Debate em português do Brasil sobre religião, ateísmo, política e falácias lógicas."


class TranscribeError(Exception):
    """The transcriber could not produce a result (server down, HTTP error, unreadable reply)."""


# ------------------------------------------------------------------ text cleaning
_NOISE_WORDS = {"musica", "aplausos", "risos", "silencio", "ruido", "palmas", "riso", "som", "musica de fundo"}
_HALLUCINATIONS = [re.compile(p) for p in (
    r"^obrigad[oa] (por|pela) (assistir|assistirem|ter assistido)( o video)?$",
    r"^legendas? (pela comunidade|por|de|feitas? por|adriana zanotto).*$",
    r"^transcricao e legendas (por|pela).*$",
    r"\bamara org\b",
    r"^(inscreva se|se inscreva) no canal( e ative o sininho)?$",
)]


def _norm(text: str) -> str:
    """Lower case, no accents, punctuation -> spaces: for comparisons only."""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w]+", " ", t).split())


def clean_text(raw: str) -> str:
    """The speech in a recognised segment, or '' when there is none (noise tag, music, hallucination, loop, no letters)."""
    t = unicodedata.normalize("NFC", raw or "")
    t = re.sub(r"[♪♫♬]+", " ", t)
    t = re.sub(r"\[[^\]]*\]", " ", t)
    t = re.sub(r"\(([^)]*)\)", lambda m: " " if _norm(m.group(1)) in _NOISE_WORDS else m.group(0), t)
    t = " ".join(t.split())
    if not re.search(r"[^\W\d_]", t):
        return ""
    n = _norm(t)
    if any(p.search(n) for p in _HALLUCINATIONS):
        return ""
    tokens = n.split()
    if len(tokens) >= 4 and len(set(tokens)) == 1:
        return ""
    if len(tokens) >= 6 and len(set(tokens)) <= len(tokens) // 3:
        return ""
    return t


def _key(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


def trim_overlap(prev: str, text: str) -> str:
    """Remove from the start of `text` the longest run of >= 2 words that ends `prev` (the ~1 s of audio two chunks share)."""
    pw, tw = prev.split(), text.split()
    for k in range(min(len(pw), len(tw)), 1, -1):
        if [_key(w) for w in pw[-k:]] == [_key(w) for w in tw[:k]]:
            return " ".join(tw[k:])
    return text


# ------------------------------------------------------------------ audio helpers
@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class Chunk:
    start_ms: int          # offset from the start of the capture (includes the overlap prepended to it)
    wav: bytes


@dataclass(frozen=True)
class Line:
    key: str
    text: str


def _read_wav(wav: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(wav), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("expected 16-bit mono WAV")
        return w.readframes(w.getnframes()), w.getframerate()


def _make_wav(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_seconds(wav: bytes) -> float:
    pcm, rate = _read_wav(wav)
    return len(pcm) / 2 / rate


def is_silent(wav: bytes, threshold: float = SILENCE_RMS) -> bool:
    """True when the chunk has no audible sound (or cannot be read): it is neither sent to whisper (cost, hallucinations) nor stored."""
    try:
        pcm, _ = _read_wav(wav)
    except Exception:
        return True
    if len(pcm) < 2:
        return True
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    sample = samples[::4] or samples
    rms = (sum(s * s for s in sample) / len(sample)) ** 0.5
    return rms < threshold


class ChunkAssembler:
    """Turns consecutive ffmpeg segments into chunks that start `overlap_s` earlier (the tail of the previous segment is prepended),
    so a word cut at a segment border is heard whole in one of the two chunks."""

    def __init__(self, chunk_s: int, overlap_s: int):
        self.chunk_s, self.overlap_s = chunk_s, overlap_s
        self._tail = b""
        self._last_index: int | None = None

    def add(self, index: int, wav: bytes) -> Chunk:
        pcm, rate = _read_wav(wav)
        tail = self._tail if self._last_index is not None and index == self._last_index + 1 else b""
        start_ms = index * self.chunk_s * 1000 - int(len(tail) / 2 / rate * 1000)
        keep = self.overlap_s * rate * 2
        self._tail = pcm[-keep:] if keep else b""
        self._last_index = index
        return Chunk(max(0, start_ms), _make_wav(tail + pcm, rate))


# ------------------------------------------------------------------ whisper.cpp client
def parse_whispercpp(payload) -> list[Segment]:
    """Segments from a whisper.cpp server reply (verbose_json: start/end in seconds; plain json: just "text")."""
    if not isinstance(payload, dict):
        return []
    segs = payload.get("segments")
    out: list[Segment] = []
    if isinstance(segs, list):
        for s in segs:
            if not isinstance(s, dict) or not isinstance(s.get("text"), str) or not s["text"].strip():
                continue
            try:
                a, b = int(float(s.get("start", 0)) * 1000), int(float(s.get("end", 0)) * 1000)
            except (TypeError, ValueError):
                a = b = 0
            out.append(Segment(a, b, s["text"].strip()))
        return out
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return [Segment(0, 0, text.strip())]
    return []


class Transcriber(Protocol):
    def transcribe(self, wav: bytes, prompt: str = "") -> list[Segment]: ...


def _multipart(fields: dict[str, str], wav: bytes) -> tuple[bytes, str]:
    """multipart/form-data body: the text fields first, the audio file LAST (xAI requires that order)."""
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="chunk.wav"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode() + wav + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class WhisperCppTranscriber:
    """POST <url>/inference (multipart) to a whisper.cpp server. Raises TranscribeError on any failure."""

    def __init__(self, url: str, timeout: float = 120.0, language: str = "pt"):
        self.url, self.timeout, self.language = url.rstrip("/"), timeout, language

    def _body(self, wav: bytes, prompt: str) -> tuple[bytes, str]:
        fields = {"temperature": "0.0", "temperature_inc": "0.2", "response_format": "verbose_json", "language": self.language}
        if prompt:
            fields["prompt"] = prompt
        return _multipart(fields, wav)

    def transcribe(self, wav: bytes, prompt: str = "") -> list[Segment]:
        body, ctype = self._body(wav, prompt)
        req = urllib.request.Request(f"{self.url}/inference", data=body, method="POST", headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return parse_whispercpp(json.loads(r.read().decode("utf-8", errors="replace")))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise TranscribeError(str(e)) from e


# Hosted speech-to-text (chosen in the STT provider spike: spikes/stt_compare.py; the self-hosted whisper `small` on Fly's shared CPU was too slow).
USER_AGENT = "livegames-stt/1.0"      # urllib's default one is blocked by Cloudflare (Groq answers 403, error code 1010)
API_PROVIDERS = {          # name -> (default URL, key env var, model)
    "groq": ("https://api.groq.com/openai/v1/audio/transcriptions", "GROQ_API_KEY", "whisper-large-v3-turbo"),
    "grok": ("https://api.x.ai/v1/stt", "XAI_API_KEY", "grok-voice-transcribe-2.0"),
}


class ApiTranscriber:
    """One multipart POST per chunk to Groq or xAI. TranscribeError on any failure (its message never contains the key).

    An EMPTY answer for a chunk that is not silent is asked once more: in the spike Grok returned nothing for 12 s of clear speech.
    Errors (401, 429, 5xx, network) are NOT retried here; the worker backs off and drops the chunk (live audio is stale later).
    """

    def __init__(self, provider: str, key: str, url: str = "", language: str = "pt", timeout: float = 30.0):
        default_url, _, self.model = API_PROVIDERS[provider]
        self.provider, self._key, self.url, self.language, self.timeout = provider, key, url or default_url, language, timeout

    def _fields(self, prompt: str) -> dict[str, str]:
        auto = self.language == "auto"
        if self.provider == "groq":
            f = {"model": self.model, "response_format": "verbose_json", "temperature": "0"}
            if prompt:
                f["prompt"] = prompt                   # xAI has no free-text prompt, only `keyterm`
        else:
            f = {"model": self.model}
            if not auto:
                f["format"] = "true"                   # formatting needs a language
        if not auto:
            f["language"] = self.language
        return f

    def _post(self, wav: bytes, prompt: str) -> list[Segment]:
        body, ctype = _multipart(self._fields(prompt), wav)
        req = urllib.request.Request(self.url, data=body, method="POST", headers={"Content-Type": ctype, "Authorization": f"Bearer {self._key}", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return parse_whispercpp(json.loads(r.read().decode("utf-8", errors="replace")))
        except urllib.error.HTTPError as e:
            raise TranscribeError(f"{self.provider}: HTTP {e.code}") from None      # no body, no headers: nothing that could carry the key
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise TranscribeError(f"{self.provider}: {type(e).__name__}") from None

    def transcribe(self, wav: bytes, prompt: str = "") -> list[Segment]:
        for _ in range(2):
            segments = self._post(wav, prompt)
            if segments:
                return segments
        return []


# ------------------------------------------------------------------ the audio capture process
def capture_commands(video_id: str, workdir: Path, chunk_s: int) -> tuple[list[str], list[str]]:
    """(yt-dlp, ffmpeg): yt-dlp writes the live audio (live edge, not from the start) to stdout, ffmpeg cuts 16 kHz mono WAV segments."""
    if not YT_ID.match(video_id or ""):
        raise ValueError("not a YouTube video id")
    ytdlp = [sys.executable, "-m", "yt_dlp", "-f", "ba/b", "--no-warnings", "--no-playlist", "--quiet", "-o", "-",
             f"https://www.youtube.com/watch?v={video_id}"]
    ffmpeg = ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-vn", "-ac", "1", "-ar", str(RATE), "-f", "segment",
              "-segment_time", str(chunk_s), "-segment_format", "wav", str(Path(workdir) / "seg%05d.wav")]
    return ytdlp, ffmpeg


class SegmentSource:
    """AudioSource: start() / next_chunk() / alive() / stop(). Files live in a private temp dir, removed by stop()."""

    def __init__(self, video_id: str, chunk_s: int, overlap_s: int, workdir: Path | None = None,
                 commands: tuple[list[str], ...] | None = None):
        self.workdir = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="lgstt-"))
        self.commands = commands if commands is not None else capture_commands(video_id, self.workdir, chunk_s)
        self._asm = ChunkAssembler(chunk_s, overlap_s)
        self._procs: list[subprocess.Popen] = []
        self._errfiles: list = []
        self._next = 0

    def start(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        prev = None
        for cmd in self.commands:
            err = open(self.workdir / f"proc{len(self._procs)}.err", "wb")  # noqa: SIM115 - closed in stop(); why a capture died (certificates, 403, ...)
            self._errfiles.append(err)
            p = subprocess.Popen(cmd, cwd=self.workdir, stdin=prev.stdout if prev else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE if cmd is not self.commands[-1] else subprocess.DEVNULL,
                                 stderr=err, start_new_session=True)
            if prev is not None and prev.stdout is not None:
                prev.stdout.close()
            self._procs.append(p)
            prev = p

    def alive(self) -> bool:
        return bool(self._procs) and self._procs[-1].poll() is None

    def error_tail(self, limit: int = 400) -> str:
        """The last thing the capture processes printed on stderr (one line per process), for the log."""
        out = []
        for f in sorted(self.workdir.glob("proc*.err")):
            try:
                text = " ".join(f.read_text(errors="replace").split())
            except OSError:
                continue
            if text:
                out.append(text[-limit:])
        return " | ".join(out)

    def _path(self, index: int) -> Path:
        return self.workdir / f"seg{index:05d}.wav"

    def next_chunk(self) -> Chunk | None:
        """The next finished segment: one is finished when the following file exists, or when the capture process has ended."""
        path = self._path(self._next)
        if not path.exists() or (not self._path(self._next + 1).exists() and self.alive()):
            return None
        index, self._next = self._next, self._next + 1
        try:
            wav = path.read_bytes()
            path.unlink(missing_ok=True)
            return self._asm.add(index, wav)
        except Exception:
            path.unlink(missing_ok=True)
            return None

    def stop(self) -> None:
        procs, self._procs = self._procs, []
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        for f in self._errfiles:
            f.close()
        self._errfiles = []
        shutil.rmtree(self.workdir, ignore_errors=True)


class AudioSource(Protocol):
    def start(self) -> None: ...
    def next_chunk(self) -> Chunk | None: ...
    def alive(self) -> bool: ...
    def stop(self) -> None: ...


# ------------------------------------------------------------------ storing
def ingest(conn: psycopg.Connection, session_id: UUID, lines: list[Line]) -> int:
    """Insert transcript lines into an ACTIVE live Falácias session; a key already stored (retry, restart) is skipped. Returns rows added."""
    added = 0
    for ln in lines:
        added += conn.execute(
            "INSERT INTO stream_events (session_id, kind, source_key, author, text) "
            "SELECT id, 'transcript', %s, NULL, %s FROM game_sessions "
            "WHERE id = %s AND status = 'active' AND game_type = 'falacias' AND source_mode = 'live' "
            "ON CONFLICT DO NOTHING", (ln.key, ln.text, session_id)).rowcount
    return added


# ------------------------------------------------------------------ configuration
@dataclass(frozen=True)
class SttConfig:
    provider: str
    url: str
    chunk_s: int
    overlap_s: int
    prompt: str
    language: str = "pt"
    api_key: str = field(default="", repr=False)      # never printed


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return v if lo <= v <= hi else default


def config() -> SttConfig:
    provider = os.environ.get("STT_PROVIDER", "").strip().lower()
    chunk_s = _int_env("STT_CHUNK_S", 12, 4, 30)
    overlap_s = _int_env("STT_OVERLAP_S", 1, 0, 5)
    if overlap_s >= chunk_s:
        overlap_s = 1
    lang = os.environ.get("STT_LANGUAGE", "").strip().lower()
    if not re.fullmatch(r"auto|[a-z]{2,3}", lang):
        lang = "pt"
    prompt = os.environ.get("STT_PROMPT", "").strip() or (DEFAULT_PROMPT if lang == "pt" else "")   # the default prompt is Portuguese
    url = os.environ.get("STT_URL", "").strip()
    if provider in API_PROVIDERS:
        key = os.environ.get(API_PROVIDERS[provider][1], "").strip()
        # STT_URL is the whisper.cpp server only. Fly merges env, so a stale STT_URL can outlive its removal from the config and
        # must never receive a hosted provider's key; STT_API_URL is the explicit override (tests, proxies).
        return SttConfig(provider if key else "off", os.environ.get("STT_API_URL", "").strip(), chunk_s, overlap_s, prompt, lang, key)
    return SttConfig("whispercpp" if provider == "whispercpp" else "off", url, chunk_s, overlap_s, prompt, lang)


def enabled() -> bool:
    c = config()
    return c.provider in API_PROVIDERS or (c.provider == "whispercpp" and bool(c.url))


def _default_source(video_id: str) -> SegmentSource:
    c = config()
    return SegmentSource(video_id, c.chunk_s, c.overlap_s)


def _default_transcriber(cfg: SttConfig) -> Transcriber:
    if cfg.provider in API_PROVIDERS:
        return ApiTranscriber(cfg.provider, cfg.api_key, cfg.url, cfg.language)
    return WhisperCppTranscriber(cfg.url, language=cfg.language)


source_factory: Callable[[str], AudioSource] = _default_source
make_transcriber: Callable[[SttConfig], Transcriber] = _default_transcriber
connect_fn: Callable[[], psycopg.Connection] = connect


# ------------------------------------------------------------------ the worker
class SttWorker:
    """One live session. step() is one deterministic iteration (the thread just calls it); the clock is injectable for tests."""

    def __init__(self, session_id: UUID, video_id: str, factory: Callable[[str], AudioSource], transcriber: Transcriber,
                 connect_fn: Callable[[], psycopg.Connection], clock: Callable[[], float] = time.monotonic,
                 stall_s: float = STALL_S, prompt: str = ""):
        self.session_id, self.video_id = session_id, video_id
        self._factory, self._transcriber, self._connect = factory, transcriber, connect_fn
        self._clock, self.stall_s, self.prompt = clock, stall_s, prompt
        self._conn: psycopg.Connection | None = None
        self._src: AudioSource | None = None
        self._ok = True
        self._failures = 0
        self._next_try = 0.0
        self._last_activity = 0.0
        self._last_text = ""
        self.stop_event = threading.Event()

    def healthy(self) -> bool:
        return self._ok

    def retry_in(self) -> float:
        return max(0.0, self._next_try - self._clock())

    def _drop_src(self) -> None:
        s, self._src = self._src, None
        if s is not None:
            try:
                s.stop()
            except Exception:
                pass

    def _backoff(self, now: float) -> None:
        self._ok = False
        self._failures += 1
        self._next_try = now + min(BACKOFF_BASE_S * 2 ** (self._failures - 1), BACKOFF_MAX_S)

    def _report(self, what: str, detail: object = "") -> None:
        """Log a failure with its reason: the first one, then at the 2nd, 4th, 8th... (a long outage must not flood the log)."""
        n = self._failures
        if n & (n - 1) == 0:
            log.warning("stt %s (video %s, failure #%d): %s", what, self.video_id, n, str(detail)[:500] or "no details")

    def _fail(self, now: float, what: str = "capture or database failed", detail: object = "") -> None:
        """The audio capture or the database failed: restart the capture after the backoff."""
        self._backoff(now)
        self._report(what, detail)
        self._drop_src()

    def _db(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = self._connect()
        return self._conn

    def close(self) -> None:
        self._drop_src()
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _lines(self, chunk: Chunk, segments: list[Segment]) -> list[Line]:
        out: list[Line] = []
        for i, seg in enumerate(segments):
            text = trim_overlap(self._last_text, clean_text(seg.text))
            if text and self.prompt and _norm(text) in _norm(self.prompt):     # whisper answered a quiet chunk with its own prompt
                text = ""
            if text:
                out.append(Line(f"{chunk.start_ms}:{i}", text))
                self._last_text = text
        return out

    def step(self) -> str:
        now = self._clock()
        try:
            conn = self._db()
            active = conn.execute("SELECT 1 FROM game_sessions WHERE id = %s AND status = 'active'", (self.session_id,)).fetchone()
        except Exception as e:
            self._conn = None
            self._fail(now, "database error", e)
            return "down"
        if not active:
            self._drop_src()
            return "ended"
        if self._src is None:
            if now < self._next_try:
                return "down"
            try:
                src = self._factory(self.video_id)
                src.start()
            except Exception as e:
                self._fail(now, "cannot start the audio capture", e)
                return "down"
            self._src, self._last_activity = src, now
        try:
            chunk = self._src.next_chunk()
            if chunk is None:
                if not self._src.alive() or now - self._last_activity > self.stall_s:
                    tail = getattr(self._src, "error_tail", lambda: "")()
                    self._fail(now, "audio capture ended" if not self._src.alive() else "no audio for too long", tail)
                    return "down"
                return "ok" if self._ok else "down"
            self._last_activity = now
            if now < self._next_try:                     # transcriber in backoff: drain the live audio, do not queue it up
                return "down"
            if is_silent(chunk.wav):
                return "ok" if self._ok else "down"
            try:
                segments = self._transcriber.transcribe(chunk.wav, self.prompt)
            except Exception as e:
                self._backoff(now)                       # the capture keeps running; only this chunk is lost
                self._report("transcriber failed", e)
                return "down"
            if self._failures:
                log.info("stt recovered (video %s) after %d failure(s)", self.video_id, self._failures)
            self._ok, self._failures = True, 0
            ingest(conn, self.session_id, self._lines(chunk, segments))
        except Exception as e:
            self._conn = None
            self._fail(now, "error while processing audio", e)
            return "down"
        return "ok"


# ------------------------------------------------------------------ registry (one thread per live session)
_workers: dict[UUID, tuple[SttWorker, threading.Thread]] = {}
_lock = threading.Lock()


def _run(w: SttWorker) -> None:
    try:
        while not w.stop_event.is_set():
            try:
                if w.step() == "ended":
                    break
            except Exception:
                w._fail(w._clock())
            w.stop_event.wait(POLL_S)
    finally:
        w.close()
        with _lock:
            cur = _workers.get(w.session_id)
            if cur is not None and cur[0] is w:
                del _workers[w.session_id]


def ensure(session_id: UUID, video_id: str) -> SttWorker | None:
    """Start (once) the transcription worker of a live session; None while STT is off/misconfigured."""
    if not enabled():
        return None
    with _lock:
        cur = _workers.get(session_id)
        if cur is not None and cur[1].is_alive():
            return cur[0]
        cfg = config()
        w = SttWorker(session_id, video_id, source_factory, make_transcriber(cfg), connect_fn, prompt=cfg.prompt)
        t = threading.Thread(target=_run, args=(w,), name=f"stt-{session_id}", daemon=True)
        _workers[session_id] = (w, t)
        t.start()
        return w


def get(session_id: UUID) -> SttWorker | None:
    with _lock:
        cur = _workers.get(session_id)
    return cur[0] if cur else None


def health(session_id: UUID) -> str:
    """'ok' | 'down'. No worker (off, or not started yet) = 'ok': transcript health never blocks anything else."""
    w = get(session_id)
    return "ok" if w is None or w.healthy() else "down"


def stop_all() -> None:
    with _lock:
        items = list(_workers.values())
    for w, _ in items:
        w.stop_event.set()
    for _, t in items:
        t.join(timeout=5)
    with _lock:
        _workers.clear()
