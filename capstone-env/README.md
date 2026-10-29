# D23 Capstone Dev Environment

Containerized workspace for the Intuit safety-drift capstone.
Same Python, same packages, same FinVault commit for everyone.

## Prerequisites

| Platform | Install |
|---|---|
| **Mac (Intel or Apple Silicon)** | [Docker Desktop for Mac](https://docs.docker.com/desktop/install/mac-install/) |
| **Windows / WSL2** | [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/) with the WSL2 backend enabled. Run all commands from your WSL2 terminal. |

Make sure `docker compose version` prints **v2+**.

## Quick start

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd capstone-env

# 2. Add your API keys
cp .env.example .env
# Edit .env and paste your keys

# 3. Build the image (~2 min first time)
make build

# 4. Verify everything works (no keys needed)
make smoke

# 5. Start working
make shell          # interactive bash inside the container
make jupyter        # Jupyter Lab at http://localhost:8888
make test           # run pytest suite
```

## Make targets

| Target | What it does |
|---|---|
| `make build` | Build the base image |
| `make build-guard` | Build the heavier image with torch/transformers |
| `make shell` | Interactive bash inside the container |
| `make jupyter` | Start Jupyter Lab on port 8888 |
| `make smoke` | Run the smoke test (no API keys needed) |
| `make test` | Run the full pytest suite |
| `make run ARGS="..."` | Run the experiment harness (see below) |

## Project layout

```
src/capstone/      ← our code (mounted into the container)
configs/           ← models.yaml: which provider/model each --model name means
scripts/           ← one-off scripts, smoke test
notebooks/         ← Jupyter notebooks
cases/             ← case definition files
results/           ← experiment outputs (git-ignored)
tests/             ← pytest tests
```

FinVault lives at `/opt/finvault` inside the image (read-only reference).
It is already on `PYTHONPATH`; import with `from sandbox.sandbox_00 import ...`.

## Experiment harness

Plays each case through the agent, records everything, and hands the record
to the checker:

```
case file ─► script player ─► agent (LLM + tools) ─► environment
                                   │
                                   ▼
                               trace JSON ─► checker ─► runs.csv / summary.csv
```

| File | Piece |
|---|---|
| `src/capstone/player.py` | Case file format, script player (`scripted` mode), `baseline` mode |
| `src/capstone/agent.py` | Agent loop: model → tool calls via `env.step({"tool","args"})` → results → … until a text reply; per-turn tool-call cap (default 15) |
| `src/capstone/models.py` | `model.chat(system, messages, tools) → Reply`; adapters for Anthropic, OpenAI, OpenRouter, plus a no-key `mock` |
| `src/capstone/trace.py` | Trace format and saving (one JSON per run) |
| `src/capstone/runner.py` | Repeat runner, results tables, pass^k |
| `src/capstone/cli.py` | `run`, `score`, `show`, `chat` |
| `src/capstone/env.py` | Environment interface + `toy_invoice`, a stand-in world (not FinVault) |
| `src/capstone/checker.py` | Placeholder `check(trace)` until the real checker exists |

### Commands

```bash
# all cases × both modes × 4 repeats, then score; rerun the same command to resume after a crash
python -m capstone.cli run --cases cases/ --model claude --model open-weight --k 4 --out results/exp1
python -m capstone.cli run --model mock --k 2 --out results/smoke     # no keys: checks the pipeline
python -m capstone.cli run ... --dry-run                              # print how many runs, then stop

python -m capstone.cli score results/exp1          # regrade saved traces (e.g. after the checker changes)
python -m capstone.cli show results/exp1/traces/<run_id>.json        # read a conversation by hand
python -m capstone.cli chat --case cases/example_invoice_correction.yaml --model claude   # type turns yourself
```

### Models

`configs/models.yaml` maps each `--model` name to a provider, model id, API
params and prices. The model ids are deliberately blank; fill in `model:` for
the entries you use (keys stay in `.env`). A run refuses to start while the id
is blank. Cost comes from `price` (USD per 1M tokens), or from OpenRouter's
own usage report.

### Cases

See `cases/example_invoice_correction.yaml` and the docstring in
`player.py`. Each case has `case_data` for `env.reset()`, customer `turns`
(optionally several `versions`), `facts` to write into the database right
before a turn, `expected_outcome`, and a `baseline_message` with all final
facts at once. Unknown fields and fact paths that don't exist are errors.

- **scripted**: turns are sent one at a time; the agent finishes (tool calls
  included) before the next turn; each turn's fact changes are applied just
  before it is sent.
- **baseline**: all fact changes are applied up front and `baseline_message`
  is the only customer message.

### Outputs

`<out>/traces/<case>__<version>__<mode>__<model>__r<repeat>.json` holds the
run metadata and settings, the system prompt and tools, the database at the
start and end, and every event in order: customer messages, injected fact
changes (old → new), agent messages with per-call latency, TTFT, tokens/s,
tokens and cost, and tool calls with arguments, result and the database
before and after. Its `summary` has the final action (`pay`/`hold`/… from the
environment's decision tools, or `ask` if the agent never acted).

`<out>/runs.csv` has one row per run; `<out>/summary.csv` has pass^1…pass^k
per model × mode × case type × version (errored runs are counted but not
graded). `<out>/experiment.json` records the command and settings.

### Plugging in FinVault and the checker

- **Environment**: write a class with the methods of `Environment` in
  `env.py` (wrapping a FinVault env's `reset`/`step`/`get_tool_definitions`)
  and add it to `ENVIRONMENTS`, or name it in a case as `env: package.module:Class`.
- **Checker**: any `check(trace) -> dict` with a `passed` key; pass
  `--checker package.module:check`. Until then, the stub in `checker.py` only
  compares the final action with `expected_outcome`, so its numbers are
  placeholders.
