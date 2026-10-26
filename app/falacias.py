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
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import errors
from psycopg.rows import class_row

from app.db import ensure_autocommit
from app.sessions import SessionError

KINDS = ("transcript", "chat")      # panes a player can SELECT from
SPOKEN = "spoken"                    # typed by the player (what was said on the stream); never shown in the panes
MAX_SPOKEN = 300
MAX_NOTE = 280                       # the free-form message of the slide panel (same limit as a host message)
VERDICTS = ("correct", "wrong", "spam")
BASE = 500
CORRECT = 500
SPEED_MAX = 600                  # hundredths: 0,1 x 60 s
SPEED_WINDOW_S = 60
SPOKEN_LATENCY_MS = SPEED_WINDOW_S * 1000     # a typed pair has no line to time from: no speed bonus
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


class SpokenTooLong(FalaciasError):
    code = "spoken_too_long"


class NoteTooLong(FalaciasError):
    code = "note_too_long"


class NeedFallacy(FalaciasError):
    """A passage is selected but no fallacy was chosen."""
    code = "need_fallacy"


class NeedText(FalaciasError):
    """A fallacy is chosen but there is neither a selected passage nor a typed phrase."""
    code = "need_text"


class NothingToSend(FalaciasError):
    code = "empty"


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
    note: str = ""


_MARK_COLS = ("id, session_id, user_id, kind, start_event_id, start_offset, end_event_id, end_offset, quote, "
              "fallacy_id, latency_ms, counts, status, created_at, reviewed_at, note")


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


# The 20 fallacies players can pick: the most common ones in debates like this channel's (religion x atheism). EXACT names
# from the pinned pt.wikipedia seed (tests check every one exists there). The other seeded rows stay in the table, disabled;
# manual rows are never touched by the sync.
TOP_FALLACIES: tuple[str, ...] = (
    "Ad hominem",
    "Falácia do espantalho",
    "Falsa dicotomia",
    "Argumentum ad verecundiam (Apelo à autoridade)",
    "Argumentum ad populum (apelo à desinformação, apelo à maioria)",
    "Bola de neve (Slippery slope)",
    "Petitio principii",
    "Pista falsa",
    "Tu quoque (Apelo à hipocrisia)",
    "Falsa analogia",
    "Generalização precipitada",
    'Post hoc ergo propter hoc (Latim para "após isso, portanto por causa disso"; sequência temporal não implica causalidade)',
    "Argumentum ad ignorantiam (Apelo à ignorância)",
    "Falácia do escocês de verdade",
    "Equívoco",
    "Envenenando o poço",
    "Apelo à emoção",
    "Argumentum ad consequentiam (Apelo à consequências)",
    "Argumentum ad antiquitatem",
    "Falácia divina (apelo à incredulidade)",
    "Argumento ad logicam (falácia da falácia)",     # added on request: the list has 21
)


