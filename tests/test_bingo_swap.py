"""Bingo secret keywords and card swaps (.hermes/plans/bingo-keywords-swap.md).

The admin picks up to 3 keywords among the phrases on this game's cards (a keyword is frozen once a mark of it is confirmed).
A confirmed mark of a keyword earns its player ONE swap: any time until the game ends they may exchange their card (with its
marks and confirmations) with a player of their choice, who cannot refuse, except players whose current card has that keyword
marked; or decline it. An Incorreto on the earning mark takes an unused swap back. Only the admin sees the keywords."""
from __future__ import annotations

import random
import re

import psycopg
import pytest

from app import admin as A
from app import cards as C
from app import marks as M
from app import scoring as G
from app import sessions as S
from app import swaps as W
from app import texts as T
from app.db import connect
from tests.test_admin import PW, basic
from tests.test_lines import conn, web  # noqa: F401  (fixtures)

HX = {"HX-Request": "true"}


@pytest.fixture
def game(conn):  # noqa: F811
    """Ana, Bia and Caio each with a card (fixed seeds)."""
    s, ana, _ = S.start_session(conn, "Ana", "10.0.0.1")
    _, bia, _ = S.join_session(conn, "Bia", "10.0.0.2")
    _, caio, _ = S.join_session(conn, "Caio", "10.0.0.3")
    for i, p in enumerate((ana, bia, caio)):
        C.assign_card(conn, s.id, p.id, random.Random(i + 1))
    return s, ana, bia, caio


def card(conn, s, p):  # noqa: F811
    return C.get_card(conn, s.id, p.id)


def pid(conn, s, p, i):  # noqa: F811
    return card(conn, s, p).cells[i]["phrase_id"]


def find(conn, s, p, phrase_id):  # noqa: F811
    """Index of a phrase on that player's card, or None."""
    return next((i for i, c in enumerate(card(conn, s, p).cells) if c.get("phrase_id") == phrase_id), None)


def keyword_marked_by_ana(conn, s, ana):  # noqa: F811
    """A keyword = Ana's cell 0; Ana marks it and the admin confirms it."""
    k = pid(conn, s, ana, 0)
    W.add_keyword(conn, s.id, k)
    M.toggle_mark(conn, ana.id, 0)
    assert M.confirm_mark(conn, s.id, ana.id, 0)
    return k


# ------------------------------------------------------------------ keywords
def test_keyword_options_are_the_phrases_on_this_games_cards(conn, game):  # noqa: F811
    s, ana, bia, caio = game
    opts = {o.phrase_id: o for o in W.keyword_options(conn, s.id)}
    on_cards = {c["phrase_id"] for p in (ana, bia, caio) for c in card(conn, s, p).cells if "phrase_id" in c}
    assert set(opts) == on_cards
    k = pid(conn, s, ana, 0)
    assert opts[k].text == card(conn, s, ana).cells[0]["text"] and opts[k].cards >= 1
    off = conn.execute("SELECT id FROM phrases WHERE NOT (id = ANY(%s)) LIMIT 1", (list(on_cards),)).fetchone()[0]
    with pytest.raises(W.KeywordError) as e:
        W.add_keyword(conn, s.id, off)
    assert e.value.code == "not_on_cards"


def test_at_most_three_keywords_and_only_while_the_game_runs(conn, game):  # noqa: F811
    s, ana, *_ = game
    for i in range(3):
        W.add_keyword(conn, s.id, pid(conn, s, ana, i))
    W.add_keyword(conn, s.id, pid(conn, s, ana, 0))                      # already there: no-op
    with pytest.raises(W.KeywordError) as e:
        W.add_keyword(conn, s.id, pid(conn, s, ana, 3))
    assert e.value.code == "max"
    assert [k.phrase_id for k in W.keywords(conn, s.id)] == [pid(conn, s, ana, i) for i in range(3)]
    G.finish_session(conn, s.id)
    with pytest.raises(W.KeywordError) as e:
        W.remove_keyword(conn, s.id, pid(conn, s, ana, 0))
    assert e.value.code == "not_active"


