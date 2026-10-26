# ruff: noqa: F811
"""Task 8 (user, 2026-10-26): the Bingo admin page updates itself.
  * the player cards (marks, new players) are re-read every 5 s like the users table; 204 while nothing changed (signature),
    the open cards stay open (admin.js keeps them across the swap);
  * a page with no running game (none yet / the last one ended) reloads itself when a game starts (a player can start Bingo).
"""
from __future__ import annotations

import re
from pathlib import Path

from app import cards as C
from app import marks as M
from app import scoring as G
from app import sessions as S
from tests.test_admin_group3 import basic, bweb, running  # noqa: F401
from tests.test_falacias_web import _env, conn  # noqa: F401

JS = (Path(__file__).resolve().parents[1] / "app" / "static" / "admin.js").read_text()


def poll_url(html: str, elem_id: str) -> str:
    tag = re.search(rf'<div[^>]*id="{elem_id}"[^>]*>', html).group(0)
    return re.search(r'hx-get="([^"]+)"', tag).group(1).replace("&amp;", "&")


def test_the_cards_panel_polls_every_5s_while_the_game_runs(bweb, running):
    html = bweb.get("/admin", headers=basic()).text
    tag = re.search(r'<div[^>]*id="cards-panel"[^>]*>', html).group(0)
    assert 'hx-trigger="every 5s"' in tag and "/admin/cards?" in tag and "sig=" in tag
    assert html.count('id="card-') == 2


def test_nothing_changed_is_a_204(bweb, running):
    url = poll_url(bweb.get("/admin", headers=basic()).text, "cards-panel")
    assert bweb.get(url).status_code == 401
    assert bweb.get(url, headers=basic()).status_code == 204


def test_a_mark_and_a_new_player_redraw_the_cards(bweb, running, conn):
    url = poll_url(bweb.get("/admin", headers=basic()).text, "cards-panel")
    from app import admin as A_
    rows = {r.name: r for r in A_.players(conn, running.id)}
    idx = next(i for i, c in enumerate(rows["Ana"].cells) if "phrase_id" in c)
    assert M.toggle_mark(conn, rows["Ana"].user_id, idx)
    r = bweb.get(url, headers=basic())
    assert r.status_code == 200 and 'id="cards-panel"' in r.text and "Ana - 1/" in r.text and "<html" not in r.text
    url2 = poll_url(r.text, "cards-panel")
    assert url2 != url and bweb.get(url2, headers=basic()).status_code == 204          # the new signature is current
    _, caio, _ = S.join_session(conn, "Caio", "10.0.0.3")
    C.assign_card(conn, running.id, caio.id)
    r = bweb.get(url2, headers=basic())
    assert r.status_code == 200 and r.text.count('id="card-') == 3 and "Caio - 0/" in r.text


def test_an_admin_unblock_or_unmark_changes_the_signature(bweb, running, conn):
    from app import admin as A_
    row = A_.players(conn, running.id)[0]
    idx = next(i for i, c in enumerate(row.cells) if "phrase_id" in c)
    M.toggle_mark(conn, row.user_id, idx)
    url = poll_url(bweb.get("/admin", headers=basic()).text, "cards-panel")
    M.revoke_mark(conn, running.id, row.user_id, idx)                 # unmarked + blocked: the count is the same as before the mark
    assert bweb.get(url, headers=basic()).status_code == 200


def test_the_cards_poll_reloads_the_page_when_the_game_ends(bweb, running, conn):
    url = poll_url(bweb.get("/admin", headers=basic()).text, "cards-panel")
    G.finish_session(conn, running.id)
    assert bweb.get(url, headers=basic()).headers.get("hx-refresh") == "true"
    html = bweb.get("/admin", headers=basic()).text
    assert "/admin/cards" not in re.search(r'<div[^>]*id="cards-panel"[^>]*>', html).group(0)


def test_a_page_without_a_game_reloads_when_one_starts(bweb, conn):
    html = bweb.get("/admin", headers=basic()).text
    url = poll_url(html, "bingo-watch")
    assert 'hx-trigger="every 5s"' in re.search(r'<div[^>]*id="bingo-watch"[^>]*>', html).group(0)
    assert bweb.get(url).status_code == 401
    assert bweb.get(url, headers=basic()).status_code == 204
    S.start_session(conn, "Ana", "10.0.0.1")                         # the first visitor starts Bingo
    assert bweb.get(url, headers=basic()).headers.get("hx-refresh") == "true"


def test_the_ended_page_reloads_when_a_new_game_starts(bweb, running, conn):
    G.finish_session(conn, running.id)
    url = poll_url(bweb.get("/admin", headers=basic()).text, "bingo-watch")
    assert bweb.get(url, headers=basic()).status_code == 204
    S.start_session_as_admin(conn)
    assert bweb.get(url, headers=basic()).headers.get("hx-refresh") == "true"


def test_the_cards_poll_reloads_when_another_game_started_between_two_polls(bweb, running, conn):
    url = poll_url(bweb.get("/admin", headers=basic()).text, "cards-panel")
    G.finish_session(conn, running.id)
    S.start_session_as_admin(conn)                                    # a new game: the old cards must not stay on screen
    assert bweb.get(url, headers=basic()).headers.get("hx-refresh") == "true"


def test_the_watcher_reloads_for_a_game_that_started_and_ended_between_two_polls(bweb, conn):
    url = poll_url(bweb.get("/admin", headers=basic()).text, "bingo-watch")
    s = S.start_session_as_admin(conn)
    G.finish_session(conn, s.id)
    assert bweb.get(url, headers=basic()).headers.get("hx-refresh") == "true"   # its result is worth showing


def test_a_running_game_has_no_watcher(bweb, running):
    assert 'id="bingo-watch"' not in bweb.get("/admin", headers=basic()).text


def test_admin_js_keeps_open_cards_open_across_the_swap_and_never_swaps_during_an_action():
    assert "cards-panel" in JS and "htmx:beforeSwap" in JS and "htmx:afterSwap" in JS and "details[id^=\"card-\"]" in JS
    assert "htmx-request" in JS and "shouldSwap = false" in JS
