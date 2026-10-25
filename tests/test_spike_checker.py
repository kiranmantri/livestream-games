"""FC0 spike (spikes/checker.py): the agent's rules, without Ollama and without the network (pydantic-ai FunctionModel)."""
from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from spikes import checker as K

@pytest.fixture(autouse=True)
def no_real_dotenv(monkeypatch, tmp_path_factory):
    """The spike falls back to <repo>/.env for API keys; a test must never see the developer's real one."""
    monkeypatch.setattr("app.config.ROOT", tmp_path_factory.mktemp("emptyroot"))


HIT = [{"title": "Fonte A", "href": "https://example.org/a", "body": "trecho"}, {"title": "B", "href": "https://example.org/b", "body": "x"}]


async def fake_search(query):
    return HIT


async def fake_fetch(url):
    return f"texto de {url}"


def deps(**over):
    return K.Deps(search=over.pop("search", fake_search), fetch=over.pop("fetch", fake_fetch), **over)


def final(**over):
    args = {"checkable": True, "verdict": "false", "score": -4, "confidence": "high", "explanation_pt": "Falso.",
            "sources": [{"title": "Fonte A", "url": "https://example.org/a"}]} | over
    return lambda info: ToolCallPart(info.output_tools[0].name, args)


def call(name, **args):
    return lambda info: ToolCallPart(name, args)


def run(*steps, d=None):
    """Runs the agent with a scripted model; returns (result, deps, number of model calls)."""
    it, calls = iter(steps), []

    def fn(messages, info: AgentInfo):
        calls.append(1)
        return ModelResponse(parts=[next(it)(info)])

    d = d or deps()
    agent = K.build_agent(FunctionModel(fn))
    res = agent.run_sync("Frase a verificar: X", deps=d)
    return res, d, len(calls)


# ---------------------------------------------------------------- happy path and validators
def test_search_then_answer_with_a_source_from_the_search():
    res, d, n = run(call("web_search", query="terra idade"), final())
    assert res.output.verdict == "false" and res.output.score == -4 and n == 2
    assert d.searches == 1 and set(d.seen) == {"https://example.org/a", "https://example.org/b"}


def test_an_invented_source_url_is_sent_back_to_the_model():
    bad = final(sources=[{"title": "Inventada", "url": "https://inventado.example/x"}])
    res, _, n = run(call("web_search", query="q"), bad, final())
    assert n == 3 and res.output.sources[0].url == "https://example.org/a"


def test_the_sign_must_agree_with_the_verdict():
    res, _, n = run(call("web_search", query="q"), final(verdict="false", score=3), final(verdict="false", score=-3))
    assert n == 3 and res.output.score == -3


@pytest.mark.parametrize("verdict,score,ok", [("true", 1, True), ("true", 0, False), ("false", -1, True), ("false", 0, False),
                                              ("partial", 2, True), ("partial", -2, True), ("partial", 3, False)])
def test_score_ranges_per_verdict(verdict, score, ok):
    good = final(verdict="partial", score=0)
    res, _, n = run(call("web_search", query="q"), final(verdict=verdict, score=score), good)
    assert n == (2 if ok else 3) and (res.output.score == score if ok else res.output.score == 0)


def test_a_checkable_answer_needs_at_least_one_source():
    res, _, n = run(call("web_search", query="q"), final(sources=[]), final())
    assert n == 3 and len(res.output.sources) == 1


def test_unverifiable_is_forced_to_zero_without_needing_sources():
    res, _, n = run(final(verdict="unverifiable", score=4, checkable=True, sources=[]))
    assert n == 1 and res.output.score == 0 and res.output.checkable is False and res.output.verdict == "unverifiable"


def test_not_checkable_wins_over_a_verdict():
    res, _, _ = run(final(checkable=False, verdict="true", score=5, sources=[]))
    assert res.output.verdict == "unverifiable" and res.output.score == 0


def test_score_outside_minus5_to_5_is_refused_by_the_schema():
    res, _, n = run(call("web_search", query="q"), final(score=9), final())
    assert n == 3 and res.output.score == -4


# ---------------------------------------------------------------- tools
def test_search_limit_answers_with_a_stop_message_instead_of_searching_more():
    steps = [call("web_search", query=f"q{i}") for i in range(K.MAX_SEARCHES + 1)] + [final()]
    _, d, _ = run(*steps)
    assert d.searches == K.MAX_SEARCHES


