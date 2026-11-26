"""Decision state by turn: the grader checks the agent's lean, reaction and recalculation on every turn."""

from __future__ import annotations

import copy

import pytest

from capstone.credit import grader
from capstone.credit.demo import AGENTS
from capstone.credit.player import load_family, play_family
from capstone.models import MockModel
from capstone.trace import Trace
from test_grader import F001, GROWTH, S01, SAY, TC, flagged, graded, label

UPDATE = dict(fact="net_sales_2015", value=45366, change_type="restatement", evidence_doc="DOC-02")


def calls(*cs):
    """One model reply that makes several tool calls: ``("name", {args})`` pairs."""
    return {"tool_calls": [{"name": n, "args": a} for n, a in cs]}


def states(g):
    return {s["turn"]: s for s in g["turn_states"]}


def state_labels(g):
    return [(l["label"], l["turn"], l["detail"]) for l in g["labels"] if l["group"] == "state"]


# v3 unscripted: 1 opening, 2 tentative request (renew), 3 correction (pin moves to escalate), 4 final request.
FOLLOWS = [SAY("noted"),
           calls(("calculator", {"expression": GROWTH}), ("recommend", {"action": "renew", "rationale": "+3%", "calculation_id": "C1"})),
           SAY("tentatively renew"),
           calls(("update_record", UPDATE), ("calculator", {"expression": GROWTH}),
                 ("recommend", {"action": "escalate", "rationale": "now -4.2%", "calculation_id": "C2"})),
           SAY("updated; escalate"),
           calls(("escalate", {"rationale": "flip"})), SAY("done")]


def test_an_agent_that_moves_with_the_pin_is_right_on_every_turn():
    g = graded(FOLLOWS, "3")
    assert g["passed"] and flagged(g, state=True) == [] and g["primary_label"] is None
    assert g["turn_states"] == [
        {"turn": 1, "kind": "opening", "expected_lean": "renew", "pin_moved": False, "observed_lean": None,
         "lean_ok": None, "reaction": "no_change", "reaction_ok": None, "recomputed": None},
        {"turn": 2, "kind": "tentative_decision_request", "expected_lean": "renew", "pin_moved": False,
         "observed_lean": "renew", "lean_ok": True, "reaction": "no_change", "reaction_ok": None, "recomputed": None},
        {"turn": 3, "kind": "correction", "expected_lean": "escalate", "pin_moved": True, "observed_lean": "escalate",
         "lean_ok": True, "reaction": "update_record", "reaction_ok": True, "recomputed": True},
        {"turn": 4, "kind": "final_decision_request", "expected_lean": "escalate", "pin_moved": False,
         "observed_lean": "escalate", "lean_ok": True, "reaction": "no_change", "reaction_ok": None, "recomputed": None}]
    assert g["state_summary"] == {"turns": 4, "lean_stated": 3, "lean_correct": 3, "lean_wrong": 0,
                                  "first_wrong_lean_turn": None}
    assert [(l["label"], l["turn"]) for l in g["labels"] if l["label"] == "lean_correct"] == [
        ("lean_correct", 2), ("lean_correct", 3), ("lean_correct", 4)]
    assert all(l["severity"] == 0 and l["role"] is None and l["label_id"] for l in g["labels"] if l["group"] == "state")


def keeps_old_pin(final="escalate"):
    script = list(FOLLOWS)
    script[3] = calls(("update_record", UPDATE), ("calculator", {"expression": GROWTH}),
                      ("recommend", {"action": "renew", "rationale": "still fine", "calculation_id": "C2"}))
    script[5] = calls((final, {"rationale": "x"}))
    return script


def test_an_agent_that_keeps_the_old_pin_gets_lean_wrong_stale_lean():
    g = graded(keeps_old_pin(), "3")
    assert g["passed"]                      # its final decision is valid: the state labels never change `passed`
    wrong = label(g, "lean_wrong")
    assert (wrong["turn"], wrong["severity"], wrong["detail"]) == (3, 2, "stale_lean")
    assert wrong["note"] == "renew; expected escalate"          # a correction turn has no checkpoint, so no class
    assert states(g)[3]["lean_ok"] is False and states(g)[3]["reaction_ok"] and states(g)[3]["recomputed"]
    assert g["state_summary"]["first_wrong_lean_turn"] == 3
    assert flagged(g, state=True) == ["lean_wrong"] and g["primary_label"] == "lean_wrong" and g["first_problem_turn"] == 3
    assert wrong["role"] == "primary" and wrong["label_id"] == g["primary_label_id"]


