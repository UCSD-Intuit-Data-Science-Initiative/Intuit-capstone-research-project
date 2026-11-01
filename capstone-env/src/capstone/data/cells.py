"""Table cells: reading a number out of a cell, and tracing program inputs to cells.

Tracing rule (Nov 1 definition, kept for comparability):
  * only body cells are searched: rows table[1:] (header skipped), columns row[1:] (row label skipped);
  * a cell's number is the FIRST number in it after removing '$' and ',', so
    "$ 6305" -> 6305, "14.3% ( 14.3 % )" -> 14.3, "-23158 ( 23158 )" -> -23158;
  * a literal matches a cell when their ABSOLUTE values are equal (within 1e-9);
  * a '%' literal is compared as written ("4.02%" -> 4.02), not divided by 100.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

CELL_NUMBER = re.compile(r"-?\d+\.?\d*")
MATCH_EPS = 1e-9

Table = Sequence[Sequence[str]]


def first_number(cell: str) -> float | None:
    m = CELL_NUMBER.search(cell.replace("$", "").replace(",", ""))
    return float(m.group()) if m else None


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
