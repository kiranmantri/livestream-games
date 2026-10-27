# ruff: noqa: F811, RUF059
"""/admin/checker: the table is searchable (q) and sortable (sort, dir), on the server so it works without JS and survives the 3 s poll."""
from __future__ import annotations

import re

import pytest

from app import checker as C
from app import texts as T
from tests.test_checker_admin_web import (  # noqa: F401
    _env,
    admin,
    app,
    basic,
    conn,
    game,
    needs_admin,
    result,
    score,
)


def page(admin, qs=""):
    """The page up to the players' card grid: the search and the pending toggle filter the checks table only (the cards list every claim)."""
    return admin.get("/admin/checker" + qs, headers=basic()).text.split('id="ckcards"', 1)[0]


def order(html, *texts):
    return [t for _, t in sorted((html.index(t), t) for t in texts)]


@pytest.fixture
def three(conn):
    s, (ana, bia) = game(conn, ("Ana", "Bia"))
    a = needs_admin(conn, bia, "Zebras são azuis", 3, score=-2, explanation="Zebras são listradas.")
    b = needs_admin(conn, ana, "Ação do Sol na Terra", -4, score=4, verdict="true", explanation="O Sol aquece a Terra.")
    c = C.submit_typed_claim(conn, ana, "Mamute sem nota da IA", 0)               # queued: no AI score
    return s, a, b, c


# ---------------------------------------------------------------- search
def test_there_is_a_search_box_outside_the_polled_panel(admin, three):
    html = page(admin)
    box = re.search(r'<input[^>]*type="search"[^>]*>', html).group(0)
    assert 'name="q"' in box and "aria-label" in box
    assert 'hx-trigger="input changed delay:300ms' in re.search(r"<form[^>]*ck-search[^>]*>", html).group(0)
    assert html.index('type="search"') < html.index('id="ckpanel"')


def test_search_ignores_case_and_accents_and_looks_at_claim_player_explanation_and_verdict(admin, three):
    assert order(page(admin, "?q=acao"), "Ação do Sol") == ["Ação do Sol"] and "Zebras são azuis" not in page(admin, "?q=ACAO")
    assert "Zebras são azuis" in page(admin, "?q=bia") and "Ação do Sol" not in page(admin, "?q=bia")            # player
    assert "Ação do Sol" in page(admin, "?q=aquece") and "Zebras" not in page(admin, "?q=aquece")                # explanation
    assert "Ação do Sol" in page(admin, "?q=verdadeira") and "Zebras" not in page(admin, "?q=verdadeira")       # verdict label
    assert "Mamute" in page(admin, "?q=na+fila") and "Zebras" not in page(admin, "?q=na+fila")                 # state label


def test_no_match_says_so_and_empty_search_shows_everything(admin, three):
    html = page(admin, "?q=xyzxyz")
    assert T.CK_NO_MATCH in html and "Zebras" not in html
    assert all(t in page(admin, "?q=") for t in ("Zebras", "Ação do Sol", "Mamute"))


def test_the_query_is_kept_in_the_box_escaped_and_in_the_poll_url(admin, three):
    html = page(admin, '?q=%22%3E%3Cscript%3Ex%3C%2Fscript%3E')
    assert "<script>x" not in html and "&lt;script&gt;" in html
    html = page(admin, "?q=Sol&sort=claim&dir=desc&all=1")
    assert 'value="Sol"' in html
    poll = re.search(r'<div[^>]*id="ckpanel"[^>]*>', html).group(0)
    assert "q=Sol" in poll and "sort=claim" in poll and "dir=desc" in poll and "all=1" in poll


def test_the_search_also_applies_to_the_panel_fragment(admin, three):
    r = admin.get("/admin/checker/panel?q=acao", headers=basic())
    table = r.text.split('id="ckcards"', 1)[0]                            # the cards below list every claim
    assert r.status_code == 200 and "Ação do Sol" in table and "Zebras" not in table and "<html" not in r.text


