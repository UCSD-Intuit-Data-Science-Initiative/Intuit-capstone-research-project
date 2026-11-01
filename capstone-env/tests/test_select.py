"""Stage 3 selection: metric classification, table checks, cut versions, split, gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capstone.data import select as S
from capstone.data.program import parse

REPO_ROOT = Path(__file__).resolve().parents[1]
TABLE = [
    ["", "2009", "2008"],
    ["revenue", "$ 6305", "$ 6197"],
    ["operating income", "900", "880"],
]


def m(text):
    return S.classify_metric(parse(text))


# ---------------------------------------------------------------- metric classification


def test_growth_rate_shapes():
    g = m("subtract(6305, 6197), divide(#0, 6197)")
    assert (g.type, g.scale) == ("growth_rate", "fraction")
    assert g.numerator_inputs == ("6305", "6197") and g.denominator_inputs == ("6197",)
    assert m("subtract(6305, 6197), divide(#0, 6197), multiply(#1, const_100)").scale == "percent"
    g = m("divide(6305, 6197), subtract(#0, const_1)")  # a / b - 1
    assert g.type == "growth_rate" and g.numerator_inputs == ("6305",) and g.denominator_inputs == ("6197",)


def test_ratio_plain_and_composite():
    r = m("divide(900, 6305)")
    assert r.type == "ratio" and r.numerator_inputs == ("900",) and r.denominator_inputs == ("6305",)
    share = m("add(900, 880), divide(900, #0)")  # a / (a + b)
    assert share.type == "ratio"
    assert share.numerator_inputs == ("900",) and share.denominator_inputs == ("900", "880")
    margin = m("subtract(6305, 900), divide(#0, 6305)")  # (a - b) / a
    assert margin.type == "ratio" and margin.denominator_inputs == ("6305",)
    other = m("subtract(6305, 6197), divide(#0, 900)")  # (a - b) / c
    assert other.type == "ratio" and other.inputs == ("6305", "6197", "900")
    assert m("add(1, 2), add(#0, 3), divide(#1, 4)").numerator_inputs == ("1", "2", "3")


def test_difference_and_roles():
    d = m("subtract(6305, 6197)")
    assert (d.type, d.scale) == ("difference", "amount")
    assert d.roles() == {"minuend": ("6305",), "subtrahend": ("6197",)}


@pytest.mark.parametrize("text", [
    "add(759, 808), add(#0, 874), divide(#1, const_3)",   # average: has a constant
    "subtract(258.82, const_100), divide(#0, const_100)",  # index vs base 100
    "add(1, 2), divide(3, 4)",                              # first step unused
    "add(1, 2), add(#0, 3)",                                # sum, not a ratio
    "subtract(5, 2), multiply(#0, const_100)",              # difference x100 is not a metric
    "multiply(2, 3)",
    "6305",                                                 # selection turn
])
def test_not_a_policy_metric(text):
    assert m(text) is None


def test_metric_parts_reads_division_step():
    prog = parse("add(900, 880), divide(900, #0)")
    assert S.metric_parts(prog, S.classify_metric(prog), TABLE) == (900, 1780)
    prog = parse("subtract(6305, 6197)")
    assert S.metric_parts(prog, S.classify_metric(prog), TABLE) == (None, None)


# ---------------------------------------------------------------- table checks


TOTALS = [
    ["", "2009", "2008", "total"],
    ["us", "100", "90", "190"],
    ["intl", "50", "40", "90"],
    ["total revenue", "150", "130", "280"],
    ["headcount", "7", "8", "9"],
]


def test_total_problem():
    assert S.total_problem(TOTALS, 3, 1) == "input_is_total"     # total row
    assert S.total_problem(TOTALS, 1, 3) == "input_is_total"     # total column
    assert S.total_problem(TOTALS, 1, 1) == "input_feeds_total"  # 100 + 50 = 150 (and 100 + 90 = 190)
    # headcount sits below the total row and 7 + 8 != 9 in its total column -> safe
    assert S.total_problem(TOTALS, 4, 1) is None
    # column total only: rows do not sum to a total row, but the row sums to its 'total' column
    col_only = [["", "a", "b", "total"], ["x", "1", "2", "3"], ["y", "10", "20", "99"]]
    assert S.total_problem(col_only, 1, 1) == "input_feeds_total"
    assert S.total_problem(col_only, 2, 1) is None
    assert S.total_problem(TABLE, 1, 1) is None


def test_irrelevant_cells_excludes_inputs_percent_and_totals():
    table = [["", "a", "b"], ["x", "6305", "6197"], ["y", "900", "12%"], ["z", "6305", "5"]]
    cells = S.irrelevant_cells(table, {(1, 1), (1, 2)}, {6305.0, 6197.0})
    assert cells == [(2, 1), (3, 2)]  # 12% is a percent; (3, 1) equals an input value


def test_page_unit():
    e = {"table": [["", "2009"]], "pre_text": ["( in millions )"], "post_text": []}
    assert S.page_unit(e) == "millions"
    assert S.page_unit({**e, "pre_text": []}) is None
    assert S.page_unit({**e, "post_text": ["shares in thousands"]}) == ("millions", "thousands")


# ---------------------------------------------------------------- cut versions


def conv(id_, turns, answers, qa_answer="1.7%", qa_exe=0.01743, table=TABLE, unit=True, type_ii=False):
    e = {"id": id_, "table": table, "pre_text": ["( dollars in millions )"] if unit else [], "post_text": [],
         "annotation": {"turn_program": turns, "exe_ans_list": answers,
                        "dialogue_break": [f"q{k}" for k in range(len(turns))]}}
    if type_ii:
        e["qa_0"] = e["qa_1"] = {"answer": qa_answer, "exe_ans": qa_exe}
    else:
        e["qa"] = {"answer": qa_answer, "exe_ans": qa_exe}
    return e


GROWTH_TURNS = ["6305", "6197", "subtract(6305, 6197)", "subtract(6305, 6197), divide(#0, 6197)"]
GROWTH_ANS = [6305, 6197, 108, 0.01743]


def test_cut_at_first_plain_subtract_from_turn_3():
    kept, cut = S.cut_at_difference(conv("Single_B/2009/p.pdf-1", GROWTH_TURNS, GROWTH_ANS))
    assert kept == 3
    assert cut["annotation"]["turn_program"] == GROWTH_TURNS[:3]
    assert cut["annotation"]["exe_ans_list"] == GROWTH_ANS[:3]
    assert len(cut["annotation"]["dialogue_break"]) == 3
    # subtract only at turn 2, or only as the last turn -> no cut
    assert S.cut_at_difference(conv("x", ["6305", "subtract(6305, 6197)", "divide(#0, 1)"], [0, 0, 0])) is None
    assert S.cut_at_difference(conv("x", ["6305", "6197", "subtract(6305, 6197)"], [0, 0, 108])) is None


# ---------------------------------------------------------------- end to end on synthetic records


def test_select_end_to_end():
    convs = [
        ("train", conv("Single_PIL/2009/p.pdf-1", GROWTH_TURNS, GROWTH_ANS)),
        ("train", conv("Single_HLD/2009/p.pdf-1", GROWTH_TURNS, GROWTH_ANS)),
        ("dev", conv("Single_HLD/2010/p.pdf-1", GROWTH_TURNS, GROWTH_ANS, unit=False)),
        ("dev", conv("Single_PIL/2010/p.pdf-1", GROWTH_TURNS, GROWTH_ANS, qa_answer="380")),
        ("dev", conv("Double_PIL/2011/p.pdf", GROWTH_TURNS, GROWTH_ANS, type_ii=True)),
    ]
    rows, funnels = S.select(convs, {"PIL": "pilot", "HLD": "heldout"})
    by_id = {r["id"]: r for r in rows}
    assert len(rows) == 7  # 5 full + 2 cut versions (held-out only)

    g = by_id["Single_PIL/2009/p.pdf-1"]
    assert (g["status"], g["metric_type"], g["metric_scale"], g["split"]) == \
        ("candidate", "growth_rate", "fraction", "pilot")
    assert [d["literal"] for d in json.loads(g["numerator_inputs"])] == ["6305", "6197"]
    den = json.loads(g["denominator_inputs"])
    assert den == [{"literal": "6197", "row": 1, "col": 2, "row_label": "revenue",
                    "col_label": "2008", "cell": "$ 6197"}]
    assert g["truncated_at"] == "" and g["n_irrelevant_cells"] == 2

    d = by_id["Single_HLD/2009/p.pdf-1|cut@3"]
    assert (d["status"], d["metric_type"], d["metric_scale"], d["unit"], d["truncated_at"]) == \
        ("candidate", "difference", "amount", "millions", 3)
    assert d["source_id"] == "Single_HLD/2009/p.pdf-1" and d["n_turns"] == 3
    assert [x["role"] for x in json.loads(d["inputs"])] == ["minuend", "subtrahend"]

    assert by_id["Single_HLD/2010/p.pdf-1|cut@3"]["drop_reason"] == "unit_unknown"
    assert by_id["Single_HLD/2010/p.pdf-1"]["status"] == "candidate"  # growth needs no unit
    assert by_id["Single_PIL/2010/p.pdf-1"]["drop_reason"] == "written_answer_disagree"
    assert by_id["Double_PIL/2011/p.pdf"]["drop_reason"] == "type_ii"

    for funnel in funnels.values():  # every conversation is accounted for at every stage
        for a, b in zip(funnel, funnel[1:]):
            assert a["in"] == a["passed"] + sum(a["dropped"].values())
            assert b["in"] == a["passed"]


def test_difference_dropped_on_pilot_side():
    convs = [("train", conv("Single_PIL/2009/p.pdf-1", GROWTH_TURNS[:3], GROWTH_ANS[:3]))]
    rows, _ = S.select(convs, {"PIL": "pilot"})
    assert rows[0]["drop_reason"] == "difference_reserved_for_heldout"


def test_degenerate_metrics_dropped():
    zero = conv("Single_A/2009/p.pdf-1", ["6305", "6197", "subtract(6305, 6305), divide(#0, 6305)"],
                [6305, 6197, 0.0], qa_answer="0%", qa_exe=0.0,
                table=[["", "a", "b"], ["r", "6305", "6197"], ["s", "1", "2"]])
    rows, _ = S.select([("train", zero)], {"A": "pilot"})
    assert rows[0]["drop_reason"] == "metric_zero"
    huge = conv("Single_A/2009/p.pdf-2", ["50000", "2", "subtract(50000, 2), divide(#0, 2)"],
                [50000, 2, 24999.0], qa_answer="2499900%", qa_exe=24999.0,
                table=[["", "a", "b"], ["r", "50000", "2"], ["s", "7", "8"]])
    rows, _ = S.select([("train", huge)], {"A": "pilot"})
    assert rows[0]["drop_reason"] == "absurd_magnitude"


# ---------------------------------------------------------------- split and gate


def test_assign_splits_deterministic_disjoint_and_nested():
    cos = [f"C{k}" for k in range(50)]
    a, b = S.assign_splits(cos), S.assign_splits(list(reversed(cos)))
    assert a == b  # order of input does not matter
    held = {c for c, s in a.items() if s == "heldout"}
    assert len(held) == round(S.HELDOUT_FRACTION * 50)
    held20 = {c for c, s in S.assign_splits(cos, heldout_fraction=0.2).items() if s == "heldout"}
    assert held20 < held  # raising the share only adds companies


def _row(side, metric, company):
    return {"status": "candidate", "split": side, "metric_type": metric, "company": company}


def test_gate_counts_companies_per_required_metric():
    rows = [_row("pilot", "growth_rate", f"G{k}") for k in range(40)] + \
           [_row("pilot", "ratio", f"R{k % 9}") for k in range(30)] + \
           [_row("heldout", "growth_rate", f"H{k}") for k in range(20)] + \
           [_row("heldout", "difference", f"D{k}") for k in range(12)] + \
           [_row("heldout", "ratio", "X")]  # held-out ratios are not gated
    sc = S.split_counts(rows)
    assert not sc["pilot"]["passes_gate"] and sc["pilot"]["gate_problems"] == ["ratio: 9 companies < 10"]
    assert sc["heldout"]["passes_gate"]
    assert sc["pilot"]["by_metric"]["ratio"] == {"candidates": 30, "companies": 9}


# ---------------------------------------------------------------- real data


@pytest.mark.skipif(not (REPO_ROOT / "data/raw/convfinqa/data/train.json").exists(),
                    reason="raw data not fetched (run `make data`)")
def test_real_data_split_passes_gate():
    raw = REPO_ROOT / "data/raw/convfinqa/data"
    convs = [(s, e) for s in ("train", "dev") for e in json.loads((raw / f"{s}.json").read_text())]
    splits = S.assign_splits([S.company_of(e["id"]) for _, e in convs])
    rows, _ = S.select(convs, splits)
    sc = S.split_counts(rows)
    assert sc["pilot"]["passes_gate"] and sc["heldout"]["passes_gate"], sc
    cands = [r for r in rows if r["status"] == "candidate"]
    pilot_cos = {r["company"] for r in cands if r["split"] == "pilot"}
    held_cos = {r["company"] for r in cands if r["split"] == "heldout"}
    assert not pilot_cos & held_cos
    for r in cands:
        assert r["metric_scale"] in ("fraction", "percent", "amount") and r["inputs"]
        if r["metric_type"] == "difference":
            assert r["split"] == "heldout" and r["truncated_at"] >= 3 and r["unit"]
        else:
            assert r["numerator_inputs"] and r["denominator_inputs"]
