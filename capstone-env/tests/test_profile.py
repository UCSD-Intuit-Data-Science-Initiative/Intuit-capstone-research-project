"""Profile aggregation on synthetic records, plus the real-data check against expected numbers."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from capstone.data import profile as P

REPO_ROOT = Path(__file__).resolve().parents[1]
TABLE = [["", "2009", "2008"], ["revenue", "$ 6305", "$ 6197"], ["margin", "26.2%", "25.0%"]]


def finqa(id_, program, ans, gold_inds=None, table=TABLE):
    return {"id": id_, "table": table,
            "qa": {"program": program, "exe_ans": ans, "gold_inds": gold_inds or {"table_1": ""}}}


def conv(id_, programs, answers, table=TABLE, type_ii=False):
    e = {"id": id_, "table": table,
         "annotation": {"turn_program": programs, "exe_ans_list": answers,
                        "dialogue_break": [f"q{i}" for i in range(len(programs))]}}
    e["qa_0" if type_ii else "qa"] = {}
    return e


# ---------------------------------------------------------------- FinQA


def test_profile_finqa_counts():
    splits = {
        "train": [
            finqa("AON/2009/page_46.pdf-1", "subtract(6305, 6197), divide(#0, 6197)", 0.01743),
            finqa("AON/2009/page_46.pdf-2", "greater(6305, 6197)", "yes", {"text_1": ""}),
        ],
        "dev": [finqa("ABC/2012/page_1.pdf-1", "table_max(margin, none)", 0.262,
                      {"table_1": "", "text_2": ""})],
        "test": [finqa("ABC/1999/page_2.pdf-1", "add(1, 2)", 3, table=[])],
        "private_test": [{}, {}],
    }
    s, rows = P.profile_finqa(splits)
    assert s["finqa_split_counts"] == {"train": 2, "dev": 1, "test": 1, "private_test": 2}
    assert s["finqa_labelled"] == 4
    assert s["finqa_companies"] == 2 and s["finqa_years"] == "1999-2012"
    assert s["finqa_ops"] == {"subtract": 1, "divide": 1, "greater": 1, "table_max": 1, "add": 1}
    assert s["finqa_steps"] == {1: 3, 2: 1}
    assert s["finqa_reexec_match"] == 3  # table_max reads 26.2, gold is 0.262
    assert s["finqa_evidence"] == {"table": 2, "text": 1, "both": 1}
    # base: has literals AND a table -> rows 1, 2 (row 3 is a table op only, row 4 has no table)
    assert s["finqa_trace"] == {"base": 2, "all_found": 2, "exactly_one": 2}
    fail = [r for r in rows if not r["reexec_ok"]]
    assert [r["failure_cause"] for r in fail] == ["percent_cells"]


def test_evidence_location():
    assert P.evidence_location({"table_1": "", "table_4": ""}) == "table"
    assert P.evidence_location({"text_0": ""}) == "text"
    assert P.evidence_location({"table_1": "", "text_0": ""}) == "both"


def test_failure_cause_duplicate_row():
    table = [["", "a"], ["fourth quarter", "10"], ["fourth quarter", "20"]]
    prog = P.parse("table_max(fourth quarter, none)")
    assert P.failure_cause(prog, table) == "duplicate_row_name"


# ---------------------------------------------------------------- ConvFinQA


def test_profile_convfinqa_counts_pool_and_selection_variant():
    splits = {
        "train": [
            # pool: 3 turns, inputs trace to one cell each, later turn has an op
            conv("c1", ["6305", "6197", "subtract(6305, 6197), divide(#0, 6197)"],
                 [6305, 6197, 0.01743]),
            # 2 turns -> not pool
            conv("c2", ["6305", "subtract(6305, 6197)"], [6305, 108], type_ii=True),
        ],
        "dev": [
            # selected 99 is never used by an op turn: invisible to Nov 1 tracing, fails with selection
            conv("c3", ["99", "6197", "add(6305, 6197)"], [99, 6197, 12502]),
        ],
        "test_private": [{}],
    }
    s, pl, conv_rows, turn_rows = P.profile_convfinqa(splits)
    assert s["conv_labelled"] == 3 and s["conv_split_counts"]["test_private"] == 1
    assert s["conv_types"] == {"I": 2, "II": 1}
    assert s["conv_turns_per_conv"] == {2: 1, 3: 2}
    assert s["conv_turns"] == 8 and s["conv_reexec_match"] == 8
    assert s["conv_number_selection_turns"] == 5
    assert s["conv_multistep_turns(#)"] == 1
    assert s["conv_trace"] == {"base": 3, "all_found": 3, "exactly_one": 3}
    assert s["conv_pool"] == {"pool": 2, "of": 3}
    assert pl["conv_trace_with_selection"] == {"base": 3, "all_found": 2, "exactly_one": 2}
    assert pl["conv_pool_with_selection"] == {"pool": 1, "of": 3}
    assert pl["conv_selection_values_not_in_ops"] == 1
    assert pl["conv_turn_length_mismatches"] == 0


def test_pool_requires_an_op_after_first_turn():
    splits = {"train": [conv("c", ["subtract(6305, 6197)", "6305", "6197"], [108, 6305, 6197])],
              "dev": [], "test_private": []}
    s, *_ = P.profile_convfinqa(splits)
    assert s["conv_pool"]["pool"] == 0


# ---------------------------------------------------------------- comparison


def test_compare_flags_differs_missing_extra():
    rows = {r["field"]: r["status"] for r in P.compare(
        {"a": 1, "b": {"x": 2, "y": 3}, "new": 0},
        {"a": 1, "b": {"x": 2, "y": 4}, "gone": 5})}
    assert rows == {"a": "match", "b.x": "match", "b.y": "differs", "gone": "missing", "new": "extra"}


def test_compare_int_keys_from_yaml_match_counter_keys():
    assert P.compare({"steps": {1: 5}}, yaml.safe_load("steps: {1: 5}"))[0]["status"] == "match"


# ---------------------------------------------------------------- real data


@pytest.mark.skipif(not (REPO_ROOT / "data/raw/finqa/dataset/train.json").exists(),
                    reason="raw data not fetched (run `make data`)")
def test_real_data_reproduces_every_expected_number(tmp_path):
    """Our code on the pinned data must equal data/profile_expected.yaml in every field."""
    import json
    raw = REPO_ROOT / "data/raw"
    fin = {s: json.loads((raw / f"finqa/dataset/{s}.json").read_text())
           for s in P.FINQA_LABELLED + P.FINQA_UNLABELLED}
    cv = {s: json.loads((raw / f"convfinqa/data/{s}.json").read_text())
          for s in P.CONV_LABELLED + P.CONV_UNLABELLED}
    ours = {**P.profile_finqa(fin)[0], **P.profile_convfinqa(cv)[0]}
    expected = yaml.safe_load((REPO_ROOT / "data/profile_expected.yaml").read_text())
    bad = [r for r in P.compare(ours, expected) if r["status"] != "match"]
    assert not bad, bad


def test_expected_file_carries_the_documented_nov1_numbers():
    """Guards profile_expected.yaml against edits: these are the numbers in CLAUDE.md / data plan."""
    x = yaml.safe_load((REPO_ROOT / "data/profile_expected.yaml").read_text())
    assert x["finqa_labelled"] == 8281 and x["finqa_companies"] == 137
    assert x["finqa_reexec_match"] == 8249
    assert x["finqa_trace"]["base"] == 8006 and x["finqa_trace"]["exactly_one"] == 4477
    assert x["finqa_evidence"] == {"table": 5170, "text": 1939, "both": 1172}
    assert x["conv_labelled"] == 3458 and x["conv_turns"] == 12594
    assert x["conv_types"] == {"I": 2409, "II": 1049}
    assert x["conv_multistep_turns(#)"] == 3704 and x["conv_number_selection_turns"] == 4398
    assert x["conv_trace"]["base"] == 3455 and x["conv_trace"]["exactly_one"] == 1754
    assert x["conv_pool"] == {"pool": 1304, "of": 3458}
