"""Admin management of the fallacy list (service layer): search, create, update, delete, floor, manual defaults."""
from __future__ import annotations

import threading

import pytest

from app import falacias as F
from app import fallacies_admin as FA
from app import sessions as S
from app.db import connect


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users, fallacies CASCADE")
    yield c
    c.execute("TRUNCATE game_sessions, users, fallacies CASCADE")
    c.close()


def add(conn, name, category="Relevância", enabled=True, source="manual"):
    return conn.execute("INSERT INTO fallacies (name, category, source, enabled) VALUES (%s,%s,%s,%s) RETURNING id",
                        (name, category, source, enabled)).fetchone()[0]


def many(conn, n=FA.MIN_ENABLED + 3):
    return [add(conn, f"Falácia {i}") for i in range(n)]


def code_of(fn, *a, **kw):
    with pytest.raises(FA.FallacyAdminError) as e:
        fn(*a, **kw)
    return e.value.code


# ------------------------------------------------------------------ create
def test_create_makes_an_enabled_manual_row_with_clean_text(conn):
    f = FA.create(conn, "  Ladeira   escorregadia  (Slippery Slope) ", " Relevância ", "  uma   descrição ")
    assert (f.name, f.category, f.description) == ("Ladeira escorregadia (Slippery Slope)", "Relevância", "uma descrição")
    assert (f.source, f.enabled, f.used) == ("manual", True, 0)
    assert conn.execute("SELECT name, source, enabled FROM fallacies WHERE id=%s", (f.id,)).fetchone() == (f.name, "manual", True)


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
def test_create_refuses_a_bad_name(conn, name):
    assert code_of(FA.create, conn, name, "Cat") == "invalid_name"


@pytest.mark.parametrize("category", ["", "  ", "c" * 61])
def test_create_refuses_a_bad_category(conn, category):
    assert code_of(FA.create, conn, "Nome", category) == "invalid_category"


def test_create_refuses_a_long_description_but_allows_an_empty_one(conn):
    assert code_of(FA.create, conn, "Nome", "Cat", "d" * (FA.MAX_DESCRIPTION + 1)) == "invalid_description"
    assert FA.create(conn, "Nome", "Cat").description == ""


def test_control_characters_are_dropped(conn):
    assert FA.create(conn, "No\x00me\x07", "Cat").name == "Nome"


def test_names_are_unique_ignoring_case_and_spaces(conn):
    FA.create(conn, "Falácia da falácia (Fallacy Fallacy)", "Cat")
    assert code_of(FA.create, conn, "  falácia DA falácia (fallacy fallacy) ", "Cat") == "name_taken"
    assert conn.execute("SELECT count(1) FROM fallacies").fetchone()[0] == 1


def test_concurrent_creates_of_the_same_name_make_one_row(conn, test_db_url):
    results, barrier = [], threading.Barrier(6)

    def go():
        c = connect(test_db_url)
        try:
            barrier.wait()
            results.append(FA.create(c, "Mesmo nome", "Cat").id)
        except FA.FallacyAdminError as e:
            results.append(e.code)
        finally:
            c.close()

    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(isinstance(r, int) for r in results) == 1 and results.count("name_taken") == 5
    assert conn.execute("SELECT count(1) FROM fallacies").fetchone()[0] == 1


# ------------------------------------------------------------------ search / get
def test_search_filters_by_text_accents_case_enabled_and_source(conn):
    many(conn)
    a = add(conn, "Pista Falsa (Red Herring)", "Falácias red herring", source="wikipedia")
    b = add(conn, "Apelo à emoção (Appeal to Emotion)", "Emoção", enabled=False)
    assert [r.id for r in FA.search(conn, "pista")] == [a]
    assert [r.id for r in FA.search(conn, "EMOCAO")] == [b]                       # accent/case-insensitive
    assert [r.id for r in FA.search(conn, "red herring")] == [a]
    assert [r.id for r in FA.search(conn, "emoção")] == [b]
    only_cat = add(conn, "Sem relação", "Categoria Exótica")
    assert [r.id for r in FA.search(conn, "exotica")] == [only_cat]               # the category alone is searched too
    only_desc = FA.create(conn, "Outra", "Cat", "descrição singular").id
    assert [r.id for r in FA.search(conn, "SINGULAR")] == [only_desc]
    assert [r.id for r in FA.search(conn, enabled="0")] == [b]
    assert a in [r.id for r in FA.search(conn, source="wikipedia")] and b not in [r.id for r in FA.search(conn, source="wikipedia")]
    assert len(FA.search(conn)) == FA.MIN_ENABLED + 3 + 2 + 2


