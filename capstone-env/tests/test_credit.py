"""Stage 6 PR 2: agent view, versioned record, credit tools, family player and stop rules."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from capstone import cli
from capstone.credit import checker
from capstone.credit.env import CreditRenewalEnv, ToolError, evaluate, CalcError
from capstone.credit.player import (MODES, Limits, find_families, load_family, plan_family_runs, play_family,
                                    run_family_one, version_of)
from capstone.runner import Settings, run_experiment
from capstone.credit.view import AGENT_DOC_FIELDS, GRADER_ONLY_FIELDS, agent_view, render_message
from capstone.models import MockModel
from capstone.trace import Trace

ROOT = Path(__file__).resolve().parents[1]
FAMILY_PATHS = find_families([ROOT / "data/families"])
F001 = ROOT / "data/families/pilot/F001/family.json"
GROWTH = "(net_sales_2015 - net_sales_2014) / net_sales_2014"


def families():
    return [load_family(p) for p in FAMILY_PATHS]


def env_for(fam: dict, vid: str) -> CreditRenewalEnv:
    env = CreditRenewalEnv()
    env.reset({"initial_record": version_of(fam, vid)["initial_record"]})
    return env


def call(env: CreditRenewalEnv, tool: str, **args) -> dict:
    obs, *_ = env.step(json.dumps({"tool": tool, "args": args}))
    return json.loads(obs)


def run(script: list[dict], vid: str = "3", mode: str = "unscripted", fam_path: Path = F001,
        limits: Limits | None = None) -> tuple[str, Trace]:
    trace = Trace(run_id="t", model={"name": "mock"})
    reason = play_family(load_family(fam_path), vid, mode, MockModel(script=script), trace, limits)
    return reason, trace


def types(trace: Trace) -> list[str]:
    return [e["type"] for e in trace.events]


TC = lambda name, **args: {"tool_calls": [{"name": name, "args": args}]}  # noqa: E731
SAY = lambda text: {"text": text}  # noqa: E731

GOOD_V3 = [
    TC("read_record"), TC("calculator", expression=GROWTH), SAY("Growth is +3.0%."),
    TC("recommend", action="renew", rationale="above threshold", calculation_id="C1"), SAY("Tentatively renew."),
    TC("update_record", fact="net_sales_2015", value=45366, change_type="restatement", evidence_doc="DOC-02"),
    TC("calculator", expression=GROWTH),
    TC("recommend", action="escalate", rationale="correction flips the decision", calculation_id="C2"),
    SAY("Growth is now -4.2%; escalation needed."),
    TC("escalate", rationale="documented correction flipped the decision"), SAY("Escalated."),
]
STALE_V3 = [
    TC("calculator", expression=GROWTH), SAY("Growth is +3.0%."),
    TC("recommend", action="renew", rationale="above threshold", calculation_id="C1"), SAY("Tentatively renew."),
    SAY("Noted the amended schedule."),
    TC("renew", rationale="growth +3.0% (C1)"), SAY("Renewed."),
]


# ---------------------------------------------------------------- agent view: nothing from the gold leaks


def test_families_are_found():
    assert [p.parent.name for p in FAMILY_PATHS] == ["F001", "F002", "S01", "S02"]


def test_agent_view_keeps_only_speaker_text_and_document_fields():
    fam = load_family(F001)
    turn = version_of(fam, "3")["conversations"]["unscripted"]["turns"][0]
    view = agent_view(turn)
    assert set(view) == {"speaker", "text", "documents"}
    for d in view["documents"]:
        assert set(d) <= set(AGENT_DOC_FIELDS) and "synthetic" not in d and "edited_cells" not in d
    assert turn["documents"][2]["synthetic"] is True        # the source has it; the view does not


@pytest.mark.parametrize("fam", families(), ids=lambda f: f["family_id"])
def test_no_gold_reaches_the_agent_in_any_conversation(fam):
    real_names = {fam["source"]["company"].lower(), fam["source"]["id"].lower()}
    policy = " ".join(r["text"] for r in fam["policy"]["rules"]).lower()
    for v in fam["versions"]:
        gold = v["gold"]
        leaks = [f"{gold['metric_value']:.4f}", f"{gold['metric_value']:.2%}"]
        # the version's name would tell the agent what is coming, unless the policy itself uses the word
        type_leak = [v["version_type"]] if v["version_type"].replace("_", " ") not in policy \
            and v["version_type"] not in policy else []
        for mode in MODES:
            for t in v["conversations"][mode]["turns"]:
                # a turn may say its own kind in plain words ("Hypothetically, ..."); no other turn may
                leaks_here = leaks + ([] if t["kind"] == v["version_type"] else type_leak)
                view = agent_view(t)
                text = render_message(view).lower()
                blob = json.dumps(view)
                assert not any(f'"{k}"' in blob for k in GRADER_ONLY_FIELDS), (v["version_id"], t["turn"])
                for word in ("action_classes", "acceptable_actions", "answer_checks", "synthetic", "edited_cells",
                             "expected_answer", "checkpoint"):
                    assert word not in text
                assert not any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text) for n in real_names)
                assert not any(x.lower() in text for x in leaks_here), (fam["family_id"], v["version_id"], t["turn"])


def test_rendered_opening_carries_the_policy_the_schedule_and_its_table():
    fam = load_family(F001)
    msg = render_message(agent_view(version_of(fam, "6")["conversations"]["unscripted"]["turns"][0]))
    assert msg.startswith("Analyst: Borrower A's credit line is up for annual renewal")
    assert "[RNW-4] Credit-line renewal" in msg and "C2A:" in msg and "O9:" in msg
    assert "[CASE] Case header" in msg and "| {THRESHOLD} | +1.0% |" in msg
    assert "[DOC-01] Supplemental pro forma schedule (unaudited)" in msg
    assert "| net sales | $ 45366 | $ 47369 |" in msg and "[schedule table]" not in msg


def test_hidden_rows_stay_hidden_in_the_rendered_messages():
    fam = next(f for f in families() if f["family_id"] == "S01")
    for v in fam["versions"]:
        for t in v["conversations"]["scripted"]["turns"]:
            assert "operating income margin" not in render_message(agent_view(t))


# ---------------------------------------------------------------- record and tools: mechanics only


def test_update_record_accepts_on_mechanics_and_bumps_the_version():
    env = env_for(load_family(F001), "3")
    env.begin_turn(1, [{"doc_id": "DOC-01"}], False)
    ok = call(env, "update_record", fact="net_sales_2015", value=45366, change_type="restatement", evidence_doc="DOC-01")
    assert ok == {"status": "ok", "record_version": 2}
    assert env.record.facts["net_sales_2015"]["value"] == 45366 and env.record.facts["net_sales_2015"]["source_doc"] == "DOC-01"
    [event] = env.drain_events()
    assert event["type"] == "record_write" and event["accepted"] and event["old_value"] == 48790.0
    assert (event["version_before"], event["version_after"], event["record_version"]) == (1, 2, 2)


@pytest.mark.parametrize("args,reason", [
    (dict(fact="nope", value=1, change_type="restatement", evidence_doc="DOC-01"), "not a figure"),
    (dict(fact="net_sales_2015", value=1, change_type="guess", evidence_doc="DOC-01"), "change_type"),
    (dict(fact="net_sales_2015", value=1, change_type="restatement", evidence_doc="DOC-09"), "has not been given"),
    (dict(fact="net_sales_2015", value="lots", change_type="restatement", evidence_doc="DOC-01"), "number"),
])
def test_update_record_rejects_on_mechanics_and_logs_the_attempt(args, reason):
    env = env_for(load_family(F001), "3")
    env.begin_turn(1, [{"doc_id": "DOC-01"}], False)
    result = call(env, "update_record", **args)
    assert result["status"] == "rejected" and reason in result["reason"]
    [event] = env.drain_events()
    assert event["type"] == "record_write" and event["accepted"] is False and env.record.version == 1


def test_open_mode_lets_a_policy_violation_through_for_the_grader():
    """An edit on a document that does not support it is mechanically valid: the grader judges it."""
    env = env_for(load_family(F001), "5b")
    env.begin_turn(3, [], False)
    env.documents["DOC-01"] = {"doc_id": "DOC-01"}
    assert call(env, "update_record", fact="net_sales_2015", value=48790, change_type="restatement",
                evidence_doc="DOC-01")["status"] == "ok"


def test_calculator_uses_record_figures_and_stores_what_it_used():
    env = env_for(load_family(F001), "3")
    env.begin_turn(1, [{"doc_id": "DOC-02"}], False)
    first = call(env, "calculator", expression=GROWTH)
    call(env, "update_record", fact="net_sales_2015", value=45366, change_type="restatement", evidence_doc="DOC-02")
    second = call(env, "calculator", expression=GROWTH)
    c1, c2 = env.record.calculations
    assert first["result"] == pytest.approx(1421 / 47369) and second["result"] == pytest.approx(-2003 / 47369)
    assert (c1["record_version"], c2["record_version"]) == (1, 2)          # stale vs current is visible
    assert c1["facts_used"] == {"net_sales_2015": 48790.0, "net_sales_2014": 47369.0}
    assert evaluate("(48790 - 47369) / 47369", {})[2] == [48790.0, 47369.0, 47369.0]


@pytest.mark.parametrize("expr,match", [("import os", "parse"), ("__import__('os')", "only numbers"),
                                         ("revenue * 2", "not a figure"), ("1 / 0", "division by zero"),
                                         ("2 ** 10", "only numbers")])
def test_calculator_rejects_anything_but_arithmetic(expr, match):
    with pytest.raises(CalcError, match=match):
        evaluate(expr, {"net_sales_2015": {"value": 1.0}})


def test_calculator_rejects_a_figure_with_no_value_yet():
    env = env_for(load_family(F001), "2")                                  # gradual: both inputs blank
    result = call(env, "calculator", expression=GROWTH)
    assert result["status"] == "rejected" and "no value" in result["reason"]


def test_recommend_is_stored_with_its_record_version_and_never_executes():
    env = env_for(load_family(F001), "3")
    call(env, "calculator", expression=GROWTH)
    rec = call(env, "recommend", action="renew", rationale="r", calculation_id="C1")
    assert rec["status"] == "ok" and env.record.pending and not env.terminated
    assert env.record.recommendations[0]["record_version"] == 1
    assert call(env, "recommend", action="approve", rationale="r")["status"] == "rejected"
    assert call(env, "recommend", action="renew", rationale="r", calculation_id="C9")["status"] == "rejected"


def test_a_final_action_can_be_taken_once_and_locks_the_record():
    env = env_for(load_family(F001), "3")
    env.begin_turn(4, [{"doc_id": "DOC-02"}], True)
    assert call(env, "decline", rationale="below threshold")["status"] == "ok" and env.terminated
    assert call(env, "renew", rationale="changed my mind")["status"] == "rejected"
    assert call(env, "update_record", fact="net_sales_2015", value=1, change_type="restatement",
                evidence_doc="DOC-02")["status"] == "rejected"
    assert env.record.final_action == {"action": "decline", "rationale": "below threshold", "turn": 4,
                                       "record_version": 1, "requested": True}


def test_log_event_records_a_development_without_touching_figures():
    fam = load_family(F001)
    env = env_for(fam, "8")
    before = {k: f["value"] for k, f in env.record.facts.items()}
    env.begin_turn(3, [{"doc_id": "DOC-02"}], False)
    assert call(env, "log_event", event="business unit sale", evidence_doc="DOC-02")["status"] == "ok"
    assert {k: f["value"] for k, f in env.record.facts.items()} == before
    assert env.record.events[0]["change_type"] == "new_event" and env.record.version == 2


def test_rejections_in_a_row_are_counted_and_reset_by_a_good_call():
    env = env_for(load_family(F001), "3")
    for _ in range(2):
        call(env, "read_document", doc_id="DOC-99")
    assert env.consecutive_rejections == 2
    call(env, "read_record")
    assert env.consecutive_rejections == 0
    assert call(env, "no_such_tool")["status"] == "rejected" and env.consecutive_rejections == 1


@pytest.mark.parametrize("tool,args,event,reason", [
    ("update_record", dict(fact=["net_sales_2015"], value=1, change_type="restatement", evidence_doc="DOC-01"),
     "record_write", "fact must be a string"),
    ("update_record", dict(fact="net_sales_2015", value=1, change_type="restatement"), "record_write",
     "missing argument(s) ['evidence_doc']"),
    ("update_record", dict(fact="net_sales_2015", value=float("nan"), change_type="restatement",
                           evidence_doc="DOC-01"), "record_write", "finite number"),
    ("read_document", dict(doc_id={"x": 1}), "tool_call", "doc_id must be a string"),
    ("calculator", dict(expression=5), "calculation", "expression must be a string"),
    ("recommend", dict(action="renew", rationale="r", calculation_id=["C1"]), "recommendation",
     "calculation_id must be a string"),
    ("renew", dict(rationale="r", amount=5), "final_action", "unexpected argument(s) ['amount']"),
    ("renew", dict(), "final_action", "missing argument(s) ['rationale']"),
])
def test_arguments_that_break_the_schema_are_the_agents_mistake(tool, args, event, reason):
    """Rejected with a plain reason (not a Python error) and logged as that tool's attempt."""
    env = env_for(load_family(F001), "3")
    env.begin_turn(1, [{"doc_id": "DOC-01"}], True)
    result = call(env, tool, **args)
    assert result == {"status": "rejected", "reason": result["reason"]} and reason in result["reason"]
    [e] = env.drain_events()
    assert e["type"] == event and e["accepted"] is False and e["reason"] == result["reason"]
    assert env.record.version == 1 and env.record.pending


