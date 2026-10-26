# ruff: noqa: F811
"""Task 4: select a passage, choose a fallacy, submit (POST /falacias/mark), my pairs, leaderboard (GET /falacias/side)."""
from __future__ import annotations

import re

import pytest

from app import falacias as F
from app import feed
from app import sessions as S
from app import texts as T
from tests.test_falacias_web import IP_A, IP_B, ROWS, VID, _env, age, conn, named, start, web  # noqa: F401


@pytest.fixture
def fal(conn):
    """Two enabled fallacies in two categories and one disabled."""
    ids = {}
    for name, cat, desc, enabled in (("Espantalho", "Red herring", "Distorce o argumento <do outro>", True),
                                     ("Apelo à autoridade", "Relevância", "Cita alguém só pelo cargo", True),
                                     ("Falácia desativada", "Formais", "não aparece", False)):
        ids[name] = conn.execute("INSERT INTO fallacies (name, category, description, source, enabled) "
                                 "VALUES (%s,%s,%s,'manual',%s) RETURNING id", (name, cat, desc, enabled)).fetchone()[0]
    yield ids
    conn.execute("TRUNCATE game_sessions, users CASCADE")
    conn.execute("DELETE FROM fallacies")


def ev(conn, text):
    sid = S.get_active_session(conn, "falacias").id
    row = conn.execute("SELECT id, kind FROM stream_events WHERE session_id = %s AND text = %s", (sid, text)).fetchone()
    return row


def body(conn, fal, text="fala um", a=0, b=None, fallacy="Espantalho", **over):
    eid, kind = ev(conn, text)
    data = {"kind": kind, "start_id": eid, "start_off": a, "end_id": eid, "end_off": len(text) if b is None else b,
            "fallacy_id": fal[fallacy]}
    data.update(over)
    return data


def playing(web, conn, name="Ana", ip=IP_A):
    a = web(ip)
    start(a, name)
    age(conn, 10)
    a.get("/falacias")                       # ticks the feed: the lines are now in stream_events
    return a


def marks(conn):
    return conn.execute("SELECT quote, fallacy_id, status, counts, latency_ms FROM fallacy_marks ORDER BY created_at").fetchall()


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    monkeypatch.setattr(F, "RATE_LIMIT_S", 0)


# ================================================================ the page
def test_page_has_the_picker_with_enabled_fallacies_grouped_and_described(web, conn, fal):
    html = playing(web, conn).get("/falacias").text
    assert 'id="picker"' in html and 'hx-post="/falacias/mark"' in html
    assert "Espantalho" in html and "Apelo à autoridade" in html and "Falácia desativada" not in html
    assert "Red herring" in html and "Relevância" in html and "Formais" not in html
    assert 'title="Distorce o argumento &lt;do outro&gt;"' in html           # tooltip = description, escaped
    assert "CC BY-SA 4.0" in html and "pt.wikipedia.org" in html                # attribution wherever the list appears
    assert 'src="/falacias.js"' in html and 'id="side"' in html and 'hx-get="/falacias/side"' in html


def test_picker_starts_hidden_with_one_radio_per_fallacy(web, conn, fal):
    html = playing(web, conn).get("/falacias").text
    assert re.search(r'<form[^>]*id="picker"[^>]*hidden', html) or re.search(r'<form[^>]*hidden[^>]*id="picker"', html)
    assert len(re.findall(r'type="radio"[^>]*name="fallacy_id"|name="fallacy_id"[^>]*type="radio"', html)) == 2


# ================================================================ submit
def test_submit_stores_a_pair_with_a_server_made_quote(web, conn, fal):
    a = playing(web, conn)
    r = a.post("/falacias/mark", data=body(conn, fal, "fala um", 5, 7) | {"quote": "FORGED BY THE CLIENT"})
    assert r.status_code == 200 and "fal:sent" in r.headers.get("hx-trigger", "")
    assert marks(conn)[0][:4] == ("um", fal["Espantalho"], "pending", True)
    assert "FORGED" not in r.text


def test_submit_answers_with_my_pairs_and_the_leaderboard_out_of_band(web, conn, fal):
    a = playing(web, conn)
    r = a.post("/falacias/mark", data=body(conn, fal, "fala um"))
    assert 'id="side"' in r.text and 'hx-swap-oob="true"' in r.text
    assert "fala um" in r.text and "Espantalho" in r.text and T.FAL_PENDING in r.text
    assert "5,0 pontos" in a.get("/falacias/score").text and T.LEADERBOARD not in r.text     # the base 5 points, in the score line (no panel any more)


def test_submit_across_two_lines_of_one_pane(web, conn, fal):
    a = playing(web, conn)
    first, kind = ev(conn, "fala um")
    last, _ = ev(conn, "fala dois <b>x</b>")
    r = a.post("/falacias/mark", data={"kind": kind, "start_id": first, "start_off": 5, "end_id": last, "end_off": 9,
                                       "fallacy_id": fal["Espantalho"]})
    assert "fal:sent" in r.headers.get("hx-trigger", "")
    assert marks(conn)[0][0] == "um fala dois"


