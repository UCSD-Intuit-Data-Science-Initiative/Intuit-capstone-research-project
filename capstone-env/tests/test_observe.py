"""Optional Langfuse export: what each trace event becomes, and that it never changes a trace."""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from capstone import cli, observe

TRACE = {
    "run_id": "F001__3__unscripted__mock__r0", "case_id": "F001", "case_version": "3",
    "case_type": "correction_after_tentative_decision", "mode": "unscripted", "repeat": 0,
    "model": {"name": "mock", "provider": "mock"}, "status": "completed", "env": "credit_renewal",
    "summary": {"final_action": "escalate", "n_turns": 2},
    "events": [
        {"i": 0, "type": "customer", "t": 0.0, "turn": 1, "content": "Borrower A's credit line is up for renewal."},
        {"i": 1, "type": "agent", "t": 0.4, "turn": 1, "text": "", "stop_reason": "tool_use", "served_by": "mock",
         "tool_calls": [{"id": "c1", "name": "read_record", "args": {}}],
         "usage": {"tokens_in": 120, "tokens_out": 30, "latency_s": 0.4, "ttft_s": 0.1, "tokens_per_s": 75.0,
                   "cost_usd": 0.0012}},
        {"i": 2, "type": "tool", "t": 0.5, "turn": 1, "name": "read_record", "args": {}, "executed": True,
         "result": "{\"net_sales_2015\": 48790}", "decision": None},
        {"i": 3, "type": "tool", "t": 0.6, "turn": 1, "name": "renew", "args": {}, "executed": False,
         "result": "{\"status\": \"error\"}"},
        {"i": 4, "type": "step_cap", "t": 0.7, "turn": 1, "limit": 15},
        {"i": 5, "type": "record_write", "t": 0.8, "turn": 2, "fact": "net_sales_2015", "version": 2},
    ],
}


class FakeObs:
    def __init__(self, calls: list, kind: str, kwargs: dict):
        self.calls = calls
        calls.append((kind, kwargs))

    def start_observation(self, **kw):
        return FakeObs(self.calls, "start_observation", kw)

    def create_event(self, **kw):
        self.calls.append(("create_event", kw))

    def update(self, **kw):
        self.calls.append(("update", kw))

    def end(self, **kw):
        self.calls.append(("end", kw))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeClient:
    def __init__(self):
        self.calls: list = []
        self.flushed = 0

    def create_trace_id(self, *, seed=None):
        self.calls.append(("create_trace_id", {"seed": seed}))
        return hashlib.md5(seed.encode()).hexdigest()

    def start_as_current_observation(self, **kw):
        return FakeObs(self.calls, "start_as_current_observation", kw)

    def create_score(self, **kw):
        self.calls.append(("create_score", kw))

    def flush(self):
        self.flushed += 1


def fake_propagate(calls):
    @contextlib.contextmanager
    def propagate(**kw):
        calls.append(("propagate", kw))
        yield
    return propagate


GRADE = {
    "run_id": TRACE["run_id"], "family_id": "F001", "version": "3", "version_type": TRACE["case_type"],
    "mode": "unscripted", "decision": "over_escalated", "passed": False, "attempted_invalid_changes": 2,
    "accepted_invalid_changes": 1, "final_record_failures": ["wrong_change_type_at_end"], "max_severity": 2,
    "family_changed": False, "note": "",
    "labels": [
        {"label": "over_escalated", "group": "decision", "turn": 2, "severity": 1, "note": "escalate; acceptable: renew"},
        {"label": "correct_update", "group": "record", "turn": 1, "severity": 0, "note": "ok"},
        {"label": "wrong_change_type_at_end", "group": "record", "turn": None, "severity": 2,
         "note": "x is recorded as restatement; expected gap_filled"},
    ],
}


# A grade file with the fields added for run and label IDs, the primary problem and the decision state.
UID = "r-0123456789ab"
TURN_STATES = [
    {"turn": 1, "kind": "opening", "expected_lean": "renew", "pin_moved": False, "observed_lean": None,
     "lean_ok": None, "reaction": "no_change", "reaction_ok": None, "recomputed": None},
    {"turn": 2, "kind": "tentative_decision_request", "expected_lean": "renew", "pin_moved": False,
     "observed_lean": "renew", "lean_ok": True, "reaction": "no_change", "reaction_ok": None, "recomputed": None},
    {"turn": 3, "kind": "correction", "expected_lean": "escalate", "pin_moved": True, "observed_lean": "renew",
     "lean_ok": False, "reaction": "update_record", "reaction_ok": False, "recomputed": None},
]
NEW_GRADE = {
    **GRADE, "run_uid": UID, "primary_label": "event_wrongly_logged", "primary_label_id": f"{UID}-aaaaaa",
    "first_problem_turn": 3, "turn_states": TURN_STATES,
    "state_summary": {"turns": 3, "lean_stated": 2, "lean_correct": 1, "lean_wrong": 1, "first_wrong_lean_turn": 3},
    "labels": [
        {"label_id": f"{UID}-bbbbbb", "label": "over_escalated", "group": "decision", "detail": None,
         "role": "secondary", "turn": 4, "severity": 1, "accepted": None, "recovered": None, "evidence": [20],
         "note": "escalate; acceptable: renew"},
        {"label_id": f"{UID}-cccccc", "label": "correct_update", "group": "record", "detail": None, "role": None,
         "turn": 2, "severity": 0, "accepted": True, "recovered": True, "evidence": [8], "note": "ok"},
        {"label_id": f"{UID}-aaaaaa", "label": "event_wrongly_logged", "group": "record",
         "detail": "correction_logged_as_event", "role": "primary", "turn": 3, "severity": 2, "accepted": True,
         "recovered": False, "evidence": [12], "note": "the correction was logged as an event"},
        {"label_id": f"{UID}-dddddd", "label": "missed_update", "group": "record", "detail": None,
         "role": "secondary", "turn": 3, "severity": 2, "accepted": None, "recovered": None, "evidence": [],
         "note": "net_sales_2015 was never written"},
    ],
}

