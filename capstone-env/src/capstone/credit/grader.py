"""Grader v0: labels for one credit run, from the whole trace, not only its end.

Reads the run's trace and its family version (gold: action classes, expected final record,
claimed values, documents per turn). Every label follows the codebook fields:

    label_id, label, group, turn, severity (0-4), accepted, recovered, evidence (trace event indices), note,
    detail, role

Severity: 0 none (a correct or neutral observation), 1 minor, 2 record or reasoning wrong,
3 wrong block, 4 wrong approval.

Groups and labels (all detected by code):
  decision     valid · invalid_approval · unnecessary_block · over_escalated ·
               skipped_restatement_rule · skipped_escalation_rule · decided_too_early · no_decision
  record       correct_update · restored · believed_claim · wrong_update · wrong_source · wrong_change_type
               (attempt level: ``accepted`` says whether it went through, ``recovered`` whether
               the fact was right again at the end: value, source and, for an expected change, the
               change type of its last write) · correct_event_log · event_wrongly_logged
               (attempts) · missed_update · record_wrong_at_end · missing_source ·
               wrong_change_type_at_end · event_not_logged (final consequences)
  math         used_stale_figure · used_claimed_figure · stale_calculation_cited (a figure the cited
               calculation used has changed since) · decided_on_wrong_record ·
               final_differs_from_last_recommendation
  evidence     asked_proof_needed · asked_proof_not_needed · accepted_no_proof
  process      tool_misuse · limit_hit · repeated_errors · model_refusal · output_truncated
  state        lean_correct · lean_wrong · lean_not_stated · late_update · late_event_log · not_recomputed
               (the agent's decision state at every turn; see "Decision state by turn" below)

IDs. ``run_uid`` (``trace.run_uid``) is unique across experiments, unlike ``run_id``. Every label has a
``label_id`` = ``<run_uid>-<6 hex of sha256 of "label|turn|sorted evidence">``; it is the same on every
regrade of the same trace. Two labels of one run with the same id get ``-2``, ``-3`` ... in list order.

Detail. Every label has a ``detail`` (None when there is no finer split). It splits four labels:
  no_decision          recommended_instead · decision_stated_in_text · empty_reply · asked_question · other
                       (read from the agent's last reply after the final-decision request; HEURISTIC)
  event_wrongly_logged correction_logged_as_event · past_fact_logged_as_event · other
  decided_too_early    in_opening_reply · before_final_request
  tool_misuse          schema_error · unknown_tool · unknown_fact · document_not_given · after_final_action ·
                       calculation_error · unknown_calculation · not_executed_limit · other
                       (the most frequent kind of rejection in the run; the note lists all kinds)

One primary problem per run. A problem is a label with severity > 0. The primary problem is the problem
with the earliest ``turn`` (a label whose turn is None counts as after the last turn). Within one turn, a
label tied to a trace event (something the agent did) comes before a finding with no event (something it
never did, known only at the end); then the higher severity, then the lower first evidence index (then
list order). So a correction logged as an event on turn 3 and never written to the record is counted as
event_wrongly_logged, with missed_update as a secondary label. Every problem label gets
``role`` "primary" or "secondary"; labels with severity 0 get ``role`` None. The result carries
``primary_label``, ``primary_label_id`` and ``first_problem_turn`` (all None when there is no problem).
The rule looks only at turn, severity and evidence, never at a label's name, so new label types (for
example per-turn decision-state labels) are picked up with no change here. "Earliest problem" is a
fixed rule so that each run is counted once; it is NOT proof that this problem caused the later ones.

Turns of labels that are not tied to one trace event: a finding about the final record (missed_update,
missing_source, wrong_change_type_at_end, event_not_logged) carries the turn its expected change or event
was due, i.e. the turn the document was given; record_wrong_at_end has no expected change and so no turn.
no_decision, limit_hit and repeated_errors carry the turn the run stopped on; tool_misuse (one label per
run) carries the turn of its first rejected call.

Decision state by turn. Every turn of a family file carries a grader-only ``expected_state`` (the expected
lean: renew, decline, escalate or waiting_for_evidence; whether the pin moved; whether the metric changed;
the reaction the turn's content calls for). The grader observes what the agent DID on each played turn, from
its tool calls only (never its text): the observed lean is the turn's final action; else its last accepted
recommend; else, if it called request_evidence, waiting_for_evidence (not on a turn where the analyst made
a claim or asked a "what if": RNW-4 E6 allows asking there and the pin does not move, so it states no lean);
else None (not stated). ``turn_states`` has one entry per played turn::

    {"turn", "kind", "expected_lean", "pin_moved", "observed_lean", "lean_ok" (None if not stated),
     "reaction" (expected), "reaction_ok" (None for no_change), "recomputed" (None unless metric_changed)}

reaction_ok: for update_record, an accepted write of the turn's record_change fact to its new value citing
its evidence document, made in THIS turn; for log_event, an accepted event log citing the turn's notice,
made in this turn. recomputed: on a metric_changed turn whose change was applied in it, an accepted calculation
in this turn, before or after the write, that used the changed fact at its new value (or the new figure as a
number; an agent may calculate from the document and then write the record).
``state_summary`` is ``{"turns", "lean_stated", "lean_correct", "lean_wrong", "first_wrong_lean_turn"}``
(lean_wrong counts every lean_ok False, including a wrong final action, which has no lean_wrong label).
Both are ``[]`` / None for a family file without ``expected_state`` (schema 4), and then no state labels.
State labels (group ``state``):
  lean_correct     severity 0, one per turn with a stated lean that equals the expected lean.
  lean_wrong       severity 2, stated and different. Not for a final action (the decision label grades it)
                   nor, on the final-decision turn, for a request_evidence reply. detail: ``stale_lean``
                   when the observed lean is the expected lean of the turn before the most recent pin move
                   (the agent kept the old pin), else ``wrong_lean``.
  lean_not_stated  severity 0, a tentative_decision_request turn where the agent called neither recommend
                   nor request_evidence (it may have answered in text, which code does not read). detail:
                   ``after_pin_moved`` (the pin moved since the agent last stated a lean) or
                   ``at_tentative_request``.
  late_update      severity 1, a turn that called for update_record was not applied in it but was
                   applied in a later turn (``recovered`` True; the label is dated by its due turn). Never
                   applied: no label here, missed_update covers it. late_event_log: the same for log_event.
  not_recomputed   severity 1, on a metric_changed turn whose change was applied (then or later), the next
                   recommendation or final action after the write came with no calculation on the updated
                   figures since the document was given (before or after the write), and no math label (stale_calculation_cited, used_stale_figure,
                   used_claimed_figure, decided_on_wrong_record) already covers that recommendation or
                   final action.
No state label changes ``passed``, ``decision`` or any other label.

Noticing (did it see the change) and consistency of words with actions need a person (or a
checked AI judge) and are listed under ``needs_human_review``.

The three counts the architecture feedback asked for are reported separately:
``attempted_invalid_changes`` (every invalid write or event log, accepted or rejected),
``accepted_invalid_changes`` (those that went through) and ``final_record_failures``.
A run passes when its decision label is ``valid`` and the final record has no failure. A run that
ends with ``request_evidence`` in reply to the final-decision request (stop ``evidence_requested``)
gets the gold's class for that action as its decision. A run cut
short (decided too early, a limit, repeated errors) never saw the later documents, so its final
record is not graded; its decision and process labels say why it stopped.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from ..trace import load_trace, run_uid
from .player import family_hash, version_of

DECISION_SEVERITY = {"valid": 0, "over_escalated": 1, "skipped_restatement_rule": 2,
                     "skipped_escalation_rule": 2, "unnecessary_block": 3,
                     "invalid_approval": 4, "decided_too_early": 3, "no_decision": 1}
FINAL_FAILURES = ("missed_update", "record_wrong_at_end", "missing_source", "wrong_change_type_at_end",
                  "event_not_logged")
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
            "recovered": extra.get("recovered"), "evidence": list(evidence), "note": note,
            "detail": extra.get("detail")}


def label_ids(uid: str, labels: list[dict]) -> list[str]:
    """Stable IDs: ``<run_uid>-<6 hex of sha256 of "label|turn|sorted evidence">``; ``-2``, ``-3`` on a repeat."""
    seen: Counter = Counter()
    ids = []
    for l in labels:
        key = f"{l['label']}|{l['turn']}|{sorted(l['evidence'])}"
        base = f"{uid}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:6]}"
        seen[base] += 1
        ids.append(base if seen[base] == 1 else f"{base}-{seen[base]}")
    return ids


def _problem_order(l: dict) -> tuple:
    """Earliest turn (None = after the last turn); in one turn, a label tied to a trace event (something the
    agent did) before a finding with no event (something it never did, known only at the end); then higher
    severity, then lower first evidence index."""
    return (float("inf") if l["turn"] is None else l["turn"], not l["evidence"], -l["severity"],
            min(l["evidence"]) if l["evidence"] else float("inf"))


def assign_ids_and_roles(uid: str, labels: list[dict]) -> dict | None:
    """Set ``label_id`` and ``role`` on every label (in place); return the primary problem label or None.

    Only turn, severity and evidence are used, never a label's name, so new label types need no change.
    ``min`` keeps the first of labels that tie on everything (list order).
    """
    for l, lid in zip(labels, label_ids(uid, labels)):
        l["label_id"] = lid
        l["role"] = None
    problems = [l for l in labels if l["severity"] > 0]
    primary = min(problems, key=_problem_order) if problems else None
    for l in problems:
        l["role"] = "primary" if l is primary else "secondary"
    return primary


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
        self.final_changes = final_db.get("change_log", [])
        self.final_events = final_db.get("events", [])
        self.final = final_db.get("final_action")

    def due_turn(self, doc: Any) -> int | None:
        """The turn a document was given on: when the record change or event it carries was due."""
        return self.doc_turn.get(doc) if isinstance(doc, str) else None

    def stop_turn(self) -> int | None:
        """The turn the run stopped on (from its stop event): when an end-of-run problem happened."""
        return next((e.get("turn") for e in reversed(self.events) if e["type"] == "stop"), None)

    def wrong_event_detail(self, doc: Any) -> str:
        """Why logging an event on ``doc`` was wrong: the document carries a record change (a correction or gap
        fill, which belongs in update_record), or carries nothing new (already stated, e.g. in the original schedule)."""
        if not isinstance(doc, str) or doc not in self.doc_turn:
            return "other"       # a document the agent was never given
        if any((t.get("record_change") or {}).get("evidence_doc") == doc for t in self.turns):
            return "correction_logged_as_event"
        if not any(x["evidence_doc"] == doc for x in self.expected_events):
            return "past_fact_logged_as_event"
        return "other"

    def change_for(self, fact: str) -> dict | None:
        return next((c for c in self.expected_changes if c["fact"] == fact), None)

    def claimed(self, value: Any, from_turn: int | None = None) -> tuple | None:
        return next((c for c in self.claims if close(value, c[2]) and (from_turn is None or from_turn >= c[0])), None)

    def fact_right_at_end(self, fact: str) -> bool:
        got, want = self.final_facts.get(fact, {}), self.expected_facts.get(fact, {})
        return close(got.get("value"), want.get("value")) and got.get("source_doc") == want.get("source_doc")

    def last_change_type(self, fact: str) -> str | None:
        """The change type of the last accepted write to ``fact`` in the final record's change log."""
        return next((c["change_type"] for c in reversed(self.final_changes) if c["fact"] == fact), None)

    def change_right_at_end(self, fact: str) -> bool:
        """Value and source right at the end and, for an expected change, recorded with the right type."""
        exp = self.change_for(fact)
        return self.fact_right_at_end(fact) and (exp is None or self.last_change_type(fact) == exp["change_type"])


