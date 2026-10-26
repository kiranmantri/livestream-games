"""Completed lines (blue boxes), the users-table sort, admin unmark over HTTP and the game-screen toggles."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import lines as L
from app.db import connect
from app.main import create_app
from tests.test_admin import PW, _csrf_of, basic
from tests.test_cards import load_phrases


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    load_phrases(c, n=60)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


@pytest.fixture
def web(conn, test_db_url, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", PW)
    app = create_app(test_db_url)
    with TestClient(app):
        def browser(ip="203.0.113.10"):
            return TestClient(app, client=(ip, 50000), follow_redirects=False)
        yield browser


PH4 = [{"phrase_id": i, "text": f"f{i}"} for i in range(16)]
PH5 = [{"phrase_id": i, "text": f"f{i}"} if i != 12 else {"type": "image"} for i in range(25)]


def test_4x4_rows_columns_and_both_diagonals():
    assert L.complete_lines(PH4, {0, 1, 2, 3}) == [("row", 0)]
    assert L.complete_lines(PH4, {1, 5, 9, 13}) == [("col", 1)]
    assert L.complete_lines(PH4, {0, 5, 10, 15}) == [("diag", 0)]
    assert L.complete_lines(PH4, {3, 6, 9, 12}) == [("diag", 1)]
    assert L.complete_lines(PH4, {0, 1, 2}) == [] and L.complete_lines(PH4, set()) == []


def test_several_lines_at_once():
    marked = {0, 1, 2, 3, 4, 8, 12}                       # row 0 + column 0
    assert L.complete_lines(PH4, marked) == [("row", 0), ("col", 0)]


def test_5x5_avatar_counts_as_marked_for_lines():
    assert L.complete_lines(PH5, {10, 11, 13, 14}) == [("row", 2)]
    assert ("diag", 0) in L.complete_lines(PH5, {0, 6, 18, 24})
    assert L.complete_lines(PH5, {10, 11, 13}) == []


def test_row_and_column_are_one_rectangle_and_a_diagonal_is_one_box_per_cell():
    assert L.line_areas(("row", 1), 4) == ["grid-area:2 / 1 / 3 / 5"]           # explicit ends: rows are implicit
    assert L.line_areas(("col", 2), 4) == ["grid-area:1 / 3 / 5 / 4"]
    assert L.line_areas(("diag", 0), 4) == [f"grid-area:{i} / {i} / {i + 1} / {i + 1}" for i in (1, 2, 3, 4)]
    assert L.line_areas(("diag", 1), 4) == ["grid-area:1 / 4 / 2 / 5", "grid-area:2 / 3 / 3 / 4",
                                            "grid-area:3 / 2 / 4 / 3", "grid-area:4 / 1 / 5 / 2"]
    assert len(L.line_boxes(PH4, {0, 1, 2, 3, 4, 8, 12})) == 2                     # row + column
    assert len(L.line_boxes(PH4, {0, 5, 10, 15})) == 4                              # diagonal: 4 boxes
    assert len(L.line_boxes(PH5, {0, 6, 18, 24})) == 5


def test_line_boxes_are_blue_not_red():
    css = Path("app/static/app.css").read_text()
    line = css[css.index(".line {"):css.index(".cell.revoked")]
    assert "#3b82f6" in line and "#ef4444" not in line and "rotate" not in line


def test_player_page_draws_a_red_box_only_for_a_complete_line(web):
    a = web("203.0.113.10")
    a.post("/start", data={"name": "Ana"})
    for i in (0, 1, 2):
        a.post(f"/mark/{i}")
    assert 'class="line row"' not in a.get("/").text
    a.post("/mark/3")
    assert a.get("/").text.count('class="line row"') == 1


def test_game_page_has_the_toggles_and_no_sort_order_toggle(web):
    a = web("203.0.113.10")
    a.post("/start", data={"name": "Ana"})
    html = a.get("/").text
    assert 'data-pref="live:toggle"' in html and 'data-pref="view:grid"' in html and 'data-pref="view:list"' in html
    assert "order:" not in html and 'src="/game.js"' in html
    assert a.get("/game.js").status_code == 200


def test_others_are_ranked_by_marks_for_the_list_view(web):
    a, b, c = web("203.0.113.10"), web("203.0.113.11"), web("203.0.113.12")
    a.post("/start", data={"name": "Ana"})
    b.post("/join", data={"name": "Bia"})
    c.post("/join", data={"name": "Caio"})
    for i in (0, 1):
        c.post(f"/mark/{i}")
    b.post("/mark/0")
    html = a.get("/").text                                  # Ana sees Bia (joined 1st) and Caio (2nd)
    pos = {n: w for w, n in re.findall(r'--o-win:(\d+)">.*?<span class="who">(\w+)</span>', html, re.DOTALL)}
    assert pos == {"Bia": "1", "Caio": "0"}                 # Caio has more marks -> first in the ranked list


def test_users_table_sorts_by_column_and_ignores_unknown_columns(web):
    a, b, c = web("203.0.113.10"), web("203.0.113.11"), web("203.0.113.12")
    a.post("/start", data={"name": "Zed"})
    b.post("/join", data={"name": "Amy"})
    c.post("/join", data={"name": "Max"})
    for i in (0, 1):
        b.post(f"/mark/{i}")
    c.post("/mark/0")
    adm = web("203.0.113.99")

    def order(q):
        html = adm.get("/admin" + q, headers=basic()).text
        return re.findall(r"<td[^>]*>\s*(Zed|Amy|Max)\s*</td>", html)
    assert order("?sort=name&dir=asc") == ["Amy", "Max", "Zed"]
    assert order("?sort=name&dir=desc") == ["Zed", "Max", "Amy"]
    assert order("?sort=marked") == ["Amy", "Max", "Zed"]            # default direction for marks: most first
    assert order("?sort=nonsense; drop table") == order("")          # whitelisted: falls back to rank
    assert "203.0.113.1" not in adm.get("/admin", headers=basic()).text


def test_admin_unmark_blocks_the_cell_and_unblock_lifts_it(web, conn):
    a, admin = web("203.0.113.10"), web("203.0.113.99")
    a.post("/start", data={"name": "Ana"})
    a.post("/mark/3")
    sid, tok = _csrf_of(conn)
    uid = str(conn.execute("SELECT id FROM users WHERE name='Ana'").fetchone()[0])
    data = {"session_id": sid, "user_id": uid, "cell": "3", "csrf": tok}
    assert admin.post("/admin/unmark", data={**data, "csrf": "bad"}, headers=basic()).status_code == 403
    assert conn.execute("SELECT count(*) FROM bingo_marks").fetchone()[0] == 1          # bad csrf: untouched
    r = admin.post("/admin/unmark", data=data, headers=basic())
    assert r.status_code == 303 and conn.execute("SELECT count(*) FROM bingo_marks").fetchone()[0] == 0
    assert conn.execute("SELECT cell_index FROM revoked_marks").fetchall() == [(3,)]
    a.post("/mark/3")                                                                    # the player cannot re-mark it
    assert conn.execute("SELECT count(*) FROM bingo_marks").fetchone()[0] == 0
    assert "revoked" in a.get("/").text
    admin.post("/admin/unblock", data=data, headers=basic())
    a.post("/mark/3")
    assert conn.execute("SELECT count(*) FROM bingo_marks").fetchone()[0] == 1


def test_is_full_ignores_the_avatar_cell():
    assert L.is_full(PH4, set(range(16))) and not L.is_full(PH4, set(range(15)))
    assert L.is_full(PH5, set(range(25)) - {12}) and not L.is_full(PH5, {0, 1})


def test_admin_shows_a_yellow_star_only_for_players_with_a_completed_line(web, conn):
    a, b, admin = web("203.0.113.10"), web("203.0.113.11"), web("203.0.113.99")
    a.post("/start", data={"name": "Ana"})
    b.post("/join", data={"name": "Bia"})
    for i in (0, 1, 2):
        a.post(f"/mark/{i}")                     # Ana: 3 of a row -> not yet
        b.post(f"/mark/{i}")
    b.post("/mark/3")                            # Bia: full row
    html = admin.get("/admin", headers=basic()).text
    assert html.count('class="star"') == 2       # Bia: users table + her card header; Ana: none
    assert re.search(r"<td[^>]*>\s*Bia\s*<span[^>]*class=\"star\"", html) and not re.search(r"Ana\s*<span[^>]*class=\"star\"", html)
    assert "1 linha completa" in html and "1 linhas" not in html
    for i in range(4, 16):
        b.post(f"/mark/{i}")
    html = admin.get("/admin", headers=basic()).text
    assert 'class="star full"' in html and "cartela completa" in html


def test_admin_unmark_over_htmx_returns_only_the_card_and_the_users_table(web, conn):
    a, admin = web("203.0.113.10"), web("203.0.113.99")
    a.post("/start", data={"name": "Ana"})
    for i in (0, 1, 2, 3):
        a.post(f"/mark/{i}")                                  # a full row -> star
    page = admin.get("/admin", headers=basic()).text
    sid, tok = _csrf_of(conn)
    uid = str(conn.execute("SELECT id FROM users WHERE name='Ana'").fetchone()[0])
    assert 'hx-post="/admin/unmark"' in page and f'hx-target="#card-{uid}"' in page and f'id="card-{uid}"' in page
    data = {"session_id": sid, "user_id": uid, "cell": "0", "csrf": tok}
    r = admin.post("/admin/unmark", data=data, headers={**basic(), "HX-Request": "true",
                                                        "HX-Current-URL": "http://x/admin?sort=name&dir=desc"})
    assert r.status_code == 200 and "<html" not in r.text                  # a fragment, not a page, not a redirect
    assert r.text.count(f'id="card-{uid}"') == 1 and "<details" in r.text and "open" in r.text.split(">")[0]
    assert 'id="users-panel"' in r.text and 'hx-swap-oob="outerHTML"' in r.text
    assert 'class="star"' not in r.text and "3/16" in r.text               # row broken -> star gone, count updated
    assert "Liberar" in r.text                                              # the blocked cell now offers the lift button
    assert conn.execute("SELECT count(*) FROM bingo_marks").fetchone()[0] == 3
    # unblock the same way (also htmx)
    r2 = admin.post("/admin/unblock", data=data, headers={**basic(), "HX-Request": "true"})
    assert r2.status_code == 200 and "Liberar" not in r2.text
    # without htmx it is still POST -> redirect (no-JS fallback), and bad csrf is refused either way
    assert admin.post("/admin/unmark", data={**data, "cell": "1"}, headers=basic()).status_code == 303
    bad = admin.post("/admin/unmark", data={**data, "csrf": "x"}, headers={**basic(), "HX-Request": "true"})
    assert bad.status_code == 403
