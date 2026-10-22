# ruff: noqa: F811
"""Follow-up to the adversarial test: YouTube emoji shortcodes, "name [HH:MM:SS]: text" on pane lines, IP limit editable in the admin."""
from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from app import feed
from app import sessions as S
from app.falacias_web import line_view
from corpus import emoji as E
from corpus import replay
from tests.test_admin_group3 import PW, basic, bweb  # noqa: F401
from tests.test_falacias_web import _env, conn  # noqa: F401


# ------------------------------------------------------------------ emoji shortcodes
def test_youtube_emoji_names_become_unicode():
    assert E.replace_shortcodes("Hoje é treta?:face-turquoise-covering-eyes:") == "Hoje é treta?🙈"
    assert E.replace_shortcodes(":face-green-smiling: oi :elbowcough:") == "😊 oi 🤧"
    assert E.replace_shortcodes("chorando :eyes-purple-crying:") == "chorando 😭"
    assert E.replace_shortcodes("@fulano :hands-yellow-heart-red:") == "@fulano 🫶"


def test_unknown_youtube_style_name_is_dropped_and_channel_emoji_and_clock_times_stay():
    assert E.replace_shortcodes("oi :face-pink-unheard-of: tchau") == "oi tchau"
    assert E.replace_shortcodes("salve :canalemoji: ok") == "salve :canalemoji: ok"      # channel emoji: not ours to guess
    assert E.replace_shortcodes("às 10:30:45 começa") == "às 10:30:45 começa"


def test_chat_runs_use_the_mapping_for_image_emoji_only():
    runs = [{"text": "oi "}, {"emoji": {"isCustomEmoji": True, "emojiId": "UCx/abc", "shortcuts": [":face-blue-smiling:"]}},
            {"emoji": {"emojiId": "😀"}}, {"emoji": {"isCustomEmoji": True, "emojiId": "UCx/def", "shortcuts": [":canal:"]}}]
    assert replay._runs_text(runs) == "oi 😊😀:canal:"


def test_fix_replay_events_rewrites_old_rows_once(conn):
    conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES "
                 "('vid', 'chat', 1, 'a', 'oi:face-blue-smiling:'), ('vid', 'chat', 2, 'b', 'sem emoji'), "
                 "('vid', 'chat', 3, 'c', ':face-zzz-unknown-thing:'), ('other', 'chat', 1, 'd', 'x :washhands:')")
    assert E.fix_replay_events(conn, "vid") == 1
    texts = {r[0]: r[1] for r in conn.execute("SELECT offset_ms, text FROM replay_events WHERE video_id = 'vid'")}
    assert texts == {1: "oi😊", 2: "sem emoji", 3: ":face-zzz-unknown-thing:"}      # a message that would become empty is left alone
    assert conn.execute("SELECT text FROM replay_events WHERE video_id = 'other'").fetchone()[0] == "x :washhands:"
    assert E.fix_replay_events(conn, "vid") == 0                                   # idempotent
    assert E.fix_replay_events(conn) == 1                                          # no video id: every video


# ------------------------------------------------------------------ "name [HH:MM:SS]: text"
def _ev(author, text="olá", kind="chat"):
    return feed.Event(7, kind, author, text, datetime(2026, 10, 22, 15, 4, 5, tzinfo=UTC))


