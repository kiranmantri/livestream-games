# ruff: noqa: RUF059  (tests unpack game() tuples and keep only what they use)
"""FC1: the fact-checking game ("Quem tá metendo o loco?") — schema (migration 0013) and services (app/checker.py). No AI here."""
from __future__ import annotations

import threading

import pytest
from psycopg import errors

from app import checker as C
from app import falacias as F
from app import sessions as S
from app.db import connect


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users CASCADE")
    yield c
    c.execute("TRUNCATE game_sessions, users CASCADE")
    c.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "50")
    monkeypatch.setattr(C, "RATE_LIMIT_S", 0)


def game(conn, players=("Ana", "Bia")):
    s = S.start_session_as_admin(conn, game_type="checker")
    ids = [S.join_session(conn, n, f"10.0.0.{i + 1}", game_type="checker")[1].id for i, n in enumerate(players)]
    return s, ids


def ai(**over):
    base = {"verdict": "false", "score": -3, "confidence": "high", "explanation": "Falso.", "sources": [{"title": "Fonte", "url": "https://example.org/a"}],
            "model": "groq:openai/gpt-oss-120b", "input_tokens": 1000, "output_tokens": 100, "elapsed_ms": 2500}
    return C.AiResult(**(base | over))


def typed(conn, uid, text="Os Annunakis existem", guess=-4, **kw):
    return C.submit_typed_claim(conn, uid, text, guess, **kw)


# ---------------------------------------------------------------- pure rules
@pytest.mark.parametrize(("guess", "final", "expected"), [
    (5, 5, 7), (-5, -5, 7),            # exact: 5 + sign bonus 2
    (3, 5, 5),                          # 5 - 2 = 3, + 2 right sign
    (-3, 5, 0),                         # 5 - 8 -> 0, wrong sign: nothing
    (1, 5, 3),                          # 5 - 4 = 1, + 2
    (0, 4, 0), (0, -3, 0),              # hedging at 0 when |final| >= 3: nothing
    (0, 2, 3), (0, -2, 3),              # 0 is allowed when the truth is mild: 5 - 2, no sign bonus (0 has no sign)
    (0, 0, 5),                          # exact on a mixed result: 5, no sign bonus (final 0 has no sign)
    (1, 0, 4), (-1, 0, 4),
    (5, -5, 0), (-5, 5, 0),
])
def test_points_rule(guess, final, expected):
    assert C.points(guess, final) == expected


def test_points_are_zero_while_there_is_no_final_score():
    assert C.points(3, None) == 0


@pytest.mark.parametrize(("status", "verdict", "ai_score", "admin", "expected"), [
    ("done", "false", -3, None, -3),
    ("done", "unverifiable", 0, None, None),       # nobody scores on an unverifiable claim
    ("queued", None, None, None, None), ("running", None, None, None, None),
    ("needs_admin", "false", -3, None, None),      # low confidence: the AI result is shown, points wait for the admin
    ("resolved", "false", -3, 4, 4),               # the admin's score wins
    ("resolved", "false", -3, None, None),         # dismissed
    ("done", "false", -3, 2, 2),                   # an override of a confident result also wins
])
def test_final_score(status, verdict, ai_score, admin, expected):
    assert C.final_score(status, verdict, ai_score, admin) == expected


# ---------------------------------------------------------------- schema
def test_a_checker_session_can_exist_next_to_the_other_games(conn):
    S.start_session_as_admin(conn, game_type="bingo")
    S.start_session_as_admin(conn, game_type="falacias")
    s = S.start_session_as_admin(conn, game_type="checker")
    assert s.game_type == "checker"
    with pytest.raises(S.SessionAlreadyActive):
        S.start_session_as_admin(conn, game_type="checker")


def test_a_checker_session_has_its_own_cookie():
    assert S.cookie_name("checker") == "lg_token_checker"


def test_a_checker_session_may_follow_a_video(conn):
    s = S.start_session_as_admin(conn, game_type="checker", video_id="abcdefghijk", source_mode="live")
    assert (s.video_id, s.source_mode) == ("abcdefghijk", "live")


