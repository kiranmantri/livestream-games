"""Admin page: GET /admin (users + cards of the current session), POST /admin/finish."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from fasthtml.common import (
    H1,
    H2,
    A,
    Button,
    Details,
    Dialog,
    Div,
    Form,
    Header,
    Input,
    Label,
    Li,
    Main,
    Nav,
    Option,
    P,
    Script,
    Select,
    Small,
    Span,
    Summary,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Time,
    Title,
    Tr,
    Ul,
    to_xml,
)
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from app import admin as A_
from app import cards as C
from app import feed, globalsession, livestream, replaycollector, timefmt
from app import marks as M
from app import replays as R
from app import scoring as G
from app import sessions as S
from app import swaps as W
from app import texts as T
from app.lines import complete_lines, is_full, line_boxes
from app.ui import client_ip_of

_ts = timefmt.dmy_hms


def _cell_form(action: str, session_id: uuid.UUID, user_id: uuid.UUID, cell: int, token: str, label: str,
               confirm: str | None = None):
    return Form(Input(type="hidden", name="session_id", value=str(session_id)),
                Input(type="hidden", name="user_id", value=str(user_id)),
                Input(type="hidden", name="cell", value=str(cell)),
                Input(type="hidden", name="csrf", value=token),
                Button(label, type="submit", cls="mcbtn", title=label),
                # htmx: swap only THIS player's card (+ the users table, out of band); no page reload, no scroll jump.
                # Without htmx it is still a plain POST + redirect.
                method="post", action=action, cls="mcform", hx_post=action, hx_target=f"#card-{user_id}",
                hx_swap="outerHTML", hx_confirm=confirm)


def star(row: A_.PlayerRow):
    """Yellow star next to the player's name once they completed any row, column or diagonal (or the whole card)."""
    found = complete_lines(row.cells, row.marked)
    if not found:
        return None
    full = is_full(row.cells, row.marked)
    tip = f"{len(found)} {T.ADMIN_STAR_LINE if len(found) == 1 else T.ADMIN_STAR_LINES}" + (f", {T.ADMIN_STAR_FULL}" if full else "")
    return Span("★", cls="star" + (" full" if full else ""), title=tip, aria_label=tip, role="img")


def mini_card(row: A_.PlayerRow, session_id: uuid.UUID | None = None, token: str = "", active: bool = False):
    """The player's card for the admin. While the game is ACTIVE every marked cell has an unmark button (illegal
    marking) and every blocked cell a button to lift the block."""
    cells = []
    for i, cell in enumerate(row.cells):
        if "phrase_id" not in cell:
            cells.append(Div("★", cls="mc free", title=T.AVATAR_ALT))
            continue
        on = i in row.marked
        blocked = i in row.revoked
        ctl = None
        if active and session_id is not None:
            if on:
                ctl = _cell_form("/admin/unmark", session_id, row.user_id, i, token, "✕ " + T.ADMIN_UNMARK,
                                 T.ADMIN_UNMARK_CONFIRM)
            elif blocked:
                ctl = _cell_form("/admin/unblock", session_id, row.user_id, i, token, T.ADMIN_UNBLOCK)
        ok = on and i in row.confirmed
        cells.append(Div(cell["text"], Small(("🔒 " if ok else "") + timefmt.hms(row.marked_at[i]), cls="at",
                                             title=T.ADMIN_MARK_CONFIRMED if ok else None) if on else None,
                         Small(T.ADMIN_BLOCKED, cls="at") if blocked else None, ctl,
                         cls="mc" + (" on" if on else "") + (" locked" if ok else "") + (" revoked" if blocked else "")))
    return Div(*cells, *line_boxes(row.cells, row.marked), cls="mini", lang="pt-BR",
               style=f"--cols: {C.grid_of(len(row.cells))}")


# ------------------------------------------------------------------ users table: sortable columns
SORTS = {   # column -> (header text, key function(row, rank), direction used on the first click)
    "rank": (T.ADMIN_COL_RANK, lambda r, rank: rank.get(r.user_id, 10 ** 9), "asc"),
    "name": (T.ADMIN_COL_NAME, lambda r, rank: r.name.casefold(), "asc"),
    "marked": (T.ADMIN_COL_MARKED, lambda r, rank: len(r.marked), "desc"),
    "joined": (T.ADMIN_COL_JOINED, lambda r, rank: r.joined_at, "asc"),
    "seen": (T.ADMIN_COL_SEEN, lambda r, rank: r.last_seen_at, "desc"),
}


def sort_rows(rows: list[A_.PlayerRow], rank: dict, sort: str, direction: str) -> tuple[list[A_.PlayerRow], str, str]:
    """Rows ordered by a whitelisted column (unknown values fall back to rank, ascending). -> (rows, sort, dir)."""
    if sort not in SORTS:
        sort, direction = "rank", "asc"
    if direction not in ("asc", "desc"):
        direction = SORTS[sort][2]
    key = SORTS[sort][1]
    return sorted(rows, key=lambda r: (key(r, rank), r.name.casefold()), reverse=direction == "desc"), sort, direction


