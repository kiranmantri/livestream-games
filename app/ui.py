"""Page chrome shared by both games: the menu between games, the page shell, the name form, cookies, client IP."""
from __future__ import annotations

import os

from fasthtml.common import A, Button, Div, Form, Input, Label, Main, Nav, Span, Title
from starlette.requests import Request
from starlette.responses import Response

from app import sessions as S
from app import texts as T

COOKIE_MAX_AGE = 60 * 60 * 48
MENU = (("bingo", T.MENU_BINGO, "/"), ("falacias", T.MENU_FALACIAS, "/falacias"))


def menu(current: str, running: frozenset[str] = frozenset()):
    """Links between the games. Rendered without state first; the fragment from /menu adds a dot to games in progress."""
    links = [A(label, Span(cls="dot", title=T.MENU_RUNNING, role="img", aria_label=T.MENU_RUNNING) if key in running else None,
               href=href, cls="active" if key == current else None, aria_current="page" if key == current else None)
             for key, label, href in MENU]
    return Nav(*links, id="menu", cls="menu", hx_get=f"/menu?cur={current}", hx_trigger="load, every 30s",
               hx_swap="outerHTML", aria_label=T.MENU_LABEL)


def page(*content, title: str = T.TITLE, cls: str | None = None, current: str = "bingo"):
    return Title(title), Main(menu(current), *content, cls=cls)


def error_box(code: str | None):
    if not code:
        return None
    msg = T.ERRORS.get(code, T.ERRORS["generic"]).format(n=S.max_users_per_ip())
    return Div(msg, cls="error", role="alert")


def entry_form(action: str, button: str, value: str = ""):
    return Form(
        Label(T.NAME_LABEL, fr="name"),
        Input(type="text", id="name", name="name", value=value, maxlength=S.MAX_NAME_LEN,
              required=True, autocomplete="nickname", autofocus=True),
        Button(button, type="submit", cls="primary"),
        method="post", action=action, cls="entry")


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
