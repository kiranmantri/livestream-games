# ruff: noqa: F811
"""Task 5b/5c: admin tab "Encontre as Falácias" (queue, verdicts, messages, board) and the player's message box."""
from __future__ import annotations

import base64
import re
import uuid

import pytest
from starlette.testclient import TestClient

from app import admin as A
from app import falacias as F
from app import host_messages as H
from app import sessions as S
from app import texts as T
from app.main import create_app
from tests.test_falacias_web import IP_A, IP_B, _env, age, conn, named, start  # noqa: F401

PW = "s3cret-pässword"
IP_ADMIN = "203.0.113.99"


def basic(pw=PW):
    return {"authorization": "Basic " + base64.b64encode(f"admin:{pw}".encode()).decode()}


@pytest.fixture
def web(conn, test_db_url, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", PW)
    monkeypatch.setattr(F, "RATE_LIMIT_S", 0)
    monkeypatch.setattr(H, "RATE_LIMIT_S", 0)
    app = create_app(test_db_url)
    with TestClient(app):
        def browser(ip=IP_A):
            return TestClient(app, client=(ip, 50000), follow_redirects=False)
        yield browser


@pytest.fixture
def fal(conn):
    conn.execute("TRUNCATE game_sessions, users CASCADE")
    conn.execute("DELETE FROM fallacies")                    # a crashed earlier run must not break this one
    ids = {}
    for name in ("Espantalho", "Ad hominem"):
        ids[name] = conn.execute("INSERT INTO fallacies (name, category, description, source, enabled) "
                                 "VALUES (%s,'C','d','manual',true) RETURNING id", (name,)).fetchone()[0]
    yield ids
    conn.execute("TRUNCATE game_sessions, users CASCADE")
    conn.execute("DELETE FROM fallacies")


H_ = {"hx-request": "true"}


def sid_of(conn):
    return S.get_active_session(conn, "falacias").id


def token(conn):
    return A.csrf_token(PW, sid_of(conn))


def play(web, conn, fal, name="Ana", ip=IP_A, first=False):
    c = web(ip)
    if first:
        start(c, name)
    else:
        named(c, name).post("/falacias/join")
    age(conn, 10)
    c.get("/falacias")
    return c


def send(c, conn, fal, text="fala um", a=0, b=None, fallacy="Espantalho", ev=None):
    row = conn.execute("SELECT id, kind FROM stream_events WHERE session_id = %s AND text = %s", (sid_of(conn), text)).fetchone()
    return c.post("/falacias/mark", data={"kind": row[1], "start_id": row[0], "start_off": a, "end_id": row[0],
                                          "end_off": len(text) if b is None else b, "fallacy_id": fal[fallacy]})


def mids(conn):
    return [r[0] for r in conn.execute("SELECT id FROM fallacy_marks ORDER BY created_at")]


def review(admin, conn, mid, verdict, group="0", tok=None, htmx=True, **over):
    data = {"session_id": str(sid_of(conn)), "mark_id": str(mid), "verdict": verdict, "group": group,
            "csrf": token(conn) if tok is None else tok} | over
    return admin.post("/admin/falacias/review", data=data, headers=basic() | (H_ if htmx else {}))


@pytest.fixture
def game(web, conn, fal):
    """A running Falácias session with Ana (starter) and Bia, lines fed, and an admin client."""
    ana = play(web, conn, fal, "Ana", IP_A, first=True)
    bia = play(web, conn, fal, "Bia", IP_B)
    return {"ana": ana, "bia": bia, "admin": web(IP_ADMIN)}


# ================================================================ auth + tabs
def test_admin_falacias_needs_credentials(web):
    for path in ("/admin/falacias", "/admin/falacias/queue", "/admin/falacias/side"):
        assert web(IP_ADMIN).get(path).status_code == 401
        assert web(IP_ADMIN).get(path, headers=basic("wrong")).status_code == 401
    for path in ("/admin/falacias/start", "/admin/falacias/finish", "/admin/falacias/review", "/admin/falacias/message"):
        assert web(IP_ADMIN).post(path, data={}).status_code == 401


def test_admin_is_disabled_without_a_password(conn, test_db_url, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    with TestClient(create_app(test_db_url)) as c:
        assert c.get("/admin/falacias").status_code == 503


def test_tabs_are_on_every_admin_page_with_the_current_one_marked(web, conn):
    for path, key in (("/admin", "/admin"), ("/admin/falacias", "/admin/falacias"), ("/admin/phrases", "/admin/phrases")):
        html = web(IP_ADMIN).get(path, headers=basic()).text
        assert 'class="menu admintabs"' in html
        for href in ("/admin", "/admin/falacias", "/admin/phrases"):
            assert f'href="{href}"' in html
        assert re.search(rf'<a[^>]*href="{key}"[^>]*aria-current="page"|<a[^>]*aria-current="page"[^>]*href="{key}"', html)
        assert html.count('aria-current="page"') == 1


# ================================================================ start / finish
def test_page_without_a_session_offers_start(web):
    html = web(IP_ADMIN).get("/admin/falacias", headers=basic()).text
    assert T.AF_NO_SESSION in html and 'action="/admin/falacias/start"' in html


def test_start_needs_csrf_then_creates_an_admin_started_session(web, conn):
    admin = web(IP_ADMIN)
    assert admin.post("/admin/falacias/start", data={"csrf": "x"}, headers=basic()).status_code == 403
    assert S.get_active_session(conn, "falacias") is None
    r = admin.post("/admin/falacias/start", data={"csrf": A.start_token(PW)}, headers=basic())
    assert r.status_code == 303 and r.headers["location"] == "/admin/falacias"
    s = S.get_active_session(conn, "falacias")
    assert s is not None and s.created_by is None
    assert S.get_active_session(conn, "bingo") is None                         # Bingo untouched
    assert admin.post("/admin/falacias/start", data={"csrf": A.start_token(PW)}, headers=basic()).status_code == 303   # double click


def test_start_is_refused_without_the_feed_and_says_so(web, conn):
    conn.execute("TRUNCATE replay_events")
    r = web(IP_ADMIN).post("/admin/falacias/start", data={"csrf": A.start_token(PW)}, headers=basic())
    assert r.status_code == 200 and T.FEED_UNAVAILABLE in r.text and S.get_active_session(conn, "falacias") is None


def test_finish_needs_the_session_csrf_and_ends_only_falacias(web, conn, game):
    admin = game["admin"]
    sid = str(sid_of(conn))
    for bad in ({"session_id": sid, "csrf": "x"}, {"session_id": "nope", "csrf": token(conn)}):
        assert admin.post("/admin/falacias/finish", data=bad, headers=basic()).status_code in (400, 403)
    assert S.get_active_session(conn, "falacias") is not None
    r = admin.post("/admin/falacias/finish", data={"session_id": sid, "csrf": token(conn)}, headers=basic())
    assert r.status_code == 303 and S.get_active_session(conn, "falacias") is None
    assert admin.post("/admin/falacias/finish", data={"session_id": sid, "csrf": A.csrf_token(PW, uuid.UUID(sid))},
                      headers=basic()).status_code == 303                       # double click


def test_bingo_csrf_token_works_for_the_same_session_only(web, conn, game):
    other = A.csrf_token(PW, uuid.uuid4())
    assert review(game["admin"], conn, uuid.uuid4(), "correct", tok=other).status_code == 403


def test_ended_session_page_has_no_queue_and_offers_a_new_start(web, conn, game):
    S.end_session(conn, sid_of(conn), None)
    html = game["admin"].get("/admin/falacias", headers=basic()).text
    assert T.ADMIN_ENDED in html and 'id="queue"' not in html and 'action="/admin/falacias/start"' in html
    assert 'id="adminside"' in html


# ================================================================ queue
def test_page_lists_pending_pairs_fifo_with_context_and_buttons(web, conn, fal, game):
    send(game["bia"], conn, fal, "fala um", 0, 4)
    send(game["ana"], conn, fal, "chat um", 0, 4, fallacy="Ad hominem")
    html = game["admin"].get("/admin/falacias", headers=basic()).text
    assert html.index("Bia") < html.index('class="who">Ana<')              # oldest first
    assert "“fala”" in html and "Espantalho" in html and "Ad hominem" in html
    assert 'id="queue"' in html and 'id="queue-poll"' in html and 'hx-trigger="every 3s"' in html
    assert html.count('action="/admin/falacias/review"') == 6 and T.AF_CORRECT in html and T.AF_WRONG in html and T.AF_SPAM in html
    assert 'class="sel"' in html and 'id="pendingcount"' in html


def test_empty_queue_message(web, conn, game):
    assert T.AF_QUEUE_EMPTY in game["admin"].get("/admin/falacias", headers=basic()).text


def test_quote_and_names_are_escaped(web, conn, fal, game):
    conn.execute("UPDATE users SET name = %s WHERE name = 'Bia'", ("<i>Bia</i>",))
    send(game["bia"], conn, fal, "fala dois <b>x</b>", 0, 18)
    html = game["admin"].get("/admin/falacias", headers=basic()).text
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html and "&lt;i&gt;Bia&lt;/i&gt;" in html and "<i>Bia</i>" not in html


def test_poll_returns_204_when_nothing_new_and_appends_new_pairs_at_the_bottom(web, conn, fal, game):
    admin = game["admin"]
    send(game["bia"], conn, fal, "fala um", 0, 4)
    first = F.queue_items(conn, sid_of(conn))[0].created_us
    assert admin.get(f"/admin/falacias/queue?after={first}", headers=basic()).status_code == 204
    send(game["ana"], conn, fal, "chat um", 0, 4)
    r = admin.get(f"/admin/falacias/queue?after={first}", headers=basic())
    assert r.status_code == 200 and 'hx-swap-oob="beforeend:#queue"' in r.text
    assert "chat" in r.text and "fala um" not in r.text.split('hx-swap-oob="beforeend:#queue"')[1]
    last = F.queue_items(conn, sid_of(conn))[-1].created_us
    assert f"after={last}" in r.text and 'id="pendingcount"' in r.text and ">2<" in r.text


def test_poll_without_a_running_session_refreshes(web, conn):
    assert web(IP_ADMIN).get("/admin/falacias/queue?after=0", headers=basic()).headers.get("hx-refresh") == "true"


def test_poll_after_the_session_ended_refreshes(web, conn, game):
    S.end_session(conn, sid_of(conn), None)
    assert game["admin"].get("/admin/falacias/queue?after=0", headers=basic()).headers.get("hx-refresh") == "true"


# ================================================================ verdicts
@pytest.mark.parametrize("verdict", ["correct", "wrong", "spam"])
def test_review_stores_the_verdict_and_redraws_only_that_item(web, conn, fal, game, verdict):
    send(game["bia"], conn, fal, "fala um", 0, 4)
    send(game["ana"], conn, fal, "chat um", 0, 4)
    first = mids(conn)[0]
    r = review(game["admin"], conn, first, verdict)
    assert r.status_code == 200 and f'id="m-{first}"' in r.text and f'id="m-{mids(conn)[1]}"' not in r.text
    assert F.get_mark(conn, first).status == verdict and F.get_mark(conn, mids(conn)[1]).status == "pending"
    assert T.FAL_VERDICTS[verdict] in r.text and 'id="pendingcount"' in r.text and ">1<" in r.text


def test_a_verdict_can_be_corrected(web, conn, fal, game):
    send(game["bia"], conn, fal, "fala um", 0, 4)
    m = mids(conn)[0]
    review(game["admin"], conn, m, "wrong")
    r = review(game["admin"], conn, m, "correct")
    assert F.get_mark(conn, m).status == "correct" and 'aria-pressed="true"' in r.text


def test_the_player_sees_the_verdict_and_points(web, conn, fal, game):
    send(game["bia"], conn, fal, "fala um", 0, 4)
    m = mids(conn)[0]
    review(game["admin"], conn, m, "correct")
    side = game["bia"].get("/falacias/side").text
    assert T.FAL_VERDICTS["correct"] in side and F.fmt_points(F.points_of(F.get_mark(conn, m))) in side


def test_group_review_gives_every_identical_pending_pair_the_verdict_and_redraws_the_twins(web, conn, fal, game):
    send(game["ana"], conn, fal, "fala um", 0, 4)
    send(game["bia"], conn, fal, "fala um", 0, 4)
    send(game["bia"], conn, fal, "chat um", 0, 4)
    a, b, c = mids(conn)
    html = game["admin"].get("/admin/falacias", headers=basic()).text
    assert T.AF_ALL_SAME.format(n=2) in html and html.count(T.AF_ALL_SAME.format(n=2)) == 2 and T.AF_ALL_SAME.format(n=1) not in html
    r = review(game["admin"], conn, a, "correct", group="1")
    assert [F.get_mark(conn, x).status for x in (a, b, c)] == ["correct", "correct", "pending"]
    assert f'id="m-{a}"' in r.text and f'id="m-{b}"' in r.text and f'id="m-{c}"' not in r.text
    assert re.search(rf'<li[^>]*id="m-{b}"[^>]*hx-swap-oob="true"|<li[^>]*hx-swap-oob="true"[^>]*id="m-{b}"', r.text)
    assert ">1<" in r.text


def test_single_review_leaves_the_twin_alone(web, conn, fal, game):
    send(game["ana"], conn, fal, "fala um", 0, 4)
    send(game["bia"], conn, fal, "fala um", 0, 4)
    a, b = mids(conn)
    review(game["admin"], conn, a, "spam")
    assert F.get_mark(conn, b).status == "pending"


def test_review_input_errors(web, conn, fal, game):
    send(game["bia"], conn, fal, "fala um", 0, 4)
    m = mids(conn)[0]
    admin = game["admin"]
    assert review(admin, conn, m, "correct", tok="bad").status_code == 403
    assert review(admin, conn, m, "great").status_code == 400
    assert review(admin, conn, m, "correct", mark_id="nope").status_code == 400
    assert review(admin, conn, m, "correct", session_id="nope").status_code == 400
    assert review(admin, conn, uuid.uuid4(), "correct").status_code == 404
    assert F.get_mark(conn, m).status == "pending"


def test_review_without_htmx_redirects_back(web, conn, fal, game):
    send(game["bia"], conn, fal, "fala um", 0, 4)
    r = review(game["admin"], conn, mids(conn)[0], "wrong", htmx=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/falacias"


def test_no_review_after_the_session_ended(web, conn, fal, game):
    send(game["bia"], conn, fal, "fala um", 0, 4)
    m = mids(conn)[0]
    tok = token(conn)
    sid = str(sid_of(conn))
    S.end_session(conn, sid_of(conn), None)
    r = game["admin"].post("/admin/falacias/review", data={"session_id": sid, "mark_id": str(m), "verdict": "correct", "group": "0",
                                                           "csrf": tok}, headers=basic() | H_)
    assert r.headers.get("hx-redirect") == "/admin/falacias" and F.get_mark(conn, m).status == "pending"


def test_a_pair_of_another_session_cannot_be_reviewed(web, conn, fal, game):
    send(game["bia"], conn, fal, "fala um", 0, 4)
    m = mids(conn)[0]
    S.end_session(conn, sid_of(conn), None)
    web(IP_ADMIN).post("/admin/falacias/start", data={"csrf": A.start_token(PW)}, headers=basic())
    assert review(game["admin"], conn, m, "correct").status_code == 404 and F.get_mark(conn, m).status == "pending"


# ================================================================ host messages
def say(c, text="Pergunte sobre a evolução"):
    return c.post("/falacias/message", data={"text": text})


def test_player_box_is_on_the_page_with_the_counter(web, conn, fal, game):
    html = game["ana"].get("/falacias").text
    assert 'id="hostbox"' in html and 'hx-post="/falacias/message"' in html and 'maxlength="280"' in html
    assert T.HM_COUNT.format(n=0, max=5) in html


def test_player_sends_a_message_and_sees_the_count(web, conn, fal, game):
    r = say(game["ana"])
    assert r.status_code == 200 and "fal:msg-sent" in r.headers.get("hx-trigger", "") and T.HM_SENT in r.text
    assert T.HM_COUNT.format(n=1, max=5) in r.text
    assert conn.execute("SELECT text FROM host_messages").fetchone()[0] == "Pergunte sobre a evolução"
    assert T.HM_COUNT.format(n=1, max=5) in game["ana"].get("/falacias").text


@pytest.mark.parametrize("text,code", [("", "empty_message"), ("   ", "empty_message"), ("x" * 281, "message_too_long"),
                                       ("a\x07b", "bad_message")])
def test_message_errors_are_shown_inline(web, conn, fal, game, text, code):
    r = say(game["ana"], text)
    assert r.status_code == 200 and T.HM_ERRORS[code] in r.text and "fal:msg-sent" not in r.headers.get("hx-trigger", "")
    assert conn.execute("SELECT count(*) FROM host_messages").fetchone()[0] == 0


def test_message_rate_limit_and_cap_messages(web, conn, fal, game, monkeypatch):
    monkeypatch.setattr(H, "RATE_LIMIT_S", 30)
    say(game["ana"])
    assert T.HM_ERRORS["message_rate_limited"].format(n=30) in say(game["ana"], "outra").text
    monkeypatch.setattr(H, "RATE_LIMIT_S", 0)
    for i in range(4):
        say(game["ana"], f"m{i}")
    assert T.HM_ERRORS["message_limit_reached"].format(max=5) in say(game["ana"], "demais").text


def test_message_needs_a_falacias_identity(web, conn, fal, game):
    assert web(IP_B).post("/falacias/message", data={"text": "oi"}).status_code == 401
    bingo = named(web("203.0.113.50"), "Ze")
    bingo.post("/start")
    assert bingo.post("/falacias/message", data={"text": "oi"}).status_code == 401


def test_message_after_the_session_ended_refreshes(web, conn, fal, game, monkeypatch):
    monkeypatch.setattr(H, "send", lambda *a, **k: (_ for _ in ()).throw(F.SessionNotActive("ended")))
    assert say(game["ana"]).headers.get("hx-refresh") == "true"


def test_admin_side_shows_new_messages_escaped_oldest_first_with_author(web, conn, fal, game):
    say(game["bia"], "primeira <script>alert(1)</script>")
    say(game["ana"], "segunda")
    html = game["admin"].get("/admin/falacias/side", headers=basic()).text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<script>" not in html
    assert html.index("primeira") < html.index("segunda") and "Bia" in html and 'id="adminside"' in html
    assert 'hx-trigger="every 3s"' in html and T.AF_MSG_DONE in html and T.AF_MSG_HIDE in html


def test_admin_marks_a_message_done_hidden_and_back(web, conn, fal, game):
    say(game["bia"], "olá apresentador")
    mid = conn.execute("SELECT id FROM host_messages").fetchone()[0]
    admin = game["admin"]

    def act(status, tok=None):
        return admin.post("/admin/falacias/message", data={"session_id": str(sid_of(conn)), "message_id": str(mid), "status": status,
                                                           "csrf": token(conn) if tok is None else tok}, headers=basic() | H_)
    r = act("done")
    assert r.status_code == 200 and T.AF_NO_MESSAGES in r.text and "olá apresentador" in r.text      # moved to "Tratadas"
    assert conn.execute("SELECT status FROM host_messages").fetchone()[0] == "done"
    assert T.AF_MSG_UNDO in r.text
    act("hidden")
    assert conn.execute("SELECT status FROM host_messages").fetchone()[0] == "hidden"
    r = act("new")
    assert conn.execute("SELECT status, handled_at FROM host_messages").fetchone() == ("new", None) and T.AF_MSG_DONE in r.text


def test_message_actions_validate_input(web, conn, fal, game):
    say(game["bia"], "olá")
    mid = conn.execute("SELECT id FROM host_messages").fetchone()[0]
    admin = game["admin"]
    base = {"session_id": str(sid_of(conn)), "message_id": str(mid), "status": "done", "csrf": token(conn)}
    assert admin.post("/admin/falacias/message", data=base | {"csrf": "x"}, headers=basic()).status_code == 403
    assert admin.post("/admin/falacias/message", data=base | {"status": "deleted"}, headers=basic()).status_code == 400
    assert admin.post("/admin/falacias/message", data=base | {"message_id": "nope"}, headers=basic()).status_code == 400
    assert admin.post("/admin/falacias/message", data=base | {"message_id": str(uuid.uuid4())}, headers=basic()).status_code == 404
    assert conn.execute("SELECT status FROM host_messages").fetchone()[0] == "new"


def test_admin_side_has_the_leaderboard(web, conn, fal, game):
    send(game["bia"], conn, fal, "fala um", 0, 4)
    m = mids(conn)[0]
    review(game["admin"], conn, m, "correct")
    html = game["admin"].get("/admin/falacias/side", headers=basic()).text
    assert T.AF_BOARD in html and F.fmt_points(F.points_of(F.get_mark(conn, m))) in html
    assert html.index("Bia") < html.index("Ana")


def test_side_after_the_session_ended_still_works_and_messages_can_be_handled(web, conn, fal, game):
    say(game["bia"], "tarde")
    S.end_session(conn, sid_of(conn), None)
    admin = game["admin"]
    assert admin.get("/admin/falacias/side", headers=basic()).status_code == 200
