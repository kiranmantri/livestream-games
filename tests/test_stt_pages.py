# ruff: noqa: F811
"""S2: the speech-to-text worker wired into the live Falácias game (feed.tick starts it, the page shows the transcript pane with its
label, a SEPARATE banner when it is down, the video above the panes). STT is off by default: with it off, nothing changes."""
from __future__ import annotations

import re

import pytest

from app import feed
from app import sessions as S
from app import sttworker as W
from app import texts as T
from app.db import connect
from tests.test_admin_falacias import fal  # noqa: F401
from tests.test_falacias_web import _env, conn  # noqa: F401
from tests.test_live_pages import HORIZONTAL, LIVE_ID, live_game, named, start  # noqa: F401
from tests.test_stream_source import VID

HX = {"hx-request": "true"}


@pytest.fixture
def stt_on(monkeypatch):
    """STT enabled for the page, but no thread and no whisper: ensure() is a spy, health is settable."""
    state = {"health": "ok", "ensure": []}
    monkeypatch.setattr(W, "enabled", lambda: True)
    monkeypatch.setattr(W, "ensure", lambda sid, vid: state["ensure"].append((sid, vid)))
    monkeypatch.setattr(W, "health", lambda sid: state["health"])
    return state


def game(test_db_url):
    tc, b = live_game(test_db_url)
    return tc, b


def add_lines(test_db_url, sid, *texts):
    c = connect(test_db_url)
    W.ingest(c, sid, [W.Line(f"0:{i}", t) for i, t in enumerate(texts)])
    c.close()


# ---------------------------------------------------------------- feed.tick starts the worker
def test_tick_of_a_live_session_asks_the_stt_worker_to_start(conn, stt_on):
    live = S.start_session_as_admin(conn, game_type="falacias", video_id=LIVE_ID, source_mode="live")
    feed.tick(conn, live.id)
    assert stt_on["ensure"] == [(live.id, LIVE_ID)]


def test_tick_of_a_replay_session_never_starts_it(conn, stt_on):
    s = S.start_session_as_admin(conn, game_type="falacias", video_id=VID, source_mode="replay")
    feed.tick(conn, s.id)
    assert stt_on["ensure"] == []


def test_tick_with_stt_off_is_harmless(conn, monkeypatch):
    monkeypatch.delenv("STT_PROVIDER", raising=False)
    live = S.start_session_as_admin(conn, game_type="falacias", video_id=LIVE_ID, source_mode="live")
    assert feed.tick(conn, live.id) == 0 and W.get(live.id) is None


# ---------------------------------------------------------------- the page
def test_with_stt_off_the_live_page_is_what_it_was(conn, test_db_url, monkeypatch):
    monkeypatch.delenv("STT_PROVIDER", raising=False)
    tc, b = game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        html = b.get("/falacias").text
    assert T.FAL_NO_TRANSCRIPT in html and "sttlabel" not in html
    assert 'id="sttbanner"' not in html and "vidbox" not in html


def test_with_stt_on_the_transcript_pane_has_its_label(conn, test_db_url, stt_on):
    tc, b = game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        html = b.get("/falacias").text
    assert 'id="pane-transcript"' in html and 'id="pane-chat"' in html
    assert re.search(rf'<span class="sttlabel"[^>]*>{re.escape(T.FAL_STT_LABEL)}</span>', html)
    assert f"youtube-nocookie.com/embed/{LIVE_ID}" in html and "vidbox" not in html, "the video is the first of three panels now"
    assert T.FAL_NO_TRANSCRIPT not in html and T.FAL_LIVE_HOW_TO_STT in html
    assert 'name="note"' in html, "the panel's message box is the fallback for a missed phrase"


def test_stored_transcript_lines_show_with_a_time_and_no_author(conn, test_db_url, stt_on):
    tc, b = game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        sid = S.get_active_session(conn, "falacias").id
        add_lines(test_db_url, sid, "todo mundo sabe que isso é verdade")
        html = b.get("/falacias").text
    pane = re.search(r'id="pane-transcript".*?</div>\s*</div>', html, re.DOTALL).group(0)
    assert "todo mundo sabe que isso é verdade" in pane and re.search(r"<time[^>]*>\[\d\d:\d\d:\d\d\]</time>", pane)


