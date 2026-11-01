"""Web layer of "Falaciômetro": the menu fragment, entry/join, the two panes and their live feed.

    GET  /falacias          entry button / the game page / the end screen
    POST /falacias/start    first visitor starts the session (refused when the feed is not ready)
    POST /falacias/join     join the running session
    GET  /falacias/feed     polled every ~2 s: new lines (htmx out-of-band appends), 204 when nothing changed
    GET  /menu              the menu with a dot on games in progress

Selecting a passage and flagging it (Task 4), the leaderboard (Task 4), the admin queue (Task 5) come later.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from fasthtml.common import (
    H1,
    H2,
    A,
    Button,
    Div,
    Form,
    Iframe,
    Input,
    Label,
    Li,
    Ol,
    P,
    Script,
    Small,
    Span,
    Table,
    Tbody,
    Td,
    Textarea,
    Th,
    Thead,
    Time,
    Tr,
    to_xml,
)
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app import falacias as F
from app import feed, livestream, sttworker, timefmt
from app import host_messages as H
from app import scorebar as SB
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
    remember_name,
    result_tabs,
    set_token_cookie,
    token_of,
    url_error,
)

GAME = "falacias"
FIRST_LINES = 120          # lines per pane shown when a player opens the page
MAX_INT_DIGITS = 18
PANES = (("transcript", T.FAL_TRANSCRIPT), ("chat", T.FAL_CHAT))


# ------------------------------------------------------------------ views
def fal_page(*content, cls: str | None = None, who: str | None = None):
    return page(*content, title=T.FAL_TITLE, cls=cls, current=GAME, who=who)


def line_view(e: feed.Event):
    """One line. The text sits alone in its own span (Task 4 computes selection offsets inside it); the author is
    outside it and cannot be selected."""
    stamp = Time(f"[{timefmt.hms(e.fed_at)}]", datetime=e.fed_at.isoformat()) if e.fed_at else None
    who = Span(e.author, " " if (stamp and e.author) else None, stamp, cls="au") if (e.author or stamp) else None      # "name [HH:MM:SS]:"
    return Div(who, Span(e.text, cls="tx"), cls="pline", data_id=e.id)


def pane_view(kind: str, title: str, events: list[feed.Event], note: str | None = None, label: str | None = None):
    return Div(
        Div(Span(title, cls="who"), Span(label, cls="sttlabel") if label else None,
            Button("", type="button", cls="pill", hidden=True, data_pill=kind), cls="bar"),
        Div(P(note, cls="pnote") if note else None, *[line_view(e) for e in events], id=f"pane-{kind}", cls="scroll",
            data_kind=kind, lang="pt-BR", tabindex="0"),
        cls=f"pane panel {kind}")


def video_pane(video_id: str):
    """First of the three panels of a live game: the stream, with a button on the right of the title that hides it (falacias.js
    unloads the iframe so the sound stops; the two text panels then share the width)."""
    return Div(Div(Span(T.STREAM_TITLE, cls="who"),
                   Button(T.VIDEO_HIDE, type="button", cls="pill", data_hidevideo="1"), cls="bar"),
               Div(Iframe(src=f"https://www.youtube-nocookie.com/embed/{video_id}?autoplay=1&mute=1&playsinline=1&rel=0",
                          title=T.STREAM_TITLE, allow="autoplay; encrypted-media; picture-in-picture; fullscreen",
                          allowfullscreen=True, referrerpolicy="strict-origin-when-cross-origin"), cls="ratio"),
               cls="pane video panel")


def banner_view(st: str, oob: bool = False, live: bool = False):
    extra = {"hx_swap_oob": "true"} if oob else {}
    if st == "down":
        return Div(T.FAL_CHAT_DOWN if live else T.FEED_UNAVAILABLE, id="feedbanner", cls="error", role="alert", **extra)
    if st == "ended":
        return Div(T.FAL_VIDEO_END, id="feedbanner", cls="muted", role="status", **extra)
    return Div(id="feedbanner", **extra)


def stt_banner_view(tr: str, oob: bool = False):
    """Transcript health, SEPARATE from the chat/feed banner: a failing STT only informs, it never blocks pairs (the chat decides that)."""
    extra = {"hx_swap_oob": "true"} if oob else {}
    if tr == "down":
        return Div(T.FAL_STT_DOWN, id="sttbanner", cls="error", role="alert", **extra)
    return Div(id="sttbanner", **extra)


def poller_view(after: int, st: str, tr: str | None = None):
    """`tr` (transcript health) is only part of the URL while STT is on for this game."""
    q = f"after={after}&st={st}" + (f"&tr={tr}" if tr else "")
    return Div(id="feedpoll", hx_get=f"/falacias/feed?{q}", hx_trigger="every 2s", hx_swap="outerHTML")


def picker_view(fallacies: list[F.Fallacy]):
    """The slide panel for EVERYTHING a player sends: opened by falacias.js when a chat/transcript pane is clicked (with or without
    selected text). Selection + fallacy = a pair; fallacy + message = a typed fallacy; message alone = a note to the host. Radios (not
    <select>): iOS ignores hidden <option>s, so the filter would not work there. The selection fields are filled by the script; the
    server never trusts them beyond the ids."""
    by_cat: dict[str, list[F.Fallacy]] = defaultdict(list)
    for f in fallacies:
        by_cat[f.category].append(f)
    groups = [Div(Span(cat, cls="cat"),
                  *[Label(Input(type="radio", name="fallacy_id", value=f.id), Span(f.name), title=f.description,
                          data_name=f.name.casefold()) for f in items], cls="grp") for cat, items in by_cat.items()]
    return Form(
        Div(Span(T.FAL_SELECTED, cls="who"), Button(T.FAL_CLEAR, type="button", id="pk-clear", cls="link"), cls="bar"),
        *[Input(type="hidden", name=n, id=f"pk-{n}") for n in ("kind", "start_id", "start_off", "end_id", "end_off")],
        Input(type="text", id="pk-filter", placeholder=T.FAL_FILTER, autocomplete="off", aria_label=T.FAL_FILTER),
        Div(*groups, cls="opts"),
        Label(T.FAL_NOTE_LABEL, Textarea(id="pk-note", name="note", maxlength=F.MAX_QUOTE, rows=4, placeholder=T.FAL_NOTE_PLACEHOLDER,
                                         autocomplete="off"), cls="notefield"),
        Div(Button(T.FAL_SEND, type="submit", cls="primary"), Div(id="feedback", role="status"), cls="sendrow"),
        Small(A(T.FAL_SOURCE, href=T.FAL_SOURCE_URL, target="_blank", rel="noopener noreferrer"), cls="muted"),
        id="picker", cls="panel picker", hidden=True, hx_post="/falacias/mark", hx_swap="none")


def feedback_view(code: str | None = None, ok: bool = False, text: str | None = None):
    if code is None:
        return Div(text or T.FAL_SENT if ok else "", id="feedback", cls="ok" if ok else None, role="status", hx_swap_oob="true")
    msg = T.FAL_ERRORS.get(code) or T.HM_ERRORS.get(code) or T.FAL_ERRORS["generic"]
    return Div(msg.format(n=F.RATE_LIMIT_S if code == "rate_limited" else H.RATE_LIMIT_S, max=H.MAX_PER_PLAYER), id="feedback", cls="error",
               role="alert", hx_swap_oob="true")


def status_badge(m: F.Mark):
    label = T.FAL_VERDICTS.get(m.status, T.FAL_PENDING)
    return Span(label, cls=f"badge {m.status}")


def pair_view(m: F.Mark, names: dict[int, str], who: str | None = None):
    """One submitted pair as a card; `who` (other participants) puts the player's name on top."""
    return Li(Span(who, cls="pwho") if who else None, Span(status_badge(m), Span(F.fmt_points(F.points_of(m)), cls="pts"), cls="meta"),
              Span(f"“{m.quote}”", cls="q"), Span(names.get(m.fallacy_id, "?"), cls="fl"))


