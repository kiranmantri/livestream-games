"""Web layer of "Encontre as Falácias": the menu fragment, entry/join, the two panes and their live feed.

    GET  /falacias          entry form / the game page / the end screen (+ automatic same-name join from Bingo)
    POST /falacias/start    first visitor starts the session (refused when the feed is not ready)
    POST /falacias/join     join the running session
    GET  /falacias/feed     polled every ~2 s: new lines (htmx out-of-band appends), 204 when nothing changed
    GET  /menu              the menu with a dot on games in progress

Selecting a passage and flagging it (Task 4), the leaderboard (Task 4), the admin queue (Task 5) come later.
"""
from __future__ import annotations

from fasthtml.common import H1, H2, Button, Div, Li, Ol, P, Script, Span
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app import falacias as F
from app import feed
from app import sessions as S
from app import texts as T
from app.ui import client_ip_of, entry_form, error_box, menu, page, set_token_cookie, token_of

GAME = "falacias"
FIRST_LINES = 120          # lines per pane shown when a player opens the page
PANES = (("transcript", T.FAL_TRANSCRIPT), ("chat", T.FAL_CHAT))


# ------------------------------------------------------------------ views
def fal_page(*content, cls: str | None = None):
    return page(*content, title=T.FAL_TITLE, cls=cls, current=GAME)


def line_view(e: feed.Event):
    """One line. The text sits alone in its own span (Task 4 computes selection offsets inside it); the author is
    outside it and cannot be selected."""
    return Div(Span(e.author, cls="au") if e.author else None, Span(e.text, cls="tx"), cls="line", data_id=e.id)


def pane_view(kind: str, title: str, events: list[feed.Event]):
    return Div(
        Div(Span(title, cls="who"), Button("", type="button", cls="pill", hidden=True, data_pill=kind), cls="bar"),
        Div(*[line_view(e) for e in events], id=f"pane-{kind}", cls="scroll", data_kind=kind, lang="pt-BR", tabindex="0"),
        cls=f"pane panel {kind}")


def banner_view(st: str, oob: bool = False):
    extra = {"hx_swap_oob": "true"} if oob else {}
    if st == "down":
        return Div(T.FEED_UNAVAILABLE, id="feedbanner", cls="error", role="alert", **extra)
    if st == "ended":
        return Div(T.FAL_VIDEO_END, id="feedbanner", cls="muted", role="status", **extra)
    return Div(id="feedbanner", **extra)


def poller_view(after: int, st: str):
    return Div(id="feedpoll", hx_get=f"/falacias/feed?after={after}&st={st}", hx_trigger="every 2s", hx_swap="outerHTML")


def entry_view(active: S.GameSession | None, n_players: int = 0, error: str | None = None, value: str = ""):
    if active is None:
        panel = Div(H2(T.FAL_START_HEADING), P(T.START_HELP, cls="muted"),
                    entry_form("/falacias/start", T.START_BUTTON, value), cls="panel")
    else:
        panel = Div(H2(T.FAL_JOIN_HEADING), P(T.JOIN_HELP.format(n=n_players), cls="muted"),
                    entry_form("/falacias/join", T.JOIN_BUTTON, value), cls="panel")
    return fal_page(H1(T.FAL_TITLE), P(T.FAL_TAGLINE, cls="tag"), P(T.FAL_REPLAY, cls="muted"), error_box(error), panel)


def game_view(me: S.Player, events: list[feed.Event], last_id: int, st: str):
    by_kind = {k: [e for e in events if e.kind == k] for k, _ in PANES}
    return fal_page(
        H1(T.FAL_TITLE), P(f"{me.name} ({T.YOU})", cls="tag"), P(T.FAL_HOW_TO, cls="muted"), P(T.FAL_REPLAY, cls="muted"),
        banner_view(st),
        Div(*[pane_view(k, title, by_kind[k]) for k, title in PANES], cls="panes"),
        poller_view(last_id, st),
        Script(src="/falacias.js", defer=True),
        cls="fal")


