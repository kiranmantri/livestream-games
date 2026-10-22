# ruff: noqa: F811
"""Backlog group 3: admin navigation, status bar, auto-refresh, bigger buttons, Falácias queue filter/shortcuts/age, phone tables."""
from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import cards as C
from app import sessions as S
from app import texts as T
from app.main import create_app
from tests.test_admin_falacias import fal, game, send, sid_of, web  # noqa: F401
from tests.test_falacias_web import _env, conn  # noqa: F401

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
CSS = (STATIC / "app.css").read_text()
PW = "s3cret-pässword"
IP_ADMIN = "203.0.113.99"


def basic():
    return {"authorization": "Basic " + base64.b64encode(f"admin:{PW}".encode()).decode()}


# ------------------------------------------------------------------ Bingo admin
@pytest.fixture
def bweb(conn, test_db_url, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", PW)
    app = create_app(test_db_url)
    with TestClient(app):
        yield TestClient(app, client=(IP_ADMIN, 50000), follow_redirects=False)


@pytest.fixture
def running(conn):
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    C.assign_card(conn, s.id, p.id)
    _, bia, _ = S.join_session(conn, "Bia", "10.0.0.2")
    C.assign_card(conn, s.id, bia.id)
    return s


def test_admin_tabs_are_grouped_into_games_and_content(bweb, conn):
    html = bweb.get("/admin", headers=basic()).text
    nav = re.search(r'<nav[^>]*admintabs.*?</nav>', html, re.DOTALL).group(0)
    assert nav.count('class="admingroup"') == 2
    assert nav.index(T.ADMIN_GROUP_GAMES) < nav.index(T.ADMIN_TAB_BINGO) < nav.index(T.ADMIN_TAB_FALACIAS) \
        < nav.index(T.ADMIN_GROUP_CONTENT) < nav.index(T.ADMIN_TAB_PHRASES) < nav.index(T.ADMIN_TAB_FALLACIES)
    assert T.ADMIN_TAB_FALLACIES == "Lista de falácias"            # no longer confusable with the game tab


def test_bingo_status_bar_has_state_count_elapsed_and_finish_at_the_end(bweb, running):
    html = bweb.get("/admin", headers=basic()).text
    bar = re.search(r'<div[^>]*id="statusbar".*?action="/admin/finish".*?</form>', html, re.DOTALL)
    assert bar, "sticky status bar"
    b = bar.group(0)
    assert T.ADMIN_ACTIVE in b and 'id="playercount"' in b and re.search(r'<time[^>]*data-since="\d+"', b)
    assert 'action="/admin/finish"' in b and b.index('id="playercount"') < b.index('action="/admin/finish"')
    assert T.ADMIN_REFRESH in b                                    # "Atualizar" no longer hides below the fold
    assert 'id="updated"' in html and 'src="/admin.js"' in html


def test_users_table_polls_every_5s_keeping_the_sort_and_stops_when_ended(bweb, running, conn):
    html = bweb.get("/admin?sort=name&dir=desc", headers=basic()).text
    assert 'hx-get="/admin/live?sort=name&amp;dir=desc"' in html or 'hx-get="/admin/live?sort=name&dir=desc"' in html
    assert 'hx-trigger="every 5s"' in html
    from app import scoring as G
    G.finish_session(conn, running.id)
    assert "/admin/live" not in bweb.get("/admin", headers=basic()).text


def test_live_fragment_needs_auth_and_returns_table_and_oob_status(bweb, running):
    assert bweb.get("/admin/live").status_code == 401
    r = bweb.get("/admin/live?sort=name&dir=asc", headers=basic())
    assert r.status_code == 200 and 'id="users-panel"' in r.text
    assert re.search(r'id="playercount"[^>]*hx-swap-oob="true"|hx-swap-oob="true"[^>]*id="playercount"', r.text) and "Jogadores: 2" in r.text
    assert r.text.index("Ana") < r.text.index("Bia")


def test_live_fragment_refreshes_the_page_when_the_game_has_ended(bweb, running, conn):
    from app import scoring as G
    G.finish_session(conn, running.id)
    assert bweb.get("/admin/live", headers=basic()).headers.get("hx-refresh") == "true"


def test_users_table_cells_are_labelled_for_the_phone_card_layout(bweb, running):
    html = bweb.get("/admin", headers=basic()).text
    users = re.search(r'<table class="users stack".*?</table>', html, re.DOTALL)
    assert users and users.group(0).count("data-label=") >= 5 * 2


# ------------------------------------------------------------------ Falácias admin
def test_falacias_status_bar_and_queue_controls(web, game, conn, fal):
    send(game["ana"], conn, fal)
    html = game["admin"].get("/admin/falacias", headers=basic()).text
    assert 'id="statusbar"' in html and re.search(r'<time[^>]*data-since="\d+"', html)
    assert re.search(r'<input[^>]*id="onlypending"[^>]*checked|<input[^>]*checked[^>]*id="onlypending"', html)
    assert T.AF_SHORTCUTS in html and "only-pending" in html
    assert re.search(r'<time[^>]*class="age"[^>]*data-us="\d{13,}"', html) or re.search(r'<time[^>]*data-us="\d{13,}"[^>]*class="age"', html)


def test_polled_queue_items_carry_their_age_too(web, game, conn, fal):
    admin = game["admin"]
    send(game["ana"], conn, fal)
    r = admin.get("/admin/falacias/queue?after=0", headers=basic())
    assert 'class="age"' in r.text and "data-us=" in r.text


# ------------------------------------------------------------------ static files
def test_admin_js_has_filter_shortcuts_age_and_elapsed_and_guards_in_flight_requests():
    js = (STATIC / "admin.js").read_text()
    for needle in ("onlypending", "only-pending", "c: 'correct'", "e: 'wrong'", "s: 'spam'", "shiftKey", "htmx-request", "data-us", "data-since",
                   "INPUT", "TEXTAREA", "updated"):
        assert needle in js, needle


def test_css_big_targets_sticky_bar_and_phone_tables():
    block = CSS[CSS.index("/* admin (group 3)"):]
    for needle in ("#statusbar", "position: sticky", ".vbtn", "min-height: 44px", ".only-pending .qitem:not(.pending)",
                   "table.stack", "attr(data-label)", ".mcbtn", ".admingroup", ".qitem.current"):
        assert needle in block, needle
