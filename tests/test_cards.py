"""Task 6: bingo card generation + assignment."""
from __future__ import annotations

import random
import threading
from collections import Counter

import psycopg
import pytest
from psycopg import errors

from app import cards as C
from app import sessions as S
from app.db import connect


@pytest.fixture
def conn(test_db_url):
    """Service-layer connection: autocommit, like production (overrides the conftest fixture)."""
    c = connect(test_db_url)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


def test_card_is_durable_and_visible_from_another_connection(conn, test_db_url):
    """Regression: the card must really be COMMITTED (the first demo showed 0 rows in psql)."""
    load_phrases(conn)
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    card = C.assign_card(conn, s.id, p.id)
    with psycopg.connect(test_db_url) as other:
        row = other.execute("SELECT id, jsonb_array_length(cells) FROM bingo_cards").fetchone()
    assert row == (card.id, 16)


def test_assign_card_refuses_non_autocommit_connection(conn, test_db_url):
    import uuid
    with psycopg.connect(test_db_url) as bad, pytest.raises(RuntimeError, match="autocommit"):
        C.assign_card(bad, uuid.uuid4(), uuid.uuid4())


def load_phrases(conn, n=60, disabled=0, manual=0):
    """n auto phrases with ascending score, plus optional disabled / manual ones."""
    for i in range(n):
        conn.execute("INSERT INTO phrases (text, source, score) VALUES (%s,'transcript',%s)",
                     (f"frase auto numero {i}", 1 + i * 0.2))
    for i in range(disabled):
        conn.execute("INSERT INTO phrases (text, source, score, enabled) VALUES (%s,'chat',9,false)",
                     (f"frase desligada numero {i}",))
    for i in range(manual):
        conn.execute("INSERT INTO phrases (text, source, score) VALUES (%s,'manual',99)",
                     (f"frase manual numero {i}",))
    conn.commit()


def P(i, score=5.0, source="transcript"):
    return C.Phrase(i, f"frase numero {i}", source, score)


POOL = [P(i) for i in range(60)]


# ---------------------------------------------------------------- pure generation
def test_center_image_url_is_exactly_the_specified_one():
    assert C.CENTER_IMAGE_URL == (
        "https://yt3.googleusercontent.com/LSxmMTuZ7eATKJKq10HbVV29fuHJej6zse6odK-C-95Fg85xBXDowLysZzqlOFFzq6EoTRkcjQ="
        "s160-c-k-c0x00ffffff-no-rj")


def test_cells_shape_4x4_has_only_phrases_and_no_free_cell():
    cells = C.generate_cells(POOL, random.Random(1))                  # default grid: 4x4
    assert len(cells) == 16 and all("phrase_id" in c and "text" in c for c in cells)
    assert len({c["phrase_id"] for c in cells}) == 16 and len({c["text"] for c in cells}) == 16   # no repeats


def test_cells_shape_5x5_legacy_has_the_avatar_in_the_centre():
    cells = C.generate_cells(POOL, random.Random(1), grid=5)
    assert len(cells) == 25 and cells[12] == {"type": "image"}
    phrase_cells = [c for i, c in enumerate(cells) if i != 12]
    assert all("phrase_id" in c and "text" in c for c in phrase_cells)
    assert len({c["phrase_id"] for c in phrase_cells}) == 24


def test_seeded_generation_is_deterministic_and_different_seeds_differ():
    a = C.generate_cells(POOL, random.Random(7))
    assert a == C.generate_cells(POOL, random.Random(7))
    assert a != C.generate_cells(POOL, random.Random(8))


def test_exactly_enough_phrases_is_enough_one_less_is_not():
    assert len(C.generate_cells(POOL[:16], random.Random(1))) == 16
    with pytest.raises(C.NotEnoughPhrases):
        C.generate_cells(POOL[:15], random.Random(1))
    assert len(C.generate_cells(POOL[:24], random.Random(1), grid=5)) == 25
    with pytest.raises(C.NotEnoughPhrases):
        C.generate_cells(POOL[:23], random.Random(1), grid=5)
    with pytest.raises(C.NotEnoughPhrases):
        C.generate_cells([], random.Random(1))


