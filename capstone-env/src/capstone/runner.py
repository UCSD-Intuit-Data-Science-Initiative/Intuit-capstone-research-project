"""Repeat runner: every case x version x mode x model x k repeats, then the results tables.

Each run gets a fresh environment, model client and conversation, and is saved
to ``<out>/traces/<run_id>.json``. Runs whose trace is already saved as
completed are skipped, so rerunning the same command after a crash resumes it.
"""

from __future__ import annotations

import csv
import importlib
import itertools
import math
import re
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .agent import DEFAULT_MAX_TOOL_CALLS
from .env import make_env
from .models import make_model
from .player import MODES, Case, play
from .trace import Trace, load_trace

DEFAULT_CHECKER = "capstone.checker:check"
DEFAULT_GROUP_BY = ("model", "mode", "case_type", "version")


@dataclass
class Settings:
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    workers: int = 4
    retries: int = 2  # extra attempts for a run that errors (API outage, etc.)
    retry_wait_s: float = 10.0


@dataclass(eq=False)
class RunSpec:
    case: Case
    mode: str
    model: str
    repeat: int

    @property
    def run_id(self) -> str:
        parts = (self.case.case_id, self.case.version, self.mode, self.model, f"r{self.repeat}")
        return "__".join(re.sub(r"[^A-Za-z0-9._-]", "-", p) for p in parts)


def plan_runs(cases: list[Case], modes: list[str], models: list[str], k: int) -> list[RunSpec]:
    bad_modes = set(modes) - set(MODES)
    if bad_modes:
        raise ValueError(f"unknown modes {sorted(bad_modes)}; use {MODES}")
    if "baseline" in modes:
        missing = [f"{c.case_id}/{c.version}" for c in cases if c.baseline_message is None]
        if missing:
            raise ValueError(f"baseline mode needs a baseline_message for: {', '.join(missing)}")
    return [RunSpec(c, mode, model, r)
            for c, mode, model, r in itertools.product(cases, modes, models, range(k))]


def trace_path(out_dir: Path, spec: RunSpec) -> Path:
    return out_dir / "traces" / f"{spec.run_id}.json"


def is_done(path: Path) -> bool:
    try:
        return load_trace(path).get("status") == "completed"
    except (OSError, ValueError):
        return False


def run_one(spec: RunSpec, model_configs: dict[str, dict], settings: Settings) -> Trace:
    case = spec.case
    trace = Trace(
        run_id=spec.run_id, case_id=case.case_id, case_version=case.version, case_type=case.case_type,
        case_file=case.source, case_hash=case.case_hash, expected_outcome=case.expected_outcome,
        env=case.env, mode=spec.mode, model={"name": spec.model, **model_configs[spec.model]},
        settings={"max_tool_calls": settings.max_tool_calls}, repeat=spec.repeat,
    )
    env = None
    try:
        model = make_model(spec.model, model_configs)
        env = make_env(case.env)
        play(case, spec.mode, env, model, trace, settings.max_tool_calls)
        trace.finish("completed")
    except Exception:
        trace.finish("error", error=traceback.format_exc())
    finally:
        if env is not None:
            env.close()
    return trace


def run_experiment(specs: list[RunSpec], model_configs: dict[str, dict], out_dir: str | Path,
                   settings: Settings, log: Callable[[str], None] = print) -> Counter:
    out_dir = Path(out_dir)
    todo = [s for s in specs if not is_done(trace_path(out_dir, s))]
    log(f"{len(specs)} runs planned, {len(specs) - len(todo)} already saved, {len(todo)} to run")

    def attempt(spec: RunSpec) -> tuple[RunSpec, Trace]:
        for n in range(settings.retries + 1):
            if n:
                time.sleep(settings.retry_wait_s * n)
            trace = run_one(spec, model_configs, settings)
            trace.data["attempt"] = n + 1
            if trace.data["status"] == "completed":
                break
        trace.save(trace_path(out_dir, spec))
        return spec, trace

    counts: Counter = Counter()
    with ThreadPoolExecutor(max_workers=settings.workers) as pool:
        futures = [pool.submit(attempt, s) for s in todo]
        for done, fut in enumerate(as_completed(futures), 1):
            spec, trace = fut.result()
            status = trace.data["status"]
            counts[status] += 1
            s = trace.data["summary"]
            detail = f"final={s['final_action']}" if status == "completed" else trace.data["error"].strip().splitlines()[-1]
            log(f"[{done}/{len(todo)}] {status:9} {spec.run_id}  {detail}")
    return counts


