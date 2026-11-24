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


def test_our_calls_match_the_installed_langfuse_api():
    langfuse = pytest.importorskip("langfuse")
    client, _ = exported(grade=GRADE)
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


def test_export_runs_finds_traces_and_flushes_once(tmp_path):
    out = write_experiment(tmp_path)
    client, lines = FakeClient(), []
    assert observe.export_runs([out], client, lines.append, fake_propagate([])) == 2
    assert client.flushed == 1 and lines[-1] == "langfuse: 2 runs sent"
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
