"""FastHTML web app: join/start a bingo session, play the card, live leaderboard.

Run:  uv run uvicorn app.main:app --reload      (health: GET /healthz)
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from urllib.parse import quote

from fasthtml.common import (
    H1,
    H2,
    A,
    Button,
    Div,
    FastHTML,
    Iframe,
    Img,
    Li,
    Link,
    Ol,
    P,
    Script,
    Small,
    Span,
    fast_app,
)
from psycopg_pool import ConnectionPool
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from app import admin_web, falacias_admin_web, falacias_web, livestream, phrases_web, timefmt, ui
from app import cards as C
from app import marks as M
from app import scoring as G
from app import sessions as S
from app import texts as T
from app.config import ROOT, database_url
from app.lines import line_boxes
from app.ui import (
    NAME_ERRORS,
    client_ip_of,
    error_box,
    name_gate,
    name_of,
    page,
    play_form,
    remember_name,
    set_token_cookie,
    token_of,
)

STATIC_DIR = ROOT / "app" / "static"
LEADERBOARD_TOP = 10
CHANNEL_URL = "https://www.youtube.com/@O_Poligrafo/streams"
POLL_CARDS = "every 5s"


# ------------------------------------------------------------------ views
def entry_page(active: S.GameSession | None, n_players: int = 0, error: str | None = None,
               name: str = ""):
    if active is None:
        panel = Div(H2(T.START_HEADING), P(T.START_HELP, cls="muted"),
                    play_form("/start", T.START_BUTTON, name, "/"), cls="panel")
    else:
        panel = Div(H2(T.JOIN_HEADING), P(T.JOIN_HELP.format(n=n_players), cls="muted"),
                    play_form("/join", T.JOIN_BUTTON, name, "/"), cls="panel")
    return page(H1(T.TITLE), P(T.TAGLINE, cls="tag"), error_box(error), panel)


def cell_size_cls(text: str) -> str:
    return "xlong" if len(text) > 48 else "long" if len(text) > 26 else ""


def board(card: C.Card, marked: dict[int, datetime], player_name: str, revoked: frozenset[int] = frozenset(),
          error: str | None = None):
    cells = []
    for i, cell in enumerate(card.cells):
        if "phrase_id" not in cell:                    # the avatar free cell (5x5 cards only)
            cells.append(Div(Img(src="/center.jpg", alt=T.AVATAR_ALT, width=160, height=160),
                             cls="cell free", aria_label=T.AVATAR_ALT))
        else:
            on = i in marked
            cls = " ".join(x for x in ("cell", "revoked" if i in revoked else "", cell_size_cls(cell["text"])) if x)
            cells.append(Button(
                cell["text"], Small(timefmt.hms(marked[i]), cls="at") if on else None, type="button", cls=cls,
                title=T.CELL_REVOKED if i in revoked else None,
                aria_pressed="true" if on else "false",
                hx_post=f"/mark/{i}", hx_target="#game-board", hx_swap="outerHTML"))
    return Div(
        Div(Span(f"{player_name} ({T.YOU})", cls="who"),
            Span(f"{T.MARKED}: {len(marked)}/{card.max_marks}", cls="count", id="count"),
            cls="bar"),
        Div(error, cls="cellerr", role="alert") if error else None,
        Div(*cells, *line_boxes(card.cells, marked), cls="board", lang="pt-BR", style=f"--cols: {card.size}"),
        id="game-board")


def readonly_card(pb: M.PlayerBoard, join_pos: int = 0, win_pos: int = 0):
    """Another player's card: same grid, no buttons. Marked cells are green; no times."""
    cells = []
    for i, cell in enumerate(pb.cells):
        if "phrase_id" not in cell:
            cells.append(Div(Img(src="/center.jpg", alt=T.AVATAR_ALT, width=160, height=160), cls="cell free ro"))
        else:
            cls = " ".join(x for x in ("cell", "ro", "on" if i in pb.marked else "", cell_size_cls(cell["text"])) if x)
            cells.append(Div(cell["text"], cls=cls, title=cell["text"]))     # title: phrase on hover when tiny
    pct = round(100 * len(pb.marked) / pb.max_marks) if pb.max_marks else 0
    return Div(Div(Span(pb.name, cls="who"), Span(f"{len(pb.marked)}/{pb.max_marks}", cls="count"), cls="bar"),
               Div(Span(style=f"width:{pct}%"), cls="meter"),       # shown in the list view only (CSS)
               Div(*cells, *line_boxes(pb.cells, pb.marked), cls="board", lang="pt-BR",
                   style=f"--cols: {C.grid_of(len(pb.cells))}"), cls="cardbox other",
               style=f"--o-join:{join_pos};--o-win:{win_pos}")      # CSS `order`: join order / most marked first


