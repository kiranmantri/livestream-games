# ruff: noqa: F811
"""Task 6: finishing a Falácias session records the winner; end screens (player + admin) show the final ranking."""
from __future__ import annotations

import threading

import pytest

from app import admin as A
from app import falacias as F
from app import sessions as S
from app import texts as T
from app.db import connect
from tests.test_admin_falacias import IP_ADMIN, PW, basic, fal, web  # noqa: F401
from tests.test_falacias import age, ev, fallacy, game, mark
from tests.test_falacias_web import IP_A, IP_B, _env, conn, named, start  # noqa: F401
from tests.test_falacias_web import age as web_age


# ================================================================ service
@pytest.fixture
def world(conn):
    c = conn
    c.execute("DELETE FROM fallacies")
    s, (ana, bia, cai) = game(c, ("Ana", "Bia", "Cai"))
    f = fallacy(c)
    e = [ev(c, s.id, key=str(i)) for i in range(4)]
    yield {"c": c, "s": s, "ana": ana, "bia": bia, "cai": cai, "f": f, "e": e}
    c.execute("TRUNCATE game_sessions, users CASCADE")
    c.execute("DELETE FROM fallacies")


def winner_of(c, sid):
    return c.execute("SELECT winner_user_id, status FROM game_sessions WHERE id = %s", (sid,)).fetchone()


def test_finish_records_the_top_scorer_and_returns_the_standings(world, test_db_url):
    c = world["c"]
    a = mark(c, world["ana"], world["e"][0], world["f"])
    b = mark(c, world["bia"], world["e"][1], world["f"])
    F.review_mark(c, world["s"].id, b.id, "correct")
    r = F.finish_session(c, world["s"].id)
    assert r is not None and r.winner.user_id == world["bia"] and [x.name for x in r.standings] == ["Bia", "Ana", "Cai"]
    other = connect(test_db_url)                                     # durability: read through a SECOND connection
    assert winner_of(other, world["s"].id) == (world["bia"], "ended")
    other.close()
    assert a.status == "pending"


def test_finish_with_no_points_has_no_winner(world):
    c = world["c"]
    r = F.finish_session(c, world["s"].id)
    assert r is not None and r.winner is None and winner_of(c, world["s"].id) == (None, "ended")


def test_spam_only_player_is_not_a_winner(world):
    c = world["c"]
    m = mark(c, world["ana"], world["e"][0], world["f"])
    F.review_mark(c, world["s"].id, m.id, "spam")
    assert F.finish_session(c, world["s"].id).winner is None


def test_finish_twice_returns_none_and_keeps_the_winner(world):
    c = world["c"]
    m = mark(c, world["ana"], world["e"][0], world["f"])
    F.finish_session(c, world["s"].id)
    assert F.finish_session(c, world["s"].id) is None
    assert winner_of(c, world["s"].id) == (world["ana"], "ended")
    assert m.status == "pending"


def test_finish_unknown_or_bingo_session_is_refused(world):
    import uuid
    c = world["c"]
    assert F.finish_session(c, uuid.uuid4()) is None
    b = S.start_session_as_admin(c, "bingo")
    assert F.finish_session(c, b.id) is None and S.get_active_session(c, "bingo").id == b.id


def test_reviews_are_frozen_after_finish(world):
    c = world["c"]
    m = mark(c, world["ana"], world["e"][0], world["f"])
    F.finish_session(c, world["s"].id)
    with pytest.raises(F.SessionNotActive):
        F.review_mark(c, world["s"].id, m.id, "correct")


def test_tie_goes_to_more_correct_then_earlier_last_submit(world):
    c = world["c"]
    a = mark(c, world["ana"], world["e"][0], world["f"])
    age(c, world["ana"], 30)
    b = mark(c, world["bia"], world["e"][1], world["f"])
    # same points (5,0 each, both pending) -> whoever submitted their last pair first wins
    r = F.finish_session(c, world["s"].id)
    assert r.winner.user_id == world["ana"] and a.id != b.id


def test_two_concurrent_finishes_end_it_once(world, test_db_url):
    c = world["c"]
    mark(c, world["ana"], world["e"][0], world["f"])
    out, barrier = [], threading.Barrier(2)

    def run():
        k = connect(test_db_url)
        barrier.wait()
        out.append(F.finish_session(k, world["s"].id))
        k.close()
    ts = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(x is None for x in out) == [False, True]


# ================================================================ pages
def sid(c):
    return S.get_active_session(c, "falacias").id


def play_and_finish(web, conn, fal):
    ana = named(web(IP_A), "Ana")
    start(ana, "Ana")
    bia = named(web(IP_B), "Bia")
    bia.post("/falacias/join")
    web_age(conn, 10)
    ana.get("/falacias")
    return ana, bia


