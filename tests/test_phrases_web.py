"""Admin phrases page: auth, CSRF, add / edit / delete / search, and the seed-only corpus loader."""
from __future__ import annotations

import base64
import csv
import re

import pytest
from starlette.testclient import TestClient

from app import admin as A
from app import phrases as P
from app.db import connect
from app.main import create_app
from corpus.load import load_phrases as seed_phrases
from tests.test_cards import load_phrases

PW = "s3cret-pässword"
TOK = A.phrases_token(PW)
AUTH = {"authorization": "Basic " + base64.b64encode(f"admin:{PW}".encode()).decode()}
HX = {**AUTH, "x-csrf": TOK, "hx-request": "true"}      # what htmx sends: fragments, not full pages


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    load_phrases(c, n=40)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


@pytest.fixture
def web(conn, test_db_url, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", PW)
    app = create_app(test_db_url)
    with TestClient(app):
        yield TestClient(app, client=("203.0.113.99", 50000), follow_redirects=False)


def pid_of(conn, text):
    return conn.execute("SELECT id FROM phrases WHERE text=%s", (text,)).fetchone()[0]


# ------------------------------------------------------------------ auth / csrf
def test_every_phrase_route_requires_credentials(web, conn):
    pid = conn.execute("SELECT min(id) FROM phrases").fetchone()[0]
    for method, url in [("get", "/admin/phrases"), ("post", "/admin/phrases/add"), ("get", f"/admin/phrases/{pid}"),
                        ("get", f"/admin/phrases/{pid}/edit"), ("post", f"/admin/phrases/{pid}/save"),
                        ("post", f"/admin/phrases/{pid}/delete"), ("post", "/admin/phrases/bulk-delete"),
                        ("post", "/admin/phrases/bulk-enable"), ("post", "/admin/phrases/bulk-disable")]:
        r = getattr(web, method)(url)
        assert r.status_code == 401, (method, url)
        r = getattr(web, method)(url, headers={"authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()})
        assert r.status_code == 401, (method, url)


def test_mutations_need_csrf_and_change_nothing_without_it(web, conn):
    pid = conn.execute("SELECT min(id) FROM phrases").fetchone()[0]
    before = conn.execute("SELECT id, text, score, enabled FROM phrases ORDER BY id").fetchall()
    for tok in (None, "", "deadbeef", A.start_token(PW)):                        # another scope's token must not work
        h = dict(AUTH, **({"x-csrf": tok} if tok is not None else {}))
        assert web.post("/admin/phrases/add", data={"text": "nova frase teste"}, headers=h).status_code == 403
        assert web.post(f"/admin/phrases/{pid}/save", data={"text": "x y z", "score": "1"}, headers=h).status_code == 403
        assert web.post(f"/admin/phrases/{pid}/delete", headers=h).status_code == 403
    assert conn.execute("SELECT id, text, score, enabled FROM phrases ORDER BY id").fetchall() == before


def test_page_embeds_csrf_for_htmx_and_for_the_add_form(web):
    html = web.get("/admin/phrases", headers=AUTH).text
    assert TOK in html and html.count(TOK) == 3             # hx-headers + add form + bulk form


def test_admin_dashboard_links_to_phrases(web, conn):
    assert 'href="/admin/phrases"' in web.get("/admin", headers=AUTH).text


# ------------------------------------------------------------------ list / search
def test_list_shows_phrases_with_stats_and_frozen_marker(web, conn):
    P.create(conn, "minha frase manual")
    conn.execute("UPDATE phrases SET dealt=10, marked=4 WHERE text='minha frase manual'")
    html = web.get("/admin/phrases", headers=AUTH).text
    assert "minha frase manual" in html and "40%" in html and "fixo" in html
    assert html.count('class="disabled"') == 0 and "41 frase(s)" in html


def test_search_filter_and_escaping(web, conn):
    conn.execute("INSERT INTO phrases (text, source, score) VALUES ('com <i>html</i> dentro', 'chat', 3)")
    html = web.get("/admin/phrases", headers=AUTH, params={"q": "html"}).text
    assert "com &lt;i&gt;html&lt;/i&gt; dentro" in html and "<i>html</i>" not in html and "1 frase(s)" in html
    assert "1 frase(s)" in web.get("/admin/phrases", headers=AUTH, params={"source": "chat", "q": "html"}).text
    assert "Nenhuma frase" in web.get("/admin/phrases", headers=AUTH, params={"q": "zzzzzz"}).text


def test_pagination_links_keep_filters(web, conn):
    for i in range(60):
        conn.execute("INSERT INTO phrases (text, source, score) VALUES (%s, 'chat', 1)", (f"frase paginada numero {i}",))
    p1 = web.get("/admin/phrases", headers=AUTH, params={"q": "paginada"}).text
    assert "Página 1 de 2" in p1 and "q=paginada" in p1 and "page=2" in p1
    p2 = web.get("/admin/phrases", headers=AUTH, params={"q": "paginada", "page": 2}).text
    assert "Página 2 de 2" in p2
    assert web.get("/admin/phrases", headers=AUTH, params={"q": "paginada", "page": 99}).status_code == 200   # clamps


# ------------------------------------------------------------------ add
def test_add_flow_creates_manual_phrase_with_score_99(web, conn):
    r = web.post("/admin/phrases/add", data={"text": "  Minha Nova FRASE ", "csrf": TOK}, headers=AUTH)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/phrases?")
    assert conn.execute("SELECT source, score, prior_score, enabled FROM phrases WHERE text='minha nova frase'").fetchone() \
        == ("manual", 99.0, 99.0, True)
    page = web.get(r.headers["location"], headers=AUTH).text
    assert "minha nova frase" in page and "Frase adicionada com score 99" in page and "fixo" in page


@pytest.mark.parametrize("bad,msg", [(" ".join(["w"] * 11), "Frase inválida"), ("bom dia a todos", "Frase inválida"),
                                      ("a" * 101, "Frase inválida")])
def test_add_invalid_shows_error_and_stores_nothing(web, conn, bad, msg):
    n = conn.execute("SELECT count(*) FROM phrases").fetchone()[0]
    r = web.post("/admin/phrases/add", data={"text": bad, "csrf": TOK}, headers=AUTH)
    assert r.status_code == 200 and msg in r.text
    assert conn.execute("SELECT count(*) FROM phrases").fetchone()[0] == n


def test_add_duplicate_shows_error(web, conn):
    web.post("/admin/phrases/add", data={"text": "minha nova frase", "csrf": TOK}, headers=AUTH)
    r = web.post("/admin/phrases/add", data={"text": "MINHA nova frase", "csrf": TOK}, headers=AUTH)
    assert r.status_code == 200 and "já existe" in r.text
    assert conn.execute("SELECT count(*) FROM phrases WHERE text='minha nova frase'").fetchone()[0] == 1


# ------------------------------------------------------------------ edit (inline rows)
def test_edit_form_row_then_save(web, conn):
    P.create(conn, "minha frase manual")
    pid = pid_of(conn, "minha frase manual")
    form = web.get(f"/admin/phrases/{pid}/edit", headers=HX).text
    assert f'id="p-{pid}"' in form and 'name="text"' in form and 'name="score"' in form and 'name="enabled"' in form
    r = web.post(f"/admin/phrases/{pid}/save", data={"text": "Frase Editada Aqui", "score": "7,5", "enabled": "1"}, headers=HX)
    assert r.status_code == 200 and "frase editada aqui" in r.text and "7.5" in r.text and "fixo" not in r.text
    assert conn.execute("SELECT text, score, prior_score, enabled FROM phrases WHERE id=%s", (pid,)).fetchone() \
        == ("frase editada aqui", 7.5, 7.5, True)


def test_edit_can_disable_and_unchecked_box_means_disabled(web, conn):
    pid = conn.execute("SELECT min(id) FROM phrases").fetchone()[0]
    text, score = conn.execute("SELECT text, score FROM phrases WHERE id=%s", (pid,)).fetchone()
    r = web.post(f"/admin/phrases/{pid}/save", data={"text": text, "score": str(score)}, headers=HX)   # no 'enabled' field
    assert r.status_code == 200 and 'class="disabled"' in r.text
    assert conn.execute("SELECT enabled FROM phrases WHERE id=%s", (pid,)).fetchone()[0] is False


def test_edit_validation_errors_keep_the_typed_values_and_store_nothing(web, conn):
    pid = conn.execute("SELECT min(id) FROM phrases").fetchone()[0]
    before = conn.execute("SELECT text, score FROM phrases WHERE id=%s", (pid,)).fetchone()
    r = web.post(f"/admin/phrases/{pid}/save", data={"text": "ok tres palavras", "score": "abc", "enabled": "1"}, headers=HX)
    assert r.status_code == 200 and "Score inválido" in r.text and 'value="abc"' in r.text and 'value="ok tres palavras"' in r.text
    r = web.post(f"/admin/phrases/{pid}/save", data={"text": " ".join(["w"] * 11), "score": "5", "enabled": "1"}, headers=HX)
    assert "Frase inválida" in r.text
    r = web.post(f"/admin/phrases/{pid}/save", data={"text": before[0], "score": "100", "enabled": "1"}, headers=HX)
    assert "Score inválido" in r.text
    assert conn.execute("SELECT text, score FROM phrases WHERE id=%s", (pid,)).fetchone() == before


def test_edit_can_shorten_an_extracted_phrase_to_one_word_and_keeps_its_source(web, conn):
    pid = pid_of(conn, "frase auto numero 3")
    src = conn.execute("SELECT source FROM phrases WHERE id=%s", (pid,)).fetchone()[0]
    r = web.post(f"/admin/phrases/{pid}/save", data={"text": "Curta", "score": "5", "enabled": "1"}, headers=HX)
    assert r.status_code == 200 and "Frase inválida" not in r.text
    assert conn.execute("SELECT text, source FROM phrases WHERE id=%s", (pid,)).fetchone() == ("curta", src)
    r = web.post(f"/admin/phrases/{pid}/save", data={"text": " ".join(["w"] * 11), "score": "5", "enabled": "1"}, headers=HX)
    assert "Frase inválida" in r.text
    assert conn.execute("SELECT text FROM phrases WHERE id=%s", (pid,)).fetchone()[0] == "curta"


def test_edit_to_existing_text_is_duplicate_error(web, conn):
    a, b = [r[0] for r in conn.execute("SELECT text FROM phrases ORDER BY id LIMIT 2")]
    pid = pid_of(conn, b)
    r = web.post(f"/admin/phrases/{pid}/save", data={"text": a, "score": "3", "enabled": "1"}, headers=HX)
    assert "já existe" in r.text and conn.execute("SELECT text FROM phrases WHERE id=%s", (pid,)).fetchone()[0] == b


def test_cancel_returns_display_row_and_unknown_id_is_404(web, conn):
    pid = conn.execute("SELECT min(id) FROM phrases").fetchone()[0]
    assert f'id="p-{pid}"' in web.get(f"/admin/phrases/{pid}", headers=HX).text
    assert web.get("/admin/phrases/99999999", headers=AUTH).status_code == 404
    assert web.get("/admin/phrases/99999999/edit", headers=AUTH).status_code == 404
    assert "não encontrada" in web.post("/admin/phrases/99999999/save", data={"text": "a b c", "score": "1"}, headers=HX).text


# ------------------------------------------------------------------ delete
def test_delete_removes_row(web, conn):
    P.create(conn, "minha frase manual")
    pid = pid_of(conn, "minha frase manual")
    r = web.post(f"/admin/phrases/{pid}/delete", headers=HX)
    assert r.status_code == 200 and r.text == ""
    assert conn.execute("SELECT count(*) FROM phrases WHERE id=%s", (pid,)).fetchone()[0] == 0
    assert web.post(f"/admin/phrases/{pid}/delete", headers=HX).text == ""          # double click: harmless


def test_delete_and_disable_blocked_below_24_enabled(web, conn):
    conn.execute("DELETE FROM phrases WHERE id NOT IN (SELECT id FROM phrases ORDER BY id LIMIT 24)")
    pid = conn.execute("SELECT min(id) FROM phrases").fetchone()[0]
    r = web.post(f"/admin/phrases/{pid}/delete", headers=HX)
    assert "pelo menos 24" in r.text and f'id="p-{pid}"' in r.text                    # row stays, with an error row
    text, score = conn.execute("SELECT text, score FROM phrases WHERE id=%s", (pid,)).fetchone()
    r = web.post(f"/admin/phrases/{pid}/save", data={"text": text, "score": str(score)}, headers=HX)   # unchecked = disable
    assert "pelo menos 24" in r.text
    assert conn.execute("SELECT count(*) FROM phrases WHERE enabled").fetchone()[0] == 24


# ------------------------------------------------------------------ end to end with a game
def test_learned_score_is_visible_on_the_page_after_a_game(web, conn):
    from app import scoring as G
    from app import sessions as S
    P.create(conn, "minha frase manual")
    c = TestClient(web.app, client=("203.0.113.10", 5), follow_redirects=False)
    c.post("/start", data={"name": "Ana"})
    c.post("/mark/0")
    sid = S.get_active_session(conn).id
    assert G.finish_session(conn, sid) is not None
    rows, _ = P.search(conn, sort="dealt")
    assert rows[0].dealt == 1
    html = web.get("/admin/phrases", headers=AUTH, params={"sort": "dealt"}).text
    assert "100%" in html                                                              # the marked one: 1/1


# ------------------------------------------------------------------ loader is seed-only
def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["text", "source", "score", "enabled"])
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def empty(conn):
    conn.execute("TRUNCATE phrases CASCADE")
    return conn


ROWS = [{"text": "primeira frase boa", "source": "chat", "score": "4.5", "enabled": "1"},
        {"text": "segunda frase boa", "source": "transcript", "score": "2", "enabled": "0"},
        {"text": "frase manual csv", "source": "manual", "score": "0", "enabled": "1"},
        {"text": "longa demais " + "x " * 9 + "y", "source": "chat", "score": "9", "enabled": "1"}]   # 12 words: rejected


def test_seed_populates_an_empty_table(empty, tmp_path):
    write_csv(tmp_path / "p.csv", ROWS)
    stats = seed_phrases(empty, tmp_path / "p.csv")
    assert (stats["upserted"], stats["enabled"], stats["disabled"], stats["rejected"]) == (3, 2, 1, 1)
    got = {r[0]: r[1:] for r in empty.execute("SELECT text, score, prior_score, enabled, source FROM phrases")}
    assert got["primeira frase boa"] == (4.5, 4.5, True, "chat")
    assert got["segunda frase boa"][2] is False
    assert got["frase manual csv"][:2] == (99.0, 99.0)                              # manual without score -> 99


def test_seed_does_not_touch_a_populated_table(empty, tmp_path):
    write_csv(tmp_path / "p.csv", ROWS)
    seed_phrases(empty, tmp_path / "p.csv")
    empty.execute("UPDATE phrases SET score = 7, prior_score = 7, enabled = false WHERE text='primeira frase boa'")
    empty.execute("DELETE FROM phrases WHERE text='segunda frase boa'")
    stats = seed_phrases(empty, tmp_path / "p.csv")
    assert "skipped" in stats and stats["upserted"] == 0
    assert empty.execute("SELECT score, enabled FROM phrases WHERE text='primeira frase boa'").fetchone() == (7.0, False)
    assert empty.execute("SELECT count(*) FROM phrases WHERE text='segunda frase boa'").fetchone()[0] == 0   # stays deleted


def test_seed_force_reimports_but_keeps_play_statistics(empty, tmp_path):
    write_csv(tmp_path / "p.csv", ROWS)
    seed_phrases(empty, tmp_path / "p.csv")
    empty.execute("UPDATE phrases SET score = 7, prior_score = 7, dealt = 5, marked = 2 WHERE text='primeira frase boa'")
    seed_phrases(empty, tmp_path / "p.csv", force=True)
    assert empty.execute("SELECT score, prior_score, dealt, marked FROM phrases WHERE text='primeira frase boa'").fetchone() \
        == (4.5, 4.5, 5, 2)


def test_seed_cli_flag_exists():
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "-m", "corpus.cli", "load", "--help"], capture_output=True, text=True, check=False)
    assert out.returncode == 0 and "--force" in out.stdout and re.search(r"EMPTY", out.stdout)


