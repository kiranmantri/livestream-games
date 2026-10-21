"""L2: typed (spoken-word) pairs — kind 'spoken' (migration 0009) — and the per-session video source."""
from __future__ import annotations

import threading

import pytest
from psycopg import errors

from app import falacias as F
from app import feed
from app import sessions as S
from app.db import connect

REAL = "abcdefghijk"                    # 11-char, YouTube-like ids
OTHER = "lmnopqrstuv"


@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE game_sessions, users, fallacies, replay_events CASCADE")
    yield c
    c.execute("TRUNCATE game_sessions, users, fallacies, replay_events CASCADE")
    c.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "50")
    for k in ("FALACIAS_SPEED", "FALACIAS_START_AT", "FALACIAS_VIDEO_ID"):
        monkeypatch.delenv(k, raising=False)
    feed._last_tick.clear()


def fallacy(conn, name="Ad hominem", enabled=True):
    return conn.execute("INSERT INTO fallacies (name, category, source, enabled) VALUES (%s,'C','manual',%s) RETURNING id",
                        (name, enabled)).fetchone()[0]


def game(conn, players=("Ana",), **source):
    s = S.start_session_as_admin(conn, game_type="falacias", **source)
    ids = [S.join_session(conn, n, f"10.0.0.{i + 1}", game_type="falacias")[1].id for i, n in enumerate(players)]
    return s, ids


def replay_rows(conn, video_id):
    for kind, off, text in (("transcript", 1000, "fala um"), ("chat", 2000, "chat um")):
        conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES (%s,%s,%s,%s,%s)",
                     (video_id, kind, off, "@a" if kind == "chat" else None, text))


def spoken(conn, uid, text="Deus existe porque a Bíblia diz", fid=None):
    return F.submit_spoken(conn, uid, text, fid)


@pytest.fixture
def w(conn, monkeypatch):
    monkeypatch.setattr(F, "RATE_LIMIT_S", 0)
    s, (ana, bia) = game(conn, ("Ana", "Bia"))
    return {"c": conn, "s": s, "ana": ana, "bia": bia, "f": fallacy(conn), "f2": fallacy(conn, "Espantalho")}


# ================================================================ migration 0009
def test_db_accepts_spoken_events_and_marks_and_enforces_its_shape(w):
    c, s, u, f = w["c"], w["s"], w["ana"], w["f"]
    eid = c.execute("INSERT INTO stream_events (session_id, kind, source_key, text) VALUES (%s,'spoken','k1','falei') RETURNING id",
                    (s.id,)).fetchone()[0]
    ins = ("INSERT INTO fallacy_marks (session_id, user_id, kind, start_event_id, start_offset, end_event_id, end_offset, quote, "
           "fallacy_id, latency_ms, counts) VALUES (%s,%s,'spoken',%s,%s,%s,%s,%s,%s,0,true)")
    c.execute(ins, (s.id, u, eid, 0, eid, 5, "falei", f))
    with pytest.raises(errors.CheckViolation):                                     # a spoken pair is its own single event, from offset 0
        c.execute(ins, (s.id, u, eid, 1, eid, 5, "alei", f))
    eid2 = c.execute("INSERT INTO stream_events (session_id, kind, source_key, text) VALUES (%s,'spoken','k2','x') RETURNING id",
                     (s.id,)).fetchone()[0]
    with pytest.raises(errors.CheckViolation):
        c.execute(ins, (s.id, u, eid, 0, eid2, 5, "falei x", f))
    with pytest.raises(errors.CheckViolation):                                     # at most 300 chars for a spoken quote
        c.execute(ins, (s.id, u, eid2, 0, eid2, 5, "x" * 301, f))
    with pytest.raises(errors.CheckViolation):
        c.execute("INSERT INTO stream_events (session_id, kind, source_key, text) VALUES (%s,'other','k3','x')", (s.id,))


def test_selected_quotes_may_still_be_500_chars(w):
    c, s, u, f = w["c"], w["s"], w["ana"], w["f"]
    eid = c.execute("INSERT INTO stream_events (session_id, kind, source_key, text) VALUES (%s,'chat','k1','x') RETURNING id",
                    (s.id,)).fetchone()[0]
    c.execute("INSERT INTO fallacy_marks (session_id, user_id, kind, start_event_id, start_offset, end_event_id, end_offset, quote, "
              "fallacy_id, latency_ms, counts) VALUES (%s,%s,'chat',%s,0,%s,1,%s,%s,0,true)", (s.id, u, eid, eid, "x" * 500, f))


