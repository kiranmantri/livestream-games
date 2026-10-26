"""The fact-checking game ("Quem tá metendo o loco?"): claims, checks, scoring. NO AI in this module — the worker (FC3) and the agent (FC2) use it.

Rules (decided with the user, see AGENTS.md / docs/fact-checker-plan.md):
  * a player picks a passage (or types it) and GUESSES a score from -5 (horrible lie) to +5 (great truthful sentence);
  * one CHECK per distinct claim text inside a session (casefolded); many players' claims point at it;
  * a check is queued -> running -> done (confident AI result) | needs_admin (low confidence, unverifiable, attempts exhausted) -> resolved (admin acted);
  * final score = the admin's score if there is one, else the AI's score of a `done` check; none while pending/dismissed/unverifiable => 0 points;
  * points = max(0, 5 - |guess - final|) + 2 for the right sign (a guess of 0 has no sign); a guess of 0 earns nothing when |final| >= 3;
  * points are computed ON DEMAND from the current final score, so an admin override re-scores everybody;
  * at most MAX_CLAIMS_PER_PLAYER claims per player, one every RATE_LIMIT_S seconds;
  * winner = most points; ties: more exact guesses, then whoever sent their last claim first.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg import errors
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from app import falacias as F
from app.db import ensure_autocommit
from app.sessions import SessionError

MAX_CLAIMS_PER_PLAYER = 10           # revisit later (user, 2026-10-25)
RATE_LIMIT_S = 10                    # one claim per player per 10 s
MAX_ATTEMPTS = 3                     # runs of the agent before the admin is asked
SIGN_BONUS = 2
HEDGE_FROM = 3                       # a guess of 0 earns nothing when the truth is this far from 0
CONTEXT_LINES = 3                    # lines before/after the selected passage given to the agent
SCORE_MIN, SCORE_MAX = -5, 5
VERDICTS = ("true", "partial", "false", "unverifiable")
CONFIDENCES = ("low", "medium", "high")
MAX_EXPLANATION = 1000
MAX_SOURCES = 3


class CheckFailed(Exception):
    """The AI check could not be completed. The message is a short pt-BR reason shown to the admin; it never contains a key or a response body."""


CHECKER_MODEL_UNSET = "CHECKER_MODEL is not set (use groq:<model id> or openrouter:<model id>)"


class CheckerError(SessionError):
    code = "checker_error"


class BadGuess(CheckerError):
    code = "bad_guess"


class BadScore(CheckerError):
    code = "bad_score"


class NotACheckerPlayer(CheckerError):
    code = "not_a_checker_player"


class SessionNotActive(CheckerError):
    code = "session_not_active"


class RateLimited(CheckerError):
    code = "rate_limited"


class ClaimLimitReached(CheckerError):
    code = "claim_limit_reached"


class DuplicateClaim(CheckerError):
    code = "duplicate_claim"


class CheckNotFound(CheckerError):
    code = "check_not_found"


class CheckNotRunning(CheckerError):
    code = "check_not_running"


# ------------------------------------------------------------------ pure rules
def points(guess: int, final: int | None) -> int:
    if final is None:
        return 0
    if guess == 0 and abs(final) >= HEDGE_FROM:
        return 0
    return max(0, 5 - abs(guess - final)) + (SIGN_BONUS if guess * final > 0 else 0)


def final_score(status: str, verdict: str | None, ai_score: int | None, admin_score: int | None) -> int | None:
    """The score the players are compared with, or None while there is none (pending, dismissed, unverifiable)."""
    if admin_score is not None:
        return admin_score
    if status == "done" and verdict != "unverifiable":
        return ai_score
    return None


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _check_guess(guess) -> int:
    if not _is_int(guess) or not SCORE_MIN <= guess <= SCORE_MAX:
        raise BadGuess(f"the guess must be an integer from {SCORE_MIN} to {SCORE_MAX}")
    return guess


# ------------------------------------------------------------------ rows
@dataclass(frozen=True)
class Claim:
    id: UUID
    session_id: UUID
    user_id: UUID
    check_id: UUID
    kind: str
    start_event_id: int
    start_offset: int
    end_event_id: int
    end_offset: int
    quote: str
    guess: int
    note: str
    created_at: datetime


@dataclass(frozen=True)
class Check:
    id: UUID
    session_id: UUID
    claim_text: str
    status: str
    attempts: int
    verdict: str | None
    ai_score: int | None
    confidence: str | None
    explanation: str
    sources: list
    admin_score: int | None
    error: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    elapsed_ms: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    resolved_at: datetime | None

    @property
    def final(self) -> int | None:
        return final_score(self.status, self.verdict, self.ai_score, self.admin_score)


_CLAIM_COLS = "id, session_id, user_id, check_id, kind, start_event_id, start_offset, end_event_id, end_offset, quote, guess, note, created_at"
_CHECK_COLS = ("id, session_id, claim_text, status, attempts, verdict, ai_score, confidence, explanation, sources, admin_score, error, model, "
               "input_tokens, output_tokens, elapsed_ms, created_at, started_at, finished_at, resolved_at")


def get_check(conn: psycopg.Connection, check_id: UUID) -> Check | None:
    with conn.cursor(row_factory=class_row(Check)) as cur:
        cur.execute(f"SELECT {_CHECK_COLS} FROM fact_checks WHERE id = %s", (check_id,))
        return cur.fetchone()


# ------------------------------------------------------------------ submitting
def _player_session(conn: psycopg.Connection, user_id: UUID) -> UUID:
    row = conn.execute("SELECT u.session_id, s.status, s.game_type FROM users u JOIN game_sessions s ON s.id = u.session_id WHERE u.id = %s",
                       (user_id,)).fetchone()
    if row is None or row[2] != "checker":
        raise NotACheckerPlayer("not a player of a checker session")
    if row[1] != "active":
        raise SessionNotActive("the session has ended")
    return row[0]


def _store(conn: psycopg.Connection, session_id: UUID, user_id: UUID, insert_event, quote: str, guess: int, note: str) -> Claim:
    """Under the per-player lock: session still active, rate limit, cap; then the shared check (one per text) and the claim."""
    try:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"checker-claim:{user_id}",))
            status = conn.execute("SELECT status FROM game_sessions WHERE id = %s FOR SHARE", (session_id,)).fetchone()
            if status is None or status[0] != "active":
                raise SessionNotActive("the session has ended")
            if RATE_LIMIT_S > 0 and conn.execute(
                    "SELECT 1 FROM fact_claims WHERE user_id = %s AND created_at > clock_timestamp() - make_interval(secs => %s)",
                    (user_id, RATE_LIMIT_S)).fetchone():
                raise RateLimited(f"wait {RATE_LIMIT_S} s between claims")
            if conn.execute("SELECT count(*) FROM fact_claims WHERE user_id = %s", (user_id,)).fetchone()[0] >= MAX_CLAIMS_PER_PLAYER:
                raise ClaimLimitReached(f"at most {MAX_CLAIMS_PER_PLAYER} claims per player")
            kind, start_event_id, start_offset, end_event_id, end_offset = insert_event()
            conn.execute("INSERT INTO fact_checks (session_id, claim_key, claim_text) VALUES (%s, %s, %s) "
                         "ON CONFLICT (session_id, claim_key) DO NOTHING", (session_id, quote.casefold(), quote))
            check_id = conn.execute("SELECT id FROM fact_checks WHERE session_id = %s AND claim_key = %s",
                                    (session_id, quote.casefold())).fetchone()[0]
            with conn.cursor(row_factory=class_row(Claim)) as cur:
                cur.execute("INSERT INTO fact_claims (session_id, user_id, check_id, kind, start_event_id, start_offset, end_event_id, end_offset, "
                            "quote, guess, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                            f"RETURNING {_CLAIM_COLS}",
                            (session_id, user_id, check_id, kind, start_event_id, start_offset, end_event_id, end_offset, quote, guess, note))
                return cur.fetchone()
    except errors.UniqueViolation as e:
        if e.diag.constraint_name == "fact_claim_one_per_player_and_text":
            raise DuplicateClaim("you already sent this claim") from e
        raise


def submit_claim(conn: psycopg.Connection, user_id: UUID, kind: str, start_event_id: int, start_offset: int,
                 end_event_id: int, end_offset: int, guess: int, note: str = "") -> Claim:
    """A player picks a passage of a pane and guesses its score. The quote is rebuilt by the server from the stored lines."""
    ensure_autocommit(conn)
    _check_guess(guess)
    note = F.normalize_note(note)
    session_id = _player_session(conn, user_id)
    quote = F.build_quote(conn, session_id, kind, start_event_id, start_offset, end_event_id, end_offset)
    return _store(conn, session_id, user_id, lambda: (kind, start_event_id, start_offset, end_event_id, end_offset), quote, guess, note)


def submit_typed_claim(conn: psycopg.Connection, user_id: UUID, text: str, guess: int, note: str = "") -> Claim:
    """A player TYPES what was said. It becomes its own one-line event of kind 'spoken' (never shown in the panes)."""
    ensure_autocommit(conn)
    _check_guess(guess)
    note = F.normalize_note(note)
    quote = F.normalize_spoken(text)
    session_id = _player_session(conn, user_id)

    def insert_event():
        eid = conn.execute("INSERT INTO stream_events (session_id, kind, source_key, author, text, fed_at) "
                           "SELECT %s, 'spoken', %s, u.name, %s, clock_timestamp() FROM users u WHERE u.id = %s RETURNING id",
                           (session_id, str(uuid.uuid4()), quote, user_id)).fetchone()[0]
        return "spoken", eid, 0, eid, F._units(quote)

    return _store(conn, session_id, user_id, insert_event, quote, guess, note)


# ------------------------------------------------------------------ the queue (used by the worker)
@dataclass(frozen=True)
class AiResult:
    verdict: str
    score: int
    confidence: str
    explanation: str
    sources: list
    model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    elapsed_ms: int | None = None


def claim_next_check(conn: psycopg.Connection) -> Check | None:
    """Atomically take the oldest queued check of an ACTIVE session and mark it running (safe with several workers)."""
    ensure_autocommit(conn)
    with conn.cursor(row_factory=class_row(Check)) as cur:
        cur.execute(
            "WITH nxt AS (SELECT c.id FROM fact_checks c JOIN game_sessions g ON g.id = c.session_id "
            "             WHERE c.status = 'queued' AND g.status = 'active' ORDER BY c.created_at, c.id "
            "             FOR UPDATE OF c SKIP LOCKED LIMIT 1) "
            "UPDATE fact_checks c SET status = 'running', attempts = c.attempts + 1, started_at = now(), finished_at = NULL "
            f"FROM nxt WHERE c.id = nxt.id RETURNING {', '.join('c.' + col.strip() for col in _CHECK_COLS.split(','))}")
        return cur.fetchone()


def _validate_result(r: AiResult) -> None:
    if r.verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {r.verdict!r}")
    if not _is_int(r.score) or not SCORE_MIN <= r.score <= SCORE_MAX:
        raise ValueError("score must be an integer from -5 to 5")
    lo, hi = {"true": (1, 5), "false": (-5, -1), "partial": (-2, 2), "unverifiable": (0, 0)}[r.verdict]
    if not lo <= r.score <= hi:
        raise ValueError(f"verdict {r.verdict!r} needs a score from {lo} to {hi}, got {r.score}")
    if r.confidence not in CONFIDENCES:
        raise ValueError(f"unknown confidence {r.confidence!r}")
    if not isinstance(r.explanation, str) or len(r.explanation) > MAX_EXPLANATION:
        raise ValueError(f"explanation must be text of at most {MAX_EXPLANATION} characters")
    if not isinstance(r.sources, list) or len(r.sources) > MAX_SOURCES:
        raise ValueError(f"at most {MAX_SOURCES} sources")
    for s in r.sources:
        if not (isinstance(s, dict) and isinstance(s.get("title"), str) and isinstance(s.get("url"), str)
                and re.match(r"https?://\S+$", s["url"])):
            raise ValueError("a source needs a title and an http(s) url")
    if r.verdict != "unverifiable" and not r.sources:
        raise ValueError("a verdict needs at least one source")


def complete_check(conn: psycopg.Connection, check_id: UUID, result: AiResult) -> Check:
    """Store the agent's answer on a RUNNING check. Confident -> done; low confidence or unverifiable -> needs_admin (still shown to players)."""
    ensure_autocommit(conn)
    _validate_result(result)
    status = "needs_admin" if result.confidence == "low" or result.verdict == "unverifiable" else "done"
    sources = [{"title": s["title"], "url": s["url"]} for s in result.sources]
    with conn.cursor(row_factory=class_row(Check)) as cur:
        cur.execute("UPDATE fact_checks SET status = %s, verdict = %s, ai_score = %s, confidence = %s, explanation = %s, sources = %s, "
                    "model = %s, input_tokens = %s, output_tokens = %s, elapsed_ms = %s, error = NULL, finished_at = now() "
                    f"WHERE id = %s AND status = 'running' RETURNING {_CHECK_COLS}",
                    (status, result.verdict, result.score, result.confidence, result.explanation, Jsonb(sources), result.model or None,
                     result.input_tokens, result.output_tokens, result.elapsed_ms, check_id))
        row = cur.fetchone()
    if row is None:
        raise CheckNotRunning("the check is not running")
    return row


