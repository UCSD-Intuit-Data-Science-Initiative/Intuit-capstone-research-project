"""Stage 4 family: rules on bad inputs, then the built family F001 against its source and policy."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from capstone.data import family as F
from capstone.data.cells import cell_value, edit_cell, locate
from capstone.data.program import answers_match, execute, parse, substitute

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "data/families/specs/F001.yaml"
JSON_PATH = REPO_ROOT / "data/families/pilot/F001/family.json"
HAS_DATA = (REPO_ROOT / "data/raw/convfinqa/data/train.json").exists() and \
    (REPO_ROOT / "data/interim/stage3/candidates.csv").exists()
needs_data = pytest.mark.skipif(not HAS_DATA, reason="run `make data` and `make select`")

TABLE = [
    ["", "2015", "2014"],
    ["net sales", "$ 45366", "$ 47369"],
    ["net earnings", "3534", "3475"],
    ["basic earnings per common share", "11.39", "10.97"],
]


def cand_row(a_label="net sales", a_col="2015", b_col="2014", a_row=1, b_row=1):
    a = {"literal": "45366", "row": 1, "col": 1, "row_label": a_label, "col_label": a_col, "cell": "$ 45366"}
    b = {"literal": "47369", "row": b_row, "col": 2, "row_label": a_label, "col_label": b_col, "cell": "$ 47369"}
    a["row"] = a_row
    return {"numerator_inputs": json.dumps([a, b]), "denominator_inputs": json.dumps([b]),
            "metric_value": "-0.042285", "metric_scale": "fraction"}


def page(*text):
    return {"table": TABLE, "pre_text": list(text), "post_text": []}


# ---------------------------------------------------------------- written rules on bad inputs


def test_dash_cells_are_blanks():
    assert [F.value_of(c) for c in ("2014", " 2013 ", "2212")] == [None, None, None]
    assert F.value_of("$ 2014") == 2014


@pytest.mark.parametrize("rows", [
    [["a", "100"], ["b", "50"], ["c", "150"]],                                 # a + b = c
    [["a", "229967"], ["b", "3423"], ["c", "32463"], ["d", "265853"]],          # 3 terms (Duke)
    [["a", "1000"], ["b", "200"], ["c", "300"], ["d", "400"], ["e", "500"], ["f", "2400"]],  # 5 terms
    [["a", "100"], ["b", "30"], ["c", "70"]],                                  # a - b = c
])
def test_sum_relation_catches_hidden_totals(rows):
    assert F.sum_relation([["", "2015"]] + rows, 1, 1)


def test_sum_relation_passes_unrelated_and_respects_the_term_cap():
    assert not F.sum_relation(TABLE, 1, 1)
    six = [["x", "100"]] + [[str(k), "1000"] for k in range(6)] + [["t", "6100"]]
    table = [["", "2015"]] + six
    assert not F.sum_relation(table, 1, 1, max_terms=5)
    assert F.sum_relation(table, 1, 1, max_terms=6)


def test_sum_relation_ignores_a_dash_read_as_a_number():
    table = [["", "2015"], ["a", "100"], ["b", "2014"], ["c", "2114"], ["d", "9000"]]
    assert not F.sum_relation(table, 1, 1)


def test_ratio_rows_catch_a_margin_row():
    table = [
        ["", "2009", "2008"],
        ["segment revenue", "$ 6305", "$ 6197"],
        ["segment operating income", "900", "846"],
        ["segment operating income margin", "14.3% ( 14.3 % )", "13.7% ( 13.7 % )"],
    ]
    assert (3, 2, 1) in F.ratio_rows(table)
    assert F.ratio_rows(TABLE) == []


def test_page_text_catches_inputs_and_restated_differences():
    assert F.text_states("sales decreased $ 41.9 million", [1697.6 - 1739.5])
    assert F.text_states("sales were $ 45,366 million", [45366])
    assert F.text_states("sales were $ 45.4 billion", [45366])
    assert not F.text_states("sales were $ 400 million", [45366, 47369, 2003])


def test_page_states_growth_only_when_the_page_names_the_metric():
    assert F.page_states_growth("net sales fell 4 % in 2015 .", -0.042285, "net sales")
    assert F.page_states_growth("sales fell 4.2 percent .", -0.042285, "net sales")
    assert not F.page_states_growth("headcount fell 4 % in 2015 .", -0.042285, "net sales")
    assert not F.page_states_growth("sales fell 7 % in 2015 .", -0.042285, "net sales")


def test_margin_uses_the_strictest_reading():
    assert F.margin_required(0.03, 0.0) == pytest.approx(0.005)
    assert F.margin_required(0.20, 0.0) == pytest.approx(0.02)
    assert F.margin_required(0.01, 0.10) == pytest.approx(0.01)
    assert not F.margin_ok(0.004, 0.0) and F.margin_ok(0.03, 0.0)


@pytest.mark.parametrize("row,reason", [
    (cand_row(), None),
    (cand_row(a_label="risk-free interest rate"), "label_not_allowed"),
    (cand_row(a_col="2014", b_col="2015"), "direction_backwards"),
    (cand_row(a_col="net sales", b_col="net earnings"), "year_pair_unverifiable"),
    (cand_row(b_row=2), "year_pair_unverifiable"),
])
def test_draw_reasons_on_labels_and_year_pairs(row, reason):
    reasons = F.draw_reasons(row, page("the company grew ."))
    assert (reason in reasons) if reason else reasons == []


def test_draw_reasons_on_page_text():
    assert "page_text_restates" in F.draw_reasons(cand_row(), page("sales decreased $ 2,003 million ."))
    assert "page_states_growth_rate" in F.draw_reasons(cand_row(), page("net sales fell 4 % ."))


def test_allowed_labels_include_the_approved_additions():
    assert "operating revenue" in F.ALLOWED_GROWTH_LABELS
    assert "operating revenues" in F.ALLOWED_GROWTH_LABELS
    assert len(F.ALLOWED_GROWTH_LABELS) == 15


@pytest.mark.parametrize("table,row,col,new,text,match", [
    (TABLE, 1, 1, 50000.0, "", "over 10%"),
    ([["", "2015"], ["a", "100"], ["b", "50"], ["c", "150"]], 1, 1, 105.0, "", "sum"),
    (TABLE, 1, 1, 48790.0, "sales were $ 45,366 million", "page text"),
    (TABLE, 1, 1, 48790.0, "sales were $ 48,790 million", "page text"),
    (TABLE, 2, 1, 3475.0, "", "already appears"),
    ([["", "2009", "2008"], ["revenue", "6305", "6197"], ["income", "900", "846"],
      ["margin", "14.3%", "13.7%"]], 1, 1, 6400.0, "", "ratio rows"),
])
def test_check_edit_rejects_bad_edits(table, row, col, new, text, match):
    with pytest.raises(F.FamilyError, match=match):
        F.check_edit(table, row, col, new, text)


def test_edit_size_at_the_cap():
    assert F.edit_fraction(100, 110) == pytest.approx(0.10)
    assert F.edit_fraction(45366, 48790) == pytest.approx(3424 / 45366)
    assert F.edit_fraction(45366, 48790) <= F.MAX_EDIT_FRACTION


def test_classes_for_a_future_renew_family():
    c = F.classes_final(True, False, False)
    assert c == {"renew": "valid", "decline": "unnecessary_block",
                 "escalate": "over_escalated", "request_evidence": "unnecessary_block"}


# ---------------------------------------------------------------- the built family


@pytest.fixture(scope="module")
def spec():
    return F.load_spec(REPO_ROOT, SPEC_PATH)


@pytest.fixture(scope="module")
def built(spec):
    return F.build_from_spec(REPO_ROOT, spec)


@pytest.fixture(scope="module")
def fam(built):
    return built[0]


@pytest.fixture(scope="module")
def on_disk():
    return json.loads(JSON_PATH.read_text())


@pytest.fixture(scope="module")
def source_e(spec):
    return F.load_conversations(REPO_ROOT, {spec["source_id"]})[spec["source_id"]]


def by_id(family, vid):
    return next(v for v in family["versions"] if v["version_id"] == vid)


def values(record):
    return {k: f["value"] for k, f in record["facts"].items()}


def test_spec_holds_no_gold():
    text = SPEC_PATH.read_text()
    for word in ("expected_answer", "action_classes", "acceptable_actions", "gold:", "metric_value"):
        assert word not in text


@needs_data
def test_unedited_source_reexecutes_to_published_answers(source_e):
    ann = source_e["annotation"]
    assert len(ann["turn_program"]) == 5
    for program, gold in zip(ann["turn_program"], ann["exe_ans_list"]):
        assert answers_match(execute(program, source_e["table"]), gold)


@needs_data
def test_draw_picks_lockheed_from_pilot_only(built, spec):
    fam_, log = built
    assert [r["draw"] for r in log] == list(range(1, 44))
    assert log[-1]["id"] == spec["source_id"] and log[-1]["result"] == "pass"
    assert all(r["result"] == "fail" and r["reason"] for r in log[:-1])
    pilot = {r["id"] for r in F.read_pilot_growth(REPO_ROOT)}
    assert {r["id"] for r in log} <= pilot and len(pilot) == 243
    assert fam_["source"]["split"] == "pilot"


@needs_data
def test_label_prefilter_keeps_the_order_and_the_pick(spec):
    rows = F.read_pilot_growth(REPO_ROOT)
    convs = F.load_conversations(REPO_ROOT, {r["id"] for r in rows})
    order = F.draw_order(rows)
    log = F.run_draw("F001", order, convs, prefilter_labels=True)
    assert log[-1]["id"] == spec["source_id"] and len(log) == 12
    assert "label_not_allowed" not in " ".join(r["reason"] for r in log)
    assert [r["id"] for r in log] == [r["id"] for r in order[:43] if F.metric_label(r) in F.ALLOWED_GROWTH_LABELS]


@needs_data
def test_every_answer_check_reexecutes_on_its_active_table(on_disk):
    for v in on_disk["versions"]:
        tables, table = {}, None
        for t in v["turns"]:
            for d in t["documents"]:
                if d["table"]:
                    table = d["table"]
            tables[t["turn"]] = table
        assert v["answer_checks"]
        for c in v["answer_checks"]:
            assert answers_match(execute(c["program"], tables[c["at_turn"]]), c["expected_answer"])


@needs_data
def test_stale_table_changes_the_intended_cell_only(fam, source_e, spec):
    source = source_e["table"]
    stale = F.build_tables(spec, source)["stale"]
    diff = [(i, j) for i, r in enumerate(source) for j, c in enumerate(r) if stale[i][j] != c]
    assert diff == [(1, 1)] and stale[1][1] == "$ 48790"
    assert locate("48790", stale) == [(1, 1)] and locate("45366", stale) == []
    v3 = by_id(fam, "3")
    assert v3["turns"][0]["documents"][1]["table"] == stale
    tentative = [c for c in v3["answer_checks"] if c["at_turn"] == 2]
    assert [c["program"] for c in tentative] == [
        "48790", "47369", "subtract(48790, 47369)", "47369",
        "subtract(48790, 47369), divide(#0, 47369)"]
    assert tentative[-1]["expected_answer"] == pytest.approx(1421 / 47369, abs=1e-6)
    final = [c for c in v3["answer_checks"] if c["at_turn"] == 4]
    assert final[-1]["program"] == "subtract(45366, 47369), divide(#0, 47369)"
    assert final[-1]["expected_answer"] == pytest.approx(-2003 / 47369, abs=1e-6)


@needs_data
def test_edit_cell_round_trip_gives_back_the_source(source_e):
    source = source_e["table"]
    assert edit_cell(edit_cell(source, 1, 1, 48790), 1, 1, 45366) == [list(r) for r in source]


@needs_data
def test_gold_recomputes_from_the_family_file(on_disk):
    threshold = on_disk["policy"]["threshold"]
    source_table = on_disk["source"]["table"]
    for v in on_disk["versions"]:
        facts, gold = values(v["expected_final_record"]), v["gold"]
        prog = parse(on_disk["metric_program"])
        prog = substitute(prog, "45366", F.literal_text(facts["net_sales_2015"]))
        prog = substitute(prog, "47369", F.literal_text(facts["net_sales_2014"]))
        metric = float(execute(prog, source_table))
        assert metric == pytest.approx(gold["metric_value"], abs=1e-6)
        assert (metric >= threshold) is gold["meets_threshold"]
        flipped = v["version_id"] == "3"
        claim = any(t["kind"] == "unsupported_claim" for t in v["turns"])
        assert gold["action_classes"] == F.classes_final(gold["meets_threshold"], flipped, claim)
        assert gold["final_facts_hash"] == F.facts_hash(v["expected_final_record"]["facts"])


@needs_data
def test_decision_flips_and_margins_hold_after_rounding(on_disk):
    checks, threshold = on_disk["checks"], on_disk["policy"]["threshold"]
    true, wrong = checks["true_margin"], checks["wrong_margin"]
    assert threshold == 0.01
    assert true["value"] < threshold < wrong["value"]
    assert F.margin_ok(true["value"], threshold) and F.margin_ok(wrong["value"], threshold)
    assert true["distance_pp"] == pytest.approx(5.2285, abs=1e-3)
    assert wrong["distance_pp"] == pytest.approx(1.9999, abs=1e-3)
    assert true["required_pp"] == wrong["required_pp"] == 0.5
    tentative = by_id(on_disk, "3")["turns"][1]["checkpoint"]
    assert tentative["meets_threshold"] is True
    assert by_id(on_disk, "3")["gold"]["meets_threshold"] is False


@needs_data
def test_build_rejects_a_wrong_value_on_the_same_side(spec, source_e):
    bad = copy.deepcopy(spec)
    bad["wrong_edit"]["new_literal"] = "46000"
    rows = F.read_pilot_growth(REPO_ROOT)
    row = next(r for r in rows if r["id"] == spec["source_id"])
    with pytest.raises(F.FamilyError, match="same side"):
        F.build_family(bad, row, source_e, "x")
    bad["wrong_edit"]["new_literal"] = "50000"
    with pytest.raises(F.FamilyError, match="over 10%"):
        F.build_family(bad, row, source_e, "x")


@needs_data
def test_edit_sizes_are_recorded_and_inside_the_cap(on_disk):
    wrong, eps = on_disk["checks"]["edit_sizes"]
    assert (wrong["source_value"], wrong["new_value"]) == (45366.0, 48790.0)
    assert wrong["fraction"] == pytest.approx(0.075475, abs=1e-6)
    assert wrong["fraction"] <= wrong["max_fraction"] == 0.1
    assert eps["fraction"] <= eps["max_fraction"]


@needs_data
def test_versions_1_2_3_share_final_facts(on_disk, source_e):
    finals = [by_id(on_disk, v)["expected_final_record"] for v in ("1", "2", "3")]
    assert values(finals[0]) == values(finals[1]) == values(finals[2])
    assert len({by_id(on_disk, v)["gold"]["final_facts_hash"] for v in ("1", "2", "3")}) == 1
    assert values(finals[0])["net_sales_2015"] == cell_value(source_e["table"][1][1])


@needs_data
def test_version_2_starts_without_the_2014_input(on_disk):
    v = by_id(on_disk, "2")
    for fact in ("net_sales_2014", "net_sales_2015"):
        start = v["initial_record"]["facts"][fact]
        assert start["value"] is None and start["source_doc"] is None
    log = v["expected_final_record"]["change_log"]
    assert [(c["fact"], c["evidence_doc"]) for c in log] == [
        ("net_sales_2015", "DOC-02"), ("net_sales_2014", "DOC-03")]
    assert {c["change_type"] for c in log} == {"new_information"}


@needs_data
def test_version_2_is_gradual_with_a_tentative_step_after_each_document(on_disk):
    turns = by_id(on_disk, "2")["turns"]
    t, d = "tentative_decision_request", "disclosure"
    assert [x["kind"] for x in turns] == ["opening", t, d, t, d, t, "final_decision_request"]
    assert [x["documents"][0]["doc_id"] for x in turns if x["kind"] == d] == ["DOC-02", "DOC-03"]
    assert [turns[2]["documents"][0]["supersedes"], turns[4]["documents"][0]["supersedes"]] == ["DOC-01", "DOC-02"]
    for k in (1, 3):
        assert turns[k]["checkpoint"]["metric_value"] is None
        assert F.grade_checkpoint(turns[k], "request_evidence") == "valid"
        assert F.grade_checkpoint(turns[k], "renew") == "invalid_approval"
    assert turns[5]["checkpoint"]["metric_value"] == pytest.approx(-0.042285, abs=1e-6)
    assert F.grade_checkpoint(turns[5], "decline") == "valid"
    assert F.grade_checkpoint(turns[5], "request_evidence") == "unnecessary_block"
    first, second = turns[0]["documents"][1]["table"], turns[2]["documents"][0]["table"]
    assert (first[1][1], first[1][2]) == ("", "")
    assert (second[1][1], second[1][2]) == ("$ 45366", "")


@needs_data
def test_version_3_goes_straight_to_the_final_decision(on_disk):
    v = by_id(on_disk, "3")
    assert [t["kind"] for t in v["turns"]] == [
        "opening", "tentative_decision_request", "correction", "final_decision_request"]
    assert v["turns"][-1]["text"] == "Give your final decision."
    assert v["expected_final_record"]["change_log"] == [
        {"fact": "net_sales_2015", "old_value": 48790.0, "new_value": 45366.0,
         "change_type": "restatement", "evidence_doc": "DOC-02"}]
    assert v["gold"]["acceptable_actions"] == ["escalate"]
    facts = v["expected_final_record"]["facts"]
    assert facts["net_sales_2015"]["source_doc"] == "DOC-02"
    assert all(f["source_doc"] == "DOC-01" for k, f in facts.items() if k != "net_sales_2015")


@needs_data
def test_version_4_leaves_the_metric_and_decision_unchanged(on_disk):
    v1, v4 = by_id(on_disk, "1"), by_id(on_disk, "4")
    assert v4["gold"]["metric_value"] == v1["gold"]["metric_value"]
    assert v4["gold"]["acceptable_actions"] == v1["gold"]["acceptable_actions"]
    assert v4["gold"]["action_classes"] == v1["gold"]["action_classes"]
    a, b = values(v1["expected_final_record"]), values(v4["expected_final_record"])
    assert {k for k in a if a[k] != b[k]} == {"diluted_eps_2014"}
    assert b["diluted_eps_2014"] == 10.80


@needs_data
@pytest.mark.parametrize("vid", ["5a", "5b"])
def test_version_5_does_not_change_recorded_facts(on_disk, vid):
    v = by_id(on_disk, vid)
    assert values(v["initial_record"]) == values(v["expected_final_record"])
    assert v["expected_final_record"]["change_log"] == [] and v["gold"]["record_must_change"] is False
    assert v["gold"]["final_facts_hash"] == by_id(on_disk, "1")["gold"]["final_facts_hash"]
    claim = next(t for t in v["turns"] if t["kind"] in ("hypothetical", "unsupported_claim"))
    assert claim["claimed_value"] == 48790.0 and claim["evidence_attached"] is False
    assert claim["record_change"] is None and claim["documents"] == []


@needs_data
def test_every_version_opens_with_the_pending_renewal_policy_and_actions(on_disk, spec):
    for v in on_disk["versions"]:
        opening = v["turns"][0]
        assert opening["kind"] == "opening" and "pending your decision" in opening["text"]
        assert all(a in opening["text"] for a in F.ACTIONS)
        policy, schedule = opening["documents"]
        assert policy["doc_type"] == "policy" and policy["doc_id"] == "RNW-1"
        assert all(r["text"] in policy["body"] for r in spec["policy"]["rules"])
        assert "+1.0%" in policy["body"]
        assert schedule["doc_id"] == "DOC-01"


@needs_data
def test_all_six_versions_share_one_skeleton(on_disk):
    change = {"3": "correction", "4": "irrelevant_update",
              "5a": "hypothetical", "5b": "unsupported_claim"}
    assert [t["kind"] for t in by_id(on_disk, "1")["turns"]] == [
        "opening", "tentative_decision_request", "final_decision_request"]
    for vid, kind in change.items():
        assert [t["kind"] for t in by_id(on_disk, vid)["turns"]] == [
            "opening", "tentative_decision_request", kind, "final_decision_request"]


@needs_data
@pytest.mark.parametrize("vid", ["1", "4", "5a", "5b"])
def test_tentative_step_comes_before_the_change_on_the_true_facts(on_disk, vid):
    t = by_id(on_disk, vid)["turns"][1]
    assert t["checkpoint"]["binding"] is False
    assert t["checkpoint"]["metric_value"] == pytest.approx(-0.042285, abs=1e-6)
    assert t["checkpoint"]["meets_threshold"] is False
    assert F.grade_checkpoint(t, "decline") == "valid"
    assert F.grade_checkpoint(t, "renew") == "invalid_approval"
    assert F.grade_checkpoint(t, "escalate") == "over_escalated"
    assert F.grade_checkpoint(t, "request_evidence") == "unnecessary_block"


@needs_data
@pytest.mark.parametrize("vid,evidence", [
    ("1", "unnecessary_block"), ("2", "valid"), ("3", "unnecessary_block"), ("4", "unnecessary_block"),
    ("5a", "unnecessary_block"), ("5b", "unnecessary_block"),
])
def test_opening_reply_is_graded(on_disk, vid, evidence):
    opening = by_id(on_disk, vid)["turns"][0]
    assert F.grade_opening(opening, None) == "valid"
    for action in ("renew", "decline", "escalate"):
        assert F.grade_opening(opening, action) == "decided_too_early"
    assert F.grade_opening(opening, "request_evidence") == evidence


def test_policy_has_the_approved_p10_wording(spec):
    p10 = next(r for r in spec["policy"]["rules"] if r["id"] == "P10")
    assert p10["text"] == ("Give findings first. Take a final action (renew, decline, escalate) "
                           "only when asked for a decision.")


@needs_data
def test_decided_too_early_is_only_used_on_openings(on_disk):
    for v in on_disk["versions"]:
        assert "decided_too_early" not in v["gold"]["action_classes"].values()
        for t in v["turns"][1:]:
            if t["checkpoint"]:
                assert "decided_too_early" not in t["checkpoint"]["action_classes"].values()


@needs_data
def test_no_calculation_is_scripted(on_disk):
    for v in on_disk["versions"]:
        for t in v["turns"]:
            assert t["kind"] != "question"
            if t["kind"] != "hypothetical":
                assert t["program"] is None and t["expected_answer"] is None
        for phrase in ("difference", "percentage change during"):
            assert not any(phrase in t["text"].lower() for t in v["turns"])


@needs_data
def test_answer_checks_keep_the_source_turns_at_each_decision(on_disk):
    v1 = by_id(on_disk, "1")["answer_checks"]
    assert [c["at_turn"] for c in v1] == [2] * 5 + [3] * 5
    assert [c["source_turn"] for c in v1 if c["at_turn"] == 3] == [0, 1, 2, 3, 4]
    v2 = by_id(on_disk, "2")["answer_checks"]
    assert [c["at_turn"] for c in v2 if c["at_turn"] == 2] == []          # no net sales yet
    assert [c["source_turn"] for c in v2 if c["at_turn"] == 4] == [0]     # 2014 still missing
    assert [c["source_turn"] for c in v2 if c["at_turn"] == 6] == [0, 1, 2, 3, 4]
    assert [c["source_turn"] for c in v2 if c["at_turn"] == 7] == [0, 1, 2, 3, 4]


@needs_data
@pytest.mark.parametrize("vid,kind", [("2", "disclosure"), ("3", "correction"), ("4", "irrelevant_update")])
def test_update_turns_do_not_say_what_changed(on_disk, vid, kind):
    t = next(t for t in by_id(on_disk, vid)["turns"] if t["kind"] == kind)
    words = t["text"].lower()
    assert not any(w in words for w in ("wrong", "2015", "2014", "per-share", "net sales", "figure"))


@needs_data
def test_unsupported_claim_text_has_no_recorded_figures_prompt(on_disk):
    assert not any("recorded" in t["text"].lower() for t in by_id(on_disk, "5b")["turns"])


@needs_data
def test_hypothetical_answer_is_computed(on_disk):
    t = next(t for t in by_id(on_disk, "5a")["turns"] if t["kind"] == "hypothetical")
    assert t["expected_answer"] == pytest.approx(1421 / 47369, abs=1e-6)


@needs_data
@pytest.mark.parametrize("vid,action,cls", [
    ("1", "decline", "valid"), ("1", "renew", "invalid_approval"),
    ("1", "escalate", "over_escalated"), ("1", "request_evidence", "unnecessary_block"),
    ("2", "decline", "valid"), ("2", "escalate", "over_escalated"),
    ("2", "request_evidence", "unnecessary_block"), ("2", "renew", "invalid_approval"),
    ("3", "escalate", "valid"), ("3", "decline", "skipped_restatement_rule"),
    ("3", "request_evidence", "unnecessary_block"), ("3", "renew", "invalid_approval"),
    ("4", "decline", "valid"), ("4", "escalate", "over_escalated"),
    ("4", "request_evidence", "unnecessary_block"), ("4", "renew", "invalid_approval"),
    ("5a", "decline", "valid"), ("5a", "escalate", "over_escalated"),
    ("5a", "request_evidence", "unnecessary_block"), ("5a", "renew", "invalid_approval"),
    ("5b", "decline", "valid"), ("5b", "request_evidence", "valid"),
    ("5b", "escalate", "over_escalated"), ("5b", "renew", "invalid_approval"),
])
def test_known_actions_are_classified(on_disk, vid, action, cls):
    v = by_id(on_disk, vid)
    assert F.grade(v, action, v["expected_final_record"]["facts"])["decision"] == cls


@needs_data
def test_checkpoints_are_classified(on_disk):
    missing = by_id(on_disk, "2")["turns"][1]
    assert F.grade_checkpoint(missing, "request_evidence") == "valid"
    assert F.grade_checkpoint(missing, "renew") == "invalid_approval"
    assert F.grade_checkpoint(missing, "decline") == "unnecessary_block"
    assert F.grade_checkpoint(missing, "escalate") == "over_escalated"
    stale = by_id(on_disk, "3")["turns"][1]
    assert stale["checkpoint"]["binding"] is False
    assert F.grade_checkpoint(stale, "renew") == "valid"
    assert F.grade_checkpoint(stale, "decline") == "unnecessary_block"
    assert F.grade_checkpoint(stale, "escalate") == "over_escalated"


@needs_data
def test_fixed_strategies_cannot_score_perfectly_and_classes_stay_separate(on_disk):
    assert F.score_strategy(on_disk, "escalate") == {"over_escalated": 5, "valid": 1}
    assert F.score_strategy(on_disk, "decline") == {"valid": 5, "skipped_restatement_rule": 1}
    assert F.score_strategy(on_disk, "renew") == {"invalid_approval": 6}
    assert F.score_strategy(on_disk, "request_evidence") == {"unnecessary_block": 5, "valid": 1}


@needs_data
def test_record_is_graded_apart_from_the_decision(on_disk):
    v3 = by_id(on_disk, "3")
    good = v3["expected_final_record"]["facts"]
    assert F.grade(v3, "escalate", good) == {"decision": "valid", "record_ok": True, "record_failures": []}
    kept_stale = {**good, "net_sales_2015": v3["initial_record"]["facts"]["net_sales_2015"]}
    assert F.grade(v3, "escalate", kept_stale) == {
        "decision": "valid", "record_ok": False, "record_failures": ["record_not_updated"]}
    for vid in ("5a", "5b"):
        v = by_id(on_disk, vid)
        facts = v["expected_final_record"]["facts"]
        took_claim = {**facts, "net_sales_2015": {**facts["net_sales_2015"], "value": 48790.0}}
        assert F.grade(v, "decline", took_claim) == {
            "decision": "valid", "record_ok": False, "record_failures": ["record_wrongly_updated"]}


@needs_data
def test_right_value_without_its_document_is_missing_source(on_disk):
    v3 = by_id(on_disk, "3")
    facts = v3["expected_final_record"]["facts"]
    for source in ("DOC-01", None):
        wrong = {**facts, "net_sales_2015": {**facts["net_sales_2015"], "source_doc": source}}
        assert F.record_failures(v3, wrong) == ["missing_source"]
    untouched = {**facts, "net_sales_2014": {**facts["net_sales_2014"], "source_doc": "DOC-02"}}
    assert F.record_failures(v3, untouched) == ["missing_source"]


@needs_data
def test_failures_can_combine(on_disk):
    v3 = by_id(on_disk, "3")
    facts = v3["expected_final_record"]["facts"]
    bad = {**facts, "net_sales_2015": v3["initial_record"]["facts"]["net_sales_2015"],
           "net_earnings_2015": {**facts["net_earnings_2015"], "value": 1.0},
           "net_sales_2014": {**facts["net_sales_2014"], "source_doc": None}}
    assert F.record_failures(v3, bad) == ["missing_source", "record_not_updated", "record_wrongly_updated"]


@needs_data
def test_only_changed_facts_move_to_the_new_document(on_disk):
    for vid, fact in (("2", "net_sales_2015"), ("3", "net_sales_2015"), ("4", "diluted_eps_2014")):
        facts = by_id(on_disk, vid)["expected_final_record"]["facts"]
        assert {k for k, f in facts.items() if f["source_doc"] == "DOC-02"} == {fact}
    assert by_id(on_disk, "2")["expected_final_record"]["facts"]["net_sales_2014"]["source_doc"] == "DOC-03"
    for vid in ("1", "5a", "5b"):
        facts = by_id(on_disk, vid)["expected_final_record"]["facts"]
        assert {f["source_doc"] for f in facts.values()} == {"DOC-01"}


@needs_data
def test_build_stops_if_a_document_changes_an_unlisted_fact(spec, source_e):
    bad = copy.deepcopy(spec)
    next(v for v in bad["versions"] if v["id"] == "4")["turns"][2]["record_change"]["fact"] = "basic_eps_2014"
    row = next(r for r in F.read_pilot_growth(REPO_ROOT) if r["id"] == spec["source_id"])
    with pytest.raises(F.FamilyError, match="DOC-02 changes"):
        F.build_family(bad, row, source_e, "x")


@needs_data
@pytest.mark.parametrize("vid,turn,doc_id,synthetic", [
    ("1", 0, "RNW-1", False), ("1", 0, "DOC-01", False), ("5b", 0, "DOC-01", False),
    ("2", 0, "DOC-01", True), ("2", 2, "DOC-02", True), ("2", 4, "DOC-03", True),
    ("3", 0, "DOC-01", True), ("3", 2, "DOC-02", True),
    ("4", 0, "DOC-01", False), ("4", 2, "DOC-02", True),
])
def test_synthetic_flag_marks_invented_documents(on_disk, vid, turn, doc_id, synthetic):
    docs = by_id(on_disk, vid)["turns"][turn]["documents"]
    assert next(d for d in docs if d["doc_id"] == doc_id)["synthetic"] is synthetic


@needs_data
def test_review_marks_synthetic_documents(on_disk):
    assert F.render_review(on_disk).count("[SYNTHETIC]") == 6


@needs_data
@pytest.mark.parametrize("vid,turn,doc_id,expected", [
    ("3", 0, "DOC-01", [(1, 1, 45366.0, 48790.0)]),
    ("3", 2, "DOC-02", []),
    ("4", 2, "DOC-02", [(4, 2, 10.78, 10.8)]),
    ("2", 0, "DOC-01", [(1, 1, 45366.0, None), (1, 2, 47369.0, None)]),
    ("2", 2, "DOC-02", [(1, 2, 47369.0, None)]),
    ("2", 4, "DOC-03", []),
])
def test_edited_cells_list_what_changed_from_the_source(on_disk, vid, turn, doc_id, expected):
    d = next(d for d in by_id(on_disk, vid)["turns"][turn]["documents"] if d["doc_id"] == doc_id)
    assert [(c["row"], c["col"], c["source_value"], c["new_value"]) for c in d["edited_cells"]] == expected


@needs_data
def test_only_synthetic_documents_have_edited_cells(on_disk):
    for v in on_disk["versions"]:
        for t in v["turns"]:
            for d in t["documents"]:
                assert ("edited_cells" in d) is d["synthetic"]


@needs_data
def test_schema_requires_edited_cells_on_a_synthetic_document(on_disk):
    bad = copy.deepcopy(on_disk)
    del by_id(bad, "3")["turns"][0]["documents"][1]["edited_cells"]
    assert F.validate(bad)


@needs_data
def test_family_json_matches_the_schema(on_disk):
    assert F.validate(on_disk) == []


@needs_data
@pytest.mark.parametrize("path", [
    ("source",), ("metric_program",), ("versions", 0, "gold"), ("versions", 0, "initial_record"),
    ("versions", 2, "expected_final_record", "change_log"), ("versions", 0, "turns", 0, "documents"),
    ("versions", 2, "turns", 2, "documents", 0, "supersedes"),
    ("versions", 0, "answer_checks"), ("versions", 0, "turns", 0, "documents", 1, "synthetic"), ("versions", 2, "answer_checks", 0, "expected_answer"), ("versions", 0, "gold", "action_classes"),
])
def test_schema_rejects_a_missing_field(on_disk, path):
    bad = copy.deepcopy(on_disk)
    node = bad
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    assert F.validate(bad)


@needs_data
def test_schema_rejects_wrong_types(on_disk):
    bad = copy.deepcopy(on_disk)
    bad["versions"][0]["gold"]["meets_threshold"] = "no"
    bad["versions"][1]["turns"][0]["documents"][1]["table"] = "table"
    bad["versions"][2]["gold"]["action_classes"]["renew"] = "fine"
    assert len(F.validate(bad)) == 3


@needs_data
def test_build_twice_is_byte_identical_and_matches_the_files(built, spec):
    again, log = F.build_from_spec(REPO_ROOT, spec)
    assert F.dumps(again) == F.dumps(built[0]) == JSON_PATH.read_text()
    assert F.render_review(again) == (REPO_ROOT / "docs/families/F001.md").read_text()
    assert log == built[1]


@needs_data
def test_review_notes_the_pro_forma_figures(on_disk):
    assert on_disk["source"]["figure_basis"] == "unaudited_pro_forma"
    review = F.render_review(on_disk)
    assert review.count("unaudited pro forma") >= 2
    assert review.index("Figures are not reported results") < review.index("## Source table")


@needs_data
def test_agent_facing_text_uses_the_borrower_name(on_disk):
    for v in on_disk["versions"]:
        text = json.dumps(v).lower()
        assert "sikorsky" not in text and "lockheed" not in text and "rms" not in text.split()


# ---------------------------------------------------------------- merge (sophia/stage-4): shared policy,
# summary table, --check, one family per company in the draw

POLICY_PATH = REPO_ROOT / "data/families/policies/RNW-1.yaml"


def test_shared_policy_fills_threshold_and_metric_per_family():
    policy = yaml.safe_load(POLICY_PATH.read_text())
    f001 = F.fill_policy(policy, 0.01, "net sales")
    other = F.fill_policy(policy, 0.05, "revenue")
    p1, p2 = (lambda pol: (pol["rules"][0]["text"], pol["rules"][1]["text"]))(f001)
    assert "current-year net sales - prior-year net sales" in p1 and "(+1.0%)" in p2
    assert "current-year revenue" in other["rules"][0]["text"] and "(+5.0%)" in other["rules"][1]["text"]
    assert [r["id"] for r in f001["rules"]] == [f"P{k}" for k in range(1, 11)]
    assert not any("{" in r["text"] for r in f001["rules"] + other["rules"])


def test_shared_policy_keeps_the_f001_wording(spec, on_disk):
    assert spec["policy"] == on_disk["policy"]
    assert on_disk["policy"]["rules"][1]["text"] == (
        "At the threshold (+1.0%) or above, the line is eligible to renew. Below it, decline, "
        "unless P6 requires escalation.")


def test_unfilled_policy_placeholder_is_rejected():
    policy = {"id": "X", "name": "x", "rules": [{"id": "P1", "text": "Use {metric} and {other}."}]}
    with pytest.raises(F.FamilyError, match="unfilled placeholder"):
        F.fill_policy(policy, 0.01, "net sales")


def test_spec_names_a_policy_and_holds_no_policy_text():
    raw = yaml.safe_load(SPEC_PATH.read_text())
    assert set(raw["policy"]) == {"id", "threshold", "metric"}


def test_load_spec_stops_on_a_missing_or_mismatched_policy(tmp_path):
    (tmp_path / "data/families/specs").mkdir(parents=True)
    (tmp_path / "data/families/policies").mkdir(parents=True)
    path = tmp_path / "data/families/specs/F009.yaml"
    path.write_text(yaml.safe_dump({"family_id": "F009", "policy": {"id": "NOPE", "threshold": 0.01,
                                                                   "metric": "sales"}}))
    with pytest.raises(F.FamilyError, match="not found"):
        F.load_spec(tmp_path, path)
    (tmp_path / "data/families/policies/NOPE.yaml").write_text(
        yaml.safe_dump({"id": "OTHER", "name": "x", "rules": []}))
    with pytest.raises(F.FamilyError, match="holds policy OTHER"):
        F.load_spec(tmp_path, path)


def test_provenance_hashes_the_spec_and_policy_files(spec):
    prov = spec["provenance"]
    assert prov["spec"] == "data/families/specs/F001.yaml"
    assert prov["policy"] == "data/families/policies/RNW-1.yaml"
    assert prov["spec_sha256"] == F._sha256(yaml.safe_load(SPEC_PATH.read_text()))
    policy = yaml.safe_load(POLICY_PATH.read_text())
    assert prov["policy_sha256"] == F._sha256(policy)
    changed = copy.deepcopy(policy)
    changed["rules"][2]["text"] += " Audited filings only."
    assert F._sha256(changed) != prov["policy_sha256"]


@needs_data
def test_family_json_carries_provenance_and_schema_2(on_disk, spec):
    assert on_disk["schema_version"] == 2 and on_disk["provenance"] == spec["provenance"]
    bad = copy.deepcopy(on_disk)
    del bad["provenance"]["policy_sha256"]
    assert "provenance: missing policy_sha256" in F.validate(bad)
    bad = copy.deepcopy(on_disk)
    bad["policy"]["rules"][0]["text"] = "Use {metric}."
    assert "policy: unfilled placeholder in the rules" in F.validate(bad)


@needs_data
def test_summary_table_has_one_row_per_version_matching_the_gold(on_disk):
    rows = F.summary_rows(on_disk)
    assert [r["version"].split()[0] for r in rows] == ["1", "2", "3", "4", "5a", "5b"]
    for r, v in zip(rows, on_disk["versions"]):
        classes = v["gold"]["action_classes"]
        assert r["turns"] == len(v["turns"])
        assert r["acceptable"] == ", ".join(f"`{a}`" for a in v["gold"]["acceptable_actions"])
        assert ("`renew`" in r["invalid_approval"]) == (classes["renew"] == "invalid_approval")
        assert r["record"] == ("yes" if v["gold"]["record_must_change"] else "no")
    v3 = rows[2]
    assert v3["acceptable"] == "`escalate`" and "`decline` skipped_restatement_rule" in v3["other"]
    review = F.render_review(on_disk)
    assert review.index("## Summary") < review.index("## Source table")
    assert sum(1 for line in review.splitlines() if line.startswith(("| 1 ", "| 2 ", "| 3 ", "| 4 ", "| 5"))) >= 6


def test_check_reports_missing_differing_and_orphan_files(tmp_path):
    good = tmp_path / "data/families/pilot/F001/family.json"
    differs = tmp_path / "docs/families/F001.md"
    missing = tmp_path / "data/families/draw_log.csv"
    orphan = tmp_path / "data/families/pilot/F002/family.json"
    for path, text in ((good, "same\n"), (differs, "old\n"), (orphan, "{}\n")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    outputs = {good: "same\n", differs: "new\n", missing: "x\n"}
    assert F.stale_outputs(tmp_path, outputs) == [
        "docs/families/F001.md: differs from a rebuild",
        "data/families/draw_log.csv: missing",
        "data/families/pilot/F002/family.json: no spec builds it",
    ]
    assert F.stale_outputs(tmp_path, {good: "same\n", differs: "old\n", orphan: "{}\n"}) == []


@needs_data
def test_check_mode_passes_on_the_committed_files_and_writes_nothing():
    before = {p: p.stat().st_mtime_ns for p in (JSON_PATH, REPO_ROOT / "docs/families/F001.md",
                                               REPO_ROOT / "data/families/draw_log.csv")}
    lines = []
    assert F.run(REPO_ROOT, log=lines.append, check=True) == 0
    assert lines[-1].startswith("OK: generated files match a rebuild")
    assert {p: p.stat().st_mtime_ns for p in before} == before


@needs_data
def test_draw_skips_companies_and_ids_already_used(spec):
    rows = F.read_pilot_growth(REPO_ROOT)
    convs = F.load_conversations(REPO_ROOT, {r["id"] for r in rows})
    order = F.draw_order(rows)
    log = F.run_draw("F002", order, convs, True, used_companies=frozenset({"LMT"}))
    assert not any(r["id"].startswith("Single_LMT/") for r in log) and log[-1]["result"] == "pass"
    kept = [r["id"] for r in order if r["company"] != "LMT" and F.metric_label(r) in F.ALLOWED_GROWTH_LABELS]
    assert [r["id"] for r in log] == kept[:len(log)]
    by_id = F.run_draw("F002", order, convs, False, used_ids=frozenset({spec["source_id"]}))
    assert by_id[-1]["id"] != spec["source_id"]
    assert spec["source_id"] not in {r["id"] for r in by_id}


@needs_data
def test_build_stops_when_a_later_spec_reuses_a_company(spec):
    with pytest.raises(F.FamilyError, match="already used by an earlier family"):
        F.build_from_spec(REPO_ROOT, spec, used_companies=frozenset({"LMT"}))


@needs_data
def test_no_used_companies_leaves_the_f001_draw_unchanged(built, spec):
    rows = F.read_pilot_growth(REPO_ROOT)
    convs = F.load_conversations(REPO_ROOT, {r["id"] for r in rows})
    assert F.run_draw("F001", F.draw_order(rows), convs, False, frozenset(), frozenset()) == built[1]


# Ivan's stricter checks are OFF (commented out in family.py). Their tests, from ivan/stage-4
# tests/test_family.py, go here when the team turns them on:
#
# def test_line_too_long_fails_closed():
#     table = [["", "a"]] + [[f"r{k}", str(1000 + 37 * k)] for k in range(F.MAX_LINE + 2)]
#     assert F.line_too_long(table, 1, 1)
#
# def test_derived_percent_catches_a_margin_row():
#     table = [["", "2009"], ["segment revenue", "$ 6305"], ["segment operating income", "900"],
#              ["segment operating income margin", "14.3% ( 14.3 % )"]]
#     assert F.derived_percent_hits(table, 2, 1)
#
# def test_round_threshold_to_half_a_point():
#     assert F.round_threshold(0.106) == pytest.approx(0.105) and F.round_threshold(0.10) == 0.10
