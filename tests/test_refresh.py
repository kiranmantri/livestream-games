# ruff: noqa: F811
"""Visual refresh v1: app bar + tabs with icons + user menu, bottom tab bar on phones, progress bar on my card, live video in the
Falácias page, picker drawer, semantic admin buttons. Server-rendered structure is tested here; the look itself is checked in a browser."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import texts as T
from app.db import connect
from app.main import create_app
from tests.test_cards import load_phrases
from tests.test_falacias_web import _env, conn  # noqa: F401
from tests.test_live_pages import HORIZONTAL, LIVE_ID, live_game, named, start  # noqa: F401
from tests.test_stream_source import make_live, page_for

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
CSS = (STATIC / "app.css").read_text()
ADMIN_JS = (STATIC / "admin.js").read_text()
HX = {"hx-request": "true"}


@pytest.fixture
def bconn(test_db_url):
    c = connect(test_db_url)
    load_phrases(c, n=60)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks, revoked_marks CASCADE")
    c.close()


@pytest.fixture
def me(bconn, test_db_url):
    app = create_app(test_db_url)
    with TestClient(app):
        b = TestClient(app, client=("203.0.113.10", 50000), follow_redirects=False)
        b.post("/name", data={"name": "Zé Maria", "next": "/"})
        yield b


# ------------------------------------------------------------------ app bar, tabs, user menu
def test_name_gate_is_a_centred_hero_with_no_app_bar(me):
    html = TestClient(me.app, follow_redirects=False).get("/").text
    assert 'class="hero"' in html and "appbar" not in html and 'id="menu"' not in html


def test_pages_after_the_gate_have_one_app_bar_with_brand_tabs_and_user_menu(me):
    html = me.get("/").text
    bar = re.search(r'<header class="appbar">.*?</header>', html, re.DOTALL)
    assert bar, "app bar"
    b = bar.group(0)
    assert re.search(r'<a[^>]*class="brand"[^>]*href="/"|<a[^>]*href="/"[^>]*class="brand"', b) and T.SITE_TITLE in b
    assert 'id="menu"' in b and b.index('class="brand"') < b.index('id="menu"') < b.index('class="usermenu"')
    assert html.count('id="menu"') == 1 and html.count("<header") == 1


def test_each_tab_has_an_icon_a_label_and_a_one_line_subtitle(me):
    menu = re.search(r'<nav[^>]*id="menu".*?</nav>', me.get("/").text, re.DOTALL).group(0)
    tabs = re.findall(r"<a [^>]*>.*?</a>", menu, re.DOTALL)
    assert len(tabs) == 2
    for tab, label, sub in zip(tabs, (T.MENU_BINGO, T.MENU_FALACIAS), (T.MENU_BINGO_SUB, T.MENU_FALACIAS_SUB), strict=True):
        assert 'class="ico"' in tab and label in tab and f'<small class="sub">{sub}</small>' in tab
    assert 'aria-current="page"' in tabs[0] and 'aria-current="page"' not in tabs[1]


def test_menu_fragment_keeps_icons_and_subtitles(me):
    frag = me.get("/menu?cur=falacias").text
    assert frag.count('class="ico"') == 2 and frag.count('class="sub"') == 2


def test_user_menu_shows_the_name_and_change_name_for_the_current_page(me):
    html = me.get("/falacias").text
    um = re.search(r'<details class="usermenu".*?</details>', html, re.DOTALL).group(0)
    assert "Zé Maria" in um and 'href="/name?next=/falacias"' in um
    um2 = re.search(r'<details class="usermenu".*?</details>', me.get("/").text, re.DOTALL).group(0)
    assert 'href="/name?next=/"' in um2


def test_user_menu_name_is_escaped(bconn, test_db_url):
    app = create_app(test_db_url)
    with TestClient(app):
        b = TestClient(app, client=("203.0.113.11", 50000), follow_redirects=False)
        b.post("/name", data={"name": "<b>x</b>", "next": "/"})
        html = b.get("/").text
    assert "<b>x</b>" not in html.split('class="usermenu"')[1].split("</details>")[0]


# ------------------------------------------------------------------ Bingo: progress on my card
def test_my_card_has_a_progress_bar_that_follows_the_marks(me):
    me.post("/start", data={"name": "Zé Maria"})
    html = me.get("/").text
    bar = re.search(r'<[^>]*role="progressbar"[^>]*>', html).group(0)
    assert 'aria-valuenow="0"' in bar and 'aria-valuemax="16"' in bar
    r = me.post("/mark/3", headers=HX)
    assert 'role="progressbar"' in r.text and 'aria-valuenow="1"' in r.text and "width:6%" in r.text.replace(" ", "")


# ------------------------------------------------------------------ Falácias: live video replaces the empty transcript pane
def test_live_game_shows_the_video_where_the_empty_transcript_pane_was(conn, test_db_url):
    tc, b = live_game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        html = b.get("/falacias").text
    assert re.search(rf'<div class="pane video[^"]*".*?youtube-nocookie\.com/embed/{LIVE_ID}', html, re.DOTALL)
    assert 'id="pane-transcript"' not in html and 'id="pane-chat"' in html
    assert T.FAL_NO_TRANSCRIPT in html                                       # the explanation stays, as a caption


def test_replay_game_keeps_both_text_panes_and_has_no_embed(conn, test_db_url):
    tc, b = page_for(test_db_url, make_live(None))
    with tc:
        start(named(b, "Ana"), "Ana")
        html = b.get("/falacias").text
    assert 'id="pane-transcript"' in html and 'id="pane-chat"' in html and "youtube-nocookie" not in html


# ------------------------------------------------------------------ CSS layer (structure only)
def test_phones_get_a_fixed_bottom_tab_bar_and_content_makes_room():
    block = re.search(r"@media \(max-width: 760px\) \{[^@]*?\.appbar \.menu[^}]*position:\s*fixed[^}]*bottom:\s*0[^}]*\}", CSS, re.DOTALL)
    assert block, "bottom tab bar on phones"
    assert re.search(r"@media \(max-width: 760px\) \{[^@]*main\s*\{[^}]*padding-bottom:", CSS, re.DOTALL)


def test_picker_is_a_side_drawer_on_desktop_and_the_panes_make_room():
    assert re.search(r"@media \(min-width: 761px\) \{[^@]*main\.fal \.picker\s*\{[^}]*position:\s*fixed[^}]*right:\s*0", CSS, re.DOTALL)
    assert ":has(.picker:not([hidden]))" in CSS


def test_motion_is_small_and_respects_reduced_motion():
    assert "@keyframes pop" in CSS and re.search(r"\.cell\[aria-pressed=true\]\s*\{[^}]*animation:\s*pop", CSS)
    assert re.search(r"@media \(prefers-reduced-motion: reduce\)", CSS)


def test_focus_is_visible_and_admin_verdicts_show_their_keys():
    assert ":focus-visible" in CSS
    for cls, key in (("correct", "C"), ("wrong", "E"), ("spam", "S")):
        assert re.search(rf'\.vbtn\.{cls}::after\s*\{{[^}}]*content:\s*"{key}"', CSS)


def test_palette_has_the_depth_levels_and_the_live_accent():
    for token in ("--surface-raised", "--amber", "--shadow"):
        assert token in CSS


def test_admin_tab_title_shows_the_pending_count():
    assert "document.title" in ADMIN_JS and "pendingcount" in ADMIN_JS


def test_phone_app_bar_has_no_backdrop_filter_or_the_fixed_tab_bar_anchors_to_it():
    """A backdrop-filter (like transform/filter) makes the element the containing block of position:fixed children: the bottom tab bar
    would be drawn at the TOP, on top of the brand. Found in a real browser; the phone rules must switch it off."""
    phone = re.search(r"@media \(max-width: 760px\) \{[^@]*?\.appbar\s*\{[^}]*backdrop-filter:\s*none[^}]*\}", CSS, re.DOTALL)
    assert phone


# ------------------------------------------------------------------ admin shell (same language as the player pages)
from tests.test_admin_group3 import basic, bweb, running  # noqa: F401


def test_every_admin_page_has_the_app_bar_with_icons_brand_and_a_link_to_the_site(bweb, running):
    for path in ("/admin", "/admin/falacias", "/admin/phrases", "/admin/fallacies"):
        html = bweb.get(path, headers=basic()).text
        assert re.search(r'<header class="appbar admin"', html), path
        assert re.search(r'<a[^>]*class="brand"[^>]*href="/admin"|<a[^>]*href="/admin"[^>]*class="brand"', html), path
        assert T.ADMIN_BRAND in html and T.ADMIN_VIEW_SITE in html
        assert re.search(r'<a[^>]*href="/"[^>]*class="viewsite"|<a[^>]*class="viewsite"[^>]*href="/"', html), path
        nav = re.search(r"<nav[^>]*admintabs.*?</nav>", html, re.DOTALL).group(0)
        assert nav.count('class="ico"') == 4, "one icon per tab"
        assert html.count('aria-current="page"') == 1


def test_admin_status_pill_has_a_live_dot_only_while_the_game_runs(bweb, running, conn):
    html = bweb.get("/admin", headers=basic()).text
    assert re.search(r'class="pill on"[^>]*>\s*<span class="livedot"', html)
    conn.execute("UPDATE game_sessions SET status='ended', ended_at=now() WHERE id=%s", (running.id,))
    assert "livedot" not in bweb.get("/admin", headers=basic()).text


def test_admin_css_phone_tabs_sticky_offset_and_table_polish():
    assert re.search(r"@media \(max-width: 760px\)[^@]*\.appbar\.admin \.admingroup\s*\{[^}]*display:\s*none", CSS, re.DOTALL)
    assert re.search(r"#statusbar\s*\{[^}]*top:\s*5\d+px", CSS), "status bar must sit below the sticky app bar, not under it"
    assert "table.users tbody tr:hover" in CSS and ".livedot" in CSS
