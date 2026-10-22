"""Admin tab "Encontre as Falácias": start/finish, the live queue of pairs (Correto / Errado / Spam), host messages, leaderboard.

    GET  /admin/falacias            the page (HTTP Basic, same as /admin)
    POST /admin/falacias/start      start a session (same feed-readiness check as for players)
    POST /admin/falacias/finish     end it: no review after that
    GET  /admin/falacias/queue      polled every 3 s: NEW pending pairs appended at the BOTTOM (204 when none).
                                    Never inserted above: the admin could click the wrong pair.
    POST /admin/falacias/review     a verdict for one pair, or for every identical pending pair; redraws only those items
    GET  /admin/falacias/side       polled every 3 s: host messages + leaderboard
    POST /admin/falacias/message    mark a host message Passada / Ocultar / Desfazer
"""
from __future__ import annotations

import uuid

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
    Main,
    Ol,
    P,
    Small,
    Span,
    Summary,
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
from app import falacias as F
from app import feed, livestream, timefmt
from app import host_messages as H
from app import sessions as S
from app import texts as T
from app.admin_web import admin_tabs, denied

QUEUE_POLL = "every 3s"
VERDICT_LABELS = {"correct": T.AF_CORRECT, "wrong": T.AF_WRONG, "spam": T.AF_SPAM}


# ------------------------------------------------------------------ views
def pending_span(n: int):
    return Span(str(n), id="pendingcount", cls="pill on", hx_swap_oob="true")


def verdict_form(it: F.QueueItem, verdict: str, token: str, group: bool = False):
    """One button = one tiny htmx form that redraws only this item (and, for a group verdict, its identical twins)."""
    current = it.mark.status == verdict and not group
    return Form(
        Input(type="hidden", name="session_id", value=str(it.mark.session_id)),
        Input(type="hidden", name="mark_id", value=str(it.mark.id)),
        Input(type="hidden", name="verdict", value=verdict),
        Input(type="hidden", name="group", value="1" if group else "0"),
        Input(type="hidden", name="csrf", value=token),
        Button(VERDICT_LABELS[verdict], type="submit", cls=f"vbtn {verdict}" + (" on" if current else ""),
               aria_pressed="true" if current else "false"),
        method="post", action="/admin/falacias/review", hx_post="/admin/falacias/review",
        hx_target=f"#m-{it.mark.id}", hx_swap="outerHTML", cls="vform")


def item_view(it: F.QueueItem, token: str, oob: bool = False):
    m = it.mark
    badge = T.FAL_VERDICTS.get(m.status, T.FAL_PENDING)
    typed = m.kind == F.SPOKEN
    context = None if typed else Div(*[P(t, cls="ctx") for t in it.before], *[P(t, cls="sel") for t in it.selected],
                                     *[P(t, cls="ctx") for t in it.after], cls="context", lang="pt-BR")
    pending = m.status == "pending"
    return Li(
        Div(Span(it.name, cls="who"), Span(it.fallacy, cls="fl"),
            Span(T.AF_TYPED, cls="badge typed") if typed
            else Small(T.AF_SENT_AFTER.format(s=f"{m.latency_ms / 1000:.1f}".replace(".", ",")), cls="muted"),
            Span(badge, cls=f"badge {m.status}"), cls="qhead"),
        context,
        P(f"“{m.quote}”", cls="quote"),
        Div(*[verdict_form(it, v, token) for v in F.VERDICTS], cls="vrow"),
        Div(Small(T.AF_ALL_SAME.format(n=it.same), cls="muted"), *[verdict_form(it, v, token, group=True) for v in F.VERDICTS],
            cls="vrow group") if pending and it.same > 1 else None,
        id=f"m-{m.id}", cls=f"qitem {m.status}", hx_swap_oob="true" if oob else None)


def poller(after: int):
    return Div(id="queue-poll", hx_get=f"/admin/falacias/queue?after={after}", hx_trigger=QUEUE_POLL, hx_swap="outerHTML")


def message_card(msg: H.MessageRow, token: str, sid: uuid.UUID):
    def act(status: str, label: str):
        return Form(Input(type="hidden", name="session_id", value=str(sid)), Input(type="hidden", name="message_id", value=str(msg.id)),
                    Input(type="hidden", name="status", value=status), Input(type="hidden", name="csrf", value=token),
                    Button(label, type="submit", cls="link"), method="post", action="/admin/falacias/message",
                    hx_post="/admin/falacias/message", hx_target="#adminside", hx_swap="outerHTML", cls="vform")
    handled = msg.status != "new"
    return Div(P(msg.text, cls="mtext"),
               Div(Small(f"{msg.name} · {timefmt.hms(msg.created_at)}", cls="muted"),
                   *([act("new", T.AF_MSG_UNDO)] if handled else [act("done", T.AF_MSG_DONE), act("hidden", T.AF_MSG_HIDE)]),
                   cls="mfoot"),
               cls="mcard" + (" handled" if handled else ""))


