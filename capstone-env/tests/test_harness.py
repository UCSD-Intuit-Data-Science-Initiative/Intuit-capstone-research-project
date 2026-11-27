"""Harness tests. No API keys or network: models are scripted mocks, and the
provider adapters are fed recorded streams through a fake HTTP transport."""

import json
from pathlib import Path

import pytest

from capstone import env as env_module
from capstone.agent import run_agent_turn
from capstone.env import ToyInvoiceEnv, set_path
from capstone.models import (MockModel, ToolCall, make_model, parse_tool_args, to_anthropic_messages,
                             to_openai_messages)
from capstone.player import Conversation, load_case_file, load_cases, play
from capstone.runner import Settings, pass_hat_k, plan_runs, run_experiment, score_experiment, trace_path
from capstone.trace import Trace, load_trace

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "cases" / "example_invoice_correction.yaml"


def call(name, **args):
    return {"name": name, "args": args}


def events(trace, type_):
    return [e for e in trace.events if e["type"] == type_]


# ---- Case files -------------------------------------------------------------------

def test_example_case_loads_both_versions():
    cases = load_case_file(EXAMPLE)
    assert [c.version for c in cases] == ["correction_turn2", "correction_turn3"]
    c = cases[0]
    assert c.env == "toy_invoice" and c.case_type == "correction" and c.expected_outcome == "pay"
    assert c.turns[1].facts[0].path == "invoices.INV-1001.amount" and c.turns[1].facts[0].value == 800
    assert c.baseline_message.endswith("Its amount is $800.")


def test_single_turn_case_is_its_own_baseline(tmp_path):
    f = tmp_path / "one.yaml"
    f.write_text("case_id: one\nenv: toy_invoice\nturns:\n  - say: Pay INV-1.\n")
    [case] = load_case_file(f)
    assert case.version == "v1" and case.baseline_message == "Pay INV-1."


def test_case_file_typos_fail_loudly(tmp_path):
    f = tmp_path / "typo.yaml"
    f.write_text("case_id: t\nenv: toy_invoice\nturns:\n  - say: hi\n    fact: []\n")
    with pytest.raises(ValueError, match="typo.yaml"):
        load_case_file(f)


def test_duplicate_case_versions_rejected(tmp_path):
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text("case_id: same\nenv: toy_invoice\nturns:\n  - say: hi\n")
    with pytest.raises(ValueError, match="defined in both"):
        load_cases([tmp_path])


def test_baseline_needs_a_message():
    multi_turn = load_case_file(EXAMPLE)[0].model_copy(update={"baseline_message": None})
    with pytest.raises(ValueError, match="baseline_message"):
        plan_runs([multi_turn], ["baseline"], ["mock"], 1)


# ---- Script player and baseline ---------------------------------------------------

def test_scripted_mode_injects_fact_right_before_its_turn():
    case = load_case_file(EXAMPLE)[0]
    model = MockModel(script=[
        {"tool_calls": [call("get_invoice", invoice_id="INV-1001")]},
        {"text": "It's $1,000. I'll wait."},
        {"tool_calls": [call("get_invoice", invoice_id="INV-1001")]},
        {"tool_calls": [call("pay_invoice", invoice_id="INV-1001", amount=800)]},
        {"text": "Paid $800."},
    ])
    trace = Trace(run_id="t")
    play(case, "scripted", ToyInvoiceEnv(), model, trace)
    trace.finish("completed")

    order = [(e["type"], e.get("turn")) for e in trace.events]
    assert order.index(("fact_change", 2)) < order.index(("customer", 2))
    assert order.index(("customer", 2)) > max(i for i, o in enumerate(order) if o[1] == 1)

    lookups = [json.loads(e["result"])["amount"] for e in events(trace, "tool") if e["name"] == "get_invoice"]
    assert lookups == [1000, 800]
    fact = events(trace, "fact_change")[0]
    assert (fact["old"], fact["new"]) == (1000, 800)

    pay = events(trace, "tool")[-1]
    assert pay["db_before"]["payments"] == [] and pay["db_after"]["payments"] == [{"invoice_id": "INV-1001", "amount": 800}]
    assert trace.data["initial_db"]["invoices"]["INV-1001"]["amount"] == 1000
    assert trace.data["final_db"]["invoices"]["INV-1001"]["status"] == "PAID"
    assert events(trace, "customer")[0]["content"].startswith("[Case notice]")
    s = trace.data["summary"]
    assert (s["final_action"], s["n_turns"], s["n_tool_calls"], s["terminal_turn"]) == ("pay", 2, 3, 2)


