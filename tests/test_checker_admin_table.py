# ruff: noqa: F811, RUF059
"""/admin/checker as ONE table: Jogador | Alegação | Pontos | Verificação da IA | Ações.
One row per claim; the cells of the check (claim, scores, AI check, actions) span the rows of the players who sent it."""
from __future__ import annotations

import re
from pathlib import Path

from app import checker as C
from app import sessions as S
from app import texts as T
from tests.test_checker_admin_web import (  # noqa: F401
    _env,
    admin,
    app,
    basic,
    conn,
    dismiss,
    game,
    needs_admin,
    result,
    score,
)

ROOT = Path(__file__).resolve().parents[1]
LONG = "A Terra tem exatamente seis mil anos de idade e isso foi comprovado por cientistas de várias universidades"
WHY = "Evidências geológicas e radiométricas independentes indicam uma idade de cerca de 4,5 bilhões de anos para o planeta."


def page(admin, path="/admin/checker"):
    return admin.get(path, headers=basic()).text


def tbody_of(html, check_id):
    m = re.search(rf'<tbody[^>]*id="ck-{check_id}".*?</tbody>', html, re.DOTALL)
    assert m, "no tbody for the check"
    return m.group(0)


def test_the_header_has_exactly_the_requested_columns_in_order(admin, conn):
    s, (ana, _) = game(conn)
    needs_admin(conn, ana, LONG, -4)
    html = page(admin)
    head = re.search(r"<table[^>]*cktable[^>]*>\s*<thead>(.*?)</thead>", html, re.DOTALL).group(1)
    assert [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", head, re.DOTALL)] == [T.CK_COL_PLAYER, T.CK_TH_CLAIM, T.CK_TH_POINTS,
                                                       T.CK_TH_AI_CHECK, T.CK_TH_ACTIONS]


def test_one_row_per_claim_and_the_check_cells_span_the_rows(admin, conn):
    s, (ana, bia) = game(conn)
    cl = needs_admin(conn, ana, LONG, -4, explanation=WHY)
    C.submit_typed_claim(conn, bia, LONG.lower(), 1)                       # the same text: the same check, a second player
    body = tbody_of(page(admin), cl.check_id)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL)
    assert len(rows) == 2
    assert "Ana" in rows[0] and "💩💩💩💩" in rows[0] and "Bia" in rows[1] and "⭐" in rows[1]
    assert rows[0].count('rowspan="2"') == 3 and "rowspan" not in rows[1]          # claim, AI check, actions: 3 cells
    assert LONG in rows[0] and LONG.lower() not in rows[1]


def lines_of(body):
    cell = re.search(r'<td[^>]*data-label="Pontos"[^>]*>(.*?)</td>', body, re.DOTALL).group(1)
    first_two = [re.sub(r"\s+", " ", d) for d in re.findall(r'<div[^>]*class="ptline"[^>]*>(.*?)</div>', cell, re.DOTALL)]
    host = re.search(r"<summary[^>]*>(.*?)</summary>", cell, re.DOTALL)
    host = host.group(1) if host else re.search(r'<div[^>]*ptline host[^>]*>(.*?)</div>', cell, re.DOTALL).group(1)
    return first_two + [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", host))]


def test_pontos_cell_has_three_lines_with_poop_or_stars(admin, conn):
    s, (ana, _) = game(conn)
    a = needs_admin(conn, ana, LONG, -4, score=3, verdict="true")
    lines = lines_of(tbody_of(page(admin), a.check_id))
    assert len(lines) == 3
    assert "Palpite:" in lines[0] and "💩💩💩💩" in lines[0]
    assert "IA:" in lines[1] and "⭐⭐⭐" in lines[1]
    assert "Host:" in lines[2] and "💩" not in lines[2] and "⭐" not in lines[2]                 # nothing set yet
    score(admin, conn, a.check_id, "-2")
    assert "💩💩" in lines_of(tbody_of(page(admin, "/admin/checker?all=1"), a.check_id))[2]


def test_no_score_yet_shows_a_dash_and_zero_shows_the_neutral_face(admin, conn):
    s, (ana, _) = game(conn)
    b = C.submit_typed_claim(conn, ana, "ainda na fila", 0)
    lines = lines_of(tbody_of(page(admin), b.check_id))
    assert "😐 0" in lines[0] and "–" in lines[1] and "–" in lines[2]


def test_clicking_host_opens_a_picker_of_buttons_from_minus_5_to_5(admin, conn):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, LONG, -4)
    body = tbody_of(page(admin), cl.check_id)
    det = re.search(r'<details[^>]*hostpick[^>]*>(.*?)</details>', body, re.DOTALL)
    assert det and " open" not in re.search(r'<details[^>]*hostpick[^>]*>', body).group(0)
    assert "Host:" in re.search(r"<summary[^>]*>(.*?)</summary>", det.group(1), re.DOTALL).group(1)
    form = re.search(r"<form[^>]*hostscore[^>]*>.*?</form>", det.group(1), re.DOTALL).group(0)
    assert 'hx-post="/admin/checker/score"' in form and f"#ck-{cl.check_id}" in form and "<input type=\"number\"" not in form and "<select" not in form
    assert all(n in form for n in ('name="session_id"', 'name="check_id"', 'name="csrf"'))
    vals = re.findall(r'<button[^>]*name="score"[^>]*value="(-?\d+)"|<button[^>]*value="(-?\d+)"[^>]*name="score"', form)
    assert [int(a or b) for a, b in vals] == list(range(-5, 6))