def test_a_keyword_is_frozen_once_a_mark_of_it_is_confirmed(conn, game):  # noqa: F811
    s, ana, *_ = game
    k = keyword_marked_by_ana(conn, s, ana)
    assert [x.frozen for x in W.keywords(conn, s.id)] == [True]
    with pytest.raises(W.KeywordError) as e:
        W.remove_keyword(conn, s.id, k)
    assert e.value.code == "frozen"
    M.reject_mark(conn, s.id, ana.id, 0)                                  # the confirmation is gone: free again
    W.remove_keyword(conn, s.id, k)
    assert W.keywords(conn, s.id) == []


# ------------------------------------------------------------------ earning a swap
def test_a_confirmed_keyword_mark_earns_one_swap(conn, game, test_db_url):  # noqa: F811
    s, ana, bia, _ = game
    k = keyword_marked_by_ana(conn, s, ana)
    with connect(test_db_url) as other:
        r = W.open_right(other, ana.id)
    assert r is not None and r.phrase_id == k
    M.toggle_mark(conn, bia.id, 1)
    M.confirm_mark(conn, s.id, bia.id, 1)                                 # not a keyword (unless Bia's cell 1 is k)
    assert (W.open_right(conn, bia.id) is None) == (pid(conn, s, bia, 1) != k)


def test_a_keyword_added_after_the_confirmation_also_earns(conn, game):  # noqa: F811
    s, ana, *_ = game
    M.toggle_mark(conn, ana.id, 2)
    M.confirm_mark(conn, s.id, ana.id, 2)
    assert W.open_right(conn, ana.id) is None
    W.add_keyword(conn, s.id, pid(conn, s, ana, 2))
    assert W.open_right(conn, ana.id) is not None


def test_pending_marks_earn_nothing_and_incorreto_takes_an_unused_swap_back(conn, game):  # noqa: F811
    s, ana, *_ = game
    W.add_keyword(conn, s.id, pid(conn, s, ana, 0))
    M.toggle_mark(conn, ana.id, 0)
    assert W.open_right(conn, ana.id) is None                             # only the admin's confirmation counts
    M.confirm_mark(conn, s.id, ana.id, 0)
    assert W.open_right(conn, ana.id) is not None
    M.reject_mark(conn, s.id, ana.id, 0)
    assert W.open_right(conn, ana.id) is None


def test_a_keyword_chosen_while_its_mark_is_pending_earns_nothing_yet(conn, game):  # noqa: F811
    s, ana, *_ = game
    M.toggle_mark(conn, ana.id, 0)
    W.add_keyword(conn, s.id, pid(conn, s, ana, 0))
    assert W.open_right(conn, ana.id) is None and W.session_rights(conn, s.id) == []


def test_incorreto_leaves_a_declined_swap_in_the_log(conn, game):  # noqa: F811
    s, ana, *_ = game
    keyword_marked_by_ana(conn, s, ana)
    W.decline(conn, W.open_right(conn, ana.id).id, ana.id)
    M.reject_mark(conn, s.id, ana.id, 0)
    assert [x.declined_at is not None for x in W.session_rights(conn, s.id)] == [True]


# ------------------------------------------------------------------ targets and the swap
@pytest.fixture
def twins(conn):  # noqa: F811
    """Ana and Bia with the SAME card (same seed), so every phrase is on both; Ana's cell 0 is a confirmed keyword."""
    s, ana, _ = S.start_session(conn, "Ana", "10.0.0.1")
    _, bia, _ = S.join_session(conn, "Bia", "10.0.0.2")
    _, caio, _ = S.join_session(conn, "Caio", "10.0.0.3")
    for p, seed in ((ana, 5), (bia, 5), (caio, 6)):
        C.assign_card(conn, s.id, p.id, random.Random(seed))
    W.add_keyword(conn, s.id, pid(conn, s, ana, 0))
    M.toggle_mark(conn, ana.id, 0)
    return s, ana, bia, caio