def test_baseline_mode_sends_one_message_with_final_facts():
    case = load_case_file(EXAMPLE)[1]
    model = MockModel(script=[{"tool_calls": [call("get_invoice", invoice_id="INV-1001")]}, {"text": "ok"}])
    trace = Trace(run_id="t")
    play(case, "baseline", ToyInvoiceEnv(), model, trace)

    [customer] = events(trace, "customer")
    assert customer["content"].endswith(case.baseline_message)
    assert [e["type"] for e in trace.events][0] == "fact_change"
    assert json.loads(events(trace, "tool")[0]["result"])["amount"] == 800


def test_ending_without_a_decision_is_ask():
    trace = Trace(run_id="t")
    play(load_case_file(EXAMPLE)[0], "scripted", ToyInvoiceEnv(), MockModel(), trace)
    trace.finish("completed")
    assert trace.data["summary"]["final_action"] == "ask"


def test_unknown_fact_path_fails_loudly():
    env = ToyInvoiceEnv()
    env.reset({"db": {"invoices": {"INV-1": {"amount": 5}}}})
    with pytest.raises(KeyError, match="amout"):
        env.set_fact("invoices.INV-1.amout", 6)
    assert set_path({"a": [{"b": 1}]}, "a.0.b", 2) == 1


# ---- Agent loop -------------------------------------------------------------------

def _convo(model, max_tool_calls=15):
    trace = Trace(run_id="t")
    convo = Conversation(ToyInvoiceEnv(), model, trace, max_tool_calls)
    convo.start(load_case_file(EXAMPLE)[0].case_data)
    return convo, trace


def test_step_cap_ends_the_turn_and_answers_every_tool_call():
    lookup = {"tool_calls": [call("get_invoice", invoice_id="INV-1001")] * 2}
    convo, trace = _convo(MockModel(script=[lookup] * 10), max_tool_calls=3)
    result = convo.send("hi")

    assert result.hit_step_cap and result.tool_calls == 3
    tools = events(trace, "tool")
    assert sum(e["executed"] for e in tools) == 3 and sum(not e["executed"] for e in tools) == 3
    assert len(events(trace, "step_cap")) == 1
    # Every tool call still gets a result, so the next request is valid.
    asked = {c.id for m in convo.messages if m["role"] == "assistant" for c in m["tool_calls"]}
    answered = {m["tool_call_id"] for m in convo.messages if m["role"] == "tool"}
    assert asked == answered


def test_broken_tool_arguments_are_not_executed():
    bad = {"tool_calls": [{"name": "pay_invoice", "args": {}, "raw_args": '{"invoice_id": "INV-1001", '}]}
    convo, trace = _convo(MockModel(script=[bad, {"text": "sorry"}]))
    convo.send("pay it")
    [tool] = events(trace, "tool")
    assert not tool["executed"] and "not a valid JSON" in tool["result"]
    assert trace.data["initial_db"] == convo.env.snapshot()


# ---- Repeat runner and tables -------------------------------------------------------

MODEL_CONFIGS = {
    "good": {"provider": "mock", "script": [
        {"tool_calls": [call("pay_invoice", invoice_id="INV-1001", amount=800)]}, {"text": "Paid."}]},
    "lazy": {"provider": "mock"},
}


