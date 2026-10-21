"""Services of "Encontre as Falácias": fallacy list, feed rows, selection -> quote, pairs (marks), review, leaderboard.

Framework-free functions over a psycopg AUTOCOMMIT connection (see app.db.connect). Points are never stored: they are
derived from (status, latency_ms, counts) by `points()`, the single place with the scoring constants, in hundredths of a
point (integers: no float noise; 1600 = 16,0 points).

Scoring (AGENTS.md / docs plan rev. 5):
    base    +5,00  every pair that "counts" (the player's first 20 UNREVIEWED pairs; flag stored at submit time)
    correct +5,00  verdict Correto
    speed   0,1 x max(0, 60 - seconds)  only on Correto; seconds = server clock from the moment the LAST selected line was
            fed to the panes until the pair was submitted (0,00 .. 6,00)
    spam    0      (the base is retracted)      wrong  base only
Selection offsets are UTF-16 code units, i.e. what the browser's Selection/Range APIs report (chat lines contain emoji).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import errors
from psycopg.rows import class_row

from app.db import ensure_autocommit
from app.sessions import SessionError

KINDS = ("transcript", "chat")
VERDICTS = ("correct", "wrong", "spam")
BASE = 500
CORRECT = 500
SPEED_MAX = 600                  # hundredths: 0,1 x 60 s
SPEED_WINDOW_S = 60
RATE_LIMIT_S = 10                # at most one pair per player per 10 s
CAP_UNREVIEWED = 20              # only the first 20 unreviewed pairs earn the base
MAX_QUOTE = 500
MAX_LINES = 30                   # a selection may span at most this many consecutive lines of one pane
SEED_PATH = Path(__file__).resolve().parent.parent / "deploy" / "seed" / "fallacies_seed.sql"


class FalaciasError(SessionError):
    code = "falacias_error"


class BadSelection(FalaciasError):
    code = "bad_selection"


class SelectionTooLong(FalaciasError):
    code = "selection_too_long"


class UnknownFallacy(FalaciasError):
    code = "unknown_fallacy"


class RateLimited(FalaciasError):
    code = "rate_limited"


class DuplicateMark(FalaciasError):
    code = "duplicate_mark"


class NotAFalaciasPlayer(FalaciasError):
    code = "not_a_falacias_player"


class SessionNotActive(FalaciasError):
    code = "session_not_active"


class MarkNotFound(FalaciasError):
    code = "mark_not_found"


# ------------------------------------------------------------------ scoring (pure)
def points(status: str, latency_ms: int, counts: bool) -> int:
    """Hundredths of a point for one pair."""
    if status not in ("pending", "correct", "wrong", "spam"):
        raise ValueError(f"unknown status {status!r}")
    if latency_ms < 0:
        raise ValueError("latency must be >= 0")
    if status == "spam":
        return 0
    total = BASE if counts else 0
    if status == "correct":
        total += CORRECT + max(0, SPEED_MAX - latency_ms // 100)
    return total


def fmt_points(hundredths: int) -> str:
    return f"{hundredths / 100:.1f}".replace(".", ",")


# ------------------------------------------------------------------ rows
@dataclass(frozen=True)
class Fallacy:
    id: int
    name: str
    category: str
    description: str
    wikipedia_url: str | None
    source: str
    enabled: bool


@dataclass(frozen=True)
class Mark:
    id: UUID
    session_id: UUID
    user_id: UUID
    kind: str
    start_event_id: int
    start_offset: int
    end_event_id: int
    end_offset: int
    quote: str
    fallacy_id: int
    latency_ms: int
    counts: bool
    status: str
    created_at: datetime
    reviewed_at: datetime | None


_MARK_COLS = ("id, session_id, user_id, kind, start_event_id, start_offset, end_event_id, end_offset, quote, "
              "fallacy_id, latency_ms, counts, status, created_at, reviewed_at")


def points_of(m: Mark) -> int:
    return points(m.status, m.latency_ms, m.counts)


def get_mark(conn: psycopg.Connection, mark_id: UUID) -> Mark | None:
    with conn.cursor(row_factory=class_row(Mark)) as cur:
        cur.execute(f"SELECT {_MARK_COLS} FROM fallacy_marks WHERE id = %s", (mark_id,))
        return cur.fetchone()


def user_marks(conn: psycopg.Connection, user_id: UUID) -> list[Mark]:
    with conn.cursor(row_factory=class_row(Mark)) as cur:
        cur.execute(f"SELECT {_MARK_COLS} FROM fallacy_marks WHERE user_id = %s ORDER BY created_at DESC, id", (user_id,))
        return cur.fetchall()


# ------------------------------------------------------------------ the fallacy list
def list_fallacies(conn: psycopg.Connection, enabled_only: bool = True) -> list[Fallacy]:
    where = "WHERE enabled" if enabled_only else ""
    with conn.cursor(row_factory=class_row(Fallacy)) as cur:
        cur.execute(f"SELECT id, name, category, description, wikipedia_url, source, enabled FROM fallacies {where} "
                    "ORDER BY id")
        return cur.fetchall()


def load_fallacy_seed(conn: psycopg.Connection, path: Path = SEED_PATH) -> dict:
    """Apply the Wikipedia seed ONLY when the table is empty (the DB is the truth afterwards: deleted rows stay deleted)."""
    ensure_autocommit(conn)
    if not Path(path).exists():
        return {"skipped": f"no seed file at {path}"}
    sql = Path(path).read_text(encoding="utf-8")
    with conn.transaction():
        conn.execute("LOCK TABLE fallacies IN EXCLUSIVE MODE")
        n = conn.execute("SELECT count(*) FROM fallacies").fetchone()[0]
        if n:
            return {"skipped": f"fallacies table already has {n} rows"}
        conn.execute(sql)
        return {"inserted": conn.execute("SELECT count(*) FROM fallacies").fetchone()[0]}


# ------------------------------------------------------------------ feed rows
def add_event(conn: psycopg.Connection, session_id: UUID, kind: str, source_key: str, text: str,
              author: str | None = None, fed_at: datetime | None = None) -> int | None:
    """Show one line in a pane. Returns its id, or None when `source_key` was already fed (idempotent)."""
    ensure_autocommit(conn)
    if kind not in KINDS:
        raise ValueError(f"unknown pane {kind!r}")
    text = text.strip()
    if not text:
        raise ValueError("empty text")
    row = conn.execute(
        "INSERT INTO stream_events (session_id, kind, source_key, author, text, fed_at) "
        "VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now())) "
        "ON CONFLICT (session_id, kind, source_key) DO NOTHING RETURNING id",
        (session_id, kind, source_key, author, text, fed_at)).fetchone()
    return row[0] if row else None


# ------------------------------------------------------------------ selection -> quote (the client's text is never trusted)
def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _slice(text: str, a: int, b: int) -> str:
    """text[a:b] in UTF-16 code units (JS offsets); BadSelection when a bound falls inside a surrogate pair."""
    raw = text.encode("utf-16-le")
    try:
        return raw[2 * a:2 * b].decode("utf-16-le")
    except UnicodeDecodeError as e:
        raise BadSelection("offset inside a character") from e


def build_quote(conn: psycopg.Connection, session_id: UUID, kind: str, start_event_id: int, start_offset: int,
                end_event_id: int, end_offset: int) -> str:
    if kind not in KINDS:
        raise BadSelection("unknown pane")
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (start_event_id, start_offset, end_event_id, end_offset)):
        raise BadSelection("offsets must be integers")
    if start_offset < 0 or end_offset < 1:
        raise BadSelection("negative or empty offsets")        # reversed ids simply match no rows below
    if end_event_id > 2**62 or start_event_id < 0:
        raise BadSelection("unknown line")
    rows = conn.execute(
        "SELECT id, text FROM stream_events WHERE session_id = %s AND kind = %s AND id BETWEEN %s AND %s "
        "ORDER BY id LIMIT %s", (session_id, kind, start_event_id, end_event_id, MAX_LINES + 1)).fetchall()
    if len(rows) > MAX_LINES:
        raise SelectionTooLong(f"more than {MAX_LINES} lines")
    if not rows or rows[0][0] != start_event_id or rows[-1][0] != end_event_id:
        raise BadSelection("unknown line")
    if len(rows) == 1:
        text = rows[0][1]
        if not (start_offset < end_offset <= _units(text)):
            raise BadSelection("offsets outside the line")
        pieces = [_slice(text, start_offset, end_offset)]
    else:
        first, last = rows[0][1], rows[-1][1]
        if start_offset >= _units(first) or end_offset > _units(last):
            raise BadSelection("offsets outside the line")
        pieces = [_slice(first, start_offset, _units(first)), *(r[1] for r in rows[1:-1]), _slice(last, 0, end_offset)]
    quote = re.sub(r"\s+", " ", " ".join(pieces)).strip()
    if not quote:
        raise BadSelection("nothing selected")
    if len(quote) > MAX_QUOTE:
        raise SelectionTooLong(f"more than {MAX_QUOTE} characters")
    return quote


# ------------------------------------------------------------------ pairs
def submit_mark(conn: psycopg.Connection, user_id: UUID, kind: str, start_event_id: int, start_offset: int,
                end_event_id: int, end_offset: int, fallacy_id: int) -> Mark:
    """A player flags (passage, fallacy). Server-side: quote, latency, rate limit, duplicate and cap checks."""
    ensure_autocommit(conn)
    row = conn.execute("SELECT u.session_id, s.status, s.game_type FROM users u JOIN game_sessions s ON s.id = u.session_id "
                       "WHERE u.id = %s", (user_id,)).fetchone()
    if row is None or row[2] != "falacias":
        raise NotAFalaciasPlayer("not a player of a falacias session")
    session_id = row[0]
    if row[1] != "active":
        raise SessionNotActive("the session has ended")
    if conn.execute("SELECT 1 FROM fallacies WHERE id = %s AND enabled", (fallacy_id,)).fetchone() is None:
        raise UnknownFallacy("unknown or disabled fallacy")
    quote = build_quote(conn, session_id, kind, start_event_id, start_offset, end_event_id, end_offset)
    try:
        with conn.transaction():
            # one player's submits are serialised: the rate limit and the cap are count-then-insert
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"falacias-mark:{user_id}",))
            status = conn.execute("SELECT status FROM game_sessions WHERE id = %s FOR SHARE", (session_id,)).fetchone()
            if status is None or status[0] != "active":
                raise SessionNotActive("the session has ended")
            if RATE_LIMIT_S > 0 and conn.execute(
                    "SELECT 1 FROM fallacy_marks WHERE user_id = %s AND created_at > clock_timestamp() - make_interval(secs => %s)",
                    (user_id, RATE_LIMIT_S)).fetchone():
                raise RateLimited(f"wait {RATE_LIMIT_S} s between pairs")
            pending = conn.execute("SELECT count(*) FROM fallacy_marks WHERE user_id = %s AND status = 'pending' AND counts",
                                   (user_id,)).fetchone()[0]
            with conn.cursor(row_factory=class_row(Mark)) as cur:
                cur.execute(
                    "INSERT INTO fallacy_marks (session_id, user_id, kind, start_event_id, start_offset, end_event_id, "
                    "end_offset, quote, fallacy_id, latency_ms, counts, created_at) "
                    "SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "       GREATEST(0, floor(extract(epoch FROM clock_timestamp() - e.fed_at) * 1000))::int, %s, clock_timestamp() "
                    "FROM stream_events e WHERE e.id = %s "
                    f"RETURNING {_MARK_COLS}",
                    (session_id, user_id, kind, start_event_id, start_offset, end_event_id, end_offset, quote, fallacy_id,
                     pending < CAP_UNREVIEWED, end_event_id))
                return cur.fetchone()
    except errors.UniqueViolation as e:
        if e.diag.constraint_name == "fallacy_marks_unique_pair":
            raise DuplicateMark("you already flagged this passage with this fallacy") from e
        raise


# ------------------------------------------------------------------ admin review
def _check_verdict(verdict: str) -> None:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")


def _require_active(conn: psycopg.Connection, session_id: UUID) -> None:
    row = conn.execute("SELECT status FROM game_sessions WHERE id = %s FOR SHARE", (session_id,)).fetchone()
    if row is None or row[0] != "active":
        raise SessionNotActive("the session has ended")           # no late reviews


def review_mark(conn: psycopg.Connection, session_id: UUID, mark_id: UUID, verdict: str) -> Mark:
    """Correto / Errado / Spam. A verdict can be corrected while the session is active."""
    _check_verdict(verdict)
    ensure_autocommit(conn)
    with conn.transaction():
        _require_active(conn, session_id)
        with conn.cursor(row_factory=class_row(Mark)) as cur:
            cur.execute(f"UPDATE fallacy_marks SET status = %s, reviewed_at = now() WHERE id = %s AND session_id = %s "
                        f"RETURNING {_MARK_COLS}", (verdict, mark_id, session_id))
            m = cur.fetchone()
    if m is None:
        raise MarkNotFound("no such pair in this session")
    return m


def review_same_pairs(conn: psycopg.Connection, session_id: UUID, mark_id: UUID, verdict: str) -> int:
    """Give the verdict to every still PENDING pair identical to this one (same passage + same fallacy, any player)."""
    _check_verdict(verdict)
    ensure_autocommit(conn)
    with conn.transaction():
        _require_active(conn, session_id)
        n = conn.execute(
            "UPDATE fallacy_marks m SET status = %s, reviewed_at = now() FROM fallacy_marks ref "
            "WHERE ref.id = %s AND ref.session_id = %s AND m.session_id = ref.session_id AND m.kind = ref.kind "
            "AND m.start_event_id = ref.start_event_id AND m.start_offset = ref.start_offset "
            "AND m.end_event_id = ref.end_event_id AND m.end_offset = ref.end_offset "
            "AND m.fallacy_id = ref.fallacy_id AND m.status = 'pending'", (verdict, mark_id, session_id)).rowcount
        if n == 0 and conn.execute("SELECT 1 FROM fallacy_marks WHERE id = %s AND session_id = %s",
                                   (mark_id, session_id)).fetchone() is None:
            raise MarkNotFound("no such pair in this session")
        return n


# ------------------------------------------------------------------ admin queue (read model)
CONTEXT_LINES = 3


@dataclass(frozen=True)
class QueueItem:
    mark: Mark
    name: str                 # who sent it
    fallacy: str
    same: int                 # PENDING pairs identical to this one (same passage + fallacy, any player), itself included
    before: list[str]         # up to CONTEXT_LINES lines of the same pane before the selection
    selected: list[str]       # the full text of the selected lines
    after: list[str]
    created_us: int           # cursor for the live queue: microseconds since the epoch


def queue_items(conn: psycopg.Connection, session_id: UUID, after_us: int = 0, mark_id: UUID | None = None,
                limit: int = 200) -> list[QueueItem]:
    """PENDING pairs oldest first (FIFO) whose created_us > after_us; or exactly one pair, whatever its status, when
    `mark_id` is given (used to redraw an item after a verdict)."""
    cols = ", ".join("m." + c.strip() for c in _MARK_COLS.split(","))
    where, params = ("m.id = %s", (mark_id,)) if mark_id else ("m.status = 'pending' AND created_us > %s", (after_us,))
    rows = conn.execute(
        f"SELECT * FROM (SELECT {cols}, u.name, f.name AS fname, "
        "(SELECT count(*) FROM fallacy_marks x WHERE x.session_id = m.session_id AND x.kind = m.kind "
        " AND x.start_event_id = m.start_event_id AND x.start_offset = m.start_offset AND x.end_event_id = m.end_event_id "
        " AND x.end_offset = m.end_offset AND x.fallacy_id = m.fallacy_id AND x.status = 'pending') AS same, "
        "floor(extract(epoch FROM m.created_at) * 1000000)::bigint AS created_us "
        "FROM fallacy_marks m JOIN users u ON u.id = m.user_id JOIN fallacies f ON f.id = m.fallacy_id "
        f"WHERE m.session_id = %s) q WHERE {where.replace('m.id', 'q.id').replace('m.status', 'q.status')} "
        "ORDER BY q.created_at, q.id LIMIT %s", (session_id, *params, limit)).fetchall()
    n = len(_MARK_COLS.split(","))
    items = []
    for r in rows:
        m = Mark(*r[:n])
        names = r[n:]
        around = conn.execute(
            "(SELECT 'b', id, text FROM stream_events WHERE session_id = %s AND kind = %s AND id < %s ORDER BY id DESC LIMIT %s) "
            "UNION ALL (SELECT 's', id, text FROM stream_events WHERE session_id = %s AND kind = %s AND id BETWEEN %s AND %s) "
            "UNION ALL (SELECT 'a', id, text FROM stream_events WHERE session_id = %s AND kind = %s AND id > %s ORDER BY id LIMIT %s) "
            "ORDER BY 2",
            (m.session_id, m.kind, m.start_event_id, CONTEXT_LINES, m.session_id, m.kind, m.start_event_id, m.end_event_id,
             m.session_id, m.kind, m.end_event_id, CONTEXT_LINES)).fetchall()
        items.append(QueueItem(m, names[0], names[1], names[2], [t for k, _, t in around if k == "b"],
                               [t for k, _, t in around if k == "s"], [t for k, _, t in around if k == "a"], names[3]))
    return items


def identical_pending_ids(conn: psycopg.Connection, session_id: UUID, mark_id: UUID) -> list[UUID]:
    """Ids of the PENDING pairs identical to this one (same passage + fallacy, any player), oldest first."""
    return [r[0] for r in conn.execute(
        "SELECT m.id FROM fallacy_marks m JOIN fallacy_marks ref ON ref.id = %s AND ref.session_id = %s "
        "WHERE m.session_id = ref.session_id AND m.kind = ref.kind AND m.start_event_id = ref.start_event_id "
        "AND m.start_offset = ref.start_offset AND m.end_event_id = ref.end_event_id AND m.end_offset = ref.end_offset "
        "AND m.fallacy_id = ref.fallacy_id AND m.status = 'pending' ORDER BY m.created_at, m.id", (mark_id, session_id))]


def pending_count(conn: psycopg.Connection, session_id: UUID) -> int:
    return conn.execute("SELECT count(*) FROM fallacy_marks WHERE session_id = %s AND status = 'pending'",
                        (session_id,)).fetchone()[0]


# ------------------------------------------------------------------ leaderboard
@dataclass(frozen=True)
class LeaderRow:
    rank: int
    user_id: UUID
    name: str
    points: int                       # hundredths
    pairs: int
    correct: int
    wrong: int
    spam: int
    pending: int
    last_submit_at: datetime | None


def leaderboard(conn: psycopg.Connection, session_id: UUID) -> list[LeaderRow]:
    """Order: points, then Correto pairs, then who submitted their last pair first, then join order."""
    users = conn.execute("SELECT u.id, u.name FROM users u JOIN game_sessions g ON g.id = u.session_id "
                         "WHERE u.session_id = %s AND g.game_type = 'falacias' ORDER BY u.created_at, u.id",
                         (session_id,)).fetchall()
    stats: dict[UUID, dict] = {u: {"points": 0, "pairs": 0, "correct": 0, "wrong": 0, "spam": 0, "pending": 0, "last": None}
                               for u, _ in users}
    for uid, status, latency, counts, created in conn.execute(
            "SELECT user_id, status, latency_ms, counts, created_at FROM fallacy_marks WHERE session_id = %s", (session_id,)):
        st = stats[uid]
        st["points"] += points(status, latency, counts)
        st["pairs"] += 1
        st[status] += 1
        st["last"] = created if st["last"] is None or created > st["last"] else st["last"]
    return _rank(users, stats)


def _rank(users: list, stats: dict) -> list[LeaderRow]:
    def key(i: int):
        s = stats[users[i][0]]
        last = s["last"]
        return (-s["points"], -s["correct"], last is None, last.timestamp() if last else 0.0, i)

    out = []
    for rank, i in enumerate(sorted(range(len(users)), key=key), start=1):
        uid, name = users[i]
        s = stats[uid]
        out.append(LeaderRow(rank, uid, name, s["points"], s["pairs"], s["correct"], s["wrong"], s["spam"], s["pending"],
                             s["last"]))
    return out
