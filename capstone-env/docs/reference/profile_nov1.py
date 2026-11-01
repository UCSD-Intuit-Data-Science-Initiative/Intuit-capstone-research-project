"""Nov 1 2026 profiling of FinQA / ConvFinQA -- the exact definitions behind the numbers in
CLAUDE.md and the data plan. Reference only: Stage 2 should re-implement this cleanly with tests.

Usage: python profile_nov1.py <FINQA_DATASET_DIR> <CONVFINQA_DATA_DIR>
   e.g. python profile_nov1.py data/raw/FinQA/dataset data/raw/ConvFinQA/data
(ConvFinQA ships its data inside data.zip; unzip it first.)

Definitions (the ones Stage 2 asked about are marked >>):
  * FinQA "labelled" = train+dev+test (8,281). private_test has no answers; excluded everywhere.
  * ConvFinQA "labelled" = train+dev (3,458). test_private has no answers; excluded everywhere.
  * company = id.split('/')[0], year = id.split('/')[1]   (FinQA ids like 'ADI/2009/page_49.pdf-1').
  * re-execution match: our value vs the dataset's `exe_ans` (FinQA) / `exe_ans_list` (ConvFinQA);
    numbers: |ours - gold| <= 1e-3 * max(1, |gold|); strings ('yes'/'no'): exact.
  * evidence location: all gold_inds keys start 'table' -> table; all start 'text' -> text; else both.
  >> literals of a program = arguments of NON-table ops that match r'-?[\\d.]+%?$'
     (so: no #n, no const_*, no table_* row names). Found with re.findall(r'([a-z_]+)\\(([^)]*)\\)').
     NOTE: a bare number-selection turn program like '6305' has no parentheses, so it contributes
     NO literals. That is a quirk of this definition, not a deliberate choice.
  >> traceability base = examples with >=1 literal AND a non-empty table
     (FinQA 8,006 of 8,281; ConvFinQA 3,455 of 3,458, using ', '.join(turn_program)).
  >> a literal "traces" to a cell when abs(float(lit.rstrip('%'))) == abs(first number in the cell)
     within 1e-9. Cells searched: table[1:] (header row skipped) x row[1:] (row-label column skipped).
     Cell number = first match of r'-?\\d+\\.?\\d*' after removing '$' and ','.
     Note the % literal is compared as written ('4.02%' -> 4.02), not divided by 100.
     "all found" = every literal has >=1 matching cell; "exactly one" = every literal has exactly 1.
  >> "reuse" (3,704 / 12,594) = ConvFinQA turn programs that contain '#', i.e. multi-step turn
     programs. In ConvFinQA '#n' refers to a step INSIDE the same turn's program (earlier work is
     re-written inline), so this is a PROXY for "builds on earlier turns", not a direct measure.
     The data plan's wording ("reuse an earlier turn's result") overstates it; relabel it.
  >> pool (1,304) = ConvFinQA train+dev conversations with len(turn_program) >= 3, >=1 literal,
     every literal (joined over all turns) traces to exactly one cell, and at least one turn after
     the first contains an op. Base for the "of N" is all 3,458 conversations.
"""

import collections
import json
import re
import sys

OP_RE = re.compile(r"([a-z_]+)\(([^)]*)\)")


def num(tok):
    t = tok.strip().replace(",", "")
    if t.startswith("const_"):
        t = t[6:]
        return -1.0 if t == "m1" else float(t)
    pct = t.endswith("%")
    v = float(t.rstrip("%"))
    return v / 100 if pct else v


def cell_value(c):
    m = re.search(r"-?\d+\.?\d*", c.replace("$", "").replace(",", ""))
    return float(m.group()) if m else None


def run(prog, table):
    if "(" not in prog:  # ConvFinQA number-selection turn
        return num(prog)
    res = []
    for op, args in OP_RE.findall(prog):
        a = [s.strip() for s in args.split(",")]
        if op.startswith("table_"):
            row = next(r for r in table if r[0].strip().lower() == a[0].lower())
            vals = [v for v in map(cell_value, row[1:]) if v is not None]
            v = {"table_max": max, "table_min": min, "table_sum": sum,
                 "table_average": lambda z: sum(z) / len(z)}[op](vals)
        else:
            x, y = [res[int(s[1:])] if s.startswith("#") else num(s) for s in a]
            v = {"add": lambda: x + y, "subtract": lambda: x - y, "multiply": lambda: x * y,
                 "divide": lambda: x / y, "exp": lambda: x ** y,
                 "greater": lambda: "yes" if x > y else "no"}[op]()
        res.append(v)
    return res[-1]


