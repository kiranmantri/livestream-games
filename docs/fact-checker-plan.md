# "Quem tá metendo o loco?" — fact-checking game (plan v2, not built yet)

Status (2026-10-26): BUILT as "Aponte o mentiroso!" (FC0-FC7, docs/validation/checker.md); this file is the historical plan.
Original status: PLAN for review. Nothing below is implemented. v2 (2026-10-24) replaces the OpenAI + Jev pipeline with ONE local model (Gemma 4) driven by a
pydantic-ai Agent. OpenAI and Jev are ignored for now (v1 was: OpenAI search brief -> Jev score; Jev is TypeSafe's judge model that cannot browse; it could come back later
as a second opinion).

## 1. What it is
Third game of the site, next to Bingo and "Encontre as Falácias", on the SAME site/URL (third tab, one name for all games).
Same stage as Falácias: live transcript + live chat (+ video), players select text, an optional message, a slide panel.
The judge is an AI agent that fact-checks each claim and gives a whole-number score from -5 (5 poops 💩, horrible lie) to +5 (5 stars ⭐, great truthful sentence).

Decisions (user):
- Players score by GUESSING: when sending a claim they pick a rating (-5..+5, shown as 💩/⭐); the closer to the final score, the more points.
- Everybody sees the AI result (score, explanation, 3 sources) as soon as it is ready; the admin can override and the override replaces it.
- Same mechanics and limits as Falácias (select in transcript/chat, optional message, typed claims, 1 per 10 s, max 20 unreviewed; admin starts from any link).
- No model available (Ollama down, model not pulled, search failing) -> claims wait for the admin, who assigns the scores on an admin page.
- v2: ONE local model (Gemma 4, via Ollama) reads the search results, summarises in pt-BR with 3 sources, classifies and scores.
  Agent framework: pydantic-ai, with its `WebSearch` capability.

## 2. What the research says (web, 2026-10-24; verify by running it — see FC0)
pydantic-ai `WebSearch` capability:
- It defaults to NATIVE search, i.e. the model PROVIDER runs the search (OpenAI, Anthropic, Google...). A local Ollama/Gemma model has NO native search, so
  `WebSearch()` alone raises on it. For local models the capability needs `WebSearch(local='duckduckgo')` (extra `pydantic-ai-slim[duckduckgo]`):
  the agent gets a DuckDuckGo search TOOL and the model decides when to call it. So "built-in web search" works, but as the DuckDuckGo fallback, not a provider built-in.
- That fallback does not enforce allowed/blocked domains or max_uses: cap tool calls ourselves (usage limits).
- DuckDuckGo returns titles + snippets + URLs, not page text. Evidence from snippets is thin. A page-fetch tool (top 3 URLs, trimmed text) helps a lot but
  adds tokens (= time on a local model). Decision needed (section 9).
- DuckDuckGo may rate-limit or block datacenter IPs (Fly): same risk class as YouTube vs Fly. Fallbacks: Tavily/Exa (key, paid) or a self-hosted SearXNG.
Gemma 4 (Apache 2.0, in the Ollama library, native tool calling, 140+ languages incl. Portuguese):
- Tags and sizes (Q4): `gemma4:e2b` ~7 GB, `gemma4:e4b` (default) ~9.6 GB, `gemma4:12b` ~7.6 GB, `gemma4:26b` (MoE, ~4B active) ~18 GB, `gemma4:31b` ~20 GB.
  Context 128K (small) / 256K (large). Ollama's DEFAULT context is 4096 tokens: `num_ctx` must be raised (32K is plenty) or tool calls and long evidence break.
- Third-party numbers (not measured by us): tool-calling accuracy ~85% (26B), ~90% (31B). Small models degrade under memory pressure; tool-call formatting breaks first.
- Your Mac: Apple M5 Pro, 48 GB RAM, Ollama installed (server not running, no models pulled) -> 26B and 31B fit locally.

