# Running local Qwen 3.5 9B in parallel (Nov 26 2026)

Question: can the 216-run Qwen job (about 15-20 h one run at a time on Ollama)
run faster on this machine (Apple M4 Max, 64 GB)?

## Result

| Backend (same Qwen 3.5 9B, 4-bit) | Benchmark wall time | Output tok/s | vs Ollama |
|---|---|---|---|
| Ollama 0.40.11, 1 slot (current set-up) | 592 s | 19.3 | 1.0× |
| llama.cpp `llama-server`, 1 slot | 551 s | 19.7 | 1.07× |
| llama.cpp `llama-server -np 4` | 360 s | 28.4 | **1.64×** |
| MLX `mlx_lm.server`, 4 at once | 300 s | 40.3 | **1.97×** |

All on AC power, each server alone on the GPU. Benchmark: `scripts/bench_parallel.py` replays the first 4
recorded agent requests of 8 conversations from the Nov 23 Qwen run (32 requests, 255k prompt tokens, output
capped at each call's recorded length, up to 768). Every backend gets identical requests, so the history does
not depend on the model's answers. 8 at once was no faster than 4 on either llama.cpp (383 s) or MLX (319 s):
the GPU is the limit.

**Harness check** (the real `run-families`, S01 v3 and v5b, both modes, 4 workers; current `main` code):

| Backend | Wall time, 4 runs | Output tokens | Decided | Passed | Rejected tool calls |
|---|---|---|---|---|---|
| Ollama, Nov 23 (one at a time, older code) | 19.2 min (sum of the 4 runs) | 22,039 | 4/4 | — | — |
| llama.cpp `-np 4` | 9.9 min | 20,052 | 4/4 | 3/4 | 0 |
| MLX, 4 at once | 14.3 min | 34,550 | 4/4 | 3/4 | 0 |

Both work with the harness unchanged: tool calls parse, thinking is saved, every run reaches a final action.
The MLX runs produced more tokens (more calls and longer thinking), so their wall time is not directly
comparable; four runs are too few to say whether that is the backend or chance.

## What did not help

- **Ollama's parallel setting.** Ollama runs `qwen3.5:9b` with one slot whatever `OLLAMA_NUM_PARALLEL` says
  (`n_slots = 1` in its log at 4 and 8). Sending several runs to that one slot is slower (697 s at 4 at
  once vs 576 s one at a time): each conversation evicts the last one's cached prompt, so every request
  re-reads its ~8k-token prompt.
- **Several Ollama servers** (4 servers, one conversation each): 741 s. They share one GPU and cannot batch.
- **Ollama's model file in llama.cpp**: does not load (`qwen35.rope.dimension_sections has wrong array
  length`); llama.cpp needs a standard GGUF.

## Trade-offs

- **llama.cpp** (`brew install llama.cpp`; `unsloth/Qwen3.5-9B-GGUF`, `Qwen3.5-9B-Q4_K_M.gguf`, 5.68 GB): the
  same Q4_K_M quantization as Ollama's model, and Ollama itself runs on llama.cpp, so it is the closest to the
  Nov 23 runs. Its output per run in the harness check was close to Ollama's.
- **MLX** (`pip install mlx-lm`; `mlx-community/Qwen3.5-9B-4bit`, 5.98 GB): the fastest, but a different
  4-bit quantization, so its answers may differ more from Ollama's. `mlx_lm.server` cuts every reply at 512
  tokens unless the request sets `max_tokens`; set it in the models file (below), or the model stops
  mid-thought and never decides.
- Either way, runs on a new backend are not comparable with the Nov 23 Ollama runs (they were not anyway:
  the policy text and prompts changed since).

## Set-up (llama.cpp or Ollama)

Both backends are in `configs/local_qwen.yaml` (`llama-qwen9b`, `ollama-qwen9b`); `configs/models.yaml`
stays without open-weight ids until the team picks one. `.env` needs `OLLAMA_API_KEY=<any value>` (neither
server checks it). Start one server in its own terminal and leave it running, then run from `capstone-env/`:

```bash
scripts/serve_local_qwen.sh llama        # or: ollama
scripts/run_local_qwen.sh llama          # all four families, k = 3; or: ollama
scripts/run_local_qwen.sh llama 1 data/families/smoke   # smoke families only, k = 1
```

`run_local_qwen.sh` stops at once if the server is not up, names the results folder
`results/<date>_<HHMM>_<families>_k<K>_qwen3.5-9b_<backend>`, copies the models file into it, uses 4 workers
on llama.cpp and 1 on Ollama, and sends the runs to Langfuse (`NO_LANGFUSE=1` skips that). Run one backend at
a time: they share the GPU. The servers it starts:

```bash
# llama.cpp: 4 slots, 32k context each
llama-server -m ~/models/Qwen3.5-9B-Q4_K_M.gguf --jinja -np 4 -c 131072 -fa on \
  --host 127.0.0.1 --port 11437 --alias qwen3.5:9b
# Ollama: one slot; 32k context, since the default can be shorter than a conversation and cuts it silently
OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_HOST=127.0.0.1:11435 ollama serve
# MLX (not in the scripts): 4 requests at once; model: the path the server was started with
mlx_lm.server --model ~/models/qwen3.5-9b-mlx-4bit --host 127.0.0.1 --port 11437 \
  --decode-concurrency 4 --prompt-concurrency 4 --prompt-cache-size 16
```

## Full run on llama.cpp (Nov 26)

All four families x 9 versions x 2 modes x k = 3 on `llama-server -np 4`, PR #19 policy text:
216 / 216 completed in 6 h 48 min (about 15-20 h on Ollama one at a time), no harness errors, no step-cap
hits, no prompt cut to fit, 176 passed. Results folder
`results/2026-11-26_1055_rnw4_all-families_k3_qwen3.5-9b_llama-np4`; time breakdown in
`docs/run_time_breakdown.html` (token generation is 94% of the time; about two thirds of it is thinking).

- 43 of 2,722 model replies (1.6%) have no text and no tool call: the model wrote its answer inside its
  thinking. The Nov 23 Ollama run has the same rate (7 of 392, 1.8%), so it is the model, not the server.
  The analyst sees an empty reply; when it falls on the final decision the run ends as `no_decision`.
- The Langfuse copy lost steps for 69 runs because the export flushed only at the end; `export_runs` now
  flushes after each run, and the 69 were re-sent (all 216 checked against their trace files).

## Not tried

- Turning thinking off: much faster, but it changes what is measured.
- Speculative decoding with a small draft model (`--draft-model` / `-md`, e.g. Qwen 3.5 0.8B): another
  download; helps one request at a time more than a batch.
- llama.cpp `--kv-unified`, so the policy text that opens every conversation is cached once for all slots.

## Reproduce

```bash
python scripts/bench_parallel.py --traces results/<run>/traces --conversations 8 --calls 4 \
  --base-url http://127.0.0.1:11437/v1 --model qwen3.5:9b --concurrency 4 --label llama-np4
scripts/bench_ollama.sh <traces> <out.jsonl> <log dir>
scripts/bench_llama_mlx.sh <traces> <out.jsonl> <log dir> <model.gguf> <mlx model dir> <mlx venv>
```
