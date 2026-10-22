"""Admin management of the fallacy list: search / create / update / delete (the DB is the source of truth).

Rules
  * Names are unique ignoring case and spacing (the DB UNIQUE is exact, so the service checks too, under one advisory lock).
  * At least MIN_ENABLED fallacies stay enabled (the picker must never be empty); enabling is never refused.
  * A fallacy that players already used (rows in fallacy_marks) cannot be deleted: disable it instead.
  * Editing a Wikipedia row keeps its source and link. `corpus fallacies-top` (manual, never at boot) can still rename/toggle
    Wikipedia rows by their ORIGINAL names, so do not run it after editing those online.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import psycopg
from psycopg import errors
from psycopg.rows import class_row

from app.db import ensure_autocommit

MIN_ENABLED = 5
MAX_NAME = 120                      # = the DB CHECK
MAX_CATEGORY = 60
MAX_DESCRIPTION = 1000
SOURCES = ("wikipedia", "manual")
MANUAL_CATEGORY = "Outras falácias comuns"

# Added on request; inserted by `corpus fallacies-manual` (never at boot: a deleted/renamed row must stay so).
MANUAL_DEFAULTS: tuple[tuple[str, str, str], ...] = (
    ("Deus das lacunas (God of the gaps)", MANUAL_CATEGORY,
     "usar uma lacuna no conhecimento atual como prova de que Deus (ou o sobrenatural) é a explicação."),
    ("Ônus da prova invertido (Shift the burden of proof)", MANUAL_CATEGORY,
     "exigir que o outro prove que a afirmação é falsa, em vez de provar a afirmação feita."),
)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class FallacyAdminError(Exception):
    code = "error"


class InvalidName(FallacyAdminError):
    code = "invalid_name"


class InvalidCategory(FallacyAdminError):
    code = "invalid_category"


class InvalidDescription(FallacyAdminError):
    code = "invalid_description"


class NameTaken(FallacyAdminError):
    code = "name_taken"


class InUse(FallacyAdminError):
    code = "in_use"


class TooFewEnabled(FallacyAdminError):
    code = "too_few_enabled"


class NotFound(FallacyAdminError):
    code = "not_found"


@dataclass(frozen=True)
class FallacyRow:
    id: int
    name: str
    category: str
    description: str
    wikipedia_url: str | None
    source: str
    enabled: bool
    used: int                       # how many player pairs point at it


_COLS = ("f.id, f.name, f.category, f.description, f.wikipedia_url, f.source, f.enabled, "
         "(SELECT count(1) FROM fallacy_marks m WHERE m.fallacy_id = f.id) AS used")


def clean(raw: str | None) -> str:
    """NFC, control characters dropped, whitespace collapsed."""
    return " ".join(_CONTROL.sub("", unicodedata.normalize("NFC", raw or "")).split())


def fold(s: str) -> str:
    """Case- and accent-insensitive key."""
    return "".join(c for c in unicodedata.normalize("NFD", clean(s).casefold()) if not unicodedata.combining(c))


def _validate(name: str, category: str, description: str) -> tuple[str, str, str]:
    name, category, description = clean(name), clean(category), clean(description)
    if not 1 <= len(name) <= MAX_NAME:
        raise InvalidName()
    if not 1 <= len(category) <= MAX_CATEGORY:
        raise InvalidCategory()
    if len(description) > MAX_DESCRIPTION:
        raise InvalidDescription()
    return name, category, description


def _lock(conn: psycopg.Connection) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('livegames.fallacies'))")


def _taken(conn: psycopg.Connection, name: str, except_id: int | None = None) -> bool:
    key = fold(name)
    return any(fold(n) == key for i, n in conn.execute("SELECT id, name FROM fallacies") if i != except_id)


def _enabled_others(conn: psycopg.Connection, fid: int) -> int:
    return conn.execute("SELECT count(1) FROM fallacies WHERE enabled AND id <> %s", (fid,)).fetchone()[0]  # type: ignore[index]


# ------------------------------------------------------------------ reads
def get(conn: psycopg.Connection, fid: int) -> FallacyRow | None:
    with conn.cursor(row_factory=class_row(FallacyRow)) as cur:
        cur.execute(f"SELECT {_COLS} FROM fallacies f WHERE f.id = %s", (fid,))
        return cur.fetchone()


def search(conn: psycopg.Connection, q: str = "", enabled: str = "", source: str = "") -> list[FallacyRow]:
    """All matching rows ordered by category then name. `q` matches name, category and description, ignoring case and accents."""
    where, args = [], []
    if enabled in ("0", "1"):
        where.append("f.enabled = %s")
        args.append(enabled == "1")
    if source in SOURCES:
        where.append("f.source = %s")
        args.append(source)
    sql = f"SELECT {_COLS} FROM fallacies f" + (" WHERE " + " AND ".join(where) if where else "")
    with conn.cursor(row_factory=class_row(FallacyRow)) as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    needle = fold(q)
    if needle:
        rows = [r for r in rows if needle in fold(f"{r.name} {r.category} {r.description}")]
    return sorted(rows, key=lambda r: (fold(r.category), fold(r.name), r.id))


# ------------------------------------------------------------------ writes
def create(conn: psycopg.Connection, name: str, category: str, description: str = "") -> FallacyRow:
    """Add a MANUAL, enabled fallacy."""
    ensure_autocommit(conn)
    name, category, description = _validate(name, category, description)
    with conn.transaction():
        _lock(conn)
        if _taken(conn, name):
            raise NameTaken()
        try:
            fid = conn.execute("INSERT INTO fallacies (name, category, description, source, enabled) "
                               "VALUES (%s,%s,%s,'manual',true) RETURNING id", (name, category, description)).fetchone()[0]  # type: ignore[index]
        except errors.UniqueViolation:
            raise NameTaken() from None
    return get(conn, fid)  # type: ignore[return-value]


def update(conn: psycopg.Connection, fid: int, name: str, category: str, description: str, enabled: bool) -> FallacyRow:
    ensure_autocommit(conn)
    name, category, description = _validate(name, category, description)
    with conn.transaction():
        _lock(conn)
        cur = conn.execute("SELECT enabled FROM fallacies WHERE id = %s FOR UPDATE", (fid,)).fetchone()
        if cur is None:
            raise NotFound()
        if _taken(conn, name, except_id=fid):
            raise NameTaken()
        if cur[0] and not enabled and _enabled_others(conn, fid) < MIN_ENABLED:
            raise TooFewEnabled()
        conn.execute("UPDATE fallacies SET name = %s, category = %s, description = %s, enabled = %s WHERE id = %s",
                     (name, category, description, enabled, fid))
    return get(conn, fid)  # type: ignore[return-value]


def delete(conn: psycopg.Connection, fid: int) -> None:
    ensure_autocommit(conn)
    with conn.transaction():
        _lock(conn)
        cur = conn.execute("SELECT enabled FROM fallacies WHERE id = %s FOR UPDATE", (fid,)).fetchone()
        if cur is None:
            raise NotFound()
        if conn.execute("SELECT count(1) FROM fallacy_marks WHERE fallacy_id = %s", (fid,)).fetchone()[0]:  # type: ignore[index]
            raise InUse()
        if cur[0] and _enabled_others(conn, fid) < MIN_ENABLED:
            raise TooFewEnabled()
        conn.execute("DELETE FROM fallacies WHERE id = %s", (fid,))


def ensure_manual(conn: psycopg.Connection) -> int:
    """Insert the requested default manual fallacies that are missing BY NAME; never touches an existing row. Returns how many."""
    ensure_autocommit(conn)
    added = 0
    with conn.transaction():
        _lock(conn)
        for name, category, description in MANUAL_DEFAULTS:
            if not _taken(conn, name):
                conn.execute("INSERT INTO fallacies (name, category, description, source, enabled) VALUES (%s,%s,%s,'manual',true)",
                             (name, category, description))
                added += 1
    return added
