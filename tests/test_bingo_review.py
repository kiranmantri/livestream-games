"""Bingo admin review of marks: Correto locks a mark (the player can no longer unmark it), Incorreto clears it (the player may
mark it again later), and the admin can kick a player (user + card deleted; the player sees the join screen with a notice and
may join again with a new card). Players see admin changes within one poll (GET /myboard?v=<what my screen shows>)."""
from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from app import admin as A
from app import cards as C
from app import marks as M
from app import scoring as G
from app import sessions as S
from app import texts as T
from app.db import connect
from tests.test_admin import PW, basic
from tests.test_lines import conn, web  # noqa: F401  (fixtures)

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
GAME = (STATIC / "game.js").read_text()
FX = (STATIC / "fx.js").read_text()
HX = {"HX-Request": "true"}


@pytest.fixture
def two(conn):  # noqa: F811
    """A running game with Ana (the starter) and Bia, each with a card; Ana marked cells 0 and 1, Bia cell 2."""
    s, ana, _ = S.start_session(conn, "Ana", "10.0.0.1")
    _, bia, _ = S.join_session(conn, "Bia", "10.0.0.2")
    C.assign_card(conn, s.id, ana.id, random.Random(1))
    C.assign_card(conn, s.id, bia.id, random.Random(2))
    M.toggle_mark(conn, ana.id, 0)
    M.toggle_mark(conn, ana.id, 1)
    M.toggle_mark(conn, bia.id, 2)
    return s, ana, bia


def card_id(conn, uid):  # noqa: F811
    return conn.execute("SELECT id FROM bingo_cards WHERE user_id = %s", (uid,)).fetchone()[0]


# ------------------------------------------------------------------ services
def test_confirm_locks_the_mark(conn, two, test_db_url):  # noqa: F811
    s, ana, _ = two
    assert M.confirm_mark(conn, s.id, ana.id, 0) is True
    assert M.confirm_mark(conn, s.id, ana.id, 0) is False                  # already confirmed: nothing to do
    with pytest.raises(M.CellLocked):
        M.toggle_mark(conn, ana.id, 0)
    with connect(test_db_url) as other:                                     # durable
        assert M.confirmed_cells(other, card_id(other, ana.id)) == frozenset({0})
        assert M.marked_cells(other, card_id(other, ana.id)) == {0, 1}
    assert M.toggle_mark(conn, ana.id, 1) is False                          # a pending mark still toggles


def test_confirm_needs_a_mark_and_a_running_game(conn, two):  # noqa: F811
    s, ana, _ = two
    assert M.confirm_mark(conn, s.id, ana.id, 5) is False                   # not marked
    G.finish_session(conn, s.id)
    assert M.confirm_mark(conn, s.id, ana.id, 1) is False                   # game over: the result is final
    assert M.reject_mark(conn, s.id, ana.id, 1) is False
    assert M.marked_cells(conn, card_id(conn, ana.id)) == {0, 1}


def test_reject_clears_the_mark_and_the_player_may_mark_it_again(conn, two, test_db_url):  # noqa: F811
    s, ana, _ = two
    M.confirm_mark(conn, s.id, ana.id, 0)
    assert M.reject_mark(conn, s.id, ana.id, 0) is True                     # also a confirmed one (the admin changed their mind)
    assert M.reject_mark(conn, s.id, ana.id, 0) is False
    with connect(test_db_url) as other:
        assert M.marked_cells(other, card_id(other, ana.id)) == {1}
    assert M.toggle_mark(conn, ana.id, 0) is True                           # not blocked
    assert M.confirmed_cells(conn, card_id(conn, ana.id)) == frozenset()    # pending again


def shared_phrase(conn, s, ana, bia):  # noqa: F811
    """(phrase_id, Ana's cell, Bia's cell) of a phrase on both cards, else skip."""
    a = {c["phrase_id"]: i for i, c in enumerate(C.get_card(conn, s.id, ana.id).cells) if c.get("phrase_id")}
    b = {c["phrase_id"]: i for i, c in enumerate(C.get_card(conn, s.id, bia.id).cells) if c.get("phrase_id")}
    both = sorted(set(a) & set(b))
    if not both:
        pytest.skip("the seeded cards share no phrase")
    return both[0], a[both[0]], b[both[0]]


@pytest.fixture
def shared(conn):  # noqa: F811
    """Ana and Bia whose cards share a phrase (the same card, seeded identically); nothing marked."""
    s, ana, _ = S.start_session(conn, "Ana", "10.0.0.1")
    _, bia, _ = S.join_session(conn, "Bia", "10.0.0.2")
    C.assign_card(conn, s.id, ana.id, random.Random(7))
    C.assign_card(conn, s.id, bia.id, random.Random(7))
    return (s, ana, bia, *shared_phrase(conn, s, ana, bia))