FALLACY_NAMES: dict[str, str] = {   # pt.wikipedia name (the key the seed/sync match on) -> the name players and the admin see
    "Ad hominem":
        "Ataque pessoal (Personal Attack - Ad hominem)",
    "Falácia do espantalho":
        "Falácia do espantalho (Straw Man)",
    "Falsa dicotomia":
        "Falsa dicotomia (False Dilemma)",
    "Argumentum ad verecundiam (Apelo à autoridade)":
        "Apelo à autoridade (Appeal to Authority - Argumentum ad verecundiam)",
    "Argumentum ad populum (apelo à desinformação, apelo à maioria)":
        "Apelo à maioria (Appeal to the Majority - Argumentum ad populum)",
    "Bola de neve (Slippery slope)":
        "Ladeira escorregadia (Slippery Slope)",
    "Petitio principii":
        "Petição de princípio (Begging the Question - Petitio principii)",
    "Pista falsa":
        "Pista Falsa (Red Herring)",
    "Tu quoque (Apelo à hipocrisia)":
        "Apelo à hipocrisia (Appeal to Hypocrisy - Tu quoque)",
    "Falsa analogia":
        "Falsa analogia (False Analogy)",
    "Generalização precipitada":
        "Generalização precipitada (Hasty Generalization)",
    'Post hoc ergo propter hoc (Latim para "após isso, portanto por causa disso"; sequência temporal não implica causalidade)':
        "Falácia post hoc (Post Hoc Fallacy - Post hoc ergo propter hoc)",
    "Argumentum ad ignorantiam (Apelo à ignorância)":
        "Apelo à ignorância (Argument from Ignorance - Ad ignorantiam)",
    "Falácia do escocês de verdade":
        "Falácia do escocês de verdade (No True Scotsman)",
    "Equívoco":
        "Equívoco (Equivocation)",
    "Envenenando o poço":
        "Envenenando o poço (Poisoning the Well)",
    "Apelo à emoção":
        "Apelo à emoção (Appeal to Emotion)",
    "Argumentum ad consequentiam (Apelo à consequências)":
        "Apelo às consequências (Appeal to Consequences - Ad consequentiam)",
    "Argumentum ad antiquitatem":
        "Apelo à tradição (Appeal to Tradition - Ad antiquitatem)",
    "Falácia divina (apelo à incredulidade)":
        "Apelo à incredulidade (Argument from Incredulity)",
    "Argumento ad logicam (falácia da falácia)":
        "Falácia da falácia (Fallacy Fallacy - Argumentum ad logicam)",
}


def sync_top_fallacies(conn: psycopg.Connection) -> dict:
    """Bring the Wikipedia rows to the curated state: rename them to their display names (FALLACY_NAMES; same row id, so marks keep
    pointing at it; skipped when the new name is taken), then enable only the curated ones. Idempotent: the result counts the rows
    that CHANGED; `missing` lists curated display names not found in the table."""
    ensure_autocommit(conn)
    top = list(FALLACY_NAMES.values())
    with conn.transaction():
        renamed = sum(conn.execute("UPDATE fallacies SET name = %s WHERE source = 'wikipedia' AND name = %s "
                                   "AND NOT EXISTS (SELECT 1 FROM fallacies WHERE name = %s)", (new, old, new)).rowcount
                      for old, new in FALLACY_NAMES.items())
        on = conn.execute("UPDATE fallacies SET enabled = true WHERE source = 'wikipedia' AND name = ANY(%s) AND NOT enabled",
                          (top,)).rowcount
        off = conn.execute("UPDATE fallacies SET enabled = false WHERE source = 'wikipedia' AND NOT (name = ANY(%s)) AND enabled",
                           (top,)).rowcount
        present = {n for (n,) in conn.execute("SELECT name FROM fallacies WHERE name = ANY(%s)", (top,))}
    return {"renamed": renamed, "enabled": on, "disabled": off, "missing": [n for n in top if n not in present]}


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
def _player_session(conn: psycopg.Connection, user_id: UUID, fallacy_id: int) -> UUID:
    """The checks every submit starts with: a player of an ACTIVE falacias session, and an enabled fallacy."""
    row = conn.execute("SELECT u.session_id, s.status, s.game_type FROM users u JOIN game_sessions s ON s.id = u.session_id "
                       "WHERE u.id = %s", (user_id,)).fetchone()
    if row is None or row[2] != "falacias":
        raise NotAFalaciasPlayer("not a player of a falacias session")
    if row[1] != "active":
        raise SessionNotActive("the session has ended")
    if conn.execute("SELECT 1 FROM fallacies WHERE id = %s AND enabled", (fallacy_id,)).fetchone() is None:
        raise UnknownFallacy("unknown or disabled fallacy")
    return row[0]


def _guarded_insert(conn: psycopg.Connection, session_id: UUID, user_id: UUID, insert) -> Mark:
    """Run `insert(counts) -> Mark` under the per-player lock, with the session still active, the rate limit applied and the
    cap computed. Shared by selected and typed pairs: one player's submits are serialised (count-then-insert)."""
    try:
        with conn.transaction():
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
            return insert(pending < CAP_UNREVIEWED)
    except errors.UniqueViolation as e:
        if e.diag.constraint_name == "fallacy_marks_unique_pair":
            raise DuplicateMark("you already flagged this passage with this fallacy") from e
        raise


