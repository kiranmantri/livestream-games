"""Player side of "Quem tá metendo o loco?". For now: the tab, the name gate and a page that says whether a game is running
or about to start (the guessing UI comes later).

    GET /checker          the page
    GET /checker/status   the part that follows the game (polled every 10 s)
"""
from __future__ import annotations

from fasthtml.common import H1, H2, A, Div, P, Span, to_xml
from starlette.requests import Request
from starlette.responses import Response

from app import feed
from app import sessions as S
from app import texts as T
from app.ui import name_gate, name_of, page


def status_view(conn):
    """No game: 'about to start'. A game: the heading, how many players, and its video when the host chose one."""
    session = S.get_active_session(conn, "checker")
    attrs = {"hx_get": "/checker/status", "hx_trigger": "every 10s", "hx_swap": "outerHTML"}
    if session is None:
        return Div(H2(T.CK_NONE_HEADING), P(T.CK_NONE_HELP, cls="muted"), id="ckstatus", cls="panel", **attrs)
    vid, mode = feed.session_source(conn, session.id)
    link = None
    if vid:
        url = f"https://www.youtube.com/watch?v={vid}"
        link = P(Span((T.SRC_SESSION_LIVE if mode == "live" else T.SRC_VIDEO) + ": ", cls="who"), A(url, href=url, target="_blank", rel="noopener"), cls="muted")
    return Div(H2(T.CK_RUNNING_HEADING), P(T.players_in_game(S.count_players(conn, session.id)), cls="muted"), link,
               P(T.CK_SOON), id="ckstatus", cls="panel", **attrs)


def register(rt, pool):
    @rt("/checker", methods=["GET"])
    def ck_home(request: Request):
        name = name_of(request)
        if not name:
            return name_gate("/checker")
        with pool.connection() as conn:
            return page(H1(T.CK_TITLE), P(T.CK_TAGLINE, cls="tag"), status_view(conn), title=T.CK_TITLE, current="checker", who=name)

    @rt("/checker/status", methods=["GET"])
    def ck_status(request: Request):
        with pool.connection() as conn:
            return Response(to_xml(status_view(conn)), media_type="text/html")
