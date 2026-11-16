"""The versioned record one credit run works on.

``version`` goes up by one on every accepted write (a figure, an event). Calculations and
tentative recommendations are stored as objects with the record version and the figures they
used, so the grader can tell when reasoning was done on facts the record no longer holds.
Facts may carry extra fields (unit, period, how they entered) so a richer policy (RNW-1-v2, G1)
needs no change here.
"""

from __future__ import annotations

import copy
from typing import Any

FINAL_ACTIONS = ("renew", "decline", "escalate")
CHANGE_TYPES = ("restatement", "new_information")


class Record:
    def __init__(self, initial: dict) -> None:
        self.version = 1
        self.facts: dict[str, dict] = copy.deepcopy(initial["facts"])
        self.change_log: list[dict] = []
        self.events: list[dict] = []
        self.calculations: list[dict] = []
        self.recommendations: list[dict] = []
        self.decision_status = "pending"
        self.final_action: dict | None = None

    @property
    def pending(self) -> bool:
        return self.decision_status == "pending"

    def write_fact(self, fact: str, value: float, change_type: str, evidence_doc: str, turn: int) -> dict:
        old = self.facts[fact]["value"]
        self.facts[fact] = {**self.facts[fact], "value": value, "source_doc": evidence_doc}
        entry = {"fact": fact, "old_value": old, "new_value": value, "change_type": change_type,
                 "evidence_doc": evidence_doc, "turn": turn, "version_before": self.version,
                 "version_after": self.version + 1}
        self.version += 1
        self.change_log.append(entry)
        return entry

    def log_event(self, event: str, evidence_doc: str, turn: int) -> dict:
        entry = {"event": event, "change_type": "new_event", "evidence_doc": evidence_doc, "turn": turn,
                 "version_before": self.version, "version_after": self.version + 1}
        self.version += 1
        self.events.append(entry)
        return entry

    def add_calculation(self, expression: str, facts_used: dict, numbers: list, result: float, turn: int) -> dict:
        calc = {"id": f"C{len(self.calculations) + 1}", "expression": expression, "facts_used": facts_used,
                "numbers": numbers, "result": result, "record_version": self.version, "turn": turn}
        self.calculations.append(calc)
        return calc

    def add_recommendation(self, action: str, rationale: str, calculation_id: str | None, turn: int) -> dict:
        rec = {"id": f"R{len(self.recommendations) + 1}", "action": action, "rationale": rationale,
               "calculation_id": calculation_id, "record_version": self.version, "turn": turn}
        self.recommendations.append(rec)
        return rec

    def finalize(self, action: str, rationale: str, turn: int, requested: bool) -> dict:
        self.decision_status = "final"
        self.final_action = {"action": action, "rationale": rationale, "turn": turn,
                             "record_version": self.version, "requested": requested}
        return self.final_action

    def agent_view(self) -> dict:
        """What read_record shows: figures with their sources, logs and the decision status."""
        return {"version": self.version,
                "facts": {k: {"value": f["value"], "source_doc": f["source_doc"]} for k, f in self.facts.items()},
                "change_log": copy.deepcopy(self.change_log), "events": copy.deepcopy(self.events),
                "calculations": copy.deepcopy(self.calculations),
                "recommendations": copy.deepcopy(self.recommendations), "decision_status": self.decision_status}

    def snapshot(self) -> dict[str, Any]:
        """Everything, for the trace (before and after every tool call)."""
        return {**self.agent_view(), "facts": copy.deepcopy(self.facts),
                "final_action": copy.deepcopy(self.final_action)}