def normalize_note(raw: str | None, limit: int = MAX_NOTE) -> str:
    """The free-form message: NFC, control characters dropped, whitespace collapsed. May be empty; at most `limit` (MAX_NOTE) characters."""
    text = unicodedata.normalize("NFC", raw or "")
    text = "".join(" " if c.isspace() else c for c in text if c.isspace() or unicodedata.category(c) not in ("Cc", "Cs"))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        raise NoteTooLong(f"more than {limit} characters")
    return text


def submit_mark(conn: psycopg.Connection, user_id: UUID, kind: str, start_event_id: int, start_offset: int,
                end_event_id: int, end_offset: int, fallacy_id: int, note: str = "") -> Mark:
    """A player flags (passage, fallacy), optionally with a free-form message for the admin. Server-side: quote, latency,
    rate limit, duplicate and cap checks."""
    ensure_autocommit(conn)
    note = normalize_note(note)
    session_id = _player_session(conn, user_id, fallacy_id)
    quote = build_quote(conn, session_id, kind, start_event_id, start_offset, end_event_id, end_offset)

    def insert(counts: bool) -> Mark:
        with conn.cursor(row_factory=class_row(Mark)) as cur:
            cur.execute(
                "INSERT INTO fallacy_marks (session_id, user_id, kind, start_event_id, start_offset, end_event_id, "
                "end_offset, quote, fallacy_id, latency_ms, counts, note, created_at) "
                "SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "       GREATEST(0, floor(extract(epoch FROM clock_timestamp() - e.fed_at) * 1000))::int, %s, %s, clock_timestamp() "
                "FROM stream_events e WHERE e.id = %s "
                f"RETURNING {_MARK_COLS}",
                (session_id, user_id, kind, start_event_id, start_offset, end_event_id, end_offset, quote, fallacy_id,
                 counts, note, end_event_id))
            return cur.fetchone()
    return _guarded_insert(conn, session_id, user_id, insert)


def normalize_spoken(raw: str | None) -> str:
    """What the player typed: NFC, control characters dropped, whitespace collapsed. 1..MAX_SPOKEN characters AFTER cleaning."""
    text = unicodedata.normalize("NFC", raw or "")
    text = "".join(" " if c.isspace() else c for c in text if c.isspace() or unicodedata.category(c) not in ("Cc", "Cs"))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise BadSelection("nothing typed")
    if len(text) > MAX_SPOKEN:
        raise SpokenTooLong(f"more than {MAX_SPOKEN} characters")
    return text


def submit_spoken(conn: psycopg.Connection, user_id: UUID, text: str, fallacy_id: int) -> Mark:
    """A player TYPES the passage that was said on the stream and flags it. The pair is its own one-line event of kind 'spoken'
    (author = the player; never shown in the panes). Same rate limit / cap / duplicate rules as selected pairs; no speed bonus
    (latency is fixed at SPEED_WINDOW_S); a duplicate is the same text (case-insensitive) + fallacy from the same player."""
    ensure_autocommit(conn)
    quote = normalize_spoken(text)
    session_id = _player_session(conn, user_id, fallacy_id)

    def insert(counts: bool) -> Mark:
        if conn.execute("SELECT 1 FROM fallacy_marks WHERE user_id = %s AND kind = 'spoken' AND fallacy_id = %s "
                        "AND lower(quote) = lower(%s)", (user_id, fallacy_id, quote)).fetchone():
            raise DuplicateMark("you already flagged this passage with this fallacy")
        eid = conn.execute(
            "INSERT INTO stream_events (session_id, kind, source_key, author, text, fed_at) "
            "SELECT %s, 'spoken', %s, u.name, %s, clock_timestamp() FROM users u WHERE u.id = %s RETURNING id",
            (session_id, str(uuid.uuid4()), quote, user_id)).fetchone()[0]
        with conn.cursor(row_factory=class_row(Mark)) as cur:
            cur.execute(
                "INSERT INTO fallacy_marks (session_id, user_id, kind, start_event_id, start_offset, end_event_id, end_offset, "
                "quote, fallacy_id, latency_ms, counts, created_at) "
                "VALUES (%s, %s, 'spoken', %s, 0, %s, %s, %s, %s, %s, %s, clock_timestamp()) "
                f"RETURNING {_MARK_COLS}",
                (session_id, user_id, eid, eid, _units(quote), quote, fallacy_id, SPOKEN_LATENCY_MS, counts))
            return cur.fetchone()
    return _guarded_insert(conn, session_id, user_id, insert)


