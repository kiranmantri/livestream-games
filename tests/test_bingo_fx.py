"""Bingo celebration effects (app/static/fx.js, called by game.js on a tap that MARKS a cell):
a small burst clipped to the tapped cell, a larger one clipped to my card when the tap completes a row / column /
diagonal, and fireworks over the whole window when it completes the card."""
import re
from pathlib import Path

from tests.test_live import conn, play, web  # noqa: F401  (fixtures)

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
FX = (STATIC / "fx.js").read_text() if (STATIC / "fx.js").exists() else ""
GAME = (STATIC / "game.js").read_text()
CSS = (STATIC / "app.css").read_text()


def test_game_page_loads_the_effects(web):  # noqa: F811  (fixture)
    browser, _ = web
    ana, _bia = play(browser)
    html = ana.get("/").text
    assert html.count('src="/fx.js"') == 1
    assert ana.get("/fx.js").status_code == 200


def test_game_js_celebrates_only_a_tap_that_marks_a_live_cell():
    tap = GAME.split("c.classList.add('pending')", 1)[0].split("closest('.cardbox.mine button.cell')", 1)[1]
    # only when the cell turned ON, never on a revoked cell (the server refuses it), and only if fx.js loaded
    assert "else if (!c.classList.contains('revoked')) window.bingoFx.tap(c, e);" in tap
    assert "if (window.bingoFx) {" in tap


def test_three_sizes_cell_card_and_window():
    for fn in ("function cellFx", "function cardFx", "function screenFx", "function newLines"):
        assert fn in FX
    assert "'.cardbox.mine'" in FX
    assert "devicePixelRatio" in FX and "requestAnimationFrame" in FX
    assert "prefers-reduced-motion" in FX                                        # no effects for users who opted out of motion


def test_full_card_beats_the_line_effect():
    tap = FX.split("function tap", 1)[1]
    full, rest = tap.split("screenFx(", 1)
    assert "full" in full and "cardFx(" in rest                                  # a full card gets the window, else lines


def test_effect_layers_are_clipped_and_never_block_taps():
    rule = re.search(r"\.fx\s*\{[^}]*\}", CSS)
    assert rule and "pointer-events: none" in rule.group(0) and "position: absolute" in rule.group(0)
    assert re.search(r"\.fx\.cell-fx\s*\{[^}]*z-index", CSS)
    assert re.search(r"\.fx\.card-fx\s*\{[^}]*inset: 0", CSS)                   # the card layer is exactly the card
    assert re.search(r"\.fx\.screen-fx\s*\{[^}]*position: fixed[^}]*inset: 0", CSS)
    assert re.search(r"\.cardbox\.mine\s*\{[^}]*position: relative", CSS)        # the card layer positions against the card


def test_durations_cell_and_line():
    assert "CELL_MS = 2800" in FX and "CARD_MS = 6400" in FX      # 0.7 s / 1.6 s, doubled twice on request
