"""Page chrome shared by both games: the menu between games, the page shell, the name form, cookies, client IP."""
from __future__ import annotations

import os

from fasthtml.common import H1, H2, A, B, Button, Div, Form, Input, Label, Main, Nav, P, Span, Title
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app import sessions as S
from app import texts as T

COOKIE_MAX_AGE = 60 * 60 * 48
NAME_COOKIE = "lg_name"          # the nickname, asked once for both games (NOT an identity: only pre-fills joins)
TAB_PATHS = ("/", "/falacias")
MENU = (("bingo", T.MENU_BINGO, "/"), ("falacias", T.MENU_FALACIAS, "/falacias"))


def menu(current: str, running: frozenset[str] = frozenset(), first_load: bool = True):
    """Links between the games. Rendered without state first; the fragment from /menu adds a dot to games in progress."""
    links = [A(label, Span(cls="dot", title=T.MENU_RUNNING, role="img", aria_label=T.MENU_RUNNING) if key in running else None,
               href=href, cls="active" if key == current else None, aria_current="page" if key == current else None)
             for key, label, href in MENU]
    return Nav(*links, id="menu", cls="menu", hx_get=f"/menu?cur={current}", hx_trigger="load, every 30s" if first_load else "every 30s",
               hx_swap="outerHTML", aria_label=T.MENU_LABEL)


def page(*content, title: str = T.TITLE, cls: str | None = None, current: str = "bingo", tabs: bool = True):
    return Title(title), Main(menu(current) if tabs else None, *content, cls=cls)


def error_box(code: str | None):
    if not code:
        return None
    msg = T.ERRORS.get(code, T.ERRORS["generic"]).format(n=S.max_users_per_ip())
    return Div(msg, cls="error", role="alert")


def name_of(request: Request) -> str | None:
    """The remembered nickname, or None when missing/invalid (then the name gate is shown)."""
    raw = request.cookies.get(NAME_COOKIE)
    try:
        return S.normalize_name(raw) if raw else None
    except S.InvalidName:
        return None


def safe_next(value: str | None) -> str:
    return value if value in TAB_PATHS else "/"


def name_gate(next_path: str = "/", error: str | None = None, value: str = ""):
    """The FIRST thing a visitor sees: ask for a nickname. No tabs yet."""
    return page(
        H1(T.TITLE), H2(T.NAME_HEADING), P(T.NAME_HELP, cls="tag"), error_box(error),
        Div(Form(Label(T.NAME_LABEL, fr="name"),
                 Input(type="text", id="name", name="name", value=value, maxlength=S.MAX_NAME_LEN,
                       required=True, autocomplete="nickname", autofocus=True),
                 Input(type="hidden", name="next", value=safe_next(next_path)),
                 Button(T.NAME_BUTTON, type="submit", cls="primary"),
                 method="post", action="/name", cls="entry"), cls="panel"),
        tabs=False)


def play_form(action: str, button: str, name: str, next_path: str, extra=None):
    """Start/join with the remembered nickname: one button, no name field."""
    return Form(P(T.PLAYING_AS, " ", B(name), " · ", A(T.CHANGE_NAME, href=f"/name?next={next_path}"), cls="muted who"),
                extra, Button(button, type="submit", cls="primary"), method="post", action=action, cls="entry")


def name_error_gate(code: str, next_path: str, value: str):
    """Name problems (invalid / taken) go back to the name gate, with the message."""
    return name_gate(next_path, error=code, value=value)


NAME_ERRORS = ("invalid_name", "name_taken")


def register(rt) -> None:
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
        resp.set_cookie(NAME_COOKIE, clean, max_age=COOKIE_MAX_AGE * 15, httponly=True, samesite="lax",
                        secure=os.environ.get("COOKIE_SECURE", "0") == "1", path="/")
        return resp


def remember_name(resp: Response, name: str) -> Response:
    resp.set_cookie(NAME_COOKIE, name, max_age=COOKIE_MAX_AGE * 15, httponly=True, samesite="lax",
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
