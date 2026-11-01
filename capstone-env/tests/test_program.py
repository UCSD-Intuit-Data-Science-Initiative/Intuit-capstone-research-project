"""Program parser/executor and cell tracing, on hand-checked examples."""

from __future__ import annotations

import pytest

from capstone.data.cells import first_number, locate, trace
from capstone.data.program import (
    ProgramError, answers_match, execute, is_number_literal, literal_value, literals, parse,
)

# Aon 2009 segment revenue, the data plan's worked example (ConvFinQA Single_AON/2009/page_46.pdf-3)
AON_TABLE = [
    ["", "2009", "2008"],
    ["revenue", "$ 6305", "$ 6197"],
    ["operating income", "900", "880"],
]


# ---------------------------------------------------------------- parse


def test_parse_multistep_program():
    p = parse("subtract(6305, 6197), divide(#0, 6197)")
    assert [s.op for s in p.steps] == ["subtract", "divide"]
    assert p.steps[1].args == ("#0", "6197")
    assert p.is_multistep and not p.is_selection


def test_parse_number_selection_turn():
    p = parse("6305")
    assert p.is_selection and p.selection == "6305" and p.steps == ()
    assert not p.is_multistep


@pytest.mark.parametrize("bad", [
    "", "hello", "subtract(1, 2) garbage", "frobnicate(1, 2)", "add(1)", "add(1, 2, 3)",
    "subtract(1, 2) divide(#0, 2)",  # missing comma between steps
])
def test_parse_rejects_malformed(bad):
    with pytest.raises(ProgramError):
        parse(bad)


# ---------------------------------------------------------------- literals / constants


@pytest.mark.parametrize("tok,value", [
    ("6305", 6305.0), ("-1.5", -1.5), ("4.02%", 0.0402), ("const_100", 100.0),
    ("const_m1", -1.0), ("const_1000", 1000.0),
])
def test_literal_value(tok, value):
    assert literal_value(tok) == pytest.approx(value)


def test_literals_skip_refs_constants_and_row_names():
    p = parse("table_sum(net revenue, none), add(#0, 5.5), multiply(#1, const_100), divide(4.02%, 2)")
    assert literals(p) == ["5.5", "4.02%", "2"]


def test_selection_literal_only_when_asked():
    p = parse("6305")
    assert literals(p) == []  # Nov 1 quirk, kept for comparison
    assert literals(p, include_selection=True) == ["6305"]


def test_number_literal_pattern():
    assert is_number_literal("12.5%") and is_number_literal("-3")
    assert not is_number_literal("#0") and not is_number_literal("const_100")


# ---------------------------------------------------------------- execute


def test_aon_growth_is_1_74_percent():
    assert execute("subtract(6305, 6197), divide(#0, 6197)") == pytest.approx(0.017428, abs=1e-6)


def test_hypothetical_value_recomputes():
    # data plan 5a: "what if 2009 revenue were 6,150?" -> -0.76%
    assert execute("subtract(6150, 6197), divide(#0, 6197)") == pytest.approx(-0.00758, abs=1e-5)


def test_percent_literal_and_constants():
    assert execute("multiply(50%, const_100)") == pytest.approx(50.0)
    assert execute("multiply(3, const_m1)") == -3.0
    assert execute("exp(2, 3)") == 8.0


def test_greater_returns_yes_no():
    assert execute("greater(5, 3)") == "yes"
    assert execute("greater(3, 5)") == "no"


def test_selection_turn_executes_to_its_number():
    assert execute("6305") == 6305.0


def test_table_ops_use_named_row():
    assert execute("table_sum(revenue, none)", AON_TABLE) == 6305 + 6197
    assert execute("table_average(revenue, none)", AON_TABLE) == (6305 + 6197) / 2
    assert execute("table_max(Operating Income, none)", AON_TABLE) == 900  # case-insensitive
    assert execute("table_min(operating income, none), subtract(#0, 80)", AON_TABLE) == 800


@pytest.mark.parametrize("bad,msg", [
    ("divide(1, 0)", "division by zero"),
    ("add(#1, 2)", "does not exist"),
    ("greater(2, 1), add(#0, 1)", "yes/no"),
    ("table_sum(missing row, none)", "no table row"),
])
def test_execute_fails_loudly(bad, msg):
    with pytest.raises(ProgramError, match=msg):
        execute(bad, AON_TABLE)


# ---------------------------------------------------------------- tolerance


def test_tolerance_relative_for_large_absolute_for_small():
    assert answers_match(1000.9, 1000.0)       # 0.9 <= 0.001 * 1000
    assert not answers_match(1001.1, 1000.0)
    assert answers_match(0.0179, 0.0174)       # small gold: absolute 0.001
    assert not answers_match(0.0185, 0.0174)


def test_yes_no_must_match_exactly():
    assert answers_match("yes", "yes")
    assert not answers_match("yes", "no")
    assert not answers_match(1.0, "yes")


# ---------------------------------------------------------------- cells / tracing


@pytest.mark.parametrize("cell,value", [
    ("$ 6305", 6305.0), ("14.3% ( 14.3 % )", 14.3), ("-23158 ( 23158 )", -23158.0),
    ("1,234.5", 1234.5), ("$ (12)", 12.0), ("n/a", None), ("", None),
])
def test_first_number_handles_known_cell_traps(cell, value):
    assert first_number(cell) == value


def test_locate_skips_header_row_and_label_column():
    table = [["2009", "2009", "x"], ["2009", "5", "6305"], ["rev", "6305", "7"]]
    assert locate("2009", table) == []           # only in header / label cells
    assert locate("6305", table) == [(1, 2), (2, 1)]


def test_locate_ignores_sign_and_compares_percent_as_written():
    table = [["", "a"], ["r", "-23158 ( 23158 )"], ["p", "4.02%"]]
    assert locate("23158", table) == [(1, 1)]
    assert locate("4.02%", table) == [(2, 1)]


def test_trace_counts_found_and_unique():
    t = trace(["6305", "6197"], AON_TABLE)
    assert t.all_found and t.exactly_one
    dup = [["", "a", "b"], ["r", "5", "5"]]
    t = trace(["5"], dup)
    assert t.all_found and not t.exactly_one
    t = trace(["5", "99"], dup)
    assert not t.all_found and not t.exactly_one


def test_trace_outside_base():
    assert trace([], AON_TABLE) is None
    assert trace(["6305"], []) is None
