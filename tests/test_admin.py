"""Task 8: admin auth, read model and the finish action."""
from __future__ import annotations

import base64
import uuid

import pytest
from starlette.testclient import TestClient

from app import admin as A
from app import sessions as S
from app.db import connect
from app.main import create_app
from tests.test_cards import load_phrases

PW = "s3cret-pässword"


def basic(user="admin", pw=PW):
    return {"authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    load_phrases(c, n=60)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


@pytest.fixture
def web(conn, test_db_url, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", PW)
    app = create_app(test_db_url)
    with TestClient(app):
        def browser(ip="203.0.113.10"):
            return TestClient(app, client=(ip, 50000), follow_redirects=False)
        yield browser


# ------------------------------------------------------------ pure auth
def test_check_basic_accepts_only_exact_credentials():
    assert A.check_basic(basic()["authorization"], PW)
    assert not A.check_basic(basic(pw="wrong")["authorization"], PW)
    assert not A.check_basic(basic(user="root")["authorization"], PW)
    assert not A.check_basic(basic(pw=PW + "x")["authorization"], PW)
    assert not A.check_basic(basic(pw="")["authorization"], PW)
    for bad in (None, "", "Bearer abc", "Basic", "Basic !!!notbase64", "Basic " + base64.b64encode(b"\xff\xfe").decode()):
        assert not A.check_basic(bad, PW)


def test_other_auth_schemes_rejected_even_with_valid_base64():
    b64 = basic()["authorization"].split(" ", 1)[1]
    for scheme in ("Bearer", "Digest", "Token"):
        assert not A.check_basic(f"{scheme} {b64}", PW)
    assert A.check_basic(f"basic {b64}", PW)                              # scheme is case-insensitive


def test_password_with_colon_is_supported():
    assert A.check_basic(basic(pw="a:b:c")["authorization"], "a:b:c")


@pytest.mark.parametrize("val", ["", "  ", "change-me"])
def test_placeholder_or_empty_password_disables_admin(monkeypatch, val):
    monkeypatch.setenv("ADMIN_PASSWORD", val)
    assert A.admin_password() is None


def test_csrf_token_bound_to_session_and_password():
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    t = A.csrf_token(PW, s1)
    assert A.csrf_ok(PW, s1, t)
    assert not A.csrf_ok(PW, s2, t) and not A.csrf_ok("other", s1, t)
    assert not A.csrf_ok(PW, s1, "") and not A.csrf_ok(PW, s1, None)


# ------------------------------------------------------------ web: auth
def test_admin_requires_credentials(web):
    b = web()
    r = b.get("/admin")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert b.get("/admin", headers=basic(pw="nope")).status_code == 401
    assert b.post("/admin/finish", data={"session_id": str(uuid.uuid4()), "csrf": "x"}).status_code == 401


def test_admin_disabled_without_password(conn, test_db_url, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "change-me")
    with TestClient(create_app(test_db_url)) as c:
        assert c.get("/admin", headers=basic(pw="change-me")).status_code == 503


def test_player_cookie_does_not_grant_admin(web):
    b = web()
    b.post("/start", data={"name": "Ana"})
    assert b.get("/admin").status_code == 401


# ------------------------------------------------------------ web: content
def test_admin_without_any_session(web):
    r = web().get("/admin", headers=basic())
    assert r.status_code == 200 and "Nenhuma partida" in r.text


def test_admin_lists_users_cards_and_marks(web, conn):
    a, b = web("203.0.113.10"), web("203.0.113.11")
    a.post("/start", data={"name": "Ana"})
    b.post("/join", data={"name": "Bia <i>"})
    for i in (0, 1, 2):
        b.post(f"/mark/{i}")
    html = web().get("/admin", headers=basic()).text
    assert "Partida em andamento" in html and "Jogadores: 2" in html
    assert "203.0.113.10" not in html and "203.0.113.11" not in html      # IPs are not shown
    assert "Bia &lt;i&gt;" in html and "Bia <i>" not in html           # escaped
    assert html.count('class="mini"') == 2
    assert html.count('class="mc on"') == 3 and html.count('class="mc free"') == 0
    cells = conn.execute("SELECT cells FROM bingo_cards c JOIN users u ON u.id=c.user_id WHERE u.name='Ana'").fetchone()[0]
    assert cells[0]["text"] in html and cells[15]["text"] in html        # real card content shown
    assert "Bia &lt;i&gt; - 3/16" in html
    assert "/admin/finish" in html and "csrf" in html


def test_players_table_read_model(conn):
    s, _p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    rows = A.players(conn, s.id)
    assert len(rows) == 1 and rows[0].name == "Ana" and rows[0].ip == "10.0.0.1"
    assert rows[0].cells == [] and rows[0].marked == frozenset()          # no card yet: no crash


def test_users_listed_in_join_order(web):
    for ip, name in (("203.0.113.10", "Zed"), ("203.0.113.11", "Amy"), ("203.0.113.12", "Max")):
        c = web(ip)
        c.post("/start" if name == "Zed" else "/join", data={"name": name})
    import re
    html = web().get("/admin", headers=basic()).text
    names = re.findall(r"<td>\s*(Zed|Amy|Max)\s*</td>", html)
    assert names == ["Zed", "Amy", "Max"]


# ------------------------------------------------------------ web: finish
def _csrf_of(html: str) -> tuple[str, str]:
    import re
    sid = re.search(r'name="session_id" value="([^"]+)"', html).group(1)
    tok = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
    return sid, tok


def test_finish_ends_session_and_records_winner(web, conn):
    a, b, admin = web("203.0.113.10"), web("203.0.113.11"), web("203.0.113.99")
    a.post("/start", data={"name": "Ana"})
    b.post("/join", data={"name": "Bia"})
    a.post("/mark/0")
    b.post("/mark/0")
    b.post("/mark/1")
    sid, tok = _csrf_of(admin.get("/admin", headers=basic()).text)
    r = admin.post("/admin/finish", data={"session_id": sid, "csrf": tok}, headers=basic())
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    st, winner = conn.execute("SELECT status, u.name FROM game_sessions g JOIN users u ON u.id=g.winner_user_id").fetchone()
    assert (st, winner) == ("ended", "Bia")
    html = admin.get("/admin", headers=basic()).text                      # results stay visible
    assert "Partida encerrada" in html and "Vencedor(a): Bia" in html and "/admin/finish" not in html
    assert "Fim de jogo!" in b.get("/").text                              # players see the end


def test_finish_without_or_with_bad_csrf_is_403_and_changes_nothing(web, conn):
    a = web()
    a.post("/start", data={"name": "Ana"})
    a.post("/mark/0")
    sid = str(S.get_active_session(conn).id)
    for tok in ("", "deadbeef"):
        r = web("203.0.113.99").post("/admin/finish", data={"session_id": sid, "csrf": tok}, headers=basic())
        assert r.status_code == 403
    assert S.get_active_session(conn) is not None


def test_csrf_token_of_another_session_rejected(web, conn):
    a = web()
    a.post("/start", data={"name": "Ana"})
    sid = str(S.get_active_session(conn).id)
    r = web("203.0.113.99").post("/admin/finish", data={"session_id": sid, "csrf": A.csrf_token(PW, uuid.uuid4())}, headers=basic())
    assert r.status_code == 403 and S.get_active_session(conn) is not None


def test_finish_bad_session_id_400(web):
    assert web().post("/admin/finish", data={"session_id": "nope", "csrf": "x"}, headers=basic()).status_code == 400


def test_double_finish_is_harmless_and_keeps_winner(web, conn):
    a, admin = web(), web("203.0.113.99")
    a.post("/start", data={"name": "Ana"})
    a.post("/mark/0")
    sid, tok = _csrf_of(admin.get("/admin", headers=basic()).text)
    for _ in range(2):
        assert admin.post("/admin/finish", data={"session_id": sid, "csrf": tok}, headers=basic()).status_code == 303
    assert conn.execute("SELECT count(*) FROM game_sessions WHERE status='ended' AND winner_user_id IS NOT NULL").fetchone()[0] == 1


def test_new_game_can_start_after_admin_finish(web, conn):
    a, admin = web(), web("203.0.113.99")
    a.post("/start", data={"name": "Ana"})
    sid, tok = _csrf_of(admin.get("/admin", headers=basic()).text)
    admin.post("/admin/finish", data={"session_id": sid, "csrf": tok}, headers=basic())
    assert web("203.0.113.50").post("/start", data={"name": "Caio"}).status_code == 303
    assert "Partida em andamento" in admin.get("/admin", headers=basic()).text


def test_admin_cards_show_time_of_each_mark(web, conn):
    import re
    a = web()
    a.post("/start", data={"name": "Ana"})
    a.post("/mark/4")
    a.post("/mark/7")
    conn.execute("UPDATE bingo_marks SET marked_at = '2026-10-19 20:00:01+00' WHERE cell_index = 4")
    conn.execute("UPDATE bingo_marks SET marked_at = '2026-10-19 21:30:59+00' WHERE cell_index = 7")
    html = web("203.0.113.99").get("/admin", headers=basic()).text
    assert re.findall(r'<small class="at">(\d\d:\d\d:\d\d)</small>', html) == ["17:00:01", "18:30:59"]   # Sao_Paulo, board order


def test_players_read_model_has_mark_times(conn):
    from app import cards as C
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    card = C.assign_card(conn, s.id, p.id)
    conn.execute("INSERT INTO bingo_marks (card_id, cell_index, marked_at) VALUES (%s, 9, '2026-01-01 10:00:00+00'), (%s, 2, '2026-01-01 11:00:00+00')", (card.id, card.id))
    row = A.players(conn, s.id)[0]
    assert row.marked == frozenset({2, 9})
    assert [(k, v.hour) for k, v in sorted(row.marked_at.items())] == [(2, 11), (9, 10)]


# ---------------------------------------------------------------- admin starts a session
def _start_csrf(html: str) -> str:
    import re
    return re.search(r'action="/admin/start".*?name="csrf" value="([^"]+)"', html, re.DOTALL).group(1)


def test_admin_page_offers_start_when_no_session(web):
    html = web().get("/admin", headers=basic()).text
    assert "Iniciar nova partida" in html and 'action="/admin/start"' in html


def test_admin_start_flow_then_players_join(web, conn):
    admin = web("203.0.113.99")
    tok = _start_csrf(admin.get("/admin", headers=basic()).text)
    r = admin.post("/admin/start", data={"csrf": tok}, headers=basic())
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    s = S.get_active_session(conn)
    assert s is not None and s.created_by is None
    page = admin.get("/admin", headers=basic()).text
    assert "Partida em andamento" in page and "Jogadores: 0" in page
    assert "Iniciar nova partida" not in page and "/admin/finish" in page
    visitor = web("203.0.113.10")
    visitor.cookies.set("lg_name", "Ana")                                     # the name gate comes first
    home = visitor.get("/").text
    assert 'action="/join"' in home and 'action="/start"' not in home        # joins, cannot start
    assert visitor.post("/join", data={"name": "Ana"}).status_code == 303
    assert "Jogadores: 1" in admin.get("/admin", headers=basic()).text


def test_admin_start_after_a_finished_game_shows_button_next_to_results(web, conn):
    a, admin = web(), web("203.0.113.99")
    a.post("/start", data={"name": "Ana"})
    sid, tok = _csrf_of(admin.get("/admin", headers=basic()).text)
    admin.post("/admin/finish", data={"session_id": sid, "csrf": tok}, headers=basic())
    html = admin.get("/admin", headers=basic()).text
    assert "Partida encerrada" in html and "Iniciar nova partida" in html
    assert admin.post("/admin/start", data={"csrf": _start_csrf(html)}, headers=basic()).status_code == 303
    assert S.get_active_session(conn) is not None
    assert "Fim de jogo!" not in a.get("/").text                             # Ana's old cookie: now sees the join page
    assert 'action="/join"' in a.get("/").text


def test_admin_start_requires_auth_and_csrf(web, conn):
    b = web()
    assert b.post("/admin/start", data={"csrf": "x"}).status_code == 401
    for tok in ("", "deadbeef", A.csrf_token(PW, uuid.uuid4())):              # a finish-token must not work either
        assert web().post("/admin/start", data={"csrf": tok}, headers=basic()).status_code == 403
    assert S.get_active_session(conn) is None


def test_admin_start_double_click_and_player_race_are_harmless(web, conn):
    admin = web("203.0.113.99")
    tok = _start_csrf(admin.get("/admin", headers=basic()).text)
    for _ in range(2):
        assert admin.post("/admin/start", data={"csrf": tok}, headers=basic()).status_code == 303
    assert conn.execute("SELECT count(*) FROM game_sessions").fetchone()[0] == 1


def test_start_token_pure():
    assert A.start_ok(PW, A.start_token(PW))
    assert not A.start_ok(PW, "") and not A.start_ok(PW, None) and not A.start_ok("other", A.start_token(PW))
    assert A.start_token(PW) != A.csrf_token(PW, uuid.uuid4())