# ------------------------------------------------------------------ admin review
def _same(a: str, b: str) -> str:
    """SQL: row `a` and row `b` of fallacy_marks are the SAME pair for grouped verdicts: same fallacy and, for selected pairs, the
    same passage; for typed pairs the same text (case-insensitive), because every typed pair is its own event."""
    return (f"({a}.session_id = {b}.session_id AND {a}.kind = {b}.kind AND {a}.fallacy_id = {b}.fallacy_id AND "
            f"(({a}.kind <> 'spoken' AND {a}.start_event_id = {b}.start_event_id AND {a}.start_offset = {b}.start_offset "
            f"AND {a}.end_event_id = {b}.end_event_id AND {a}.end_offset = {b}.end_offset) "
            f"OR ({a}.kind = 'spoken' AND lower({a}.quote) = lower({b}.quote))))")


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
            f"WHERE ref.id = %s AND ref.session_id = %s AND {_same('m', 'ref')} AND m.status = 'pending'",
            (verdict, mark_id, session_id)).rowcount
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
        f"(SELECT count(*) FROM fallacy_marks x WHERE {_same('x', 'm')} AND x.status = 'pending') AS same, "
        "floor(extract(epoch FROM m.created_at) * 1000000)::bigint AS created_us "
        "FROM fallacy_marks m JOIN users u ON u.id = m.user_id JOIN fallacies f ON f.id = m.fallacy_id "
        f"WHERE m.session_id = %s) q WHERE {where.replace('m.id', 'q.id').replace('m.status', 'q.status')} "
        "ORDER BY q.created_at, q.id LIMIT %s", (session_id, *params, limit)).fetchall()
    n = len(_MARK_COLS.split(","))
    items = []
    for r in rows:
        m = Mark(*r[:n])
        names = r[n:]
        if m.kind == SPOKEN:                      # a typed quote has no surrounding lines (its neighbours are other players' typing)
            items.append(QueueItem(m, names[0], names[1], names[2], [], [m.quote], [], names[3]))
            continue
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
        f"WHERE {_same('m', 'ref')} AND m.status = 'pending' ORDER BY m.created_at, m.id", (mark_id, session_id))]


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


@dataclass(frozen=True)
class FinishResult:
    session_id: UUID
    winner: LeaderRow | None          # None: nobody scored
    standings: list[LeaderRow]


def finish_session(conn: psycopg.Connection, session_id: UUID) -> FinishResult | None:
    """Admin action: end an ACTIVE Falácias session and record the winner (most points; ties: more Correto pairs, then who
    submitted their last pair first). Nobody with points > 0 -> no winner. Returns None when the session is unknown, not a
    Falácias session or already ended (double click / two admins never overwrite the winner). Reviews are frozen afterwards
    (review_mark requires an active session); pairs still pending keep only their base points."""
    ensure_autocommit(conn)
    with conn.transaction():
        locked = conn.execute("SELECT 1 FROM game_sessions WHERE id = %s AND status = 'active' AND game_type = 'falacias' "
                              "FOR UPDATE", (session_id,)).fetchone()
        if locked is None:
            return None
        standings = leaderboard(conn, session_id)
        winner = standings[0] if standings and standings[0].points > 0 else None
        conn.execute("UPDATE game_sessions SET status = 'ended', ended_at = now(), winner_user_id = %s WHERE id = %s",
                     (winner.user_id if winner else None, session_id))
    return FinishResult(session_id, winner, standings)


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
