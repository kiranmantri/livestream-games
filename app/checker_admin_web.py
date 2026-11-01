"""Admin tab "Verdade ou Lorota": the checks waiting for a decision (score by hand, dismiss), the board.

    GET  /admin/checker            the page (HTTP Basic, same as /admin); ?all=1 also lists the checks already decided
    GET  /admin/checker/panel      polled every 3 s with the page's signature: 204 while nothing changed, else the redrawn panel
    POST /admin/checker/score      the admin's score for one check (replaces the AI's; players are re-scored on the next read)
    POST /admin/checker/dismiss    nobody scores on this check
"""
from __future__ import annotations

import hashlib
import unicodedata
import uuid
from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    A,
    Button,
    Details,
    Div,
    Form,
    Input,
    Main,
    Ol,
    P,
    Script,
    Span,
    Summary,
    Table,
    Tbody,
    Td,
    Template,
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
from app.checker_web import claim_view, rating

POLL = "every 3s"
SORTS = ("player", "claim", "points", "check")      # sortable columns (the actions column is not)


def fold(text: str) -> str:
    """Case- and accent-insensitive form used by the search."""
    return "".join(c for c in unicodedata.normalize("NFD", text or "") if not unicodedata.combining(c)).casefold()


def matches(it: C.AdminCheck, q: str) -> bool:
    hay = " ".join([it.claim_text, it.explanation or "", it.error or "", T.CK_STATUS.get(it.status, it.status),
                    T.CK_VERDICTS.get(it.verdict or "", ""), T.CK_CONFIDENCE.get(it.confidence or "", ""), *[n for n, _ in it.guesses]])
    return all(word in fold(hay) for word in fold(q).split())


def sort_key(col: str):
    """Per check; the first player's name/guess stand for the rows that share the check. None = no value (always listed last)."""
    return {"player": lambda c: fold(c.guesses[0][0]) if c.guesses else None,
            "claim": lambda c: fold(c.claim_text),
            "points": lambda c: c.admin_score if c.admin_score is not None else c.ai_score,        # the score that counts: the host's, else the AI's
            "check": lambda c: fold(T.CK_VERDICTS.get(c.verdict or "", "") or T.CK_STATUS.get(c.status, c.status))}[col]


def search_and_sort(checks: list[C.AdminCheck], q: str = "", sort: str = "", dir_: str = "asc") -> list[C.AdminCheck]:
    out = [c for c in checks if matches(c, q)] if q.strip() else list(checks)
    if sort not in SORTS:
        return out                                            # arrival order
    key = sort_key(sort)
    have = sorted([c for c in out if key(c) is not None], key=key, reverse=dir_ == "desc")
    return have + [c for c in out if key(c) is None]


def url_for(path: str = "/admin/checker", **params) -> str:
    qs = urlencode({k: v for k, v in params.items() if v})
    return f"{path}?{qs}" if qs else path


def icons(n: int | None) -> str:
    """-3 -> 💩💩💩, +4 -> ⭐⭐⭐⭐, 0 -> 😐 0, none -> –."""
    return "–" if n is None else rating(n)


def fmt_score(n: int | None) -> str:
    return "-" if n is None else "0" if n == 0 else f"{n:+d}"


def pending_span(n: int):
    return Span(str(n), id="pendingcount", cls="pill on", hx_swap_oob="true")


def _source_links(sources: list):
    links = [A(s.get("title") or s["url"], href=s["url"], target="_blank", rel="noopener noreferrer")
             for s in sources if isinstance(s, dict) and str(s.get("url", "")).startswith(("http://", "https://"))]
    return Div(Span(f"{T.CK_SOURCES}: ", cls="who"), *[Span(link, " ") for link in links], cls="muted small") if links else None


def _fold(summary_children, body=None, key: str = ""):
    """A collapsed-by-default block: the summary clamps its text to one line, `open` shows it all (then `body` below it)."""
    return Details(Summary(*summary_children), body, cls="ckfold", data_id=key)


