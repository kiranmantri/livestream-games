"""R3: replay catalogue on /admin/falacias — send a link, watch the job, start a game from a ready replay, retry, delete."""
from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from app import admin as A
from app import livestream as L
from app import replaycollector as RC
from app import replays as R
from app import sessions as S
from app import texts as T
from app.db import connect
from app.main import create_app
from tests.test_admin_falacias import PW, basic

VID = "abcdefghijk"
VID2 = "lmnopqrstuv"
LIVE_ID = "Ko_lIgMJudY"
HORIZONTAL = L.StreamInfo(LIVE_ID, "Debate horizontal", 1920, 1080)
Q = "/admin/falacias"
HX = {"hx-request": "true"}


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users, replay_events, replays CASCADE")
    yield c
    c.execute("TRUNCATE game_sessions, users, replay_events, replays CASCADE")
    c.close()


@pytest.fixture
def woke(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(RC, "wake", lambda: calls.append(1))
    return calls


@pytest.fixture
def adm(conn, test_db_url, monkeypatch, woke):
    monkeypatch.setenv("ADMIN_PASSWORD", PW)
    monkeypatch.delenv("FALACIAS_VIDEO_ID", raising=False)
    app = create_app(test_db_url, live=L.LiveStream(resolver=lambda: None, video_resolver=lambda v: None, ttl=3600, background=False))
    with TestClient(app, client=("203.0.113.99", 50000), follow_redirects=False) as c:
        yield c


def csrf():
    return A.replays_token(PW)


def make_ready(conn, vid=VID, title="Debate longo", duration_s=33500):
    for kind, off in (("transcript", 1000), ("chat", 0)):
        conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES (%s,%s,%s,%s,%s)",
                     (vid, kind, off, "@a" if kind == "chat" else None, "texto"))
    R.register_ready(conn, vid)
    conn.execute("UPDATE replays SET title = %s, duration_s = %s WHERE video_id = %s", (title, duration_s, vid))


def post(adm, path, data=None, token=None, headers=None):
    return adm.post(path, data={"csrf": csrf() if token is None else token} | (data or {}), headers=basic() | (headers or {}))


def falacias_sessions(conn):
    return conn.execute("SELECT video_id, source_mode, status FROM game_sessions WHERE game_type = 'falacias'").fetchall()


# ------------------------------------------------------------------ page + auth
def test_every_replay_route_needs_the_admin_password(adm):
    assert adm.get(f"{Q}/replays").status_code == 401
    for path in ("add", "start", "delete", "retry"):
        assert adm.post(f"{Q}/replays/{path}", data={"csrf": csrf()}).status_code == 401


def test_every_replay_post_needs_the_csrf_token(adm, conn):
    make_ready(conn)
    for path, data in (("add", {"video": VID2}), ("start", {"video_id": VID}), ("delete", {"video_id": VID}), ("retry", {"video_id": VID})):
        assert post(adm, f"{Q}/replays/{path}", data, token="nope").status_code == 403
    assert post(adm, f"{Q}/replays/add", {"video": VID2}, token=A.start_token(PW)).status_code == 403     # another action's token
    assert R.get(conn, VID2) is None and falacias_sessions(conn) == [] and R.get(conn, VID) is not None






# ------------------------------------------------------------------ add
def test_adding_a_link_queues_it_and_wakes_the_collector(adm, conn, woke):
    r = post(adm, f"{Q}/replays/add", {"video": f"https://www.youtube.com/watch?v={VID}"})
    assert r.status_code == 303 and r.headers["location"] == Q
    assert R.get(conn, VID).status == "queued" and woke == [1]


def test_adding_with_htmx_returns_the_list_that_keeps_polling(adm, conn):
    r = post(adm, f"{Q}/replays/add", {"video": VID}, headers=HX)
    assert r.status_code == 200 and VID in r.text and T.AF_REPLAY_STATUS["queued"] in r.text
    assert 'hx-get="/admin/falacias/replays"' in r.text and "every 3s" in r.text


