"""Bingo card generation + assignment.

A card is 25 cells (row-major 5x5). Index 12 (centre) is the channel avatar (free cell); the other
24 are distinct phrases drawn at random from the enabled corpus. Cards are persisted once and are
immutable (DB trigger); assign_card() is idempotent so a user can never be re-rolled.
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

CENTER_INDEX = 12
GRID = 5
PHRASE_CELLS = GRID * GRID - 1  # 24
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
        return [self.cells[r * GRID:(r + 1) * GRID] for r in range(GRID)]


# ------------------------------------------------------------------ pure generation
def phrase_weights(phrases: list[Phrase]) -> list[float]:
    """Sampling weight per phrase = 1 + score. Higher-scored phrases (said more often, learned from real
    games) are more likely to land on a card. Manual phrases carry their own score (99 until the admin
    edits it), so they are NOT special-cased here."""
    return [1.0 + max(p.score, 0.0) for p in phrases]


def generate_cells(phrases: list[Phrase], rng: random.Random | None = None) -> list[dict]:
    """24 distinct phrases (weighted, without replacement) at uniformly random positions + centre."""
    if len(phrases) < PHRASE_CELLS:
        raise NotEnoughPhrases(f"need {PHRASE_CELLS} enabled phrases, have {len(phrases)}")
    rng = rng or random.SystemRandom()
    weights = phrase_weights(phrases)
    # Efraimidis-Spirakis: weighted sampling without replacement via key = u ** (1/w)
    keyed = sorted(((rng.random() ** (1.0 / w), p) for w, p in zip(weights, phrases, strict=True)),
                   key=lambda kp: kp[0], reverse=True)
    chosen = [p for _, p in keyed[:PHRASE_CELLS]]
    rng.shuffle(chosen)  # selection is weighted; POSITIONS must be uniform
    cells = [{"phrase_id": p.id, "text": p.text} for p in chosen]
    cells.insert(CENTER_INDEX, dict(CENTER_CELL))
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
    cells = generate_cells(enabled_phrases(conn), rng)
    with conn.transaction():
        # a concurrent call may have inserted first: UNIQUE(session_id, user_id) makes this a no-op
        conn.execute(
            "INSERT INTO bingo_cards (session_id, user_id, cells) VALUES (%s, %s, %s) "
            "ON CONFLICT (session_id, user_id) DO NOTHING",
            (session_id, user_id, json.dumps(cells)))
    card = get_card(conn, session_id, user_id)
    assert card is not None
    return card