def test_the_guess_is_limited_to_minus5_plus5_by_the_database(conn):
    s, (ana, _) = game(conn)
    cid = typed(conn, ana).check_id
    eid = conn.execute("INSERT INTO stream_events (session_id, kind, source_key, text) VALUES (%s,'spoken','k','t') RETURNING id", (s.id,)).fetchone()[0]
    for bad in (-6, 6):
        with pytest.raises(errors.CheckViolation):
            conn.execute("INSERT INTO fact_claims (session_id, user_id, kind, start_event_id, start_offset, end_event_id, end_offset, quote, guess, check_id) "
                         "VALUES (%s,%s,'spoken',%s,0,%s,1,'t',%s,%s)", (s.id, ana, eid, eid, bad, cid))


def test_the_database_refuses_a_score_that_disagrees_with_the_verdict(conn):
    s, (ana, _) = game(conn)
    cid = typed(conn, ana).check_id
    for verdict, score in (("true", -2), ("false", 2), ("partial", 3), ("unverifiable", 1), ("true", 0)):
        with pytest.raises(errors.CheckViolation):
            conn.execute("UPDATE fact_checks SET status='done', verdict=%s, ai_score=%s, confidence='high' WHERE id=%s", (verdict, score, cid))


def test_a_done_check_needs_a_verdict_and_a_score(conn):
    s, (ana, _) = game(conn)
    cid = typed(conn, ana).check_id
    with pytest.raises(errors.CheckViolation):
        conn.execute("UPDATE fact_checks SET status='done' WHERE id=%s", (cid,))


def test_deleting_a_session_removes_its_claims_and_checks(conn):
    s, (ana, _) = game(conn)
    typed(conn, ana)
    conn.execute("DELETE FROM game_sessions WHERE id=%s", (s.id,))
    assert conn.execute("SELECT (SELECT count(*) FROM fact_claims) + (SELECT count(*) FROM fact_checks)").fetchone()[0] == 0


# ---------------------------------------------------------------- submitting a claim
def test_a_typed_claim_creates_a_queued_check_and_a_claim(conn):
    s, (ana, _) = game(conn)
    cl = typed(conn, ana, "  Os   Annunakis existem ", -4, note="  acho que é lenda ")
    assert (cl.quote, cl.guess, cl.note, cl.kind, cl.user_id, cl.session_id) == ("Os Annunakis existem", -4, "acho que é lenda", "spoken", ana, s.id)
    ck = C.get_check(conn, cl.check_id)
    assert (ck.status, ck.attempts, ck.verdict, ck.ai_score) == ("queued", 0, None, None)
    ev = conn.execute("SELECT kind, author, text FROM stream_events WHERE session_id=%s", (s.id,)).fetchall()
    assert ev == [("spoken", "Ana", "Os Annunakis existem")]


def test_a_selected_claim_uses_the_server_built_quote(conn):
    s, (ana, _) = game(conn)
    eid = F.add_event(conn, s.id, "transcript", "1", "a Terra é plana e todo mundo sabe disso")
    cl = C.submit_claim(conn, ana, "transcript", eid, 2, eid, 15, guess=-5)
    assert cl.quote == "Terra é plana" and cl.kind == "transcript" and cl.guess == -5


def test_a_bad_selection_is_refused(conn):
    s, (ana, _) = game(conn)
    eid = F.add_event(conn, s.id, "transcript", "1", "texto curto")
    with pytest.raises(F.BadSelection):
        C.submit_claim(conn, ana, "transcript", eid, 0, eid, 999, guess=1)
    with pytest.raises(F.BadSelection):
        C.submit_claim(conn, ana, "spoken", eid, 0, eid, 3, guess=1)        # a typed line is not a pane


@pytest.mark.parametrize("guess", [-6, 6, 1.5, "3", None, True])
def test_a_guess_must_be_an_integer_from_minus5_to_5(conn, guess):
    s, (ana, _) = game(conn)
    with pytest.raises(C.BadGuess):
        typed(conn, ana, guess=guess)


def test_empty_and_oversized_text_are_refused(conn):
    s, (ana, _) = game(conn)
    with pytest.raises(F.BadSelection):
        typed(conn, ana, "   ")
    with pytest.raises(F.SpokenTooLong):
        typed(conn, ana, "x" * 301)
    with pytest.raises(F.NoteTooLong):
        typed(conn, ana, note="y" * 281)


def test_only_a_player_of_an_active_checker_session_can_submit(conn):
    f = S.start_session_as_admin(conn, game_type="falacias")
    _, fp, _ = S.join_session(conn, "Fal", "10.0.0.9", game_type="falacias")
    with pytest.raises(C.NotACheckerPlayer):
        typed(conn, fp.id)
    s, (ana, _) = game(conn)
    S.end_session(conn, s.id, None)
    with pytest.raises(C.SessionNotActive):
        typed(conn, ana)
    assert f.game_type == "falacias"