def test_search_orders_by_category_then_name_and_reports_usage(conn):
    x = add(conn, "B nome", "Z cat")
    y = add(conn, "A nome", "Z cat")
    z = add(conn, "C nome", "A cat")
    assert [r.id for r in FA.search(conn)] == [z, y, x]
    _s, (p,) = _game(conn)
    S_ = F.submit_spoken(conn, p, "uma frase dita", x)
    assert S_.fallacy_id == x
    used = {r.id: r.used for r in FA.search(conn)}
    assert used == {x: 1, y: 0, z: 0}


def _game(conn):
    s = S.start_session_as_admin(conn, game_type="falacias")
    return s, [S.join_session(conn, "Ana", "10.0.0.1", game_type="falacias")[1].id]


def test_get_returns_none_for_an_unknown_id(conn):
    assert FA.get(conn, 999) is None
    i = add(conn, "Uma")
    assert FA.get(conn, i).name == "Uma"


# ------------------------------------------------------------------ update
def test_update_changes_every_field_keeping_the_id(conn):
    ids = many(conn)
    f = FA.update(conn, ids[0], "Novo nome (New Name - Nomen)", "Nova cat", "nova descrição", False)
    assert (f.id, f.name, f.category, f.description, f.enabled) == (ids[0], "Novo nome (New Name - Nomen)", "Nova cat", "nova descrição", False)
    assert conn.execute("SELECT name, enabled FROM fallacies WHERE id=%s", (ids[0],)).fetchone() == (f.name, False)


def test_update_keeps_marks_pointing_at_the_same_fallacy(conn):
    ids = many(conn)
    _s, (p,) = _game(conn)
    m = F.submit_spoken(conn, p, "frase", ids[0])
    FA.update(conn, ids[0], "Renomeada", "Cat", "", True)
    assert F.get_mark(conn, m.id).fallacy_id == ids[0]


def test_update_may_keep_its_own_name_but_not_take_anothers(conn):
    a, b, *_ = many(conn)
    name_a = conn.execute("SELECT name FROM fallacies WHERE id=%s", (a,)).fetchone()[0]
    assert FA.update(conn, a, name_a, "Outra", "", True).category == "Outra"
    assert code_of(FA.update, conn, b, name_a.upper(), "Cat", "", True) == "name_taken"


def test_update_validates_like_create_and_reports_not_found(conn):
    a, *_ = many(conn)
    assert code_of(FA.update, conn, a, "", "Cat", "", True) == "invalid_name"
    assert code_of(FA.update, conn, a, "Ok", "", "", True) == "invalid_category"
    assert code_of(FA.update, conn, 99999, "Ok", "Cat", "", True) == "not_found"


def test_update_a_wikipedia_row_is_allowed_and_keeps_its_source_and_url(conn):
    i = conn.execute("INSERT INTO fallacies (name, category, source, wikipedia_url) VALUES ('W','C','wikipedia','https://pt.wikipedia.org/wiki/W') "
                     "RETURNING id").fetchone()[0]
    many(conn)
    f = FA.update(conn, i, "W2", "C", "", True)
    assert (f.source, f.wikipedia_url) == ("wikipedia", "https://pt.wikipedia.org/wiki/W")


# ------------------------------------------------------------------ floor
def test_cannot_disable_below_the_floor_but_can_at_the_floor(conn):
    ids = [add(conn, f"F{i}") for i in range(FA.MIN_ENABLED + 1)]
    FA.update(conn, ids[0], "F0", "Relevância", "", False)                          # still MIN_ENABLED left
    assert code_of(FA.update, conn, ids[1], "F1", "Relevância", "", False) == "too_few_enabled"
    assert conn.execute("SELECT enabled FROM fallacies WHERE id=%s", (ids[1],)).fetchone() == (True,)