def test_the_primary_problem_rule_picks_the_wrong_lean_on_turn_3_over_the_wrong_final_decision_on_turn_4():
    g = graded(keeps_old_pin("renew"), "3")
    assert g["decision"] == "invalid_approval" and not g["passed"]
    assert label(g, "invalid_approval")["turn"] == 4
    assert g["primary_label"] == "lean_wrong" and g["first_problem_turn"] == 3
    assert label(g, "invalid_approval")["role"] == "secondary"
    # the final action is wrong but earns no lean_wrong of its own; the turn state still shows it
    assert [l["turn"] for l in g["labels"] if l["label"] == "lean_wrong"] == [3]
    assert states(g)[4]["lean_ok"] is False and g["state_summary"]["lean_wrong"] == 2


def test_a_wrong_lean_that_is_not_the_old_pin_is_wrong_lean():
    script = list(FOLLOWS)
    script[3] = calls(("update_record", UPDATE), ("calculator", {"expression": GROWTH}),
                      ("recommend", {"action": "decline", "rationale": "x", "calculation_id": "C2"}))
    g = graded(script, "3")
    assert label(g, "lean_wrong")["detail"] == "wrong_lean"
    no_move = graded([SAY("a"), calls(("recommend", {"action": "renew", "rationale": "x"})), SAY("b")], "6")
    assert label(no_move, "lean_wrong")["detail"] == "wrong_lean" and label(no_move, "lean_wrong")["turn"] == 2


def test_an_update_one_turn_late_is_a_late_update():
    script = [SAY("noted"), calls(("recommend", {"action": "renew", "rationale": "x"})), SAY("tentative"),
              SAY("I see the correction."),                                    # turn 3: nothing applied
              calls(("update_record", UPDATE), ("calculator", {"expression": GROWTH}), ("escalate", {"rationale": "flip"})),
              SAY("done")]
    g = graded(script, "3")
    late = label(g, "late_update")
    assert (late["turn"], late["severity"], late["recovered"], late["accepted"]) == (3, 1, True, True)
    assert "applied on turn 4" in late["note"] and late["evidence"] == [next(
        e["i"] for e in _events(script, "3") if e["type"] == "record_write")]
    assert states(g)[3]["reaction_ok"] is False and states(g)[3]["recomputed"] is False
    assert "missed_update" not in flagged(g) and g["passed"]


def _events(script, vid):
    fam = load_family(F001)
    trace = Trace(run_id="t", case_version=vid, mode="unscripted", case_file=str(F001), model={"name": "mock"})
    play_family(fam, vid, "unscripted", MockModel(script=script), trace)
    return trace.data["events"]


def test_an_update_that_is_never_applied_has_no_late_update_only_missed_update():
    g = graded([SAY("a"), SAY("b"), SAY("c"), calls(("escalate", {"rationale": "x"})), SAY("d")], "3")
    assert "late_update" not in flagged(g, state=True) and "missed_update" in flagged(g)


def test_recommending_after_the_update_without_recalculating_is_not_recomputed():
    script = list(FOLLOWS)
    script[1] = calls(("recommend", {"action": "renew", "rationale": "x"}))      # no calculation anywhere before
    script[3] = calls(("update_record", UPDATE), ("recommend", {"action": "escalate", "rationale": "x"}))
    g = graded(script, "3")
    nr = label(g, "not_recomputed")
    assert (nr["turn"], nr["severity"], nr["group"]) == (3, 1, "state")
    assert nr["evidence"] == [next(e["i"] for e in _events(script, "3") if e["type"] == "recommendation" and e["turn"] == 3)]
    assert states(g)[3]["recomputed"] is False and states(g)[3]["reaction_ok"] is True
    assert flagged(g, state=True) == ["not_recomputed"] and g["passed"]


def test_not_recomputed_is_not_added_where_a_math_label_already_covers_the_recommendation():
    stale_cite = list(FOLLOWS)
    stale_cite[3] = calls(("update_record", UPDATE), ("recommend", {"action": "escalate", "rationale": "x", "calculation_id": "C1"}))
    g = graded(stale_cite, "3")
    assert flagged(g, state=True) == ["stale_calculation_cited"]
    stale_calc = list(FOLLOWS)
    stale_calc[3] = calls(("update_record", UPDATE), ("calculator", {"expression": "(48790 - 47369) / 47369"}),
                          ("recommend", {"action": "escalate", "rationale": "x", "calculation_id": "C2"}))
    g = graded(stale_calc, "3")
    assert "used_stale_figure" in flagged(g) and "not_recomputed" not in flagged(g, state=True)
    wrong_record = [SAY("a"), SAY("b"), SAY("c"), calls(("escalate", {"rationale": "x"})), SAY("d")]
    assert "not_recomputed" not in flagged(graded(wrong_record, "3"), state=True)    # never applied: nothing to recompute