def side_view(conn, session: S.GameSession, token: str):
    msgs = H.list_messages(conn, session.id)
    new = [m for m in msgs if m.status == "new"]
    old = [m for m in msgs if m.status != "new"]
    rows = F.leaderboard(conn, session.id)
    board = Table(Thead(Tr(Th("#"), Th(T.AF_COL_PLAYER), Th(T.AF_COL_POINTS), Th(T.AF_COL_PAIRS), Th(T.AF_COL_PENDING))),
                  Tbody(*[Tr(Td(str(r.rank)), Td(r.name), Td(F.fmt_points(r.points)), Td(str(r.pairs)), Td(str(r.pending)))
                          for r in rows]), cls="users")
    return Div(
        Div(H2(T.AF_MESSAGES, " ", Span(str(len(new)), cls="pill on" if new else "pill")),
            *[message_card(m, token, session.id) for m in new] or [P(T.AF_NO_MESSAGES, cls="muted")],
            Details(Summary(f"{T.AF_MSG_HANDLED} ({len(old)})"), *[message_card(m, token, session.id) for m in old]) if old else None,
            cls="panel"),
        Div(H2(T.AF_BOARD), board if rows else P(T.ADMIN_NO_USERS, cls="muted"), cls="panel"),
        id="adminside", hx_get="/admin/falacias/side", hx_trigger=QUEUE_POLL, hx_swap="outerHTML")


def start_form(pw: str, show_video: bool):
    return Form(Input(type="hidden", name="csrf", value=A_.start_token(pw)),
                Label(T.AF_VIDEO_FIELD, Input(type="url", name="video", placeholder="https://www.youtube.com/watch?v=...",
                                              autocomplete="off"), cls="videofield") if show_video else None,
                Button(T.AF_START, type="submit", cls="primary"), method="post", action="/admin/falacias/start", cls="startform")


def error_text(error: str, detail: str | None) -> str:
    if error == "feed_unavailable" and detail:
        return T.FAL_REPLAY_MISSING.format(id=detail)
    if error in ("feed_unavailable", "feed_error"):
        return T.FEED_UNAVAILABLE
    return T.ERRORS[error] if error in ("no_source", "invalid_video") else T.ERRORS["generic"]


def source_line(conn, session: S.GameSession):
    vid, mode = feed.session_source(conn, session.id)
    if mode is None:
        vid, mode = feed.feed_config().video_id, "replay"
    url = f"https://www.youtube.com/watch?v={vid}"
    return P(Span((T.SRC_SESSION_LIVE if mode == "live" else T.SRC_VIDEO) + ": ", cls="who"),
             A(url, href=url, target="_blank", rel="noopener"), cls="muted")


def page_view(conn, session: S.GameSession | None, pw: str, error: str | None = None, show_video: bool = False,
              detail: str | None = None):
    parts = [admin_tabs("falacias"), H1(T.AF_TITLE)]
    if error:
        parts.append(Div(error_text(error, detail), cls="error", role="alert"))
    if session is None:
        parts += [P(T.AF_NO_SESSION, cls="muted"), start_form(pw, show_video)]
        return (Title(T.AF_TITLE), Main(*parts, cls="wide"))
    token = A_.csrf_token(pw, session.id)
    active = session.status == "active"
    parts.append(Div(Span(T.ADMIN_ACTIVE if active else T.ADMIN_ENDED, cls="pill on" if active else "pill"),
                     Span(f"{T.ADMIN_STARTED}: {timefmt.dmy_hms(session.created_at)}", cls="muted"),
                     Span(f"{T.ADMIN_PLAYERS}: {S.count_players(conn, session.id)}", cls="muted"), cls="adminbar"))
    parts.append(source_line(conn, session))
    if not active:
        winner = next((r for r in F.leaderboard(conn, session.id) if r.user_id == session.winner_user_id), None)
        parts.append(P(f"{T.ADMIN_WINNER}: {winner.name} ({F.fmt_points(winner.points)})", cls="winner") if winner
                     else P(T.FAL_NO_POINTS, cls="muted"))
    if active:
        parts.append(Form(Input(type="hidden", name="session_id", value=str(session.id)), Input(type="hidden", name="csrf", value=token),
                          Button(T.AF_FINISH, type="submit", cls="primary danger"), method="post", action="/admin/falacias/finish",
                          onsubmit=f"return confirm({T.AF_FINISH_CONFIRM!r})"))
        items = F.queue_items(conn, session.id)
        after = max((i.created_us for i in items), default=0)
        parts.append(Div(H2(T.AF_QUEUE, " ", Span(T.AF_PENDING, cls="muted"), " ", pending_span(F.pending_count(conn, session.id))),
                         Ol(*[item_view(i, token) for i in items], id="queue", cls="queue"),
                         P(T.AF_QUEUE_EMPTY, cls="muted empty"), poller(after), cls="panel"))
    else:
        parts.append(start_form(pw, show_video))
    parts.append(side_view(conn, session, token))
    return (Title(T.AF_TITLE), Main(*parts, cls="wide"))


