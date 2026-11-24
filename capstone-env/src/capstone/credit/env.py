"""The credit-renewal environment: the agent's tools over one versioned record.

Open mode (the pilot): the harness enforces only mechanics. A write needs a fact that exists, a
document that was already given and a decision that is still pending; a final action can be
taken once. Whether an edit or an action follows the policy is left to the grader, so mistakes
stay visible in the trace. Every call, accepted or rejected, produces an event (``drain_events``)
that the agent loop writes into the trace with the record version at that moment.

Implements the harness ``Environment`` interface (``env.py``); ``case_data`` is
``{"initial_record": <family version initial_record>}``.
"""

from __future__ import annotations

import ast
import json
import math
import operator
from typing import Any, Callable

from .record import CHANGE_TYPES, FINAL_ACTIONS, Record
from .view import render_document

SYSTEM_PROMPT = """You are a credit analyst agent at a bank. You review one borrower's annual credit-line renewal, following the written policy you are given in the conversation. You work only with the documents you are given and the tools below; documents are referred to by their ID (for example RNW-4, CASE or DOC-02).

The record holds the borrower's figures, each with the document it came from. read_record shows it. update_record changes a figure and must cite the document that supports the change. log_event records a development, something that happened; it never changes a figure. calculator does arithmetic and can use figures from the record by name. recommend records a tentative recommendation; it is not executed. request_evidence asks for a supporting document.

renew, decline and escalate are final actions: each can be taken once, cannot be undone and ends the review. A final action is taken only by calling its tool; stating a decision in your reply text does not take it.

When you have finished your turn, reply to the person you are talking to in plain text."""


def _schema(properties: dict[str, dict], required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _tool(name: str, description: str, properties: dict[str, dict] | None = None,
          required: list[str] | None = None) -> dict:
    props = properties or {}
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": _schema(props, list(props) if required is None else required)}}


_STR = {"type": "string"}
TOOLS = [
    _tool("read_document", "Show a document you have been given, by its ID.", {"doc_id": _STR}),
    _tool("read_record", "Show the record: each figure with its source document, the change log, logged "
                         "events, your calculations and recommendations, and the decision status."),
    _tool("update_record", "Change a recorded figure. Cite the document that supports the change.",
          {"fact": {"type": "string", "description": "the figure's name in the record"},
           "value": {"type": "number"},
           "change_type": {"type": "string", "enum": list(CHANGE_TYPES),
                           "description": "restatement: corrects a past figure; gap_filled: supplies a "
                                          "figure the record did not yet hold"},
           "evidence_doc": {"type": "string", "description": "ID of the supporting document"}}),
    _tool("log_event", "Log a development (something that happened) reported in a document. It never changes a figure; "
                    "to change or correct a figure, use update_record.",
          {"event": {"type": "string", "description": "short description of what happened"},
           "evidence_doc": {"type": "string", "description": "ID of the document that reports it"}}),
    _tool("calculator", "Evaluate arithmetic (+ - * / and parentheses). Figures from the record can be used "
                        "by name, e.g. (net_sales_2015 - net_sales_2014) / net_sales_2014. Each result is "
                        "stored with an ID.", {"expression": _STR}),
    _tool("recommend", "Record a tentative recommendation. It is not a final action and is not executed.",
          {"action": {"type": "string", "enum": list(FINAL_ACTIONS)}, "rationale": _STR,
           "calculation_id": {"type": "string", "description": "optional: the calculation it rests on"}},
          required=["action", "rationale"]),
    _tool("request_evidence", "Ask for a supporting document.", {"what": _STR}),
    *[_tool(a, f"Final action: {a} the credit line. Can be taken once, cannot be undone, ends the review.",
            {"rationale": _STR}) for a in FINAL_ACTIONS],
]

PARAMETERS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS}
# The trace event a call's attempt is logged as, accepted or rejected (Rule 4: every attempt is logged).
ATTEMPT_EVENT = {"update_record": "record_write", "log_event": "event_log", "calculator": "calculation",
                 "recommend": "recommendation", **{a: "final_action" for a in FINAL_ACTIONS}}


class ToolError(RuntimeError):
    """A tool failed inside the harness: our bug, not the agent's. The run ends as an error, ungraded."""


def _type_ok(value: Any, spec: dict) -> bool:
    if spec.get("type") == "string":
        return isinstance(value, str)
    if spec.get("type") == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return True