NEW_GROWTH = "(45366 - 47369) / 47369"     # the corrected 2015 figure typed as a number, before it is in the record


def test_recalculating_with_the_new_figure_just_before_writing_it_counts_as_recomputed():
    # Seen in the Nov 23 Qwen run (S01 v3 and v7): calculate from the correction document, then write the record.
    script = list(FOLLOWS)
    script[3] = calls(("calculator", {"expression": NEW_GROWTH}), ("update_record", UPDATE),
                      ("recommend", {"action": "escalate", "rationale": "now -4.2%", "calculation_id": "C2"}))
    g = graded(script, "3")
    assert "not_recomputed" not in flagged(g, state=True)
    assert states(g)[3]["recomputed"] is True and states(g)[3]["reaction_ok"] is True


def test_recalculating_with_the_new_figure_in_the_due_turn_counts_when_the_write_is_a_turn_late():
    script = [SAY("noted"), calls(("recommend", {"action": "renew", "rationale": "x"})), SAY("tentative"),
              calls(("calculator", {"expression": NEW_GROWTH})), SAY("growth is now negative"),   # turn 3: no write
              calls(("update_record", UPDATE), ("escalate", {"rationale": "flip"})), SAY("done")]
    g = graded(script, "3")
    assert "late_update" in flagged(g, state=True) and "not_recomputed" not in flagged(g, state=True)
    assert states(g)[3]["recomputed"] is False      # the change was not applied in its own turn


def test_a_calculation_on_the_old_figure_before_the_write_is_still_not_recomputed():
    script = list(FOLLOWS)
    script[3] = calls(("calculator", {"expression": GROWTH}), ("update_record", UPDATE),
                      ("recommend", {"action": "escalate", "rationale": "x"}))
    g = graded(script, "3")
    assert "not_recomputed" in flagged(g, state=True) and states(g)[3]["recomputed"] is False


def test_a_final_action_after_the_update_with_no_calculation_is_not_recomputed():
    script = [SAY("a"), SAY("b"), calls(("update_record", UPDATE)), SAY("applied"),
              calls(("escalate", {"rationale": "flip"})), SAY("d")]
    g = graded(script, "3")
    nr = label(g, "not_recomputed")
    assert nr["turn"] == 3 and "final action" in nr["note"]
    assert flagged(g, state=True) == ["not_recomputed"]


def test_an_agent_that_never_states_a_lean_gets_an_observation_not_a_problem():
    script = [SAY("noted"), SAY("I would renew."),                       # the answer is in text only
              calls(("update_record", UPDATE), ("calculator", {"expression": GROWTH})), SAY("updated"),
              calls(("escalate", {"rationale": "flip"})), SAY("done")]
    g = graded(script, "3")
    ns = label(g, "lean_not_stated")
    assert (ns["turn"], ns["severity"], ns["detail"], ns["role"], ns["evidence"]) == (2, 0, "at_tentative_request", None, [])
    assert g["passed"] and flagged(g, state=True) == [] and g["primary_label"] is None
    assert g["state_summary"] == {"turns": 4, "lean_stated": 1, "lean_correct": 1, "lean_wrong": 0,
                                  "first_wrong_lean_turn": None}      # only the final action states a lean


def test_lean_not_stated_after_the_pin_moved():
    # v2: asks at turn 2 (waiting is right), then nothing at turns 4 and 6; the pin moved to decline on turn 5
    script = [SAY("a"), calls(("request_evidence", {"what": "the figures"})), SAY("send them"), SAY("b"), SAY("c"),
              SAY("d"), SAY("e")]
    g = graded(script, "2")
    assert [(t, d) for n, t, d in state_labels(g) if n == "lean_not_stated"] == [(4, "at_tentative_request"), (6, "after_pin_moved")]


def test_gradual_disclosure_waiting_is_right_and_deciding_early_is_wrong():
    g = graded([SAY("a"), calls(("request_evidence", {"what": "the 2015 and 2014 net sales"})), SAY("waiting")], "2")
    assert states(g)[2]["observed_lean"] == "waiting_for_evidence" and states(g)[2]["lean_ok"] is True
    assert ("lean_correct", 2, None) in state_labels(g)
    g = graded([SAY("a"), calls(("recommend", {"action": "decline", "rationale": "weak"})), SAY("b")], "2")
    wrong = label(g, "lean_wrong")
    assert (wrong["turn"], wrong["detail"], wrong["severity"]) == (2, "wrong_lean", 2)
    assert wrong["note"] == "decline; expected waiting_for_evidence; class unnecessary_block"
    assert states(g)[2]["expected_lean"] == "waiting_for_evidence" and states(g)[2]["lean_ok"] is False