def fail_check(conn: psycopg.Connection, check_id: UUID, error: str) -> Check:
    """The run failed: queue it again, or ask the admin when MAX_ATTEMPTS runs are used up."""
    ensure_autocommit(conn)
    with conn.cursor(row_factory=class_row(Check)) as cur:
        cur.execute("UPDATE fact_checks SET status = CASE WHEN attempts >= %s THEN 'needs_admin' ELSE 'queued' END, "
                    "error = left(%s, 500), finished_at = now() WHERE id = %s AND status = 'running' "
                    f"RETURNING {_CHECK_COLS}", (MAX_ATTEMPTS, error, check_id))
        row = cur.fetchone()
    if row is None:
        raise CheckNotRunning("the check is not running")
    return row


def recover_interrupted(conn: psycopg.Connection) -> int:
    """After a restart: checks left `running` go back to the queue (their attempt stays counted)."""
    ensure_autocommit(conn)
    return conn.execute("UPDATE fact_checks SET status = 'queued' WHERE status = 'running'").rowcount


@dataclass(frozen=True)
class Context:
    lines: list[str]
    note: str


def context_for(conn: psycopg.Connection, check_id: UUID) -> Context:
    """What the agent may read besides the claim: CONTEXT_LINES lines around the first claim's passage, and that player's message."""
    row = conn.execute("SELECT session_id, kind, start_event_id, end_event_id, note FROM fact_claims WHERE check_id = %s "
                       "ORDER BY created_at, id LIMIT 1", (check_id,)).fetchone()
    if row is None:
        return Context([], "")
    session_id, kind, first, last, note = row
    if kind == "spoken":
        return Context([], note)
    before = conn.execute("SELECT text FROM stream_events WHERE session_id = %s AND kind = %s AND id < %s ORDER BY id DESC LIMIT %s",
                          (session_id, kind, first, CONTEXT_LINES)).fetchall()
    middle = conn.execute("SELECT text FROM stream_events WHERE session_id = %s AND kind = %s AND id BETWEEN %s AND %s ORDER BY id",
                          (session_id, kind, first, last)).fetchall()
    after = conn.execute("SELECT text FROM stream_events WHERE session_id = %s AND kind = %s AND id > %s ORDER BY id LIMIT %s",
                         (session_id, kind, last, CONTEXT_LINES)).fetchall()
    return Context([t for (t,) in [*reversed(before), *middle, *after]], note)