def test_a_later_mark_of_a_confirmed_keyword_earns_at_once_and_excludes_the_target(conn, twins):  # noqa: F811
    s, ana, bia, caio = twins
    M.toggle_mark(conn, bia.id, 0)                                        # Bia marks it too, before the confirmation
    M.confirm_mark(conn, s.id, ana.id, 0)                                 # confirms both: Bia earns one as well
    M.reject_mark(conn, s.id, ana.id, 0)                                  # ... and Incorreto clears both
    M.toggle_mark(conn, ana.id, 0)
    M.confirm_mark(conn, s.id, ana.id, 0)                                 # only Ana now
    M.toggle_mark(conn, bia.id, 0)                                        # a later mark: confirmed at once, Bia earns too
    assert W.open_right(conn, bia.id) is not None
    M.reject_mark(conn, s.id, ana.id, 0)
    M.toggle_mark(conn, ana.id, 0)
    M.confirm_mark(conn, s.id, ana.id, 0)
    if find(conn, s, caio, pid(conn, s, ana, 0)) is not None:
        pytest.skip("Caio has the keyword too")
    r = W.open_right(conn, ana.id)
    assert {t.name for t in W.targets(conn, r)} == {"Bia", "Caio"}       # Bia's mark was cleared by the Incorreto
    M.toggle_mark(conn, bia.id, 0)                                        # Bia marks it again: confirmed at once
    assert {t.name for t in W.targets(conn, r)} == {"Caio"}


def test_a_pending_keyword_mark_excludes_the_target(conn, twins):  # noqa: F811
    s, ana, bia, _caio = twins
    M.confirm_mark(conn, s.id, ana.id, 0)
    r = W.open_right(conn, ana.id)
    i = find(conn, s, bia, r.phrase_id)
    assert i == 0
    M.revoke_mark(conn, s.id, bia.id, 1)                                  # unrelated
    # a pending (unconfirmed) mark of the keyword: insert it directly, since the phrase is confirmed (toggle would lock it)
    conn.execute("INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s, 0)", (card(conn, s, bia).id,))
    assert "Bia" not in {t.name for t in W.targets(conn, r)}
    with pytest.raises(W.SwapError) as e:
        W.use_swap(conn, r.id, ana.id, bia.id)
    assert e.value.code == "not_eligible"


def test_a_declined_swap_cannot_be_used(conn, game):  # noqa: F811
    s, ana, bia, _ = game
    keyword_marked_by_ana(conn, s, ana)
    r = W.open_right(conn, ana.id)
    W.decline(conn, r.id, ana.id)
    with pytest.raises(W.SwapError) as e:
        W.use_swap(conn, r.id, ana.id, bia.id)
    assert e.value.code == "used"



def test_targets_exclude_me_and_whoever_has_the_keyword_marked(conn, game):  # noqa: F811
    s, ana, bia, _caio = game
    k = keyword_marked_by_ana(conn, s, ana)
    r = W.open_right(conn, ana.id)
    assert {t.name for t in W.targets(conn, r)} == {"Bia", "Caio"}
    # put the keyword on Bia's card as a PENDING mark if it is there; else check that an unmarked keyword does not exclude
    i = find(conn, s, bia, k)
    if i is not None:
        M.toggle_mark(conn, bia.id, i)
        assert {t.name for t in W.targets(conn, r)} == {"Caio"}


