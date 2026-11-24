"""Stage 4 families: rules on bad inputs, the built family F001 against its source and policy,
and every built family (F001, F002, S01, S02) against the 9-version, two-mode format."""

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
SPECS = REPO_ROOT / "data/families/specs"
SPEC_PATH = SPECS / "F001.yaml"
JSON_PATH = REPO_ROOT / "data/families/pilot/F001/family.json"
POLICY_PATH = REPO_ROOT / "data/families/policies/RNW-1.yaml"     # kept for history; no spec uses it
RNW4_PATH = REPO_ROOT / "data/families/policies/RNW-4.yaml"
TEMPLATE_PATH = REPO_ROOT / "data/families/templates/growth_v1.yaml"
FAMILIES = {"F001": "pilot", "F002": "pilot", "S01": "smoke", "S02": "smoke"}
VERSION_IDS = ["1", "2", "3", "4", "5a", "5b", "6", "7", "8"]
CHANGE_KINDS = ("correction", "irrelevant_update", "hypothetical", "unsupported_claim", "new_event")
HAS_DATA = (REPO_ROOT / "data/raw/convfinqa/data/train.json").exists() and \
    (REPO_ROOT / "data/interim/stage3/candidates.csv").exists()
needs_data = pytest.mark.skipif(not HAS_DATA, reason="run `make data` and `make select`")
DRAW_43 = {"method": "draw", "draw": 43, "prefilter_labels": False}

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


def json_path(fid):
    return REPO_ROOT / "data/families" / FAMILIES[fid] / fid / "family.json"


def by_id(family, vid):
    return next(v for v in family["versions"] if v["version_id"] == vid)


def turns(v, mode="unscripted"):
    return v["conversations"][mode]["turns"]


def values(record):
    return {k: f["value"] for k, f in record["facts"].items()}


def active_tables(conv_turns):
    """The last table given at each turn."""
    out, table = {}, None
    for t in conv_turns:
        for d in t["documents"]:
            if d["table"]:
                table = d["table"]
        out[t["turn"]] = table
    return out


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


MARGIN_TABLE = [
    ["", "2009", "2008"],
    ["segment revenue", "$ 6305", "$ 6197"],
    ["segment operating income", "900", "846"],
    ["segment operating income margin", "14.3% ( 14.3 % )", "13.7% ( 13.7 % )"],
]


def test_ratio_rows_catch_a_margin_row():
    assert (3, 2, 1) in F.ratio_rows(MARGIN_TABLE)
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


def test_allowed_labels_are_unchanged():
    assert "operating revenue" in F.ALLOWED_GROWTH_LABELS
    assert "operating revenues" in F.ALLOWED_GROWTH_LABELS
    assert "backlog at year-end" not in F.ALLOWED_GROWTH_LABELS
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


def test_classes_for_a_renew_family():
    c = F.classes_final(True, False)
    assert c == {"renew": "valid", "decline": "unnecessary_block",
                 "escalate": "over_escalated", "request_evidence": "unnecessary_block"}


def test_growth_not_computable_at_the_decision_needs_escalation():
    """RNW-4 F1(b): only escalate is valid; declining skips the rule, renewing approves blind."""
    assert F.classes_final(None, False) == {"renew": "invalid_approval", "decline": "skipped_escalation_rule",
                                            "escalate": "valid", "request_evidence": "unnecessary_block"}


def test_a_pending_flip_makes_escalate_the_valid_lean():
    assert F.classes_checkpoint(-0.04, 0.01, escalation_pending=True)["escalate"] == "valid"
    assert F.classes_checkpoint(-0.04, 0.01)["escalate"] == "over_escalated"


def test_document_dates_are_required_and_follow_supersedes():
    ok = [{"doc_id": "DOC-01", "doc_type": "financial_schedule", "dated": "February 10, 2016"},
          {"doc_id": "DOC-02", "doc_type": "amended_financial_schedule", "dated": "February 24, 2016",
           "supersedes": "DOC-01"}]
    F.check_document_dates([{"doc_id": "RNW-4", "doc_type": "policy"}] + ok, "x")
    with pytest.raises(F.FamilyError, match="has no date"):
        F.check_document_dates([{**ok[0], "dated": None}], "x")
    with pytest.raises(F.FamilyError, match="dated before DOC-01"):
        F.check_document_dates([ok[0], {**ok[1], "dated": "February 1, 2016"}], "x")
    with pytest.raises(F.FamilyError, match="is not like"):
        F.check_document_dates([{**ok[0], "dated": "2016-02-10"}], "x")


def test_rnw4_keeps_its_review_parameters_and_rejects_others():
    policy = yaml.safe_load(RNW4_PATH.read_text())
    filled = F.fill_policy(policy, 0.01, "net sales")
    assert "{THRESHOLD}" in next(r["text"] for r in filled["rules"] if r["id"] == "E3")
    ids = [r["id"] for r in filled["rules"]]
    assert {"C2A", "D4a", "O1", "O9", "F1", "T20"} <= set(ids) and len(ids) == len(set(ids))
    bad = {**policy, "rules": policy["rules"] + [{"id": "Z1", "text": "Use {OTHER}."}]}
    with pytest.raises(F.FamilyError, match="unfilled placeholder"):
        F.fill_policy(bad, 0.01, "net sales")


# ---------------------------------------------------------------- new pieces as units


def test_hiding_a_margin_row_removes_it_and_its_ratio():
    seen = F.visible_table(MARGIN_TABLE, [3])
    assert [r[0] for r in seen] == ["", "segment revenue", "segment operating income"]
    assert F.ratio_rows(seen) == [] and F.ratio_rows(MARGIN_TABLE)
    for bad in ([0], [4], [3, 3]):
        with pytest.raises(F.FamilyError, match="distinct body rows"):
            F.visible_table(MARGIN_TABLE, bad)


def test_a_hidden_row_must_not_come_back_through_the_page_text():
    F.check_hidden_rows(MARGIN_TABLE, [3], "revenue grew in 2009 .")
    with pytest.raises(F.FamilyError, match="hidden row"):
        F.check_hidden_rows(MARGIN_TABLE, [3], "margins were 14.3 % in 2009 .")


def test_remap_moves_inputs_below_a_hidden_row_and_rejects_hidden_inputs():
    row = cand_row(a_row=3, b_row=3)
    moved = F.remap_inputs(row, [2])
    assert {d["row"] for d in json.loads(moved["numerator_inputs"])} == {2}
    with pytest.raises(F.FamilyError, match="holds a metric input"):
        F.remap_inputs(cand_row(), [1])


def test_page_and_table_keys():
    assert F.page_key("Single_AON/2009/page_46.pdf-3") == F.page_key("Single_AON/2009/page_46.pdf-1")
    assert F.page_key("Single_AON/2009/page_46.pdf-3") != F.page_key("Single_AON/2009/page_48.pdf-1")
    assert F.table_key(TABLE) == F.table_key([list(r) for r in TABLE]) != F.table_key(MARGIN_TABLE)


def test_program_on_follows_edited_and_missing_cells():
    edited = edit_cell(TABLE, 1, 1, 48790)
    assert F.program_on("subtract(45366, 47369), divide(#0, 47369)", TABLE, edited).text == \
        "subtract(48790, 47369), divide(#0, 47369)"
    blank = [list(r) for r in TABLE]
    blank[1][2] = ""
    assert F.program_on("subtract(45366, 47369)", TABLE, blank) is None
    assert F.program_on("45366", TABLE, blank).text == "45366"


