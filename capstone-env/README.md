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
| `make data` | Fetch FinQA + ConvFinQA at pinned commits and verify checksums |
| `make data-verify` | Offline check of `data/raw` against `data/checksums.sha256` |
| `make profile` | Profile the data and compare to the Nov 1 numbers |
| `make select` | Select candidates, split by company, check the gate |
| `make family` | Stage 4: build every family in `data/families/specs/` (9 versions × 2 modes each; see data/README.md) |
| `make family-check` | Rebuild families in memory; fail if any generated file differs (writes nothing) |
| `make run ARGS="..."` | Stage 6 harness: play cases through an agent and score them (see below) |
| `make run-families ARGS="..."` | Stage 6: play the credit families through an agent and score them |
| `make demo` | Stage 6: four scripted mock agents through F001, no keys needed |

## Project layout

```
src/capstone/      ← our code (mounted into the container)
scripts/           ← one-off scripts, smoke test
notebooks/         ← Jupyter notebooks
cases/             ← case definition files
data/              ← FinQA/ConvFinQA (fetch with `make data`; see data/README.md)
docs/              ← dataset card, review log
results/           ← experiment outputs (git-ignored)
tests/             ← pytest tests
```

FinVault lives at `/opt/finvault` inside the image (read-only reference).
It is already on `PYTHONPATH`; import with `from sandbox.sandbox_00 import ...`.

## Experiment harness (Stage 6, in progress)

Ported from Ivan's `setup/script-player-agent-loop` branch (PR 1 of the plan in
`docs/architecture_plan.md`). It plays each case through the agent, records everything, and hands
the record to the checker. `run` plays the stand-in `toy_invoice` case; `run-families` (PR 2) plays the
credit-renewal families in `data/families/` and grades them with the grader (PR 3), described below.

```
case file ─► script player ─► agent (LLM + tools) ─► environment
                                   │
                                   ▼
                               trace JSON ─► checker ─► runs.csv / summary.csv
                                   │
                                   └─► Langfuse (optional copy, for browsing)
```

| File | Piece |
|---|---|
| `src/capstone/player.py` | Case file format, script player, `scripted` and `baseline` modes |
| `src/capstone/agent.py` | Agent loop: model → tool calls → results → … until a text reply; tool-call cap per turn (default 15) |
| `src/capstone/models.py` | `model.chat(system, messages, tools) → Reply`; adapters for Anthropic, OpenAI, OpenRouter, plus a no-key `mock` |
| `src/capstone/trace.py` | Trace format and saving (one JSON per run): the official record the grader reads |
| `src/capstone/runner.py` | Repeat runner, results tables, pass^k |
| `src/capstone/cli.py` | `run`, `run-families`, `score`, `grade`, `show`, `chat`, `langfuse` |
| `src/capstone/env.py` | Environment interface + `toy_invoice`, a stand-in world |
| `src/capstone/checker.py` | Placeholder `check(trace)` for the `toy_invoice` case; credit runs use `credit/grader.py` |
| `src/capstone/observe.py` | Optional copy of finished traces to Langfuse |

Naming: the harness's `scripted` / `baseline` modes (turns one at a time vs all facts at once) are
not the families' `unscripted` / `scripted` modes (whether the analyst asks the calculation
questions). PR 2 replaces the harness case format with the families, so only the families' names
remain.

### Credit families (PR 2)

