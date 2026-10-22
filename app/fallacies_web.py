"""Admin page for the fallacy list: list / search / add / inline edit / delete (HTMX rows). Same shape as /admin/phrases."""
from __future__ import annotations

import json
from urllib.parse import urlencode

from fasthtml.common import (
    H1,
    Button,
    Datalist,
    Div,
    Form,
    Input,
    Label,
    Main,
    Option,
    P,
    Select,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Title,
    Tr,
)
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse

from app import admin as A_
from app import fallacies_admin as FA
from app import texts as T
from app.admin_web import admin_tabs, denied


def _err(code: str | None):
    return Div(T.FA_ERRORS.get(code, T.FA_ERRORS["invalid_name"]), cls="cellerr", role="alert") if code else None


def display_row(f: FA.FallacyRow, error: str | None = None):
    return Tr(
        Td(f.name, Div(f.description, cls="muted small") if f.description else None, _err(error), cls="ph-text"),
        Td(f.category), Td(f.source), Td(str(f.used), cls="num"),
        Td(T.PH_YES if f.enabled else T.PH_NO, cls="" if f.enabled else "off"),
        Td(Button(T.PH_EDIT, type="button", cls="link", hx_get=f"/admin/fallacies/{f.id}/edit",
                  hx_target=f"#f-{f.id}", hx_swap="outerHTML"),
           Button(T.PH_DELETE, type="button", cls="link danger", hx_post=f"/admin/fallacies/{f.id}/delete",
                  hx_target=f"#f-{f.id}", hx_swap="outerHTML", hx_confirm=T.FA_DELETE_CONFIRM),
           cls="act"),
        id=f"f-{f.id}", cls="" if f.enabled else "disabled")


def edit_row(f: FA.FallacyRow, name: str | None = None, category: str | None = None, description: str | None = None,
             enabled: bool | None = None, error: str | None = None):
    return Tr(
        Td(Input(type="text", name="name", value=f.name if name is None else name, maxlength=FA.MAX_NAME + 20, required=True,
                 cls="ph-input", aria_label=T.FA_NAME),
           Input(type="text", name="description", value=f.description if description is None else description,
                 maxlength=FA.MAX_DESCRIPTION + 50, cls="ph-input", placeholder=T.FA_DESCRIPTION, aria_label=T.FA_DESCRIPTION),
           _err(error)),
        Td(Input(type="text", name="category", value=f.category if category is None else category, maxlength=FA.MAX_CATEGORY + 10,
                 required=True, cls="ph-input", aria_label=T.FA_CATEGORY)),
        Td(f.source), Td(str(f.used), cls="num"),
        Td(Input(type="checkbox", name="enabled", value="1", aria_label=T.FA_ACTIVE,
                 checked=f.enabled if enabled is None else enabled)),
        Td(Button(T.PH_SAVE, type="button", cls="link", hx_post=f"/admin/fallacies/{f.id}/save", hx_include="closest tr",
                  hx_target=f"#f-{f.id}", hx_swap="outerHTML"),
           Button(T.PH_CANCEL, type="button", cls="link", hx_get=f"/admin/fallacies/{f.id}", hx_target=f"#f-{f.id}",
                  hx_swap="outerHTML"),
           cls="act"),
        id=f"f-{f.id}", cls="editing")


def error_row(code: str):
    return Tr(Td(T.FA_ERRORS.get(code, T.FA_ERRORS["not_found"]), colspan=6, role="alert"), cls="rowerr")


def _qs(**kw) -> str:
    clean = {k: v for k, v in kw.items() if v not in ("", None)}
    return "?" + urlencode(clean) if clean else ""