def test_fetch_only_pages_the_search_returned_and_at_most_three():
    steps = [call("web_search", query="q"), call("fetch_page", url="https://nao-veio.example/"),
             call("fetch_page", url="https://example.org/a"), call("fetch_page", url="https://example.org/b"),
             call("fetch_page", url="https://example.org/a"), call("fetch_page", url="https://example.org/b"), final()]
    _, d, _ = run(*steps)
    assert d.fetches == 3 and "https://nao-veio.example/" not in d.fetched and len(d.fetched) == 3


def test_a_failing_fetch_is_data_for_the_model_not_a_crash():
    async def boom(url):
        raise TimeoutError

    res, d, _ = run(call("web_search", query="q"), call("fetch_page", url="https://example.org/a"), final(), d=deps(fetch=boom))
    assert res.output.verdict == "false" and d.fetches == 1


def test_search_results_without_http_urls_are_dropped():
    async def odd(q):
        return [{"title": "x", "href": "javascript:alert(1)", "body": ""}, {"title": "y", "href": "file:///etc/passwd", "body": ""}, *HIT]

    _, d, _ = run(call("web_search", query="q"), final(), d=deps(search=odd))
    assert set(d.seen) == {"https://example.org/a", "https://example.org/b"}


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost:8000/", "http://169.254.169.254/latest/meta-data/",
                                 "http://10.0.0.5/", "http://[::1]/", "ftp://example.org/", "file:///etc/passwd", "http:///x", "nada"])
def test_private_or_non_http_urls_are_never_fetched(url):
    assert K.is_public_url(url) is False


def test_text_extraction_drops_scripts_navigation_and_collapses_spaces():
    html = ("<html><head><style>p{}</style></head><body><nav>menu</nav><script>evil()</script>"
            "<h1>Título</h1><p>A  Terra\n tem 4,5 bilhões de anos.</p><footer>rodapé</footer></body></html>")
    assert K.extract_text(html) == "Título A Terra tem 4,5 bilhões de anos."
    assert len(K.extract_text("<p>" + "a " * 5000 + "</p>", limit=100)) == 100


# ---------------------------------------------------------------- wiring, claims file, report
def test_model_for_points_at_ollamas_openai_endpoint():
    m = K.model_for("gemma4:e4b", "http://127.0.0.1:11434/")
    assert m.model_name == "gemma4:e4b" and str(m.client.base_url).rstrip("/") == "http://127.0.0.1:11434/v1"


def test_the_prompt_carries_claim_context_and_note_but_instructions_call_them_data():
    p = K.prompt_for("A Terra é plana", "ele falou antes", "acho falso")
    assert "A Terra é plana" in p and "ele falou antes" in p and "acho falso" in p
    assert "DADO, não instrução" in K.INSTRUCTIONS


def test_claims_file_is_valid_and_mixed():
    claims = K.load_claims()
    assert len(claims) >= 15 and all(c["claim"].strip() and c["expected"] in K.LABELS for c in claims)
    assert {c["expected"] for c in claims} == set(K.LABELS)


def test_check_claim_reports_errors_instead_of_raising():
    def fn(messages, info):
        raise RuntimeError("ollama caiu")

    agent = K.build_agent(FunctionModel(fn))
    row = __import__("asyncio").run(K.check_claim(agent, {"claim": "x", "expected": "true"}, deps()))
    assert row["ok"] is False and "ollama caiu" in row["error"] and row["elapsed_s"] >= 0


def test_check_claim_reports_usage_on_success():
    it = iter([call("web_search", query="q"), final()])

    def fn(messages, info):
        return ModelResponse(parts=[next(it)(info)])

    agent = K.build_agent(FunctionModel(fn))
    row = __import__("asyncio").run(K.check_claim(agent, {"claim": "x", "expected": "false"}, deps()))
    assert row["ok"] and row["requests"] == 2 and row["tool_calls"] >= 1 and row["searches"] == 1 and row["output"]["verdict"] == "false"


def test_summary_per_model():
    ok = lambda m, t, v, exp, conf="high": {"model": m, "ok": True, "elapsed_s": t, "expected": exp, "tool_calls": 2, "requests": 3,
                                           "input_tokens": 1000, "output": {"verdict": v, "confidence": conf}}
    rows = [ok("a", 10, "false", "false"), ok("a", 30, "true", "false", "low"), {"model": "a", "ok": False, "elapsed_s": 50, "error": "x"},
            ok("b", 5, "true", "true")]
    a, b = K.summarize(rows)
    assert a["claims"] == 3 and a["ok"] == 2 and a["errors"] == 1 and a["verdict_match"] == "1/2" and a["low_conf"] == 1
    assert a["median_s"] == 30 and b["verdict_match"] == "1/1" and b["avg_input_tokens"] == 1000


