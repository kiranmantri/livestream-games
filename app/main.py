"""FastHTML web app: join/start a bingo session, play the card, live leaderboard.

Run:  uv run uvicorn app.main:app --reload      (health: GET /healthz)
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from fasthtml.common import (
    H1,
    H2,
    A,
    Button,
    Div,
    FastHTML,
    Form,
    Iframe,
    Img,
    Input,
    Label,
    Li,
    Link,
    Ol,
    Option,
    P,
    Script,
    Select,
    Small,
    Span,
    Strong,
    fast_app,
    to_xml,
)
from psycopg_pool import ConnectionPool
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from app import (
    admin_web,
    checker_admin_web,
    checker_web,
    checkworker,
    falacias_admin_web,
    falacias_web,
    fallacies_web,
    livestream,
    phrases_web,
    replaycollector,
    timefmt,
    ui,
)
from app import cards as C
from app import marks as M
from app import scoring as G
from app import sessions as S
from app import swaps as W
from app import texts as T
from app.config import ROOT, database_url
from app.lines import is_full, line_boxes
from app.ui import (
    NAME_ERRORS,
    URL_ERRORS,
    client_ip_of,
    error_box,
    name_gate,
    name_of,
    page,
    play_form,
    remember_name,
    set_token_cookie,
    token_of,
    url_error,
)

# The app's own log lines (workers: "fact-checker on: ...", "stt recovered", failures) go to stderr at INFO. Without this, nothing configured
# logging and under uvicorn only WARNING+ came out (Python's last-resort handler). Records still propagate (pytest's caplog reads them at the root).
_app_log = logging.getLogger("app")
if not _app_log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    _app_log.addHandler(_h)
    _app_log.setLevel(logging.INFO)

STATIC_DIR = ROOT / "app" / "static"
LEADERBOARD_TOP = 10
CHANNEL_URL = "https://www.youtube.com/@O_Poligrafo/streams"
POLL_CARDS = "every 5s"


# ------------------------------------------------------------------ views
def not_found(req, exc):
    return page(H1(T.NOT_FOUND), P(A(T.BACK_HOME, href="/")), title=T.SITE_TITLE, tabs=False)


def entry_page(active: S.GameSession | None, n_players: int = 0, error: str | None = None,
               name: str = ""):
    if active is None:
        panel = Div(H2(T.START_HEADING), P(T.START_HELP, cls="muted"),
                    play_form("/start", T.START_BUTTON, name, "/"), cls="panel")
    else:
        panel = Div(H2(T.JOIN_HEADING), P(T.players_in_game(n_players), cls="muted"),
                    play_form("/join", T.JOIN_BUTTON, name, "/"), cls="panel")
    return page(H1(T.TITLE), P(T.TAGLINE, cls="tag"), error_box(error), panel, who=name or None)


def cell_size_cls(text: str) -> str:
    return "xlong" if len(text) > 48 else "long" if len(text) > 26 else ""


_KIND_RE = re.compile(r"^(.*\S)\s*\((falácia[^)]*)\)\s*$")


def split_kind(text: str) -> tuple[str, str | None]:
    """'ad populum (falácia)' -> ('ad populum', 'falácia'): the repeated word becomes a small tag under the phrase."""
    m = _KIND_RE.match(text)
    return (m.group(1), m.group(2)) if m else (text, None)


def cell_button(i: int, cell: dict, marked: dict[int, datetime], revoked: frozenset[int],
                locked: frozenset[int] = frozenset()):
    on = i in marked
    lock = on and i in locked                       # confirmed by the admin: game.js never sends a tap, the server refuses one
    text, kind = split_kind(cell["text"])
    cls = " ".join(x for x in ("cell", "revoked" if i in revoked else "", "locked" if lock else "", cell_size_cls(text)) if x)
    return Button(
        text, Small(kind, cls="kind") if kind else None, Small(timefmt.hms(marked[i]), cls="at") if on else None,
        type="button", cls=cls, id=f"cell-{i}",
        title=T.CELL_REVOKED if i in revoked else T.CELL_LOCKED if lock else None,
        aria_pressed="true" if on else "false",
        hx_post=f"/mark/{i}", hx_target="this", hx_swap="outerHTML")     # a tap swaps only this cell (+ count and lines, out of band);
                                                                         # game.js sends the taps one at a time (this is the no-JS-queue fallback)


def count_view(marked: dict[int, datetime], card: C.Card, oob: bool = False):
    pct = round(100 * len(marked) / card.max_marks) if card.max_marks else 0
    bar = Span(Span(style=f"width:{pct}%"), cls="progress", role="progressbar", aria_label=T.MARKED, aria_valuemin="0",
               aria_valuenow=str(len(marked)), aria_valuemax=str(card.max_marks))
    return Span(f"{T.MARKED}: {len(marked)}/{card.max_marks}", bar, cls="count", id="count", hx_swap_oob="true" if oob else None)


def lines_view(card: C.Card, marked: dict[int, datetime], oob: bool = False):
    return Div(*line_boxes(card.cells, marked), cls="lines", id="lines", hx_swap_oob="true" if oob else None)


def card_version(card: C.Card) -> str:
    """Changes when the card's phrases change (a swap moves another card's content onto this card row)."""
    return hashlib.sha1(repr([c.get("phrase_id") for c in card.cells]).encode()).hexdigest()[:10]


def board_sig(marked, locked: frozenset[int], revoked: frozenset[int], cv: str = "", right: int | None = None) -> str:
    """What my board shows, as game.js reads it back (marked / locked / blocked cells, the card version, the open swap offer):
    the /myboard poll answers 204 while the screen and the DB agree."""
    def j(xs):
        return ",".join(str(i) for i in sorted(xs))
    return f"m={j(marked)};l={j(set(locked) & set(marked))};r={j(revoked)};c={cv};s={right or ''}"


@dataclass(frozen=True)
class MyBoard:
    """Everything my own board shows, read in one place (game page, tap answers, the /myboard poll)."""
    marked: dict
    revoked: frozenset
    locked: frozenset
    right: W.Right | None
    targets: list
    last_swap: tuple | None

    def sig(self, card: C.Card) -> str:
        return board_sig(self.marked, self.locked, self.revoked, card_version(card), self.right.id if self.right else None)


def my_state(conn, card: C.Card, me: S.Player) -> MyBoard:
    right = W.open_right(conn, me.id)
    return MyBoard(M.marked_times(conn, card.id), M.revoked_cells(conn, card.id), M.confirmed_cells(conn, card.id),
                   right, W.targets(conn, right) if right else [], W.last_swap(conn, me.id))


def swap_box(right: W.Right, targets: list, max_marks: int):
    """I earned a card swap (never says which keyword). Plain forms: the answer reloads the page with my new card."""
    pick = Form(Input(type="hidden", name="right", value=str(right.id)),
                Label(T.SWAP_PICK, Select(*[Option(f"{t.name} ({t.marked}/{max_marks})", value=str(t.user_id)) for t in targets],
                                          name="target")),
                Button(T.SWAP_BUTTON, type="submit", cls="primary"),
                method="post", action="/swap", cls="swappick", onsubmit=f"return confirm({T.SWAP_CONFIRM!r})") if targets \
        else P(T.SWAP_NOBODY, cls="muted")
    return Div(Strong(T.SWAP_OFFER), P(T.SWAP_HELP, cls="muted"), pick,
               Form(Input(type="hidden", name="right", value=str(right.id)),
                    Button(T.SWAP_DECLINE, type="submit", cls="link"), method="post", action="/swap/decline", cls="swapno"),
               id="swapbox", cls="swapbox", data_right=str(right.id))


def swap_note(last: tuple | None):
    if not last:
        return None
    kind, name, at = last
    return Div((T.SWAP_DONE if kind == "made" else T.SWAP_RECEIVED).format(name=name, at=timefmt.hms(at)), cls="swapnote",
               role="status")


def my_board(card: C.Card, st: MyBoard, player_name: str, error: str | None = None):
    return board(card, st.marked, player_name, st.revoked, error, st.locked,
                 swap=swap_box(st.right, st.targets, card.max_marks) if st.right else None, note=swap_note(st.last_swap))


def board(card: C.Card, marked: dict[int, datetime], player_name: str, revoked: frozenset[int] = frozenset(),
          error: str | None = None, locked: frozenset[int] = frozenset(), swap=None, note=None):
    cells = []
    for i, cell in enumerate(card.cells):
        if "phrase_id" not in cell:                    # the avatar free cell (5x5 cards only)
            cells.append(Div(Img(src="/center.jpg", alt=T.AVATAR_ALT, width=160, height=160),
                             cls="cell free", aria_label=T.AVATAR_ALT))
        else:
            cells.append(cell_button(i, cell, marked, revoked, locked))
    return Div(
        Div(Span(f"{player_name} ({T.YOU})", cls="who"), count_view(marked, card), cls="bar"),
        Div(error, cls="cellerr", role="alert") if error else None,
        swap, note,
        Div(*cells, lines_view(card, marked), cls="board", lang="pt-BR", style=f"--cols: {card.size}"),
        id="game-board", data_cv=card_version(card))


def final_board(card: C.Card, marked: dict[int, datetime], player_name: str):
    """My card on the end screen: the game's board (same classes, marked times, blue lines) with no buttons and no ids,
    so game.js's tap handler and htmx have nothing to send."""
    cells = []
    for i, cell in enumerate(card.cells):
        if "phrase_id" not in cell:
            cells.append(Div(Img(src="/center.jpg", alt=T.AVATAR_ALT, width=160, height=160), cls="cell free", aria_label=T.AVATAR_ALT))
        else:
            on = i in marked
            text, kind = split_kind(cell["text"])
            cells.append(Div(text, Small(kind, cls="kind") if kind else None, Small(timefmt.hms(marked[i]), cls="at") if on else None,
                             cls=" ".join(x for x in ("cell", "ro", cell_size_cls(text)) if x), aria_pressed="true" if on else "false"))
    return Div(Div(Span(f"{player_name} ({T.YOU})", cls="who"), count_view(marked, card), cls="bar"),
               Div(*cells, lines_view(card, marked), cls="board", lang="pt-BR", style=f"--cols: {card.size}"),
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


def others_view(boards: list[M.PlayerBoard], version: str, poll: bool = True):
    # display: contents (CSS): the heading and the cards become direct items of the .cardgrid around this div
    # same ranking rule as scoring: most marks, then whoever reached that count first, then who joined first
    by_win = sorted(range(len(boards)), key=lambda i: (-len(boards[i].marked),
                                                       max(boards[i].marked.values(), default=datetime.max.replace(tzinfo=UTC)), i))
    win_pos = {i: pos for pos, i in enumerate(by_win)}
    return Div(H2(T.OTHERS_HEADING, cls="othershead") if boards else None,
               *[readonly_card(b, i, win_pos[i]) for i, b in enumerate(boards)],
               id="others",
               **({"hx_get": f"/cards?v={version}", "hx_trigger": POLL_CARDS, "hx_swap": "outerHTML"} if poll else {}))   # the end screen is static


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


def game_page(card: C.Card, st: MyBoard, me: S.Player, others: list[M.PlayerBoard],
              video_id: str | None, live_known: bool, host: str = "", error: str | None = None):
    """Top row (50/50): LEFT = live stream with the chat below it, RIGHT = MY card (RED border); both columns have
    the same top and bottom. Below: everybody else, smaller. No scoreboard: their cards show who is ahead."""
    return page(
        H1(T.TITLE), P(T.HOW_TO, cls="tag"),
        Div(Button(Span(T.LIVE_HIDE, cls="when-shown"), Span(T.LIVE_SHOW, cls="when-hidden"), type="button",
                   cls="pref", data_pref="live:toggle", aria_pressed="false"),
            None if video_id else Span(T.OFFLINE_NOTE, cls="offline-note"), cls=f"gamebar{'' if video_id else ' nolive'}"),
        Div(Div(stream_panel(video_id, live_known), chat_panel(video_id, host), cls="leftcol"),
            Div(my_board(card, st, me.name, error), cls="cardbox mine"), cls="toprow"),
        Div(Button("▦", type="button", cls="pref icon", data_pref="view:grid", aria_pressed="true",
                   title=T.VIEW_GRID, aria_label=T.VIEW_GRID),
            Button("☰", type="button", cls="pref icon", data_pref="view:list", aria_pressed="false",
                   title=T.VIEW_LIST, aria_label=T.VIEW_LIST),
            cls="gamebar others"),
        Div(others_view(others, boards_version(others)), cls="cardgrid small"),
        Div(id="cardzoom", cls="cardzoom", hidden=True, aria_hidden="true"),   # game.js: hovered card at 2x
        Script(src="/fx.js", defer=True),                                      # mark / line / full-card effects
        Script(src="/game.js", defer=True),
        cls="game", who=me.name)


def results_page(session: S.GameSession, me: S.Player, standings: list[G.Standing], card: C.Card | None = None,
                 marked: dict[int, datetime] | None = None, others: list[M.PlayerBoard] = ()):
    """The game screen after the end: the result message, MY card, then everybody else's cards (grid / list toggle and
    the 2x hover zoom of game.js). Nothing can be clicked: no marks, no polling, no stream and chat."""
    winner = next((s for s in standings if s.user_id == session.winner_user_id), None)
    mine = next((s for s in standings if s.user_id == me.id), None)
    others = list(others)
    # I won with every cell marked: the full-card fireworks of the game go on here, forever (fx.js)
    fireworks = card is not None and winner is not None and winner.user_id == me.id and is_full(card.cells, marked or {})
    return page(
        H1(T.GAME_OVER),
        Div(T.WINNER.format(name=winner.name, n=winner.marked) if winner else T.NO_WINNER,
            cls="winner"),
        P(T.YOUR_RESULT.format(n=mine.marked, rank=mine.rank), cls="muted") if mine else None,
        Div(Div(final_board(card, marked or {}, me.name), cls="cardbox mine"), cls="toprow") if card is not None else None,
        Div(Button("▦", type="button", cls="pref icon", data_pref="view:grid", aria_pressed="true",
                   title=T.VIEW_GRID, aria_label=T.VIEW_GRID),
            Button("☰", type="button", cls="pref icon", data_pref="view:list", aria_pressed="false",
                   title=T.VIEW_LIST, aria_label=T.VIEW_LIST),
            cls="gamebar others") if others else None,
        Div(others_view(others, "", poll=False), cls="cardgrid small") if others else None,
        Div(id="cardzoom", cls="cardzoom", hidden=True, aria_hidden="true"),
        Div(data_fx="fireworks", hidden=True) if fireworks else None,
        Script(src="/fx.js", defer=True) if fireworks else None,
        Script(src="/game.js", defer=True),
        cls="game ended", who=me.name)


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
        replaycollector.ensure_started(url)     # downloads queued replays (off with REPLAY_COLLECTOR=off)
        checkworker.ensure_started(url)         # AI fact checks (off unless CHECKER_MODEL and its key are set)
        try:
            yield
        finally:
            checkworker.stop()
            replaycollector.stop()
            pool.close()

    app, rt = fast_app(
        lifespan=lifespan, pico=False, htmx=False, surreal=False, canonical=False, title=T.TITLE,
        static_path=str(STATIC_DIR), secret_key=os.environ.get("SESSION_SECRET") or "dev-only-not-used",
        htmlkw={"lang": "pt-BR"}, exception_handlers={404: not_found},
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
                if e.code in URL_ERRORS:                 # post/redirect/get: reloading must not re-post the join
                    return RedirectResponse(f"/?err={e.code}", status_code=303)
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
                err = request.query_params.get("swap_err")
                return game_page(card, my_state(conn, card, me), me, others, live.current(), live.known,
                                 request.url.hostname or "", T.SWAP_ERRORS.get(err or ""))
            active = S.get_active_session(conn)
            found = S.find_player(conn, token_of(request))
            if active is None and found and found[1].status == "ended":
                player, session = found
                card = C.get_card(conn, session.id, player.id)
                return results_page(session, player, G.leaderboard(conn, session.id), card,
                                    M.marked_times(conn, card.id) if card else {},
                                    [b for b in M.session_boards(conn, session.id) if b.user_id != player.id])
            name = name_of(request)
            if name is None:
                return name_gate("/")
            if active is not None and S.name_taken(conn, active.id, name):
                return name_gate("/", error="name_taken", value=name)    # tell them BEFORE the button, not after the tap
            kicked = active is not None and S.was_kicked(conn, active.id, token_of(request))
            return entry_page(active, S.count_players(conn, active.id) if active else 0, name=name,
                              error="kicked" if kicked else url_error(request))

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
            ajax = request.headers.get("hx-request") == "true"
            try:
                M.toggle_mark(conn, me.id, idx)
            except (M.CellRevoked, M.CellLocked) as e:   # blocked / confirmed by the admin: the board again with the reason
                assert card is not None
                shown = my_board(card, my_state(conn, card, me), me.name,
                                 T.CELL_REVOKED if isinstance(e, M.CellRevoked) else T.CELL_LOCKED)
                if ajax:                # the button swaps only itself, so redirect this answer to the whole board
                    return Response(to_xml(shown), media_type="text/html",
                                    headers={"HX-Retarget": "#game-board", "HX-Reswap": "outerHTML"})
                return shown
            except M.InvalidCell:
                return PlainTextResponse("invalid cell", status_code=400)
            except M.SessionNotActive:
                return Response(status_code=409, headers={"HX-Refresh": "true"})  # game just ended
            card = C.get_card(conn, me.session_id, me.id)
            assert card is not None
            marked, revoked, locked = M.marked_times(conn, card.id), M.revoked_cells(conn, card.id), M.confirmed_cells(conn, card.id)
            if ajax:                    # this cell + the count and the blue lines (out of band): nothing else is redrawn
                return cell_button(idx, card.cells[idx], marked, revoked, locked), count_view(marked, card, oob=True), \
                    lines_view(card, marked, oob=True)
            return my_board(card, my_state(conn, card, me), me.name)

    @rt("/myboard")
    def myboard(request: Request, v: str = ""):
        """My board as it is in the DB, polled every 5 s by game.js with `v` = what my screen shows (board_sig): 204 while they
        agree, else the board (the admin confirmed / cleared / blocked a mark). A player who is no longer in the active game
        (game over, kicked) reloads: the end screen or the join screen."""
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request))
            card = C.get_card(conn, me.session_id, me.id) if me is not None else None
            if me is None or card is None:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            st = my_state(conn, card, me)
            if v and v == st.sig(card):
                return Response(status_code=204)
            return my_board(card, st, me.name)

    @rt("/swap", methods=["POST"])
    def swap(request: Request, right: int = 0, target: str = ""):
        """Use my card swap with the chosen player (they cannot refuse). Plain post -> back to the game page."""
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request))
            if me is None:
                return RedirectResponse("/", status_code=303)
            try:
                W.use_swap(conn, right, me.id, uuid.UUID(target))
            except ValueError:
                return RedirectResponse("/?swap_err=not_eligible", status_code=303)
            except W.SwapError as e:
                return RedirectResponse(f"/?swap_err={e.code}", status_code=303)
        return RedirectResponse("/", status_code=303)

    @rt("/swap/decline", methods=["POST"])
    def swap_decline(request: Request, right: int = 0):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request))
            if me is not None:
                try:
                    W.decline(conn, right, me.id)
                except W.SwapError:
                    pass                    # already used / declined: a double click
        return RedirectResponse("/", status_code=303)

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

    admin_web.register(rt, pool, live)
    phrases_web.register(rt, pool)
    fallacies_web.register(rt, pool)
    falacias_web.register(rt, pool, live)
    falacias_admin_web.register(rt, pool, live)
    checker_admin_web.register(rt, pool, live)
    checker_web.register(rt, pool)
    ui.register(rt, pool)
    app.add_middleware(ui.CookieContext)
    return app


def __getattr__(name: str):
    # `uvicorn app.main:app` builds the app lazily so importing this module needs no DB.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