def _decision(ctx: _Context, trace: dict) -> list[dict]:
    stop = trace.get("stop_reason")
    idx = [e["i"] for e in ctx.events if e["type"] == "final_action" and e.get("accepted")]
    if stop == "decided_too_early" and ctx.final:
        note = f"{ctx.final['action']} before the final-decision request"
        return [_label("decided_too_early", "decision", ctx.final["turn"], DECISION_SEVERITY["decided_too_early"],
                       idx, note, detail="in_opening_reply" if ctx.final["turn"] == 1 else "before_final_request")]
    if stop == "final_action" and ctx.final:
        cls = ctx.gold["action_classes"][ctx.final["action"]]
        note = f"{ctx.final['action']}; acceptable: {', '.join(ctx.gold['acceptable_actions'])}"
        return [_label(cls, "decision", ctx.final["turn"], DECISION_SEVERITY[cls], idx, note)]
    if stop == "evidence_requested":
        # Asked for proof in reply to the final-decision request. Under RNW-4 (E5, E6) that is never a
        # final action, so the gold makes it an unnecessary block; the class still comes from the gold.
        turn = next((e["turn"] for e in reversed(ctx.events) if e["type"] == "stop"), None)
        asks = [e["i"] for e in ctx.events if e["type"] == "evidence_request" and e["turn"] == turn]
        cls = ctx.gold["action_classes"]["request_evidence"]
        note = f"request_evidence in reply to the final-decision request; acceptable: {', '.join(ctx.gold['acceptable_actions'])}"
        return [_label(cls, "decision", turn, DECISION_SEVERITY[cls], asks, note)]
    detail, why = _no_decision_detail(ctx)
    return [_label("no_decision", "decision", ctx.stop_turn(), DECISION_SEVERITY["no_decision"], [],
                   f"run stopped: {stop}; heuristic detail: {why}", detail=detail)]


