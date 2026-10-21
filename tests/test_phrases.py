"""Phrase management service (CRUD) and the score learned from finished games."""
from __future__ import annotations

import json
import random
import threading

import psycopg
import pytest

from app import cards as C
from app import marks as M
from app import phrases as P
from app import scoring as G
from app import sessions as S
from app.db import connect
from tests.test_cards import load_phrases


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


def row(conn, text):
    return conn.execute("SELECT id, score, prior_score, dealt, marked, enabled, source FROM phrases WHERE text=%s",
                        (text,)).fetchone()


# ------------------------------------------------------------------ pure
def test_normalize_text():
    assert P.normalize_text("  Café   COM\tLeite ") == "café com leite"
    assert P.normalize_text("cafe\u0301 com leite") == "café com leite"      # NFC: same key as precomposed


@pytest.mark.parametrize("bad", ["", "   ", " ".join(["w"] * 11), "a" * 101, "a" * 51 + " " + "b" * 50,
                                 "bom dia pessoal aqui", "tudo bem com você", "ctrl\x00 char aqui"])
def test_validate_text_rejects(bad):
    with pytest.raises(P.InvalidPhrase):
        P.validate_text(bad)


@pytest.mark.parametrize("good", ["três palavras aqui", "uma frase de cinco", "Frase  Com Maiúscula", "x\ny z",
                                  "oi", "dois palavras", " ".join(["w"] * 10)])
def test_validate_text_accepts(good):
    assert P.validate_text(good) == P.normalize_text(good)


@pytest.mark.parametrize("raw,val", [("5", 5.0), ("5,5", 5.5), (" 99 ", 99.0), (0, 0.0), ("1.23456", 1.235)])
def test_parse_score_ok(raw, val):
    assert P.parse_score(raw) == val


@pytest.mark.parametrize("raw", ["", "abc", "-0.1", "99.01", "100", "nan", "inf", None])
def test_parse_score_rejects(raw):
    with pytest.raises(P.InvalidScore):
        P.parse_score(raw)


def test_blend_score_values():
    assert P.blend_score(5.0, 0, 0) == 5.0                                    # no data: prior
    assert P.blend_score(5.0, 20, 20) == pytest.approx((20 * 5 + 20 * 11) / 40)   # always marked -> halfway to 11
    assert P.blend_score(5.0, 20, 0) == pytest.approx((20 * 5 + 20 * 1) / 40)     # never marked -> halfway to 1
    assert P.blend_score(5.0, 2000, 2000) == pytest.approx(10.94, abs=0.01)   # lots of data dominates the prior
    assert P.blend_score(3.0, 10, 5) > P.blend_score(3.0, 10, 4) > P.blend_score(3.0, 10, 0)   # monotone in marks


# ------------------------------------------------------------------ create
def test_create_manual_phrase_has_score_99_and_is_enabled(conn):
    r = P.create(conn, "  Minha FRASE nova ")
    assert (r.text, r.source, r.score, r.prior_score, r.enabled, r.dealt, r.marked) == (
        "minha frase nova", "manual", 99.0, 99.0, True, 0, 0)
    assert r.frozen


def test_create_is_durable_from_another_connection(conn, test_db_url):
    P.create(conn, "minha frase nova")
    with psycopg.connect(test_db_url) as other:
        assert other.execute("SELECT score FROM phrases WHERE text='minha frase nova'").fetchone() == (99.0,)


def test_create_duplicate_and_invalid(conn):
    P.create(conn, "minha frase nova")
    with pytest.raises(P.DuplicatePhrase):
        P.create(conn, "MINHA frase   nova")                                   # same after normalisation
    with pytest.raises(P.InvalidPhrase):
        P.create(conn, " ".join(["w"] * 11))
    assert P.create(conn, "só").source == "manual"                               # manual phrases may have 1 word
    assert conn.execute("SELECT count(*) FROM phrases").fetchone()[0] == 2


def test_create_requires_autocommit(test_db_url):
    with psycopg.connect(test_db_url) as bad, pytest.raises(RuntimeError, match="autocommit"):
        P.create(bad, "minha frase nova")