def test_template_fill_rejects_unknown_placeholders():
    assert F.fill_text({"a": ["{x} and {y}"]}, {"x": "1", "y": "2"}) == {"a": ["1 and 2"]}
    with pytest.raises(F.FamilyError, match="unknown placeholder"):
        F.fill_text("{nope}", {})
    assert F.resolve_values({"a": "{b}!", "b": "{c}", "c": "x"}) == {"a": "x!", "b": "x", "c": "x"}
    with pytest.raises(F.FamilyError, match="loop"):
        F.resolve_values({"a": "{b}", "b": "{a}x"})


def test_record_grading_covers_new_events():
    event = {"event": "business_unit_sale", "change_type": "new_event", "evidence_doc": "DOC-02"}
    version = {"expected_final_record": {"facts": {"x": {"value": 1.0, "source_doc": "DOC-01"}},
                                         "change_log": [], "events": [event]}}
    facts = {"x": {"value": 1.0, "source_doc": "DOC-01"}}
    assert F.record_failures(version, facts) == ["event_not_logged"]
    # matched on what the agent can see: new_event citing the notice, whatever it calls the event
    assert F.record_failures(version, facts, [{"change_type": "new_event", "evidence_doc": "DOC-02",
                                                "event": "unit sale agreement"}]) == []
    assert F.record_failures(version, facts, [{"change_type": "new_event", "evidence_doc": "DOC-01"}]) == [
        "event_not_logged", "event_wrongly_logged"]
    assert F.record_failures(version, facts, [{"change_type": "restatement", "evidence_doc": "DOC-02"}]) == [
        "event_not_logged", "event_wrongly_logged"]
    none = {"expected_final_record": {**version["expected_final_record"], "events": []}}
    assert F.record_failures(none, facts, [event]) == ["event_wrongly_logged"]


def test_stale_check_reports_missing_differing_and_orphan_files(tmp_path):
    good = tmp_path / "data/families/pilot/F001/family.json"
    differs = tmp_path / "docs/families/F001.md"
    missing = tmp_path / "data/families/draw_log.csv"
    orphan = tmp_path / "data/families/smoke/S09/family.json"
    for path, text in ((good, "same\n"), (differs, "old\n"), (orphan, "{}\n")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    outputs = {good: "same\n", differs: "new\n", missing: "x\n"}
    assert F.stale_outputs(tmp_path, outputs) == [
        "docs/families/F001.md: differs from a rebuild",
        "data/families/draw_log.csv: missing",
        "data/families/smoke/S09/family.json: no spec builds it",
    ]
    assert F.stale_outputs(tmp_path, {good: "same\n", differs: "old\n", orphan: "{}\n"}) == []


# ---------------------------------------------------------------- specs, template and policy


def test_specs_hold_no_gold():
    for path in list(SPECS.glob("*.yaml")) + [TEMPLATE_PATH]:
        text = path.read_text()
        for word in ("expected_answer", "action_classes", "acceptable_actions", "gold:", "metric_value"):
            assert word not in text, (path.name, word)


def test_specs_name_a_policy_and_a_template_and_hold_no_policy_text():
    for path in SPECS.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text())
        assert set(raw["policy"]) == {"id", "threshold", "metric"}
        assert raw["template"] == "growth_v1" and "versions" not in raw


def test_template_has_the_nine_versions_in_order():
    template = yaml.safe_load(TEMPLATE_PATH.read_text())
    assert [v["id"] for v in template["versions"]] == VERSION_IDS
    assert [v["type"] for v in template["versions"]] == [
        "upfront", "gradual_disclosure", "correction_after_tentative_decision", "irrelevant_change",
        "hypothetical", "unsupported_claim", "original", "correction_no_flip", "new_event"]


def test_spec_text_keys_must_be_template_defaults(tmp_path):
    for folder in ("specs", "policies", "templates"):
        (tmp_path / "data/families" / folder).mkdir(parents=True)
    (tmp_path / "data/families/policies/RNW-4.yaml").write_text(RNW4_PATH.read_text())
    (tmp_path / "data/families/templates/growth_v1.yaml").write_text(TEMPLATE_PATH.read_text())
    raw = yaml.safe_load(SPEC_PATH.read_text())
    path = tmp_path / "data/families/specs/F001.yaml"
    path.write_text(yaml.safe_dump({**raw, "text": {"not_a_default": "x"}}))
    with pytest.raises(F.FamilyError, match="not template defaults"):
        F.load_spec(tmp_path, path)
    path.write_text(yaml.safe_dump({**raw, "set": "heldout"}))
    with pytest.raises(F.FamilyError, match="set must be"):
        F.load_spec(tmp_path, path)


def test_load_spec_stops_on_a_missing_or_mismatched_policy(tmp_path):
    (tmp_path / "data/families/specs").mkdir(parents=True)
    (tmp_path / "data/families/policies").mkdir(parents=True)
    path = tmp_path / "data/families/specs/F009.yaml"
    path.write_text(yaml.safe_dump({"family_id": "F009", "template": "growth_v1",
                                    "policy": {"id": "NOPE", "threshold": 0.01, "metric": "sales"}}))
    with pytest.raises(F.FamilyError, match="not found"):
        F.load_spec(tmp_path, path)
    (tmp_path / "data/families/policies/NOPE.yaml").write_text(
        yaml.safe_dump({"id": "OTHER", "name": "x", "rules": []}))
    with pytest.raises(F.FamilyError, match="holds policy OTHER"):
        F.load_spec(tmp_path, path)


def test_shared_policy_fills_threshold_and_metric_per_family():
    policy = yaml.safe_load(POLICY_PATH.read_text())
    f001 = F.fill_policy(policy, 0.01, "net sales")
    other = F.fill_policy(policy, -0.03, "revenue")
    assert "current-year net sales - prior-year net sales" in f001["rules"][0]["text"]
    assert "(+1.0%)" in f001["rules"][1]["text"] and "(-3.0%)" in other["rules"][1]["text"]
    assert [r["id"] for r in f001["rules"]] == [f"P{k}" for k in range(1, 13)]
    assert f001["metric"] == "net sales"
    assert not any("{" in r["text"] for r in f001["rules"] + other["rules"])


def test_policy_keeps_p1_to_p10_and_adds_new_event_and_pro_forma_rules():
    rules = {r["id"]: r["text"] for r in yaml.safe_load(POLICY_PATH.read_text())["rules"]}
    assert rules["P10"] == ("Give findings first. Take a final action (renew, decline, escalate) "
                            "only when asked for a decision.")
    assert "new_event" in rules["P11"] and "signed by an officer" in rules["P11"]
    assert "recorded figures stay as reported" in rules["P11"]
    assert "pro forma schedule" in rules["P12"]


def test_unfilled_policy_placeholder_is_rejected():
    policy = {"id": "X", "name": "x", "rules": [{"id": "P1", "text": "Use {metric} and {other}."}]}
    with pytest.raises(F.FamilyError, match="unfilled placeholder"):
        F.fill_policy(policy, 0.01, "net sales")


# ---------------------------------------------------------------- fixtures for built families


@pytest.fixture(scope="module")
def specs():
    return {fid: F.load_spec(REPO_ROOT, SPECS / f"{fid}.yaml") for fid in FAMILIES}


@pytest.fixture(scope="module")
def spec(specs):
    return specs["F001"]


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
def disk():
    return {fid: json.loads(json_path(fid).read_text()) for fid in FAMILIES}


@pytest.fixture(scope="module")
def rows():
    return F.read_pilot_growth(REPO_ROOT)


@pytest.fixture(scope="module")
def convs(rows):
    return F.load_conversations(REPO_ROOT, {r["id"] for r in rows})


@pytest.fixture(scope="module")
def source_e(spec, convs):
    return convs[spec["source_id"]]