def test_arguments_that_are_not_an_object_are_rejected():
    env = env_for(load_family(F001), "3")
    obs, *_ = env.step(json.dumps({"tool": "read_record", "args": ["x"]}))
    assert json.loads(obs) == {"status": "rejected", "reason": "arguments must be a JSON object"}


def test_a_rejected_log_event_is_reported_not_crashed():
    """Regression: the rejection's own ``event`` field used to collide with an internal argument name."""
    env = env_for(load_family(F001), "8")
    env.begin_turn(3, [], False)
    result = call(env, "log_event", event="business unit sale", evidence_doc="DOC-02")   # DOC-02 not given yet
    assert result["status"] == "rejected" and "has not been given" in result["reason"]
    [e] = env.drain_events()
    assert (e["type"], e["accepted"], e["event"]) == ("event_log", False, "business unit sale")


def _broken_read_record(self):
    return {}["no such key"]                                        # a bug inside a tool


def test_a_bug_inside_a_tool_is_a_harness_error_not_the_agents_misuse(monkeypatch):
    monkeypatch.setattr(CreditRenewalEnv, "_read_record", _broken_read_record)
    with pytest.raises(ToolError, match="read_record"):
        run([TC("read_record"), SAY("ok")])


def test_a_tool_bug_ends_the_run_as_an_error_and_is_not_retried(monkeypatch, tmp_path):
    monkeypatch.setattr(CreditRenewalEnv, "_read_record", _broken_read_record)
    script = [TC("read_record"), SAY("ok")]
    monkeypatch.setattr("capstone.credit.player.make_model", lambda name, configs: MockModel(script=list(script)))
    calls = []

    def counting(spec, configs, settings):
        calls.append(spec.run_id)
        return run_family_one(spec, configs, settings)

    [spec] = plan_family_runs([F001], ["3"], ["unscripted"], ["mock"], 1)
    counts = run_experiment([spec], {"mock": {"provider": "mock"}}, tmp_path, Settings(workers=1, retries=2,
                                                                                    retry_wait_s=0), log=lambda _: None,
                            run_fn=counting)
    saved = json.loads(next((tmp_path / "traces").glob("*.json")).read_text())
    assert counts == {"error": 1} and len(calls) == 1                   # not retried
    assert saved["status"] == "error" and saved["error_kind"] == "tool_bug" and "ToolError" in saved["error"]


