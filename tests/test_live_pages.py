# ruff: noqa: F811
"""L4 / step 3: pages of the live Falácias game — source choice at start, typed quotes, chat-down banner, admin queue + admin start."""
from __future__ import annotations

import re
import time

import pytest

from app import chatworker as CW
from app import falacias as F
from app import livestream as L
from app import sessions as S
from app import texts as T
from tests.chat_fakes import Factory, FakeProcess, wait_until
from tests.test_admin_falacias import PW, A, basic, fal, token  # noqa: F401
from tests.test_falacias_web import IP_A, IP_B, _env, conn, named, start  # noqa: F401
from tests.test_stream_source import LIVE_ID, VID, _replay_video, make_live, page_for  # noqa: F401

HORIZONTAL = L.StreamInfo(LIVE_ID, "Debate horizontal", 1920, 1080)
VERTICAL = L.StreamInfo("W4hXn3D9qtI", "Debate vertical 📱", 1080, 1920)


def sessions(conn):
    return conn.execute("SELECT video_id, source_mode FROM game_sessions WHERE game_type = 'falacias'").fetchall()


@pytest.fixture
def no_env_video(monkeypatch):
    monkeypatch.delenv("FALACIAS_VIDEO_ID", raising=False)


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    monkeypatch.setattr(F, "RATE_LIMIT_S", 0)


def post_start(test_db_url, live, data=None, name="Ana"):
    tc, b = page_for(test_db_url, live)
    with tc:
        r = named(b, name).post("/falacias/start", data={"name": name} | (data or {}))
    return r


# ---------------------------------------------------------------- which video a new game follows
def test_a_horizontal_live_is_the_source(conn, test_db_url, no_env_video):
    r = post_start(test_db_url, make_live(HORIZONTAL))
    assert r.status_code == 303 and sessions(conn) == [(LIVE_ID, "live")]


def test_a_horizontal_live_beats_a_video_url_typed_by_the_player(conn, test_db_url):
    r = post_start(test_db_url, make_live(HORIZONTAL), {"video": f"https://www.youtube.com/watch?v={VID}"})
    assert r.status_code == 303 and sessions(conn) == [(LIVE_ID, "live")]


def test_a_horizontal_live_beats_the_env_replay(conn, test_db_url):                # env FALACIAS_VIDEO_ID is set by the fixtures
    post_start(test_db_url, make_live(HORIZONTAL))
    assert sessions(conn) == [(LIVE_ID, "live")]


def test_the_lookup_that_is_still_running_is_awaited_at_start(conn, test_db_url, no_env_video):
    live = L.LiveStream(resolver=lambda: HORIZONTAL, ttl=3600)          # background lookups, never run yet
    assert not live.known
    r = post_start(test_db_url, live)
    assert r.status_code == 303 and sessions(conn) == [(LIVE_ID, "live")]


def test_a_vertical_live_is_ignored_and_without_a_url_nothing_starts(conn, test_db_url, no_env_video):
    r = post_start(test_db_url, make_live(VERTICAL))
    assert r.status_code == 200 and sessions(conn) == [] and T.ERRORS["no_source"] in r.text
    assert 'name="video"' in r.text


def test_offline_without_a_url_asks_for_one(conn, test_db_url, no_env_video):
    r = post_start(test_db_url, make_live(None))
    assert r.status_code == 200 and sessions(conn) == [] and T.ERRORS["no_source"] in r.text


def test_offline_with_an_imported_replay_url_starts_that_replay(conn, test_db_url, no_env_video):
    r = post_start(test_db_url, make_live(None), {"video": f"https://youtu.be/{VID}"})
    assert r.status_code == 303 and sessions(conn) == [(VID, "replay")]


def test_offline_with_a_replay_that_was_not_imported_names_the_video_and_the_command(conn, test_db_url, no_env_video):
    r = post_start(test_db_url, make_live(None), {"video": "https://youtu.be/zzzzzzzzzzz"})
    assert r.status_code == 200 and sessions(conn) == []
    assert "zzzzzzzzzzz" in r.text and "corpus replay-import zzzzzzzzzzz" in r.text


