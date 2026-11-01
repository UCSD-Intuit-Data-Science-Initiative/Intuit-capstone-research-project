"""Stage 3: select candidate ConvFinQA conversations for case families.

Usage (inside Docker, from the repo root):
    python -m capstone.data.select

Every labelled ConvFinQA conversation (train + dev) goes through FILTERS in order; the
first filter it fails is its drop reason, and every filter logs how many conversations it
received and passed. Held-out companies also get a CUT version of each conversation: the
conversation ends at its first plain subtract turn (turn 3 or later), giving a `difference`
metric. Cut versions run through the same FILTERS again (one funnel of their own).

Outputs:
    data/interim/stage3/candidates.csv   one row per conversation (and cut version)
    data/interim/stage3/selection.json   funnels, split counts, gate, answer agreement
    docs/selection_report.md             generated from selection.json
Exits 1 if the gate fails (see GATE): then the split must not be fixed.

Decisions behind the filters (Nov 1, see CLAUDE.md):
  A written answer vs exe_ans (answers.py)      B type I only
  C percent inputs left out                     D degenerate metrics dropped; thresholds are Stage 4
  E company split 30% held-out, seed 1101; pilot metrics growth_rate + ratio,
    held-out adds difference (cut conversations) as its unseen metric type.
    (Raised from 20%: the held-out difference gate failed at 20% under the strict unit rule.)
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from capstone.data import fetch
from capstone.data.answers import KEEP, classify
from capstone.data.cells import cell_number, cell_value, is_percent_cell, locate, trace
from capstone.data.program import (
    Program, ProgramError, answers_match, evaluate_steps, execute, is_number_literal,
    literals, parse,
)

SEED = 1101
HELDOUT_FRACTION = 0.30  # decided Nov 1 (was 0.20; see CLAUDE.md decision log)
METRICS = ("growth_rate", "ratio", "difference")
HELDOUT_ONLY_METRICS = ("difference",)
GATE = {
    "min_candidates": {"pilot": 30, "heldout": 30},
    "min_companies_per_metric": 10,
    "required_metrics": {"pilot": ("growth_rate", "ratio"), "heldout": ("growth_rate", "difference")},
}
PILOT_MIX_TARGET = {"growth_rate": 5, "ratio": 5}  # for Stage 4/5: 10 families, one per company
CUT_MIN_TURNS = 3  # a cut conversation keeps at least 3 turns (subtract at turn 3 or later)
MAX_ABS = {"growth_rate": 10.0, "ratio": 100.0}  # fraction scale: growth 10 = +1000%; ratio 100x
NEAR_ZERO = 1e-6  # |denominator| below this share of |numerator| counts as near zero
TOTAL_TOL_REL, TOTAL_TOL_ABS = 0.005, 1.0  # rounded tables: 0.5% or 1 unit
UNIT_RE = re.compile(r"\bin (thousands|millions|billions)\b")

Table = Sequence[Sequence[str]]


# ---------------------------------------------------------------- metric classification


@dataclass(frozen=True)
class Metric:
    type: str    # growth_rate | ratio | difference
    scale: str   # fraction | percent | amount
    numerator_inputs: tuple[str, ...] = ()    # growth_rate / ratio: literals feeding the dividend
    denominator_inputs: tuple[str, ...] = ()  # growth_rate / ratio: literals feeding the divisor
    minuend_inputs: tuple[str, ...] = ()      # difference: a in a - b
    subtrahend_inputs: tuple[str, ...] = ()   # difference: b in a - b
    divide_step: Optional[int] = None         # index of the division step (growth_rate / ratio)

    @property
    def inputs(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.numerator_inputs + self.denominator_inputs
                                   + self.minuend_inputs + self.subtrahend_inputs))

    def roles(self) -> dict[str, tuple[str, ...]]:
        if self.type == "difference":
            return {"minuend": self.minuend_inputs, "subtrahend": self.subtrahend_inputs}
        return {"numerator": self.numerator_inputs, "denominator": self.denominator_inputs}


def _step_deps(prog: Program) -> list[tuple[str, ...]]:
    """For each step, the number literals it depends on (directly or through '#n'), in order."""
    deps: list[tuple[str, ...]] = []
    for s in prog.steps:
        d: list[str] = []
        for a in s.args:
            if a.startswith("#"):
                d.extend(deps[int(a[1:])])
            elif is_number_literal(a):
                d.append(a)
        deps.append(tuple(dict.fromkeys(d)))
    return deps


def _arg_deps(arg: str, deps: list[tuple[str, ...]]) -> tuple[str, ...]:
    if arg.startswith("#"):
        return deps[int(arg[1:])]
    return (arg,) if is_number_literal(arg) else ()


def _same_number(a: str, b: str) -> bool:
    return is_number_literal(a) and is_number_literal(b) and \
        a.endswith("%") == b.endswith("%") and float(a.rstrip("%")) == float(b.rstrip("%"))


def classify_metric(prog: Program) -> Metric | None:
    """Recognise the policy-metric shapes (decided Nov 1); anything else returns None.

    growth_rate  subtract(a, b), divide(#0, b)   (a - b) / b
                 divide(a, b), subtract(#0, const_1)   a / b - 1
    ratio        last step divide; earlier steps only add/subtract; inputs are cells;
                 no constants; every earlier step feeds the result.
                 Covers a / b, a / (a + b), (a - b) / a, (a + b) / c ...
    difference   subtract(a, b)
    growth_rate and ratio may end with multiply(#k, const_100): same metric, percent scale.
    """
    steps = list(prog.steps)
    if not steps:
        return None
    percent = False
    if len(steps) >= 2 and steps[-1].op == "multiply" and \
            sorted(steps[-1].args) == sorted((f"#{len(steps) - 2}", "const_100")):
        percent, steps = True, steps[:-1]
    core = Program(text=prog.text, steps=tuple(steps))
    ops = [s.op for s in steps]
    scale = "percent" if percent else "fraction"
    lit = lambda *xs: all(is_number_literal(x) for x in xs)

    if ops == ["subtract", "divide"] and steps[1].args[0] == "#0" and lit(*steps[0].args) \
            and _same_number(steps[1].args[1], steps[0].args[1]):
        a, b = steps[0].args
        return Metric("growth_rate", scale, (a, b), (b,), divide_step=1)
    if ops == ["divide", "subtract"] and lit(*steps[0].args) and steps[1].args == ("#0", "const_1"):
        a, b = steps[0].args
        return Metric("growth_rate", scale, (a,), (b,), divide_step=0)
    if ops == ["subtract"] and lit(*steps[0].args) and not percent:
        a, b = steps[0].args
        return Metric("difference", "amount", minuend_inputs=(a,), subtrahend_inputs=(b,))

    args = [a for s in steps for a in s.args]
    if ops[-1] == "divide" and set(ops[:-1]) <= {"add", "subtract"} \
            and all(a.startswith("#") or is_number_literal(a) for a in args):
        referenced = {int(a[1:]) for a in args if a.startswith("#")}
        if referenced >= set(range(len(steps) - 1)):  # every earlier step is used
            deps = _step_deps(core)
            num, den = (_arg_deps(a, deps) for a in steps[-1].args)
            if num and den:
                return Metric("ratio", scale, num, den, divide_step=len(steps) - 1)
    return None


def metric_parts(prog: Program, m: Metric, table: Table) -> tuple[Optional[float], Optional[float]]:
    """(numerator value, denominator value) of the division step; (None, None) for difference."""
    if m.divide_step is None:
        return None, None
    results = evaluate_steps(prog, table)
    vals = [results[int(a[1:])] if a.startswith("#") else float(execute(a))
            for a in prog.steps[m.divide_step].args]
    return vals[0], vals[1]


# ---------------------------------------------------------------- table checks


def _is_total(label: str) -> bool:
    return "total" in label.strip().lower()


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(TOTAL_TOL_ABS, TOTAL_TOL_REL * abs(b))


def _cell(table: Table, i: int, j: int) -> str:
    return table[i][j] if 0 <= i < len(table) and 0 <= j < len(table[i]) else ""


def total_problem(table: Table, i: int, j: int) -> str | None:
    """Would editing body cell (i, j) leave a 'total' inconsistent?

    Heuristic (documented limit: only rows/columns labelled 'total' are recognised):
      input_is_total     the cell's row label or column header says 'total'
      input_feeds_total  a 'total' row below it equals the sum of its block (rows since the
                         previous total), or a 'total' column equals the sum of its row.
    """
    header = table[0]
    if _is_total(table[i][0]) or (j < len(header) and _is_total(header[j])):
        return "input_is_total"
    start = 1
    for t in range(1, len(table)):
        if not table[t] or not _is_total(table[t][0]):
            continue
        if start <= i < t:
            vals = [cell_value(_cell(table, r, j)) for r in range(start, t)]
            vals = [v for v in vals if v is not None]
            tot = cell_value(_cell(table, t, j))
            if tot is not None and len(vals) >= 2 and _close(sum(vals), tot):
                return "input_feeds_total"
        start = t + 1
    for c in range(1, len(header)):
        if c == j or not _is_total(header[c]):
            continue
        tot = cell_value(_cell(table, i, c))
        vals = [cell_value(_cell(table, i, k)) for k in range(1, len(table[i]))
                if k != c and not (k < len(header) and _is_total(header[k]))]
        vals = [v for v in vals if v is not None]
        if tot is not None and len(vals) >= 2 and _close(sum(vals), tot):
            return "input_feeds_total"
    return None


def irrelevant_cells(table: Table, input_cells: set[tuple[int, int]],
                     input_values: set[float]) -> list[tuple[int, int]]:
    """Body cells safe to edit for version 4 (irrelevant change): numeric, not a percent,
    not an input, not equal to an input's value, and not part of a total."""
    out = []
    for i in range(1, len(table)):
        for j in range(1, len(table[i])):
            n = cell_number(table[i][j])
            if n is None or n.is_percent or (i, j) in input_cells or abs(n.shown) in input_values:
                continue
            if total_problem(table, i, j) is None:
                out.append((i, j))
    return out


def page_unit(e: dict) -> str | tuple[str, ...] | None:
    """The single 'in thousands/millions/billions' the page states; a tuple if several; None if none."""
    header = list(e["table"][0]) if e["table"] else []
    text = " ".join(e.get("pre_text", []) + e.get("post_text", []) + header).lower()
    found = tuple(sorted(set(UNIT_RE.findall(text))))
    return found[0] if len(found) == 1 else (found or None)


# ---------------------------------------------------------------- per-conversation context


def company_of(conv_id: str) -> str:
    """'Single_AON/2009/page_46.pdf-3' -> 'AON'."""
    return conv_id.split("_", 1)[1].split("/")[0]


@dataclass
class Conv:
    source_split: str
    e: dict
    split: str = ""
    truncated_at: Optional[int] = None  # cut versions: number of turns kept
    progs: list[Program] = field(default_factory=list)
    row: dict = field(default_factory=dict)
    metric: Optional[Metric] = None
    input_cells: dict[str, tuple[int, int]] = field(default_factory=dict)  # every literal -> cell

    @property
    def table(self) -> Table:
        return self.e["table"]

    @property
    def turns(self) -> list[str]:
        return self.e["annotation"]["turn_program"]


Drop = Optional[tuple[str, str]]  # (reason, detail)


def f_reexec(c: Conv) -> Drop:
    for k, (text, gold) in enumerate(zip(c.turns, c.e["annotation"]["exe_ans_list"])):
        try:
            p = parse(text)
            ours = execute(p, c.table, percent_rule="dataset")
        except ProgramError as exc:
            return "program_error", f"turn {k}: {exc}"
        if not answers_match(ours, gold):
            return "reexec_mismatch", f"turn {k}: ours {ours} vs {gold}"
        c.progs.append(p)
    return None


def f_type_i(c: Conv) -> Drop:
    return ("type_ii", "two questions joined") if "qa_0" in c.e else None


def f_no_table_ops(c: Conv) -> Drop:
    for k, p in enumerate(c.progs):
        if any(s.is_table_op for s in p.steps):
            return "table_op", f"turn {k}: {p.text}"
    return None


def f_written_answer(c: Conv) -> Drop:
    """Label quality of the SOURCE question (cut versions inherit it)."""
    qa = c.e["qa"]
    cat = classify(qa["answer"], qa["exe_ans"])
    c.row.update(written_answer=qa["answer"], written_category=cat)
    return None if cat in KEEP else (f"written_answer_{cat}", f"{qa['answer']!r} vs exe_ans {qa['exe_ans']}")


def f_shape(c: Conv) -> Drop:
    if len(c.progs) < 3:
        return "too_few_turns", f"{len(c.progs)} turns"
    if not any(not p.is_selection for p in c.progs[1:]):
        return "no_op_after_first_turn", ""
    return None


def f_trace(c: Conv) -> Drop:
    lits = [x for p in c.progs for x in literals(p, include_selection=True)]
    t = trace(lits, c.table)
    if t is None:
        return "no_inputs_or_table", ""
    if not t.all_found:
        return "input_not_in_table", ""
    if not t.exactly_one:
        return "input_in_several_cells", ""
    c.input_cells = {x: locate(x, c.table)[0] for x in lits}
    return None


def f_no_percent(c: Conv) -> Drop:
    for x, (i, j) in c.input_cells.items():
        if x.endswith("%") or is_percent_cell(c.table, i, j):
            return "percent_input", f"{x} at {c.table[i][j]!r}"
    return None


def f_metric(c: Conv) -> Drop:
    c.metric = classify_metric(c.progs[-1])
    return None if c.metric else ("metric_other", c.progs[-1].text)


def f_scale(c: Conv) -> Drop:
    # "17.4%" written while exe_ans is 17.4 means the source program returns percent points;
    # a full (uncut) program without x100 must then be on the fraction scale -> contradiction.
    if c.truncated_at is None and c.row.get("written_category") == "agree_percent_format" \
            and c.metric.scale == "fraction":
        return "scale_conflict", "written in percent points, program gives a fraction"
    return None


def f_not_degenerate(c: Conv) -> Drop:
    m, prog = c.metric, c.progs[-1]
    value = execute(prog, c.table)
    c.row["metric_value"] = value
    num, den = metric_parts(prog, m, c.table)
    if den is not None:
        if den == 0:
            return "denominator_zero", ""
        if abs(den) < NEAR_ZERO * max(1.0, abs(num)):
            return "denominator_near_zero", f"{den} vs {num}"
    if value == 0:
        return "metric_zero", ""
    frac = value / 100 if m.scale == "percent" else value
    if m.type in MAX_ABS and abs(frac) > MAX_ABS[m.type]:
        return "absurd_magnitude", f"{frac:.4g} (cap {MAX_ABS[m.type]})"
    return None


def f_unit(c: Conv) -> Drop:
    unit = page_unit(c.e)
    c.row["unit"] = unit if isinstance(unit, str) else ("|".join(unit) if unit else "")
    if c.metric.type != "difference":
        return None  # growth rates and ratios are unitless
    if unit is None:
        return "unit_unknown", ""
    if isinstance(unit, tuple):
        return "unit_ambiguous", "|".join(unit)
    return None


def f_unique_rows(c: Conv) -> Drop:
    labels = [r[0].strip().lower() for r in c.table[1:] if r and r[0].strip()]
    dup = sorted(k for k, n in Counter(labels).items() if n > 1)
    return ("duplicate_row_name", "; ".join(dup)) if dup else None


def f_inputs_not_totals(c: Conv) -> Drop:
    for x in c.metric.inputs:
        i, j = c.input_cells[x]
        problem = total_problem(c.table, i, j)
        if problem:
            return problem, f"{x} at row {c.table[i][0]!r}"
    return None


def f_irrelevant_cell(c: Conv) -> Drop:
    values = {abs(float(x.rstrip("%"))) for x in c.input_cells}
    cells = irrelevant_cells(c.table, set(c.input_cells.values()), values)
    c.row["n_irrelevant_cells"] = len(cells)
    return None if cells else ("no_irrelevant_cell", "")


def f_split_metric(c: Conv) -> Drop:
    if c.split == "pilot" and c.metric.type in HELDOUT_ONLY_METRICS:
        return "difference_reserved_for_heldout", ""
    return None


FILTERS: list[tuple[str, str, Callable[[Conv], Drop]]] = [
    ("reexec", "every turn re-executes to exe_ans (dataset percent rule)", f_reexec),
    ("type_i", "type I conversation (one question split into turns)", f_type_i),
    ("no_table_ops", "no table aggregate ops in any turn", f_no_table_ops),
    ("written_answer", "source written answer agrees with exe_ans (formatting allowed)", f_written_answer),
    ("shape", ">= 3 turns and a later turn has an op", f_shape),
    ("trace", "every input, incl. selection turns, is in exactly one body cell", f_trace),
    ("no_percent_inputs", "no percent inputs (rule C)", f_no_percent),
    ("metric_type", "final turn is a growth rate, ratio or difference", f_metric),
    ("scale", "metric scale agrees with the written answer", f_scale),
    ("not_degenerate", "no zero/near-zero denominator, zero metric or absurd magnitude", f_not_degenerate),
    ("unit", "difference metrics state exactly one unit", f_unit),
    ("unique_rows", "no row name appears twice", f_unique_rows),
    ("inputs_not_totals", "metric inputs are not totals and do not feed one", f_inputs_not_totals),
    ("irrelevant_cell", "a safe cell exists for the irrelevant-change version", f_irrelevant_cell),
    ("split_metric", "difference metrics only on the held-out side", f_split_metric),
]


# ---------------------------------------------------------------- cut versions (held-out differences)


def _is_plain_subtract(text: str) -> bool:
    try:
        p = parse(text)
    except ProgramError:
        return False
    return len(p.steps) == 1 and p.steps[0].op == "subtract" and all(map(is_number_literal, p.steps[0].args))


def cut_at_difference(e: dict) -> Optional[tuple[int, dict]]:
    """The conversation cut at its first plain subtract turn at turn CUT_MIN_TURNS or later.

    Returns (turns kept, cut record) or None. The cut is not the final turn (that would be
    the uncut conversation itself)."""
    ann = e["annotation"]
    tp = ann["turn_program"]
    for k in range(CUT_MIN_TURNS - 1, len(tp) - 1):
        if _is_plain_subtract(tp[k]):
            cut_ann = dict(ann)
            for key in ("turn_program", "exe_ans_list", "dialogue_break"):
                cut_ann[key] = ann[key][:k + 1]
            return k + 1, {**e, "annotation": cut_ann}
    return None


# ---------------------------------------------------------------- split


def assign_splits(companies: Sequence[str], seed: int = SEED,
                  heldout_fraction: float = HELDOUT_FRACTION) -> dict[str, str]:
    """Company -> 'pilot' | 'heldout'. Deterministic: sorted, then shuffled with a fixed seed."""
    order = sorted(set(companies))
    random.Random(seed).shuffle(order)
    n_held = round(heldout_fraction * len(order))
    return {co: ("heldout" if k < n_held else "pilot") for k, co in enumerate(order)}


# ---------------------------------------------------------------- run


def _new_funnel() -> list[dict]:
    return [{"filter": name, "rule": rule, "in": 0, "passed": 0, "dropped": Counter()}
            for name, rule, _ in FILTERS]


def _input_cells_json(c: Conv, lits: Sequence[str], role: Optional[str] = None) -> list[dict]:
    out = []
    for x in lits:
        i, j = c.input_cells[x]
        d = {"literal": x, "row": i, "col": j, "row_label": c.table[i][0],
             "col_label": _cell(c.table, 0, j), "cell": c.table[i][j]}
        if role:
            d = {"role": role, **d}
        out.append(d)
    return out


def run_filters(c: Conv, funnel: list[dict]) -> dict:
    status, reason, detail = "candidate", "", ""
    for stage, (_, _, fn) in zip(funnel, FILTERS):
        stage["in"] += 1
        drop = fn(c)
        if drop:
            reason, detail = drop
            stage["dropped"][reason] += 1
            status = "dropped"
            break
        stage["passed"] += 1
    m = c.metric
    if m:
        c.row.update(metric_type=m.type, metric_scale=m.scale, metric_program=c.progs[-1].text)
        if c.input_cells and all(x in c.input_cells for x in m.inputs):
            c.row["inputs"] = json.dumps([d for role, lits in m.roles().items()
                                          for d in _input_cells_json(c, lits, role)])
            if m.type != "difference":
                c.row["numerator_inputs"] = json.dumps(_input_cells_json(c, m.numerator_inputs))
                c.row["denominator_inputs"] = json.dumps(_input_cells_json(c, m.denominator_inputs))
    c.row.update(status=status, drop_reason=reason, drop_detail=detail)
    return c.row


def _base_row(c: Conv, conv_id: str, source_id: str) -> dict:
    return {"id": conv_id, "source_id": source_id, "source_split": c.source_split,
            "company": company_of(source_id), "year": source_id.split("/")[1],
            "type": "II" if "qa_0" in c.e else "I", "n_turns": len(c.turns),
            "truncated_at": c.truncated_at if c.truncated_at is not None else "", "split": c.split}


def select(convs: list[tuple[str, dict]], splits: dict[str, str]) -> tuple[list[dict], dict]:
    """Rows for every conversation, plus held-out cut versions; funnels keyed 'full' / 'cut'."""
    funnels = {"full": _new_funnel(), "cut": _new_funnel()}
    rows = []
    for source_split, e in convs:
        split = splits[company_of(e["id"])]
        c = Conv(source_split, e, split)
        c.row = _base_row(c, e["id"], e["id"])
        rows.append(run_filters(c, funnels["full"]))
        if split != "heldout":
            continue
        cut = cut_at_difference(e)
        if cut is None:
            continue
        kept, cut_e = cut
        cc = Conv(source_split, cut_e, split, truncated_at=kept)
        cc.row = _base_row(cc, f"{e['id']}|cut@{kept}", e["id"])
        rows.append(run_filters(cc, funnels["cut"]))
    for f in funnels.values():
        for stage in f:
            stage["dropped"] = dict(stage["dropped"].most_common())
    return rows, funnels


def split_counts(rows: list[dict]) -> dict:
    out = {}
    for side in ("pilot", "heldout"):
        cands = [r for r in rows if r["status"] == "candidate" and r["split"] == side]
        by = {t: [r for r in cands if r["metric_type"] == t] for t in METRICS}
        need = GATE["required_metrics"][side]
        s = {
            "candidates": len(cands),
            "companies": len({r["company"] for r in cands}),
            "by_metric": {t: {"candidates": len(v), "companies": len({r["company"] for r in v})}
                          for t, v in by.items()},
            "required_metrics": list(need),
            "min_candidates": GATE["min_candidates"][side],
            "min_companies_per_metric": GATE["min_companies_per_metric"],
        }
        problems = []
        if s["candidates"] < s["min_candidates"]:
            problems.append(f"{s['candidates']} candidates < {s['min_candidates']}")
        for t in need:
            if s["by_metric"][t]["companies"] < GATE["min_companies_per_metric"]:
                problems.append(f"{t}: {s['by_metric'][t]['companies']} companies "
                                f"< {GATE['min_companies_per_metric']}")
        s["gate_problems"] = problems
        s["passes_gate"] = not problems
        out[side] = s
    return out


def endings(convs: list[tuple[str, dict]]) -> dict:
    """What type I conversations end in, before any other filter (dataset limitation)."""
    by, shapes, n = Counter(), Counter(), 0
    for _, e in convs:
        if "qa_0" in e:
            continue
        n += 1
        try:
            p = parse(e["annotation"]["turn_program"][-1])
        except ProgramError:
            by["unparseable"] += 1
            continue
        m = classify_metric(p)
        by[m.type if m else "other"] += 1
        if m and m.type == "growth_rate":
            shapes["growth (a - b) / b" if m.divide_step == 1 else "growth a / b - 1"] += 1
        elif m and m.type == "ratio":
            shapes["ratio a / b" if m.divide_step == 0 else "ratio composite"] += 1
    return {"type_i": n, "by_metric": dict(by.most_common()), "by_shape": dict(shapes.most_common())}


def answer_agreement(fin: dict[str, list[dict]], conv: list[tuple[str, dict]]) -> dict:
    """Written answer vs exe_ans across FinQA (all labelled) and ConvFinQA (every qa block)."""
    finqa = Counter(classify(e["qa"]["answer"], e["qa"]["exe_ans"])
                    for s in ("train", "dev", "test") for e in fin[s])
    convq = Counter(classify(e[k]["answer"], e[k]["exe_ans"])
                    for _, e in conv for k in ("qa", "qa_0", "qa_1") if k in e)
    kept = lambda c: sum(v for k, v in c.items() if k in KEEP)
    return {"finqa": dict(finqa.most_common()), "finqa_total": sum(finqa.values()),
            "finqa_dropped": sum(finqa.values()) - kept(finqa),
            "convfinqa_qa_blocks": dict(convq.most_common()), "convfinqa_total": sum(convq.values()),
            "convfinqa_dropped": sum(convq.values()) - kept(convq)}


def _funnel_table(funnel: list[dict]) -> list[str]:
    L = ["| # | Filter | In | Passed | Dropped (reason: count) |", "|---:|---|---:|---:|---|"]
    for k, st in enumerate(funnel, 1):
        drops = ", ".join(f"{r}: {n}" for r, n in st["dropped"].items()) or "—"
        L.append(f"| {k} | {st['rule']} | {st['in']:,} | {st['passed']:,} | {drops} |")
    return L


def render_report(result: dict) -> str:
    sc, ag, en, run = result["splits"], result["answer_agreement"], result["endings"], result["run"]
    L = [
        "# Stage 3 selection report",
        "",
        "Generated by `python -m capstone.data.select` (do not edit by hand).",
        "",
        f"- Run: {run['finished']}; data verified against `data/checksums.sha256`",
        f"- Split: {round(HELDOUT_FRACTION * 100)}% of {run['n_companies']} companies held out "
        f"({len(run['heldout_companies'])}), seed {SEED}",
        f"- Gate: ≥{GATE['min_candidates']['pilot']} candidates per side and "
        f"≥{GATE['min_companies_per_metric']} companies per required metric "
        "(pilot: growth_rate, ratio; held-out: growth_rate, difference)",
        f"- Pilot mix target for Stage 4/5 (one family per company): "
        + ", ".join(f"{k} {v}" for k, v in PILOT_MIX_TARGET.items()),
        "",
        f"## Candidates per split — gate {'PASSED' if all(s['passes_gate'] for s in sc.values()) else '**FAILED**'}",
        "",
        "Cells: candidates (companies). Families need one company each.",
        "",
        "| Side | Candidates | Companies | growth_rate | ratio | difference | Gate |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for side in ("pilot", "heldout"):
        s = sc[side]
        cells = []
        for t in METRICS:
            v = s["by_metric"][t]
            mark = " *" if t in s["required_metrics"] else ""
            cells.append(f"{v['candidates']} ({v['companies']}){mark}")
        L.append(f"| {side} | {s['candidates']} | {s['companies']} | " + " | ".join(cells)
                 + f" | {'pass' if s['passes_gate'] else '**FAIL**: ' + '; '.join(s['gate_problems'])} |")
    L += ["", "\\* required metric for that side (gated at ≥10 companies).", ""]
    L += ["## Dataset limitation: ConvFinQA endings are dominated by growth rates", "",
          f"Of {en['type_i']:,} type I conversations, the last turn is: "
          + ", ".join(f"{k} {v:,}" for k, v in en["by_metric"].items())
          + " (by shape: " + ", ".join(f"{k} {v:,}" for k, v in en["by_shape"].items()) + "). "
          "Type I conversations split one multi-step question into turns, so the final turn is the "
          f"full calculation. Plain `a / b` ratios end only {en['by_shape'].get('ratio a / b', 0):,} "
          f"conversations and plain differences end {en['by_metric'].get('difference', 0):,}, so "
          "held-out differences come from conversations cut at an earlier subtract turn.", ""]
    L += ["## Funnel: full conversations", ""] + _funnel_table(result["funnels"]["full"])
    L += ["", "## Funnel: held-out cut versions (difference metric)", "",
          f"Held-out conversations are cut at their first plain `subtract(a, b)` turn at turn "
          f"{CUT_MIN_TURNS} or later (`truncated_at` = turns kept) and run through every filter again. "
          "The written-answer check uses the source question.", ""] + _funnel_table(result["funnels"]["cut"])
    L += ["", "## Written answer vs computed `exe_ans`", "",
          "Rule (decision A): keep `agree`, `agree_display_rounding`, `agree_percent_format`; drop "
          "`disagree` (incl. sign flips) and `unparseable`. ConvFinQA's per-turn `answer_list` is not "
          "a written answer (calculated turns hold placeholders like `A0`), so the check uses `qa.answer`.",
          "", "| Category | FinQA (all labelled) | ConvFinQA (all qa blocks) |", "|---|---:|---:|"]
    for cat in dict.fromkeys(list(ag["finqa"]) + list(ag["convfinqa_qa_blocks"])):
        L.append(f"| {cat} | {ag['finqa'].get(cat, 0):,} | {ag['convfinqa_qa_blocks'].get(cat, 0):,} |")
    L += [f"| **dropped** | **{ag['finqa_dropped']:,} / {ag['finqa_total']:,}** | "
          f"**{ag['convfinqa_dropped']:,} / {ag['convfinqa_total']:,}** |", ""]
    L += ["## Notes for Stage 4", "",
          "- `metric_scale`: `fraction` (0.0174 = 1.74%), `percent` (program ends ×100) or `amount` "
          "(difference; see `unit`). Thresholds must use that scale.",
          "- `numerator_inputs` / `denominator_inputs` (growth_rate, ratio) and `inputs` (with role: "
          "numerator, denominator, minuend, subtrahend) give each input's cell (row, col, label, text).",
          "- `n_irrelevant_cells` counts cells safe to edit for version 4.",
          "- Total detection only recognises rows/columns labelled \"total\".", ""]
    return "\n".join(L)


def _write_csv(path: Path, rows: list[dict]) -> None:
    fields: dict[str, None] = {}
    for r in rows:
        fields.update(dict.fromkeys(r))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields))
        w.writeheader()
        w.writerows(rows)


def run(root: Path, log: Callable[[str], None] = print) -> int:
    paths = fetch.Paths(root)
    fetch.verify(paths, log=log)
    raw = paths.raw
    conv = [(s, e) for s in ("train", "dev")
            for e in json.loads((raw / f"convfinqa/data/{s}.json").read_text())]
    fin = {s: json.loads((raw / f"finqa/dataset/{s}.json").read_text()) for s in ("train", "dev", "test")}
    log(f"  loaded {len(conv):,} ConvFinQA conversations, {sum(map(len, fin.values())):,} FinQA examples")

    splits = assign_splits([company_of(e["id"]) for _, e in conv])
    rows, funnels = select(conv, splits)
    result = {
        "run": {"finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "seed": SEED, "heldout_fraction": HELDOUT_FRACTION, "n_companies": len(splits),
                "heldout_companies": sorted(c for c, s in splits.items() if s == "heldout")},
        "gate": GATE,
        "pilot_mix_target": PILOT_MIX_TARGET,
        "funnels": funnels,
        "splits": split_counts(rows),
        "endings": endings(conv),
        "answer_agreement": answer_agreement(fin, conv),
    }
    out = root / "data" / "interim" / "stage3"
    out.mkdir(parents=True, exist_ok=True)
    _write_csv(out / "candidates.csv", rows)
    (out / "selection.json").write_text(json.dumps(result, indent=2) + "\n")
    (root / "docs" / "selection_report.md").write_text(render_report(result))

    for kind, funnel in funnels.items():
        log(f"  funnel ({kind}):")
        for k, st in enumerate(funnel, 1):
            log(f"    [{k:2}] {st['filter']:<18} in {st['in']:>5,}  passed {st['passed']:>5,}  "
                f"dropped {st['dropped']}")
    failed = []
    for side, s in result["splits"].items():
        log(f"  {side}: {s['candidates']} candidates, {s['companies']} companies, by metric "
            f"{ {t: (v['candidates'], v['companies']) for t, v in s['by_metric'].items()} }")
        if not s["passes_gate"]:
            failed.append(f"{side} ({'; '.join(s['gate_problems'])})")
    log(f"  wrote {out.relative_to(root)}/ and docs/selection_report.md")
    if failed:
        log("STOP: gate failed for " + ", ".join(failed) + "; do not fix the split")
        return 1
    log("OK: gate passed on both sides")
    return 0


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
