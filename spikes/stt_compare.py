"""STT SPIKE: which speech-to-text can transcribe a Portuguese live in (near) real time, for how much money?

Standalone (stdlib + httpx; the local engines are imported only when used). Same audio, same 12 s chunks, every provider:

    groq            Groq whisper-large-v3-turbo (API, ~US$0.04/h)        key: GROQ_API_KEY   (https://console.groq.com/keys)
    grok            xAI grok-voice-transcribe-2.0 batch (API, ~US$0.10/h) key: XAI_API_KEY    (https://console.x.ai)
    faster:<size>   faster-whisper on the CPU, int8 (tiny|base|small|medium|large-v3|...)
    mlx:<model>     lightning-whisper-mlx on Apple Silicon (small|medium|large-v3; the distil-* models are English only)

    PATH=/opt/homebrew/bin:$PATH uv run --no-project --python 3.11 --with httpx --with yt-dlp --with faster-whisper --with lightning-whisper-mlx \\
        python spikes/stt_compare.py --video _nVWPIFCUzQ --start 600 --seconds 300 --providers groq,grok,faster:small,mlx:small

Output in data/spike/stt/: <audio>.wav (cached), <provider>.txt, side_by_side.md, summary.json.
The reference text is YouTube's own automatic caption for the same minutes (data/corpus/<id>/<id>.pt-orig.vtt): it is ANOTHER
speech recognizer, not the truth. Treat WER as "agreement with YouTube", and read side_by_side.md with your own ears/eyes.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
import wave
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "spike" / "stt"
CORPUS = ROOT / "data" / "corpus"


class ProviderError(Exception):
    """A transcription call failed (message safe to print: never contains a key)."""


class MissingKey(Exception):
    pass


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


# ---------------------------------------------------------------- text metrics
def normalize(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text.casefold())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]", " ", t).split()


def wer(reference: str, hypothesis: str) -> float:
    r, h = normalize(reference), normalize(hypothesis)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i]
        for j, hw in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw)))
        prev = cur
    return prev[-1] / len(r)


# ---------------------------------------------------------------- reference: YouTube captions
_TS = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})")


def _secs(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_vtt(text: str) -> list[tuple[float, float, str]]:
    """Cues as (start, end, line). YouTube's rolling captions repeat the previous line: each distinct line is kept once."""
    cues, seen_last = [], ""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _TS.search(lines[i])
        i += 1
        if not m:
            continue
        start, end = _secs(*m.group(1, 2, 3, 4)), _secs(*m.group(5, 6, 7, 8))
        while i < len(lines) and lines[i].strip():
            line = re.sub(r"<[^>]+>", "", lines[i]).strip()
            i += 1
            if line and line != seen_last and not (cues and cues[-1][2] == line):
                cues.append((start, end, line))
            if line:
                seen_last = line
    return cues


def reference_text(cues: list[tuple[float, float, str]], start: float, end: float) -> str:
    return " ".join(c[2] for c in cues if start <= c[0] < end)


# ---------------------------------------------------------------- audio
def wav_seconds(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / w.getframerate()


def split_wav(wav: bytes, chunk_s: int) -> list[bytes]:
    out = []
    with wave.open(io.BytesIO(wav)) as w:
        rate, per = w.getframerate(), w.getframerate() * chunk_s
        params = (w.getnchannels(), w.getsampwidth(), rate)
        while True:
            frames = w.readframes(per)
            if len(frames) // (params[1] * params[0]) < rate:      # nothing left, or a tail shorter than 1 s
                break
            buf = io.BytesIO()
            with wave.open(buf, "wb") as o:
                o.setnchannels(params[0])
                o.setsampwidth(params[1])
                o.setframerate(rate)
                o.writeframes(frames)
            out.append(buf.getvalue())
    return out


# ---------------------------------------------------------------- providers
class _HttpProvider:
    name = ""
    price_per_hour = 0.0
    url = ""
    key_env = ""

    def __init__(self, key: str, client: httpx.Client | None = None, language: str = "pt"):
        self._key, self.language = key, language
        self._client = client or httpx.Client(timeout=120)

    def _fields(self) -> dict[str, str]:
        raise NotImplementedError

    def transcribe(self, wav: bytes) -> str:
        for attempt in (1, 2):
            try:
                r = self._client.post(self.url, headers={"Authorization": f"Bearer {self._key}"}, data=self._fields(),
                                      files={"file": ("chunk.wav", wav, "audio/wav")})     # data fields first, file LAST (xAI requires it)
            except httpx.HTTPError as e:
                raise ProviderError(f"{self.name}: {type(e).__name__}") from e
            if r.status_code == 429 and attempt == 1:
                time.sleep(min(float(r.headers.get("retry-after", "5") or 5), 30.0))
                continue
            if r.status_code != 200:
                raise ProviderError(f"{self.name}: HTTP {r.status_code} {r.text[:160]}".replace(self._key, "***"))
            try:
                return str(r.json()["text"]).strip()
            except (ValueError, KeyError) as e:
                raise ProviderError(f"{self.name}: unexpected reply") from e
        raise ProviderError(f"{self.name}: rate limited")


class GroqProvider(_HttpProvider):
    name, price_per_hour = "groq", 0.04
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    key_env = "GROQ_API_KEY"

    def _fields(self) -> dict[str, str]:
        return {"model": "whisper-large-v3-turbo", "language": self.language, "response_format": "json", "temperature": "0"}


class GrokProvider(_HttpProvider):
    name, price_per_hour = "grok", 0.10
    url = "https://api.x.ai/v1/stt"
    key_env = "XAI_API_KEY"

    def _fields(self) -> dict[str, str]:
        return {"model": "grok-voice-transcribe-2.0", "language": self.language, "format": "true"}


class FasterWhisperProvider:
    price_per_hour = 0.0

    def __init__(self, size: str, language: str = "pt", threads: int = 0):
        self.name, self.size, self.language, self.threads, self._model = f"faster:{size}", size, language, threads, None

    def transcribe(self, wav: bytes) -> str:
        if not _has("faster_whisper"):
            raise ProviderError("faster-whisper is not installed (run with --with faster-whisper)")
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.size, device="cpu", compute_type="int8", cpu_threads=self.threads)
        segs, _ = self._model.transcribe(io.BytesIO(wav), language=self.language, beam_size=1, vad_filter=True,
                                         condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segs).strip()