def test_line_shows_name_time_and_colon_outside_the_selectable_text(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/Sao_Paulo")
    html = str(line_view(_ev("Ana")))
    assert re.search(r'<span class="au">Ana <time[^>]*>\[12:04:05\]</time></span>', html)       # 15:04 UTC = 12:04 in São Paulo
    assert '<span class="tx">olá</span>' in html
    assert html.index('class="au"') < html.index('class="tx"')


def test_transcript_line_has_only_the_time():
    html = str(line_view(_ev(None, kind="transcript")))
    assert re.search(r'<span class="au"><time[^>]*>\[\d\d:\d\d:\d\d\]</time></span>', html) and "None" not in html


def test_feed_events_carry_the_time_the_line_reached_the_pane(conn):
    s, _p, _ = S.start_session(conn, "Ana", "10.0.0.1", game_type="falacias")
    conn.execute("INSERT INTO stream_events (session_id, kind, source_key, author, text, fed_at) "
                 "VALUES (%s, 'chat', 'k', 'Zé', 'oi', '2026-10-22 15:00:00+00')", (s.id,))
    ev = feed.recent_events(conn, s.id)[-1]
    assert ev.fed_at == datetime(2026, 10, 22, 15, 0, tzinfo=UTC) and feed.events_after(conn, s.id, 0)[-1].fed_at == ev.fed_at


def test_css_keeps_time_unselectable_and_colon_after_it():
    css = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text()
    assert ".pline .au time" in css and ".pline .au::after" in css


# ------------------------------------------------------------------ IP limit in the DB
@pytest.fixture(autouse=True)
def _clean_settings(conn):
    conn.execute("DELETE FROM app_settings")             # a stored limit must never leak into other tests
    yield
    conn.execute("DELETE FROM app_settings")


def test_limit_defaults_to_env_then_db_overrides_it(conn, monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "3")
    assert S.max_users_per_ip(conn) == 3
    S.set_max_users_per_ip(conn, 5)
    assert S.max_users_per_ip(conn) == 5
    S.set_max_users_per_ip(conn, 2)                                               # upsert, not a second row
    assert S.max_users_per_ip(conn) == 2
    assert conn.execute("SELECT count(1) FROM app_settings").fetchone()[0] == 1


@pytest.mark.parametrize("bad", [0, -1, 51, "x", None])
def test_limit_rejects_nonsense(conn, bad):
    with pytest.raises(ValueError):
        S.set_max_users_per_ip(conn, bad)


def test_join_uses_the_db_limit(conn, monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "1")
    S.set_max_users_per_ip(conn, 2)
    S.start_session(conn, "Ana", "10.0.0.1")
    S.join_session(conn, "Bia", "10.0.0.1")
    with pytest.raises(S.IpLimitReached):
        S.join_session(conn, "Caio", "10.0.0.1")


def test_admin_page_shows_the_form_and_saving_changes_the_limit(bweb, conn, monkeypatch):
    monkeypatch.setenv("MAX_USERS_PER_IP", "3")
    html = bweb.get("/admin", headers=basic()).text
    form = re.search(r'<form[^>]*action="/admin/settings/ip-limit".*?</form>', html, re.DOTALL).group(0)
    assert 'name="limit"' in form and 'value="3"' in form and 'name="csrf"' in form
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', form).group(1)
    r = bweb.post("/admin/settings/ip-limit", data={"limit": "6", "csrf": token}, headers=basic())
    assert r.status_code == 303 and S.max_users_per_ip(conn) == 6
    assert 'value="6"' in bweb.get("/admin", headers=basic()).text


def test_saving_needs_auth_a_valid_token_and_a_sane_number(bweb, conn):
    assert bweb.post("/admin/settings/ip-limit", data={"limit": "6", "csrf": "x"}).status_code == 401
    assert bweb.post("/admin/settings/ip-limit", data={"limit": "6", "csrf": "bad"}, headers=basic()).status_code == 403
    before = S.max_users_per_ip(conn)
    html = bweb.get("/admin", headers=basic()).text
    token = re.search(r'action="/admin/settings/ip-limit".*?name="csrf" value="([0-9a-f]+)"', html, re.DOTALL).group(1)
    for bad in ("0", "99", "abc", ""):
        r = bweb.post("/admin/settings/ip-limit", data={"limit": bad, "csrf": token}, headers=basic())
        assert r.status_code == 303 and "err=" in r.headers["location"]
    assert S.max_users_per_ip(conn) == before                                      # unchanged


def test_player_message_names_the_current_db_limit(conn, monkeypatch):
    from app.ui import error_box
    S.set_max_users_per_ip(conn, 4)
    assert "4" in str(error_box("ip_limit_reached", limit=S.max_users_per_ip(conn)))
