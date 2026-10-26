# ruff: noqa: RUF059
"""FC5: admin page /admin/checker (start, queue of checks to decide, manual scores, board, finish) and the player's tab/page /checker."""
from __future__ import annotations

import base64
import re
import uuid

import pytest
from starlette.testclient import TestClient

from app import admin as A
from app import checker as C
from app import checkworker as W
from app import factcheck as FC
from app import livestream as L
from app import sessions as S
from app import texts as T
from app.db import connect
from app.main import create_app

PW = "s3cret-pässword"
IP_ADMIN, IP_P = "203.0.113.99", "203.0.113.10"
H_ = {"hx-request": "true"}
LIVE, VERT, UPC, OLD, NEW = "LiveVid_001", "Vertical_01", "Upcoming_01", "OldVideo_01", "NewVideo_01"
PROBES = {
    LIVE: L.VideoProbe("live", L.StreamInfo(LIVE, "Ao vivo", 1920, 1080)),
    VERT: L.VideoProbe("live", L.StreamInfo(VERT, "Vertical", 1080, 1920)),
    UPC: L.VideoProbe("upcoming", L.StreamInfo(UPC, "Em breve", None, None)),
    OLD: L.VideoProbe("vod", L.StreamInfo(OLD, "Gravado", 1920, 1080)),
    NEW: L.VideoProbe("vod", L.StreamInfo(NEW, "Gravado novo", 1920, 1080)),
}


def basic(pw=PW):
    return {"authorization": "Basic " + base64.b64encode(f"admin:{pw}".encode()).decode()}


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users, replay_events CASCADE")
    for kind in ("transcript", "chat"):
        c.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES (%s,%s,1000,'a','x')", (OLD, kind))
    yield c
    c.execute("TRUNCATE game_sessions, users, replay_events CASCADE")
    c.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "50")
    monkeypatch.setenv("ADMIN_PASSWORD", PW)
    monkeypatch.setattr(C, "RATE_LIMIT_S", 0)


@pytest.fixture
def app(conn, test_db_url):
    def prober(video_id):
        if video_id == "Broken_0001":
            raise L.LookupFailed("network")
        return PROBES.get(video_id)

    a = create_app(test_db_url, live=L.LiveStream(resolver=lambda: None, video_resolver=lambda v: "t", ttl=3600, background=False, prober=prober))
    with TestClient(a):
        yield a


@pytest.fixture
def admin(app):
    return TestClient(app, client=(IP_ADMIN, 50000), follow_redirects=False)


def visitor(app, name="Zed"):
    c = TestClient(app, client=(IP_P, 50000), follow_redirects=False)
    if name:
        c.cookies.set("lg_name", name)
    return c


def sid_of(conn):
    return S.get_active_session(conn, "checker").id


def tok(conn):
    return A.csrf_token(PW, sid_of(conn))


def game(conn, players=("Ana", "Bia")):
    s = S.start_session_as_admin(conn, game_type="checker")
    return s, [S.join_session(conn, n, f"10.0.0.{i + 1}", game_type="checker")[1].id for i, n in enumerate(players)]


def result(**over):
    base = {"verdict": "false", "score": -3, "confidence": "high", "explanation": "Não existem provas.",
            "sources": [{"title": "Fonte <A>", "url": "https://example.org/a"}], "model": "fake", "input_tokens": 1, "output_tokens": 1, "elapsed_ms": 5}
    return C.AiResult(**(base | over))


def needs_admin(conn, uid, text, guess=-4, **over):
    cl = C.submit_typed_claim(conn, uid, text, guess)
    ck = C.claim_next_check(conn)
    C.complete_check(conn, ck.id, result(**{"confidence": "low"} | over))
    return cl


def score(admin, conn, check_id, value, htmx=True, csrf=None, sid=None):
    return admin.post("/admin/checker/score", data={"session_id": str(sid or sid_of(conn)), "check_id": str(check_id), "score": value,
                                                     "csrf": tok(conn) if csrf is None else csrf}, headers=basic() | (H_ if htmx else {}))