## 3. Pipeline per claim (one pydantic-ai Agent)
1. Input: claim (quote + optional note + 3 lines of context before/after, as the admin queue has).
2. Agent with `WebSearch(local='duckduckgo')` (+ optional page fetch), instructions in pt-BR, tool-call limit ~6, request timeout.
3. Structured output (pydantic model, validated; output retries 2):
   `checkable: bool`, `verdict: true|partial|false|unverifiable`, `score: int -5..5`, `confidence: low|medium|high`,
   `explanation_pt: str (<= 400 chars)`, `sources: list[{title, url}] (1..3)`.
4. Output validators (these matter more than the prompt): every source URL MUST be one the search tool actually returned (no invented links; http/https only);
   unverifiable => score forced to 0 and no points; the sign of the score must agree with the verdict (true => >= 1, false => <= -1, partial => -2..2).
5. Stored with model name, tokens and elapsed time. `confidence = low` or `unverifiable` goes to the admin review queue (players see "aguardando o apresentador").
6. Any failure (Ollama down, timeout, validation retries exhausted, search blocked) -> `needs_admin`. One retry, then `needs_admin`.
Final score = admin score if set, else AI score. Points are DERIVED (nothing about points is stored): an override re-scores everybody.

## 4. Scoring proposal (to confirm)
points = max(0, 5 - |guess - final_score|) (exact = 5, 5 or more off = 0). No base points: the guess is the skill.
Pros: simple, easy to explain. Cons: guessing 0 on everything yields ~2-3 points per claim (hedging). Mitigation: +2 for the right SIGN (lie vs truth) and 0 points for a
guess of 0 when |final| >= 3. Unverifiable claims: no points for anyone. Winner = most points; tie = more exact guesses, then earliest last claim.

## 5. Data model (migration 0013, sketch)
- `game_sessions.game_type` CHECK gains 'checker'; cookie `lg_token_checker`; the partial unique index already gives one active session per game.
- `fact_claims`: like `fallacy_marks` (kind selected|spoken, offsets, quote, note) + `guess smallint CHECK (-5..5)`.
- `fact_checks`: claim_id UNIQUE, status (queued|running|done|needs_review|needs_admin|failed), verdict, ai_score, confidence, explanation, sources jsonb,
  admin_score smallint NULL, model, tokens, elapsed_ms, timestamps. Identical claim text in the same session shares ONE check (dedup = time saved).
- Reuse as they are: stream_events/feed (transcript, chat, STT), host messages, start-from-any-link, the slide panel.

## 6. WHERE the model runs — the big open problem (be critical)
The app is on Fly (shared CPU, 1 GB). Gemma needs real compute:
- Fly CPU Machine (no GPU): e2b/e4b run at a few tokens/s on 8 vCPUs. A check = search results in the prompt (~1.5-4K tokens, minutes of prompt processing on CPU)
  + ~300 output tokens. Estimate 1-3 minutes per claim with small-model quality, and a 16 GB Machine costs real money while on. UNMEASURED; the queue would back up
  fast with 20 claims per player. Fly GPUs: I believe Fly is retiring them; not verified -> check before counting on it.
- Your Mac (M5 Pro, 48 GB) running gemma4:26b: fast and free, but the cloud app cannot reach it. It needs a tunnel (Tailscale / Cloudflare Tunnel) and your Mac on, awake
  and online during every live. The worker would call http://<tunnel>:11434. One laptop becomes a production dependency; sleep/travel/wifi = checker down (the admin fallback still works).
- Run the WHOLE site from the Mac during a live: rejected, loses the Fly setup.
- A rented GPU box (RunPod/Vast/Hetzner GPU) with Ollama, started per live: works, costs per hour, one more thing to operate.
Recommendation: FC0 spike FIRST (before any schema): run Gemma 4 e4b, 12b and 26b on the Mac with 15-20 real claims from the corpus (pt-BR) and time them; then e4b on a
Fly performance-CPU Machine for an hour. Decide the hosting from numbers. The code only needs OLLAMA_BASE_URL + OLLAMA_MODEL, so it does not care where the model runs.

