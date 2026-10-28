"""Bingo player page: hovering another player's card shows it 2x larger in a temporary overlay (game.js + app.css)."""
import re
from pathlib import Path

from tests.test_live import conn, play, web  # noqa: F401  (fixtures)

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
JS = (STATIC / "game.js").read_text()
CSS = (STATIC / "app.css").read_text()


def test_game_page_has_one_hidden_zoom_overlay_outside_the_polled_cards(web):  # noqa: F811  (fixture)
    browser, _ = web
    ana, _bia = play(browser)
    html = ana.get("/").text
    assert html.count('id="cardzoom"') == 1
    m = re.search(r'<div[^>]*id="cardzoom"[^>]*>', html)
    assert m
    tag = m.group(0)
    assert re.search(r"\shidden[\s>=]", tag) and 'aria-hidden="true"' in tag                         # a visual copy: screen readers skip it
    others = html.split('id="others"', 1)[1].split('id="cardzoom"', 1)[0]
    assert "</div>" in others                                                       # #others closes before the overlay
    poll = ana.get("/cards?v=x").text                                               # the poll reply never carries it
    assert 'id="cardzoom"' not in poll


def test_zoom_script_clones_the_hovered_card_at_twice_its_width():
    assert "ZOOM = 2" in JS
    assert "'.cardbox.other'" in JS and "cloneNode(true)" in JS
    assert "rect.width * ZOOM" in JS                                                # wider box: container-query fonts grow too
    assert "'pointerover'" in JS and "'pointerout'" in JS


def test_zoom_only_with_a_real_pointer_and_never_in_the_list_view():
    assert "e.pointerType !== 'mouse'" in JS                                       # touch keeps the long-press tooltip
    assert "view-list" in JS.split("function showZoom", 1)[1].split("}", 1)[0]


def test_zoom_follows_the_poll_redraw_of_the_cards():
    assert "htmx:afterSettle" in JS and ".cardbox.other:hover" in JS


def test_zoom_overlay_css_is_fixed_click_through_and_hidden_attribute_wins():
    m = re.search(r"\.cardzoom\s*\{([^}]*)\}", CSS)
    assert m
    rule = m.group(1)
    assert "position: fixed" in rule and "pointer-events: none" in rule
    assert re.search(r"\.cardzoom\[hidden\]\s*\{\s*display: none !important;", CSS)
    assert re.search(r"\.cardzoom \.cardbox\.other\s*\{[^}]*box-shadow", CSS)


def test_zoomed_copy_lifts_the_small_cards_font_caps_so_text_grows_too():
    assert re.search(r"\.cardzoom \.cardbox\.other \.cell\s*\{\s*font-size: clamp\(9px, 2\.5cqw, 18px\)", CSS)