def item_view(it: C.AdminCheck, token: str, session_id: uuid.UUID, active: bool, oob: bool = False):
    """One check = one <tbody>: a row per player who sent it. The cells of the check span those rows."""
    n = max(len(it.guesses), 1)
    span = {"rowspan": str(n)} if n > 1 else {}
    ids = [Input(type="hidden", name="session_id", value=str(session_id)), Input(type="hidden", name="check_id", value=str(it.check_id)),
           Input(type="hidden", name="csrf", value=token)]
    target = {"hx_target": f"#ck-{it.check_id}", "hx_swap": "outerHTML"}

    claim = _fold([Span(it.claim_text, cls="clamp")], key=f"{it.check_id}:claim")

    if active:
        cur = it.admin_score
        picks = [Button(fmt_score(v) if v else "0", type="submit", name="score", value=str(v), cls=f"pick {'neg' if v < 0 else 'pos' if v > 0 else 'zero'}"
                        + (" on" if v == cur else ""), aria_pressed="true" if v == cur else "false", title=icons(v))
                 for v in range(C.SCORE_MIN, C.SCORE_MAX + 1)]
        picker = Form(*ids, Div(*picks, cls="picks"), method="post", action="/admin/checker/score", cls="hostscore",
                      hx_post="/admin/checker/score", **target)
        actions = Form(*ids, Button(T.CK_DISMISS, type="submit", cls="danger", disabled=it.decided and it.admin_score is None),
                       method="post", action="/admin/checker/dismiss", hx_post="/admin/checker/dismiss", cls="dismissform", **target)
        recheck = Form(*ids, Button(T.CK_RECHECK, type="submit", cls="recheck"), method="post", action="/admin/checker/recheck",
                       hx_post="/admin/checker/recheck", **target) if it.status != "done" and it.error else None
        actions = Div(recheck, Button(T.CK_EDIT, type="button", cls="edit", data_ckedit="1", title=T.CK_HOST_EDIT), actions, cls="acts")
    else:
        picker = None
        actions = None
    dismissed = Span(T.CK_DISMISSED, cls="ckdecided") if it.decided and it.admin_score is None else None

    def points(guess: int | None, first: bool):
        """The three lines of the Pontos cell; the editable host score sits in the first row of the check."""
        host_line = [Span(f"{T.CK_LINE_HOST}: ", cls="who"), icons(it.admin_score)]
        host_cell = Details(Summary(*host_line, title=T.CK_HOST_EDIT), picker, cls="hostpick", data_id=f"{it.check_id}:host") if (first and picker is not None) \
            else Div(*host_line, title=fmt_score(it.admin_score))
        return Div(Div(Span(f"{T.CK_LINE_GUESS}: ", cls="who"), icons(guess), title=fmt_score(guess), cls="ptline"),
                   Div(Span(f"{T.CK_LINE_AI}: ", cls="who"), icons(it.ai_score), title=fmt_score(it.ai_score), cls="ptline"),
                   Div(host_cell, dismissed if first else None, cls="ptline host"))

    # the AI's own state; "Aguardando você" only while the host has not decided (a decided check may still be queued/running for the AI)
    shown = "resolved" if it.decided and it.status == "needs_admin" else it.status
    state = Span(T.CK_STATUS.get(shown, shown), cls="pill on" if it.pending and it.status == "needs_admin" else "pill")
    err = Div(f"{T.CK_ERROR}: {it.error}", cls="muted small", role="alert") if it.error and it.status != "done" else None
    if it.verdict:
        head = [Span(T.CK_VERDICTS.get(it.verdict, it.verdict), cls="pill"), " ", state, " ",
                Span(T.CK_CONFIDENCE.get(it.confidence or "", it.confidence or ""), cls="muted small"),
                Span(it.explanation, cls="clamp") if it.explanation else None]
        check = _fold(head, Div(_source_links(it.sources), err), key=f"{it.check_id}:ai")
    else:
        check = Div(state, err)

    first = it.guesses[0] if it.guesses else ("", None)
    rows = [Tr(Td(first[0], data_label=T.CK_COL_PLAYER),
               Td(claim, data_label=T.CK_TH_CLAIM, **span),
               Td(points(first[1], True), data_label=T.CK_TH_POINTS, cls="pts"),
               Td(check, data_label=T.CK_TH_AI_CHECK, **span),
               Td(actions, data_label=T.CK_TH_ACTIONS, **span))]
    rows += [Tr(Td(name, data_label=T.CK_COL_PLAYER), Td(points(g, False), data_label=T.CK_TH_POINTS, cls="pts")) for name, g in it.guesses[1:]]
    return Tbody(*rows, id=f"ck-{it.check_id}", cls=f"ckitem {it.status}", hx_swap_oob="true" if oob else None)


def checks_table(shown, token: str, session_id: uuid.UUID, active: bool, q: str = "", sort: str = "", dir_: str = "asc", all_: bool = False):
    cols = ((T.CK_COL_PLAYER, "player"), (T.CK_TH_CLAIM, "claim"), (T.CK_TH_POINTS, "points"), (T.CK_TH_AI_CHECK, "check"), (T.CK_TH_ACTIONS, None))
    heads = []
    for label, key in cols:
        if key is None:
            heads.append(Th(label))
            continue
        here = sort == key
        nxt = "desc" if here and dir_ == "asc" else "asc"
        mark = (" ▲" if dir_ == "asc" else " ▼") if here else ""
        heads.append(Th(A(label + mark, href=url_for(q=q, sort=key, dir=nxt, all="1" if all_ else ""), cls="sortlink"),
                        **({"aria_sort": "ascending" if dir_ == "asc" else "descending"} if here else {})))
    return Div(Table(Thead(Tr(*heads)), *[item_view(c, token, session_id, active) for c in shown], cls="users cktable stack"),
               id="ckqueue", cls="ckwrap")


