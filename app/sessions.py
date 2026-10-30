"""Session + identity services (framework-free; all functions take a psycopg connection).

Rules (see AGENTS.md): one active session PER GAME ('bingo', 'falacias'); first visitor starts it, others join; unique
names; identity = random cookie token (hash stored, ONE cookie per game) + source IP limit. Every function that works
on "the" session takes `game_type` (default 'bingo', so Bingo callers are unchanged).
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg import errors
from psycopg.rows import class_row

import app.config  # noqa: F401  (loads .env)
from app.db import ensure_autocommit

MAX_NAME_LEN = 20
COOKIE_NAME = "lg_token"          # the Bingo cookie (kept: players of a running game stay logged in)
GAMES = ("bingo", "falacias", "checker")


def check_game(game_type: str) -> str:
    if game_type not in GAMES:
        raise ValueError(f"unknown game {game_type!r} (expected one of {GAMES})")
    return game_type


def cookie_name(game_type: str = "bingo") -> str:
    """One cookie per game: a person playing both has two independent players (same name, different tokens)."""
    return COOKIE_NAME if check_game(game_type) == "bingo" else f"{COOKIE_NAME}_{game_type}"


IP_LIMIT_MAX = 50


def max_users_per_ip(conn: psycopg.Connection | None = None) -> int:
    """The admin's online setting (table app_settings) wins; else MAX_USERS_PER_IP from the environment; else 3."""
    if conn is not None:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'max_users_per_ip'").fetchone()
        if row is not None:
            return int(row[0])
    return int(os.environ.get("MAX_USERS_PER_IP", "3"))


def set_max_users_per_ip(conn: psycopg.Connection, value) -> int:
    """Store the limit (1..50). Raises ValueError for anything else. Applies to both games, from the next join on."""
    ensure_autocommit(conn)
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("the limit must be a whole number") from None
    if not 1 <= n <= IP_LIMIT_MAX:
        raise ValueError(f"the limit must be between 1 and {IP_LIMIT_MAX}")
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('max_users_per_ip', %s) "
                 "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (str(n),))
    return n


# ------------------------------------------------------------------ errors
class SessionError(Exception):
    """Base class; `.code` is a stable machine-readable reason for the UI."""
    code = "error"


class SessionAlreadyActive(SessionError):
    code = "session_already_active"


class NoActiveSession(SessionError):
    code = "no_active_session"


class NameTaken(SessionError):
    code = "name_taken"


class IpLimitReached(SessionError):
    code = "ip_limit_reached"


class InvalidName(SessionError):
    code = "invalid_name"


# ------------------------------------------------------------------ models
@dataclass(frozen=True)
class GameSession:
    id: uuid.UUID
    game_type: str
    status: str
    created_by: uuid.UUID | None   # None = started by the admin
    winner_user_id: uuid.UUID | None
    created_at: datetime
    ended_at: datetime | None
    video_id: str | None = None        # Falácias only: the video the game follows ...
    source_mode: str | None = None     # ... and how: 'live' or 'replay' (NULL = legacy session: the FALACIAS_VIDEO_ID replay)


@dataclass(frozen=True)
class Player:
    id: uuid.UUID
    session_id: uuid.UUID
    name: str
    ip: str
    created_at: datetime
    last_seen_at: datetime


_SESSION_COLS = "id, game_type, status, created_by, winner_user_id, created_at, ended_at, video_id, source_mode"
_PLAYER_COLS = "id, session_id, name, host(ip) AS ip, created_at, last_seen_at"


# ------------------------------------------------------------------ helpers
def normalize_name(raw: str) -> str:
    """NFC-normalise, collapse whitespace, validate. Returns the display name."""
    name = " ".join(unicodedata.normalize("NFC", raw or "").split())
    if not name:
        raise InvalidName("name is required")
    if len(name) > MAX_NAME_LEN:
        raise InvalidName(f"name must have at most {MAX_NAME_LEN} characters")
    if any(unicodedata.category(c).startswith("C") for c in name):
        raise InvalidName("name contains invalid characters")
    return name


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def normalize_ip(ip: str) -> str:
    try:
        return str(ipaddress.ip_address(ip.strip()))
    except ValueError as e:
        raise ValueError(f"invalid IP address: {ip!r}") from e