def test_session_source_columns_and_constraints(conn):
    s = S.start_session_as_admin(conn, "falacias", video_id=REAL, source_mode="live")
    assert (s.video_id, s.source_mode) == (REAL, "live")
    assert S.get_active_session(conn, "falacias").source_mode == "live"
    assert S.start_session_as_admin(conn, "bingo").video_id is None                 # Bingo keeps NULLs
    bad = [("falacias", "x", None), ("falacias", None, "live"), ("falacias", "x", "stream"), ("falacias", "bad id!", "live")]
    for gt, vid, mode in bad:
        with pytest.raises(errors.CheckViolation):
            conn.execute("INSERT INTO game_sessions (game_type, status, ended_at, video_id, source_mode) "
                         "VALUES (%s,'ended', now(), %s, %s)", (gt, vid, mode))
    with pytest.raises(errors.CheckViolation):                                        # Bingo has no video source
        conn.execute("INSERT INTO game_sessions (game_type, status, ended_at, video_id, source_mode) "
                     "VALUES ('bingo','ended', now(), %s, 'live')", (REAL,))


# ================================================================ normalisation
@pytest.mark.parametrize("raw,expected", [
    ("  Deus   existe\n\tmesmo  ", "Deus existe mesmo"),
    ("a\x07b\x00c", "abc"),
    ("e\u0301", "é"),                                          # NFC like player names
    ("x" * 300, "x" * 300),
])
def test_normalize_spoken(raw, expected):
    assert F.normalize_spoken(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "\x07\x00", "\n\t", None])
def test_normalize_spoken_rejects_empty(raw):
    with pytest.raises(F.BadSelection):
        F.normalize_spoken(raw)


def test_normalize_spoken_rejects_over_300_after_cleaning():
    with pytest.raises(F.SpokenTooLong):
        F.normalize_spoken("x" * 301)
    assert F.normalize_spoken("x" * 300 + "\x07\x07  ") == "x" * 300                  # measured after normalising
    assert F.SpokenTooLong.code == "spoken_too_long"


# ================================================================ submit_spoken
def test_submit_spoken_creates_its_own_event_and_a_pending_mark(w):
    c = w["c"]
    m = spoken(c, w["ana"], "  Deus  existe  ", w["f"])
    assert (m.kind, m.status, m.quote, m.counts, m.latency_ms) == ("spoken", "pending", "Deus existe", True, 60_000)
    ev = c.execute("SELECT kind, author, text FROM stream_events WHERE id = %s", (m.start_event_id,)).fetchone()
    assert ev == ("spoken", "Ana", "Deus existe") and m.start_event_id == m.end_event_id and m.start_offset == 0
    assert m.end_offset == len("Deus existe")
    assert F.points_of(m) == 500                                                           # +5 for sending


def test_spoken_correct_has_no_speed_bonus(w):
    m = spoken(w["c"], w["ana"], "falei isso", w["f"])
    ok = F.review_mark(w["c"], w["s"].id, m.id, "correct")
    assert F.points_of(ok) == 1000                                                         # 5 + 5, bonus (60 - 60 s) = 0


def test_end_offset_counts_utf16_units(w):
    m = spoken(w["c"], w["ana"], "oi 😀", w["f"])
    assert m.end_offset == 5 and m.quote == "oi 😀"


def test_another_player_may_send_the_same_text_and_it_scores(w):
    a = spoken(w["c"], w["ana"], "mesma frase", w["f"])
    b = spoken(w["c"], w["bia"], "mesma frase", w["f"])
    assert a.id != b.id and a.counts and b.counts


def test_same_player_same_text_same_fallacy_is_a_duplicate_case_insensitive(w):
    spoken(w["c"], w["ana"], "Mesma Frase", w["f"])
    with pytest.raises(F.DuplicateMark):
        spoken(w["c"], w["ana"], "mesma   frase", w["f"])
    assert spoken(w["c"], w["ana"], "mesma frase", w["f2"]).fallacy_id == w["f2"]            # another fallacy is fine
    assert w["c"].execute("SELECT count(*) FROM stream_events WHERE kind = 'spoken'").fetchone()[0] == 2     # no orphan event


