"""Completed lines of a bingo card (full rows, full columns, the two diagonals) and their red boxes.

Purely visual: the winner is still the player with the MOST marked cells. On a 5x5 card the avatar free cell counts
as marked. A line is a box drawn over the board (position: absolute, see .line in app.css); the board is square,
so percentages of its width/height map to cells (the diagonals are a band rotated by 45 degrees).
"""
from __future__ import annotations

from collections.abc import Collection

from fasthtml.common import Div

from app.cards import grid_of

Line = tuple[str, int]     # ("row", r) | ("col", c) | ("diag", 0 = top-left to bottom-right, 1 = top-right to bottom-left)
SQRT2 = 2 ** 0.5


def complete_lines(cells: list[dict], marked: Collection[int]) -> list[Line]:
    n = grid_of(len(cells))

    def ok(i: int) -> bool:
        return i in marked or "phrase_id" not in cells[i]       # the avatar free cell is always "marked"

    out: list[Line] = []
    out += [("row", r) for r in range(n) if all(ok(r * n + c) for c in range(n))]
    out += [("col", c) for c in range(n) if all(ok(r * n + c) for r in range(n))]
    if all(ok(i * n + i) for i in range(n)):
        out.append(("diag", 0))
    if all(ok(i * n + (n - 1 - i)) for i in range(n)):
        out.append(("diag", 1))
    return out


def line_style(line: Line, n: int) -> str:
    size = 100 / n
    kind, k = line
    if kind == "row":
        return f"left:0;top:{k * size:.3f}%;width:100%;height:{size:.3f}%"
    if kind == "col":
        return f"top:0;left:{k * size:.3f}%;height:100%;width:{size:.3f}%"
    length, thick = 100 * SQRT2, size * 1.1                      # board diagonal x a bit more than one cell
    angle = 45 if k == 0 else -45
    return (f"left:{50 - length / 2:.3f}%;top:{50 - thick / 2:.3f}%;width:{length:.3f}%;height:{thick:.3f}%;"
            f"transform:rotate({angle}deg)")


def line_boxes(cells: list[dict], marked: Collection[int]) -> list:
    n = grid_of(len(cells))
    return [Div(cls=f"line {kind}", style=line_style((kind, k), n), aria_hidden="true")
            for kind, k in complete_lines(cells, marked)]
