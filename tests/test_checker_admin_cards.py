# ruff: noqa: F811, RUF059
"""/admin/checker: below the board ("Placar"), the claim cards of EVERY player, in the same 3-column grid as /checker
(ol.pairs.claims of details.ckcard), newest first. A click on a board row toggles that player in a filter (several allowed);
no player selected = every card. The filter lives in the browser (ckadmin.js), so the 3 s poll keeps it."""
from __future__ import annotations

import re
import time
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


def page(admin, path="/admin/checker"):
    return admin.get(path, headers=basic()).text


def cards_block(html):
    """From the #ckcards div to its 'no match' line (the cards hold nested <ol>/<li> of sources, so no lazy </ol> match)."""
    m = re.search(r'<div[^>]*id="ckcards".*?id="ckcards-none"[^>]*>[^<]*</p>', html, re.DOTALL)
    assert m, "no #ckcards block with a list"
    return m.group(0)


def card_items(html):
    """(user id, html of the card) per top-level card: split on the <li data-user=...> openings."""
    parts = re.split(r'<li[^>]*data-user="([^"]+)"[^>]*>', cards_block(html))[1:]
    return list(zip(parts[0::2], parts[1::2]))


def three(conn):
    s, (ana, bia) = game(conn)
    a1 = C.submit_typed_claim(conn, ana, "A Terra é plana", -5)
    time.sleep(0.01)
    b1 = C.submit_typed_claim(conn, bia, "O Brasil tem 26 estados e um Distrito Federal", 4)
    time.sleep(0.01)
    a2 = C.submit_typed_claim(conn, ana, "Vacinas causam autismo", -3)
    return s, ana, bia, (a1, b1, a2)


# ---------------------------------------------------------------- domain
def test_session_claims_lists_every_players_claims_newest_first_with_the_name(conn):
    s, ana, bia, (a1, b1, a2) = three(conn)
    rows = C.session_claims(conn, s.id)
    assert [(r.name, r.claim.claim_id) for r in rows] == [("Ana", a2.id), ("Bia", b1.id), ("Ana", a1.id)]
    assert {r.user_id for r in rows} == {ana, bia}
    assert rows[1].claim.guess == 4 and rows[1].claim.quote.startswith("O Brasil")


def test_session_claims_only_sees_its_own_session(conn):
    s, ana, bia, _ = three(conn)
    S.end_session(conn, s.id, None)
    s2, (cai,) = game(conn, players=("Caio",))
    C.submit_typed_claim(conn, cai, "A Lua é feita de queijo", -5)
    assert [r.name for r in C.session_claims(conn, s2.id)] == ["Caio"]
    assert len(C.session_claims(conn, s.id)) == 3


def test_session_claims_scores_like_the_players_own_list(conn):
    s, ana, bia, (a1, _, _) = three(conn)
    mine = {v.claim_id: v for v in C.user_claims(conn, ana)}
    for r in C.session_claims(conn, s.id):
        if r.user_id == ana:
            assert r.claim == mine[r.claim.claim_id]


def test_a_dismissed_check_scores_nobody_on_the_admin_cards_either(admin, conn):
    """A host dismissal (decided, no host score) beats the AI's confident answer: no final score, 0 points, 'Descartada'."""
    s, (ana, _) = game(conn)
    cl = C.submit_typed_claim(conn, ana, "A Terra é plana", -4)
    C.complete_check(conn, C.claim_next_check(conn).id, result(confidence="high", score=-4))
    dismiss(admin, conn, cl.check_id)
    (row,) = C.session_claims(conn, s.id)
    assert row.claim.final is None and row.claim.points == 0
    body = card_items(admin.get("/admin/checker", headers=basic()).text)[0][1]
    assert T.CK_STATE_DISMISSED in body and T.CK_POINTS.format(n=C.points(-4, -4)) not in body


# ---------------------------------------------------------------- page
def test_the_cards_sit_below_the_board_in_the_same_grid_as_the_player_page(admin, conn):
    three(conn)
    html = page(admin)
    assert html.index('id="ckboard"') < html.index('id="ckcards"')
    assert re.search(r'<ol[^>]*class="pairs claims"', cards_block(html))
    assert len(re.findall(r'<details[^>]*class="ckcard"', cards_block(html))) == 3


def test_every_card_of_every_player_is_listed_newest_first_even_when_no_check_is_pending(admin, conn):
    s, ana, bia, (a1, b1, a2) = three(conn)
    html = page(admin)                                           # default table = pending checks only (none here: all queued)
    items = card_items(html)
    assert [u for u, _ in items] == [str(ana), str(bia), str(ana)]
    assert "Vacinas causam autismo" in items[0][1] and "O Brasil" in items[1][1] and "A Terra é plana" in items[2][1]