def test_rate_limit_is_shared_with_selected_pairs(w, monkeypatch):
    monkeypatch.setattr(F, "RATE_LIMIT_S", 10)
    c = w["c"]
    e = F.add_event(c, w["s"].id, "transcript", "1", "Se Deus existe então por que existe sofrimento")
    F.submit_mark(c, w["ana"], "transcript", e, 0, e, 7, w["f"])
    with pytest.raises(F.RateLimited):
        spoken(c, w["ana"], "outra frase", w["f"])
    assert c.execute("SELECT count(*) FROM stream_events WHERE kind = 'spoken'").fetchone()[0] == 0
    assert spoken(c, w["bia"], "outra pessoa", w["f"])                                      # limits are per player


def test_cap_of_20_scoring_pairs_is_shared(w):
    c = w["c"]
    ms = [spoken(c, w["ana"], f"frase {i}", w["f"]) for i in range(21)]
    assert [m.counts for m in ms] == [True] * 20 + [False]
    assert F.points_of(ms[-1]) == 0


@pytest.mark.parametrize("what", ["unknown", "disabled", "ended", "bingo"])
def test_refusals_create_nothing(w, what):
    c = w["c"]
    off = fallacy(c, "Desligada", enabled=False)
    uid, fid, exc = w["ana"], w["f"], None
    if what == "unknown":
        fid, exc = 9999, F.UnknownFallacy
    elif what == "disabled":
        fid, exc = off, F.UnknownFallacy
    elif what == "ended":
        S.end_session(c, w["s"].id, None)
        exc = F.SessionNotActive
    else:
        b = S.start_session(c, "Zé", "10.9.9.9")[1]
        uid, exc = b.id, F.NotAFalaciasPlayer
    with pytest.raises(exc):
        spoken(c, uid, "falei algo", fid)
    assert c.execute("SELECT count(*) FROM stream_events WHERE kind = 'spoken'").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM fallacy_marks").fetchone()[0] == 0


def test_a_selection_cannot_claim_the_spoken_kind(w):
    c = w["c"]
    m = spoken(c, w["ana"], "falei isso", w["f"])
    with pytest.raises(F.BadSelection):
        F.submit_mark(c, w["bia"], "spoken", m.start_event_id, 0, m.start_event_id, 3, w["f"])


def test_two_parallel_sends_of_one_player_let_only_one_through(test_db_url, w, monkeypatch):
    monkeypatch.setattr(F, "RATE_LIMIT_S", 10)
    out, barrier = [], threading.Barrier(2)

    def run(text):
        k = connect(test_db_url)
        barrier.wait()
        try:
            out.append(("ok", F.submit_spoken(k, w["ana"], text, w["f"]).id))
        except F.RateLimited:
            out.append(("limited", None))
        finally:
            k.close()
    ts = [threading.Thread(target=run, args=(t,)) for t in ("primeira", "segunda")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(o[0] for o in out) == ["limited", "ok"]
    assert w["c"].execute("SELECT count(*) FROM stream_events WHERE kind = 'spoken'").fetchone()[0] == 1


# ================================================================ the panes never show typed quotes
def test_feed_reads_ignore_spoken_events(w):
    c, sid = w["c"], w["s"].id
    e = F.add_event(c, sid, "chat", "1", "linha do chat")
    spoken(c, w["ana"], "segredo digitado", w["f"])
    assert [x.id for x in feed.recent_events(c, sid)] == [e]
    assert feed.events_after(c, sid, 0) == feed.recent_events(c, sid)
    assert feed.last_event_id(c, sid) == e


# ================================================================ admin queue
def test_queue_item_of_a_spoken_pair_has_no_context(w):
    c = w["c"]
    for i in range(5):
        spoken(c, w["bia"], f"vizinha {i}", w["f2"])
    F.add_event(c, w["s"].id, "chat", "1", "linha do chat")
    m = spoken(c, w["ana"], "a que importa", w["f"])
    item = F.queue_items(c, w["s"].id, mark_id=m.id)[0]
    assert (item.before, item.selected, item.after, item.mark.kind) == ([], ["a que importa"], [], "spoken")


def test_identical_typed_pairs_group_for_one_verdict(w):
    c, sid = w["c"], w["s"].id
    a = spoken(c, w["ana"], "Mesma Frase", w["f"])
    b = spoken(c, w["bia"], "mesma  frase", w["f"])
    other = spoken(c, w["bia"], "mesma frase", w["f2"])                                 # another fallacy: not the same
    e = F.add_event(c, sid, "chat", "9", "mesma frase")
    chat = F.submit_mark(c, w["ana"], "chat", e, 0, e, 11, w["f"])                         # a CHAT pair with the same text: not the same
    items = {i.mark.id: i for i in F.queue_items(c, sid)}
    assert items[a.id].same == items[b.id].same == 2 and items[other.id].same == 1 and items[chat.id].same == 1
    assert sorted(F.identical_pending_ids(c, sid, a.id)) == sorted([a.id, b.id])
    assert F.review_same_pairs(c, sid, a.id, "wrong") == 2
    st = {m.id: m.status for m in (F.get_mark(c, x) for x in (a.id, b.id, other.id, chat.id))}
    assert (st[a.id], st[b.id], st[other.id], st[chat.id]) == ("wrong", "wrong", "pending", "pending")


def test_spoken_pairs_count_on_the_leaderboard(w):
    spoken(w["c"], w["ana"], "um", w["f"])
    top = F.leaderboard(w["c"], w["s"].id)[0]
    assert (top.name, top.points, top.pairs) == ("Ana", 500, 1)


# ================================================================ video id parsing
@pytest.mark.parametrize("raw,expected", [
    (REAL, REAL),
    (f"  {REAL}  ", REAL),
    (f"https://www.youtube.com/watch?v={REAL}", REAL),
    (f"https://youtube.com/watch?v={REAL}&t=30s&list=PLx", REAL),
    (f"http://m.youtube.com/watch?feature=share&v={REAL}", REAL),
    (f"https://youtu.be/{REAL}?si=abc", REAL),
    (f"https://www.youtube.com/live/{REAL}", REAL),
    (f"https://www.youtube.com/embed/{REAL}", REAL),
    ("https://www.youtube.com/watch?v=_nVWPIFCUzQ", "_nVWPIFCUzQ"),
])
def test_parse_video_id(raw, expected):
    assert feed.parse_video_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None, "short", "x" * 12, f"https://evil.example/watch?v={REAL}",
                                 f"https://youtube.com.evil.example/watch?v={REAL}", "https://www.youtube.com/watch?v=short",
                                 "https://www.youtube.com/", f"javascript:alert('{REAL}')", f"{REAL}/../x"])