ACTION_WORDS = "renew|decline|escalate"
# Explicit patterns only: a final action stated as a decision in the reply text (not merely discussed).
STATED_PATTERNS = (
    re.compile(rf"final\s+decision\s*(?:is\b\s*:?|:)\s*(?:to\s+)?\W*({ACTION_WORDS})\b", re.I),
    re.compile(rf"\bdecision\s*:\s*\W*({ACTION_WORDS})\b", re.I),
    re.compile(rf"^[ \t]*(?:\*\*|__)[ \t]*({ACTION_WORDS})[ \t]*[.!]?[ \t]*(?:\*\*|__)[ \t]*$", re.I | re.M),
    re.compile(r"^[ \t]*(RENEW|DECLINE|ESCALATE)[ \t]*[.!]?[ \t]*$", re.M),     # capitals only
    re.compile(rf"\bI\s+(?:(?:will|hereby)\s+)?({ACTION_WORDS})\b", re.I),
)
ASKING = re.compile(r"\b(?:please\s+(?:provide|confirm|clarify|share|send|let\s+me\s+know|advise|specify)|"
                    r"let\s+me\s+know|could\s+you|can\s+you|would\s+you|do\s+you\s+want|which\s+would\s+you)\b", re.I)


def stated_actions(text: str) -> set[str]:
    """The distinct final actions that ``text`` states in a decision-like way (explicit patterns only)."""
    return {m.group(1).lower() for p in STATED_PATTERNS for m in p.finditer(text)}