def test_trigger_defaults_prior_to_score(conn):
    conn.execute("INSERT INTO phrases (text, source, score) VALUES ('uma frase qualquer', 'chat', 4.5)")
    assert row(conn, "uma frase qualquer")[2] == 4.5


# ------------------------------------------------------------------ update
def test_update_text_score_enabled(conn):
    load_phrases(conn, n=40)
    p = P.create(conn, "minha frase nova")
    u = P.update(conn, p.id, text="Outra Frase Nova", score="7,5", enabled=False)
    assert (u.text, u.score, u.prior_score, u.enabled) == ("outra frase nova", 7.5, 7.5, False)
    assert not u.frozen


def test_update_score_keeps_play_statistics(conn):
    load_phrases(conn, n=40)
    p = P.create(conn, "minha frase nova")
    conn.execute("UPDATE phrases SET dealt=10, marked=4 WHERE id=%s", (p.id,))
    u = P.update(conn, p.id, score=6)
    assert (u.dealt, u.marked, u.prior_score) == (10, 4, 6.0)


def test_update_noop_and_not_found(conn):
    p = P.create(conn, "minha frase nova")
    assert P.update(conn, p.id) == p
    assert P.update(conn, p.id, text="minha frase nova", score=99) == p
    with pytest.raises(P.NotFound):
        P.update(conn, 999999, score=1)


def test_update_rejects_bad_values_and_changes_nothing(conn):
    p = P.create(conn, "minha frase nova")
    for kw in ({"text": " ".join(["w"] * 11)}, {"text": "bom dia a todos"}, {"score": "abc"}, {"score": 100}, {"score": -1}):
        with pytest.raises(P.PhraseError):
            P.update(conn, p.id, **kw)
    assert P.get(conn, p.id) == p


def test_update_text_to_existing_is_duplicate(conn):
    a, b = P.create(conn, "primeira frase aqui"), P.create(conn, "segunda frase aqui")
    with pytest.raises(P.DuplicatePhrase):
        P.update(conn, b.id, text="primeira frase aqui")
    assert P.get(conn, b.id).text == "segunda frase aqui" and P.get(conn, a.id).text == "primeira frase aqui"


# ------------------------------------------------------------------ enabled floor
def test_cannot_disable_or_delete_below_minimum(conn):
    load_phrases(conn, n=P.MIN_ENABLED)                                        # exactly 24 enabled
    pid = conn.execute("SELECT id FROM phrases WHERE enabled LIMIT 1").fetchone()[0]
    with pytest.raises(P.TooFewEnabled):
        P.update(conn, pid, enabled=False)
    with pytest.raises(P.TooFewEnabled):
        P.delete(conn, pid)
    assert P.enabled_count(conn) == P.MIN_ENABLED
    P.create(conn, "minha frase nova")                                         # 25 -> now one may go
    P.update(conn, pid, enabled=False)
    assert P.enabled_count(conn) == P.MIN_ENABLED


def test_deleting_or_editing_a_disabled_phrase_is_always_allowed(conn):
    load_phrases(conn, n=P.MIN_ENABLED, disabled=3)
    dis = conn.execute("SELECT id FROM phrases WHERE NOT enabled LIMIT 1").fetchone()[0]
    P.update(conn, dis, score=3)
    P.delete(conn, dis)
    assert P.get(conn, dis) is None


def test_enabled_floor_holds_under_concurrency(conn, test_db_url):
    load_phrases(conn, n=P.MIN_ENABLED + 1)                                    # 25 enabled: exactly ONE may go
    ids = [r[0] for r in conn.execute("SELECT id FROM phrases ORDER BY id LIMIT 4")]
    out, barrier = [], threading.Barrier(4)

    def go(pid):
        with connect(test_db_url) as c:
            barrier.wait()
            try:
                P.delete(c, pid)
                out.append("ok")
            except P.TooFewEnabled:
                out.append("refused")

    ts = [threading.Thread(target=go, args=(i,)) for i in ids]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert sorted(out) == ["ok", "refused", "refused", "refused"]
    assert P.enabled_count(conn) == P.MIN_ENABLED