def pair_tabs(conn, session_id, me_id):
    """"Minhas frases | Outros participantes" (shared by the game page and the end screen)."""
    names = {f.id: f.name for f in F.list_fallacies(conn, enabled_only=False)}
    marks = F.session_marks(conn, session_id)
    mine = [pair_view(m, names) for uid, _, m in marks if uid == me_id]
    others = [pair_view(m, names, name) for uid, name, m in marks if uid != me_id]
    return result_tabs(Ol(*mine, cls="pairs") if mine else P(T.FAL_NO_PAIRS, cls="muted"),
                       Ol(*others, cls="pairs") if others else P(T.FAL_NO_OTHERS, cls="muted"))


def side_view(conn, me: S.Player, oob: bool = False):
    """My pairs and the other participants' (tabs). Polled every 5 s (the admin reviews in between) and returned after each submit."""
    return Div(pair_tabs(conn, me.session_id, me.id),
               id="side", cls="side", hx_get="/falacias/side", hx_trigger="every 5s", hx_swap="outerHTML",
               **({"hx_swap_oob": "true"} if oob else {}))


def points_text(hundredths: int) -> str:
    return T.POINTS_N.format(n=F.fmt_points(hundredths))


def score_view(conn, me: S.Player):
    rows = F.leaderboard(conn, me.session_id)
    mine = next((r for r in rows if r.user_id == me.id), None)
    return SB.score_bar("/falacias", me.name, points_text(mine.points if mine else 0))