@pytest.mark.parametrize("bad", ["not a link", "https://evil.example/watch?v=abcdefghijk", "javascript:alert(1)",
                                 "https://www.youtube.com.evil.com/watch?v=abcdefghijk"])
def test_a_bad_url_is_refused_without_a_session(conn, test_db_url, no_env_video, bad):
    r = post_start(test_db_url, make_live(None), {"video": bad})
    assert r.status_code == 200 and sessions(conn) == [] and T.ERRORS["invalid_video"] in r.text


def test_env_replay_still_works_when_nothing_else_is_given(conn, test_db_url):             # test/demo mode (FALACIAS_VIDEO_ID)
    r = post_start(test_db_url, make_live(None))
    assert r.status_code == 303 and sessions(conn)[0][1] == "replay"


def test_joining_never_needs_a_source(conn, test_db_url, no_env_video):
    S.start_session_as_admin(conn, game_type="falacias", video_id=LIVE_ID, source_mode="live")
    tc, b = page_for(test_db_url, make_live(None))
    with tc:
        r = named(b, "Bia").post("/falacias/join")
    assert r.status_code == 303


# ---------------------------------------------------------------- the entry page
def test_entry_offers_the_url_field_when_there_is_no_horizontal_live(conn, test_db_url, no_env_video):
    tc, b = page_for(test_db_url, make_live(VERTICAL))
    with tc:
        html = named(b, "Ana").get("/falacias").text
    assert 'name="video"' in html and T.FAL_VIDEO_FIELD in html


def test_entry_hides_the_url_field_and_the_replay_line_when_a_horizontal_live_exists(conn, test_db_url):
    tc, b = page_for(test_db_url, make_live(HORIZONTAL))
    with tc:
        html = named(b, "Ana").get("/falacias").text
    assert 'name="video"' not in html and T.SRC_LIVE_NOW in html and T.SRC_VIDEO not in html


def test_entry_for_joining_has_no_url_field(conn, test_db_url, no_env_video):
    S.start_session_as_admin(conn, game_type="falacias", video_id=LIVE_ID, source_mode="live")
    tc, b = page_for(test_db_url, make_live(None))
    with tc:
        html = named(b, "Bia").get("/falacias").text
    assert 'name="video"' not in html and "/falacias/join" in html


# ---------------------------------------------------------------- the game page of a live session
def other(tc, ip):
    """Another browser on the SAME app (its pool is open only inside `with tc`)."""
    from starlette.testclient import TestClient
    return TestClient(tc.app, client=(ip, 50000), follow_redirects=False)


def live_game(test_db_url, live=None):
    tc, b = page_for(test_db_url, live or make_live(HORIZONTAL))
    return tc, b


def test_live_game_page_shows_the_live_source_not_the_env_replay(conn, test_db_url):
    tc, b = live_game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        html = b.get("/falacias").text
    assert f"watch?v={LIVE_ID}" in html and T.SRC_SESSION_LIVE in html and T.SRC_VIDEO not in html


def test_live_game_page_explains_there_is_no_transcript_and_has_the_typed_quote_box(conn, test_db_url):
    tc, b = live_game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        html = b.get("/falacias").text
    assert T.FAL_NO_TRANSCRIPT in html and 'hx-post="/falacias/spoken"' in html and 'maxlength="300"' in html
    assert T.FAL_REPLAY not in html                                       # it is not a replay


def test_replay_game_page_has_no_typed_quote_box(conn, test_db_url):
    tc, b = page_for(test_db_url, make_live(None))
    with tc:
        start(named(b, "Ana"), "Ana")
        html = b.get("/falacias").text
    assert '/falacias/spoken' not in html and T.FAL_NO_TRANSCRIPT not in html and T.FAL_REPLAY in html


def test_live_chat_lines_reach_the_pane_through_the_poll(conn, test_db_url):
    tc, b = live_game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        sid = S.get_active_session(conn, "falacias").id
        ts = int(float(conn.execute("SELECT extract(epoch FROM now())").fetchone()[0] + 1) * 1e6)
        from tests.chat_fakes import chat_line
        CW.ingest(conn, sid, CW.parse_lines([chat_line("Y1", "mensagem ao vivo", "@x", ts_us=ts)]))
        html = b.get("/falacias/feed?after=0&st=ok").text
    assert "mensagem ao vivo" in html


