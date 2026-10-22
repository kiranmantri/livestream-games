"""Adversarial-test fixes: non-Latin-1 names (was HTTP 500), rapid taps (board out of sync), lost-cookie recovery link."""
from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from app import sessions as S
from app.db import connect
from app.main import create_app
from tests.test_cards import load_phrases

IP = "203.0.113.10"
NAMES = ["Ana😀", "аdmin", "日本", "Zoë", "Ação"]


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    load_phrases(c, n=60)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


@pytest.fixture
def web(conn, test_db_url):
    app = create_app(test_db_url)
    with TestClient(app):
        def browser(ip=IP):
            return TestClient(app, client=(ip, 50000), follow_redirects=False)
        yield browser


@pytest.mark.parametrize("name", NAMES)
def test_any_unicode_name_is_remembered_and_can_play(web, name):
    b = web()
    r = b.post("/name", data={"name": name, "next": "/"})
    assert r.status_code == 303, r.text
    assert name in b.get("/").text                      # the cookie round-trips, not just ASCII/Latin-1
    r = b.post("/start", data={"name": name})
    assert r.status_code == 303, r.text
    assert name in b.get("/").text


def test_board_taps_are_serialised_by_a_container_that_is_never_swapped(web):
    b = web()
    b.post("/name", data={"name": "Ana", "next": "/"})
    b.post("/start", data={"name": "Ana"})
    html = b.get("/").text
    assert 'class="cardbox mine"' in html
    buttons = re.findall(r'<button[^>]*hx-post="/mark/\d+"[^>]*>', html)
    assert len(buttons) == 16
    assert all('hx-sync="closest .cardbox:queue all"' in x for x in buttons)


def test_recovery_link_restores_the_player_on_a_new_device(web):
    a = web()
    a.post("/name", data={"name": "Ana", "next": "/"})
    a.post("/start", data={"name": "Ana"})
    a.post("/mark/3")
    link = re.search(r'href="(/r/bingo/[^"]+)"', a.get("/").text)
    assert link, "the game page must show the recovery link"
    other = web()                                       # no cookies at all
    r = other.get(link.group(1))
    assert r.status_code == 303 and r.headers["location"] == "/" and S.cookie_name() in r.headers["set-cookie"]
    html = other.get("/").text
    assert "Ana" in html and 'aria-pressed="true"' in html     # same card, mark kept


@pytest.mark.parametrize("path", ["/r/bingo/nope", "/r/falacias/nope", "/r/chess/x", "/r/bingo/" + "a" * 500])
def test_bad_recovery_links_set_no_cookie(web, path):
    r = web().get(path)
    assert r.status_code in (303, 404) and "set-cookie" not in r.headers


def test_recovery_link_is_not_shown_on_the_entry_page(web):
    b = web()
    b.post("/name", data={"name": "Ana", "next": "/"})
    assert "/r/bingo/" not in b.get("/").text
