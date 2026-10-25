"""Hosted STT providers in app/sttworker.py (STT_PROVIDER=groq|grok): config, request shape, errors, retry on an empty reply."""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from app import sttworker as W
from app.sttworker import Segment
from tests.stt_fakes import make_wav, tone

KEY = "sk-TEST-0123456789abcdef"


class _Api(BaseHTTPRequestHandler):
    seen: ClassVar[list] = []
    replies: ClassVar[list] = []          # (status, payload) consumed one per request; the last one repeats

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).seen.append((self.path, dict(self.headers), body))
        r = type(self).replies
        status, payload = r.pop(0) if len(r) > 1 else r[0]
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def api():
    _Api.seen, _Api.replies = [], [(200, {"text": "oi pessoal"})]
    srv = HTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1/transcribe", _Api
    srv.shutdown()
    srv.server_close()


def fields_of(body: bytes) -> list[str]:
    return [m.decode() for m in re.findall(rb'name="([a-z_]+)"', body)]


# ---------------------------------------------------------------- config
@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("STT_PROVIDER", "STT_URL", "STT_LANGUAGE", "STT_PROMPT", "GROQ_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("provider,env", [("groq", "GROQ_API_KEY"), ("grok", "XAI_API_KEY")])
def test_hosted_provider_is_enabled_only_with_its_key(monkeypatch, provider, env):
    monkeypatch.setenv("STT_PROVIDER", provider)
    assert not W.enabled() and W.config().provider == "off", "no key, no provider"
    monkeypatch.setenv(env, "   ")
    assert not W.enabled()
    monkeypatch.setenv(env, KEY)
    assert W.enabled() and W.config().provider == provider and W.config().api_key == KEY


def test_each_provider_reads_only_its_own_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.setenv("STT_PROVIDER", "grok")
    assert not W.enabled()


def test_the_key_never_shows_in_repr_or_str_of_the_config(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    c = W.config()
    assert KEY not in repr(c) and KEY not in str(c)


def test_whispercpp_is_unchanged(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "whispercpp")
    assert not W.enabled()
    monkeypatch.setenv("STT_URL", "http://stt:8080")
    assert W.enabled() and W.config().provider == "whispercpp" and W.config().api_key == ""


def test_default_transcriber_is_built_from_the_config(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "grok")
    monkeypatch.setenv("XAI_API_KEY", KEY)
    t = W._default_transcriber(W.config())
    assert isinstance(t, W.ApiTranscriber) and t.provider == "grok" and t.url == "https://api.x.ai/v1/stt"
    monkeypatch.setenv("STT_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    t = W._default_transcriber(W.config())
    assert t.provider == "groq" and t.url == "https://api.groq.com/openai/v1/audio/transcriptions"
    monkeypatch.setenv("STT_API_URL", "http://127.0.0.1:9/x")
    assert W._default_transcriber(W.config()).url == "http://127.0.0.1:9/x", "STT_API_URL overrides the vendor URL (tests, proxies)"


@pytest.mark.parametrize("provider,env,vendor", [("groq", "GROQ_API_KEY", "https://api.groq.com/"), ("grok", "XAI_API_KEY", "https://api.x.ai/")])
def test_a_stale_whisper_url_never_redirects_a_hosted_provider(monkeypatch, provider, env, vendor):
    """Fly MERGES a container's env: STT_URL (whisper) stayed on the live Machine after it was removed from the file.
    With it, the Groq key would have been POSTed to http://livebingo-stt.internal."""
    monkeypatch.setenv("STT_PROVIDER", provider)
    monkeypatch.setenv(env, KEY)
    monkeypatch.setenv("STT_URL", "http://livebingo-stt.internal:8080")
    t = W._default_transcriber(W.config())
    assert t.url.startswith(vendor) and "livebingo-stt" not in t.url


# ---------------------------------------------------------------- requests
def test_groq_request_shape(api):
    url, h = api
    h.replies = [(200, {"segments": [{"start": 0.0, "end": 2.0, "text": " olá "}]})]
    wav = make_wav(tone(0.5))
    segs = W.ApiTranscriber("groq", KEY, url=url).transcribe(wav, prompt="ateísmo")
    assert segs == [Segment(0, 2000, "olá")]
    _, headers, body = h.seen[0]
    assert headers["Authorization"] == f"Bearer {KEY}" and headers["Content-Type"].startswith("multipart/form-data; boundary=")
    names = fields_of(body)
    assert names[-1] == "file" and {"model", "language", "temperature", "response_format", "prompt"} <= set(names)
    assert b"whisper-large-v3-turbo" in body and b"verbose_json" in body and "ateísmo".encode() in body and wav in body


def test_grok_request_shape_file_last_and_text_only_reply(api):
    url, h = api
    segs = W.ApiTranscriber("grok", KEY, url=url).transcribe(make_wav(tone(0.5)), prompt="ignored by xAI")
    assert segs == [Segment(0, 0, "oi pessoal")]
    _, headers, body = h.seen[0]
    names = fields_of(body)
    assert names[-1] == "file" and {"model", "language", "format"} <= set(names) and "prompt" not in names
    assert b"grok-voice-transcribe-2.0" in body and headers["Authorization"] == f"Bearer {KEY}"


@pytest.mark.parametrize("provider", ["groq", "grok"])
def test_auto_language_sends_no_language_field(api, provider):
    url, h = api
    W.ApiTranscriber(provider, KEY, url=url, language="auto").transcribe(make_wav(tone(0.5)))
    names = fields_of(h.seen[0][2])
    assert "language" not in names and "format" not in names, "xAI refuses format=true without a language"


def test_no_prompt_field_when_the_prompt_is_empty(api):
    url, h = api
    W.ApiTranscriber("groq", KEY, url=url).transcribe(make_wav(tone(0.5)), prompt="")
    assert "prompt" not in fields_of(h.seen[0][2])


# ---------------------------------------------------------------- errors
@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_http_errors_raise_transcribe_error_with_the_status_and_without_the_key(api, status):
    url, h = api
    h.replies = [(status, {"error": {"message": f"nope {KEY}"}})]
    with pytest.raises(W.TranscribeError) as e:
        W.ApiTranscriber("groq", KEY, url=url).transcribe(make_wav(tone(0.2)))
    assert str(status) in str(e.value) and KEY not in str(e.value)
    assert len(h.seen) == 1, "errors are not retried here: the worker backs off"


def test_a_dead_server_raises_transcribe_error():
    with pytest.raises(W.TranscribeError):
        W.ApiTranscriber("grok", KEY, url="http://127.0.0.1:1/x", timeout=1).transcribe(make_wav(tone(0.2)))


def test_an_unreadable_reply_is_not_a_crash(api):
    url, h = api
    h.replies = [(200, ["not", "a", "dict"])]
    assert W.ApiTranscriber("groq", KEY, url=url).transcribe(make_wav(tone(0.2))) == []


# ---------------------------------------------------------------- empty replies (Grok dropped a whole 12 s chunk in the spike)
def test_an_empty_reply_is_retried_once_and_the_second_answer_wins(api):
    url, h = api
    h.replies = [(200, {"text": ""}), (200, {"text": "quem sabe onde o Léo fala"})]
    segs = W.ApiTranscriber("grok", KEY, url=url).transcribe(make_wav(tone(0.5)))
    assert segs == [Segment(0, 0, "quem sabe onde o Léo fala")] and len(h.seen) == 2


def test_two_empty_replies_give_an_empty_result_and_stop(api):
    url, h = api
    h.replies = [(200, {"text": "  "})]
    assert W.ApiTranscriber("grok", KEY, url=url).transcribe(make_wav(tone(0.5))) == [] and len(h.seen) == 2


def test_a_good_first_reply_is_not_repeated(api):
    url, h = api
    W.ApiTranscriber("groq", KEY, url=url).transcribe(make_wav(tone(0.5)))
    assert len(h.seen) == 1


@pytest.mark.parametrize("provider", ["groq", "grok"])
def test_requests_carry_their_own_user_agent(api, provider):
    """Cloudflare (in front of Groq) answers 403 "error code: 1010" to urllib's default `Python-urllib/3.x` User-Agent."""
    url, h = api
    W.ApiTranscriber(provider, KEY, url=url).transcribe(make_wav(tone(0.5)))
    ua = h.seen[0][1]["User-Agent"]
    assert ua.startswith("livegames-stt/") and "urllib" not in ua