def client_ip(peer: str, x_forwarded_for: str | None = None,
              trusted_proxies: frozenset[str] | None = None, client_ip_header: str | None = None) -> str:
    """Real client IP. X-Forwarded-For is honoured ONLY when the direct peer is a trusted proxy
    (otherwise any client could spoof its IP). Walk the chain right-to-left, skipping proxies.
    client_ip_header: value of the header named by CLIENT_IP_HEADER (e.g. Fly-Client-IP on Fly.io). Set that env
    ONLY when every request reaches the app through a platform proxy that overwrites the header (else spoofable);
    an invalid value falls back to the normal logic."""
    if client_ip_header:
        try:
            return normalize_ip(client_ip_header)
        except ValueError:
            pass
    if trusted_proxies is None:
        trusted_proxies = frozenset(
            p.strip() for p in os.environ.get("TRUSTED_PROXIES", "").split(",") if p.strip())
    peer = normalize_ip(peer)
    if peer not in trusted_proxies or not x_forwarded_for:
        return peer
    hops = [h.strip() for h in x_forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            hop = normalize_ip(hop)
        except ValueError:
            return peer  # malformed header: don't trust it
        if hop not in trusted_proxies:
            return hop
    return peer


# ------------------------------------------------------------------ queries
def get_active_session(conn: psycopg.Connection, game_type: str = "bingo") -> GameSession | None:
    check_game(game_type)
    with conn.cursor(row_factory=class_row(GameSession)) as cur:
        cur.execute(f"SELECT {_SESSION_COLS} FROM game_sessions WHERE status = 'active' AND game_type = %s",
                    (game_type,))
        return cur.fetchone()


def get_player_by_token(conn: psycopg.Connection, raw_token: str | None, game_type: str = "bingo") -> Player | None:
    """Player of the ACTIVE session of `game_type` owning this cookie token, or None (unknown / old session /
    a token that belongs to the other game)."""
    check_game(game_type)
    if not raw_token:
        return None
    with conn.cursor(row_factory=class_row(Player)) as cur:
        cur.execute(
            "SELECT u.id, u.session_id, u.name, host(u.ip) AS ip, u.created_at, u.last_seen_at "
            "FROM users u JOIN game_sessions s ON s.id = u.session_id "
            "WHERE u.token_hash = %s AND s.status = 'active' AND s.game_type = %s",
            (hash_token(raw_token), game_type),
        )
        return cur.fetchone()


def find_player(conn: psycopg.Connection, raw_token: str | None) -> tuple[Player, GameSession] | None:
    """Player + their session regardless of status (used to show the results of an ended game)."""
    if not raw_token:
        return None
    with conn.cursor(row_factory=class_row(Player)) as cur:
        cur.execute(
            "SELECT id, session_id, name, host(ip) AS ip, created_at, last_seen_at "
            "FROM users WHERE token_hash = %s", (hash_token(raw_token),))
        player = cur.fetchone()
    return (player, _get_session(conn, player.session_id)) if player else None


def name_taken(conn: psycopg.Connection, session_id: uuid.UUID, name: str) -> bool:
    """True when a player of the session already has this name (case-insensitive, same rule as the DB's name_ci)."""
    try:
        clean = normalize_name(name)
    except InvalidName:
        return False
    return conn.execute("SELECT 1 FROM users WHERE session_id = %s AND name_ci = lower(btrim(%s))",
                        (session_id, clean)).fetchone() is not None


def count_players(conn: psycopg.Connection, session_id: uuid.UUID) -> int:
    row = conn.execute("SELECT count(*) FROM users WHERE session_id = %s", (session_id,)).fetchone()
    return row[0] if row else 0


def _count_ip(conn: psycopg.Connection, session_id: uuid.UUID, ip: str) -> int:
    row = conn.execute(
        "SELECT count(*) FROM users WHERE session_id = %s AND ip = %s", (session_id, ip)).fetchone()
    return row[0] if row else 0


# ------------------------------------------------------------------ commands
def start_session(conn: psycopg.Connection, name: str, ip: str, game_type: str = "bingo",
                  video_id: str | None = None, source_mode: str | None = None) -> tuple[GameSession, Player, str]:
    """First visitor starts the session of `game_type` and becomes its first player.
    Returns (session, player, raw_token). Raises SessionAlreadyActive if one is running for that game
    (also when a concurrent request wins the race: the DB's unique index decides)."""
    check_game(game_type)
    name, ip = normalize_name(name), normalize_ip(ip)
    ensure_autocommit(conn)
    token = new_token()
    sid, uid = uuid.uuid4(), uuid.uuid4()
    try:
        with conn.transaction():
            # both rows in one transaction; the circular FKs are deferred (see migration 0001)
            conn.execute("INSERT INTO game_sessions (id, game_type, created_by, video_id, source_mode) VALUES (%s, %s, %s, %s, %s)",
                         (sid, game_type, uid, video_id, source_mode))
            conn.execute(
                "INSERT INTO users (id, session_id, name, ip, token_hash) VALUES (%s,%s,%s,%s,%s)",
                (uid, sid, name, ip, hash_token(token)))
    except errors.UniqueViolation as e:
        if e.diag.constraint_name == "one_active_session_per_game":
            raise SessionAlreadyActive(f"a {game_type} session is already running") from e
        raise
    return _get_session(conn, sid), _get_player(conn, uid), token


def start_session_as_admin(conn: psycopg.Connection, game_type: str = "bingo", video_id: str | None = None,
                           source_mode: str | None = None) -> GameSession:
    """Admin starts a session of `game_type` with no players yet (created_by NULL). Players then join it.
    Raises SessionAlreadyActive if one is running for that game (the DB's unique index decides, also on races)."""
    check_game(game_type)
    ensure_autocommit(conn)
    sid = uuid.uuid4()
    try:
        conn.execute("INSERT INTO game_sessions (id, game_type, created_by, video_id, source_mode) VALUES (%s, %s, NULL, %s, %s)",
                     (sid, game_type, video_id, source_mode))
    except errors.UniqueViolation as e:
        if e.diag.constraint_name == "one_active_session_per_game":
            raise SessionAlreadyActive(f"a {game_type} session is already running") from e
        raise
    return _get_session(conn, sid)


def join_session(conn: psycopg.Connection, name: str, ip: str, existing_token: str | None = None,
                 game_type: str = "bingo") -> tuple[GameSession, Player, str]:
    """Join the running session of `game_type`. If `existing_token` already identifies a player of it, that
    player is returned (resume) instead of creating a new one."""
    check_game(game_type)
    ensure_autocommit(conn)
    session = get_active_session(conn, game_type)
    if session is None:
        raise NoActiveSession(f"no {game_type} session is running")
    current = get_player_by_token(conn, existing_token, game_type)
    if current is not None:
        touch(conn, current.id)
        return session, current, existing_token  # type: ignore[return-value]
    name, ip = normalize_name(name), normalize_ip(ip)
    token = new_token()
    uid = uuid.uuid4()
    try:
        with conn.transaction():
            # Re-check "still active" atomically with the insert (session may have just ended):
            row = conn.execute(
                "SELECT 1 FROM game_sessions WHERE id = %s AND status = 'active' FOR SHARE",
                (session.id,)).fetchone()
            if row is None:
                raise NoActiveSession("the session has just ended")
            # serialise joins per (session, ip) so the IP limit cannot be raced past
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                         (f"{session.id}:{ip}",))
            limit = max_users_per_ip(conn)
            if _count_ip(conn, session.id, ip) >= limit:
                raise IpLimitReached(f"at most {limit} players per IP address")
            conn.execute(
                "INSERT INTO users (id, session_id, name, ip, token_hash) VALUES (%s,%s,%s,%s,%s)",
                (uid, session.id, name, ip, hash_token(token)))
    except errors.UniqueViolation as e:
        if e.diag.constraint_name == "users_name_unique_per_session":
            raise NameTaken(f"the name {name!r} is already taken") from e
        raise
    return session, _get_player(conn, uid), token