@pytest.mark.parametrize("bad", ["not a link", "https://evil.example/watch?v=abcdefghijk", ""])
def test_a_bad_link_is_refused_with_a_message(adm, conn, woke, bad):
    r = post(adm, f"{Q}/replays/add", {"video": bad}, headers=HX)
    assert T.AF_REPLAY_ERRORS["invalid_video"] in r.text and R.list_replays(conn) == [] and woke == []


def test_a_bad_link_without_htmx_shows_the_whole_page_with_the_error(adm):
    r = post(adm, f"{Q}/replays/add", {"video": "nope"})
    assert r.status_code == 200 and T.AF_REPLAY_ERRORS["invalid_video"] in r.text and "<html" in r.text.lower()


def test_already_ready_already_queued_and_full_queue_are_explained(adm, conn):
    make_ready(conn)
    assert T.AF_REPLAY_ERRORS["already_ready"] in post(adm, f"{Q}/replays/add", {"video": VID}, headers=HX).text
    post(adm, f"{Q}/replays/add", {"video": VID2})
    assert T.AF_REPLAY_ERRORS["already_running"] in post(adm, f"{Q}/replays/add", {"video": VID2}, headers=HX).text
    for i in range(R.MAX_QUEUED - 1):                       # VID2 is already queued
        R.submit(conn, f"zzzzzzzzzz{i}")
    assert T.AF_REPLAY_ERRORS["queue_full"] in post(adm, f"{Q}/replays/add", {"video": "qqqqqqqqqqq"}, headers=HX).text


# ------------------------------------------------------------------ the list
def test_the_list_shows_title_duration_counts_and_status(adm, conn):
    make_ready(conn, title="Debate longo", duration_s=33500)
    html = adm.get(f"{Q}/replays", headers=basic()).text
    assert "Debate longo" in html and "9h18" in html and "1" in html
    assert T.AF_REPLAY_STATUS["ready"] in html and f"https://www.youtube.com/watch?v={VID}" in html


def test_a_queued_video_without_title_shows_its_id(adm, conn):
    R.submit(conn, VID)
    html = adm.get(f"{Q}/replays", headers=basic()).text
    assert VID in html and T.AF_REPLAY_STATUS["queued"] in html


def test_titles_and_errors_are_escaped(adm, conn):
    make_ready(conn, title="<script>alert(1)</script>")
    R.submit(conn, VID2)
    R.claim_next(conn)
    R.mark_failed(conn, VID2, "<img src=x onerror=1>")
    html = adm.get(f"{Q}/replays", headers=basic()).text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html and "&lt;img" in html


def test_polling_runs_only_while_a_job_is_running(adm, conn):
    make_ready(conn)
    assert "hx-trigger" not in adm.get(f"{Q}/replays", headers=basic()).text
    R.submit(conn, VID2)
    assert "every 3s" in adm.get(f"{Q}/replays", headers=basic()).text
    R.claim_next(conn)
    assert "every 3s" in adm.get(f"{Q}/replays", headers=basic()).text


def test_a_failed_video_shows_its_error_and_a_retry_button_only(adm, conn):
    R.submit(conn, VID)
    R.claim_next(conn)
    R.mark_failed(conn, VID, "Legendas em português ainda não disponíveis.")
    html = adm.get(f"{Q}/replays", headers=basic()).text
    assert "Legendas em português ainda não disponíveis." in html and T.AF_REPLAY_STATUS["failed"] in html
    assert T.AF_REPLAY_RETRY in html and T.AF_REPLAY_START not in html and T.AF_REPLAY_DELETE in html


def test_buttons_per_status(adm, conn):
    make_ready(conn, VID)
    R.submit(conn, VID2)
    html = adm.get(f"{Q}/replays", headers=basic()).text
    ready_row = next(r for r in re.findall(r"<tr.*?</tr>", html, re.DOTALL) if VID in r)
    queued_row = next(r for r in re.findall(r"<tr.*?</tr>", html, re.DOTALL) if VID2 in r)
    assert T.AF_REPLAY_START in ready_row and T.AF_REPLAY_DELETE in ready_row
    assert T.AF_REPLAY_START not in queued_row and T.AF_REPLAY_DELETE not in queued_row and T.AF_REPLAY_RETRY not in queued_row