def sort_header(col: str, sort: str, direction: str):
    label = SORTS[col][0]
    if col == sort:
        nxt = "desc" if direction == "asc" else "asc"
        return Th(A(f"{label} {'▲' if direction == 'asc' else '▼'}", href=f"/admin?sort={col}&dir={nxt}",
                    cls="sortlink on"), aria_sort="ascending" if direction == "asc" else "descending")
    return Th(A(label, href=f"/admin?sort={col}&dir={SORTS[col][2]}", cls="sortlink"))


def title_row(title: str, action=None):
    """Page title on the left, the page's main action (start a new game) on the right, on the same line."""
    return Div(H1(title), action, cls="titlerow")


def start_form(start_token: str):
    return Form(Input(type="hidden", name="csrf", value=start_token),
                Button(T.ADMIN_START, type="submit", cls="primary"),
                method="post", action="/admin/start", cls="startform")


def ip_limit_form(limit: int, token: str, bad: bool = False):
    return Form(
        Label(T.ADMIN_IPLIMIT_LABEL, Input(type="number", name="limit", value=str(limit), min="1", max=str(S.IP_LIMIT_MAX),
                                           required=True, inputmode="numeric")),
        Input(type="hidden", name="csrf", value=token),
        Button(T.ADMIN_IPLIMIT_SAVE, type="submit"),
        Small(T.ADMIN_IPLIMIT_HELP, cls="muted"),
        P(T.ADMIN_IPLIMIT_BAD, cls="error", role="alert") if bad else None,
        method="post", action="/admin/settings/ip-limit", cls="iplimit")


def kick_form(r: A_.PlayerRow, session_id: uuid.UUID, token: str):
    """Kick the player out of the running game (card annulled; they may join again with a new card). A plain post + reload."""
    return Form(Input(type="hidden", name="session_id", value=str(session_id)),
                Input(type="hidden", name="user_id", value=str(r.user_id)),
                Input(type="hidden", name="csrf", value=token),
                Button(T.ADMIN_KICK, type="submit", cls="danger kickbtn"),
                method="post", action="/admin/kick", cls="kickform",
                onsubmit=f"return confirm({T.ADMIN_KICK_CONFIRM.format(name=r.name)!r})")


def users_panel(rows: list[A_.PlayerRow], rank: dict, sort: str = "rank", direction: str = "asc", oob: bool = False,
                poll: bool = False, session_id: uuid.UUID | None = None, token: str = ""):
    """The users table. While the game is active it re-reads itself every 5 s (keeping the sort order) and each row has
    the kick button."""
    table_rows, sort, direction = sort_rows(rows, rank, sort, direction)
    kick = poll and session_id is not None
    table = Table(
        Thead(Tr(*[sort_header(c, sort, direction) for c in SORTS], Th("") if kick else None)),
        Tbody(*[Tr(Td(str(rank.get(r.user_id, "-")), data_label=SORTS["rank"][0]), Td(r.name, star(r), data_label=SORTS["name"][0]),
                   Td(str(len(r.marked)), data_label=SORTS["marked"][0]), Td(_ts(r.joined_at), data_label=SORTS["joined"][0]),
                   Td(_ts(r.last_seen_at), data_label=SORTS["seen"][0]),
                   Td(kick_form(r, session_id, token), cls="kickcell") if kick and session_id is not None else None)
                for r in table_rows]),
        cls="users stack")
    poll_attrs = {"hx_get": f"/admin/live?sort={sort}&dir={direction}", "hx_trigger": "every 5s", "hx_swap": "outerHTML"} if poll else {}
    # (a kick's confirm() blocks the page, so a poll cannot swap the table under the admin's answer)
    return Div(H2(T.ADMIN_USERS), table if rows else P(T.ADMIN_NO_USERS, cls="muted"), cls="panel", id="users-panel",
               hx_swap_oob="outerHTML" if oob else None, **poll_attrs)


def card_panel(r: A_.PlayerRow, session_id: uuid.UUID, token: str, active: bool, open_: bool = False):
    return Details(Summary(f"{r.name} - {len(r.marked)}/{C.max_marks(r.cells)}", star(r)),
                   mini_card(r, session_id, token, active), cls="panel", open=open_ or None, id=f"card-{r.user_id}")


LIVE_POLL = "every 5s"