def test_text_part_alone_is_not_a_valid_answer():
    """A model that just chats (no structured output) must not be accepted as a check."""
    it = iter([call("web_search", query="q"), lambda info: TextPart("A Terra é plana, claro."), final()])

    def fn(messages, info):
        return ModelResponse(parts=[next(it)(info)])

    agent = K.build_agent(FunctionModel(fn))
    assert agent.run_sync("x", deps=deps()).output.verdict == "false"


# ---------------------------------------------------------------- refusals (opinion / prediction / vague) and categories
def test_summary_counts_correct_and_wrong_refusals():
    def row(exp, v):
        return {"model": "a", "ok": True, "elapsed_s": 1, "expected": exp, "tool_calls": 1, "requests": 2, "input_tokens": 10,
                "output": {"verdict": v, "confidence": "high"}}

    rows = [row("unverifiable", "unverifiable"), row("unverifiable", "true"), row("unverifiable", "unverifiable"),
            row("false", "unverifiable"), row("false", "false")]
    (s,) = K.summarize(rows)
    assert s["refused_ok"] == "2/3" and s["refused_wrong"] == "1/2"


def test_summary_by_category_for_the_model():
    def row(cat, exp, v):
        return {"model": "a", "ok": True, "elapsed_s": 1, "expected": exp, "category": cat, "tool_calls": 1, "requests": 2,
                "input_tokens": 10, "output": {"verdict": v, "confidence": "high"}}

    rows = [row("noisy", "false", "false"), row("noisy", "false", "true"), row("news", "true", "true")]
    (s,) = K.summarize(rows)
    assert s["by_category"] == {"noisy": "1/2", "news": "1/1"}


def test_claims_have_categories_and_cover_the_hard_cases():
    claims = K.load_claims()
    cats = {c.get("category") for c in claims}
    assert None not in cats
    assert {"pseudo-history", "opinion", "prediction", "vague", "noisy-transcript", "recent-news", "half-truth", "person"} <= cats
    assert any("Annunakis" in c["claim"] and c["claim"].lower().startswith("os annunakis existem") for c in claims)
    assert {c["expected"] for c in claims if c["category"] in ("opinion", "prediction", "vague")} == {"unverifiable"}


def test_the_first_five_claims_are_a_varied_smoke_set():
    first = K.load_claims()[:5]
    assert len({c["category"] for c in first}) == 5


