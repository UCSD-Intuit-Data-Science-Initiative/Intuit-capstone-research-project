"""Play one family version in one mode through the agent, with the stop rules.

Each script turn becomes one message (``view.render_message``); its documents become available
to ``read_document`` and as evidence. The agent may call tools until it replies in text. The run
stops at the first of:

  final_action        a final action after the final-decision request (the normal end)
  evidence_requested  no final action, but ``request_evidence`` in reply to the final-decision request
                      (RNW-1 P4/P7: asking for proof instead of deciding; the grader reads its class from
                      the gold, e.g. valid in 5b and unnecessary_block elsewhere)
  decided_too_early   a final action before it
  tool_limit_turn     more tool calls in one turn than ``Limits.tool_calls_per_turn``
  tool_limit_run      more tool calls in the run than ``Limits.tool_calls_per_run``
  time_limit          one agent turn took longer than ``Limits.seconds_per_turn``
  repeated_errors     ``Limits.rejected_in_a_row`` rejected tool calls in a row
  no_decision         the script ended with no final action

The 5b reaction: when a turn carries ``reactions`` and the agent called ``request_evidence``
during it, the claimant's scripted reply is sent as one more message and the agent answers it.

Trace events added on top of the harness's: ``analyst`` (the message, its documents' ids and the
script turn's kind, for the grader), ``reaction``, the environment's ``record_write`` /
``event_log`` / ``calculation`` / ``recommendation`` / ``evidence_request`` / ``final_action`` /
``tool_call`` (each with the record version), and ``stop``.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..agent import DEFAULT_MAX_TOOL_CALLS, run_agent_turn
from ..models import Model, make_model
from ..trace import Trace
from .env import CreditRenewalEnv, ToolError
from .view import agent_view, render_message

MODES = ("unscripted", "scripted")
DEFAULT_FAMILIES = "data/families"
STOP_REASONS = ("final_action", "evidence_requested", "decided_too_early", "tool_limit_turn", "tool_limit_run",
                "time_limit", "repeated_errors", "no_decision")


@dataclass
class Limits:
    tool_calls_per_turn: int = DEFAULT_MAX_TOOL_CALLS
    tool_calls_per_run: int = 60
    seconds_per_turn: float = 120.0
    rejected_in_a_row: int = 3


def load_family(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def family_hash(raw: bytes) -> str:
    """The family file's fingerprint, saved in each trace as ``case_hash`` and checked by the grader."""
    return hashlib.sha256(raw).hexdigest()[:12]


def find_families(paths: Sequence[str | Path] = (DEFAULT_FAMILIES,)) -> list[Path]:
    """family.json files from files or folders (searched recursively), sorted and de-duplicated."""
    found: set[Path] = set()
    for p in map(Path, paths):
        if p.is_dir():
            found |= set(p.rglob("family.json"))
        elif p.name == "family.json" or p.suffix == ".json":
            found.add(p)
    return sorted(found)


def version_of(family: dict, version_id: str) -> dict:
    for v in family["versions"]:
        if v["version_id"] == version_id:
            return v
    raise KeyError(f"{family['family_id']} has no version {version_id!r}")


def _agent_turn(model: Model, env: CreditRenewalEnv, messages: list, trace: Trace, turn: int,
                limits: Limits, totals: dict) -> str | None:
    """Run one agent turn; return a stop reason, or None to continue."""
    start = time.perf_counter()
    result = run_agent_turn(model, env.system_prompt(), messages, env.tool_definitions(), env, trace, turn,
                            limits.tool_calls_per_turn)
    totals["tool_calls"] += result.tool_calls
    final = env.record.final_action
    if final is not None:
        return "final_action" if final["requested"] else "decided_too_early"
    if result.hit_step_cap:
        return "tool_limit_turn"
    if totals["tool_calls"] > limits.tool_calls_per_run:
        return "tool_limit_run"
    if time.perf_counter() - start > limits.seconds_per_turn:
        return "time_limit"
    if env.consecutive_rejections >= limits.rejected_in_a_row:
        return "repeated_errors"
    if env.final_requested and env.requested_evidence_in(turn):
        return "evidence_requested"
    return None