def cards_signature(session: S.GameSession, rows: list[A_.PlayerRow]) -> str:
    """Changes when anything shown on the cards changes (a mark, an admin block, a new player); NOT on last_seen_at."""
    raw = repr((session.id, session.status, [(r.user_id, r.name, sorted(r.marked), sorted(r.revoked)) for r in rows]))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def cards_panel(session: S.GameSession, rows: list[A_.PlayerRow], token: str, open_uid: str = ""):
    """The players' cards. While the game is active it re-reads itself every 5 s: 204 while the signature is the same;
    admin.js keeps the open cards open across the swap (and skips a swap while an unmark/unblock is on its way)."""
    active = session.status == "active"
    poll = {"hx_get": f"/admin/cards?sid={session.id}&sig={cards_signature(session, rows)}", "hx_trigger": LIVE_POLL,
            "hx_swap": "outerHTML"} if active else {}
    return Div(H2(T.ADMIN_CARDS), *[card_panel(r, session.id, token, active, str(r.user_id) == open_uid) for r in rows],
               id="cards-panel", **poll)


def _mark_form(action: str, session_id: uuid.UUID, pm: M.PhraseMarks, token: str, label: str, cls: str):
    """Correto / Incorreto act on the PHRASE (every player's mark of it); the form names one marked cell of it."""
    m = pm.who[0]
    return Form(Input(type="hidden", name="session_id", value=str(session_id)),
                Input(type="hidden", name="user_id", value=str(m.user_id)),
                Input(type="hidden", name="cell", value=str(m.cell_index)),
                Input(type="hidden", name="csrf", value=token),
                Button(label, type="submit", cls=cls),
                method="post", action=action, cls="mkform", hx_post=action, hx_target="#marks-panel", hx_swap="outerHTML")


def marks_signature(session: S.GameSession, marks: list[M.PhraseMarks], keys: frozenset[int] = frozenset()) -> str:
    raw = repr((session.id, session.status, sorted(keys),
                [(p.phrase_id, p.confirmed, [(w.user_id, w.marked_at) for w in p.who]) for p in marks]))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def marks_panel(session: S.GameSession, marks: list[M.PhraseMarks], token: str, keys: frozenset[int] = frozenset()):
    """Every marked phrase of the game, one row per phrase with everybody who marked it (pending first, then newest).
    Correto confirms the phrase for ALL players (their cells lock, later marks of it are confirmed at once); Incorreto clears
    it for all (they may mark it again later). While the game is active it re-reads itself every 5 s: 204 while nothing changed."""
    active = session.status == "active"
    pending = sum(1 for p in marks if not p.confirmed)
    poll = {"hx_get": f"/admin/marks?sid={session.id}&sig={marks_signature(session, marks, keys)}", "hx_trigger": LIVE_POLL,
            "hx_swap": "outerHTML"} if active else {}
    rows = []
    for p in marks:
        ok = p.confirmed
        acts = (None if ok else _mark_form("/admin/confirm", session.id, p, token, "✔ " + T.ADMIN_MARK_OK, "okbtn"),
                _mark_form("/admin/reject", session.id, p, token, "✕ " + T.ADMIN_MARK_WRONG, "wrongbtn")) if active else ()
        who = Ul(*[Li(w.name, " ", Small(timefmt.hms(w.marked_at), cls="muted")) for w in p.who], cls="mkwho")
        key = Span("🔑", title=T.ADMIN_KW, cls="kwmark") if p.phrase_id in keys else None     # only the admin sees it
        rows.append(Tr(Td(key, p.text, cls="phrase", data_label=T.ADMIN_COL_PHRASE, lang="pt-BR"),
                       Td(who, data_label=T.ADMIN_COL_PLAYERS),
                       Td(Span(("🔒 " + T.ADMIN_MARK_CONFIRMED) if ok else T.ADMIN_MARK_PENDING, cls="mkstate" + (" ok" if ok else "")),
                          data_label=T.ADMIN_COL_STATUS),
                       Td(*acts, cls="mkacts"), cls="mk" + (" ok" if ok else " pending")))
    table = Table(Thead(Tr(Th(T.ADMIN_COL_PHRASE), Th(T.ADMIN_COL_PLAYERS), Th(T.ADMIN_COL_STATUS), Th(""))),
                  Tbody(*rows), cls="users stack marks")
    return Div(H2(T.ADMIN_MARKS, Span(T.ADMIN_MARKS_PENDING.format(n=pending), cls="pill" + (" on" if pending else ""))
                  if marks else None),
               P(T.ADMIN_MARKS_HELP, cls="muted") if active else None,
               table if marks else P(T.ADMIN_MARKS_NONE, cls="muted"),
               cls="panel", id="marks-panel", **poll)


def keyword_ids(conn, session_id: uuid.UUID) -> frozenset[int]:
    return frozenset(k.phrase_id for k in W.keywords(conn, session_id))


def _kw_form(action: str, session_id: uuid.UUID, token: str, *fields):
    return Form(Input(type="hidden", name="session_id", value=str(session_id)), Input(type="hidden", name="csrf", value=token),
                *fields, method="post", action=action, cls="kwform")