def chat_down(conn, test_db_url, monkeypatch, sid, video=LIVE_ID):
    monkeypatch.setattr(CW, "process_factory", Factory(FakeProcess(fail_start=True)))
    CW.ensure(sid, video)
    assert wait_until(lambda: CW.health(sid) == "down")


def test_chat_down_shows_its_own_banner_and_refuses_selected_pairs(conn, test_db_url, fal, monkeypatch):
    tc, b = live_game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        sid = S.get_active_session(conn, "falacias").id
        chat_down(conn, test_db_url, monkeypatch, sid)
        html = b.get("/falacias").text
        poll = b.get("/falacias/feed?after=0&st=ok")
    assert T.FAL_CHAT_DOWN in html and T.FEED_UNAVAILABLE not in html
    assert T.FAL_CHAT_DOWN in poll.text


# ---------------------------------------------------------------- typed quotes
def spoken(b, text="Deus existe porque a Bíblia diz", fid=None, **over):
    return b.post("/falacias/spoken", data={"text": text, "fallacy_id": fid} | over)


@pytest.fixture
def lg(conn, test_db_url, fal):
    tc, b = live_game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        yield b, fal, conn


def marks(conn):
    return conn.execute("SELECT kind, quote, status FROM fallacy_marks ORDER BY created_at").fetchall()


def test_typed_quote_is_saved_as_a_pending_spoken_pair(lg):
    b, fal, conn = lg
    r = spoken(b, fid=fal["Espantalho"])
    assert r.status_code == 200 and T.FAL_SENT in r.text
    assert marks(conn) == [("spoken", "Deus existe porque a Bíblia diz", "pending")]


def test_typed_quote_shows_up_in_my_pairs_in_the_response(lg):
    b, fal, _conn = lg
    r = spoken(b, "frase digitada", fal["Espantalho"])
    assert "frase digitada" in r.text and 'id="side"' in r.text


def test_typed_quote_is_escaped(lg):
    b, fal, _conn = lg
    r = spoken(b, "<script>alert(1)</script>", fal["Espantalho"])
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text


def test_typed_quote_is_cleaned_like_the_service_does(lg):
    b, fal, conn = lg
    spoken(b, "  muitos \n  espaços   ", fal["Espantalho"])
    assert marks(conn)[0][1] == "muitos espaços"


@pytest.mark.parametrize("text,msg", [("", T.FAL_SPOKEN_ERRORS["bad_selection"]), ("   \n ", T.FAL_SPOKEN_ERRORS["bad_selection"]),
                                      ("x" * 301, T.FAL_SPOKEN_ERRORS["spoken_too_long"])])
def test_bad_typed_quotes_show_an_inline_error_and_save_nothing(lg, text, msg):
    b, fal, conn = lg
    r = spoken(b, text, fal["Espantalho"])
    assert msg in r.text and marks(conn) == []


def test_unknown_or_missing_or_disabled_fallacy_is_refused(lg):
    b, _fal, conn = lg
    off = conn.execute("INSERT INTO fallacies (name, category, source, enabled) VALUES ('Off','C','manual',false) RETURNING id").fetchone()[0]
    for fid in ("", "abc", "999999", str(off), "1" * 40):
        r = spoken(b, fid=fid)
        assert T.FAL_ERRORS["unknown_fallacy"] in r.text, fid
    assert marks(conn) == []


def test_exactly_300_characters_are_accepted(lg):
    b, fal, _conn = lg
    assert T.FAL_SENT in spoken(b, "y" * 300, fal["Espantalho"]).text


def test_duplicate_typed_quote_is_refused_even_with_other_case(lg):
    b, fal, conn = lg
    spoken(b, "Mesma frase", fal["Espantalho"])
    r = spoken(b, "mesma FRASE", fal["Espantalho"])
    assert T.FAL_ERRORS["duplicate_mark"] in r.text and len(marks(conn)) == 1
    assert T.FAL_SENT in spoken(b, "Mesma frase", fal["Ad hominem"]).text           # another fallacy is a new pair


