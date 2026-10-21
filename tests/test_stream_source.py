# ruff: noqa: F811
"""Stream source info: title/URL of the live stream and of the replay video, shown on the Falácias page.
Only HORIZONTAL streams are used (width > height; the 📱 title marker is only a hint when the size is unknown)."""
from __future__ import annotations

from typing import ClassVar

import pytest
from starlette.testclient import TestClient

from app import livestream as L
from app import texts as T
from tests.test_falacias_web import IP_A, _env, conn, named, start  # noqa: F401
from tests.test_falacias_web import VID as ROWS_VID
from tests.test_live import FakeYDL

LIVE_ID = "W4hXn3D9qtI"
VID = "abcdefghijk"                          # a REAL-looking 11-char id: the replay video of these tests
REAL_INFO = L.resolve_channel_info           # captured before the autouse fixture stubs it
REAL_TITLE = L.resolve_video_title
TITLE = "🔴 SOBRENATURAL: AS EXPERIÊNCIAS SÃO REAIS OU FALSAS?   -DEBATE ABERTO- 📱 2026-10-21 18:33"


# ------------------------------------------------------------------ StreamInfo / resolve_channel_info
def test_stream_info_url_and_orientation():
    v = L.StreamInfo(LIVE_ID, TITLE, 1080, 1920)
    h = L.StreamInfo(LIVE_ID, "Debate", 1920, 1080)
    assert v.url == f"https://www.youtube.com/watch?v={LIVE_ID}" and not v.horizontal and h.horizontal
    assert not L.StreamInfo(LIVE_ID, "q", 1000, 1000).horizontal                      # square is not horizontal


@pytest.mark.parametrize("title,expected", [("Debate 📱 hoje", False), ("Debate normal", True), ("", True)])
def test_unknown_size_falls_back_to_the_phone_marker(title, expected):
    assert L.StreamInfo(LIVE_ID, title, None, None).horizontal is expected
    assert L.StreamInfo(LIVE_ID, title, 0, 1080).horizontal is expected               # 0 = unknown too


def test_size_beats_the_marker():
    assert L.StreamInfo(LIVE_ID, "Debate 📱", 1920, 1080).horizontal          # the marker is a secondary hint only
    assert not L.StreamInfo(LIVE_ID, "Debate", 1080, 1920).horizontal


def test_resolve_channel_info_cases(monkeypatch):
    import yt_dlp
    from yt_dlp.utils import DownloadError
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    FakeYDL.info = {"id": LIVE_ID, "is_live": True, "title": TITLE, "width": 1080, "height": 1920}
    assert REAL_INFO() == L.StreamInfo(LIVE_ID, TITLE, 1080, 1920)
    FakeYDL.info = {"id": LIVE_ID, "is_live": False, "title": "x", "width": 1920, "height": 1080}
    assert REAL_INFO() is None
    FakeYDL.info = {"id": "short", "is_live": True, "title": "x"}
    assert REAL_INFO() is None
    FakeYDL.info = {"id": LIVE_ID, "is_live": True}                                  # no title/size at all
    assert REAL_INFO() == L.StreamInfo(LIVE_ID, "", None, None)
    FakeYDL.info = DownloadError("ERROR: The channel is not currently live")
    assert REAL_INFO() is None
    FakeYDL.info = OSError("down")
    with pytest.raises(L.LookupFailed):
        REAL_INFO()


def test_resolve_video_title(monkeypatch):
    import yt_dlp
    from yt_dlp.utils import DownloadError
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    FakeYDL.info = {"id": VID, "title": "CRENTES VS ATEUS"}
    assert REAL_TITLE(VID) == "CRENTES VS ATEUS"
    FakeYDL.info = {"id": VID}
    assert REAL_TITLE(VID) is None
    assert REAL_TITLE("not an id!") is None
    FakeYDL.info = DownloadError("ERROR: Video unavailable")
    with pytest.raises(L.LookupFailed):
        REAL_TITLE(VID)


# ------------------------------------------------------------------ LiveStream.info / title_of
def test_info_of_a_str_resolver_has_only_the_id_and_current_is_unchanged():
    s = L.LiveStream(resolver=lambda: LIVE_ID, ttl=60, background=False)
    assert s.current() == LIVE_ID and s.info() == L.StreamInfo(LIVE_ID, "", None, None)


def test_info_keeps_a_vertical_stream_but_current_still_returns_it_for_bingo():
    v = L.StreamInfo(LIVE_ID, TITLE, 1080, 1920)
    s = L.LiveStream(resolver=lambda: v, ttl=60, background=False)
    assert s.info() == v and s.current() == LIVE_ID                  # Bingo's behaviour is unchanged


def test_info_forced_id_and_offline():
    assert L.LiveStream(resolver=lambda: None, forced_id=LIVE_ID).info() == L.StreamInfo(LIVE_ID, "", None, None)
    assert L.LiveStream(resolver=lambda: None, ttl=60, background=False).info() is None


def test_info_rejects_a_malformed_id():
    s = L.LiveStream(resolver=lambda: L.StreamInfo('"><script>', "t", 1920, 1080), ttl=60, background=False)
    assert s.info() is None and s.current() is None