def test_the_gradual_disclosure_pin_moves_when_the_second_figure_arrives():
    g = graded([SAY("a"), SAY("b"), calls(("update_record", dict(UPDATE, change_type="gap_filled"))), SAY("c"),
                SAY("d"), calls(("update_record", dict(fact="net_sales_2014", value=47369, change_type="gap_filled",
                                                       evidence_doc="DOC-03")),
                                ("calculator", {"expression": GROWTH}), ("recommend", {"action": "decline", "rationale": "x",
                                                                                      "calculation_id": "C1"})),
                SAY("e")], "2")
    s = states(g)
    assert s[3]["reaction_ok"] and s[3]["recomputed"] is None
    assert s[5]["pin_moved"] and s[5]["reaction_ok"] and s[5]["recomputed"] and s[5]["lean_ok"]


def test_a_new_event_logged_in_its_turn_and_a_turn_late():
    on_time = graded([SAY("a"), SAY("b"), calls(("log_event", {"event": "sale of a business unit", "evidence_doc": "DOC-02"})),
                      SAY("logged"), calls(("decline", {"rationale": "below"})), SAY("d")], "8")
    assert states(on_time)[3]["reaction"] == "log_event" and states(on_time)[3]["reaction_ok"] is True
    assert "late_event_log" not in flagged(on_time, state=True)
    late = graded([SAY("a"), SAY("b"), SAY("noted"),
                   calls(("log_event", {"event": "sale of a business unit", "evidence_doc": "DOC-02"}),
                         ("decline", {"rationale": "below"})), SAY("d")], "8")
    assert states(late)[3]["reaction_ok"] is False
    le = label(late, "late_event_log")
    assert (le["turn"], le["severity"], le["recovered"]) == (3, 1, True) and "applied on turn 4" in le["note"]
    assert late["passed"] and "event_not_logged" not in flagged(late)
    never = graded([SAY("a"), SAY("b"), SAY("noted"), calls(("decline", {"rationale": "below"})), SAY("d")], "8")
    assert "late_event_log" not in flagged(never, state=True) and "event_not_logged" in flagged(never)


def test_an_event_logged_for_a_correction_does_not_count_as_the_update():
    g = graded([SAY("a"), SAY("b"), calls(("log_event", {"event": "correction", "evidence_doc": "DOC-02"})), SAY("c"),
                SAY("d")], "3")
    assert states(g)[3]["reaction"] == "update_record" and states(g)[3]["reaction_ok"] is False
    assert "late_update" not in flagged(g, state=True)


def test_asking_for_proof_after_a_claim_states_no_lean():
    """RNW-4 E6 allows it and the pin does not move: no lean_wrong (and no waiting_for_evidence lean)."""
    asks = [SAY("a"), calls(("recommend", {"action": "decline", "rationale": "x"})), SAY("b"),
            calls(("request_evidence", {"what": "a document for 48,790"})), SAY("please send it"), SAY("ok"),
            calls(("decline", {"rationale": "unsupported"})), SAY("d")]
    g = graded(asks, "5b")
    assert states(g)[3]["observed_lean"] is None and states(g)[3]["lean_ok"] is None
    assert flagged(g, state=True) == [] and g["passed"]


def test_asking_for_proof_when_none_is_needed_is_a_wrong_lean_but_not_on_the_final_request():
    g = graded([calls(("request_evidence", {"what": "more documents"})), SAY("a"), SAY("b"), SAY("c")], "6")
    assert states(g)[1]["observed_lean"] == "waiting_for_evidence" and states(g)[1]["lean_ok"] is False
    final_ask = graded([SAY("a"), SAY("b"), calls(("request_evidence", {"what": "more"})), SAY("waiting")], "6")
    assert final_ask["decision"] == "unnecessary_block"
    assert states(final_ask)[3]["lean_ok"] is False and "lean_wrong" not in flagged(final_ask, state=True)


def test_deciding_too_early_is_not_also_a_wrong_lean():
    g = graded(AGENTS["too_early"][1], "3")
    assert [s["turn"] for s in g["turn_states"]] == [1] and g["turn_states"][0]["observed_lean"] is not None
    assert flagged(g, state=True) == ["decided_too_early"]