def test_rate_limit_applies_to_typed_quotes(lg, monkeypatch):
    b, fal, conn = lg
    monkeypatch.setattr(F, "RATE_LIMIT_S", 10)
    spoken(b, "primeira", fal["Espantalho"])
    r = spoken(b, "segunda", fal["Espantalho"])
    assert T.FAL_ERRORS["rate_limited"].format(n=10) in r.text and len(marks(conn)) == 1


def test_typed_quotes_work_while_the_chat_is_down(lg, monkeypatch, test_db_url):
    b, fal, conn = lg
    chat_down(conn, test_db_url, monkeypatch, S.get_active_session(conn, "falacias").id)
    assert T.FAL_SENT in spoken(b, "falada", fal["Espantalho"]).text


def test_typed_quote_needs_a_falacias_identity(conn, test_db_url, fal):
    tc, b = live_game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        stranger = other(tc, "203.0.113.77")
        r = stranger.post("/falacias/spoken", data={"text": "oi", "fallacy_id": fal["Espantalho"]})
    assert r.status_code == 401 and marks(conn) == []


def test_typed_quote_after_the_session_ended_refreshes_the_page(lg):
    b, fal, conn = lg
    S.end_session(conn, S.get_active_session(conn, "falacias").id, None)
    r = spoken(b, fid=fal["Espantalho"])
    assert r.headers.get("hx-refresh") == "true" and marks(conn) == []


def test_typed_quote_is_not_shown_in_the_panes(lg):
    b, fal, _conn = lg
    spoken(b, "só no meu envio", fal["Espantalho"])
    html = b.get("/falacias/feed?after=0&st=ok")
    assert "só no meu envio" not in html.text


# ---------------------------------------------------------------- admin
def admin_app(test_db_url, live):
    from starlette.testclient import TestClient

    from app.main import create_app
    app = create_app(test_db_url, live=live)
    return TestClient(app), TestClient(app, client=("203.0.113.99", 50000), follow_redirects=False)


def admin_start(test_db_url, live, data=None):
    tc, adm = admin_app(test_db_url, live)
    with tc:
        r = adm.post("/admin/falacias/start", data={"csrf": A.start_token(PW)} | (data or {}), headers=basic())
        page = adm.get("/admin/falacias", headers=basic())
    return r, page