def put(c, who, text, fallacy_id, a=0, b=4):
    row = c.execute("SELECT id, kind FROM stream_events WHERE text = %s", (text,)).fetchone()
    return who.post("/falacias/mark", data={"kind": row[1], "start_id": row[0], "start_off": a, "end_id": row[0], "end_off": b,
                                            "fallacy_id": fallacy_id})


def admin_finish(web, conn, ):
    s = sid(conn)
    return web(IP_ADMIN).post("/admin/falacias/finish", data={"session_id": str(s), "csrf": A.csrf_token(PW, s)}, headers=basic())


def mids(c):
    return [r[0] for r in c.execute("SELECT id FROM fallacy_marks ORDER BY created_at")]


def test_admin_finish_records_the_winner_and_the_players_see_the_ranking(web, conn, fal):
    _ana, bia = play_and_finish(web, conn, fal)
    text = conn.execute("SELECT text FROM stream_events WHERE kind = 'transcript' ORDER BY id LIMIT 1").fetchone()[0]
    f_id = fal["Espantalho"]
    assert put(conn, bia, text, f_id).status_code in (200, 204)
    s = sid(conn)
    m = mids(conn)[0]
    web(IP_ADMIN).post("/admin/falacias/review", data={"session_id": str(s), "mark_id": str(m), "verdict": "correct", "group": "0",
                                                       "csrf": A.csrf_token(PW, s)}, headers=basic())
    assert admin_finish(web, conn).status_code == 303
    assert conn.execute("SELECT u.name FROM game_sessions g JOIN users u ON u.id = g.winner_user_id").fetchone() == ("Bia",)
    html = bia.get("/falacias").text
    pts = F.fmt_points(F.points_of(F.get_mark(conn, m)))
    assert T.FAL_GAME_OVER in html and T.FAL_WINNER.format(name="Bia", pts=pts) in html
    assert T.FAL_COL_CORRECT in html and T.FAL_COL_WRONG in html and T.FAL_COL_SPAM in html
    assert '<main class="fal">' in html                    # wide page: the 3-column cards need it (default main is 560 px)
    assert 'class="winner"' in html and T.CK_MINE in html and T.CK_OTHERS in html and T.FAL_VERDICTS["correct"] in html


def test_end_screen_marks_my_row_and_the_final_note_about_unreviewed_pairs(web, conn, fal):
    ana, bia = play_and_finish(web, conn, fal)
    text = conn.execute("SELECT text FROM stream_events WHERE kind = 'transcript' ORDER BY id LIMIT 1").fetchone()[0]
    put(conn, ana, text, fal["Espantalho"])
    admin_finish(web, conn)
    html = ana.get("/falacias").text
    assert 'class="me"' in html and T.FAL_UNREVIEWED_NOTE in html and T.FAL_PENDING in html
    assert T.FAL_UNREVIEWED_NOTE not in bia.get("/falacias").text            # Bia has nothing unreviewed


def test_end_screen_without_points_has_no_winner_line(web, conn, fal):
    ana, _bia = play_and_finish(web, conn, fal)
    admin_finish(web, conn)
    html = ana.get("/falacias").text
    assert "Ninguém pontuou" not in html and 'class="winner"' not in html


def test_admin_page_shows_the_winner_after_the_end(web, conn, fal):
    ana, _bia = play_and_finish(web, conn, fal)
    text = conn.execute("SELECT text FROM stream_events WHERE kind = 'transcript' ORDER BY id LIMIT 1").fetchone()[0]
    put(conn, ana, text, fal["Espantalho"])
    admin_finish(web, conn)
    html = web(IP_ADMIN).get("/admin/falacias", headers=basic()).text
    assert T.ADMIN_WINNER in html and "Ana" in html and 'class="winner"' in html


def test_admin_finish_twice_keeps_the_first_winner(web, conn, fal):
    ana, _bia = play_and_finish(web, conn, fal)
    text = conn.execute("SELECT text FROM stream_events WHERE kind = 'transcript' ORDER BY id LIMIT 1").fetchone()[0]
    put(conn, ana, text, fal["Espantalho"])
    s = sid(conn)
    tok = A.csrf_token(PW, s)
    admin = web(IP_ADMIN)
    for _ in range(2):
        assert admin.post("/admin/falacias/finish", data={"session_id": str(s), "csrf": tok}, headers=basic()).status_code == 303
    assert conn.execute("SELECT u.name FROM game_sessions g JOIN users u ON u.id = g.winner_user_id").fetchone() == ("Ana",)


def test_bingo_end_screen_is_unchanged(web, conn, fal):
    b = named(web("203.0.113.70"), "Zed")
    b.post("/start")
    from app import scoring as G
    G.finish_session(conn, S.get_active_session(conn).id)
    assert T.GAME_OVER in b.get("/").text and T.FAL_GAME_OVER not in b.get("/").text