def check_args(name: str, args: Any) -> str | None:
    """Why a call's arguments break the tool's schema, or None if they fit it."""
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    schema = PARAMETERS[name]
    props, required = schema["properties"], schema["required"]
    extra = sorted(set(args) - set(props))
    if extra:
        return f"unexpected argument(s) {extra}; {name} takes {sorted(props)}"
    missing = [k for k in required if args.get(k) is None]
    if missing:
        return f"missing argument(s) {missing}"
    for key, value in args.items():
        spec = props[key]
        if value is None and key not in required:
            continue  # an optional argument sent as null counts as absent
        if not _type_ok(value, spec):
            return f"{key} must be a {'finite ' if spec['type'] == 'number' else ''}{spec['type']}"
        if "enum" in spec and value not in spec["enum"]:
            return f"{key} must be one of {spec['enum']}"
    return None


_OPS: dict[type, Callable] = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                              ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos}


class CalcError(ValueError):
    pass


def evaluate(expression: str, facts: dict[str, dict]) -> tuple[float, dict, list]:
    """Safe arithmetic; names are record facts. Returns (result, facts used, numeric literals)."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalcError(f"cannot parse {expression!r}") from exc
    used: dict[str, float] = {}
    numbers: list[float] = []

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            numbers.append(float(node.value))
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in facts:
                raise CalcError(f"{node.id} is not a figure in the record")
            value = facts[node.id]["value"]
            if value is None:
                raise CalcError(f"{node.id} has no value in the record yet")
            used[node.id] = value
            return float(value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise CalcError("division by zero")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise CalcError("only numbers, record figures, + - * / and parentheses are allowed")

    return ev(tree), used, numbers


class CreditRenewalEnv:
    """One run's world: the documents given so far and the versioned record."""

    action_labels = {a: a for a in FINAL_ACTIONS}

    def __init__(self) -> None:
        self.record: Record | None = None
        self.documents: dict[str, dict] = {}
        self.turn = 0
        self.final_requested = False
        self.terminated = False
        self.consecutive_rejections = 0
        self.evidence_requests: list[dict] = []
        self._events: list[dict] = []
        self._tools = {"read_document": self._read_document, "read_record": self._read_record,
                       "update_record": self._update_record, "log_event": self._log_event,
                       "calculator": self._calculator, "recommend": self._recommend,
                       "request_evidence": self._request_evidence,
                       **{a: (lambda rationale="", _a=a: self._final(_a, rationale)) for a in FINAL_ACTIONS}}

    # ---- harness interface
    def reset(self, case_data: dict) -> None:
        self.__init__()
        self.record = Record(case_data["initial_record"])

    def observation(self) -> str:
        return ""

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def tool_definitions(self) -> list[dict]:
        return TOOLS

    def snapshot(self) -> dict:
        return self.record.snapshot()

    def set_fact(self, path: str, value: Any) -> Any:
        raise NotImplementedError("credit runs change facts only through documents and the agent's own writes")

    def close(self) -> None:
        pass

    def drain_events(self) -> list[dict]:
        events, self._events = self._events, []
        return events

    # ---- player hooks
    def begin_turn(self, turn: int, documents: list[dict], final_requested: bool) -> None:
        self.turn, self.final_requested = turn, final_requested
        for d in documents:
            self.documents[d["doc_id"]] = d

    def requested_evidence_in(self, turn: int) -> bool:
        return any(r["turn"] == turn for r in self.evidence_requests)

    # ---- tools
    def step(self, action: str) -> tuple[str, float, bool, bool, dict]:
        call = json.loads(action)
        name, args = call.get("tool"), call.get("args")
        args = {} if args is None else args
        handler = self._tools.get(name)
        if handler is None:
            result = self._reject("tool_call", f"unknown tool {name!r}", tool=name)
        elif (problem := check_args(name, args)) is not None:
            # The agent's mistake: logged as a rejected attempt of that tool, with what it sent.
            event = ATTEMPT_EVENT.get(name, "tool_call")
            props = PARAMETERS[name]["properties"]
            sent: dict[str, Any] = {"tool": name} if event == "tool_call" else {}
            if isinstance(args, dict):
                sent.update({k: v for k, v in args.items() if k in props})
                if set(args) - set(props):
                    sent["unexpected_args"] = {k: v for k, v in args.items() if k not in props}
            else:
                sent["args"] = args
            result = self._reject(event, problem, **sent)
        else:
            # Arguments fit the schema, so an exception here is a harness bug: end the run as an
            # error instead of reporting it to the agent (and the grader) as the agent's misuse.
            try:
                result = handler(**args)
            except Exception as exc:
                raise ToolError(f"tool {name!r} failed inside the harness: {exc!r}") from exc
        if result.get("status") == "rejected":
            self.consecutive_rejections += 1
        else:
            self.consecutive_rejections = 0
        return json.dumps(result), 0.0, self.terminated, False, {}

    # Positional-only names, so an agent field such as log_event's ``event`` cannot collide with them.
    def _emit(self, _type: str, /, **fields: Any) -> None:
        self._events.append({"type": _type, "record_version": self.record.version, **fields})

    def _reject(self, _event: str, _reason: str, /, **fields: Any) -> dict:
        self._emit(_event, accepted=False, reason=_reason, **fields)
        return {"status": "rejected", "reason": _reason}

    def _read_document(self, doc_id: str) -> dict:
        if doc_id not in self.documents:
            return self._reject("tool_call", f"document {doc_id!r} has not been given", tool="read_document")
        return {"status": "ok", "document": render_document(self.documents[doc_id])}

    def _read_record(self) -> dict:
        return {"status": "ok", "record": self.record.agent_view()}

    def _update_record(self, fact: str, value: Any, change_type: str, evidence_doc: str) -> dict:
        attempt = {"fact": fact, "value": value, "change_type": change_type, "evidence_doc": evidence_doc}
        if not self.record.pending:
            return self._reject("record_write", "the decision is already final", **attempt)
        if fact not in self.record.facts:
            return self._reject("record_write", f"{fact!r} is not a figure in the record", **attempt)
        if change_type not in CHANGE_TYPES:
            return self._reject("record_write", f"change_type must be one of {list(CHANGE_TYPES)}", **attempt)
        if evidence_doc not in self.documents:
            return self._reject("record_write", f"document {evidence_doc!r} has not been given", **attempt)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return self._reject("record_write", "value must be a number", **attempt)
        entry = self.record.write_fact(fact, float(value), change_type, evidence_doc, self.turn)
        self._emit("record_write", accepted=True, **attempt, old_value=entry["old_value"],
                   version_before=entry["version_before"], version_after=entry["version_after"])
        return {"status": "ok", "record_version": self.record.version}

    def _log_event(self, event: str, evidence_doc: str) -> dict:
        attempt = {"event": event, "evidence_doc": evidence_doc}
        if not self.record.pending:
            return self._reject("event_log", "the decision is already final", **attempt)
        if evidence_doc not in self.documents:
            return self._reject("event_log", f"document {evidence_doc!r} has not been given", **attempt)
        entry = self.record.log_event(event, evidence_doc, self.turn)
        self._emit("event_log", accepted=True, **attempt, version_before=entry["version_before"],
                   version_after=entry["version_after"])
        return {"status": "ok", "record_version": self.record.version}

    def _calculator(self, expression: str) -> dict:
        try:
            result, used, numbers = evaluate(expression, self.record.facts)
        except CalcError as exc:
            return self._reject("calculation", str(exc), expression=expression)
        calc = self.record.add_calculation(expression, used, numbers, result, self.turn)
        self._emit("calculation", accepted=True, **{k: calc[k] for k in ("id", "expression", "facts_used",
                                                                        "numbers", "result")})
        return {"status": "ok", "calculation_id": calc["id"], "result": result}

    def _recommend(self, action: str, rationale: str, calculation_id: str | None = None) -> dict:
        if action not in FINAL_ACTIONS:
            return self._reject("recommendation", f"action must be one of {list(FINAL_ACTIONS)}", action=action)
        calcs = {c["id"]: c for c in self.record.calculations}
        if calculation_id is not None and calculation_id not in calcs:
            return self._reject("recommendation", f"no calculation {calculation_id!r}", action=action)
        rec = self.record.add_recommendation(action, rationale, calculation_id, self.turn)
        self._emit("recommendation", accepted=True, id=rec["id"], action=action, rationale=rationale,
                   calculation_id=calculation_id,
                   calculation_record_version=calcs[calculation_id]["record_version"] if calculation_id else None)
        return {"status": "ok", "recommendation_id": rec["id"], "note": "recorded; nothing was executed"}

    def _request_evidence(self, what: str) -> dict:
        self.evidence_requests.append({"turn": self.turn, "what": what})
        self._emit("evidence_request", accepted=True, what=what)
        return {"status": "ok", "note": "request sent"}

    def _final(self, action: str, rationale: str) -> dict:
        if not self.record.pending:
            return self._reject("final_action", "the decision is already final", action=action)
        final = self.record.finalize(action, rationale, self.turn, self.final_requested)
        self.terminated = True
        self._emit("final_action", accepted=True, action=action, rationale=rationale, requested=final["requested"])
        return {"status": "ok", "final_action": action, "note": "executed; the review is closed"}