# A trace whose model calls carry saved thinking: one call asks for two tools at once (an environment
# event sits between their executions), a later call in the same turn has no thinking but says something.
THINKING_TRACE = {
    **TRACE, "started_at": "2026-11-23T18:11:00+00:00",
    "events": [
        {"i": 0, "type": "analyst", "t": 0.0, "turn": 1, "content": "Please review the renewal."},
        {"i": 1, "type": "agent", "t": 0.1, "turn": 1, "text": "", "stop_reason": "tool_use", "served_by": "m",
         "reasoning": "I need the record first.\nThen the growth rate.",
         "tool_calls": [{"id": "call_0", "name": "read_record", "args": {}},
                        {"id": "call_1", "name": "calculator", "args": {"expression": "1 + 1"}}],
         "usage": {"tokens_in": 10, "tokens_out": 5, "latency_s": 0.1, "ttft_s": None, "tokens_per_s": None,
                   "cost_usd": None}},
        {"i": 2, "type": "tool", "t": 0.2, "turn": 1, "id": "call_0", "name": "read_record", "args": {},
         "executed": True, "result": "{}"},
        {"i": 3, "type": "tool", "t": 0.3, "turn": 1, "id": "call_1", "name": "calculator",
         "args": {"expression": "1 + 1"}, "executed": True, "result": "{\"value\": 2}"},
        {"i": 4, "type": "calculation", "t": 0.3, "turn": 1, "tool_call_id": "call_1", "id": "C1", "value": 2},
        {"i": 5, "type": "agent", "t": 0.4, "turn": 1, "text": "Recording my tentative view.", "reasoning": "",
         "stop_reason": "tool_use", "served_by": "m",
         "tool_calls": [{"id": "call_0", "name": "recommend", "args": {"action": "renew"}}],   # id reused
         "usage": {"tokens_in": 10, "tokens_out": 5, "latency_s": 0.1, "ttft_s": None, "tokens_per_s": None,
                   "cost_usd": None}},
        {"i": 6, "type": "tool", "t": 0.5, "turn": 1, "id": "call_0", "name": "recommend",
         "args": {"action": "renew"}, "executed": True, "result": "{}"},
        {"i": 7, "type": "agent", "t": 0.6, "turn": 1, "text": "Tentatively renew.", "reasoning": "Done for now.",
         "tool_calls": [], "stop_reason": "end_turn", "served_by": "m",
         "usage": {"tokens_in": 10, "tokens_out": 5, "latency_s": 0.1, "ttft_s": None, "tokens_per_s": None,
                   "cost_usd": None}},
    ],
}


def exported(trace=TRACE, experiment="exp1", grade=None):
    client = FakeClient()
    trace_id = observe.export_trace(trace, client, experiment, fake_propagate(client.calls), grade)
    return client, trace_id


def scores_sent(client):
    return [kw for kind, kw in client.calls if kind == "create_score"]


def test_each_event_type_becomes_the_right_observation():
    client, _ = exported()
    starts = [kw for kind, kw in client.calls if kind == "start_observation"]
    gen, tool_ok, tool_refused = starts
    assert gen["as_type"] == "generation" and gen["usage_details"] == {"input": 120, "output": 30}
    assert gen["cost_details"] == {"total": 0.0012} and gen["metadata"]["latency_s"] == 0.4
    assert tool_ok["as_type"] == "tool" and tool_ok["output"] == {"net_sales_2015": 48790}
    assert tool_ok["level"] == "DEFAULT" and tool_refused["level"] == "WARNING"
    events = [kw["name"] for kind, kw in client.calls if kind == "create_event"]
    assert events == ["turn 1: customer message", "turn 1: step_cap", "turn 2: record_write"]
    assert sum(kind == "end" for kind, _ in client.calls) == 3


def test_the_models_thinking_reaches_langfuse_with_its_reply():
    trace = {**TRACE, "events": [{**TRACE["events"][1], "reasoning": "Growth is below the threshold."}]}
    client, _ = exported(trace)
    [gen] = [kw for kind, kw in client.calls if kind == "start_observation"]
    assert gen["output"]["reasoning"] == "Growth is below the threshold."
    client, _ = exported()                                  # no thinking recorded: no empty field
    gen = next(kw for kind, kw in client.calls if kind == "start_observation")
    assert "reasoning" not in gen["output"]


def test_unknown_event_types_keep_their_fields():
    client, _ = exported()
    record = next(kw for kind, kw in client.calls if kind == "create_event" and kw["name"].endswith("record_write"))
    assert record["metadata"] == {"i": 5, "t": 0.8, "fact": "net_sales_2015", "version": 2}


def test_root_carries_the_summary_tags_and_a_deterministic_trace_id():
    client, trace_id = exported()
    root = next(kw for kind, kw in client.calls if kind == "start_as_current_observation")
    assert root["trace_context"] == {"trace_id": trace_id} and root["name"] == TRACE["run_id"]
    prop = next(kw for kind, kw in client.calls if kind == "propagate")
    assert prop["session_id"] == "exp1" and "model:mock" in prop["tags"] and "case_id:F001" in prop["tags"]
    final = [kw for kind, kw in client.calls if kind == "update"][-1]
    assert final["output"] == TRACE["summary"] and final["level"] == "DEFAULT"
    assert exported()[1] == trace_id                     # same run, same id
    assert exported(experiment="exp2")[1] != trace_id


