"""Table cells: reading a number out of a cell, tracing program inputs to cells, editing cells.

Two percent rules exist; pick one explicitly:
  * "dataset" (default for the pipeline, decided Nov 1): a '%' cell means a fraction,
    "26.2%" -> 0.262. This is how FinQA's own table ops read it (see cell_number().value).
  * "nov1" (only to reproduce the Nov 1 profile): the first number as written, "26.2%" -> 26.2
    (first_number()).

Tracing rule (Nov 1 definition; used for both rules, because program literals copy the number
as written: a "26.2%" cell usually appears in a program as 26.2):
  * only body cells are searched: rows table[1:] (header skipped), columns row[1:] (row label skipped);
  * a cell's number is the FIRST number in it after removing '$' and ',', so
    "$ 6305" -> 6305, "14.3% ( 14.3 % )" -> 14.3, "-23158 ( 23158 )" -> -23158;
  * a literal matches a cell when their ABSOLUTE values are equal (within 1e-9);
  * a '%' literal is compared as written ("4.02%" -> 4.02), not divided by 100.
Percent inputs are flagged (is_percent_cell) so the pipeline can treat them separately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence

CELL_NUMBER = re.compile(r"-?\d+\.?\d*")
NUMBER_TOKEN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
MATCH_EPS = 1e-9

Table = Sequence[Sequence[str]]
PercentRule = Literal["dataset", "nov1"]


def first_number(cell: str) -> float | None:
    """First number in the cell as written (Nov 1 rule): "26.2%" -> 26.2."""
    m = CELL_NUMBER.search(cell.replace("$", "").replace(",", ""))
    return float(m.group()) if m else None


@dataclass(frozen=True)
class CellNumber:
    shown: float      # first number as written: "26.2%" -> 26.2
    is_percent: bool  # the cell carries a '%' sign

    @property
    def value(self) -> float:
        """Dataset rule: "26.2%" -> 0.262; other cells unchanged."""
        return self.shown / 100 if self.is_percent else self.shown


def cell_number(cell: str) -> CellNumber | None:
    shown = first_number(cell)
    return None if shown is None else CellNumber(shown, "%" in cell)


def cell_value(cell: str, rule: PercentRule = "dataset") -> float | None:
    """The number a cell stands for under the chosen percent rule."""
    if rule == "nov1":
        return first_number(cell)
    n = cell_number(cell)
    return None if n is None else n.value


def is_percent_cell(table: Table, row: int, col: int) -> bool:
    return "%" in table[row][col]


def locate(literal: str, table: Table) -> list[tuple[int, int]]:
    """(row, col) of every body cell whose number equals the literal (sign ignored)."""
    target = abs(float(literal.strip().rstrip("%")))
    hits = []
    for i, row in enumerate(table[1:], start=1):
        for j, cell in enumerate(row[1:], start=1):
            v = first_number(cell)
            if v is not None and abs(abs(v) - target) < MATCH_EPS:
                hits.append((i, j))
    return hits


@dataclass(frozen=True)
class Trace:
    n_literals: int
    all_found: bool    # every literal matches >= 1 cell
    exactly_one: bool  # every literal matches exactly 1 cell


def trace(literals: Sequence[str], table: Table) -> Trace | None:
    """None when the example is outside the base (no literals or empty table)."""
    if not literals or not table:
        return None
    hits = [locate(lit, table) for lit in literals]
    return Trace(
        n_literals=len(literals),
        all_found=all(hits),
        exactly_one=all(len(h) == 1 for h in hits),
    )


# ---------------------------------------------------------------- editing (used by Stage 4)


def _format_token(token: str, value: float) -> str:
    """Write value in the style of token: same decimals, commas if the token had them."""
    decimals = len(token.split(".")[1]) if "." in token else 0
    text = f"{value:,.{decimals}f}" if "," in token else f"{value:.{decimals}f}"
    return text


def format_like(cell: str, new_shown: float) -> str:
    """Rewrite a cell's number in place, keeping its style.

    "$ 6305" -> "$ 6405"; "26.2% ( 26.2 % )" -> "27.0% ( 27.0 % )";
    "-23158 ( 23158 )" -> "-20000 ( 20000 )" (the first number carries the sign,
    repeated copies in parentheses are written as absolute values).
    """
    tokens = list(NUMBER_TOKEN.finditer(cell))
    if not tokens:
        raise ValueError(f"no number in cell {cell!r}")
    first = float(tokens[0].group().replace(",", ""))
    out, pos = [], 0
    for k, m in enumerate(tokens):
        if k > 0 and abs(abs(float(m.group().replace(",", ""))) - abs(first)) > MATCH_EPS:
            continue  # an unrelated number in the same cell: leave it
        out.append(cell[pos:m.start()])
        out.append(_format_token(m.group(), new_shown if k == 0 else abs(new_shown)))
        pos = m.end()
    out.append(cell[pos:])
    return "".join(out)


def edit_cell(table: Table, row: int, col: int, new_shown: float) -> list[list[str]]:
    """Copy of table with one body cell's number replaced (as written, e.g. 27.0 for "27.0%")."""
    if row < 1 or col < 1:
        raise ValueError("only body cells can be edited (row >= 1, col >= 1)")
    new = [list(r) for r in table]
    new[row][col] = format_like(new[row][col], new_shown)
    return new
