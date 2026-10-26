"""The checker is built ONCE by the worker and every check runs in a NEW event loop (asyncio.run per check, one thread per worker).
Found in the FC7 smoke test against the real OpenRouter API: the second check of the same checker failed with "Event loop is closed"
(the HTTP client of the model stayed bound to the first loop). These tests use a local OpenAI-compatible server with keep-alive
connections, the setting that exposes the reuse of a connection from a closed loop. No network, no keys."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import factcheck as FC

ANSWER = {"checkable": False, "verdict": "unverifiable", "score": 0, "confidence": "high", "explanation_pt": "Nada a checar.", "sources": []}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep-alive: the client pools the connection

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)   # type: ignore[attr-defined]
        content = "Notas: opinião, nada a pesquisar." if body.get("tools") else json.dumps(ANSWER)
        out = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                          "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def fake_api():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.requests = []   # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _env(srv, provider):
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    if provider == "groq":
        return {"CHECKER_MODEL": "groq:openai/gpt-oss-120b", "GROQ_API_KEY": "gsk_test", "GROQ_BASE_URL": url}
    return {"CHECKER_MODEL": "openrouter:openai/gpt-oss-120b", "OPENROUTER_API_KEY": "sk-or-test", "OPENROUTER_BASE_URL": url}


@pytest.mark.parametrize("provider", ["groq", "openrouter"])
def test_the_same_checker_runs_check_after_check_each_in_a_new_event_loop(fake_api, provider):
    c = FC.from_env(_env(fake_api, provider))
    assert c is not None
    for claim in ("primeira", "segunda", "terceira"):
        r = asyncio.run(c.check(claim))                   # exactly what checkworker does for every check
        assert r.verdict == "unverifiable", claim
    assert len(fake_api.requests) == 6                    # 2 steps x 3 checks, all reached the server


@pytest.mark.parametrize("provider", ["groq", "openrouter"])
def test_two_worker_threads_share_one_checker(fake_api, provider):
    c = FC.from_env(_env(fake_api, provider))
    assert c is not None
    errors = []

    def worker():
        for i in range(3):
            try:
                asyncio.run(c.check(f"frase {threading.get_ident()} {i}"))
            except Exception as e:                       # noqa: BLE001 - collected and asserted below
                errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    assert len(fake_api.requests) == 12