def test_parse_video_id_rejects(raw):
    with pytest.raises(feed.InvalidVideo):
        feed.parse_video_id(raw)


# ================================================================ choosing the source
HLIVE = feed.Source("live", REAL)


def info(vid, w, h):
    from app.livestream import StreamInfo
    return StreamInfo(vid, "t", w, h)


def test_a_horizontal_live_is_always_chosen_even_when_a_replay_is_requested(conn):
    replay_rows(conn, OTHER)
    assert feed.resolve_source(conn, info(REAL, 1920, 1080), requested=OTHER) == feed.Source("live", REAL)
    assert feed.resolve_source(conn, info(REAL, 1920, 1080), requested=None) == feed.Source("live", REAL)


def test_a_vertical_live_is_never_chosen(conn):
    replay_rows(conn, OTHER)
    assert feed.resolve_source(conn, info(REAL, 1080, 1920), requested=OTHER) == feed.Source("replay", OTHER)
    with pytest.raises(feed.NoSource):
        feed.resolve_source(conn, info(REAL, 1080, 1920), requested=None)


def test_no_live_needs_a_video_url(conn):
    with pytest.raises(feed.NoSource) as e:
        feed.resolve_source(conn, None, requested="  ")
    assert e.value.code == "no_source"


def test_requested_replay_must_be_imported_and_complete(conn):
    url = f"https://www.youtube.com/watch?v={OTHER}"
    with pytest.raises(feed.ReplayNotImported) as e:
        feed.resolve_source(conn, None, requested=url)
    assert e.value.video_id == OTHER and isinstance(e.value, feed.FeedUnavailable)
    conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, text) VALUES (%s,'chat',1,'só chat')", (OTHER,))
    with pytest.raises(feed.ReplayNotImported):                                    # one track only is not enough
        feed.resolve_source(conn, None, requested=url)
    replay_rows(conn, OTHER)
    conn.execute("DELETE FROM replay_events WHERE video_id = %s AND text = 'só chat'", (OTHER,))
    assert feed.resolve_source(conn, None, requested=url) == feed.Source("replay", OTHER)


def test_invalid_requested_url_is_reported(conn):
    with pytest.raises(feed.InvalidVideo):
        feed.resolve_source(conn, None, requested="https://evil.example/x")