def test_rate_limit_between_claims(conn, monkeypatch):
    monkeypatch.setattr(C, "RATE_LIMIT_S", 60)
    s, (ana, bia) = game(conn)
    typed(conn, ana, "primeira frase")
    with pytest.raises(C.RateLimited):
        typed(conn, ana, "segunda frase")
    typed(conn, bia, "outra pessoa pode")


def test_at_most_ten_claims_per_player(conn):
    s, (ana, bia) = game(conn)
    for i in range(C.MAX_CLAIMS_PER_PLAYER):
        typed(conn, ana, f"frase número {i}")
    with pytest.raises(C.ClaimLimitReached):
        typed(conn, ana, "a décima primeira")
    typed(conn, bia, "a Bia ainda pode")           # the cap is per player


def test_the_same_player_cannot_send_the_same_text_twice(conn):
    s, (ana, _) = game(conn)
    typed(conn, ana, "A Terra é plana")
    with pytest.raises(C.DuplicateClaim):
        typed(conn, ana, "  a terra   É PLANA ", guess=2)


def test_identical_text_from_two_players_shares_one_check(conn):
    s, (ana, bia) = game(conn)
    a = typed(conn, ana, "A Terra é plana", -5)
    b = typed(conn, bia, "a terra é plana", -1)
    assert a.check_id == b.check_id and a.id != b.id
    assert conn.execute("SELECT count(*) FROM fact_checks WHERE session_id=%s", (s.id,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM fact_claims WHERE session_id=%s", (s.id,)).fetchone()[0] == 2


def test_the_same_text_in_another_session_is_checked_again(conn):
    s1, (a1, _) = game(conn)
    c1 = typed(conn, a1, "A Terra é plana")
    S.end_session(conn, s1.id, None)
    s2, (a2, _) = game(conn)
    c2 = typed(conn, a2, "A Terra é plana")
    assert c1.check_id != c2.check_id


def test_concurrent_submits_of_one_player_respect_the_cap(conn, test_db_url, monkeypatch):
    s, (ana, _) = game(conn)
    results, lock = [], threading.Lock()

    def worker(i):
        c = connect(test_db_url)
        try:
            C.submit_typed_claim(c, ana, f"frase concorrente {i}", 1)
            out = "ok"
        except C.ClaimLimitReached:
            out = "cap"
        finally:
            c.close()
        with lock:
            results.append(out)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(C.MAX_CLAIMS_PER_PLAYER + 6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count("ok") == C.MAX_CLAIMS_PER_PLAYER and results.count("cap") == 6


# ---------------------------------------------------------------- the queue (what the worker will use)
def test_claim_next_takes_the_oldest_queued_check_and_marks_it_running(conn):
    s, (ana, bia) = game(conn)
    a = typed(conn, ana, "primeira")
    b = typed(conn, bia, "segunda")
    first = C.claim_next_check(conn)
    assert first.id == a.check_id and first.status == "running" and first.attempts == 1 and first.claim_text == "primeira"
    assert C.claim_next_check(conn).id == b.check_id
    assert C.claim_next_check(conn) is None


def test_two_workers_never_take_the_same_check(conn, test_db_url):
    s, (ana, bia) = game(conn)
    typed(conn, ana, "um")
    typed(conn, bia, "dois")
    taken, lock = [], threading.Lock()

    def worker():
        c = connect(test_db_url)
        try:
            ck = C.claim_next_check(c)
            with lock:
                taken.append(ck.id if ck else None)
        finally:
            c.close()

    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    got = [t for t in taken if t]
    assert len(got) == 2 and len(set(got)) == 2


def test_claims_of_an_ended_session_are_not_checked(conn):
    s, (ana, _) = game(conn)
    typed(conn, ana)
    S.end_session(conn, s.id, None)
    assert C.claim_next_check(conn) is None


def _running(conn, text="Os Annunakis existem"):
    s, (ana, bia) = game(conn)
    cl = typed(conn, ana, text)
    return s, ana, bia, cl, C.claim_next_check(conn)


def test_a_confident_result_is_done_and_stores_everything(conn):
    s, ana, bia, cl, ck = _running(conn)
    out = C.complete_check(conn, ck.id, ai())
    assert out.status == "done" and (out.verdict, out.ai_score, out.confidence) == ("false", -3, "high")
    assert out.explanation == "Falso." and out.sources == [{"title": "Fonte", "url": "https://example.org/a"}]
    assert (out.model, out.input_tokens, out.output_tokens, out.elapsed_ms) == ("groq:openai/gpt-oss-120b", 1000, 100, 2500)
    assert out.finished_at is not None


def test_low_confidence_goes_to_the_admin_but_keeps_the_ai_result_visible(conn):
    s, ana, bia, cl, ck = _running(conn)
    out = C.complete_check(conn, ck.id, ai(confidence="low"))
    assert out.status == "needs_admin" and out.verdict == "false" and out.explanation == "Falso."


def test_an_unverifiable_claim_goes_to_the_admin(conn):
    s, ana, bia, cl, ck = _running(conn)
    out = C.complete_check(conn, ck.id, ai(verdict="unverifiable", score=0, confidence="high", sources=[]))
    assert out.status == "needs_admin" and out.verdict == "unverifiable"


def test_an_inconsistent_ai_result_is_refused_before_it_reaches_the_database(conn):
    s, ana, bia, cl, ck = _running(conn)
    for bad in (ai(verdict="true", score=-2), ai(score=9), ai(verdict="maybe"), ai(confidence="sure"), ai(sources=[{"title": "x"}]),
                ai(sources=[{"title": "a", "url": "javascript:alert(1)"}]), ai(sources=[{"title": "a", "url": "https://x.org"}] * 4), ai(sources=[])):
        with pytest.raises(ValueError):
            C.complete_check(conn, ck.id, bad)
    assert C.get_check(conn, ck.id).status == "running"


def test_a_failure_requeues_until_the_attempts_run_out_then_asks_the_admin(conn):
    s, ana, bia, cl, ck = _running(conn)
    for n in range(1, C.MAX_ATTEMPTS):
        out = C.fail_check(conn, ck.id, f"timeout {n}")
        assert out.status == "queued" and out.error == f"timeout {n}"
        ck = C.claim_next_check(conn)
        assert ck.attempts == n + 1
    out = C.fail_check(conn, ck.id, "último erro")
    assert out.status == "needs_admin" and out.error == "último erro"


def test_recover_interrupted_requeues_running_checks_after_a_restart(conn):
    s, ana, bia, cl, ck = _running(conn)
    assert C.recover_interrupted(conn) == 1
    assert C.get_check(conn, ck.id).status == "queued"
    assert C.recover_interrupted(conn) == 0


def test_completing_a_check_that_is_not_running_does_nothing(conn):
    s, ana, bia, cl, ck = _running(conn)
    C.complete_check(conn, ck.id, ai())
    with pytest.raises(C.CheckNotRunning):
        C.complete_check(conn, ck.id, ai(score=-5))
    assert C.get_check(conn, ck.id).ai_score == -3


def test_the_text_and_context_for_the_agent(conn):
    s = S.start_session_as_admin(conn, game_type="checker")
    _, p, _ = S.join_session(conn, "Ana", "10.0.0.1", game_type="checker")
    ids = [F.add_event(conn, s.id, "transcript", str(i), f"linha {i} do apresentador") for i in range(1, 8)]
    C.submit_claim(conn, p.id, "transcript", ids[3], 0, ids[3], 5, guess=0, note="não acredito")
    ck = C.claim_next_check(conn)
    assert ck.claim_text == "linha"
    ctx = C.context_for(conn, ck.id)
    assert ctx.note == "não acredito"
    assert ctx.lines[:3] == ["linha 1 do apresentador", "linha 2 do apresentador", "linha 3 do apresentador"] and len(ctx.lines) == 7


# ---------------------------------------------------------------- admin
def test_the_admin_score_resolves_a_check_and_wins(conn):
    s, ana, bia, cl, ck = _running(conn)
    C.complete_check(conn, ck.id, ai(confidence="low"))
    out = C.admin_set_score(conn, s.id, ck.id, 4)
    assert out.status == "resolved" and out.admin_score == 4 and out.resolved_at is not None


def test_the_admin_can_dismiss_a_check_so_nobody_scores(conn):
    s, ana, bia, cl, ck = _running(conn)
    C.complete_check(conn, ck.id, ai(verdict="unverifiable", score=0, sources=[]))
    out = C.admin_dismiss(conn, s.id, ck.id)
    assert out.status == "resolved" and out.admin_score is None


def test_the_admin_can_score_a_check_that_failed_or_is_still_queued(conn):
    s, (ana, _) = game(conn)
    cl = typed(conn, ana)
    assert C.admin_set_score(conn, s.id, cl.check_id, -2).status == "resolved"


@pytest.mark.parametrize("bad", [-6, 6, 1.5, "2", None, True])
def test_the_admin_score_must_be_an_integer_from_minus5_to_5(conn, bad):
    s, ana, bia, cl, ck = _running(conn)
    with pytest.raises(C.BadScore):
        C.admin_set_score(conn, s.id, ck.id, bad)


def test_admin_actions_need_an_active_session_and_the_right_session(conn):
    s, ana, bia, cl, ck = _running(conn)
    with pytest.raises(C.CheckNotFound):
        C.admin_set_score(conn, s.id, __import__("uuid").uuid4(), 1)
    S.end_session(conn, s.id, None)
    with pytest.raises(C.SessionNotActive):
        C.admin_set_score(conn, s.id, ck.id, 1)


def test_a_check_of_another_session_cannot_be_scored_by_this_one(conn):
    s1, (a1, _) = game(conn)
    cl = typed(conn, a1)
    S.end_session(conn, s1.id, None)
    s2, _ = game(conn)
    with pytest.raises(C.CheckNotFound):
        C.admin_set_score(conn, s2.id, cl.check_id, 1)


def test_the_admin_queue_lists_what_needs_attention_oldest_first(conn):
    s, (ana, bia) = game(conn)
    a, b, c = typed(conn, ana, "um"), typed(conn, ana, "dois"), typed(conn, bia, "três")
    for _ in range(3):
        C.claim_next_check(conn)
    C.complete_check(conn, a.check_id, ai(confidence="low"))
    C.complete_check(conn, b.check_id, ai())
    C.fail_check(conn, c.check_id, "x")                          # attempt 1 -> queued again
    for _ in range(C.MAX_ATTEMPTS - 1):
        C.claim_next_check(conn)
        C.fail_check(conn, c.check_id, "x")                      # the last failure exhausts the attempts
    assert [i.claim_text for i in C.admin_queue(conn, s.id)] == ["um", "três"]
    assert C.admin_queue_count(conn, s.id) == 2


# ---------------------------------------------------------------- what the player sees and the scores
def test_my_claims_show_state_ai_result_and_points(conn):
    s, ana, bia, cl, ck = _running(conn)
    mine = C.user_claims(conn, ana)
    assert [(m.quote, m.status, m.points, m.final) for m in mine] == [("Os Annunakis existem", "running", 0, None)]
    C.complete_check(conn, ck.id, ai(score=-4))
    (m,) = C.user_claims(conn, ana)
    assert (m.status, m.final, m.guess, m.points, m.explanation) == ("done", -4, -4, 7, "Falso.")
    assert m.sources == [{"title": "Fonte", "url": "https://example.org/a"}]


def test_everyone_sees_the_result_of_a_shared_check_but_only_their_own_claims(conn):
    s, (ana, bia) = game(conn)
    typed(conn, ana, "A Terra é plana", -5)
    typed(conn, bia, "a terra é plana", -1)
    ck = C.claim_next_check(conn)
    C.complete_check(conn, ck.id, ai(score=-5, verdict="false"))
    assert [(m.guess, m.points) for m in C.user_claims(conn, ana)] == [(-5, 7)]
    assert [(m.guess, m.points) for m in C.user_claims(conn, bia)] == [(-1, 3)]       # 5 - 4 = 1, +2 for the right sign


def test_an_admin_override_rescores_the_players(conn):
    s, (ana, bia) = game(conn)
    typed(conn, ana, "Frase", -5)
    typed(conn, bia, "frase", 5)
    ck = C.claim_next_check(conn)
    C.complete_check(conn, ck.id, ai(score=-5))
    before = {r.name: r.points for r in C.leaderboard(conn, s.id)}
    C.admin_set_score(conn, s.id, ck.id, 5)
    after = {r.name: r.points for r in C.leaderboard(conn, s.id)}
    assert before == {"Ana": 7, "Bia": 0} and after == {"Ana": 0, "Bia": 7}


def test_pending_and_dismissed_claims_earn_nothing(conn):
    s, (ana, bia) = game(conn)
    typed(conn, ana, "pendente", 3)
    typed(conn, bia, "descartada", 3)
    ck = C.claim_next_check(conn)
    ck2 = C.claim_next_check(conn)
    C.admin_dismiss(conn, s.id, ck2.id)
    assert {r.name: r.points for r in C.leaderboard(conn, s.id)} == {"Ana": 0, "Bia": 0}
    assert ck.id != ck2.id


def test_leaderboard_orders_by_points_then_exact_guesses_then_earliest_last_claim(conn):
    s, (ana, bia, cai) = game(conn, ("Ana", "Bia", "Cai"))
    for uid, text, guess in ((ana, "f1", -5), (bia, "f2", -4), (cai, "f3", -3)):
        typed(conn, uid, text, guess)
    for _ in range(3):
        ck = C.claim_next_check(conn)
        C.complete_check(conn, ck.id, ai(score=-5))
    rows = C.leaderboard(conn, s.id)
    assert [(r.rank, r.name, r.points, r.exact, r.claims) for r in rows] == [(1, "Ana", 7, 1, 1), (2, "Bia", 6, 0, 1), (3, "Cai", 5, 0, 1)]


def test_a_tie_goes_to_whoever_sent_the_last_claim_first(conn):
    s, (ana, bia) = game(conn)
    typed(conn, bia, "bia primeiro", -5)
    typed(conn, ana, "ana depois", -5)
    for _ in range(2):
        C.complete_check(conn, C.claim_next_check(conn).id, ai(score=-5))
    assert [r.name for r in C.leaderboard(conn, s.id)] == ["Bia", "Ana"]


# ---------------------------------------------------------------- finish
def test_finish_records_the_winner(conn):
    s, (ana, bia) = game(conn)
    typed(conn, ana, "uma", -5)
    typed(conn, bia, "outra", 5)
    for _ in range(2):
        C.complete_check(conn, C.claim_next_check(conn).id, ai(score=-5))
    res = C.finish_session(conn, s.id)
    assert res.winner.name == "Ana" and res.winner.points == 7
    row = conn.execute("SELECT status, winner_user_id FROM game_sessions WHERE id=%s", (s.id,)).fetchone()
    assert row == ("ended", ana)
    assert C.finish_session(conn, s.id) is None                    # a second finish never overwrites


def test_finish_with_nobody_scoring_has_no_winner(conn):
    s, (ana, _) = game(conn)
    typed(conn, ana, "pendente", 1)
    res = C.finish_session(conn, s.id)
    assert res.winner is None and conn.execute("SELECT winner_user_id FROM game_sessions WHERE id=%s", (s.id,)).fetchone()[0] is None


def test_finish_only_ends_a_checker_session(conn):
    s = S.start_session_as_admin(conn, game_type="falacias")
    assert C.finish_session(conn, s.id) is None
    assert conn.execute("SELECT status FROM game_sessions WHERE id=%s", (s.id,)).fetchone()[0] == "active"


def test_a_tie_in_points_goes_to_more_exact_guesses_even_if_the_other_sent_first(conn):
    s, (ana, bia) = game(conn)
    typed(conn, bia, "b1", -2)            # final -5: 3 + 2 + ... = 4
    typed(conn, bia, "b2", -1)            # 3
    typed(conn, ana, "a1", -5)            # exact: 7, sent AFTER Bia's claims
    for _ in range(3):
        C.complete_check(conn, C.claim_next_check(conn).id, ai(score=-5))
    rows = C.leaderboard(conn, s.id)
    assert [(r.name, r.points, r.exact) for r in rows] == [("Ana", 7, 1), ("Bia", 7, 0)]


def test_my_claims_follow_the_admin_override(conn):
    s, ana, bia, cl, ck = _running(conn)
    C.complete_check(conn, ck.id, ai(score=-3))
    C.admin_set_score(conn, s.id, ck.id, 2)
    (m,) = C.user_claims(conn, ana)
    assert (m.final, m.admin_score, m.ai_score) == (2, 2, -3)
    assert m.points == C.points(-4, 2) == 0   # guess -4 vs final 2: wrong sign, 5 - 6 -> 0


def test_workers_in_parallel_each_get_a_different_check_when_there_is_enough_work(conn, test_db_url):
    s, (ana, _) = game(conn)
    for i in range(6):
        typed(conn, ana, f"trabalho {i}") if i < C.MAX_CLAIMS_PER_PLAYER else None
    taken, lock = [], threading.Lock()

    def worker():
        c = connect(test_db_url)
        try:
            ck = C.claim_next_check(c)
            with lock:
                taken.append(ck.id if ck else None)
        finally:
            c.close()

    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert None not in taken and len(set(taken)) == 6