def test_phrase_marks_one_row_per_phrase_pending_first(conn, two):  # noqa: F811
    s, ana, _bia = two
    M.confirm_mark(conn, s.id, ana.id, 1)
    rows = M.phrase_marks(conn, s.id)
    cards = {u: C.get_card(conn, s.id, u.id).cells for u in (ana, _bia)}
    assert [(r.text, r.confirmed, [w.name for w in r.who]) for r in rows] == [
        (cards[_bia][2]["text"], False, ["Bia"]), (cards[ana][0]["text"], False, ["Ana"]), (cards[ana][1]["text"], True, ["Ana"])]


def test_a_phrase_marked_by_two_players_is_one_row(conn, shared):  # noqa: F811
    s, ana, bia, pid, ia, ib = shared
    M.toggle_mark(conn, ana.id, ia)
    M.toggle_mark(conn, bia.id, ib)
    [row] = M.phrase_marks(conn, s.id)
    assert row.phrase_id == pid and [(w.name, w.cell_index) for w in row.who] == [("Ana", ia), ("Bia", ib)]


def test_correto_confirms_the_phrase_for_everybody_and_for_later_marks(conn, shared, test_db_url):  # noqa: F811
    s, ana, bia, _pid, ia, ib = shared
    _, cid, _ = S.join_session(conn, "Cris", "10.0.0.3")
    C.assign_card(conn, s.id, cid.id, random.Random(7))                     # the same card again
    M.toggle_mark(conn, ana.id, ia)
    M.toggle_mark(conn, bia.id, ib)
    assert M.confirm_mark(conn, s.id, bia.id, ib) is True                   # from Bia's row: Ana's mark too
    with connect(test_db_url) as other:
        assert M.confirmed_cells(other, card_id(other, ana.id)) == frozenset({ia})
        assert M.confirmed_cells(other, card_id(other, bia.id)) == frozenset({ib})
    with pytest.raises(M.CellLocked):
        M.toggle_mark(conn, ana.id, ia)
    assert M.toggle_mark(conn, cid.id, ia) is True                          # Cris marks it AFTER the confirmation
    assert M.confirmed_cells(conn, card_id(conn, cid.id)) == frozenset({ia})  # confirmed at once (locked)
    with pytest.raises(M.CellLocked):
        M.toggle_mark(conn, cid.id, ia)
    [row] = M.phrase_marks(conn, s.id)
    assert row.confirmed and len(row.who) == 3


def test_incorreto_clears_the_phrase_for_everybody_and_unconfirms_it(conn, shared, test_db_url):  # noqa: F811
    s, ana, bia, pid, ia, ib = shared
    M.toggle_mark(conn, ana.id, ia)
    M.toggle_mark(conn, bia.id, ib)
    M.confirm_phrase(conn, s.id, pid)
    assert M.reject_mark(conn, s.id, ana.id, ia) is True
    with connect(test_db_url) as other:
        assert M.marked_cells(other, card_id(other, ana.id)) == set()
        assert M.marked_cells(other, card_id(other, bia.id)) == set()
    assert M.toggle_mark(conn, bia.id, ib) is True                          # not blocked, and pending again
    assert M.confirmed_cells(conn, card_id(conn, bia.id)) == frozenset()
    assert M.phrase_marks(conn, s.id)[0].confirmed is False


def test_phrase_review_only_in_a_running_game(conn, shared):  # noqa: F811
    s, ana, _bia, pid, ia, _ib = shared
    M.toggle_mark(conn, ana.id, ia)
    G.finish_session(conn, s.id)
    assert M.confirm_phrase(conn, s.id, pid) == 0 and M.reject_phrase(conn, s.id, pid) == 0
    assert M.confirmed_cells(conn, card_id(conn, ana.id)) == frozenset()


def test_kick_deletes_the_player_and_frees_the_name(conn, two, test_db_url):  # noqa: F811
    s, ana, _bia = two
    assert S.kick_player(conn, s.id, ana.id) is True                        # the starter too (created_by is cleared)
    assert S.kick_player(conn, s.id, ana.id) is False
    with connect(test_db_url) as other:
        assert other.execute("SELECT count(*) FROM users WHERE id = %s", (ana.id,)).fetchone()[0] == 0
        assert other.execute("SELECT count(*) FROM bingo_cards WHERE user_id = %s", (ana.id,)).fetchone()[0] == 0
        assert other.execute("SELECT created_by FROM game_sessions WHERE id = %s", (s.id,)).fetchone()[0] is None
        assert S.get_active_session(other).id == s.id                     # the game goes on
    assert [r.name for r in A.players(conn, s.id)] == ["Bia"]
    _, again, _ = S.join_session(conn, "Ana", "10.0.0.1")                  # same name, a new player and card
    assert again.id != ana.id