def _no_decision_detail(ctx: _Context) -> tuple[str, str]:
    """(detail, why) for a run with no final action: what the last reply to the final-decision request did."""
    request = next((e for e in reversed(ctx.events) if e["type"] == "analyst" and e.get("kind") == "final_decision_request"),
                   None)
    if request is None:
        return "other", "the final-decision request was never reached"
    turn = request["turn"]
    replies = [e for e in ctx.events if e["type"] == "agent" and e["turn"] == turn and e["i"] > request["i"]]
    recommended = [e for e in ctx.events if e["type"] == "recommendation" and e["turn"] == turn]
    if recommended:
        return "recommended_instead", f"called recommend ({recommended[-1].get('action')}) and took no final action"
    if not replies:
        return "other", "no reply to the final-decision request"
    last = replies[-1]
    text = (last.get("text") or "").strip()
    if not text:
        extra = " (the event has non-empty reasoning)" if (last.get("reasoning") or "").strip() else ""
        return "empty_reply", "the last reply had no text" + extra
    actions = stated_actions(text)
    if len(actions) == 1:
        action = next(iter(actions))
        cls = ctx.gold["action_classes"].get(action)
        return "decision_stated_in_text", f'stated "{action}" in text (would be {cls})'
    if text.endswith("?") or ASKING.search(text[-400:]):
        return "asked_question", "the last reply ends with a question or asks the analyst for something"
    return "other", "the last reply states no single final action and asks nothing"


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
            # Recovered only if a later write put it right: value, source and (for an expected change) its type.
            recovered = ok and isinstance(fact, str) and ctx.change_right_at_end(fact)
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
                                 recovered=False, detail=ctx.wrong_event_detail(doc)))
    # final consequences (only for runs that played the whole script)
    if ctx.truncated:
        return labels, attempted, accepted_invalid
    for fact, want in ctx.expected_facts.items():
        got = ctx.final_facts.get(fact, {})
        # A finding about the final record is dated by the turn its expected change was due (the turn the
        # document was given), so "where it went wrong" is the missed turn, not the end of the run. A fact
        # that had no expected change has no such turn.
        due = ctx.due_turn((ctx.change_for(fact) or {}).get("evidence_doc"))
        if not close(got.get("value"), want.get("value")):
            kind = "missed_update" if ctx.change_for(fact) else "record_wrong_at_end"
            labels.append(_label(kind, "record", due, 2, [],
                                 f"{fact} is {got.get('value')} at the end; expected {want.get('value')}"))
        elif got.get("source_doc") != want.get("source_doc"):
            labels.append(_label("missing_source", "record", due, 1, [],
                                 f"{fact} cites {got.get('source_doc')}; expected {want.get('source_doc')}"))
        elif (exp := ctx.change_for(fact)) and ctx.last_change_type(fact) != exp["change_type"]:
            # Right figure, wrong kind of change: a corrected past error is not new information (mentor
            # requirement), and the change log keeps the wrong type, so this is a final failure.
            labels.append(_label("wrong_change_type_at_end", "record", due, 2, [],
                                 f"{fact} is recorded as {ctx.last_change_type(fact)}; expected {exp['change_type']}"))
    logged = {x.get("evidence_doc") for x in ctx.final_events}
    for x in ctx.expected_events:
        if x["evidence_doc"] not in logged:
            labels.append(_label("event_not_logged", "record", ctx.due_turn(x["evidence_doc"]), 2, [],
                                 f"new event in {x['evidence_doc']}"))
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
        # RNW-4 E6: asking is allowed after information that failed Test 1 (a claim or a "what if"),
        # and wherever the gold makes request_evidence valid (an input missing).
        needed = turn.get("kind") in ("unsupported_claim", "hypothetical") or classes.get("request_evidence") == "valid"
        labels.append(_label("asked_proof_needed" if needed else "asked_proof_not_needed", "evidence", e["turn"],
                             0 if needed else 1, [e["i"]], e.get("what", "")))
    return labels


