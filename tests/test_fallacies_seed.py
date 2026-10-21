"""Task 1a: parser + SQL seed for the Portuguese Wikipedia fallacy list (offline, on a SAVED copy of the page)."""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest

from deploy import make_fallacies_seed as M

FIXTURE = Path(__file__).parent / "fixtures" / "lista_de_falacias_70610117.json"


@pytest.fixture(scope="module")
def page():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["parse"]


@pytest.fixture(scope="module")
def items(page):
    return M.parse_fallacies(page["text"])


def by_name(items, start):
    hit = [f for f in items if f.name.startswith(start)]
    assert len(hit) == 1, (start, [f.name for f in hit])
    return hit[0]


def wrap(h2, body):
    return f'<div><h2 id="x">{h2}</h2></div>{body}'


# ---------------------------------------------------------------- the saved page
def test_fixture_is_the_pinned_revision(page):
    assert page["revid"] == M.OLDID == 70610117


def test_counts_on_the_pinned_page(items):
    assert len(items) == 97
    assert sum(f.enabled for f in items) == 20
    assert {f.kind for f in items} == {"formal", "informal"}
    assert len({M.fold(f.name) for f in items}) == len(items)            # names unique (table key)


def test_only_the_top_20_are_enabled(items):
    from app.falacias import TOP_FALLACIES
    assert len(TOP_FALLACIES) == 20 and len(set(TOP_FALLACIES)) == 20
    assert {f.name for f in items if f.enabled} == set(TOP_FALLACIES)
    assert {f.name for f in items} >= set(TOP_FALLACIES)           # every curated name exists on the pinned page (no typos)
    assert not by_name(items, "Argumento ad logicam").enabled      # formal ones and the rest stay in the table, disabled
    assert not by_name(items, "Pooh-pooh").enabled
    assert by_name(items, "Falácia do espantalho").enabled


def test_category_is_the_nearest_heading(items):
    assert by_name(items, "Argumentum ad temperantiam").category == "Falácias informais"      # list before the h3s
    assert by_name(items, "Falácia do espantalho").category == "Falácias red herring"      # h4 wins over h3
    cats = {f.category for f in items}
    assert {"Premissa imprópria", "Generalizações falsas", "Causa questionável", "Falácias de relevância",
            "Falácias red herring", "Falácias silogísticas formais"} <= cats
    assert not cats & {"Ver também", "Referências", "Bibliografia", "Ligações externas"}


def test_description_has_no_citation_marks_or_markup(items):
    for f in items:
        assert f.name and f.description
        assert "[" not in f.description and "&#" not in f.description and "<" not in f.description, f.name
        assert f.name == f.name.strip() and "  " not in f.name


def test_article_link_is_absolute_and_only_for_real_articles(items):
    f = by_name(items, "Falácia do espantalho")
    assert f.url and f.url.startswith("https://pt.wikipedia.org/wiki/") and "#" not in f.url
    assert all(x.url is None or x.url.startswith("https://pt.wikipedia.org/wiki/") for x in items)
    assert sum(x.url is None for x in items) == 33                       # nested/unlinked entries exist


def test_nested_items_know_their_parent(items):
    parent = by_name(items, "Falácia do escocês de verdade").parent
    assert parent and parent.startswith("Dicto secundum quid")
    assert by_name(items, "Falácia do espantalho").parent is None


def test_hyphen_separator_entries_are_kept_and_group_labels_dropped(items):
    names = [f.name for f in items]
    assert any(n.startswith("Pergunta complexa") for n in names)
    assert any(n.startswith("Nut-picking") for n in names)
    assert "Whataboutism" not in names                                   # a group label without description


def test_must_have_report_names_exactly_what_the_page_lacks(items):
    assert M.missing_must_haves(items) == ["deus das lacunas", "onus da prova invertido"]


# ---------------------------------------------------------------- parser rules on synthetic html
def test_only_the_two_fallacy_sections_are_read():
    html = (wrap("Falácias informais", '<ul><li><a href="/wiki/A">Alfa</a> – primeira.<sup>[1]</sup></li></ul>')
            + wrap("Ver também", '<ul><li><a href="/wiki/B">Beta</a> – fora.</li></ul>'))
    got = M.parse_fallacies(html)
    assert [(f.name, f.description, f.kind) for f in got] == [("Alfa", "primeira.", "informal")]