def dismiss(admin, conn, check_id, htmx=True):
    return admin.post("/admin/checker/dismiss", data={"session_id": str(sid_of(conn)), "check_id": str(check_id), "csrf": tok(conn)},
                      headers=basic() | (H_ if htmx else {}))


# ================================================================ auth, csrf, tabs
def test_admin_checker_needs_credentials(admin):
    for path in ("/admin/checker", "/admin/checker/panel"):
        assert admin.get(path).status_code == 401
        assert admin.get(path, headers=basic("wrong")).status_code == 401
    for path in ("/admin/checker/score", "/admin/checker/dismiss"):
        assert admin.post(path, data={}).status_code == 401


def test_posts_need_the_csrf_token(admin, conn):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, "A Terra é plana")
    assert score(admin, conn, cl.check_id, "2", csrf="nope").status_code == 403
    assert admin.post("/admin/checker/dismiss", data={"session_id": str(s.id), "check_id": str(cl.check_id), "csrf": "nope"}, headers=basic()).status_code == 403
    assert C.get_check(conn, cl.check_id).status == "needs_admin" and S.get_active_session(conn, "checker") is not None


# ================================================================ the queue
def test_no_game_page_points_to_the_session_button(admin):
    html = admin.get("/admin/checker", headers=basic()).text
    assert T.CK_NO_SESSION in html and T.SESSION_NONE_HINT in html and "Iniciar sessão" in html


def test_the_page_says_whether_the_automatic_check_is_on(admin, conn, test_db_url):
    S.start_session_as_admin(conn, game_type="checker")
    html = admin.get("/admin/checker", headers=basic()).text
    assert "CHECKER_MODEL is not set" in html and T.CK_AUTO_OFF.split("(")[0].strip() in html
    W.ensure_started(test_db_url, checker=FC.FakeChecker([result()]))
    html = admin.get("/admin/checker", headers=basic()).text
    assert T.CK_AUTO_ON.format(model="fake") in html


def test_a_check_waiting_for_the_admin_shows_the_claim_the_ai_result_and_the_guesses(admin, conn):
    s, (ana, bia) = game(conn)
    needs_admin(conn, ana, "A Terra é plana", -4)
    C.submit_typed_claim(conn, bia, "a terra é plana", 1)
    html = admin.get("/admin/checker", headers=basic()).text
    assert "A Terra é plana" in html and T.CK_STATUS["needs_admin"] in html
    assert T.CK_VERDICTS["false"] in html and "-3" in html and T.CK_CONFIDENCE["low"] in html and "Não existem provas." in html
    assert re.search(r"Ana.*?-4", html, re.DOTALL) and re.search(r"Bia.*?\+1", html, re.DOTALL)
    m = re.search(r'<a[^>]*href="https://example.org/a"[^>]*>', html)
    assert m and 'target="_blank"' in m.group(0) and "noopener" in m.group(0)
    assert "Fonte &lt;A&gt;" in html and "Fonte <A>" not in html


def test_claim_text_and_explanations_are_escaped(admin, conn):
    s, (ana, _) = game(conn)
    needs_admin(conn, ana, "<script>alert(1)</script> x", 1, explanation="<img src=x onerror=alert(2)>")
    html = admin.get("/admin/checker", headers=basic()).text
    assert "<script>alert(1)" not in html and "<img src=x" not in html and "&lt;script&gt;" in html


def test_queued_running_and_failed_checks_are_listed_with_their_state(admin, conn):
    s, (ana, bia) = game(conn)
    c3 = C.submit_typed_claim(conn, bia, "falhou muito", 1)
    for _ in range(C.MAX_ATTEMPTS):
        C.claim_next_check(conn)
        C.fail_check(conn, c3.check_id, "limite de requisições do provedor do modelo (HTTP 429)")
    C.submit_typed_claim(conn, ana, "na fila", 1)
    C.submit_typed_claim(conn, ana, "rodando", 1)
    C.claim_next_check(conn)
    html = admin.get("/admin/checker", headers=basic()).text
    assert T.CK_STATUS["queued"] in html and T.CK_STATUS["running"] in html and T.CK_STATUS["needs_admin"] in html
    assert "limite de requisições do provedor do modelo (HTTP 429)" in html and f"{T.CK_ERROR}:" in html


