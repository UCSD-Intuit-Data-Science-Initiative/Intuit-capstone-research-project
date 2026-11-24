"""Before a run: a rough cost estimate. After a run: a results page from the experiment folder.

    python -m capstone.cli run-families ... --estimate          # print the estimate, run nothing
    python -m capstone.cli report results/poc1 --out docs/results/poc_run_1.md

The estimate is deliberately simple and errs high. It renders every message each planned run
sends, assumes ``calls_per_turn`` model calls per turn (the first plus tool-call follow-ups) that
each resend the whole conversation so far plus the tools and system prompt, and
``output_tokens_per_call`` output tokens per call (thinking included). Tokens are characters / 4.
Real runs are logged with exact tokens and cost, so the results page reports what was spent.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .env import SYSTEM_PROMPT, TOOLS
from .player import load_family, version_of
from .view import agent_view, render_message

CHARS_PER_TOKEN = 4
LOW, HIGH = {"calls_per_turn": 2, "output_tokens_per_call": 400}, {"calls_per_turn": 4, "output_tokens_per_call": 1500}


def _tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def run_tokens(family: dict, version_id: str, mode: str, calls_per_turn: int, output_tokens_per_call: int) -> tuple[int, int]:
    """(input, output) tokens one run would use under these assumptions."""
    fixed = _tokens(SYSTEM_PROMPT) + _tokens(json.dumps(TOOLS))
    context, tokens_in, tokens_out = 0, 0, 0
    for t in version_of(family, version_id)["conversations"][mode]["turns"]:
        context += _tokens(render_message(agent_view(t)))
        for _ in range(calls_per_turn):
            tokens_in += fixed + context
            tokens_out += output_tokens_per_call
            context += output_tokens_per_call // 2   # replies and tool results stay in the conversation
    return tokens_in, tokens_out


def estimate(specs: Sequence, model_configs: dict[str, dict]) -> dict:
    """Low and high token and cost estimates for a list of FamilyRunSpec."""
    families: dict[Path, dict] = {}
    out = {"runs": len(specs), "low": {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0},
           "high": {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}, "priced": True}
    for s in specs:
        fam = families.setdefault(s.family_path, load_family(s.family_path))
        price = (model_configs.get(s.model) or {}).get("price") or {}
        for name, case in (("low", LOW), ("high", HIGH)):
            tin, tout = run_tokens(fam, s.version_id, s.mode, **case)
            out[name]["tokens_in"] += tin
            out[name]["tokens_out"] += tout
            if price.get("input") is not None and price.get("output") is not None:
                out[name]["cost_usd"] += (tin * price["input"] + tout * price["output"]) / 1e6
            elif (model_configs.get(s.model) or {}).get("provider") != "mock":
                out["priced"] = False
    for name in ("low", "high"):
        out[name]["cost_usd"] = round(out[name]["cost_usd"], 2)
    return out


def format_estimate(est: dict) -> str:
    lo, hi = est["low"], est["high"]
    lines = [f"estimate for {est['runs']} runs (rough; real tokens and cost are logged per run):",
             f"  low   {lo['tokens_in']:>10,} in  {lo['tokens_out']:>9,} out  ${lo['cost_usd']:,.2f}"
             f"   ({LOW['calls_per_turn']} calls/turn, {LOW['output_tokens_per_call']} out/call)",
             f"  high  {hi['tokens_in']:>10,} in  {hi['tokens_out']:>9,} out  ${hi['cost_usd']:,.2f}"
             f"   ({HIGH['calls_per_turn']} calls/turn, {HIGH['output_tokens_per_call']} out/call)"]
    if not est["priced"]:
        lines.append("  (no price set for some models in configs/models.yaml, so their cost is not included)")
    return "\n".join(lines)


# ---- results page


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(v: str | None) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _run_key(row: dict) -> str:
    """A run's identity in a table row: run_uid, or run_id for experiments graded before run_uid existed."""
    return row.get("run_uid") or row["run_id"]


