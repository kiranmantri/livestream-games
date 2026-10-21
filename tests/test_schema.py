"""Schema constraint tests (task 4): the DB itself enforces the domain rules."""
from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from psycopg import errors

from tests.conftest import add_user, new_session

CELLS = json.dumps([{"phrase_id": i, "text": f"frase numero {i}"} if i != 12 else {"type": "image"}
                    for i in range(25)])


def add_card(conn, sid, uid, cells=CELLS):
    cid = uuid.uuid4()
    conn.execute("INSERT INTO bingo_cards (id, session_id, user_id, cells) VALUES (%s,%s,%s,%s)",
                 (cid, sid, uid, cells))
    conn.commit()
    return cid


# ---- migrations
def test_migrations_are_idempotent(conn):
    from app.db import migrate
    assert migrate(conn) == []  # already applied by the fixture
    names = [r[0] for r in conn.execute("SELECT name FROM schema_migrations")]
    assert names == ["0001_init.sql", "0002_admin_started_sessions.sql",
                     "0003_phrase_stats.sql", "0004_phrase_words_1_10.sql",
                     "0005_phrase_words_any_source.sql", "0006_grid4_drop_videos.sql",
                     "0007_sessions_per_game.sql"]


# ---- sessions
def test_session_and_first_user_created_in_one_transaction(conn):
    sid, uid = new_session(conn)
    row = conn.execute("SELECT created_by, status FROM game_sessions WHERE id=%s", (sid,)).fetchone()
    assert row == (uid, "active")


def test_session_may_have_no_creator_when_started_by_admin(conn):
    conn.execute("INSERT INTO game_sessions (created_by) VALUES (NULL)")
    conn.commit()
    assert conn.execute("SELECT status, created_by FROM game_sessions").fetchone() == ("active", None)


def test_admin_started_session_still_unique_while_active(conn):
    conn.execute("INSERT INTO game_sessions (created_by) VALUES (NULL)")
    conn.commit()
    with pytest.raises(errors.UniqueViolation):
        conn.execute("INSERT INTO game_sessions (created_by) VALUES (NULL)")
    conn.rollback()
    with pytest.raises(errors.UniqueViolation):
        new_session(conn, "ana")                    # a player cannot start one either


def test_only_one_active_session(conn):
    new_session(conn, "ana")
    with pytest.raises(errors.UniqueViolation):
        new_session(conn, "bia")


def test_new_session_allowed_after_previous_ended(conn):
    new_session(conn, "ana", end=True)
    new_session(conn, "bia")  # must not raise
    assert conn.execute("SELECT count(*) FROM game_sessions").fetchone()[0] == 2


def test_session_must_reference_existing_user(conn):
    conn.execute("INSERT INTO game_sessions (created_by) VALUES (%s)", (uuid.uuid4(),))
    with pytest.raises(errors.ForeignKeyViolation):
        conn.commit()  # deferred FK is checked at commit


def test_ended_session_requires_end_time_and_active_forbids_it(conn):
    sid, _uid = new_session(conn)
    with pytest.raises(errors.CheckViolation):
        conn.execute("UPDATE game_sessions SET status='ended' WHERE id=%s", (sid,))
    conn.rollback()
    with pytest.raises(errors.CheckViolation):
        conn.execute("UPDATE game_sessions SET ended_at=now() WHERE id=%s", (sid,))
    conn.rollback()


def test_winner_recorded_when_session_ends(conn):
    sid, uid = new_session(conn)
    conn.execute("UPDATE game_sessions SET status='ended', ended_at=now(), winner_user_id=%s "
                 "WHERE id=%s", (uid, sid))
    conn.commit()
    assert conn.execute("SELECT winner_user_id FROM game_sessions WHERE id=%s", (sid,)).fetchone()[0] == uid


def test_invalid_status_and_game_type_rejected(conn):
    uid = uuid.uuid4()
    with pytest.raises(errors.CheckViolation):
        conn.execute("INSERT INTO game_sessions (created_by, status) VALUES (%s, 'paused')", (uid,))
    conn.rollback()
    with pytest.raises(errors.CheckViolation):
        conn.execute("INSERT INTO game_sessions (created_by, game_type) VALUES (%s, 'chess')", (uid,))
    conn.rollback()