def test_with_exactly_16_phrases_all_are_used_but_positions_vary():
    pool = POOL[:16]
    a, b = (C.generate_cells(pool, random.Random(s)) for s in (1, 2))
    assert {c["phrase_id"] for c in a} == {p.id for p in pool}
    assert a != b


def test_positions_are_uniform_not_biased_by_weight():
    """Selection is weighted but where a phrase lands must be uniform (no 'strong phrases first')."""
    pool = [P(i, score=1.0) for i in range(16)]
    pool[0] = P(0, score=100.0)                       # very heavy phrase
    rng = random.Random(3)
    positions = Counter()
    for _ in range(4800):
        cells = C.generate_cells(pool, rng)
        positions[next(i for i, c in enumerate(cells) if c.get("phrase_id") == 0)] += 1
    assert len(positions) == 16
    expected = 4800 / 16
    assert all(0.7 * expected < n < 1.3 * expected for n in positions.values()), positions


def test_weighting_prefers_high_score_phrases():
    pool = [P(i, score=1.0) for i in range(50)] + [P(100 + i, score=12.0) for i in range(50)]
    rng, hi = random.Random(5), 0
    for _ in range(300):
        hi += sum(1 for c in C.generate_cells(pool, rng) if c.get("phrase_id", 0) >= 100)
    share = hi / (300 * 16)
    assert share > 0.6, share         # weights 13 vs 2 -> clearly favoured, but...
    assert share < 0.95, share        # ...low-score phrases still appear (variety)


def test_weight_is_one_plus_score_and_manual_is_not_special_cased():
    auto = [P(i, score=sc) for i, sc in enumerate([2, 4, 6, 8, 10])]
    man = C.Phrase(99, "frase manual numero 1", "manual", 99.0)
    w = C.phrase_weights(auto + [man])
    assert w == [3.0, 5.0, 7.0, 9.0, 11.0, 100.0]
    assert C.phrase_weights([C.Phrase(1, "a b c", "chat", -5.0)]) == [1.0]     # negative never below 1


def test_negative_scores_do_not_break_weights():
    assert C.phrase_weights([C.Phrase(1, "a b c", "chat", -3.0)]) == [1.0]


def test_grid_is_row_major_for_both_sizes():
    g = C.Card(None, None, None, C.generate_cells(POOL, random.Random(1)), None).grid()
    assert len(g) == 4 and all(len(r) == 4 for r in g)
    legacy = C.Card(None, None, None, C.generate_cells(POOL, random.Random(1), grid=5), None)
    g5 = legacy.grid()
    assert len(g5) == 5 and all(len(r) == 5 for r in g5) and g5[2][2] == {"type": "image"}
    assert legacy.max_marks == 24 and C.Card(None, None, None, C.generate_cells(POOL), None).max_marks == 16


# ---------------------------------------------------------------- persistence
def test_assign_card_persists_and_is_idempotent(conn):
    load_phrases(conn)
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    assert C.get_card(conn, s.id, p.id) is None
    c1 = C.assign_card(conn, s.id, p.id, random.Random(1))
    c2 = C.assign_card(conn, s.id, p.id, random.Random(2))           # different rng: must NOT re-roll
    assert c1.id == c2.id and c1.cells == c2.cells
    assert conn.execute("SELECT count(*) FROM bingo_cards").fetchone()[0] == 1
    assert len(c1.cells) == 16 and all("phrase_id" in c for c in c1.cells)      # new sessions: 4x4, no avatar


def test_sessions_that_already_existed_keep_5x5_cards(conn):
    """grid_size is per session: a session created before the 4x4 change (5) still deals 5x5 cards."""
    load_phrases(conn)
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    assert conn.execute("SELECT grid_size FROM game_sessions WHERE id=%s", (s.id,)).fetchone() == (4,)
    conn.execute("UPDATE game_sessions SET grid_size = 5 WHERE id=%s", (s.id,))
    card = C.assign_card(conn, s.id, p.id)
    assert len(card.cells) == 25 and card.cells[12] == {"type": "image"} and card.size == 5


