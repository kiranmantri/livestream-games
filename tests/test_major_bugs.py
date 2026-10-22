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


def test_board_taps_are_serialised_by_the_page_script(web):
    b = web()
    b.post("/name", data={"name": "Ana", "next": "/"})
    b.post("/start", data={"name": "Ana"})
    html = b.get("/").text
    assert 'class="cardbox mine"' in html and 'src="/game.js"' in html
    js = b.get("/game.js").text
    assert "chain = chain.then" in js                   # one request at a time, in tap order (htmx's queue drops them)


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


# ---------------------------------------------------------------- group 1 (backlog bugs)
def _named(b, name):
    b.post("/name", data={"name": name, "next": "/"})
    return b


def test_entry_page_does_not_offer_join_for_a_name_that_is_already_taken(web):
    a = _named(web(), "Ana")
    a.post("/start", data={"name": "Ana"})
    b = _named(web("203.0.113.11"), "ANA")                      # same name, other letters' case, no cookie
    html = b.get("/").text
    assert 'action="/join"' not in html and S_ERR_TAKEN in html
    assert 'name="name"' in html                                # the name form is back, to pick another one
    c = _named(web("203.0.113.12"), "Bia")
    assert 'action="/join"' in c.get("/").text                  # a free name still gets the one-button join


S_ERR_TAKEN = "Esse nome já está em uso nesta partida."


def test_name_field_is_selected_on_focus_so_typing_replaces_it(web):
    html = web().get("/name?next=/").text
    assert 'onfocus="this.select()"' in html


def test_ip_limit_redirects_after_post_and_explains(web, monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "1")
    _named(web(), "Ana").post("/start", data={"name": "Ana"})
    b = _named(web(), "Bia")                                    # same IP
    r = b.post("/join", data={"name": "Bia"})
    assert r.status_code == 303 and r.headers["location"] == "/?err=ip_limit_reached"
    page = b.get(r.headers["location"]).text
    assert "apresentador" in page and "set-cookie" not in b.get("/").headers


def test_unknown_err_codes_in_the_url_are_ignored(web):
    b = _named(web(), "Ana")
    html = b.get("/?err=%3Cscript%3E").text
    assert "<script>" not in html and 'role="alert"' not in html


def test_player_count_has_correct_singular_and_plural(web):
    a = _named(web(), "Ana")
    a.post("/start", data={"name": "Ana"})
    one = _named(web("203.0.113.11"), "Bia").get("/").text
    assert "1 jogador na partida" in one and "jogador(es)" not in one
    _named(web("203.0.113.11"), "Bia").post("/join", data={"name": "Bia"})
    two = _named(web("203.0.113.12"), "Cris").get("/").text
    assert "2 jogadores na partida" in two


def test_unknown_page_is_a_friendly_404_with_a_way_back(web):
    r = web().get("/nao-existe")
    assert r.status_code == 404 and 'href="/"' in r.text and "<html" in r.text


def test_name_gate_is_not_titled_as_bingo(web):
    html = web().get("/").text
    assert "<title>Bingo da Live</title>" not in html and "<title>Jogos da Live</title>" in html
    assert "<h1>Jogos da Live</h1>" in html