def resume(conn: psycopg.Connection, raw_token: str | None, game_type: str = "bingo") -> Player | None:
    """Identify the caller by cookie; refreshes last_seen_at. None if not a current player of that game."""
    player = get_player_by_token(conn, raw_token, game_type)
    if player is not None:
        touch(conn, player.id)
    return player


def touch(conn: psycopg.Connection, user_id: uuid.UUID) -> None:
    ensure_autocommit(conn)
    with conn.transaction():
        conn.execute("UPDATE users SET last_seen_at = now() WHERE id = %s", (user_id,))


def end_session(conn: psycopg.Connection, session_id: uuid.UUID,
                winner_user_id: uuid.UUID | None = None) -> bool:
    """End an active session (winner declared, or admin ends it). Idempotent: returns False if it
    was not active, so exactly one caller wins a 'declare BINGO' race."""
    ensure_autocommit(conn)
    with conn.transaction():
        cur = conn.execute(
            "UPDATE game_sessions SET status = 'ended', ended_at = now(), winner_user_id = %s "
            "WHERE id = %s AND status = 'active'", (winner_user_id, session_id))
        return cur.rowcount == 1


def kick_player(conn: psycopg.Connection, session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """ADMIN: remove a player from a RUNNING session: the user row goes (card, marks, blocks with it, by cascade), so the cookie
    no longer resumes anything and the name / IP slot is free; the cookie's hash is kept in kicked_players so the join screen
    can say why. The player may join again with a new card. Returns False if the session is not active or the user is not
    in it."""
    ensure_autocommit(conn)
    with conn.transaction():
        if conn.execute("SELECT 1 FROM game_sessions WHERE id = %s AND status = 'active' FOR UPDATE",
                        (session_id,)).fetchone() is None:
            return False
        row = conn.execute("SELECT token_hash, name FROM users WHERE id = %s AND session_id = %s",
                           (user_id, session_id)).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE game_sessions SET created_by = NULL WHERE id = %s AND created_by = %s", (session_id, user_id))
        conn.execute("INSERT INTO kicked_players (session_id, token_hash, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                     (session_id, row[0], row[1]))
        # an unused card swap earned by a mark on this card goes with it (used / declined ones stay in the log)
        conn.execute("DELETE FROM swap_rights WHERE card_id IN (SELECT id FROM bingo_cards WHERE user_id = %s) "
                     "AND used_at IS NULL AND declined_at IS NULL", (user_id,))
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
        return True


def was_kicked(conn: psycopg.Connection, session_id: uuid.UUID, raw_token: str | None) -> bool:
    """This cookie belonged to a player the admin kicked out of that session."""
    return bool(raw_token) and conn.execute("SELECT 1 FROM kicked_players WHERE session_id = %s AND token_hash = %s",
                                            (session_id, hash_token(raw_token or ""))).fetchone() is not None


def _get_player(conn: psycopg.Connection, user_id: uuid.UUID) -> Player:
    with conn.cursor(row_factory=class_row(Player)) as cur:
        cur.execute(f"SELECT {_PLAYER_COLS} FROM users WHERE id = %s", (user_id,))
        player = cur.fetchone()
    assert player is not None
    return player


def _get_session(conn: psycopg.Connection, session_id: uuid.UUID) -> GameSession:
    with conn.cursor(row_factory=class_row(GameSession)) as cur:
        cur.execute(f"SELECT {_SESSION_COLS} FROM game_sessions WHERE id = %s", (session_id,))
        session = cur.fetchone()
    assert session is not None
    return session