def test_submit_a_chat_selection(web, conn, fal):
    a = playing(web, conn)
    a.post("/falacias/mark", data=body(conn, fal, "chat um", 0, 4))
    assert marks(conn)[0][0] == "chat"


@pytest.mark.parametrize("over", [
    {"start_off": 3, "end_off": 3}, {"start_off": -1}, {"end_off": 999}, {"start_off": "x"}, {"start_id": ""},
    {"kind": "video"}, {"end_id": 10**30}, {"start_off": "1e3"}])
def test_bad_selections_are_refused_with_a_message_and_store_nothing(web, conn, fal, over):
    a = playing(web, conn)
    r = a.post("/falacias/mark", data=body(conn, fal, "fala um") | over)
    assert r.status_code == 200 and 'id="feedback"' in r.text and T.FAL_ERRORS["bad_selection"] in r.text
    assert "fal:sent" not in r.headers.get("hx-trigger", "") and marks(conn) == []


def test_missing_fields_do_not_crash(web, conn, fal):
    a = playing(web, conn)
    r = a.post("/falacias/mark", data={})
    assert r.status_code == 200 and T.FAL_ERRORS["empty"] in r.text and marks(conn) == []


def test_too_long_selection_message(web, conn, fal, monkeypatch):
    a = playing(web, conn)
    monkeypatch.setattr(F, "MAX_LINES", 1)
    first, kind = ev(conn, "fala um")
    last, _ = ev(conn, "fala dois <b>x</b>")
    r = a.post("/falacias/mark", data={"kind": kind, "start_id": first, "start_off": 0, "end_id": last, "end_off": 3,
                                       "fallacy_id": fal["Espantalho"]})
    assert T.FAL_ERRORS["selection_too_long"] in r.text and marks(conn) == []


def test_unknown_and_disabled_fallacies_are_refused(web, conn, fal):
    a = playing(web, conn)
    for bad in (fal["Falácia desativada"], 99999, "abc"):
        r = a.post("/falacias/mark", data=body(conn, fal) | {"fallacy_id": bad})
        assert T.FAL_ERRORS["unknown_fallacy"] in r.text or T.FAL_ERRORS["bad_selection"] in r.text
    assert marks(conn) == []


def test_duplicate_pair_message(web, conn, fal):
    a = playing(web, conn)
    a.post("/falacias/mark", data=body(conn, fal))
    r = a.post("/falacias/mark", data=body(conn, fal))
    assert T.FAL_ERRORS["duplicate_mark"] in r.text and len(marks(conn)) == 1
    assert a.post("/falacias/mark", data=body(conn, fal, fallacy="Apelo à autoridade")).status_code == 200
    assert len(marks(conn)) == 2                                              # same passage, other fallacy: fine


def test_rate_limit_message(web, conn, fal, monkeypatch):
    monkeypatch.setattr(F, "RATE_LIMIT_S", 10)
    a = playing(web, conn)
    a.post("/falacias/mark", data=body(conn, fal))
    r = a.post("/falacias/mark", data=body(conn, fal, "chat um"))
    assert T.FAL_ERRORS["rate_limited"].format(n=10) in r.text and len(marks(conn)) == 1


def test_no_identity_refreshes_the_page(web, conn, fal):
    playing(web, conn)
    r = web(IP_B).post("/falacias/mark", data=body(conn, fal))
    assert r.status_code == 401 and r.headers.get("hx-refresh") == "true"


def test_a_bingo_cookie_cannot_submit(web, conn, fal):
    playing(web, conn, "Ana", IP_A)
    b = web(IP_B)
    b.post("/start", data={"name": "Bia"})
    assert b.post("/falacias/mark", data=body(conn, fal)).status_code == 401 and marks(conn) == []


def test_after_the_session_ended_the_page_refreshes_and_nothing_is_stored(web, conn, fal):
    a = playing(web, conn)
    data = body(conn, fal)
    S.end_session(conn, S.get_active_session(conn, "falacias").id, None)
    r = a.post("/falacias/mark", data=data)
    assert r.headers.get("hx-refresh") == "true" and marks(conn) == []


def test_refused_while_the_feed_is_down(web, conn, fal):
    a = playing(web, conn)
    data = body(conn, fal)
    conn.execute("DELETE FROM replay_events WHERE kind = 'chat'")
    r = a.post("/falacias/mark", data=data)
    assert T.FEED_UNAVAILABLE in r.text and marks(conn) == []
    assert feed.status(conn, S.get_active_session(conn, "falacias").id) == "down"


def test_the_latency_is_measured_by_the_server(web, conn, fal):
    a = playing(web, conn)                      # the lines were fed ~7-9 s ago
    a.post("/falacias/mark", data=body(conn, fal, "fala um") | {"latency_ms": 1})
    assert 5_000 <= marks(conn)[0][4] <= 15_000