def test_errors_live_inside_the_row_so_a_later_save_leaves_no_stale_message(web, conn):
    """Regression (found in the browser): an error used to be a SIBLING <tr> that survived the next save."""
    pid = conn.execute("SELECT min(id) FROM phrases").fetchone()[0]
    text = conn.execute("SELECT text FROM phrases WHERE id=%s", (pid,)).fetchone()[0]
    bad = web.post(f"/admin/phrases/{pid}/save", data={"text": text, "score": "abc", "enabled": "1"}, headers=HX).text
    assert bad.count("<tr") == 1 and "Score inválido" in bad and 'class="cellerr"' in bad     # ONE row, error inside it
    ok = web.post(f"/admin/phrases/{pid}/save", data={"text": text, "score": "4", "enabled": "1"}, headers=HX).text
    assert ok.count("<tr") == 1 and "cellerr" not in ok


# ------------------------------------------------------------------ bulk delete
def test_page_has_a_checkbox_per_row_and_select_buttons(web, conn):
    html = web.get("/admin/phrases", headers=AUTH).text
    assert html.count('name="ids"') == conn.execute("SELECT count(*) FROM phrases").fetchone()[0]
    assert "Selecionar todas" in html and "Limpar seleção" in html and "Excluir selecionadas" in html


def test_bulk_delete_removes_selected_only(web, conn):
    ids = [r[0] for r in conn.execute("SELECT id FROM phrases ORDER BY id LIMIT 4")]
    r = web.post("/admin/phrases/bulk-delete", headers=AUTH, data={"csrf": TOK, "ids": [str(i) for i in ids[:3]]})
    assert r.status_code == 303 and "deleted=3" in r.headers["location"]
    assert conn.execute("SELECT count(*) FROM phrases").fetchone()[0] == 37
    assert conn.execute("SELECT count(*) FROM phrases WHERE id=%s", (ids[3],)).fetchone()[0] == 1
    assert "3 frase(s) excluída(s)" in web.get(r.headers["location"], headers=AUTH).text