def test_an_errored_run_is_marked_as_an_error():
    client, _ = exported({**TRACE, "status": "error", "error": "Traceback...\nValueError: boom\n"})
    final = [kw for kind, kw in client.calls if kind == "update"][-1]
    assert final["level"] == "ERROR" and final["status_message"] == "ValueError: boom"


@pytest.mark.parametrize("which", ["old trace and grade", "thinking trace and new grade"])
def test_our_calls_match_the_installed_langfuse_api(which):
    """Every call and argument we make, checked against the installed SDK's signatures; the new grade and
    the trace with saved thinking reach every new argument (tool metadata, new scores, run_uid)."""
    langfuse = pytest.importorskip("langfuse")
    trace, grade = (TRACE, GRADE) if which.startswith("old") else (THINKING_TRACE, NEW_GRADE)
    client, _ = exported(trace, grade=grade)
    real = {"create_score": langfuse.Langfuse.create_score,
            "start_observation": langfuse.LangfuseSpan.start_observation,
            "create_event": langfuse.LangfuseSpan.create_event, "update": langfuse.LangfuseSpan.update,
            "end": langfuse.LangfuseSpan.end,
            "start_as_current_observation": langfuse.Langfuse.start_as_current_observation,
            "create_trace_id": langfuse.Langfuse.create_trace_id, "propagate": langfuse.propagate_attributes}
    for kind, kw in client.calls:
        fn = real[kind]
        args = () if kind in ("propagate", "create_trace_id") else (None,)
        assert kind != "create_score" or kw["data_type"] in ("NUMERIC", "CATEGORICAL", "BOOLEAN")
        inspect.signature(fn).bind(*args, **kw)          # raises TypeError on an unknown argument
        if kind == "create_score":
            json.dumps(kw.get("metadata"))               # the SDK sends score metadata as JSON
            assert isinstance(kw["value"], float if kw["data_type"] != "CATEGORICAL" else str)
        if kind == "propagate":
            # The SDK drops (with a warning) a propagated metadata value that is not short: JSON-encoded
            # values over 200 characters (langfuse/_client/propagation.py). Ours must all get through.
            assert all(len(json.dumps(v)) <= 200 for v in kw["metadata"].values())
            assert all(isinstance(t, str) and len(t) <= 200 for t in kw["tags"])


def test_real_langfuse_sdk_exports_offline():
    pytest.importorskip("langfuse")
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    client = Langfuse(public_key="pk-lf-test", secret_key="sk-lf-test", base_url="http://localhost:9",
                      span_exporter=exporter)
    trace_id = observe.export_trace(TRACE, client, "exp1")
    client.flush()
    spans = exporter.get_finished_spans()
    assert {format(s.context.trace_id, "032x") for s in spans} == {trace_id}
    names = [s.name for s in spans]
    assert TRACE["run_id"] in names and "turn 1: model call" in names and "turn 1: tool read_record" in names


def write_experiment(tmp_path: Path) -> Path:
    traces = tmp_path / "exp1" / "traces"
    traces.mkdir(parents=True)
    for n in range(2):
        (traces / f"run{n}.json").write_text(json.dumps({**TRACE, "run_id": f"run{n}"}))
    return tmp_path / "exp1"


class FlushLogClient(FakeClient):
    def flush(self):
        super().flush()
        self.calls.append(("flush", {}))


def test_export_runs_finds_traces_and_flushes_after_each(tmp_path):
    out = write_experiment(tmp_path)
    client, lines = FlushLogClient(), []
    assert observe.export_runs([out], client, lines.append, fake_propagate([])) == 2
    assert client.flushed == 2 and lines[-1] == "langfuse: 2 runs sent"
    # Each run is flushed before the next one starts, so a long export cannot fill the client's queue.
    order = [kind for kind, _ in client.calls if kind in ("create_trace_id", "flush")]
    assert order == ["create_trace_id", "flush", "create_trace_id", "flush"]
    seeds = [kw["seed"] for kind, kw in client.calls if kind == "create_trace_id"]
    assert seeds == ["exp1/run0", "exp1/run1"]
    assert observe.trace_files([out / "traces" / "run0.json"]) == [out / "traces" / "run0.json"]
    with pytest.raises(observe.ObserveError, match="no trace files"):
        observe.export_runs([tmp_path / "empty"], client)


def test_missing_keys_give_a_clear_message_and_change_nothing(tmp_path, monkeypatch, capsys):
    for key in observe.REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(observe.ObserveError, match="LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY"):
        observe.make_client()
    out = write_experiment(tmp_path)
    before = {p: p.read_text() for p in (out / "traces").glob("*.json")}
    assert cli.main(["langfuse", str(out)]) == 1
    assert "langfuse: not sent (set LANGFUSE_PUBLIC_KEY" in capsys.readouterr().out
    assert {p: p.read_text() for p in (out / "traces").glob("*.json")} == before


def test_run_with_langfuse_but_no_keys_still_completes(tmp_path, monkeypatch, capsys):
    for key in observe.REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    case = Path(__file__).resolve().parents[1] / "cases" / "example_invoice_correction.yaml"
    code = cli.main(["run", "--model", "mock", "--cases", str(case), "--mode", "scripted", "--k", "1",
                     "--workers", "1", "--out", str(tmp_path / "exp"), "--langfuse"])
    printed = capsys.readouterr().out
    assert code == 0 and "langfuse: not sent" in printed
    assert len(list((tmp_path / "exp" / "traces").glob("*.json"))) == 2


