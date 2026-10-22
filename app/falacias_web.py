"""Web layer of "Encontre as Falácias": the menu fragment, entry/join, the two panes and their live feed.

    GET  /falacias          entry button / the game page / the end screen
    POST /falacias/start    first visitor starts the session (refused when the feed is not ready)
    POST /falacias/join     join the running session
    GET  /falacias/feed     polled every ~2 s: new lines (htmx out-of-band appends), 204 when nothing changed
    GET  /menu              the menu with a dot on games in progress

Selecting a passage and flagging it (Task 4), the leaderboard (Task 4), the admin queue (Task 5) come later.
"""
from __future__ import annotations

import re
from collections import defaultdict

from fasthtml.common import (
    H1,
    H2,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    Li,
    Ol,
    Option,
    P,
    Script,
    Select,
    Small,
    Span,
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

from app import falacias as F
from app import feed, livestream
from app import host_messages as H
from app import sessions as S
from app import texts as T
from app.ui import (
    NAME_ERRORS,
    URL_ERRORS,
    client_ip_of,
    error_box,
    menu,
    name_gate,
    name_of,
    page,
    play_form,
    recovery_view,
    remember_name,
    set_token_cookie,
    token_of,
    url_error,
)

GAME = "falacias"
FIRST_LINES = 120          # lines per pane shown when a player opens the page
MAX_INT_DIGITS = 18
PANES = (("transcript", T.FAL_TRANSCRIPT), ("chat", T.FAL_CHAT))


# ------------------------------------------------------------------ views
def fal_page(*content, cls: str | None = None):
    return page(*content, title=T.FAL_TITLE, cls=cls, current=GAME)


def line_view(e: feed.Event):
    """One line. The text sits alone in its own span (Task 4 computes selection offsets inside it); the author is
    outside it and cannot be selected."""
    return Div(Span(e.author, cls="au") if e.author else None, Span(e.text, cls="tx"), cls="pline", data_id=e.id)


def pane_view(kind: str, title: str, events: list[feed.Event], note: str | None = None):
    return Div(
        Div(Span(title, cls="who"), Button("", type="button", cls="pill", hidden=True, data_pill=kind), cls="bar"),
        Div(P(note, cls="pnote") if note else None, *[line_view(e) for e in events], id=f"pane-{kind}", cls="scroll",
            data_kind=kind, lang="pt-BR", tabindex="0"),
        cls=f"pane panel {kind}")


def banner_view(st: str, oob: bool = False, live: bool = False):
    extra = {"hx_swap_oob": "true"} if oob else {}
    if st == "down":
        return Div(T.FAL_CHAT_DOWN if live else T.FEED_UNAVAILABLE, id="feedbanner", cls="error", role="alert", **extra)
    if st == "ended":
        return Div(T.FAL_VIDEO_END, id="feedbanner", cls="muted", role="status", **extra)
    return Div(id="feedbanner", **extra)


def poller_view(after: int, st: str):
    return Div(id="feedpoll", hx_get=f"/falacias/feed?after={after}&st={st}", hx_trigger="every 2s", hx_swap="outerHTML")


def picker_view(fallacies: list[F.Fallacy]):
    """Hidden until falacias.js captures a selection. Radios (not <select>): iOS ignores hidden <option>s, so the filter
    would not work there. The selection fields are filled by the script; the server never trusts them beyond the ids."""
    by_cat: dict[str, list[F.Fallacy]] = defaultdict(list)
    for f in fallacies:
        by_cat[f.category].append(f)
    groups = [Div(Span(cat, cls="cat"),
                  *[Label(Input(type="radio", name="fallacy_id", value=f.id, required=True), Span(f.name), title=f.description,
                          data_name=f.name.casefold()) for f in items], cls="grp") for cat, items in by_cat.items()]
    return Form(
        Div(Span(T.FAL_SELECTED, cls="who"), Button(T.FAL_CLEAR, type="button", id="pk-clear", cls="link"), cls="bar"),
        P(id="pk-quote", cls="quote"),
        *[Input(type="hidden", name=n, id=f"pk-{n}") for n in ("kind", "start_id", "start_off", "end_id", "end_off")],
        Input(type="text", id="pk-filter", placeholder=T.FAL_FILTER, autocomplete="off", aria_label=T.FAL_FILTER),
        Div(*groups, cls="opts"),
        Div(Button(T.FAL_SEND, type="submit", cls="primary"), Div(id="feedback", role="status"), cls="sendrow"),
        Small(A(T.FAL_SOURCE, href=T.FAL_SOURCE_URL, target="_blank", rel="noopener noreferrer"), cls="muted"),
        id="picker", cls="panel picker", hidden=True, hx_post="/falacias/mark", hx_swap="none")


def feedback_view(code: str | None = None, ok: bool = False):
    if code is None:
        return Div(T.FAL_SENT if ok else "", id="feedback", cls="ok" if ok else None, role="status", hx_swap_oob="true")
    return Div(T.FAL_ERRORS.get(code, T.FAL_ERRORS["generic"]).format(n=F.RATE_LIMIT_S), id="feedback", cls="error",
               role="alert", hx_swap_oob="true")


def status_badge(m: F.Mark):
    label = T.FAL_VERDICTS.get(m.status, T.FAL_PENDING)
    return Span(label, cls=f"badge {m.status}")


def side_view(conn, me: S.Player, oob: bool = False):
    """My pairs + the leaderboard. Polled every 5 s (the admin reviews in between) and returned after each submit."""
    names = {f.id: f.name for f in F.list_fallacies(conn, enabled_only=False)}
    mine = [Li(Span(status_badge(m), Span(F.fmt_points(F.points_of(m)), cls="pts"), cls="meta"),
               Span(f"“{m.quote}”", cls="q"), Span(names.get(m.fallacy_id, "?"), cls="fl")) for m in F.user_marks(conn, me.id)]
    rows = F.leaderboard(conn, me.session_id)
    board = [Li(Span(f"{r.rank}.", cls="pos"), Span(r.name, cls="nm"), Span(F.fmt_points(r.points), cls="pts"),
                cls="me" if r.user_id == me.id else None) for r in rows]
    return Div(Div(H2(T.FAL_MINE), Ol(*mine, cls="pairs") if mine else P(T.FAL_NO_PAIRS, cls="muted"), cls="panel"),
               Div(H2(T.LEADERBOARD), Ol(*board, cls="lb"), cls="panel"),
               id="side", cls="side", hx_get="/falacias/side", hx_trigger="every 5s", hx_swap="outerHTML",
               **({"hx_swap_oob": "true"} if oob else {}))


def hm_count_view(n: int, oob: bool = False):
    return Span(T.HM_COUNT.format(n=n, max=H.MAX_PER_PLAYER), id="hm-count", cls="muted", hx_swap_oob="true" if oob else None)


def hm_feedback_view(code: str | None = None):
    if code is None:
        return Div(T.HM_SENT, id="hm-feedback", cls="ok", role="status", hx_swap_oob="true")
    return Div(T.HM_ERRORS.get(code, T.HM_ERRORS["generic"]).format(n=H.RATE_LIMIT_S, max=H.MAX_PER_PLAYER),
               id="hm-feedback", cls="error", role="alert", hx_swap_oob="true")


def message_box_view(sent: int):
    return Form(H2(T.HM_HEADING), P(T.HM_HELP, cls="muted"),
                Textarea(id="hm-text", name="text", maxlength=H.MAX_LEN, rows=3, required=True, placeholder=T.HM_PLACEHOLDER,
                         aria_label=T.HM_HEADING),
                Div(Button(T.HM_SEND, type="submit", cls="primary"), hm_count_view(sent), Div(id="hm-feedback", role="status"),
                    cls="sendrow"),
                id="hostbox", cls="panel", hx_post="/falacias/message", hx_swap="none")


def source_view(live: livestream.LiveStream, sess: tuple[str | None, str | None] | None = None):
    """Title + URL of the video the game uses and of the channel's live stream, if any. `sess` = (video_id, mode) of the running
    session: a live session shows the live stream, a replay session its video; without a session (entry page) the replay line only
    appears when no horizontal live exists (a horizontal live always wins). Polled like /menu: fast while a title/live answer is
    still being looked up, slowly afterwards; the fragment has NO `load` trigger (no loop)."""
    info = live.info()
    sess_vid, sess_mode = sess if sess else (None, None)
    if sess_vid and sess_mode in ("live", "replay"):
        vid, label = sess_vid, (T.SRC_SESSION_LIVE if sess_mode == "live" else T.SRC_VIDEO)
    else:
        vid, label = feed.feed_config().video_id, T.SRC_VIDEO
    show_video = sess_vid is not None            # without a session (entry page) players cannot start a replay: nothing to show
    title = info.title if info is not None and info.id == vid and info.title else live.title_of(vid)
    poll = "every 5s" if title is None or not live.known else "every 60s"
    url = f"https://www.youtube.com/watch?v={vid}"
    if info is None:
        liveline = P(T.SRC_LIVE_OFFLINE, cls="muted") if live.known else None
    elif sess_mode == "live" and info.id == vid:
        liveline = None                    # the session's own line already shows this stream
    else:
        lab = T.SRC_LIVE_NOW if info.horizontal else T.SRC_LIVE_VERTICAL
        liveline = P(Span(lab + ": ", cls="who"), info.title or T.SRC_TITLE_UNKNOWN, " · ", A(info.url, href=info.url, target="_blank", rel="noopener"),
                     cls="muted" if info.horizontal else "muted vertical")
    video = P(Span(label + ": ", cls="who"), title or T.SRC_TITLE_UNKNOWN, " · ", A(url, href=url, target="_blank", rel="noopener")) \
        if show_video else None
    return Div(video, liveline, P(T.SRC_ONLY_HORIZONTAL, cls="muted small"),
               id="source", cls="source", hx_get="/falacias/source", hx_trigger=poll, hx_swap="outerHTML")


def spoken_view(fallacies: list[F.Fallacy]):
    return Form(H2(T.FAL_SPOKEN_HEADING), P(T.FAL_SPOKEN_HELP, cls="muted"),
                Textarea(id="sp-text", name="text", maxlength=F.MAX_SPOKEN, rows=3, required=True, placeholder=T.FAL_SPOKEN_PLACEHOLDER,
                         aria_label=T.FAL_SPOKEN_HEADING),
                Select(*[Option(f.name, value=str(f.id)) for f in fallacies], name="fallacy_id", required=True, aria_label=T.FAL_SPOKEN_FALLACY),
                Div(Button(T.FAL_SPOKEN_SEND, type="submit", cls="primary"), Div(id="sp-feedback", role="status"), cls="sendrow"),
                id="spokenbox", cls="panel", hx_post="/falacias/spoken", hx_swap="none")


def spoken_feedback_view(code: str | None = None):
    if code is None:
        return Div(T.FAL_SENT, id="sp-feedback", cls="ok", role="status", hx_swap_oob="true")
    return Div(T.FAL_SPOKEN_ERRORS.get(code, T.FAL_SPOKEN_ERRORS["generic"]).format(n=F.RATE_LIMIT_S), id="sp-feedback", cls="error",
               role="alert", hx_swap_oob="true")


def entry_view(active: S.GameSession | None, n_players: int = 0, error: str | None = None, name: str = "", source=None,
               detail: str | None = None):
    if active is None:
        panel = Div(H2(T.FAL_START_HEADING), P(T.START_HELP, cls="muted"),
                    play_form("/falacias/start", T.START_BUTTON, name, "/falacias"), cls="panel")
    else:
        panel = Div(H2(T.FAL_JOIN_HEADING), P(T.players_in_game(n_players), cls="muted"),
                    play_form("/falacias/join", T.JOIN_BUTTON, name, "/falacias"), cls="panel")
    err = Div(T.FAL_REPLAY_MISSING.format(id=detail), cls="error", role="alert") if error == "feed_unavailable" and detail \
        else error_box(error)
    return fal_page(H1(T.FAL_TITLE), P(T.FAL_TAGLINE, cls="tag"), source, err, panel)


def game_view(me: S.Player, events: list[feed.Event], last_id: int, st: str, fallacies, side, sent: int = 0, source=None,
              live: bool = False, recover=None):
    by_kind = {k: [e for e in events if e.kind == k] for k, _ in PANES}
    intro = [P(T.FAL_LIVE_HOW_TO, cls="muted")] if live else [P(T.FAL_HOW_TO, cls="muted"), P(T.FAL_PICK_HELP, cls="muted"),
                                                              P(T.FAL_REPLAY, cls="muted")]
    return fal_page(
        H1(T.FAL_TITLE), P(f"{me.name} ({T.YOU})", cls="tag"), *intro,
        source,
        banner_view(st, live=live),
        Div(*[pane_view(k, title, by_kind[k], note=T.FAL_NO_TRANSCRIPT if live and k == "transcript" else None) for k, title in PANES],
            cls="panes"),
        poller_view(last_id, st),
        picker_view(fallacies),
        spoken_view(fallacies) if live else None,
        side,
        message_box_view(sent),
        recover,
        Script(src="/falacias.js", defer=True),
        cls="fal")


def ended_view(conn, session: S.GameSession, me: S.Player, rows: list[F.LeaderRow]):
    """Final ranking: the stored winner (not recomputed), per-player verdict counts, and my own pairs with their verdicts."""
    winner = next((r for r in rows if r.user_id == session.winner_user_id), None)
    head = P(T.FAL_WINNER.format(name=winner.name, pts=F.fmt_points(winner.points)), cls="winner") if winner \
        else P(T.FAL_NO_POINTS, cls="muted")
    table = Table(Thead(Tr(Th("#"), Th(T.AF_COL_PLAYER), Th(T.AF_COL_POINTS), Th(T.FAL_COL_CORRECT), Th(T.FAL_COL_WRONG),
                           Th(T.FAL_COL_SPAM), Th(T.FAL_COL_PENDING))),
                  Tbody(*[Tr(Td(str(r.rank)), Td(r.name), Td(F.fmt_points(r.points)), Td(str(r.correct)), Td(str(r.wrong)),
                             Td(str(r.spam)), Td(str(r.pending)), cls="me" if r.user_id == me.id else None) for r in rows]),
                  cls="users final")
    mine = next((r for r in rows if r.user_id == me.id), None)
    names = {f.id: f.name for f in F.list_fallacies(conn, enabled_only=False)}
    pairs = [Li(Span(status_badge(m), Span(F.fmt_points(F.points_of(m)), cls="pts"), cls="meta"),
                Span(f"“{m.quote}”", cls="q"), Span(names.get(m.fallacy_id, "?"), cls="fl")) for m in F.user_marks(conn, me.id)]
    return fal_page(H1(T.FAL_GAME_OVER), head,
                    Div(H2(T.LEADERBOARD), table, P(T.FAL_UNREVIEWED_NOTE, cls="muted") if mine and mine.pending else None,
                        cls="panel"),
                    Div(H2(T.FAL_MINE), Ol(*pairs, cls="pairs") if pairs else P(T.FAL_NO_PAIRS, cls="muted"), cls="panel"))


# ------------------------------------------------------------------ routes
def register(rt, pool, live: livestream.LiveStream | None = None) -> None:
    live = live or livestream.LiveStream()

    def session_src(conn, request: Request):
        me = S.resume(conn, token_of(request, GAME), GAME)
        return feed.session_source(conn, me.session_id) if me is not None else None

    def enter(request: Request, kind: str, name: str):
        ip = client_ip_of(request)
        name = name or name_of(request) or ""
        with pool.connection() as conn:
            try:
                if kind == "start":
                    if not live.known:
                        live.refresh()                 # the first visitor waits for the answer: a live must never be missed
                    source = feed.choose_source(conn, live.info(), None)   # players never pick a replay: the admin does
                    _, player, token = feed.start_falacias(conn, name, ip, source)
                else:
                    _, player, token = S.join_session(conn, name, ip, existing_token=token_of(request, GAME), game_type=GAME)
            except S.SessionError as e:
                if e.code in NAME_ERRORS:
                    return name_gate("/falacias", error=e.code, value=name)
                if e.code in URL_ERRORS:
                    return RedirectResponse(f"/falacias?err={e.code}", status_code=303)
                active = S.get_active_session(conn, GAME)
                n = S.count_players(conn, active.id) if active else 0
                return entry_view(active, n, error=e.code, name=name, source=source_view(live),
                                  detail=getattr(e, "video_id", None))
        return remember_name(set_token_cookie(RedirectResponse("/falacias", status_code=303), GAME, token), player.name)

    @rt("/falacias")
    def falacias_home(request: Request):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is not None:
                feed.tick_throttled(conn, me.session_id)
                events = feed.recent_events(conn, me.session_id, FIRST_LINES)
                src = feed.session_source(conn, me.session_id)
                return game_view(me, events, feed.last_event_id(conn, me.session_id), feed.status(conn, me.session_id),
                                 F.list_fallacies(conn), side_view(conn, me), H.sent_count(conn, me.id), source_view(live, src),
                                 live=src[1] == "live", recover=recovery_view(request, GAME))
            active = S.get_active_session(conn, GAME)
            found = S.find_player(conn, token_of(request, GAME))
            if active is None and found and found[1].status == "ended" and found[1].game_type == GAME:
                return ended_view(conn, found[1], found[0], F.leaderboard(conn, found[1].id))
            name = name_of(request)
            if name is None:
                return name_gate("/falacias")
            if active is not None and S.name_taken(conn, active.id, name):
                return name_gate("/falacias", error="name_taken", value=name)
            return entry_view(active, S.count_players(conn, active.id) if active else 0, name=name, source=source_view(live),
                              error=url_error(request))

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
            is_live = feed.session_source(conn, me.session_id)[1] == "live"
        if not events and now_st == st:
            return Response(status_code=204)
        new_after = events[-1].id if events else after
        parts = [poller_view(new_after, now_st)]
        for kind, _ in PANES:
            lines = [line_view(e) for e in events if e.kind == kind]
            if lines:       # htmx appends the CHILDREN of an out-of-band element with a swap style
                parts.append(Div(*lines, hx_swap_oob=f"beforeend:#pane-{kind}"))
        if now_st != st:
            parts.append(banner_view(now_st, oob=True, live=is_live))
        return tuple(parts)

    def fragment(*parts, headers: dict | None = None) -> Response:
        return Response(to_xml(parts), media_type="text/html", headers=headers or {})

    def to_int(raw: str) -> int:
        if not re.fullmatch(rf"-?\d{{1,{MAX_INT_DIGITS}}}", raw or ""):
            raise F.BadSelection("not an integer")
        return int(raw)

    @rt("/falacias/mark", methods=["POST"])
    def falacias_mark(request: Request, kind: str = "", start_id: str = "", start_off: str = "", end_id: str = "",
                      end_off: str = "", fallacy_id: str = ""):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:
                return Response(status_code=401, headers={"HX-Refresh": "true"})
            try:
                feed.ensure_up(conn, me.session_id)
                F.submit_mark(conn, me.id, kind, to_int(start_id), to_int(start_off), to_int(end_id), to_int(end_off),
                              to_int(fallacy_id))
            except F.SessionNotActive:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            except F.FalaciasError as e:
                return fragment(feedback_view(e.code))
            except feed.FeedError:
                return fragment(feedback_view("feed_down"))
            return fragment(feedback_view(ok=True), side_view(conn, me, oob=True), headers={"HX-Trigger": "fal:sent"})

    @rt("/falacias/source")
    def falacias_source(request: Request):
        with pool.connection() as conn:
            return fragment(source_view(live, session_src(conn, request)))        # public information, like /menu

    @rt("/falacias/spoken", methods=["POST"])
    def falacias_spoken(request: Request, text: str = "", fallacy_id: str = ""):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:
                return Response(status_code=401, headers={"HX-Refresh": "true"})
            try:
                F.submit_spoken(conn, me.id, text, to_int(fallacy_id))
            except F.SessionNotActive:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            except F.BadSelection:
                code = "unknown_fallacy" if not re.fullmatch(rf"\d{{1,{MAX_INT_DIGITS}}}", fallacy_id or "") else "bad_selection"
                return fragment(spoken_feedback_view(code))
            except F.FalaciasError as e:
                return fragment(spoken_feedback_view(e.code))
            return fragment(spoken_feedback_view(), side_view(conn, me, oob=True), headers={"HX-Trigger": "fal:spoken-sent"})

    @rt("/falacias/message", methods=["POST"])
    def falacias_message(request: Request, text: str = ""):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:
                return Response(status_code=401, headers={"HX-Refresh": "true"})
            try:
                H.send(conn, me.id, text)
            except F.SessionNotActive:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            except H.HostMessageError as e:
                return fragment(hm_feedback_view(e.code))
            return fragment(hm_feedback_view(), hm_count_view(H.sent_count(conn, me.id), oob=True),
                            headers={"HX-Trigger": "fal:msg-sent"})

    @rt("/falacias/side")
    def falacias_side(request: Request):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            return side_view(conn, me)

    @rt("/menu")
    def menu_fragment(cur: str = "bingo"):
        with pool.connection() as conn:
            running = frozenset(g for g in S.GAMES if S.get_active_session(conn, g) is not None)
        return menu(cur if cur in S.GAMES else "bingo", running, first_load=False)    # "load" here would loop forever
