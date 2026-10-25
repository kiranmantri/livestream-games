"""STT spike (spikes/stt_compare.py): metrics, reference text, chunking and the two HTTP providers. No network, no models."""
from __future__ import annotations

import io
import subprocess
import sys
import wave

import httpx
import pytest

from spikes import stt_compare as S


def wav_of(seconds: float, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


# ---------------------------------------------------------------- WER
def test_wer_ignores_case_accents_and_punctuation():
    assert S.wer("Não, a Terra é plana!", "nao a terra e plana") == 0


def test_wer_counts_substitution_deletion_insertion():
    assert S.wer("a b c d", "a x c d") == 0.25
    assert S.wer("a b c d", "a c d") == 0.25
    assert S.wer("a b c d", "a b c d e") == 0.25


def test_wer_edge_cases():
    assert S.wer("", "") == 0
    assert S.wer("", "algo") == 1.0
    assert S.wer("a b", "") == 1.0


# ---------------------------------------------------------------- reference from YouTube captions
VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
olá pessoal

00:00:03.000 --> 00:00:05.000
olá pessoal
bem-vindos ao canal

00:00:05.000 --> 00:00:07.000
bem-vindos ao canal
hoje <00:00:06.000><c>vamos falar</c>

00:01:10.000 --> 00:01:12.000
fora da janela
"""


def test_vtt_rolling_captions_are_deduplicated_and_tags_removed():
    cues = S.parse_vtt(VTT)
    assert [c[2] for c in cues] == ["olá pessoal", "bem-vindos ao canal", "hoje vamos falar", "fora da janela"]


def test_reference_takes_only_cues_inside_the_window():
    cues = S.parse_vtt(VTT)
    assert S.reference_text(cues, 0, 60) == "olá pessoal bem-vindos ao canal hoje vamos falar"
    assert S.reference_text(cues, 60, 120) == "fora da janela"
    assert S.reference_text(cues, 200, 260) == ""


# ---------------------------------------------------------------- chunking
def test_split_wav_makes_equal_chunks_and_a_shorter_last_one():
    chunks = S.split_wav(wav_of(30), 12)
    assert [round(S.wav_seconds(c), 1) for c in chunks] == [12.0, 12.0, 6.0]


def test_split_wav_drops_a_tail_shorter_than_one_second():
    assert len(S.split_wav(wav_of(12.4), 12)) == 1


# ---------------------------------------------------------------- HTTP providers (httpx MockTransport)
def client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_groq_request_shape_and_response():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], req.read()
        return httpx.Response(200, json={"text": " Olá, mundo. "})

    p = S.GroqProvider("k-123", client=client_with(handler))
    assert p.transcribe(wav_of(1)) == "Olá, mundo."
    assert seen["url"] == "https://api.groq.com/openai/v1/audio/transcriptions" and seen["auth"] == "Bearer k-123"
    for needle in (b"whisper-large-v3-turbo", b'name="language"', b"pt", b'name="temperature"', b"RIFF"):
        assert needle in seen["body"]


def test_grok_request_shape_file_is_last_field():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], req.read()
        return httpx.Response(200, json={"text": "oi", "duration": 1.0})

    p = S.GrokProvider("xai-9", client=client_with(handler))
    assert p.transcribe(wav_of(1)) == "oi"
    body = seen["body"]
    assert seen["url"] == "https://api.x.ai/v1/stt" and seen["auth"] == "Bearer xai-9"
    assert b"grok-voice-transcribe-2.0" in body and b'name="language"' in body and b'name="format"' in body
    assert body.index(b'name="file"') > max(body.index(b'name="model"'), body.index(b'name="language"'), body.index(b'name="format"'))


def test_http_error_becomes_a_provider_error_with_status_and_no_key_in_message():
    p = S.GroqProvider("SECRET-KEY", client=client_with(lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})))
    with pytest.raises(S.ProviderError) as e:
        p.transcribe(wav_of(1))
    assert "401" in str(e.value) and "SECRET-KEY" not in str(e.value)


def test_a_429_is_retried_once_after_retry_after(monkeypatch):
    slept, calls = [], []
    monkeypatch.setattr(S.time, "sleep", slept.append)

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "2"}) if len(calls) == 1 else httpx.Response(200, json={"text": "ok"})

    assert S.GroqProvider("k", client=client_with(handler)).transcribe(wav_of(1)) == "ok"
    assert slept == [2.0] and len(calls) == 2


def test_network_failure_is_a_provider_error():
    def boom(req):
        raise httpx.ConnectError("sem rede")

    with pytest.raises(S.ProviderError):
        S.GrokProvider("k", client=client_with(boom)).transcribe(wav_of(1))


# ---------------------------------------------------------------- provider registry
def test_make_provider_names_prices_and_missing_keys():
    with pytest.raises(S.MissingKey) as e:
        S.make_provider("groq", env={})
    assert "GROQ_API_KEY" in str(e.value) and "console.groq.com" in str(e.value)
    with pytest.raises(S.MissingKey) as e:
        S.make_provider("grok", env={})
    assert "XAI_API_KEY" in str(e.value) and "console.x.ai" in str(e.value)
    assert S.make_provider("groq", env={"GROQ_API_KEY": "k"}).price_per_hour == 0.04
    assert S.make_provider("grok", env={"XAI_API_KEY": "k"}).price_per_hour == 0.10
    assert S.make_provider("faster:small", env={}).name == "faster:small"
    assert S.make_provider("mlx:small", env={}).name == "mlx:small"
    with pytest.raises(ValueError):
        S.make_provider("nope", env={})


def test_english_only_mlx_models_are_refused():
    with pytest.raises(ValueError, match="inglês"):
        S.make_provider("mlx:distil-large-v3", env={})


def test_local_providers_say_how_to_install_when_the_library_is_missing(monkeypatch):
    monkeypatch.setattr(S, "_has", lambda module: False)
    with pytest.raises(S.ProviderError, match="faster-whisper"):
        S.make_provider("faster:small", env={}).transcribe(wav_of(1))
    with pytest.raises(S.ProviderError, match="lightning-whisper-mlx"):
        S.make_provider("mlx:small", env={}).transcribe(wav_of(1))


# ---------------------------------------------------------------- running and reporting
class Fake:
    def __init__(self, name, texts, secs=1.0, price=0.0):
        self.name, self.price_per_hour, self._t, self._s, self.i = name, price, texts, secs, 0

    def transcribe(self, wav):
        t = self._t[self.i % len(self._t)]
        self.i += 1
        if isinstance(t, Exception):
            raise t
        return t


def test_run_provider_records_time_text_and_errors_without_raising():
    clock = iter([0, 1.0, 1.0, 3.0, 3.0, 3.5, 10.0])
    p = Fake("f", ["um dois", S.ProviderError("HTTP 500"), ""])
    rows = S.run_provider(p, [wav_of(12)] * 3, chunk_s=12, now=lambda: next(clock))
    assert [r["text"] for r in rows] == ["um dois", "", ""] and [r["error"] for r in rows] == [None, "HTTP 500", None]
    assert [r["elapsed_s"] for r in rows] == [1.0, 2.0, 0.5]


def test_summary_has_rtf_cost_wer_empty_and_errors():
    rows = [{"i": 0, "text": "a b c d", "error": None, "elapsed_s": 2.0}, {"i": 1, "text": "", "error": None, "elapsed_s": 4.0},
            {"i": 2, "text": "", "error": "x", "elapsed_s": 6.0}]
    s = S.summarize("f", 0.04, rows, audio_s=36, reference="a b c e")
    assert s["chunks"] == 3 and s["errors"] == 1 and s["empty"] == 1
    assert s["rtf"] == round(12 / 36, 2) and s["median_s"] == 4.0
    assert s["usd_per_hour"] == 0.04 and s["usd_for_audio"] == round(0.04 * 36 / 3600, 4)
    assert s["wer"] == round(S.wer("a b c e", "a b c d"), 3)


def test_summary_without_reference_has_no_wer():
    s = S.summarize("f", 0.0, [{"i": 0, "text": "x", "error": None, "elapsed_s": 1}], audio_s=12, reference="")
    assert s["wer"] is None


def test_side_by_side_markdown_lines_up_chunks():
    md = S.side_by_side({"a": [{"i": 0, "text": "oi", "error": None}], "b": [{"i": 0, "text": "", "error": "HTTP 500"}]}, 12, "ref aqui")
    assert "00:00" in md and "oi" in md and "HTTP 500" in md and "ref aqui" in md


def test_cli_without_audio_source_explains_itself(capsys):
    assert S.main(["--providers", "faster:small"]) == 2
    assert "--wav" in capsys.readouterr().err


def test_cli_with_a_missing_key_stops_before_any_download(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    wav = tmp_path / "a.wav"
    wav.write_bytes(wav_of(5))
    assert S.main(["--wav", str(wav), "--providers", "groq", "--out-dir", str(tmp_path / "o")]) == 2
    assert "GROQ_API_KEY" in capsys.readouterr().err


def test_the_script_compiles_on_python_3_11():
    """The spike runs on 3.11 (lightning-whisper-mlx pins an old tiktoken); 3.12+ accepts syntax that 3.11 rejects (f-string backslashes)."""
    import shutil
    import subprocess

    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv not available")
    found = subprocess.run([uv, "python", "find", "3.11"], capture_output=True, text=True, check=False)
    if found.returncode != 0:
        pytest.skip("no Python 3.11 installed")
    r = subprocess.run([found.stdout.strip(), "-c", "import ast,sys; ast.parse(open(sys.argv[1]).read())", S.__file__], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


def test_ytdlp_prefers_the_python_module_so_with_yt_dlp_gets_the_latest(monkeypatch):
    monkeypatch.setattr(S, "_has", lambda module: module == "yt_dlp")
    assert S.ytdlp_cmd() == [sys.executable, "-m", "yt_dlp"]
    monkeypatch.setattr(S, "_has", lambda module: False)
    assert S.ytdlp_cmd() == ["yt-dlp"]


def test_a_failed_download_explains_how_to_update_yt_dlp_instead_of_a_traceback(monkeypatch, capsys, tmp_path):
    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, ["yt-dlp"])

    monkeypatch.setattr(S, "fetch_audio", boom)
    assert S.main(["--video", "abc", "--providers", "faster:small", "--out-dir", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "yt-dlp" in err and "--with yt-dlp" in err and "--wav" in err