class MlxProvider:
    price_per_hour = 0.0

    def __init__(self, model: str, language: str = "pt", batch_size: int = 12):
        self.name, self.model, self.language, self.batch_size, self._w = f"mlx:{model}", model, language, batch_size, None

    def transcribe(self, wav: bytes) -> str:
        if not _has("lightning_whisper_mlx"):
            raise ProviderError("lightning-whisper-mlx is not installed (run with --with lightning-whisper-mlx, Python 3.11)")
        if self._w is None:
            from lightning_whisper_mlx import LightningWhisperMLX
            self._w = LightningWhisperMLX(model=self.model, batch_size=self.batch_size, quant=None)
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            f.write(wav)
            f.flush()
            return str(self._w.transcribe(audio_path=f.name, language=self.language)["text"]).strip()


def make_provider(spec: str, env: Mapping[str, str] | None = None, language: str = "pt"):
    env = os.environ if env is None else env
    kind, _, arg = spec.partition(":")
    if kind in ("groq", "grok"):
        cls = GroqProvider if kind == "groq" else GrokProvider
        site = "console.groq.com/keys" if kind == "groq" else "console.x.ai"
        if not env.get(cls.key_env, "").strip():
            raise MissingKey(f"{spec}: set {cls.key_env} (create a key at https://{site})")
        return cls(env[cls.key_env].strip(), language=language)
    if kind == "faster" and arg:
        return FasterWhisperProvider(arg, language)
    if kind == "mlx" and arg:
        if arg.startswith("distil-"):
            raise ValueError(f"{spec}: os modelos distil-* do lightning-whisper-mlx são só em inglês; use small, medium ou large-v3")
        return MlxProvider(arg, language)
    raise ValueError(f"unknown provider {spec!r} (groq, grok, faster:<size>, mlx:<model>)")


# ---------------------------------------------------------------- run and report
def run_provider(provider, chunks: list[bytes], chunk_s: int, now: Callable[[], float] = time.monotonic) -> list[dict]:
    rows = []
    for i, wav in enumerate(chunks):
        t0 = now()
        text, err = "", None
        try:
            text = provider.transcribe(wav)
        except ProviderError as e:
            err = str(e)
        except Exception as e:  # noqa: BLE001  a crash of an engine is a data point of the spike
            err = f"{type(e).__name__}: {str(e)[:160]}"
        rows.append({"i": i, "start_s": i * chunk_s, "text": text, "error": err, "elapsed_s": round(now() - t0, 2)})
    return rows


def summarize(name: str, price_per_hour: float, rows: list[dict], audio_s: float, reference: str) -> dict:
    times = sorted(r["elapsed_s"] for r in rows)
    text = " ".join(r["text"] for r in rows if r["text"])
    return {
        "provider": name, "chunks": len(rows), "errors": sum(1 for r in rows if r["error"]),
        "empty": sum(1 for r in rows if not r["error"] and not r["text"]),
        "total_s": round(sum(times), 1), "rtf": round(sum(times) / audio_s, 2) if audio_s else None,   # < 1 = faster than real time
        "median_s": round(statistics.median(times), 2) if times else None,
        "p90_s": times[min(len(times) - 1, int(len(times) * 0.9))] if times else None,
        "usd_per_hour": price_per_hour, "usd_for_audio": round(price_per_hour * audio_s / 3600, 4),
        "wer": round(wer(reference, text), 3) if reference else None,
    }


