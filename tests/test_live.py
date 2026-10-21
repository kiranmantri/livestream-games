"""Live stream detection (app/livestream.py) and the game page: stream on top, every card below, mine in RED."""
from __future__ import annotations

import re
import threading

import pytest
from starlette.testclient import TestClient

from app import livestream as L
from app import marks as M
from app import texts as T
from app.db import connect
from app.main import create_app
from tests.test_cards import load_phrases

VID = "LZcecJ5gxRI"
VID2 = "abcdefghijk"
REAL_RESOLVE = L.resolve_channel          # captured before the autouse fixture stubs it


# ------------------------------------------------------------------ LiveStream (cache / background)
def test_forced_id_wins_and_invalid_forced_id_is_ignored(monkeypatch):
    called = []
    s = L.LiveStream(resolver=lambda: called.append(1) or "zzzzzzzzzzz", forced_id=VID)
    assert s.current() == VID and s.known and called == []
    bad = L.LiveStream(resolver=lambda: None, forced_id="not an id!", background=False)
    assert bad.forced is None and bad.current() is None


def test_current_never_blocks_and_answers_after_the_background_lookup():
    gate, started = threading.Event(), threading.Event()

    def slow():
        started.set()
        gate.wait(5)
        return VID

    s = L.LiveStream(resolver=slow, ttl=60)
    assert s.current() is None and not s.known                   # returned at once, still searching
    assert started.wait(2)
    gate.set()
    for _ in range(100):                                          # wait for the thread to store the answer
        if s.known:
            break
        threading.Event().wait(0.02)
    assert s.known and s.current() == VID


def test_only_one_lookup_is_in_flight_at_a_time():
    gate, calls = threading.Event(), []

    def slow():
        calls.append(1)
        gate.wait(5)
        return VID

    s = L.LiveStream(resolver=slow, ttl=0)
    for _ in range(20):
        s.current()
    gate.set()
    for _ in range(100):
        if s.known:
            break
        threading.Event().wait(0.02)
    assert len(calls) == 1


def test_ttl_controls_how_often_it_looks():
    calls = []
    s = L.LiveStream(resolver=lambda: calls.append(1) or VID, ttl=3600, background=False)
    assert [s.current() for _ in range(5)] == [VID] * 5 and len(calls) == 1
    s0 = L.LiveStream(resolver=lambda: calls.append(1) or VID, ttl=0, background=False)
    for _ in range(3):
        s0.current()
    assert len(calls) == 1 + 3


def test_failed_lookup_keeps_previous_answer_and_not_live_clears_it():
    answers = iter([VID, L.LookupFailed("network"), None])

    def resolver():
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    s = L.LiveStream(resolver=resolver, ttl=0, background=False)
    assert s.current() == VID
    assert s.current() == VID                                     # lookup failed -> still the previous id
    assert s.current() is None                                    # a real "not live" answer clears it


def test_a_crashing_resolver_does_not_wedge_the_cache():
    n = []

    def boom():
        n.append(1)
        raise RuntimeError("boom")

    s = L.LiveStream(resolver=boom, ttl=0, background=True)
    s.current()
    for _ in range(100):
        if s.known:
            break
        threading.Event().wait(0.02)
    assert s.known and not s._busy


def test_resolver_answer_must_look_like_a_video_id():
    assert L.LiveStream(resolver=lambda: '"><script>', ttl=0, background=False).current() is None


# ------------------------------------------------------------------ resolve_channel (yt-dlp faked)
class FakeYDL:
    info: object = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        if isinstance(FakeYDL.info, Exception):
            raise FakeYDL.info
        return FakeYDL.info


def test_resolve_channel_cases(monkeypatch):
    import yt_dlp
    from yt_dlp.utils import DownloadError
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    FakeYDL.info = {"id": VID, "is_live": True}
    assert REAL_RESOLVE() == VID
    FakeYDL.info = {"id": VID, "is_live": False}
    assert REAL_RESOLVE() is None                                 # a finished stream is not "live"
    FakeYDL.info = {"id": "short", "is_live": True}
    assert REAL_RESOLVE() is None                                 # malformed id is never trusted
    FakeYDL.info = DownloadError("ERROR: The channel is not currently live")
    assert REAL_RESOLVE() is None
    FakeYDL.info = DownloadError("ERROR: Unable to download webpage: timed out")
    with pytest.raises(L.LookupFailed):
        REAL_RESOLVE()
    FakeYDL.info = OSError("network down")
    with pytest.raises(L.LookupFailed):
        REAL_RESOLVE()


# ------------------------------------------------------------------ web
@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    load_phrases(c, n=60)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


def make_web(test_db_url, live):
    app = create_app(test_db_url, live=live)
    tc = TestClient(app)
    tc.__enter__()

    def browser(ip="203.0.113.10"):
        return TestClient(app, client=(ip, 50000), follow_redirects=False)
    return tc, browser


