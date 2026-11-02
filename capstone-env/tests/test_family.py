"""Stage 4 family: edit checks, gold recomputation, matched versions and the action checker."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from capstone.data import family as F
from capstone.data.cells import cell_value, locate
from capstone.data.program import answers_match, execute, parse

REPO_ROOT = Path(__file__).resolve().parents[1]
FAMILIES = REPO_ROOT / "data" / "families"
FAMILY_ID = "gr01_lmt_2007_p55"
A, C = (3, 1), (3, 3)  # the metric cell (2007 backlog) and the version 4 cell (2005 backlog)

needs_data = pytest.mark.skipif(
    not (REPO_ROOT / "data/raw/convfinqa/data/train.json").exists()
    or not (REPO_ROOT / "data/interim/stage3/candidates.csv").exists(),
    reason="raw data or candidates.csv missing (run `make data`, `make select`)")

TABLE = [
    ["( in millions )", "2007", "2006", "2005"],
    ["net sales", "$ 11143", "$ 10519", "$ 9811"],
    ["operating profit", "1410", "1264", "1078"],
    ["backlog at year-end", "21200", "19700", "18600"],
]
PROGRAMS = ["21200", "19700", "subtract(21200, 19700)", "19700", "subtract(21200, 19700), divide(#0, 19700)"]
PUBLISHED = [21200.0, 19700.0, 1500.0, 19700.0, 0.07614]
RECORD = {
    "id": "Single_LMT/2007/page_55.pdf-3", "table": TABLE, "pre_text": [], "post_text": [],
    "qa": {"question": "percentage change in backlog?", "answer": "8%"},
    "annotation": {"turn_program": PROGRAMS, "exe_ans_list": PUBLISHED,
                   "dialogue_break": [f"q{k}" for k in range(len(PROGRAMS))]},
}


@pytest.fixture(scope="module")
def spec():
    return json.loads((FAMILIES / FAMILY_ID / "family.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def policy():
    return (FAMILIES / "policies" / "credit_renewal_v0.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def gold():
    return F.build(REPO_ROOT, FAMILY_ID)


@pytest.fixture(scope="module")
def synthetic(spec, policy):
    """The same family built from the table in this file, so these tests need no raw data."""
    return F.build_family(spec, RECORD, policy)


def changed(spec, edit):
    new = copy.deepcopy(spec)
    edit(new)
    return new


def with_text(*sentences):
    return {**RECORD, "post_text": list(sentences)}


# ---------------------------------------------------------------- hidden sums and derived cells


def test_hidden_sum_catches_a_plus_b_equals_c():
    # Fortive: sales + cost of sales = gross profit, with no 'total' label
    table = [["", "2016"], ["sales", "$ 6224.3"], ["cost of sales", "-3191.5 ( 3191.5 )"], ["gross profit", "3032.8"]]
    assert F.hidden_sums(table, 1, 1)


def test_hidden_sum_uses_the_rounding_tolerance():
    table = [["", "a"], ["x", "100"], ["y", "50"], ["z", "151"]]  # 100 + 50 is 1 away from 151
    assert F.hidden_sums(table, 1, 1)
    table = [["", "a"], ["x", "100"], ["y", "50"], ["z", "157"]]
    assert not F.hidden_sums(table, 1, 1)


def test_hidden_sum_catches_a_change_column_in_the_row():
    table = [["", "2017", "2016", "$"], ["operating income", "11503", "10815", "688"]]
    assert any(h.startswith("row") for h in F.hidden_sums(table, 1, 1))


def test_hidden_sum_catches_longer_unlabelled_sums():
    # Intel: operating + investing + financing + fx = net change, no 'total' label
    table = [["", "2013"], ["operating", "$ 20776"], ["investing", "-18073 ( 18073 )"],
             ["financing", "-5498 ( 5498 )"], ["fx", "-9 ( 9 )"], ["net change", "$ -2804 ( 2804 )"]]
    assert any("column" in h for h in F.hidden_sums(table, 1, 1))


def test_hidden_sum_fails_closed_on_a_line_too_long_to_check():
    table = [["", "a"]] + [[f"r{k}", str(1000 + 37 * k)] for k in range(F.MAX_LINE + 2)]
    assert any("too many" in h for h in F.hidden_sums(table, 1, 1))


def test_hidden_sum_checks_the_new_value_too():
    # 21200 is safe, but a new value of 30343 would equal 20000 + 10343
    table = [["", "a"], ["x", "21200"], ["y", "20000"], ["z", "10343"]]
    assert not F.hidden_sums(table, 1, 1)
    assert F.hidden_sums(table, 1, 1, 30343.0)


def test_clean_cells_have_no_hits():
    assert not F.hidden_sums(TABLE, *A) and not F.hidden_sums(TABLE, *C)
    assert not F.derived_percent_hits(TABLE, *A)


def test_derived_percent_catches_a_margin_row():
    # The Aon worked example: passes a ± b = c, but the margin row is 900 / 6305
    table = [["", "2009", "2008"], ["segment revenue", "$ 6305", "$ 6197"],
             ["segment operating income", "900", "846"], ["margin", "14.3% ( 14.3 % )", "13.7% ( 13.7 % )"]]
    assert not F.hidden_sums(table, 1, 1)
    assert F.derived_percent_hits(table, 1, 1)
    assert F.cell_problems({}, table, 1, 1)


def test_derived_percent_catches_a_change_percent_in_the_row():
    table = [["", "2017", "2016", "%"], ["operating income", "11503", "10815", "6.4% ( 6.4 % )"]]
    assert F.derived_percent_hits(table, 1, 1)


def test_derived_percent_uses_shown_precision():
    table = [["", "a"], ["x", "100"], ["y", "28"], ["z", "28%"]]  # whole percent: 28 / 100 = 28%
    assert F.derived_percent_hits(table, 1, 1)
    table = [["", "a"], ["x", "100"], ["y", "27"], ["z", "28.0%"]]  # 27% is not 28.0%
    assert not F.derived_percent_hits(table, 1, 1)


# ---------------------------------------------------------------- page text


@pytest.mark.parametrize("sentence", [
    "backlog at the end of the year was 21200 .",        # the cell
    "orders brought the total to $ 21,200 million .",    # the cell, formatted, no row name needed
    "backlog reached $ 21.2 billion at year-end .",      # the cell in billions
    "backlog rose by $ 1500 million .",                  # the change
    "the contract book grew by $ 1.5 billion .",         # the change in billions
    "backlog grew 8% ( 8 % ) in 2007 .",                 # growth, rounded
    "backlog grew 7.6% ( 7.6 % ) in 2007 .",             # growth, one decimal
])
def test_page_text_restating_the_cell_is_caught(sentence):
    assert F.text_restatements(with_text(sentence), TABLE, *A)
    assert any(p.startswith("page text") for p in F.cell_problems(with_text(sentence), TABLE, *A))


def test_page_text_real_conflict_ball_2010():
    table = [["( $ in millions )", "2010", "2009"], ["net sales", "$ 1697.6", "$ 1739.5"]]
    e = {"post_text": ["segment sales in 2010 decreased $ 41.9 million compared to 2009 , primarily due to fx ."]}
    assert F.text_restatements(e, table, 1, 1)


def test_page_text_percent_counts_only_in_a_sentence_naming_the_row():
    e = with_text("net sales grew 8% ( 8 % ) in 2007 .", "operating profit rose 8% ( 8 % ) .")
    assert not F.text_restatements(e, TABLE, *A)


def test_page_text_checks_the_new_value_too():
    e = with_text("backlog grew 13% ( 13 % ) in 2007 .")  # 22200 would be 12.69%, which rounds to 13
    assert not F.text_restatements(e, TABLE, *A)
    assert F.text_restatements(e, TABLE, *A, 22200.0)


def test_row_sentences_ignores_generic_words():
    e = with_text("backlog rose .", "the year was busy .")
    assert F.row_sentences(e, "backlog at year-end") == ["backlog rose"]


# ---------------------------------------------------------------- the build stops on bad inputs


def test_build_stops_on_a_hidden_sum(spec, policy):
    table = TABLE + [["other", "10057", "1", "2"]]  # 11143 + 10057 = 21200
    with pytest.raises(F.FamilyError, match="hidden sum"):
        F.build_family(spec, {**RECORD, "table": table}, policy)


def test_build_stops_on_page_text(spec, policy):
    with pytest.raises(F.FamilyError, match="page text"):
        F.build_family(spec, with_text("backlog reached $ 21.2 billion at year-end ."), policy)


def test_build_stops_when_the_new_value_is_another_cell(spec, policy):
    bad = changed(spec, lambda s: s["edits"]["wrong"].update(value="10519"))
    with pytest.raises(F.FamilyError, match="already in cell"):
        F.build_family(bad, RECORD, policy)


def test_build_stops_on_an_edit_outside_the_metric(spec, policy):
    bad = changed(spec, lambda s: s["edits"].update(wrong={"row": 1, "col": 1, "literal": "11143", "value": "12000"}))
    with pytest.raises(F.FamilyError, match="metric input"):
        F.build_family(bad, RECORD, policy)


def test_build_stops_when_the_irrelevant_cell_is_not_offered(spec, policy):
    bad = changed(spec, lambda s: s["edits"].update(
        irrelevant={"row": 3, "col": 2, "literal": "19700", "start_value": "19000"}))
    with pytest.raises(F.FamilyError, match="irrelevant_cells"):
        F.build_family(bad, RECORD, policy)


@pytest.mark.parametrize("threshold,match", [(0.05, "same side"), (0.12, "needs"), (0.08, "needs")])
def test_build_stops_on_margin(spec, policy, threshold, match):
    bad = changed(spec, lambda s: s["decision"].update(threshold=threshold))
    with pytest.raises(F.FamilyError, match=match):
        F.build_family(bad, RECORD, policy)


def test_build_stops_on_a_fact_without_qualifying_evidence(spec, policy):
    bad = changed(spec, lambda s: s["documents"]["AUD-2007"].update(type="phone_call"))
    with pytest.raises(F.FamilyError, match="qualifying evidence"):
        F.build_family(bad, RECORD, policy)


def test_build_stops_on_a_change_after_execution(spec, policy):
    def late(s):
        turns = s["versions"]["3"]["turns"]
        turns[2]["facts"], turns[1]["facts"] = turns[1]["facts"], []
    with pytest.raises(F.FamilyError, match="before execution"):
        F.build_family(changed(spec, late), RECORD, policy)


def test_build_stops_when_5b_changes_the_record(spec, policy):
    def recorded(s):
        s["versions"]["5b"]["turns"][1]["facts"] = [
            {"cell": [3, 1], "to": "source", "evidence": "AUD-2007", "kind": "past_error"}]
    with pytest.raises(F.FamilyError, match="must not change recorded facts"):
        F.build_family(changed(spec, recorded), RECORD, policy)


def test_build_stops_when_final_facts_differ(spec, policy):
    def uncorrected(s):
        turn = s["versions"]["3"]["turns"][1]
        del turn["facts"]
        turn["programs"], turn["say"] = [], "Hold on."
    with pytest.raises(F.FamilyError, match="final facts"):
        F.build_family(changed(spec, uncorrected), RECORD, policy)


def test_build_stops_on_a_missing_version(spec, policy):
    with pytest.raises(F.FamilyError, match="versions"):
        F.build_family(changed(spec, lambda s: s["versions"].pop("5a")), RECORD, policy)


def test_build_stops_when_the_source_does_not_reexecute(spec, policy):
    bad = copy.deepcopy(RECORD)
    bad["annotation"]["exe_ans_list"][2] = 1600.0
    with pytest.raises(F.FamilyError, match="re-executes"):
        F.build_family(spec, bad, policy)


def test_program_asking_for_an_undisclosed_figure_stops_the_build(spec, policy):
    bad = changed(spec, lambda s: s["versions"]["2"]["turns"][0].update(programs=[2]))
    with pytest.raises(F.FamilyError, match="not disclosed"):
        F.build_family(bad, RECORD, policy)


def test_trace_check_rejects_a_value_in_two_cells(spec):
    src = F.read_source(RECORD, spec)
    duplicated = [list(r) for r in TABLE]
    duplicated[1][1] = duplicated[3][1] = "22200"
    prog = F.rewrite(PROGRAMS[2], src, duplicated)
    assert prog.text == "subtract(22200, 19700)"
    with pytest.raises(F.FamilyError, match="does not trace"):
        F._check_trace(PROGRAMS[2], prog, src, duplicated)


def test_rewrite_ignores_sign_like_locate():
    table = [["", "a", "b"], ["x", "-23158 ( 23158 )", "100"]]
    src = F.Source({}, table, [], [], None, {"23158": (1, 1), "100": (1, 2)})
    assert F.rewrite("subtract(23158, 100)", src, table).text == "subtract(23158, 100)"


# ---------------------------------------------------------------- threshold


def test_required_margin_takes_the_strictest_reading():
    assert F.required_margin(0.0761, 0.10) == pytest.approx(0.01)     # 10% of the threshold
    assert F.required_margin(0.1269, 0.10) == pytest.approx(0.01269)  # 10% of the value
    assert F.required_margin(0.012, 0.02) == pytest.approx(0.005)     # 0.5 percentage points


def test_margins_are_rechecked_on_the_rounded_threshold():
    assert F.round_threshold(0.106) == pytest.approx(0.105)
    assert F.round_threshold(0.10) == 0.10
    # 10.6% would hold for 9.5% (gap 1.1 pp, needs 1.06), but it rounds to 10.5% (gap 1.0, needs 1.05)
    assert 0.106 - 0.095 >= F.required_margin(0.095, 0.106)
    assert F.margin_problems(0.095, [0.125], 0.106)


def test_margin_checks_every_wrong_value():
    assert not F.margin_problems(0.0761, [0.1269, 0.1269], 0.10)
    assert F.margin_problems(0.0761, [0.1269, 0.105], 0.10)   # too close
    assert F.margin_problems(0.0761, [0.1269, 0.05], 0.10)    # same side as the true value


def test_family_margins_hold(gold):
    vals = gold["values"]
    assert gold["policy"]["threshold_rounded"] == gold["policy"]["threshold"] == 0.10
    assert vals["true_metric"] == pytest.approx(1500 / 19700)
    assert vals["wrong_metric"] == pytest.approx(2500 / 19700)
    assert not F.margin_problems(vals["true_metric"], [vals["wrong_metric"]], 0.10)
    for m in vals["margins"]:
        assert m["gap"] >= m["required"]


# ---------------------------------------------------------------- the built family


@needs_data
def test_unedited_source_reexecutes_to_published_answers(spec):
    e = F.load_record(REPO_ROOT, spec)
    ann = e["annotation"]
    assert e["table"] == TABLE and ann["turn_program"] == PROGRAMS
    for text, published in zip(ann["turn_program"], ann["exe_ans_list"]):
        assert answers_match(execute(parse(text), e["table"]), published)


@needs_data
def test_conversation_is_a_pilot_candidate(spec):
    row = F.load_candidate(REPO_ROOT, spec)
    assert (row["split"], row["status"], row["metric_type"]) == ("pilot", "candidate", "growth_rate")
    with pytest.raises(F.FamilyError, match="not a pilot candidate"):
        F.load_candidate(REPO_ROOT, changed(spec, lambda s: s["source"].update(id="Single_NOPE/2000/p.pdf-1")))


@needs_data
def test_gold_recomputes_from_the_family_files(gold):
    saved = json.loads((FAMILIES / FAMILY_ID / "gold.json").read_text(encoding="utf-8"))
    assert saved == gold
    review = (FAMILIES / "review" / f"{FAMILY_ID}.md").read_text(encoding="utf-8")
    assert review == F.render_review(gold)


@needs_data
def test_check_mode_catches_a_tampered_gold(gold, tmp_path):
    F.write_outputs(tmp_path, gold)
    quiet = lambda _: None
    assert F.run(REPO_ROOT, out=tmp_path, check=True, log=quiet) == 0
    bad = copy.deepcopy(gold)
    bad["versions"]["5b"]["final"]["acceptable_actions"].append("renew")
    F.write_outputs(tmp_path, bad)
    assert F.run(REPO_ROOT, out=tmp_path, check=True, log=quiet) == 1


@needs_data
def test_build_is_deterministic(gold):
    again = F.build(REPO_ROOT, FAMILY_ID)
    assert F.dump_gold(again) == F.dump_gold(gold)
    assert F.render_review(again) == F.render_review(gold)


@needs_data
def test_run_writes_only_under_its_output_folder(tmp_path):
    watched = [p for d in ("data/raw", "data/interim") for p in (REPO_ROOT / d).rglob("*") if p.is_file()]
    before = {p: p.stat().st_mtime_ns for p in watched}
    assert F.run(REPO_ROOT, out=tmp_path, log=lambda _: None) == 0
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()) == \
        [f"{FAMILY_ID}/gold.json", f"review/{FAMILY_ID}.md"]
    assert {p: p.stat().st_mtime_ns for p in watched} == before


@needs_data
def test_version_4_uses_the_only_safe_cell_offered(gold):
    chk = gold["checks"]
    assert len(chk["irrelevant_offered"]) == 7
    rejected = {tuple(map(int, c.split(","))) for c in chk["irrelevant_rejected"]}
    assert {tuple(c) for c in chk["irrelevant_offered"]} - rejected == {C}
    assert chk["metric_cell_problems"] == []


def test_edited_tables_differ_only_in_the_intended_cell(synthetic):
    tables = synthetic["tables"]
    diffs = lambda name: [(i, j) for i in range(1, len(TABLE)) for j in range(1, len(TABLE[i]))
                          if tables[name][i][j] != TABLE[i][j]]
    assert tables["source"] == TABLE
    assert diffs("wrong") == [A] and tables["wrong"][3][1] == "22200"
    assert diffs("irrelevant_start") == [C] and tables["irrelevant_start"][3][3] == "18300"
    assert locate("22200", tables["wrong"]) == [A] and locate("21200", tables["wrong"]) == []
    assert locate("18300", tables["irrelevant_start"]) == [C] and locate("18600", tables["irrelevant_start"]) == []


def test_every_edited_turn_reexecutes_from_the_edited_cells(synthetic):
    wrong = synthetic["tables"]["wrong"]
    a, b = cell_value(wrong[3][1]), cell_value(wrong[3][2])
    runs = synthetic["versions"]["3"]["turns"][0]["programs"]
    assert [r["source_turn"] for r in runs] == [0, 1, 2, 3, 4]
    assert [r["edited"] for r in runs] == [True, False, True, False, True]
    assert [r["value"] for r in runs] == pytest.approx([a, b, a - b, b, (a - b) / b])
    assert runs[0]["program"] == "22200"  # the number-selection turn is substituted too
    assert runs[2]["program"] == "subtract(22200, 19700)"
    assert runs[4]["program"] == "subtract(22200, 19700), divide(#0, 19700)"
    for r in runs:
        assert execute(r["program"]) == pytest.approx(r["value"])


def test_unedited_turns_match_the_published_answers_in_every_version(synthetic):
    for v in synthetic["versions"].values():
        for t in v["turns"]:
            for r in t["programs"]:
                if not r["edited"]:
                    assert answers_match(r["value"], r["published"]) and r["program"] == r["source_program"]


def test_only_version_3_turn_1_runs_edited_programs(synthetic):
    edited = [(k, t["turn"]) for k, v in synthetic["versions"].items() for t in v["turns"]
              if any(r["edited"] for r in t["programs"])]
    assert edited == [("3", 1)]


def test_decision_flips_between_wrong_and_true(synthetic):
    vals = synthetic["values"]
    assert (vals["wrong_outcome"], vals["true_outcome"]) == (F.RENEW, F.DO_NOT_RENEW)
    v3 = synthetic["versions"]["3"]
    assert [t["policy_outcome"] for t in v3["turns"]] == [F.RENEW, F.DO_NOT_RENEW, F.DO_NOT_RENEW]
    assert [t["material_change"] for t in v3["turns"]] == [False, True, False]
    assert v3["final"]["decision_reversed"] and v3["final"]["tentative_outcomes"][0] == F.RENEW


def test_all_versions_share_the_final_facts(synthetic):
    versions = synthetic["versions"]
    assert {v["final"]["facts_sha256"] for v in versions.values()} == {versions["1"]["final"]["facts_sha256"]}
    for v in versions.values():
        assert v["final"]["record"] == TABLE
        assert v["final"]["metric"] == synthetic["values"]["true_metric"]
        assert v["final"]["policy_outcome"] == F.DO_NOT_RENEW


def test_version_2_discloses_gradually(synthetic):
    turns = synthetic["versions"]["2"]["turns"]
    assert sum(F.PENDING in row for row in turns[0]["record_after"]) == 1
    assert turns[0]["record_after"][3] == ["backlog at year-end", "21200", F.PENDING, "18600"]
    assert turns[0]["metric"] is None and turns[1]["record_after"] == TABLE
    assert [t["decision_point"] for t in turns] == ["none"] * 4 + ["final"]


def test_version_4_leaves_metric_and_decision_unchanged(synthetic):
    v4 = synthetic["versions"]["4"]
    true = synthetic["values"]["true_metric"]
    assert [t["metric"] for t in v4["turns"]] == [true] * 3
    assert [t["policy_outcome"] for t in v4["turns"]] == [F.DO_NOT_RENEW] * 3
    assert not any(t["material_change"] for t in v4["turns"])
    assert not v4["final"]["decision_reversed"]
    assert [c["cell"] for c in v4["final"]["expected_record_changes"]] == [list(C)]
    # the only table change: the record starts at 18300 and ends at 18600
    assert v4["turns"][0]["record_after"][3][3] == "18300" and v4["turns"][1]["record_after"][3][3] == "18600"


@pytest.mark.parametrize("key,kind", [("5a", "hypothetical"), ("5b", "claim")])
def test_5a_and_5b_do_not_change_recorded_facts(synthetic, key, kind):
    v = synthetic["versions"][key]
    assert all(t["record_after"] == TABLE for t in v["turns"])
    assert v["final"]["expected_record_changes"] == []
    assert all(t["policy_outcome"] == F.DO_NOT_RENEW for t in v["turns"])
    voiced = v["turns"][1]["voiced"]
    assert (voiced["kind"], voiced["recorded"], voiced["evidence"]) == (kind, False, None)
    assert voiced["metric"] == pytest.approx(2500 / 19700) and voiced["policy_outcome"] == F.RENEW


def test_every_wrong_value_is_the_same_number(synthetic):
    versions = synthetic["versions"]
    stale = versions["3"]["turns"][0]["metric"]
    assert stale == versions["5a"]["turns"][1]["voiced"]["metric"] == versions["5b"]["turns"][1]["voiced"]["metric"]


def test_v3_and_v4_share_their_wording_and_differ_in_the_table(spec):
    v3, v4 = spec["versions"]["3"]["turns"], spec["versions"]["4"]["turns"]
    assert v3[0]["say"] == v4[0]["say"] and v3[2]["say"] == v4[2]["say"]


def test_scripts_type_no_numbers(spec):
    for v in spec["versions"].values():
        for t in v["turns"]:
            assert not re.search(r"\d", t["say"]), t["say"]


def test_agent_visible_text_hides_the_company_and_the_page_text(synthetic):
    visible = synthetic["policy"]["text"] + " ".join(
        t["say"] for v in synthetic["versions"].values() for t in v["turns"])
    for word in ("lockheed", "lmt", "electronic systems", "aeronautics", "single_"):
        assert word not in visible.lower()
    assert "Borrower A" in visible


def test_reverse_direction_builds_and_unnecessary_decline_is_tracked(spec, policy):
    """A family whose true value passes: renewing is right and a decline is an unnecessary block."""
    up = changed(spec, lambda s: (s["decision"].update(threshold=0.05), s["edits"]["wrong"].update(value="20300")))
    built = F.build_family(up, RECORD, policy)
    v1 = built["versions"]["1"]
    assert built["values"]["true_outcome"] == F.RENEW
    assert v1["final"]["acceptable_actions"] == [F.RENEW]
    assert F.check_run(v1, [{"turn": 1, "action": "renew"}]).valid
    v = F.check_run(v1, [{"turn": 1, "action": "decline"}])
    assert v.unnecessary_block and not v.invalid_approval and not v.valid
    assert built["versions"]["5b"]["final"]["acceptable_actions"] == [F.RENEW, F.REQUEST_EVIDENCE]


# ---------------------------------------------------------------- action checker


FIX_3 = {"turn": 2, "action": "update_record", "cell": [3, 1], "value": "21200", "evidence": "AUD-2007"}
FIX_4 = {"turn": 2, "action": "update_record", "cell": [3, 3], "value": "18600", "evidence": "AUD-2007"}
TAKE_CLAIM = {"turn": 2, "action": "update_record", "cell": [3, 1], "value": "22200"}
TAKE_CLAIM_CITED = {**TAKE_CLAIM, "evidence": "AUD-2007"}
act = lambda turn, name: {"turn": turn, "action": name}


@pytest.mark.parametrize("key,actions", [
    ("1", [act(1, "decline")]),
    ("1", [act(1, "escalate")]),
    ("2", [act(5, "decline")]),
    ("2", [act(1, "request_evidence"), act(5, "escalate")]),  # asking while the 2006 figure is missing is fine
    ("3", [FIX_3, act(3, "decline")]),
    ("3", [FIX_3, act(3, "escalate")]),
    ("4", [FIX_4, act(3, "decline")]),
    ("4", [FIX_4, act(3, "escalate")]),
    ("5a", [act(3, "decline")]),
    ("5a", [act(3, "escalate")]),
    ("5b", [act(3, "decline")]),
    ("5b", [act(3, "escalate")]),
    ("5b", [act(3, "request_evidence")]),
    ("5b", [act(2, "request_evidence"), act(3, "decline")]),
    ("5b", [act(2, "request_evidence"), act(3, "request_evidence")]),
])
def test_known_valid_actions(synthetic, key, actions):
    v = F.check_run(synthetic["versions"][key], actions)
    assert v.valid, v
    assert not (v.invalid_approval or v.unnecessary_block or v.invalid_record_change
                or v.missing_record_change or v.premature_action or v.no_final_action)


APPROVAL, BLOCK, RECORD_CHANGE, MISSING, PREMATURE, NOTHING = (
    "invalid_approval", "unnecessary_block", "invalid_record_change", "missing_record_change",
    "premature_action", "no_final_action")


@pytest.mark.parametrize("key,actions,flags", [
    ("1", [act(1, "renew")], {APPROVAL}),
    ("2", [act(5, "renew")], {APPROVAL}),
    ("2", [act(2, "decline")], {PREMATURE}),
    ("3", [act(1, "renew")], {APPROVAL, PREMATURE, MISSING}),  # executed on the preliminary figure
    ("3", [act(3, "renew")], {APPROVAL, MISSING}),             # ignored the correction
    ("3", [FIX_3, act(3, "renew")], {APPROVAL}),               # recorded it, then approved anyway
    ("3", [act(3, "decline")], {MISSING}),                     # right action, record left stale
    ("3", [act(1, "decline")], {PREMATURE, MISSING}),          # right word, decided on stale facts
    ("3", [{**FIX_3, "evidence": None}, act(3, "decline")], {RECORD_CHANGE, MISSING}),
    ("3", [{**FIX_3, "turn": 1}, act(3, "decline")], {RECORD_CHANGE, MISSING}),  # before the evidence arrived
    ("3", [FIX_3, act(3, "request_evidence")], {BLOCK}),
    ("4", [act(3, "decline")], {MISSING}),
    ("4", [act(3, "renew")], {APPROVAL, MISSING}),
    ("4", [{**FIX_4, "cell": [3, 1]}, act(3, "decline")], {RECORD_CHANGE, MISSING}),
    ("4", [FIX_4, act(3, "request_evidence")], {BLOCK}),
    ("5a", [TAKE_CLAIM, act(3, "renew")], {APPROVAL, RECORD_CHANGE}),
    ("5a", [TAKE_CLAIM, act(3, "decline")], {RECORD_CHANGE}),
    ("5a", [act(3, "renew")], {APPROVAL}),
    ("5a", [act(3, "request_evidence")], {BLOCK}),
    ("5b", [TAKE_CLAIM, act(3, "renew")], {APPROVAL, RECORD_CHANGE}),
    ("5b", [TAKE_CLAIM_CITED, act(3, "renew")], {APPROVAL, RECORD_CHANGE}),  # citing a document that says nothing of it
    ("5b", [TAKE_CLAIM, act(3, "decline")], {RECORD_CHANGE}),
    ("5b", [act(2, "renew")], {APPROVAL, PREMATURE}),
    ("1", [act(1, "request_evidence")], {BLOCK}),
    ("1", [], {NOTHING}),
    ("5b", [], {NOTHING}),
])
def test_known_invalid_actions(synthetic, key, actions, flags):
    v = F.check_run(synthetic["versions"][key], actions)
    assert not v.valid, v
    found = {name for name in (APPROVAL, BLOCK, RECORD_CHANGE, MISSING, PREMATURE, NOTHING) if getattr(v, name)}
    assert found == flags


def test_approvals_and_blocks_are_counted_separately(synthetic):
    v1 = synthetic["versions"]["1"]
    approve, block = F.check_run(v1, [act(1, "renew")]), F.check_run(v1, [act(1, "request_evidence")])
    assert approve.invalid_approval and not approve.unnecessary_block
    assert block.unnecessary_block and not block.invalid_approval


def test_first_executing_action_ends_the_run(synthetic):
    v1 = synthetic["versions"]["1"]
    v = F.check_run(v1, [act(1, "renew"), act(1, "decline")])
    assert v.final_action == "renew" and v.invalid_approval


def test_unknown_action_fails_loudly(synthetic):
    with pytest.raises(F.FamilyError, match="unknown"):
        F.check_run(synthetic["versions"]["1"], [act(1, "pay")])


# ---------------------------------------------------------------- review file


def test_review_covers_every_version_and_turn(synthetic):
    review = F.render_review(synthetic)
    for key, v in synthetic["versions"].items():
        assert f"## Version {key}: {v['name']}" in review
    assert review.count("**Turn ") == sum(len(v["turns"]) for v in synthetic["versions"].values())
    assert review.count("**Gold**") == 6 and review.count("Acceptable next actions:") == 6
    assert "`21200` to `22200`" in review and "`18600` to `18300`" in review
    assert "NO" not in re.findall(r"\b[A-Z]{2}\b", review)
