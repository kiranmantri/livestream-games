"""Admin tab "Aponte o mentiroso!": the checks waiting for a decision (score by hand, dismiss), the board.

    GET  /admin/checker            the page (HTTP Basic, same as /admin); ?all=1 also lists the checks already decided
    GET  /admin/checker/panel      polled every 3 s with the page's signature: 204 while nothing changed, else the redrawn panel
    POST /admin/checker/score      the admin's score for one check (replaces the AI's; players are re-scored on the next read)
    POST /admin/checker/dismiss    nobody scores on this check
"""
from __future__ import annotations

import hashlib
import uuid

from fasthtml.common import (
    H2,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    Li,
    Main,
    Ol,
    P,
    Script,
    Span,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Title,
    Tr,
    to_xml,
)
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from app import admin as A_
from app import checker as C
from app import checkworker, feed, livestream
from app import sessions as S
from app import texts as T
from app.admin_web import admin_tabs, denied, status_bar, title_row

POLL = "every 3s"


def fmt_score(n: int | None) -> str:
    return "-" if n is None else "0" if n == 0 else f"{n:+d}"


def pending_span(n: int):
    return Span(str(n), id="pendingcount", cls="pill on", hx_swap_oob="true")


def _source_links(sources: list):
    links = [A(s.get("title") or s["url"], href=s["url"], target="_blank", rel="noopener noreferrer")
             for s in sources if isinstance(s, dict) and str(s.get("url", "")).startswith(("http://", "https://"))]
    return Div(Span(f"{T.CK_SOURCES}: ", cls="who"), *[Span(link, " ") for link in links], cls="muted small") if links else None


def item_view(it: C.AdminCheck, token: str, session_id: uuid.UUID, active: bool, oob: bool = False):
    """One check: the claim, what the AI said, the guesses, and (while the game runs) the forms to score or dismiss it."""
    ai = None
    if it.verdict:
        ai = Div(Span(f"{T.CK_AI}: ", cls="who"), Span(T.CK_VERDICTS.get(it.verdict, it.verdict), cls="pill"), " ",
                 Span(fmt_score(it.ai_score), cls="ckscore"), " · ", T.CK_CONFIDENCE.get(it.confidence or "", it.confidence or ""),
                 Div(it.explanation, cls="muted") if it.explanation else None, _source_links(it.sources), cls="ckai")
    decided = None
    if it.status == "resolved":
        decided = Div(f"{T.CK_ADMIN_SCORE}: {fmt_score(it.admin_score)}" if it.admin_score is not None else T.CK_DISMISSED, cls="ckdecided")
    forms = None
    if active:
        target = {"hx_post": "/admin/checker/score", "hx_target": f"#ck-{it.check_id}", "hx_swap": "outerHTML"}
        ids = [Input(type="hidden", name="session_id", value=str(session_id)), Input(type="hidden", name="check_id", value=str(it.check_id)),
               Input(type="hidden", name="csrf", value=token)]
        forms = Div(
            Form(*ids, Label(T.CK_SCORE_LABEL, Input(type="number", name="score", min=str(C.SCORE_MIN), max=str(C.SCORE_MAX), step="1",
                                                     required=True, inputmode="numeric", value="" if it.admin_score is None else str(it.admin_score))),
                 Button(T.CK_SET_SCORE, type="submit", cls="primary"), method="post", action="/admin/checker/score", cls="inline", **target),
            Form(*ids, Button(T.CK_DISMISS, type="submit"), method="post", action="/admin/checker/dismiss", cls="inline",
                 hx_post="/admin/checker/dismiss", hx_target=f"#ck-{it.check_id}", hx_swap="outerHTML"),
            cls="acts")
    guesses = ", ".join(f"{n}: {fmt_score(g)}" for n, g in it.guesses)
    return Li(Div(Span(it.claim_text, cls="ckclaim"), " ", Span(T.CK_STATUS.get(it.status, it.status), cls="pill on" if it.status == "needs_admin" else "pill"),
                  cls="ckhead"),
              Div(f"{T.CK_GUESSES}: {guesses}", cls="muted small") if guesses else None,
              ai, Div(f"{T.CK_ERROR}: {it.error}", cls="muted small", role="alert") if it.error and it.status != "done" else None,
              decided, forms, id=f"ck-{it.check_id}", cls=f"ckitem {it.status}", hx_swap_oob="true" if oob else None)


def board_view(rows: list[C.LeaderRow], oob: bool = False):
    table = Table(Thead(Tr(Th("#"), Th(T.ADMIN_PLAYERS), Th(T.CK_COL_POINTS), Th(T.CK_COL_EXACT), Th(T.CK_COL_CLAIMS))),
                  Tbody(*[Tr(Td(str(r.rank), data_label="#"), Td(r.name, data_label=T.ADMIN_PLAYERS), Td(str(r.points), data_label=T.CK_COL_POINTS),
                             Td(str(r.exact), data_label=T.CK_COL_EXACT), Td(str(r.claims), data_label=T.CK_COL_CLAIMS)) for r in rows]),
                  cls="users stack") if rows else P(T.ADMIN_NO_USERS, cls="muted")
    return Div(H2(T.AF_BOARD), table, id="ckboard", cls="panel", hx_swap_oob="outerHTML" if oob else None)


