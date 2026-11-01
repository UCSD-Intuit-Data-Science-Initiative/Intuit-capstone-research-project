"""The single percent rule (Stage 3 note), cell edits, and recomputation after an edit."""

from __future__ import annotations

import pytest

from capstone.data.cells import cell_number, cell_value, edit_cell, format_like, locate
from capstone.data.program import ProgramError, evaluate_steps, execute, parse, substitute

MARGIN_TABLE = [
    ["", "2009", "2008"],
    ["revenue", "$ 6305", "$ 6197"],
    ["operating margin", "26.2% ( 26.2 % )", "25.0% ( 25.0 % )"],
]


# ---------------------------------------------------------------- one percent rule


def test_dataset_rule_reads_percent_cells_as_fractions():
    assert cell_value("26.2% ( 26.2 % )") == pytest.approx(0.262)
    assert cell_value("$ 6305") == 6305
    n = cell_number("26.2% ( 26.2 % )")
    assert n.is_percent and n.shown == 26.2 and n.value == pytest.approx(0.262)


def test_nov1_rule_kept_for_the_profile():
    assert cell_value("26.2% ( 26.2 % )", rule="nov1") == 26.2


def test_cells_and_program_agree_on_percent():
    # a '%' literal and a '%' cell mean the same number under the dataset rule
    from_literal = execute("add(26.2%, const_0)")
    from_cell = execute("table_max(operating margin, none)", MARGIN_TABLE)
    assert from_literal == pytest.approx(from_cell) == pytest.approx(0.262)
    assert execute("table_max(operating margin, none)", MARGIN_TABLE, percent_rule="nov1") == 26.2


# ---------------------------------------------------------------- editing


@pytest.mark.parametrize("cell,new,expected", [
    ("$ 6305", 6405, "$ 6405"),
    ("6305", 6205, "6205"),
    ("1,234.5", 2000, "2,000.0"),
    ("26.2% ( 26.2 % )", 27.0, "27.0% ( 27.0 % )"),
    ("-23158 ( 23158 )", -20000, "-20000 ( 20000 )"),
    ("12 ( 5 )", 13, "13 ( 5 )"),  # unrelated second number left alone
])
def test_format_like_keeps_cell_style(cell, new, expected):
    assert format_like(cell, new) == expected


def test_edit_cell_copies_and_rejects_header():
    edited = edit_cell(MARGIN_TABLE, 1, 1, 6405)
    assert edited[1][1] == "$ 6405" and MARGIN_TABLE[1][1] == "$ 6305"
    with pytest.raises(ValueError):
        edit_cell(MARGIN_TABLE, 0, 1, 1)
    with pytest.raises(ValueError):
        format_like("n/a", 1)


def test_edit_percent_cell_and_recompute():
    """Stage 4 edits percent cells: the table op, the literal program and tracing must all follow."""
    edited = edit_cell(MARGIN_TABLE, 2, 1, 27.0)
    assert edited[2][1] == "27.0% ( 27.0 % )"
    # table op reads the edited cell as a fraction
    assert execute("table_average(operating margin, none)", MARGIN_TABLE) == pytest.approx(0.256)
    assert execute("table_average(operating margin, none)", edited) == pytest.approx(0.26)
    # a program that copied the cell as written follows via substitute
    prog = substitute("subtract(26.2, 25.0)", "26.2", "27.0")
    assert prog.text == "subtract(27.0, 25.0)" and execute(prog) == pytest.approx(2.0)
    prog = substitute("subtract(26.2%, 25.0%)", "26.2%", "27.0%")
    assert execute(prog) == pytest.approx(0.02)
    # tracing finds the new value in exactly one cell and the old one nowhere
    assert locate("27.0", edited) == [(2, 1)]
    assert locate("26.2", edited) == []


def test_edit_number_cell_flips_growth():
    """Data plan version 3: preliminary 6,205 gives 0.13% growth, the audited 6,305 gives 1.74%."""
    prog = "subtract(6305, 6197), divide(#0, 6197)"
    assert execute(prog) == pytest.approx(0.01743, abs=1e-5)
    assert execute(substitute(prog, "6305", "6205")) == pytest.approx(0.00129, abs=1e-5)


# ---------------------------------------------------------------- program helpers


def test_substitute_leaves_refs_constants_and_row_names():
    p = substitute("table_sum(6305, none), add(6305, #0), multiply(#1, const_100)", "6305", "1")
    assert p.text == "table_sum(6305, none), add(1, #0), multiply(#1, const_100)"
    assert substitute("6305", "6305.0", "7").text == "7"
    with pytest.raises(ProgramError):
        substitute("add(1, 2)", "#0", "3")


def test_evaluate_steps_returns_every_result():
    assert evaluate_steps("subtract(10, 4), divide(#0, 4)") == [6.0, 1.5]
    assert parse("subtract(10, 4), divide(#0, 4)").is_multistep