def test_pending_hides_finished_checks_and_show_all_brings_them_back(admin, conn):
    s, (ana, bia) = game(conn)
    needs_admin(conn, ana, "pendente aqui", 1)
    C.submit_typed_claim(conn, bia, "ja resolvida", 1)
    done = C.claim_next_check(conn)
    C.complete_check(conn, done.id, result(confidence="high"))
    html = admin.get("/admin/checker", headers=basic()).text
    assert "pendente aqui" in html and "ja resolvida" not in html and T.CK_SHOW_ALL in html
    html = admin.get("/admin/checker?all=1", headers=basic()).text
    assert "pendente aqui" in html and "ja resolvida" in html and T.CK_SHOW_PENDING in html


def test_the_pending_counter_counts_queued_running_and_waiting(admin, conn):
    s, (ana, _) = game(conn)
    needs_admin(conn, ana, "um", 1)
    C.submit_typed_claim(conn, ana, "dois", 1)
    html = admin.get("/admin/checker", headers=basic()).text
    assert re.search(r'<span[^>]*id="pendingcount"[^>]*>2<', html)


def test_an_empty_queue_says_so(admin, conn):
    S.start_session_as_admin(conn, game_type="checker")
    assert T.CK_NO_CHECKS in admin.get("/admin/checker", headers=basic()).text


# ================================================================ scoring by hand
def test_the_admin_sets_a_score_and_only_that_item_comes_back(admin, conn):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, "A Terra é plana", -4)
    r = score(admin, conn, cl.check_id, "-5")
    assert r.status_code == 200
    ck = C.get_check(conn, cl.check_id)
    assert (ck.status, ck.admin_score) == ("resolved", -5)
    assert f'id="ck-{cl.check_id}"' in r.text and "Nota do apresentador" not in r.text and 'id="pendingcount"' in r.text
    assert "<html" not in r.text


def test_a_plain_post_redirects_back_to_the_page(admin, conn):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, "x y", 1)
    r = score(admin, conn, cl.check_id, "3", htmx=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/checker" and C.get_check(conn, cl.check_id).admin_score == 3


def test_the_admin_can_override_a_confident_ai_result_and_the_board_follows(admin, conn):
    s, (ana, bia) = game(conn)
    C.submit_typed_claim(conn, ana, "A Terra é plana", -5)
    C.submit_typed_claim(conn, bia, "a terra é plana", 5)
    ck = C.claim_next_check(conn)
    C.complete_check(conn, ck.id, result(score=-5, confidence="high"))
    board = admin.get("/admin/checker?all=1", headers=basic()).text
    assert re.search(r"Ana.*?>7<", board, re.DOTALL)
    score(admin, conn, ck.id, "5")
    board = admin.get("/admin/checker?all=1", headers=basic()).text
    assert re.search(r"Bia.*?>7<", board, re.DOTALL) and "Falsa" in board


def test_the_admin_can_score_a_check_the_ai_never_ran(admin, conn):
    s, (ana, _) = game(conn)
    cl = C.submit_typed_claim(conn, ana, "ninguém verificou", 2)
    score(admin, conn, cl.check_id, "2")
    assert C.get_check(conn, cl.check_id).status == "resolved"


def test_dismissing_means_nobody_scores(admin, conn):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, "x y", 1)
    r = dismiss(admin, conn, cl.check_id)
    ck = C.get_check(conn, cl.check_id)
    assert (ck.status, ck.admin_score) == ("resolved", None) and T.CK_DISMISSED in r.text
    assert C.user_claims(conn, ana)[0].points == 0


@pytest.mark.parametrize("bad", ["99", "-6", "abc", "", "1.5"])
def test_a_bad_score_is_refused_and_nothing_changes(admin, conn, bad):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, "x y", 1)
    assert score(admin, conn, cl.check_id, bad).status_code == 400
    assert C.get_check(conn, cl.check_id).status == "needs_admin"


