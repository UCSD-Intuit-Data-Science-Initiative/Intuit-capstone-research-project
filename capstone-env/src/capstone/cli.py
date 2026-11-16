"""Command line for the harness.

  python -m capstone.cli run   --cases cases/ --model claude --k 4 --out results/exp1
  python -m capstone.cli score results/exp1          # regrade saved traces, rewrite the tables
  python -m capstone.cli show  results/exp1/traces/<run_id>.json
  python -m capstone.cli chat  --case cases/<file>.yaml --model claude   # type customer turns by hand
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import yaml

from .agent import DEFAULT_MAX_TOOL_CALLS
from .env import make_env
from .models import DEFAULT_MODELS_CONFIG, load_model_configs, make_model
from .player import MODES, Conversation, load_case_file, load_cases
from .runner import (DEFAULT_CHECKER, DEFAULT_GROUP_BY, Settings, format_table, plan_runs,
                     run_experiment, score_experiment, trace_path)
from .trace import Trace, load_trace


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 3] + "..."


def _indent(text: str) -> str:
    return text.replace("\n", "\n" + " " * 12)


def format_events(events: list[dict], width: int = 300) -> list[str]:
    lines, turn = [], None
    for e in events:
        if e.get("turn") != turn:
            turn = e.get("turn")
            lines.append(f"--- turn {turn} " + "-" * 40)
        if e["type"] == "fact_change":
            note = f"  ({e['note']})" if e.get("note") else ""
            lines.append(f"  FACT      {e['path']}: {e['old']!r} -> {e['new']!r}{note}")
        elif e["type"] == "customer":
            lines.append(f"CUSTOMER    {_indent(e['content'])}")
        elif e["type"] == "agent":
            u = e["usage"]
            ttft = f", ttft {u['ttft_s']:.2f}s" if u.get("ttft_s") is not None else ""
            stats = f"[{u['latency_s']:.2f}s{ttft}, {u['tokens_in']}->{u['tokens_out']} tok, stop={e['stop_reason']}]"
            lines.append(f"AGENT       {_indent(e['text'])}  {stats}" if e["text"] else f"AGENT       {stats}")
        elif e["type"] == "tool":
            args = e["raw_args"] if e.get("raw_args") is not None else json.dumps(e["args"])
            flag = "" if e["executed"] else "  [NOT EXECUTED]"
            decision = f"  => decision: {e['decision']}" if e.get("decision") else ""
            lines.append(f"  TOOL      {e['name']}({args}){flag}{decision}")
            lines.append(f"            -> {_clip(e['result'], width)}")
        elif e["type"] == "step_cap":
            lines.append(f"  STEP CAP  limit of {e['limit']} tool calls reached; turn ended")
        elif e["type"] in ("analyst", "reaction"):
            label = "ANALYST" if e["type"] == "analyst" else "REACTION"
            lines.append(f"{label:<11} {_indent(_clip(e['content'], width * 4) if e['type'] == 'analyst' else e['content'])}")
        elif e["type"] in ("record_write", "event_log", "calculation", "recommendation", "evidence_request",
                           "final_action", "tool_call"):
            ok = "accepted" if e.get("accepted") else f"REJECTED: {e.get('reason')}"
            detail = {k: v for k, v in e.items() if k not in ("i", "type", "t", "turn", "tool_call_id",
                                                               "accepted", "reason", "record_version")}
            lines.append(f"  RECORD    {e['type']} v{e.get('record_version')} {ok}  {_clip(json.dumps(detail), width)}")
        elif e["type"] == "stop":
            lines.append(f"STOP        {e['reason']}  (final action: {e.get('final_action')}, record v{e.get('record_version')})")
    return lines


def format_trace(t: dict, width: int = 300) -> str:
    m = t["model"]
    lines = [f"{t['run_id']}  [{t['status']}]",
             f"case {t.get('case_id')} / {t.get('case_version')}  mode={t.get('mode')}  "
             f"model={m['name']} ({m.get('model') or '-'})  repeat={t.get('repeat')}"]
    lines += format_events(t["events"], width)
    s = t.get("summary") or {}
    cost = "-" if s.get("cost_usd") is None else f"${s['cost_usd']:.4f}"
    lines.append("=" * 50)
    lines.append(f"final action: {s.get('final_action')}  (expected: {t.get('expected_outcome')})  "
                 f"tokens {s.get('tokens_in')}/{s.get('tokens_out')}  cost {cost}")
    if t.get("error"):
        lines.append(t["error"])
    return "\n".join(lines)


def print_summary(summary: list[dict], k: int, group_by: tuple[str, ...]) -> None:
    passes = ["pass^1"] + ([f"pass^{k}"] if k > 1 else [])
    cols = [*group_by, "n_tasks", "n_runs", "n_errors", *passes, "mean_cost_usd"]
    print(format_table(summary, cols))


def cmd_run(args: argparse.Namespace) -> int:
    configs = load_model_configs(args.models_config)
    try:
        for name in args.model:
            make_model(name, configs)  # fail now on a blank model id or missing key, not inside every run
    except (KeyError, ValueError) as exc:
        sys.exit(f"error: {exc.args[0]}")
    cases = load_cases(args.cases)
    if args.only:
        cases = [c for c in cases if c.case_id in args.only]
    if not cases:
        sys.exit("no cases found")
    for env_name in sorted({c.env for c in cases}):
        make_env(env_name).close()
    modes = args.mode or list(MODES)
    specs = plan_runs(cases, modes, args.model, args.k)
    print(f"{len(cases)} case versions x {len(modes)} modes x {len(args.model)} models x {args.k} repeats "
          f"= {len(specs)} runs -> {args.out}")
    if args.dry_run:
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    settings = Settings(max_tool_calls=args.max_tool_calls, workers=args.workers, retries=args.retries)
    (out / "experiment.json").write_text(json.dumps({
        "command": sys.argv, "started_at": datetime.now().isoformat(timespec="seconds"),
        "cases": [f"{c.case_id}/{c.version} ({c.source} {c.case_hash})" for c in cases],
        "modes": modes, "models": {n: configs[n] for n in args.model}, "k": args.k,
        "settings": vars(settings), "checker": args.checker,
    }, indent=2), encoding="utf-8")

    counts = run_experiment(specs, configs, out, settings)
    _, summary = score_experiment(out, args.checker, k=args.k)
    print()
    print_summary(summary, args.k, DEFAULT_GROUP_BY)
    print(f"\nruns: {out / 'runs.csv'}\nsummary: {out / 'summary.csv'}\ntraces: {out / 'traces'}")
    if args.langfuse:
        _send_to_langfuse([trace_path(out, s) for s in specs if trace_path(out, s).exists()])
    if counts["error"]:
        print(f"{counts['error']} runs errored; rerun the same command to retry them")
    return 1 if counts["error"] else 0


def _send_to_langfuse(paths: list) -> int:
    """Copy saved traces to Langfuse; the trace files stay the official record."""
    from .observe import ObserveError, export_runs
    try:
        export_runs(paths)
    except ObserveError as exc:
        print(f"langfuse: not sent ({exc})")
        return 1
    return 0


def cmd_langfuse(args: argparse.Namespace) -> int:
    return _send_to_langfuse(args.paths)


def cmd_run_families(args: argparse.Namespace) -> int:
    from .credit.player import MODES as FAMILY_MODES, find_families, plan_family_runs, run_family_one
    configs = load_model_configs(args.models_config)
    try:
        for name in args.model:
            make_model(name, configs)
    except (KeyError, ValueError) as exc:
        sys.exit(f"error: {exc.args[0]}")
    paths = find_families(args.families)
    if not paths:
        sys.exit("no family.json files found")
    modes = args.mode or list(FAMILY_MODES)
    specs = plan_family_runs(paths, args.versions, modes, args.model, args.k)
    print(f"{len(paths)} families x {len(args.versions) if args.versions else 'all'} versions x {len(modes)} modes "
          f"x {len(args.model)} models x {args.k} repeats = {len(specs)} runs -> {args.out}")
    if args.dry_run:
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    settings = Settings(max_tool_calls=args.max_tool_calls, workers=args.workers, retries=args.retries)
    (out / "experiment.json").write_text(json.dumps({
        "command": sys.argv, "started_at": datetime.now().isoformat(timespec="seconds"),
        "families": [str(p) for p in paths], "versions": args.versions, "modes": modes,
        "models": {n: configs[n] for n in args.model}, "k": args.k, "settings": vars(settings),
        "checker": args.checker,
    }, indent=2), encoding="utf-8")
    counts = run_experiment(specs, configs, out, settings, run_fn=run_family_one)
    _, summary = score_experiment(out, _checker(args), k=args.k)
    from .credit.grader import grade_experiment
    grades, skipped = grade_experiment(out, allow_family_change=args.allow_family_change)
    print()
    print_summary(summary, args.k, DEFAULT_GROUP_BY)
    print(f"\nruns: {out / 'runs.csv'}\nsummary: {out / 'summary.csv'}\ntraces: {out / 'traces'}")
    print(f"labels: {out / 'labels.csv'} ({sum(len(g['labels']) for g in grades)} labels, {len(grades)} runs)")
    _print_skipped(skipped)
    if args.langfuse:
        _send_to_langfuse([trace_path(out, s) for s in specs if trace_path(out, s).exists()])
    if counts["error"]:
        print(f"{counts['error']} runs errored; rerun the same command to retry them")
    return 1 if counts["error"] else 0


CREDIT_CHECKER = "capstone.credit.grader:check"
ALLOW_HELP = ("grade runs whose family.json changed since they ran, against the current file "
              "(results are marked family_changed); by default such runs are not graded")


def _checker(args: argparse.Namespace) -> str:
    """The checker to score with; --allow-family-change switches the credit grader to its override."""
    if getattr(args, "allow_family_change", False) and args.checker == CREDIT_CHECKER:
        return "capstone.credit.grader:check_allow_family_change"
    return args.checker


def _print_skipped(skipped: list[str]) -> None:
    for reason in skipped:
        print(f"SKIP  {reason}")
    if skipped:
        print(f"{len(skipped)} runs not graded: their family file changed since the run "
              f"(regrade with --allow-family-change to use the current file; results are marked family_changed)")


def cmd_grade(args: argparse.Namespace) -> int:
    from .credit.grader import grade_experiment
    grades, skipped = grade_experiment(args.out, allow_family_change=args.allow_family_change)
    for g in grades:
        bad = sorted({l["label"] for l in g["labels"] if l["severity"] > 0})
        changed = "  [family changed]" if g["family_changed"] else ""
        print(f"{'PASS' if g['passed'] else 'FAIL'}  {g['run_id']}  decision={g['decision']}  "
              f"invalid attempted/accepted={g['attempted_invalid_changes']}/{g['accepted_invalid_changes']}  "
              f"{', '.join(bad) or '-'}{changed}")
    print(f"labels: {Path(args.out) / 'labels.csv'}")
    _print_skipped(skipped)
    return 1 if skipped else 0


def cmd_score(args: argparse.Namespace) -> int:
    group_by = tuple(args.group_by.split(","))
    rows, summary = score_experiment(args.out, _checker(args), group_by, args.k)
    k = args.k or max((r["repeat"] + 1 for r in rows), default=1)
    print_summary(summary, k, group_by)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    for path in args.traces:
        print(format_trace(load_trace(path), args.width))
        print()
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    configs = load_model_configs(args.models_config)
    model = make_model(args.model, configs)
    case_data, env_name = {}, args.env
    if args.case:
        case = load_case_file(args.case)[0]
        case_data, env_name = case.case_data, args.env or case.env
    if not env_name:
        sys.exit("give --env or --case")
    env = make_env(env_name)
    run_id = f"chat__{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    trace = Trace(run_id=run_id, case_id=Path(args.case).stem if args.case else None, case_version=None,
                  case_type="chat", env=env_name, mode="chat", model={"name": args.model, **configs[args.model]},
                  settings={"max_tool_calls": args.max_tool_calls}, repeat=0)
    convo = Conversation(env, model, trace, args.max_tool_calls)
    convo.start(case_data)
    print("Type customer turns. '/fact <path> <value>' writes a fact change to the database; '/quit' ends.")
    status, error = "completed", None
    try:
        while True:
            line = input("customer> ").strip()
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line.startswith("/fact "):
                _, path, raw = line.split(maxsplit=2)
                convo.apply_fact(path, yaml.safe_load(raw))
                print(f"  fact set: {path} = {yaml.safe_load(raw)!r}")
                continue
            start = len(trace.events)
            convo.send(line)
            print("\n".join(format_events(trace.events[start:])))
    except (EOFError, KeyboardInterrupt):
        pass
    except Exception:
        status, error = "error", traceback.format_exc()
        raise
    finally:
        convo.finish()
        trace.finish(status, error)
        path = Path(args.out) / f"{run_id}.json"
        trace.save(path)
        print(f"\ntrace saved to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    p = argparse.ArgumentParser(prog="capstone.cli")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run cases x modes x models x k repeats, then score")
    r.add_argument("--cases", nargs="+", default=["cases"], help="case files or directories")
    r.add_argument("--model", action="append", required=True, help="name from the models config (repeatable)")
    r.add_argument("--mode", action="append", choices=MODES, help="default: both modes")
    r.add_argument("--k", type=int, default=4, help="repeats per case, version, mode and model")
    r.add_argument("--out", default="results/default", help="experiment folder; rerun to resume")
    r.add_argument("--only", nargs="+", help="only these case ids")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--retries", type=int, default=2, help="extra attempts for a run that errors")
    r.add_argument("--max-tool-calls", type=int, default=DEFAULT_MAX_TOOL_CALLS, help="per agent turn")
    r.add_argument("--models-config", default=DEFAULT_MODELS_CONFIG)
    r.add_argument("--checker", default=DEFAULT_CHECKER, help="module:function returning a dict with 'passed'")
    r.add_argument("--dry-run", action="store_true", help="print the plan and stop")
    r.add_argument("--langfuse", action="store_true",
                   help="also copy the finished traces to Langfuse for browsing (needs LANGFUSE_* keys)")
    r.set_defaults(func=cmd_run)

    rf = sub.add_parser("run-families", help="run credit families x versions x modes x models x k, then score")
    rf.add_argument("--families", nargs="+", default=["data/families"], help="family.json files or folders")
    rf.add_argument("--versions", nargs="+", help="only these version ids (default: all 9)")
    rf.add_argument("--mode", action="append", choices=("unscripted", "scripted"), help="default: both")
    rf.add_argument("--model", action="append", required=True, help="name from the models config (repeatable)")
    rf.add_argument("--k", type=int, default=1, help="repeats per family, version, mode and model")
    rf.add_argument("--out", default="results/families", help="experiment folder; rerun to resume")
    rf.add_argument("--workers", type=int, default=4)
    rf.add_argument("--retries", type=int, default=2)
    rf.add_argument("--max-tool-calls", type=int, default=DEFAULT_MAX_TOOL_CALLS, help="per agent turn")
    rf.add_argument("--models-config", default=DEFAULT_MODELS_CONFIG)
    rf.add_argument("--checker", default=CREDIT_CHECKER)
    rf.add_argument("--dry-run", action="store_true")
    rf.add_argument("--langfuse", action="store_true", help="also copy the finished traces to Langfuse")
    rf.add_argument("--allow-family-change", action="store_true", help=ALLOW_HELP)
    rf.set_defaults(func=cmd_run_families)

    g = sub.add_parser("grade", help="label saved credit runs (labels/<run>.json and labels.csv)")
    g.add_argument("out")
    g.add_argument("--allow-family-change", action="store_true", help=ALLOW_HELP)
    g.set_defaults(func=cmd_grade)

    lf = sub.add_parser("langfuse", help="copy saved traces to Langfuse for browsing")
    lf.add_argument("paths", nargs="+", help="experiment folders, trace folders or trace files")
    lf.set_defaults(func=cmd_langfuse)

    s = sub.add_parser("score", help="regrade saved traces and rewrite runs.csv / summary.csv")
    s.add_argument("out")
    s.add_argument("--checker", default=DEFAULT_CHECKER)
    s.add_argument("--allow-family-change", action="store_true", help=ALLOW_HELP + " (credit grader only)")
    s.add_argument("--k", type=int)
    s.add_argument("--group-by", default=",".join(DEFAULT_GROUP_BY))
    s.set_defaults(func=cmd_score)

    sh = sub.add_parser("show", help="print traces for reading by hand")
    sh.add_argument("traces", nargs="+")
    sh.add_argument("--width", type=int, default=300, help="max characters of each tool result")
    sh.set_defaults(func=cmd_show)

    c = sub.add_parser("chat", help="talk to the agent yourself, one turn at a time")
    c.add_argument("--model", required=True)
    c.add_argument("--case", help="case file whose case_data and env to load")
    c.add_argument("--env", help="environment name (default: the case's)")
    c.add_argument("--out", default="results/chat")
    c.add_argument("--max-tool-calls", type=int, default=DEFAULT_MAX_TOOL_CALLS)
    c.add_argument("--models-config", default=DEFAULT_MODELS_CONFIG)
    c.set_defaults(func=cmd_chat)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
