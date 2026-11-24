"""Stage 6 PR 3: the grader labels scripted agents with known behaviour exactly as expected."""

from __future__ import annotations

import csv
import json
import re
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
                  TC("update_record", fact="net_sales_2015", value=45366, change_type="gap_filled",
                     evidence_doc="DOC-02"), SAY("c"), TC("escalate", rationale="flip"), SAY("d")]
    g = graded(wrong_type, "3")
    # right value and source, but the change log still calls the correction new information: a final failure
    assert flagged(g) == ["wrong_change_type", "wrong_change_type_at_end"] and not g["passed"]
    assert label(g, "wrong_change_type")["recovered"] is False
    assert g["final_record_failures"] == ["wrong_change_type_at_end"]
    fixed = [SAY("a"), SAY("b"),
             TC("update_record", fact="net_sales_2015", value=45366, change_type="gap_filled", evidence_doc="DOC-02"),
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
def test_asking_for_proof_instead_of_deciding_is_an_unnecessary_block_in_5b(path):
    """RNW-4 E5/E6: asking for proof after the claim is allowed, but at the final-decision request the
    agent must take a final action; asking again there is an unnecessary block."""
    g = graded(FINAL_ASK, "5b", path=path)
    assert g["decision"] == "unnecessary_block" and not g["passed"]
    assert [l["turn"] for l in g["labels"] if l["label"] == "asked_proof_needed"] == [3]
    assert [l["turn"] for l in g["labels"] if l["label"] == "asked_proof_not_needed"] == [4]
    assert "request_evidence" in label(g, "unnecessary_block")["note"]


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
        assert set(l) == {"label", "group", "turn", "severity", "accepted", "recovered", "evidence", "note",
                          "label_id", "detail", "role"}
        assert l["group"] in ("decision", "record", "math", "evidence", "process") and 0 <= l["severity"] <= 4
    assert len(g["needs_human_review"]) == 2


# ---------------------------------------------------------------- no false failures on the correct path


def _metric_facts(fam: dict, version: dict) -> tuple[str, str]:
    """Record names of the metric's current and prior figures (the program's first two numbers)."""
    cur, prior = (float(x) for x in re.findall(r"(?<![#\d.])\d+(?:\.\d+)?", fam["metric_program"])[:2])
    final = version["expected_final_record"]["facts"]
    pick = lambda n: next(k for k, f in final.items() if f["value"] is not None and abs(f["value"] - n) < 1e-6)  # noqa: E731
    return pick(cur), pick(prior)


def oracle_script(fam: dict, version: dict, mode: str) -> list[dict]:
    """An agent that does exactly what the gold expects, turn by turn: the writes and events of the
    expected final record when their documents arrive, a calculation whenever both inputs are known,
    the checkpoint's valid tentative step, and an acceptable final action when asked."""
    cur, prior = _metric_facts(fam, version)
    expected = version["expected_final_record"]
    values = {k: f["value"] for k, f in version["initial_record"]["facts"].items()}
    script, n_calc = [], 0
    for turn in version["conversations"][mode]["turns"]:
        docs = {d["doc_id"] for d in turn.get("documents", [])}
        calls = []
        for c in expected["change_log"]:
            if c["evidence_doc"] in docs:
                calls.append({"name": "update_record", "args": {k: c[k] for k in ("fact", "change_type", "evidence_doc")}
                              | {"value": c["new_value"]}})
                values[c["fact"]] = c["new_value"]
        calls += [{"name": "log_event", "args": {"event": e.get("event", "new event"), "evidence_doc": e["evidence_doc"]}}
                  for e in expected["events"] if e["evidence_doc"] in docs]
        if values[cur] is not None and values[prior] is not None:
            calls.append({"name": "calculator", "args": {"expression": f"({cur} - {prior}) / {prior}"}})
            n_calc += 1
        valid = [a for a, c in ((turn.get("checkpoint") or {}).get("action_classes") or {}).items() if c == "valid"]
        if turn["kind"] == "final_decision_request":
            act = next(a for a in version["gold"]["acceptable_actions"] if a in ("renew", "decline", "escalate"))
            calls.append({"name": act, "args": {"rationale": "per policy"}})
        elif valid == ["request_evidence"]:
            calls.append({"name": "request_evidence", "args": {"what": "the missing figure"}})
        elif turn["kind"] == "tentative_decision_request" and valid:
            calls.append({"name": "recommend", "args": {"action": valid[0], "rationale": "per policy"}
                          | ({"calculation_id": f"C{n_calc}"} if n_calc else {})})
        if calls:
            script.append({"tool_calls": calls})
        script.append({"text": "Done for this turn."})
    return script


ORACLE_CASES = [(p, v["version_id"], m) for p in sorted((ROOT / "data/families").rglob("family.json"))
                for v in load_family(p)["versions"] for m in ("unscripted", "scripted")]


def test_the_oracle_covers_every_conversation():
    assert len(ORACLE_CASES) == 72


@pytest.mark.parametrize("path,vid,mode", ORACLE_CASES,
                         ids=[f"{p.parent.name}-v{v}-{m}" for p, v, m in ORACLE_CASES])
def test_an_agent_that_follows_the_gold_passes_with_no_problem_labels(path, vid, mode):
    """Guards against false failures: any grader change that fails correct behaviour breaks this."""
    fam = load_family(path)
    g = graded(oracle_script(fam, next(v for v in fam["versions"] if v["version_id"] == vid), mode), vid, mode, path)
    assert g["passed"] and g["decision"] == "valid" and flagged(g) == [], (g["decision"], flagged(g))
    assert (g["attempted_invalid_changes"], g["accepted_invalid_changes"], g["final_record_failures"]) == (0, 0, [])
    assert g["primary_label"] is None and g["primary_label_id"] is None and g["first_problem_turn"] is None
    assert all(l["role"] is None for l in g["labels"])


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


# ---------------------------------------------------------------- IDs, primary problem, detail (grader-labels change)

from capstone.trace import run_uid  # noqa: E402


def lab(label="x", turn=1, severity=1, evidence=()):
    return {"label": label, "group": "process", "turn": turn, "severity": severity, "accepted": None,
            "recovered": None, "evidence": list(evidence), "note": "", "detail": None}


def test_run_uid_is_deterministic_and_tells_repeats_apart():
    t = {"run_id": "F001__3__scripted__qwen-local__r0", "started_at": "2026-11-17T01:08:45+00:00"}
    assert run_uid(t) == run_uid(dict(t)) and re.fullmatch(r"r-[0-9a-f]{12}", run_uid(t))
    again = {**t, "started_at": "2026-11-18T09:00:00+00:00"}              # same run played in another experiment
    assert again["run_id"] == t["run_id"] and run_uid(again) != run_uid(t)
    assert run_uid({**t, "run_id": "other"}) != run_uid(t)


def test_grade_carries_run_uid_and_label_ids_are_stable_across_regrades():
    fam = load_family(F001)
    t = Trace(run_id="t-3", case_version="3", mode="unscripted", case_file=str(F001), model={"name": "mock"})
    play_family(fam, "3", "unscripted", MockModel(script=AGENTS["believes_claim"][1]), t)
    t.finish("completed")
    a, b = grader.grade(t.data, fam), grader.grade(json.loads(json.dumps(t.data)), fam)
    assert a["run_uid"] == run_uid(t.data) == b["run_uid"]
    ids = [l["label_id"] for l in a["labels"]]
    assert ids == [l["label_id"] for l in b["labels"]] and len(set(ids)) == len(ids)
    assert all(re.fullmatch(rf"{a['run_uid']}-[0-9a-f]{{6}}", i) for i in ids)


def test_label_ids_of_identical_labels_get_a_suffix_in_list_order():
    labels = [lab("tool_misuse", None, 1, [3, 4]), lab("other", 2, 1, [1]), lab("tool_misuse", None, 1, [4, 3]),
              lab("tool_misuse", None, 1, [4, 3])]
    ids = grader.label_ids("r-abc", labels)
    assert ids[2] == ids[0] + "-2" and ids[3] == ids[0] + "-3"
    assert len(set(ids)) == 4 and ids == grader.label_ids("r-abc", labels)    # evidence order does not matter


def test_primary_is_the_earliest_turn_and_the_rest_are_secondary():
    labels = [lab("late", 5, 4), lab("early", 2, 1), lab("zero", 1, 0), lab("mid", 3, 3)]
    primary = grader.assign_ids_and_roles("r-1", labels)
    assert primary["label"] == "early"
    assert [l["role"] for l in labels] == ["secondary", "primary", None, "secondary"]


def test_primary_ties_go_to_severity_then_first_evidence_then_list_order():
    labels = [lab("a", 2, 1, [9]), lab("b", 2, 3, [8]), lab("c", 2, 3, [5])]
    assert grader.assign_ids_and_roles("r-1", labels)["label"] == "c"
    same = [lab("first", 2, 2, [4]), lab("second", 2, 2, [4])]
    assert grader.assign_ids_and_roles("r-1", same)["label"] == "first"
    assert grader.assign_ids_and_roles("r-1", [lab("no_ev", 2, 2, []), lab("ev", 2, 2, [7])])["label"] == "ev"


def test_a_label_with_no_turn_counts_as_after_the_last_turn():
    labels = [lab("final_failure", None, 4), lab("turn9", 9, 1)]
    assert grader.assign_ids_and_roles("r-1", labels)["label"] == "turn9"
    assert grader.assign_ids_and_roles("r-1", [lab("a", None, 1), lab("b", None, 2)])["label"] == "b"


def test_no_problem_means_no_primary():
    assert grader.assign_ids_and_roles("r-1", [lab("valid", 4, 0)]) is None
    g = graded(AGENTS["good"][1], "3")
    assert (g["primary_label"], g["primary_label_id"], g["first_problem_turn"]) == (None, None, None)
    assert all(l["role"] is None and l["label_id"] for l in g["labels"])


def test_an_event_logged_wrongly_early_is_the_primary_problem_not_the_no_decision_after_it():
    g = graded([SAY("a"), SAY("b"), TC("log_event", event="correction", evidence_doc="DOC-02"), SAY("c"),
                SAY("I am not sure.")], "3")
    assert flagged(g) == ["event_wrongly_logged", "missed_update", "no_decision"]
    assert g["primary_label"] == "event_wrongly_logged" and g["first_problem_turn"] == 3
    assert g["primary_label_id"] == label(g, "event_wrongly_logged")["label_id"]
    assert {l["label"]: l["role"] for l in g["labels"]} == {
        "event_wrongly_logged": "primary", "missed_update": "secondary", "no_decision": "secondary"}


def test_a_new_label_type_is_picked_without_changing_the_rule():
    labels = [lab("per_turn_state_label", 1, 2), lab("event_wrongly_logged", 3, 1)]
    assert grader.assign_ids_and_roles("r-1", labels)["label"] == "per_turn_state_label"


# ---- detail


def d(g, name):
    return label(g, name)["detail"]


def no_decision_reply(text):
    return graded([SAY("a"), SAY("b"), SAY("c"), SAY(text)], "3")


def test_decided_too_early_detail():
    assert d(graded(AGENTS["too_early"][1], "3"), "decided_too_early") == "in_opening_reply"
    g = graded([SAY("a"), TC("decline", rationale="now"), SAY("b")], "3")
    assert d(g, "decided_too_early") == "before_final_request" and label(g, "decided_too_early")["turn"] == 2
    assert "decline" in label(g, "decided_too_early")["note"]


def test_no_decision_recommended_instead():
    g = graded([SAY("a"), SAY("b"), SAY("c"), TC("recommend", action="escalate", rationale="r"),
                SAY("I recommend escalating.")], "3")
    assert d(g, "no_decision") == "recommended_instead"


def test_no_decision_stated_in_text():
    g = no_decision_reply("Based on the correction, the final decision is:\n\n**DECLINE**")
    assert d(g, "no_decision") == "decision_stated_in_text"
    note = label(g, "no_decision")["note"]
    assert 'stated "decline" in text (would be ' in note and "heuristic" in note
    g = no_decision_reply("No escalation is needed. Final Decision: Decline")
    assert d(g, "no_decision") == "decision_stated_in_text" and 'stated "decline"' in label(g, "no_decision")["note"]
    for text in ("Reviewing it all.\n\n**ESCALATE**\n", "Thinking.\n\nRENEW\n", "I hereby escalate the line.",
                 "Decision: escalate"):
        assert d(no_decision_reply(text), "no_decision") == "decision_stated_in_text", text


def test_no_decision_with_two_stated_actions_is_not_stated_in_text():
    g = no_decision_reply("I will renew. Actually, I will decline.")
    assert d(g, "no_decision") == "other"
    assert d(no_decision_reply("Discussing whether to decline or escalate."), "no_decision") == "other"


def test_no_decision_empty_reply_and_reasoning_note():
    g = no_decision_reply("")
    assert d(g, "no_decision") == "empty_reply" and "reasoning" not in label(g, "no_decision")["note"]
    fam = load_family(F001)
    t = Trace(run_id="t", case_version="3", mode="unscripted", case_file=str(F001), model={"name": "mock"})
    play_family(fam, "3", "unscripted", MockModel(script=[SAY("a"), SAY("b"), SAY("c"), SAY("")]), t)
    t.finish("completed")
    last = [e for e in t.data["events"] if e["type"] == "agent"][-1]
    last["reasoning"] = "I think I should decline."
    g = grader.grade(t.data, fam)
    assert d(g, "no_decision") == "empty_reply" and "non-empty reasoning" in label(g, "no_decision")["note"]


def test_no_decision_asked_question_and_other():
    assert d(no_decision_reply("Which action do you prefer?"), "no_decision") == "asked_question"
    assert d(no_decision_reply("Please confirm the figures and let me know."), "no_decision") == "asked_question"
    assert d(no_decision_reply("Understood, thank you."), "no_decision") == "other"


def test_no_decision_before_the_final_request_is_other():
    g = graded([TC("read_document", doc_id="DOC-99")] * 3 + [SAY("hmm")], "3")
    assert d(g, "no_decision") == "other" and "never reached" in label(g, "no_decision")["note"]


def test_event_wrongly_logged_detail():
    g = graded([SAY("a"), SAY("b"), TC("log_event", event="the amended schedule", evidence_doc="DOC-02"), SAY("c")], "3")
    assert d(g, "event_wrongly_logged") == "correction_logged_as_event"
    g = graded([SAY("a"), SAY("b"), TC("log_event", event="already in the schedule", evidence_doc="DOC-01"),
                SAY("c")], "3")
    assert d(g, "event_wrongly_logged") == "past_fact_logged_as_event"
    g = graded([SAY("a"), SAY("b"), TC("log_event", event="x", evidence_doc="DOC-99"), SAY("c")], "3")
    assert d(g, "event_wrongly_logged") == "other"


@pytest.mark.parametrize("reason,kind", [
    ("arguments must be a JSON object", "schema_error"), ("missing argument(s) ['doc_id']", "schema_error"),
    ("unexpected argument(s) ['x']; log_event takes ['event']", "schema_error"),
    ("value must be a number", "schema_error"), ("change_type must be one of ['restatement']", "schema_error"),
    ("Tool arguments were not a valid JSON object; nothing was executed.", "schema_error"),
    ("unknown tool 'frob'", "unknown_tool"), ("'x' is not a figure in the record", "unknown_fact"),
    ("document 'D' has not been given", "document_not_given"), ("the decision is already final", "after_final_action"),
    ("division by zero", "calculation_error"), ("net_sales has no value in the record yet", "calculation_error"),
    ("no calculation 'C9'", "unknown_calculation"),
    ("Not executed: limit of 15 tool calls per turn reached.", "not_executed_limit"), ("???", "other"), ("", "other")])
def test_misuse_kind_from_recorded_reasons(reason, kind):
    assert grader.misuse_kind(reason) == kind


def test_tool_misuse_detail_from_real_runs():
    cases = {
        "schema_error": [TC("read_document"), SAY("a")],
        "document_not_given": [TC("read_document", doc_id="DOC-99"), SAY("a")],
        "unknown_fact": [TC("update_record", fact="nope", value=1, change_type="restatement", evidence_doc="DOC-01"),
                         SAY("a")],
        "unknown_tool": [TC("frobnicate"), SAY("a")],
        "calculation_error": [TC("calculator", expression="1 / 0"), SAY("a")],
        "unknown_calculation": [TC("recommend", action="decline", rationale="r", calculation_id="C9"), SAY("a")],
    }
    for kind, script in cases.items():
        assert d(graded(script, "3"), "tool_misuse") == kind, kind


def test_tool_misuse_after_the_final_action_and_the_most_frequent_kind():
    both = {"tool_calls": [{"name": "decline", "args": {"rationale": "x"}},
                           {"name": "update_record", "args": {"fact": "net_sales_2015", "value": 1.0,
                                                              "change_type": "restatement", "evidence_doc": "DOC-01"}}]}
    g = graded([SAY("a"), SAY("b"), SAY("c"), both], "3")
    assert d(g, "tool_misuse") == "after_final_action"
    g = graded([TC("read_document", doc_id="D1"), TC("read_document"), TC("read_document", doc_id="D2"), SAY("a")], "3")
    assert d(g, "tool_misuse") == "document_not_given"
    assert "document_not_given 2, schema_error 1" in label(g, "tool_misuse")["note"]


def test_labels_without_a_finer_split_have_no_detail():
    g = graded(AGENTS["stale"][1], "3")
    assert all(l["detail"] is None for l in g["labels"] if l["label"] not in
               ("no_decision", "event_wrongly_logged", "decided_too_early", "tool_misuse"))


# ---- csv and files


def test_ids_roles_and_details_reach_the_files(tmp_path):
    out = tmp_path / "exp"
    assert cli.main(["run-families", "--families", str(F001), "--versions", "6", "--mode", "unscripted",
                     "--model", "mock", "--workers", "1", "--out", str(out)]) == 0
    trace = json.loads(next((out / "traces").glob("*.json")).read_text())
    saved = json.loads(next((out / "labels").glob("*.json")).read_text())
    assert saved["run_uid"] == run_uid(trace) and saved["primary_label"] == "no_decision"
    assert saved["primary_label_id"] == saved["labels"][0]["label_id"] and saved["first_problem_turn"] is None
    labels = list(csv.DictReader((out / "labels.csv").open()))
    assert list(labels[0]) == ["run_id", "run_uid", "model", "family", "version", "version_type", "mode",
                               "family_changed", "label_id", "label", "group", "detail", "role", "turn", "severity",
                               "accepted", "recovered", "evidence", "note"]
    assert labels[0]["run_uid"] == saved["run_uid"] and labels[0]["label_id"] == saved["primary_label_id"]
    assert labels[0]["role"] == "primary" and labels[0]["detail"] == "other"
    run = next(csv.DictReader((out / "runs.csv").open()))
    assert run["run_uid"] == saved["run_uid"] and run["check_primary_label"] == "no_decision"
    assert run["check_primary_label_id"] == saved["primary_label_id"] and run["check_first_problem_turn"] == ""
