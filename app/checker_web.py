"""Player side of "Aponte o mentiroso!": the tab, join, the three panels (video | chat | transcript), the slide panel with a rating,
"my claims" with the AI answer, the leaderboard and the end screen.

    GET  /checker          name gate / status + join button / the game page / the end screen
    POST /checker/join     join the running session (the admin starts it, a player never does)
    GET  /checker/status   the part of the entry page that follows the game (polled every 10 s)
    GET  /checker/feed     polled every ~2 s: new lines (htmx out-of-band appends), 204 when nothing changed
    POST /checker/mark     the slide panel: a passage or a typed phrase + a rating
    GET  /checker/side     my claims + the leaderboard (polled every 5 s)

Chat and transcript come from the Falácias session of the same video (see feed._mirror_live): one chat poll and one STT bill per live.
The panel script is the one of Falácias (falacias.js); it only needs the element ids used below.
"""
from __future__ import annotations

import re
import unicodedata

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
    Li,
    Ol,
    P,
    Script,
    Small,
    Span,
    Summary,
    Table,
    Tbody,
    Td,
    Textarea,
    Th,
    Thead,
    Tr,
    to_xml,
)
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app import checker as C
from app import checkworker, feed, sttworker
from app import falacias as F
from app import scorebar as SB
from app import sessions as S
from app import texts as T
from app.falacias_web import (
    FIRST_LINES,
    PANES,
    banner_view,
    line_view,
    pane_view,
    stt_banner_view,
    video_pane,
)
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
    result_tabs,
    set_token_cookie,
    token_of,
    url_error,
)

GAME = "checker"
MAX_INT_DIGITS = 18


def rating(n: int | None) -> str:
    """-3 -> 💩💩💩, +4 -> ⭐⭐⭐⭐, 0 -> 😐 0."""
    if n is None:
        return ""
    return "💩" * -n if n < 0 else "⭐" * n if n > 0 else "😐 0"


# ------------------------------------------------------------------ views
def ck_page(*content, cls: str | None = None, who: str | None = None):
    return page(*content, title=T.CK_TITLE, cls=cls, current=GAME, who=who)


def status_view(conn, name: str | None = None, joinable: bool = True):
    """No game: 'about to start'. A game: the heading, how many players, its video, and (for a visitor who is not in it) the join button."""
    session = S.get_active_session(conn, GAME)
    attrs = {"hx_get": "/checker/status", "hx_trigger": "every 10s", "hx_swap": "outerHTML"}
    if session is None:
        return Div(H2(T.CK_NONE_HEADING), P(T.CK_NONE_HELP, cls="muted"), id="ckstatus", cls="panel", **attrs)
    vid, mode = feed.session_source(conn, session.id)
    link = None
    if vid:
        url = f"https://www.youtube.com/watch?v={vid}"
        link = P(Span((T.SRC_SESSION_LIVE if mode == "live" else T.SRC_VIDEO) + ": ", cls="who"), A(url, href=url, target="_blank", rel="noopener"), cls="muted")
    join = play_form("/checker/join", T.JOIN_BUTTON, name, "/checker") if name and joinable else None
    return Div(H2(T.CK_RUNNING_HEADING), P(T.players_in_game(S.count_players(conn, session.id)), cls="muted"), link, join,
               id="ckstatus", cls="panel", **attrs)


def poller_view(after: int, st: str, tr: str | None = None):
    q = f"after={after}&st={st}" + (f"&tr={tr}" if tr else "")
    return Div(id="feedpoll", hx_get=f"/checker/feed?{q}", hx_trigger="every 2s", hx_swap="outerHTML")


def picker_view():
    """The slide panel (opened by falacias.js on any click in a pane): the text box the passage is written into, the rating, send."""
    scale = Div(*[Label(Input(type="radio", name="guess", value=v), Span(f"{v:+d}" if v else "0"), title=rating(v), cls=f"g{'n' if v < 0 else 'p' if v > 0 else 'z'}")
                  for v in range(-5, 6)], cls="scale", role="radiogroup", aria_label=T.CK_GUESS_LABEL)
    return Form(
        Div(Span(T.CK_PANEL_BAR, cls="who"), Button(T.FAL_CLEAR, type="button", id="pk-clear", cls="link"), cls="bar"),
        *[Input(type="hidden", name=n, id=f"pk-{n}") for n in ("kind", "start_id", "start_off", "end_id", "end_off")],
        Label(T.CK_NOTE_LABEL, Textarea(id="pk-note", name="note", maxlength=F.MAX_QUOTE, rows=4, placeholder=T.CK_NOTE_PLACEHOLDER,
                                        autocomplete="off"), cls="notefield"),
        Div(Span(T.CK_GUESS_LABEL, cls="who"), Small("💩 -5 … +5 ⭐", cls="muted"), cls="bar"),
        scale,
        Div(Button(T.CK_SEND, type="submit", cls="primary"), Div(id="feedback", role="status"), cls="sendrow"),
        id="picker", cls="panel picker ckpicker", hidden=True, hx_post="/checker/mark", hx_swap="none")


