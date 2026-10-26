# ruff: noqa: F811
"""One slide panel for everything a player sends: the selected passage (optional), the fallacy (optional) and a free-form message.
  passage + fallacy (+ message)  -> a pair; the message travels with it as a note the admin reads in the queue
  fallacy + message (no passage) -> a typed fallacy (the old "Frase falada")
  message only                   -> a plain message to the host (the old "Mensagem para o apresentador")
The panel opens when a chat or transcript pane is clicked, with or without selected text (the transcript/chat may fail)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import falacias as F
from app import host_messages as H
from app import texts as T
from tests.test_admin_falacias import (  # noqa: F401
    IP_ADMIN,
    IP_B,
    PW,
    A,
    _env,
    basic,
    conn,
    fal,
    game,
    named,
    review,
    sid_of,
    web,
)

ROOT = Path(__file__).resolve().parents[1]


def sel(conn, text="fala um", a=0, b=None):
    row = conn.execute("SELECT id, kind FROM stream_events WHERE session_id = %s AND text = %s", (sid_of(conn), text)).fetchone()
    return {"kind": row[1], "start_id": row[0], "start_off": a, "end_id": row[0], "end_off": len(text) if b is None else b}


def post(c, **over):
    data = {"kind": "", "start_id": "", "start_off": "", "end_id": "", "end_off": "", "fallacy_id": "", "note": ""} | over
    return c.post("/falacias/mark", data=data)


def marks(conn):
    return conn.execute("SELECT kind, quote, note FROM fallacy_marks ORDER BY created_at").fetchall()


def messages(conn):
    return [r[0] for r in conn.execute("SELECT text FROM host_messages ORDER BY created_at").fetchall()]


# ---------------------------------------------------------------- the page
def test_the_old_boxes_are_gone_and_the_panel_has_the_message_field(web, conn, fal, game):
    html = game["ana"].get("/falacias").text
    assert 'id="spokenbox"' not in html and 'id="hostbox"' not in html
    assert '/falacias/spoken' not in html and '/falacias/message' not in html
    assert not hasattr(T, "FAL_SPOKEN_HEADING") and not hasattr(T, "HM_HEADING")
    picker = re.search(r'<form[^>]*id="picker".*?</form>', html, re.DOTALL).group(0)
    note = re.search(r"<textarea[^>]*name=\"note\"[^>]*>", picker)
    assert note and 'maxlength="500"' in note.group(0) and "required" not in note.group(0)
    assert picker.index('name="note"') < picker.index(f">{T.FAL_SEND}<"), "the message box comes right before the send button"
    assert T.FAL_NOTE_LABEL in picker
    assert 'id="pk-quote"' not in picker, "there is ONE text box: the selected passage is written into it"


def test_choosing_a_fallacy_is_no_longer_mandatory_in_the_form(web, conn, fal, game):
    picker = re.search(r'<form[^>]*id="picker".*?</form>', game["ana"].get("/falacias").text, re.DOTALL).group(0)
    radios = re.findall(r'<input[^>]*type="radio"[^>]*>', picker)
    assert radios and not any("required" in r for r in radios)


def test_the_panel_script_opens_on_any_pane_click(web):
    js = (ROOT / "app" / "static" / "falacias.js").read_text()
    assert "field('note')" in js and "openPicker" in js
    assert "fal:spoken-sent" not in js and "fal:msg-sent" not in js and "sp-text" not in js and "hm-text" not in js
    assert re.search(r"pane\.addEventListener\('click',\s*function[^)]*\)\s*\{[^}]*openPicker", js), "a click on a pane opens the panel"


def test_the_old_routes_are_gone(web, conn, fal, game):
    assert game["ana"].post("/falacias/spoken", data={"text": "x", "fallacy_id": fal["Espantalho"]}).status_code == 404
    assert game["ana"].post("/falacias/message", data={"text": "x"}).status_code == 404


# ---------------------------------------------------------------- a pair with a note
def test_passage_in_the_text_box_untouched_is_an_anchored_pair(web, conn, fal, game):
    r = post(game["ana"], **sel(conn), fallacy_id=fal["Espantalho"], note="  fala   um ")
    assert r.status_code == 200 and T.FAL_SENT in r.text and "fal:sent" in r.headers.get("hx-trigger", "")
    assert marks(conn) == [("transcript", "fala um", "")] and messages(conn) == []


def test_a_passage_whose_text_was_edited_becomes_typed_text_without_context(web, conn, fal, game):
    r = post(game["ana"], **sel(conn), fallacy_id=fal["Espantalho"], note="acho que fala um é espantalho")
    assert T.FAL_SENT in r.text
    assert marks(conn) == [("spoken", "acho que fala um é espantalho", "")] and messages(conn) == []


def test_an_empty_text_box_with_a_passage_still_sends_the_anchored_pair(web, conn, fal, game):
    post(game["ana"], **sel(conn), fallacy_id=fal["Espantalho"])
    assert marks(conn) == [("transcript", "fala um", "")]


def test_an_edited_passage_text_is_capped_at_280_but_an_untouched_passage_may_be_longer(web, conn, fal, game):
    assert T.FAL_ERRORS["note_too_long"] in post(game["ana"], **sel(conn), fallacy_id=fal["Espantalho"], note="fala um " + "x" * 300).text
    assert marks(conn) == []


def test_the_script_writes_the_passage_into_the_text_box(web):
    js = (ROOT / "app" / "static" / "falacias.js").read_text()
    assert "putText(" in js and "field('quote')" not in js


def test_a_pair_without_a_note_has_an_empty_note(web, conn, fal, game):
    post(game["ana"], **sel(conn), fallacy_id=fal["Espantalho"])
    assert marks(conn) == [("transcript", "fala um", "")]


def test_the_admin_queue_shows_a_stored_note_escaped(web, conn, fal, game):
    post(game["ana"], **sel(conn), fallacy_id=fal["Espantalho"])
    conn.execute("UPDATE fallacy_marks SET note = %s", ("<b>veja</b> o minuto 3",))      # notes of marks sent before the single text box
    html = game["admin"].get("/admin/falacias", headers=basic()).text
    assert "&lt;b&gt;veja&lt;/b&gt; o minuto 3" in html and "<b>veja</b>" not in html
    assert T.AF_NOTE in html


def test_a_pair_without_a_note_shows_no_note_line_in_the_queue(web, conn, fal, game):
    post(game["ana"], **sel(conn), fallacy_id=fal["Espantalho"])
    assert 'class="note"' not in game["admin"].get("/admin/falacias", headers=basic()).text


def test_a_passage_with_a_note_but_no_fallacy_is_refused(web, conn, fal, game):
    r = post(game["ana"], **sel(conn), note="só um comentário")
    assert T.FAL_ERRORS["need_fallacy"] in r.text and marks(conn) == [] and messages(conn) == []
    assert "fal:sent" not in r.headers.get("hx-trigger", "")


# ---------------------------------------------------------------- a typed fallacy (no passage)
def test_fallacy_and_message_without_a_passage_is_a_typed_fallacy(web, conn, fal, game):
    r = post(game["ana"], fallacy_id=fal["Espantalho"], note="  Deus existe porque a Bíblia diz  ")
    assert T.FAL_SENT in r.text and "fal:sent" in r.headers.get("hx-trigger", "")
    assert marks(conn) == [("spoken", "Deus existe porque a Bíblia diz", "")] and messages(conn) == []


def test_a_typed_fallacy_is_in_the_players_list_and_in_the_queue(web, conn, fal, game):
    post(game["ana"], fallacy_id=fal["Ad hominem"], note="frase digitada")
    assert "frase digitada" in game["ana"].get("/falacias/side").text
    assert "frase digitada" in game["admin"].get("/admin/falacias", headers=basic()).text


def test_a_fallacy_without_any_text_asks_for_the_text(web, conn, fal, game):
    r = post(game["ana"], fallacy_id=fal["Espantalho"])
    assert T.FAL_ERRORS["need_text"] in r.text and marks(conn) == []


def test_the_same_typed_fallacy_twice_is_refused(web, conn, fal, game):
    post(game["ana"], fallacy_id=fal["Espantalho"], note="Mesma frase")
    r = post(game["ana"], fallacy_id=fal["Espantalho"], note="mesma FRASE")
    assert T.FAL_ERRORS["duplicate_mark"] in r.text and len(marks(conn)) == 1


# ---------------------------------------------------------------- a plain message
def test_a_message_alone_goes_to_the_host(web, conn, fal, game):
    r = post(game["ana"], note="Pergunte sobre a evolução")
    assert r.status_code == 200 and T.FAL_SENT_MESSAGE in r.text and "fal:sent" in r.headers.get("hx-trigger", "")
    assert messages(conn) == ["Pergunte sobre a evolução"] and marks(conn) == []
    assert "Pergunte sobre a evolução" in game["admin"].get("/admin/falacias", headers=basic()).text


def test_nothing_at_all_is_refused(web, conn, fal, game):
    r = post(game["ana"])
    assert T.FAL_ERRORS["empty"] in r.text and marks(conn) == [] and messages(conn) == []
    assert T.FAL_ERRORS["empty"] in post(game["ana"], note="   \n ").text


def test_message_limits_still_apply_and_are_explained(web, conn, fal, game, monkeypatch):
    monkeypatch.setattr(H, "RATE_LIMIT_S", 30)
    post(game["ana"], note="primeira")
    assert T.HM_ERRORS["message_rate_limited"].format(n=30) in post(game["ana"], note="outra").text
    monkeypatch.setattr(H, "RATE_LIMIT_S", 0)
    for i in range(4):
        post(game["ana"], note=f"m{i}")
    assert T.HM_ERRORS["message_limit_reached"].format(max=5) in post(game["ana"], note="demais").text
    assert len(messages(conn)) == 5


# ---------------------------------------------------------------- limits shared by all three
@pytest.mark.parametrize("extra", [{}, {"fallacy_id": "FAL"}, {"sel": True, "fallacy_id": "FAL"}])
def test_a_note_of_281_characters_is_refused_and_280_is_accepted(web, conn, fal, game, extra):
    def go(text):
        over = {k: v for k, v in extra.items() if k not in ("sel", "fallacy_id")}
        if extra.get("sel"):
            over |= sel(conn)
        if "fallacy_id" in extra:
            over["fallacy_id"] = fal["Espantalho"]
        return post(game["ana"], note=text, **over)
    assert T.FAL_ERRORS["note_too_long"] in go("x" * 281).text and not marks(conn) and not messages(conn)
    assert T.FAL_SENT in go("y" * 280).text or T.FAL_SENT_MESSAGE in go("y" * 280).text


def test_control_characters_in_a_message_are_refused_or_cleaned_never_stored_raw(web, conn, fal, game):
    post(game["ana"], fallacy_id=fal["Espantalho"], note="a\x07b   c")
    stored = marks(conn) + [(m,) for m in messages(conn)]
    assert stored and all("\x07" not in str(v) for row in stored for v in row)


def test_a_pair_keeps_its_own_rate_limit(web, conn, fal, game, monkeypatch):
    monkeypatch.setattr(F, "RATE_LIMIT_S", 10)
    post(game["ana"], fallacy_id=fal["Espantalho"], note="primeira")
    r = post(game["ana"], fallacy_id=fal["Espantalho"], note="segunda")
    assert T.FAL_ERRORS["rate_limited"].format(n=10) in r.text and len(marks(conn)) == 1


def test_unknown_fallacy_with_a_message_is_refused(web, conn, fal, game):
    r = post(game["ana"], fallacy_id="999999", note="frase")
    assert T.FAL_ERRORS["unknown_fallacy"] in r.text and marks(conn) == [] and messages(conn) == []


# ---------------------------------------------------------------- identity and session state
def test_needs_a_falacias_identity(web, conn, fal, game):
    assert post(web(IP_B), note="oi").status_code == 401
    bingo = named(web("203.0.113.50"), "Ze")
    bingo.post("/start")
    assert post(bingo, note="oi").status_code == 401
    assert marks(conn) == [] and messages(conn) == []


def test_after_the_session_ended_the_page_refreshes(web, conn, fal, game):
    from app import sessions as S
    S.end_session(conn, sid_of(conn), None)
    for over in ({"note": "oi"}, {"fallacy_id": fal["Espantalho"], "note": "frase"}):
        assert post(game["ana"], **over).headers.get("hx-refresh") == "true"


# ---------------------------------------------------------------- schema
def test_the_note_column_exists_and_is_capped(web, conn, fal, game):
    cols = {r[0]: r[1] for r in conn.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'fallacy_marks'")}
    assert cols.get("note") == "text"
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO fallacy_marks (session_id, user_id, kind, start_event_id, start_offset, end_event_id, end_offset, quote, "
                     "fallacy_id, latency_ms, counts, note) SELECT s.id, u.id, 'spoken', 1, 0, 1, 1, 'q', 1, 0, true, %s "
                     "FROM game_sessions s, users u LIMIT 1", ("x" * 281,))


def test_css_has_no_rules_left_for_the_removed_boxes():
    css = (ROOT / "app" / "static" / "app.css").read_text()
    assert "#spokenbox" not in css and "#hostbox" not in css
    assert "#picker textarea" in css or ".picker textarea" in css