def test_title_of_is_cached_and_validated():
    calls = []
    s = L.LiveStream(resolver=lambda: None, video_resolver=lambda v: calls.append(v) or "Meu título", background=False)
    assert s.title_of(VID) == "Meu título" and s.title_of(VID) == "Meu título" and calls == [VID]
    assert s.title_of("not an id!") is None and calls == [VID]


def test_title_of_failure_is_retried_later_and_never_raises():
    answers = [L.LookupFailed("down"), "Depois"]

    def resolver(_):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    s = L.LiveStream(resolver=lambda: None, video_resolver=resolver, ttl=0, background=False)
    assert s.title_of(VID) is None and s.title_of(VID) == "Depois"


def test_title_of_does_not_block_on_the_network():
    import threading
    gate = threading.Event()

    def slow(_):
        gate.wait(5)
        return "Lento"
    s = L.LiveStream(resolver=lambda: None, video_resolver=slow, ttl=60)
    assert s.title_of(VID) is None                                   # returned at once, lookup runs in the background
    gate.set()


# ------------------------------------------------------------------ the page
@pytest.fixture(autouse=True)
def _replay_video(_env, conn, monkeypatch):
    """The fixtures' replay rows live under a short fake id; move them to an 11-char id like a real video."""
    conn.execute("UPDATE replay_events SET video_id = %s WHERE video_id = %s", (VID, ROWS_VID))
    monkeypatch.setenv("FALACIAS_VIDEO_ID", VID)

def page_for(test_db_url, live, browser_ip=IP_A):
    from app.main import create_app
    app = create_app(test_db_url, live=live)
    return TestClient(app), TestClient(app, client=(browser_ip, 50000), follow_redirects=False)


def make_live(info=None, title="Debate de teste <b>x</b>"):
    return L.LiveStream(resolver=lambda: info, video_resolver=lambda v: title, ttl=3600, background=False)


def replay_url():
    return f"https://www.youtube.com/watch?v={VID}"


def test_entry_page_shows_the_replay_video_url_and_escaped_title(conn, test_db_url):
    tc, b = page_for(test_db_url, make_live())
    with tc:
        html = named(b, "Ana").get("/falacias").text
    assert T.SRC_VIDEO in html and replay_url() in html and "Debate de teste &lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html
    assert 'id="source"' in html and T.SRC_ONLY_HORIZONTAL in html


def test_game_page_shows_it_too(conn, test_db_url):
    tc, b = page_for(test_db_url, make_live())
    with tc:
        start(named(b, "Ana"), "Ana")
        html = b.get("/falacias").text
    assert 'id="source"' in html and replay_url() in html and "Debate de teste" in html


def test_unknown_title_shows_the_url_and_a_notice(conn, test_db_url):
    tc, b = page_for(test_db_url, make_live(title=None))
    with tc:
        html = named(b, "Ana").get("/falacias").text
    assert replay_url() in html and T.SRC_TITLE_UNKNOWN in html


def test_vertical_live_is_shown_but_marked_as_not_used(conn, test_db_url):
    live = make_live(L.StreamInfo(LIVE_ID, TITLE, 1080, 1920))
    tc, b = page_for(test_db_url, live)
    with tc:
        html = named(b, "Ana").get("/falacias").text
    assert T.SRC_LIVE_VERTICAL in html and TITLE in html and f"watch?v={LIVE_ID}" in html
    assert T.SRC_LIVE_NOW not in html


def test_horizontal_live_is_shown_as_available(conn, test_db_url):
    live = make_live(L.StreamInfo(LIVE_ID, "Debate horizontal", 1920, 1080))
    tc, b = page_for(test_db_url, live)
    with tc:
        html = named(b, "Ana").get("/falacias").text
    assert T.SRC_LIVE_NOW in html and "Debate horizontal" in html and f"watch?v={LIVE_ID}" in html
    assert T.SRC_LIVE_VERTICAL not in html


def test_offline_channel_shows_no_live_line(conn, test_db_url):
    tc, b = page_for(test_db_url, make_live(None))
    with tc:
        html = named(b, "Ana").get("/falacias").text
    assert T.SRC_LIVE_NOW not in html and T.SRC_LIVE_VERTICAL not in html and T.SRC_LIVE_OFFLINE in html


def test_source_fragment_polls_without_a_load_trigger(conn, test_db_url):
    tc, b = page_for(test_db_url, make_live())
    with tc:
        frag = b.get("/falacias/source")
    assert frag.status_code == 200 and 'id="source"' in frag.text and "<html" not in frag.text
    assert 'hx-trigger="every 60s"' in frag.text and "load" not in frag.text.split('hx-trigger="')[1].split('"')[0]


def test_source_fragment_polls_faster_while_the_title_is_unknown(conn, test_db_url):
    tc, b = page_for(test_db_url, make_live(title=None))
    with tc:
        assert 'hx-trigger="every 5s"' in b.get("/falacias/source").text


def test_source_needs_no_identity(conn, test_db_url):
    tc, b = page_for(test_db_url, make_live())
    with tc:
        assert b.get("/falacias/source").status_code == 200              # same as /menu: public information