def test_bulk_delete_needs_csrf(web, conn):
    ids = [str(r[0]) for r in conn.execute("SELECT id FROM phrases LIMIT 2")]
    for data in ({"ids": ids}, {"csrf": "deadbeef", "ids": ids}, {"csrf": A.start_token(PW), "ids": ids}):
        assert web.post("/admin/phrases/bulk-delete", headers=AUTH, data=data).status_code == 403
    assert conn.execute("SELECT count(*) FROM phrases").fetchone()[0] == 40


def test_bulk_delete_below_the_floor_shows_error_and_deletes_nothing(web, conn):
    ids = [str(r[0]) for r in conn.execute("SELECT id FROM phrases ORDER BY id LIMIT 20")]     # 40 - 20 < 24
    r = web.post("/admin/phrases/bulk-delete", headers=AUTH, data={"csrf": TOK, "ids": ids})
    assert r.status_code == 200 and 'role="alert"' in r.text
    assert conn.execute("SELECT count(*) FROM phrases").fetchone()[0] == 40


def test_bulk_delete_with_nothing_selected_or_junk_ids_is_harmless(web, conn):
    r = web.post("/admin/phrases/bulk-delete", headers=AUTH, data={"csrf": TOK, "ids": ["abc", "-1", ""]})
    assert r.status_code == 303 and "deleted=0" in r.headers["location"]
    assert conn.execute("SELECT count(*) FROM phrases").fetchone()[0] == 40


