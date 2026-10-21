"""Phrase management (admin CRUD) and the score learned from real games.

The DB is the source of truth for phrases (corpus/phrases.csv is only the initial seed).

Score model
  * `prior_score`  - what the corpus (or the admin, by hand) says about the phrase.
  * `dealt/marked` - over FINISHED games: cards that held the phrase / cards that marked it.
  * `score`        - used for dealing (weight = 1 + score). On game finish, for every phrase that was on
                     a card of that game:  score = (K*prior + dealt*obs) / (K + dealt),
                     obs = BASE + SPAN * marked/dealt   (rate 0..1 mapped onto the corpus' ~1..11 range).
                     K = PRIOR_STRENGTH acts like "K imaginary deals at the prior".
  * A phrase whose prior is MANUAL_SCORE (99) is frozen: counters grow, the score does not move until
    the admin edits the score by hand (which sets prior = score).
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass

import psycopg
from psycopg import errors
from psycopg.rows import dict_row

from app.cards import PHRASE_CELLS
from app.db import ensure_autocommit
from corpus.extract import MANUAL_MAX_WORDS, MANUAL_MIN_WORDS, has_greeting

MANUAL_SCORE = 99.0
MAX_SCORE = 99.0
MAX_TEXT_LEN = 100
MIN_ENABLED = PHRASE_CELLS          # fewer enabled phrases than cells -> no card can be dealt
PRIOR_STRENGTH = 20.0
RATE_BASE, RATE_SPAN = 1.0, 10.0
SOURCES = ("transcript", "chat", "transcript+chat", "manual")
PER_PAGE = 50

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class PhraseError(Exception):
    code = "error"


class InvalidPhrase(PhraseError):
    code = "invalid_phrase"


class InvalidScore(PhraseError):
    code = "invalid_score"


class DuplicatePhrase(PhraseError):
    code = "duplicate_phrase"


class NotFound(PhraseError):
    code = "not_found"


class TooFewEnabled(PhraseError):
    code = "too_few_enabled"


@dataclass(frozen=True)
class PhraseRow:
    id: int
    text: str
    source: str
    score: float
    prior_score: float
    enabled: bool
    dealt: int
    marked: int

    @property
    def rate(self) -> float | None:
        return self.marked / self.dealt if self.dealt else None

    @property
    def frozen(self) -> bool:
        return self.prior_score >= MANUAL_SCORE


_COLS = "id, text, source, score::float8 AS score, prior_score::float8 AS prior_score, enabled, dealt, marked"


# ------------------------------------------------------------------ pure helpers
def normalize_text(raw: str) -> str:
    """NFC, whitespace collapsed, lowercase (the corpus is lowercase, so duplicates collide)."""
    return " ".join(unicodedata.normalize("NFC", raw or "").split()).lower()


def validate_text(raw: str) -> str:
    """Rules for anything the admin types (add AND edit), whatever the phrase's source: 1-10 words."""
    text = normalize_text(raw)
    if _CONTROL.search(text) or not text or len(text) > MAX_TEXT_LEN:   # tabs/newlines were collapsed to spaces
        raise InvalidPhrase("empty, too long or contains control characters")
    n = len(text.split())
    if not MANUAL_MIN_WORDS <= n <= MANUAL_MAX_WORDS:
        raise InvalidPhrase(f"{MANUAL_MIN_WORDS}-{MANUAL_MAX_WORDS} words required (got {n})")
    if has_greeting(text):
        raise InvalidPhrase("greetings are not allowed")
    return text


def parse_score(raw: object) -> float:
    try:
        value = float(str(raw).strip().replace(",", "."))
    except ValueError as e:
        raise InvalidScore(f"not a number: {raw!r}") from e
    if not 0.0 <= value <= MAX_SCORE:                    # NaN fails this comparison too
        raise InvalidScore(f"score must be between 0 and {MAX_SCORE:g}")
    return round(value, 3)


def blend_score(prior: float, dealt: int, marked: int) -> float:
    """The learned score. Python twin of the SQL in `apply_session_stats` (tests keep them equal)."""
    if dealt <= 0:
        return prior
    obs = RATE_BASE + RATE_SPAN * marked / dealt
    return round((PRIOR_STRENGTH * prior + dealt * obs) / (PRIOR_STRENGTH + dealt), 3)


# ------------------------------------------------------------------ queries
def _lock_enabled_count(conn: psycopg.Connection) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('livegames.phrases.enabled'))")