def keyword_panel(session: S.GameSession, kws: list[W.Keyword], options: list[W.KeywordOption], log: list[W.RightLog],
                  token: str, error: str | None = None, oob: bool = False):
    """The secret keywords (only the admin sees them) and every card swap of the game. Plain forms: the page reloads."""
    active = session.status == "active"
    chosen = {k.phrase_id for k in kws}
    items = [Li(Span("🔑 ", aria_hidden="true"), Span(k.text, cls="phrase", lang="pt-BR"), " ",
                Small(T.ADMIN_KW_FROZEN, cls="muted") if k.frozen else
                (_kw_form("/admin/keywords/remove", session.id, token, Input(type="hidden", name="phrase_id", value=str(k.phrase_id)),
                          Button(T.ADMIN_KW_REMOVE, type="submit", cls="link")) if active else None))
             for k in kws]
    add = _kw_form("/admin/keywords/add", session.id, token,
                   Label(T.ADMIN_KW_PICK, Select(*[Option(f"{o.text} — {T.ADMIN_KW_CARDS.format(n=o.cards)}", value=str(o.phrase_id))
                                                  for o in options if o.phrase_id not in chosen], name="phrase_id")),
                   Button(T.ADMIN_KW_ADD, type="submit")) if active and len(kws) < W.MAX_KEYWORDS else None

    def fate(r: W.RightLog):
        if r.used_at:
            return T.ADMIN_KW_USED.format(target=r.target or "?", at=timefmt.hms(r.used_at))
        return T.ADMIN_KW_DECLINED if r.declined_at else T.ADMIN_KW_OPEN

    swaps = [Li(T.ADMIN_KW_EARNED.format(holder=r.holder, text=r.text), " ", Small(timefmt.hms(r.earned_at), cls="muted"),
                " — ", fate(r)) for r in log]
    return Div(H2(T.ADMIN_KW), P(T.ADMIN_KW_HELP, cls="muted") if active else None,
               Div(error, cls="error", role="alert") if error else None,
               Ul(*items, cls="kwlist") if kws else P(T.ADMIN_KW_NONE, cls="muted"), add,
               Div(H2(T.ADMIN_KW_LOG), Ul(*swaps, cls="kwlog")) if log else None,
               cls="panel", id="kw-panel", **({"hx_swap_oob": "true"} if oob else {}))


def watcher(session: S.GameSession | None):
    """No game running (none yet, or the last one ended): reload the page as soon as a new Bingo game starts (a player can start it)."""
    return Div(id="bingo-watch", hx_get=f"/admin/watch?sid={session.id if session else ''}", hx_trigger=LIVE_POLL, hx_swap="none")


def admin_page(session: S.GameSession, rows: list[A_.PlayerRow], standings: list[G.Standing],
               token: str, start_token: str, sort: str = "rank", direction: str = "asc", open_uid: str = "",
               settings=None, marks: list[M.PhraseMarks] = (), kw=None, keys: frozenset[int] = frozenset()):
    rank = {s.user_id: s.rank for s in standings}
    active = session.status == "active"
    winner = next((r for r in rows if r.user_id == session.winner_user_id), None)
    bar = status_bar(session, len(rows), None, winner.name if winner else None)
    return (Title(f"{T.TITLE} - admin"), admin_tabs("bingo"),
            Main(title_row(f"{T.TITLE} - admin"), bar,
                 users_panel(rows, rank, sort, direction, poll=active, session_id=session.id, token=token),
                 kw, marks_panel(session, list(marks), token, keys),
                 cards_panel(session, rows, token, open_uid), None if active else watcher(session),
                 settings, Script(src="/admin.js", defer=True), cls="wide"))


GAME_OF = {"bingo": "bingo", "phrases": "bingo", "falacias": "falacias", "fallacies": "falacias", "checker": "checker"}
GAME_TABS = (("bingo", T.ADMIN_TAB_BINGO, "/admin", "🎯"), ("falacias", T.ADMIN_TAB_FALACIAS, "/admin/falacias", "🔎"),
             ("checker", T.ADMIN_TAB_CHECKER, "/admin/checker", "🕵️"))
SUB_TABS = {"bingo": (("bingo", T.ADMIN_SUB_GAME, "/admin"), ("phrases", T.ADMIN_SUB_CONTENT, "/admin/phrases")),
            "falacias": (("falacias", T.ADMIN_SUB_GAME, "/admin/falacias"), ("fallacies", T.ADMIN_SUB_CONTENT, "/admin/fallacies")),
            "checker": (("checker", T.ADMIN_SUB_GAME, "/admin/checker"),)}
_pool = None


def replay_option(r):
    label = r.title or r.video_id
    if r.status == "ready":
        return Option(label, value=r.video_id)
    return Option(f"{label} — {T.AF_REPLAY_STATUS.get(r.status, r.status)}", value=r.video_id, disabled=True)