def side_by_side(results: dict[str, list[dict]], chunk_s: int, reference: str = "") -> str:
    names = list(results)
    out = ["# STT side by side", "", f"Chunks of {chunk_s} s.", ""]
    if reference:
        out += ["Reference (YouTube automatic caption for the whole window, not the truth):", "", f"> {reference}", ""]
    n = max((len(v) for v in results.values()), default=0)
    for i in range(n):
        out.append(f"## {i * chunk_s // 60:02d}:{i * chunk_s % 60:02d}")
        for name in names:
            rows = results[name]
            r = rows[i] if i < len(rows) else None
            out.append(f"- **{name}**: " + ("-" if r is None else f"ERRO {r['error']}" if r["error"] else r["text"] or "(vazio)"))
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- audio download and CLI
def ytdlp_cmd() -> list[str]:
    """The yt_dlp Python module when present (so `--with yt-dlp` gets the latest: YouTube breaks old versions), else the binary."""
    return [sys.executable, "-m", "yt_dlp"] if _has("yt_dlp") else ["yt-dlp"]


def fetch_audio(video: str, start: int, seconds: int, out_dir: Path) -> Path:
    wav = out_dir / f"{video}_{start}_{seconds}.wav"
    if wav.exists():
        return wav
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        raw = Path(tmp) / "a.%(ext)s"
        subprocess.run([*ytdlp_cmd(), "--no-update", "-f", "bestaudio", "--download-sections", f"*{start}-{start + seconds}", "--force-keyframes-at-cuts",
                        "-o", str(raw), f"https://www.youtube.com/watch?v={video}"], check=True)
        src = next(Path(tmp).glob("a.*"))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", "16000", "-ac", "1", str(wav)], check=True)
    return wav


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--providers", default="faster:small", help="comma separated: groq,grok,faster:<size>,mlx:<model>")
    ap.add_argument("--wav", type=Path, help="a 16 kHz mono wav (instead of --video)")
    ap.add_argument("--video", help="YouTube id: downloads the audio of the window with yt-dlp")
    ap.add_argument("--start", type=int, default=600)
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--chunk-s", type=int, default=12)
    ap.add_argument("--reference-file", type=Path, help="your own text for the window (better than YouTube's captions)")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--language", default="pt")
    a = ap.parse_args(argv)
    if not a.wav and not a.video:
        print("give --wav FILE or --video ID", file=sys.stderr)
        return 2
    try:
        providers = [make_provider(s.strip(), language=a.language) for s in a.providers.split(",") if s.strip()]
    except (MissingKey, ValueError) as e:
        print(e, file=sys.stderr)
        return 2
    a.out_dir.mkdir(parents=True, exist_ok=True)
    try:
        wav_path = a.wav or fetch_audio(a.video, a.start, a.seconds, a.out_dir)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"audio download failed ({type(e).__name__}). Old yt-dlp versions break often: run with `--with yt-dlp` (latest) and "
              "PATH=/opt/homebrew/bin:$PATH for ffmpeg, or pass your own audio with --wav FILE.", file=sys.stderr)
        return 1
    wav = wav_path.read_bytes()
    chunks, audio_s = split_wav(wav, a.chunk_s), wav_seconds(wav)
    reference = ""
    if a.reference_file:
        reference = a.reference_file.read_text()
    elif a.video and (CORPUS / a.video / f"{a.video}.pt-orig.vtt").exists():
        cues = parse_vtt((CORPUS / a.video / f"{a.video}.pt-orig.vtt").read_text())
        reference = reference_text(cues, a.start, a.start + a.seconds)
    print(f"{len(chunks)} chunks of {a.chunk_s} s ({audio_s:.0f} s of audio); reference words: {len(normalize(reference))}", flush=True)
    results, summary = {}, []
    for p in providers:
        print(f"[{p.name}] ...", flush=True)
        rows = run_provider(p, chunks, a.chunk_s)
        results[p.name] = rows
        fname = re.sub(r"[^\w.-]", "_", p.name) + ".txt"
        lines = [f"[{r['start_s']:>5}s] {r['error'] or r['text']}" for r in rows]
        (a.out_dir / fname).write_text("\n".join(lines))
        summary.append(summarize(p.name, p.price_per_hour, rows, audio_s, reference))
        print(json.dumps(summary[-1], ensure_ascii=False), flush=True)
    (a.out_dir / "side_by_side.md").write_text(side_by_side(results, a.chunk_s, reference))
    (a.out_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(f"\nfiles in {a.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