def toolbar(q: str, sort: str, dir_: str, all_: bool, live: bool):
    """One row above the table: the pending/all toggle on the left, the search box on the right."""
    sort = sort if sort in SORTS else ""
    toggle = A(T.CK_SHOW_PENDING if all_ else T.CK_SHOW_ALL, href=url_for(q=q, sort=sort, dir=dir_ if sort else "", all="" if all_ else "1"),
               cls="cktoggle", data_ckall="1")
    return Div(toggle, search_form(q, sort, dir_, all_, live), cls="cktoolbar")


def search_form(q: str, sort: str, dir_: str, all_: bool, live: bool):
    """Outside the polled panel (typing is never interrupted). With JS it filters as you type; without, it is a plain GET."""
    live_attrs = {"hx_get": "/admin/checker/panel", "hx_trigger": "input changed delay:300ms, search", "hx_target": "#ckpanel", "hx_swap": "outerHTML"} if live else {}
    hidden = [Input(type="hidden", name=n, value=v) for n, v in (("all", "1" if all_ else ""), ("sort", sort), ("dir", dir_ if sort else "")) if v]
    return Form(Input(type="search", name="q", value=q, placeholder=T.CK_SEARCH_PLACEHOLDER, aria_label=T.CK_SEARCH_LABEL, autocomplete="off"), *hidden,
                Button(T.CK_SEARCH_BTN, type="submit", cls="nojs" if live else None), method="get", action="/admin/checker", cls="ck-search", **live_attrs)


def board_view(rows: list[C.LeaderRow], oob: bool = False):
    """The board. Each row toggles its player in the card filter below (ckadmin.js; the selection is kept in the browser)."""
    table = Table(Thead(Tr(Th("#"), Th(T.ADMIN_PLAYERS), Th(T.CK_COL_POINTS), Th(T.CK_COL_EXACT), Th(T.CK_COL_CLAIMS))),
                  Tbody(*[Tr(Td(str(r.rank), data_label="#"), Td(r.name, data_label=T.ADMIN_PLAYERS), Td(str(r.points), data_label=T.CK_COL_POINTS),
                             Td(str(r.exact), data_label=T.CK_COL_EXACT), Td(str(r.claims), data_label=T.CK_COL_CLAIMS),
                             data_user=str(r.user_id), tabindex="0", aria_selected="false", cls="ckpick", title=T.CK_CARDS_ROW_TITLE) for r in rows]),
                  cls="users stack", role="grid", aria_multiselectable="true") if rows else P(T.ADMIN_NO_USERS, cls="muted")
    return Div(H2(T.AF_BOARD), table, id="ckboard", cls="panel", hx_swap_oob="outerHTML" if oob else None)


def cards_view(conn, session_id: uuid.UUID, oob: bool = False):
    """Every player's claim cards, newest first, in the grid of /checker (ol.pairs.claims). Filtered in the browser by the board rows."""
    items = [claim_view(r.claim, who=r.name, user_id=r.user_id) for r in C.session_claims(conn, session_id)]
    body = [P(T.CK_CARDS_HINT, cls="muted small"),
            Button(T.CK_CARDS_ALL, type="button", data_ckshowall="1", hidden=True, cls="link"),
            Ol(*items, cls="pairs claims"),
            P(T.CK_CARDS_NONE, id="ckcards-none", hidden=True, cls="muted")] if items else [P(T.CK_CARDS_EMPTY, cls="muted empty")]
    return Div(H2(T.CK_CARDS_TITLE), *body, id="ckcards", cls="panel", data_session=str(session_id), hx_swap_oob="outerHTML" if oob else None)