def session_dialog(conn, pw: str, error: str | None = None, notice: str | None = None, value: str = "", show: bool = False):
    """"Iniciar sessão": a replay already collected (select) OR any YouTube link. One video for the three games."""
    rows = R.list_replays(conn)
    message = bool(error or notice)
    attrs = {"open": True} if show or message else {}
    if message:
        attrs["data_autoopen"] = "1"
    return Dialog(
        Form(H2(T.ADMIN_SESSION_START),
             Div(error, cls="error", role="alert") if error else None,
             Div(notice, cls="notice", role="status") if notice else None,
             Input(type="hidden", name="csrf", value=A_.session_token(pw)),
             Label(T.AF_PICK_LABEL, Select(Option(T.AF_PICK_NONE, value=""), *[replay_option(r) for r in rows], name="replay", aria_label=T.AF_PICK_LABEL)),
             None if rows else P(T.AF_REPLAY_EMPTY, cls="muted"),
             Div(T.OR, cls="or"),
             Label(T.AF_URL_LABEL, Input(type="url", name="video", value=value, placeholder=T.AF_URL_PLACEHOLDER, maxlength="200", autocomplete="off",
                                         aria_label=T.AF_URL_LABEL)),
             Small(T.SESSION_DIALOG_HELP, cls="muted"),
             Div(Button(T.AF_URL_CANCEL, type="button", data_close="1"), Button(T.AF_URL_START, type="submit", cls="primary"), cls="dlgactions"),
             method="post", action="/admin/session/start"),
        id="startdlg", cls="startdlg", **attrs)


def session_controls(conn, pw: str):
    """Top right of every admin page: start the session, or (while one runs) its video and the end button."""
    active = globalsession.active_games(conn)
    if not active:
        return Div(A(T.ADMIN_SESSION_START, href="/admin/session", cls="btn primary", data_open="startdlg"), cls="sessionctl")
    video = globalsession.current_video(conn)
    url = f"https://www.youtube.com/watch?v={video[0]}" if video else None
    return Div(A(url.removeprefix("https://www."), href=url, target="_blank", rel="noopener", cls="sessionurl", title=url) if url else None,
               Form(Input(type="hidden", name="csrf", value=A_.session_token(pw)),
                    Button(T.ADMIN_SESSION_END, type="submit", cls="primary danger"), method="post", action="/admin/session/finish",
                    onsubmit=f"return confirm({T.ADMIN_SESSION_END_CONFIRM!r})"),
               cls="sessionctl")


def admin_tabs(active: str, dialog: bool = True):
    """Top row: brand at the far left, the three games, the session (start / video + end). Second row: Jogo | Conteúdo of the active game."""
    game = GAME_OF.get(active)
    pw = A_.admin_password() or ""
    games = [A(Span(icon, cls="ico", aria_hidden="true"), Span(Span(label, cls="lbl"), cls="txt"),
               href=href, cls="active" if key == game else None, aria_current="page" if key == game else None)
             for key, label, href, icon in GAME_TABS]
    subs = [A(Span(label, cls="lbl"), href=href, cls="active" if key == active else None, aria_current="page" if key == active else None)
            for key, label, href in SUB_TABS.get(game, ())]
    # grid column of the selected game in row 1 (column 1 = brand): the sub menu starts there (app.css "admin shell v2")
    sub_col = 2 + next((i for i, t in enumerate(GAME_TABS) if t[0] == game), 0)
    conn_ctx = _pool.connection() if _pool is not None else None
    if conn_ctx is None:
        controls, dlg = Div(cls="sessionctl"), None
    else:
        with conn_ctx as conn:
            controls = session_controls(conn, pw)
            dlg = session_dialog(conn, pw) if dialog and not globalsession.active_games(conn) else None
    return Header(Div(A(Span("🛠", cls="mark", aria_hidden="true"), Span(T.ADMIN_BRAND, cls="name"), href="/admin", cls="brand"),
                      Nav(*games, cls="menu admintabs gamenav", aria_label="admin"), controls,
                      A(T.ADMIN_VIEW_SITE, " ↗", href="/", cls="viewsite", target="_blank", rel="noopener"),
                      Nav(Div(*subs, cls="subtabs"), cls="subnav", style=f"--sub-col: {sub_col}", aria_label="admin submenu") if subs else None,
                      cls="adminrow"),
                  dlg,
                  Script(src="/startdlg.js", defer=True) if dlg is not None else None,
                  cls="appbar admin")


def status_bar(session: S.GameSession, n_players: int, finish=None, winner: str | None = None, source=None):
    """Sticky bar of an admin game page: state, start time, players, elapsed time (admin.js keeps it ticking), the end button at the right."""
    active = session.status == "active"
    since = int(session.created_at.timestamp() * 1000)
    seconds = (datetime.now(session.created_at.tzinfo) - session.created_at).total_seconds()
    return Div(
        Span(Span(cls="livedot") if active else None, T.ADMIN_ACTIVE if active else T.ADMIN_ENDED, cls="pill on" if active else "pill"),
        Span(f"{T.ADMIN_STARTED}: {_ts(session.created_at)}", cls="muted started"),
        Span(f"{T.ADMIN_ENDED_AT}: {_ts(session.ended_at)}", cls="muted") if session.ended_at else None,
        player_count(n_players),
        Span(f"{T.ADMIN_ELAPSED}: ", Time(timefmt.elapsed(seconds), cls="elapsed", data_since=str(since)), cls="muted") if active else None,
        Span(f"{T.ADMIN_WINNER}: {winner}", cls="muted") if winner else None,
        Span(cls="grow"), finish,
        Div(source, cls="srcrow") if source is not None else None,
        id="statusbar", cls="adminbar")