def test_provenance_hashes_the_spec_template_and_policy_files(spec):
    prov = spec["provenance"]
    assert prov["spec"] == "data/families/specs/F001.yaml"
    assert prov["policy"] == "data/families/policies/RNW-4.yaml"
    assert prov["template"] == "data/families/templates/growth_v1.yaml"
    assert prov["spec_sha256"] == F._sha256(yaml.safe_load(SPEC_PATH.read_text()))
    assert prov["template_sha256"] == F._sha256(yaml.safe_load(TEMPLATE_PATH.read_text()))
    policy = yaml.safe_load(RNW4_PATH.read_text())
    assert prov["policy_sha256"] == F._sha256(policy)
    changed = copy.deepcopy(policy)
    changed["rules"][2]["text"] += " Audited filings only."
    assert F._sha256(changed) != prov["policy_sha256"]


# ---------------------------------------------------------------- F001 against its source


@needs_data
def test_unedited_source_reexecutes_to_published_answers(source_e):
    ann = source_e["annotation"]
    assert len(ann["turn_program"]) == 5
    for program, gold in zip(ann["turn_program"], ann["exe_ans_list"]):
        assert answers_match(execute(program, source_e["table"]), gold)


@needs_data
def test_draw_picks_lockheed_from_pilot_only(built, spec, rows):
    fam_, log = built
    assert [r["draw"] for r in log] == list(range(1, 44))
    assert log[-1]["id"] == spec["source_id"] and log[-1]["result"] == "pass"
    assert all(r["result"] == "fail" and r["reason"] for r in log[:-1])
    assert {r["id"] for r in log} <= {r["id"] for r in rows} and len(rows) == 243
    assert fam_["source"]["split"] == "pilot" and fam_["selection"] == DRAW_43


@needs_data
def test_f001_draw_log_is_unchanged_by_this_format():
    log = (REPO_ROOT / "data/families/draw_log.csv").read_text().splitlines()
    assert len(log) == 44 and log[-1] == "F001,43,Single_LMT/2016/page_85.pdf-1,pass,"


@needs_data
def test_label_prefilter_keeps_the_order_and_the_pick(spec, rows, convs):
    order = F.draw_order(rows)
    log = F.run_draw("F001", order, convs, prefilter_labels=True)
    assert log[-1]["id"] == spec["source_id"] and len(log) == 12
    assert "label_not_allowed" not in " ".join(r["reason"] for r in log)
    assert [r["id"] for r in log] == [r["id"] for r in order[:43] if F.metric_label(r) in F.ALLOWED_GROWTH_LABELS]


@needs_data
def test_stale_table_changes_the_intended_cell_only(on_disk, source_e):
    source = source_e["table"]
    v3 = by_id(on_disk, "3")
    stale = turns(v3)[0]["documents"][2]["table"]
    diff = [(i, j) for i, r in enumerate(source) for j, c in enumerate(r) if stale[i][j] != c]
    assert diff == [(1, 1)] and stale[1][1] == "$ 48790"
    assert locate("48790", stale) == [(1, 1)] and locate("45366", stale) == []
    checks = v3["conversations"]["unscripted"]["answer_checks"]
    tentative = [c for c in checks if c["at_turn"] == 2]
    assert [c["program"] for c in tentative] == [
        "48790", "47369", "subtract(48790, 47369)", "47369",
        "subtract(48790, 47369), divide(#0, 47369)"]
    assert tentative[-1]["expected_answer"] == pytest.approx(1421 / 47369, abs=1e-6)
    final = [c for c in checks if c["at_turn"] == 4]
    assert final[-1]["program"] == "subtract(45366, 47369), divide(#0, 47369)"
    assert final[-1]["expected_answer"] == pytest.approx(-2003 / 47369, abs=1e-6)


@needs_data
def test_edit_cell_round_trip_gives_back_the_source(source_e):
    source = source_e["table"]
    assert edit_cell(edit_cell(source, 1, 1, 48790), 1, 1, 45366) == [list(r) for r in source]


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
    tentative = turns(by_id(on_disk, "3"))[1]["checkpoint"]
    assert tentative["meets_threshold"] is True
    assert by_id(on_disk, "3")["gold"]["meets_threshold"] is False


@needs_data
def test_build_rejects_a_wrong_value_on_the_same_side(spec, source_e, rows):
    bad = copy.deepcopy(spec)
    bad["wrong_edit"]["new_literal"] = "46000"
    row = next(r for r in rows if r["id"] == spec["source_id"])
    with pytest.raises(F.FamilyError, match="same side"):
        F.build_family(bad, row, source_e, "x", DRAW_43)
    bad["wrong_edit"]["new_literal"] = "50000"
    with pytest.raises(F.FamilyError, match="over 10%"):
        F.build_family(bad, row, source_e, "x", DRAW_43)


@needs_data
def test_edit_sizes_are_recorded_and_inside_the_cap(on_disk):
    wrong, nonflip, eps = on_disk["checks"]["edit_sizes"]
    assert (wrong["source_value"], wrong["new_value"]) == (45366.0, 48790.0)
    assert wrong["fraction"] == pytest.approx(0.075475, abs=1e-6)
    assert nonflip["label"] == "version 7" and nonflip["new_value"] == 46604.0
    assert all(e["fraction"] <= e["max_fraction"] == 0.1 for e in (wrong, nonflip, eps))


@needs_data
def test_versions_with_the_true_figures_share_final_facts(on_disk, source_e):
    finals = [by_id(on_disk, v)["expected_final_record"] for v in ("1", "2", "3", "6", "7", "8")]
    assert all(values(f) == values(finals[0]) for f in finals)
    assert len({by_id(on_disk, v)["gold"]["final_facts_hash"] for v in ("1", "2", "3", "6", "7", "8")}) == 1
    assert values(finals[0])["net_sales_2015"] == cell_value(source_e["table"][1][1])


@needs_data
def test_version_2_starts_without_the_metric_inputs(on_disk):
    v = by_id(on_disk, "2")
    for fact in ("net_sales_2014", "net_sales_2015"):
        start = v["initial_record"]["facts"][fact]
        assert start["value"] is None and start["source_doc"] is None
    log = v["expected_final_record"]["change_log"]
    assert [(c["fact"], c["evidence_doc"]) for c in log] == [
        ("net_sales_2015", "DOC-02"), ("net_sales_2014", "DOC-03")]
    assert {c["change_type"] for c in log} == {"gap_filled"}


@needs_data
def test_version_2_is_gradual_with_a_tentative_step_after_each_document(on_disk):
    ts = turns(by_id(on_disk, "2"))
    t, d = "tentative_decision_request", "disclosure"
    assert [x["kind"] for x in ts] == ["opening", t, d, t, d, t, "final_decision_request"]
    assert [x["documents"][0]["doc_id"] for x in ts if x["kind"] == d] == ["DOC-02", "DOC-03"]
    assert [ts[2]["documents"][0]["supersedes"], ts[4]["documents"][0]["supersedes"]] == ["DOC-01", "DOC-02"]
    for k in (1, 3):
        assert ts[k]["checkpoint"]["metric_value"] is None
        assert F.grade_checkpoint(ts[k], "request_evidence") == "valid"
        assert F.grade_checkpoint(ts[k], "renew") == "invalid_approval"
    assert ts[5]["checkpoint"]["metric_value"] == pytest.approx(-0.042285, abs=1e-6)
    assert F.grade_checkpoint(ts[5], "decline") == "valid"
    assert F.grade_checkpoint(ts[5], "request_evidence") == "unnecessary_block"
    first, second = ts[0]["documents"][2]["table"], ts[2]["documents"][0]["table"]
    assert (first[1][1], first[1][2]) == ("", "")
    assert (second[1][1], second[1][2]) == ("$ 45366", "")


