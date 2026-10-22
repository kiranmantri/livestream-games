"""Time display: one configured timezone for everything shown to people (DB stores timestamptz)."""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Sao_Paulo"      # audience of the stream; override with TIMEZONE in .env


def display_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TIMEZONE") or DEFAULT_TZ)


def hms(dt: datetime | None) -> str:
    """HH:MM:SS in the display timezone ('-' when missing)."""
    return dt.astimezone(display_tz()).strftime("%H:%M:%S") if dt else "-"


def dmy_hms(dt: datetime | None) -> str:
    return dt.astimezone(display_tz()).strftime("%d/%m %H:%M:%S") if dt else "-"


def ago(seconds: float) -> str:
    """'há 12 s' / 'há 3 min' / 'há 2 h' (static text; admin.js keeps it current)."""
    s = max(0, int(seconds))
    return f"há {s} s" if s < 60 else f"há {s // 60} min" if s < 3600 else f"há {s // 3600} h"


def elapsed(seconds: float) -> str:
    """'12 min 05 s' / '1 h 03 min' (static text; admin.js keeps it current)."""
    s = max(0, int(seconds))
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60:02d} s" if s < 3600 else f"{s // 3600} h {s % 3600 // 60:02d} min"