def signature(session: S.GameSession, checks: list[C.AdminCheck], board: list[C.LeaderRow], all_: bool) -> str:
    raw = repr((session.status, all_, [(c.check_id, c.status, c.ai_score, c.admin_score, c.error, len(c.guesses)) for c in checks],
                [(r.user_id, r.points, r.claims, r.exact) for r in board]))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def panel_view(conn, session: S.GameSession, pw: str, all_: bool = False):
    active = session.status == "active"
    every = C.admin_checks(conn, session.id, pending_only=False)
    board = C.leaderboard(conn, session.id)
    shown = every if all_ else [c for c in every if c.status in C.PENDING_STATES]
    token = A_.csrf_token(pw, session.id)
    poll = {"hx_get": f"/admin/checker/panel?sig={signature(session, every, board, all_)}{'&all=1' if all_ else ''}",
            "hx_trigger": POLL, "hx_swap": "outerHTML"} if active else {}
    toggle = A(T.CK_SHOW_PENDING if all_ else T.CK_SHOW_ALL, href="/admin/checker" if all_ else "/admin/checker?all=1", cls="btn")
    return Div(
        Div(H2(T.CK_CHECKS, " ", Span(T.CK_PENDING, cls="muted"), " ", pending_span(sum(c.status in C.PENDING_STATES for c in every))),
            Div(toggle, cls="qtools"),
            Ol(*[item_view(c, token, session.id, active) for c in shown], id="ckqueue", cls="queue") if shown else P(T.CK_NO_CHECKS, cls="muted empty"),
            cls="panel"),
        board_view(board), id="ckpanel", **poll)


def source_line(conn, session: S.GameSession):
    vid, mode = feed.session_source(conn, session.id)
    if vid is None:
        return None
    url = f"https://www.youtube.com/watch?v={vid}"
    return Span(Span((T.SRC_SESSION_LIVE if mode == "live" else T.SRC_VIDEO) + ": ", cls="who"),
                A(url, href=url, target="_blank", rel="noopener"), cls="muted")


def auto_line():
    model, why = checkworker.status()
    return P(T.CK_AUTO_ON.format(model=model) if model else T.CK_AUTO_OFF.format(reason=why), cls="muted", id="autocheck")


def page_view(conn, session: S.GameSession | None, pw: str, all_: bool = False):
    """The game page ("Jogo"): the cards of every check. Starting and ending happen in the header."""
    parts = [title_row(T.CK_ADMIN_TITLE)]
    if session is None:
        parts += [P(T.CK_NO_SESSION, cls="muted"), P(T.SESSION_NONE_HINT, cls="muted")]
        return Title(T.CK_ADMIN_TITLE), admin_tabs("checker"), Main(*parts, cls="wide")
    active = session.status == "active"
    winner = None if active else next((r for r in C.leaderboard(conn, session.id) if r.user_id == session.winner_user_id), None)
    parts.append(status_bar(session, S.count_players(conn, session.id), None, winner=winner.name if winner else None,
                            source=source_line(conn, session)))
    if active:
        parts.append(auto_line())
    parts += [panel_view(conn, session, pw, all_), Script(src="/admin.js", defer=True)]
    return Title(T.CK_ADMIN_TITLE), admin_tabs("checker"), Main(*parts, cls="wide")


def register(rt, pool, live: livestream.LiveStream | None = None):
    live = live or livestream.LiveStream()

    def pw() -> str:
        return A_.admin_password() or ""

    def fragment(*parts, headers: dict | None = None) -> Response:
        return Response(to_xml(parts), media_type="text/html", headers=headers or {})

    @rt("/admin/checker", methods=["GET"])
    def ck_page(request: Request, all: str = ""):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            return page_view(conn, A_.current_session(conn, "checker"), pw(), all_=all == "1")

    @rt("/admin/checker/panel", methods=["GET"])
    def ck_panel(request: Request, sig: str = "", all: str = ""):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            session = A_.current_session(conn, "checker")
            if session is None or session.status != "active":
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            panel = panel_view(conn, session, pw(), all == "1")
            if f"sig={sig}" in to_xml(panel).split("hx-get=")[1].split(" ")[0]:
                return Response(status_code=204)
            return fragment(panel)

    def decide(request: Request, session_id: str, check_id: str, csrf: str, score: str | None):
        """Shared by score and dismiss. `score` None = dismiss."""
        if (d := denied(request)) is not None:
            return d
        try:
            sid, cid = uuid.UUID(session_id), uuid.UUID(check_id)
        except ValueError:
            return PlainTextResponse("bad request", status_code=400)
        if not A_.csrf_ok(pw(), sid, csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        value = None
        if score is not None:
            try:
                value = int(score.strip())
            except ValueError:
                return PlainTextResponse("bad score", status_code=400)
        htmx = request.headers.get("hx-request") == "true"
        with pool.connection() as conn:
            try:
                if value is None:
                    C.admin_dismiss(conn, sid, cid)
                else:
                    C.admin_set_score(conn, sid, cid, value)
            except C.SessionNotActive:
                return Response("", status_code=200, headers={"HX-Redirect": "/admin/checker"}) if htmx else RedirectResponse("/admin/checker", status_code=303)
            except C.BadScore:
                return PlainTextResponse("bad score", status_code=400)
            except C.CheckNotFound:
                return PlainTextResponse("no such check", status_code=404)
            if not htmx:
                return RedirectResponse("/admin/checker", status_code=303)
            token = A_.csrf_token(pw(), sid)
            item = C.admin_check(conn, sid, cid)
            return fragment(item_view(item, token, sid, True), pending_span(C.pending_count(conn, sid)),
                            board_view(C.leaderboard(conn, sid), oob=True))

    @rt("/admin/checker/score", methods=["POST"])
    def ck_score(request: Request, session_id: str = "", check_id: str = "", score: str = "", csrf: str = ""):
        return decide(request, session_id, check_id, csrf, score)

    @rt("/admin/checker/dismiss", methods=["POST"])
    def ck_dismiss(request: Request, session_id: str = "", check_id: str = "", csrf: str = ""):
        return decide(request, session_id, check_id, csrf, None)