def board_view(conn, me: S.Player):
    return SB.board_list([(r.rank, r.name, points_text(r.points), r.user_id == me.id) for r in F.leaderboard(conn, me.session_id)])


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
        liveline = None                    # offline channel: say nothing (the admin chooses the source, not the channel)
    elif sess_mode == "live" and info.id == vid:
        liveline = None                    # the session's own line already shows this stream
    else:
        lab = T.SRC_LIVE_NOW if info.horizontal else T.SRC_LIVE_VERTICAL
        liveline = P(Span(lab + ": ", cls="who"), info.title or T.SRC_TITLE_UNKNOWN, " · ", A(info.url, href=info.url, target="_blank", rel="noopener"),
                     cls="muted" if info.horizontal else "muted vertical")
    video = P(Span(label + ": ", cls="who"), title or T.SRC_TITLE_UNKNOWN, " · ", A(url, href=url, target="_blank", rel="noopener")) \
        if show_video else None
    return Div(video, liveline,
               id="source", cls="source", hx_get="/falacias/source", hx_trigger=poll, hx_swap="outerHTML")


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
    return fal_page(H1(T.FAL_TITLE), P(T.FAL_TAGLINE, cls="tag"), source, err, panel, who=name or None)


def game_view(me: S.Player, events: list[feed.Event], last_id: int, st: str, fallacies, side, source=None,
              live: bool = False, video_id: str | None = None, stt: bool = False, tr: str = "ok", score=None):
    stt = stt and live
    by_kind = {k: [e for e in events if e.kind == k] for k, _ in PANES}
    intro = [P(T.FAL_LIVE_HOW_TO_STT if stt else T.FAL_LIVE_HOW_TO, cls="muted")] if live else [P(T.FAL_HOW_TO, cls="muted"), P(T.FAL_PICK_HELP, cls="muted"),
                                                              P(T.FAL_REPLAY, cls="muted")]
    show = bool(live and video_id)                  # live game: video | chat | transcript; replay: transcript | chat as before
    kinds = (("chat", T.FAL_CHAT), ("transcript", T.FAL_TRANSCRIPT)) if show else PANES
    return fal_page(
        H1(T.FAL_TITLE), score or P(f"{me.name} ({T.YOU})", cls="tag"), *intro,
        source,
        banner_view(st, live=live),
        stt_banner_view(tr) if stt else None,
        Button(T.VIDEO_SHOW, type="button", cls="pill showvideo", data_showvideo="1", hidden=True) if show else None,
        Div(*([video_pane(video_id)] if show else []),
            *[pane_view(k, title, by_kind[k], note=T.FAL_NO_TRANSCRIPT if live and not stt and k == "transcript" else None,
                        label=T.FAL_STT_LABEL if stt and k == "transcript" else None)
              for k, title in kinds],
            cls="panes" + (" three" if show else "")),
        poller_view(last_id, st, tr if stt else None),
        picker_view(fallacies),
        side,
        SB.board_dialog(),
        Script(src="/falacias.js", defer=True),
        Script(src="/tabs.js", defer=True),
        cls="fal", who=me.name)