@pytest.fixture
def web(test_db_url, conn):
    live = L.LiveStream(resolver=lambda: VID, ttl=3600, background=False)
    tc, browser = make_web(test_db_url, live)
    yield browser, live
    tc.__exit__(None, None, None)


def play(browser, names=("Ana", "Bia")):
    out = []
    for i, n in enumerate(names):
        b = browser(f"203.0.113.{10 + i}")
        assert b.post("/start" if i == 0 else "/join", data={"name": n}).status_code == 303
        out.append(b)
    return out


def marked_idx(conn, name):
    return sorted(r[0] for r in conn.execute(
        "SELECT m.cell_index FROM bingo_marks m JOIN bingo_cards c ON c.id=m.card_id JOIN users u ON u.id=c.user_id "
        "WHERE u.name=%s", (name,)))


def test_page_embeds_the_live_stream_beside_my_card(web):
    browser, _ = web
    ana, _ = play(browser)
    html = ana.get("/").text
    assert f'src="https://www.youtube-nocookie.com/embed/{VID}?' in html and "autoplay=1" in html
    assert f"https://www.youtube.com/watch?v={VID}" in html
    top = html.split('class="toprow"')[1].split('class="cardgrid small"')[0]     # top row: stream | my card (50/50)
    assert top.index('id="stream"') < top.index('class="cardbox mine"')
    assert 'class="cardbox other"' not in top
    assert f'hx-get="/stream?cur={VID}"' in html


def test_chat_panel_sits_between_the_stream_and_the_cards_column_end(web):
    browser, _ = web
    ana, _ = play(browser)
    html = ana.get("/").text
    left = html.split('class="leftcol"')[1].split('class="cardbox mine"')[0]               # left column of the top row
    assert left.index('id="stream"') < left.index('id="chat"')                              # chat BELOW the stream
    assert f"https://www.youtube.com/live_chat?v={VID}" in left and "embed_domain=testserver" in left
    assert T.CHAT_TITLE in left and f"https://www.youtube.com/watch?v={VID}" in left


def test_chat_panel_says_so_when_not_live(test_db_url, conn):
    live = L.LiveStream(resolver=lambda: None, ttl=3600, background=False)
    tc, browser = make_web(test_db_url, live)
    try:
        html = play(browser)[0].get("/").text
        assert "live_chat" not in html and T.CHAT_OFFLINE in html
    finally:
        tc.__exit__(None, None, None)


@pytest.mark.parametrize("host,expect", [("my.site.com", True), ("127.0.0.1", True), ('"><script>', False),
                                         ("a b", False), ("", False), ("x" * 300, False)])
def test_chat_embed_domain_is_only_added_for_a_sane_host(host, expect):
    from fasthtml.common import to_xml

    from app.main import chat_panel
    html = to_xml(chat_panel(VID, host))
    assert ("embed_domain=" in html) is expect and f"live_chat?v={VID}" in html
    assert "<script>" not in html


def test_page_says_so_when_the_channel_is_not_live(test_db_url, conn):
    live = L.LiveStream(resolver=lambda: None, ttl=3600, background=False)
    tc, browser = make_web(test_db_url, live)
    try:
        html = play(browser)[0].get("/").text
        assert "<iframe" not in html and T.STREAM_OFFLINE in html
    finally:
        tc.__exit__(None, None, None)


def test_page_says_searching_until_the_first_lookup_finishes(test_db_url, conn):
    gate = threading.Event()
    live = L.LiveStream(resolver=lambda: gate.wait(5) and VID, ttl=3600)
    tc, browser = make_web(test_db_url, live)
    try:
        html = play(browser)[0].get("/").text
        assert T.STREAM_SEARCHING in html and "<iframe" not in html
        assert re.search(r'hx-trigger="every 5s"[^>]*id="stream"', html)                # polls fast while searching
    finally:
        gate.set()
        tc.__exit__(None, None, None)


def test_stream_poll_is_quiet_until_the_live_id_changes(web):
    browser, live = web
    ana, _ = play(browser)
    assert ana.get(f"/stream?cur={VID}").status_code == 204
    live.forced = VID2
    r = ana.get(f"/stream?cur={VID}")
    assert r.status_code == 200 and r.headers["hx-refresh"] == "true"
    live.forced = None
    live._id, live.ttl = None, 3600
    assert ana.get(f"/stream?cur={VID}").headers["hx-refresh"] == "true"           # went offline -> reload


