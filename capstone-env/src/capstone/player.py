"""Case files, the script player, and baseline mode.

A case file (YAML or JSON) is a hand-written conversation with the facts that
change during it. Shared fields sit at the top; either ``turns`` (one version,
named "v1") or ``versions`` (name -> turns plus optional overrides)::

    case_id: invoice_amount_correction
    env: toy_invoice                  # registered environment name
    case_type: correction             # free-form label for grouping results
    expected_outcome: pay             # what the checker grades against
    case_data: {...}                  # handed to env.reset()
    baseline_message: "..."           # all final facts in one message
    versions:
      v1:
        turns:
          - say: "Please pay invoice INV-1001."
          - say: "Correction: the invoice is $800, not $1,000."
            facts:                    # written to the database before this turn is sent
              - {path: invoices.INV-1001.amount, value: 800, note: vendor correction}

Modes:
  scripted  turns are sent one at a time; each turn's facts are applied right
            before it is sent, and the agent finishes (tool calls included)
            before the next turn.
  baseline  every fact change is applied up front, and the customer sends
            ``baseline_message`` as a single turn. A one-turn case with no fact
            changes uses that turn as its baseline message.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent import DEFAULT_MAX_TOOL_CALLS, TurnResult, run_agent_turn
from .env import Environment
from .models import Model
from .trace import Trace

MODES = ("scripted", "baseline")
CASE_SUFFIXES = (".yaml", ".yml", ".json")

CHAT_PROTOCOL = """

## Conversation
You are chatting with a customer. Use the tools to look things up and to take actions. When you need something from the customer, or you have finished, reply to them in plain text; that reply ends your turn."""


class FactChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    value: Any
    note: str = ""


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    say: str
    facts: list[FactChange] = []


class Version(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turns: list[Turn] = Field(min_length=1)
    baseline_message: str | None = None
    expected_outcome: str | None = None
    case_type: str | None = None
    description: str | None = None


class CaseFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    env: str
    description: str = ""
    case_type: str = "default"
    expected_outcome: str | None = None
    case_data: dict[str, Any] = {}
    baseline_message: str | None = None
    turns: list[Turn] | None = None
    versions: dict[str, Version] | None = None

    @model_validator(mode="after")
    def _turns_or_versions(self) -> "CaseFile":
        if (self.turns is None) == (self.versions is None):
            raise ValueError("give either `turns` or `versions`, not both")
        if self.turns is not None and not self.turns:
            raise ValueError("`turns` is empty")
        return self


class Case(BaseModel):
    """One playable version of a case."""
    case_id: str
    version: str
    env: str
    case_type: str
    description: str
    expected_outcome: str | None
    case_data: dict[str, Any]
    turns: list[Turn]
    baseline_message: str | None
    source: str
    case_hash: str


def load_case_file(path: str | Path) -> list[Case]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        cf = CaseFile.model_validate(yaml.safe_load(text))  # YAML is a superset of JSON
    except Exception as exc:
        raise ValueError(f"{path}: {exc}") from None
    versions = cf.versions or {"v1": Version(turns=cf.turns)}
    cases = []
    for name, v in versions.items():
        baseline = v.baseline_message or cf.baseline_message
        if baseline is None and len(v.turns) == 1 and not v.turns[0].facts:
            baseline = v.turns[0].say
        cases.append(Case(
            case_id=cf.case_id, version=name, env=cf.env,
            case_type=v.case_type or cf.case_type,
            description=v.description or cf.description,
            expected_outcome=v.expected_outcome or cf.expected_outcome,
            case_data=cf.case_data, turns=v.turns, baseline_message=baseline,
            source=str(path), case_hash=hashlib.sha256(text.encode()).hexdigest()[:12],
        ))
    return cases


def load_cases(paths: list[str | Path]) -> list[Case]:
    """Load case files, or every case file under the given directories."""
    files: list[Path] = []
    for p in map(Path, paths):
        files += sorted(f for f in p.rglob("*") if f.suffix in CASE_SUFFIXES) if p.is_dir() else [p]
    cases = [c for f in files for c in load_case_file(f)]
    seen: dict[tuple[str, str], str] = {}
    for c in cases:
        key = (c.case_id, c.version)
        if key in seen:
            raise ValueError(f"case {c.case_id} version {c.version} is defined in both {seen[key]} and {c.source}")
        seen[key] = c.source
    return cases


class Conversation:
    """One agent conversation against a fresh environment, recorded in a trace."""

    def __init__(self, env: Environment, model: Model, trace: Trace,
                 max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS) -> None:
        self.env, self.model, self.trace, self.max_tool_calls = env, model, trace, max_tool_calls
        self.messages: list[dict] = []
        self.turn = 0

    def start(self, case_data: dict) -> None:
        self.env.reset(copy.deepcopy(case_data))
        self.system = self.env.system_prompt() + CHAT_PROTOCOL
        self.tools = self.env.tool_definitions()
        self.trace.set(system_prompt=self.system, tools=self.tools, initial_db=self.env.snapshot())

    def apply_fact(self, path: str, value: Any, note: str = "") -> None:
        """Write a fact change into the database before the next customer turn."""
        old = self.env.set_fact(path, value)
        self.trace.add("fact_change", turn=self.turn + 1, path=path, old=old, new=value, note=note)

    def send(self, text: str) -> TurnResult:
        """Send one customer turn and wait for the agent to finish, tool calls included."""
        self.turn += 1
        if self.turn == 1:
            notice = self.env.observation()
            self.trace.set(observation=notice)
            if notice:
                text = f"[Case notice]\n{notice}\n\n[Customer]\n{text}"
        self.messages.append({"role": "user", "content": text})
        self.trace.add("customer", turn=self.turn, content=text)
        return run_agent_turn(self.model, self.system, self.messages, self.tools, self.env,
                              self.trace, self.turn, self.max_tool_calls)

    def finish(self) -> None:
        self.trace.set(final_db=self.env.snapshot())


def play(case: Case, mode: str, env: Environment, model: Model, trace: Trace,
         max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS) -> None:
    """Play one case through the agent in ``scripted`` or ``baseline`` mode."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if mode == "baseline" and case.baseline_message is None:
        raise ValueError(f"case {case.case_id} version {case.version} has no baseline_message")

    convo = Conversation(env, model, trace, max_tool_calls)
    convo.start(case.case_data)
    if mode == "baseline":
        for fact in (f for t in case.turns for f in t.facts):
            convo.apply_fact(fact.path, fact.value, fact.note)
        convo.send(case.baseline_message)
    else:
        for turn in case.turns:
            for fact in turn.facts:
                convo.apply_fact(fact.path, fact.value, fact.note)
            convo.send(turn.say)
    convo.finish()
