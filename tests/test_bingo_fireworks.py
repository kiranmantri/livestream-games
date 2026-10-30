"""Full-card fireworks that never stop: they stop when I unmark a cell (game.js -> bingoFx.stop) or when the admin unmarks one
(game.js polls GET /myboard; fx.js stops once a redraw leaves the board not full), and they go on forever on the end
screen when I am the winner with a full card (data-fx="fireworks")."""
import re
from pathlib import Path

from app import marks as M
from app import scoring as G
from app import sessions as S
from tests.test_web import IP_A, IP_B, conn, start, web  # noqa: F401  (fixtures)

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
FX = (STATIC / "fx.js").read_text()
GAME = (STATIC / "game.js").read_text()


def ids(html):
    return [int(i) for i in re.findall(r'id="cell-(\d+)"', html)]


HX = {"HX-Request": "true"}                                   # fx.js polls through htmx: a fragment, not a whole page


def fill(b):
    for i in ids(b.get("/").text):
        assert b.post(f"/mark/{i}").status_code == 200


def test_myboard_is_my_board_and_shows_an_admin_unmark(web, conn):  # noqa: F811
    a = web(IP_A)
    start(a)
    fill(a)
    html = a.get("/myboard", headers=HX).text
    assert re.match(r'<div[^>]*id="game-board"', html.lstrip()) and html.count('aria-pressed="true"') == 16
    s = S.get_active_session(conn)
    me = conn.execute("SELECT id FROM users WHERE session_id = %s", (s.id,)).fetchone()[0]
    first = ids(html)[0]
    assert M.revoke_mark(conn, s.id, me, first)
    html = a.get("/myboard", headers=HX).text
    assert html.count('aria-pressed="true"') == 15
    tag = re.search(rf'<button[^>]*id="cell-{first}"[^>]*>', html).group(0)
    assert re.search(r'class="cell revoked[\s"]', tag) and 'aria-pressed="false"' in tag   # shown as removed by the admin


def test_myboard_without_a_player_or_after_the_end_reloads_the_page(web, conn):  # noqa: F811
    assert web(IP_B).get("/myboard", headers=HX).headers.get("hx-refresh") == "true"
    a = web(IP_A)
    start(a)
    G.finish_session(conn, S.get_active_session(conn).id)
    r = a.get("/myboard", headers=HX)
    assert r.headers.get("hx-refresh") == "true" and "game-board" not in r.text


def test_end_screen_fireworks_only_for_the_winner_with_a_full_card(web, conn):  # noqa: F811
    a, b = web(IP_A), web(IP_B)
    start(a)
    b.post("/join", data={"name": "Bia"})
    fill(a)
    b.post(f"/mark/{ids(b.get('/').text)[0]}")
    G.finish_session(conn, S.get_active_session(conn).id)
    win, lose = a.get("/").text, b.get("/").text
    assert 'data-fx="fireworks"' in win and 'src="/fx.js"' in win
    assert 'data-fx="fireworks"' not in lose and 'src="/fx.js"' not in lose


def test_end_screen_full_card_that_lost_the_tie_gets_no_fireworks(web, conn):  # noqa: F811
    a, b = web(IP_A), web(IP_B)
    start(a)
    b.post("/join", data={"name": "Bia"})
    fill(a)
    fill(b)                                                  # 16 each: Ana reached it first and wins the tie
    G.finish_session(conn, S.get_active_session(conn).id)
    assert 'data-fx="fireworks"' in a.get("/").text
    assert 'data-fx="fireworks"' not in b.get("/").text


def test_end_screen_winner_without_a_full_card_gets_no_fireworks(web, conn):  # noqa: F811
    a = web(IP_A)
    start(a)
    a.post(f"/mark/{ids(a.get('/').text)[0]}")
    G.finish_session(conn, S.get_active_session(conn).id)
    html = a.get("/").text
    assert 'class="winner"' in html and 'data-fx="fireworks"' not in html


def test_game_page_has_no_end_screen_marker(web, conn):  # noqa: F811
    a = web(IP_A)
    start(a)
    fill(a)
    assert 'data-fx="fireworks"' not in a.get("/").text      # during the game fx.js decides from the cells themselves


def test_unmarking_a_cell_stops_the_fireworks():
    assert "if (!on) window.bingoFx.stop();" in GAME


def test_fireworks_loop_forever_and_stop_only_when_the_card_is_no_longer_full():
    assert "function stop" in FX
    assert "[data-fx=fireworks]" in FX                       # the end screen starts them on load
    assert "addEventListener('resize'" in FX                 # the window layer follows the window size
    settle = FX.split("htmx:afterSettle", 1)[1].split("});", 1)[0]
    assert "myFull()" in settle and "stop()" in settle         # game.js's board redraw (admin clear / block) stops them
    poll = GAME.split("function syncBoard", 1)[1].split("\n  }\n", 1)[0]
    assert ".pending" in poll and "'/myboard?v='" in poll      # never redraws the board under a tap in flight