def test_no_start_button_while_a_falacias_game_is_active(adm, conn):
    make_ready(conn)
    S.start_session_as_admin(conn, game_type="falacias", video_id=VID, source_mode="replay")
    html = adm.get(f"{Q}/replays", headers=basic()).text
    assert T.AF_REPLAY_START not in html


# ------------------------------------------------------------------ start a game from a ready replay
def test_starting_from_a_ready_replay_follows_that_video(adm, conn):
    make_ready(conn)
    r = post(adm, f"{Q}/replays/start", {"video_id": VID})
    assert r.status_code == 303 and r.headers["location"] == Q
    assert falacias_sessions(conn) == [(VID, "replay", "active")]


def test_the_admin_pick_beats_a_horizontal_live(conn, test_db_url, monkeypatch, woke):
    monkeypatch.setenv("ADMIN_PASSWORD", PW)
    make_ready(conn)
    app = create_app(test_db_url, live=L.LiveStream(resolver=lambda: HORIZONTAL, video_resolver=lambda v: None, ttl=3600, background=False))
    with TestClient(app, client=("203.0.113.99", 50000), follow_redirects=False) as c:
        r = post(c, f"{Q}/replays/start", {"video_id": VID})
    assert r.status_code == 303 and falacias_sessions(conn) == [(VID, "replay", "active")]


@pytest.mark.parametrize("state", ["queued", "downloading", "importing", "failed", "missing"])
def test_only_a_ready_replay_can_start(adm, conn, state):
    if state != "missing":
        R.submit(conn, VID)
        if state != "queued":
            R.claim_next(conn)
        if state == "importing":
            R.mark_importing(conn, VID)
        if state == "failed":
            R.mark_failed(conn, VID, "x")
    r = post(adm, f"{Q}/replays/start", {"video_id": VID})
    assert r.status_code == 200 and T.AF_REPLAY_ERRORS["not_ready"] in r.text and falacias_sessions(conn) == []


def test_a_second_game_is_not_started(adm, conn):
    make_ready(conn, VID)
    make_ready(conn, VID2)
    post(adm, f"{Q}/replays/start", {"video_id": VID})
    r = post(adm, f"{Q}/replays/start", {"video_id": VID2})
    assert T.AF_REPLAY_ERRORS["session_active"] in r.text and falacias_sessions(conn) == [(VID, "replay", "active")]


def test_a_bad_video_id_is_not_ready(adm, conn):
    r = post(adm, f"{Q}/replays/start", {"video_id": "x'; drop table replays; --"})
    assert r.status_code == 200 and T.AF_REPLAY_ERRORS["not_ready"] in r.text and falacias_sessions(conn) == []


# ------------------------------------------------------------------ retry + delete
def test_retry_queues_a_failed_video_again(adm, conn, woke):
    R.submit(conn, VID)
    R.claim_next(conn)
    R.mark_failed(conn, VID, "x")
    r = post(adm, f"{Q}/replays/retry", {"video_id": VID}, headers=HX)
    assert r.status_code == 200 and R.get(conn, VID).status == "queued" and woke == [1]


def test_retry_of_a_ready_video_is_refused(adm, conn):
    make_ready(conn)
    r = post(adm, f"{Q}/replays/retry", {"video_id": VID}, headers=HX)
    assert T.AF_REPLAY_ERRORS["already_ready"] in r.text and R.get(conn, VID).status == "ready"


def test_delete_removes_the_replay_and_its_rows(adm, conn):
    make_ready(conn)
    r = post(adm, f"{Q}/replays/delete", {"video_id": VID}, headers=HX)
    assert r.status_code == 200 and R.get(conn, VID) is None
    assert conn.execute("SELECT count(1) FROM replay_events WHERE video_id=%s", (VID,)).fetchone()[0] == 0