def test_swap_exchanges_cards_with_their_marks_confirmations_and_blocks(conn, game, test_db_url):  # noqa: F811
    s, ana, bia, _ = game
    keyword_marked_by_ana(conn, s, ana)
    M.toggle_mark(conn, ana.id, 5)
    for i in (3, 4):
        M.toggle_mark(conn, bia.id, i)
    M.confirm_mark(conn, s.id, bia.id, 3)
    M.revoke_mark(conn, s.id, bia.id, 7)
    a_cells, b_cells = card(conn, s, ana).cells, card(conn, s, bia).cells
    r = W.open_right(conn, ana.id)
    W.use_swap(conn, r.id, ana.id, bia.id)
    with connect(test_db_url) as other:
        ca, cb = C.get_card(other, s.id, ana.id), C.get_card(other, s.id, bia.id)
        assert ca.cells == b_cells and cb.cells == a_cells
        assert M.marked_cells(other, ca.id) == {3, 4} and M.confirmed_cells(other, ca.id) == frozenset({3})
        assert M.revoked_cells(other, ca.id) == frozenset({7})
        assert M.marked_cells(other, cb.id) == {0, 5} and M.confirmed_cells(other, cb.id) == frozenset({0})
        assert W.open_right(other, ana.id) is None
    with pytest.raises(W.SwapError) as e:
        W.use_swap(conn, r.id, ana.id, bia.id)
    assert e.value.code == "used"


def test_swap_refuses_a_bad_target_or_someone_elses_right(conn, game):  # noqa: F811
    s, ana, bia, caio = game
    keyword_marked_by_ana(conn, s, ana)
    r = W.open_right(conn, ana.id)
    for holder, target, code in ((bia.id, caio.id, "not_yours"), (ana.id, ana.id, "not_eligible")):
        with pytest.raises(W.SwapError) as e:
            W.use_swap(conn, r.id, holder, target)
        assert e.value.code == code
    G.finish_session(conn, s.id)
    with pytest.raises(W.SwapError) as e:
        W.use_swap(conn, r.id, ana.id, bia.id)
    assert e.value.code == "not_active"


def test_decline_closes_the_right_without_a_swap(conn, game):  # noqa: F811
    s, ana, _bia, _ = game
    keyword_marked_by_ana(conn, s, ana)
    before = card(conn, s, ana).cells
    W.decline(conn, W.open_right(conn, ana.id).id, ana.id)
    assert W.open_right(conn, ana.id) is None and card(conn, s, ana).cells == before


def test_cards_stay_immutable_outside_a_swap(conn, game):  # noqa: F811
    s, ana, *_ = game
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE bingo_cards SET cells = cells WHERE id = %s", (card(conn, s, ana).id,))


def test_an_unused_swap_goes_when_the_card_with_its_mark_leaves_with_a_kicked_player(conn, game):  # noqa: F811
    s, ana, bia, _ = game
    k1, k2 = pid(conn, s, ana, 0), pid(conn, s, ana, 1)
    for i, k in ((0, k1), (1, k2)):
        W.add_keyword(conn, s.id, k)
        M.toggle_mark(conn, ana.id, i)
        M.confirm_mark(conn, s.id, ana.id, i)
    first = W.open_right(conn, ana.id)
    if find(conn, s, bia, k1) is not None or find(conn, s, bia, k2) is not None:
        pytest.skip("seeded cards share a keyword")
    W.use_swap(conn, first.id, ana.id, bia.id)                            # Bia now holds the card with Ana's k2 mark
    assert W.open_right(conn, ana.id) is not None
    S.kick_player(conn, s.id, bia.id)
    assert W.open_right(conn, ana.id) is None                             # its earning mark left the game
    assert [x.used_at is not None for x in W.session_rights(conn, s.id)] == [True]


def test_kicks_and_the_final_ranking_after_a_swap(conn, game):  # noqa: F811
    s, ana, bia, caio = game
    keyword_marked_by_ana(conn, s, ana)
    for i in (1, 2, 3):
        M.toggle_mark(conn, bia.id, i)
    W.use_swap(conn, W.open_right(conn, ana.id).id, ana.id, bia.id)        # Ana takes Bia's 3 marks, Bia gets Ana's 1
    S.kick_player(conn, s.id, caio.id)
    log = W.session_rights(conn, s.id)
    assert [(x.holder, x.target, x.used_at is not None) for x in log] == [("Ana", "Bia", True)]
    S.kick_player(conn, s.id, bia.id)
    assert W.session_rights(conn, s.id)[0].target is None                 # the log keeps the swap, the name is gone
    assert G.finish_session(conn, s.id).winner.user_id == ana.id