def signature(session: S.GameSession, checks: list[C.AdminCheck], board: list[C.LeaderRow], all_: bool) -> str:
    raw = repr((session.status, all_, [(c.check_id, c.status, c.ai_score, c.admin_score, c.error, len(c.guesses)) for c in checks],
                [(r.user_id, r.points, r.claims, r.exact) for r in board]))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def panel_view(conn, session: S.GameSession, pw: str, all_: bool = False, q: str = "", sort: str = "", dir_: str = "asc"):
    active = session.status == "active"
    every = C.admin_checks(conn, session.id, pending_only=False)
    board = C.leaderboard(conn, session.id)
    shown = search_and_sort(every if all_ else [c for c in every if c.status in C.PENDING_STATES], q, sort, dir_)
    token = A_.csrf_token(pw, session.id)
    poll = {"hx_get": url_for("/admin/checker/panel", sig=signature(session, every, board, all_), all="1" if all_ else "", q=q,
                              sort=sort if sort in SORTS else "", dir=dir_ if sort in SORTS else ""),
            "hx_trigger": POLL, "hx_swap": "outerHTML"} if active else {}
    return Div(
        Div(H2(T.CK_CHECKS, " ", Span(T.CK_PENDING, cls="muted"), " ", pending_span(sum(c.status in C.PENDING_STATES for c in every))),
            checks_table(shown, token, session.id, active, q, sort if sort in SORTS else "", dir_, all_) if shown
            else P(T.CK_NO_MATCH if q.strip() else T.CK_NO_CHECKS, cls="muted empty"),
            cls="panel"),
        board_view(board), cards_view(conn, session.id), id="ckpanel", **poll)


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


def page_view(conn, session: S.GameSession | None, pw: str, all_: bool = False, q: str = "", sort: str = "", dir_: str = "asc"):
    """The game page ("Jogo"): the cards of every check. Starting and ending happen in the header."""
    parts = [title_row(T.CK_ADMIN_TITLE)]
    if session is None:
        parts += [P(T.CK_NO_SESSION, cls="muted"), P(T.SESSION_NONE_HINT, cls="muted")]
        return Title(T.CK_ADMIN_TITLE), admin_tabs("checker"), Main(*parts, cls="wide")
    active = session.status == "active"
    winner = None if active else next((r for r in C.leaderboard(conn, session.id) if r.user_id == session.winner_user_id), None)
    parts.append(status_bar(session, S.count_players(conn, session.id), None, winner=winner.name if winner else None,
                            source=source_line(conn, session) if active else None))   # the video link only while the game runs
    if active:
        parts.append(auto_line())
    parts += [toolbar(q, sort, dir_, all_, active), panel_view(conn, session, pw, all_, q, sort, dir_), Script(src="/admin.js", defer=True), Script(src="/checker.js", defer=True), Script(src="/ckadmin.js", defer=True)]
    return Title(T.CK_ADMIN_TITLE), admin_tabs("checker"), Main(*parts, cls="wide")


def register(rt, pool, live: livestream.LiveStream | None = None):
    live = live or livestream.LiveStream()

    def pw() -> str:
        return A_.admin_password() or ""

    def fragment(*parts, headers: dict | None = None) -> Response:
        return Response(to_xml(parts), media_type="text/html", headers=headers or {})

    @rt("/admin/checker", methods=["GET"])
    def ck_page(request: Request, all: str = "", q: str = "", sort: str = "", dir: str = "asc"):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            return page_view(conn, A_.current_session(conn, "checker"), pw(), all_=all == "1", q=q[:100], sort=sort, dir_="desc" if dir == "desc" else "asc")

    @rt("/admin/checker/panel", methods=["GET"])
    def ck_panel(request: Request, sig: str = "", all: str = "", q: str = "", sort: str = "", dir: str = "asc"):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            session = A_.current_session(conn, "checker")
            if session is None or session.status != "active":
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            panel = panel_view(conn, session, pw(), all == "1", q[:100], sort, "desc" if dir == "desc" else "asc")
            if sig and f"sig={sig}" in to_xml(panel).split("hx-get=")[1].split(" ")[0]:
                return Response(status_code=204)
            return fragment(panel)

    def decide(request: Request, session_id: str, check_id: str, csrf: str, score: str | None, recheck: bool = False):
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
                if recheck:
                    C.admin_recheck(conn, sid, cid)
                    checkworker.wake()
                elif value is None:
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
            # the reply starts with a <tbody>, so htmx parses it inside a <table>: out-of-band pieces must sit in a <template>
            # or the browser pulls them into that table (a stray table showed up inside the page)
            return fragment(item_view(item, token, sid, True),
                            Template(pending_span(C.pending_count(conn, sid)), board_view(C.leaderboard(conn, sid), oob=True),
                                     cards_view(conn, sid, oob=True)))

    @rt("/admin/checker/score", methods=["POST"])
    def ck_score(request: Request, session_id: str = "", check_id: str = "", score: str = "", csrf: str = ""):
        return decide(request, session_id, check_id, csrf, score)

    @rt("/admin/checker/recheck", methods=["POST"])
    def ck_recheck(request: Request, session_id: str = "", check_id: str = "", csrf: str = ""):
        return decide(request, session_id, check_id, csrf, None, recheck=True)

    @rt("/admin/checker/dismiss", methods=["POST"])
    def ck_dismiss(request: Request, session_id: str = "", check_id: str = "", csrf: str = ""):
        return decide(request, session_id, check_id, csrf, None)