def test_my_card_is_in_the_top_row_with_the_red_box_and_is_the_only_interactive_one(web):
    browser, _ = web
    ana, bia = play(browser)
    html = ana.get("/").text
    top, below = html.split('class="toprow"')[1].split('class="cardgrid small"')
    assert 'class="cardbox mine"' in top and html.count('class="cardbox mine"') == 1
    assert 'class="cardbox other"' not in top and below.count('class="cardbox other"') == 1
    assert html.count('hx-post="/mark/') == 16                                        # only MY 16 cells are buttons
    assert "Ana (você)" in top and re.search(r'<span class="who">Bia</span>', below)
    assert "Bia (você)" in bia.get("/").text and 'class="who">Ana</span>' in bia.get("/").text


def test_there_is_no_scoreboard_on_the_game_page(web):
    browser, _ = web
    ana, _ = play(browser)
    html = ana.get("/").text
    assert 'id="leaderboard"' not in html and 'class="lb"' not in html and T.LEADERBOARD not in html
    assert "/leaderboard" not in html


def test_others_heading_only_when_there_are_others_and_cards_are_small_and_titled(web):
    browser, _ = web
    [ana] = play(browser, ("Ana",))
    assert T.OTHERS_HEADING not in ana.get("/").text
    browser("203.0.113.78").post("/join", data={"name": "Duda"})
    html = ana.get("/").text
    assert T.OTHERS_HEADING in html and 'class="cardgrid small"' in html
    assert re.search(r'<div class="cell ro[^"]*" title="[^"]+"', html)                # phrase kept as a tooltip


def test_other_cards_show_their_marks_read_only_and_never_mine_twice(web, conn):
    browser, _ = web
    ana, bia = play(browser)
    for i in (0, 1, 2):
        bia.post(f"/mark/{i}")

    def others_of(page: str) -> str:
        return page.split('id="others"')[1].split('class="lbwrap"')[0]

    others = others_of(ana.get("/").text)
    assert len(re.findall(r'class="cell ro on', others)) == 3 and "3/16" in others
    assert "hx-post" not in others                                                     # read-only: no buttons to click
    ana.post("/mark/5")
    assert len(re.findall(r'class="cell ro on', others_of(ana.get("/").text))) == 3    # my own marks never show there


def test_cards_poll_returns_204_until_someone_elses_marks_change(web):
    browser, _ = web
    ana, bia = play(browser)
    v = re.search(r'hx-get="/cards\?v=(\w+)"', ana.get("/").text).group(1)
    assert ana.get(f"/cards?v={v}").status_code == 204
    ana.post("/mark/4")                                                                # my own mark: no change for others
    assert ana.get(f"/cards?v={v}").status_code == 204
    bia.post("/mark/7")
    r = ana.get(f"/cards?v={v}")
    assert r.status_code == 200 and 'id="others"' in r.text and "1/16" in r.text
    v2 = re.search(r'hx-get="/cards\?v=(\w+)"', r.text).group(1)
    assert v2 != v and ana.get(f"/cards?v={v2}").status_code == 204
    bia.post("/mark/7")                                                                # unmark -> changes again
    assert ana.get(f"/cards?v={v2}").status_code == 200


def test_cards_poll_for_strangers_and_after_the_game_ends_reloads_the_page(web, conn):
    browser, _ = web
    ana, _bia = play(browser)
    assert browser("203.0.113.50").get("/cards").headers["hx-refresh"] == "true"
    from app import scoring as G
    from app import sessions as S
    G.finish_session(conn, S.get_active_session(conn).id)
    assert ana.get("/cards").headers["hx-refresh"] == "true"


def test_a_new_player_appears_on_the_others_poll(web):
    browser, _ = web
    [ana] = play(browser, ("Ana",))
    v = re.search(r'hx-get="/cards\?v=(\w+)"', ana.get("/").text).group(1)
    assert 'class="cardbox other"' not in ana.get("/").text
    browser("203.0.113.77").post("/join", data={"name": "Caio"})
    r = ana.get(f"/cards?v={v}")
    assert r.status_code == 200 and "Caio" in r.text


# ------------------------------------------------------------------ session_boards read model
def test_session_boards_lists_every_card_in_join_order_with_marks(conn, test_db_url):
    from app import cards as C
    from app import sessions as S
    s, a, _ = S.start_session(conn, "Ana", "10.0.0.1")
    _, b, _ = S.join_session(conn, "Bia", "10.0.0.2")
    _, c, _ = S.join_session(conn, "Caio", "10.0.0.3")                       # no card dealt yet -> not listed
    ca, cb = C.assign_card(conn, s.id, a.id), C.assign_card(conn, s.id, b.id)
    M.toggle_mark(conn, b.id, 3)
    boards = M.session_boards(conn, s.id)
    assert [x.name for x in boards] == ["Ana", "Bia"] and c.id not in {x.user_id for x in boards}
    assert boards[0].cells == ca.cells and boards[1].cells == cb.cells
    assert boards[0].marked == {} and list(boards[1].marked) == [3]
    assert M.session_boards(conn, __import__("uuid").uuid4()) == []
