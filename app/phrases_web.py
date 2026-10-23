"""Admin page for phrases: list / search / add / inline edit / delete (HTMX rows)."""
from __future__ import annotations

import json
from urllib.parse import urlencode

from fasthtml.common import (
    H1,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    Main,
    Option,
    P,
    Script,
    Select,
    Span,
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
from app import phrases as P_
from app import texts as T
from app.admin_web import admin_tabs, denied


def _fmt_score(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".")


def _err(code: str | None):
    return Div(T.PH_ERRORS.get(code, T.PH_ERRORS["invalid_phrase"]), cls="cellerr", role="alert") if code else None


def display_row(p: P_.PhraseRow, error: str | None = None):
    rate = f"{p.rate * 100:.0f}%" if p.rate is not None else "-"
    return Tr(
        Td(Input(type="checkbox", name="ids", value=str(p.id), form="bulkform", cls="sel",
                 aria_label=p.text, onchange="phCount()")),
        Td(p.text, _err(error), cls="ph-text"), Td(p.source),
        Td(_fmt_score(p.score), Span(T.PH_FROZEN, cls="pill") if p.frozen else None, cls="num"),
        Td(str(p.dealt), cls="num"), Td(str(p.marked), cls="num"), Td(rate, cls="num"),
        Td(T.PH_YES if p.enabled else T.PH_NO, cls="" if p.enabled else "off"),
        Td(Button(T.PH_EDIT, type="button", cls="link", hx_get=f"/admin/phrases/{p.id}/edit",
                  hx_target=f"#p-{p.id}", hx_swap="outerHTML"),
           Button(T.PH_DELETE, type="button", cls="link danger", hx_post=f"/admin/phrases/{p.id}/delete",
                  hx_target=f"#p-{p.id}", hx_swap="outerHTML", hx_confirm=T.PH_DELETE_CONFIRM),
           cls="act"),
        id=f"p-{p.id}", cls="" if p.enabled else "disabled")


def edit_row(p: P_.PhraseRow, text: str | None = None, score: str | None = None, enabled: bool | None = None,
             error: str | None = None):
    return Tr(
        Td(""),
        Td(Input(type="text", name="text", value=p.text if text is None else text, maxlength=P_.MAX_TEXT_LEN + 20,
                 required=True, cls="ph-input"), _err(error)),
        Td(p.source),
        Td(Input(type="text", name="score", value=_fmt_score(p.score) if score is None else score,
                 inputmode="decimal", size=6, cls="ph-input num")),
        Td(str(p.dealt), cls="num"), Td(str(p.marked), cls="num"), Td("", cls="num"),
        Td(Input(type="checkbox", name="enabled", value="1", checked=p.enabled if enabled is None else enabled)),
        Td(Button(T.PH_SAVE, type="button", cls="link", hx_post=f"/admin/phrases/{p.id}/save",
                  hx_include="closest tr", hx_target=f"#p-{p.id}", hx_swap="outerHTML"),
           Button(T.PH_CANCEL, type="button", cls="link", hx_get=f"/admin/phrases/{p.id}",
                  hx_target=f"#p-{p.id}", hx_swap="outerHTML"),
           cls="act"),
        id=f"p-{p.id}", cls="editing")


def error_row(code: str):
    return Tr(Td(T.PH_ERRORS.get(code, T.PH_ERRORS["invalid_phrase"]), colspan=9, role="alert"), cls="rowerr")


def _qs(**kw) -> str:
    clean = {k: v for k, v in kw.items() if v not in ("", None)}
    return "?" + urlencode(clean) if clean else ""


BULK_JS = """
function phBoxes() { return [...document.querySelectorAll('input.sel')]; }
function phCount() {
  document.getElementById('selcount').textContent = '__SEL__'.replace('{n}', phBoxes().filter(b => b.checked).length);
}
function phAll(on) { phBoxes().forEach(b => { b.checked = on; }); phCount(); }
function phNone(none) {
  if (!phBoxes().some(b => b.checked)) { alert(none); return false; }
  return true;
}
function phConfirm(msg, none) { return phNone(none) && confirm(msg); }
phCount();
"""


def phrases_page(rows: list[P_.PhraseRow], total: int, token: str, q: str, source: str, enabled: str,
                 sort: str, page: int, error: str | None = None, notice: str | None = None):
    pages = max(1, -(-total // P_.PER_PAGE))
    page = min(page, pages)
    base = {"q": q, "source": source, "enabled": enabled, "sort": sort if sort != "score" else ""}
    add = Form(
        Label(T.PH_ADD_LABEL, fr="newtext"),
        Div(Input(type="text", id="newtext", name="text", required=True, maxlength=P_.MAX_TEXT_LEN + 20,
                  autocomplete="off"),
            Input(type="hidden", name="csrf", value=token),
            Button(T.PH_ADD_BUTTON, type="submit", cls="primary"), cls="inline"),
        P(T.PH_ADD_HELP, cls="muted"),
        method="post", action="/admin/phrases/add", cls="panel addform")
    flt = Form(
        Input(type="search", name="q", value=q, placeholder=T.PH_SEARCH, aria_label=T.PH_SEARCH),
        Select(Option(T.PH_ALL, value=""), *[Option(s, value=s, selected=s == source) for s in P_.SOURCES],
               name="source", aria_label="origem"),
        Select(*[Option(label, value=k, selected=k == enabled) for k, label in T.PH_ENABLED_FILTER.items()],
               name="enabled", aria_label="ativa"),
        Select(*[Option(label, value=k, selected=k == sort) for k, label in T.PH_SORTS.items()],
               name="sort", aria_label=T.PH_SORT),
        Button(T.PH_FILTER, type="submit"), method="get", action="/admin/phrases", cls="filters")
    nav = Div(
        A(T.PH_PREV, href="/admin/phrases" + _qs(**base, page=page - 1 if page > 2 else "")) if page > 1 else Span(T.PH_PREV, cls="muted"),
        Span(T.PH_PAGE.format(p=page, n=pages), cls="muted"),
        A(T.PH_NEXT, href="/admin/phrases" + _qs(**base, page=page + 1)) if page < pages else Span(T.PH_NEXT, cls="muted"),
        cls="pager")
    bulk = Form(
        *[Input(type="hidden", name=k, value=v) for k, v in base.items()],
        Input(type="hidden", name="page", value=str(page)),
        Input(type="hidden", name="csrf", value=token),
        Button(T.PH_SELECT_ALL, type="button", cls="link", onclick="phAll(true)"),
        Button(T.PH_DESELECT_ALL, type="button", cls="link", onclick="phAll(false)"),
        Span("", id="selcount", cls="muted"),
        Button(T.PH_ACTIVATE_SELECTED, type="submit", cls="link", formaction="/admin/phrases/bulk-enable",
               onclick=f"return phNone({json.dumps(T.PH_BULK_NONE)})"),
        Button(T.PH_DEACTIVATE_SELECTED, type="submit", cls="link", formaction="/admin/phrases/bulk-disable",
               onclick=f"return phNone({json.dumps(T.PH_BULK_NONE)})"),
        Button(T.PH_DELETE_SELECTED, type="submit", cls="link danger", formaction="/admin/phrases/bulk-delete",
               onclick=f"return phConfirm({json.dumps(T.PH_BULK_CONFIRM)}, {json.dumps(T.PH_BULK_NONE)})"),
        method="post", action="/admin/phrases/bulk-delete", id="bulkform", cls="bulkbar")
    table = (Table(Thead(Tr(*[Th(h) for h in T.PH_COLS])), Tbody(*[display_row(r) for r in rows]),
                   cls="users phrases") if rows else P(T.PH_EMPTY, cls="muted"))
    return (Title(f"{T.TITLE} - {T.PH_TITLE}"), admin_tabs("phrases"),
            Main(H1(T.PH_TITLE),
                 Div(error, cls="error", role="alert") if error else None,
                 Div(notice, cls="notice", role="status") if notice else None,
                 add, Div(flt, P(T.PH_TOTAL.format(n=total), cls="muted"), bulk, cls="panel toolbar"),
                 Div(table, cls="panel tablewrap"), nav, Script(BULK_JS.replace("__SEL__", T.PH_SELECTED)),
                 cls="wide", hx_headers=json.dumps({"X-CSRF": token})))


def register(rt, pool):
    def token() -> str:
        return A_.phrases_token(A_.admin_password() or "")

    def csrf_bad(request: Request, field: str = ""):
        supplied = request.headers.get("x-csrf") or field
        if A_.phrases_ok(A_.admin_password() or "", supplied):
            return None
        return PlainTextResponse("bad csrf token", status_code=403)

    @rt("/admin/phrases")
    def list_phrases(request: Request, q: str = "", source: str = "", enabled: str = "", sort: str = "score",
                     page: int = 1, added: int = 0, deleted: int = 0,
                     activated: int = -1, deactivated: int = -1):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            rows, total = P_.search(conn, q, source, enabled, sort, page)
            pages = max(1, -(-total // P_.PER_PAGE))
            if page > pages:                                   # e.g. after deleting the last row of the last page
                rows, total = P_.search(conn, q, source, enabled, sort, pages)
        return phrases_page(rows, total, token(), q, source, enabled, sort, min(max(page, 1), pages),
                            notice=(T.PH_ADDED if added else T.PH_DELETED_N.format(n=deleted) if deleted
                                    else T.PH_ACTIVATED_N.format(n=activated) if activated >= 0
                                    else T.PH_DEACTIVATED_N.format(n=deactivated) if deactivated >= 0 else None))

    @rt("/admin/phrases/add", methods=["POST"])
    def add_phrase(request: Request, text: str = "", csrf: str = ""):
        if (d := denied(request)) is not None:
            return d
        if (bad := csrf_bad(request, csrf)) is not None:
            return bad
        with pool.connection() as conn:
            try:
                created = P_.create(conn, text)
            except P_.PhraseError as e:
                rows, total = P_.search(conn)
                return phrases_page(rows, total, token(), "", "", "", "score", 1, error=T.PH_ERRORS.get(e.code))
        return RedirectResponse("/admin/phrases" + _qs(q=created.text, added=1), status_code=303)

    @rt("/admin/phrases/bulk-delete", methods=["POST"])
    async def bulk_delete(request: Request):
        if (d := denied(request)) is not None:
            return d
        form = await request.form()
        if (bad := csrf_bad(request, str(form.get("csrf", "")))) is not None:
            return bad
        ids = [int(v) for v in form.getlist("ids") if str(v).isdigit()]
        keep = {k: str(form.get(k, "")) for k in ("q", "source", "enabled", "sort")}
        page = int(form["page"]) if str(form.get("page", "")).isdigit() else 1
        with pool.connection() as conn:
            try:
                n = P_.delete_many(conn, ids)
            except P_.TooFewEnabled as e:
                rows, total = P_.search(conn, keep["q"], keep["source"], keep["enabled"],
                                        keep["sort"] or "score", page)
                return phrases_page(rows, total, token(), keep["q"], keep["source"], keep["enabled"],
                                    keep["sort"] or "score", page, error=T.PH_ERRORS.get(e.code))
        return RedirectResponse("/admin/phrases" + _qs(**keep, page=page if page > 1 else "", deleted=n),
                                status_code=303)

    async def bulk_set_enabled(request: Request, enabled: bool):
        if (d := denied(request)) is not None:
            return d
        form = await request.form()
        if (bad := csrf_bad(request, str(form.get("csrf", "")))) is not None:
            return bad
        ids = [int(v) for v in form.getlist("ids") if str(v).isdigit()]
        keep = {k: str(form.get(k, "")) for k in ("q", "source", "enabled", "sort")}
        page = int(form["page"]) if str(form.get("page", "")).isdigit() else 1
        with pool.connection() as conn:
            try:
                n = P_.set_enabled_many(conn, ids, enabled)
            except P_.TooFewEnabled as e:
                rows, total = P_.search(conn, keep["q"], keep["source"], keep["enabled"],
                                        keep["sort"] or "score", page)
                return phrases_page(rows, total, token(), keep["q"], keep["source"], keep["enabled"],
                                    keep["sort"] or "score", page, error=T.PH_ERRORS.get(e.code))
        result = {"activated" if enabled else "deactivated": n}
        return RedirectResponse("/admin/phrases" + _qs(**keep, page=page if page > 1 else "", **result),
                                status_code=303)

    @rt("/admin/phrases/bulk-enable", methods=["POST"])
    async def bulk_enable(request: Request):
        return await bulk_set_enabled(request, True)

    @rt("/admin/phrases/bulk-disable", methods=["POST"])
    async def bulk_disable(request: Request):
        return await bulk_set_enabled(request, False)

    @rt("/admin/phrases/{pid}")
    def show_row(request: Request, pid: int):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            p = P_.get(conn, pid)
        return display_row(p) if p else PlainTextResponse("not found", status_code=404)

    @rt("/admin/phrases/{pid}/edit")
    def edit_form(request: Request, pid: int):
        if (d := denied(request)) is not None:
            return d
        with pool.connection() as conn:
            p = P_.get(conn, pid)
        return edit_row(p) if p else PlainTextResponse("not found", status_code=404)

    @rt("/admin/phrases/{pid}/save", methods=["POST"])
    def save_row(request: Request, pid: int, text: str = "", score: str = "", enabled: str = ""):
        if (d := denied(request)) is not None:
            return d
        if (bad := csrf_bad(request)) is not None:
            return bad
        with pool.connection() as conn:
            try:
                p = P_.update(conn, pid, text=text, score=score, enabled=bool(enabled))
            except P_.NotFound:
                return error_row("not_found")
            except P_.PhraseError as e:
                cur = P_.get(conn, pid)
                if cur is None:
                    return error_row("not_found")
                return edit_row(cur, text, score, bool(enabled), error=e.code)
        return display_row(p)

    @rt("/admin/phrases/{pid}/delete", methods=["POST"])
    def delete_row(request: Request, pid: int):
        if (d := denied(request)) is not None:
            return d
        if (bad := csrf_bad(request)) is not None:
            return bad
        with pool.connection() as conn:
            try:
                P_.delete(conn, pid)
            except P_.NotFound:
                return ""                                   # already gone: just remove the row
            except P_.TooFewEnabled as e:
                cur = P_.get(conn, pid)
                return display_row(cur, error=e.code) if cur else ""
        return ""
