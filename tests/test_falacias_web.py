"""Task 3: menu + the two panes of "Encontre as Falácias", through the real ASGI app on a throwaway DB."""
from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from app import feed
from app import sessions as S
from app import texts as T
from app.db import connect
from app.main import create_app
from tests.test_cards import load_phrases

IP_A, IP_B, IP_C = "203.0.113.10", "203.0.113.11", "203.0.113.12"
VID = "vid1"
ROWS = [("transcript", 1_000, "fala um"), ("chat", 2_000, "chat um"), ("transcript", 3_000, "fala dois <b>x</b>"),
        ("chat", 4_000, "chat dois"), ("transcript", 90_000, "fala tarde")]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("FALACIAS_SPEED", "FALACIAS_START_AT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FALACIAS_VIDEO_ID", VID)
    monkeypatch.setenv("MAX_USERS_PER_IP", "50")
    feed._last_tick.clear()


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users, replay_events, phrases, bingo_cards, bingo_marks CASCADE")
    load_phrases(c, n=60)
    for kind, off, text in ROWS:
        c.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES (%s,%s,%s,%s,%s)",
                  (VID, kind, off, "@au" if kind == "chat" else None, text))
    yield c
    c.execute("TRUNCATE game_sessions, users, replay_events, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


@pytest.fixture
def web(conn, test_db_url):
    app = create_app(test_db_url)
    with TestClient(app):
        def browser(ip=IP_A):
            return TestClient(app, client=(ip, 50000), follow_redirects=False)
        yield browser


def age(conn, seconds):
    conn.execute("UPDATE game_sessions SET created_at = created_at - make_interval(secs => %s) WHERE game_type='falacias'",
                 (seconds,))
    feed._last_tick.clear()


def named(browser, name="Zed"):
    browser.cookies.set("lg_name", name)
    return browser


def start(b, name="Ana"):
    r = b.post("/falacias/start", data={"name": name})
    assert r.status_code == 303, r.text
    return r


def lines(html: str, kind: str) -> list[str]:
    pane = re.search(rf'id="pane-{kind}".*?(?=<div class="pane |<div id="feedpoll"|$)', html, re.DOTALL)
    return re.findall(r'<span class="tx">(.*?)</span>', pane.group(0) if pane else "")


# ================================================================ entry
def test_home_asks_for_the_name_first(web):
    r = web().get("/falacias")
    assert r.status_code == 200 and T.NAME_HEADING in r.text and 'action="/name"' in r.text
    assert 'id="menu"' not in r.text and 'action="/falacias/start"' not in r.text


def test_home_without_session_offers_start_with_the_remembered_name(web):
    r = named(web(), "Zed").get("/falacias")
    assert r.status_code == 200 and T.FAL_START_HEADING in r.text and 'action="/falacias/start"' in r.text
    assert 'name="name"' not in r.text and "Zed" in r.text and 'id="menu"' in r.text


def test_start_sets_only_the_falacias_cookie_and_shows_both_panes(web, conn):
    a = web()
    r = start(a)
    assert S.cookie_name("falacias") in r.headers["set-cookie"] and "lg_token=" not in r.headers["set-cookie"]
    assert a.get("/falacias").status_code == 200
    html = a.get("/falacias").text
    assert 'id="pane-transcript"' in html and 'id="pane-chat"' in html and "Ana" in html


def test_start_is_refused_without_feed_and_creates_no_session(web, conn):
    conn.execute("TRUNCATE replay_events")
    r = web().post("/falacias/start", data={"name": "Ana"})
    assert r.status_code == 200 and T.FEED_UNAVAILABLE in r.text and "set-cookie" not in r.headers
    assert S.get_active_session(conn, "falacias") is None


def test_second_visitor_sees_join_and_dup_name_goes_back_to_the_name_gate(web):
    start(web(IP_A))
    r = named(web(IP_B), "Bia").get("/falacias")
    assert T.FAL_JOIN_HEADING in r.text and 'action="/falacias/join"' in r.text
    r = named(web(IP_B), " ANA ").post("/falacias/join")
    assert r.status_code == 200 and T.ERRORS["name_taken"] in r.text and 'action="/name"' in r.text
    assert "set-cookie" not in r.headers
    assert named(web(IP_B), "Bia").post("/falacias/join").status_code == 303


def test_a_bingo_cookie_is_not_a_falacias_identity(web, conn):
    a = web()
    a.post("/start", data={"name": "Ana"})                      # Bingo only
    r = a.get("/falacias")
    assert r.status_code == 200 and T.FAL_START_HEADING in r.text    # no auto-join: a button to start


# ================================================================ panes
def test_page_shows_only_lines_that_are_due_and_escapes_text(web, conn):
    a = web(); start(a)
    age(conn, 10)
    html = a.get("/falacias").text
    assert lines(html, "transcript") == ["fala um", "fala dois &lt;b&gt;x&lt;/b&gt;"]
    assert lines(html, "chat") == ["chat um", "chat dois"]
    assert "fala tarde" not in html and "<b>x</b>" not in html


def test_authors_are_outside_the_selectable_text_span(web, conn):
    a = web(); start(a); age(conn, 10)
    assert '<span class="au">@au</span><span class="tx">chat um</span>' in a.get("/falacias").text


def test_page_does_not_show_lines_before_their_time(web, conn):
    a = web(); start(a)
    assert lines(a.get("/falacias").text, "transcript") == []


# ================================================================ feed poll
def test_poll_returns_204_when_nothing_is_new(web, conn):
    a = web(); start(a)
    assert a.get("/falacias/feed?after=0&st=ok").status_code == 204


def test_poll_appends_new_lines_per_pane_out_of_band(web, conn):
    a = web(); start(a)
    age(conn, 10)
    r = a.get("/falacias/feed?after=0&st=ok")
    assert r.status_code == 200
    assert 'hx-swap-oob="beforeend:#pane-transcript"' in r.text and 'hx-swap-oob="beforeend:#pane-chat"' in r.text
    for kind, want, other in (("transcript", "fala dois", "chat dois"), ("chat", "chat dois", "fala dois")):
        block = re.search(rf'hx-swap-oob="beforeend:#pane-{kind}">(.*?)</div></div>|hx-swap-oob="beforeend:#pane-{kind}">(.*)',
                          r.text, re.DOTALL)
        assert want in block.group(0) and other not in block.group(0).split('hx-swap-oob="beforeend:#pane-' + (
            "chat" if kind == "transcript" else "transcript"))[0]
    assert "fala um" in r.text and "chat dois" in r.text and "fala tarde" not in r.text
    last = int(re.search(r"after=(\d+)&", r.text).group(1))
    assert last == feed.last_event_id(conn, S.get_active_session(conn, "falacias").id)
    assert a.get(f"/falacias/feed?after={last}&st=ok").status_code == 204        # nothing is sent twice


def test_poll_sends_only_lines_after_the_cursor(web, conn):
    a = web(); start(a); age(conn, 10)
    first = int(re.search(r"after=(\d+)&", a.get("/falacias/feed?after=0&st=ok").text).group(1))
    age(conn, 100)
    r = a.get(f"/falacias/feed?after={first}&st=ok")
    assert "fala tarde" in r.text and "fala um" not in r.text


def test_poll_reports_a_feed_that_went_down_and_back(web, conn):
    a = web(); start(a)
    conn.execute("DELETE FROM replay_events WHERE kind = 'chat'")
    r = a.get("/falacias/feed?after=0&st=ok")
    assert r.status_code == 200 and T.FEED_UNAVAILABLE in r.text and 'id="feedbanner"' in r.text
    assert 'st=down' in r.text
    assert a.get("/falacias/feed?after=0&st=down").status_code == 204          # state unchanged
    for kind, off, text in ROWS:
        if kind == "chat":
            conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES (%s,%s,%s,%s,%s)",
                         (VID, kind, off, "@au", text))
    r = a.get("/falacias/feed?after=0&st=down")
    assert r.status_code == 200 and T.FEED_UNAVAILABLE not in r.text and "st=ok" in r.text


