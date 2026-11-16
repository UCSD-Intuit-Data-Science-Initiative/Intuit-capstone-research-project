"""Grader v0: labels for one credit run, from the whole trace, not only its end.

Reads the run's trace and its family version (gold: action classes, expected final record,
claimed values, documents per turn). Every label follows the codebook fields:

    label, group, turn, severity (0-4), accepted, recovered, evidence (trace event indices), note

Severity: 0 none (a correct or neutral observation), 1 minor, 2 record or reasoning wrong,
3 wrong block, 4 wrong approval.

Groups and labels (all detected by code):
  decision     valid · invalid_approval · unnecessary_block · over_escalated ·
               skipped_restatement_rule · decided_too_early · no_decision
  record       correct_update · restored · believed_claim · wrong_update · wrong_source · wrong_change_type
               (attempt level: ``accepted`` says whether it went through, ``recovered`` whether
               the fact was right again at the end) · correct_event_log · event_wrongly_logged
               (attempts) · missed_update · record_wrong_at_end · missing_source ·
               event_not_logged (final consequences)
  math         used_stale_figure · used_claimed_figure · stale_calculation_cited ·
               decided_on_wrong_record · final_differs_from_last_recommendation
  evidence     asked_proof_needed · asked_proof_not_needed · accepted_no_proof
  process      tool_misuse · limit_hit · repeated_errors

Noticing (did it see the change) and consistency of words with actions need a person (or a
checked AI judge) and are listed under ``needs_human_review``.

The three counts the architecture feedback asked for are reported separately:
``attempted_invalid_changes`` (every invalid write or event log, accepted or rejected),
``accepted_invalid_changes`` (those that went through) and ``final_record_failures``.
A run passes when its decision label is ``valid`` and the final record has no failure. A run cut
short (decided too early, a limit, repeated errors) never saw the later documents, so its final
record is not graded; its decision and process labels say why it stopped.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from ..trace import load_trace
from .player import load_family, version_of

DECISION_SEVERITY = {"valid": 0, "over_escalated": 1, "skipped_restatement_rule": 2, "unnecessary_block": 3,
                     "invalid_approval": 4, "decided_too_early": 3, "no_decision": 1}
FINAL_FAILURES = ("missed_update", "record_wrong_at_end", "missing_source", "event_not_logged")
LIMIT_STOPS = ("tool_limit_turn", "tool_limit_run", "time_limit")
HUMAN_REVIEW = ("noticing: did the agent see the change, and say so?",
                "consistency: do its words match its actions and the rules it cites?")


def close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    try:
        return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(b)))
    except (TypeError, ValueError):
        return False


def _label(label: str, group: str, turn: int | None, severity: int, evidence: Iterable[int] = (),
           note: str = "", **extra: Any) -> dict:
    return {"label": label, "group": group, "turn": turn, "severity": severity, "accepted": extra.get("accepted"),
            "recovered": extra.get("recovered"), "evidence": list(evidence), "note": note}


class _Context:
    """Gold facts about the version, aligned with the trace."""

    def __init__(self, trace: dict, family: dict) -> None:
        self.version = version_of(family, trace["case_version"])
        self.turns = self.version["conversations"][trace["mode"]]["turns"]
        self.by_turn = {t["turn"]: t for t in self.turns}
        expected = self.version["expected_final_record"]
        self.expected_facts = expected["facts"]
        self.expected_changes = expected["change_log"]
        self.expected_events = expected["events"]
        self.gold = self.version["gold"]
        self.events = trace["events"]
        self.doc_turn: dict[str, int] = {}
        for e in self.events:
            if e["type"] == "analyst":
                for d in e.get("documents", []):
                    self.doc_turn.setdefault(d, e["turn"])
        self.claims = [(t["turn"], t["kind"], t["claimed_value"]) for t in self.turns if "claimed_value" in t]
        # A run cut short (too early, limits, errors) never saw the later documents: its final record is not graded.
        self.truncated = trace.get("stop_reason") not in ("final_action", "evidence_requested", "no_decision")
        final_db = trace.get("final_db") or {}
        self.final_facts = final_db.get("facts", {})
        self.final_events = final_db.get("events", [])
        self.final = final_db.get("final_action")

    def change_for(self, fact: str) -> dict | None:
        return next((c for c in self.expected_changes if c["fact"] == fact), None)

    def claimed(self, value: Any, from_turn: int | None = None) -> tuple | None:
        return next((c for c in self.claims if close(value, c[2]) and (from_turn is None or from_turn >= c[0])), None)

    def fact_right_at_end(self, fact: str) -> bool:
        got, want = self.final_facts.get(fact, {}), self.expected_facts.get(fact, {})
        return close(got.get("value"), want.get("value")) and got.get("source_doc") == want.get("source_doc")


def _decision(ctx: _Context, trace: dict) -> list[dict]:
    stop = trace.get("stop_reason")
    idx = [e["i"] for e in ctx.events if e["type"] == "final_action" and e.get("accepted")]
    if stop == "decided_too_early" and ctx.final:
        return [_label("decided_too_early", "decision", ctx.final["turn"], DECISION_SEVERITY["decided_too_early"],
                       idx, f"{ctx.final['action']} before the final-decision request")]
    if stop == "final_action" and ctx.final:
        cls = ctx.gold["action_classes"][ctx.final["action"]]
        note = f"{ctx.final['action']}; acceptable: {', '.join(ctx.gold['acceptable_actions'])}"
        return [_label(cls, "decision", ctx.final["turn"], DECISION_SEVERITY[cls], idx, note)]
    if stop == "evidence_requested":
        # Asked for proof in reply to the final-decision request (RNW-1 P4/P7); the gold says whether
        # that was right here (valid in 5b) or an unnecessary block.
        turn = next((e["turn"] for e in reversed(ctx.events) if e["type"] == "stop"), None)
        asks = [e["i"] for e in ctx.events if e["type"] == "evidence_request" and e["turn"] == turn]
        cls = ctx.gold["action_classes"]["request_evidence"]
        note = f"request_evidence in reply to the final-decision request; acceptable: {', '.join(ctx.gold['acceptable_actions'])}"
        return [_label(cls, "decision", turn, DECISION_SEVERITY[cls], asks, note)]
    return [_label("no_decision", "decision", None, DECISION_SEVERITY["no_decision"], [],
                   f"run stopped: {stop}")]


def _record(ctx: _Context) -> tuple[list[dict], int, int]:
    labels, attempted, accepted_invalid = [], 0, 0
    for e in ctx.events:
        if e["type"] == "record_write":
            fact, value, ok = e.get("fact"), e.get("value"), bool(e.get("accepted"))
            exp = ctx.change_for(fact) if isinstance(fact, str) else None
            note = f"{fact} -> {value} citing {e.get('evidence_doc')} ({e.get('change_type')})"
            if exp and close(value, exp["new_value"]) and e.get("evidence_doc") == exp["evidence_doc"]:
                if e.get("change_type") == exp["change_type"]:
                    if ok:
                        labels.append(_label("correct_update", "record", e["turn"], 0, [e["i"]], note, accepted=True))
                    continue
                kind, sev = "wrong_change_type", 1
                note += f"; expected {exp['change_type']}"
            elif exp and close(value, exp["new_value"]):
                kind, sev = "wrong_source", 2
                note += f"; expected evidence {exp['evidence_doc']}"
            elif (not exp and isinstance(fact, str) and fact in ctx.expected_facts
                  and close(value, ctx.expected_facts[fact]["value"])
                  and e.get("evidence_doc") == ctx.expected_facts[fact]["source_doc"]):
                if ok:  # writing the right value back from its own source undoes an earlier wrong write
                    labels.append(_label("restored", "record", e["turn"], 0, [e["i"]], note, accepted=True))
                continue
            elif ctx.claimed(value, e["turn"]):
                kind, sev = "believed_claim", 2
            else:
                kind, sev = "wrong_update", 2
            attempted += 1
            accepted_invalid += ok
            recovered = ok and isinstance(fact, str) and ctx.fact_right_at_end(fact)
            labels.append(_label(kind, "record", e["turn"], sev, [e["i"]], note if ok else note + "; rejected by the harness",
                                 accepted=ok, recovered=recovered))
            if ok and kind == "believed_claim":
                labels.append(_label("accepted_no_proof", "evidence", e["turn"], 2, [e["i"]],
                                     f"wrote the claimed figure {value} with no document that supports it",
                                     accepted=True, recovered=recovered))
        elif e["type"] == "event_log":
            ok, doc = bool(e.get("accepted")), e.get("evidence_doc")
            if any(x["evidence_doc"] == doc for x in ctx.expected_events):
                if ok:
                    labels.append(_label("correct_event_log", "record", e["turn"], 0, [e["i"]], f"citing {doc}",
                                         accepted=True))
                continue
            attempted += 1
            accepted_invalid += ok
            labels.append(_label("event_wrongly_logged", "record", e["turn"], 1, [e["i"]],
                                 f"{e.get('event')!r} citing {doc}", accepted=ok,
                                 recovered=False))
    # final consequences (only for runs that played the whole script)
    if ctx.truncated:
        return labels, attempted, accepted_invalid
    for fact, want in ctx.expected_facts.items():
        got = ctx.final_facts.get(fact, {})
        if not close(got.get("value"), want.get("value")):
            kind = "missed_update" if ctx.change_for(fact) else "record_wrong_at_end"
            labels.append(_label(kind, "record", None, 2, [],
                                 f"{fact} is {got.get('value')} at the end; expected {want.get('value')}"))
        elif got.get("source_doc") != want.get("source_doc"):
            labels.append(_label("missing_source", "record", None, 1, [],
                                 f"{fact} cites {got.get('source_doc')}; expected {want.get('source_doc')}"))
    logged = {x.get("evidence_doc") for x in ctx.final_events}
    for x in ctx.expected_events:
        if x["evidence_doc"] not in logged:
            labels.append(_label("event_not_logged", "record", None, 2, [], f"new event in {x['evidence_doc']}"))
    return labels, attempted, accepted_invalid


def _math(ctx: _Context) -> list[dict]:
    labels = []
    calcs = {e["id"]: e for e in ctx.events if e["type"] == "calculation" and e.get("accepted")}
    restated = [c for c in ctx.expected_changes if c["change_type"] == "restatement" and c["old_value"] is not None]
    for c in calcs.values():
        values = list(c.get("facts_used", {}).values()) + list(c.get("numbers", []))
        for r in restated:
            given = ctx.doc_turn.get(r["evidence_doc"])
            if given is not None and c["turn"] >= given and any(close(v, r["old_value"]) for v in values):
                labels.append(_label("used_stale_figure", "math", c["turn"], 2, [c["i"]],
                                     f"{c['id']} used {r['fact']} = {r['old_value']} after {r['evidence_doc']} corrected it"))
        for v in values:
            claim = ctx.claimed(v, c["turn"])
            if claim and not (claim[1] == "hypothetical" and c["turn"] == claim[0]):
                labels.append(_label("used_claimed_figure", "math", c["turn"], 2, [c["i"]],
                                     f"{c['id']} used the {claim[1].replace('_', ' ')} figure {v}"))
                break
    recs = [e for e in ctx.events if e["type"] == "recommendation" and e.get("accepted")]
    tool_calls = {e["id"]: e for e in ctx.events if e["type"] == "tool"}
    for r in recs:
        calc = calcs.get(r.get("calculation_id"))
        if calc is None:
            continue
        # Stale means a figure the calculation used has changed since, not that the record version moved
        # (logging an event bumps the version without changing any figure). Compare with the record as it
        # was when the recommendation was made.
        record = (tool_calls.get(r.get("tool_call_id")) or {}).get("db_before", {}).get("facts", {})
        changed = [(k, v, record[k]["value"]) for k, v in calc.get("facts_used", {}).items()
                   if k in record and not close(record[k]["value"], v)]
        if changed:
            what = "; ".join(f"{k} = {old} there, {new} in the record now" for k, old, new in changed)
            labels.append(_label("stale_calculation_cited", "math", r["turn"], 1, [r["i"], calc["i"]],
                                 f"{r['id']} rests on {calc['id']}, which used {what}"))
    final_ev = next((e for e in ctx.events if e["type"] == "final_action" and e.get("accepted")), None)
    if final_ev is not None:
        tool = next((e for e in reversed(ctx.events[:final_ev["i"]]) if e["type"] == "tool"), None)
        facts = (tool or {}).get("db_before", {}).get("facts", {})
        wrong = [k for k, want in ctx.expected_facts.items() if not close(facts.get(k, {}).get("value"), want["value"])]
        if wrong and not ctx.truncated:
            labels.append(_label("decided_on_wrong_record", "math", final_ev["turn"], 2, [final_ev["i"]],
                                 f"at the final action the record still differed on {', '.join(wrong)}"))
        if recs and recs[-1]["action"] != final_ev["action"]:
            labels.append(_label("final_differs_from_last_recommendation", "math", final_ev["turn"], 0, [final_ev["i"]],
                                 f"last recommendation {recs[-1]['action']}, final {final_ev['action']}"))
    return labels


def _evidence(ctx: _Context) -> list[dict]:
    labels = []
    for e in ctx.events:
        if e["type"] != "evidence_request":
            continue
        turn = ctx.by_turn.get(e["turn"], {})
        classes = (turn.get("checkpoint") or {}).get("action_classes") or {}
        if not classes and turn.get("kind") == "final_decision_request":
            classes = ctx.gold["action_classes"]       # the final turn has no checkpoint: the version's gold applies
        needed = turn.get("kind") == "unsupported_claim" or classes.get("request_evidence") == "valid"
        labels.append(_label("asked_proof_needed" if needed else "asked_proof_not_needed", "evidence", e["turn"],
                             0 if needed else 1, [e["i"]], e.get("what", "")))
    return labels


def _process(ctx: _Context, trace: dict) -> list[dict]:
    labels = []
    rejected = [e["i"] for e in ctx.events if e["type"] in ("tool_call", "record_write", "event_log", "calculation",
                                                             "recommendation", "final_action") and e.get("accepted") is False]
    refused = [e["i"] for e in ctx.events if e["type"] == "tool" and not e.get("executed", True)]
    if rejected or refused:
        labels.append(_label("tool_misuse", "process", None, 1, rejected + refused,
                             f"{len(rejected)} rejected, {len(refused)} not executed"))
    stop = trace.get("stop_reason")
    if stop in LIMIT_STOPS:
        labels.append(_label("limit_hit", "process", None, 2, [], stop))
    if stop == "repeated_errors":
        labels.append(_label("repeated_errors", "process", None, 2, [], "three rejected calls in a row"))
    return labels


def grade(trace: dict, family: dict) -> dict:
    """All labels for one run, plus the separate counts and the overall pass."""
    ctx = _Context(trace, family)
    decision = _decision(ctx, trace)
    record, attempted, accepted_invalid = _record(ctx)
    labels = decision + record + _math(ctx) + _evidence(ctx) + _process(ctx, trace)
    final_failures = sorted({l["label"] for l in labels if l["label"] in FINAL_FAILURES})
    return {
        "run_id": trace.get("run_id"), "family_id": family["family_id"], "version": trace["case_version"],
        "version_type": ctx.version["version_type"], "mode": trace["mode"],
        "decision": decision[0]["label"], "passed": decision[0]["label"] == "valid" and not final_failures,
        "attempted_invalid_changes": attempted, "accepted_invalid_changes": accepted_invalid,
        "final_record_failures": final_failures, "max_severity": max(l["severity"] for l in labels),
        "labels": labels, "needs_human_review": list(HUMAN_REVIEW),
        "note": "run stopped early; the final record is not graded" if ctx.truncated else "",
    }


def _family_for(trace: dict, root: Path | None = None) -> dict:
    path = Path(trace["case_file"])
    if not path.is_absolute() and root is not None and not path.exists():
        path = root / path
    return load_family(path)


def check(trace: dict) -> dict:
    """The runner's checker: the pass plus scalar columns for runs.csv."""
    g = grade(trace, _family_for(trace))
    return {"passed": g["passed"], "checker": "grader_v0", "decision": g["decision"],
            "attempted_invalid": g["attempted_invalid_changes"], "accepted_invalid": g["accepted_invalid_changes"],
            "final_record_failures": ";".join(g["final_record_failures"]), "max_severity": g["max_severity"],
            "labels": ";".join(sorted({l["label"] for l in g["labels"] if l["severity"] > 0}))}


def grade_experiment(out_dir: str | Path, root: Path | None = None) -> list[dict]:
    """Write ``labels/<run_id>.json`` and ``labels.csv`` (one row per label) for every completed run."""
    out_dir = Path(out_dir)
    (out_dir / "labels").mkdir(parents=True, exist_ok=True)
    rows, grades = [], []
    for path in sorted((out_dir / "traces").glob("*.json")):
        trace = load_trace(path)
        if trace.get("status") != "completed":
            continue
        g = grade(trace, _family_for(trace, root))
        g["model"] = (trace.get("model") or {}).get("name")
        grades.append(g)
        (out_dir / "labels" / path.name).write_text(json.dumps(g, indent=2), encoding="utf-8")
        for l in g["labels"]:
            rows.append({"run_id": g["run_id"], "model": g["model"], "family": g["family_id"], "version": g["version"],
                         "version_type": g["version_type"], "mode": g["mode"],
                         **{k: (json.dumps(v) if isinstance(v, list) else v) for k, v in l.items()}})
    columns = ["run_id", "model", "family", "version", "version_type", "mode", "label", "group", "turn", "severity",
               "accepted", "recovered", "evidence", "note"]
    with open(out_dir / "labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return grades
