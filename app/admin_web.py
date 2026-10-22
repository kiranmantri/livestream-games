"""Admin page: GET /admin (users + cards of the current session), POST /admin/finish."""
from __future__ import annotations

import uuid
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from fasthtml.common import (
    H1,
    H2,
    A,
    Button,
    Details,
    Div,
    Form,
    Input,
    Label,
    Main,
    Nav,
    P,
    Script,
    Small,
    Span,
    Summary,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Time,
    Title,
    Tr,
)
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from app import admin as A_
from app import cards as C
from app import marks as M
from app import scoring as G
from app import sessions as S
from app import texts as T
from app import timefmt
from app.lines import complete_lines, is_full, line_boxes
from app.ui import client_ip_of

_ts = timefmt.dmy_hms


def _cell_form(action: str, session_id: uuid.UUID, user_id: uuid.UUID, cell: int, token: str, label: str,
               confirm: str | None = None):
    return Form(Input(type="hidden", name="session_id", value=str(session_id)),
                Input(type="hidden", name="user_id", value=str(user_id)),
                Input(type="hidden", name="cell", value=str(cell)),
                Input(type="hidden", name="csrf", value=token),
                Button(label, type="submit", cls="mcbtn", title=label),
                # htmx: swap only THIS player's card (+ the users table, out of band); no page reload, no scroll jump.
                # Without htmx it is still a plain POST + redirect.
                method="post", action=action, cls="mcform", hx_post=action, hx_target=f"#card-{user_id}",
                hx_swap="outerHTML", hx_confirm=confirm)


def star(row: A_.PlayerRow):
    """Yellow star next to the player's name once they completed any row, column or diagonal (or the whole card)."""
    found = complete_lines(row.cells, row.marked)
    if not found:
        return None
    full = is_full(row.cells, row.marked)
    tip = f"{len(found)} {T.ADMIN_STAR_LINE if len(found) == 1 else T.ADMIN_STAR_LINES}" + (f", {T.ADMIN_STAR_FULL}" if full else "")
    return Span("★", cls="star" + (" full" if full else ""), title=tip, aria_label=tip, role="img")


def mini_card(row: A_.PlayerRow, session_id: uuid.UUID | None = None, token: str = "", active: bool = False):
    """The player's card for the admin. While the game is ACTIVE every marked cell has an unmark button (illegal
    marking) and every blocked cell a button to lift the block."""
    cells = []
    for i, cell in enumerate(row.cells):
        if "phrase_id" not in cell:
            cells.append(Div("★", cls="mc free", title=T.AVATAR_ALT))
            continue
        on = i in row.marked
        blocked = i in row.revoked
        ctl = None
        if active and session_id is not None:
            if on:
                ctl = _cell_form("/admin/unmark", session_id, row.user_id, i, token, "✕ " + T.ADMIN_UNMARK,
                                 T.ADMIN_UNMARK_CONFIRM)
            elif blocked:
                ctl = _cell_form("/admin/unblock", session_id, row.user_id, i, token, T.ADMIN_UNBLOCK)
        cells.append(Div(cell["text"], Small(timefmt.hms(row.marked_at[i]), cls="at") if on else None,
                         Small(T.ADMIN_BLOCKED, cls="at") if blocked else None, ctl,
                         cls="mc" + (" on" if on else "") + (" revoked" if blocked else "")))
    return Div(*cells, *line_boxes(row.cells, row.marked), cls="mini", lang="pt-BR",
               style=f"--cols: {C.grid_of(len(row.cells))}")


# ------------------------------------------------------------------ users table: sortable columns
SORTS = {   # column -> (header text, key function(row, rank), direction used on the first click)
    "rank": (T.ADMIN_COL_RANK, lambda r, rank: rank.get(r.user_id, 10 ** 9), "asc"),
    "name": (T.ADMIN_COL_NAME, lambda r, rank: r.name.casefold(), "asc"),
    "marked": (T.ADMIN_COL_MARKED, lambda r, rank: len(r.marked), "desc"),
    "joined": (T.ADMIN_COL_JOINED, lambda r, rank: r.joined_at, "asc"),
    "seen": (T.ADMIN_COL_SEEN, lambda r, rank: r.last_seen_at, "desc"),
}