def test_the_stt_banner_is_separate_from_the_chat_banner(conn, test_db_url, stt_on):
    stt_on["health"] = "down"
    tc, b = game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        down = b.get("/falacias").text
        stt_on["health"] = "ok"
        up = b.get("/falacias").text
    assert re.search(r'<div[^>]*role="alert"[^>]*id="sttbanner"[^>]*>' + re.escape(T.FAL_STT_DOWN), down)
    assert T.FAL_CHAT_DOWN not in down and T.FEED_UNAVAILABLE not in down
    assert re.search(r'<div[^>]*id="sttbanner"[^>]*></div>', up) and T.FAL_STT_DOWN not in up


# ---------------------------------------------------------------- the poll
def test_poller_url_carries_the_transcript_state(conn, test_db_url, stt_on):
    tc, b = game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        html = b.get("/falacias").text
    assert re.search(r'hx-get="/falacias/feed\?after=\d+&(amp;)?st=ok&(amp;)?tr=ok"', html)


def test_poll_without_news_is_204_and_a_state_change_swaps_only_the_stt_banner(conn, test_db_url, stt_on):
    tc, b = game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        assert b.get("/falacias/feed?after=0&st=ok&tr=ok").status_code == 204
        stt_on["health"] = "down"
        r = b.get("/falacias/feed?after=0&st=ok&tr=ok")
        assert r.status_code == 200 and T.FAL_STT_DOWN in r.text and 'id="sttbanner"' in r.text and "hx-swap-oob" in r.text
        assert T.FAL_CHAT_DOWN not in r.text and "tr=down" in r.text
        stt_on["health"] = "ok"
        back = b.get("/falacias/feed?after=0&st=ok&tr=down")
        assert back.status_code == 200 and T.FAL_STT_DOWN not in back.text and 'id="sttbanner"' in back.text


def test_new_transcript_lines_arrive_through_the_poll(conn, test_db_url, stt_on):
    tc, b = game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        sid = S.get_active_session(conn, "falacias").id
        add_lines(test_db_url, sid, "linha nova da transcrição")
        r = b.get("/falacias/feed?after=0&st=ok&tr=ok")
    assert "linha nova da transcrição" in r.text and "beforeend:#pane-transcript" in r.text


def test_an_old_poller_without_tr_still_works(conn, test_db_url, stt_on):
    tc, b = game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        assert b.get("/falacias/feed?after=0&st=ok").status_code == 204


def test_with_stt_off_the_poll_never_mentions_the_banner(conn, test_db_url, monkeypatch):
    monkeypatch.delenv("STT_PROVIDER", raising=False)
    tc, b = game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        add_lines(test_db_url, S.get_active_session(conn, "falacias").id, "uma linha")
        r = b.get("/falacias/feed?after=0&st=ok")
    assert "sttbanner" not in r.text


# ---------------------------------------------------------------- a failing transcript must not block the game
def test_a_transcript_line_can_be_flagged_while_the_stt_is_down(conn, test_db_url, stt_on, fal):
    stt_on["health"] = "down"
    tc, b = game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        sid = S.get_active_session(conn, "falacias").id
        add_lines(test_db_url, sid, "então todo mundo sabe que é assim")
        eid, text = conn.execute("SELECT id, text FROM stream_events WHERE session_id = %s AND kind = 'transcript'", (sid,)).fetchone()
        r = b.post("/falacias/mark", data={"kind": "transcript", "start_id": eid, "start_off": 0, "end_id": eid,
                                          "end_off": len(text), "fallacy_id": fal["Espantalho"]})
    assert r.status_code == 200 and T.FAL_SENT in r.text
    assert conn.execute("SELECT kind, status FROM fallacy_marks").fetchall() == [("transcript", "pending")]


def test_the_typed_box_works_while_the_stt_is_down(conn, test_db_url, stt_on, fal):
    stt_on["health"] = "down"
    tc, b = game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        r = b.post("/falacias/mark", data={"note": "frase que o STT perdeu", "fallacy_id": fal["Espantalho"]})
    assert r.status_code == 200 and T.FAL_SENT in r.text


def test_css_styles_the_stt_label_and_a_collapsible_video_box():
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text()
    assert ".sttlabel" in css and ".vidbox" in css
    assert re.search(r"\.vidbox\s*\{[^}]*max-width", css), "the video must not take the whole page on wide screens"
    assert re.search(r"\.vidbox summary\s*\{[^}]*min-height:\s*44px", css), "tap target"
