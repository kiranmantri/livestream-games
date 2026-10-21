"""Completed lines of a bingo card (full rows, full columns, the two diagonals) and their blue boxes.

Purely visual: the winner is still the player with the MOST marked cells. On a 5x5 card the avatar free cell counts
as marked. The boxes are children of the board grid, placed with `grid-area` (so they follow the cells exactly,
gaps included): a row/column is one rectangle, a diagonal is one box around EACH of its cells.
"""
from __future__ import annotations

from collections.abc import Collection

from fasthtml.common import Div

from app.cards import grid_of

Line = tuple[str, int]     # ("row", r) | ("col", c) | ("diag", 0 = top-left to bottom-right, 1 = top-right to bottom-left)


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


def line_areas(line: Line, n: int) -> list[str]:
    """CSS grid areas (row-start / col-start / row-end / col-end, all explicit: the board's rows are IMPLICIT, so `-1`
    would not reach the last row) of the boxes of a line: ONE rectangle for a row/column, one box PER CELL for a diagonal."""
    kind, k = line
    if kind == "row":
        return [f"grid-area:{k + 1} / 1 / {k + 2} / {n + 1}"]
    if kind == "col":
        return [f"grid-area:1 / {k + 1} / {n + 1} / {k + 2}"]
    return [f"grid-area:{i + 1} / {(i if k == 0 else n - 1 - i) + 1} / {i + 2} / {(i if k == 0 else n - 1 - i) + 2}"
            for i in range(n)]


def is_full(cells: list[dict], marked: Collection[int]) -> bool:
    """Every phrase cell of the card is marked."""
    return all(i in marked for i, c in enumerate(cells) if "phrase_id" in c)


def line_boxes(cells: list[dict], marked: Collection[int]) -> list:
    n = grid_of(len(cells))
    return [Div(cls=f"line {kind}", style=area, aria_hidden="true")
            for kind, k in complete_lines(cells, marked) for area in line_areas((kind, k), n)]