def feedback_view(code: str | None = None, ok: bool = False):
    if code is None:
        return Div(T.CK_SENT if ok else "", id="feedback", cls="ok" if ok else None, role="status", hx_swap_oob="true")
    msg = T.CK_ERRORS.get(code) or T.CK_ERRORS["generic"]
    return Div(msg.format(n=C.RATE_LIMIT_S, max=C.MAX_CLAIMS_PER_PLAYER), id="feedback", cls="error", role="alert", hx_swap_oob="true")


def claim_view(v: C.ClaimView, who: str | None = None, user_id=None):
    """One of my claims: the phrase, my guess, the state or the answer (verdict, explanation, sources) and my points.
    `who` (the admin's grid of every player's cards): the card names its player, says "Palpite" and carries data-user for the filter."""
    guess_label = T.CK_LINE_GUESS if who else T.CK_YOUR_GUESS
    answered = v.status in ("done", "needs_admin") and v.verdict is not None
    if v.decided and v.admin_score is None:
        state = Span(T.CK_STATE_DISMISSED, cls="badge spam")
    elif v.status in ("queued", "running"):            # also with a host score: the AI's explanation is still coming
        state = Span(T.CK_STATE_WAITING, cls="badge pending")
    elif v.status == "needs_admin" and not v.decided:
        state = Span(T.CK_STATE_ADMIN, cls="badge pending")
    else:
        state = Span(T.CK_VERDICTS.get(v.verdict or "", ""), cls=f"badge {v.verdict}") if v.verdict else None
    result = None
    if answered or (v.decided and v.admin_score is not None):
        sources = [Li(A(s.get("title") or s["url"], href=s["url"], target="_blank", rel="noopener noreferrer")) for s in (v.sources or [])
                   if isinstance(s, dict) and str(s.get("url", "")).startswith(("http://", "https://"))]
        result = Div(P(v.explanation, cls="why") if v.explanation else None,
                     Div(Span(T.CK_SOURCES + ":", cls="who"), Ol(*sources, cls="srcs")) if sources else None, cls="answer")
    icons = Span(Span(rating(v.guess), cls="gs", title=guess_label),
                 Span("→", cls="arrow", aria_hidden="true") if v.final is not None else None,
                 Span(rating(v.final), cls="fs", title=T.CK_FINAL) if v.final is not None else None, cls="icons")
    head = Summary(Span(who, cls="who ckwho") if who else None, Span(f"“{v.quote}”", cls="q"),
                   Span(state, icons, Span(T.CK_POINTS.format(n=v.points), cls="pts") if v.final is not None else None, cls="meta"))
    detail = Div(Span(f"{guess_label}: {rating(v.guess)} ({v.guess:+d})", cls="gs"),
                 Span(f"{T.CK_FINAL}: {rating(v.final)} ({v.final:+d})", cls="fs") if v.final is not None else None, result, cls="detail")
    return Li(Details(head, detail, cls="ckcard", data_id=str(v.claim_id)), data_user=str(user_id) if user_id else None)


def points_text(n: int) -> str:
    return T.POINTS_N.format(n=n)


def score_view(conn, me: S.Player):
    mine = next((r for r in C.leaderboard(conn, me.session_id) if r.user_id == me.id), None)
    return SB.score_bar("/checker", me.name, points_text(mine.points if mine else 0))


def board_view(conn, me: S.Player):
    return SB.board_list([(r.rank, r.name, points_text(r.points), r.user_id == me.id) for r in C.leaderboard(conn, me.session_id)])


def side_view(conn, me: S.Player, oob: bool = False):
    """Two tabs in one polled block: my claims, and the claims of the other players (same 3-column foldable cards, newest first).
    The chosen tab lives in the browser (tabs.js re-applies it after every redraw); the server always draws "mine" visible."""
    mine = [claim_view(v) for v in reversed(C.user_claims(conn, me.id))]
    others = [claim_view(r.claim, who=r.name) for r in C.session_claims(conn, me.session_id) if r.user_id != me.id]
    return Div(result_tabs(Ol(*mine, cls="pairs claims") if mine else P(T.CK_NO_CLAIMS, cls="muted"),
                           Ol(*others, cls="pairs claims") if others else P(T.CK_NO_OTHERS, cls="muted")),
               id="side", cls="side", hx_get="/checker/side", hx_trigger="every 5s", hx_swap="outerHTML",
               **({"hx_swap_oob": "true"} if oob else {}))


