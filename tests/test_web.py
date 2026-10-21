"""Task 7: web layer, exercised through the real ASGI app against a throwaway database."""
from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from app import cards as C
from app import scoring as G
from app import sessions as S
from app import texts as T
from app.db import connect
from app.main import create_app
from tests.test_cards import load_phrases

IP_A, IP_B, IP_C = "203.0.113.10", "203.0.113.11", "203.0.113.12"


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    load_phrases(c, n=60)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


@pytest.fixture
def web(conn, test_db_url):
    """Factory for browsers (each with its own cookie jar and source IP); one app/pool/lifespan."""
    app = create_app(test_db_url)
    with TestClient(app):                       # runs lifespan: opens the pool
        def browser(ip=IP_A):
            return TestClient(app, client=(ip, 50000), follow_redirects=False)
        yield browser


def cells_of(html: str) -> list[str]:
    """Texts of the 16 phrase buttons, in board order."""
    return [re.sub(r"<[^>]+>", "", re.sub(r"<small[^>]*>.*?</small>", "", m)).strip() for m in re.findall(r'<button[^>]*class="cell(?: (?:x)?long)?"[^>]*>(.*?)</button>', html, re.DOTALL)]


def start(b, name="Ana"):
    r = b.post("/start", data={"name": name})
    assert r.status_code == 303, r.text
    return r


# ---------------------------------------------------------------- entry
def test_home_without_session_offers_start(web):
    r = web().get("/")
    assert r.status_code == 200 and T.START_BUTTON in r.text and 'action="/start"' in r.text
    assert 'action="/join"' not in r.text and "set-cookie" not in r.headers


def test_first_visitor_starts_and_gets_card(web, conn):
    b = web()
    r = start(b)
    cookie = r.headers["set-cookie"].lower()
    assert "lg_token=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    page = b.get("/")
    assert page.status_code == 200 and "Ana" in page.text
    assert len(cells_of(page.text)) == 16                              # new games are 4x4: all phrases, no free cell
    assert page.text.count('class="cell free"') == 0 and "--cols: 4" in page.text
    assert f"{T.MARKED}: 0/16" in page.text
    assert conn.execute("SELECT count(*) FROM bingo_cards").fetchone()[0] == 1


def test_board_matches_the_card_stored_in_db(web, conn):
    b = web()
    start(b)
    db_cells = conn.execute("SELECT cells FROM bingo_cards").fetchone()[0]
    assert cells_of(b.get("/").text) == [c["text"] for c in db_cells]


def test_same_card_on_every_reload_and_in_other_tab(web):
    b = web()
    start(b)
    first = cells_of(b.get("/").text)
    assert all(cells_of(b.get("/").text) == first for _ in range(3))


def test_second_visitor_sees_join_not_start(web):
    start(web(IP_A))
    r = web(IP_B).get("/")
    assert T.JOIN_BUTTON in r.text and 'action="/join"' in r.text and 'action="/start"' not in r.text
    assert "1 jogador" in r.text


def test_second_start_is_refused_and_creates_nothing(web, conn):
    start(web(IP_A))
    r = web(IP_B).post("/start", data={"name": "Bia"})
    assert r.status_code == 200 and T.ERRORS["session_already_active"] in r.text
    assert 'action="/join"' in r.text and "set-cookie" not in r.headers
    assert conn.execute("SELECT count(*) FROM game_sessions").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1


def test_join_gets_own_different_card(web, conn):
    a, b = web(IP_A), web(IP_B)
    start(a)
    assert b.post("/join", data={"name": "Bia"}).status_code == 303
    assert conn.execute("SELECT count(*) FROM bingo_cards").fetchone()[0] == 2
    assert cells_of(a.get("/").text) != cells_of(b.get("/").text)


@pytest.mark.parametrize("dup", ["Ana", "ana", " ANA "])
def test_duplicate_name_rejected_with_message(web, dup):
    start(web(IP_A))
    r = web(IP_B).post("/join", data={"name": dup})
    assert r.status_code == 200 and T.ERRORS["name_taken"] in r.text and "set-cookie" not in r.headers


@pytest.mark.parametrize("bad", ["", "   ", "x" * 21, "bad\x00name"])
def test_invalid_names_rejected(web, bad):
    r = web().post("/start", data={"name": bad})
    assert r.status_code == 200 and "set-cookie" not in r.headers
    assert T.ERRORS["invalid_name"] in r.text or "required" in r.text