# ------------------------------------------------------------------ delete
def test_delete_removes_phrase_but_issued_cards_keep_their_text(conn):
    load_phrases(conn, n=40)
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    card = C.assign_card(conn, s.id, p.id, random.Random(1))
    victim = card.cells[0]
    P.delete(conn, victim["phrase_id"])
    assert P.get(conn, victim["phrase_id"]) is None
    assert C.get_card(conn, s.id, p.id).cells[0]["text"] == victim["text"]
    assert M.toggle_mark(conn, p.id, 0) is True                                # still markable
    res = G.finish_session(conn, s.id)                                         # finishing with a deleted phrase works
    assert res is not None and res.winner.marked == 1


def test_delete_not_found(conn):
    with pytest.raises(P.NotFound):
        P.delete(conn, 123456)


# ------------------------------------------------------------------ search / paging
def test_search_filters_sorts_and_paginates(conn):
    load_phrases(conn, n=30, disabled=5, manual=3)
    rows, total = P.search(conn)
    assert total == 38 and len(rows) == 38
    assert [r.score for r in rows] == sorted((r.score for r in rows), reverse=True)
    assert P.search(conn, source="manual")[1] == 3
    assert P.search(conn, enabled="no")[1] == 5 and P.search(conn, enabled="yes")[1] == 33
    assert P.search(conn, q="MANUAL")[1] == 3
    assert P.search(conn, q="numero 1")[1] >= 1
    assert P.search(conn, source="bogus", enabled="bogus", sort="bogus")[1] == 38          # unknown values ignored
    page1, _ = P.search(conn, per_page=10, page=1)
    page4, _ = P.search(conn, per_page=10, page=4)
    page5, _ = P.search(conn, per_page=10, page=5)
    assert len(page1) == 10 and len(page4) == 8 and page5 == []
    assert not {r.id for r in page1} & {r.id for r in page4}
    assert [r.text for r in P.search(conn, sort="text")[0]] == sorted(r.text for r in P.search(conn)[0])


def test_search_treats_like_wildcards_literally(conn):
    P.create(conn, "tem 100% de certeza")
    P.create(conn, "tem cem por cento")
    assert [r.text for r in P.search(conn, q="100%")[0]] == ["tem 100% de certeza"]
    assert P.search(conn, q="%")[1] == 1 and P.search(conn, q="_")[1] == 0
    assert P.search(conn, q="x' OR '1'='1")[1] == 0                             # no injection


# ------------------------------------------------------------------ learning from games
def play(conn, n_players=3, marks_per_player=None, seed=1):
    """Run a session: players get cards, mark given cell lists, admin finishes. Returns (session, cards)."""
    s, p0, _ = S.start_session(conn, "P0", "10.0.0.1")
    players = [p0] + [S.join_session(conn, f"P{i}", f"10.0.0.{i + 1}")[1] for i in range(1, n_players)]
    rng = random.Random(seed)
    cards = [C.assign_card(conn, s.id, p.id, rng) for p in players]
    for p, cells in zip(players, marks_per_player or [[]] * n_players, strict=False):
        for i in cells:
            M.toggle_mark(conn, p.id, i)
    assert G.finish_session(conn, s.id) is not None
    return s, cards


def test_finish_counts_dealt_and_marked_per_phrase(conn):
    load_phrases(conn, n=40)
    _, cards = play(conn, 3, [[0, 1, 2], [0, 1], []])
    exp_dealt, exp_marked = {}, {}
    marks = {0: {0, 1, 2}, 1: {0, 1}, 2: set()}
    for k, card in enumerate(cards):
        for i, cell in enumerate(card.cells):
            if "phrase_id" in cell:
                exp_dealt[cell["phrase_id"]] = exp_dealt.get(cell["phrase_id"], 0) + 1
                exp_marked[cell["phrase_id"]] = exp_marked.get(cell["phrase_id"], 0) + (i in marks[k])
    got = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, dealt, marked FROM phrases WHERE dealt > 0")}
    assert got == {pid: (exp_dealt[pid], exp_marked[pid]) for pid in exp_dealt}
    assert sum(m for _, m in got.values()) == 5 and sum(d for d, _ in got.values()) == 72