def enabled_count(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT count(*) FROM phrases WHERE enabled").fetchone()[0]  # type: ignore[index]


def get(conn: psycopg.Connection, phrase_id: int) -> PhraseRow | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {_COLS} FROM phrases WHERE id = %s", (phrase_id,))  # type: ignore[arg-type]
        r = cur.fetchone()
    return PhraseRow(**r) if r else None


_SORTS = {
    "score": "score DESC, text",
    "text": "text",
    "dealt": "dealt DESC, text",
    "marked": "marked DESC, text",
    "rate": "(CASE WHEN dealt = 0 THEN -1 ELSE marked::float8 / dealt END) DESC, text",
    "newest": "id DESC",
}


def search(conn: psycopg.Connection, q: str = "", source: str = "", enabled: str = "",
           sort: str = "score", page: int = 1, per_page: int = PER_PAGE) -> tuple[list[PhraseRow], int]:
    """Filtered, sorted, paginated list -> (rows, total matching). Unknown filter/sort values are ignored."""
    where, args = [], []
    q = normalize_text(q)
    if q:
        where.append("text LIKE %s ESCAPE '\\'")
        args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if source in SOURCES:
        where.append("source = %s")
        args.append(source)
    if enabled in ("yes", "no"):
        where.append("enabled = %s")
        args.append(enabled == "yes")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = _SORTS.get(sort, _SORTS["score"])
    total = conn.execute(f"SELECT count(*) FROM phrases {clause}", args).fetchone()[0]  # type: ignore[index,call-overload]
    page = max(1, int(page))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {_COLS} FROM phrases {clause} ORDER BY {order} LIMIT %s OFFSET %s",  # type: ignore[call-overload]
                    [*args, per_page, (page - 1) * per_page])
        return [PhraseRow(**r) for r in cur.fetchall()], total


# ------------------------------------------------------------------ commands
def create(conn: psycopg.Connection, raw_text: str) -> PhraseRow:
    """Add a MANUAL phrase with the maximum score (99) until the admin edits it."""
    ensure_autocommit(conn)
    text = validate_text(raw_text)
    try:
        row = conn.execute(
            "INSERT INTO phrases (text, source, score, prior_score, enabled) "
            "VALUES (%s, 'manual', %s, %s, true) RETURNING id", (text, MANUAL_SCORE, MANUAL_SCORE)).fetchone()
    except errors.UniqueViolation as e:
        raise DuplicatePhrase(text) from e
    created = get(conn, row[0])  # type: ignore[index]
    assert created is not None
    return created


def update(conn: psycopg.Connection, phrase_id: int, *, text: str | None = None,
           score: object | None = None, enabled: bool | None = None) -> PhraseRow:
    """Edit text / score / enabled. A changed score becomes the new prior (statistics are kept).
    Refuses to disable the phrase if fewer than MIN_ENABLED enabled phrases would remain."""
    ensure_autocommit(conn)
    new_text = validate_text(text) if text is not None else None
    new_score = parse_score(score) if score is not None else None
    with conn.transaction():
        _lock_enabled_count(conn)
        cur = get(conn, phrase_id)
        if cur is None:
            raise NotFound(str(phrase_id))
        sets, args = [], []
        if new_text is not None and new_text != cur.text:
            sets.append("text = %s")
            args.append(new_text)
        if new_score is not None and abs(new_score - cur.score) > 1e-9:
            sets += ["score = %s", "prior_score = %s"]
            args += [new_score, new_score]
        if enabled is not None and enabled != cur.enabled:
            if not enabled and enabled_count(conn) <= MIN_ENABLED:
                raise TooFewEnabled(f"at least {MIN_ENABLED} enabled phrases are needed")
            sets.append("enabled = %s")
            args.append(enabled)
        if sets:
            try:
                conn.execute(f"UPDATE phrases SET {', '.join(sets)} WHERE id = %s", [*args, phrase_id])  # type: ignore[call-overload]
            except errors.UniqueViolation as e:
                raise DuplicatePhrase(new_text or cur.text) from e
    updated = get(conn, phrase_id)
    assert updated is not None
    return updated