def test_unknown_check_and_bad_ids(admin, conn):
    s, _ = game(conn)
    assert score(admin, conn, uuid.uuid4(), "1").status_code == 404
    r = admin.post("/admin/checker/score", data={"session_id": "x", "check_id": "y", "score": "1", "csrf": "z"}, headers=basic())
    assert r.status_code == 400


def test_a_score_for_an_ended_game_is_redirected_and_ignored(admin, conn):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, "x y", 1)
    tk = tok(conn)
    S.end_session(conn, s.id, None)
    r = admin.post("/admin/checker/score", data={"session_id": str(s.id), "check_id": str(cl.check_id), "score": "1", "csrf": tk}, headers=basic() | H_)
    assert r.headers.get("HX-Redirect") == "/admin/checker" and C.get_check(conn, cl.check_id).status == "needs_admin"


# ================================================================ the polled panel
def sig_of(html):
    return re.search(r"sig=([0-9a-f]+)", html).group(1)


def test_the_panel_answers_204_until_something_changes(admin, conn):
    s, (ana, bia) = game(conn)
    cl = needs_admin(conn, ana, "x y", 1)
    sig = sig_of(admin.get("/admin/checker", headers=basic()).text)
    assert admin.get(f"/admin/checker/panel?sig={sig}", headers=basic()).status_code == 204
    score(admin, conn, cl.check_id, "1")
    r = admin.get(f"/admin/checker/panel?sig={sig}", headers=basic())
    assert r.status_code == 200 and sig_of(r.text) != sig and 'id="ckpanel"' in r.text
    C.submit_typed_claim(conn, bia, "outra frase", 1)                      # a new claim changes it too
    sig2 = sig_of(r.text)
    assert admin.get(f"/admin/checker/panel?sig={sig2}", headers=basic()).status_code == 200


def test_the_panel_keeps_the_filter_in_its_poll_url(admin, conn):
    game(conn)
    html = admin.get("/admin/checker?all=1", headers=basic()).text
    assert "all=1" in re.search(r'<div[^>]*id="ckpanel"[^>]*>', html).group(0)
    assert "all=1" not in re.search(r'<div[^>]*id="ckpanel"[^>]*>', admin.get("/admin/checker", headers=basic()).text).group(0)


def test_the_panel_asks_for_a_refresh_when_the_game_is_gone(admin, conn):
    r = admin.get("/admin/checker/panel", headers=basic())
    assert r.status_code == 200 and r.headers.get("HX-Refresh") == "true"


def test_the_board_lists_points_claims_and_exact_guesses(admin, conn):
    s, (ana, bia) = game(conn)
    C.submit_typed_claim(conn, ana, "uma", -3)
    C.submit_typed_claim(conn, bia, "outra", 3)
    C.complete_check(conn, C.claim_next_check(conn).id, result(score=-3, confidence="high"))
    html = admin.get("/admin/checker", headers=basic()).text
    assert T.AF_BOARD in html and T.CK_COL_EXACT in html
    assert re.search(r"<td[^>]*>1</td>\s*<td[^>]*>Ana</td>\s*<td[^>]*>7</td>\s*<td[^>]*>1</td>\s*<td[^>]*>1</td>", html)


# ================================================================ finish
def test_finishing_the_session_ends_the_game_and_shows_the_winner(admin, conn):
    s, (ana, bia) = game(conn)
    C.submit_typed_claim(conn, ana, "uma", -3)
    C.complete_check(conn, C.claim_next_check(conn).id, result(score=-3, confidence="high"))
    r = admin.post("/admin/session/finish", data={"csrf": A.session_token(PW)}, headers=basic())
    assert r.status_code == 303 and S.get_active_session(conn, "checker") is None
    html = admin.get("/admin/checker", headers=basic()).text
    assert f"{T.ADMIN_WINNER}: Ana" in html and T.ADMIN_ENDED in html