# ------------------------------------------------------------------ admin
def _require_active(conn: psycopg.Connection, session_id: UUID) -> None:
    row = conn.execute("SELECT 1 FROM game_sessions WHERE id = %s AND status = 'active' AND game_type = 'checker'", (session_id,)).fetchone()
    if row is None:
        raise SessionNotActive("no active checker session")


def _resolve(conn: psycopg.Connection, session_id: UUID, check_id: UUID, score: int | None) -> Check:
    ensure_autocommit(conn)
    if score is not None and (not _is_int(score) or not SCORE_MIN <= score <= SCORE_MAX):
        raise BadScore(f"the score must be an integer from {SCORE_MIN} to {SCORE_MAX}")
    _require_active(conn, session_id)
    with conn.cursor(row_factory=class_row(Check)) as cur:
        cur.execute("UPDATE fact_checks SET status = 'resolved', admin_score = %s, resolved_at = now() "
                    f"WHERE id = %s AND session_id = %s RETURNING {_CHECK_COLS}", (score, check_id, session_id))
        row = cur.fetchone()
    if row is None:
        raise CheckNotFound("unknown check")
    return row


def admin_set_score(conn: psycopg.Connection, session_id: UUID, check_id: UUID, score: int) -> Check:
    """The admin's score replaces the AI's (works on any state, also to override a confident result). Players are re-scored on the next read."""
    if score is None:
        raise BadScore("a score is required (use admin_dismiss for 'nobody scores')")
    return _resolve(conn, session_id, check_id, score)