def test_poll_without_identity_refreshes_the_page(web):
    r = web().get("/falacias/feed?after=0&st=ok")
    assert r.headers.get("hx-refresh") == "true"


def test_poll_of_a_bingo_player_is_not_accepted(web, conn):
    start(web(IP_B))                                             # a Falácias session exists
    a = web()
    a.post("/start", data={"name": "Ana"})
    assert a.get("/falacias/feed?after=0&st=ok").headers.get("hx-refresh") == "true"


def test_poll_of_an_ended_session_refreshes_to_the_results(web, conn):
    a = web(); start(a)
    S.end_session(conn, S.get_active_session(conn, "falacias").id, None)
    assert a.get("/falacias/feed?after=0&st=ok").headers.get("hx-refresh") == "true"
    assert T.FAL_GAME_OVER in a.get("/falacias").text


# ================================================================ one name, both games
def test_the_name_is_asked_once_for_both_games(web, conn):
    a = web()
    a.post("/name", data={"name": "Ana", "next": "/"})
    a.post("/start")                                            # Bingo, name from the cookie
    html = a.get("/falacias").text
    assert T.NAME_HEADING not in html and "Ana" in html and 'id="menu"' in html


def test_opening_the_other_game_never_joins_by_itself(web, conn):
    start(web(IP_B), "Zeca")                                    # a Falácias session exists
    a = web()
    a.post("/start", data={"name": "Ana"})                      # Ana plays Bingo
    r = a.get("/falacias")
    assert r.status_code == 200 and 'action="/falacias/join"' in r.text
    assert conn.execute("SELECT count(*) FROM users WHERE name = 'Ana'").fetchone()[0] == 1
    assert a.post("/falacias/join").status_code == 303           # one click, remembered name
    assert conn.execute("SELECT count(*) FROM users WHERE name = 'Ana'").fetchone()[0] == 2