def test_card_survives_corpus_changes(conn):
    """Cells are a snapshot: later edits/disabling of phrases never change an issued card."""
    load_phrases(conn)
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    before = C.assign_card(conn, s.id, p.id, random.Random(1))
    conn.execute("UPDATE phrases SET enabled=false, text=text||' alterada'")
    conn.commit()
    assert C.assign_card(conn, s.id, p.id).cells == before.cells


def test_each_user_gets_own_card_in_same_session(conn):
    load_phrases(conn)
    s, ana, _ = S.start_session(conn, "Ana", "10.0.0.1")
    _, bia, _ = S.join_session(conn, "Bia", "10.0.0.2")
    rng = random.Random(11)
    ca, cb = C.assign_card(conn, s.id, ana.id, rng), C.assign_card(conn, s.id, bia.id, rng)
    assert ca.id != cb.id and ca.cells != cb.cells
    assert conn.execute("SELECT count(*) FROM bingo_cards").fetchone()[0] == 2


def test_only_enabled_phrases_are_dealt(conn):
    load_phrases(conn, n=30, disabled=40)
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    for seed in range(20):                                           # many draws, never a disabled one
        cells = C.generate_cells(C.enabled_phrases(conn), random.Random(seed))
        assert not any("desligada" in c.get("text", "") for c in cells)
    card = C.assign_card(conn, s.id, p.id)
    assert not any("desligada" in c.get("text", "") for c in card.cells)


def test_manual_phrases_can_be_dealt(conn):
    load_phrases(conn, n=10, manual=20)                              # 30 enabled; manual are 2/3 of the pool
    seen = set()
    for seed in range(30):
        seen |= {c["text"] for c in C.generate_cells(C.enabled_phrases(conn), random.Random(seed))
                 if "text" in c}
    assert any("manual" in t for t in seen)


def test_not_enough_enabled_phrases_stores_nothing(conn):
    load_phrases(conn, n=10, disabled=50)
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    with pytest.raises(C.NotEnoughPhrases):
        C.assign_card(conn, s.id, p.id)
    assert conn.execute("SELECT count(*) FROM bingo_cards").fetchone()[0] == 0
    for i in range(20):                                              # corpus fixed later -> retry works
        conn.execute("INSERT INTO phrases (text, source, score) VALUES (%s,'manual',99)",
                     (f"extra frase numero {i}",))
    conn.commit()
    assert len(C.assign_card(conn, s.id, p.id).cells) == 16


def test_user_not_in_session_is_rejected(conn):
    import uuid
    load_phrases(conn)
    s, _, _ = S.start_session(conn, "Ana", "10.0.0.1")
    with pytest.raises(errors.ForeignKeyViolation):
        C.assign_card(conn, s.id, uuid.uuid4())


def test_issued_card_cannot_be_modified_in_db(conn):
    load_phrases(conn)
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    card = C.assign_card(conn, s.id, p.id)
    with pytest.raises(psycopg.Error, match="immutable"):
        conn.execute("UPDATE bingo_cards SET cells=%s WHERE id=%s", ("[]", card.id))


def test_concurrent_assignment_yields_one_identical_card(conn, test_db_url):
    load_phrases(conn)
    s, p, _ = S.start_session(conn, "Ana", "10.0.0.1")
    barrier, out = threading.Barrier(8), []

    def worker(seed):
        with connect(test_db_url) as c:
            barrier.wait()
            out.append(C.assign_card(c, s.id, p.id, random.Random(seed)))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert len(out) == 8 and len({c.id for c in out}) == 1 and len({str(c.cells) for c in out}) == 1
    assert conn.execute("SELECT count(*) FROM bingo_cards").fetchone()[0] == 1