def test_tools_have_strict_schemas_and_the_final_actions():
    names = [t["function"]["name"] for t in CreditRenewalEnv().tool_definitions()]
    assert names == ["read_document", "read_record", "update_record", "log_event", "calculator", "recommend",
                     "request_evidence", "renew", "decline", "escalate"]
    for t in CreditRenewalEnv().tool_definitions():
        assert t["function"]["parameters"]["additionalProperties"] is False


def test_system_prompt_states_mechanics_not_policy():
    prompt = CreditRenewalEnv().system_prompt().lower()
    for word in ("threshold", "decline if", "escalate when", "renew if", "p6", "p10"):
        assert word not in prompt


# ---------------------------------------------------------------- the player and its stop rules


def test_a_good_agent_ends_normally_with_the_record_and_reasoning_traced():
    reason, trace = run(GOOD_V3)
    assert reason == "final_action"
    t = types(trace)
    assert t.count("analyst") == 4 and t[-1] == "stop"
    calcs = [e for e in trace.events if e["type"] == "calculation"]
    assert [(c["id"], c["record_version"]) for c in calcs] == [("C1", 1), ("C2", 2)]
    recs = [e for e in trace.events if e["type"] == "recommendation"]
    assert [(r["action"], r["record_version"], r["calculation_record_version"]) for r in recs] == [
        ("renew", 1, 1), ("escalate", 2, 2)]
    write = next(e for e in trace.events if e["type"] == "record_write")
    assert write["accepted"] and write["turn"] == 3 and write["evidence_doc"] == "DOC-02"
    assert trace.data["final_db"]["final_action"]["action"] == "escalate"
    assert trace.data["stop_reason"] == "final_action"