def test_kick_only_in_a_running_game(conn, two):  # noqa: F811
    s, ana, _ = two
    G.finish_session(conn, s.id)
    assert S.kick_player(conn, s.id, ana.id) is False
    assert [r.name for r in A.players(conn, s.id)] == ["Ana", "Bia"]


def test_finish_after_a_kick_still_works(conn, two):  # noqa: F811
    s, ana, bia = two
    S.kick_player(conn, s.id, ana.id)
    res = G.finish_session(conn, s.id)
    assert res.winner.user_id == bia.id


# ------------------------------------------------------------------ player pages
def ids(html):
    return [int(i) for i in re.findall(r'id="cell-(\d+)"', html)]


def player(web, ip="203.0.113.10", name="Ana", first=True):  # noqa: F811
    b = web(ip)
    assert b.post("/start" if first else "/join", data={"name": name}).status_code == 303
    return b


def uid_of(conn, name):  # noqa: F811
    return conn.execute("SELECT u.id FROM users u JOIN game_sessions s ON s.id = u.session_id "
                        "WHERE s.status = 'active' AND u.name = %s", (name,)).fetchone()[0]


def test_a_confirmed_cell_is_locked_on_my_board(web, conn):  # noqa: F811
    a = player(web)
    i = ids(a.get("/").text)[0]
    a.post(f"/mark/{i}")
    s = S.get_active_session(conn)
    M.confirm_mark(conn, s.id, uid_of(conn, "Ana"), i)
    tag = re.search(rf'<button[^>]*id="cell-{i}"[^>]*>', a.get("/").text).group(0)
    assert re.search(r'class="cell[^"]*\blocked\b', tag) and 'aria-pressed="true"' in tag and T.CELL_LOCKED in tag
    r = a.post(f"/mark/{i}", headers=HX)                                    # an unmark is refused, the mark stays
    assert T.CELL_LOCKED in r.text and r.headers.get("hx-retarget") == "#game-board"
    assert M.marked_cells(conn, card_id(conn, uid_of(conn, "Ana"))) == {i}


def test_myboard_answers_204_while_my_screen_matches_the_server(web, conn):  # noqa: F811
    a = player(web)
    cells = ids(a.get("/").text)
    a.post(f"/mark/{cells[0]}")
    a.post(f"/mark/{cells[1]}")
    cv = re.search(r'data-cv="([^"]+)"', a.get("/").text).group(1)
    v = f"m={min(cells[0], cells[1])},{max(cells[0], cells[1])};l=;r=;c={cv};s="
    assert a.get(f"/myboard?v={v}", headers=HX).status_code == 204
    s, uid = S.get_active_session(conn), uid_of(conn, "Ana")
    M.confirm_mark(conn, s.id, uid, cells[0])
    r = a.get(f"/myboard?v={v}", headers=HX)
    assert r.status_code == 200 and 'id="game-board"' in r.text and "locked" in r.text
    M.reject_mark(conn, s.id, uid, cells[1])
    r = a.get(f"/myboard?v=m={cells[0]},{cells[1]};l={cells[0]};r=;c={cv};s=", headers=HX)
    assert r.status_code == 200 and r.text.count('aria-pressed="true"') == 1


def test_board_carries_the_signature_the_poll_compares():  # the JS must build exactly the server's format
    assert "'m=' +" in GAME and "';l=' +" in GAME and "';r=' +" in GAME
    assert "'/myboard?v='" in GAME


def test_kicked_player_sees_the_join_screen_with_a_notice_and_can_join_again(web, conn):  # noqa: F811
    a = player(web)
    b = player(web, "203.0.113.11", "Bia", first=False)
    s = S.get_active_session(conn)
    S.kick_player(conn, s.id, uid_of(conn, "Bia"))
    assert b.get("/myboard?v=x", headers=HX).headers.get("hx-refresh") == "true"
    html = b.get("/").text
    assert T.ERRORS["kicked"] in html and 'action="/join"' in html
    assert b.post("/join", data={"name": "Bia"}).status_code == 303
    html = b.get("/").text
    assert len(ids(html)) == 16 and T.ERRORS["kicked"] not in html
    assert 'id="game-board"' in a.get("/").text                            # the others play on


def test_game_js_never_sends_a_tap_on_a_locked_cell():
    tap = GAME.split("closest('.cardbox.mine button.cell')", 1)[1].split("c.classList.add('pending')", 1)[0]
    assert "c.classList.contains('locked')" in tap


