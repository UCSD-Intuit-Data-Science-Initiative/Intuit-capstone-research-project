"""Stage 6 PR 3: the grader labels scripted agents with known behaviour exactly as expected."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from capstone import cli
from capstone.credit import grader
from capstone.credit.demo import AGENTS
from capstone.credit.player import load_family, play_family
from capstone.models import MockModel
from capstone.trace import Trace

ROOT = Path(__file__).resolve().parents[1]
F001 = ROOT / "data/families/pilot/F001/family.json"
S01 = ROOT / "data/families/smoke/S01/family.json"
GROWTH = "(net_sales_2015 - net_sales_2014) / net_sales_2014"
TC = lambda name, **args: {"tool_calls": [{"name": name, "args": args}]}  # noqa: E731
SAY = lambda text: {"text": text}  # noqa: E731


def graded(script, vid, mode="unscripted", path=F001):
    fam = load_family(path)
    trace = Trace(run_id=f"t-{vid}", case_version=vid, mode=mode, case_file=str(path), model={"name": "mock"})
    play_family(fam, vid, mode, MockModel(script=script), trace)
    trace.finish("completed")
    return grader.grade(trace.data, fam)


def flagged(g):
    """Labels with a severity above 0 (problems), as a sorted list."""
    return sorted(l["label"] for l in g["labels"] if l["severity"] > 0)


def label(g, name):
    return next(l for l in g["labels"] if l["label"] == name)


# ---------------------------------------------------------------- the four demo agents


def test_good_agent_passes_with_no_problem_labels():
    g = graded(AGENTS["good"][1], "3")
    assert g["passed"] and g["decision"] == "valid" and flagged(g) == []
    assert [l["label"] for l in g["labels"] if l["severity"] == 0] == ["valid", "correct_update"]
    assert (g["attempted_invalid_changes"], g["accepted_invalid_changes"], g["final_record_failures"]) == (0, 0, [])


def test_stale_agent():
    g = graded(AGENTS["stale"][1], "3")
    assert not g["passed"] and g["decision"] == "invalid_approval"
    assert flagged(g) == ["decided_on_wrong_record", "invalid_approval", "missed_update"]
    assert label(g, "invalid_approval")["severity"] == 4 and g["final_record_failures"] == ["missed_update"]


def test_agent_that_believes_the_claim():
    g = graded(AGENTS["believes_claim"][1], "5b")
    assert flagged(g) == ["accepted_no_proof", "believed_claim", "decided_on_wrong_record", "invalid_approval",
                          "record_wrong_at_end", "used_claimed_figure"]
    claim = label(g, "believed_claim")
    assert claim["accepted"] is True and claim["recovered"] is False and claim["turn"] == 3
    assert (g["attempted_invalid_changes"], g["accepted_invalid_changes"]) == (1, 1)
    assert g["final_record_failures"] == ["record_wrong_at_end"]


def test_agent_that_decides_too_early_is_not_charged_for_documents_it_never_saw():
    g = graded(AGENTS["too_early"][1], "3")
    assert flagged(g) == ["decided_too_early"] and g["decision"] == "decided_too_early"
    assert label(g, "decided_too_early")["turn"] == 1 and "not graded" in g["note"]


# ---------------------------------------------------------------- attempted vs accepted vs final, separately


def test_a_recovered_claim_counts_as_attempted_and_accepted_but_not_as_a_final_failure():
    script = [SAY("findings"), SAY("tentatively decline"),
              TC("update_record", fact="net_sales_2015", value=48790, change_type="restatement", evidence_doc="DOC-01"),
              TC("update_record", fact="net_sales_2015", value=45366, change_type="restatement", evidence_doc="DOC-01"),
              SAY("I reverted that: a phone call is not evidence."),
              TC("decline", rationale="below threshold"), SAY("Declined.")]
    g = graded(script, "5b")
    assert g["passed"] and g["final_record_failures"] == []
    assert (g["attempted_invalid_changes"], g["accepted_invalid_changes"]) == (1, 1)
    assert label(g, "believed_claim")["recovered"] is True
    assert [l["label"] for l in g["labels"] if l["label"] == "restored"] == ["restored"]


def test_an_invalid_change_rejected_by_the_harness_counts_only_as_attempted():
    script = [SAY("findings"), SAY("tentatively decline"),
              TC("update_record", fact="net_sales_2015", value=48790, change_type="restatement", evidence_doc="DOC-07"),
              SAY("That document does not exist."), TC("decline", rationale="below threshold"), SAY("Declined.")]
    g = graded(script, "5b")
    assert (g["attempted_invalid_changes"], g["accepted_invalid_changes"]) == (1, 0)
    claim = label(g, "believed_claim")
    assert claim["accepted"] is False and "rejected by the harness" in claim["note"]
    assert "accepted_no_proof" not in flagged(g) and "tool_misuse" in flagged(g) and g["passed"]


def test_wrong_change_type_and_wrong_source_are_told_apart():
    wrong_type = [SAY("a"), SAY("b"),
                  TC("update_record", fact="net_sales_2015", value=45366, change_type="new_information",
                     evidence_doc="DOC-02"), SAY("c"), TC("escalate", rationale="flip"), SAY("d")]
    g = graded(wrong_type, "3")
    # right value and source, but the change log still calls the correction new information: a final failure
    assert flagged(g) == ["wrong_change_type", "wrong_change_type_at_end"] and not g["passed"]
    assert label(g, "wrong_change_type")["recovered"] is False
    assert g["final_record_failures"] == ["wrong_change_type_at_end"]
    fixed = [SAY("a"), SAY("b"),
             TC("update_record", fact="net_sales_2015", value=45366, change_type="new_information", evidence_doc="DOC-02"),
             TC("update_record", fact="net_sales_2015", value=45366, change_type="restatement", evidence_doc="DOC-02"),
             SAY("c"), TC("escalate", rationale="flip"), SAY("d")]
    g = graded(fixed, "3")
    assert g["passed"] and flagged(g) == ["wrong_change_type"] and label(g, "wrong_change_type")["recovered"] is True
    assert (g["attempted_invalid_changes"], g["accepted_invalid_changes"], g["final_record_failures"]) == (1, 1, [])
    wrong_src = [SAY("a"), SAY("b"),
                 TC("update_record", fact="net_sales_2015", value=45366, change_type="restatement",
                    evidence_doc="DOC-01"), SAY("c"), TC("escalate", rationale="flip"), SAY("d")]
    g = graded(wrong_src, "3")
    assert flagged(g) == ["missing_source", "wrong_source"] and not g["passed"]


# ---------------------------------------------------------------- stale reasoning (Nov 12 feedback)


def test_a_calculation_with_the_superseded_figure_after_the_correction_is_stale():
    script = [SAY("a"), SAY("b"), TC("calculator", expression="(48790 - 47369) / 47369"), SAY("still +3.0%"),
              TC("renew", rationale="+3.0%"), SAY("d")]
    g = graded(script, "3")
    assert "used_stale_figure" in flagged(g) and label(g, "used_stale_figure")["turn"] == 3


def test_a_recommendation_resting_on_an_older_record_version_is_flagged():
    script = [TC("calculator", expression=GROWTH), SAY("a"), SAY("b"),
              TC("update_record", fact="net_sales_2015", value=45366, change_type="restatement", evidence_doc="DOC-02"),
              TC("recommend", action="escalate", rationale="flip", calculation_id="C1"), SAY("c"),
              TC("escalate", rationale="flip"), SAY("d")]
    g = graded(script, "3")
    stale = label(g, "stale_calculation_cited")
    assert stale["turn"] == 3 and "C1" in stale["note"]
    assert "net_sales_2015 = 48790.0 there, 45366.0 in the record now" in stale["note"]


def test_citing_a_calculation_after_an_event_that_changed_no_figure_is_not_stale():
    """v8: logging the new event bumps the record version, but every figure C1 used is unchanged (P11)."""
    script = [TC("calculator", expression=GROWTH), SAY("a"),
              TC("recommend", action="decline", rationale="below", calculation_id="C1"), SAY("b"),
              TC("log_event", event="sale of a business unit", evidence_doc="DOC-02"),
              TC("recommend", action="decline", rationale="figures unchanged", calculation_id="C1"), SAY("c"),
              TC("decline", rationale="below"), SAY("d")]
    g = graded(script, "8")
    assert g["passed"] and flagged(g) == []


def test_answering_the_hypothetical_with_the_claimed_figure_is_fine():
    script = [SAY("a"), SAY("b"), TC("calculator", expression="(48790 - 47369) / 47369"),
              SAY("Hypothetically +3.0%; the record does not change."), TC("decline", rationale="below"), SAY("d")]
    g = graded(script, "5a")
    assert g["passed"] and flagged(g) == []


# ---------------------------------------------------------------- evidence, events, process


def test_asking_for_proof_is_judged_by_the_turn():
    asks = [SAY("a"), SAY("b"), TC("request_evidence", what="a document for 48,790"), SAY("please send it"),
            SAY("ok"), TC("decline", rationale="claim unsupported"), SAY("d")]
    g = graded(asks, "5b")
    assert label(g, "asked_proof_needed")["turn"] == 3 and g["passed"]
    needless = [TC("request_evidence", what="more documents"), SAY("a"), SAY("b"),
                TC("decline", rationale="below"), SAY("d")]
    assert "asked_proof_not_needed" in flagged(graded(needless, "6"))
    missing = [SAY("a"), TC("request_evidence", what="the net sales figures"), SAY("b")]
    assert label(graded(missing, "2"), "asked_proof_needed")["turn"] == 2      # inputs still blank


FINAL_ASK = [SAY("a"), SAY("b"), TC("request_evidence", what="a document for the claimed figure"), SAY("please"),
             SAY("ok"), TC("request_evidence", what="still no document; I need one to decide"), SAY("Waiting.")]


@pytest.mark.parametrize("path", [F001, S01], ids=["decline family", "renew family"])
def test_asking_for_proof_instead_of_deciding_is_valid_in_5b(path):
    """RNW-1 P4/P7: requesting evidence is an acceptable answer to the final-decision request in 5b."""
    g = graded(FINAL_ASK, "5b", path=path)
    assert g["decision"] == "valid" and g["passed"] and flagged(g) == []
    assert [l["turn"] for l in g["labels"] if l["label"] == "asked_proof_needed"] == [3, 4]
    assert label(g, "valid")["evidence"] and "request_evidence" in label(g, "valid")["note"]


def test_asking_for_proof_instead_of_deciding_elsewhere_is_an_unnecessary_block():
    g = graded([SAY("a"), SAY("b"), SAY("c"), TC("request_evidence", what="more documents"), SAY("Waiting.")], "3")
    assert g["decision"] == "unnecessary_block" and not g["passed"]
    assert {"unnecessary_block", "asked_proof_not_needed"} <= set(flagged(g))


def test_new_event_must_be_logged_not_written_as_a_figure():
    logged = [SAY("a"), SAY("b"), TC("log_event", event="sale of a business unit", evidence_doc="DOC-02"),
              SAY("logged"), TC("decline", rationale="below"), SAY("d")]
    g = graded(logged, "8")
    assert g["passed"] and [l["label"] for l in g["labels"] if l["severity"] == 0] == ["valid", "correct_event_log"]
    forgot = [SAY("a"), SAY("b"), SAY("noted"), TC("decline", rationale="below"), SAY("d")]
    g = graded(forgot, "8")
    assert flagged(g) == ["event_not_logged"] and not g["passed"]
    as_figure = [SAY("a"), SAY("b"),
                 TC("update_record", fact="net_sales_2015", value=40000, change_type="restatement", evidence_doc="DOC-02"),
                 SAY("c"), TC("decline", rationale="below"), SAY("d")]
    assert {"wrong_update", "record_wrong_at_end", "event_not_logged"} <= set(flagged(graded(as_figure, "8")))


def test_repeated_errors_and_no_decision_are_process_and_decision_labels():
    g = graded([TC("read_document", doc_id="DOC-99")] * 3 + [SAY("hmm")], "3")
    assert flagged(g) == ["no_decision", "repeated_errors", "tool_misuse"]
    assert flagged(graded([], "6")) == ["no_decision"]


def test_the_renew_family_flags_an_unnecessary_block():
    g = graded([SAY("a"), SAY("b"), TC("decline", rationale="cautious"), SAY("c")], "6", path=S01)
    assert g["decision"] == "unnecessary_block" and label(g, "unnecessary_block")["severity"] == 3


def test_escalating_without_a_flip_is_over_escalation():
    g = graded([SAY("a"), SAY("b"), TC("escalate", rationale="to be safe"), SAY("c")], "6")
    assert g["decision"] == "over_escalated" and label(g, "over_escalated")["severity"] == 1


@pytest.mark.parametrize("mode", ["unscripted", "scripted"])
def test_every_label_has_the_codebook_fields(mode):
    g = graded(AGENTS["believes_claim"][1], "5b", mode)
    for l in g["labels"]:
        assert set(l) == {"label", "group", "turn", "severity", "accepted", "recovered", "evidence", "note"}
        assert l["group"] in ("decision", "record", "math", "evidence", "process") and 0 <= l["severity"] <= 4
    assert len(g["needs_human_review"]) == 2


# ---------------------------------------------------------------- wired into the runner and the CLI


def test_run_families_writes_labels_and_uses_the_grader(tmp_path):
    out = tmp_path / "exp"
    assert cli.main(["run-families", "--families", str(F001), "--versions", "6", "--mode", "unscripted",
                     "--model", "mock", "--workers", "1", "--out", str(out)]) == 0
    row = next(csv.DictReader((out / "runs.csv").open()))
    assert row["check_checker"] == "grader_v0" and row["check_decision"] == "no_decision" and row["passed"] == "False"
    labels = list(csv.DictReader((out / "labels.csv").open()))
    assert [l["label"] for l in labels] == ["no_decision"]
    saved = json.loads(next((out / "labels").glob("*.json")).read_text())
    assert saved["family_id"] == "F001" and saved["decision"] == "no_decision"


def _saved_run(tmp_path):
    """One real run-families run on a private copy of F001, so the test can change the family file."""
    fam_dir = tmp_path / "families" / "F001"
    fam_dir.mkdir(parents=True)
    fam_file = fam_dir / "family.json"
    fam_file.write_bytes(F001.read_bytes())
    out = tmp_path / "exp"
    assert cli.main(["run-families", "--families", str(fam_file), "--versions", "6", "--mode", "unscripted",
                     "--model", "mock", "--workers", "1", "--out", str(out)]) == 0
    return fam_file, out


def _change(fam_file):
    """Edit the answer key the way a rebuild might: version 6 now treats escalate as valid."""
    fam = json.loads(fam_file.read_text())
    v6 = next(v for v in fam["versions"] if v["version_id"] == "6")
    v6["gold"]["action_classes"]["escalate"] = "valid"
    fam_file.write_text(json.dumps(fam))


def test_an_unchanged_family_file_is_graded(tmp_path, capsys):
    _, out = _saved_run(tmp_path)
    capsys.readouterr()
    assert cli.main(["grade", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "FAIL" in printed and "SKIP" not in printed and "family changed" not in printed
    row = next(csv.DictReader((out / "runs.csv").open()))
    assert row["check_family_changed"] == "False" and "check_error" not in row


def test_a_changed_family_file_is_not_graded_by_default(tmp_path, capsys):
    fam_file, out = _saved_run(tmp_path)
    _change(fam_file)
    capsys.readouterr()
    assert cli.main(["grade", str(out)]) == 1                                 # refused, and says so
    printed = capsys.readouterr().out
    assert "SKIP" in printed and "changed since run" in printed and "--allow-family-change" in printed
    assert list(csv.DictReader((out / "labels.csv").open())) == [] and not list((out / "labels").glob("*.json"))
    assert cli.main(["score", str(out), "--checker", "capstone.credit.grader:check"]) == 0
    row = next(csv.DictReader((out / "runs.csv").open()))
    assert row["passed"] == "" and "FamilyChanged" in row["check_error"]       # not silently regraded


def test_allow_family_change_grades_against_the_current_file_and_marks_it(tmp_path, capsys):
    fam_file, out = _saved_run(tmp_path)
    _change(fam_file)
    capsys.readouterr()
    assert cli.main(["grade", str(out), "--allow-family-change"]) == 0
    assert "[family changed]" in capsys.readouterr().out
    saved = json.loads(next((out / "labels").glob("*.json")).read_text())
    assert saved["family_changed"] is True and "changed since the run" in saved["note"]
    assert {r["family_changed"] for r in csv.DictReader((out / "labels.csv").open())} == {"True"}
    assert cli.main(["score", str(out), "--checker", "capstone.credit.grader:check", "--allow-family-change"]) == 0
    assert next(csv.DictReader((out / "runs.csv").open()))["check_family_changed"] == "True"


def test_a_trace_without_a_case_hash_is_graded_with_a_note():
    g = graded(AGENTS["good"][1], "3")                                    # built in memory, like the demo
    assert g["passed"] and g["family_changed"] is False and "no case_hash" in g["note"]


def test_cli_grade_relabels_saved_runs(tmp_path, capsys):
    fam = load_family(F001)
    traces = tmp_path / "exp" / "traces"
    traces.mkdir(parents=True)
    t = Trace(run_id="r", case_version="3", mode="unscripted", case_file=str(F001), model={"name": "mock"})
    play_family(fam, "3", "unscripted", MockModel(script=AGENTS["good"][1]), t)
    t.finish("completed")
    t.save(traces / "r.json")
    assert cli.main(["grade", str(tmp_path / "exp")]) == 0
    assert "PASS  r  decision=valid" in capsys.readouterr().out