## 7. Other risks
- Quality: a small local model judging its own search results will be wrong often and confidently. Mitigations: the validators above, an "verificação automática, pode conter erros" label,
  the admin override, and `confidence`. Self-reported LLM confidence is weak; optional upgrade: run the check twice and compare (2x time), or add Jev later as a second opinion.
- Not every claim is checkable (opinions, jokes, religion: "Deus existe"): the `unverifiable` outcome exists for that and must not earn points.
- Prompt injection: claims, chat and web pages are untrusted. Output is schema-validated, text is escaped, links rel=noopener, the agent has no tool besides search/fetch and no secrets in its context.
- Search-quality bias: DuckDuckGo top results are not authoritative. Consider a preference for Wikipedia, government and major outlets in the instructions.
- Latency: the UI shows "verificando..." and polls like the other pages; one worker thread per process with a queue (a local model serves one request at a time anyway).
- Editorial: labelling named people's statements 💩 is editorial; keep the label and the sources visible.
- The test suite never calls Ollama or DuckDuckGo: pydantic-ai ships test/function models for that; the real model only runs in the explicit spike/smoke.

## 8. Tasks (one at a time, tests first where code exists, stop after each for review)
- FC0 SPIKE (no schema, no UI): standalone script (e.g. `uv run python -m spikes.checker "<claim>"`): Ollama + pydantic-ai agent + WebSearch(local='duckduckgo') (+ fetch variant),
  structured output + validators; 15-20 real claims on e4b/12b/26b/31b; report time per claim, tool-call failures, source validity, my judgement of the scores. YOU run the long part.
- FC1 Schema + services: migration 0013, claims/checks/scoring/leaderboard/dedup, per-session budget. No AI.
- FC2 Agent module (pydantic-ai) behind an interface + fakes, validators, error mapping.
- FC3 Checker worker (queue thread, SKIP LOCKED, retries, wake-up), `needs_admin` fallback.
- FC4 Player pages: third tab, rating picker 💩..⭐ in the slide panel, "Meus envios" with state/score/explanation/sources, leaderboard.
- FC5 Admin page /admin/checker: start, queue by status, manual score / override, end game, results.
- FC6 End screen, pt-BR texts, menu entry.
- FC7 Hosting + deploy (depends on FC0): where Ollama runs, env, backup (migration), cost check on a real game.

## 9. Open questions (all answered; kept for history)
1. Model hosting: after the FC0 spike a hosted API was chosen; local Ollama was dropped for cost (hosting a model on Fly CPU, a Mac
   plus tunnel or a rented GPU; see docs/validation/checker.md). Running now: `CHECKER_MODEL=openrouter:openai/gpt-oss-120b`, 2 workers, about 50-100 s and US$0.003 per claim.
2. Page fetch: yes. The research step has `web_search` and `fetch_page` tools, with caps (`MAX_SEARCHES`, `MAX_FETCHES` in `app/factcheck.py`).
3. Scoring: as built, `app/checker.py:points`: max(0, 5 - |guess - final|) + a bonus when the sign is right; a 0 guess on a clear
   claim scores 0; "unverifiable" or dismissed means 0 points for everyone. The host's score always wins (migration 0014).
4. Gemma tag: moot (see 1).
Current architecture: `docs/architecture.md`.

## Probe: Google Fact Check Explorer (2026-10-24)
- `factcheckexplorer` (GitHub GONZOsint) scrapes the undocumented `toolbox.google.com/factcheck/api/search`. 8 live queries, ~1 s each, no key.
- "os Annunakis existem": 0 results (pt and all languages); "Anunnaki" (all languages): 2 unrelated results. "vacina causa autismo" (pt): 7 results (Estadão, Observador).
- The library crashed on 6 of 8 queries (empty answers); positional parsing; unofficial endpoint (ToS and stability risk). Do not depend on it.
- The official Google Fact Check Tools API (`claims:search`, free key) gives the same data, documented. Use only as an optional first step: high precision, low recall.
- Decisions (user, 2026-10-24): reason for local model is cost; "pode conter erros" accepted; checks run async (1-3 min OK) and appear later as a card; admin override acceptable.