def ended_view(session: S.GameSession, me: S.Player, rows: list[F.LeaderRow]):
    scored = [r for r in rows if r.points > 0]
    items = [Li(Span(f"{r.rank}.", cls="pos"), Span(r.name, cls="nm"), Span(F.fmt_points(r.points), cls="pts"),
                cls="me" if r.user_id == me.id else None) for r in rows]
    return fal_page(H1(T.FAL_GAME_OVER), None if scored else P(T.FAL_NO_POINTS, cls="muted"),
                    Div(H2(T.LEADERBOARD), Ol(*items, cls="lb"), cls="panel"))


# ------------------------------------------------------------------ routes
def register(rt, pool) -> None:
    def enter(request: Request, kind: str, name: str):
        ip = client_ip_of(request)
        with pool.connection() as conn:
            try:
                if kind == "start":
                    _, _, token = feed.start_falacias(conn, name, ip)
                else:
                    _, _, token = S.join_session(conn, name, ip, existing_token=token_of(request, GAME), game_type=GAME)
            except S.SessionError as e:
                active = S.get_active_session(conn, GAME)
                n = S.count_players(conn, active.id) if active else 0
                return entry_view(active, n, error=e.code, value=name)
        return set_token_cookie(RedirectResponse("/falacias", status_code=303), GAME, token)

    @rt("/falacias")
    def falacias_home(request: Request):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is not None:
                feed.tick_throttled(conn, me.session_id)
                events = feed.recent_events(conn, me.session_id, FIRST_LINES)
                return game_view(me, events, feed.last_event_id(conn, me.session_id), feed.status(conn, me.session_id))
            active = S.get_active_session(conn, GAME)
            if active is not None and token_of(request):
                # opened Falácias while playing Bingo: join with the SAME name (own cookie, no form)
                try:
                    got = S.join_as_same_name(conn, token_of(request), "bingo", client_ip_of(request), GAME,
                                              existing_token=token_of(request, GAME))
                except S.SessionError as e:                 # name taken here, IP limit: ask for a name
                    return entry_view(active, S.count_players(conn, active.id), error=e.code)
                if got is not None:
                    return set_token_cookie(RedirectResponse("/falacias", status_code=303), GAME, got[2])
            found = S.find_player(conn, token_of(request, GAME))
            if active is None and found and found[1].status == "ended" and found[1].game_type == GAME:
                return ended_view(found[1], found[0], F.leaderboard(conn, found[1].id))
            return entry_view(active, S.count_players(conn, active.id) if active else 0)

    @rt("/falacias/start", methods=["POST"])
    def falacias_start(request: Request, name: str = ""):
        return enter(request, "start", name)

    @rt("/falacias/join", methods=["POST"])
    def falacias_join(request: Request, name: str = ""):
        return enter(request, "join", name)

    @rt("/falacias/feed")
    def falacias_feed(request: Request, after: int = 0, st: str = "ok"):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:       # the session ended (or the cookie is unknown): reload -> end screen / entry form
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            feed.tick_throttled(conn, me.session_id)
            events = feed.events_after(conn, me.session_id, max(after, 0))
            now_st = feed.status(conn, me.session_id)
        if not events and now_st == st:
            return Response(status_code=204)
        new_after = events[-1].id if events else after
        parts = [poller_view(new_after, now_st)]
        for kind, _ in PANES:
            lines = [line_view(e) for e in events if e.kind == kind]
            if lines:       # htmx appends the CHILDREN of an out-of-band element with a swap style
                parts.append(Div(*lines, hx_swap_oob=f"beforeend:#pane-{kind}"))
        if now_st != st:
            parts.append(banner_view(now_st, oob=True))
        return tuple(parts)

    @rt("/menu")
    def menu_fragment(cur: str = "bingo"):
        with pool.connection() as conn:
            running = frozenset(g for g in S.GAMES if S.get_active_session(conn, g) is not None)
        return menu(cur if cur in S.GAMES else "bingo", running)
