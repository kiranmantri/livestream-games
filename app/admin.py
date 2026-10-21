"""Admin: HTTP Basic auth (ADMIN_PASSWORD env) + read model for the admin page. No web code here."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg

from app import sessions as S

ADMIN_USER = "admin"
_REFUSED_PASSWORDS = {"", "change-me"}     # the .env.example placeholder must never open the admin


def admin_password() -> str | None:
    """None = admin disabled (unset or placeholder password)."""
    pw = os.environ.get("ADMIN_PASSWORD", "")
    return None if pw.strip() in _REFUSED_PASSWORDS else pw


def check_basic(header: str | None, password: str) -> bool:
    """Constant-time check of an `Authorization: Basic ...` header (user must be 'admin')."""
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        user, _, pw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    # compare digests so lengths never leak and both fields are always checked
    ok_user = hmac.compare_digest(hashlib.sha256(user.encode()).digest(),
                                  hashlib.sha256(ADMIN_USER.encode()).digest())
    ok_pw = hmac.compare_digest(hashlib.sha256(pw.encode()).digest(),
                                hashlib.sha256(password.encode()).digest())
    return ok_user and ok_pw


def csrf_token(password: str, session_id: uuid.UUID) -> str:
    """Basic auth is re-sent by the browser on cross-site requests, so state-changing admin POSTs
    carry a token only a page rendered for the admin can contain."""
    return hmac.new(password.encode(), f"finish:{session_id}".encode(), hashlib.sha256).hexdigest()


def csrf_ok(password: str, session_id: uuid.UUID, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(csrf_token(password, session_id), token or "")


def start_token(password: str) -> str:
    return hmac.new(password.encode(), b"start", hashlib.sha256).hexdigest()


def start_ok(password: str, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(start_token(password), token or "")


def phrases_token(password: str) -> str:
    """One token for every state-changing phrase action (add / edit / delete)."""
    return hmac.new(password.encode(), b"phrases", hashlib.sha256).hexdigest()


def phrases_ok(password: str, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(phrases_token(password), token or "")


@dataclass(frozen=True)
class PlayerRow:
    user_id: uuid.UUID
    name: str
    ip: str
    joined_at: datetime
    last_seen_at: datetime
    cells: list[dict]          # 16 or 25 entries as stored on the card (immutable snapshot)
    marked: frozenset[int]     # marked cell indexes (never the avatar free cell)
    marked_at: dict[int, datetime]   # cell_index -> time it was marked
    revoked: frozenset[int] = frozenset()   # cells the admin unmarked and blocked


_SQL = """
SELECT u.id, u.name, host(u.ip), u.created_at, u.last_seen_at, c.cells,
       COALESCE(array_agg(m.cell_index ORDER BY m.cell_index) FILTER (WHERE m.cell_index IS NOT NULL), '{}'),
       COALESCE(array_agg(m.marked_at ORDER BY m.cell_index) FILTER (WHERE m.cell_index IS NOT NULL), '{}')
FROM users u
LEFT JOIN bingo_cards c ON c.user_id = u.id AND c.session_id = u.session_id
LEFT JOIN bingo_marks m ON m.card_id = c.id
WHERE u.session_id = %s
GROUP BY u.id, u.name, u.ip, u.created_at, u.last_seen_at, c.cells
ORDER BY u.created_at, u.id
"""


def current_session(conn: psycopg.Connection, game_type: str = "bingo") -> S.GameSession | None:
    """The active session of that game, else its most recently ended one (so results stay visible), else None."""
    active = S.get_active_session(conn, game_type)
    if active:
        return active
    row = conn.execute(f"SELECT {S._SESSION_COLS} FROM game_sessions WHERE game_type = %s "
                       "ORDER BY created_at DESC LIMIT 1", (game_type,)).fetchone()
    return S.GameSession(*row) if row else None


def players(conn: psycopg.Connection, session_id: uuid.UUID) -> list[PlayerRow]:
    rows = conn.execute(_SQL, (session_id,)).fetchall()  # type: ignore[arg-type]
    revoked: dict[uuid.UUID, set[int]] = {}
    for uid, idx in conn.execute(
            "SELECT c.user_id, r.cell_index FROM revoked_marks r JOIN bingo_cards c ON c.id = r.card_id "
            "WHERE c.session_id = %s", (session_id,)):
        revoked.setdefault(uid, set()).add(idx)
    return [PlayerRow(r[0], r[1], r[2], r[3], r[4], r[5] or [], frozenset(r[6]), dict(zip(r[6], r[7], strict=True)),
                      frozenset(revoked.get(r[0], ()))) for r in rows]