def player_count(n: int, oob: bool = False):
    return Span(f"{T.ADMIN_PLAYERS}: {n}", id="playercount", cls="muted", hx_swap_oob="true" if oob else None)


def denied(request: Request):
    """None when the request is authorised, else the response to send (401 / 503)."""
    pw = A_.admin_password()
    if pw is None:
        return PlainTextResponse("admin disabled: set ADMIN_PASSWORD in the environment", status_code=503)
    ip = client_ip_of(request)
    wait = A_.lock_remaining(ip)
    if wait:
        return PlainTextResponse(T.ADMIN_LOCKED, status_code=429, headers={"Retry-After": str(wait)})
    header = request.headers.get("authorization")
    if not A_.check_basic(header, pw):
        if header:                         # a request without credentials is the browser's first call, not an attack
            A_.record_failure(ip)
        return Response("authentication required", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="Live Games admin", charset="UTF-8"'})
    A_.record_success(ip)
    return None


def register(rt, pool, live: livestream.LiveStream | None = None):
    global _pool
    _pool = pool
    live = live or livestream.LiveStream()
    @rt("/admin")
    def admin_get(request: Request, sort: str = "rank", dir: str = "", open: str = "", err: str = "", kwerr: str = ""):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            session = A_.current_session(conn)
            pw = A_.admin_password() or ""
            settings = ip_limit_form(S.max_users_per_ip(conn), A_.settings_token(pw), err == "ip_limit")
            if session is None:
                return (Title(f"{T.TITLE} - admin"), admin_tabs("bingo"),
                        Main(title_row(f"{T.TITLE} - admin"), P(T.ADMIN_NO_SESSION, cls="muted"), P(T.SESSION_NONE_HINT, cls="muted"),
                            settings,
                            Div(A(T.ADMIN_PHRASES_LINK, href="/admin/phrases", cls="btn"),
                                A(T.ADMIN_REFRESH, href="/admin", cls="btn"), cls="adminbar"), watcher(None), cls="wide"))
            token = A_.csrf_token(pw, session.id)
            kws = W.keywords(conn, session.id)
            return admin_page(session, A_.players(conn, session.id), G.leaderboard(conn, session.id),
                              token, A_.start_token(pw), sort, dir, open, settings,
                              M.phrase_marks(conn, session.id),
                              keyword_panel(session, kws, W.keyword_options(conn, session.id), W.session_rights(conn, session.id),
                                            token, T.ADMIN_KW_ERRORS.get(kwerr)),
                              frozenset(k.phrase_id for k in kws))

    @rt("/admin/live")
    def admin_live(request: Request, sort: str = "rank", dir: str = ""):
        """Polled every 5 s by the users table: the table again + the player count (out of band). The cards are NOT redrawn:
        an open card would collapse under the admin's hands."""
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            session = A_.current_session(conn)
            if session is None or session.status != "active":
                return Response(status_code=200, headers={"HX-Refresh": "true"})      # the game ended: show the final page
            rows = A_.players(conn, session.id)
            rank = {s.user_id: s.rank for s in G.leaderboard(conn, session.id)}
            return (users_panel(rows, rank, sort, dir, poll=True, session_id=session.id,
                                token=A_.csrf_token(A_.admin_password() or "", session.id)), player_count(len(rows), oob=True))

    @rt("/admin/marks")
    def admin_marks(request: Request, sid: str = "", sig: str = ""):
        """Polled every 5 s by the marks list: 204 while no mark changed, else the list again; another game / ended: reload."""
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            session = A_.current_session(conn)
            if session is None or session.status != "active" or str(session.id) != sid:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            marks, keys = M.phrase_marks(conn, session.id), keyword_ids(conn, session.id)
            if sig and sig == marks_signature(session, marks, keys):
                return Response(status_code=204)
            return Response(to_xml(marks_panel(session, marks, A_.csrf_token(A_.admin_password() or "", session.id), keys)),
                            media_type="text/html")

    @rt("/admin/cards")
    def admin_cards(request: Request, sid: str = "", sig: str = ""):
        """Polled every 5 s by the cards panel: 204 while nothing on the cards changed, else the panel again.
        The game ended or another one started: reload the page."""
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            session = A_.current_session(conn)
            if session is None or session.status != "active" or str(session.id) != sid:
                return Response(status_code=200, headers={"HX-Refresh": "true"})
            rows = A_.players(conn, session.id)
            if sig and sig == cards_signature(session, rows):
                return Response(status_code=204)
            return Response(to_xml(cards_panel(session, rows, A_.csrf_token(A_.admin_password() or "", session.id))), media_type="text/html")

    @rt("/admin/watch")
    def admin_watch(request: Request, sid: str = ""):
        """Polled by a page with no running game: reload when the current Bingo game is another one, or became active."""
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            session = A_.current_session(conn)
        if session is not None and (str(session.id) != sid or session.status == "active"):
            return Response(status_code=200, headers={"HX-Refresh": "true"})
        return Response(status_code=204)

    @rt("/admin/settings/ip-limit", methods=["POST"])
    def admin_ip_limit(request: Request, limit: str = "", csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        if not A_.settings_ok(A_.admin_password() or "", csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            try:
                S.set_max_users_per_ip(conn, limit)
            except ValueError:
                return RedirectResponse("/admin?err=ip_limit", status_code=303)
        return RedirectResponse("/admin", status_code=303)

    # ---- the session: start / end the three games together
    def session_view(conn, error: str | None = None, notice: str | None = None, value: str = ""):
        pw = A_.admin_password() or ""
        return (Title(T.ADMIN_SESSION_START), admin_tabs("", dialog=False),
                Main(title_row(T.ADMIN_SESSION_START), session_dialog(conn, pw, error, notice, value, show=True), Script(src="/startdlg.js", defer=True),
                     cls="wide"))

    @rt("/admin/session", methods=["GET"])
    def session_page(request: Request):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            if len(globalsession.active_games(conn)) == len(globalsession.GAMES):
                return RedirectResponse("/admin", status_code=303)
            return session_view(conn)

    @rt("/admin/session/start", methods=["POST"])
    def session_start(request: Request, csrf: str = "", video: str = "", replay: str = ""):
        if (d := denied(request)) is not None:
            return d
        if not A_.session_ok(A_.admin_password() or "", csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        shown = video.strip()[:200]
        with pool.connection() as conn:
            if len(globalsession.active_games(conn)) == len(globalsession.GAMES):
                return RedirectResponse("/admin", status_code=303)           # a double click or a stale page
            try:
                if shown:                                                      # a typed link wins over the select
                    source = feed.source_from_url(conn, shown, live.probe)     # may wait ~5-15 s for YouTube
                elif replay.strip():                                           # a collected replay: nothing to ask YouTube
                    source = feed.Source("replay", feed.parse_video_id(replay))
                    feed.check_video_ready(conn, source.video_id)
                else:
                    return session_view(conn, error=T.AF_URL_ERRORS["empty"])
                globalsession.start_all(conn, source)
            except feed.UrlRefused as e:
                if e.reason != "not_imported":
                    return session_view(conn, error=T.AF_URL_ERRORS[e.reason], value=shown)
                try:                                                           # a finished video: collect its replay (the cache), start later
                    R.submit(conn, shown)
                except R.ReplayError as re_:
                    if re_.code == "already_running":
                        return session_view(conn, notice=T.AF_URL_COLLECTING, value=shown)
                    return session_view(conn, error=T.AF_REPLAY_ERRORS.get(re_.code, T.AF_REPLAY_ERRORS["generic"]), value=shown)
                replaycollector.wake()
                return session_view(conn, notice=T.AF_URL_QUEUED, value=shown)
            except feed.FeedUnavailable:                                       # a picked replay that is not ready (forged post)
                return session_view(conn, error=T.AF_REPLAY_ERRORS.get("not_ready", T.AF_REPLAY_ERRORS["generic"]), value=shown)
            except feed.InvalidVideo:
                return session_view(conn, error=T.AF_URL_ERRORS["invalid"], value=shown)
            except feed.FeedError:
                return session_view(conn, error=T.ERRORS["generic"], value=shown)
        return RedirectResponse("/admin", status_code=303)

    @rt("/admin/session/finish", methods=["POST"])
    def session_finish(request: Request, csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        if not A_.session_ok(A_.admin_password() or "", csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            globalsession.finish_all(conn)               # nothing running: harmless double click
        return RedirectResponse("/admin", status_code=303)

    @rt("/admin/start", methods=["POST"])
    def admin_start(request: Request, csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        if not A_.start_ok(A_.admin_password() or "", csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            try:
                S.start_session_as_admin(conn)
            except S.SessionAlreadyActive:
                pass                       # already running (double click / a player was faster): fine
        return RedirectResponse("/admin", status_code=303)

    @rt("/admin/finish", methods=["POST"])
    def admin_finish(request: Request, session_id: str = "", csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            return PlainTextResponse("bad session id", status_code=400)
        if not A_.csrf_ok(A_.admin_password() or "", sid, csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            G.finish_session(conn, sid)        # None if already finished: harmless double click
        return RedirectResponse("/admin", status_code=303)

    def _cell_action(request: Request, session_id: str, user_id: str, cell: str, csrf: str, action, marks_reply: bool = False):
        if (d := denied(request)) is not None:
            return d
        try:
            sid, uid, idx = uuid.UUID(session_id), uuid.UUID(user_id), int(cell)
        except ValueError:
            return PlainTextResponse("bad request", status_code=400)
        if not A_.csrf_ok(A_.admin_password() or "", sid, csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            action(conn, sid, uid, idx)        # False = nothing to do (game over / not marked): harmless
            if request.headers.get("hx-request") != "true":
                return RedirectResponse(f"/admin?open={uid}", status_code=303)
            # htmx: send back just this player's card plus the users table (marks, rank and star changed)
            session = A_.current_session(conn)
            rows = A_.players(conn, session.id) if session else []
            row = next((r for r in rows if r.user_id == uid), None)
            if session is None or session.id != sid or (row is None and not marks_reply):
                return Response("", status_code=200, headers={"HX-Redirect": "/admin"})
            q = parse_qs(urlparse(request.headers.get("hx-current-url", "")).query)      # keep the table's sort order
            rank = {s.user_id: s.rank for s in G.leaderboard(conn, session.id)}
            pw = A_.admin_password() or ""
            token = A_.csrf_token(pw, session.id)
            if marks_reply:          # the review list: the list again + the users table (counts changed), out of band
                kws = W.keywords(conn, session.id)          # a confirmation may freeze a keyword and grant swaps
                return (marks_panel(session, M.phrase_marks(conn, session.id), token, frozenset(k.phrase_id for k in kws)),
                        keyword_panel(session, kws, W.keyword_options(conn, session.id), W.session_rights(conn, session.id),
                                      token, oob=True),
                        users_panel(rows, rank, q.get("sort", ["rank"])[0], q.get("dir", [""])[0], oob=True,
                                    poll=session.status == "active", session_id=session.id, token=token))
            return (card_panel(row, session.id, token, session.status == "active", True),
                    users_panel(rows, rank, q.get("sort", ["rank"])[0], q.get("dir", [""])[0], oob=True,
                              poll=session.status == "active", session_id=session.id, token=token))

    @rt("/admin/unmark", methods=["POST"])
    def admin_unmark(request: Request, session_id: str = "", user_id: str = "", cell: str = "", csrf: str = ""):
        return _cell_action(request, session_id, user_id, cell, csrf, M.revoke_mark)

    @rt("/admin/unblock", methods=["POST"])
    def admin_unblock(request: Request, session_id: str = "", user_id: str = "", cell: str = "", csrf: str = ""):
        return _cell_action(request, session_id, user_id, cell, csrf, M.lift_revoke)


    @rt("/admin/confirm", methods=["POST"])
    def admin_confirm(request: Request, session_id: str = "", user_id: str = "", cell: str = "", csrf: str = ""):
        return _cell_action(request, session_id, user_id, cell, csrf, M.confirm_mark, marks_reply=True)

    @rt("/admin/reject", methods=["POST"])
    def admin_reject(request: Request, session_id: str = "", user_id: str = "", cell: str = "", csrf: str = ""):
        return _cell_action(request, session_id, user_id, cell, csrf, M.reject_mark, marks_reply=True)

    def _keyword_action(request: Request, session_id: str, phrase_id: str, csrf: str, action):
        if (d := denied(request)) is not None:
            return d
        try:
            sid, pid = uuid.UUID(session_id), int(phrase_id)
        except ValueError:
            return PlainTextResponse("bad request", status_code=400)
        if not A_.csrf_ok(A_.admin_password() or "", sid, csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            try:
                action(conn, sid, pid)
            except W.KeywordError as e:
                return RedirectResponse(f"/admin?kwerr={e.code}#kw-panel", status_code=303)
        return RedirectResponse("/admin#kw-panel", status_code=303)

    @rt("/admin/keywords/add", methods=["POST"])
    def admin_keyword_add(request: Request, session_id: str = "", phrase_id: str = "", csrf: str = ""):
        return _keyword_action(request, session_id, phrase_id, csrf, W.add_keyword)

    @rt("/admin/keywords/remove", methods=["POST"])
    def admin_keyword_remove(request: Request, session_id: str = "", phrase_id: str = "", csrf: str = ""):
        return _keyword_action(request, session_id, phrase_id, csrf, W.remove_keyword)

    @rt("/admin/kick", methods=["POST"])
    def admin_kick(request: Request, session_id: str = "", user_id: str = "", csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        try:
            sid, uid = uuid.UUID(session_id), uuid.UUID(user_id)
        except ValueError:
            return PlainTextResponse("bad request", status_code=400)
        if not A_.csrf_ok(A_.admin_password() or "", sid, csrf):
            return PlainTextResponse("bad csrf token", status_code=403)
        with pool.connection() as conn:
            S.kick_player(conn, sid, uid)          # False = already gone / game over: harmless double click
        return RedirectResponse("/admin", status_code=303)