def admin_dismiss(conn: psycopg.Connection, session_id: UUID, check_id: UUID) -> Check:
    """Nobody scores on this claim."""
    return _resolve(conn, session_id, check_id, None)


@dataclass(frozen=True)
class QueueItem:
    check_id: UUID
    claim_text: str
    status: str
    verdict: str | None
    ai_score: int | None
    confidence: str | None
    explanation: str
    sources: list
    error: str | None
    claims: int
    created_at: datetime


def admin_queue(conn: psycopg.Connection, session_id: UUID) -> list[QueueItem]:
    """Checks waiting for the admin (low confidence, unverifiable, attempts exhausted), oldest first."""
    rows = conn.execute(
        "SELECT c.id, c.claim_text, c.status, c.verdict, c.ai_score, c.confidence, c.explanation, c.sources, c.error, "
        "       (SELECT count(*) FROM fact_claims k WHERE k.check_id = c.id), c.created_at "
        "FROM fact_checks c WHERE c.session_id = %s AND c.status = 'needs_admin' ORDER BY c.created_at, c.id", (session_id,)).fetchall()
    return [QueueItem(*r) for r in rows]


def admin_queue_count(conn: psycopg.Connection, session_id: UUID) -> int:
    return conn.execute("SELECT count(*) FROM fact_checks WHERE session_id = %s AND status = 'needs_admin'", (session_id,)).fetchone()[0]


