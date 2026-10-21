"""Bingo card generation + assignment.

A new session deals 4x4 cards (16 distinct phrases, no free cell). Sessions created before that change keep
their 5x5 cards: 25 cells, index 12 (centre) is the channel avatar (free cell), the other 24 are phrases. The
size is stored per session (game_sessions.grid_size); phrases are drawn at random from the enabled corpus.
Cards are persisted once and are immutable (DB trigger); assign_card() is idempotent so a user can never be re-rolled.
"""
from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from app.db import ensure_autocommit

DEFAULT_GRID = 4                  # new sessions: 4x4, 16 phrase cells, no free cell
SUPPORTED_GRIDS = (4, 5)          # 5x5 only exists for sessions created before the 4x4 change (and in tests)
CENTER_INDEX = 12                 # the avatar free cell of a 5x5 card; a 4x4 card has none
MAX_CELLS = 25                    # cells of the largest supported card (5x5)
PHRASE_CELLS = 24                 # MOST phrases any supported card needs (5x5): floor for the enabled corpus
CENTER_IMAGE_URL = ("https://yt3.googleusercontent.com/LSxmMTuZ7eATKJKq10HbVV29fuHJej6zse6odK-"
                    "C-95Fg85xBXDowLysZzqlOFFzq6EoTRkcjQ=s160-c-k-c0x00ffffff-no-rj")
CENTER_CELL = {"type": "image"}


class NotEnoughPhrases(Exception):
    """Fewer than 24 enabled phrases in the corpus."""


@dataclass(frozen=True)
class Phrase:
    id: int
    text: str
    source: str
    score: float


@dataclass(frozen=True)
class Card:
    id: uuid.UUID
    session_id: uuid.UUID
    user_id: uuid.UUID
    cells: list[dict]
    created_at: datetime

    def grid(self) -> list[list[dict]]:
        n = grid_of(len(self.cells))
        return [self.cells[r * n:(r + 1) * n] for r in range(n)]

    @property
    def size(self) -> int:
        return grid_of(len(self.cells))

    @property
    def max_marks(self) -> int:
        return max_marks(self.cells)


# ------------------------------------------------------------------ grid helpers
def grid_of(n_cells: int) -> int:
    """Side of the square for a card with n_cells cells (16 -> 4, 25 -> 5)."""
    n = round(n_cells ** 0.5)
    if n * n != n_cells or n not in SUPPORTED_GRIDS:
        raise ValueError(f"unsupported card size: {n_cells} cells")
    return n


def centre_of(grid: int) -> int | None:
    """Index of the free avatar cell: only a 5x5 card has one."""
    return CENTER_INDEX if grid == 5 else None


def phrase_cell_count(grid: int) -> int:
    return grid * grid - (1 if centre_of(grid) is not None else 0)


def max_marks(cells: list[dict]) -> int:
    """Markable cells of a stored card (every cell holding a phrase)."""
    return sum(1 for c in cells if "phrase_id" in c)


def is_phrase_cell(cells: list[dict], index: int) -> bool:
    return 0 <= index < len(cells) and "phrase_id" in cells[index]


# ------------------------------------------------------------------ pure generation
def phrase_weights(phrases: list[Phrase]) -> list[float]:
    """Sampling weight per phrase = 1 + score. Higher-scored phrases (said more often, learned from real
    games) are more likely to land on a card. Manual phrases carry their own score (99 until the admin
    edits it), so they are NOT special-cased here."""
    return [1.0 + max(p.score, 0.0) for p in phrases]


def generate_cells(phrases: list[Phrase], rng: random.Random | None = None, grid: int = DEFAULT_GRID) -> list[dict]:
    """The phrase cells (weighted, without replacement) at uniformly random positions; a 5x5 card also gets the
    avatar in the centre, a 4x4 card is all phrases."""
    need = phrase_cell_count(grid)
    if len(phrases) < need:
        raise NotEnoughPhrases(f"need {need} enabled phrases, have {len(phrases)}")
    rng = rng or random.SystemRandom()
    weights = phrase_weights(phrases)
    # Efraimidis-Spirakis: weighted sampling without replacement via key = u ** (1/w)
    keyed = sorted(((rng.random() ** (1.0 / w), p) for w, p in zip(weights, phrases, strict=True)),
                   key=lambda kp: kp[0], reverse=True)
    chosen = [p for _, p in keyed[:need]]
    rng.shuffle(chosen)  # selection is weighted; POSITIONS must be uniform
    cells = [{"phrase_id": p.id, "text": p.text} for p in chosen]
    centre = centre_of(grid)
    if centre is not None:
        cells.insert(centre, dict(CENTER_CELL))
    return cells


# ------------------------------------------------------------------ persistence
def enabled_phrases(conn: psycopg.Connection) -> list[Phrase]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, text, source, score FROM phrases WHERE enabled ORDER BY id")
        return [Phrase(r["id"], r["text"], r["source"], float(r["score"])) for r in cur.fetchall()]


def get_card(conn: psycopg.Connection, session_id: uuid.UUID, user_id: uuid.UUID) -> Card | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, session_id, user_id, cells, created_at FROM bingo_cards "
                    "WHERE session_id = %s AND user_id = %s", (session_id, user_id))
        r = cur.fetchone()
    return Card(**r) if r else None


def assign_card(conn: psycopg.Connection, session_id: uuid.UUID, user_id: uuid.UUID,
                rng: random.Random | None = None) -> Card:
    """Return the user's card, creating it on first call. Idempotent and race-safe: concurrent
    calls all return the same single card; the user is never re-rolled.
    Raises NotEnoughPhrases (nothing is stored) if the corpus is too small, and a ForeignKeyViolation
    if the user is not a player of that session."""
    ensure_autocommit(conn)
    existing = get_card(conn, session_id, user_id)
    if existing:
        return existing
    row = conn.execute("SELECT grid_size FROM game_sessions WHERE id = %s", (session_id,)).fetchone()
    grid = row[0] if row else DEFAULT_GRID      # unknown session: the INSERT below fails on the foreign key
    cells = generate_cells(enabled_phrases(conn), rng, grid)
    with conn.transaction():
        # a concurrent call may have inserted first: UNIQUE(session_id, user_id) makes this a no-op
        conn.execute(
            "INSERT INTO bingo_cards (session_id, user_id, cells) VALUES (%s, %s, %s) "
            "ON CONFLICT (session_id, user_id) DO NOTHING",
            (session_id, user_id, json.dumps(cells)))
    card = get_card(conn, session_id, user_id)
    assert card is not None
    return card