def test_runner_runs_everything_resumes_and_writes_tables(tmp_path):
    cases = load_case_file(EXAMPLE)
    specs = plan_runs(cases, ["scripted", "baseline"], ["good", "lazy"], k=2)
    assert len(specs) == 2 * 2 * 2 * 2
    log: list[str] = []
    counts = run_experiment(specs, MODEL_CONFIGS, tmp_path, Settings(workers=4), log.append)
    assert counts == {"completed": 16}
    t = load_trace(trace_path(tmp_path, specs[0]))
    assert t["status"] == "completed" and t["model"]["name"] == "good" and t["case_hash"]

    log.clear()
    run_experiment(specs, MODEL_CONFIGS, tmp_path, Settings(), log.append)
    assert log == ["16 runs planned, 16 already saved, 0 to run"]

    rows, summary = score_experiment(tmp_path, k=2)
    assert len(rows) == 16 and (tmp_path / "runs.csv").exists() and (tmp_path / "summary.csv").exists()
    by_model = {}
    for r in summary:
        by_model.setdefault(r["model"], set()).add((r["pass^1"], r["pass^2"]))
    assert by_model == {"good": {(1.0, 1.0)}, "lazy": {(0.0, 0.0)}}
    assert {r["check_checker"] for r in rows} == {"stub"}


class BrokenEnv(ToyInvoiceEnv):
    def step(self, action):
        raise RuntimeError("env bug")


def test_errored_runs_are_retried_saved_and_rerun_later(tmp_path, monkeypatch):
    monkeypatch.setitem(env_module.ENVIRONMENTS, "broken", BrokenEnv)
    f = tmp_path / "case.yaml"
    f.write_text("case_id: b\nenv: broken\nturns:\n  - say: hi\n")
    specs = plan_runs(load_case_file(f), ["scripted"], ["good"], k=1)
    out = tmp_path / "out"
    counts = run_experiment(specs, MODEL_CONFIGS, out, Settings(retries=1, retry_wait_s=0), lambda _: None)
    assert counts == {"error": 1}
    t = load_trace(trace_path(out, specs[0]))
    assert t["status"] == "error" and t["attempt"] == 2 and "env bug" in t["error"]

    log: list[str] = []
    run_experiment(specs, MODEL_CONFIGS, out, Settings(retries=0), log.append)
    assert log[0] == "1 runs planned, 0 already saved, 1 to run"


def test_pass_hat_k():
    assert pass_hat_k(4, 3, 1) == 0.75
    assert pass_hat_k(4, 3, 2) == 0.5
    assert pass_hat_k(4, 4, 4) == 1.0
    assert pass_hat_k(3, 3, 4) is None


def test_models_with_no_id_refuse_to_start():
    with pytest.raises(ValueError, match="no model id"):
        make_model("claude", {"claude": {"provider": "anthropic", "model": None}})


def test_models_config_has_no_model_ids_filled_in():
    from capstone.models import load_model_configs
    configs = load_model_configs(ROOT / "configs" / "models.yaml")
    assert {c["provider"] for c in configs.values()} == {"anthropic", "openai", "openrouter", "mock"}
    # Only `claude` is chosen (Stage 6 PR 4, the first real run); every other id stays blank until picked.
    assert configs["claude"]["model"] == "claude-opus-5-5"
    assert all(not c.get("model") for name, c in configs.items() if name != "claude")



def test_local_qwen_config_matches_the_serve_and_run_scripts(monkeypatch):
    """configs/local_qwen.yaml: both backends build, on the ports and server alias the scripts use."""
    from capstone.models import load_model_configs
    monkeypatch.setenv("OLLAMA_API_KEY", "any")
    configs = load_model_configs(ROOT / "configs" / "local_qwen.yaml")
    assert set(configs) == {"ollama-qwen9b", "llama-qwen9b"}
    serve = (ROOT / "scripts" / "serve_local_qwen.sh").read_text()
    run = (ROOT / "scripts" / "run_local_qwen.sh").read_text()
    for name, port in (("ollama-qwen9b", 11435), ("llama-qwen9b", 11437)):
        cfg = configs[name]
        assert cfg["model"] == "qwen3.5:9b" and cfg["base_url"] == f"http://host.docker.internal:{port}/v1"
        assert f"MODEL={name} PORT={port}" in run and str(port) in serve
        make_model(name, configs)
    assert "--alias qwen3.5:9b" in serve