def test_editing_a_disabled_row_never_trips_the_floor(conn):
    ids = [add(conn, f"F{i}") for i in range(2)] + [add(conn, "off", enabled=False)]            # already BELOW the floor
    assert FA.update(conn, ids[-1], "off2", "Relevância", "", False).name == "off2"
    assert FA.update(conn, ids[-1], "off2", "Relevância", "", True).enabled is True          # enabling is never refused


def test_the_floor_cannot_be_broken_by_parallel_disables(conn, test_db_url):
    ids = [add(conn, f"F{i}") for i in range(FA.MIN_ENABLED + 3)]
    barrier, out = threading.Barrier(len(ids)), []

    def go(i):
        c = connect(test_db_url)
        try:
            barrier.wait()
            FA.update(c, i, f"F{ids.index(i)}", "Relevância", "", False)
            out.append("ok")
        except FA.FallacyAdminError as e:
            out.append(e.code)
        finally:
            c.close()

    ts = [threading.Thread(target=go, args=(i,)) for i in ids]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out.count("ok") == 3 and out.count("too_few_enabled") == FA.MIN_ENABLED
    assert conn.execute("SELECT count(1) FROM fallacies WHERE enabled").fetchone()[0] == FA.MIN_ENABLED


# ------------------------------------------------------------------ delete
def test_delete_removes_an_unused_row(conn):
    ids = many(conn)
    FA.delete(conn, ids[0])
    assert FA.get(conn, ids[0]) is None


def test_delete_refuses_a_fallacy_that_players_used_and_says_to_disable_it(conn):
    ids = many(conn)
    _s, (p,) = _game(conn)
    F.submit_spoken(conn, p, "frase", ids[0])
    assert code_of(FA.delete, conn, ids[0]) == "in_use"
    assert FA.get(conn, ids[0]) is not None


def test_delete_respects_the_floor_and_not_found(conn):
    ids = [add(conn, f"F{i}") for i in range(FA.MIN_ENABLED)]
    assert code_of(FA.delete, conn, ids[0]) == "too_few_enabled"
    assert code_of(FA.delete, conn, 99999) == "not_found"
    off = add(conn, "off", enabled=False)
    FA.delete(conn, off)                                                          # a disabled one is not counted by the floor
    assert FA.get(conn, off) is None


# ------------------------------------------------------------------ the two requested defaults
def test_ensure_manual_adds_the_two_requested_fallacies_once(conn):
    assert FA.ensure_manual(conn) == 2
    names = {n for (n,) in conn.execute("SELECT name FROM fallacies WHERE source='manual' AND enabled")}
    assert names == {"Deus das lacunas (God of the gaps)", "Ônus da prova invertido (Shift the burden of proof)"}
    assert FA.ensure_manual(conn) == 0
    assert conn.execute("SELECT count(1) FROM fallacies").fetchone()[0] == 2


def test_ensure_manual_never_touches_a_row_the_admin_already_edited(conn):
    FA.ensure_manual(conn)
    i = conn.execute("SELECT id FROM fallacies WHERE name LIKE 'Deus das lacunas%'").fetchone()[0]
    many(conn)
    FA.update(conn, i, "Deus das lacunas (Outro)", "Minha cat", "minha descrição", False)
    FA.ensure_manual(conn)                                       # the name differs now: the default is added again, the edited row stays
    row = FA.get(conn, i)
    assert (row.name, row.category, row.description, row.enabled) == ("Deus das lacunas (Outro)", "Minha cat", "minha descrição", False)


def test_writes_need_an_autocommit_connection(test_db_url):
    import psycopg
    c = psycopg.connect(test_db_url)
    try:
        with pytest.raises(Exception, match="autocommit"):
            FA.create(c, "Nome", "Cat")
    finally:
        c.close()


def test_cli_fallacies_manual_adds_the_defaults_once_and_reports(conn, test_db_url, monkeypatch, capsys):
    from corpus import cli
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    assert cli.main(["fallacies-manual"]) == 0
    assert "'added': 2" in capsys.readouterr().out
    assert cli.main(["fallacies-manual"]) == 0
    assert "'added': 0" in capsys.readouterr().out
    assert conn.execute("SELECT count(1) FROM fallacies").fetchone()[0] == 2