def boards_version(boards: list[M.PlayerBoard]) -> str:
    """Fingerprint of what the others' cards show (who + which cells are marked): unchanged -> poll answers 204."""
    h = hashlib.sha1()
    for b in boards:
        h.update(f"{b.user_id}|{b.name}|{sorted(b.marked)}\n".encode())
    return h.hexdigest()[:12]


def others_view(boards: list[M.PlayerBoard], version: str):
    # display: contents (CSS): the heading and the cards become direct items of the .cardgrid around this div
    # same ranking rule as scoring: most marks, then whoever reached that count first, then who joined first
    by_win = sorted(range(len(boards)), key=lambda i: (-len(boards[i].marked),
                                                       max(boards[i].marked.values(), default=datetime.max.replace(tzinfo=UTC)), i))
    win_pos = {i: pos for pos, i in enumerate(by_win)}
    return Div(H2(T.OTHERS_HEADING, cls="othershead") if boards else None,
               *[readonly_card(b, i, win_pos[i]) for i, b in enumerate(boards)],
               id="others",
               hx_get=f"/cards?v={version}", hx_trigger=POLL_CARDS, hx_swap="outerHTML")


HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


def stream_panel(video_id: str | None, known: bool):
    if video_id:
        body = Div(Iframe(
            src=f"https://www.youtube-nocookie.com/embed/{video_id}?autoplay=1&mute=1&playsinline=1&rel=0",
            title=T.STREAM_TITLE, allow="autoplay; encrypted-media; picture-in-picture; fullscreen",
            allowfullscreen=True, referrerpolicy="strict-origin-when-cross-origin"), cls="ratio")
    else:
        body = Div(T.STREAM_OFFLINE if known else T.STREAM_SEARCHING, cls="ratio offline", role="status")
    # polled lightly: 204 while nothing changed, HX-Refresh when the live id changed
    return Div(body, id="stream", cls="stream", hx_get=f"/stream?cur={video_id or ''}",
               hx_trigger="every 60s" if known or video_id else "every 5s", hx_swap="none")


def chat_panel(video_id: str | None, host: str):
    """YouTube's own live-chat embed (needs embed_domain = the host serving this page). The panel fills whatever
    height is left under the stream, so the left column ends exactly where my card ends."""
    if video_id:
        dom = f"&embed_domain={quote(host)}" if HOST_RE.match(host or "") else ""
        body = Div(Iframe(src=f"https://www.youtube.com/live_chat?v={video_id}{dom}&dark_theme=1",
                          title=T.CHAT_TITLE, referrerpolicy="strict-origin-when-cross-origin"), cls="chatbody")
        link = A(T.STREAM_OPEN, href=f"https://www.youtube.com/watch?v={video_id}", target="_blank", rel="noopener")
    else:
        body = Div(T.CHAT_OFFLINE, cls="chatbody offline", role="status")
        link = A(T.STREAM_CHANNEL, href=CHANNEL_URL, target="_blank", rel="noopener")
    return Div(Div(Span(T.CHAT_TITLE, cls="who"), link, cls="bar"), body, cls="chat panel", id="chat")