# ------------------------------------------------------------------ routes
def register(rt, pool, live: livestream.LiveStream | None = None):
    live = live or livestream.LiveStream()

    def pw() -> str:
        return A_.admin_password() or ""

    def show_video() -> bool:
        info = live.info()
        return not (info is not None and info.horizontal)

    def fragment(*parts, headers: dict | None = None) -> Response:
        return Response(to_xml(parts), media_type="text/html", headers=headers or {})

    @rt("/admin/falacias", methods=["GET"])
    def af_page(request: Request):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            return page_view(conn, A_.current_session(conn, "falacias"), pw(), show_video=show_video())

    @rt("/admin/falacias/start", methods=["POST"])
    def af_start(request: Request, csrf: str = "", video: str = ""):
        if (d := denied(request)) is not None:
            return d
        if not A_.start_ok(pw(), csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            try:
                if not live.known:
                    live.refresh()
                feed.start_falacias_as_admin(conn, feed.choose_source(conn, live.info(), video))
            except S.SessionAlreadyActive:
                pass
            except feed.FeedError as e:
                return page_view(conn, A_.current_session(conn, "falacias"), pw(), error=e.code, show_video=show_video(),
                                 detail=getattr(e, "video_id", None))
        return RedirectResponse("/admin/falacias", status_code=303)

    @rt("/admin/falacias/finish", methods=["POST"])
    def af_finish(request: Request, session_id: str = "", csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            return PlainTextResponse("bad session id", status_code=400)
        if not A_.csrf_ok(pw(), sid, csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            F.finish_session(conn, sid)             # None if already ended: harmless double click
        return RedirectResponse("/admin/falacias", status_code=303)

    @rt("/admin/falacias/queue", methods=["GET"])
    def af_queue(request: Request, after: int = 0):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            session = A_.current_session(conn, "falacias")
            if session is None or session.status != "active":
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            items = F.queue_items(conn, session.id, max(after, 0))
            if not items:
                return Response(status_code=204)
            token = A_.csrf_token(pw(), session.id)
            return fragment(poller(items[-1].created_us),
                            Div(*[item_view(i, token) for i in items], hx_swap_oob="beforeend:#queue"),
                            pending_span(F.pending_count(conn, session.id)))

    @rt("/admin/falacias/review", methods=["POST"])
    def af_review(request: Request, session_id: str = "", mark_id: str = "", verdict: str = "", group: str = "0",
                  csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        try:
            sid, mid = uuid.UUID(session_id), uuid.UUID(mark_id)
        except ValueError:
            return PlainTextResponse("bad request", status_code=400)
        if verdict not in F.VERDICTS:
            return PlainTextResponse("bad verdict", status_code=400)
        if not A_.csrf_ok(pw(), sid, csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        htmx = request.headers.get("hx-request") == "true"
        with pool.connection() as conn:
            try:
                twins = F.identical_pending_ids(conn, sid, mid) if group == "1" else []
                if group == "1":
                    F.review_same_pairs(conn, sid, mid, verdict)
                else:
                    F.review_mark(conn, sid, mid, verdict)
            except F.SessionNotActive:
                return Response("", status_code=200, headers={"HX-Redirect": "/admin/falacias"}) if htmx else \
                    RedirectResponse("/admin/falacias", status_code=303)
            except F.MarkNotFound:
                return PlainTextResponse("no such pair", status_code=404)
            if not htmx:
                return RedirectResponse("/admin/falacias", status_code=303)
            token = A_.csrf_token(pw(), sid)
            [mine] = F.queue_items(conn, sid, mark_id=mid)
            others = [it for t in twins if t != mid for it in F.queue_items(conn, sid, mark_id=t)]
            return fragment(item_view(mine, token), *[item_view(o, token, oob=True) for o in others],
                            pending_span(F.pending_count(conn, sid)))

    @rt("/admin/falacias/side", methods=["GET"])
    def af_side(request: Request):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            session = A_.current_session(conn, "falacias")
            if session is None:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            return side_view(conn, session, A_.csrf_token(pw(), session.id))

    @rt("/admin/falacias/message", methods=["POST"])
    def af_message(request: Request, session_id: str = "", message_id: str = "", status: str = "", csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        try:
            sid, mid = uuid.UUID(session_id), uuid.UUID(message_id)
        except ValueError:
            return PlainTextResponse("bad request", status_code=400)
        if status not in H.STATUSES:
            return PlainTextResponse("bad status", status_code=400)
        if not A_.csrf_ok(pw(), sid, csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            try:
                H.handle(conn, sid, mid, status)
            except H.MessageNotFound:
                return PlainTextResponse("no such message", status_code=404)
            session = A_.current_session(conn, "falacias")
            if session is None or session.id != sid:
                return Response("", status_code=200, headers={"HX-Redirect": "/admin/falacias"})
            return side_view(conn, session, A_.csrf_token(pw(), sid))