def test_red_links_and_fragments_do_not_become_urls():
    html = wrap("Falácias formais",
                '<ul><li><a href="/w/index.php?title=Z&action=edit&redlink=1" class="new">Zeta</a> – x.</li>'
                '<li><a href="/wiki/Eta#Secao">Eta</a> – y.</li></ul>')
    z, e = M.parse_fallacies(html)
    assert z.url is None and e.url == "https://pt.wikipedia.org/wiki/Eta"


def test_each_red_link_marker_alone_is_enough_to_drop_the_url():
    html = wrap("Falácias formais",
                '<ul><li><a href="/w/index.php?title=Y&redlink=1">Ypsilon</a> – x.</li>'
                '<li><a href="/wiki/Teta" class="new">Teta</a> – y.</li></ul>')
    assert [f.url for f in M.parse_fallacies(html)] == [None, None]


def test_duplicate_names_keep_the_first_ignoring_case_and_accents():
    html = wrap("Falácias informais", "<ul><li>Falácia – um.</li><li>FALACIA – dois.</li></ul>")
    assert [f.description for f in M.parse_fallacies(html)] == ["um."]


def test_parent_text_stops_at_the_nested_list():
    html = wrap("Falácias informais",
                "<ul><li>Pai – descrição do pai.<ul><li>Filho – descrição do filho.</li></ul></li></ul>")
    pai, filho = M.parse_fallacies(html)
    assert pai.description == "descrição do pai." and pai.parent is None
    assert filho.description == "descrição do filho." and filho.parent == "Pai"


# ---------------------------------------------------------------- SQL
def test_sql_escapes_quotes_and_is_idempotent_on_conflict():
    f = M.Fallacy("D'Alembert", "diz 'x'; DROP TABLE y;--", "Cat", "informal", None, None)
    sql = M.to_sql([f])
    assert "'D''Alembert'" in sql and "'diz ''x''; DROP TABLE y;--'" in sql and ", NULL, 'wikipedia', false)" in sql   # not in the top 20
    assert ", NULL, 'wikipedia', true)" in M.to_sql([M.Fallacy("Ad hominem", "d", "Cat", "informal", None, None)])
    assert sql.rstrip().endswith("ON CONFLICT (name) DO NOTHING;")


PLANNED = """CREATE TEMP TABLE fallacies (id smallserial PRIMARY KEY, name text NOT NULL UNIQUE, category text NOT NULL,
  description text NOT NULL, wikipedia_url text, source text NOT NULL CHECK (source IN ('wikipedia','manual')),
  enabled boolean NOT NULL DEFAULT true)"""


def test_seed_applies_twice_without_duplicates(items, test_db_url):
    """Against a TEMP table with the layout planned for migration 0008 (the real table arrives in Task 1b)."""
    sql = M.to_sql(items)
    with psycopg.connect(test_db_url, autocommit=True) as c:
        c.execute(PLANNED)
        c.execute(sql)
        c.execute(sql)
        n, on = c.execute("SELECT count(*), count(*) FILTER (WHERE enabled) FROM fallacies").fetchone()
        assert (n, on) == (97, 20)
        assert c.execute("SELECT count(*) FROM fallacies WHERE source <> 'wikipedia'").fetchone()[0] == 0


# ---------------------------------------------------------------- CLI
def test_main_writes_the_seed_from_a_saved_file(tmp_path, capsys):
    out = tmp_path / "s.sql"
    assert M.main(["--from-file", str(FIXTURE), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("-- Fallacy seed from pt.wikipedia.org")
    assert "97 fallacies" in capsys.readouterr().out


def test_main_refuses_another_revision_and_a_page_that_changed_shape(tmp_path, capsys):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    wrong = tmp_path / "w.json"
    wrong.write_text(json.dumps({"parse": {**data["parse"], "revid": 1}}), encoding="utf-8")
    assert M.main(["--from-file", str(wrong), "--out", str(tmp_path / "o.sql")]) == 1
    tiny = tmp_path / "t.json"
    tiny.write_text(json.dumps({"parse": {**data["parse"], "text": "<p>vazio</p>"}}), encoding="utf-8")
    assert M.main(["--from-file", str(tiny), "--out", str(tmp_path / "o.sql")]) == 1
    assert not (tmp_path / "o.sql").exists() and "error" in capsys.readouterr().err