def _state_section(out_dir: Path, done: list[dict]) -> list[str]:
    """"Decision state by turn": per version type and turn kind, from the grade files (``labels/*.json``)."""
    keys = {_run_key(r) for r in done}
    grades = []
    for path in sorted((out_dir / "labels").glob("*.json")) if (out_dir / "labels").is_dir() else []:
        try:
            g = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if g.get("turn_states") and (g.get("run_uid") or g.get("run_id")) in keys:
            grades.append(g)
    L = ["", "## Decision state by turn", "",
         "What the agent did on each turn (tool calls only, never its text) against the expected state: the lean "
         "it stated (a final action, else its last recommend, else request_evidence = waiting_for_evidence), "
         "whether it was the expected lean, and on turns where the expected lean moved (the pin), whether the "
         "agent moved with it on that turn. Wrong = a stated lean that differs; stale = it kept the lean from "
         "before the pin moved; decision only = a wrong final action, graded as the decision (no lean_wrong label). "
         "Not stated = no lean on that turn (often fine: only tentative requests ask for one).", ""]
    if not grades:
        return L + ["Not available: no run in this experiment has per-turn states (graded before they existed, "
                    f"or the family files have no expected_state); regrade with `python -m capstone.cli grade {out_dir}`."]
    cells: dict[tuple, Counter] = defaultdict(Counter)
    order: dict[tuple, tuple] = {}
    for g in grades:
        wrong_detail = {l["turn"]: l.get("detail") for l in g["labels"] if l["label"] == "lean_wrong"}
        vkey = (int(str(g["version"]).rstrip("ab") or 0), str(g["version"]), g["version_type"])
        for s_ in g["turn_states"]:
            c = cells[(vkey[2], s_["kind"])]
            order.setdefault((vkey[2], s_["kind"]), (vkey, len(order)))
            c["turns"] += 1
            if s_["observed_lean"] is None:
                c["not_stated"] += 1
                if s_["pin_moved"]:
                    c["moves"] += 1
                continue
            c["stated"] += 1
            c["correct"] += s_["lean_ok"] is True
            if s_["lean_ok"] is False:
                c["wrong"] += 1
                d = wrong_detail.get(s_["turn"])
                c["stale" if d == "stale_lean" else "other" if d == "wrong_lean" else "decision"] += 1
            if s_["pin_moved"]:
                c["moves"] += 1
                c["moved_with"] += s_["lean_ok"] is True
    L += ["| Version type | Turn kind | Run-turns | Stated | Correct | Wrong | stale | other | decision only | Not stated | "
          "Pin moves | Moved with it |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key in sorted(cells, key=lambda k: order[k]):
        c = cells[key]
        L.append(f"| {key[0]} | {key[1]} | {c['turns']} | {c['stated']} | {c['correct']} | {c['wrong']} | {c['stale']} | "
                 f"{c['other']} | {c['decision']} | {c['not_stated']} | {c['moves']} | {c['moved_with']} |")
    return L


def render_report(out_dir: str | Path, title: str | None = None) -> str:
    """A Markdown results page from runs.csv, labels.csv and experiment.json."""
    out_dir = Path(out_dir)
    runs = _read_csv(out_dir / "runs.csv")
    labels = _read_csv(out_dir / "labels.csv")
    exp = json.loads((out_dir / "experiment.json").read_text()) if (out_dir / "experiment.json").exists() else {}
    done = [r for r in runs if r["status"] == "completed"]
    models = sorted({r["model"] for r in runs})
    model_ids = {n: (cfg or {}).get("model") for n, cfg in (exp.get("models") or {}).items()}
    cost = sum(_num(r.get("cost_usd")) for r in done)
    L = [f"# {title or 'Results: ' + out_dir.name}", "",
         f"Generated by `python -m capstone.cli report {out_dir}` (do not edit by hand) on "
         f"{datetime.now().date().isoformat()}.", "",
         f"- Models: {', '.join(f'{m} ({model_ids.get(m) or m})' for m in models) or '-'}",
         f"- Runs: {len(runs)} ({len(done)} completed, {len(runs) - len(done)} errored)",
         f"- Families: {', '.join(sorted({r['case_id'] for r in runs})) or '-'}; modes: "
         f"{', '.join(sorted({r['mode'] for r in runs})) or '-'}",
         f"- Tokens: {int(sum(_num(r.get('tokens_in')) for r in done)):,} in, "
         f"{int(sum(_num(r.get('tokens_out')) for r in done)):,} out; cost ${cost:,.2f}",
         f"- Passed: {sum(r['passed'] == 'True' for r in done)} of {len(done)}", ""]

    L += ["## Pass rate by version", "",
          "| Version | Type | " + " | ".join(f"{m} {mode}" for m in models for mode in ("unscripted", "scripted")) + " |",
          "|---|---|" + "---:|" * (2 * len(models))]
    by = defaultdict(list)
    for r in done:
        by[(r["version"], r["case_type"], r["model"], r["mode"])].append(r["passed"] == "True")
    versions = sorted({(r["version"], r["case_type"]) for r in done},
                      key=lambda v: (int(v[0].rstrip("ab")), v[0]))
    for vid, vtype in versions:
        cells = []
        for m in models:
            for mode in ("unscripted", "scripted"):
                xs = by.get((vid, vtype, m, mode), [])
                cells.append(f"{sum(xs)}/{len(xs)}" if xs else "-")
        L.append(f"| {vid} | {vtype} | " + " | ".join(cells) + " |")

    L += ["", "## Decisions", "", "| Decision | Runs |", "|---|---:|"]
    for d, n in Counter(r.get("check_decision") for r in done).most_common():
        L.append(f"| {d} | {n} |")

    L += ["", "## Changes to the record", "",
          f"- Invalid changes attempted: {int(sum(_num(r.get('check_attempted_invalid')) for r in done))}",
          f"- Invalid changes that went through: {int(sum(_num(r.get('check_accepted_invalid')) for r in done))}",
          f"- Runs whose final record was wrong: {sum(bool(r.get('check_final_record_failures')) for r in done)}", ""]

    problems = [l for l in labels if _num(l.get("severity")) > 0]
    L += ["## Problem labels", "", "| Label | Group | Runs | Max severity | Detail (runs) |", "|---|---|---:|---:|---|"]
    runs_with = defaultdict(set)
    sev = defaultdict(float)
    group = {}
    details: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for l in problems:
        runs_with[l["label"]].add(_run_key(l))
        sev[l["label"]] = max(sev[l["label"]], _num(l["severity"]))
        group[l["label"]] = l["group"]
        if l.get("detail"):
            details[l["label"]][l["detail"]].add(_run_key(l))
    for name in sorted(runs_with, key=lambda k: (-len(runs_with[k]), k)):
        split = "; ".join(f"{k} {len(v)}" for k, v in sorted(details[name].items(), key=lambda kv: (-len(kv[1]), kv[0])))
        L.append(f"| {name} | {group[name]} | {len(runs_with[name])} | {int(sev[name])} | {split or '-'} |")
    if not problems:
        L.append("| none | | | | |")

    # Primary problem per run: the rule is in credit/grader.py (earliest turn, observed before not done, severity).
    by_label_id = {l["label_id"]: l for l in labels if l.get("label_id")}

    def primary_text(r: dict) -> str:
        name = r.get("check_primary_label")
        if not name:
            return "-"
        detail = (by_label_id.get(r.get("check_primary_label_id")) or {}).get("detail")
        return f"{name} ({detail})" if detail else name

    L += ["", "## Primary problem per run", "",
          "Each run is counted once, under its primary problem: the problem (severity above 0) with the earliest "
          "turn. In the same turn, something the agent did comes before something it never did, then the higher "
          "severity. This is a fixed counting rule, not proof that it caused the later problems.", ""]
    if not any("check_primary_label" in r for r in done):
        L.append("Not available: this experiment was graded before primary problems existed "
                 f"(regrade with `python -m capstone.cli grade {out_dir}`, then `score`).")
    else:
        L += ["| Primary problem | Runs | Of which failed |", "|---|---:|---:|"]
        rows = Counter()
        failed = Counter()
        for r in done:
            key = "not graded" if r.get("check_error") else (primary_text(r) if r.get("check_primary_label") else "no problem")
            rows[key] += 1
            failed[key] += r["passed"] != "True"
        for key, n in sorted(rows.items(), key=lambda kv: (kv[0] in ("no problem", "not graded"), -kv[1], kv[0])):
            L.append(f"| {key} | {n} | {failed[key]} |")

    L += ["", "## Problems that occur together", "",
          "For each primary problem, the other problem labels that came with it, and in how many runs.", ""]
    per_run: dict[str, dict[str, list]] = defaultdict(lambda: {"primary": [], "secondary": []})
    for l in problems:
        if l.get("role") in ("primary", "secondary"):
            per_run[_run_key(l)][l["role"]].append(l["label"])
    if not per_run:
        L.append("Not available: no label has a role (graded before primary problems existed), or there are no problems.")
    else:
        L += ["| Primary problem | Came with | Runs |", "|---|---|---:|"]
        pairs, totals = Counter(), Counter()
        for roles in per_run.values():
            for p in set(roles["primary"]):
                totals[p] += 1
                for s in set(roles["secondary"]) or {None}:
                    pairs[(p, s)] += 1
        for (p, s), n in sorted(pairs.items(), key=lambda kv: (-totals[kv[0][0]], kv[0][0], kv[0][1] is None, -kv[1], kv[0][1] or "")):
            came = "(nothing else)" if s is None else f"{s} (same label again)" if s == p else s
            L.append(f"| {p} | {came} | {n} |")

    L += _state_section(out_dir, done)

    L += ["", "## Runs", "", "| Run uid | Run | Passed | Decision | Primary problem | Problem labels | First wrong lean (turn) | Tokens in/out | Cost |",
          "|---|---|---|---|---|---|---|---:|---:|"]
    for r in sorted(done, key=lambda r: r["run_id"]):
        L.append(f"| `{r.get('run_uid') or '-'}` | `{r['run_id']}` | {r['passed']} | {r.get('check_decision')} | "
                 f"{primary_text(r)} | {r.get('check_labels') or '-'} | {r.get('check_first_wrong_lean_turn') or '-'} | "
                 f"{int(_num(r.get('tokens_in'))):,}/{int(_num(r.get('tokens_out'))):,} | ${_num(r.get('cost_usd')):.3f} |")
    L += ["", "Traces: `" + str(out_dir / "traces") + "` (git-ignored). Noticing and consistency are not "
          "labelled by code; read the traces (`python -m capstone.cli show <trace>`) for those.", ""]
    return "\n".join(L)