# ------------------------------------------------------------------ player pages
def uid_of(conn, name):  # noqa: F811
    return conn.execute("SELECT u.id FROM users u JOIN game_sessions s ON s.id = u.session_id "
                        "WHERE s.status = 'active' AND u.name = %s", (name,)).fetchone()[0]


def ids(html):
    return [int(i) for i in re.findall(r'id="cell-(\d+)"', html)]


@pytest.fixture
def players(web, conn):  # noqa: F811
    a, b, c = web("203.0.113.10"), web("203.0.113.11"), web("203.0.113.12")
    assert a.post("/start", data={"name": "Ana"}).status_code == 303
    assert b.post("/join", data={"name": "Bia"}).status_code == 303
    assert c.post("/join", data={"name": "Caio"}).status_code == 303
    for x in (a, b, c):
        x.get("/")                                                         # deals the cards
    s = S.get_active_session(conn)
    ana = S._get_player(conn, uid_of(conn, "Ana"))
    k = keyword_marked_by_ana(conn, s, ana)
    return a, b, c, s, k


def swapbox(html):
    m = re.search(r'<div[^>]*id="swapbox".*?</form>\s*</div>', html, re.DOTALL)
    return m.group(0) if m else ""


def test_the_winner_of_a_swap_sees_the_offer_and_nobody_sees_the_keywords(players, conn):  # noqa: F811
    a, b, c, s, _k = players
    text = C.get_card(conn, s.id, uid_of(conn, "Ana")).cells[0]["text"]
    box = swapbox(a.get("/").text)
    assert T.SWAP_OFFER in box and "Bia" in box and "Caio" in box and text not in box
    assert 'action="/swap"' in box and 'action="/swap/decline"' in box
    for other in (b, c):
        html = other.get("/").text
        assert 'id="swapbox"' not in html and T.SWAP_OFFER not in html
    assert a.get("/myboard?v=x", headers=HX).text.count('id="swapbox"') == 1   # the 5 s redraw keeps it


def test_post_swap_exchanges_the_cards_and_tells_the_other_player(players, conn):  # noqa: F811
    a, b, _c, s, k = players
    before_b = b.get("/").text
    cv = re.search(r'data-cv="([^"]+)"[^>]*id="game-board"', before_b).group(1)
    v = f"m=;l=;r=;c={cv};s="                                              # what Bia's screen shows (nothing marked)
    assert b.get(f"/myboard?v={v}", headers=HX).status_code == 204          # before the swap: unchanged
    rid = re.search(r'name="right" value="(\d+)"', a.get("/").text).group(1)
    r = a.post("/swap", data={"right": rid, "target": str(uid_of(conn, "Bia"))})
    assert r.status_code == 303 and r.headers["location"] == "/"
    html = a.get("/").text
    assert 'id="swapbox"' not in html and T.SWAP_DONE.format(name="Bia", at="X").split(" às")[0] in html
    redraw = b.get(f"/myboard?v={v}", headers=HX)                          # Bia's next poll: her card changed
    assert redraw.status_code == 200 and T.SWAP_RECEIVED.format(name="Ana", at="X").split(" às")[0] in redraw.text
    assert ids(redraw.text) == ids(before_b)                                  # same grid, other phrases
    assert re.search(r'data-cv="([^"]+)"', redraw.text).group(1) != cv
    assert C.get_card(conn, s.id, uid_of(conn, "Bia")).cells[0]["phrase_id"] == k


def test_a_bad_swap_post_shows_the_reason_on_my_board(players, conn):  # noqa: F811
    a, *_ = players
    rid = re.search(r'name="right" value="(\d+)"', a.get("/").text).group(1)
    r = a.post("/swap", data={"right": rid, "target": str(uid_of(conn, "Ana"))})
    assert r.status_code == 303 and "swap_err=not_eligible" in r.headers["location"]
    assert T.SWAP_ERRORS["not_eligible"] in a.get(r.headers["location"]).text


