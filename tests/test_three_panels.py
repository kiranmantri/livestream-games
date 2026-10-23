# ruff: noqa: F811
"""Falácias live page: three panels side by side, in this order: video, chat, transcript. The video panel has a hide button;
hidden, the other two share the width 50/50."""
from __future__ import annotations

import re
from pathlib import Path

from app import texts as T
from tests.test_falacias_web import _env, conn  # noqa: F401
from tests.test_live_pages import LIVE_ID, live_game, make_live, named, page_for, start

ROOT = Path(__file__).resolve().parents[1]


def html_of(test_db_url, live=True):
    tc, b = live_game(test_db_url) if live else page_for(test_db_url, make_live(None))
    with tc:
        start(named(b, "Ana"), "Ana")
        return b.get("/falacias").text


def panes(html):
    return re.search(r'<div[^>]*class="panes[^"]*"[^>]*>', html).group(0)


def test_live_page_has_video_chat_transcript_in_this_order(conn, test_db_url):
    html = html_of(test_db_url)
    order = [html.index(x) for x in ('class="pane video', 'id="pane-chat"', 'id="pane-transcript"')]
    assert order == sorted(order) and "three" in panes(html)
    assert "vidbox" not in html and html.count(f"youtube-nocookie.com/embed/{LIVE_ID}") == 1


def test_video_panel_title_has_the_hide_button_on_its_right(conn, test_db_url):
    html = html_of(test_db_url)
    bar = re.search(r'<div class="pane video.*?<div class="bar"[^>]*>(.*?)</div>', html, re.DOTALL).group(1)
    assert T.STREAM_TITLE in bar and T.VIDEO_HIDE in bar and bar.index(T.STREAM_TITLE) < bar.index(T.VIDEO_HIDE)
    assert T.VIDEO_HIDE == "Ocultar vídeo"
    btn = re.search(r"<button[^>]*data-hidevideo[^>]*>", html) or re.search(r"<button[^>]*hidevideo[^>]*>", html)
    assert btn and 'type="button"' in btn.group(0)


def test_there_is_a_hidden_show_button_for_when_the_video_is_hidden(conn, test_db_url):
    html = html_of(test_db_url)
    show = re.search(r"<button[^>]*showvideo[^>]*>.*?</button>", html, re.DOTALL)
    assert show and "hidden" in show.group(0) and T.VIDEO_SHOW in show.group(0)


def test_replay_page_has_no_video_panel_and_no_button(conn, test_db_url):
    html = html_of(test_db_url, live=False)
    assert "pane video" not in html and T.VIDEO_HIDE not in html and "three" not in panes(html)


def test_script_hides_and_unloads_the_video_and_restores_it():
    js = (ROOT / "app" / "static" / "falacias.js").read_text()
    assert "hidevideo" in js and "showvideo" in js and "about:blank" in js and "novideo" in js


def test_css_three_columns_then_fifty_fifty_when_hidden():
    css = (ROOT / "app" / "static" / "app.css").read_text()
    assert re.search(r"\.panes\.three\s*\{[^}]*grid-template-columns:[^;}]*fr[^;}]*fr[^;}]*fr", css)
    assert re.search(r"\.panes\.three\.novideo\s*\{[^}]*grid-template-columns:\s*1fr 1fr", css)
    assert re.search(r"\.pane\[hidden\]\s*\{[^}]*display:\s*none", css)
    assert re.search(r"@media \(max-width: 760px\)[^{]*\{[^}]*\.panes\.three[^}]*1fr\s*;", css), "one column on phones"
