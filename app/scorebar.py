"""The small score line at the top of a player page ("Kiran (você) · 5 pontos · Placar atual") and the modal behind its link.

Shared by Falaciômetro and Verdade ou Lorota. The line re-reads itself every 5 s from `<game>/score`; the modal's body is loaded from
`<game>/board` when the link is clicked (so it is always fresh, and the 5 s redraw never closes it: the dialog lives outside the polled element).
Without JS the link simply opens `<game>/board` as a page.
"""
from __future__ import annotations

from fasthtml.common import H2, A, Button, Dialog, Div, Form, Li, Ol, Script, Span

from app import texts as T


def score_bar(path: str, name: str, points: str):
    """`path` = "/falacias" or "/checker"; `points` already formatted ("6 pontos")."""
    return Div(Span(f"{name} ({T.YOU})", cls="who"), " · ", Span(points, cls="pts"), " · ",
               A(T.SCORE_NOW, href=f"{path}/board", hx_get=f"{path}/board", hx_target="#boardbody", hx_swap="innerHTML", data_board="1"),
               id="scorebar", cls="tag scorebar", hx_get=f"{path}/score", hx_trigger="every 5s", hx_swap="outerHTML")


def board_dialog():
    return Div(Dialog(Form(H2(T.SCORE_NOW), Div(id="boardbody"), Button(T.SCORE_CLOSE, type="submit", cls="primary"), method="dialog"),
                      id="scoredlg", cls="scoredlg"),
               Script(src="/board.js", defer=True))


def board_list(rows):
    """rows: (rank, name, points text, is_me)"""
    return Ol(*[Li(Span(f"{rank}.", cls="pos"), Span(name, cls="nm"), Span(pts, cls="pts"), cls="me" if me else None) for rank, name, pts, me in rows],
              cls="lb")
