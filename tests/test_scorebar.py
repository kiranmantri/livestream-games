# ruff: noqa: F811
"""Player pages of Quem é Falacioso? and Aponte o mentiroso!: no "Placar" panel at the bottom; a small line at the top instead
("Kiran (você) · 5 pontos · Placar atual") whose link opens a modal with everybody's points."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import checker as C
from app import texts as T
from tests.test_admin_falacias import (  # noqa: F401
    IP_B,
    _env,
    conn,
    fal,
    game,
    web,
)
from tests.test_admin_falacias import named as fal_named  # noqa: F401
from tests.test_checker_admin_web import admin, app, visitor  # noqa: F401
from tests.test_checker_play import _slow_replay, play, result, send  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
HX = {"hx-request": "true"}


def bar_of(html):
    m = re.search(r'<div[^>]*id="scorebar".*?</div>', html, re.DOTALL)
    assert m, "no score bar"
    return m.group(0)


# ---------------------------------------------------------------- Quem é Falacioso?
def test_falacias_page_has_no_scoreboard_panel_and_a_bar_with_the_link(web, conn, fal, game):
    html = game["ana"].get("/falacias").text
    assert f"<h2>{T.LEADERBOARD}</h2>" not in html and 'class="lb"' not in html
    bar = bar_of(html)
    assert "Ana" in bar and f"({T.YOU})" in bar and T.SCORE_NOW in bar and "0,0 pontos" in bar
    assert 'href="/falacias/board"' in bar and 'hx-get="/falacias/board"' in bar and 'hx-get="/falacias/score"' in bar
    assert 'id="scoredlg"' in html and 'src="/board.js"' in html


def test_falacias_side_no_longer_carries_the_leaderboard(web, conn, fal, game):
    side = game["ana"].get("/falacias/side").text
    assert T.LEADERBOARD not in side and 'class="lb"' not in side and T.CK_MINE in side


def test_falacias_score_fragment_and_board_list_everybody(web, conn, fal, game):
    r = game["ana"].get("/falacias/score")
    assert r.status_code == 200 and "<html" not in r.text and "Ana" in r.text and "0,0 pontos" in r.text
    board = game["ana"].get("/falacias/board", headers=HX).text
    assert "<html" not in board and "Ana" in board and "Bia" in board and board.count("<li") >= 2
    assert re.search(r'<li[^>]*class="[^"]*me[^"]*"[^>]*>.*?Ana', board, re.DOTALL)
    assert "<html" in game["ana"].get("/falacias/board").text           # without JS the link opens a page


def test_falacias_score_and_board_need_a_player(web, conn, fal, game):
    for path in ("/falacias/score", "/falacias/board"):
        assert web(IP_B).get(path).headers.get("hx-refresh") == "true"


# ---------------------------------------------------------------- Aponte o mentiroso!
def test_checker_page_has_no_scoreboard_panel_and_a_bar_with_the_link(app, conn):
    c = play(app, conn)
    html = c.get("/checker").text
    assert f"<h2>{T.LEADERBOARD}</h2>" not in html and 'class="lb"' not in html
    bar = bar_of(html)
    assert "Ana" in bar and f"({T.YOU})" in bar and "0 pontos" in bar and T.SCORE_NOW in bar
    assert 'href="/checker/board"' in bar and 'hx-get="/checker/score"' in bar
    assert 'id="scoredlg"' in html and 'src="/board.js"' in html
    assert T.LEADERBOARD not in c.get("/checker/side").text


def test_checker_points_in_the_bar_follow_the_scoring_and_the_board_lists_everybody(app, conn):
    a = play(app, conn, "Ana", ip="10.1.1.1")
    b = visitor(app, "Bia")
    b.headers["x-forwarded-for"] = "10.1.1.2"
    b.post("/checker/join")
    send(a, guess=-4, note="uma mentira")
    ck = C.claim_next_check(conn)
    C.complete_check(conn, ck.id, result(score=-3))
    assert "6 pontos" in a.get("/checker/score").text and "0 pontos" in b.get("/checker/score").text
    board = b.get("/checker/board", headers=HX).text
    assert board.index("Ana") < board.index("Bia") and "6 pontos" in board and "<html" not in board
    assert "<html" in b.get("/checker/board").text


def test_checker_score_and_board_need_a_player(app, conn):
    for path in ("/checker/score", "/checker/board"):
        assert visitor(app).get(path).headers.get("hx-refresh") == "true"


# ---------------------------------------------------------------- the script and the modal shell
def test_board_script_is_small_and_opens_the_dialog():
    js = (ROOT / "app" / "static" / "board.js").read_text()
    assert "showModal" in js and "scoredlg" in js and len(js.splitlines()) < 30


@pytest.mark.parametrize("path", ["/falacias", "/checker"])
def test_the_modal_has_a_close_button_and_a_body_the_board_is_loaded_into(path, web, conn, fal, game, app):
    html = game["ana"].get("/falacias").text if path == "/falacias" else play(app, conn).get("/checker").text
    dlg = re.search(r'<dialog[^>]*id="scoredlg".*?</dialog>', html, re.DOTALL).group(0)
    assert 'id="boardbody"' in dlg and T.SCORE_CLOSE in dlg and 'method="dialog"' in dlg


def test_my_cards_are_a_three_column_grid_in_both_games():
    css = (ROOT / "app" / "static" / "app.css").read_text()
    assert re.search(r"ol\.pairs \{[^}]*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)", css)
    assert re.search(r"\.side \{[^}]*grid-template-columns: 1fr;", css)
    assert re.search(r"@media \(max-width: 760px\) \{ ol\.pairs \{ grid-template-columns: 1fr; \} \}", css)
    assert "ol.claims { list-style" not in css, "claims inherit the pairs grid"
