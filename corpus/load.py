"""Seed the phrases table of an EMPTY database from the SQL seed made by deploy/make_seed.py."""
from __future__ import annotations

from pathlib import Path

import psycopg


def load_sql_seed(conn: psycopg.Connection, sql_path: Path) -> dict[str, int | str]:
    """Data-only seed (ON CONFLICT DO NOTHING). Runs ONLY when the phrases table is EMPTY: re-applying it on every
    start would bring back phrases the admin deleted in the UI (the database is the source of truth). The file
    carries its own BEGIN/COMMIT, so the connection must be autocommit."""
    if not conn.autocommit:
        raise RuntimeError("load_sql_seed needs an autocommit connection (the file has its own BEGIN/COMMIT)")
    existing = conn.execute("SELECT count(*) FROM phrases").fetchone()[0]  # type: ignore[index]
    if existing:
        return {"skipped": f"phrases table already has {existing} rows"}
    conn.execute(sql_path.read_text(encoding="utf-8"))  # type: ignore[arg-type]  # multi-statement, no params
    return {"phrases": conn.execute("SELECT count(*) FROM phrases").fetchone()[0],  # type: ignore[index]
            "enabled": conn.execute("SELECT count(*) FROM phrases WHERE enabled").fetchone()[0]}  # type: ignore[index]