`src/capstone/credit/` plays the families in `data/families/` through the agent. The agent sees only
the speaker, text and documents of each turn (`view.py`); it works on a versioned record
(`record.py`) through ten tools (`env.py`: read_document, read_record, update_record, log_event,
calculator, recommend, request_evidence, renew, decline, escalate). The environment checks only
mechanics (the fact exists, the document was given, the decision is pending, the arguments fit the
tool's schema); policy is graded afterwards. The player (`player.py`) stops on a final action, a
request for evidence in reply to the final-decision request, a final action before it was asked for,
tool or time limits, three rejected calls in a row, or the end of the script. A bug inside a tool ends
the run as an error (not graded, not retried).

The grader (`grader.py`, PR 3) labels each run from its whole trace, in five groups (decision,
record, math, evidence, process), with a turn, a severity 0-4, whether a change went through and
whether it was fixed later. It writes `labels/<run>.json` and `labels.csv` next to the traces. It
grades a run only against the `family.json` it was played with (checked by `case_hash`); after a
`make family` rebuild, old runs are skipped unless you pass `--allow-family-change` (results are then
marked `family_changed`).

Each run has a `run_uid` (unique across experiments) and each label a `label_id`. `labels.csv` has one
row per label (columns: run_id, run_uid, model, family, version, version_type, mode, family_changed,
label_id, label, group, detail, role, turn, severity, accepted, recovered, evidence, note). A run's
earliest problem is its primary problem (`role` primary; the other problems are secondary). `runs.csv`
gains `run_uid`, `check_primary_label`, `check_primary_label_id`, `check_first_problem_turn` and
`check_first_wrong_lean_turn`. A sixth group, `state`, grades the agent's decision state turn by turn
against the family file's `expected_state`; it never changes pass or fail. `report` makes a results page
with, besides the older tables, "Primary problem per run", "Problems that occur together" and "Decision
state by turn". The rules are in the docstring of `grader.py` and in `docs/architecture_plan.md`.

```bash
python -m capstone.cli report results/fam1 --out results/fam1/report.md   # results page from a graded folder
make demo                                                     # 4 scripted mock agents, table + traces
python -m capstone.cli grade results/fam1                     # relabel saved runs
make poc-estimate                                             # first real run: rough cost (36 runs)
make poc                                                      # run it (needs ANTHROPIC_API_KEY) + results page
make run-families ARGS="--model mock --versions 3 --out results/fam1"
python -m capstone.cli run-families --model claude --families data/families/smoke/S01 --mode unscripted
python -m capstone.cli show results/demo/traces/demo__good__F001__3.json
```

### Commands

```bash
# all cases × both modes × 4 repeats, then score; rerun the same command to resume after a crash
python -m capstone.cli run --cases cases/ --model claude --k 4 --out results/exp1
python -m capstone.cli run --model mock --k 2 --out results/smoke     # no keys: checks the pipeline
python -m capstone.cli run ... --dry-run                              # print how many runs, then stop
python -m capstone.cli run ... --langfuse                             # also copy the runs to Langfuse

python -m capstone.cli score results/exp1          # regrade saved traces (e.g. after the checker changes)
python -m capstone.cli show results/exp1/traces/<run_id>.json        # read a conversation by hand
python -m capstone.cli langfuse results/exp1       # copy saved runs to Langfuse later
python -m capstone.cli chat --case cases/example_invoice_correction.yaml --model claude   # type turns yourself
```

### Models

`configs/models.yaml` maps each `--model` name to a provider, model id, API params and prices. The
model ids are deliberately blank; fill in `model:` for the entries you use (keys stay in `.env`). A
run refuses to start while the id is blank. Cost comes from `price` (USD per 1M tokens), or from
OpenRouter's own usage report.

### Outputs

`<out>/traces/<case>__<version>__<mode>__<model>__r<repeat>.json` holds the run metadata and
settings, the system prompt and tools, the database at the start and end, and every event in order:
messages, injected fact changes (old → new), agent messages with latency, TTFT, tokens and cost, and
tool calls with arguments, result and the database before and after. `<out>/runs.csv` has one row
per run; `<out>/summary.csv` has pass^1…pass^k per model × mode × case type × version.
`<out>/experiment.json` records the command and settings.

### Langfuse (optional)

Put `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env` (see `.env.example`), then add
`--langfuse` to `run`, or send saved runs with `python -m capstone.cli langfuse results/<exp>`.
Each run becomes one Langfuse trace: messages as events, model calls as generations (tokens, cost),
tool calls as tool steps (each one notes the model call that asked for it and that call's saved
thinking). A graded run also gets the grader's result as scores: `passed`, `decision`, the primary
problem, one `problem` score per label (keyed by its `label_id`) and the per-turn decision-state counts;
the trace is tagged with its `run_uid`. The trace files stay the official record; Langfuse is a copy
for browsing, and a run without keys still completes and saves its traces.

