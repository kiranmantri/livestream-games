"""Settings from environment (a local .env file is read if present; real env vars win)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> bool:
    """Read a .env file (default: <repo>/.env) with python-dotenv. Real environment variables always win; no ${VAR} interpolation,
    so a secret that contains '${...}' reaches the app unchanged. A missing file is fine (Docker/Fly inject real env vars)."""
    return load_dotenv(path or ROOT / ".env", override=False, interpolate=False)


load_env()


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    return url