# ================================================================ starting with a source
def test_start_stores_the_source_live_needs_no_replay_rows(conn):
    s, p, _ = feed.start_falacias(conn, "Ana", "10.0.0.1", HLIVE)
    assert (s.video_id, s.source_mode, s.game_type) == (REAL, "live", "falacias") and p.name == "Ana"


def test_start_replay_requires_its_rows_and_creates_no_session_otherwise(conn):
    with pytest.raises(feed.ReplayNotImported):
        feed.start_falacias(conn, "Ana", "10.0.0.1", feed.Source("replay", OTHER))
    assert S.get_active_session(conn, "falacias") is None
    replay_rows(conn, OTHER)
    s, _, _ = feed.start_falacias(conn, "Ana", "10.0.0.1", feed.Source("replay", OTHER))
    assert (s.video_id, s.source_mode) == (OTHER, "replay")


def test_admin_start_with_a_source(conn):
    s = feed.start_falacias_as_admin(conn, HLIVE)
    assert s.created_by is None and (s.video_id, s.source_mode) == (REAL, "live")


def test_legacy_start_without_a_source_uses_the_env_replay_and_stores_it(conn, monkeypatch):
    monkeypatch.setenv("FALACIAS_VIDEO_ID", OTHER)
    replay_rows(conn, OTHER)
    s, _, _ = feed.start_falacias(conn, "Ana", "10.0.0.1")
    assert (s.video_id, s.source_mode) == (OTHER, "replay")


def test_a_second_start_is_still_refused(conn):
    feed.start_falacias(conn, "Ana", "10.0.0.1", HLIVE)
    with pytest.raises(S.SessionAlreadyActive):
        feed.start_falacias(conn, "Bia", "10.0.0.2", HLIVE)


# ================================================================ clock and health follow the SESSION's source
def age(conn, sid, seconds):
    conn.execute("UPDATE game_sessions SET created_at = created_at - make_interval(secs => %s) WHERE id = %s", (seconds, sid))


def test_tick_of_a_live_session_releases_no_replay_lines(conn, monkeypatch):
    monkeypatch.setenv("FALACIAS_VIDEO_ID", OTHER)
    replay_rows(conn, OTHER)                                      # rows of the env video exist, but the session is LIVE
    s = feed.start_falacias_as_admin(conn, HLIVE)
    age(conn, s.id, 100)
    assert feed.tick(conn, s.id) == 0
    assert conn.execute("SELECT count(*) FROM stream_events").fetchone()[0] == 0


def test_tick_of_a_live_session_ignores_replay_rows_of_its_own_video_id(conn):
    replay_rows(conn, REAL)                                       # a recorded copy of the very video that is live now
    s = feed.start_falacias_as_admin(conn, HLIVE)
    age(conn, s.id, 100)
    assert feed.tick(conn, s.id) == 0 and conn.execute("SELECT count(*) FROM stream_events").fetchone()[0] == 0


def test_tick_of_a_replay_session_uses_its_own_video_not_the_env_one(conn, monkeypatch):
    monkeypatch.setenv("FALACIAS_VIDEO_ID", OTHER)               # env says OTHER, the session says REAL
    replay_rows(conn, OTHER)
    conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES (%s,'chat',500,'@z','do REAL')", (REAL,))
    conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, text) VALUES (%s,'transcript',600,'fala REAL')", (REAL,))
    s = feed.start_falacias_as_admin(conn, feed.Source("replay", REAL))
    age(conn, s.id, 10)
    assert feed.tick(conn, s.id) == 2
    assert sorted(r[0] for r in conn.execute("SELECT text FROM stream_events")) == ["do REAL", "fala REAL"]


def test_status_of_a_live_session_is_ok_and_of_a_replay_checks_its_rows(conn, monkeypatch):
    live = feed.start_falacias_as_admin(conn, HLIVE)
    assert feed.status(conn, live.id) == "ok"
    S.end_session(conn, live.id, None)
    replay_rows(conn, OTHER)
    rep = feed.start_falacias_as_admin(conn, feed.Source("replay", OTHER))
    assert feed.status(conn, rep.id) == "ok"
    conn.execute("DELETE FROM replay_events WHERE video_id = %s AND kind = 'chat'", (OTHER,))
    assert feed.status(conn, rep.id) == "down"
    with pytest.raises(feed.FeedDown):
        feed.ensure_up(conn, rep.id)


def test_ensure_up_never_blocks_a_live_session(conn):
    live = feed.start_falacias_as_admin(conn, HLIVE)
    feed.ensure_up(conn, live.id)                                    # the chat worker's own health arrives in L3