def delete(conn: psycopg.Connection, phrase_id: int) -> None:
    """Hard delete. Issued cards keep their own text snapshot, so running/finished games are unaffected.
    Its play statistics are lost with it. Refused if it would leave fewer than MIN_ENABLED enabled."""
    ensure_autocommit(conn)
    with conn.transaction():
        _lock_enabled_count(conn)
        cur = get(conn, phrase_id)
        if cur is None:
            raise NotFound(str(phrase_id))
        if cur.enabled and enabled_count(conn) <= MIN_ENABLED:
            raise TooFewEnabled(f"at least {MIN_ENABLED} enabled phrases are needed")
        conn.execute("DELETE FROM phrases WHERE id = %s", (phrase_id,))


def delete_many(conn: psycopg.Connection, phrase_ids: list[int]) -> int:
    """Bulk hard delete, all-or-nothing. Unknown ids are ignored. Refused (nothing deleted) if fewer than
    MIN_ENABLED enabled phrases would remain. Returns the number of rows deleted."""
    ensure_autocommit(conn)
    ids = sorted({int(i) for i in phrase_ids})
    if not ids:
        return 0
    with conn.transaction():
        _lock_enabled_count(conn)
        hit = conn.execute("SELECT count(*) FROM phrases WHERE enabled AND id = ANY(%s)",
                           (ids,)).fetchone()[0]  # type: ignore[index]
        if hit and enabled_count(conn) - hit < MIN_ENABLED:
            raise TooFewEnabled(f"at least {MIN_ENABLED} enabled phrases are needed")
        return conn.execute("DELETE FROM phrases WHERE id = ANY(%s)", (ids,)).rowcount


def set_enabled_many(conn: psycopg.Connection, phrase_ids: list[int], enabled: bool) -> int:
    """Bulk activate / deactivate, all-or-nothing. Unknown ids are ignored; phrases already in the requested state
    are left alone and not counted. Deactivating is refused (nothing changes) if fewer than MIN_ENABLED enabled
    phrases would remain. Returns the number of phrases that actually changed."""
    ensure_autocommit(conn)
    ids = sorted({int(i) for i in phrase_ids})
    if not ids:
        return 0
    with conn.transaction():
        _lock_enabled_count(conn)
        if not enabled:
            hit = conn.execute("SELECT count(*) FROM phrases WHERE enabled AND id = ANY(%s)",
                               (ids,)).fetchone()[0]  # type: ignore[index]
            if hit and enabled_count(conn) - hit < MIN_ENABLED:
                raise TooFewEnabled(f"at least {MIN_ENABLED} enabled phrases are needed")
        return conn.execute("UPDATE phrases SET enabled = %s WHERE id = ANY(%s) AND enabled <> %s",
                            (enabled, ids, enabled)).rowcount


# ------------------------------------------------------------------ learning from games
_STATS_SQL = """
WITH held AS (                       -- one row per (card, phrase) of this session, with its cell index
    SELECT c.id AS card_id, (e.value ->> 'phrase_id')::bigint AS pid, (e.ord - 1)::int AS cell
    FROM bingo_cards c
    CROSS JOIN LATERAL jsonb_array_elements(c.cells) WITH ORDINALITY AS e(value, ord)
    WHERE c.session_id = %(sid)s AND e.value ? 'phrase_id'
), per_phrase AS (
    SELECT h.pid, count(*) AS dealt, count(m.cell_index) AS marked
    FROM held h
    LEFT JOIN bingo_marks m ON m.card_id = h.card_id AND m.cell_index = h.cell
    GROUP BY h.pid
)
UPDATE phrases p SET
    dealt  = p.dealt  + s.dealt,
    marked = p.marked + s.marked,
    score  = CASE WHEN p.prior_score >= %(manual)s THEN p.score ELSE
                 round((((%(k)s * p.prior_score + (p.dealt + s.dealt)
                          * (%(base)s + %(span)s * (p.marked + s.marked)::float8 / (p.dealt + s.dealt)))
                         / (%(k)s + (p.dealt + s.dealt)))::numeric), 3)::real END
FROM per_phrase s
WHERE p.id = s.pid
"""


def apply_session_stats(conn: psycopg.Connection, session_id: uuid.UUID) -> int:
    """Add the session's deals/marks to the phrase counters and relearn their scores.
    Call ONCE per session, inside the transaction that ends it (scoring.finish_session does).
    Phrases deleted meanwhile are skipped. Returns the number of phrases updated."""
    return conn.execute(_STATS_SQL, {"sid": session_id, "manual": MANUAL_SCORE, "k": PRIOR_STRENGTH,
                                     "base": RATE_BASE, "span": RATE_SPAN}).rowcount
