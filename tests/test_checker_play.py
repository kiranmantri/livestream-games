# ruff: noqa: F811
"""FC4: the player side of "Verdade ou Lorota": join, the three panels (video | chat | transcript), the slide panel with a rating,
"my claims" with the AI answer, the leaderboard, the end screen, and the feed that a checker session shares with Falácias."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import checker as C
from app import checkworker as W
from app import feed
from app import sessions as S
from app import texts as T
from tests.test_checker_admin_web import (  # noqa: F401
    IP_P,
    LIVE,
    OLD,
    _env,
    admin,
    app,
    basic,
    conn,
    result,
    visitor,
)


@pytest.fixture(autouse=True)
def _slow_replay(monkeypatch):
    monkeypatch.setenv("FALACIAS_SPEED", "0.001")        # a replay session releases (almost) nothing by itself: the tests insert their own lines


def play(app, conn, name="Ana", ip=IP_P, video=OLD, mode="replay"):
    """A visitor who joined the running checker game (started here when none runs; a replay of OLD unless told otherwise)."""
    if S.get_active_session(conn, "checker") is None:
        S.start_session_as_admin(conn, game_type="checker", video_id=video, source_mode=mode)
    c = visitor(app, name)
    c.headers["x-forwarded-for"] = ip
    r = c.post("/checker/join")
    assert r.status_code == 303, r.text
    return c


def lines(conn, kind="chat", texts=("primeira linha", "a Terra tem 6 mil anos")):
    sid = S.get_active_session(conn, "checker").id
    return [conn.execute("INSERT INTO stream_events (session_id, kind, source_key, author, text, fed_at) VALUES (%s,%s,%s,%s,%s, now()) RETURNING id",
                         (sid, kind, f"k{i}-{t}", "fulano" if kind == "chat" else None, t)).fetchone()[0] for i, t in enumerate(texts)]


def send(c, **over):
    data = {"kind": "", "start_id": "", "start_off": "", "end_id": "", "end_off": "", "guess": "", "note": ""} | {k: str(v) for k, v in over.items()}
    return c.post("/checker/mark", data=data)


def sel(eid, text, a=0, b=None):
    return {"kind": "chat", "start_id": eid, "start_off": a, "end_id": eid, "end_off": len(text) if b is None else b}


def claims(conn):
    return conn.execute("SELECT kind, quote, guess FROM fact_claims ORDER BY created_at").fetchall()


# ---------------------------------------------------------------- join
def test_a_visitor_can_join_the_running_game(app, conn):
    S.start_session_as_admin(conn, game_type="checker")
    v = visitor(app)
    html = v.get("/checker").text
    assert 'action="/checker/join"' in html and "Zed" in html and "/admin" not in html and "csrf" not in html
    r = v.post("/checker/join")
    assert r.status_code == 303 and r.headers["location"] == "/checker" and "lg_token_checker" in r.headers["set-cookie"]
    assert S.count_players(conn, S.get_active_session(conn, "checker").id) == 1


def test_there_is_no_join_form_while_no_game_runs_and_a_post_does_nothing(app, conn):
    v = visitor(app)
    assert 'action="/checker/join"' not in v.get("/checker").text
    r = v.post("/checker/join")
    assert r.status_code in (200, 303) and "lg_token_checker" not in r.headers.get("set-cookie", "")


def test_joining_twice_resumes_the_same_player(app, conn):
    c = play(app, conn)
    n = conn.execute("SELECT count(*) FROM users").fetchone()[0]
    c.post("/checker/join")
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == n


def test_a_taken_name_goes_back_to_the_name_form(app, conn):
    play(app, conn, "Ana")
    other = visitor(app, "ana")
    other.headers["x-forwarded-for"] = "10.9.9.9"
    r = other.post("/checker/join")
    assert T.ERRORS["name_taken"] in r.text and "lg_token_checker" not in r.headers.get("set-cookie", "")


# ---------------------------------------------------------------- the game page
def test_a_player_sees_panes_a_hidden_panel_and_the_rating_scale(app, conn):
    c = play(app, conn)
    html = c.get("/checker").text
    assert 'id="pane-chat"' in html and 'id="pane-transcript"' in html and 'id="picker"' in html
    picker = re.search(r'<form[^>]*id="picker".*?</form>', html, re.DOTALL).group(0)
    assert '/checker/mark' in picker
    values = [int(m) for m in re.findall(r'<input[^>]*name="guess"[^>]*value="(-?\d+)"', picker)]
    assert values == list(range(-5, 6)), values
    assert 'name="note"' in picker and 'maxlength="500"' in picker and 'id="pk-quote"' not in picker
    assert "/falacias" not in picker


def test_the_rating_scale_is_one_line_of_outlined_icons_with_the_numbers_below(app, conn):
    """💩 x5, ⭕ (0), ⭐ x5 in ONE row, numbers -5..5 under them (no '+'); outlined until picked."""
    picker = re.search(r'<form[^>]*id="picker".*?</form>', play(app, conn).get("/checker").text, re.DOTALL).group(0)
    scale = re.search(r'<div[^>]*class="scale"[^>]*>.*?</div>\s*<div[^>]*class="sendrow"', picker, re.DOTALL).group(0)
    labels = re.findall(r'<label[^>]*class="(lv[^"]*)"[^>]*>(.*?)</label>', scale, re.DOTALL)
    assert [c for c, _ in labels] == [f"lv m{-v}" for v in range(-5, 0)] + ["lv z"] + [f"lv p{v}" for v in range(1, 6)]
    kinds = [re.search(r'<svg[^>]*class="ic (\w+)"', body).group(1) for _, body in labels]
    assert kinds == ["poop"] * 5 + ["zero"] + ["star"] * 5
    nums = [re.search(r'<span class="n">([^<]+)</span>', body).group(1) for _, body in labels]
    assert nums == [str(v) for v in range(-5, 6)]
    assert "…" not in picker                                        # the old "💩 -5 … +5 ⭐" hint is gone


def test_picking_a_level_lights_the_icons_from_the_centre_out_to_it():
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text()
    block = css.split("/* Verdade ou Lorota: rating scale */", 1)[1].split("/* end rating scale */", 1)[0]
    assert re.search(r"\.scale \{[^}]*grid-template-columns: repeat\(11,", block)            # one straight line
    assert re.search(r"\.ckpicker \.scale \.ic \{[^}]*fill: none", block)                    # outlined by default
    for n in range(1, 6):
        lit = ", ".join(f".m{i} .ic" for i in range(1, n + 1))
        assert f'.ckpicker .scale:has(input[value="-{n}"]:checked) :is({lit})' in block, n
        lit = ", ".join(f".p{i} .ic" for i in range(1, n + 1))
        assert f'.ckpicker .scale:has(input[value="{n}"]:checked) :is({lit})' in block, n
    assert '.ckpicker .scale:has(input[value="0"]:checked) .z .ic' in block
    assert re.search(r"\.ckpicker \.scale input \{[^}]*opacity: 0", block)                     # radio hidden, still focusable


def test_the_page_loads_the_shared_panel_script_which_closes_the_panel_after_a_send(app, conn):
    html = play(app, conn).get("/checker").text
    assert 'src="/falacias.js"' in html
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "falacias.js").read_text()
    assert "'ck:sent'" in js and "if (field('filter'))" in js


def test_a_live_game_shows_video_chat_and_transcript(app, conn):
    c = play(app, conn, video=LIVE, mode="live")
    html = c.get("/checker").text
    assert f"youtube-nocookie.com/embed/{LIVE}" in html and "panes three" in html


def test_a_game_without_video_has_no_video_panel(app, conn):
    html = play(app, conn).get("/checker").text
    assert "youtube-nocookie" not in html


def test_lines_of_the_session_are_in_the_panes_and_the_poll_appends_new_ones(app, conn):
    c = play(app, conn)
    a, b = lines(conn)
    html = c.get("/checker").text
    assert "a Terra tem 6 mil anos" in html and f'data-id="{a}"' in html
    (late,) = lines(conn, texts=("linha tardia",))
    assert c.get(f"/checker/feed?after={b}&st=ok").status_code in (200, 204)
    r = c.get(f"/checker/feed?after={b}&st=ok")
    assert "linha tardia" in r.text and f'data-id="{late}"' in r.text and "/checker/feed?after=" in r.text


def test_the_poll_is_204_when_nothing_changed(app, conn):
    c = play(app, conn)
    ids = lines(conn)
    assert c.get(f"/checker/feed?after={ids[-1]}&st=ok").status_code == 204


def test_the_poll_of_a_stranger_or_an_ended_game_refreshes_the_page(app, conn):
    assert visitor(app).get("/checker/feed?after=0&st=ok").headers.get("hx-refresh") == "true"


# ---------------------------------------------------------------- sending a claim
def test_a_selected_passage_with_a_guess_becomes_a_claim_and_wakes_the_worker(app, conn, monkeypatch):
    woken = []
    monkeypatch.setattr(W, "wake", lambda: woken.append(1))
    c = play(app, conn)
    _, e = lines(conn)
    r = send(c, **sel(e, "a Terra tem 6 mil anos"), guess=-4, note="a Terra tem 6 mil anos")
    assert r.status_code == 200 and T.CK_SENT in r.text and "ck:sent" in r.headers.get("hx-trigger", "")
    assert claims(conn) == [("chat", "a Terra tem 6 mil anos", -4)] and woken
    assert conn.execute("SELECT status FROM fact_checks").fetchone()[0] == "queued"


def test_an_empty_text_box_with_a_selection_still_sends_the_anchored_claim(app, conn):
    c = play(app, conn)
    _, e = lines(conn)
    send(c, **sel(e, "a Terra tem 6 mil anos"), guess=3)
    assert claims(conn) == [("chat", "a Terra tem 6 mil anos", 3)]


def test_an_edited_text_is_sent_as_a_typed_claim_without_context(app, conn):
    c = play(app, conn)
    _, e = lines(conn)
    send(c, **sel(e, "a Terra tem 6 mil anos"), guess=-5, note="A Terra tem seis mil anos de idade")
    assert claims(conn) == [("spoken", "A Terra tem seis mil anos de idade", -5)]


def test_text_without_a_selection_is_a_typed_claim(app, conn):
    c = play(app, conn)
    r = send(c, guess=0, note="  os Annunakis   existem ")
    assert T.CK_SENT in r.text and claims(conn) == [("spoken", "os Annunakis existem", 0)]


def test_a_guess_is_required_and_must_be_in_range(app, conn):
    c = play(app, conn)
    for bad in ("", "6", "-6", "x", "1.5"):
        assert T.CK_ERRORS["bad_guess"] in send(c, guess=bad, note="uma frase").text
    assert claims(conn) == []


def test_nothing_to_send_is_refused(app, conn):
    c = play(app, conn)
    assert T.CK_ERRORS["empty"] in send(c, guess=2).text and claims(conn) == []


def test_a_typed_claim_over_280_characters_is_refused(app, conn):
    c = play(app, conn)
    assert T.CK_ERRORS["note_too_long"] in send(c, guess=1, note="x" * 281).text and claims(conn) == []


def test_limits_are_explained(app, conn, monkeypatch):
    monkeypatch.setattr(C, "RATE_LIMIT_S", 30)
    c = play(app, conn)
    send(c, guess=1, note="primeira")
    assert T.CK_ERRORS["rate_limited"].format(n=30) in send(c, guess=1, note="segunda").text
    monkeypatch.setattr(C, "RATE_LIMIT_S", 0)
    assert T.CK_ERRORS["duplicate_claim"] in send(c, guess=1, note="PRIMEIRA").text
    monkeypatch.setattr(C, "MAX_CLAIMS_PER_PLAYER", 2)
    send(c, guess=1, note="terceira")
    assert T.CK_ERRORS["claim_limit_reached"].format(max=2) in send(c, guess=1, note="quarta").text


def test_a_stranger_cannot_send(app, conn):
    S.start_session_as_admin(conn, game_type="checker")
    assert send(visitor(app), guess=1, note="oi").status_code == 401 and claims(conn) == []


def test_a_bingo_player_is_not_a_checker_player(app, conn):
    S.start_session_as_admin(conn, game_type="checker")
    c = visitor(app, "Bia")
    c.post("/start")
    assert send(c, guess=1, note="oi").status_code == 401


def test_after_the_game_ended_the_page_refreshes(app, conn):
    c = play(app, conn)
    C.finish_session(conn, S.get_active_session(conn, "checker").id)
    assert send(c, guess=1, note="oi").headers.get("hx-refresh") == "true"


def test_a_garbage_selection_is_a_friendly_error(app, conn):
    c = play(app, conn)
    lines(conn)
    r = send(c, kind="chat", start_id="abc", start_off="0", end_id="1", end_off="3", guess=1)
    assert T.CK_ERRORS["bad_selection"] in r.text and claims(conn) == []


# ---------------------------------------------------------------- my claims and the board
def test_my_claims_show_waiting_then_the_ai_answer_with_sources_and_points(app, conn):
    c = play(app, conn)
    send(c, guess=-4, note="os Annunakis existem")
    side = c.get("/checker/side").text
    assert "os Annunakis existem" in side and T.CK_STATE_WAITING in side
    ck = C.claim_next_check(conn)
    C.complete_check(conn, ck.id, result(score=-3))
    side = c.get("/checker/side").text
    assert T.CK_VERDICTS["false"] in side and "Não existem provas." in side and 'href="https://example.org/a"' in side
    assert "Fonte &lt;A&gt;" in side and "<A>" not in side
    assert "💩💩💩" in side and "6 pts" in side             # guess -4 vs -3: 4 + 2 for the right sign


def test_a_claim_waiting_for_the_admin_says_so_and_dismissed_ones_earn_nothing(app, conn):
    c = play(app, conn)
    send(c, guess=2, note="frase um")
    ck = C.claim_next_check(conn)
    C.complete_check(conn, ck.id, result(confidence="low"))
    assert T.CK_STATE_ADMIN in c.get("/checker/side").text
    C.admin_dismiss(conn, S.get_active_session(conn, "checker").id, ck.id)
    assert T.CK_STATE_DISMISSED in c.get("/checker/side").text


def test_the_admin_score_replaces_the_ai_one_on_the_players_page(app, conn):
    c = play(app, conn)
    send(c, guess=1, note="frase dois")
    ck = C.claim_next_check(conn)
    C.complete_check(conn, ck.id, result(verdict="true", score=2))
    C.admin_set_score(conn, S.get_active_session(conn, "checker").id, ck.id, 5)
    assert "⭐⭐⭐⭐⭐" in c.get("/checker/side").text


def test_other_players_claims_are_in_their_own_tab_and_never_in_mine(app, conn):
    a = play(app, conn, "Ana", ip="10.1.1.1")
    b = visitor(app, "Bia")
    b.headers["x-forwarded-for"] = "10.1.1.2"
    b.post("/checker/join")
    send(a, guess=1, note="frase da Ana")
    send(b, guess=-2, note="frase da Bia")
    board = b.get("/checker/board", headers={"hx-request": "true"}).text
    assert "Ana" in board and "Bia" in board

    def panels(html):
        mine, others = re.split(r'data-ckpanel="others"', html.split('data-ckpanel="mine"', 1)[1], maxsplit=1) if 'data-ckpanel="mine"' in html else ("", "")
        return mine, others

    mine, others = panels(b.get("/checker/side").text)
    assert "frase da Bia" in mine and "frase da Ana" not in mine
    assert "frase da Ana" in others and "frase da Bia" not in others
    assert 'class="who ckwho"' in others and ">Ana<" in others and 'class="pairs claims"' in others
    assert T.CK_MINE in b.get("/checker/side").text and T.CK_OTHERS in b.get("/checker/side").text
    mine, others = panels(a.get("/checker/side").text)
    assert "frase da Ana" in mine and "frase da Bia" in others and "frase da Ana" not in others


def test_others_tab_says_so_when_nobody_else_sent_anything(app, conn):
    a = play(app, conn, "Ana", ip="10.1.1.1")
    send(a, guess=1, note="so eu")
    assert T.CK_NO_OTHERS in a.get("/checker/side").text


def test_side_of_a_stranger_refreshes(app, conn):
    assert visitor(app).get("/checker/side").headers.get("hx-refresh") == "true"


# ---------------------------------------------------------------- claims as small expandable cards
def card_of(html):
    m = re.search(r'<details[^>]*class="[^"]*ckcard[^"]*"[^>]*>(.*?)</details>', html, re.DOTALL)
    assert m, "no expandable card"
    summary = re.search(r"<summary[^>]*>(.*?)</summary>", m.group(1), re.DOTALL).group(1)
    return summary, m.group(1).replace(summary, "")


def test_a_claim_is_a_collapsed_card_with_the_phrase_the_score_and_icons_in_the_summary(app, conn):
    c = play(app, conn)
    send(c, guess=-4, note="os Annunakis existem")
    ck = C.claim_next_check(conn)
    C.complete_check(conn, ck.id, result(score=-3))
    side = c.get("/checker/side").text
    summary, body = card_of(side)
    assert "os Annunakis existem" in summary and "💩💩💩💩" in summary and "💩💩💩" in summary and "6 pts" in summary
    assert "<details" in side and " open" not in re.search(r"<details[^>]*>", side).group(0)
    assert "Não existem provas." in body and 'href="https://example.org/a"' in body and "Não existem provas." not in summary


def test_a_waiting_claim_card_shows_the_state_and_my_guess_in_the_summary(app, conn):
    c = play(app, conn)
    send(c, guess=2, note="frase pendente")
    summary, _ = card_of(c.get("/checker/side").text)
    assert "frase pendente" in summary and T.CK_STATE_WAITING in summary and "⭐⭐" in summary and "pts" not in summary


def test_each_card_has_a_stable_id_so_the_page_can_keep_it_open_across_polls(app, conn):
    c = play(app, conn)
    send(c, guess=2, note="frase um")
    cl = conn.execute("SELECT id FROM fact_claims").fetchone()[0]
    assert f'data-id="{cl}"' in c.get("/checker/side").text


def test_the_page_loads_the_script_that_keeps_open_cards_open(app, conn):
    html = play(app, conn).get("/checker").text
    assert 'src="/checker.js"' in html
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "checker.js").read_text()
    assert "htmx:beforeSwap" in js and "htmx:afterSwap" in js and "toggle" in js and len(js.splitlines()) < 40


def test_the_end_screen_uses_the_same_cards(app, conn):
    c = play(app, conn)
    send(c, guess=1, note="frase final")
    C.finish_session(conn, S.get_active_session(conn, "checker").id)
    assert "frase final" in card_of(c.get("/checker").text)[0]


# ---------------------------------------------------------------- the end screen
def test_the_end_screen_shows_the_winner_the_board_and_my_claims(app, conn):
    c = play(app, conn, "Ana", ip="10.1.1.1")
    send(c, guess=-3, note="uma mentira")
    ck = C.claim_next_check(conn)
    C.complete_check(conn, ck.id, result(score=-3))
    C.finish_session(conn, S.get_active_session(conn, "checker").id)
    html = c.get("/checker").text
    assert T.CK_GAME_OVER in html and "Ana" in html and "uma mentira" in html and 'id="picker"' not in html
    assert T.CK_WINNER.format(name="Ana", pts=7) in html
    assert '<main class="fal ck">' in html            # the wide page (1180 px): the default 560 px main squeezed the 3-column cards into each other


def test_the_end_screen_with_no_points_names_no_winner(app, conn):
    c = play(app, conn)
    C.finish_session(conn, S.get_active_session(conn, "checker").id)
    html = c.get("/checker").text
    assert T.CK_GAME_OVER in html and "winner" not in html


# ---------------------------------------------------------------- the shared feed (no second chat/STT worker)
def pair(conn, video=LIVE, mode="live"):
    f = S.start_session_as_admin(conn, game_type="falacias", video_id=video, source_mode=mode)
    ck = S.start_session_as_admin(conn, game_type="checker", video_id=video, source_mode=mode)
    return f, ck


@pytest.fixture
def workers(monkeypatch):
    seen = []
    monkeypatch.setattr(feed.chatworker, "ensure", lambda sid, vid: seen.append(("chat", sid, vid)))
    monkeypatch.setattr(feed.sttworker, "ensure", lambda sid, vid: seen.append(("stt", sid, vid)))
    return seen


def put(conn, sid, kind, key, text, author=None):
    conn.execute("INSERT INTO stream_events (session_id, kind, source_key, author, text, fed_at) VALUES (%s,%s,%s,%s,%s, now())", (sid, kind, key, author, text))


def texts(conn, sid):
    return [r[0] for r in conn.execute("SELECT text FROM stream_events WHERE session_id = %s AND kind IN ('chat','transcript') ORDER BY id", (sid,))]


def test_a_live_checker_session_mirrors_the_falacias_lines_and_starts_the_workers_once_on_the_falacias_session(conn, workers):
    f, ck = pair(conn)
    put(conn, f.id, "chat", "c1", "oi gente", "ze")
    put(conn, f.id, "transcript", "t1", "boa noite")
    feed.tick(conn, ck.id)
    assert texts(conn, ck.id) == ["oi gente", "boa noite"]
    assert ("chat", f.id, LIVE) in workers and ("stt", f.id, LIVE) in workers and not any(w[1] == ck.id for w in workers)


def test_mirroring_is_incremental_and_idempotent(conn, workers):
    f, ck = pair(conn)
    put(conn, f.id, "chat", "c1", "um")
    feed.tick(conn, ck.id)
    feed.tick(conn, ck.id)
    put(conn, f.id, "chat", "c2", "dois")
    feed.tick(conn, ck.id)
    assert texts(conn, ck.id) == ["um", "dois"]
    assert conn.execute("SELECT author FROM stream_events WHERE session_id = %s AND text = 'um'", (ck.id,)).fetchone() == (None,)


def test_a_checker_session_without_a_falacias_sibling_gets_nothing_and_reports_down(conn, workers):
    ck = S.start_session_as_admin(conn, game_type="checker", video_id=LIVE, source_mode="live")
    feed.tick(conn, ck.id)
    assert texts(conn, ck.id) == [] and feed.status(conn, ck.id) == "down"


def test_the_sibling_must_follow_the_same_video(conn, workers):
    f = S.start_session_as_admin(conn, game_type="falacias", video_id="Other_00001", source_mode="live")
    ck = S.start_session_as_admin(conn, game_type="checker", video_id=LIVE, source_mode="live")
    put(conn, f.id, "chat", "c1", "de outro video")
    feed.tick(conn, ck.id)
    assert texts(conn, ck.id) == []


def test_a_replay_checker_session_releases_the_replay_lines_on_its_own_clock(conn):
    ck = S.start_session_as_admin(conn, game_type="checker", video_id=OLD, source_mode="replay")
    n = feed.tick(conn, ck.id, feed.FeedConfig(video_id=OLD, speed=1000.0, start_at_ms=0))
    assert n == 2 and texts(conn, ck.id) == ["x", "x"]


def test_status_of_a_live_checker_session_follows_the_chat_worker_of_its_sibling(conn, workers, monkeypatch):
    f, ck = pair(conn)
    monkeypatch.setattr(feed.chatworker, "health", lambda sid: "down" if sid == f.id else "ok")
    assert feed.status(conn, ck.id) == "down"
    monkeypatch.setattr(feed.chatworker, "health", lambda sid: "ok")
    assert feed.status(conn, ck.id) == "ok"


def test_a_claim_on_a_mirrored_line_is_accepted(app, conn, workers):
    f, ck = pair(conn)
    put(conn, f.id, "chat", "c1", "a Terra tem 6 mil anos", "ze")
    c = visitor(app)
    c.headers["x-forwarded-for"] = "10.2.2.2"
    c.post("/checker/join")
    c.get("/checker")
    eid = conn.execute("SELECT id FROM stream_events WHERE session_id = %s", (ck.id,)).fetchone()[0]
    send(c, **sel(eid, "a Terra tem 6 mil anos"), guess=-4)
    assert claims(conn) == [("chat", "a Terra tem 6 mil anos", -4)]


def test_a_chat_that_is_down_refuses_selected_claims_but_not_typed_ones(app, conn, workers, monkeypatch):
    f, ck = pair(conn)
    put(conn, f.id, "chat", "c1", "uma frase", "ze")
    c = visitor(app)
    c.headers["x-forwarded-for"] = "10.2.2.3"
    c.post("/checker/join")
    c.get("/checker")
    eid = conn.execute("SELECT id FROM stream_events WHERE session_id = %s", (ck.id,)).fetchone()[0]
    monkeypatch.setattr(feed.chatworker, "health", lambda sid: "down")
    assert T.FAL_ERRORS["feed_down"] in send(c, **sel(eid, "uma frase"), guess=1).text
    assert T.CK_SENT in send(c, guess=1, note="frase digitada").text
