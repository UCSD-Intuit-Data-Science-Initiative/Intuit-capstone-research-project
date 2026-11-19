"""Swappable models: one ``chat`` interface, one adapter per provider.

Adapters take provider-neutral messages and return a ``Reply``::

    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [ToolCall, ...]}
    {"role": "tool", "tool_call_id": "...", "content": "...", "is_error": False}

Which provider, model id, request params and prices a name like ``claude``
means lives in configs/models.yaml, so changing models is a config setting.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

DEFAULT_MODELS_CONFIG = "configs/models.yaml"


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict
    raw_args: str | None = None  # set when the model's arguments were not a JSON object


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    latency_s: float = 0.0
    ttft_s: float | None = None  # time until the first output (text, thinking or tool call) streamed in
    tokens_per_s: float | None = None  # output tokens / time spent generating after the first one
    cost_usd: float | None = None  # None when no price is known


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str | None = None
    served_by: str | None = None  # model id the provider says answered
    provider_content: Any = None  # provider-native assistant content, replayed as-is


class Model(Protocol):
    name: str

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> Reply: ...


def _finish_usage(usage: Usage, start: float, first: float | None, price: dict | None) -> Usage:
    end = time.perf_counter()
    usage.latency_s = round(end - start, 3)
    if first is not None:
        usage.ttft_s = round(first - start, 3)
        if usage.tokens_out and end > first:
            usage.tokens_per_s = round(usage.tokens_out / (end - first), 1)
    if usage.cost_usd is None and price and price.get("input") is not None and price.get("output") is not None:
        usage.cost_usd = (usage.tokens_in * price["input"] + usage.tokens_out * price["output"]) / 1e6
    return usage


# ---- Anthropic ----------------------------------------------------------------

def to_anthropic_tool(tool: dict) -> dict:
    fn = tool["function"] if tool.get("type") == "function" else tool
    schema = fn.get("parameters") or {"type": "object", "properties": {}}
    return {"name": fn["name"], "description": fn.get("description", ""), "input_schema": schema}


def to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert neutral messages; tool results and the next customer turn share one user message."""
    out: list[dict] = []
    for m in messages:
        if m["role"] == "assistant":
            if m.get("provider_content") is not None:
                # Replay Claude's own blocks unchanged so thinking blocks stay valid.
                content = list(m["provider_content"])
            else:
                content = [{"type": "text", "text": m["content"]}] if m["content"] else []
                content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in m["tool_calls"]]
            if content:
                out.append({"role": "assistant", "content": content})
            continue
        if m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if m.get("is_error"):
                block["is_error"] = True
        else:
            block = {"type": "text", "text": m["content"]}
        if out and out[-1]["role"] == "user":
            out[-1]["content"].append(block)
        else:
            out.append({"role": "user", "content": [block]})
    return out


class AnthropicModel:
    def __init__(self, name: str, model: str, params: dict | None = None, price: dict | None = None,
                 max_retries: int = 5) -> None:
        import anthropic

        self.name, self.model, self.price = name, model, price
        self.params = {"max_tokens": 16000, **(params or {})}
        self.client = anthropic.Anthropic(max_retries=max_retries)

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> Reply:
        start, first = time.perf_counter(), None
        with self.client.messages.stream(
            model=self.model,
            system=system,
            messages=to_anthropic_messages(messages),
            tools=[to_anthropic_tool(t) for t in tools],
            **self.params,
        ) as stream:
            for event in stream:
                if first is None and event.type in ("content_block_start", "content_block_delta"):
                    first = time.perf_counter()
            final = stream.get_final_message()

        u = final.usage
        tokens_in = u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)
        usage = _finish_usage(Usage(tokens_in=tokens_in, tokens_out=u.output_tokens), start, first, self.price)
        return Reply(
            text="".join(b.text for b in final.content if b.type == "text"),
            tool_calls=[ToolCall(b.id, b.name, dict(b.input)) for b in final.content if b.type == "tool_use"],
            usage=usage,
            stop_reason=final.stop_reason,
            served_by=final.model,
            provider_content=final.content,
        )


# ---- OpenAI-compatible (OpenAI, OpenRouter, vLLM, ...) -------------------------

def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            # "" rather than None: Ollama rejects a null content ("invalid message content type: <nil>")
            msg: dict[str, Any] = {"role": "assistant", "content": m["content"] or ""}
            if m["tool_calls"]:
                msg["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name,
                                  "arguments": c.raw_args if c.raw_args is not None else json.dumps(c.args)}}
                    for c in m["tool_calls"]
                ]
            out.append(msg)
    return out