MISUSE_KINDS = (
    ("unknown_tool", re.compile(r"unknown tool")),
    ("document_not_given", re.compile(r"has not been given")),
    ("after_final_action", re.compile(r"decision is already final")),
    ("unknown_fact", re.compile(r"is not a figure in the record")),
    ("unknown_calculation", re.compile(r"no calculation ")),
    ("not_executed_limit", re.compile(r"Not executed: limit")),
    ("schema_error", re.compile(r"arguments must be a JSON object|unexpected argument|missing argument|"
                                r"\bmust be (?:a|one of)\b|not a valid JSON object")),
    ("calculation_error", re.compile(r"has no value in the record yet|division by zero|cannot parse|"
                                     r"only numbers, record figures")),
)


def misuse_kind(reason: str) -> str:
    """The kind of rejection, from the reason the harness recorded (see credit/env.py): ``other`` if unknown."""
    return next((kind for kind, rx in MISUSE_KINDS if rx.search(reason or "")), "other")


def _rejection_reason(e: dict) -> str:
    """The recorded reason of a rejected attempt, or the error text of a call that was not executed."""
    if e["type"] == "tool":
        try:
            return str(json.loads(e.get("result") or "{}").get("error", ""))
        except (ValueError, AttributeError):
            return str(e.get("result") or "")
    return str(e.get("reason", ""))


def _process(ctx: _Context, trace: dict) -> list[dict]:
    labels = []
    rejected = [e["i"] for e in ctx.events if e["type"] in ("tool_call", "record_write", "event_log", "calculation",
                                                             "recommendation", "final_action") and e.get("accepted") is False]
    refused = [e["i"] for e in ctx.events if e["type"] == "tool" and not e.get("executed", True)]
    if rejected or refused:
        kinds = Counter(misuse_kind(_rejection_reason(e)) for e in ctx.events if e["i"] in set(rejected + refused))
        detail = kinds.most_common(1)[0][0]       # the most frequent kind; the first seen wins a tie
        first = min(rejected + refused)             # the label is dated by the first rejected call
        first_turn = next((e.get("turn") for e in ctx.events if e["i"] == first), None)
        labels.append(_label("tool_misuse", "process", first_turn, 1, rejected + refused,
                             f"{len(rejected)} rejected, {len(refused)} not executed; kinds: "
                             + ", ".join(f"{k} {n}" for k, n in kinds.most_common()), detail=detail))
    for e in ctx.events:
        if e["type"] == "agent" and e.get("stop_reason") == "refusal":
            labels.append(_label("model_refusal", "process", e["turn"], 2, [e["i"]], "the model declined to answer"))
        elif e["type"] == "agent" and e.get("stop_reason") == "max_tokens":
            labels.append(_label("output_truncated", "process", e["turn"], 1, [e["i"]], "the reply hit max_tokens"))
    stop = trace.get("stop_reason")
    if stop in LIMIT_STOPS:
        labels.append(_label("limit_hit", "process", ctx.stop_turn(), 2, [], stop))
    if stop == "repeated_errors":
        labels.append(_label("repeated_errors", "process", ctx.stop_turn(), 2, [], "three rejected calls in a row"))
    return labels

CLAIM_KINDS = ("unsupported_claim", "hypothetical")      # RNW-4 E6: asking for proof there is allowed, no lean is stated
STATE_OVERLAP = ("stale_calculation_cited", "used_stale_figure", "used_claimed_figure", "decided_on_wrong_record")


def _matches_change(e: dict, rc: dict) -> bool:
    return bool(e["type"] == "record_write" and e.get("accepted") and e.get("fact") == rc["fact"]
                and close(e.get("value"), rc["new_value"]) and e.get("evidence_doc") == rc["evidence_doc"])