# ---- Scoring and tables -----------------------------------------------------

def load_checker(spec: str) -> Callable[[dict], dict]:
    module, func = spec.split(":", 1)
    return getattr(importlib.import_module(module), func)


def pass_hat_k(n: int, c: int, k: int) -> float | None:
    """tau-bench pass^k for one task: chance that k runs drawn from n all pass."""
    return math.comb(c, k) / math.comb(n, k) if n >= k else None


def _mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 6) if values else None


def score_experiment(out_dir: str | Path, checker: str = DEFAULT_CHECKER,
                     group_by: tuple[str, ...] = DEFAULT_GROUP_BY, k: int | None = None) -> tuple[list[dict], list[dict]]:
    """Grade every saved trace and write runs.csv (one row per run) and summary.csv."""
    out_dir = Path(out_dir)
    check = load_checker(checker)
    rows = []
    for path in sorted((out_dir / "traces").glob("*.json")):
        t = load_trace(path)
        s = t.get("summary", {})
        row: dict[str, Any] = {
            "run_id": t["run_id"], "case_id": t["case_id"], "version": t["case_version"],
            "case_type": t["case_type"], "mode": t["mode"], "model": t["model"]["name"], "repeat": t["repeat"],
            "status": t["status"], "expected_outcome": t.get("expected_outcome"),
            "final_action": s.get("final_action"), "first_decision": s.get("first_decision"), "passed": None,
        }
        if t["status"] == "completed":
            try:
                result = check(t)
                row["passed"] = result.get("passed")
                row.update({f"check_{key}": v for key, v in result.items()
                            if key != "passed" and (v is None or isinstance(v, (str, int, float, bool)))})
            except Exception as exc:
                row["check_error"] = f"{type(exc).__name__}: {exc}"
        for key in ("n_turns", "n_model_calls", "n_tool_calls", "step_cap_hits", "tokens_in", "tokens_out",
                    "cost_usd", "model_latency_s", "ttft_mean_s"):
            row[key] = s.get(key)
        row["trace"] = path.relative_to(out_dir).as_posix()
        rows.append(row)

    k = k or max((r["repeat"] + 1 for r in rows), default=1)
    summary = summarize_rows(rows, group_by, k)
    _write_csv(out_dir / "runs.csv", rows)
    _write_csv(out_dir / "summary.csv", summary)
    return rows, summary


def summarize_rows(rows: list[dict], group_by: tuple[str, ...], k: int) -> list[dict]:
    """One row per group, with pass^1..pass^k averaged over the group's tasks (case_id, version)."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[tuple(r[g] for g in group_by)].append(r)
    out = []
    for key, rs in sorted(groups.items(), key=lambda kv: tuple(map(str, kv[0]))):
        tasks: dict[tuple, list[bool]] = defaultdict(list)
        for r in rs:
            if r["passed"] is not None:
                tasks[(r["case_id"], r["version"])].append(bool(r["passed"]))
        row = dict(zip(group_by, key))
        row.update(n_tasks=len({(r["case_id"], r["version"]) for r in rs}), n_runs=len(rs),
                   n_errors=sum(r["status"] != "completed" for r in rs),
                   n_graded=sum(len(v) for v in tasks.values()))
        for j in range(1, k + 1):
            row[f"pass^{j}"] = _mean([pass_hat_k(len(v), sum(v), j) for v in tasks.values()])
        for col in ("cost_usd", "tokens_in", "tokens_out", "model_latency_s", "ttft_mean_s"):
            row[f"mean_{col}"] = _mean([r[col] for r in rs if r["status"] == "completed"])
        out.append(row)
    return out


def _write_csv(path: Path, rows: list[dict]) -> None:
    columns = list(dict.fromkeys(c for r in rows for c in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def format_table(rows: list[dict], columns: list[str]) -> str:
    def cell(v: Any) -> str:
        return "-" if v is None else f"{v:.4f}" if isinstance(v, float) else str(v)
    cells = [[cell(r.get(c)) for c in columns] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) if cells else len(c) for i, c in enumerate(columns)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(columns, widths))]
    lines += ["  ".join(v.ljust(w) for v, w in zip(row, widths)) for row in cells]
    return "\n".join(lines)