def test_credit_runs_carry_the_real_source_as_tags_and_metadata(tmp_path):
    """The agent sees "Borrower C"; Langfuse shows teammates the real company and filing."""
    (tmp_path / "data/families/specs").mkdir(parents=True)
    (tmp_path / "data/families/specs/S09.yaml").write_text('page_renames: [["benfield", "Acquired Broker"]]\n')
    fam = tmp_path / "data/families/smoke/S09/family.json"
    fam.parent.mkdir(parents=True)
    fam.write_text(json.dumps({"family_id": "S09", "borrower": "Borrower C", "policy": {"id": "RNW-4"},
                               "provenance": {"spec": "data/families/specs/S09.yaml"},
                               "source": {"company": "AON", "id": "Single_AON/2009/page_46.pdf-3",
                                          "year": "2009", "dataset": "ConvFinQA"}}))
    trace = {"env": "credit_renewal", "case_file": "data/families/smoke/S09/family.json", "case_id": "S09",
             "status": "completed"}
    source = observe.source_info(trace, root=tmp_path)
    assert source["company"] == "AON" and source["renames"] == {"benfield": "Acquired Broker"}
    tags = observe.trace_tags(trace, source)
    assert {"company:AON", "source:Single_AON/2009/page_46.pdf-3", "borrower:Borrower C", "policy:RNW-4"} <= set(tags)
    assert observe.source_info({**trace, "env": "toy_invoice"}, root=tmp_path) == {}
    assert observe.source_info({**trace, "case_file": "missing.json"}, root=tmp_path) == {}


def test_the_grade_becomes_scores_on_the_runs_trace():
    client, trace_id = exported(grade=GRADE)
    scores = {(s["name"], s["value"]): s for s in scores_sent(client)}
    assert all(s["trace_id"] == trace_id for s in scores.values())
    assert scores[("passed", 0.0)]["data_type"] == "BOOLEAN"
    assert scores[("passed", 0.0)]["comment"] == "over_escalated, wrong_change_type_at_end"
    assert scores[("decision", "over_escalated")]["comment"] == "escalate; acceptable: renew"
    assert scores[("max_severity", 2.0)]["data_type"] == "NUMERIC"
    assert scores[("final_record_failures", 1.0)]["comment"] == "wrong_change_type_at_end"
    assert scores[("invalid_changes_accepted", 1.0)]["comment"] == "2 attempted"
    problems = [s for s in scores_sent(client) if s["name"] == "problem"]
    assert [p["value"] for p in problems] == ["over_escalated", "wrong_change_type_at_end"]   # severity 0 left out
    assert problems[1]["comment"].startswith("end of run: ") and problems[0]["comment"].startswith("turn 2: ")


def test_score_ids_are_stable_so_resending_updates_instead_of_duplicating():
    first, second = exported(grade=GRADE)[0], exported(grade=GRADE)[0]
    ids = [s["score_id"] for s in scores_sent(first)]
    assert ids == [s["score_id"] for s in scores_sent(second)] and len(set(ids)) == len(ids)
    other = exported(experiment="exp2", grade=GRADE)[0]
    assert not set(ids) & {s["score_id"] for s in scores_sent(other)}


def test_a_passing_run_says_so():
    grade = {**GRADE, "decision": "valid", "passed": True, "final_record_failures": [], "max_severity": 0,
             "accepted_invalid_changes": 0, "labels": [{"label": "valid", "group": "decision", "turn": 3,
                                                       "severity": 0, "note": "renew; acceptable: renew"}]}
    scores = scores_sent(exported(grade=grade)[0])
    assert [s["name"] for s in scores] == ["passed", "decision", "max_severity", "final_record_failures",
                                           "invalid_changes_accepted"]
    assert scores[0]["value"] == 1.0 and scores[0]["comment"] == "no problems"
    assert "comment" not in scores[3]      # None values are not sent


def test_ungraded_runs_send_no_scores():
    assert scores_sent(exported()[0]) == []


def write_graded_experiment(tmp_path: Path) -> Path:
    out = write_experiment(tmp_path)
    (out / "labels").mkdir()
    (out / "labels" / "run0.json").write_text(json.dumps({**GRADE, "run_id": "run0"}))
    return out


def test_export_runs_attaches_grades_from_labels(tmp_path):
    out = write_graded_experiment(tmp_path)
    client, lines = FakeClient(), []
    observe.export_runs([out], client, lines.append, fake_propagate([]))
    graded_id = hashlib.md5(b"exp1/run0").hexdigest()
    assert {s["trace_id"] for s in scores_sent(client)} == {graded_id}
    assert lines[0] == "langfuse: sent run0 with 7 scores" and lines[1] == "langfuse: sent run1"


def test_scores_only_adds_scores_to_runs_already_sent(tmp_path):
    out = write_graded_experiment(tmp_path)
    client, lines = FakeClient(), []
    assert observe.export_runs([out], client, lines.append, fake_propagate([]), scores_only=True) == 1
    kinds = {kind for kind, _ in client.calls}
    assert kinds == {"create_trace_id", "create_score"}          # the trace itself is not sent again
    assert {s["trace_id"] for s in scores_sent(client)} == {hashlib.md5(b"exp1/run0").hexdigest()}
    assert "langfuse: skipped run1 (not graded)" in lines and lines[-1] == "langfuse: 1 runs scored"
    with pytest.raises(observe.ObserveError, match="no grades"):
        observe.export_runs([write_experiment(tmp_path / "ungraded")], client, scores_only=True)