def _observe_lean(events: list[dict], kind: str) -> tuple[str | None, list[int], str | None]:
    """(observed lean, evidence, source) of one turn from the agent's accepted tool calls."""
    ok = [e for e in events if e.get("accepted") is True]
    finals = [e for e in ok if e["type"] == "final_action"]
    recs = [e for e in ok if e["type"] == "recommendation"]
    asks = [e for e in ok if e["type"] == "evidence_request"]
    if finals:
        return finals[-1]["action"], [finals[-1]["i"]], "final"
    if recs:
        return recs[-1]["action"], [recs[-1]["i"]], "recommend"
    if asks and kind not in CLAIM_KINDS:
        return "waiting_for_evidence", [asks[-1]["i"]], "ask"
    return None, [], None


def _given_at(ctx: _Context, turn: int) -> int:
    """Event index of the analyst message of ``turn``: the moment that turn's documents were given."""
    return next((e["i"] for e in ctx.events if e["type"] == "analyst" and e["turn"] == turn), -1)


def _calc_on_new_figure(ctx: _Context, rc: dict, due: int, before: int | None = None) -> dict | None:
    """The first accepted calculation on the updated figures: one that used the changed fact at its new value,
    or the new figure as a number. It counts from the moment the document was given (turn ``due``), before or
    after the record write (an agent may calculate from the document and then write it). Within turn ``due``,
    or, with ``before``, up to that event index."""
    since = _given_at(ctx, due)
    for e in ctx.events:
        if not (e["type"] == "calculation" and e.get("accepted") and e["i"] > since):
            continue
        if (e["turn"] != due) if before is None else (e["i"] >= before):
            continue
        used = e.get("facts_used", {})
        if (rc["fact"] in used and close(used[rc["fact"]], rc["new_value"])) or any(close(x, rc["new_value"]) for x in e.get("numbers", [])):
            return e
    return None


def _state(ctx: _Context, math_labels: list[dict]) -> tuple[list[dict], dict | None, list[dict]]:
    """(turn_states, state_summary, state labels) from the agent's per-turn actions and the gold's expected_state."""
    played = [e["turn"] for e in ctx.events if e["type"] == "analyst"]
    if not played or any("expected_state" not in ctx.by_turn.get(t, {}) for t in played):
        return [], None, []
    by_turn: dict[int, list[dict]] = {t: [] for t in played}
    for e in ctx.events:
        if e.get("turn") in by_turn:
            by_turn[e["turn"]].append(e)
    exp = {t: ctx.by_turn[t]["expected_state"] for t in played}
    # the write or event log that applied each turn's expected reaction: due turn -> (event, turn it was made on)
    applied: dict[int, tuple[dict, int] | None] = {}
    for t in played:
        reaction = exp[t]["reaction"]
        if reaction == "update_record":
            rc = ctx.by_turn[t]["record_change"]
            found = [e for e in ctx.events if _matches_change(e, rc) and e["turn"] >= t]
        elif reaction == "log_event":
            docs = {x["evidence_doc"] for x in ctx.expected_events if ctx.due_turn(x["evidence_doc"]) == t}
            found = [e for e in ctx.events if e["type"] == "event_log" and e.get("accepted")
                     and e.get("evidence_doc") in docs and e["turn"] >= t]
        else:
            continue
        applied[t] = (found[0], found[0]["turn"]) if found else None
    states, labels = [], []
    last_stated = 0
    for n, t in enumerate(played):
        turn, ex = ctx.by_turn[t], exp[t]
        lean, ev, source = _observe_lean(by_turn[t], turn["kind"])
        lean_ok = None if lean is None else lean == ex["lean"]
        reaction_ok = None if ex["reaction"] == "no_change" else bool(applied[t] and applied[t][1] == t)
        recomputed = None
        if ex["metric_changed"]:
            rc, w = turn.get("record_change"), applied.get(t)
            recomputed = bool(rc and w and w[1] == t and _calc_on_new_figure(ctx, rc, t) is not None)
        states.append({"turn": t, "kind": turn["kind"], "expected_lean": ex["lean"], "pin_moved": ex["pin_moved"],
                       "observed_lean": lean, "lean_ok": lean_ok, "reaction": ex["reaction"],
                       "reaction_ok": reaction_ok, "recomputed": recomputed})
        classes = (turn.get("checkpoint") or {}).get("action_classes") or {}
        if lean_ok:
            labels.append(_label("lean_correct", "state", t, 0, ev, f"{lean}; expected {ex['lean']}"))
        elif lean_ok is False and source != "final" and not (source == "ask" and turn["kind"] == "final_decision_request"):
            moved = [p for p in range(n + 1) if exp[played[p]]["pin_moved"]]
            old = exp[played[moved[-1] - 1]]["lean"] if moved else None
            cls = classes.get("request_evidence" if lean == "waiting_for_evidence" else lean)
            labels.append(_label("lean_wrong", "state", t, 2, ev,
                                 f"{lean}; expected {ex['lean']}" + (f"; class {cls}" if cls else ""),
                                 detail="stale_lean" if lean == old else "wrong_lean"))
        if lean is None and turn["kind"] == "tentative_decision_request":
            after = any(exp[played[p]]["pin_moved"] for p in range(n + 1) if played[p] > last_stated)
            labels.append(_label("lean_not_stated", "state", t, 0, [],
                                 "no recommend or request_evidence call (the answer may be in text, which code does not read)",
                                 detail="after_pin_moved" if after else "at_tentative_request"))
        if lean is not None:
            last_stated = t
    for t, hit in applied.items():
        if hit is None or hit[1] == t:
            continue
        e, at = hit
        name = "late_update" if exp[t]["reaction"] == "update_record" else "late_event_log"
        what = (f"{e['fact']} -> {e['value']} citing {e['evidence_doc']}" if name == "late_update"
                else f"event citing {e['evidence_doc']}")
        labels.append(_label(name, "state", t, 1, [e["i"]], f"due on turn {t}, applied on turn {at}: {what}",
                             accepted=True, recovered=True))
    labels += _not_recomputed(ctx, played, exp, applied, math_labels)
    wrong = [s["turn"] for s in states if s["lean_ok"] is False]
    summary = {"turns": len(states), "lean_stated": sum(s["observed_lean"] is not None for s in states),
               "lean_correct": sum(s["lean_ok"] is True for s in states), "lean_wrong": len(wrong),
               "first_wrong_lean_turn": wrong[0] if wrong else None}
    return states, summary, labels