# ------------------------------------------------------------------ bulk activate / deactivate
def enabled_ids(conn, flag):
    return {r[0] for r in conn.execute("SELECT id FROM phrases WHERE enabled = %s", (flag,))}


def test_page_has_bulk_activate_and_deactivate_buttons(web):
    html = web.get("/admin/phrases", headers=AUTH).text
    assert 'formaction="/admin/phrases/bulk-enable"' in html and "Ativar selecionadas" in html
    assert 'formaction="/admin/phrases/bulk-disable"' in html and "Desativar selecionadas" in html


def test_bulk_disable_then_enable_selected_only(web, conn):
    ids = [r[0] for r in conn.execute("SELECT id FROM phrases ORDER BY id LIMIT 5")]
    r = web.post("/admin/phrases/bulk-disable", headers=AUTH, data={"csrf": TOK, "ids": [str(i) for i in ids[:3]]})
    assert r.status_code == 303 and "deactivated=3" in r.headers["location"]
    assert enabled_ids(conn, False) == set(ids[:3])                                  # only the selected ones
    assert "3 frase(s) desativada(s)" in web.get(r.headers["location"], headers=AUTH).text
    r = web.post("/admin/phrases/bulk-enable", headers=AUTH, data={"csrf": TOK, "ids": [str(i) for i in ids[:2]]})
    assert r.status_code == 303 and "activated=2" in r.headers["location"]
    assert enabled_ids(conn, False) == {ids[2]}
    assert "2 frase(s) ativada(s)" in web.get(r.headers["location"], headers=AUTH).text


