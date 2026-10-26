"""Admin fallacies page: auth, CSRF, list / search / add / inline edit / delete, tab, and the picker seeing the edits."""
from __future__ import annotations

import base64

import pytest
from starlette.testclient import TestClient

from app import admin as A
from app import falacias as F
from app import fallacies_admin as FA
from app import sessions as S
from app.db import connect
from app.main import create_app

PW = "s3cret-pässword"
TOK = A.fallacies_token(PW)
AUTH = {"authorization": "Basic " + base64.b64encode(f"admin:{PW}".encode()).decode()}
HX = {**AUTH, "x-csrf": TOK, "hx-request": "true"}


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users, fallacies CASCADE")
    yield c
    c.execute("TRUNCATE game_sessions, users, fallacies CASCADE")
    c.close()


@pytest.fixture
def web(conn, test_db_url, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", PW)
    app = create_app(test_db_url)
    with TestClient(app):
        yield TestClient(app, client=("203.0.113.99", 50000), follow_redirects=False)


def rows(conn, n=FA.MIN_ENABLED + 3):
    return [conn.execute("INSERT INTO fallacies (name, category, source) VALUES (%s,'Relevância','manual') RETURNING id",
                         (f"Falácia {i}",)).fetchone()[0] for i in range(n)]


def id_of(conn, name):
    return conn.execute("SELECT id FROM fallacies WHERE name=%s", (name,)).fetchone()[0]


# ------------------------------------------------------------------ auth / csrf / tab
def test_every_route_requires_credentials(web, conn):
    i = rows(conn)[0]
    for method, url in [("get", "/admin/fallacies"), ("post", "/admin/fallacies/add"), ("get", f"/admin/fallacies/{i}"),
                        ("get", f"/admin/fallacies/{i}/edit"), ("post", f"/admin/fallacies/{i}/save"),
                        ("post", f"/admin/fallacies/{i}/delete")]:
        A.reset_throttle()                       # this test sends a wrong password on every route: not what the lock is for
        assert getattr(web, method)(url).status_code == 401, (method, url)
        bad = {"authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()}
        assert getattr(web, method)(url, headers=bad).status_code == 401, (method, url)


def test_the_page_is_disabled_without_a_real_admin_password(conn, test_db_url, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "change-me")
    app = create_app(test_db_url)
    with TestClient(app) as tc:
        assert tc.get("/admin/fallacies").status_code == 503


def test_mutations_need_the_fallacy_token_and_change_nothing_without_it(web, conn):
    i = rows(conn)[0]
    before = conn.execute("SELECT id, name, enabled FROM fallacies ORDER BY id").fetchall()
    for tok in (None, "", "deadbeef", A.phrases_token(PW), A.start_token(PW)):         # another scope's token must not work
        h = dict(AUTH, **({"x-csrf": tok} if tok is not None else {}))
        assert web.post("/admin/fallacies/add", headers=h, data={"name": "Nova", "category": "C"}).status_code == 403
        assert web.post(f"/admin/fallacies/{i}/save", headers=h, data={"name": "X", "category": "C"}).status_code == 403
        assert web.post(f"/admin/fallacies/{i}/delete", headers=h).status_code == 403
    assert conn.execute("SELECT id, name, enabled FROM fallacies ORDER BY id").fetchall() == before


def test_admin_tabs_link_to_the_fallacy_list_on_every_admin_page(web, conn):
    for url in ("/admin/falacias", "/admin/fallacies"):
        html = web.get(url, headers=AUTH).text
        assert 'href="/admin/fallacies"' in html, url
    page = web.get("/admin/fallacies", headers=AUTH).text
    assert 'class="active"' in page.split('href="/admin/fallacies"')[1][:60] or 'aria-current="page"' in page


# ------------------------------------------------------------------ list / search
def test_list_shows_name_category_source_usage_and_state(web, conn):
    rows(conn)
    conn.execute("INSERT INTO fallacies (name, category, source, enabled, wikipedia_url) VALUES "
                 "('Pista Falsa (Red Herring)','Falácias red herring','wikipedia',false,'https://pt.wikipedia.org/wiki/Pista_falsa')")
    html = web.get("/admin/fallacies", headers=AUTH).text
    assert "Pista Falsa (Red Herring)" in html and "Falácias red herring" in html and "wikipedia" in html
    assert 'name="csrf"' in html and TOK in html
    assert "Falácia 0" in html and "manual" in html
    conn.execute("UPDATE fallacies SET description = 'minha descrição visível' WHERE name = 'Falácia 0'")
    assert "minha descrição visível" in web.get("/admin/fallacies", headers=AUTH).text


def test_list_filters(web, conn):
    rows(conn)
    conn.execute("INSERT INTO fallacies (name, category, source, enabled) VALUES ('Pista Falsa','C','wikipedia',false)")
    html = web.get("/admin/fallacies?q=pista", headers=AUTH).text
    assert "Pista Falsa" in html and "Falácia 0" not in html
    html = web.get("/admin/fallacies?enabled=0", headers=AUTH).text
    assert "Pista Falsa" in html and "Falácia 1" not in html
    html = web.get("/admin/fallacies?source=manual", headers=AUTH).text
    assert "Falácia 1" in html and "Pista Falsa" not in html


# ------------------------------------------------------------------ add
def test_add_creates_a_manual_enabled_fallacy_and_redirects_with_a_notice(web, conn):
    r = web.post("/admin/fallacies/add", headers={**AUTH, "x-csrf": TOK},
                 data={"name": "Falácia da falácia (Fallacy Fallacy)", "category": "Lógica", "description": "d"})
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/fallacies")
    assert conn.execute("SELECT source, enabled, category, description FROM fallacies WHERE name LIKE 'Falácia da falácia%'"
                        ).fetchone() == ("manual", True, "Lógica", "d")
    assert "adicionada" in web.get(r.headers["location"], headers=AUTH).text


@pytest.mark.parametrize("data, text", [({"name": "", "category": "C"}, "nome"), ({"name": "N", "category": ""}, "categoria")])
def test_add_shows_inline_errors_and_creates_nothing(web, conn, data, text):
    r = web.post("/admin/fallacies/add", headers={**AUTH, "x-csrf": TOK}, data=data)
    assert r.status_code == 200 and text in r.text.lower() and 'role="alert"' in r.text
    assert conn.execute("SELECT count(1) FROM fallacies").fetchone()[0] == 0


def test_add_a_taken_name_is_an_error(web, conn):
    rows(conn)
    r = web.post("/admin/fallacies/add", headers={**AUTH, "x-csrf": TOK}, data={"name": "falácia 0", "category": "C"})
    assert r.status_code == 200 and "já existe" in r.text.lower()


# ------------------------------------------------------------------ inline edit
def test_edit_form_is_prefilled_and_cancel_returns_the_display_row(web, conn):
    i = rows(conn)[0]
    r = web.get(f"/admin/fallacies/{i}/edit", headers=HX)
    assert r.status_code == 200 and 'value="Falácia 0"' in r.text and 'name="description"' in r.text and f'id="f-{i}"' in r.text
    r = web.get(f"/admin/fallacies/{i}", headers=HX)
    assert r.status_code == 200 and "Falácia 0" in r.text and f'id="f-{i}"' in r.text and "<input" not in r.text.replace('type="button"', "")


def test_save_updates_and_returns_the_display_row(web, conn):
    i = rows(conn)[0]
    r = web.post(f"/admin/fallacies/{i}/save", headers=HX,
                 data={"name": "Ataque pessoal (Personal Attack - Ad hominem)", "category": "Relevância", "description": "desc", "enabled": "1"})
    assert r.status_code == 200 and "Ataque pessoal (Personal Attack - Ad hominem)" in r.text
    assert conn.execute("SELECT name, description, enabled FROM fallacies WHERE id=%s", (i,)).fetchone() == (
        "Ataque pessoal (Personal Attack - Ad hominem)", "desc", True)


def test_save_without_the_enabled_checkbox_disables_it(web, conn):
    i = rows(conn)[0]
    web.post(f"/admin/fallacies/{i}/save", headers=HX, data={"name": "Falácia 0", "category": "Relevância"})
    assert conn.execute("SELECT enabled FROM fallacies WHERE id=%s", (i,)).fetchone() == (False,)


def test_save_with_an_invalid_value_keeps_the_form_open_with_an_error_and_changes_nothing(web, conn):
    i = rows(conn)[0]
    r = web.post(f"/admin/fallacies/{i}/save", headers=HX, data={"name": "", "category": "C", "enabled": "1"})
    assert r.status_code == 200 and 'role="alert"' in r.text and 'name="name"' in r.text
    assert conn.execute("SELECT name FROM fallacies WHERE id=%s", (i,)).fetchone() == ("Falácia 0",)


def test_save_refuses_to_go_below_the_floor_with_a_message(web, conn):
    ids = [conn.execute("INSERT INTO fallacies (name, category, source) VALUES (%s,'C','manual') RETURNING id", (f"F{i}",)).fetchone()[0]
           for i in range(FA.MIN_ENABLED)]
    r = web.post(f"/admin/fallacies/{ids[0]}/save", headers=HX, data={"name": "F0", "category": "C"})
    assert r.status_code == 200 and str(FA.MIN_ENABLED) in r.text and 'role="alert"' in r.text
    assert conn.execute("SELECT count(1) FROM fallacies WHERE enabled").fetchone()[0] == FA.MIN_ENABLED


def test_unknown_ids_answer_404_or_a_row_error(web, conn):
    assert web.get("/admin/fallacies/9999", headers=HX).status_code == 404
    assert web.get("/admin/fallacies/9999/edit", headers=HX).status_code == 404
    r = web.post("/admin/fallacies/9999/save", headers=HX, data={"name": "x", "category": "c"})
    assert r.status_code == 200 and "não encontrada" in r.text


# ------------------------------------------------------------------ delete
def test_delete_returns_an_empty_fragment_and_removes_the_row(web, conn):
    i = rows(conn)[0]
    r = web.post(f"/admin/fallacies/{i}/delete", headers=HX)
    assert r.status_code == 200 and r.text == ""
    assert conn.execute("SELECT count(1) FROM fallacies WHERE id=%s", (i,)).fetchone()[0] == 0
    assert web.post(f"/admin/fallacies/{i}/delete", headers=HX).text == ""                 # already gone: just remove the row


def test_delete_of_a_used_fallacy_keeps_the_row_and_tells_to_disable_it(web, conn):
    i = rows(conn)[0]
    s = S.start_session_as_admin(conn, game_type="falacias")
    p = S.join_session(conn, "Ana", "10.0.0.1", game_type="falacias")[1].id
    F.submit_spoken(conn, p, "uma frase", i)
    r = web.post(f"/admin/fallacies/{i}/delete", headers=HX)
    assert r.status_code == 200 and "Falácia 0" in r.text and "desative" in r.text.lower()
    assert conn.execute("SELECT count(1) FROM fallacies WHERE id=%s", (i,)).fetchone()[0] == 1
    assert s is not None


# ------------------------------------------------------------------ the game sees the edits
def test_the_picker_of_a_game_page_uses_the_edited_and_new_names_and_hides_disabled_ones(web, conn):
    ids = rows(conn)
    web.post(f"/admin/fallacies/{ids[0]}/save", headers=HX, data={"name": "Nome editado (Edited)", "category": "Cat X", "enabled": "1"})
    web.post(f"/admin/fallacies/{ids[1]}/save", headers=HX, data={"name": "Falácia 1", "category": "Relevância"})   # disabled
    names = {f.name for f in F.list_fallacies(conn)}
    assert "Nome editado (Edited)" in names and "Falácia 1" not in names


def test_every_input_of_the_page_has_an_accessible_name(web, conn):
    import re
    rows(conn)
    html = web.get("/admin/fallacies", headers=AUTH).text
    labels = set(re.findall(r'<label[^>]*for="([^"]+)"', html))
    bad = []
    for tag in re.findall(r"<input[^>]*>", html):
        if 'type="hidden"' in tag:
            continue
        iid = re.search(r'id="([^"]+)"', tag)
        if 'aria-label=' not in tag and not (iid and iid.group(1) in labels):
            bad.append(tag)
    assert not bad, bad[:3]