def leaderboard_view(standings: list[G.Standing], me: S.Player | None, top: int = LEADERBOARD_TOP):
    rows = standings[:top]
    if me and all(r.user_id != me.id for r in rows):
        rows = rows + [r for r in standings if r.user_id == me.id]
    items = [Li(Span(f"{r.rank}.", cls="pos"),
                Span(r.name + (f" ({T.YOU})" if me and r.user_id == me.id else ""), cls="nm"),
                Span(str(r.marked), cls="pts"),
                cls="me" if me and r.user_id == me.id else None) for r in rows]
    return Div(H2(T.LEADERBOARD), Ol(*items, cls="lb"), cls="panel", id="leaderboard",
               hx_get="/leaderboard", hx_trigger="every 5s", hx_swap="outerHTML")


def game_page(card: C.Card, marked: dict[int, datetime], me: S.Player, others: list[M.PlayerBoard],
              video_id: str | None, live_known: bool, host: str = "", revoked: frozenset[int] = frozenset()):
    """Top row (50/50): LEFT = live stream with the chat below it, RIGHT = MY card (RED border); both columns have
    the same top and bottom. Below: everybody else, smaller. No scoreboard: their cards show who is ahead."""
    return page(
        H1(T.TITLE), P(T.HOW_TO, cls="tag"),
        Div(Button(Span(T.LIVE_HIDE, cls="when-shown"), Span(T.LIVE_SHOW, cls="when-hidden"), type="button",
                   cls="pref", data_pref="live:toggle", aria_pressed="false"), cls="gamebar"),
        Div(Div(stream_panel(video_id, live_known), chat_panel(video_id, host), cls="leftcol"),
            Div(board(card, marked, me.name, revoked), cls="cardbox mine"), cls="toprow"),
        Div(Button("▦", type="button", cls="pref icon", data_pref="view:grid", aria_pressed="true",
                   title=T.VIEW_GRID, aria_label=T.VIEW_GRID),
            Button("☰", type="button", cls="pref icon", data_pref="view:list", aria_pressed="false",
                   title=T.VIEW_LIST, aria_label=T.VIEW_LIST),
            cls="gamebar others"),
        Div(others_view(others, boards_version(others)), cls="cardgrid small"),
        Script(src="/game.js", defer=True),
        cls="game")


def results_page(session: S.GameSession, me: S.Player, standings: list[G.Standing]):
    winner = next((s for s in standings if s.user_id == session.winner_user_id), None)
    mine = next((s for s in standings if s.user_id == me.id), None)
    return page(
        H1(T.GAME_OVER),
        Div(T.WINNER.format(name=winner.name, n=winner.marked) if winner else T.NO_WINNER,
            cls="winner"),
        P(T.YOUR_RESULT.format(n=mine.marked, rank=mine.rank), cls="muted") if mine else None,
        Div(H2(T.LEADERBOARD),
            Ol(*[Li(Span(f"{s.rank}.", cls="pos"), Span(s.name, cls="nm"),
                    Span(str(s.marked), cls="pts"), cls="me" if s.user_id == me.id else None)
                 for s in standings], cls="lb"), cls="panel"))