# ---- users
def test_name_ci_is_lower_trimmed_copy(conn):
    _sid, uid = new_session(conn, "  Ana  ")
    assert conn.execute("SELECT name_ci FROM users WHERE id=%s", (uid,)).fetchone()[0] == "ana"


@pytest.mark.parametrize("dup", ["ana", "ANA", "Ana", "  aNa "])
def test_name_unique_case_insensitive_within_session(conn, dup):
    sid, _ = new_session(conn, "Ana")
    with pytest.raises(errors.UniqueViolation):
        add_user(conn, sid, dup)


def test_same_name_allowed_in_different_sessions(conn):
    new_session(conn, "Ana", end=True)
    new_session(conn, "Ana")  # new session, same name: fine


def test_name_max_20_chars(conn):
    sid, _ = new_session(conn, "x" * 20)  # exactly 20 ok
    with pytest.raises(errors.StringDataRightTruncation):
        add_user(conn, sid, "y" * 21)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_name_rejected(conn, blank):
    sid, _ = new_session(conn, "ana")
    with pytest.raises(errors.CheckViolation):
        add_user(conn, sid, blank)


def test_token_hash_unique(conn):
    sid, _ = new_session(conn, "ana")
    conn.execute("INSERT INTO users (session_id, name, ip, token_hash) VALUES (%s,'b','10.0.0.9','T')", (sid,))
    conn.commit()
    with pytest.raises(errors.UniqueViolation):
        conn.execute("INSERT INTO users (session_id, name, ip, token_hash) VALUES (%s,'c','10.0.0.9','T')", (sid,))


def test_ip_is_validated_as_inet(conn):
    sid, _ = new_session(conn, "ana")
    with pytest.raises(errors.InvalidTextRepresentation):
        add_user(conn, sid, "bia", ip="not-an-ip")


# ---- phrases
@pytest.mark.parametrize("source,text,ok", [
    ("manual", "sozinha", True), ("manual", "um dois", True), ("manual", "", False), ("manual", "   ", False),
    ("manual", " ".join(["w"] * 10), True), ("manual", " ".join(["w"] * 11), False),
    # extracted phrases may be SHORTENED by the admin to 1-2 words (the 3-10 rule lives in corpus/extract.py)
    ("transcript", "um dois", True), ("chat", "sozinha", True), ("transcript+chat", "um dois", True),
    ("transcript", "hoje em dia", True), ("chat", " ".join(["w"] * 10), True), ("chat", "", False),
    ("transcript", " ".join(["w"] * 11), False), ("transcript+chat", " ".join(["w"] * 11), False),
])
def test_phrase_word_count_per_source(conn, source, text, ok):
    q = "INSERT INTO phrases (text, source) VALUES (%s, %s)"
    if ok:
        conn.execute(q, (text, source))
        conn.commit()
    else:
        with pytest.raises(errors.CheckViolation):
            conn.execute(q, (text, source))
        conn.rollback()


@pytest.mark.parametrize("src", ["transcript", "chat", "transcript+chat", "manual"])
def test_phrase_valid_sources(conn, src):
    conn.execute("INSERT INTO phrases (text, source) VALUES ('frase de teste', %s)", (src,))
    conn.commit()


def test_phrase_invalid_source_and_duplicate_rejected(conn):
    with pytest.raises(errors.CheckViolation):
        conn.execute("INSERT INTO phrases (text, source) VALUES ('frase de teste', 'youtube')")
    conn.rollback()
    conn.execute("INSERT INTO phrases (text, source) VALUES ('frase de teste', 'manual')")
    conn.commit()
    with pytest.raises(errors.UniqueViolation):
        conn.execute("INSERT INTO phrases (text, source) VALUES ('frase de teste', 'chat')")


# ---- cards
def test_one_card_per_user_per_session(conn):
    sid, uid = new_session(conn)
    add_card(conn, sid, uid)
    with pytest.raises(errors.UniqueViolation):
        add_card(conn, sid, uid)