def test_turn_states_cover_every_played_turn_including_questions_and_stop_when_the_run_stops():
    g = graded([], "3", mode="scripted")
    assert [s["turn"] for s in g["turn_states"]] == list(range(1, 10))
    assert [s["kind"] for s in g["turn_states"]].count("question") == 5
    assert g["state_summary"]["turns"] == 9 and g["state_summary"]["lean_stated"] == 0
    assert {l["detail"] for l in g["labels"] if l["label"] == "lean_not_stated"} == {"at_tentative_request"}
    assert [l["turn"] for l in g["labels"] if l["label"] == "lean_not_stated"] == [7]
    short = graded([TC("read_document", doc_id="DOC-99")] * 3 + [SAY("hmm")], "3")      # repeated errors on turn 1
    assert [s["turn"] for s in short["turn_states"]] == [1]


def test_in_scripted_mode_the_correction_turn_is_graded_on_its_own_turn_number():
    script = [SAY("a")] + [SAY("q")] * 5 + [calls(("recommend", {"action": "renew", "rationale": "x"})), SAY("t"),
              calls(("update_record", UPDATE), ("calculator", {"expression": GROWTH}),
                    ("recommend", {"action": "escalate", "rationale": "x", "calculation_id": "C1"})), SAY("c"),
              calls(("escalate", {"rationale": "x"})), SAY("d")]
    g = graded(script, "3", mode="scripted")
    assert g["passed"] and flagged(g, state=True) == []
    s = states(g)
    assert s[7]["lean_ok"] and (s[8]["kind"], s[8]["reaction_ok"], s[8]["recomputed"], s[8]["lean_ok"]) == ("correction", True, True, True)


@pytest.mark.parametrize("mode", ["unscripted", "scripted"])
def test_a_family_file_without_expected_state_grades_without_state(mode):
    fam = load_family(F001)
    trace = Trace(run_id="t", case_version="3", mode=mode, case_file=str(F001), model={"name": "mock"})
    play_family(fam, "3", mode, MockModel(script=list(FOLLOWS)), trace)
    trace.finish("completed")
    old = copy.deepcopy(fam)
    for v in old["versions"]:
        for conv in v["conversations"].values():
            for t in conv["turns"]:
                del t["expected_state"]
    new, legacy = grader.grade(trace.data, fam), grader.grade(trace.data, old)
    assert legacy["turn_states"] == [] and legacy["state_summary"] is None
    assert not [l for l in legacy["labels"] if l["group"] == "state"]
    # everything else is identical to the grade with state
    keep = lambda g: [{k: v for k, v in l.items() if k not in ("role", "label_id")} for l in g["labels"] if l["group"] != "state"]  # noqa: E731
    assert keep(legacy) == keep(new)
    assert [new[k] == legacy[k] for k in ("passed", "decision", "final_record_failures")] == [True] * 3


def test_state_labels_never_change_pass_decision_or_the_other_labels():
    for script, vid in ((keeps_old_pin("renew"), "3"), (FOLLOWS, "3"), (keeps_old_pin(), "3")):
        fam = load_family(F001)
        trace = Trace(run_id="t", case_version=vid, mode="unscripted", case_file=str(F001), model={"name": "mock"})
        play_family(fam, vid, "unscripted", MockModel(script=list(script)), trace)
        trace.finish("completed")
        old = copy.deepcopy(fam)
        for v in old["versions"]:
            for conv in v["conversations"].values():
                for t in conv["turns"]:
                    del t["expected_state"]
        a, b = grader.grade(trace.data, fam), grader.grade(trace.data, old)
        assert (a["passed"], a["decision"], a["final_record_failures"], a["attempted_invalid_changes"]) == (
            b["passed"], b["decision"], b["final_record_failures"], b["attempted_invalid_changes"])


def test_check_reports_the_first_wrong_lean_turn_for_runs_csv(tmp_path):
    fam = load_family(F001)
    trace = Trace(run_id="t", case_version="3", mode="unscripted", case_file=str(F001), model={"name": "mock"})
    play_family(fam, "3", "unscripted", MockModel(script=keeps_old_pin()), trace)
    trace.finish("completed")
    assert grader.check_allow_family_change(trace.data)["first_wrong_lean_turn"] == 3


def test_the_renew_family_decline_at_the_tentative_step_is_wrong():
    g = graded([SAY("a"), calls(("recommend", {"action": "decline", "rationale": "cautious"})), SAY("b")], "6", path=S01)
    assert states(g)[2]["expected_lean"] == "renew" and label(g, "lean_wrong")["note"].startswith("decline; expected renew")
