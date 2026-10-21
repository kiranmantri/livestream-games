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