def same(v, g):
    if isinstance(v, str) or isinstance(g, str):
        return v == g
    return abs(v - g) <= 1e-3 * max(1, abs(g))


def literals(prog):
    toks = [t.strip() for op, a in OP_RE.findall(prog) if not op.startswith("table_") for t in a.split(",")]
    return [t for t in toks if re.match(r"-?[\d.]+%?$", t)]


def locate(lit, table):
    v = float(lit.rstrip("%"))
    return [(i, j) for i, r in enumerate(table[1:], 1) for j, c in enumerate(r[1:], 1)
            if (cv := cell_value(c)) is not None and abs(abs(cv) - abs(v)) < 1e-9]


def trace_stats(items):
    base = found = unique = 0
    for prog, table in items:
        lits = literals(prog)
        if not lits or not table:
            continue
        base += 1
        locs = [locate(l, table) for l in lits]
        found += all(locs)
        unique += all(len(h) == 1 for h in locs)
    return base, found, unique


def main(fdir, cdir):
    out = {}
    fin = {s: json.load(open(f"{fdir}/{s}.json")) for s in ["train", "dev", "test", "private_test"]}
    out["finqa_split_counts"] = {s: len(x) for s, x in fin.items()}
    allx = fin["train"] + fin["dev"] + fin["test"]
    out["finqa_labelled"] = len(allx)
    out["finqa_companies"] = len({e["id"].split("/")[0] for e in allx})
    years = sorted({e["id"].split("/")[1] for e in allx})
    out["finqa_years"] = f"{years[0]}-{years[-1]}"
    out["finqa_ops"] = dict(collections.Counter(o for e in allx for o, _ in OP_RE.findall(e["qa"]["program"])).most_common())
    out["finqa_steps"] = dict(sorted(collections.Counter(len(OP_RE.findall(e["qa"]["program"])) for e in allx).items()))
    out["finqa_reexec_match"] = sum(same(run(e["qa"]["program"], e["table"]), e["qa"]["exe_ans"]) for e in allx)
    ev = collections.Counter()
    for e in allx:
        ks = e["qa"]["gold_inds"].keys()
        ev["table" if all(k.startswith("table") for k in ks) else "text" if all(k.startswith("text") for k in ks) else "both"] += 1
    out["finqa_evidence"] = dict(ev)
    b, f, u = trace_stats((e["qa"]["program"], e["table"]) for e in allx)
    out["finqa_trace"] = {"base": b, "all_found": f, "exactly_one": u}

    conv = {s: json.load(open(f"{cdir}/{s}.json")) for s in ["train", "dev", "test_private"]}
    out["conv_split_counts"] = {s: len(x) for s, x in conv.items()}
    cv = conv["train"] + conv["dev"]
    out["conv_labelled"] = len(cv)
    out["conv_types"] = dict(collections.Counter("II" if "qa_0" in e else "I" for e in cv))
    out["conv_turns_per_conv"] = dict(sorted(collections.Counter(len(e["annotation"]["dialogue_break"]) for e in cv).items()))
    progs = [(p, a, e["table"]) for e in cv for p, a in zip(e["annotation"]["turn_program"], e["annotation"]["exe_ans_list"])]
    out["conv_turns"] = len(progs)
    out["conv_reexec_match"] = sum(same(run(p, t), a) for p, a, t in progs)
    out["conv_number_selection_turns"] = sum("(" not in p for p, _, _ in progs)
    out["conv_multistep_turns(#)"] = sum("#" in p for p, _, _ in progs)
    b, f, u = trace_stats((", ".join(e["annotation"]["turn_program"]), e["table"]) for e in cv)
    out["conv_trace"] = {"base": b, "all_found": f, "exactly_one": u}
    pool = 0
    for e in cv:
        tp = e["annotation"]["turn_program"]
        lits = literals(", ".join(tp))
        if len(tp) >= 3 and lits and all(len(locate(l, e["table"])) == 1 for l in lits) \
                and any("#" in p or OP_RE.search(p) for p in tp[1:]):
            pool += 1
    out["conv_pool"] = {"pool": pool, "of": len(cv)}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