def test_fireworks_stop_when_a_redraw_leaves_the_card_not_full():
    assert "htmx:afterSettle" in FX and "myFull()" in FX.split("htmx:afterSettle", 1)[1].split("});", 1)[0]


# ------------------------------------------------------------------ admin
def admin_data(conn, name, cell):  # noqa: F811
    s = S.get_active_session(conn)
    return {"session_id": str(s.id), "user_id": str(uid_of(conn, name)), "cell": str(cell), "csrf": A.csrf_token(PW, s.id)}


def test_admin_page_lists_every_mark_with_correct_and_incorrect(web, conn):  # noqa: F811
    a = player(web)
    cells = ids(a.get("/").text)
    a.post(f"/mark/{cells[0]}")
    html = web().get("/admin", headers=basic()).text
    panel = html.split('id="marks-panel"', 1)[1].split('id="cards-panel"', 1)[0]
    text = C.get_card(conn, S.get_active_session(conn).id, uid_of(conn, "Ana")).cells[cells[0]]["text"]
    assert "Ana" in panel and text in panel
    assert 'hx-post="/admin/confirm"' in panel and 'hx-post="/admin/reject"' in panel
    assert 'hx-get="/admin/marks?sid=' in html                                 # polled like the cards


def test_admin_confirm_and_reject_over_http(web, conn):  # noqa: F811
    a = player(web)
    cells = ids(a.get("/").text)
    a.post(f"/mark/{cells[0]}")
    a.post(f"/mark/{cells[1]}")
    adm = web()
    data = admin_data(conn, "Ana", cells[0])
    assert adm.post("/admin/confirm", data={**data, "csrf": "bad"}, headers=basic()).status_code == 403
    assert adm.post("/admin/confirm", data=data).status_code == 401
    r = adm.post("/admin/confirm", data=data, headers={**basic(), **HX})
    assert r.status_code == 200 and 'id="marks-panel"' in r.text and T.ADMIN_MARK_CONFIRMED in r.text
    cid = card_id(conn, uid_of(conn, "Ana"))
    assert M.confirmed_cells(conn, cid) == frozenset({cells[0]})
    r = adm.post("/admin/reject", data=admin_data(conn, "Ana", cells[1]), headers=basic())
    assert r.status_code == 303                                               # without htmx: a plain post + redirect
    assert M.marked_cells(conn, cid) == {cells[0]}
    assert "🔒" in adm.get("/admin", headers=basic()).text.split('id="cards-panel"', 1)[1]   # the lock on the admin's card


def test_admin_marks_poll_is_204_until_something_changes(web, conn):  # noqa: F811
    a = player(web)
    cells = ids(a.get("/").text)
    a.post(f"/mark/{cells[0]}")
    adm = web()
    url = re.search(r'hx-get="(/admin/marks\?[^"]+)"', adm.get("/admin", headers=basic()).text).group(1).replace("&amp;", "&")
    assert adm.get(url, headers=basic()).status_code == 204
    a.post(f"/mark/{cells[1]}")
    r = adm.get(url, headers=basic())
    assert r.status_code == 200 and 'id="marks-panel"' in r.text


def test_admin_kick_over_http(web, conn):  # noqa: F811
    player(web)
    player(web, "203.0.113.11", "Bia", first=False)
    adm = web()
    html = adm.get("/admin", headers=basic()).text
    assert 'action="/admin/kick"' in html and T.ADMIN_KICK_CONFIRM.split("{", 1)[0] in html
    data = admin_data(conn, "Bia", 0)
    data.pop("cell")
    assert adm.post("/admin/kick", data={**data, "csrf": "bad"}, headers=basic()).status_code == 403
    assert adm.post("/admin/kick", data=data).status_code == 401
    r = adm.post("/admin/kick", data=data, headers=basic())
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert [p.name for p in A.players(conn, S.get_active_session(conn).id)] == ["Ana"]


def test_admin_marks_list_one_name_per_line_without_a_count(web, conn, shared):  # noqa: F811
    _s, ana, bia, _pid, ia, ib = shared
    M.toggle_mark(conn, ana.id, ia)
    M.toggle_mark(conn, bia.id, ib)
    html = web("203.0.113.99").get("/admin", headers=basic()).text
    panel = html.split('id="marks-panel"', 1)[1].split('id="cards-panel"', 1)[0]
    who = re.search(rf'<td data-label="{T.ADMIN_COL_PLAYERS}">(.*?)</td>', panel, re.DOTALL).group(1)
    assert 'class="pill"' not in who and re.findall(r"<li>\s*(\w+)", who) == ["Ana", "Bia"]
    css = (STATIC / "app.css").read_text()
    assert re.search(r"\.mkwho \{[^}]*\}", css) and "flex" not in re.search(r"\.mkwho \{[^}]*\}", css).group(0)
