"""Which values of the local .env may become Fly secrets.

    python -m deploy.env_secrets --names        # names only (safe to read)
    python -m deploy.env_secrets | fly secrets import --stage --app livebingo      # NAME=VALUE lines for Fly (used by scripts/fly-secrets.sh)

ONLY third-party API keys are synced, and only those a container of deploy/fly.machine.json actually receives (Fly injects nothing else).
DATABASE_URL, POSTGRES_PASSWORD, ADMIN_PASSWORD and SESSION_SECRET are NEVER synced: the local .env has dev values, and a different POSTGRES_PASSWORD
would lock the cloud app out of its own database (the password lives inside the volume). Set those once by hand with `fly secrets set`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SYNCED = ("GROQ_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY")
NEVER = ("DATABASE_URL", "POSTGRES_PASSWORD", "ADMIN_PASSWORD", "SESSION_SECRET")


def machine_secret_names(path: Path) -> set[str]:
    """Every secret env var that any container of the Machine config receives."""
    containers = json.loads(Path(path).read_text()).get("containers", [])
    return {s["env_var"] for c in containers for s in c.get("secrets", [])}


def select(env: dict, listed: set[str]) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """-> (what to sync, [(name, reason it was skipped)]). Never returns a value in the skipped list."""
    chosen: dict[str, str] = {}
    skipped: list[tuple[str, str]] = []
    for name in SYNCED:
        value = env.get(name) or ""
        if name not in listed:
            skipped.append((name, "no container lists it in deploy/fly.machine.json (the app does not use it yet)"))
        elif not value.strip():
            skipped.append((name, "empty or missing in .env"))
        elif "\n" in value or "\r" in value:
            raise ValueError(f"{name}: a secret may not contain a line break")
        else:
            chosen[name] = value.strip()
    return chosen, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", type=Path, default=ROOT / ".env")
    ap.add_argument("--machine", type=Path, default=ROOT / "deploy" / "fly.machine.json")
    ap.add_argument("--names", action="store_true", help="print only the names that would be synced")
    a = ap.parse_args(argv)
    if not a.env.exists():
        print(f"{a.env} not found (copy .env.example to .env and fill in the keys)", file=sys.stderr)
        return 1
    chosen, skipped = select(dict(dotenv_values(a.env)), machine_secret_names(a.machine))
    for name, why in skipped:
        print(f"skipped {name}: {why}", file=sys.stderr)
    print(f"never synced (set by hand once): {', '.join(NEVER)}", file=sys.stderr)
    if not chosen:
        print("nothing to sync", file=sys.stderr)
        return 1
    for name, value in chosen.items():
        print(name if a.names else f"{name}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