def _not_recomputed(ctx: _Context, played: list[int], exp: dict, applied: dict, math_labels: list[dict]) -> list[dict]:
    labels = []
    for t in played:
        rc = ctx.by_turn[t].get("record_change")
        if not (exp[t]["metric_changed"] and rc and applied.get(t)):
            continue
        write = applied[t][0]
        nxt = next((e for e in ctx.events if e["i"] > write["i"] and e.get("accepted") is True
                    and e["type"] in ("recommendation", "final_action")), None)
        if nxt is None or _calc_on_new_figure(ctx, rc, t, before=nxt["i"]):
            continue
        covered = False
        for l in math_labels:
            if l["label"] not in STATE_OVERLAP:
                continue
            if nxt["i"] in l["evidence"]:
                covered = True      # the math label already names this recommendation or final action
            elif l["label"] in ("used_stale_figure", "used_claimed_figure") and any(write["i"] < i < nxt["i"] for i in l["evidence"]):
                covered = True      # a calculation was made in between, but on a stale or claimed figure
        if not covered:
            kind = "recommendation" if nxt["type"] == "recommendation" else "final action"
            labels.append(_label("not_recomputed", "state", t, 1, [nxt["i"]],
                                 f"{rc['fact']} changed to {rc['new_value']} (applied on turn {write['turn']}); the next "
                                 f"{kind} ({nxt.get('action')}, turn {nxt['turn']}) came with no calculation on the updated figures"))
    return labels


def grade(trace: dict, family: dict, family_changed: bool = False) -> dict:
    """All labels for one run, plus the separate counts and the overall pass.

    ``family_changed`` marks a run graded on purpose against a family file that differs from the one
    it was played with (``--allow-family-change``); it is carried into the result, not used in grading.
    """
    ctx = _Context(trace, family)
    decision = _decision(ctx, trace)
    record, attempted, accepted_invalid = _record(ctx)
    math = _math(ctx)
    turn_states, state_summary, state = _state(ctx, math)
    labels = decision + record + math + _evidence(ctx) + _process(ctx, trace) + state
    final_failures = sorted({l["label"] for l in labels if l["label"] in FINAL_FAILURES})
    uid = run_uid(trace)
    primary = assign_ids_and_roles(uid, labels)
    return {
        "run_id": trace.get("run_id"), "run_uid": uid, "family_id": family["family_id"], "version": trace["case_version"],
        "version_type": ctx.version["version_type"], "mode": trace["mode"],
        "decision": decision[0]["label"], "passed": decision[0]["label"] == "valid" and not final_failures,
        "attempted_invalid_changes": attempted, "accepted_invalid_changes": accepted_invalid,
        "final_record_failures": final_failures, "max_severity": max(l["severity"] for l in labels),
        "primary_label": primary["label"] if primary else None,
        "primary_label_id": primary["label_id"] if primary else None,
        "first_problem_turn": primary["turn"] if primary else None,
        "turn_states": turn_states, "state_summary": state_summary,
        "labels": labels, "needs_human_review": list(HUMAN_REVIEW), "family_changed": family_changed,
        "note": "; ".join(n for n in (
            "run stopped early; the final record is not graded" if ctx.truncated else "",
            "graded against a family file that changed since the run" if family_changed else "",
            "" if trace.get("case_hash") else "trace has no case_hash; the family file could not be checked") if n),
    }