def game_view(me: S.Player, events, last_id: int, st: str, side, live: bool, video_id: str | None, stt: bool, tr: str, score=None):
    stt = stt and live
    by_kind = {k: [e for e in events if e.kind == k] for k, _ in PANES}
    show = bool(live and video_id)
    kinds = (("chat", T.FAL_CHAT), ("transcript", T.FAL_TRANSCRIPT)) if show else PANES
    return ck_page(
        H1(T.CK_TITLE), score, P(T.CK_HOW_TO, cls="muted"), P(T.CK_AUTO_LABEL, cls="muted"),
        banner_view(st, live=live),
        stt_banner_view(tr) if stt else None,
        Button(T.VIDEO_SHOW, type="button", cls="pill showvideo", data_showvideo="1", hidden=True) if show else None,
        Div(*([video_pane(video_id)] if show else []),
            *[pane_view(k, title, by_kind[k], note=T.FAL_NO_TRANSCRIPT if live and not stt and k == "transcript" else None,
                        label=T.FAL_STT_LABEL if stt and k == "transcript" else None) for k, title in kinds],
            cls="panes" + (" three" if show else "")),
        poller_view(last_id, st, tr if stt else None),
        picker_view(),
        side,
        SB.board_dialog(),
        Script(src="/falacias.js", defer=True),
        Script(src="/checker.js", defer=True),
        Script(src="/tabs.js", defer=True),
        cls="fal ck", who=me.name)


def ended_view(conn, session: S.GameSession, me: S.Player, rows: list[C.LeaderRow]):
    winner = next((r for r in rows if r.user_id == session.winner_user_id), None)
    head = P(T.CK_WINNER.format(name=winner.name, pts=winner.points), cls="winner") if winner else None
    table = Table(Thead(Tr(Th("#"), Th(T.CK_COL_PLAYER), Th(T.CK_COL_POINTS), Th(T.CK_COL_EXACT), Th(T.CK_COL_CLAIMS))),
                  Tbody(*[Tr(Td(str(r.rank)), Td(r.name), Td(str(r.points)), Td(str(r.exact)), Td(str(r.claims)), cls="me" if r.user_id == me.id else None)
                          for r in rows]), cls="users final")
    mine = [claim_view(v) for v in reversed(C.user_claims(conn, me.id))]
    others = [claim_view(r.claim, who=r.name) for r in C.session_claims(conn, session.id) if r.user_id != me.id]
    return ck_page(H1(T.CK_GAME_OVER), head, Div(H2(T.LEADERBOARD), table, cls="panel"),
                   result_tabs(Ol(*mine, cls="pairs claims") if mine else P(T.CK_NO_CLAIMS, cls="muted"),
                               Ol(*others, cls="pairs claims") if others else P(T.CK_NO_OTHERS, cls="muted")),
                   Script(src="/checker.js", defer=True), Script(src="/tabs.js", defer=True), who=me.name)