def test_bulk_enable_of_already_active_phrases_reports_zero(web, conn):
    ids = [str(r[0]) for r in conn.execute("SELECT id FROM phrases LIMIT 2")]
    r = web.post("/admin/phrases/bulk-enable", headers=AUTH, data={"csrf": TOK, "ids": ids})
    assert r.status_code == 303 and "activated=0" in r.headers["location"]
    assert "0 frase(s) ativada(s)" in web.get(r.headers["location"], headers=AUTH).text


def test_bulk_set_enabled_keeps_filters_and_page_in_the_redirect(web, conn):
    ids = [str(r[0]) for r in conn.execute("SELECT id FROM phrases LIMIT 2")]
    r = web.post("/admin/phrases/bulk-disable", headers=AUTH,
                 data={"csrf": TOK, "ids": ids, "q": "auto", "source": "transcript", "sort": "text", "page": "1"})
    loc = r.headers["location"]
    assert "q=auto" in loc and "source=transcript" in loc and "sort=text" in loc and "deactivated=2" in loc


def test_bulk_set_enabled_needs_csrf_and_changes_nothing_without_it(web, conn):
    ids = [str(r[0]) for r in conn.execute("SELECT id FROM phrases LIMIT 2")]
    for url in ("/admin/phrases/bulk-disable", "/admin/phrases/bulk-enable"):
        for data in ({"ids": ids}, {"csrf": "deadbeef", "ids": ids}, {"csrf": A.start_token(PW), "ids": ids}):
            assert web.post(url, headers=AUTH, data=data).status_code == 403
    assert len(enabled_ids(conn, True)) == 40


def test_bulk_disable_below_the_floor_shows_error_and_changes_nothing(web, conn):
    ids = [str(r[0]) for r in conn.execute("SELECT id FROM phrases ORDER BY id LIMIT 20")]      # 40 - 20 < 24
    r = web.post("/admin/phrases/bulk-disable", headers=AUTH, data={"csrf": TOK, "ids": ids})
    assert r.status_code == 200 and 'role="alert"' in r.text and "24 frases ativas" in r.text
    assert len(enabled_ids(conn, True)) == 40


def test_bulk_set_enabled_with_junk_ids_is_harmless(web, conn):
    for url, key in (("/admin/phrases/bulk-disable", "deactivated=0"), ("/admin/phrases/bulk-enable", "activated=0")):
        r = web.post(url, headers=AUTH, data={"csrf": TOK, "ids": ["abc", "-1", ""]})
        assert r.status_code == 303 and key in r.headers["location"]
    assert len(enabled_ids(conn, True)) == 40