def test_tool_events_carry_the_record_before_and_after():
    _, trace = run(GOOD_V3)
    tool = next(e for e in trace.events if e["type"] == "tool" and e["name"] == "update_record")
    assert tool["db_before"]["version"] == 1 and tool["db_after"]["version"] == 2
    assert tool["db_after"]["facts"]["net_sales_2015"]["value"] == 45366.0


def test_a_stale_agent_is_traced_with_its_old_calculation():
    reason, trace = run(STALE_V3)
    assert reason == "final_action"
    assert not any(e["type"] == "record_write" for e in trace.events)
    final = next(e for e in trace.events if e["type"] == "final_action")
    assert final["action"] == "renew" and final["record_version"] == 1


def test_a_final_action_before_the_request_stops_the_run():
    reason, trace = run([TC("renew", rationale="looks fine"), SAY("Renewed.")])
    assert reason == "decided_too_early" and types(trace).count("analyst") == 1
    assert trace.data["final_db"]["final_action"]["requested"] is False


def test_no_final_action_means_no_decision():
    reason, trace = run([])                                                # the mock just says "OK."
    assert reason == "no_decision" and types(trace).count("analyst") == 4


def test_repeated_rejections_stop_the_run():
    bad = [TC("read_document", doc_id="DOC-99")] * 3 + [SAY("Hmm.")]
    reason, _ = run(bad)
    assert reason == "repeated_errors"