def test_the_number_field_shows_the_saved_score_and_only_that_tbody_comes_back(admin, conn):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, LONG, -4)
    r = score(admin, conn, cl.check_id, "-5")
    assert r.status_code == 200 and "<html" not in r.text and r.text.lstrip().startswith("<tbody") and f'id="ck-{cl.check_id}"' in r.text
    assert re.search(r'<button[^>]*class="[^"]*pick[^"]* on"', r.text) and "💩💩💩💩💩" in r.text and 'id="pendingcount"' in r.text
    assert C.get_check(conn, cl.check_id).admin_score == -5


def test_ninguem_pontua_is_a_button_in_the_actions_column(admin, conn):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, LONG, -4)
    body = tbody_of(page(admin), cl.check_id)
    assert re.search(r'data-label="Ações".*?<form[^>]*hx-post="/admin/checker/dismiss".*?>' + re.escape(T.CK_DISMISS) + '</button>', body, re.DOTALL)
    r = dismiss(admin, conn, cl.check_id)
    assert T.CK_DISMISSED in r.text and C.get_check(conn, cl.check_id).admin_score is None


def test_long_claim_and_ai_check_are_collapsed_by_default_and_hold_the_full_text(admin, conn):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, LONG, -4, explanation=WHY)
    body = tbody_of(page(admin), cl.check_id)
    folds = re.findall(r"<details([^>]*ckfold[^>]*)>(.*?)</details>", body, re.DOTALL)
    assert len(folds) == 2 and all("ckfold" in a and " open" not in a for a, _ in folds)
    assert LONG in folds[0][1] and WHY in folds[1][1] and "clamp" in folds[0][1] and "clamp" in folds[1][1]
    assert T.CK_VERDICTS["false"] in re.search(r"<summary[^>]*>(.*?)</summary>", folds[1][1], re.DOTALL).group(1)
    assert "https://example.org/a" in folds[1][1]


def test_a_check_the_ai_has_not_answered_shows_its_state_without_a_fold(admin, conn):
    s, (ana, _) = game(conn)
    cl = C.submit_typed_claim(conn, ana, "ainda na fila", 2)
    body = tbody_of(page(admin), cl.check_id)
    assert T.CK_STATUS["queued"] in body and body.count("ckfold") == 1


def test_an_ended_game_has_no_controls(admin, conn):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, LONG, -4)
    S.end_session(conn, s.id, None)
    body = tbody_of(page(admin), cl.check_id)
    assert "hx-post" not in body and "<button" not in body and "<select" not in body


def test_the_table_fits_the_screen_area():
    css = (ROOT / "app" / "static" / "app.css").read_text()
    assert re.search(r"table\.cktable \{[^}]*table-layout: fixed[^}]*width: 100%|table\.cktable \{[^}]*width: 100%[^}]*table-layout: fixed", css)
    assert re.search(r"\.ckwrap \{[^}]*max-height:[^}]*overflow: auto", css) and "overflow-wrap: anywhere" in css
    assert re.search(r"details\.ckfold:not\(\[open\]\) \.clamp \{[^}]*text-overflow: ellipsis", css)
    assert "min-width" not in re.search(r"table\.cktable \{[^}]*\}", css).group(0)


def test_the_page_keeps_the_open_folds_open_across_polls():
    js = (ROOT / "app" / "static" / "checker.js").read_text()
    assert "ckfold" in js


def test_the_page_wraps_the_table_and_loads_the_fold_script(admin, conn):
    s, (ana, _) = game(conn)
    needs_admin(conn, ana, LONG, -4)
    html = page(admin)
    assert re.search(r'<div[^>]*class="ckwrap"', html) and 'src="/checker.js"' in html and 'src="/ckadmin.js"' in html


def test_out_of_band_pieces_of_the_reply_sit_in_a_template(admin, conn):
    """The reply starts with a <tbody>; htmx parses it inside a <table>, which pulls loose siblings (the board's own table) into the page."""
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, LONG, -4)
    r = score(admin, conn, cl.check_id, "2")
    tail = r.text.split("</tbody>", 1)[1]
    assert tail.lstrip().startswith("<template>") and tail.rstrip().endswith("</template>")
    assert 'id="pendingcount"' in tail and 'id="ckboard"' in tail