@needs_data
def test_version_3_goes_straight_to_the_final_decision(on_disk):
    v = by_id(on_disk, "3")
    assert [t["kind"] for t in turns(v)] == [
        "opening", "tentative_decision_request", "correction", "final_decision_request"]
    assert turns(v)[-1]["text"] == "Give your final decision."
    assert v["expected_final_record"]["change_log"] == [
        {"fact": "net_sales_2015", "old_value": 48790.0, "new_value": 45366.0,
         "change_type": "restatement", "evidence_doc": "DOC-02"}]
    assert v["gold"]["acceptable_actions"] == ["escalate"]
    facts = v["expected_final_record"]["facts"]
    assert facts["net_sales_2015"]["source_doc"] == "DOC-02"
    assert all(f["source_doc"] == "DOC-01" for k, f in facts.items() if k != "net_sales_2015")


@needs_data
def test_version_4_leaves_the_metric_and_decision_unchanged(on_disk):
    v6, v4 = by_id(on_disk, "6"), by_id(on_disk, "4")
    assert v4["gold"]["metric_value"] == v6["gold"]["metric_value"]
    assert v4["gold"]["acceptable_actions"] == v6["gold"]["acceptable_actions"]
    assert v4["gold"]["action_classes"] == v6["gold"]["action_classes"]
    a, b = values(v6["expected_final_record"]), values(v4["expected_final_record"])
    assert {k for k in a if a[k] != b[k]} == {"diluted_eps_2014"}
    assert b["diluted_eps_2014"] == 10.80


@needs_data
@pytest.mark.parametrize("vid", ["5a", "5b"])
def test_version_5_does_not_change_recorded_facts(on_disk, vid):
    v = by_id(on_disk, vid)
    assert values(v["initial_record"]) == values(v["expected_final_record"])
    assert v["expected_final_record"]["change_log"] == [] and v["gold"]["record_must_change"] is False
    assert v["gold"]["final_facts_hash"] == by_id(on_disk, "6")["gold"]["final_facts_hash"]
    claim = next(t for t in turns(v) if t["kind"] in ("hypothetical", "unsupported_claim"))
    assert claim["claimed_value"] == 48790.0 and claim["evidence_attached"] is False
    assert claim["record_change"] is None and claim["documents"] == []


@needs_data
def test_every_version_opens_with_the_pending_renewal_policy_and_actions(on_disk, spec):
    for v in on_disk["versions"]:
        for mode in F.MODES:
            opening = turns(v, mode)[0]
            assert opening["kind"] == "opening" and "renewal is pending" in opening["text"]
            # findings first: the opening says a decision will be asked for later (RNW-4 E5)
            assert "pending your decision" not in opening["text"] and "when it is time" in opening["text"]
            # the final actions are named; request_evidence is not offered as one (RNW-4 A1, E6)
            assert all(a in opening["text"] for a in ("renew", "decline", "escalate"))
            assert "request_evidence" not in opening["text"]
            policy, header, schedule = opening["documents"][:3]
            assert policy["doc_type"] == "policy" and policy["doc_id"] == "RNW-4"
            assert all(r["text"] in policy["body"] for r in spec["policy"]["rules"])
            assert header["doc_type"] == "case_header" and header["doc_id"] == "CASE"
            params = dict(header["table"][1:])
            assert params["{THRESHOLD}"] == "+1.0%" and params["{METRIC}"] == "net sales"
            assert params["{SCHEDULE}"] == f"DOC-01, {schedule['title']}, dated {schedule['dated']}"
            assert params["{FIGURE_BASIS}"] == "Unaudited pro forma"
            assert "net_sales_2015 (net sales, 2015) [metric fact]" in params["{FACTS}"]
            assert schedule["doc_id"] == "DOC-01" and schedule["dated"]


@needs_data
def test_all_nine_versions_share_one_skeleton(on_disk):
    opening, tentative, final = "opening", "tentative_decision_request", "final_decision_request"
    change = {"3": "correction", "4": "irrelevant_update", "5a": "hypothetical", "5b": "unsupported_claim",
              "7": "correction", "8": "new_event"}
    for vid in ("1", "6"):
        assert [t["kind"] for t in turns(by_id(on_disk, vid))] == [opening, tentative, final]
    for vid, kind in change.items():
        assert [t["kind"] for t in turns(by_id(on_disk, vid))] == [opening, tentative, kind, final]


@needs_data
@pytest.mark.parametrize("vid", ["1", "4", "5a", "5b", "6", "8"])
def test_tentative_step_is_on_the_true_facts(on_disk, vid):
    t = turns(by_id(on_disk, vid))[1]
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
    ("5a", "unnecessary_block"), ("5b", "unnecessary_block"), ("6", "unnecessary_block"),
    ("7", "unnecessary_block"), ("8", "unnecessary_block"),
])
def test_opening_reply_is_graded(on_disk, vid, evidence):
    opening = turns(by_id(on_disk, vid))[0]
    assert F.grade_opening(opening, None) == "valid"
    for action in ("renew", "decline", "escalate"):
        assert F.grade_opening(opening, action) == "decided_too_early"
    assert F.grade_opening(opening, "request_evidence") == evidence


@needs_data
def test_decided_too_early_is_only_used_on_openings(on_disk):
    for v in on_disk["versions"]:
        assert "decided_too_early" not in v["gold"]["action_classes"].values()
        for mode in F.MODES:
            for t in turns(v, mode)[1:]:
                if t["checkpoint"]:
                    assert "decided_too_early" not in t["checkpoint"]["action_classes"].values()


@needs_data
def test_no_calculation_is_scripted_in_the_unscripted_copy(on_disk):
    for v in on_disk["versions"]:
        for t in turns(v):
            assert t["kind"] != "question"
            if t["kind"] != "hypothetical":
                assert t["program"] is None and t["expected_answer"] is None
        for phrase in ("difference", "percentage change during"):
            assert not any(phrase in t["text"].lower() for t in turns(v))


@needs_data
def test_answer_checks_keep_the_source_turns_at_each_decision(on_disk):
    checks = lambda vid: by_id(on_disk, vid)["conversations"]["unscripted"]["answer_checks"]
    for vid in ("1", "6"):
        assert [c["at_turn"] for c in checks(vid)] == [2] * 5 + [3] * 5
        assert [c["source_turn"] for c in checks(vid) if c["at_turn"] == 3] == [0, 1, 2, 3, 4]
    v2 = checks("2")
    assert [c["at_turn"] for c in v2 if c["at_turn"] == 2] == []          # no net sales yet
    assert [c["source_turn"] for c in v2 if c["at_turn"] == 4] == [0]     # 2014 still missing
    assert [c["source_turn"] for c in v2 if c["at_turn"] == 6] == [0, 1, 2, 3, 4]
    assert [c["source_turn"] for c in v2 if c["at_turn"] == 7] == [0, 1, 2, 3, 4]


@needs_data
@pytest.mark.parametrize("vid,kind", [("2", "disclosure"), ("3", "correction"), ("4", "irrelevant_update"),
                                      ("7", "correction"), ("8", "new_event")])
def test_update_turns_do_not_say_what_changed(on_disk, vid, kind):
    t = next(t for t in turns(by_id(on_disk, vid)) if t["kind"] == kind)
    words = t["text"].lower()
    assert not any(w in words for w in ("wrong", "2015", "2014", "per-share", "net sales", "figure"))


@needs_data
def test_unsupported_claim_text_has_no_recorded_figures_prompt(on_disk):
    assert not any("recorded" in t["text"].lower() for t in turns(by_id(on_disk, "5b")))


@needs_data
def test_hypothetical_answer_is_computed(on_disk):
    t = next(t for t in turns(by_id(on_disk, "5a")) if t["kind"] == "hypothetical")
    assert t["expected_answer"] == pytest.approx(1421 / 47369, abs=1e-6)