# ------------------------------------------------------------------ routes
def register(rt, pool):
    def fragment(*parts, headers: dict | None = None) -> Response:
        return Response(to_xml(parts), media_type="text/html", headers=headers or {})

    def to_int(raw: str) -> int:
        if not re.fullmatch(rf"-?\d{{1,{MAX_INT_DIGITS}}}", raw or ""):
            raise F.BadSelection("not an integer")
        return int(raw)

    def view_of(conn, me: S.Player):
        """What the game page needs that depends on the source: (live?, video id, STT on?, transcript health)."""
        vid, mode = feed.session_source(conn, me.session_id)
        live = mode == "live"
        stt = live and sttworker.enabled()
        sib = feed.sibling_falacias(conn, me.session_id) if stt else None
        return live, vid, stt, sttworker.health(sib[0]) if sib else "ok"

    @rt("/checker", methods=["GET"])
    def ck_home(request: Request):
        name = name_of(request)
        if not name:
            return name_gate("/checker")
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is not None:
                feed.tick_throttled(conn, me.session_id)
                live, vid, stt, tr = view_of(conn, me)
                return game_view(me, feed.recent_events(conn, me.session_id, FIRST_LINES), feed.last_event_id(conn, me.session_id),
                                 feed.status(conn, me.session_id), side_view(conn, me), live, vid, stt, tr, score_view(conn, me))
            found = S.find_player(conn, token_of(request, GAME))
            if found and found[1].status == "ended" and found[1].game_type == GAME and S.get_active_session(conn, GAME) is None:
                return ended_view(conn, found[1], found[0], C.leaderboard(conn, found[1].id))
            return ck_page(H1(T.CK_TITLE), P(T.CK_TAGLINE, cls="tag"), error_box(url_error(request)), status_view(conn, name), who=name)

    @rt("/checker/status", methods=["GET"])
    def ck_status(request: Request):
        with pool.connection() as conn:
            return Response(to_xml(status_view(conn, name_of(request))), media_type="text/html")

    @rt("/checker/join", methods=["POST"])
    def ck_join(request: Request, name: str = ""):
        name = name or name_of(request) or ""
        with pool.connection() as conn:
            try:
                _, player, token = S.join_session(conn, name, client_ip_of(request), existing_token=token_of(request, GAME), game_type=GAME)
            except S.SessionError as e:
                if e.code in NAME_ERRORS:
                    return name_gate("/checker", error=e.code, value=name)
                if e.code in URL_ERRORS:
                    return RedirectResponse(f"/checker?err={e.code}", status_code=303)
                return RedirectResponse("/checker", status_code=303)           # no game running: the page says so
        return remember_name(set_token_cookie(RedirectResponse("/checker", status_code=303), GAME, token), player.name)

    @rt("/checker/feed", methods=["GET"])
    def ck_feed(request: Request, after: int = 0, st: str = "ok", tr: str = ""):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:       # the session ended (or the cookie is unknown): reload -> end screen / entry page
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            feed.tick_throttled(conn, me.session_id)
            events = feed.events_after(conn, me.session_id, max(after, 0))
            now_st = feed.status(conn, me.session_id)
            live, _, stt, now_tr = view_of(conn, me)
        tr_changed = stt and tr in ("ok", "down") and tr != now_tr
        if not events and now_st == st and not tr_changed:
            return Response(status_code=204)
        parts = [poller_view(events[-1].id if events else after, now_st, now_tr if stt else None)]
        for kind, _ in PANES:
            lines = [line_view(e) for e in events if e.kind == kind]
            if lines:
                parts.append(Div(*lines, hx_swap_oob=f"beforeend:#pane-{kind}"))
        if now_st != st:
            parts.append(banner_view(now_st, oob=True, live=live))
        if tr_changed:
            parts.append(stt_banner_view(now_tr, oob=True))
        return tuple(parts)

    @rt("/checker/mark", methods=["POST"])
    def ck_mark(request: Request, kind: str = "", start_id: str = "", start_off: str = "", end_id: str = "", end_off: str = "",
                guess: str = "", note: str = ""):
        """The slide panel. The text box holds the passage the player clicked: untouched (or empty) = the claim is anchored to those
        lines; edited, or typed from scratch = a typed claim. Either way a rating is required."""
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:
                return Response(status_code=401, headers={"HX-Refresh": "true"})
            try:
                if not re.fullmatch(r"[+-]?[0-5]", (guess or "").strip()):
                    raise C.BadGuess("pick a rating from -5 to +5")
                g = int(guess)
                text = F.normalize_note(note, F.MAX_QUOTE)
                if start_id.strip() or end_id.strip():
                    feed.ensure_up(conn, me.session_id)
                    ints = (to_int(start_id), to_int(start_off), to_int(end_id), to_int(end_off))
                    quote = F.build_quote(conn, me.session_id, kind, *ints)
                    if not text or text == unicodedata.normalize("NFC", quote):
                        C.submit_claim(conn, me.id, kind, *ints, g)
                    else:
                        C.submit_typed_claim(conn, me.id, F.normalize_note(text), g)
                elif text:
                    C.submit_typed_claim(conn, me.id, F.normalize_note(text), g)
                else:
                    raise F.NothingToSend("nothing to send")
            except (F.SessionNotActive, C.SessionNotActive):
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            except (F.FalaciasError, C.CheckerError) as e:
                return fragment(feedback_view(e.code))
            except feed.FeedError:
                return fragment(feedback_view("feed_down"))
            checkworker.wake()
            return fragment(feedback_view(ok=True), side_view(conn, me, oob=True), headers={"HX-Trigger": "ck:sent"})

    @rt("/checker/score", methods=["GET"])
    def ck_score(request: Request):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            return Response(to_xml(score_view(conn, me)), media_type="text/html")

    @rt("/checker/board", methods=["GET"])
    def ck_board(request: Request):
        """The modal's body (htmx), or a plain page when the link is followed without JS."""
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            board = board_view(conn, me)
        if request.headers.get("hx-request"):
            return Response(to_xml(board), media_type="text/html")
        return ck_page(H1(T.SCORE_NOW), Div(board, cls="panel"), P(A(T.CK_TITLE, href="/checker")), who=me.name)

    @rt("/checker/side", methods=["GET"])
    def ck_side(request: Request):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            return side_view(conn, me)
