"""Agent loop: send the conversation to the model, run any tool calls in the
environment, give the results back, and repeat until the model replies in text."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .env import Environment
from .models import Model, Reply, ToolCall
from .trace import Trace

DEFAULT_MAX_TOOL_CALLS = 15


@dataclass
class TurnResult:
    text: str
    model_calls: int
    tool_calls: int
    hit_step_cap: bool


def assistant_message(reply: Reply) -> dict:
    return {"role": "assistant", "content": reply.text, "tool_calls": reply.tool_calls,
            "provider_content": reply.provider_content}


def run_agent_turn(model: Model, system: str, messages: list[dict], tools: list[dict], env: Environment,
                   trace: Trace, turn: int, max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS) -> TurnResult:
    """Run the agent on the latest customer message; ``messages`` is extended in place.

    The turn ends when the model replies without tool calls, or when it asks for
    a tool after already running ``max_tool_calls`` of them this turn.
    """
    model_calls = executed = 0
    while True:
        reply = model.chat(system, messages, tools)
        model_calls += 1
        messages.append(assistant_message(reply))
        trace.add("agent", turn=turn, text=reply.text,
                  tool_calls=[{"id": c.id, "name": c.name, "args": c.args, "raw_args": c.raw_args}
                              for c in reply.tool_calls],
                  stop_reason=reply.stop_reason, served_by=reply.served_by, usage=reply.usage)
        if not reply.tool_calls:
            return TurnResult(reply.text, model_calls, executed, hit_step_cap=False)

        capped = executed >= max_tool_calls
        for call in reply.tool_calls:
            if capped or executed >= max_tool_calls:
                content = _refuse(trace, turn, call, f"Not executed: limit of {max_tool_calls} tool calls per turn reached.")
                is_error = True
            else:
                executed += 1
                content, is_error = _execute(env, trace, turn, call)
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                             "content": content, "is_error": is_error})
        if capped:
            trace.add("step_cap", turn=turn, limit=max_tool_calls)
            return TurnResult(reply.text, model_calls, executed, hit_step_cap=True)


def _refuse(trace: Trace, turn: int, call: ToolCall, reason: str) -> str:
    result = json.dumps({"status": "error", "error": reason})
    trace.add("tool", turn=turn, id=call.id, name=call.name, args=call.args, raw_args=call.raw_args,
              executed=False, result=result)
    return result


def _execute(env: Environment, trace: Trace, turn: int, call: ToolCall) -> tuple[str, bool]:
    if call.raw_args is not None:
        return _refuse(trace, turn, call, "Tool arguments were not a valid JSON object; nothing was executed."), True

    was_terminal = any(e["type"] == "tool" and e.get("terminated") for e in trace.events)
    before = env.snapshot()
    obs, reward, terminated, truncated, info = env.step(json.dumps({"tool": call.name, "args": call.args}))
    after = env.snapshot()

    # A decision is a labelled tool that changed the database, or the call that ended the episode.
    labels = getattr(env, "action_labels", {})
    if call.name in labels and before != after:
        decision = labels[call.name]
    elif terminated and not was_terminal:
        decision = call.name
    else:
        decision = None

    trace.add("tool", turn=turn, id=call.id, name=call.name, args=call.args, executed=True, result=obs,
              reward=reward, terminated=terminated, truncated=truncated, decision=decision, info=info,
              db_before=before, db_after=after)
    # Environments may report what a call did (accepted or rejected writes, stored calculations, ...).
    for extra in (env.drain_events() if hasattr(env, "drain_events") else []):
        extra = dict(extra)
        trace.add(extra.pop("type"), turn=turn, tool_call_id=call.id, **extra)
    return obs, False
