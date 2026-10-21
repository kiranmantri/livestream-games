"""DB access: plain SQL migrations (migrations/NNNN_*.sql) applied in order, once each."""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from app.config import ROOT, database_url

MIGRATIONS_DIR = ROOT / "migrations"


def connect(url: str | None = None) -> psycopg.Connection:
    """Connection for the service layer: AUTOCOMMIT, so every `conn.transaction()` block is a REAL
    transaction that commits on exit (in non-autocommit mode it silently degrades to a savepoint
    inside the connection's implicit transaction and is never committed)."""
    return psycopg.connect(url or database_url(), autocommit=True)


def ensure_autocommit(conn: psycopg.Connection) -> None:
    if not conn.autocommit:
        raise RuntimeError(
            "service functions need an autocommit connection (use app.db.connect() / pool with "
            "autocommit=True); otherwise their transactions are never committed")


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations; each file runs in its own transaction. Returns applied names."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    conn.commit()
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for f in sorted(directory.glob("*.sql")):
        if f.name in done:
            continue
        with conn.transaction():
            conn.execute(f.read_text())  # type: ignore[arg-type]  # trusted file, multi-statement
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
        applied.append(f.name)
    return applied


def migrate_cli() -> int:
    with psycopg.connect(database_url()) as conn:
        applied = migrate(conn)
    print("applied: " + ", ".join(applied) if applied else "up to date")
    return 0


if __name__ == "__main__":
    sys.exit(migrate_cli())
