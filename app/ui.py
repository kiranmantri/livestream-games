"""Page chrome shared by both games: the menu between games, the page shell, the name form, cookies, client IP."""
from __future__ import annotations

import os
from contextvars import ContextVar
from http.cookies import SimpleCookie
from urllib.parse import quote, unquote, urlencode

from fasthtml.common import (
    H1,
    H2,
    A,
    B,
    Button,
    Details,
    Div,
    Form,
    Header,
    Input,
    Label,
    Main,
    Nav,
    P,
    Script,
    Small,
    Span,
    Summary,
    Title,
)
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app import sessions as S
from app import texts as T

COOKIE_MAX_AGE = 60 * 60 * 48
NAME_COOKIE = "lg_name"          # the nickname, asked once for both games (NOT an identity: only pre-fills joins)
TAB_PATHS = ("/", "/falacias")
MENU = (("bingo", T.MENU_BINGO, "/", "🎯", T.MENU_BINGO_SUB), ("falacias", T.MENU_FALACIAS, "/falacias", "🔎", T.MENU_FALACIAS_SUB))
BRAND_ICON = "🎲"


def menu(current: str, running: frozenset[str] = frozenset(), first_load: bool = True):
    """Links between the games. Rendered without state first; the fragment from /menu adds a dot to games in progress."""
    links = [A(Span(icon, cls="ico", aria_hidden="true"),
               Span(Span(label, cls="lbl"), Small(sub, cls="sub"), cls="txt"),
               Span(cls="dot", title=T.MENU_RUNNING, role="img", aria_label=T.MENU_RUNNING) if key in running else None,
               href=href, cls="active" if key == current else None, aria_current="page" if key == current else None)
             for key, label, href, icon, sub in MENU]
    return Nav(*links, id="menu", cls="menu", hx_get=f"/menu?cur={current}", hx_trigger="load, every 30s" if first_load else "every 30s",
               hx_swap="outerHTML", aria_label=T.MENU_LABEL)


_cookies: ContextVar[dict[str, str] | None] = ContextVar("lg_cookies", default=None)