def test_delete_without_htmx_redirects(adm, conn):
    make_ready(conn)
    r = post(adm, f"{Q}/replays/delete", {"video_id": VID})
    assert r.status_code == 303 and R.get(conn, VID) is None


def test_delete_is_refused_while_a_game_uses_it_and_while_it_runs(adm, conn):
    make_ready(conn)
    S.start_session_as_admin(conn, game_type="falacias", video_id=VID, source_mode="replay")
    assert T.AF_REPLAY_ERRORS["in_use"] in post(adm, f"{Q}/replays/delete", {"video_id": VID}, headers=HX).text
    assert R.get(conn, VID) is not None
    R.submit(conn, VID2)
    assert T.AF_REPLAY_ERRORS["busy"] in post(adm, f"{Q}/replays/delete", {"video_id": VID2}, headers=HX).text
    assert T.AF_REPLAY_ERRORS["not_found"] in post(adm, f"{Q}/replays/delete", {"video_id": "zzzzzzzzzzz"}, headers=HX).text


def test_every_replay_error_code_has_a_text():
    for code in ("invalid_video", "already_ready", "already_running", "queue_full", "busy", "in_use", "not_found",
                 "not_ready", "session_active", "bad_state", "generic"):
        assert T.AF_REPLAY_ERRORS[code]
    assert set(T.AF_REPLAY_STATUS) == {"queued", "downloading", "importing", "ready", "failed"}


def test_admin_auto_start_without_a_live_tells_to_pick_a_replay(adm, conn):
    r = adm.post(f"{Q}/start", data={"csrf": A.start_token(PW)}, headers=basic())
    assert r.status_code == 200 and T.AF_NO_SOURCE in r.text and falacias_sessions(conn) == []



# ------------------------------------------------------------------ gaps found by the mutation check
def test_retry_of_an_unknown_id_does_not_create_a_job(adm, conn, woke):
    r = post(adm, f"{Q}/replays/retry", {"video_id": "zzzzzzzzzzz"}, headers=HX)
    assert T.AF_REPLAY_ERRORS["not_found"] in r.text and R.get(conn, "zzzzzzzzzzz") is None and woke == []


def test_a_replay_with_rows_but_not_marked_ready_cannot_start(adm, conn):
    R.submit(conn, VID)
    R.claim_next(conn)
    R.mark_importing(conn, VID)
    for kind in ("transcript", "chat"):                                  # both tracks already loaded, the job has not finished
        conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES (%s,%s,1,NULL,'x')", (VID, kind))
    r = post(adm, f"{Q}/replays/start", {"video_id": VID})
    assert T.AF_REPLAY_ERRORS["not_ready"] in r.text and falacias_sessions(conn) == []


def test_polling_continues_while_importing(adm, conn):
    R.submit(conn, VID)
    R.claim_next(conn)
    R.mark_importing(conn, VID)
    assert "every 3s" in adm.get(f"{Q}/replays", headers=basic()).text


def test_a_ready_row_has_no_retry_button(adm, conn):
    make_ready(conn)
    assert T.AF_REPLAY_RETRY not in adm.get(f"{Q}/replays", headers=basic()).text


def test_the_link_text_falls_back_to_the_video_id(adm, conn):
    R.submit(conn, VID)
    assert f">{VID}</a>" in adm.get(f"{Q}/replays", headers=basic()).text


# ------------------------------------------------------------------ layout found in the browser QA
# (the replay panel is gone from the page: the start dialog replaced it, see tests/test_start_by_url.py)




def test_rows_carry_data_labels_for_the_phone_layout(adm, conn):
    make_ready(conn)
    html = adm.get(f"{Q}/replays", headers=basic()).text
    assert f'data-label="{T.AF_REPLAY_COLS[1]}"' in html and f'data-label="{T.AF_REPLAY_COLS[2]}"' in html