# ------------------------------------------------------------------ app
def create_app(db_url: str | None = None, live: livestream.LiveStream | None = None) -> FastHTML:
    url = db_url or database_url()
    live = live or livestream.LiveStream()
    pool = ConnectionPool(url, min_size=1, max_size=int(os.environ.get("POOL_MAX", "10")),
                          kwargs={"autocommit": True}, open=False)

    async def lifespan(app):   # FastHTML wraps an async GENERATOR function
        pool.open()
        pool.wait()
        live.current()          # start the first live-stream lookup in the background right away
        try:
            yield
        finally:
            pool.close()

    app, rt = fast_app(
        lifespan=lifespan, pico=False, htmx=False, surreal=False, canonical=False, title=T.TITLE,
        static_path=str(STATIC_DIR), secret_key=os.environ.get("SESSION_SECRET") or "dev-only-not-used",
        htmlkw={"lang": "pt-BR"},
        hdrs=(Link(rel="stylesheet", href="/app.css"), Script(src="/htmx.min.js")))   # self-hosted

    ip_of = client_ip_of

    def enter(request: Request, kind: str, name: str):
        """Shared start/join handler. Returns a redirect (with cookie) or the form with an error."""
        ip = ip_of(request)
        name = name or name_of(request) or ""
        with pool.connection() as conn:
            try:
                if kind == "start":
                    _, player, token = S.start_session(conn, name, ip)
                else:
                    _, player, token = S.join_session(conn, name, ip, existing_token=token_of(request))
                C.assign_card(conn, player.session_id, player.id)
            except C.NotEnoughPhrases:
                return entry_page(S.get_active_session(conn), error="no_phrases", name=name)
            except S.SessionError as e:
                if e.code in NAME_ERRORS:
                    return name_gate("/", error=e.code, value=name)
                active = S.get_active_session(conn)
                n = S.count_players(conn, active.id) if active else 0
                return entry_page(active, n, error=e.code, name=name)
        return remember_name(set_token_cookie(RedirectResponse("/", status_code=303), "bingo", token), player.name)

    @rt("/")
    def get(request: Request):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request))
            if me is not None:
                try:
                    card = C.assign_card(conn, me.session_id, me.id)   # lazy + idempotent
                except C.NotEnoughPhrases:
                    return entry_page(S.get_active_session(conn), error="no_phrases", name=me.name)
                others = [b for b in M.session_boards(conn, me.session_id) if b.user_id != me.id]
                return game_page(card, M.marked_times(conn, card.id), me, others, live.current(), live.known,
                                 request.url.hostname or "", M.revoked_cells(conn, card.id))
            active = S.get_active_session(conn)
            found = S.find_player(conn, token_of(request))
            if active is None and found and found[1].status == "ended":
                player, session = found
                return results_page(session, player, G.leaderboard(conn, session.id))
            name = name_of(request)
            if name is None:
                return name_gate("/")
            return entry_page(active, S.count_players(conn, active.id) if active else 0, name=name)

    @rt("/start", methods=["POST"])
    def start(request: Request, name: str = ""):
        return enter(request, "start", name)

    @rt("/join", methods=["POST"])
    def join(request: Request, name: str = ""):
        return enter(request, "join", name)

    @rt("/mark/{idx}", methods=["POST"])
    def mark(request: Request, idx: int):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request))
            if me is None:
                return Response(status_code=401, headers={"HX-Refresh": "true"})
            card = C.get_card(conn, me.session_id, me.id)
            try:
                M.toggle_mark(conn, me.id, idx)
            except M.CellRevoked:       # the admin removed this mark: show the board again with the reason
                assert card is not None
                return board(card, M.marked_times(conn, card.id), me.name, M.revoked_cells(conn, card.id),
                             error=T.CELL_REVOKED)
            except M.InvalidCell:
                return PlainTextResponse("invalid cell", status_code=400)
            except M.SessionNotActive:
                return Response(status_code=409, headers={"HX-Refresh": "true"})  # game just ended
            card = C.get_card(conn, me.session_id, me.id)
            assert card is not None
            return board(card, M.marked_times(conn, card.id), me.name, M.revoked_cells(conn, card.id))

    @rt("/leaderboard")
    def leaderboard(request: Request):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request))
            if me is None:   # session ended (or cookie unknown): reload -> results / entry page
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            return leaderboard_view(G.leaderboard(conn, me.session_id), me)

    @rt("/cards")
    def cards(request: Request, v: str = ""):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request))
            if me is None:   # session ended (or cookie unknown): reload -> results / entry page
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            others = [b for b in M.session_boards(conn, me.session_id) if b.user_id != me.id]
        version = boards_version(others)
        return Response(status_code=204) if version == v else others_view(others, version)

    @rt("/stream")
    def stream(cur: str = ""):
        now = live.current() or ""
        if now == cur:
            return Response(status_code=204)
        return Response(status_code=200, headers={"HX-Refresh": "true"})   # the live id changed: reload the page

    @rt("/healthz")
    def healthz():
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return PlainTextResponse("ok")

    admin_web.register(rt, pool)
    phrases_web.register(rt, pool)
    falacias_web.register(rt, pool, live)
    falacias_admin_web.register(rt, pool, live)
    ui.register(rt)
    return app


def __getattr__(name: str):
    # `uvicorn app.main:app` builds the app lazily so importing this module needs no DB.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
