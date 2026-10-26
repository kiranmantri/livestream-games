# ruff: noqa: F811
"""Admin shell v2: games named Bingo / Quem é Falacioso? / Aponte o mentiroso!, a Jogo | Conteúdo sub-menu per game, and ONE
session (start / end, shared video URL) in the header that starts and ends all three games together."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import admin as A
from app import replays as R
from app import sessions as S
from app import texts as T
from tests.test_checker_admin_web import (  # noqa: F401
    IP_ADMIN,
    LIVE,
    NEW,
    OLD,
    UPC,
    VERT,
    _env,
    admin,
    app,
    basic,
    conn,
)

LINK = "https://www.youtube.com/watch?v={}"
PAGES = ("/admin", "/admin/falacias", "/admin/checker", "/admin/phrases", "/admin/fallacies")
GAMES = ("bingo", "falacias", "checker")


def begin(admin, link="", replay="", csrf=None):
    return admin.post("/admin/session/start", data={"csrf": A.session_token("s3cret-pässword") if csrf is None else csrf, "video": link, "replay": replay},
                      headers=basic())


def end(admin, csrf=None):
    return admin.post("/admin/session/finish", data={"csrf": A.session_token("s3cret-pässword") if csrf is None else csrf}, headers=basic())


def active(conn):
    return {g: S.get_active_session(conn, g) for g in GAMES}


def header(html):
    return re.search(r'<header class="appbar admin".*?</header>', html, re.DOTALL).group(0)


def games_nav(html):
    return re.search(r"<nav[^>]*gamenav.*?</nav>", header(html), re.DOTALL).group(0)


def sub_nav(html):
    return re.search(r"<nav[^>]*subnav.*?</nav>", header(html), re.DOTALL).group(0)


# ---------------------------------------------------------------- names and menus
def test_the_games_have_the_new_names():
    assert (T.ADMIN_TAB_BINGO, T.ADMIN_TAB_FALACIAS, T.ADMIN_TAB_CHECKER) == ("Bingo", "Quem é Falacioso?", "Aponte o mentiroso!")
    assert (T.MENU_BINGO, T.MENU_FALACIAS, T.MENU_CHECKER) == ("Bingo", "Quem é Falacioso?", "Aponte o mentiroso!")


@pytest.mark.parametrize("path", PAGES)
def test_every_admin_page_lists_the_three_games_in_order(admin, path):
    labels = re.findall(r'class="lbl">(.*?)</span>', games_nav(admin.get(path, headers=basic()).text))
    assert labels == ["Bingo", "Quem é Falacioso?", "Aponte o mentiroso!"]


@pytest.mark.parametrize(("path", "game", "hrefs"), [
    ("/admin", "Bingo", ["/admin", "/admin/phrases"]),
    ("/admin/phrases", "Bingo", ["/admin", "/admin/phrases"]),
    ("/admin/falacias", "Quem é Falacioso?", ["/admin/falacias", "/admin/fallacies"]),
    ("/admin/fallacies", "Quem é Falacioso?", ["/admin/falacias", "/admin/fallacies"]),
    ("/admin/checker", "Aponte o mentiroso!", ["/admin/checker"]),
])
def test_each_game_has_its_own_sub_menu(admin, path, game, hrefs):
    html = admin.get(path, headers=basic()).text
    sub = sub_nav(html)
    assert re.findall(r'href="([^"]+)"', sub) == hrefs
    assert re.findall(r'class="lbl">(.*?)</span>', sub) == (["Jogo", "Conteúdo"] if len(hrefs) == 2 else ["Jogo"])
    current = re.search(r'<a[^>]*aria-current="page"[^>]*>.*?</a>', games_nav(html), re.DOTALL).group(0)
    assert game in current


def test_the_sub_menu_marks_game_or_content(admin):
    jogo = re.search(r'<a[^>]*aria-current="page"[^>]*>.*?</a>', sub_nav(admin.get("/admin/falacias", headers=basic()).text), re.DOTALL).group(0)
    cont = re.search(r'<a[^>]*aria-current="page"[^>]*>.*?</a>', sub_nav(admin.get("/admin/fallacies", headers=basic()).text), re.DOTALL).group(0)
    assert "Jogo" in jogo and "Conteúdo" in cont


def test_the_checker_has_no_content_entry(admin):
    assert "Conteúdo" not in sub_nav(admin.get("/admin/checker", headers=basic()).text)


# Jogo | Conteúdo start under the SELECTED game: row 1 is a grid (brand = column 1, the games = columns 2..4); the sub menu is row 2 of the same
# grid and its links start at the active game's column (--sub-col), so the left edges line up without any JavaScript.
@pytest.mark.parametrize(("path", "col"), [("/admin", 2), ("/admin/phrases", 2), ("/admin/falacias", 3), ("/admin/fallacies", 3), ("/admin/checker", 4)])
def test_the_sub_menu_starts_under_the_selected_game(admin, path, col):
    h = header(admin.get(path, headers=basic()).text)
    # the sub menu is the LAST child of the adminrow grid: right after "Ver o site", then the row closes
    assert re.search(r'class="viewsite"[^>]*>.*?</a>\s*<nav[^>]*class="subnav".*?</nav>\s*</div>', h, re.DOTALL), "sub menu inside the adminrow grid"
    assert re.search(rf'<nav[^>]*class="subnav"[^>]*style="--sub-col: {col}"', h)
    assert re.search(r'<nav[^>]*class="subnav".*?<div class="subtabs">', h, re.DOTALL)


def test_the_sub_menu_grid_rules_exist():
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.css").read_text()
    assert "grid-template-columns: subgrid" in css
    assert re.search(r"\.subtabs\s*\{[^}]*grid-column:\s*var\(--sub-col", css)


# ---------------------------------------------------------------- the header session controls
@pytest.mark.parametrize("path", PAGES)
def test_without_a_session_the_header_offers_to_start_one(admin, path):
    h = header(admin.get(path, headers=basic()).text)
    assert "Iniciar sessão" in h and "Finalizar sessão" not in h
    d = re.search(r'<dialog[^>]*id="startdlg".*?</dialog>', h, re.DOTALL).group(0)
    assert 'action="/admin/session/start"' in d and 'name="video"' in d and 'name="replay"' in d and A.session_token("s3cret-pässword") in d


def test_the_title_is_the_first_thing_in_the_header(admin):
    h = header(admin.get("/admin", headers=basic()).text)
    assert h.index(T.ADMIN_BRAND) < h.index("Bingo") < h.index("Iniciar sessão")


def test_the_old_per_game_start_and_finish_buttons_are_gone(admin, conn):
    assert T.ADMIN_START not in "".join(admin.get(p, headers=basic()).text.split("</header>", 1)[1] for p in PAGES)
    begin(admin, LINK.format(LIVE))
    for p in ("/admin", "/admin/falacias", "/admin/checker"):
        body = admin.get(p, headers=basic()).text.split("</header>", 1)[1]
        assert "Encerrar partida" not in body and 'action="/admin/finish"' not in body
        assert 'action="/admin/falacias/finish"' not in body and 'action="/admin/checker/finish"' not in body


# ---------------------------------------------------------------- start
def test_one_live_link_starts_the_three_games(admin, conn):
    r = begin(admin, LINK.format(LIVE))
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    a = active(conn)
    assert all(a.values())
    for g in ("falacias", "checker"):
        assert (a[g].video_id, a[g].source_mode) == (LIVE, "live") and a[g].created_by is None
    assert a["bingo"].created_by is None


def test_a_collected_replay_link_or_pick_starts_the_three_games(admin, conn):
    begin(admin, replay=OLD)
    a = active(conn)
    assert all(a.values()) and (a["falacias"].video_id, a["falacias"].source_mode) == (OLD, "replay") and a["checker"].video_id == OLD


def test_a_typed_link_wins_over_the_pick(admin, conn):
    begin(admin, LINK.format(LIVE), replay=OLD)
    assert active(conn)["falacias"].video_id == LIVE


def test_the_header_then_shows_the_url_and_the_end_button(admin, conn):
    begin(admin, LINK.format(LIVE))
    for p in PAGES:
        h = header(admin.get(p, headers=basic()).text)
        assert f'href="{LINK.format(LIVE)}"' in h and "Finalizar sessão" in h
        assert "Iniciar sessão" not in h and "<dialog" not in h
        assert 'action="/admin/session/finish"' in h


def test_the_url_in_the_header_opens_in_a_new_tab_safely(admin, conn):
    begin(admin, LINK.format(LIVE))
    a = re.search(r'<a[^>]*href="https://www.youtube.com/watch\?v=' + LIVE + r'"[^>]*>', header(admin.get("/admin", headers=basic()).text)).group(0)
    assert 'target="_blank"' in a and "noopener" in a


@pytest.mark.parametrize(("link", "reason"), [("", "empty"), ("https://example.com/x", "invalid"), (LINK.format(VERT), "vertical"),
                                              (LINK.format(UPC), "upcoming"), ("Broken_0001", "lookup")])
def test_refused_sources_start_nothing_and_say_why_inside_the_dialog(admin, conn, link, reason):
    r = begin(admin, link)
    assert r.status_code == 200 and T.AF_URL_ERRORS[reason] in r.text
    assert re.search(r"<dialog[^>]*open", r.text) and not any(active(conn).values())


def test_a_forged_replay_pick_is_refused(admin, conn):
    r = begin(admin, replay=NEW)
    assert r.status_code == 200 and not any(active(conn).values())


def test_a_finished_video_that_is_not_collected_is_queued_and_nothing_starts(admin, conn):
    conn.execute("DELETE FROM replays")
    r = begin(admin, LINK.format(NEW))
    assert r.status_code == 200 and T.AF_URL_QUEUED in r.text and not any(active(conn).values())
    assert R.get(conn, NEW).status == "queued"
    again = begin(admin, LINK.format(NEW))
    assert T.AF_URL_COLLECTING in again.text
    conn.execute("DELETE FROM replays")


def test_start_needs_credentials_and_the_token(admin, conn):
    assert admin.post("/admin/session/start", data={}).status_code == 401
    assert admin.post("/admin/session/finish", data={}).status_code == 401
    assert begin(admin, LINK.format(LIVE), csrf="nope").status_code == 403
    assert end(admin, csrf="nope").status_code == 403
    assert not any(active(conn).values())


def test_a_game_already_running_is_kept_and_the_others_start(admin, conn):
    b = S.start_session_as_admin(conn, game_type="bingo")
    begin(admin, LINK.format(LIVE))
    a = active(conn)
    assert a["bingo"].id == b.id and a["falacias"] and a["checker"]


def test_starting_twice_is_harmless(admin, conn):
    begin(admin, LINK.format(LIVE))
    ids = {g: s.id for g, s in active(conn).items()}
    assert begin(admin, LINK.format(LIVE)).status_code == 303
    assert {g: s.id for g, s in active(conn).items()} == ids


def test_the_dialog_page_without_javascript(admin):
    r = admin.get("/admin/session?start=1", headers=basic())
    assert r.status_code == 200 and re.search(r"<dialog[^>]*open", r.text) and r.text.count('id="startdlg"') == 1
    assert admin.get("/admin/session", headers=basic()).text.count('id="startdlg"') == 1


# ---------------------------------------------------------------- finish
def test_finish_ends_the_three_games_and_the_header_goes_back(admin, conn):
    begin(admin, LINK.format(LIVE))
    r = end(admin)
    assert r.status_code == 303 and not any(active(conn).values())
    assert conn.execute("SELECT count(*) FROM game_sessions WHERE status = 'ended'").fetchone()[0] == 3
    h = header(admin.get("/admin", headers=basic()).text)
    assert "Iniciar sessão" in h and "Finalizar sessão" not in h


def test_finish_twice_is_harmless_and_keeps_the_winner(admin, conn):
    begin(admin, LINK.format(LIVE))
    end(admin)
    assert end(admin).status_code == 303


def test_finish_ends_only_what_is_running(admin, conn):
    S.start_session_as_admin(conn, game_type="checker")
    assert end(admin).status_code == 303 and not any(active(conn).values())


def test_finish_asks_for_confirmation(admin, conn):
    begin(admin, LINK.format(LIVE))
    assert "confirm(" in header(admin.get("/admin", headers=basic()).text)


# ---------------------------------------------------------------- pages keep working
def test_a_game_page_with_no_session_points_to_the_header(admin):
    for p in ("/admin", "/admin/falacias", "/admin/checker"):
        assert T.SESSION_NONE_HINT in admin.get(p, headers=basic()).text


def test_the_checker_page_shows_the_cards_of_each_check_under_jogo(admin, conn):
    from app import checker as C
    begin(admin, LINK.format(LIVE))
    sid = S.get_active_session(conn, "checker").id
    uid = S.join_session(conn, "Ana", "10.0.0.1", game_type="checker")[1].id
    C.submit_typed_claim(conn, uid, "A Terra é plana", -4)
    html = admin.get("/admin/checker", headers=basic()).text
    assert "A Terra é plana" in html and T.CK_STATUS["queued"] in html and str(sid)