# ================================================================ the player's tab and page
def test_every_player_page_has_the_third_tab(app):
    for path in ("/", "/falacias", "/checker"):
        html = visitor(app).get(path).text
        assert 'href="/checker"' in html and T.MENU_CHECKER in html and T.MENU_CHECKER_SUB in html


def test_the_tab_is_active_on_its_page_only(app):
    html = visitor(app).get("/checker").text
    assert re.search(r'<a[^>]*aria-current="page"[^>]*href="/checker"|<a[^>]*href="/checker"[^>]*aria-current="page"', html)
    assert not re.search(r'<a[^>]*href="/falacias"[^>]*aria-current="page"', html)


def test_the_page_asks_for_the_name_first(app):
    html = visitor(app, name=None).get("/checker").text
    assert T.NAME_HEADING in html and 'name="next"' in html and 'value="/checker"' in html


def test_the_name_form_comes_back_to_this_tab(app):
    r = visitor(app, name=None).post("/name", data={"name": "Zed", "next": "/checker"})
    assert r.status_code == 303 and r.headers["location"] == "/checker"


def test_no_game_means_about_to_start(app, conn):
    html = visitor(app).get("/checker").text
    assert T.CK_TITLE in html and T.CK_NONE_HEADING in html and T.CK_NONE_HELP in html
    assert T.CK_RUNNING_HEADING not in html


@pytest.mark.parametrize(("players", "text"), [(1, "1 jogador na partida."), (2, "2 jogadores na partida.")])
def test_a_running_game_is_shown_with_its_players(app, conn, players, text):
    game(conn, ("Ana", "Bia")[:players])
    html = visitor(app).get("/checker").text
    assert T.CK_RUNNING_HEADING in html and text in html and 'action="/checker/join"' in html and T.CK_NONE_HEADING not in html


@pytest.mark.parametrize("ended", [False, True])
def test_the_admin_panel_names_the_video_only_while_the_game_runs(admin, conn, ended):
    s = S.start_session_as_admin(conn, game_type="checker", video_id=LIVE, source_mode="live")
    if ended:
        S.end_session(conn, s.id, None)
    html = admin.get("/admin/checker", headers=basic()).text
    url = f"https://www.youtube.com/watch?v={LIVE}"
    if ended:
        assert T.ADMIN_ENDED in html and T.SRC_SESSION_LIVE not in html and 'class="srcrow"' not in html
        assert url not in html.split('<header', 1)[0] + html.split('</header>', 1)[-1]
    else:
        assert T.SRC_SESSION_LIVE in html and url in html


def test_a_running_game_with_a_video_links_to_it(app, conn):
    S.start_session_as_admin(conn, game_type="checker", video_id=LIVE, source_mode="live")
    html = visitor(app).get("/checker").text
    assert f"https://www.youtube.com/watch?v={LIVE}" in html and T.SRC_SESSION_LIVE in html


def test_the_player_page_has_a_join_button_but_no_admin_controls(app, conn):
    game(conn)
    html = visitor(app).get("/checker").text
    assert 'action="/checker/join"' in html and "/admin" not in html and "csrf" not in html


def test_the_status_fragment_follows_the_game_without_a_reload(app, conn):
    v = visitor(app)
    before = v.get("/checker/status").text
    assert T.CK_NONE_HEADING in before and "<html" not in before and 'hx-get="/checker/status"' in before
    S.start_session_as_admin(conn, game_type="checker")
    assert T.CK_RUNNING_HEADING in v.get("/checker/status").text


def test_the_menu_dot_marks_a_running_checker_game(app, conn):
    assert "dot" not in visitor(app).get("/menu?cur=checker").text.split('href="/checker"')[1].split("</a>")[0]
    S.start_session_as_admin(conn, game_type="checker")
    assert "dot" in visitor(app).get("/menu?cur=checker").text.split('href="/checker"')[1].split("</a>")[0]


def test_change_name_on_this_tab_comes_back_to_this_tab(app):
    assert "/name?next=/checker" in visitor(app).get("/checker").text


def test_the_other_tabs_still_work(app):
    assert visitor(app).get("/").status_code == 200 and visitor(app).get("/falacias").status_code == 200