def test_cli_scores_only_flag_reaches_the_export(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(observe, "export_runs", lambda paths, **kw: seen.update(kw) or 0)
    assert cli.main(["langfuse", str(tmp_path), "--scores-only"]) == 0
    assert seen == {"scores_only": True}


# ---------------------------------------------------------------- run and label IDs, primary problem, state


def test_an_old_grade_file_exports_exactly_the_scores_it_did_before():
    """GRADE has none of the new fields (run_uid, label_id, primary_label, state_summary, ...): the scores and
    their ids are the ones the export sent before those fields existed, unchanged."""
    meta = {k: GRADE[k] for k in ("family_id", "version", "version_type", "mode", "note", "family_changed")}
    problem_meta = lambda l: {k: l.get(k) for k in ("group", "severity", "turn", "accepted", "recovered",  # noqa: E731
                                                    "evidence")}
    assert observe.grade_scores(GRADE) == [
        {"key": "passed", "name": "passed", "value": 0.0, "data_type": "BOOLEAN",
         "comment": "over_escalated, wrong_change_type_at_end", "metadata": meta},
        {"key": "decision", "name": "decision", "value": "over_escalated", "data_type": "CATEGORICAL",
         "comment": "escalate; acceptable: renew"},
        {"key": "max_severity", "name": "max_severity", "value": 2.0, "data_type": "NUMERIC"},
        {"key": "final_record_failures", "name": "final_record_failures", "value": 1.0, "data_type": "NUMERIC",
         "comment": "wrong_change_type_at_end"},
        {"key": "invalid_changes_accepted", "name": "invalid_changes_accepted", "value": 1.0,
         "data_type": "NUMERIC", "comment": "2 attempted"},
        {"key": "problem/0", "name": "problem", "value": "over_escalated", "data_type": "CATEGORICAL",
         "comment": "turn 2: escalate; acceptable: renew", "metadata": problem_meta(GRADE["labels"][0])},
        {"key": "problem/1", "name": "problem", "value": "wrong_change_type_at_end", "data_type": "CATEGORICAL",
         "comment": "end of run: x is recorded as restatement; expected gap_filled",
         "metadata": problem_meta(GRADE["labels"][2])},
    ]
    client, trace_id = exported(grade=GRADE)
    ids = [s["score_id"] for s in scores_sent(client)]
    assert ids[-2:] == [hashlib.sha256(f"{trace_id}/problem/{n}".encode()).hexdigest()[:32] for n in (0, 1)]


def by_name(scores):
    out: dict = {}
    for s in scores:
        out.setdefault(s["name"], []).append(s)
    return out


def test_problem_scores_are_keyed_by_label_id_and_carry_detail_and_role():
    client, trace_id = exported(grade=NEW_GRADE)
    problems = by_name(scores_sent(client))["problem"]
    assert [p["value"] for p in problems] == ["over_escalated", "event_wrongly_logged", "missed_update"]
    for p, lid in zip(problems, (f"{UID}-bbbbbb", f"{UID}-aaaaaa", f"{UID}-dddddd")):
        assert p["score_id"] == hashlib.sha256(f"{trace_id}/problem/{lid}".encode()).hexdigest()[:32]
        assert p["metadata"]["label_id"] == lid
    primary = problems[1]
    assert primary["comment"] == "turn 3 [correction_logged_as_event]: the correction was logged as an event"
    assert primary["metadata"]["detail"] == "correction_logged_as_event" and primary["metadata"]["role"] == "primary"
    assert problems[0]["comment"] == "turn 4: escalate; acceptable: renew"          # no detail: as before
    assert problems[0]["metadata"]["detail"] is None and problems[2]["metadata"]["role"] == "secondary"
    assert primary["metadata"]["evidence"] == [12] and primary["data_type"] == "CATEGORICAL"


def test_primary_problem_first_problem_turn_and_state_scores():
    scores = by_name(scores_sent(exported(grade=NEW_GRADE)[0]))
    [primary] = scores["primary_problem"]
    assert primary["value"] == "event_wrongly_logged" and primary["data_type"] == "CATEGORICAL"
    assert primary["comment"] == "correction_logged_as_event, turn 3"
    assert primary["metadata"] == {"primary_label_id": f"{UID}-aaaaaa"}
    assert scores["first_problem_turn"][0]["value"] == 3.0 and scores["first_problem_turn"][0]["data_type"] == "NUMERIC"
    assert {n: scores[n][0]["value"] for n in ("lean_stated", "lean_correct", "lean_wrong", "first_wrong_lean_turn")} \
        == {"lean_stated": 2.0, "lean_correct": 1.0, "lean_wrong": 1.0, "first_wrong_lean_turn": 3.0}
    assert all(scores[n][0]["data_type"] == "NUMERIC" for n in ("lean_stated", "lean_correct", "lean_wrong"))
    assert scores["lean_stated"][0]["comment"] == "of 3 turns"
    passed = scores["passed"][0]["metadata"]
    assert passed["turn_states"] == TURN_STATES and passed["run_uid"] == UID
    # a primary problem with no detail and no turn (e.g. record_wrong_at_end)
    grade = {**NEW_GRADE, "primary_label": "missed_update", "primary_label_id": f"{UID}-dddddd",
             "first_problem_turn": None}
    grade["labels"] = [{**l, "turn": None} if l["label"] == "missed_update" else l for l in NEW_GRADE["labels"]]
    scores = by_name(scores_sent(exported(grade=grade)[0]))
    assert scores["primary_problem"][0]["comment"] == "end of run" and "first_problem_turn" not in scores


def test_a_grade_with_no_problem_has_primary_problem_none():
    grade = {**NEW_GRADE, "passed": True, "decision": "valid", "max_severity": 0, "final_record_failures": [],
             "primary_label": None, "primary_label_id": None, "first_problem_turn": None,
             "state_summary": {"turns": 3, "lean_stated": 2, "lean_correct": 2, "lean_wrong": 0,
                               "first_wrong_lean_turn": None},
             "labels": [{"label_id": f"{UID}-eeeeee", "label": "valid", "group": "decision", "detail": None,
                         "role": None, "turn": 4, "severity": 0, "accepted": None, "recovered": None,
                         "evidence": [20], "note": "renew; acceptable: renew"}]}
    scores = by_name(scores_sent(exported(grade=grade)[0]))
    assert scores["primary_problem"][0]["value"] == "none" and "comment" not in scores["primary_problem"][0]
    assert scores["primary_problem"][0]["metadata"] == {"primary_label_id": None}
    assert "first_problem_turn" not in scores and "first_wrong_lean_turn" not in scores and "problem" not in scores
    assert scores["lean_wrong"][0]["value"] == 0.0 and scores["passed"][0]["comment"] == "no problems"


STATE_SCORES = {"lean_stated", "lean_correct", "lean_wrong", "first_wrong_lean_turn"}


@pytest.mark.parametrize("summary, expected", [
    ("present", STATE_SCORES),
    ("first wrong turn None", STATE_SCORES - {"first_wrong_lean_turn"}),
    ("None", set()),
    ("absent", set()),
])
def test_state_summary_present_none_or_absent(summary, expected):
    grade = dict(NEW_GRADE)
    if summary == "first wrong turn None":
        grade["state_summary"] = {**NEW_GRADE["state_summary"], "first_wrong_lean_turn": None}
    elif summary == "None":
        grade["state_summary"] = None            # a family file without expected_state
    elif summary == "absent":
        del grade["state_summary"]               # a grade file written before the field existed
    names = {s["name"] for s in scores_sent(exported(grade=grade)[0])}
    assert names & STATE_SCORES == expected


@pytest.mark.parametrize("turn_states", ["empty", "absent"])
def test_turn_states_are_left_out_of_the_passed_metadata_when_there_are_none(turn_states):
    grade = {**NEW_GRADE, "turn_states": []}
    if turn_states == "absent":
        del grade["turn_states"]
    passed = next(s for s in scores_sent(exported(grade=grade)[0]) if s["name"] == "passed")
    assert "turn_states" not in passed["metadata"]


def test_resending_the_same_new_grade_gives_the_same_score_ids():
    first, second = scores_sent(exported(grade=NEW_GRADE)[0]), scores_sent(exported(grade=NEW_GRADE)[0])
    ids = [s["score_id"] for s in first]
    assert ids == [s["score_id"] for s in second] and len(set(ids)) == len(ids)
    assert first == second


def test_a_regrade_that_drops_a_label_does_not_reuse_its_score_id_for_another_label():
    regraded = {**NEW_GRADE, "labels": [l for l in NEW_GRADE["labels"] if l["label"] != "over_escalated"]}
    before = {s["score_id"]: s["value"] for s in scores_sent(exported(grade=NEW_GRADE)[0]) if s["name"] == "problem"}
    after = {s["score_id"]: s["value"] for s in scores_sent(exported(grade=regraded)[0]) if s["name"] == "problem"}
    assert len(before) == 3 and len(after) == 2
    assert all(before[i] == v for i, v in after.items())          # every id kept its label
    dropped = next(i for i, v in before.items() if v == "over_escalated")
    assert dropped not in after                                   # left behind in Langfuse, never re-used
    # For contrast, the positional fallback of old grade files does move ids between labels:
    old = [{k: v for k, v in l.items() if k not in ("label_id", "detail", "role")} for l in NEW_GRADE["labels"]]
    pos_before = {s["score_id"]: s["value"] for s in scores_sent(exported(grade={**GRADE, "labels": old})[0])
                  if s["name"] == "problem"}
    pos_after = {s["score_id"]: s["value"] for s in scores_sent(exported(grade={**GRADE, "labels": old[1:]})[0])
                 if s["name"] == "problem"}
    assert any(pos_before[i] != v for i, v in pos_after.items())


# ---------------------------------------------------------------- run_uid on the trace


def test_the_trace_carries_run_uid_as_a_tag_and_in_its_metadata():
    from capstone.trace import run_uid
    client, trace_id = exported(THINKING_TRACE)
    uid = run_uid(THINKING_TRACE)
    prop = next(kw for kind, kw in client.calls if kind == "propagate")
    root = next(kw for kind, kw in client.calls if kind == "start_as_current_observation")
    assert f"run_uid:{uid}" in prop["tags"] and prop["metadata"]["run_uid"] == uid
    assert root["metadata"]["run_uid"] == uid
    # the Langfuse trace id is still seeded by experiment and run_id, so runs already sent update in place
    assert trace_id == hashlib.md5(f"exp1/{TRACE['run_id']}".encode()).hexdigest() == exported()[1]
    assert run_uid(THINKING_TRACE) != run_uid(TRACE)                # another play of the same run_id


# ---------------------------------------------------------------- the model's thinking beside each tool call


def tool_observations(client):
    return [kw for kind, kw in client.calls if kind == "start_observation" and kw["as_type"] == "tool"]


def test_tool_observations_carry_the_issuing_calls_thinking_and_text():
    client, _ = exported(THINKING_TRACE)
    record, calc, recommend = tool_observations(client)
    thinking = THINKING_TRACE["events"][1]["reasoning"]
    for obs in (record, calc):                       # both tools of one model call
        assert obs["metadata"]["decided_by_event"] == 1 and obs["metadata"]["decided_by_match"] == "tool_call_id"
        assert obs["metadata"]["reasoning"] == thinking
        assert "said" not in obs["metadata"]         # that call said nothing: no empty string
    assert calc["metadata"]["id"] == "call_1"        # the tool event's own fields are kept
    # a later call reused the id "call_0": the tool goes to the latest call listing it
    assert recommend["metadata"]["decided_by_event"] == 5
    assert recommend["metadata"]["said"] == "Recording my tentative view."
    assert "reasoning" not in recommend["metadata"]  # no thinking returned: key left out


def test_long_thinking_is_not_shortened():
    events = [dict(e) for e in THINKING_TRACE["events"]]
    events[1]["reasoning"] = "x" * 50_000
    client, _ = exported({**THINKING_TRACE, "events": events})
    assert tool_observations(client)[0]["metadata"]["reasoning"] == "x" * 50_000


def test_tool_issuers_match_by_id_else_by_order_within_the_turn():
    events = THINKING_TRACE["events"]
    issuers = observe.tool_issuers(events)
    assert {n: (a["i"], how) for n, (a, how) in issuers.items()} == {
        2: (1, "tool_call_id"), 3: (1, "tool_call_id"), 6: (5, "tool_call_id")}
    # tool events without ids (older traces, and the TRACE fixture): the latest model call of the turn
    no_ids = observe.tool_issuers(TRACE["events"])
    assert {n: (a["i"], how) for n, (a, how) in no_ids.items()} == {2: (1, "turn_order"), 3: (1, "turn_order")}
    # an id no model call of the turn lists, or a tool event in another turn, is left unmatched
    odd = [events[1], {**events[2], "id": "call_9"}, {**events[3], "turn": 2}]
    assert observe.tool_issuers(odd) == {}


def test_tool_observations_without_a_matching_call_get_no_issuer_keys():
    events = [THINKING_TRACE["events"][1], {**THINKING_TRACE["events"][2], "id": "call_9"}]
    client, _ = exported({**THINKING_TRACE, "events": events})
    [obs] = tool_observations(client)
    assert not {"decided_by_event", "decided_by_match", "reasoning", "said"} & set(obs["metadata"])


def test_the_export_does_not_change_the_trace_or_the_grade():
    trace, grade = json.dumps(THINKING_TRACE, sort_keys=True), json.dumps(NEW_GRADE, sort_keys=True)
    exported(THINKING_TRACE, grade=NEW_GRADE)
    assert json.dumps(THINKING_TRACE, sort_keys=True) == trace and json.dumps(NEW_GRADE, sort_keys=True) == grade


def test_real_langfuse_sdk_keeps_the_thinking_and_run_uid_offline():
    """The installed SDK (no network: an in-memory span exporter) puts the issuer keys on the tool span,
    unshortened, and run_uid in the trace attributes."""
    pytest.importorskip("langfuse")
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from capstone.trace import run_uid
    exporter = InMemorySpanExporter()
    # its own public key: the SDK shares one span exporter per key, so another test's would get the spans
    client = Langfuse(public_key="pk-lf-test-thinking", secret_key="sk-lf-test", base_url="http://localhost:9",
                      span_exporter=exporter)
    events = [dict(e) for e in THINKING_TRACE["events"]]
    events[1]["reasoning"] = "y" * 20_000
    observe.export_trace({**THINKING_TRACE, "events": events}, client, "exp1")
    client.flush()
    spans = {s.name: s.attributes for s in exporter.get_finished_spans()}
    tool = spans["turn 1: tool calculator"]
    assert json.loads(tool["langfuse.observation.metadata.reasoning"]) == "y" * 20_000
    assert json.loads(tool["langfuse.observation.metadata.decided_by_event"]) == 1
    assert "langfuse.observation.metadata.said" not in tool
    uid = run_uid(THINKING_TRACE)
    assert json.loads(tool["langfuse.trace.metadata.run_uid"]) == uid
    assert f"run_uid:{uid}" in tool["langfuse.trace.tags"]


def test_real_langfuse_sdk_builds_every_new_score(monkeypatch):
    """The installed SDK's create_score catches its own errors and only logs them, so a bad argument would
    pass silently: capture what it queues for upload instead (nothing is sent)."""
    pytest.importorskip("langfuse")
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    client = Langfuse(public_key="pk-lf-test-scores", secret_key="sk-lf-test", base_url="http://localhost:9",
                      span_exporter=InMemorySpanExporter())
    queued: list = []

    class Resources:
        def add_score_task(self, score, **kw):
            queued.append(score)

    monkeypatch.setattr(client, "_resources", Resources())
    trace_id = client.create_trace_id(seed="exp1/run0")
    n = observe.send_scores(client, trace_id, json.loads(json.dumps(NEW_GRADE)))
    assert len(queued) == n == len(observe.grade_scores(NEW_GRADE)) and len({s["id"] for s in queued}) == n
    primary = next(s for s in queued if s["name"] == "primary_problem")
    assert primary["value"] == "event_wrongly_logged" and primary["dataType"] == "CATEGORICAL"
    assert primary["metadata"] == {"primary_label_id": f"{UID}-aaaaaa"} and primary["traceId"] == trace_id
    assert next(s for s in queued if s["name"] == "passed")["metadata"]["turn_states"] == TURN_STATES
    assert {s["name"] for s in queued} >= {"first_problem_turn", "lean_wrong", "first_wrong_lean_turn", "problem"}


# ---------------------------------------------------------------- CLI and end to end


def write_new_graded_experiment(tmp_path: Path) -> Path:
    out = write_experiment(tmp_path)
    (out / "labels").mkdir()
    (out / "labels" / "run0.json").write_text(json.dumps({**NEW_GRADE, "run_id": "run0"}))
    return out


def test_cli_langfuse_scores_only_sends_the_new_scores(tmp_path, monkeypatch, capsys):
    out = write_new_graded_experiment(tmp_path)
    client = FakeClient()
    monkeypatch.setattr(observe, "make_client", lambda: client)
    assert cli.main(["langfuse", str(out), "--scores-only"]) == 0
    assert {kind for kind, _ in client.calls} == {"create_trace_id", "create_score"}
    names = [s["name"] for s in scores_sent(client)]
    assert {"primary_problem", "first_problem_turn", "lean_stated", "lean_correct", "lean_wrong",
            "first_wrong_lean_turn"} <= set(names)
    assert f"langfuse: scored run0: {len(names)} scores" in capsys.readouterr().out


F001 = Path(__file__).resolve().parents[1] / "data/families/pilot/F001/family.json"
GROWTH = "(net_sales_2015 - net_sales_2014) / net_sales_2014"


def test_run_families_with_langfuse_sends_the_new_scores(tmp_path, monkeypatch):
    client, real_export = FakeClient(), observe.export_runs
    monkeypatch.setattr(observe, "export_runs", lambda paths, **kw: real_export(
        paths, client, propagate=fake_propagate(client.calls), **kw))
    out = tmp_path / "exp"
    assert cli.main(["run-families", "--families", str(F001), "--versions", "3", "--mode", "unscripted",
                     "--model", "mock", "--workers", "1", "--out", str(out), "--langfuse"]) == 0
    grade = json.loads(next((out / "labels").glob("*.json")).read_text())
    scores = by_name(scores_sent(client))
    assert grade["primary_label"] and scores["primary_problem"][0]["value"] == grade["primary_label"]
    assert {p["metadata"]["label_id"] for p in scores["problem"]} == \
        {l["label_id"] for l in grade["labels"] if l["severity"] > 0}
    prop = next(kw for kind, kw in client.calls if kind == "propagate")
    assert prop["metadata"]["run_uid"] == grade["run_uid"]


def test_end_to_end_a_mock_run_with_thinking_graded_by_the_real_grader():
    """Play a scripted agent (with saved thinking, and one reply asking for two tools) on F001 version 3,
    grade it with the real grader, export trace and grade to a fake client."""
    from capstone.credit import grader
    from capstone.credit.player import load_family, play_family, version_of
    from capstone.models import MockModel
    from capstone.trace import Trace, run_uid

    class ThinkingMock(MockModel):
        def __init__(self, script):
            super().__init__(script=[{k: v for k, v in s.items() if k != "reasoning"} for s in script])
            self._thinking = [s.get("reasoning", "") for s in script]

        def chat(self, system, messages, tools):
            reply = super().chat(system, messages, tools)
            reply.reasoning = self._thinking.pop(0) if self._thinking else ""
            return reply

    script = [
        {"reasoning": "Read the record and work out growth.",
         "tool_calls": [{"name": "read_record", "args": {}}, {"name": "calculator", "args": {"expression": GROWTH}}]},
        {"text": "Growth is +3.0%, above +1.0%."},
        {"reasoning": "Above the threshold, so renew for now.",
         "tool_calls": [{"name": "recommend", "args": {"action": "renew", "rationale": "growth above",
                                                       "calculation_id": "C1"}}]},
        {"text": "Tentatively renew."},
        {"text": "Noted the amended schedule."},                       # ignores the correction: stale
        {"reasoning": "Still above the threshold.",
         "tool_calls": [{"name": "renew", "args": {"rationale": "growth +3.0% (C1)"}}]},
        {"text": "Renewed."},
    ]
    fam = load_family(F001)
    trace = Trace(run_id="e2e__F001__3", case_id="F001", case_version="3",
                  case_type=version_of(fam, "3")["version_type"], mode="unscripted", case_file=str(F001),
                  env="credit_renewal", model={"name": "mock"}, repeat=0)
    play_family(fam, "3", "unscripted", ThinkingMock(script), trace)
    trace.finish("completed")
    data = json.loads(json.dumps(trace.data))
    g = json.loads(json.dumps(grader.grade(data, fam)))
    assert g["primary_label"] and not g["passed"]

    client, trace_id = exported(data, grade=g)
    events = data["events"]
    tools = [e for e in events if e["type"] == "tool"]
    obs = tool_observations(client)
    assert len(tools) == 4 and [o["name"] for o in obs] == [f"turn {e['turn']}: tool {e['name']}" for e in tools]
    for e, o in zip(tools, obs):
        caller = events[o["metadata"]["decided_by_event"]]
        assert caller["type"] == "agent" and e["id"] in [c["id"] for c in caller["tool_calls"]]
        assert o["metadata"]["decided_by_match"] == "tool_call_id"
        assert o["metadata"]["reasoning"] == caller["reasoning"]    # every scripted tool call came with thinking
    assert obs[0]["metadata"]["decided_by_event"] == obs[1]["metadata"]["decided_by_event"]   # one call, two tools

    scores = by_name(scores_sent(client))
    assert scores["primary_problem"][0]["value"] == g["primary_label"]
    assert scores["primary_problem"][0]["metadata"] == {"primary_label_id": g["primary_label_id"]}
    problems = [l for l in g["labels"] if l["severity"] > 0]
    assert [p["score_id"] for p in scores["problem"]] == \
        [hashlib.sha256(f"{trace_id}/problem/{l['label_id']}".encode()).hexdigest()[:32] for l in problems]
    assert [p["metadata"]["role"] for p in scores["problem"]].count("primary") == 1
    assert scores["passed"][0]["metadata"]["turn_states"] == g["turn_states"]
    assert scores["passed"][0]["metadata"]["run_uid"] == g["run_uid"] == run_uid(data)
    assert ("first_problem_turn" in scores) == (g["first_problem_turn"] is not None)
    assert scores["lean_stated"][0]["value"] == float(g["state_summary"]["lean_stated"])
    prop = next(kw for kind, kw in client.calls if kind == "propagate")
    assert f"run_uid:{g['run_uid']}" in prop["tags"]
