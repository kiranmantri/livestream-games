# ruff: noqa: F811
"""The admin starts a Falácias game from ANY YouTube video link (any channel): a horizontal live stream follows the live,
a finished video follows its imported replay. The channel in livestream.DEFAULT_URL is no longer the only possible source."""
from __future__ import annotations

from typing import ClassVar

import pytest
from starlette.testclient import TestClient

from app import admin as A
from app import feed, replaycollector
from app import livestream as L
from app import replays as R
from app import sessions as S
from app import texts as T
from app.main import create_app
from tests.test_admin_falacias import IP_ADMIN, PW, basic, fal  # noqa: F401
from tests.test_falacias_web import IP_A, _env, conn  # noqa: F401

LIVE = "LiveVid_001"
OLD = "OldVideo_01"          # finished, replay imported
NEW = "NewVideo_01"          # finished, replay NOT imported
VERT = "Vertical_01"
UPC = "Upcoming_01"
LINK = "https://www.youtube.com/watch?v={}"

PROBES = {
    LIVE: L.VideoProbe("live", L.StreamInfo(LIVE, "Outro canal ao vivo", 1920, 1080)),
    VERT: L.VideoProbe("live", L.StreamInfo(VERT, "Vertical", 1080, 1920)),
    UPC: L.VideoProbe("upcoming", L.StreamInfo(UPC, "Em breve", None, None)),
    OLD: L.VideoProbe("vod", L.StreamInfo(OLD, "Gravado", 1920, 1080)),
    NEW: L.VideoProbe("vod", L.StreamInfo(NEW, "Gravado novo", 1920, 1080)),
}


