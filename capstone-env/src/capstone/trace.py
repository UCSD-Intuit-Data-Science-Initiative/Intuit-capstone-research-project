"""Trace format: one JSON file per run with everything needed to grade or reread it.

Top level: run metadata (run_id, case, version, mode, model, settings, repeat,
timestamps), the system prompt and tools the agent saw, the database at the
start and end, ``events`` in order, and a ``summary`` computed at the end.

Event types (each has ``i``, ``type``, ``t`` seconds since start, ``turn``):
  customer     content sent to the agent
  fact_change  path, old, new, note: a fact injected into the database
  agent        text, tool_calls, stop_reason, served_by, usage (latency, TTFT, tokens, cost)
  tool         name, args, executed, result, terminated, decision, db_before, db_after
  step_cap     the agent hit the per-turn tool-call limit
Credit runs (capstone.credit) add: analyst, reaction, record_write, event_log, calculation,
recommendation, evidence_request, final_action, tool_call (rejected calls) and stop, each with
the record version at that moment.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _default(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def to_jsonable(obj: Any) -> Any:
    """Deep, JSON-safe copy (later changes to the source don't leak into the trace)."""
    return json.loads(json.dumps(obj, default=_default))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Trace:
    def __init__(self, **meta: Any) -> None:
        self.data: dict[str, Any] = {"schema": SCHEMA_VERSION, **to_jsonable(meta),
                                     "status": "running", "started_at": _now(), "events": []}
        self._t0 = time.perf_counter()

    @property
    def events(self) -> list[dict]:
        return self.data["events"]

    def add(self, type: str, **fields: Any) -> dict:
        event = {"i": len(self.events), "type": type, "t": round(time.perf_counter() - self._t0, 3),
                 **to_jsonable(fields)}
        self.events.append(event)
        return event

    def set(self, **fields: Any) -> None:
        self.data.update(to_jsonable(fields))

    def finish(self, status: str, error: str | None = None) -> None:
        self.data.update(status=status, error=error, finished_at=_now(),
                         duration_s=round(time.perf_counter() - self._t0, 3))
        self.data["summary"] = summarize(self.data)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # never leave a half-written trace behind


def load_trace(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def summarize(trace: dict) -> dict:
    events = trace.get("events", [])
    agent = [e for e in events if e["type"] == "agent"]
    tools = [e for e in events if e["type"] == "tool"]
    decisions = [e for e in tools if e.get("decision")]
    usages = [e["usage"] for e in agent]
    costs = [u["cost_usd"] for u in usages]
    ttfts = [u["ttft_s"] for u in usages if u["ttft_s"] is not None]
    terminal = next((e for e in tools if e.get("terminated")), None)

    if decisions:
        final_action = decisions[-1]["decision"]
    elif agent:
        final_action = "ask"  # ended with a reply to the customer, no decision taken
    else:
        final_action = None

    return {
        "n_turns": sum(e["type"] in ("customer", "analyst") for e in events),
        "stop_reason": trace.get("stop_reason"),
        "n_model_calls": len(agent),
        "n_tool_calls": sum(e["executed"] for e in tools),
        "n_tool_calls_refused": sum(not e["executed"] for e in tools),
        "step_cap_hits": sum(e["type"] == "step_cap" for e in events),
        "n_fact_changes": sum(e["type"] == "fact_change" for e in events),
        "final_action": final_action,
        "first_decision": decisions[0]["decision"] if decisions else None,
        "decisions": [{"turn": e["turn"], "decision": e["decision"], "tool": e["name"], "args": e["args"]}
                      for e in decisions],
        "terminal_turn": terminal["turn"] if terminal else None,
        "final_text": agent[-1]["text"] if agent else None,
        "tokens_in": sum(u["tokens_in"] for u in usages),
        "tokens_out": sum(u["tokens_out"] for u in usages),
        "cost_usd": None if None in costs else round(sum(costs), 6),
        "model_latency_s": round(sum(u["latency_s"] for u in usages), 3),
        "ttft_mean_s": round(sum(ttfts) / len(ttfts), 3) if ttfts else None,
    }