# ------------------------------------------------------------------ the channel runs a vertical AND a horizontal live at once
HLIVE = "Ko_lIgMJudY"
CHANNEL_LIVE = "https://www.youtube.com/@O_Poligrafo/live"
CHANNEL_STREAMS = "https://www.youtube.com/@O_Poligrafo/streams"


def watch(v):
    return f"https://www.youtube.com/watch?v={v}"


def live_info(v, w, h, live=True):
    return {"id": v, "is_live": live, "title": f"live {v}", "width": w, "height": h}


class TableYDL:
    """yt-dlp faked by URL: table[url] -> info dict / Exception; the /streams tab answers a flat entry list."""
    table: ClassVar[dict] = {}
    calls: ClassVar[list] = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        TableYDL.calls.append(url)
        a = TableYDL.table[url]
        if isinstance(a, Exception):
            raise a
        return a


@pytest.fixture
def ydl(monkeypatch):
    import yt_dlp
    TableYDL.table, TableYDL.calls = {}, []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", TableYDL)
    return TableYDL


def streams_tab(*pairs):
    return {"entries": [{"id": i, "live_status": st} for i, st in pairs]}


def test_a_horizontal_channel_live_is_used_without_listing_the_streams_tab(ydl):
    ydl.table[CHANNEL_LIVE] = live_info(HLIVE, 1920, 1080)
    assert REAL_INFO().id == HLIVE and ydl.calls == [CHANNEL_LIVE]


def test_looks_past_a_vertical_live_for_the_horizontal_one(ydl):
    ydl.table[CHANNEL_LIVE] = live_info(LIVE_ID, 1080, 1920)
    ydl.table[CHANNEL_STREAMS] = streams_tab((LIVE_ID, "is_live"), (HLIVE, "is_live"), ("Wjgwi" + "O4l4sY", "was_live"))
    ydl.table[watch(HLIVE)] = live_info(HLIVE, 1920, 1080)
    found = REAL_INFO()
    assert found == L.StreamInfo(HLIVE, f"live {HLIVE}", 1920, 1080) and found.horizontal
    assert watch(LIVE_ID) not in ydl.calls                  # the vertical one is not fetched twice
    assert watch("WjgwiO4l4sY") not in ydl.calls            # finished streams are never looked up


def test_only_a_vertical_live_is_returned_as_such(ydl):
    ydl.table[CHANNEL_LIVE] = live_info(LIVE_ID, 1080, 1920)
    ydl.table[CHANNEL_STREAMS] = streams_tab((LIVE_ID, "is_live"), ("WjgwiO4l4sY", "was_live"))
    found = REAL_INFO()
    assert found.id == LIVE_ID and not found.horizontal


def test_a_failing_streams_tab_keeps_the_vertical_answer(ydl):
    ydl.table[CHANNEL_LIVE] = live_info(LIVE_ID, 1080, 1920)
    ydl.table[CHANNEL_STREAMS] = OSError("down")
    assert REAL_INFO().id == LIVE_ID


def test_a_failing_candidate_does_not_hide_the_next_one(ydl):
    other = "abcdefghijk"
    ydl.table[CHANNEL_LIVE] = live_info(LIVE_ID, 1080, 1920)
    ydl.table[CHANNEL_STREAMS] = streams_tab((other, "is_live"), (HLIVE, "is_live"))
    ydl.table[watch(other)] = OSError("boom")
    ydl.table[watch(HLIVE)] = live_info(HLIVE, 1920, 1080)
    assert REAL_INFO().id == HLIVE


def test_not_live_at_all_does_not_list_anything(ydl):
    from yt_dlp.utils import DownloadError
    ydl.table[CHANNEL_LIVE] = DownloadError("ERROR: The channel is not currently live")
    assert REAL_INFO() is None and ydl.calls == [CHANNEL_LIVE]


def test_candidates_are_limited_and_must_look_like_video_ids(ydl):
    ydl.table[CHANNEL_LIVE] = live_info(LIVE_ID, 1080, 1920)
    ids = [f"vvvvvvvvvv{c}" for c in "abcdefg"]
    ydl.table[CHANNEL_STREAMS] = streams_tab(("short", "is_live"), *[(i, "is_live") for i in ids])
    for i in ids:
        ydl.table[watch(i)] = live_info(i, 1080, 1920)                      # all vertical: nothing to find
    assert REAL_INFO().id == LIVE_ID
    assert len([c for c in ydl.calls if "watch?v=" in c]) == L.MAX_CANDIDATES == 4
    assert watch("short") not in ydl.calls


def test_live_stream_current_prefers_the_horizontal_one(ydl):
    ydl.table[CHANNEL_LIVE] = live_info(LIVE_ID, 1080, 1920)
    ydl.table[CHANNEL_STREAMS] = streams_tab((LIVE_ID, "is_live"), (HLIVE, "is_live"))
    ydl.table[watch(HLIVE)] = live_info(HLIVE, 1920, 1080)
    s = L.LiveStream(resolver=lambda: REAL_INFO(), ttl=60, background=False)
    assert s.current() == HLIVE and s.info().horizontal