def fallacies_page(rows: list[FA.FallacyRow], token: str, q: str = "", source: str = "", enabled: str = "",
                   error: str | None = None, notice: str | None = None, keep: dict | None = None):
    keep = keep or {}
    cats = sorted({r.category for r in rows}, key=FA.fold)
    add = Form(
        Label(T.FA_ADD_LABEL, fr="newname"),
        Div(Input(type="text", id="newname", name="name", required=True, maxlength=FA.MAX_NAME + 20, autocomplete="off",
                  placeholder=T.FA_NAME, value=keep.get("name", "")),
            Input(type="text", name="category", required=True, maxlength=FA.MAX_CATEGORY + 10, list="cats",
                  placeholder=T.FA_CATEGORY, aria_label=T.FA_CATEGORY, value=keep.get("category", FA.MANUAL_CATEGORY)),
            Datalist(*[Option(value=c) for c in cats], id="cats"),
            Input(type="text", name="description", maxlength=FA.MAX_DESCRIPTION + 50, placeholder=T.FA_DESCRIPTION,
                  aria_label=T.FA_DESCRIPTION, value=keep.get("description", "")),
            Input(type="hidden", name="csrf", value=token),
            Button(T.FA_ADD_BUTTON, type="submit", cls="primary"), cls="inline"),
        P(T.FA_ADD_HELP, cls="muted"),
        method="post", action="/admin/fallacies/add", cls="panel addform")
    flt = Form(
        Input(type="search", name="q", value=q, placeholder=T.PH_SEARCH, aria_label=T.PH_SEARCH),
        Select(*[Option(label, value=k, selected=k == source) for k, label in T.FA_SOURCES.items()], name="source",
               aria_label="origem"),
        Select(*[Option(label, value=k, selected=k == enabled) for k, label in T.FA_ENABLED_FILTER.items()], name="enabled",
               aria_label="ativa"),
        Button(T.PH_FILTER, type="submit"), method="get", action="/admin/fallacies", cls="filters")
    table = (Table(Thead(Tr(*[Th(h) for h in T.FA_COLS])), Tbody(*[display_row(r) for r in rows]), cls="users phrases")
             if rows else P(T.FA_EMPTY, cls="muted"))
    return (Title(f"{T.TITLE} - {T.FA_TITLE}"),
            Main(admin_tabs("fallacies"), H1(T.FA_TITLE),
                 Div(error, cls="error", role="alert") if error else None,
                 Div(notice, cls="notice", role="status") if notice else None,
                 add, flt, P(T.FA_TOTAL.format(n=len(rows)), cls="muted"), Div(table, cls="tablewrap"),
                 P(T.FA_ATTRIBUTION, cls="muted small"),
                 cls="wide", hx_headers=json.dumps({"X-CSRF": token})))


def register(rt, pool):
    def token() -> str:
        return A_.fallacies_token(A_.admin_password() or "")

    def csrf_bad(request: Request, field: str = ""):
        supplied = request.headers.get("x-csrf") or field
        if A_.fallacies_ok(A_.admin_password() or "", supplied):
            return None
        return PlainTextResponse("bad csrf token", status_code=403)

    @rt("/admin/fallacies", methods=["GET"])
    def list_fallacies(request: Request, q: str = "", source: str = "", enabled: str = "", added: int = 0):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            rows = FA.search(conn, q, enabled, source)
        return fallacies_page(rows, token(), q, source, enabled, notice=T.FA_ADDED if added else None)

    @rt("/admin/fallacies/add", methods=["POST"])
    def add_fallacy(request: Request, name: str = "", category: str = "", description: str = "", csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        if (bad := csrf_bad(request, csrf)) is not None:
            return bad
        with pool.connection() as conn:
            try:
                created = FA.create(conn, name, category, description)
            except FA.FallacyAdminError as e:
                return fallacies_page(FA.search(conn), token(), error=T.FA_ERRORS.get(e.code),
                                      keep={"name": name, "category": category, "description": description})
        return RedirectResponse("/admin/fallacies" + _qs(q=created.name, added=1), status_code=303)

    @rt("/admin/fallacies/{fid}", methods=["GET"])
    def show_row(request: Request, fid: int):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            f = FA.get(conn, fid)
        return display_row(f) if f else PlainTextResponse("not found", status_code=404)

    @rt("/admin/fallacies/{fid}/edit", methods=["GET"])
    def edit_form(request: Request, fid: int):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            f = FA.get(conn, fid)
        return edit_row(f) if f else PlainTextResponse("not found", status_code=404)

    @rt("/admin/fallacies/{fid}/save", methods=["POST"])
    def save_row(request: Request, fid: int, name: str = "", category: str = "", description: str = "", enabled: str = ""):
        if (d := denied(request)) is not None:
            return d
        if (bad := csrf_bad(request)) is not None:
            return bad
        with pool.connection() as conn:
            try:
                f = FA.update(conn, fid, name, category, description, bool(enabled))
            except FA.NotFound:
                return error_row("not_found")
            except FA.FallacyAdminError as e:
                cur = FA.get(conn, fid)
                if cur is None:
                    return error_row("not_found")
                return edit_row(cur, name, category, description, bool(enabled), error=e.code)
        return display_row(f)

    @rt("/admin/fallacies/{fid}/delete", methods=["POST"])
    def delete_row(request: Request, fid: int):
        if (d := denied(request)) is not None:
            return d
        if (bad := csrf_bad(request)) is not None:
            return bad
        with pool.connection() as conn:
            try:
                FA.delete(conn, fid)
            except FA.NotFound:
                return ""                                   # already gone: just remove the row
            except FA.FallacyAdminError as e:               # in use / below the floor: the row stays, with the reason
                cur = FA.get(conn, fid)
                return display_row(cur, error=e.code) if cur else ""
        return ""