def test_card_must_have_16_or_25_cells(conn):
    sid, uid = new_session(conn)
    for bad in (json.dumps([1] * 15), json.dumps([1] * 17), json.dumps([1] * 24), json.dumps([1] * 26),
                json.dumps({"a": 1})):
        with pytest.raises(errors.CheckViolation):
            add_card(conn, sid, uid, bad)
        conn.rollback()
    add_card(conn, sid, uid, json.dumps([{"phrase_id": i, "text": f"frase {i}"} for i in range(16)]))   # 4x4 is fine


def test_card_user_must_belong_to_session(conn):
    _sid_old, uid_old = new_session(conn, "ana", end=True)
    sid_new, _ = new_session(conn, "bia")
    with pytest.raises(errors.ForeignKeyViolation):
        add_card(conn, sid_new, uid_old)  # ana is a player of the OLD session


def test_card_is_immutable(conn):
    sid, uid = new_session(conn)
    cid = add_card(conn, sid, uid)
    with pytest.raises(psycopg.Error, match="immutable"):
        conn.execute("UPDATE bingo_cards SET cells=%s WHERE id=%s", (CELLS, cid))
    conn.rollback()
    with pytest.raises(psycopg.Error, match="immutable"):
        conn.execute("UPDATE bingo_cards SET user_id=%s WHERE id=%s", (uuid.uuid4(), cid))


def test_card_jsonb_roundtrip_keeps_centre_image(conn):
    sid, uid = new_session(conn)
    cid = add_card(conn, sid, uid)
    cells = conn.execute("SELECT cells FROM bingo_cards WHERE id=%s", (cid,)).fetchone()[0]
    assert cells[12] == {"type": "image"} and cells[0]["text"] == "frase numero 0"


# ---- marks
def test_marks_reject_avatar_out_of_range_and_duplicates(conn):
    sid, uid = new_session(conn)
    cid = add_card(conn, sid, uid)                                    # 25 cells: index 12 is the avatar
    for bad in (12, -1, 25):
        with pytest.raises(errors.CheckViolation):
            conn.execute("INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s,%s)", (cid, bad))
        conn.rollback()
    conn.execute("INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s,0)", (cid,))
    conn.commit()
    with pytest.raises(errors.UniqueViolation):
        conn.execute("INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s,0)", (cid,))


def test_marks_on_a_4x4_card_allow_12_and_reject_cells_beyond_the_card(conn):
    sid, uid = new_session(conn)
    cid = add_card(conn, sid, uid, json.dumps([{"phrase_id": i, "text": f"frase {i}"} for i in range(16)]))
    conn.execute("INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s,12)", (cid,))
    conn.commit()
    for bad in (16, 24):
        with pytest.raises(errors.CheckViolation):
            conn.execute("INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s,%s)", (cid, bad))
        conn.rollback()


def test_grid_size_defaults_to_4_and_only_4_or_5_are_allowed(conn):
    sid, _ = new_session(conn)
    assert conn.execute("SELECT grid_size FROM game_sessions WHERE id=%s", (sid,)).fetchone() == (4,)
    for bad in (3, 6, 0):
        with pytest.raises(errors.CheckViolation):
            conn.execute("UPDATE game_sessions SET grid_size=%s WHERE id=%s", (bad, sid))
        conn.rollback()


def test_ingested_videos_table_is_gone(conn):
    assert conn.execute("SELECT to_regclass('ingested_videos')").fetchone() == (None,)


def test_bingo_is_24_marks(conn):
    sid, uid = new_session(conn)
    cid = add_card(conn, sid, uid)
    for i in (i for i in range(25) if i != 12):
        conn.execute("INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s,%s)", (cid, i))
    conn.commit()
    assert conn.execute("SELECT count(*) FROM bingo_marks WHERE card_id=%s", (cid,)).fetchone()[0] == 24


# ---- cascade
def test_deleting_session_cascades_to_users_cards_marks(conn):
    sid, uid = new_session(conn)
    cid = add_card(conn, sid, uid)
    conn.execute("INSERT INTO bingo_marks (card_id, cell_index) VALUES (%s,0)", (cid,))
    conn.commit()
    conn.execute("DELETE FROM game_sessions WHERE id=%s", (sid,))
    conn.commit()
    for t in ("users", "bingo_cards", "bingo_marks"):
        assert conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0