def play_family(family: dict, version_id: str, mode: str, model: Model, trace: Trace,
                limits: Limits | None = None) -> str:
    """Play one version in one mode; return the stop reason. Everything is written to ``trace``."""
    limits = limits or Limits()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    version = version_of(family, version_id)
    env = CreditRenewalEnv()
    env.reset({"initial_record": version["initial_record"]})
    trace.set(system_prompt=env.system_prompt(), tools=env.tool_definitions(), initial_db=env.snapshot())
    messages: list[dict] = []
    totals = {"tool_calls": 0}
    reason, turn_no = None, 0
    for t in version["conversations"][mode]["turns"]:
        turn_no = t["turn"]
        view = agent_view(t)
        env.begin_turn(turn_no, view["documents"], t["kind"] == "final_decision_request")
        content = render_message(view)
        messages.append({"role": "user", "content": content})
        trace.add("analyst", turn=turn_no, kind=t["kind"], speaker=t["speaker"], content=content,
                  documents=[d["doc_id"] for d in view["documents"]])
        reason = _agent_turn(model, env, messages, trace, turn_no, limits, totals)
        if reason is None and t.get("reactions") and env.requested_evidence_in(turn_no):
            for r in t["reactions"]:
                if r["if_agent"] == "request_evidence":
                    reply = f"{r['speaker'].replace('_', ' ').capitalize()}: {r['text']}"
                    messages.append({"role": "user", "content": reply})
                    trace.add("reaction", turn=turn_no, speaker=r["speaker"], content=reply)
                    reason = _agent_turn(model, env, messages, trace, turn_no, limits, totals)
                    break
        if reason is not None:
            break
    reason = reason or "no_decision"
    final = env.record.final_action
    trace.add("stop", turn=turn_no, reason=reason, final_action=final["action"] if final else None,
              record_version=env.record.version)
    trace.set(stop_reason=reason, final_db=env.snapshot())
    return reason


# ---- run planning (used by the harness runner)

@dataclass(eq=False)
class FamilyRunSpec:
    family_path: Path
    family_id: str
    version_id: str
    version_type: str
    mode: str
    model: str
    repeat: int

    @property
    def run_id(self) -> str:
        parts = (self.family_id, self.version_id, self.mode, self.model, f"r{self.repeat}")
        return "__".join(re.sub(r"[^A-Za-z0-9._-]", "-", p) for p in parts)


def plan_family_runs(paths: Sequence[Path], versions: Sequence[str] | None, modes: Sequence[str],
                     models: Sequence[str], k: int) -> list[FamilyRunSpec]:
    bad = set(modes) - set(MODES)
    if bad:
        raise ValueError(f"unknown modes {sorted(bad)}; use {MODES}")
    specs = []
    for path in paths:
        fam = load_family(path)
        chosen = [v for v in fam["versions"] if versions is None or v["version_id"] in versions]
        for v, mode, model, r in itertools.product(chosen, modes, models, range(k)):
            specs.append(FamilyRunSpec(Path(path), fam["family_id"], v["version_id"], v["version_type"],
                                       mode, model, r))
    return specs


def run_family_one(spec: FamilyRunSpec, model_configs: dict[str, dict], settings) -> Trace:
    """One run in a fresh environment and model client (the runner's ``run_fn``)."""
    raw = Path(spec.family_path).read_bytes()
    fam = json.loads(raw)
    gold = version_of(fam, spec.version_id)["gold"]
    limits = Limits(tool_calls_per_turn=settings.max_tool_calls)
    trace = Trace(
        run_id=spec.run_id, case_id=spec.family_id, case_version=spec.version_id, case_type=spec.version_type,
        case_file=str(spec.family_path), case_hash=family_hash(raw), env="credit_renewal",
        family_set=fam["set"], policy_id=fam["policy"]["id"], expected_outcome=gold["acceptable_actions"],
        mode=spec.mode, model={"name": spec.model, **model_configs[spec.model]},
        settings={"limits": vars(limits)}, repeat=spec.repeat,
    )
    try:
        model = make_model(spec.model, model_configs)
        play_family(fam, spec.version_id, spec.mode, model, trace, limits)
        trace.finish("completed")
    except ToolError:
        # Our bug, not the model's or the API's: it would fail the same way again, so the runner does
        # not retry it (a retry would only spend more model calls). The run is an error, never graded.
        trace.set(error_kind="tool_bug")
        trace.finish("error", error=traceback.format_exc())
    except Exception:
        trace.finish("error", error=traceback.format_exc())
    return trace