# ================================================================ my pairs + leaderboard
def test_side_shows_my_pairs_escaped_with_status_and_points(web, conn, fal):
    a = playing(web, conn)
    a.post("/falacias/mark", data=body(conn, fal, "fala dois <b>x</b>"))
    html = a.get("/falacias/side").text
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html
    assert "Espantalho" in html and T.FAL_PENDING in html and "5,0" in html


def test_side_reflects_the_admin_verdict(web, conn, fal):
    a = playing(web, conn)
    a.post("/falacias/mark", data=body(conn, fal))
    m = F.get_mark(conn, conn.execute("SELECT id FROM fallacy_marks").fetchone()[0])
    F.review_mark(conn, m.session_id, m.id, "correct")
    html = a.get("/falacias/side").text
    assert T.FAL_VERDICTS["correct"] in html and T.FAL_PENDING not in html
    assert F.fmt_points(F.points_of(F.get_mark(conn, m.id))) in html and F.points_of(F.get_mark(conn, m.id)) > 1000
    F.review_mark(conn, m.session_id, m.id, "spam")
    assert T.FAL_VERDICTS["spam"] in a.get("/falacias/side").text


def test_leaderboard_lists_everybody_ranked_and_marks_me(web, conn, fal):
    a = playing(web, conn, "Ana", IP_A)
    b = named(web(IP_B), "Bia")
    b.post("/falacias/join")
    a.post("/falacias/mark", data=body(conn, fal))
    html = b.get("/falacias/board", headers={"hx-request": "true"}).text
    assert html.index("Ana") < html.index("Bia")                                   # Ana has points, Bia none
    assert re.search(r'<li class="me"[^>]*>.*?Bia', html, re.DOTALL) and "Espantalho" not in html   # Ana's pair is not mine
    assert "Espantalho" not in b.get("/falacias/side").text


def test_side_is_per_player_and_needs_identity(web, conn, fal):
    a = playing(web, conn)
    a.post("/falacias/mark", data=body(conn, fal))
    assert web(IP_B).get("/falacias/side").headers.get("hx-refresh") == "true"
    c = named(web(IP_B), "Caio")
    c.post("/falacias/join")
    assert "fala um" not in c.get("/falacias/side").text


def test_game_page_embeds_my_pairs_so_a_reload_keeps_them(web, conn, fal):
    a = playing(web, conn)
    a.post("/falacias/mark", data=body(conn, fal))
    assert "fala um" in a.get("/falacias").text and VID == "vid1" and ROWS


def test_ended_results_still_show_the_leaderboard_points(web, conn, fal):
    a = playing(web, conn)
    a.post("/falacias/mark", data=body(conn, fal))
    S.end_session(conn, S.get_active_session(conn, "falacias").id, None)
    html = a.get("/falacias").text
    assert T.FAL_GAME_OVER in html and "Ana" in html and "5,0" in html


# ================================================================ gaps found by the mutation check
def test_submit_confirms_to_the_player(web, conn, fal):
    r = playing(web, conn).post("/falacias/mark", data=body(conn, fal))
    assert T.FAL_SENT in r.text


def test_the_returned_side_panel_replaces_the_page_one_out_of_band(web, conn, fal):
    r = playing(web, conn).post("/falacias/mark", data=body(conn, fal))
    assert re.search(r'<div[^>]*id="side"[^>]*hx-swap-oob="true"|<div[^>]*hx-swap-oob="true"[^>]*id="side"', r.text)


def test_the_side_panel_polls_every_five_seconds_and_my_points_are_in_my_pairs(web, conn, fal):
    a = playing(web, conn)
    a.post("/falacias/mark", data=body(conn, fal))
    html = a.get("/falacias/side").text
    assert 'hx-trigger="every 5s"' in html and 'hx-swap="outerHTML"' in html
    mine = html.split('class="pairs"')[1].split("</ol>")[0]
    assert ">5,0<" in mine and T.FAL_PENDING in mine


@pytest.mark.parametrize("field", ["start_id", "start_off", "end_off", "fallacy_id"])
def test_huge_numbers_are_a_bad_selection_not_a_crash(web, conn, fal, field):
    a = playing(web, conn)
    r = a.post("/falacias/mark", data=body(conn, fal) | {field: "9" * 40})
    assert r.status_code == 200 and "fal:sent" not in r.headers.get("hx-trigger", "") and marks(conn) == []
    assert T.FAL_ERRORS["unknown_fallacy" if field == "fallacy_id" else "bad_selection"] in r.text


def test_a_session_that_ends_between_the_checks_refreshes_the_page(web, conn, fal, monkeypatch):
    a = playing(web, conn)

    def boom(*args, **kwargs):
        raise F.SessionNotActive("ended meanwhile")
    monkeypatch.setattr(F, "submit_mark", boom)
    assert a.post("/falacias/mark", data=body(conn, fal)).headers.get("hx-refresh") == "true"