def parse_tool_args(raw: str) -> tuple[dict, str | None]:
    """Return ``(args, None)``, or ``({}, raw)`` when the model sent something other than a JSON object."""
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}, raw
    return (args, None) if isinstance(args, dict) else ({}, raw)


class OpenAIModel:
    def __init__(self, name: str, model: str, params: dict | None = None, price: dict | None = None,
                 base_url: str | None = None, api_key_env: str = "OPENAI_API_KEY",
                 report_cost: bool = False, max_retries: int = 5) -> None:
        import openai

        self.name, self.model, self.price = name, model, price
        self.params = dict(params or {})
        self.report_cost = report_cost  # OpenRouter returns the billed cost with the usage
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ValueError(f"model {name!r}: {api_key_env} is not set (add it to .env)")
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=max_retries)

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> Reply:
        kwargs = dict(self.params)
        if tools:
            kwargs["tools"] = tools
        if self.report_cost:
            kwargs["extra_body"] = {"usage": {"include": True}}
        start, first = time.perf_counter(), None
        text: list[str] = []
        calls: dict[int, dict] = {}
        usage = finish = served_by = None

        stream = self.client.chat.completions.create(
            model=self.model, messages=to_openai_messages(system, messages),
            stream=True, stream_options={"include_usage": True}, **kwargs,
        )
        for chunk in stream:
            served_by = chunk.model or served_by
            if chunk.usage:
                usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            reasoning = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
            if first is None and (delta.content or delta.tool_calls or reasoning):
                first = time.perf_counter()
            if delta.content:
                text.append(delta.content)
            for tc in delta.tool_calls or []:
                slot = calls.setdefault(tc.index, {"id": None, "name": "", "args": ""})
                slot["id"] = slot["id"] or tc.id
                if tc.function:
                    slot["name"] = slot["name"] or tc.function.name or ""
                    slot["args"] += tc.function.arguments or ""
            finish = choice.finish_reason or finish

        tool_calls = []
        for i in sorted(calls):
            args, raw = parse_tool_args(calls[i]["args"])
            tool_calls.append(ToolCall(calls[i]["id"] or f"call_{i}", calls[i]["name"], args, raw))
        u = Usage(tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
                  tokens_out=getattr(usage, "completion_tokens", 0) or 0,
                  cost_usd=getattr(usage, "cost", None) if self.report_cost else None)
        return Reply(text="".join(text), tool_calls=tool_calls, stop_reason=finish, served_by=served_by,
                     usage=_finish_usage(u, start, first, self.price))


# ---- Mock (no API key) ----------------------------------------------------------

class MockModel:
    """Replays scripted replies, for tests and pipeline checks.

    Each script item is ``{"text": ..., "tool_calls": [{"name": ..., "args": {...}}]}``.
    Once the script runs out it answers "OK." in plain text.
    """

    def __init__(self, name: str = "mock", script: list[dict] | None = None, **_: Any) -> None:
        self.name = name
        self._script = list(script or [])
        self._ids = 0

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> Reply:
        item = self._script.pop(0) if self._script else {"text": "OK."}
        calls = []
        for c in item.get("tool_calls", []):
            self._ids += 1
            calls.append(ToolCall(f"mock_{self._ids}", c["name"], c.get("args", {}), c.get("raw_args")))
        return Reply(text=item.get("text", ""), tool_calls=calls, usage=Usage(cost_usd=0.0),
                     stop_reason="tool_use" if calls else "end_turn", served_by="mock")


# ---- Config -------------------------------------------------------------------

PROVIDERS = {"anthropic": AnthropicModel, "openai": OpenAIModel, "openrouter": OpenAIModel, "mock": MockModel}


def load_model_configs(path: str | Path = DEFAULT_MODELS_CONFIG) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def make_model(name: str, configs: dict[str, dict]) -> Model:
    """Build a fresh model client from its entry in configs/models.yaml."""
    if name not in configs:
        raise KeyError(f"model {name!r} is not in the models config; known: {sorted(configs)}")
    cfg = dict(configs[name])
    provider = cfg.pop("provider", None)
    if provider not in PROVIDERS:
        raise ValueError(f"model {name!r}: provider must be one of {sorted(PROVIDERS)}, got {provider!r}")
    if provider != "mock" and not cfg.get("model"):
        raise ValueError(f"model {name!r} has no model id yet; fill in `model:` in the models config")
    if provider == "openrouter":
        cfg.setdefault("base_url", "https://openrouter.ai/api/v1")
        cfg.setdefault("api_key_env", "OPENROUTER_API_KEY")
        cfg.setdefault("report_cost", True)
    return PROVIDERS[provider](name=name, **cfg)