def sort_rows(rows: list[A_.PlayerRow], rank: dict, sort: str, direction: str) -> tuple[list[A_.PlayerRow], str, str]:
    """Rows ordered by a whitelisted column (unknown values fall back to rank, ascending). -> (rows, sort, dir)."""
    if sort not in SORTS:
        sort, direction = "rank", "asc"
    if direction not in ("asc", "desc"):
        direction = SORTS[sort][2]
    key = SORTS[sort][1]
    return sorted(rows, key=lambda r: (key(r, rank), r.name.casefold()), reverse=direction == "desc"), sort, direction


def sort_header(col: str, sort: str, direction: str):
    label = SORTS[col][0]
    if col == sort:
        nxt = "desc" if direction == "asc" else "asc"
        return Th(A(f"{label} {'▲' if direction == 'asc' else '▼'}", href=f"/admin?sort={col}&dir={nxt}",
                    cls="sortlink on"), aria_sort="ascending" if direction == "asc" else "descending")
    return Th(A(label, href=f"/admin?sort={col}&dir={SORTS[col][2]}", cls="sortlink"))


def start_form(start_token: str):
    return Form(Input(type="hidden", name="csrf", value=start_token),
                Button(T.ADMIN_START, type="submit", cls="primary"),
                method="post", action="/admin/start", cls="startform")


def ip_limit_form(limit: int, token: str, bad: bool = False):
    return Form(
        Label(T.ADMIN_IPLIMIT_LABEL, Input(type="number", name="limit", value=str(limit), min="1", max=str(S.IP_LIMIT_MAX),
                                           required=True, inputmode="numeric")),
        Input(type="hidden", name="csrf", value=token),
        Button(T.ADMIN_IPLIMIT_SAVE, type="submit"),
        Small(T.ADMIN_IPLIMIT_HELP, cls="muted"),
        P(T.ADMIN_IPLIMIT_BAD, cls="error", role="alert") if bad else None,
        method="post", action="/admin/settings/ip-limit", cls="iplimit")


def users_panel(rows: list[A_.PlayerRow], rank: dict, sort: str = "rank", direction: str = "asc", oob: bool = False,
                poll: bool = False):
    """The users table. While the game is active it re-reads itself every 5 s (keeping the sort order)."""
    table_rows, sort, direction = sort_rows(rows, rank, sort, direction)
    table = Table(
        Thead(Tr(*[sort_header(c, sort, direction) for c in SORTS])),
        Tbody(*[Tr(Td(str(rank.get(r.user_id, "-")), data_label=SORTS["rank"][0]), Td(r.name, star(r), data_label=SORTS["name"][0]),
                   Td(str(len(r.marked)), data_label=SORTS["marked"][0]), Td(_ts(r.joined_at), data_label=SORTS["joined"][0]),
                   Td(_ts(r.last_seen_at), data_label=SORTS["seen"][0])) for r in table_rows]),
        cls="users stack")
    poll_attrs = {"hx_get": f"/admin/live?sort={sort}&dir={direction}", "hx_trigger": "every 5s", "hx_swap": "outerHTML"} if poll else {}
    return Div(H2(T.ADMIN_USERS), table if rows else P(T.ADMIN_NO_USERS, cls="muted"), cls="panel", id="users-panel",
               hx_swap_oob="outerHTML" if oob else None, **poll_attrs)


def card_panel(r: A_.PlayerRow, session_id: uuid.UUID, token: str, active: bool, open_: bool = False):
    return Details(Summary(f"{r.name} - {len(r.marked)}/{C.max_marks(r.cells)}", star(r)),
                   mini_card(r, session_id, token, active), cls="panel", open=open_ or None, id=f"card-{r.user_id}")