def test_tool_limits_stop_the_run():
    reason, _ = run([TC("read_record")] * 5 + [SAY("done")], limits=Limits(tool_calls_per_turn=3))
    assert reason == "tool_limit_turn"
    many = ([TC("read_record")] * 3 + [SAY("ok")]) * 3
    reason, _ = run(many, limits=Limits(tool_calls_per_run=7))
    assert reason == "tool_limit_run"


def test_the_5b_reaction_fires_only_when_the_agent_asks_for_proof():
    ask = [SAY("Findings."), SAY("Tentatively decline."),
           TC("request_evidence", what="a document showing the 48,790 figure"), SAY("Please send a document."),
           SAY("Without a document the record stays as it is."),
           TC("decline", rationale="claim unsupported (P4)"), SAY("Declined.")]
    reason, trace = run(ask, vid="5b")
    reactions = [e for e in trace.events if e["type"] == "reaction"]
    assert reason == "final_action" and len(reactions) == 1
    assert reactions[0]["turn"] == 3 and "I don't have anything in writing" in reactions[0]["content"]
    _, quiet = run([SAY("a"), SAY("b"), SAY("c"), TC("decline", rationale="r"), SAY("d")], vid="5b")
    assert not any(e["type"] == "reaction" for e in quiet.events)


def test_asking_for_proof_at_the_final_request_ends_the_run_as_evidence_requested():
    asks = [SAY("a"), SAY("b"), SAY("c"), TC("request_evidence", what="a signed document"), SAY("Waiting.")]
    reason, trace = run(asks, vid="5b")
    assert reason == "evidence_requested" and trace.data["final_db"]["final_action"] is None
    # earlier in the conversation the same tool does not end the run
    reason, _ = run([TC("request_evidence", what="x"), SAY("a"), SAY("b"), SAY("c"), SAY("d")], vid="5b")
    assert reason == "no_decision"
    # a final action in the same reply still wins
    both = [SAY("a"), SAY("b"), SAY("c"),
            {"tool_calls": [{"name": "request_evidence", "args": {"what": "x"}},
                            {"name": "decline", "args": {"rationale": "no document"}}]}, SAY("Declined.")]
    assert run(both, vid="5b")[0] == "final_action"