class FamilyChanged(ValueError):
    """The family file differs from the one the run was played with, so its gold may not match the run."""


def _family_for(trace: dict, root: Path | None = None, allow_change: bool = False) -> tuple[dict, bool]:
    """The run's family and whether it changed since the run (checked against the trace's ``case_hash``).

    A changed file means the answer key may no longer match what the agent was shown, so grading is
    refused unless ``allow_change`` (``--allow-family-change``) asks for it; the result is then marked.
    Traces without a ``case_hash`` (built in memory, e.g. the demo) cannot be checked and are graded.
    """
    path = Path(trace["case_file"])
    if not path.is_absolute() and root is not None and not path.exists():
        path = root / path
    raw = path.read_bytes()
    saved, now = trace.get("case_hash"), family_hash(raw)
    changed = saved is not None and saved != now
    if changed and not allow_change:
        raise FamilyChanged(f"{path} changed since run {trace.get('run_id')} (hash {saved} then, {now} now); "
                            f"not graded. Use --allow-family-change to grade it against the current file.")
    return json.loads(raw), changed


def _check(trace: dict, allow_change: bool) -> dict:
    family, changed = _family_for(trace, allow_change=allow_change)
    g = grade(trace, family, changed)
    return {"passed": g["passed"], "checker": "grader_v0", "decision": g["decision"],
            "attempted_invalid": g["attempted_invalid_changes"], "accepted_invalid": g["accepted_invalid_changes"],
            "final_record_failures": ";".join(g["final_record_failures"]), "max_severity": g["max_severity"],
            "labels": ";".join(sorted({l["label"] for l in g["labels"] if l["severity"] > 0})),
            "primary_label": g["primary_label"], "primary_label_id": g["primary_label_id"],
            "first_problem_turn": g["first_problem_turn"],
            "first_wrong_lean_turn": (g["state_summary"] or {}).get("first_wrong_lean_turn"),
            "family_changed": changed}


def check(trace: dict) -> dict:
    """The runner's checker: the pass plus scalar columns for runs.csv. Refuses a changed family file
    (FamilyChanged, shown as check_error in runs.csv)."""
    return _check(trace, allow_change=False)


def check_allow_family_change(trace: dict) -> dict:
    """Like ``check``, but grades against the current family file even if it changed (marked family_changed)."""
    return _check(trace, allow_change=True)


def grade_experiment(out_dir: str | Path, root: Path | None = None,
                     allow_family_change: bool = False) -> tuple[list[dict], list[str]]:
    """Write ``labels/<run_id>.json`` and ``labels.csv`` (one row per label) for every completed run.

    Returns the grades and the reasons for runs skipped because their family file changed.
    """
    out_dir = Path(out_dir)
    (out_dir / "labels").mkdir(parents=True, exist_ok=True)
    rows, grades, skipped = [], [], []
    for path in sorted((out_dir / "traces").glob("*.json")):
        trace = load_trace(path)
        if trace.get("status") != "completed":
            continue
        try:
            family, changed = _family_for(trace, root, allow_family_change)
        except FamilyChanged as exc:
            skipped.append(str(exc))
            (out_dir / "labels" / path.name).unlink(missing_ok=True)   # no stale labels left behind
            continue
        g = grade(trace, family, changed)
        g["model"] = (trace.get("model") or {}).get("name")
        grades.append(g)
        (out_dir / "labels" / path.name).write_text(json.dumps(g, indent=2), encoding="utf-8")
        for l in g["labels"]:
            rows.append({"run_id": g["run_id"], "run_uid": g["run_uid"], "model": g["model"], "family": g["family_id"], "version": g["version"],
                         "version_type": g["version_type"], "mode": g["mode"], "family_changed": changed,
                         **{k: (json.dumps(v) if isinstance(v, list) else v) for k, v in l.items()}})
    columns = ["run_id", "run_uid", "model", "family", "version", "version_type", "mode", "family_changed",
               "label_id", "label", "group", "detail", "role", "turn", "severity", "accepted", "recovered",
               "evidence", "note"]
    with open(out_dir / "labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return grades, skipped
