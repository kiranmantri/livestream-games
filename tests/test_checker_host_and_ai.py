# ruff: noqa: RUF059, F811  (tests unpack game() tuples; the imported conn fixture is re-bound as a test argument)
"""The host's score and the AI check are INDEPENDENT (user report 2026-10-26: "once the host updates the record, the AI model never runs").
Root cause: a host score set status='resolved', the worker only takes 'queued' checks and only stores a result on a 'running' one.
Now `status` is the AI pipeline only (queued -> running -> done | needs_admin) and the host's decision is resolved_at (+ admin_score;
admin_score NULL with resolved_at set = dismissed). Migration 0014. The host's score still wins for the points."""
from __future__ import annotations

import pytest
from psycopg import errors

from app import checker as C
from tests.test_checker import _env, ai, conn, game, typed  # noqa: F401  (fixtures)


def test_a_host_score_on_a_queued_check_does_not_stop_the_ai(conn):
    s, (ana, bia) = game(conn)
    cl = typed(conn, ana, guess=-4)
    scored = C.admin_set_score(conn, s.id, cl.check_id, -5)
    assert (scored.status, scored.admin_score) == ("queued", -5) and scored.resolved_at is not None
    ck = C.claim_next_check(conn)
    assert ck is not None and ck.id == cl.check_id                       # the worker still takes it
    out = C.complete_check(conn, ck.id, ai(score=-3))
    assert (out.status, out.verdict, out.ai_score, out.admin_score) == ("done", "false", -3, -5)
    assert out.final == -5                                                 # the host's score wins for the points


def test_a_host_score_while_the_ai_runs_keeps_the_ai_answer(conn):
    s, (ana, bia) = game(conn)
    cl = typed(conn, ana)
    ck = C.claim_next_check(conn)
    C.admin_set_score(conn, s.id, cl.check_id, 2)
    out = C.complete_check(conn, ck.id, ai())                              # used to raise CheckNotRunning: the answer was lost
    assert (out.status, out.ai_score, out.admin_score) == ("done", -3, 2)


def test_a_failed_run_after_a_host_score_is_retried_like_any_other(conn):
    s, (ana, bia) = game(conn)
    cl = typed(conn, ana)
    C.admin_set_score(conn, s.id, cl.check_id, 1)
    ck = C.claim_next_check(conn)
    assert C.fail_check(conn, ck.id, "tempo esgotado").status == "queued"
    assert C.claim_next_check(conn).id == cl.check_id


def test_dismissed_still_runs_the_ai_but_nobody_scores(conn):
    s, (ana, bia) = game(conn)
    cl = typed(conn, ana, guess=-3)
    C.admin_dismiss(conn, s.id, cl.check_id)
    C.complete_check(conn, C.claim_next_check(conn).id, ai(score=-3))
    ck = C.get_check(conn, cl.check_id)
    assert ck.status == "done" and ck.final is None
    row = next(r for r in C.leaderboard(conn, s.id) if r.user_id == ana)
    assert (row.points, row.pending) == (0, 0)                              # decided by the host: not pending


def test_a_host_decision_takes_the_check_out_of_the_admin_queue_and_the_pending_list(conn):
    s, (ana, bia) = game(conn)
    cl = typed(conn, ana)
    C.complete_check(conn, C.claim_next_check(conn).id, ai(confidence="low"))
    assert C.admin_queue_count(conn, s.id) == 1 and C.pending_count(conn, s.id) == 1
    C.admin_set_score(conn, s.id, cl.check_id, -2)
    assert C.get_check(conn, cl.check_id).status == "needs_admin"         # the AI's own state is kept
    assert C.admin_queue_count(conn, s.id) == 0 and C.admin_queue(conn, s.id) == []
    assert C.pending_count(conn, s.id) == 0 and C.admin_checks(conn, s.id, pending_only=True) == []
    assert C.admin_checks(conn, s.id, pending_only=False)[0].decided is True


def test_verificar_keeps_the_host_score(conn):
    s, (ana, bia) = game(conn)
    cl = typed(conn, ana)
    for _ in range(C.MAX_ATTEMPTS):
        C.fail_check(conn, C.claim_next_check(conn).id, "falha de rede")
    C.admin_set_score(conn, s.id, cl.check_id, -1)
    out = C.admin_recheck(conn, s.id, cl.check_id)
    assert (out.status, out.error, out.attempts, out.admin_score) == ("queued", None, 0, -1)


def test_the_player_view_has_the_host_score_and_later_the_ai_answer(conn):
    s, (ana, bia) = game(conn)
    cl = typed(conn, ana, guess=-4)
    C.admin_set_score(conn, s.id, cl.check_id, -4)
    v = C.user_claims(conn, ana)[0]
    assert (v.status, v.final, v.points, v.decided) == ("queued", -4, 7, True)
    C.complete_check(conn, C.claim_next_check(conn).id, ai(score=-2))
    v = C.user_claims(conn, ana)[0]
    assert (v.status, v.verdict, v.ai_score, v.final) == ("done", "false", -2, -4)


@pytest.mark.parametrize(("status", "verdict", "ai_score", "admin", "decided", "expected"), [
    ("queued", None, None, 4, True, 4), ("running", None, None, -2, True, -2), ("done", "false", -3, 4, True, 4),
    ("done", "false", -3, None, True, None),                              # dismissed: nobody scores even with a confident AI answer
    ("done", "false", -3, None, False, -3), ("needs_admin", "false", -3, None, False, None),
])
def test_final_score_with_the_host_decision(status, verdict, ai_score, admin, decided, expected):
    assert C.final_score(status, verdict, ai_score, admin, decided) == expected


def test_the_database_has_no_resolved_status_and_a_host_score_needs_a_decision_time(conn):
    s, (ana, bia) = game(conn)
    cl = typed(conn, ana)
    with pytest.raises(errors.CheckViolation):
        conn.execute("UPDATE fact_checks SET status = 'resolved' WHERE id = %s", (cl.check_id,))
    with pytest.raises(errors.CheckViolation):
        conn.execute("UPDATE fact_checks SET admin_score = 3 WHERE id = %s", (cl.check_id,))