@pytest.fixture
def web(conn, test_db_url, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", PW)
    calls = []

    def prober(video_id):
        calls.append(video_id)
        if video_id == "Broken_0001":
            raise L.LookupFailed("network")
        return PROBES.get(video_id)

    conn.execute("DELETE FROM replays")
    for kind in ("transcript", "chat"):
        conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES (%s,%s,1000,'a','x')", (OLD, kind))
    app = create_app(test_db_url, live=L.LiveStream(resolver=lambda: None, video_resolver=lambda v: "t", ttl=3600,
                                                    background=False, prober=prober))
    with TestClient(app):
        c = TestClient(app, client=(IP_ADMIN, 50000), follow_redirects=False)
        c.calls = calls
        yield c
    conn.execute("DELETE FROM replays")


def go(web, link="", replay=""):
    return web.post("/admin/session/start", data={"csrf": A.session_token(PW), "video": link, "replay": replay}, headers=basic())


def active(conn):
    s = S.get_active_session(conn, "falacias")
    return None if s is None else feed.session_source(conn, s.id)


# ---------------------------------------------------------------- the page
def dialog(html):
    import re
    m = re.search(r"<dialog[^>]*id=\"startdlg\"[^>]*>.*?</dialog>", html, re.DOTALL)
    assert m, "no start dialog"
    return m.group(0)


def test_the_start_dialog_has_the_replay_select_and_the_link_box(web, conn, fal):
    html = web.get("/admin/falacias", headers=basic()).text
    d = dialog(html)
    assert 'action="/admin/session/start"' in d and 'name="video"' in d and 'name="replay"' in d and "<select" in d
    assert A.session_token(PW) in d and T.AF_URL_LABEL in d and "open" not in d.split(">")[0].split()


def test_the_old_panels_are_gone_from_the_page(web, conn, fal):
    html = web.get("/admin/falacias", headers=basic()).text
    assert T.AF_REPLAYS not in html and "replaypanel" not in html and "Link do vídeo ou da live" not in html
    assert 'action="/admin/falacias/replays/add"' not in html and T.AF_REPLAY_ADD not in html
    assert 'action="/admin/falacias/start"' not in html, "the automatic channel button is replaced by the dialog"


def test_the_select_offers_ready_replays_and_shows_the_others_disabled(web, conn, fal):
    import re
    conn.execute("UPDATE replays SET status='ready', title='Gravado de ontem', duration_s=3600, transcript_rows=1, chat_rows=1, "
                 "finished_at=now() WHERE video_id = %s", (OLD,)) if conn.execute("SELECT 1 FROM replays WHERE video_id = %s", (OLD,)).fetchone() else conn.execute(
        "INSERT INTO replays (video_id, url, title, duration_s, status, transcript_rows, chat_rows, finished_at) "
        "VALUES (%s,'u','Gravado de ontem',3600,'ready',1,1,now())", (OLD,))
    conn.execute("INSERT INTO replays (video_id, url, title, status) VALUES (%s,'u','Ainda baixando','queued')", (NEW,))
    d = dialog(web.get("/admin/falacias", headers=basic()).text)
    ready = re.search(rf'<option[^>]*value="{OLD}"[^>]*>([^<]*)</option>', d)
    queued = re.search(rf'<option[^>]*value="{NEW}"[^>]*>([^<]*)</option>', d)
    assert ready and "Gravado de ontem" in ready.group(1) and "disabled" not in ready.group(0).split(">")[0]
    assert queued and "disabled" in queued.group(0).split(">")[0] and T.AF_REPLAY_STATUS["queued"] in queued.group(1)
    assert re.search(r'<option[^>]*value=""', d), "an empty choice comes first"


def test_with_no_replays_the_select_says_so(web, conn, fal):
    conn.execute("DELETE FROM replays")
    assert T.AF_REPLAY_EMPTY in dialog(web.get("/admin/falacias", headers=basic()).text)


def test_the_header_button_opens_the_dialog_and_works_without_js(web, conn, fal):
    import re
    html = web.get("/admin/falacias", headers=basic()).text
    assert re.search(r'<a[^>]*data-open="startdlg"[^>]*href="/admin/session"|<a[^>]*href="/admin/session"[^>]*data-open="startdlg"', html)
    assert "<dialog" in html and re.match(r"<dialog[^>]*\bopen\b", dialog(html)) is None
    forced = web.get("/admin/session", headers=basic()).text
    assert re.match(r"<dialog[^>]*\bopen\b", dialog(forced)), "no JS: /admin/session shows the dialog"
    assert 'src="/startdlg.js"' in html


def test_a_replay_picked_in_the_select_starts_without_asking_youtube(web, conn, fal):
    conn.execute("DELETE FROM replays")
    conn.execute("INSERT INTO replays (video_id, url, title, status, transcript_rows, chat_rows, finished_at) "
                 "VALUES (%s,'u','t','ready',1,1,now())", (OLD,))
    r = go(web, replay=OLD)
    assert r.status_code == 303 and active(conn) == (OLD, "replay") and web.calls == []


def test_a_picked_replay_that_is_not_ready_is_refused(web, conn, fal):
    r = go(web, replay=NEW)
    assert r.status_code == 200 and T.AF_REPLAY_ERRORS["not_ready"] in r.text and active(conn) is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_nothing_chosen_is_refused(web, conn, fal, blank):
    r = go(web, blank)
    assert r.status_code == 200 and T.AF_URL_ERRORS["empty"] in r.text and active(conn) is None and web.calls == []


def test_a_typed_link_wins_over_the_select(web, conn, fal):
    conn.execute("INSERT INTO replays (video_id, url, title, status, transcript_rows, chat_rows, finished_at) "
                 "VALUES (%s,'u','t','ready',1,1,now())", (OLD,))
    go(web, LINK.format(LIVE), replay=OLD)
    assert active(conn) == (LIVE, "live")


def test_messages_come_back_inside_the_dialog_which_opens_by_itself(web, conn, fal):
    import re
    for r, msg in ((go(web, LINK.format(VERT)), T.AF_URL_ERRORS["vertical"]), (go(web, LINK.format(NEW)), T.AF_URL_QUEUED)):
        d = dialog(r.text)
        assert msg in d and 'data-autoopen' in d.split(">")[0] and re.search(r'name="video"[^>]*value="https://www.youtube.com', d)


# ---------------------------------------------------------------- live
def test_a_horizontal_live_of_any_channel_starts_a_live_game(web, conn, fal):
    r = go(web, LINK.format(LIVE))
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert active(conn) == (LIVE, "live")
    assert S.get_active_session(conn, "falacias").created_by is None


def test_other_url_shapes_work_too(web, conn, fal):
    assert go(web, f"https://youtu.be/{LIVE}").status_code == 303 and active(conn) == (LIVE, "live")


def test_a_vertical_live_is_refused(web, conn, fal):
    r = go(web, LINK.format(VERT))
    assert r.status_code == 200 and T.AF_URL_ERRORS["vertical"] in r.text and active(conn) is None


def test_a_stream_that_has_not_started_is_refused(web, conn, fal):
    r = go(web, LINK.format(UPC))
    assert r.status_code == 200 and T.AF_URL_ERRORS["upcoming"] in r.text and active(conn) is None


# ---------------------------------------------------------------- replay
def test_a_finished_video_with_an_imported_replay_starts_a_replay_game(web, conn, fal):
    assert go(web, LINK.format(OLD)).status_code == 303
    assert active(conn) == (OLD, "replay")


def test_a_finished_video_not_imported_is_queued_for_collection_as_a_cache(web, conn, fal, monkeypatch):
    woke = []
    monkeypatch.setattr(replaycollector, "wake", lambda: woke.append(1))
    r = go(web, LINK.format(NEW))
    assert r.status_code == 200 and T.AF_URL_QUEUED in r.text and 'class="error"' not in r.text and active(conn) is None
    assert conn.execute("SELECT status FROM replays WHERE video_id = %s", (NEW,)).fetchone() == ("queued",) and woke == [1]


def test_asking_again_while_it_is_collecting_does_not_queue_twice(web, conn, fal):
    go(web, LINK.format(NEW))
    r = go(web, LINK.format(NEW))
    assert T.AF_URL_COLLECTING in r.text and conn.execute("SELECT count(1) FROM replays WHERE video_id = %s", (NEW,)).fetchone() == (1,)


def test_once_collected_the_same_link_starts_the_game(web, conn, fal):
    go(web, LINK.format(NEW))
    for kind in ("transcript", "chat"):
        conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES (%s,%s,1000,'a','x')", (NEW, kind))
    conn.execute("UPDATE replays SET status = 'ready', transcript_rows = 1, chat_rows = 1, finished_at = now() WHERE video_id = %s", (NEW,))
    assert go(web, LINK.format(NEW)).status_code == 303 and active(conn) == (NEW, "replay")


def test_a_full_queue_is_reported(web, conn, fal):
    for i in range(R.MAX_QUEUED):
        conn.execute("INSERT INTO replays (video_id, url, status) VALUES (%s,'u','queued')", (f"Queued_{i:04d}",))
    r = go(web, LINK.format(NEW))
    assert T.AF_REPLAY_ERRORS["queue_full"] in r.text and active(conn) is None


def test_a_vertical_or_live_link_is_never_queued(web, conn, fal):
    go(web, LINK.format(VERT))
    go(web, LINK.format(UPC))
    assert conn.execute("SELECT count(1) FROM replays").fetchone() == (0,)


# ---------------------------------------------------------------- bad input
@pytest.mark.parametrize("link", ["https://evil.example/watch?v=LiveVid_001", "https://www.youtube.com/@canal", "lixo"])
def test_bad_links_are_refused_without_asking_youtube(web, conn, fal, link):
    r = go(web, link)
    assert r.status_code == 200 and T.AF_URL_ERRORS["invalid"] in r.text and active(conn) is None and web.calls == []


def test_a_lookup_failure_is_reported_not_guessed(web, conn, fal):
    r = go(web, LINK.format("Broken_0001"))
    assert r.status_code == 200 and T.AF_URL_ERRORS["lookup"] in r.text and active(conn) is None


def test_an_unknown_video_is_refused(web, conn, fal):
    r = go(web, LINK.format("Unknown_001"))
    assert r.status_code == 200 and T.AF_URL_ERRORS["lookup"] in r.text and active(conn) is None


# ---------------------------------------------------------------- guards
def test_needs_admin_and_csrf(web, conn, fal):
    assert web.post("/admin/falacias/start-url", data={"csrf": A.start_token(PW), "video": LINK.format(LIVE)}).status_code == 401
    assert web.post("/admin/falacias/start-url", data={"csrf": "x", "video": LINK.format(LIVE)}, headers=basic()).status_code == 403
    assert active(conn) is None and web.calls == []


def test_a_second_start_does_not_replace_the_running_game(web, conn, fal):
    go(web, LINK.format(LIVE))
    sid = S.get_active_session(conn, "falacias").id
    assert go(web, LINK.format(OLD)).status_code == 303
    assert S.get_active_session(conn, "falacias").id == sid and active(conn) == (LIVE, "live")


def test_while_a_game_runs_the_page_has_no_link_form(web, conn, fal):
    go(web, LINK.format(LIVE))
    assert 'action="/admin/falacias/start-url"' not in web.get("/admin/falacias", headers=basic()).text


def test_players_can_join_a_game_started_from_a_link(web, conn, test_db_url, fal):
    go(web, LINK.format(LIVE))
    app = create_app(test_db_url)
    with TestClient(app):
        p = TestClient(app, client=(IP_A, 50000), follow_redirects=False)
        p.post("/name", data={"name": "Ana", "next": "/falacias"})
        assert p.post("/falacias/join").status_code == 303
    assert S.count_players(conn, S.get_active_session(conn, "falacias").id) == 1


# ---------------------------------------------------------------- livestream.probe_video
class YDL:
    info: ClassVar[dict] = {}

    def __init__(self, opts):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        return self.info


@pytest.mark.parametrize("status,kind", [("is_live", "live"), ("is_upcoming", "upcoming"), ("was_live", "vod"),
                                         ("not_live", "vod"), ("post_live", "processing")])
def test_probe_video_maps_yt_dlp_states(monkeypatch, status, kind):
    import yt_dlp
    YDL.info = {"id": LIVE, "title": "T", "live_status": status, "is_live": status == "is_live", "width": 1280, "height": 720}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", YDL)
    p = L.probe_video(LIVE)
    assert p.kind == kind and p.info.id == LIVE and p.info.horizontal


def test_probe_video_refuses_a_bad_id_without_yt_dlp(monkeypatch):
    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", lambda o: pytest.fail("must not call yt-dlp"))
    assert L.probe_video("not an id") is None


# ---------------------------------------------------------------- start buttons: same text, on the title line
TITLE_ROW = r'<div class="titlerow">\s*<h1>[^<]*</h1>\s*<form[^>]*action="{}"[^>]*>.*?{}.*?</form>'


def finish_falacias(web, conn):
    import uuid
    sid = S.get_active_session(conn, "falacias").id
    web.post("/admin/falacias/finish", data={"session_id": str(sid), "csrf": A.csrf_token(PW, uuid.UUID(str(sid)))}, headers=basic())


def test_no_start_dialog_while_a_game_runs(web, conn, fal):
    go(web, LINK.format(LIVE))
    html = web.get("/admin/falacias", headers=basic()).text
    assert "<dialog" not in html and "Iniciar sessão" not in html


def test_the_link_form_is_still_there_after_a_game_ended(web, conn, fal):
    """Bug seen locally: the textbox vanished once a game had ended."""
    go(web, LINK.format(LIVE))
    web.post("/admin/session/finish", data={"csrf": A.session_token(PW)}, headers=basic())
    html = web.get("/admin/falacias", headers=basic()).text
    assert 'action="/admin/session/start"' in dialog(html) and 'data-open="startdlg"' in html and T.ADMIN_ENDED in html


def test_a_refused_link_after_a_game_ended_keeps_the_results(web, conn, fal):
    go(web, LINK.format(LIVE))
    finish_falacias(web, conn)
    r = go(web, LINK.format(VERT))
    assert T.AF_URL_ERRORS["vertical"] in dialog(r.text)
    assert T.ADMIN_ENDED in web.get("/admin/falacias", headers=basic()).text


def test_css_lays_the_title_line_out_with_the_button_on_the_right():
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text()
    m = re.search(r"\.titlerow\s*\{([^}]*)\}", css)
    assert m and "justify-content: space-between" in m.group(1) and "flex-wrap: wrap" in m.group(1)


def test_the_dialog_script_and_css_exist():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    js = (root / "startdlg.js").read_text()
    assert "showModal" in js and "data-open" in js and "autoopen" in js and len(js.splitlines()) < 40
    css = (root / "app.css").read_text()
    assert "dialog.startdlg" in css and "::backdrop" in css


# ---------------------------------------------------------------- the source link lives INSIDE the status panel
def status_panel(html):
    """The balanced <div id="statusbar">...</div> block."""
    start = html.index('id="statusbar"')
    start = html.rindex("<div", 0, start)
    depth = 0
    import re
    for m in re.finditer(r"<(/?)div\b", html[start:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return html[start:start + m.end() + 1]
    raise AssertionError("unbalanced")


@pytest.mark.parametrize("ended", [False, True])
def test_the_source_link_is_part_of_the_status_panel(web, conn, fal, ended):
    go(web, LINK.format(LIVE))
    if ended:
        finish_falacias(web, conn)
    html = web.get("/admin/falacias", headers=basic()).text
    panel = status_panel(html)
    assert (T.ADMIN_ENDED if ended else T.ADMIN_ACTIVE) in panel
    assert T.SRC_SESSION_LIVE in panel and LINK.format(LIVE) in panel
    assert html.count(T.SRC_SESSION_LIVE) == 1, "not repeated outside the panel"


def test_a_replay_game_names_its_video_in_the_panel(web, conn, fal):
    go(web, LINK.format(OLD))
    panel = status_panel(web.get("/admin/falacias", headers=basic()).text)
    assert T.SRC_VIDEO in panel and LINK.format(OLD) in panel


def test_nobody_scored_prints_no_extra_phrase(web, conn, fal):
    go(web, LINK.format(LIVE))
    finish_falacias(web, conn)
    html = web.get("/admin/falacias", headers=basic()).text
    assert "Ninguém pontuou" not in html and not hasattr(T, "FAL_NO_POINTS")


def test_css_gives_the_source_row_its_own_line_in_the_panel():
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text()
    m = re.search(r"#statusbar \.srcrow\s*\{([^}]*)\}", css)
    assert m and "flex-basis: 100%" in m.group(1) and "overflow-wrap: anywhere" in m.group(1)