def test_finish_relearns_score_with_the_documented_formula(conn):
    load_phrases(conn, n=40)
    before = {r[0]: r[1] for r in conn.execute("SELECT id, score FROM phrases")}
    play(conn, 3, [list(range(12)) + list(range(13, 25))] * 3)               # everybody marks everything
    held = conn.execute("SELECT id, prior_score, dealt, marked, score FROM phrases WHERE dealt > 0").fetchall()
    assert held
    for pid, prior, dealt, marked, score in held:
        assert marked == dealt and score == pytest.approx(P.blend_score(prior, dealt, marked), abs=1e-3)
        assert score > before[pid] or before[pid] >= 11                          # marked 100% -> pulled up
    untouched = conn.execute("SELECT id, score FROM phrases WHERE dealt = 0").fetchall()
    assert untouched                                                             # phrases nobody held keep their score
    assert all(score == before[pid] for pid, score in untouched)


def test_never_marked_phrases_drift_down(conn):
    load_phrases(conn, n=40)
    before = {r[0]: r[1] for r in conn.execute("SELECT id, score FROM phrases")}
    play(conn, 3, [[], [], []])
    held = conn.execute("SELECT id, score FROM phrases WHERE dealt > 0").fetchall()
    assert held and all(score < before[pid] for pid, score in held if before[pid] > 1.0)


def test_phrase_frozen_at_99_collects_stats_but_keeps_score(conn):
    load_phrases(conn, n=30)
    p = P.create(conn, "minha frase nova")                                       # 99 -> on (almost) every card
    play(conn, 3, [[]] * 3)
    r = P.get(conn, p.id)
    assert r.score == 99.0 and r.dealt == 3 and r.marked == 0                    # counted, not moved
    P.update(conn, p.id, score=6)                                                # hand edit unfreezes it
    assert P.get(conn, p.id).score == 6.0
    play(conn, 3, [[]] * 3, seed=2)
    r = P.get(conn, p.id)
    assert r.dealt > 3 and r.score == pytest.approx(P.blend_score(6.0, r.dealt, 0), abs=1e-3) and r.score < 6.0


def test_stats_are_applied_exactly_once_per_session(conn):
    load_phrases(conn, n=40)
    s, _ = play(conn, 2, [[0], []])
    snap = conn.execute("SELECT id, dealt, marked, score FROM phrases ORDER BY id").fetchall()
    assert G.finish_session(conn, s.id) is None                                  # second finish: refused...
    assert conn.execute("SELECT id, dealt, marked, score FROM phrases ORDER BY id").fetchall() == snap   # ...no double count


def test_stats_accumulate_across_sessions(conn):
    load_phrases(conn, n=26)                                                     # small pool: phrases repeat across games
    play(conn, 2, [[], []], seed=1)
    play(conn, 2, [[], []], seed=2)
    assert conn.execute("SELECT max(dealt) FROM phrases").fetchone()[0] >= 3
    assert conn.execute("SELECT sum(dealt) FROM phrases").fetchone()[0] == 4 * 24


def test_session_without_cards_changes_nothing(conn):
    load_phrases(conn, n=40)
    s, _, _ = S.start_session(conn, "Ana", "10.0.0.1")                           # nobody got a card
    assert G.finish_session(conn, s.id) is not None
    assert conn.execute("SELECT count(*) FROM phrases WHERE dealt > 0").fetchone()[0] == 0


def test_stats_and_session_end_are_one_transaction(conn, monkeypatch):
    load_phrases(conn, n=40)
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    C.assign_card(conn, s.id, p.id)

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(P, "apply_session_stats", boom)
    with pytest.raises(RuntimeError, match="boom"):
        G.finish_session(conn, s.id)
    assert S.get_active_session(conn) is not None                                # not ended: rolled back together
    assert conn.execute("SELECT count(*) FROM phrases WHERE dealt > 0").fetchone()[0] == 0