@pytest.fixture
def pw(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", PW)


def test_admin_start_follows_a_horizontal_live(conn, test_db_url, pw, no_env_video):
    r, page = admin_start(test_db_url, make_live(HORIZONTAL))
    assert r.status_code == 303 and sessions(conn) == [(LIVE_ID, "live")] and LIVE_ID in page.text


def test_admin_auto_start_without_a_live_tells_to_pick_a_replay(conn, test_db_url, pw, no_env_video):
    r, _ = admin_start(test_db_url, make_live(None))
    assert r.status_code == 200 and sessions(conn) == [] and T.AF_NO_SOURCE in r.text


def test_admin_auto_start_ignores_a_typed_link(conn, test_db_url, pw, no_env_video):
    r, _ = admin_start(test_db_url, make_live(None), {"video": f"https://www.youtube.com/watch?v={VID}"})
    assert r.status_code == 200 and sessions(conn) == []                       # replays are chosen from the catalogue list now


def test_admin_start_page_never_has_a_video_field_in_the_start_form(conn, test_db_url, pw, no_env_video):
    for info in (HORIZONTAL, None):
        tc, adm = admin_app(test_db_url, make_live(info))
        with tc:
            html = adm.get("/admin/falacias", headers=basic()).text
        assert 'name="video"' in html                                           # the catalogue's own link form
        assert 'name="video"' not in html.split('action="/admin/falacias/start"')[1].split("</form>")[0]


def test_admin_shows_the_session_source(conn, test_db_url, pw, no_env_video):
    _, page = admin_start(test_db_url, make_live(HORIZONTAL))
    assert T.SRC_SESSION_LIVE in page.text and f"watch?v={LIVE_ID}" in page.text


def test_admin_queue_shows_a_typed_pair_without_context_and_tagged(conn, test_db_url, pw, fal, no_env_video):
    tc, b = live_game(test_db_url)
    with tc:
        start(named(b, "Ana"), "Ana")
        spoken(b, "fala digitada", fal["Espantalho"])
        adm = other(tc, "203.0.113.99")
        html = adm.get("/admin/falacias", headers=basic()).text
    item = re.search(r'<li[^>]*class="qitem[^"]*".*?</li>', html, re.DOTALL).group(0)
    assert T.AF_TYPED in item and item.count("fala digitada") == 1 and 'class="context"' not in item


def test_admin_groups_identical_typed_pairs_of_two_players(conn, test_db_url, pw, fal, no_env_video):
    tc, a = live_game(test_db_url)
    with tc:
        start(named(a, "Ana"), "Ana")
        bia = other(tc, IP_B)
        named(bia, "Bia").post("/falacias/join")
        spoken(a, "Mesma Frase", fal["Espantalho"])
        spoken(bia, "mesma frase", fal["Espantalho"])
        adm = other(tc, "203.0.113.99")
        html = adm.get("/admin/falacias", headers=basic()).text
        mids = [r[0] for r in conn.execute("SELECT id FROM fallacy_marks ORDER BY created_at")]
        r = adm.post("/admin/falacias/review", data={"session_id": str(S.get_active_session(conn, "falacias").id), "mark_id": str(mids[0]),
                                                     "verdict": "correct", "group": "1",
                                                     "csrf": token(conn)}, headers=basic() | {"hx-request": "true"})
    assert T.AF_ALL_SAME.format(n=2) in html and r.status_code == 200
    assert {s for (s,) in conn.execute("SELECT status FROM fallacy_marks")} == {"correct"}


def test_typed_correct_pair_scores_ten_without_speed_bonus(conn, test_db_url, pw, fal, no_env_video):
    tc, a = live_game(test_db_url)
    with tc:
        start(named(a, "Ana"), "Ana")
        spoken(a, "fala", fal["Espantalho"])
        mid = conn.execute("SELECT id FROM fallacy_marks").fetchone()[0]
        sid = S.get_active_session(conn, "falacias").id
        F.review_mark(conn, sid, mid, "correct")
        time.sleep(0)
        assert [r.points for r in F.leaderboard(conn, sid)] == [1000]


def slow_live(info):
    def resolver():
        time.sleep(0.4)
        return info
    return L.LiveStream(resolver=resolver, ttl=3600)


def test_start_waits_for_a_slow_first_lookup_instead_of_missing_the_live(conn, test_db_url, no_env_video):
    r = post_start(test_db_url, slow_live(HORIZONTAL))
    assert r.status_code == 303 and sessions(conn) == [(LIVE_ID, "live")]


def test_admin_start_waits_for_a_slow_first_lookup_too(conn, test_db_url, pw, no_env_video):
    r, _ = admin_start(test_db_url, slow_live(HORIZONTAL))
    assert r.status_code == 303 and sessions(conn) == [(LIVE_ID, "live")]


def test_sent_typed_quote_tells_the_page_to_clear_the_box(lg):
    b, fal, _conn = lg
    assert spoken(b, "para limpar", fal["Espantalho"]).headers.get("hx-trigger") == "fal:spoken-sent"
    assert "hx-trigger" not in spoken(b, "", fal["Espantalho"]).headers


def test_source_fragment_of_a_live_player_shows_the_session_stream(lg):
    b, _fal, _conn = lg
    html = b.get("/falacias/source").text
    assert T.SRC_SESSION_LIVE in html and f"watch?v={LIVE_ID}" in html


def test_typed_item_html_in_the_queue_is_tagged_and_not_timed(conn, test_db_url, pw, fal, no_env_video):
    tc, a = live_game(test_db_url)
    with tc:
        start(named(a, "Ana"), "Ana")
        spoken(a, "fala x", fal["Espantalho"])
        html = other(tc, "203.0.113.99").get("/admin/falacias", headers=basic()).text
    assert f'<span class="badge typed">{T.AF_TYPED}</span>' in html and T.AF_SENT_AFTER.split("{")[0] not in html