def admin_page(session: S.GameSession, rows: list[A_.PlayerRow], standings: list[G.Standing],
               token: str, start_token: str, sort: str = "rank", direction: str = "asc", open_uid: str = "",
               settings=None):
    rank = {s.user_id: s.rank for s in standings}
    active = session.status == "active"
    winner = next((r for r in rows if r.user_id == session.winner_user_id), None)
    finish = Form(
        Input(type="hidden", name="session_id", value=str(session.id)),
        Input(type="hidden", name="csrf", value=token),
        Button(T.ADMIN_FINISH, type="submit", cls="primary danger"),
        method="post", action="/admin/finish",
        onsubmit=f"return confirm({T.ADMIN_FINISH_CONFIRM!r})") if active else None
    bar = status_bar(session, len(rows), finish, "/admin", winner.name if winner else None)
    cards = [card_panel(r, session.id, token, active, str(r.user_id) == open_uid) for r in rows]
    return (Title(f"{T.TITLE} - admin"),
            Main(admin_tabs("bingo"), H1(f"{T.TITLE} - admin"), bar,
                 start_form(start_token) if not active else "",
                 users_panel(rows, rank, sort, direction, poll=active),
                 H2(T.ADMIN_CARDS), *cards, settings, Script(src="/admin.js", defer=True), cls="wide"))


def admin_tabs(active: str):
    """Two groups: the games being played, and the content behind them (phrases, fallacies)."""
    games = (("bingo", T.ADMIN_TAB_BINGO, "/admin"), ("falacias", T.ADMIN_TAB_FALACIAS, "/admin/falacias"))
    content = (("phrases", T.ADMIN_TAB_PHRASES, "/admin/phrases"), ("fallacies", T.ADMIN_TAB_FALLACIES, "/admin/fallacies"))

    def links(items):
        return [A(label, href=href, cls="active" if key == active else None,
                  aria_current="page" if key == active else None) for key, label, href in items]
    return Nav(Span(T.ADMIN_GROUP_GAMES, cls="admingroup"), *links(games),
               Span(T.ADMIN_GROUP_CONTENT, cls="admingroup"), *links(content),
               cls="menu admintabs", aria_label="admin")


def status_bar(session: S.GameSession, n_players: int, finish=None, refresh_href: str | None = None, winner: str | None = None):
    """Sticky bar of an admin game page: state, start time, players, elapsed time (admin.js keeps it ticking), the end button at the right."""
    active = session.status == "active"
    since = int(session.created_at.timestamp() * 1000)
    seconds = (datetime.now(session.created_at.tzinfo) - session.created_at).total_seconds()
    return Div(
        Span(T.ADMIN_ACTIVE if active else T.ADMIN_ENDED, cls="pill on" if active else "pill"),
        Span(f"{T.ADMIN_STARTED}: {_ts(session.created_at)}", cls="muted started"),
        Span(f"{T.ADMIN_ENDED_AT}: {_ts(session.ended_at)}", cls="muted") if session.ended_at else None,
        player_count(n_players),
        Span(f"{T.ADMIN_ELAPSED}: ", Time(timefmt.elapsed(seconds), cls="elapsed", data_since=str(since)), cls="muted") if active else None,
        Span(f"{T.ADMIN_WINNER}: {winner}", cls="muted") if winner else None,
        A(T.ADMIN_REFRESH, href=refresh_href, cls="btn small") if refresh_href else None,
        Span(T.ADMIN_UPDATED, id="updated", cls="muted") if active and refresh_href else None,
        Span(cls="grow"), finish,
        id="statusbar", cls="adminbar")


def player_count(n: int, oob: bool = False):
    return Span(f"{T.ADMIN_PLAYERS}: {n}", id="playercount", cls="muted", hx_swap_oob="true" if oob else None)