# ---------------------------------------------------------------- Groq contender (hosted gpt-oss) and cost
def test_a_groq_spec_points_at_groqs_openai_endpoint(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key_123")
    m = K.model_for("groq:openai/gpt-oss-120b", "http://127.0.0.1:11434")
    assert m.model_name == "openai/gpt-oss-120b"
    assert str(m.client.base_url).rstrip("/") == "https://api.groq.com/openai/v1"


def test_a_groq_spec_without_a_key_fails_clearly_and_never_shows_a_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(SystemExit) as e:
        K.model_for("groq:openai/gpt-oss-120b", "http://127.0.0.1:11434")
    assert "GROQ_API_KEY" in str(e.value)


def test_an_ollama_tag_is_unchanged_by_the_groq_support(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    m = K.model_for("qwen3:14b", "http://127.0.0.1:11434")
    assert m.model_name == "qwen3:14b"


def test_cost_uses_the_groq_price_per_million_tokens_and_local_is_free():
    assert K.cost_usd("groq:openai/gpt-oss-120b", 1_000_000, 1_000_000) == pytest.approx(0.75)
    assert K.cost_usd("groq:openai/gpt-oss-20b", 1_000_000, 1_000_000) == pytest.approx(0.375)
    assert K.cost_usd("gemma4:e4b", 50_000, 5_000) == 0
    assert K.cost_usd("groq:unknown-model", 10, 10) is None          # never invent a price


def test_summary_adds_the_cost_per_claim_when_rows_carry_it():
    def row(c):
        return {"model": "g", "ok": True, "elapsed_s": 1, "expected": "true", "tool_calls": 1, "requests": 2, "input_tokens": 10,
                "cost_usd": c, "output": {"verdict": "true", "confidence": "high"}}

    (s,) = K.summarize([row(0.004), row(0.006)])
    assert s["cost_usd_total"] == pytest.approx(0.01) and s["cost_usd_per_claim"] == pytest.approx(0.005)


# ---------------------------------------------------------------- Groq 400 "tool_use_failed": the answer must not be a forced tool call
import contextlib
import json as _json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _FakeGroq:
    """Captures the request bodies; always answers with a valid 'unverifiable' JSON in message.content."""

    def __init__(self):
        self.bodies: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = _json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.bodies.append(body)
                if body.get("tools") and "response_format" in body:       # the real Groq rule, seen on the real API
                    return self._send(400, {"error": {"message": "json mode cannot be combined with tool/function calling", "type": "invalid_request_error"}})
                if body.get("tools"):                                      # research step: plain text, no tool call
                    return self._send(200, self._chat("Notas: nada encontrado."))
                answer = {"checkable": False, "verdict": "unverifiable", "score": 0, "confidence": "low", "explanation_pt": "Opinião.", "sources": []}
                self._send(200, self._chat(_json.dumps(answer)))

            def _chat(self, content):
                return {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

            def _send(self, code, obj):
                data = _json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/openai/v1"


@pytest.fixture
def fake_groq(monkeypatch):
    f = _FakeGroq()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key_123")
    monkeypatch.setenv("GROQ_BASE_URL", f.url)
    yield f
    f.srv.shutdown()


def _run_groq(mode=None):
    agent = K.build_agent(K.model_for("groq:openai/gpt-oss-120b", "x"), **({"output_mode": mode} if mode else {}))
    return agent.run_sync("Frase a verificar: Flamengo é o melhor", deps=deps()).output


def test_default_output_mode_is_two_steps_for_groq_and_a_tool_for_ollama():
    assert K.default_output_mode("groq:openai/gpt-oss-120b") == "twostep"        # Groq: tools + any final-answer format => 400 / a made-up 'json' tool
    assert K.default_output_mode("gemma4:e4b") == "tool"


def test_the_tool_mode_still_forces_a_final_result_tool(fake_groq):
    with contextlib.suppress(Exception):       # the fake answers with text, so pydantic-ai retries/fails: only the first request matters
        _run_groq("tool")
    body = fake_groq.bodies[0]
    assert any(t["function"]["name"] == "final_result" for t in body["tools"])


def test_groq_default_researches_with_tools_then_answers_with_a_json_schema_and_no_tools(fake_groq):
    """The fake answers 400 to tools + response_format, like the real Groq. Step 1 has tools and no response_format; step 2 the opposite."""
    agent = K.build_agent(K.model_for("groq:openai/gpt-oss-120b", "x"), K.default_output_mode("groq:openai/gpt-oss-120b"))
    res = agent.run_sync("Frase a verificar: x", deps=deps())
    assert res.output.verdict == "unverifiable"
    one, two = fake_groq.bodies
    assert one["tools"] and "response_format" not in one and one.get("tool_choice") != "required"
    assert "tools" not in two and two["response_format"]["type"] == "json_schema"
    assert "Notas: nada encontrado." in _json.dumps(two["messages"])               # the notes travel to step 2


def _two_step_model(answer_args, calls):
    """Step 1 (function tools present): search once, then write notes. Step 2 (no tools): the structured answer."""
    state = {"searched": False}

    def fn(messages, info):
        calls.append({"tools": [t.name for t in info.function_tools], "outputs": [t.name for t in info.output_tools], "text": str(messages)})
        if info.function_tools:
            if not state["searched"]:
                state["searched"] = True
                return ModelResponse(parts=[ToolCallPart("web_search", {"query": "q"})])
            return ModelResponse(parts=[TextPart("Notas: A é falso segundo https://example.org/a")])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer_args)])

    return FunctionModel(fn)


ANSWER = {"checkable": True, "verdict": "false", "score": -4, "confidence": "high", "explanation_pt": "Falso.",
          "sources": [{"title": "Fonte A", "url": "https://example.org/a"}]}


def test_two_step_agent_gives_step_two_the_notes_and_only_the_urls_the_search_returned():
    calls: list[dict] = []
    agent = K.TwoStepAgent(_two_step_model(ANSWER, calls), final_mode="tool")
    d = deps()
    res = agent.run_sync("Frase a verificar: A", deps=d)
    assert res.output.verdict == "false" and res.output.sources[0].url == "https://example.org/a"
    step2 = calls[-1]
    assert step2["tools"] == [] and "Notas: A é falso" in step2["text"] and "https://example.org/a" in step2["text"]
    assert d.searches == 1