def test_ip_limit_enforced_on_join(web, monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "2")
    start(web(IP_A))
    assert web(IP_A).post("/join", data={"name": "B"}).status_code == 303
    r = web(IP_A).post("/join", data={"name": "C"})
    assert r.status_code == 200 and "limite 2" in r.text


def test_name_is_html_escaped(web):
    b = web()
    start(b, "<b>x</b>")
    html = b.get("/").text
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html


def test_stranger_on_same_ip_does_not_inherit_the_player(web):
    a = web(IP_A)
    start(a)
    stranger = web(IP_A)                       # same IP, no cookie
    page = stranger.get("/")
    assert T.JOIN_BUTTON in page.text and "Ana" not in page.text


def test_forwarded_ip_only_trusted_from_configured_proxy(web, conn, monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXIES", IP_C)
    spoof = web(IP_A)                           # NOT a trusted proxy: header must be ignored
    spoof.post("/start", data={"name": "Ana"}, headers={"x-forwarded-for": "198.51.100.77"})
    proxied = web(IP_C)                         # trusted proxy: header honoured
    proxied.post("/join", data={"name": "Bia"}, headers={"x-forwarded-for": "198.51.100.88"})
    ips = dict(conn.execute("SELECT name, host(ip) FROM users").fetchall())
    assert ips == {"Ana": IP_A, "Bia": "198.51.100.88"}


# ---------------------------------------------------------------- marking
def test_tap_marks_and_unmarks_and_persists(web, conn):
    b = web()
    start(b)
    r = b.post("/mark/3")
    assert r.status_code == 200 and f"{T.MARKED}: 1/16" in r.text
    assert r.text.count('aria-pressed="true"') == 1
    assert 'hx-post="/mark/3"' in r.text and 'id="game-board"' in r.text
    assert f"{T.MARKED}: 1/16" in b.get("/").text                      # survives reload
    assert conn.execute("SELECT cell_index FROM bingo_marks").fetchall() == [(3,)]
    r = b.post("/mark/3")
    assert f"{T.MARKED}: 0/16" in r.text and r.text.count('aria-pressed="true"') == 0


@pytest.mark.parametrize("idx", [16, 25, -1, 99])
def test_bad_cells_return_400(web, idx):
    b = web()
    start(b)
    assert b.post(f"/mark/{idx}").status_code == 400


def test_mark_without_cookie_is_401_and_changes_nothing(web, conn):
    start(web(IP_A))
    r = web(IP_B).post("/mark/3")
    assert r.status_code == 401 and conn.execute("SELECT count(*) FROM bingo_marks").fetchone()[0] == 0


def test_players_cannot_mark_each_others_cards(web, conn):
    a, b = web(IP_A), web(IP_B)
    start(a)
    b.post("/join", data={"name": "Bia"})
    a.post("/mark/5")
    assert f"{T.MARKED}: 0/16" in b.get("/").text and f"{T.MARKED}: 1/16" in a.get("/").text


# ---------------------------------------------------------------- leaderboard / end of game
def test_leaderboard_ranks_and_marks_me(web):
    a, b = web(IP_A), web(IP_B)
    start(a)
    b.post("/join", data={"name": "Bia"})
    for i in (0, 1, 2):
        b.post(f"/mark/{i}")
    a.post("/mark/4")
    html = b.get("/leaderboard").text
    assert re.findall(r'<span class="nm">(.*?)</span>', html) == [f"Bia ({T.YOU})", "Ana"]
    assert re.findall(r'<span class="pts">(\d+)</span>', html) == ["3", "1"]
    assert 'class="me"' in html and 'hx-trigger="every 5s"' in html


def test_game_over_flow(web, conn):
    a, b = web(IP_A), web(IP_B)
    start(a)
    b.post("/join", data={"name": "Bia"})
    a.post("/mark/0")
    b.post("/mark/0")
    b.post("/mark/1")
    sid = S.get_active_session(conn).id
    G.finish_session(conn, sid)                                         # admin ends the game
    # polling clients are told to reload; late taps are refused
    assert b.get("/leaderboard").headers.get("hx-refresh") == "true"
    r = b.post("/mark/2")
    assert r.status_code == 401 and r.headers.get("hx-refresh") == "true"   # ended session = no longer a player
    assert conn.execute("SELECT count(*) FROM bingo_marks").fetchone()[0] == 3
    # reload shows the results
    html = b.get("/").text
    assert T.GAME_OVER in html and "Bia" in html and "2 casas" in html
    assert T.YOUR_RESULT.format(n=2, rank=1) in html
    assert T.YOUR_RESULT.format(n=1, rank=2) in a.get("/").text


def test_new_game_after_old_one_ended(web, conn):
    a = web(IP_A)
    start(a)
    G.finish_session(conn, S.get_active_session(conn).id)
    c = web(IP_C)
    assert c.post("/start", data={"name": "Caio"}).status_code == 303      # a new session may start
    old = a.get("/")                                                       # Ana's old cookie: no longer a player
    assert T.JOIN_BUTTON in old.text
    assert a.post("/join", data={"name": "Ana"}).status_code == 303        # same name is fine in a new session
    assert C.get_card(conn, S.get_active_session(conn).id, S.find_player(conn, a.cookies.get("lg_token"))[0].id)


def test_ended_game_results_are_shown_while_no_new_game_runs(web, conn):
    a = web(IP_A)
    start(a)
    G.finish_session(conn, S.get_active_session(conn).id)
    html = a.get("/").text
    assert T.GAME_OVER in html and T.NO_WINNER in html


def test_mark_race_with_game_end_returns_409_refresh(web, monkeypatch):
    """Window between 'cookie is valid' and 'toggle': the game ends -> 409 + reload header."""
    from app import marks as M
    b = web()
    start(b)

    def boom(*a, **k):
        raise M.SessionNotActive("ended just now")
    monkeypatch.setattr(M, "toggle_mark", boom)
    r = b.post("/mark/3")
    assert r.status_code == 409 and r.headers.get("hx-refresh") == "true"


# ---------------------------------------------------------------- misc
def test_healthz_and_static_assets(web):
    b = web()
    assert b.get("/healthz").text == "ok"
    assert b.get("/app.css").status_code == 200
    img = b.get("/center.jpg")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert b.get("/htmx.min.js").status_code == 200


def test_page_has_no_external_script_dependencies(web):
    html = web().get("/").text
    assert "cdn.jsdelivr" not in html and "unpkg" not in html


def test_not_enough_phrases_shows_friendly_error(web, conn):
    conn.execute("UPDATE phrases SET enabled = false")
    r = web().post("/start", data={"name": "Ana"})
    assert r.status_code == 200 and T.ERRORS["no_phrases"] in r.text


# ---------------------------------------------------------------- time of each mark (HH:MM:SS)
HMS = r"\d{2}:\d{2}:\d{2}"


def test_marked_cell_shows_time_and_unmark_removes_it(web, conn):
    b = web()
    start(b)
    assert "class=\"at\"" not in b.get("/").text
    html = b.post("/mark/3").text
    shown = re.findall(rf'<small class="at">({HMS})</small>', html)
    assert len(shown) == 1
    stored = conn.execute("SELECT marked_at FROM bingo_marks").fetchone()[0]
    from app import timefmt
    assert shown[0] == timefmt.hms(stored)                      # what is shown == what is recorded
    assert re.findall(rf'<small class="at">({HMS})</small>', b.get("/").text) == shown   # survives reload
    assert 'class="at"' not in b.post("/mark/3").text           # unmarked: no time


def test_each_cell_shows_its_own_time(web, conn):
    b = web()
    start(b)
    b.post("/mark/1")
    b.post("/mark/2")
    conn.execute("UPDATE bingo_marks SET marked_at = '2026-10-19 20:00:00+00' WHERE cell_index = 1")
    conn.execute("UPDATE bingo_marks SET marked_at = '2026-10-19 20:05:09+00' WHERE cell_index = 2")
    assert re.findall(rf'<small class="at">({HMS})</small>', b.get("/").text) == ["17:00:00", "17:05:09"]   # America/Sao_Paulo (UTC-3)


def test_display_timezone_is_configurable(monkeypatch):
    from datetime import UTC, datetime

    from app import timefmt
    t = datetime(2026, 10, 19, 20, 5, 9, tzinfo=UTC)
    assert timefmt.hms(t) == "17:05:09"
    monkeypatch.setenv("TIMEZONE", "UTC")
    assert timefmt.hms(t) == "20:05:09"
    monkeypatch.setenv("TIMEZONE", "America/Toronto")
    assert timefmt.hms(t) == "16:05:09"
    assert timefmt.hms(None) == "-"