def denied(request: Request):
    """None when the request is authorised, else the response to send (401 / 503)."""
    pw = A_.admin_password()
    if pw is None:
        return PlainTextResponse("admin disabled: set ADMIN_PASSWORD in the environment", status_code=503)
    ip = client_ip_of(request)
    wait = A_.lock_remaining(ip)
    if wait:
        return PlainTextResponse(T.ADMIN_LOCKED, status_code=429, headers={"Retry-After": str(wait)})
    header = request.headers.get("authorization")
    if not A_.check_basic(header, pw):
        if header:                         # a request without credentials is the browser's first call, not an attack
            A_.record_failure(ip)
        return Response("authentication required", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="Live Games admin", charset="UTF-8"'})
    A_.record_success(ip)
    return None


def register(rt, pool):
    @rt("/admin")
    def admin_get(request: Request, sort: str = "rank", dir: str = "", open: str = "", err: str = ""):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            session = A_.current_session(conn)
            pw = A_.admin_password() or ""
            settings = ip_limit_form(S.max_users_per_ip(conn), A_.settings_token(pw), err == "ip_limit")
            if session is None:
                return Main(admin_tabs("bingo"), H1(f"{T.TITLE} - admin"), P(T.ADMIN_NO_SESSION, cls="muted"),
                            start_form(A_.start_token(pw)),
                            settings,
                            Div(A(T.ADMIN_PHRASES_LINK, href="/admin/phrases", cls="btn"),
                                A(T.ADMIN_REFRESH, href="/admin", cls="btn"), cls="adminbar"), cls="wide")
            return admin_page(session, A_.players(conn, session.id), G.leaderboard(conn, session.id),
                              A_.csrf_token(pw, session.id), A_.start_token(pw), sort, dir, open, settings)

    @rt("/admin/live")
    def admin_live(request: Request, sort: str = "rank", dir: str = ""):
        """Polled every 5 s by the users table: the table again + the player count (out of band). The cards are NOT redrawn:
        an open card would collapse under the admin's hands."""
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            session = A_.current_session(conn)
            if session is None or session.status != "active":
                return Response(status_code=200, headers={"HX-Refresh": "true"})      # the game ended: show the final page
            rows = A_.players(conn, session.id)
            rank = {s.user_id: s.rank for s in G.leaderboard(conn, session.id)}
            return (users_panel(rows, rank, sort, dir, poll=True), player_count(len(rows), oob=True))

    @rt("/admin/settings/ip-limit", methods=["POST"])
    def admin_ip_limit(request: Request, limit: str = "", csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        if not A_.settings_ok(A_.admin_password() or "", csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            try:
                S.set_max_users_per_ip(conn, limit)
            except ValueError:
                return RedirectResponse("/admin?err=ip_limit", status_code=303)
        return RedirectResponse("/admin", status_code=303)

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

    def _cell_action(request: Request, session_id: str, user_id: str, cell: str, csrf: str, action):
        if (d := denied(request)) is not None:
            return d
        try:
            sid, uid, idx = uuid.UUID(session_id), uuid.UUID(user_id), int(cell)
        except ValueError:
            return PlainTextResponse("bad request", status_code=400)
        if not A_.csrf_ok(A_.admin_password() or "", sid, csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            action(conn, sid, uid, idx)        # False = nothing to do (game over / not marked): harmless
            if request.headers.get("hx-request") != "true":
                return RedirectResponse(f"/admin?open={uid}", status_code=303)
            # htmx: send back just this player's card plus the users table (marks, rank and star changed)
            session = A_.current_session(conn)
            rows = A_.players(conn, session.id) if session else []
            row = next((r for r in rows if r.user_id == uid), None)
            if session is None or row is None or session.id != sid:
                return Response("", status_code=200, headers={"HX-Redirect": "/admin"})
            q = parse_qs(urlparse(request.headers.get("hx-current-url", "")).query)      # keep the table's sort order
            rank = {s.user_id: s.rank for s in G.leaderboard(conn, session.id)}
            pw = A_.admin_password() or ""
            return (card_panel(row, session.id, A_.csrf_token(pw, session.id), session.status == "active", True),
                    users_panel(rows, rank, q.get("sort", ["rank"])[0], q.get("dir", [""])[0], oob=True,
                              poll=session.status == "active"))

    @rt("/admin/unmark", methods=["POST"])
    def admin_unmark(request: Request, session_id: str = "", user_id: str = "", cell: str = "", csrf: str = ""):
        return _cell_action(request, session_id, user_id, cell, csrf, M.revoke_mark)

    @rt("/admin/unblock", methods=["POST"])
    def admin_unblock(request: Request, session_id: str = "", user_id: str = "", cell: str = "", csrf: str = ""):
        return _cell_action(request, session_id, user_id, cell, csrf, M.lift_revoke)