PENDING_STATES = ("queued", "running", "needs_admin")


@dataclass(frozen=True)
class AdminCheck:
    """One check as the admin sees it: the AI answer, the admin's score, and who guessed what."""
    check_id: UUID
    claim_text: str
    status: str
    verdict: str | None
    ai_score: int | None
    confidence: str | None
    explanation: str
    sources: list
    error: str | None
    admin_score: int | None
    created_at: datetime
    guesses: list        # [(player name, guess)] in the order they were sent


def admin_checks(conn: psycopg.Connection, session_id: UUID, pending_only: bool = True) -> list[AdminCheck]:
    """Every check of the session (or only queued / running / waiting for the admin), oldest first, with the guesses."""
    where = " AND c.status = ANY(%s)" if pending_only else ""
    args = (session_id, list(PENDING_STATES)) if pending_only else (session_id,)
    rows = conn.execute(
        "SELECT c.id, c.claim_text, c.status, c.verdict, c.ai_score, c.confidence, c.explanation, c.sources, c.error, c.admin_score, "
        f"c.created_at FROM fact_checks c WHERE c.session_id = %s{where} ORDER BY c.created_at, c.id", args).fetchall()
    guesses: dict = {}
    for check_id, name, guess in conn.execute(
            "SELECT k.check_id, u.name, k.guess FROM fact_claims k JOIN users u ON u.id = k.user_id "
            "WHERE k.session_id = %s ORDER BY k.created_at, k.id", (session_id,)):
        guesses.setdefault(check_id, []).append((name, guess))
    return [AdminCheck(*r, guesses=guesses.get(r[0], [])) for r in rows]


def admin_check(conn: psycopg.Connection, session_id: UUID, check_id: UUID) -> AdminCheck | None:
    return next((c for c in admin_checks(conn, session_id, pending_only=False) if c.check_id == check_id), None)


def pending_count(conn: psycopg.Connection, session_id: UUID) -> int:
    return conn.execute("SELECT count(*) FROM fact_checks WHERE session_id = %s AND status = ANY(%s)",
                        (session_id, list(PENDING_STATES))).fetchone()[0]


