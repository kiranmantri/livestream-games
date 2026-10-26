"""Backlog group 2: Bingo player page on phones — per-cell swaps, the (falácia) tag, phone-first structure."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import texts as T
from app.db import connect
from app.main import create_app, split_kind
from tests.test_cards import load_phrases

HX = {"hx-request": "true"}
CSS = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text()
JS = (Path(__file__).resolve().parents[1] / "app" / "static" / "game.js").read_text()


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    load_phrases(c, n=60)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks, revoked_marks CASCADE")
    c.close()


@pytest.fixture
def me(conn, test_db_url):
    app = create_app(test_db_url)
    with TestClient(app):
        b = TestClient(app, client=("203.0.113.10", 50000), follow_redirects=False)
        b.post("/name", data={"name": "Ana", "next": "/"})
        b.post("/start", data={"name": "Ana"})
        yield b


# ------------------------------------------------------------ per-cell swaps
def test_cells_have_ids_and_swap_only_themselves(me):
    html = me.get("/").text
    for i in range(16):
        tag = re.search(rf'<button[^>]*id="cell-{i}"[^>]*>', html)
        assert tag, i
        assert 'hx-target="this"' in tag.group(0) and 'hx-swap="outerHTML"' in tag.group(0)
    assert 'id="count"' in html and 'id="lines"' in html


def test_htmx_mark_answers_with_the_cell_and_out_of_band_count_and_lines(me):
    r = me.post("/mark/3", headers=HX)
    assert r.status_code == 200
    assert 'id="game-board"' not in r.text and 'class="board"' not in r.text          # not the whole board
    assert len(re.findall(r'<button', r.text)) == 1
    assert re.search(r'<button[^>]*id="cell-3"[^>]*aria-pressed="true"', r.text) or \
        re.search(r'<button[^>]*aria-pressed="true"[^>]*id="cell-3"', r.text)
    assert re.search(r'id="count"[^>]*hx-swap-oob="true"[^>]*>[^<]*1/16', r.text) or \
        re.search(r'hx-swap-oob="true"[^>]*id="count"[^>]*>[^<]*1/16', r.text)
    assert 'id="lines"' in r.text and 'hx-swap-oob="true"' in r.text
    assert 'class="at"' in r.text                                   # the mark time is part of the cell


def test_htmx_mark_updates_the_blue_lines_out_of_band(me):
    for i in (0, 1, 2):
        me.post(f"/mark/{i}", headers=HX)
    r = me.post("/mark/3", headers=HX)                              # a full first row
    lines = re.search(r'<div[^>]*id="lines"[^>]*>(.*?)</div>\s*$', r.text, re.DOTALL) or re.search(r'id="lines".*', r.text, re.DOTALL)
    assert lines and 'class="line row"' in r.text
    r = me.post("/mark/3", headers=HX)                              # unmarked again: the box disappears
    assert 'class="line row"' not in r.text and 'id="lines"' in r.text


def test_plain_post_still_returns_the_whole_board(me):
    r = me.post("/mark/3")                                          # no HX-Request: tests, curl, no-JS clients
    assert 'id="game-board"' in r.text and 'id="cell-3"' in r.text


def test_revoked_cell_swaps_the_whole_board_with_the_reason(me, conn):
    me.post("/mark/5", headers=HX)
    conn.execute("INSERT INTO revoked_marks (card_id, cell_index) SELECT id, 5 FROM bingo_cards")
    r = me.post("/mark/5", headers=HX)
    assert r.headers["hx-retarget"] == "#game-board" and r.headers["hx-reswap"] == "outerHTML"
    assert T.CELL_REVOKED in r.text and 'id="game-board"' in r.text


# ------------------------------------------------------------ (falácia) tag
@pytest.mark.parametrize("text,main,tag", [
    ("ad populum (falácia)", "ad populum", "falácia"),
    ("apelo à autoridade (falácia ad verecundiam)", "apelo à autoridade", "falácia ad verecundiam"),
    ("tudo é (relativo) mesmo", "tudo é (relativo) mesmo", None),
    ("(falácia) no começo", "(falácia) no começo", None),
    ("sem parênteses", "sem parênteses", None),
])
def test_split_kind(text, main, tag):
    assert split_kind(text) == (main, tag)


def test_a_falacia_phrase_is_drawn_with_a_small_tag(conn, test_db_url):
    # score 99 (the maximum) = the largest sampling weight: the test is about RENDERING the tag, not about sampling luck (a low-scored phrase
    # could miss all 40 cards; it failed that way in full runs).
    conn.execute("UPDATE phrases SET text = 'ad populum (falácia)', score = 99, enabled = true WHERE id = (SELECT min(id) FROM phrases)")
    app = create_app(test_db_url)
    with TestClient(app):
        seen = False
        for n in range(40):                                         # deal cards until one holds that phrase
            b = TestClient(app, client=(f"198.51.100.{n + 1}", 50000), follow_redirects=False)
            b.post("/name", data={"name": f"P{n}", "next": "/"})
            b.post("/start" if n == 0 else "/join", data={"name": f"P{n}"})
            html = b.get("/").text
            if "ad populum" in html:
                seen = True
                assert re.search(r'ad populum\s*<small class="kind">falácia</small>', html) and "(falácia)" not in html
                break
        assert seen


# ------------------------------------------------------------ phone-first structure
def test_offline_note_is_a_one_line_status_in_the_game_bar(me):
    html = me.get("/").text
    assert 'class="offline-note"' in html                          # the stubbed lookup says: no live
    assert T.LIVE_HIDE in html                                      # the toggle is still there


def test_phone_css_rules_exist():
    phone = CSS[CSS.index("/* phone (group 2)"):]
    assert "@media (max-width: 760px)" in phone
    assert "position: sticky" in phone and "font-size: 13px" in phone and "min-height: 44px" in phone
    assert "order: -1" in phone                                    # my board comes first


def test_game_js_hides_video_and_chat_by_default_on_phones_and_flips_on_tap():
    assert "hideLive === undefined" in JS and "matchMedia" not in JS
    assert "closest('.cardbox.mine button.cell')" in JS and "htmx:sendError" in JS


def test_game_js_sends_taps_one_at_a_time_in_order():
    assert "chain = chain.then" in JS and "htmx.ajax('POST'" in JS and "e.stopPropagation()" in JS