def test_post_decline(players, conn):  # noqa: F811
    a, *_ = players
    rid = re.search(r'name="right" value="(\d+)"', a.get("/").text).group(1)
    assert a.post("/swap/decline", data={"right": rid}).status_code == 303
    assert 'id="swapbox"' not in a.get("/").text


def test_board_signature_follows_the_card_content_and_the_offer():
    from pathlib import Path
    game = (Path(__file__).resolve().parents[1] / "app" / "static" / "game.js").read_text()
    assert "';c=' +" in game and "';s=' +" in game                           # the cells + the card version and the open offer
    assert "dataset.cv" in game and "dataset.right" in game


# ------------------------------------------------------------------ admin
def test_admin_keyword_panel_add_remove_and_log(web, conn, players):  # noqa: F811
    a, _b, _c, s, k = players
    adm = web("203.0.113.99")
    html = adm.get("/admin", headers=basic()).text
    panel = html.split('id="kw-panel"', 1)[1].split('id="marks-panel"', 1)[0]
    text = C.get_card(conn, s.id, uid_of(conn, "Ana")).cells[0]["text"]
    assert text in panel and T.ADMIN_KW_FROZEN in panel and "Ana" in panel     # the frozen keyword and Ana's swap
    assert 'action="/admin/keywords/add"' in panel
    assert "🔑" in html.split('id="marks-panel"', 1)[1].split('id="cards-panel"', 1)[0]
    other = next(o.phrase_id for o in W.keyword_options(conn, s.id) if o.phrase_id != k)
    tok = A.csrf_token(PW, s.id)
    data = {"session_id": str(s.id), "phrase_id": str(other), "csrf": tok}
    assert adm.post("/admin/keywords/add", data={**data, "csrf": "bad"}, headers=basic()).status_code == 403
    assert adm.post("/admin/keywords/add", data=data).status_code == 401
    assert adm.post("/admin/keywords/add", data=data, headers=basic()).status_code == 303
    assert {x.phrase_id for x in W.keywords(conn, s.id)} == {k, other}
    assert adm.post("/admin/keywords/remove", data=data, headers=basic()).status_code == 303
    assert {x.phrase_id for x in W.keywords(conn, s.id)} == {k}
    r = adm.post("/admin/keywords/remove", data={**data, "phrase_id": str(k)}, headers=basic())
    assert r.status_code == 303 and "kwerr=frozen" in r.headers["location"]
    assert T.ADMIN_KW_ERRORS["frozen"] in adm.get(r.headers["location"], headers=basic()).text
    assert "kw-panel" not in a.get("/").text and "🔑" not in a.get("/").text
    polled = adm.get(f"/admin/marks?sid={s.id}&sig=x", headers=basic()).text      # the 5 s redraw keeps the key
    assert "🔑" in polled


def test_correto_from_the_marks_list_redraws_the_keyword_panel(web, conn, game):  # noqa: F811
    s, ana, *_ = game
    k = pid(conn, s, ana, 0)
    W.add_keyword(conn, s.id, k)
    M.toggle_mark(conn, ana.id, 0)
    adm = web("203.0.113.99")
    r = adm.post("/admin/confirm", data={"session_id": str(s.id), "user_id": str(ana.id), "cell": "0",
                                         "csrf": A.csrf_token(PW, s.id)}, headers={**basic(), **HX})
    kw = r.text.split('id="kw-panel"', 1)[1]
    assert 'hx-swap-oob="true"' in r.text.split('id="kw-panel"', 1)[0].rsplit("<div", 1)[1] + kw[:200]
    assert T.ADMIN_KW_FROZEN in kw and T.ADMIN_KW_EARNED.format(holder="Ana", text=card(conn, s, ana).cells[0]["text"]) in kw