@pytest.mark.parametrize("prior,dealt,marked", [(2.0, 1, 0), (2.0, 1, 1), (5.5, 7, 3), (11.3, 50, 49), (0.0, 3, 3), (98.0, 4, 2)])
def test_sql_formula_equals_python_formula(conn, prior, dealt, marked):
    """Seed phrase X with dealt-1 / marked-(1 if marked) history, then one finished game in which X is
    held and (if marked > 0) marked once: the DB must land on blend_score(prior, dealt, marked)."""
    load_phrases(conn, n=30)
    pid = conn.execute("SELECT min(id) FROM phrases").fetchone()[0]
    conn.execute("UPDATE phrases SET prior_score=%s, score=%s, dealt=%s, marked=%s WHERE id=%s",
                 (prior, prior, dealt - 1, marked - (1 if marked else 0), pid))
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    others = [r[0] for r in conn.execute("SELECT id FROM phrases WHERE id <> %s ORDER BY id LIMIT 23", (pid,))]
    cells = [{"phrase_id": pid, "text": "x y z"}] + [{"phrase_id": i, "text": "a b c"} for i in others[:11]]
    cells += [{"type": "image"}] + [{"phrase_id": i, "text": "a b c"} for i in others[11:]]
    conn.execute("INSERT INTO bingo_cards (session_id, user_id, cells) VALUES (%s,%s,%s)",
                 (s.id, p.id, json.dumps(cells)))
    if marked:
        card_id = conn.execute("SELECT id FROM bingo_cards").fetchone()[0]
        conn.execute("INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s, 0)", (card_id,))
    G.finish_session(conn, s.id)
    score, d, m = conn.execute("SELECT score, dealt, marked FROM phrases WHERE id=%s", (pid,)).fetchone()
    assert (d, m) == (dealt, marked)
    expected = prior if prior >= P.MANUAL_SCORE else P.blend_score(prior, dealt, marked)
    assert score == pytest.approx(expected, abs=2e-3)


# ------------------------------------------------------------------ DB constraints
@pytest.mark.parametrize("sql", [
    "UPDATE phrases SET marked = dealt + 1",
    "UPDATE phrases SET dealt = -1",
    "UPDATE phrases SET score = 100",
    "UPDATE phrases SET score = -1",
    "UPDATE phrases SET prior_score = 100",
])
def test_db_constraints(conn, sql):
    load_phrases(conn, n=30)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(sql)


# ------------------------------------------------------------------ extracted phrases need >= 3 words
def test_manual_edit_allows_one_to_ten_words_for_every_source(conn):
    conn.execute("INSERT INTO phrases (text, source, score) VALUES ('frase vinda do chat', 'chat', 5)")
    pid = conn.execute("SELECT id FROM phrases").fetchone()[0]
    assert P.update(conn, pid, text="só duas").text == "só duas"                      # extracted phrase shortened to 2
    assert P.update(conn, pid, text="só").text == "só"                                # ... and to 1
    ten = " ".join(f"p{i}" for i in range(10))
    assert P.update(conn, pid, text=ten).text == ten
    assert conn.execute("SELECT source FROM phrases WHERE id=%s", (pid,)).fetchone()[0] == "chat"   # provenance kept
    for bad in (" ".join(["w"] * 11), "", "   ", "bom dia pessoal"):                   # still bounded / no greetings
        with pytest.raises(P.InvalidPhrase):
            P.update(conn, pid, text=bad)
    assert conn.execute("SELECT text FROM phrases WHERE id=%s", (pid,)).fetchone()[0] == ten
    m = P.create(conn, "manual")
    assert P.update(conn, m.id, text="dois mesmo").text == "dois mesmo"


# ------------------------------------------------------------------ bulk delete
def test_delete_many_deletes_all_selected_and_ignores_unknown(conn):
    load_phrases(conn, n=40)
    ids = [r[0] for r in conn.execute("SELECT id FROM phrases ORDER BY id LIMIT 5")]
    assert P.delete_many(conn, [*ids, ids[0], 987654]) == 5                      # duplicate + unknown id ignored
    assert conn.execute("SELECT count(*) FROM phrases").fetchone()[0] == 35
    assert P.delete_many(conn, []) == 0