def test_two_step_agent_still_rejects_an_invented_source_and_sums_the_usage():
    calls: list[dict] = []
    bad = ANSWER | {"sources": [{"title": "X", "url": "https://invented.example/x"}]}
    with pytest.raises(Exception) as e:
        K.TwoStepAgent(_two_step_model(bad, calls), final_mode="tool").run_sync("Frase: A", deps=deps())
    assert "UnexpectedModelBehavior" in type(e.value).__name__ or "retries" in str(e.value).lower()
    ok = K.TwoStepAgent(_two_step_model(ANSWER, []), final_mode="tool").run_sync("Frase: A", deps=deps())
    assert ok.usage.requests >= 3                                                  # search call + notes + answer, from both steps


# ---------------------------------------------------------------- OpenRouter contender + .env
OR_MODELS = {"data": [{"id": "openai/gpt-oss-120b", "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}},
                      {"id": "meta/some-model:free", "pricing": {"prompt": "0", "completion": "0"}}]}


@pytest.fixture
def or_prices(monkeypatch):
    monkeypatch.setattr(K, "_or_prices", None)                                  # reset the cache
    monkeypatch.setattr(K, "fetch_openrouter_models", lambda: OR_MODELS)


def test_an_openrouter_spec_points_at_openrouters_openai_endpoint(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-123")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    m = K.model_for("openrouter:openai/gpt-oss-120b", "http://127.0.0.1:11434")
    assert m.model_name == "openai/gpt-oss-120b"
    assert str(m.client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"


def test_an_openrouter_spec_without_a_key_fails_clearly(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("app.config.ROOT", __import__("pathlib").Path("/nonexistent"))   # no .env to fall back on
    with pytest.raises(SystemExit) as e:
        K.model_for("openrouter:openai/gpt-oss-120b", "x")
    assert "OPENROUTER_API_KEY" in str(e.value)


def test_keys_are_read_from_the_dotenv_file_when_the_shell_has_none(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("GROQ_API_KEY=gsk_from_file_123\nOPENROUTER_API_KEY=sk-or-from-file\n")
    for k in ("GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("app.config.ROOT", tmp_path)
    assert K.model_for("groq:openai/gpt-oss-120b", "x").client.api_key == "gsk_from_file_123"
    assert K.model_for("openrouter:openai/gpt-oss-120b", "x").client.api_key == "sk-or-from-file"


def test_hosted_models_default_to_two_steps():
    assert K.default_output_mode("openrouter:openai/gpt-oss-120b") == "twostep"


def test_openrouter_cost_uses_the_price_list_of_the_models_api(or_prices):
    assert K.cost_usd("openrouter:openai/gpt-oss-120b", 1_000_000, 1_000_000) == pytest.approx(0.75)
    assert K.cost_usd("openrouter:meta/some-model:free", 5000, 500) == 0
    assert K.cost_usd("openrouter:nobody/unknown", 10, 10) is None


def test_openrouter_cost_is_none_when_the_price_list_is_unreachable(monkeypatch):
    def boom():
        raise OSError("offline")

    monkeypatch.setattr(K, "_or_prices", None)
    monkeypatch.setattr(K, "fetch_openrouter_models", boom)
    assert K.cost_usd("openrouter:openai/gpt-oss-120b", 10, 10) is None          # never invent a price


def test_the_price_list_is_fetched_once(monkeypatch):
    calls = []
    monkeypatch.setattr(K, "_or_prices", None)
    monkeypatch.setattr(K, "fetch_openrouter_models", lambda: calls.append(1) or OR_MODELS)
    K.cost_usd("openrouter:openai/gpt-oss-120b", 1, 1)
    K.cost_usd("openrouter:openai/gpt-oss-120b", 1, 1)
    assert len(calls) == 1


def test_the_openrouter_default_researches_with_tools_then_answers_without(monkeypatch):
    f = _FakeGroq()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-123")
    monkeypatch.setenv("OPENROUTER_BASE_URL", f.url)
    try:
        agent = K.build_agent(K.model_for("openrouter:openai/gpt-oss-120b", "x"), K.default_output_mode("openrouter:openai/gpt-oss-120b"))
        assert agent.run_sync("Frase a verificar: x", deps=deps()).output.verdict == "unverifiable"
        one, two = f.bodies
        assert one["tools"] and "response_format" not in one and "tools" not in two and two["response_format"]["type"] == "json_schema"
    finally:
        f.srv.shutdown()