# ------------------------------------------------------------------ what a player sees
@dataclass(frozen=True)
class ClaimView:
    claim_id: UUID
    check_id: UUID
    kind: str
    quote: str
    guess: int
    note: str
    created_at: datetime
    status: str
    verdict: str | None
    ai_score: int | None
    confidence: str | None
    explanation: str
    sources: list
    admin_score: int | None
    final: int | None
    points: int


def user_claims(conn: psycopg.Connection, user_id: UUID) -> list[ClaimView]:
    rows = conn.execute(
        "SELECT k.id, k.check_id, k.kind, k.quote, k.guess, k.note, k.created_at, c.status, c.verdict, c.ai_score, c.confidence, "
        "       c.explanation, c.sources, c.admin_score FROM fact_claims k JOIN fact_checks c ON c.id = k.check_id "
        "WHERE k.user_id = %s ORDER BY k.created_at, k.id", (user_id,)).fetchall()
    out = []
    for r in rows:
        final = final_score(r[7], r[8], r[9], r[13])
        out.append(ClaimView(*r, final=final, points=points(r[4], final)))
    return out


@dataclass(frozen=True)
class LeaderRow:
    rank: int
    user_id: UUID
    name: str
    points: int
    claims: int
    exact: int
    pending: int
    last: datetime | None


def leaderboard(conn: psycopg.Connection, session_id: UUID) -> list[LeaderRow]:
    """Order: points, then exact guesses, then who sent their last claim first, then join order."""
    users = conn.execute("SELECT u.id, u.name FROM users u JOIN game_sessions g ON g.id = u.session_id "
                         "WHERE u.session_id = %s AND g.game_type = 'checker' ORDER BY u.created_at, u.id", (session_id,)).fetchall()
    stats = {u: {"points": 0, "claims": 0, "exact": 0, "pending": 0, "last": None} for u, _ in users}
    for uid, guess, created, status, verdict, ai_score, admin_score in conn.execute(
            "SELECT k.user_id, k.guess, k.created_at, c.status, c.verdict, c.ai_score, c.admin_score "
            "FROM fact_claims k JOIN fact_checks c ON c.id = k.check_id WHERE k.session_id = %s", (session_id,)):
        st = stats[uid]
        final = final_score(status, verdict, ai_score, admin_score)
        st["claims"] += 1
        st["points"] += points(guess, final)
        st["exact"] += 1 if final is not None and guess == final else 0
        st["pending"] += 1 if final is None and status != "resolved" else 0
        st["last"] = created if st["last"] is None or created > st["last"] else st["last"]

    def key(i: int):
        s = stats[users[i][0]]
        return (-s["points"], -s["exact"], s["last"] is None, s["last"].timestamp() if s["last"] else 0.0, i)

    out = []
    for rank, i in enumerate(sorted(range(len(users)), key=key), start=1):
        uid, name = users[i]
        s = stats[uid]
        out.append(LeaderRow(rank, uid, name, s["points"], s["claims"], s["exact"], s["pending"], s["last"]))
    return out


@dataclass(frozen=True)
class FinishResult:
    session_id: UUID
    winner: LeaderRow | None
    standings: list[LeaderRow]


def finish_session(conn: psycopg.Connection, session_id: UUID) -> FinishResult | None:
    """Admin ends an ACTIVE checker session and the winner is stored (most points; nobody with points > 0 -> no winner).
    None when unknown, not a checker session or already ended: a second finish never overwrites the winner."""
    ensure_autocommit(conn)
    with conn.transaction():
        locked = conn.execute("SELECT 1 FROM game_sessions WHERE id = %s AND status = 'active' AND game_type = 'checker' FOR UPDATE",
                              (session_id,)).fetchone()
        if locked is None:
            return None
        standings = leaderboard(conn, session_id)
        winner = standings[0] if standings and standings[0].points > 0 else None
        conn.execute("UPDATE game_sessions SET status = 'ended', ended_at = now(), winner_user_id = %s WHERE id = %s",
                     (winner.user_id if winner else None, session_id))
    return FinishResult(session_id, winner, standings)