def test_a_card_names_its_player_and_says_palpite_not_seu_palpite(admin, conn):
    three(conn)
    items = card_items(page(admin))
    assert all(re.search(r'class="[^"]*ckwho[^"]*"[^>]*>(Ana|Bia)<', body) for _, body in items)
    assert all(T.CK_YOUR_GUESS not in body for _, body in items)
    assert f"{T.CK_LINE_GUESS}:" in items[0][1]


def test_the_card_shows_the_ai_answer_and_the_points_like_the_player_page(admin, conn):
    s, (ana, _) = game(conn)
    needs_admin(conn, ana, "A Terra é plana", -4, confidence="high", verdict="false", score=-4, explanation="Não é.")
    body = card_items(page(admin))[0][1]
    assert T.CK_VERDICTS["false"] in body and "Não é." in body and "https://example.org/a" in body
    assert T.CK_POINTS.format(n=C.points(-4, -4)) in body


def test_board_rows_are_toggle_buttons_for_the_filter(admin, conn):
    s, ana, bia, _ = three(conn)
    board = re.search(r'<div[^>]*id="ckboard".*?</table>', page(admin), re.DOTALL).group(0)
    rows = re.findall(r"<tr([^>]*)>(.*?)</tr>", board.split("<tbody>", 1)[1], re.DOTALL)
    assert len(rows) == 2
    for attrs, cells in rows:                                    # board order = rank, so match each row by its name
        uid = ana if ">Ana<" in cells else bia
        assert f'data-user="{uid}"' in attrs and 'tabindex="0"' in attrs and 'aria-selected="false"' in attrs and "ckpick" in attrs


def test_the_filter_has_a_hint_a_show_all_button_and_a_no_match_line(admin, conn):
    three(conn)
    block = cards_block(page(admin))
    assert T.CK_CARDS_HINT in block
    btn = re.search(r"<button([^>]*data-ckshowall[^>]*)>", block)
    assert btn and re.search(r"\bhidden\b", btn.group(1))
    none = re.search(rf'<p([^>]*id="ckcards-none"[^>]*)>{re.escape(T.CK_CARDS_NONE)}', block)
    assert none and re.search(r"\bhidden\b", none.group(1))


def test_no_claims_yet_shows_a_short_line(admin, conn):
    game(conn)
    html = page(admin)
    assert re.search(r'<div[^>]*id="ckcards"', html) and T.CK_CARDS_EMPTY in html


def test_an_ended_game_still_shows_the_cards(admin, conn):
    s, *_ = three(conn)
    S.end_session(conn, s.id, None)
    assert len(card_items(page(admin))) == 3


def test_the_poll_reply_and_the_score_reply_carry_the_cards(admin, conn):
    s, (ana, _) = game(conn)
    cl = needs_admin(conn, ana, "A Terra é plana", -4)
    assert 'id="ckcards"' in admin.get("/admin/checker/panel", headers=basic()).text
    tail = score(admin, conn, cl.check_id, "-5").text.split("</tbody>", 1)[1]
    oob = re.search(r'<div[^>]*id="ckcards"[^>]*>', tail)
    assert oob and 'hx-swap-oob' in oob.group(0) and tail.rstrip().endswith("</template>")
    assert "💩💩💩💩💩" in tail.split('id="ckcards"', 1)[1]                 # the host score is the final one on the card


def test_the_script_toggles_rows_filters_cards_and_survives_the_poll():
    js = (ROOT / "app" / "static" / "ckadmin.js").read_text()
    assert "tr.ckpick" in js and "aria-selected" in js and "data-user" in js.replace("dataset.user", "data-user")
    assert "sessionStorage" in js and "htmx:afterSettle" in js and "ckcards-none" in js and "data-ckshowall" in js
    assert "'Enter'" in js and "' '" in js                    # keyboard: Enter / Space toggle a focused row


def test_a_selected_row_is_visible_in_css():
    css = (ROOT / "app" / "static" / "app.css").read_text()
    assert re.search(r'tr\.ckpick\[aria-selected="true"\][^{]*\{[^}]*background', css)
    assert re.search(r"tr\.ckpick \{[^}]*cursor: pointer", css)
    # ol.pairs li sets display: grid, which beats the [hidden] attribute: without this rule the filter hides nothing
    assert re.search(r"#ckcards \[hidden\] \{[^}]*display: none !important", css)