def ended_view(conn, session: S.GameSession, me: S.Player, rows: list[F.LeaderRow]):
    """Final ranking: the stored winner (not recomputed), per-player verdict counts, and my own pairs with their verdicts."""
    winner = next((r for r in rows if r.user_id == session.winner_user_id), None)
    head = P(T.FAL_WINNER.format(name=winner.name, pts=F.fmt_points(winner.points)), cls="winner") if winner \
        else None
    table = Table(Thead(Tr(Th("#"), Th(T.AF_COL_PLAYER), Th(T.AF_COL_POINTS), Th(T.FAL_COL_CORRECT), Th(T.FAL_COL_WRONG),
                           Th(T.FAL_COL_SPAM), Th(T.FAL_COL_PENDING))),
                  Tbody(*[Tr(Td(str(r.rank)), Td(r.name), Td(F.fmt_points(r.points)), Td(str(r.correct)), Td(str(r.wrong)),
                             Td(str(r.spam)), Td(str(r.pending)), cls="me" if r.user_id == me.id else None) for r in rows]),
                  cls="users final")
    mine = next((r for r in rows if r.user_id == me.id), None)
    return fal_page(H1(T.FAL_GAME_OVER), head,
                    Div(H2(T.LEADERBOARD), table, P(T.FAL_UNREVIEWED_NOTE, cls="muted") if mine and mine.pending else None,
                        cls="panel"),
                    pair_tabs(conn, session.id, me.id),
                    Script(src="/tabs.js", defer=True), cls="fal", who=me.name)


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
                stt = src[1] == "live" and sttworker.enabled()
                return game_view(me, events, feed.last_event_id(conn, me.session_id), feed.status(conn, me.session_id),
                                 F.list_fallacies(conn), side_view(conn, me), source_view(live, src),
                                 live=src[1] == "live", video_id=src[0],
                                 stt=stt, tr=sttworker.health(me.session_id) if stt else "ok", score=score_view(conn, me))
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
    def falacias_feed(request: Request, after: int = 0, st: str = "ok", tr: str = ""):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:       # the session ended (or the cookie is unknown): reload -> end screen / entry form
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            feed.tick_throttled(conn, me.session_id)
            events = feed.events_after(conn, me.session_id, max(after, 0))
            now_st = feed.status(conn, me.session_id)
            is_live = feed.session_source(conn, me.session_id)[1] == "live"
            stt = is_live and sttworker.enabled()
            now_tr = sttworker.health(me.session_id) if stt else ""
        tr_changed = stt and tr in ("ok", "down") and tr != now_tr         # an old page without `tr` just never shows the banner
        if not events and now_st == st and not tr_changed:
            return Response(status_code=204)
        new_after = events[-1].id if events else after
        parts = [poller_view(new_after, now_st, now_tr or None)]
        for kind, _ in PANES:
            lines = [line_view(e) for e in events if e.kind == kind]
            if lines:       # htmx appends the CHILDREN of an out-of-band element with a swap style
                parts.append(Div(*lines, hx_swap_oob=f"beforeend:#pane-{kind}"))
        if now_st != st:
            parts.append(banner_view(now_st, oob=True, live=is_live))
        if tr_changed:
            parts.append(stt_banner_view(now_tr, oob=True))
        return tuple(parts)

    def fragment(*parts, headers: dict | None = None) -> Response:
        return Response(to_xml(parts), media_type="text/html", headers=headers or {})

    def to_int(raw: str) -> int:
        if not re.fullmatch(rf"-?\d{{1,{MAX_INT_DIGITS}}}", raw or ""):
            raise F.BadSelection("not an integer")
        return int(raw)

    @rt("/falacias/mark", methods=["POST"])
    def falacias_mark(request: Request, kind: str = "", start_id: str = "", start_off: str = "", end_id: str = "",
                      end_off: str = "", fallacy_id: str = "", note: str = ""):
        """The slide panel. selection + fallacy = pair (the message is its note); fallacy + message = typed fallacy;
        message alone = a note to the host."""
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:
                return Response(status_code=401, headers={"HX-Refresh": "true"})
            try:
                text = F.normalize_note(note, F.MAX_QUOTE)      # an untouched passage may be longer than a typed text
                has_selection = bool(start_id.strip() or end_id.strip())
                has_fallacy = bool(fallacy_id.strip())
                if has_fallacy and not re.fullmatch(rf"\d{{1,{MAX_INT_DIGITS}}}", fallacy_id.strip()):
                    raise F.UnknownFallacy("not a fallacy id")
                message_only = False
                if has_selection:
                    if not has_fallacy:
                        raise F.NeedFallacy("a fallacy is needed for a selected passage")
                    feed.ensure_up(conn, me.session_id)
                    ints = (to_int(start_id), to_int(start_off), to_int(end_id), to_int(end_off))
                    quote = F.build_quote(conn, me.session_id, kind, *ints)
                    if not text or text == unicodedata.normalize("NFC", quote):
                        F.submit_mark(conn, me.id, kind, *ints, to_int(fallacy_id))       # the text is still the passage: anchored pair
                    else:
                        # The player edited the text: it is no longer that passage. Sent as typed text, without context or speed bonus.
                        F.submit_spoken(conn, me.id, F.normalize_note(text), to_int(fallacy_id))
                elif has_fallacy:
                    if not text:
                        raise F.NeedText("a fallacy needs a passage or a typed phrase")
                    F.submit_spoken(conn, me.id, F.normalize_note(text), to_int(fallacy_id))
                elif text:
                    H.send(conn, me.id, F.normalize_note(text))
                    message_only = True
                else:
                    raise F.NothingToSend("nothing to send")
            except F.SessionNotActive:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            except (F.FalaciasError, H.HostMessageError) as e:
                return fragment(feedback_view(e.code))
            except feed.FeedError:
                return fragment(feedback_view("feed_down"))
            return fragment(feedback_view(ok=True, text=T.FAL_SENT_MESSAGE if message_only else None), side_view(conn, me, oob=True),
                            headers={"HX-Trigger": "fal:sent"})

    @rt("/falacias/source")
    def falacias_source(request: Request):
        with pool.connection() as conn:
            return fragment(source_view(live, session_src(conn, request)))        # public information, like /menu

    @rt("/falacias/score")
    def falacias_score(request: Request):
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            return Response(to_xml(score_view(conn, me)), media_type="text/html")

    @rt("/falacias/board")
    def falacias_board(request: Request):
        """The modal's body (htmx), or a plain page when the link is followed without JS."""
        with pool.connection() as conn:
            me = S.resume(conn, token_of(request, GAME), GAME)
            if me is None:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            board = board_view(conn, me)
        if request.headers.get("hx-request"):
            return Response(to_xml(board), media_type="text/html")
        return fal_page(H1(T.SCORE_NOW), Div(board, cls="panel"), P(A(T.FAL_TITLE, href="/falacias")), who=me.name)

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