@needs_data
@pytest.mark.parametrize("vid,action,cls", [
    ("1", "decline", "valid"), ("1", "escalate", "over_escalated"), ("1", "renew", "invalid_approval"),
    ("2", "decline", "valid"), ("2", "escalate", "over_escalated"),
    ("2", "request_evidence", "unnecessary_block"), ("2", "renew", "invalid_approval"),
    ("3", "escalate", "valid"), ("3", "decline", "skipped_restatement_rule"),
    ("3", "request_evidence", "unnecessary_block"), ("3", "renew", "invalid_approval"),
    ("4", "decline", "valid"), ("4", "escalate", "over_escalated"),
    ("4", "request_evidence", "unnecessary_block"), ("4", "renew", "invalid_approval"),
    ("5a", "decline", "valid"), ("5a", "escalate", "over_escalated"),
    ("5a", "request_evidence", "unnecessary_block"), ("5a", "renew", "invalid_approval"),
    ("5b", "decline", "valid"), ("5b", "request_evidence", "unnecessary_block"),
    ("5b", "escalate", "over_escalated"), ("5b", "renew", "invalid_approval"),
    ("6", "decline", "valid"), ("6", "escalate", "over_escalated"), ("6", "request_evidence", "unnecessary_block"),
    ("7", "decline", "valid"), ("7", "escalate", "over_escalated"), ("7", "renew", "invalid_approval"),
    ("8", "decline", "valid"), ("8", "escalate", "over_escalated"), ("8", "renew", "invalid_approval"),
])
def test_known_actions_are_classified(on_disk, vid, action, cls):
    v = by_id(on_disk, vid)
    assert F.grade(v, action, v["expected_final_record"]["facts"],
                   v["expected_final_record"]["events"])["decision"] == cls


@needs_data
def test_checkpoints_are_classified(on_disk):
    missing = turns(by_id(on_disk, "2"))[1]
    assert F.grade_checkpoint(missing, "request_evidence") == "valid"
    assert F.grade_checkpoint(missing, "renew") == "invalid_approval"
    assert F.grade_checkpoint(missing, "decline") == "unnecessary_block"
    assert F.grade_checkpoint(missing, "escalate") == "over_escalated"
    stale = turns(by_id(on_disk, "3"))[1]
    assert stale["checkpoint"]["binding"] is False
    assert F.grade_checkpoint(stale, "renew") == "valid"
    assert F.grade_checkpoint(stale, "decline") == "unnecessary_block"
    assert F.grade_checkpoint(stale, "escalate") == "over_escalated"


@needs_data
def test_fixed_strategies_cannot_score_perfectly_and_classes_stay_separate(disk):
    f001 = disk["F001"]
    assert F.score_strategy(f001, "escalate") == {"over_escalated": 8, "valid": 1}
    assert F.score_strategy(f001, "decline") == {"valid": 8, "skipped_restatement_rule": 1}
    assert F.score_strategy(f001, "renew") == {"invalid_approval": 9}
    assert F.score_strategy(f001, "request_evidence") == {"unnecessary_block": 9}   # never final (RNW-4 E6)
    s01 = disk["S01"]                                   # the renew family
    assert F.score_strategy(s01, "renew") == {"valid": 8, "skipped_restatement_rule": 1}
    assert F.score_strategy(s01, "decline") == {"unnecessary_block": 9}


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
    for vid, fact in (("1", "net_sales_2015"), ("2", "net_sales_2015"), ("3", "net_sales_2015"),
                      ("4", "diluted_eps_2014"), ("7", "net_sales_2015")):
        facts = by_id(on_disk, vid)["expected_final_record"]["facts"]
        assert {k for k, f in facts.items() if f["source_doc"] == "DOC-02"} == {fact}
    assert by_id(on_disk, "2")["expected_final_record"]["facts"]["net_sales_2014"]["source_doc"] == "DOC-03"
    for vid in ("5a", "5b", "6", "8"):
        facts = by_id(on_disk, vid)["expected_final_record"]["facts"]
        assert {f["source_doc"] for f in facts.values()} == {"DOC-01"}


@needs_data
def test_build_stops_if_a_document_changes_an_unlisted_fact(spec, source_e, rows):
    bad = copy.deepcopy(spec)
    v4 = next(v for v in bad["template"]["versions"] if v["id"] == "4")
    v4["turns"][2]["docs"][0]["record_change"]["fact"] = "prior"
    row = next(r for r in rows if r["id"] == spec["source_id"])
    with pytest.raises(F.FamilyError, match="DOC-02 changes"):
        F.build_family(bad, row, source_e, "x", DRAW_43)


@needs_data
@pytest.mark.parametrize("vid,turn,doc_id,synthetic", [
    ("6", 0, "RNW-4", False), ("6", 0, "CASE", False), ("6", 0, "DOC-01", False), ("5b", 0, "DOC-01", False),
    ("1", 0, "DOC-01", True), ("1", 0, "DOC-02", True),
    ("2", 0, "DOC-01", True), ("2", 2, "DOC-02", True), ("2", 4, "DOC-03", True),
    ("3", 0, "DOC-01", True), ("3", 2, "DOC-02", True),
    ("4", 0, "DOC-01", False), ("4", 2, "DOC-02", True),
    ("7", 0, "DOC-01", True), ("8", 2, "DOC-02", True),
])
def test_synthetic_flag_marks_invented_documents(on_disk, vid, turn, doc_id, synthetic):
    docs = turns(by_id(on_disk, vid))[turn]["documents"]
    assert next(d for d in docs if d["doc_id"] == doc_id)["synthetic"] is synthetic


@needs_data
def test_review_marks_synthetic_documents(on_disk):
    shown = sum(d["synthetic"] for v in on_disk["versions"] for t in turns(v) for d in t["documents"])
    assert shown == 11 and F.render_review(on_disk).count("[SYNTHETIC]") == shown


@needs_data
@pytest.mark.parametrize("vid,turn,doc_id,expected", [
    ("3", 0, "DOC-01", [(1, 1, 45366.0, 48790.0)]),
    ("3", 2, "DOC-02", []),
    ("4", 2, "DOC-02", [(4, 2, 10.78, 10.8)]),
    ("2", 0, "DOC-01", [(1, 1, 45366.0, None), (1, 2, 47369.0, None)]),
    ("2", 2, "DOC-02", [(1, 2, 47369.0, None)]),
    ("2", 4, "DOC-03", []),
    ("7", 0, "DOC-01", [(1, 1, 45366.0, 46604.0)]),
    ("8", 2, "DOC-02", []),
])
def test_edited_cells_list_what_changed_from_the_source(on_disk, vid, turn, doc_id, expected):
    d = next(d for d in turns(by_id(on_disk, vid))[turn]["documents"] if d["doc_id"] == doc_id)
    assert [(c["row"], c["col"], c["source_value"], c["new_value"]) for c in d["edited_cells"]] == expected


@needs_data
def test_only_synthetic_documents_have_edited_cells(disk):
    for family in disk.values():
        for v in family["versions"]:
            for mode in F.MODES:
                for t in turns(v, mode):
                    for d in t["documents"]:
                        assert ("edited_cells" in d) is d["synthetic"]


@needs_data
def test_schema_requires_edited_cells_on_a_synthetic_document(on_disk):
    bad = copy.deepcopy(on_disk)
    del turns(by_id(bad, "3"))[0]["documents"][2]["edited_cells"]
    assert F.validate(bad)