def test_delete_many_is_all_or_nothing_below_the_floor(conn):
    load_phrases(conn, n=26)                                                     # floor is 24 enabled
    ids = [r[0] for r in conn.execute("SELECT id FROM phrases ORDER BY id LIMIT 3")]
    with pytest.raises(P.TooFewEnabled):
        P.delete_many(conn, ids)
    assert conn.execute("SELECT count(*) FROM phrases").fetchone()[0] == 26      # nothing was deleted
    assert P.delete_many(conn, ids[:2]) == 2                                     # exactly down to 24 is fine


def test_delete_many_of_disabled_phrases_never_hits_the_floor(conn):
    load_phrases(conn, n=24, disabled=6)
    ids = [r[0] for r in conn.execute("SELECT id FROM phrases WHERE NOT enabled")]
    assert P.delete_many(conn, ids) == 6


def test_delete_many_requires_autocommit(test_db_url):
    with psycopg.connect(test_db_url) as bad, pytest.raises(RuntimeError, match="autocommit"):
        P.delete_many(bad, [1])


# ------------------------------------------------------------------ bulk activate / deactivate
def test_set_enabled_many_toggles_and_counts_only_real_changes(conn):
    load_phrases(conn, n=30, disabled=6)
    on = [r[0] for r in conn.execute("SELECT id FROM phrases WHERE enabled ORDER BY id LIMIT 3")]
    off = [r[0] for r in conn.execute("SELECT id FROM phrases WHERE NOT enabled ORDER BY id LIMIT 4")]
    before = conn.execute("SELECT id, score, prior_score, dealt, marked, text FROM phrases ORDER BY id").fetchall()
    assert P.set_enabled_many(conn, [*off, *on, off[0], 987654], True) == 4          # 3 were on already, 1 unknown, 1 dup
    assert conn.execute("SELECT count(*) FROM phrases WHERE NOT enabled").fetchone()[0] == 2
    assert P.set_enabled_many(conn, [*on, *off], False) == 7
    assert conn.execute("SELECT count(*) FROM phrases WHERE enabled").fetchone()[0] == 30 + 4 - 7
    after = conn.execute("SELECT id, score, prior_score, dealt, marked, text FROM phrases ORDER BY id").fetchall()
    assert after == before                                                            # only `enabled` ever changes
    assert P.set_enabled_many(conn, [], True) == 0


def test_set_enabled_many_deactivate_is_all_or_nothing_at_the_floor(conn):
    load_phrases(conn, n=26)                                                          # floor is 24 enabled
    ids = [r[0] for r in conn.execute("SELECT id FROM phrases ORDER BY id LIMIT 3")]
    with pytest.raises(P.TooFewEnabled):
        P.set_enabled_many(conn, ids, False)
    assert conn.execute("SELECT count(*) FROM phrases WHERE enabled").fetchone()[0] == 26    # nothing changed
    assert P.set_enabled_many(conn, ids[:2], False) == 2                              # exactly down to 24 is fine
    assert P.set_enabled_many(conn, ids[:2], False) == 0                              # already off: no change, no error


def test_set_enabled_many_activating_is_never_blocked_by_the_floor(conn):
    load_phrases(conn, n=24, disabled=10)
    ids = [r[0] for r in conn.execute("SELECT id FROM phrases WHERE NOT enabled")]
    assert P.set_enabled_many(conn, ids, True) == 10
    low = [r[0] for r in conn.execute("SELECT id FROM phrases ORDER BY id LIMIT 11")]
    with pytest.raises(P.TooFewEnabled):                                              # 34 - 11 = 23 < 24
        P.set_enabled_many(conn, low, False)


def test_activating_a_mixed_selection_at_the_floor_is_not_refused(conn):
    load_phrases(conn, n=24, disabled=3)                                              # exactly 24 enabled
    everything = [r[0] for r in conn.execute("SELECT id FROM phrases")]             # includes all 24 active ones
    assert P.set_enabled_many(conn, everything, True) == 3                            # no TooFewEnabled, 3 switched on
    assert conn.execute("SELECT count(*) FROM phrases WHERE enabled").fetchone()[0] == 27


def test_set_enabled_many_requires_autocommit(test_db_url):
    with psycopg.connect(test_db_url) as bad, pytest.raises(RuntimeError, match="autocommit"):
        P.set_enabled_many(bad, [1], True)