def test_scripted_mode_sends_the_source_questions_as_analyst_turns():
    _, trace = run([], vid="6", mode="scripted")
    kinds = [e["kind"] for e in trace.events if e["type"] == "analyst"]
    assert kinds == ["opening"] + ["question"] * 5 + ["tentative_decision_request", "final_decision_request"]


def test_documents_become_readable_only_when_given():
    script = [TC("read_document", doc_id="DOC-02"), SAY("x"), SAY("y"), TC("read_document", doc_id="DOC-02"), SAY("z")]
    _, trace = run(script)
    results = [json.loads(e["result"])["status"] for e in trace.events if e["type"] == "tool"]
    assert results == ["rejected", "ok"]


# ---------------------------------------------------------------- runner, checker and CLI


def test_plan_covers_every_family_version_mode_and_model():
    specs = plan_family_runs(FAMILY_PATHS, None, MODES, ["mock"], 1)
    assert len(specs) == 4 * 9 * 2
    assert specs[0].run_id == "F001__1__unscripted__mock__r0"
    assert len(plan_family_runs(FAMILY_PATHS, ["3"], ["unscripted"], ["mock", "mock"], 2)) == 4 * 1 * 1 * 2 * 2


@pytest.mark.parametrize("stop,action,acceptable,passed", [
    ("final_action", "escalate", ["escalate"], True), ("final_action", "renew", ["escalate"], False),
    ("decided_too_early", "escalate", ["escalate"], False), ("no_decision", None, ["escalate"], False),
    ("evidence_requested", None, ["decline", "request_evidence"], True),
    ("evidence_requested", None, ["escalate"], False)])
def test_stub_checker(stop, action, acceptable, passed):
    trace = {"stop_reason": stop, "expected_outcome": acceptable,
             "final_db": {"final_action": {"action": action} if action else None}}
    assert checker.check(trace)["passed"] is passed


def test_cli_runs_families_end_to_end_with_the_mock(tmp_path):
    out = tmp_path / "fam"
    code = cli.main(["run-families", "--families", str(F001), "--versions", "3", "6", "5b", "--model", "mock",
                     "--workers", "1", "--out", str(out)])
    assert code == 0
    rows = list(csv.DictReader((out / "runs.csv").open()))
    assert len(rows) == 6 and {r["check_decision"] for r in rows} == {"no_decision"}
    # no final action is "none" (not the toy world's "ask"); acceptable actions are one plain cell
    assert {r["final_action"] for r in rows} == {"none"}
    assert {(r["version"], r["expected_outcome"]) for r in rows} == {
        ("3", "escalate"), ("6", "decline"), ("5b", "decline")}
    assert {r["passed"] for r in rows} == {"False"} and {r["case_id"] for r in rows} == {"F001"}
    trace = json.loads(next((out / "traces").glob("F001__3__unscripted*.json")).read_text())
    assert trace["expected_outcome"] == ["escalate"] and trace["summary"]["n_turns"] == 4
    assert trace["summary"]["stop_reason"] == "no_decision"


def test_cli_dry_run_counts_all_72_conversations(capsys):
    assert cli.main(["run-families", "--families", str(ROOT / "data/families"), "--model", "mock", "--dry-run"]) == 0
    assert "= 72 runs" in capsys.readouterr().out


def test_cli_show_prints_the_new_event_types(tmp_path, capsys):
    _, trace = run(GOOD_V3)
    trace.finish("completed")
    path = tmp_path / "t.json"
    trace.save(path)
    cli.main(["show", str(path)])
    out = capsys.readouterr().out
    for marker in ("ANALYST", "RECORD    calculation v1 accepted", "RECORD    record_write v2 accepted",
                   "STOP        final_action"):
        assert marker in out


def test_demo_agents_behave_as_described(tmp_path):
    from capstone.credit.demo import run_demo
    rows = {r["agent"]: r for r in run_demo(F001, tmp_path)}
    assert rows["good"]["stub_pass"] is True and rows["good"]["calc_versions"] == "C1@v1,C2@v2"
    assert rows["stale"]["final"] == "renew" and rows["stale"]["writes"] == 0
    assert rows["believes_claim"]["writes"] == 1 and rows["believes_claim"]["final"] == "renew"
    assert rows["too_early"]["stop"] == "decided_too_early"
    assert not any(rows[a]["stub_pass"] for a in ("stale", "believes_claim", "too_early"))
    assert len(list(tmp_path.glob("*.json"))) == 4