class CookieContext:
    """Pure ASGI middleware: remembers the request's cookies for the page builders (the header shows the personal link on every
    page without each handler passing the request down)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            raw = dict(scope.get("headers") or []).get(b"cookie", b"").decode("latin-1")
            _cookies.set({k: m.value for k, m in SimpleCookie(raw).items()})
        await self.app(scope, receive, send)


def personal_link(cookies: dict[str, str] | None = None) -> str | None:
    """ONE private link for every game the player is in: it carries each game's token, so opening it elsewhere restores them all.
    Whoever holds the link IS the player. None when the visitor has no game cookie."""
    cookies = (_cookies.get() or {}) if cookies is None else cookies
    parts = {key: cookies[S.cookie_name(game)] for key, game in (("b", "bingo"), ("f", "falacias")) if cookies.get(S.cookie_name(game))}
    return "/r?" + urlencode(parts) if parts else None


def usermenu(who: str, current: str):
    """The player's name as a small avatar button; opens a popover with the way to change it (no JS: <details>). To its left, the
    icon with the personal link (only when the player has joined a game)."""
    path = TAB_PATHS[1] if current == "falacias" else TAB_PATHS[0]
    link = personal_link()
    return Div(A("🔗", href=link, cls="mylink", title=T.MYLINK_TITLE, aria_label=T.MYLINK_TITLE,
                    data_copied=T.MYLINK_COPIED, data_failed=T.MYLINK_FAILED) if link else None,
               Script(src="/copylink.js", defer=True) if link else None, _usermenu(who, path), cls="userbox")


def _usermenu(who: str, path: str):
    return Details(Summary(Span(who[:1].upper(), cls="av", aria_hidden="true"), Span(who, cls="nm"), title=who),
                   Div(A(T.USER_CHANGE, href=f"/name?next={path}"), cls="pop"), cls="usermenu")


def appbar(current: str, who: str | None):
    """One bar for everything that navigates: brand (home), the two game tabs, and who I am. On phones the tabs move to the
    bottom of the screen (CSS), where thumbs reach them."""
    return Header(A(Span(BRAND_ICON, cls="mark", aria_hidden="true"), Span(T.SITE_TITLE, cls="name"), href="/", cls="brand"),
                  menu(current), usermenu(who, current) if who else None, cls="appbar")


def page(*content, title: str = T.TITLE, cls: str | None = None, current: str = "bingo", tabs: bool = True,
         who: str | None = None):
    return Title(title), appbar(current, who) if tabs else None, Main(*content, cls=cls)


_pool = None


def error_box(code: str | None, limit: int | None = None):
    if not code:
        return None
    if limit is None:
        if _pool is not None:
            with _pool.connection() as conn:
                limit = S.max_users_per_ip(conn)
        else:
            limit = S.max_users_per_ip()
    msg = T.ERRORS.get(code, T.ERRORS["generic"]).format(n=limit)
    return Div(msg, cls="error", role="alert")


def name_of(request: Request) -> str | None:
    """The remembered nickname, or None when missing/invalid (then the name gate is shown)."""
    raw = request.cookies.get(NAME_COOKIE)
    raw = unquote(raw) if raw else raw          # stored percent-encoded: header values must be latin-1 (emoji/Cyrillic gave a 500)
    try:
        return S.normalize_name(raw) if raw else None
    except S.InvalidName:
        return None


def safe_next(value: str | None) -> str:
    return value if value in TAB_PATHS else "/"


def name_gate(next_path: str = "/", error: str | None = None, value: str = ""):
    """The FIRST thing a visitor sees: ask for a nickname. No tabs yet."""
    return page(Div(
        Div(BRAND_ICON, cls="mark", aria_hidden="true"), H1(T.SITE_TITLE), H2(T.NAME_HEADING), P(T.NAME_HELP, cls="tag"), error_box(error),
        Div(Form(Label(T.NAME_LABEL, fr="name"),
                 Input(type="text", id="name", name="name", value=value, maxlength=S.MAX_NAME_LEN,
                       required=True, autocomplete="nickname", autofocus=True, onfocus="this.select()"),
                 Input(type="hidden", name="next", value=safe_next(next_path)),
                 Button(T.NAME_BUTTON, type="submit", cls="primary"),
                 method="post", action="/name", cls="entry"), cls="panel"), cls="hero"),
        title=T.SITE_TITLE, tabs=False)


def play_form(action: str, button: str, name: str, next_path: str, extra=None):
    """Start/join with the remembered nickname: one button, no name field."""
    return Form(P(T.PLAYING_AS, " ", B(name), " · ", A(T.CHANGE_NAME, href=f"/name?next={next_path}"), cls="muted who"),
                extra, Button(button, type="submit", cls="primary"), method="post", action=action, cls="entry")


def name_error_gate(code: str, next_path: str, value: str):
    """Name problems (invalid / taken) go back to the name gate, with the message."""
    return name_gate(next_path, error=code, value=value)


NAME_ERRORS = ("invalid_name", "name_taken")
URL_ERRORS = ("ip_limit_reached",)       # errors that survive a redirect (post/redirect/get): only these codes are read from ?err=


def url_error(request: Request) -> str | None:
    code = request.query_params.get("err")
    return code if code in URL_ERRORS else None


def register(rt, pool) -> None:
    global _pool
    _pool = pool

    @rt("/r/{game_type}/{token}", methods=["GET"])
    def recover(request: Request, game_type: str, token: str):
        if game_type not in ("bingo", "falacias") or len(token) > 200:
            return Response(status_code=404)
        with pool.connection() as conn:
            found = S.get_player_by_token(conn, token, game_type)
        if found is None:
            return RedirectResponse(TAB_PATHS[1] if game_type == "falacias" else "/", status_code=303)
        resp = RedirectResponse("/falacias" if game_type == "falacias" else "/", status_code=303)
        return remember_name(set_token_cookie(resp, game_type, token), found.name)

    @rt("/r", methods=["GET"])
    def recover_all(b: str = "", f: str = ""):
        """The personal link: restores the cookie of every game whose token is still valid."""
        found = []
        with pool.connection() as conn:
            for game, tok in (("bingo", b), ("falacias", f)):
                player = S.get_player_by_token(conn, tok, game) if tok and len(tok) <= 200 else None
                if player:
                    found.append((game, tok, player))
        if not found:
            return RedirectResponse("/", status_code=303)
        resp = RedirectResponse("/falacias" if found[0][0] == "falacias" else "/", status_code=303)
        for game, tok, _ in found:
            set_token_cookie(resp, game, tok)
        return remember_name(resp, found[0][2].name)

    @rt("/name", methods=["GET"])
    def name_form(request: Request, next: str = "/"):
        return name_gate(next, value=name_of(request) or "")

    @rt("/name", methods=["POST"])
    def name_submit(name: str = "", next: str = "/"):
        try:
            clean = S.normalize_name(name)
        except S.InvalidName:
            return name_gate(next, error="invalid_name", value=name)
        resp = RedirectResponse(safe_next(next), status_code=303)
        resp.set_cookie(NAME_COOKIE, quote(clean, safe=""), max_age=COOKIE_MAX_AGE * 15, httponly=True, samesite="lax",
                        secure=os.environ.get("COOKIE_SECURE", "0") == "1", path="/")
        return resp


def remember_name(resp: Response, name: str) -> Response:
    resp.set_cookie(NAME_COOKIE, quote(name, safe=""), max_age=COOKIE_MAX_AGE * 15, httponly=True, samesite="lax",
                    secure=os.environ.get("COOKIE_SECURE", "0") == "1", path="/")
    return resp


def client_ip_of(request: Request) -> str:
    peer = request.client.host if request.client else "0.0.0.0"
    return S.client_ip(peer, request.headers.get("x-forwarded-for"), client_ip_header=request.headers.get(
        os.environ.get("CLIENT_IP_HEADER", "").strip().lower() or "-"))


def token_of(request: Request, game_type: str = "bingo") -> str | None:
    return request.cookies.get(S.cookie_name(game_type))


def set_token_cookie(resp: Response, game_type: str, token: str) -> Response:
    resp.set_cookie(S.cookie_name(game_type), token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
                    secure=os.environ.get("COOKIE_SECURE", "0") == "1", path="/")
    return resp