def test_the_live_search_returns_the_panel_even_when_the_signature_matches(admin, three):
    html = page(admin)
    sig = re.search(r"sig=([0-9a-f]+)", html).group(1)
    assert admin.get(f"/admin/checker/panel?sig={sig}", headers=basic()).status_code == 204
    assert admin.get("/admin/checker/panel?q=zebra", headers=basic()).status_code == 200            # no sig: always answers


# ---------------------------------------------------------------- sort
def test_headers_are_sort_links_except_actions_and_the_current_one_is_marked(admin, three):
    html = page(admin, "?sort=claim&dir=asc")
    head = re.search(r"<thead>(.*?)</thead>", html, re.DOTALL).group(1)
    ths = re.findall(r"<th([^>]*)>(.*?)</th>", head, re.DOTALL)
    assert len(ths) == 5
    for attrs, inner in ths[:-1]:
        assert "<a" in inner and "sort=" in inner
    assert "<a" not in ths[-1][1]
    claim = ths[1]
    assert 'aria-sort="ascending"' in claim[0] and "dir=desc" in claim[1] and "▲" in claim[1]
    assert all("aria-sort" not in a for a, _ in ths[:1] + ths[2:])
    assert 'aria-sort="descending"' in re.findall(r"<th([^>]*)>", re.search(r"<thead>(.*?)</thead>", page(admin, "?sort=claim&dir=desc"), re.DOTALL).group(1))[1]


def test_sort_links_keep_the_search_and_the_filter(admin, three):
    html = page(admin, "?q=a&all=1")
    link = re.search(r'<th[^>]*>\s*<a[^>]*href="([^"]+)"', html).group(1)
    assert "q=a" in link and "all=1" in link


def test_sort_by_claim_both_ways(admin, three):
    texts = ("Zebras são azuis", "Ação do Sol", "Mamute")
    assert order(page(admin, "?sort=claim&dir=asc"), *texts) == ["Ação do Sol", "Mamute", "Zebras são azuis"]
    assert order(page(admin, "?sort=claim&dir=desc"), *texts) == ["Zebras são azuis", "Mamute", "Ação do Sol"]


def test_sort_by_player_and_by_the_score_that_counts(admin, three, conn):
    s, a, b, c = three
    texts = ("Zebras são azuis", "Ação do Sol", "Mamute")
    assert order(page(admin, "?sort=player&dir=asc"), *texts)[0] in ("Ação do Sol", "Mamute")          # Ana before Bia
    assert order(page(admin, "?sort=player&dir=desc"), *texts)[0] == "Zebras são azuis"
    assert order(page(admin, "?sort=points&dir=desc"), *texts)[:2] == ["Ação do Sol", "Zebras são azuis"]          # +4, -2, then none
    score(admin, conn, a.check_id, "5")                                                                              # the host's score replaces the AI's
    assert order(page(admin, "?all=1&sort=points&dir=desc"), *texts)[:2] == ["Zebras são azuis", "Ação do Sol"]


@pytest.mark.parametrize("dir_", ["asc", "desc"])
def test_checks_without_a_value_always_go_last(admin, three, dir_):
    texts = ("Zebras são azuis", "Ação do Sol", "Mamute")
    assert order(page(admin, f"?sort=points&dir={dir_}"), *texts)[-1] == "Mamute"


def test_a_bad_sort_or_direction_falls_back_to_arrival_order(admin, three):
    texts = ("Zebras são azuis", "Ação do Sol", "Mamute")
    default = order(page(admin), *texts)
    assert default == list(texts)
    assert order(page(admin, "?sort=hack&dir=sideways"), *texts) == default
    assert order(page(admin, "?sort=claim&dir=sideways"), *texts) == ["Ação do Sol", "Mamute", "Zebras são azuis"]     # bad dir = asc


def test_scoring_a_row_keeps_working_while_sorted(admin, three, conn):
    s, a, b, c = three
    assert score(admin, conn, c.check_id, "2").status_code == 200 and C.get_check(conn, c.check_id).admin_score == 2
