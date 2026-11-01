"""Stage 2: profile FinQA / ConvFinQA and compare against the Nov 1 numbers.

Usage (inside Docker, from the repo root):
    python -m capstone.data.profile

Steps:
  1. Verify data/raw against data/checksums.sha256 (refuses to profile altered data).
  2. Compute every field of data/profile_expected.yaml with our own code (definitions:
     docs/reference/profile_nov1.py, re-implemented in program.py / cells.py).
  3. Compute extra pipeline fields (traceability counting number-selection turns as inputs).
  4. Write data/interim/profile/{profile.json, finqa_examples.csv, conv_conversations.csv,
     conv_turns.csv} and docs/profile_report.md. Exit 1 if any expected field differs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from capstone.data import fetch
from capstone.data.cells import trace
from capstone.data.program import ProgramError, answers_match, execute, literals, parse

FINQA_LABELLED = ("train", "dev", "test")
FINQA_UNLABELLED = ("private_test",)
CONV_LABELLED = ("train", "dev")
CONV_UNLABELLED = ("test_private",)

Log = Callable[[str], None]


# ---------------------------------------------------------------- FinQA


def evidence_location(gold_inds: dict) -> str:
    keys = list(gold_inds)
    if all(k.startswith("table") for k in keys):
        return "table"
    if all(k.startswith("text") for k in keys):
        return "text"
    return "both"


def failure_cause(prog, table) -> str:
    """Why a program may not re-execute: known table-op quirks first, else 'other'."""
    for s in prog.steps:
        if not s.is_table_op:
            continue
        rows = [r for r in table if r and r[0].strip().lower() == s.args[0].lower()]
        if len(rows) > 1:
            return "duplicate_row_name"  # we (and Nov 1) take the first matching row
        if rows and any("%" in c for c in rows[0][1:]):
            return "percent_cells"  # dataset reads '26.2%' as 0.262; first_number gives 26.2
    return "other"


def profile_finqa(splits: dict[str, list[dict]]) -> tuple[dict, list[dict]]:
    labelled = [(s, e) for s in FINQA_LABELLED for e in splits[s]]
    ops, steps, evidence = Counter(), Counter(), Counter()
    trace_base = trace_found = trace_one = reexec = 0
    rows = []
    for split, e in labelled:
        company, year = e["id"].split("/")[:2]
        qa = e["qa"]
        row = {"id": e["id"], "split": split, "company": company, "year": year,
               "program": qa["program"], "gold": qa["exe_ans"]}
        try:
            prog = parse(qa["program"])
        except ProgramError as exc:
            row.update(parse_error=str(exc))
            rows.append(row)
            continue
        ops.update(s.op for s in prog.steps)
        steps[len(prog.steps)] += 1
        row["n_steps"] = len(prog.steps)
        try:
            ours = execute(prog, e["table"], percent_rule="nov1")
            row["ours"] = ours
            row["reexec_ok"] = answers_match(ours, qa["exe_ans"])
            row["reexec_ok_dataset_rule"] = answers_match(
                execute(prog, e["table"], percent_rule="dataset"), qa["exe_ans"])
        except ProgramError as exc:
            row.update(reexec_ok=False, reexec_ok_dataset_rule=False, exec_error=str(exc))
        if not row["reexec_ok"]:
            row["failure_cause"] = failure_cause(prog, e["table"])
        reexec += row["reexec_ok"]
        row["evidence"] = evidence_location(qa["gold_inds"])
        evidence[row["evidence"]] += 1
        t = trace(literals(prog), e["table"])
        if t is not None:
            trace_base += 1
            trace_found += t.all_found
            trace_one += t.exactly_one
            row.update(n_literals=t.n_literals, trace_all_found=t.all_found,
                       trace_exactly_one=t.exactly_one)
        rows.append(row)

    years = sorted({r["year"] for r in rows})
    summary = {
        "finqa_split_counts": {s: len(splits[s]) for s in FINQA_LABELLED + FINQA_UNLABELLED},
        "finqa_labelled": len(labelled),
        "finqa_companies": len({r["company"] for r in rows}),
        "finqa_years": f"{years[0]}-{years[-1]}",
        "finqa_ops": dict(ops.most_common()),
        "finqa_steps": dict(sorted(steps.items())),
        "finqa_reexec_match": reexec,
        "finqa_evidence": dict(evidence),
        "finqa_trace": {"base": trace_base, "all_found": trace_found, "exactly_one": trace_one},
    }
    return summary, rows


# ---------------------------------------------------------------- ConvFinQA


@dataclass
class _TraceCount:
    base: int = 0
    all_found: int = 0
    exactly_one: int = 0

    def add(self, t) -> None:
        if t is not None:
            self.base += 1
            self.all_found += t.all_found
            self.exactly_one += t.exactly_one

    def as_dict(self) -> dict:
        return {"base": self.base, "all_found": self.all_found, "exactly_one": self.exactly_one}


def profile_convfinqa(splits: dict[str, list[dict]]) -> tuple[dict, dict, list[dict], list[dict]]:
    convs = [(s, e) for s in CONV_LABELLED for e in splits[s]]
    types, turns_per_conv = Counter(), Counter()
    n_turns = reexec = selection = multistep = 0
    pool = pool_sel = length_mismatch = sel_unused = 0
    tc, tc_sel = _TraceCount(), _TraceCount()
    conv_rows, turn_rows = [], []

    for split, e in convs:
        ann, table = e["annotation"], e["table"]
        types["II" if "qa_0" in e else "I"] += 1
        turns_per_conv[len(ann["dialogue_break"])] += 1
        tp, answers = ann["turn_program"], ann["exe_ans_list"]
        if not (len(tp) == len(answers) == len(ann["dialogue_break"])):
            length_mismatch += 1
        progs = []
        for k, (text, gold) in enumerate(zip(tp, answers)):
            n_turns += 1
            row = {"id": e["id"], "turn": k, "program": text, "gold": gold}
            try:
                p = parse(text)
                ours = execute(p, table, percent_rule="nov1")
                row.update(ours=ours, reexec_ok=answers_match(ours, gold),
                           selection=p.is_selection, multistep=p.is_multistep)
                progs.append(p)
            except ProgramError as exc:
                row.update(reexec_ok=False, error=str(exc))
            reexec += row["reexec_ok"]
            selection += row.get("selection", False)
            multistep += row.get("multistep", False)
            turn_rows.append(row)

        parsed_all = len(progs) == len(tp)
        lits = [lit for p in progs for lit in literals(p)]
        lits_sel = [lit for p in progs for lit in literals(p, include_selection=True)]
        op_values = {abs(float(x.rstrip("%"))) for x in lits}
        sel_unused += any(abs(float(p.selection.rstrip("%"))) not in op_values
                          for p in progs if p.is_selection)
        t, t_sel = trace(lits, table), trace(lits_sel, table)
        tc.add(t)
        tc_sel.add(t_sel)
        later_has_op = parsed_all and any(not p.is_selection for p in progs[1:])
        in_pool = len(tp) >= 3 and t is not None and t.exactly_one and later_has_op
        in_pool_sel = len(tp) >= 3 and t_sel is not None and t_sel.exactly_one and later_has_op
        pool += in_pool
        pool_sel += in_pool_sel
        conv_rows.append({
            "id": e["id"], "split": split, "type": "II" if "qa_0" in e else "I",
            "n_turns": len(tp), "n_literals": len(lits), "n_literals_with_selection": len(lits_sel),
            "trace_exactly_one": bool(t and t.exactly_one),
            "trace_exactly_one_with_selection": bool(t_sel and t_sel.exactly_one),
            "pool": in_pool, "pool_with_selection": in_pool_sel,
        })

    summary = {
        "conv_split_counts": {s: len(splits[s]) for s in CONV_LABELLED + CONV_UNLABELLED},
        "conv_labelled": len(convs),
        "conv_types": dict(types),
        "conv_turns_per_conv": dict(sorted(turns_per_conv.items())),
        "conv_turns": n_turns,
        "conv_reexec_match": reexec,
        "conv_number_selection_turns": selection,
        "conv_multistep_turns(#)": multistep,
        "conv_trace": tc.as_dict(),
        "conv_pool": {"pool": pool, "of": len(convs)},
    }
    pipeline = {
        "conv_trace_with_selection": tc_sel.as_dict(),
        "conv_pool_with_selection": {"pool": pool_sel, "of": len(convs)},
        "conv_turn_length_mismatches": length_mismatch,
        # conversations where a selected number is never reused by an op turn
        "conv_selection_values_not_in_ops": sel_unused,
    }
    return summary, pipeline, conv_rows, turn_rows


# ---------------------------------------------------------------- comparison


def flatten(d: dict, prefix: str = "") -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out


def compare(ours: dict, expected: dict) -> list[dict]:
    """One row per expected or produced field; exact equality (counts and strings)."""
    o, x = flatten(ours), flatten(expected)
    rows = []
    for key in list(x) + [k for k in o if k not in x]:
        if key not in o:
            status = "missing"
        elif key not in x:
            status = "extra"
        else:
            status = "match" if o[key] == x[key] else "differs"
        rows.append({"field": key, "expected": x.get(key), "ours": o.get(key), "status": status})
    return rows


# ---------------------------------------------------------------- outputs


def _write_csv(path: Path, rows: list[dict]) -> None:
    fields: dict[str, None] = {}
    for r in rows:
        fields.update(dict.fromkeys(r))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields))
        w.writeheader()
        w.writerows(rows)


def _fmt(v: Any) -> str:
    return f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else str(v)


def render_report(result: dict) -> str:
    cmp_rows = result["comparison"]
    n_match = sum(r["status"] == "match" for r in cmp_rows)
    pl = result["pipeline"]
    fails = result["finqa_reexec_failures"]
    lines = [
        "# Stage 2 profile report",
        "",
        "Generated by `python -m capstone.data.profile` (do not edit by hand).",
        "",
        f"- Run: {result['run']['finished']}",
        f"- Data: `data/raw`, verified against `data/checksums.sha256` "
        f"(sha256 `{result['run']['checksums_sha256'][:12]}…`)",
        *[f"- {s['name']}: {s['repo']} @ `{s['commit'][:7]}`" for s in result["run"]["sources"]],
        "- Expected values: `data/profile_expected.yaml` (output of `docs/reference/profile_nov1.py`)",
        "",
        f"## Result: {n_match} / {len(cmp_rows)} fields match",
        "",
        "| Field | Expected | Ours | Status |",
        "|---|---:|---:|---|",
        *[f"| `{r['field']}` | {_fmt(r['expected'])} | {_fmt(r['ours'])} | "
          f"{'✅ match' if r['status'] == 'match' else '❌ ' + r['status']} |" for r in cmp_rows],
        "",
        "## Pipeline fields (not in the Nov 1 reference)",
        "",
        "The Nov 1 definition skips bare number-selection turns (e.g. `6305`) when tracing inputs,",
        "because they have no op. For our pipeline they ARE inputs the agent must read from the",
        "table, so we also count them. ConvFinQA usually re-writes a selected number into the next",
        "op turn (`60.94`, then `subtract(60.94, 25.14)`), so it is often already counted; "
        f"{pl['conv_selection_values_not_in_ops']} conversations select a number no op turn reuses.",
        "Counts that change: " + (", ".join(
            f"{k} {result['profile']['conv_trace'][k]:,} → {pl['conv_trace_with_selection'][k]:,}"
            for k in ("base", "all_found", "exactly_one")
            if result["profile"]["conv_trace"][k] != pl["conv_trace_with_selection"][k])
            + ("" if result["profile"]["conv_pool"]["pool"] == pl["conv_pool_with_selection"]["pool"]
               else f", pool {result['profile']['conv_pool']['pool']:,} → "
                    f"{pl['conv_pool_with_selection']['pool']:,}")
            or "none."),
        "",
        "| Field | Nov 1 definition | Counting selection turns |",
        "|---|---:|---:|",
        f"| ConvFinQA traceability base | {_fmt(result['profile']['conv_trace']['base'])} | "
        f"{_fmt(pl['conv_trace_with_selection']['base'])} |",
        f"| ...every input found in table | {_fmt(result['profile']['conv_trace']['all_found'])} | "
        f"{_fmt(pl['conv_trace_with_selection']['all_found'])} |",
        f"| ...every input in exactly one cell | {_fmt(result['profile']['conv_trace']['exactly_one'])} | "
        f"{_fmt(pl['conv_trace_with_selection']['exactly_one'])} |",
        f"| Starting pool (≥3 turns, exactly one cell, a later turn has an op) | "
        f"{_fmt(result['profile']['conv_pool']['pool'])} | {_fmt(pl['conv_pool_with_selection']['pool'])} |",
        "",
        f"Conversations whose `turn_program`, `exe_ans_list` and `dialogue_break` lengths differ: "
        f"{pl['conv_turn_length_mismatches']}.",
        "",
        f"## FinQA programs that do not re-execute ({len(fails)})",
        "",
        "All are table aggregates, which Stage 3 drops anyway. `gold` is the dataset's own `exe_ans`.",
        "Causes: " + ", ".join(f"{k} {v}" for k, v in pl["finqa_reexec_failure_causes"].items()) + ".",
        "",
        "- `percent_cells`: the row holds `%` cells. The dataset reads `26.2%` as 0.262; the Nov 1",
        "  cell rule (first number in the cell) reads 26.2, so ours is 100× gold. Under the Stage 3",
        f"  dataset percent rule, {pl['finqa_reexec_match_dataset_rule']:,} / "
        f"{result['profile']['finqa_labelled']:,} programs re-execute.",
        "- `duplicate_row_name`: the table has two rows with that name (e.g. a high and a low",
        "  price block); the rule takes the first, the dataset used the other.",
        "",
        "| id | program | gold | ours | cause |",
        "|---|---|---:|---:|---|",
        *[f"| `{f['id']}` | `{f['program']}` | {f['gold']} | "
          f"{f.get('ours', f.get('exec_error', f.get('parse_error')))} | {f.get('failure_cause', '')} |"
          for f in fails],
        "",
    ]
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(raw: Path, folder: str, names: Iterable[str], log: Log) -> dict[str, list[dict]]:
    out = {}
    for n in names:
        p = raw / folder / f"{n}.json"
        out[n] = json.loads(p.read_text())
        log(f"  loaded {p.relative_to(raw)}: {len(out[n]):,} records")
    return out


def run(root: Path, log: Log = print) -> int:
    paths = fetch.Paths(root)
    fetch.verify(paths, log=log)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fin = _load(paths.raw, "finqa/dataset", FINQA_LABELLED + FINQA_UNLABELLED, log)
    conv = _load(paths.raw, "convfinqa/data", CONV_LABELLED + CONV_UNLABELLED, log)

    fin_summary, fin_rows = profile_finqa(fin)
    conv_summary, pipeline, conv_rows, turn_rows = profile_convfinqa(conv)
    profile = {**fin_summary, **conv_summary}
    expected = yaml.safe_load((root / "data" / "profile_expected.yaml").read_text())
    comparison = compare(profile, expected)

    result = {
        "run": {
            "started": started,
            "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "checksums_sha256": _sha256(paths.lock),
            "sources": [{"name": s.name, "repo": s.repo, "commit": s.commit}
                        for s in fetch.load_sources(paths.sources)],
        },
        "profile": profile,
        "pipeline": pipeline,
        "comparison": comparison,
        "finqa_reexec_failures": [r for r in fin_rows if not r.get("reexec_ok")],
    }
    pipeline["finqa_reexec_failure_causes"] = dict(Counter(
        r.get("failure_cause", "parse_error") for r in result["finqa_reexec_failures"]))
    # Stage 3 percent rule ("26.2%" -> 0.262, as the dataset reads it)
    pipeline["finqa_reexec_match_dataset_rule"] = sum(bool(r.get("reexec_ok_dataset_rule"))
                                                      for r in fin_rows)
    out = root / "data" / "interim" / "profile"
    out.mkdir(parents=True, exist_ok=True)
    (out / "profile.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    _write_csv(out / "finqa_examples.csv", fin_rows)
    _write_csv(out / "conv_conversations.csv", conv_rows)
    _write_csv(out / "conv_turns.csv", turn_rows)
    report = root / "docs" / "profile_report.md"
    report.write_text(render_report(result))

    bad = [r for r in comparison if r["status"] != "match"]
    log(f"  wrote {out.relative_to(root)}/ (profile.json + 3 CSVs: "
        f"{len(fin_rows):,} FinQA rows, {len(conv_rows):,} conversations, {len(turn_rows):,} turns)")
    log(f"  wrote {report.relative_to(root)}")
    for r in bad:
        log(f"  DIFFERS {r['field']}: expected {r['expected']}, ours {r['ours']} ({r['status']})")
    log(f"{'OK' if not bad else 'MISMATCH'}: {len(comparison) - len(bad)}/{len(comparison)} fields match")
    return 0 if not bad else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    args = ap.parse_args(argv)
    try:
        return run(args.root)
    except fetch.FetchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
