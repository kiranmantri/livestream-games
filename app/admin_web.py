"""Admin page: GET /admin (users + cards of the current session), POST /admin/finish."""
from __future__ import annotations

import uuid

from fasthtml.common import (
    H1,
    H2,
    A,
    Button,
    Details,
    Div,
    Form,
    Input,
    Main,
    P,
    Small,
    Span,
    Summary,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Title,
    Tr,
)
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from app import admin as A_
from app import cards as C
from app import scoring as G
from app import sessions as S
from app import texts as T
from app import timefmt

_ts = timefmt.dmy_hms


def mini_card(row: A_.PlayerRow):
    cells = []
    for i in range(C.GRID * C.GRID):
        if i == C.CENTER_INDEX:
            cells.append(Div("★", cls="mc free", title=T.AVATAR_ALT))
        else:
            text = row.cells[i]["text"] if i < len(row.cells) else "?"
            on = i in row.marked
            cells.append(Div(text, Small(timefmt.hms(row.marked_at[i]), cls="at") if on else None,
                             cls="mc on" if on else "mc"))
    return Div(*cells, cls="mini", lang="pt-BR")


def start_form(start_token: str):
    return Form(Input(type="hidden", name="csrf", value=start_token),
                Button(T.ADMIN_START, type="submit", cls="primary"),
                method="post", action="/admin/start", cls="startform")


def admin_page(session: S.GameSession, rows: list[A_.PlayerRow], standings: list[G.Standing],
               token: str, start_token: str):
    rank = {s.user_id: s.rank for s in standings}
    active = session.status == "active"
    winner = next((r for r in rows if r.user_id == session.winner_user_id), None)
    head = Div(
        Span(T.ADMIN_ACTIVE if active else T.ADMIN_ENDED, cls="pill on" if active else "pill"),
        Span(f"{T.ADMIN_STARTED}: {_ts(session.created_at)}", cls="muted"),
        Span(f"{T.ADMIN_ENDED_AT}: {_ts(session.ended_at)}", cls="muted") if session.ended_at else None,
        Span(f"{T.ADMIN_PLAYERS}: {len(rows)}", cls="muted"),
        Span(f"{T.ADMIN_WINNER}: {winner.name}", cls="muted") if winner else None,
        A(T.ADMIN_PHRASES_LINK, href="/admin/phrases", cls="btn small"),
        cls="adminbar")
    finish = Form(
        Input(type="hidden", name="session_id", value=str(session.id)),
        Input(type="hidden", name="csrf", value=token),
        Button(T.ADMIN_FINISH, type="submit", cls="primary danger"),
        method="post", action="/admin/finish",
        onsubmit=f"return confirm({T.ADMIN_FINISH_CONFIRM!r})") if active else None
    table = Table(
        Thead(Tr(*[Th(h) for h in (T.ADMIN_COL_RANK, T.ADMIN_COL_NAME, T.ADMIN_COL_MARKED, "IP",
                                   T.ADMIN_COL_JOINED, T.ADMIN_COL_SEEN)])),
        Tbody(*[Tr(Td(str(rank.get(r.user_id, "-"))), Td(r.name), Td(str(len(r.marked))), Td(r.ip),
                   Td(_ts(r.joined_at)), Td(_ts(r.last_seen_at))) for r in rows]),
        cls="users")
    cards = [Details(Summary(f"{r.name} - {len(r.marked)}/{C.PHRASE_CELLS}"), mini_card(r), cls="panel")
             for r in rows]
    return (Title(f"{T.TITLE} - admin"),
            Main(H1(f"{T.TITLE} - admin"), head, finish or "",
                 start_form(start_token) if not active else "",
                 Div(H2(T.ADMIN_USERS), table if rows else P(T.ADMIN_NO_USERS, cls="muted"), cls="panel"),
                 H2(T.ADMIN_CARDS), *cards, A(T.ADMIN_REFRESH, href="/admin", cls="btn"), cls="wide"))


def denied(request: Request):
    """None when the request is authorised, else the response to send (401 / 503)."""
    pw = A_.admin_password()
    if pw is None:
        return PlainTextResponse("admin disabled: set ADMIN_PASSWORD in the environment", status_code=503)
    if not A_.check_basic(request.headers.get("authorization"), pw):
        return Response("authentication required", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="Live Games admin", charset="UTF-8"'})
    return None


def register(rt, pool):
    @rt("/admin")
    def admin_get(request: Request):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            session = A_.current_session(conn)
            pw = A_.admin_password() or ""
            if session is None:
                return Main(H1(f"{T.TITLE} - admin"), P(T.ADMIN_NO_SESSION, cls="muted"),
                            start_form(A_.start_token(pw)),
                            A(T.ADMIN_PHRASES_LINK, href="/admin/phrases", cls="btn small"),
                            A(T.ADMIN_REFRESH, href="/admin", cls="btn"), cls="wide")
            return admin_page(session, A_.players(conn, session.id), G.leaderboard(conn, session.id),
                              A_.csrf_token(pw, session.id), A_.start_token(pw))

    @rt("/admin/start", methods=["POST"])
    def admin_start(request: Request, csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        if not A_.start_ok(A_.admin_password() or "", csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            try:
                S.start_session_as_admin(conn)
            except S.SessionAlreadyActive:
                pass                       # already running (double click / a player was faster): fine
        return RedirectResponse("/admin", status_code=303)

    @rt("/admin/finish", methods=["POST"])
    def admin_finish(request: Request, session_id: str = "", csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            return PlainTextResponse("bad session id", status_code=400)
        if not A_.csrf_ok(A_.admin_password() or "", sid, csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            G.finish_session(conn, sid)        # None if already finished: harmless double click
        return RedirectResponse("/admin", status_code=303)
