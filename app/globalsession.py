"""ONE session for the three games: the admin starts and ends Bingo, Falaciômetro and Verdade ou Lorota together, on one video.

A game that is already running is kept (start skips it); finish ends only what is running. The video belongs to the two games that follow one
(Falácias, Verdade ou Lorota); Bingo has none."""
from __future__ import annotations

import psycopg

from app import checker, falacias, feed, scoring
from app import sessions as S

GAMES = ("bingo", "falacias", "checker")


def active_games(conn: psycopg.Connection) -> list[str]:
    return [g for g in GAMES if S.get_active_session(conn, g) is not None]


def current_video(conn: psycopg.Connection) -> tuple[str, str | None] | None:
    """(video id, mode) of the running session, or None when there is none (or it has no video)."""
    for game in ("falacias", "checker"):
        s = S.get_active_session(conn, game)
        if s is not None and s.video_id:
            return s.video_id, s.source_mode
    return None


def start_all(conn: psycopg.Connection, source: feed.Source) -> list[str]:
    """Start every game that is not running yet, on `source` (already validated by the caller). Returns the games started."""
    started = []
    for game in GAMES:
        try:
            if game == "falacias":
                feed.start_falacias_as_admin(conn, source)
            elif game == "checker":
                S.start_session_as_admin(conn, game_type="checker", video_id=source.video_id, source_mode=source.mode)
            else:
                S.start_session_as_admin(conn, game_type="bingo")
        except S.SessionAlreadyActive:
            continue
        started.append(game)
    return started


def finish_all(conn: psycopg.Connection) -> list[str]:
    """End every running game (winners are stored by each game's own rules). Returns the games ended."""
    ended = []
    for game, finish in (("bingo", scoring.finish_session), ("falacias", falacias.finish_session), ("checker", checker.finish_session)):
        s = S.get_active_session(conn, game)
        if s is not None and finish(conn, s.id) is not None:
            ended.append(game)
    return ended