def test_actions_have_editar_and_ninguem_pontua_and_an_ended_game_has_neither(admin, conn):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, LONG, -4)
    body = tbody_of(page(admin), cl.check_id)
    acts = re.search(r'data-label="Ações".*?</td>', body, re.DOTALL).group(0)
    assert re.search(r"<button[^>]*data-ckedit[^>]*>" + re.escape(T.CK_EDIT) + "</button>", acts) and T.CK_DISMISS in acts
    js = (ROOT / "app" / "static" / "ckadmin.js").read_text()
    assert "data-ckedit" in js and "hostpick" in js
    css = (ROOT / "app" / "static" / "app.css").read_text()
    assert re.search(r"table\.cktable \.acts button \{[^}]*font-size: \.72rem", css)


# ---------------------------------------------------------------- Verificar
def failed_check(conn, uid, text="A Lua é de queijo"):
    cl = C.submit_typed_claim(conn, uid, text, -4)
    for _ in range(C.MAX_ATTEMPTS):
        ck = C.claim_next_check(conn)
        C.fail_check(conn, ck.id, "limite de requisições do provedor do modelo (HTTP 429)")
    return cl


def recheck(admin, conn, check_id, htmx=True, csrf=None):
    from tests.test_checker_admin_web import H_, sid_of, tok
    return admin.post("/admin/checker/recheck", data={"session_id": str(sid_of(conn)), "check_id": str(check_id), "csrf": tok(conn) if csrf is None else csrf},
                      headers=basic() | (H_ if htmx else {}))


def test_verificar_button_shows_only_when_the_ai_call_failed(admin, conn):
    s, (ana, _) = game(conn)
    bad = failed_check(conn, ana)
    low = needs_admin(conn, ana, "outra alegação", -1)                                  # low confidence: no error
    html = page(admin)
    assert C.get_check(conn, bad.check_id).status == "needs_admin"
    assert re.search(r"<button[^>]*>" + re.escape(T.CK_RECHECK) + "</button>", tbody_of(html, bad.check_id))
    assert T.CK_RECHECK not in tbody_of(html, low.check_id)


def test_verificar_queues_the_check_again_and_clears_the_error(admin, conn, monkeypatch):
    from app import checkworker
    woke = []
    monkeypatch.setattr(checkworker, "wake", lambda: woke.append(1))
    s, (ana, _) = game(conn)
    bad = failed_check(conn, ana)
    r = recheck(admin, conn, bad.check_id)
    ck = C.get_check(conn, bad.check_id)
    assert r.status_code == 200 and r.text.lstrip().startswith("<tbody") and woke
    assert (ck.status, ck.attempts, ck.error) == ("queued", 0, None)
    assert T.CK_RECHECK not in r.text and T.CK_STATUS["queued"] in r.text
    assert C.claim_next_check(conn).id == bad.check_id                                  # the worker picks it up


def test_verificar_is_refused_without_error_other_states_wrong_csrf_and_ended_games(admin, conn):
    s, (ana, _) = game(conn)
    low = needs_admin(conn, ana, "baixa confiança", -1)
    assert recheck(admin, conn, low.check_id).status_code == 404 and C.get_check(conn, low.check_id).status == "needs_admin"
    bad = failed_check(conn, ana)
    assert recheck(admin, conn, bad.check_id, csrf="x").status_code == 403 and C.get_check(conn, bad.check_id).status == "needs_admin"
    assert recheck(admin, conn, bad.check_id, htmx=False).status_code == 303
    S.end_session(conn, s.id, None)
    cl = C.get_check(conn, bad.check_id)
    assert cl.status == "queued"                                                          # the plain POST above worked before the end
    assert admin.post("/admin/checker/recheck", data={"session_id": str(s.id), "check_id": str(bad.check_id), "csrf": "x"}, headers=basic()).status_code == 403


def test_the_domain_function_requires_an_error_and_an_active_session(conn):
    s, (ana, _) = game(conn)
    low = needs_admin(conn, ana, "baixa confiança", -1)
    import pytest
    with pytest.raises(C.CheckNotFound):
        C.admin_recheck(conn, s.id, low.check_id)
    bad = failed_check(conn, ana)
    assert C.admin_recheck(conn, s.id, bad.check_id).status == "queued"
    with pytest.raises(C.CheckNotFound):
        C.admin_recheck(conn, s.id, bad.check_id)                                          # already queued


def test_choosing_a_score_closes_the_picker():
    js = (ROOT / "app" / "static" / "ckadmin.js").read_text()
    assert "button.pick" in js and "delete open[d.dataset.id]" in js and "d.open = false" in js
