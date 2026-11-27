"""Throughput benchmark for local model servers: replays recorded agent requests at a set concurrency.

Every backend gets the same work. Requests are rebuilt from saved traces (the system prompt, tools and the
conversation up to each agent call, exactly as the agent saw it), so the history is fixed and does not depend
on what the benchmarked model answers. Each conversation is replayed in order (its requests share a growing
prefix, like a real run, so prompt caching counts); C conversations run at once. Output length is capped at
the recorded length of each call (and at --max-tokens).

Standard library only, so it runs on the host against any OpenAI-compatible server (Ollama, llama-server,
mlx_lm.server):

    python scripts/bench_parallel.py --traces results/<run>/traces --conversations 8 --calls 5 \\
        --base-url http://127.0.0.1:11436/v1 --model qwen3.5:9b --concurrency 4 --label ollama-p4

It appends one JSON line per run to --out (default bench_parallel.jsonl) and prints a summary.
"""
from __future__ import annotations

import argparse
import glob
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _tool_calls(raw) -> list[dict]:
    return raw if isinstance(raw, list) else []


def conversations(trace_dir: str, n: int, calls: int) -> list[dict]:
    """The first ``calls`` agent requests of ``n`` traces, spread over versions and modes."""
    files = sorted(glob.glob(f"{trace_dir}/*.json"))
    picked = files[:: max(1, len(files) // n)][:n]
    out = []
    for f in picked:
        t = json.loads(Path(f).read_text())
        msgs: list[dict] = [{"role": "system", "content": t["system_prompt"]}]
        reqs = []
        for e in t["events"]:
            if e["type"] in ("analyst", "reaction", "user"):
                msgs.append({"role": "user", "content": e.get("content") or e.get("text") or ""})
            elif e["type"] == "agent":
                usage = e.get("usage") or {}
                reqs.append({"messages": [dict(m) for m in msgs], "tokens_out": int(usage.get("tokens_out") or 256),
                             "tokens_in": int(usage.get("tokens_in") or 0)})
                tcs = _tool_calls(e.get("tool_calls"))
                m = {"role": "assistant", "content": e.get("text") or ""}
                if tcs:
                    m["tool_calls"] = [{"id": c["id"], "type": "function",
                                        "function": {"name": c["name"], "arguments": json.dumps(c.get("args") or {})}}
                                       for c in tcs]
                msgs.append(m)
                if len(reqs) >= calls:
                    break
            elif e["type"] == "tool":
                msgs.append({"role": "tool", "tool_call_id": e["id"], "content": e.get("result") or ""})
        out.append({"run_id": t["run_id"], "tools": t.get("tools") or [], "requests": reqs})
    return out


def post(base_url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(f"{base_url}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer local"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", required=True)
    ap.add_argument("--conversations", type=int, default=8)
    ap.add_argument("--calls", type=int, default=5, help="agent requests replayed per conversation")
    ap.add_argument("--base-url", required=True, help="comma-separated for several servers: conversation i goes to server i mod n")
    ap.add_argument("--model", required=True)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=768)
    ap.add_argument("--no-tools", action="store_true", help="for a server that rejects tool definitions")
    ap.add_argument("--extra", default="{}", help="extra JSON fields for every request body")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default="bench_parallel.jsonl")
    a = ap.parse_args()

    convs = conversations(a.traces, a.conversations, a.calls)
    extra = json.loads(a.extra)
    lock = threading.Lock()
    rows: list[dict] = []

    urls = a.base_url.split(",")

    def play(item: tuple[int, dict]) -> None:
        k, conv = item
        url = urls[k % len(urls)]
        for i, r in enumerate(conv["requests"]):
            body = {"model": a.model, "messages": r["messages"], "stream": False,
                    "max_tokens": max(16, min(r["tokens_out"], a.max_tokens)), **extra}
            if conv["tools"] and not a.no_tools:
                body["tools"] = conv["tools"]
            t0 = time.perf_counter()
            err = None
            try:
                resp = post(url, body, a.timeout)
                u = resp.get("usage") or {}
            except Exception as exc:  # report and keep going: one failure should not hide the rest
                err, u = f"{type(exc).__name__}: {exc}"[:200], {}
            with lock:
                rows.append({"run": conv["run_id"], "i": i, "s": time.perf_counter() - t0, "err": err,
                             "in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
                             "cap": body["max_tokens"]})

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=a.concurrency) as pool:
        list(pool.map(play, enumerate(convs)))
    wall = time.perf_counter() - t0

    ok = [r for r in rows if not r["err"]]
    out_tok, in_tok = sum(r["out"] for r in ok), sum(r["in"] for r in ok)
    lat = sorted(r["s"] for r in ok) or [0]
    summary = {"label": a.label, "base_url": a.base_url, "model": a.model, "concurrency": a.concurrency,
               "conversations": len(convs), "requests": len(rows), "errors": len(rows) - len(ok),
               "wall_s": round(wall, 1), "out_tokens": out_tok, "in_tokens": in_tok,
               "out_tok_per_s": round(out_tok / wall, 1), "req_per_min": round(60 * len(ok) / wall, 2),
               "latency_median_s": round(lat[len(lat) // 2], 1), "latency_p90_s": round(lat[int(.9 * (len(lat) - 1))], 1),
               "cap_tokens": sum(r["cap"] for r in ok),
               "first_errors": [r["err"] for r in rows if r["err"]][:3]}
    print(json.dumps(summary, indent=1))
    with open(a.out, "a") as fh:
        fh.write(json.dumps(summary) + "\n")


if __name__ == "__main__":
    main()