# ---- Provider adapters --------------------------------------------------------------

NEUTRAL = [
    {"role": "user", "content": "pay INV-1001"},
    {"role": "assistant", "content": "Checking.", "provider_content": None,
     "tool_calls": [ToolCall("t1", "get_invoice", {"invoice_id": "INV-1001"})]},
    {"role": "tool", "tool_call_id": "t1", "name": "get_invoice", "content": '{"amount": 800}', "is_error": False},
    {"role": "user", "content": "thanks"},
]


def test_anthropic_messages_put_tool_results_first_in_one_user_turn():
    out = to_anthropic_messages(NEUTRAL)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [b["type"] for b in out[1]["content"]] == ["text", "tool_use"]
    assert [b["type"] for b in out[2]["content"]] == ["tool_result", "text"]

    replay = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "hi"}]
    out = to_anthropic_messages([NEUTRAL[0], {**NEUTRAL[1], "tool_calls": [], "provider_content": replay}])
    assert out[1]["content"] == replay


def test_openai_messages_and_tool_argument_parsing():
    out = to_openai_messages("rules", NEUTRAL)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "user"]
    assert json.loads(out[2]["tool_calls"][0]["function"]["arguments"]) == {"invoice_id": "INV-1001"}
    # a reply that is only tool calls is sent with "" content, never null (Ollama refuses null)
    only_calls = to_openai_messages("", [{**NEUTRAL[1], "content": ""}])
    assert only_calls[0]["content"] == "" and only_calls[0]["tool_calls"]
    assert parse_tool_args('{"a": 1}') == ({"a": 1}, None)
    assert parse_tool_args('{"a": ') == ({}, '{"a": ')
    assert parse_tool_args("[1]") == ({}, "[1]")