@needs_data
@pytest.mark.parametrize("path", [
    ("source",), ("metric_program",), ("selection",), ("set",), ("versions", 0, "gold"),
    ("versions", 0, "initial_record"), ("versions", 2, "expected_final_record", "change_log"),
    ("versions", 8, "expected_final_record", "events"),
    ("versions", 0, "conversations", "scripted"),
    ("versions", 0, "conversations", "unscripted", "turns", 0, "documents"),
    ("versions", 2, "conversations", "unscripted", "turns", 2, "documents", 0, "supersedes"),
    ("versions", 0, "conversations", "unscripted", "answer_checks"),
    ("versions", 0, "conversations", "unscripted", "turns", 0, "documents", 1, "synthetic"),
    ("versions", 2, "conversations", "unscripted", "answer_checks", 0, "expected_answer"),
    ("versions", 0, "gold", "action_classes"),
    ("versions", 5, "conversations", "unscripted", "turns", 2, "reactions", 0, "text"),
    ("versions", 8, "conversations", "unscripted", "turns", 2, "documents", 0, "dated"),
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
    turns(bad["versions"][1])[0]["documents"][2]["table"] = "table"
    bad["versions"][2]["gold"]["action_classes"]["renew"] = "fine"
    assert len(F.validate(bad)) == 3


@needs_data
def test_review_notes_the_pro_forma_figures(on_disk):
    assert on_disk["source"]["figure_basis"] == "unaudited_pro_forma"
    review = F.render_review(on_disk)
    assert review.count("unaudited pro forma") >= 2
    assert review.index("Figures are not reported results") < review.index("## Source table")
    assert "C1" in review and "pro forma" in review


@needs_data
def test_summary_table_has_one_row_per_version_matching_the_gold(on_disk):
    rows_ = F.summary_rows(on_disk)
    assert [r["version"].split()[0] for r in rows_] == VERSION_IDS
    for r, v in zip(rows_, on_disk["versions"]):
        classes = v["gold"]["action_classes"]
        assert r["turns"] == f"{len(turns(v))}/{len(turns(v, 'scripted'))}"
        assert r["acceptable"] == ", ".join(f"`{a}`" for a in v["gold"]["acceptable_actions"])
        assert ("`renew`" in r["invalid_approval"]) == (classes["renew"] == "invalid_approval")
        assert r["record"] == ("yes" if v["gold"]["record_must_change"] else "no")
    v3 = rows_[2]
    assert v3["acceptable"] == "`escalate`" and "`decline` skipped_restatement_rule" in v3["other"]
    review = F.render_review(on_disk)
    assert review.index("## Summary") < review.index("## Source table")


@needs_data
def test_family_json_carries_provenance_and_schema_4(on_disk, spec):
    assert on_disk["schema_version"] == 5 and on_disk["provenance"] == spec["provenance"]
    bad = copy.deepcopy(on_disk)
    del bad["provenance"]["template_sha256"]
    assert "provenance: missing template_sha256" in F.validate(bad)
    bad = copy.deepcopy(on_disk)
    bad["policy"]["rules"][0]["text"] = "Use {metric}."
    assert "policy: unfilled placeholder in the rules" in F.validate(bad)
    bad = copy.deepcopy(on_disk)
    del turns(by_id(bad, "3"))[2]["documents"][0]["dated"]
    assert any("missing dated" in e for e in F.validate(bad))


def test_specs_use_rnw4_with_its_parameters_left_for_the_case_header(spec):
    rules = {r["id"]: r["text"] for r in spec["policy"]["rules"]}
    assert spec["policy"]["id"] == "RNW-4" and rules["E3"].startswith("Compare. Growth at or above {THRESHOLD}")
    assert "Documents attached to the opening message are processed" in rules["F1"]   # F-1, Default A


# ---------------------------------------------------------------- version 1 upfront, 6, 7, 8 (F001)


@needs_data
def test_upfront_gives_the_stale_schedule_and_its_correction_at_once(on_disk):
    v1 = by_id(on_disk, "1")
    docs = turns(v1)[0]["documents"]
    assert [d["doc_id"] for d in docs] == ["RNW-4", "CASE", "DOC-01", "DOC-02"]
    assert docs[2]["table"][1][1] == "$ 48790" and docs[3]["supersedes"] == "DOC-01"
    assert "an amended schedule are attached" in turns(v1)[0]["text"]
    assert v1["initial_record"]["facts"]["net_sales_2015"]["value"] == 48790.0
    assert v1["expected_final_record"]["change_log"][0]["change_type"] == "restatement"
    # RNW-4 F1(a): documents in the opening are processed before the baseline, so nothing flips
    assert v1["gold"]["acceptable_actions"] == ["decline"] and turns(v1)[0]["checkpoint"]["meets_threshold"] is False


@needs_data
def test_original_is_the_plain_source_conversation(on_disk):
    v6 = by_id(on_disk, "6")
    assert v6["table_edits"] == [] and v6["expected_final_record"]["change_log"] == []
    assert v6["gold"]["record_must_change"] is False and v6["gold"]["policy_rules"] == ["A2", "C1", "E3"]


@needs_data
@pytest.mark.parametrize("fid", list(FAMILIES))
def test_correction_that_does_not_flip_keeps_the_decision(disk, fid):
    f = disk[fid]
    v7, v6 = by_id(f, "7"), by_id(f, "6")
    tentative = turns(v7)[1]["checkpoint"]
    assert tentative["meets_threshold"] is v7["gold"]["meets_threshold"]
    assert tentative["metric_value"] != v7["gold"]["metric_value"]       # the correction does move it
    assert v7["gold"]["action_classes"] == v6["gold"]["action_classes"]   # but not the decision
    assert v7["gold"]["record_must_change"] and {"O2", "F3"} <= set(v7["gold"]["policy_rules"])
    threshold, nonflip = f["policy"]["threshold"], f["checks"]["nonflip_margin"]["value"]
    assert F.margin_ok(nonflip, threshold)
    assert (nonflip >= threshold) is (f["checks"]["true_margin"]["value"] >= threshold)
    sizes = {e["label"]: e for e in f["checks"]["edit_sizes"]}
    assert sizes["version 7"]["new_value"] != sizes["wrong value"]["new_value"]


@needs_data
@pytest.mark.parametrize("fid", list(FAMILIES))
def test_new_event_leaves_past_figures_unchanged(disk, fid):
    v8, v6 = by_id(disk[fid], "8"), by_id(disk[fid], "6")
    assert values(v8["expected_final_record"]) == values(v8["initial_record"]) == values(v6["expected_final_record"])
    assert v8["expected_final_record"]["change_log"] == []
    [event] = v8["expected_final_record"]["events"]
    notice = next(d for t in turns(v8) for d in t["documents"] if d["doc_id"] == event["evidence_doc"])
    assert notice["doc_type"] == "officer_notice" and notice["signed_by"] and notice["dated"] == event["dated"]
    assert notice["table"] == [] and notice["synthetic"] is True
    assert event["change_type"] == "new_event" and {"C3", "O4"} <= set(v8["gold"]["policy_rules"])
    assert v8["gold"]["action_classes"] == v6["gold"]["action_classes"]
    facts = v8["expected_final_record"]["facts"]
    assert F.grade(v8, "decline", facts)["record_failures"] == ["event_not_logged"]
    assert F.record_failures(v8, facts, [event]) == []


# ---------------------------------------------------------------- every family: scripted and unscripted


@needs_data
@pytest.mark.parametrize("fid", list(FAMILIES))
def test_every_family_has_nine_versions_in_two_modes(disk, fid):
    f = disk[fid]
    assert F.validate(f) == []
    assert [v["version_id"] for v in f["versions"]] == VERSION_IDS
    assert all(set(v["conversations"]) == set(F.MODES) for v in f["versions"])
    assert f["set"] == FAMILIES[fid]


@needs_data
@pytest.mark.parametrize("fid", list(FAMILIES))
def test_scripted_copy_is_the_unscripted_one_plus_questions(disk, fid):
    for v in disk[fid]["versions"]:
        scripted = [dict(t) for t in turns(v, "scripted") if t["kind"] != "question"]
        unscripted = turns(v)
        assert len(scripted) == len(unscripted)
        for s, u in zip(scripted, unscripted):
            assert {k: x for k, x in s.items() if k != "turn"} == {k: x for k, x in u.items() if k != "turn"}


@needs_data
@pytest.mark.parametrize("fid", list(FAMILIES))
def test_questions_come_only_before_the_tentative_step_and_never_after_a_change(disk, fid):
    for v in disk[fid]["versions"]:
        ts = turns(v, "scripted")
        for k, t in enumerate(ts):
            if t["kind"] == "question":
                nxt = next(x for x in ts[k:] if x["kind"] != "question")
                assert nxt["kind"] == "tentative_decision_request"
        first_change = next((t["turn"] for t in ts if t["kind"] in CHANGE_KINDS), None)
        if first_change is not None:
            assert all(t["turn"] < first_change for t in ts if t["kind"] == "question"), v["version_id"]


@needs_data
@pytest.mark.parametrize("fid", list(FAMILIES))
def test_each_source_question_is_asked_once_on_figures_already_given(disk, fid):
    f = disk[fid]
    n_source = len(f["source"]["turn_programs"])
    for v in f["versions"]:
        ts = turns(v, "scripted")
        tables = active_tables(ts)
        questions = [t for t in ts if t["kind"] == "question"]
        assert len(questions) == n_source
        assert len({q["text"] for q in questions}) == len({q["text"] for q in questions})
        for q in questions:
            assert answers_match(execute(q["program"], tables[q["turn"]]), q["expected_answer"])
    gradual = turns(by_id(f, "2"), "scripted")
    assert [t["kind"] for t in gradual][:2] == ["opening", "tentative_decision_request"]  # nothing given yet


@needs_data
def test_scripted_gradual_asks_the_whole_chain_once_every_figure_is_in(disk):
    for f in disk.values():
        ts = turns(by_id(f, "2"), "scripted")
        kinds = [t["kind"] for t in ts]
        n = len(f["source"]["turn_programs"])
        last_disclosure = max(k for k, x in enumerate(kinds) if x == "disclosure")
        assert kinds[last_disclosure + 1:last_disclosure + 1 + n] == ["question"] * n
        assert kinds.count("question") == n and kinds[last_disclosure + 1 + n] == "tentative_decision_request"


@needs_data
@pytest.mark.parametrize("fid", list(FAMILIES))
def test_answer_checks_ask_the_same_question_text_as_the_scripted_copy(disk, fid):
    for v in disk[fid]["versions"]:
        asked = {t["text"] for t in turns(v, "scripted") if t["kind"] == "question"}
        for mode in F.MODES:
            assert {c["question"] for c in v["conversations"][mode]["answer_checks"]} <= asked


@needs_data
def test_renames_reach_questions_and_answer_checks(spec, source_e, rows):
    renamed = copy.deepcopy(spec)
    renamed["page_renames"] = renamed["page_renames"] + [["the net sales", "the turnover"]]
    row = next(r for r in rows if r["id"] == spec["source_id"])
    fam_ = F.build_family(renamed, row, source_e, "x", DRAW_43)
    for v in fam_["versions"]:
        for mode in F.MODES:
            conv = v["conversations"][mode]
            texts = [t["text"] for t in conv["turns"] if t["kind"] == "question"] + \
                [c["question"] for c in conv["answer_checks"]]
            assert texts and not any("the net sales" in x.lower() for x in texts)
            assert any("the turnover" in x for x in texts)


@needs_data
def test_forbidden_terms_are_checked_in_document_fields(spec, source_e, rows):
    bad = copy.deepcopy(spec)
    bad["text"] = {**bad["text"], "schedule_title": "Lockheed pro forma schedule"}
    row = next(r for r in rows if r["id"] == spec["source_id"])
    with pytest.raises(F.FamilyError, match="forbidden term 'lockheed' survives in the document schedule title"):
        F.build_family(bad, row, source_e, "x", DRAW_43)


def test_a_source_figure_in_a_hidden_row_stops_the_build():
    seen = F.visible_table(MARGIN_TABLE, [3])
    F.check_source_cells(["6305", "6197", "900"], seen)
    with pytest.raises(F.FamilyError, match=r"\['14.3'\] are not in exactly one cell of the table the agent sees"):
        F.check_source_cells(["6305", "14.3"], seen)          # a source turn reading the hidden margin row


@needs_data
def test_hiding_the_metric_row_stops_the_build(spec, source_e, rows):
    bad = {**copy.deepcopy(spec), "hidden_rows": [1]}          # row 1 holds net sales, the metric input
    row = next(r for r in rows if r["id"] == spec["source_id"])
    with pytest.raises(F.FamilyError, match="in the table the agent sees"):
        F.build_family(bad, row, source_e, "x", DRAW_43)


@needs_data
def test_event_and_claim_turns_are_checked(spec, source_e, rows):
    row = next(r for r in rows if r["id"] == spec["source_id"])
    no_notice = copy.deepcopy(spec)
    v8 = next(v for v in no_notice["template"]["versions"] if v["id"] == "8")
    v8["turns"][2]["docs"] = []
    with pytest.raises(F.FamilyError, match="signed, dated notice"):
        F.build_family(no_notice, row, source_e, "x", DRAW_43)
    other_claim = copy.deepcopy(spec)
    v5b = next(v for v in other_claim["template"]["versions"] if v["id"] == "5b")
    v5b["turns"][2]["claimed"] = "true"
    with pytest.raises(F.FamilyError, match="claimed must be 'wrong'"):
        F.build_family(other_claim, row, source_e, "x", DRAW_43)


@needs_data
@pytest.mark.parametrize("fid", list(FAMILIES))
def test_correction_bodies_quote_the_numbers_as_shown_in_the_tables(disk, fid):
    for vid, doc_turn in (("3", 2), ("7", 2), ("1", 0)):
        v = by_id(disk[fid], vid)
        ts = turns(v)
        stale = ts[0]["documents"][2]["table"]
        amended = next(d for d in ts[doc_turn]["documents"] if d["doc_type"] == "amended_financial_schedule")
        [cell] = [c for c in amended.get("edited_cells", [])] or [None]
        changed = [(i, j) for i in range(1, len(stale)) for j in range(1, len(stale[i]))
                   if stale[i][j] != amended["table"][i][j]]
        assert len(changed) == 1
        i, j = changed[0]
        assert f"not {F.shown_text(stale[i][j])}." in amended["body"]
        assert f" {F.shown_text(amended['table'][i][j])}, not" in amended["body"]


@needs_data
@pytest.mark.parametrize("fid", list(FAMILIES))
def test_every_answer_check_reexecutes_on_its_active_table(disk, fid):
    for v in disk[fid]["versions"]:
        for mode in F.MODES:
            conv = v["conversations"][mode]
            tables = active_tables(conv["turns"])
            assert conv["answer_checks"]
            for c in conv["answer_checks"]:
                assert answers_match(execute(c["program"], tables[c["at_turn"]]), c["expected_answer"])


@needs_data
@pytest.mark.parametrize("fid", list(FAMILIES))
def test_gold_recomputes_from_the_last_table_given(disk, fid):
    f = disk[fid]
    threshold, seen = f["policy"]["threshold"], f["source"]["agent_table"]
    for v in f["versions"]:
        last = [t for t in turns(v) for d in t["documents"] if d["table"]]
        final_table = [d["table"] for d in last[-1]["documents"] if d["table"]][-1]
        prog = F.program_on(f["metric_program"], seen, final_table)
        metric = float(execute(prog, final_table))
        gold = v["gold"]
        assert metric == pytest.approx(gold["metric_value"], abs=1e-6)
        assert (metric >= threshold) is gold["meets_threshold"]
        flipped = v["version_id"] == "3"
        assert gold["action_classes"] == F.classes_final(gold["meets_threshold"], flipped)
        assert gold["final_facts_hash"] == F.facts_hash(v["expected_final_record"]["facts"])


@needs_data
@pytest.mark.parametrize("fid", list(FAMILIES))
def test_reaction_exists_only_on_the_unsupported_claim(disk, fid):
    for v in disk[fid]["versions"]:
        for mode in F.MODES:
            for t in turns(v, mode):
                if t["kind"] == "unsupported_claim":
                    assert [r["if_agent"] for r in t["reactions"]] == ["request_evidence"]
                    assert t["reactions"][0]["speaker"] == t["speaker"]
                    assert "anything in writing" in t["reactions"][0]["text"]
                else:
                    assert t["reactions"] == []


@needs_data
@pytest.mark.parametrize("fid", list(FAMILIES))
def test_agent_facing_text_has_no_forbidden_terms(disk, specs, fid):
    terms = specs[fid]["forbidden_terms"]
    for v in disk[fid]["versions"]:
        text = json.dumps(v["conversations"]).lower()
        for term in terms:
            assert not __import__("re").search(rf"(?<!\w){term}(?!\w)", text), (fid, term)


@needs_data
def test_version_3_flips_and_needs_escalation_in_every_family(disk):
    for f in disk.values():
        v3 = by_id(f, "3")
        assert turns(v3)[1]["checkpoint"]["meets_threshold"] is not v3["gold"]["meets_threshold"]
        assert v3["gold"]["acceptable_actions"] == ["escalate"]


@needs_data
def test_s01_is_the_renew_family(disk):
    s01 = disk["S01"]
    assert by_id(s01, "6")["gold"]["acceptable_actions"] == ["renew"]
    assert by_id(s01, "5b")["gold"]["acceptable_actions"] == ["renew"]
    assert turns(by_id(s01, "5b"))[2]["speaker"] == "risk_officer"
    assert {f["family_id"] for f in disk.values()
            if by_id(f, "6")["gold"]["meets_threshold"]} == {"S01"}


# ---------------------------------------------------------------- hand-picked, hidden rows, same table


@needs_data
@pytest.mark.parametrize("fid", ["S01", "S02"])
def test_hidden_margin_row_never_reaches_the_agent(disk, fid):
    f = disk[fid]
    [hidden] = f["source"]["hidden_rows"]
    assert hidden["label"] == "segment operating income margin"
    assert hidden["label"] in [r[0] for r in f["source"]["table"]]
    margins = [c for c in f["source"]["table"][hidden["row"]][1:]]
    for v in f["versions"]:
        text = json.dumps(v["conversations"])
        assert hidden["label"] not in text
        assert not any(m in text for m in margins)
    assert F.ratio_rows(f["source"]["agent_table"]) == []


@needs_data
def test_hand_picked_rules_must_be_waived_and_only_those(specs, rows, convs):
    f002 = copy.deepcopy(specs["F002"])
    row = next(r for r in rows if r["id"] == f002["source_id"])
    e = convs[f002["source_id"]]
    assert F.hand_picked_checks(f002, row, e)["failed_checks"] == ["label_not_allowed"]
    f002["selection"]["waived_checks"] = {}
    with pytest.raises(F.FamilyError, match="breaks"):
        F.hand_picked_checks(f002, row, e)
    s01 = copy.deepcopy(specs["S01"])
    s01_row = next(r for r in rows if r["id"] == s01["source_id"])
    s01["selection"]["waived_checks"] = {"label_not_allowed": "not needed"}
    with pytest.raises(F.FamilyError, match="waiver not needed"):
        F.hand_picked_checks(s01, s01_row, convs[s01["source_id"]])
    s01 = copy.deepcopy(specs["S01"])
    s01["hidden_rows"] = []
    with pytest.raises(F.FamilyError, match="ratio_row"):
        F.hand_picked_checks(s01, s01_row, convs[s01["source_id"]])


@needs_data
def test_a_drawn_family_cannot_hide_rows(spec):
    bad = {**spec, "hidden_rows": [4]}
    with pytest.raises(F.FamilyError, match="cannot hide rows"):
        F.build_from_spec(REPO_ROOT, bad)


@needs_data
def test_draw_skips_pages_and_tables_already_used(spec, rows, convs):
    order = F.draw_order(rows)
    used = F.page_key(spec["source_id"])
    log = F.run_draw("F009", order, convs, False, used_pages=frozenset({used}))
    assert not any(F.page_key(r["id"]) == used for r in log) and log[-1]["result"] == "pass"
    kept = [r["id"] for r in order if F.page_key(r["id"]) != used]
    assert [r["id"] for r in log] == kept[:len(log)]
    table = F.table_key(convs[spec["source_id"]]["table"])
    by_table = F.run_draw("F009", order, convs, False, used_tables=frozenset({table}))
    assert spec["source_id"] not in {r["id"] for r in by_table}


@needs_data
def test_build_stops_when_a_later_spec_reuses_a_table(spec, specs, convs):
    with pytest.raises(F.FamilyError, match="already used"):
        F.build_from_spec(REPO_ROOT, spec, used_pages=frozenset({F.page_key(spec["source_id"])}))
    f002 = specs["F002"]
    table = F.table_key(convs[f002["source_id"]]["table"])
    with pytest.raises(F.FamilyError, match="already used"):
        F.build_from_spec(REPO_ROOT, f002, used_tables=frozenset({table}))


@needs_data
def test_no_used_tables_leaves_the_f001_draw_unchanged(built, rows, convs):
    assert F.run_draw("F001", F.draw_order(rows), convs, False, frozenset(), frozenset()) == built[1]


@needs_data
def test_several_families_may_share_a_company_but_never_a_table(disk):
    companies = [f["source"]["company"] for f in disk.values()]
    assert companies.count("LMT") == 2 and companies.count("AON") == 2
    assert len({F.page_key(f["source"]["id"]) for f in disk.values()}) == len(disk)
    assert len({F.table_key(f["source"]["table"]) for f in disk.values()}) == len(disk)


@needs_data
def test_index_counts_families_per_company(disk):
    index = (REPO_ROOT / "docs/families/index.md").read_text()
    assert index == F.render_index(list(disk.values()))
    assert "| AON | 0 | 2 | 2 |" in index and "| LMT | 2 | 0 | 2 |" in index
    assert "Totals: 4 families (2 pilot, 2 smoke), 72 conversations." in index


# ---------------------------------------------------------------- reproducible outputs


@needs_data
def test_build_twice_is_byte_identical_and_matches_the_files(built, spec):
    again, log = F.build_from_spec(REPO_ROOT, spec)
    assert F.dumps(again) == F.dumps(built[0]) == JSON_PATH.read_text()
    assert F.render_review(again) == (REPO_ROOT / "docs/families/F001.md").read_text()
    assert log == built[1]


@needs_data
def test_check_mode_passes_on_the_committed_files_and_writes_nothing():
    paths = [json_path(fid) for fid in FAMILIES] + [REPO_ROOT / "docs/families" / f"{fid}.md" for fid in FAMILIES]
    paths += [REPO_ROOT / "docs/families/index.md", REPO_ROOT / "data/families/draw_log.csv"]
    before = {p: p.stat().st_mtime_ns for p in paths}
    lines = []
    assert F.run(REPO_ROOT, log=lines.append, check=True) == 0
    assert lines[-1].startswith("OK: generated files match a rebuild")
    assert any("conversations out: 72" in line for line in lines)
    assert {p: p.stat().st_mtime_ns for p in before} == before


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