def test_bingo_tab_does_not_join_by_itself_either(web, conn):
    web(IP_B).post("/start", data={"name": "Zeca"})
    a = web()
    start(a, "Ana")
    r = a.get("/")
    assert r.status_code == 200 and 'action="/join"' in r.text
    assert conn.execute("SELECT count(*) FROM users WHERE name = 'Ana'").fetchone()[0] == 1


# ================================================================ menu
def test_menu_is_on_both_pages_with_the_current_game_marked(web):
    html = named(web()).get("/").text
    assert 'href="/falacias"' in html and 'href="/"' in html
    fh = named(web()).get("/falacias").text
    assert re.search(r'<a [^>]*href="/falacias"[^>]*aria-current="page"|<a [^>]*aria-current="page"[^>]*href="/falacias"', fh)


def test_menu_fragment_marks_running_games(web, conn):
    assert 'class="dot"' not in web().get("/menu?cur=bingo").text
    start(web(IP_B))
    html = web().get("/menu?cur=bingo").text
    assert html.count('class="dot"') == 1
    assert re.search(r'href="/falacias"[^>]*>[^<]*<span class="dot"', html) or "dot" in html.split('href="/falacias"')[1]


def test_menu_fragment_ignores_an_unknown_current_game(web):
    r = web().get("/menu?cur=<script>")
    assert r.status_code == 200 and "<script>" not in r.text


def test_a_bingo_token_in_the_falacias_cookie_shows_no_results(web, conn):
    a = web()
    a.post("/start", data={"name": "Ana"})
    token = a.cookies.get("lg_token")
    S.end_session(conn, S.get_active_session(conn, "bingo").id, None)
    f = web(IP_B)
    f.cookies.set("lg_token_falacias", token)
    assert T.GAME_OVER not in f.get("/falacias").text and T.FAL_GAME_OVER not in f.get("/falacias").text


def test_menu_fragment_never_echoes_an_unknown_game(web):
    assert "cur=bingo" in web().get("/menu?cur=zzz").text and "zzz" not in web().get("/menu?cur=zzz").text


def test_menu_fragment_does_not_reload_itself_in_a_loop(web):
    """The swapped-in fragment must poll only on a timer: a `load` trigger would re-request /menu forever."""
    assert 'hx-trigger="load, every 30s"' in named(web()).get("/").text           # the page asks once on load
    frag = web().get("/menu?cur=bingo").text
    assert 'hx-trigger="every 30s"' in frag and "load" not in frag.split('hx-trigger="')[1].split('"')[0]