def _sse(events, named=True):
    lines = []
    for e in events:
        if named:
            lines.append(f"event: {e['type']}")
        lines.append(f"data: {json.dumps(e) if isinstance(e, dict) else e}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def _client_with(sdk, body, seen):
    import httpx2

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    return sdk(api_key="test", max_retries=0, http_client=sdk_http_client(sdk, httpx2.MockTransport(handler)))


def sdk_http_client(sdk, transport):
    import anthropic
    import openai
    return (anthropic.DefaultHttpxClient if sdk is anthropic.Anthropic else openai.DefaultHttpxClient)(transport=transport)


TOOLS = ToyInvoiceEnv().tool_definitions()


def test_anthropic_adapter_parses_a_streamed_tool_call(monkeypatch):
    import anthropic
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    model = make_model("c", {"c": {"provider": "anthropic", "model": "claude-test",
                                   "params": {"max_tokens": 1000}, "price": {"input": 4.0, "output": 20.0}}})
    stream = _sse([
        {"type": "message_start", "message": {"id": "m1", "type": "message", "role": "assistant", "model": "claude-test",
                                              "content": [], "stop_reason": None, "stop_sequence": None,
                                              "usage": {"input_tokens": 50, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Checking."}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_invoice", "input": {}}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '{"invoice_id": "INV-1001"}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 20}},
        {"type": "message_stop"},
    ])
    seen = []
    model.client = _client_with(anthropic.Anthropic, stream, seen)
    reply = model.chat("rules", NEUTRAL, TOOLS)

    assert reply.text == "Checking." and reply.stop_reason == "tool_use" and reply.served_by == "claude-test"
    assert [(c.id, c.name, c.args) for c in reply.tool_calls] == [("toolu_1", "get_invoice", {"invoice_id": "INV-1001"})]
    assert (reply.usage.tokens_in, reply.usage.tokens_out) == (50, 20)
    assert reply.usage.cost_usd == pytest.approx((50 * 4 + 20 * 20) / 1e6)
    assert reply.usage.ttft_s is not None
    body = seen[0]
    assert body["model"] == "claude-test" and body["system"] == "rules" and body["stream"] is True
    assert body["tools"][0]["input_schema"]["required"] == ["invoice_id"]
    # The reply's raw blocks are what gets replayed next turn.
    assert [b.type for b in reply.provider_content] == ["text", "tool_use"]


def test_openrouter_adapter_assembles_streamed_tool_calls_and_cost(monkeypatch):
    import openai
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    model = make_model("o", {"o": {"provider": "openrouter", "model": "some/model", "params": {"max_tokens": 500}}})
    base = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "some/model"}

    def chunk(delta, finish=None):
        return {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    stream = _sse([
        chunk({"role": "assistant", "content": "Checking."}),
        chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                               "function": {"name": "get_invoice", "arguments": '{"invoice_'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'id": "INV-1001"}'}}]}),
        chunk({"tool_calls": [{"index": 1, "id": "call_2", "type": "function",
                               "function": {"name": "hold_invoice", "arguments": '{"invoice_id": '}}]}),
        chunk({}, "tool_calls"),
        {**base, "choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52, "cost": 0.0001}},
        "[DONE]",
    ], named=False)
    seen = []
    model.client = _client_with(openai.OpenAI, stream, seen)
    reply = model.chat("rules", NEUTRAL, TOOLS)

    assert reply.text == "Checking." and reply.stop_reason == "tool_calls" and reply.served_by == "some/model"
    first, second = reply.tool_calls
    assert (first.id, first.name, first.args, first.raw_args) == ("call_1", "get_invoice", {"invoice_id": "INV-1001"}, None)
    assert (second.name, second.args, second.raw_args) == ("hold_invoice", {}, '{"invoice_id": ')
    assert (reply.usage.tokens_in, reply.usage.tokens_out, reply.usage.cost_usd) == (40, 12, 0.0001)
    body = seen[0]
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["usage"] == {"include": True} and body["max_tokens"] == 500 and len(body["tools"]) == 4
    assert body["messages"][0] == {"role": "system", "content": "rules"}


def test_openai_adapter_needs_its_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        make_model("o", {"o": {"provider": "openai", "model": "x"}})


def test_an_empty_stream_is_an_error_not_an_empty_reply(monkeypatch):
    from capstone.models import OpenAIModel
    monkeypatch.setenv("OLLAMA_API_KEY", "x")
    m = OpenAIModel("q", "qwen", api_key_env="OLLAMA_API_KEY")
    monkeypatch.setattr(m.client.chat.completions, "create", lambda **kw: iter(()))
    with pytest.raises(RuntimeError, match="empty stream"):
        m.chat("s", [{"role": "user", "content": "hi"}], [])


def test_openai_adapter_keeps_the_models_thinking_but_never_sends_it_back(monkeypatch):
    """Ollama and OpenRouter stream thinking as `reasoning`; it goes to the trace, not to the next call."""
    import openai
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    model = make_model("o", {"o": {"provider": "openrouter", "model": "some/model"}})
    base = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "some/model"}

    def chunk(delta, finish=None):
        return {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    stream = _sse([chunk({"role": "assistant", "content": "", "reasoning": "Growth is "}),
                   chunk({"reasoning": "below the threshold."}), chunk({}, "stop"),
                   {**base, "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}},
                   "[DONE]"], named=False)
    model.client = _client_with(openai.OpenAI, stream, [])
    reply = model.chat("rules", NEUTRAL, TOOLS)
    assert reply.reasoning == "Growth is below the threshold." and reply.text == ""
    from capstone.models import to_openai_messages
    history = to_openai_messages("", [{"role": "assistant", "content": reply.text, "tool_calls": []}])
    assert "reasoning" not in json.dumps(history)
