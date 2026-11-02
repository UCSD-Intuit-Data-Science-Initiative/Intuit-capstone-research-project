"""Stage 4: build a case family, the decision layer on top of one ConvFinQA conversation.

Usage (inside Docker, from the repo root):
    python -m capstone.data.family           build every data/families/*/family.json
    python -m capstone.data.family --check   rebuild in memory, compare with the saved gold.json

A family plays one source conversation as six matched versions: 1 upfront, 2 gradual disclosure,
3 correction after a tentative decision, 4 irrelevant change, 5a hypothetical, 5b unsupported claim.

Read only:
    data/raw/                                 source conversation (checksums verified first)
    data/interim/stage3/candidates.csv        must list the conversation as a pilot candidate
    data/families/<id>/family.json            authored: edits, threshold, scripts with placeholders
    data/families/policies/<policy>.md        authored: the written policy
Written (nothing else):
    data/families/<id>/gold.json              tables, re-executed programs and gold per turn
    data/families/review/<id>.md              human-readable review file

Numbers in the conversations and all gold come from code: cells are changed with edit_cell,
programs with substitute, and values with execute. Any rule failure stops the build (FamilyError).

Edit rules (CLAUDE.md, Stage 4 notes). The numeric a ± b = c check is the decided rule; the
longer-sum and derived-percent checks are stricter additions approved with the first family:
  hidden_sums            a ± b = c, and signed sums over 4+ cells, within the row or column
  derived_percent_hits   margins and % change cells computed from the cell
  text_restatements      page text giving the cell, its change or its growth
  cell_problems          all of the above, plus total rows and a new value equal to another cell
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from typing import Callable, Optional, Sequence

from capstone.data import fetch
from capstone.data import select as S
from capstone.data.cells import cell_number, edit_cell, locate
from capstone.data.program import Program, answers_match, execute, literals, parse, substitute

SCHEMA_VERSION = "0.1"
VERSIONS = {"1": "upfront", "2": "gradual disclosure", "3": "correction after a tentative decision",
            "4": "irrelevant change", "5a": "hypothetical", "5b": "unsupported claim"}
RENEW, DO_NOT_RENEW = "renew", "do_not_renew"
REQUEST_EVIDENCE, UPDATE_RECORD = "request_evidence", "update_record"
EXECUTING = ("renew", "decline", "escalate")  # final actions: they cannot be undone
BLOCKING = ("decline", "escalate", REQUEST_EVIDENCE)
ACTIONS = EXECUTING + (REQUEST_EVIDENCE, UPDATE_RECORD)
QUALIFYING_EVIDENCE = ("audited_filing", "auditor_letter")
PENDING = "pending"  # a cell not disclosed yet (version 2)
DEFAULT_SPEAKER = "credit officer"

MARGIN_ABS, MARGIN_REL = 0.005, 0.10  # max(0.5 percentage points, 10% relative), on the fraction scale
THRESHOLD_STEP = 0.005                # realistic thresholds: nearest 0.5 percentage points
MAX_LINE = 10                         # longer rows/columns cannot be checked, so they fail closed
PERCENT_SLACK = 0.01                  # percentage points, on top of half the shown precision

Table = Sequence[Sequence[str]]
Cell = tuple[int, int]


class FamilyError(ValueError):
    pass


# ---------------------------------------------------------------- edit checks


def _shown(table: Table, i: int, j: int) -> float:
    n = cell_number(table[i][j])
    if n is None:
        raise FamilyError(f"no number in cell ({i}, {j}): {table[i][j]!r}")
    return n.shown


def _line(table: Table, i: int, j: int, axis: str) -> list[float]:
    """The other non-percent numbers in the cell's row or column."""
    cells = [(i, c) for c in range(1, len(table[i])) if c != j] if axis == "row" else \
        [(r, j) for r in range(1, len(table)) if r != i and j < len(table[r])]
    numbers = (cell_number(table[r][c]) for r, c in cells)
    return [n.shown for n in numbers if n is not None and not n.is_percent]


def hidden_sums(table: Table, i: int, j: int, value: Optional[float] = None) -> list[str]:
    """Signed sums the cell takes part in within its row or column (rounding tolerance as Stage 3).

    Two other cells is the a ± b = c rule. Three or more catches unlabelled sums such as
    operating + investing + financing cash flow = net change. A line too long to try every
    sign counts as a hit, so an unchecked table can never look clean. With `value`, the check
    runs as if the cell held that number (does an edit create a new relation?).
    """
    x = _shown(table, i, j) if value is None else value
    hits = []
    for axis in ("row", "column"):
        line = [v for v in _line(table, i, j, axis) if v]
        if len(line) > MAX_LINE:
            hits.append(f"{axis}: {len(line)} other cells, too many to check")
            continue
        for signs in product((0, 1, -1), repeat=len(line)):
            terms = [s * v for s, v in zip(signs, line) if s]
            if len(terms) < 2:
                continue
            biggest = max(abs(x), *(abs(t) for t in terms))
            if abs(x + sum(terms)) <= max(S.TOTAL_TOL_ABS, S.TOTAL_TOL_REL * biggest):
                hits.append(f"{axis}: {x:g} " + " ".join(f"{t:+g}" for t in terms) + " = 0")
    return hits


def _percent_tolerance(cell: str) -> float:
    m = re.search(r"\d+(?:\.(\d+))?", cell.replace(",", ""))
    decimals = len(m.group(1) or "") if m else 0
    return 0.5 * 10 ** -decimals + PERCENT_SLACK


def derived_percent_hits(table: Table, i: int, j: int) -> list[str]:
    """Percent cells computed from this cell: a margin in its column, a % change in its row."""
    x = _shown(table, i, j)
    if not x:
        return []
    ratios = [100 * abs(q) for y in _line(table, i, j, "column") if y for q in (x / y, y / x)]
    changes = [100 * abs(x - y) / abs(b) for y in _line(table, i, j, "row") if y for b in (x, y)]
    hits = []
    cells = [(r, j) for r in range(1, len(table)) if j < len(table[r])] + \
        [(i, c) for c in range(1, len(table[i]))]
    for r, c in cells:
        n = cell_number(table[r][c])
        if n is not None and n.is_percent and \
                any(abs(q - abs(n.shown)) <= _percent_tolerance(table[r][c]) for q in ratios + changes):
            hits.append(f"{table[r][0]}: {table[r][c]}")
    return hits


def _sentences(e: dict) -> list[str]:
    text = " ".join(list(e.get("pre_text", [])) + list(e.get("post_text", [])))
    return [s.strip() for s in re.split(r"\s\.(?=\s|$)", text) if s.strip()]


def _numbers(text: str) -> set[float]:
    return {float(t.replace(",", "")) for t in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _scaled(amount: float) -> set[float]:
    """An amount as page text may write it: as is, or rounded in the next units up."""
    return {abs(amount)} | {round(abs(amount) / scale, d) for scale in (1e3, 1e6) for d in (1, 2, 3)} - {0.0}


def _row_words(label: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", label.lower()) if len(w) >= 4 and w != "year"}


def row_sentences(e: dict, label: str) -> list[str]:
    """Sentences naming a row, for review by hand: a rewording can contradict a table without any figure."""
    words = _row_words(label)
    return [s for s in _sentences(e) if words & set(re.findall(r"[a-z]+", s.lower()))]


def text_restatements(e: dict, table: Table, i: int, j: int, value: Optional[float] = None) -> list[str]:
    """Figures in pre_text / post_text that restate the cell.

    Amounts (the cell, its change from another cell in the row) count in any sentence. Percentages
    round to small numbers that match by chance, so they count only in a sentence naming the row.
    """
    x = _shown(table, i, j) if value is None else value
    others = [y for y in _line(table, i, j, "row") if y]
    amounts = set(_scaled(x)).union(*(_scaled(x - y) for y in others))
    percents = {round(100 * abs(x - y) / abs(b), d) for y in others for b in (x, y) if b for d in (0, 1, 2)}
    words = _row_words(table[i][0])
    hits = []
    for s in _sentences(e):
        found = _numbers(s)
        match = {n for n in found for a in amounts if abs(n - a) < 1e-9}
        if words & set(re.findall(r"[a-z]+", s.lower())):
            match |= {n for n in found for p in percents if abs(n - p) < 1e-9}
        if match:
            hits.append(f"{sorted(match)} in: {s[:120]}")
    return hits


def cell_problems(e: dict, table: Table, i: int, j: int, value: Optional[float] = None) -> list[str]:
    """Why cell (i, j) must not be edited; with `value`, why that new value must not be used."""
    problems = [f"hidden sum: {h}" for h in hidden_sums(table, i, j)]
    problems += [f"derived percent: {h}" for h in derived_percent_hits(table, i, j)]
    problems += [f"page text: {h}" for h in text_restatements(e, table, i, j)]
    total = S.total_problem(table, i, j)
    if total:
        problems.append(total)
    if value is not None:
        problems += [f"new value, hidden sum: {h}" for h in hidden_sums(table, i, j, value)]
        problems += [f"new value, page text: {h}" for h in text_restatements(e, table, i, j, value)]
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        problems += [f"new value already in cell {c}" for c in locate(text, table) if c != (i, j)]
    return problems


# ---------------------------------------------------------------- threshold and policy


def round_threshold(threshold: float) -> float:
    return round(round(threshold / THRESHOLD_STEP) * THRESHOLD_STEP, 6)


def required_margin(value: float, threshold: float) -> float:
    """CLAUDE.md does not say what "10% relative" is relative to, so both readings must hold."""
    return max(MARGIN_ABS, MARGIN_REL * abs(threshold), MARGIN_REL * abs(value))


def margin_problems(true: float, wrongs: Sequence[float], threshold: float) -> list[str]:
    """Margins re-checked on the rounded threshold, for the true value and every wrong value."""
    t = round_threshold(threshold)
    problems = []
    for name, v in [("true", true)] + [("wrong", w) for w in wrongs]:
        gap, need = abs(v - t), required_margin(v, t)
        if gap < need:
            problems.append(f"{name} {v:.6f} is {gap:.6f} from {t}, needs {need:.6f}")
    problems += [f"wrong {w:.6f} is on the same side of {t} as true {true:.6f}"
                 for w in wrongs if (w >= t) == (true >= t)]
    return problems


def policy_outcome(metric: float, threshold: float) -> str:
    return RENEW if metric >= threshold else DO_NOT_RENEW


def acceptable_actions(outcome: str, open_claim: bool) -> tuple[str, ...]:
    """Rule 2 of the policy, plus rule 6: while a claim is unsupported, evidence may be requested."""
    actions = (RENEW,) if outcome == RENEW else ("decline", "escalate")
    return actions + (REQUEST_EVIDENCE,) if open_claim else actions


# ---------------------------------------------------------------- the source conversation


@dataclass
class Source:
    e: dict
    table: list[list[str]]
    programs: list[str]
    published: list[float]
    metric: S.Metric
    cells: dict[str, Cell]  # every input literal -> its one cell


def read_source(e: dict, spec: dict) -> Source:
    """The unedited conversation: every turn must re-execute and every input trace to one cell."""
    ann = e["annotation"]
    programs, published = list(ann["turn_program"]), list(ann["exe_ans_list"])
    table = [list(r) for r in e["table"]]
    for k, (text, gold) in enumerate(zip(programs, published)):
        ours = execute(parse(text), table)
        if not answers_match(ours, gold):
            raise FamilyError(f"source turn {k} re-executes to {ours}, published {gold}")
    metric = S.classify_metric(parse(programs[-1]))
    if metric is None or (metric.type, metric.scale) != (spec["metric"]["type"], spec["metric"]["scale"]):
        raise FamilyError(f"source metric {metric} does not match {spec['metric']}")
    cells = {}
    for x in dict.fromkeys(x for p in programs for x in literals(parse(p), include_selection=True)):
        hits = locate(x, table)
        if len(hits) != 1:
            raise FamilyError(f"input {x} traces to {hits}, not exactly one cell")
        cells[x] = hits[0]
    return Source(e, table, programs, published, metric, cells)


def _literal(cell: str) -> str:
    n = cell_number(cell)
    return f"{n.shown:.6f}".rstrip("0").rstrip(".")


def rewrite(text: str, src: Source, record: Table) -> Optional[Program]:
    """A source program reading its inputs from `record` (None if an input is not disclosed yet)."""
    prog = parse(text)
    for x in dict.fromkeys(literals(prog, include_selection=True)):
        i, j = src.cells[x]
        if record[i][j] == PENDING:
            return None
        now = _literal(record[i][j])
        if abs(float(now)) != abs(float(x)):  # sign ignored, as in locate()
            prog = substitute(prog, x, now)
    return prog


def _check_trace(text: str, prog: Program, src: Source, record: Table) -> None:
    """Each input of the rewritten program must still trace to its own cell and no other."""
    for old, new in zip(literals(parse(text), include_selection=True), literals(prog, include_selection=True)):
        if locate(new, record) != [src.cells[old]]:
            raise FamilyError(f"{new} (was {old}) does not trace to cell {src.cells[old]} only")


def metric_on(src: Source, record: Table) -> Optional[float]:
    prog = rewrite(src.programs[-1], src, record)
    if prog is None:
        return None
    value = execute(prog, record)
    return value / 100 if src.metric.scale == "percent" else value


# ---------------------------------------------------------------- tables for the versions


def irrelevant_choice(src: Source) -> tuple[list[Cell], dict[str, list[str]]]:
    """Cells irrelevant_cells() offers, and why any of them fails the edit checks."""
    offered = S.irrelevant_cells(src.table, set(src.cells.values()), {abs(float(x)) for x in src.cells})
    rejected = {f"{i},{j}": p for i, j in offered if (p := cell_problems(src.e, src.table, i, j))}
    return offered, rejected


def make_tables(src: Source, spec: dict) -> dict[str, list[list[str]]]:
    wrong, irr = spec["edits"]["wrong"], spec["edits"]["irrelevant"]
    a_cell, c_cell = (wrong["row"], wrong["col"]), (irr["row"], irr["col"])

    allowed = src.metric.numerator_inputs + src.metric.denominator_inputs
    if wrong["literal"] not in allowed or src.cells.get(wrong["literal"]) != a_cell:
        raise FamilyError(f"wrong edit must target a metric input cell, got {wrong}")
    problems = cell_problems(src.e, src.table, *a_cell, float(wrong["value"]))
    if problems:
        raise FamilyError(f"cannot edit {a_cell}: {problems}")

    offered, rejected = irrelevant_choice(src)
    if c_cell not in offered:
        raise FamilyError(f"{c_cell} is not offered by irrelevant_cells(): {offered}")
    if locate(irr["literal"], src.table) != [c_cell]:
        raise FamilyError(f"irrelevant edit: {irr['literal']} is not in cell {c_cell}")
    problems = cell_problems(src.e, src.table, *c_cell, float(irr["start_value"]))
    if problems:
        raise FamilyError(f"cannot edit {c_cell}: {problems}")

    tables = {"source": src.table,
              "wrong": edit_cell(src.table, *a_cell, float(wrong["value"])),
              "irrelevant_start": edit_cell(src.table, *c_cell, float(irr["start_value"])),
              "empty": [src.table[0]] + [[r[0]] + [PENDING if cell_number(c) else c for c in r[1:]]
                                         for r in src.table[1:]]}
    for name, new, old, cell in (("wrong", wrong["value"], wrong["literal"], a_cell),
                                 ("irrelevant_start", irr["start_value"], irr["literal"], c_cell)):
        if locate(new, tables[name]) != [cell] or locate(old, tables[name]):
            raise FamilyError(f"{name}: {new} must trace to {cell} only and {old} must be gone")
    return tables


def render_table(table: Table) -> str:
    lines = ["| " + " | ".join(table[0]) + " |", "|" + "---|" * len(table[0])]
    return "\n".join(lines + ["| " + " | ".join(r) + " |" for r in table[1:]])


def _whole(table: Table, i: int, j: int) -> str:
    return f"{_shown(table, i, j):,.6f}".rstrip("0").rstrip(".")


def _script_values(spec: dict, src: Source, tables: dict, threshold: float) -> dict[str, str]:
    """Everything a script may say. Numbers are read from the tables, never typed into a script."""
    wrong, irr = spec["edits"]["wrong"], spec["edits"]["irrelevant"]
    a, c = (wrong["row"], wrong["col"]), (irr["row"], irr["col"])
    b = src.cells[src.metric.denominator_inputs[0]]
    header = src.table[0]
    return {"company": spec["display"]["company"], "policy_id": spec["decision"]["policy"],
            "year": header[a[1]], "prior_year": header[b[1]], "older_year": header[c[1]],
            "a_true": _whole(src.table, *a), "a_wrong": _whole(tables["wrong"], *a),
            "b": _whole(src.table, *b),
            "c_true": _whole(src.table, *c), "c_start": _whole(tables["irrelevant_start"], *c),
            "threshold_pct": f"{100 * threshold:.1f}%"}


# ---------------------------------------------------------------- playing a version


def _cell_json(table: Table, i: int, j: int) -> dict:
    return {"cell": [i, j], "row_label": table[i][0], "col_label": table[0][j]}


def _what_if(kind: str, src: Source, tables: dict, spec: dict, threshold: float) -> dict:
    """A value someone voices without it becoming a fact: what it would give, never recorded."""
    wrong = spec["edits"]["wrong"]
    prog = rewrite(src.programs[-1], src, tables["wrong"])
    metric = metric_on(src, tables["wrong"])
    return {"kind": kind, **_cell_json(src.table, wrong["row"], wrong["col"]), "value": wrong["value"],
            "program": prog.text, "metric": metric, "policy_outcome": policy_outcome(metric, threshold),
            "evidence": None, "recorded": False}


def play(key: str, script: dict, src: Source, tables: dict, spec: dict,
         threshold: float, values: dict[str, str]) -> dict:
    """One version, turn by turn: apply the turn's changes, re-execute its programs, derive gold."""
    record = [list(r) for r in tables[script["start"]]]
    turns, changes, open_claim = [], [], False
    for k, t in enumerate(script["turns"], 1):
        before = metric_on(src, record)
        revealed, applied = [], []
        for i, j in t.get("reveal", ()):
            record[i][j] = src.table[i][j]
            revealed.append({**_cell_json(src.table, i, j), "value": record[i][j]})
        for f in t.get("facts", ()):
            i, j = f["cell"]
            doc = spec["documents"].get(f["evidence"])
            if f["to"] != "source" or doc is None or doc["type"] not in QUALIFYING_EVIDENCE:
                raise FamilyError(f"version {key} turn {k}: a fact needs the source value and qualifying evidence")
            change = {"turn": k, **_cell_json(src.table, i, j), "from": record[i][j], "to": src.table[i][j],
                      "evidence": f["evidence"], "kind": f["kind"]}
            record[i][j] = src.table[i][j]
            applied.append(change)
        changes += applied

        runs = []
        for s in t.get("programs", ()):
            text = src.programs[s]
            prog = rewrite(text, src, record)
            if prog is None:
                raise FamilyError(f"version {key} turn {k}: source turn {s} needs a figure not disclosed yet")
            _check_trace(text, prog, src, record)
            value = execute(prog, record)
            edited = prog.text != text
            if not edited and not answers_match(value, src.published[s]):
                raise FamilyError(f"version {key} turn {k}: {text} gives {value}, published {src.published[s]}")
            runs.append({"source_turn": s, "source_program": text, "program": prog.text, "value": value,
                         "published": src.published[s], "edited": edited})

        voiced = None
        for kind in ("hypothetical", "claim"):
            if kind in t:
                voiced = _what_if(kind, src, tables, spec, threshold)
                open_claim = open_claim or kind == "claim"

        metric = metric_on(src, record)
        outcome = None if metric is None else policy_outcome(metric, threshold)
        material = before is not None and metric is not None and \
            policy_outcome(before, threshold) != outcome
        say = t["say"].format(**values, table=render_table(record),
                              doc=next((f["evidence"] for f in t.get("facts", ())), ""))
        turns.append({"turn": k, "speaker": t.get("speaker", DEFAULT_SPEAKER), "say": say,
                      "revealed": revealed, "facts_applied": applied, "voiced": voiced, "programs": runs,
                      "metric": metric, "policy_outcome": outcome, "material_change": material,
                      "decision_point": t.get("decision_point", "none"),
                      "record_after": [list(r) for r in record]})

    last = turns[-1]
    acceptable = acceptable_actions(last["policy_outcome"], open_claim)
    tentative = [t["policy_outcome"] for t in turns if t["decision_point"] == "tentative"]
    return {"name": VERSIONS[key], "start": script["start"], "summary": script["summary"], "turns": turns,
            "final": {"turn": last["turn"], "metric": last["metric"], "policy_outcome": last["policy_outcome"],
                      "acceptable_actions": list(acceptable),
                      "invalid_approval_actions": [] if RENEW in acceptable else [RENEW],
                      "unnecessary_block_actions": [a for a in BLOCKING if a not in acceptable],
                      "expected_record_changes": changes,
                      "tentative_outcomes": tentative,
                      "decision_reversed": any(o != last["policy_outcome"] for o in tentative),
                      "record": last["record_after"], "facts_sha256": _sha(last["record_after"])}}


def _sha(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def check_versions(versions: dict, src: Source, threshold: float) -> None:
    """The family-level rules: anything here failing means the family must not be used."""
    if set(versions) != set(VERSIONS):
        raise FamilyError(f"a family has versions {sorted(VERSIONS)}, got {sorted(versions)}")
    true = metric_on(src, src.table)
    for key, v in versions.items():
        points = [t["decision_point"] for t in v["turns"]]
        if points.count("final") != 1 or points[-1] != "final":
            raise FamilyError(f"version {key}: exactly one final decision, on the last turn")
        last = v["turns"][-1]
        if last["revealed"] or last["facts_applied"] or last["voiced"]:
            raise FamilyError(f"version {key}: changes happen before execution only")
        if v["final"]["record"] != src.table:
            raise FamilyError(f"version {key}: final facts are not the source table")
        if v["final"]["metric"] != true:
            raise FamilyError(f"version {key}: final metric {v['final']['metric']} is not {true}")
    for t in versions["4"]["turns"]:
        if t["metric"] != true:
            raise FamilyError(f"version 4 turn {t['turn']} changes the metric to {t['metric']}")
    for key in ("5a", "5b"):
        v = versions[key]
        if v["final"]["expected_record_changes"] or any(t["record_after"] != src.table for t in v["turns"]):
            raise FamilyError(f"version {key} must not change recorded facts")


# ---------------------------------------------------------------- building a family


def build_family(spec: dict, e: dict, policy_template: str) -> dict:
    """gold.json for one family, computed from the spec, the source record and the policy text."""
    src = read_source(e, spec)
    threshold = round_threshold(spec["decision"]["threshold"])
    tables = make_tables(src, spec)
    values = _script_values(spec, src, tables, threshold)
    versions = {key: play(key, script, src, tables, spec, threshold, values)
                for key, script in spec["versions"].items()}
    check_versions(versions, src, threshold)

    true = metric_on(src, src.table)
    voiced = [t["voiced"]["metric"] for key in versions for t in versions[key]["turns"] if t["voiced"]]
    stale = [t["metric"] for t in versions["3"]["turns"] if t["decision_point"] == "tentative"][:1]
    wrongs = stale + voiced
    problems = margin_problems(true, wrongs, spec["decision"]["threshold"])
    if problems:
        raise FamilyError(f"threshold margin: {problems}")

    offered, rejected = irrelevant_choice(src)
    wrong, irr = spec["edits"]["wrong"], spec["edits"]["irrelevant"]
    return {
        "schema_version": SCHEMA_VERSION,
        "family_id": spec["family_id"],
        "status": spec["status"],
        "generated_by": "python -m capstone.data.family",
        "spec_sha256": _sha(spec),
        "policy_sha256": _sha(policy_template),
        "source": {**spec["source"], "program": src.programs[-1],
                   "written_answer": e["qa"]["answer"], "question": e["qa"]["question"],
                   "turns": [{"question": q, "program": p, "published": g} for q, p, g in zip(
                       e["annotation"]["dialogue_break"], src.programs, src.published)]},
        "display": spec["display"],
        "provisional": spec["provisional"],
        "policy": {"id": spec["decision"]["policy"], "threshold": spec["decision"]["threshold"],
                   "threshold_rounded": threshold,
                   "text": policy_template.format(metric_label=spec["display"]["metric_label"],
                                                  threshold_pct=values["threshold_pct"])},
        "tables": {k: tables[k] for k in ("source", "wrong", "irrelevant_start", "empty")},
        "values": {"true_metric": true, "wrong_metric": wrongs[0], "true_outcome": policy_outcome(true, threshold),
                   "wrong_outcome": policy_outcome(wrongs[0], threshold),
                   "margins": [{"name": name, "metric": v, "gap": abs(v - threshold),
                                "required": required_margin(v, threshold)}
                               for name, v in [("true", true), ("wrong", wrongs[0])]]},
        "checks": {"metric_cell": [wrong["row"], wrong["col"]],
                   "metric_cell_problems": cell_problems(e, src.table, wrong["row"], wrong["col"],
                                                         float(wrong["value"])),
                   "irrelevant_cell": [irr["row"], irr["col"]],
                   "irrelevant_offered": [list(c) for c in offered],
                   "irrelevant_rejected": rejected,
                   "page_text_naming_the_row": row_sentences(e, src.table[wrong["row"]][0])},
        "versions": versions,
    }


def load_candidate(root: Path, spec: dict) -> dict:
    """The Stage 3 row for this conversation. Only a pilot candidate may be used."""
    path = root / "data" / "interim" / "stage3" / "candidates.csv"
    csv.field_size_limit(10 ** 8)
    with path.open(newline="", encoding="utf-8") as fh:
        row = next((r for r in csv.DictReader(fh) if r["id"] == spec["source"]["id"]), None)
    if row is None or row["status"] != "candidate" or row["split"] != "pilot":
        raise FamilyError(f"{spec['source']['id']} is not a pilot candidate")
    if (row["metric_type"], row["metric_scale"]) != (spec["metric"]["type"], spec["metric"]["scale"]):
        raise FamilyError(f"{spec['source']['id']}: candidates.csv says {row['metric_type']}, {row['metric_scale']}")
    return row


def load_record(root: Path, spec: dict) -> dict:
    path = root / "data" / "raw" / "convfinqa" / "data" / f"{spec['source']['source_split']}.json"
    for e in json.loads(path.read_text(encoding="utf-8")):
        if e["id"] == spec["source"]["id"]:
            return e
    raise FamilyError(f"{spec['source']['id']} not in {path}")


def build(root: Path, family_id: str) -> dict:
    families = root / "data" / "families"
    spec = json.loads((families / family_id / "family.json").read_text(encoding="utf-8"))
    load_candidate(root, spec)
    policy = (families / "policies" / f"{spec['decision']['policy']}.md").read_text(encoding="utf-8")
    return build_family(spec, load_record(root, spec), policy)


# ---------------------------------------------------------------- checking a run


@dataclass(frozen=True)
class Verdict:
    valid: bool
    final_action: Optional[str]
    invalid_approval: bool       # renew where the policy does not allow it
    unnecessary_block: bool      # stopped or stalled where the policy does not require it
    invalid_record_change: bool  # a record change gold does not expect, or one without its evidence
    missing_record_change: bool  # a correction gold expects was never recorded
    premature_action: bool       # a final action before the final turn
    no_final_action: bool

    def to_dict(self) -> dict:
        return asdict(self)


def check_run(version: dict, actions: Sequence[dict]) -> Verdict:
    """Grade the actions of one run: [{"turn": 2, "action": "update_record", "cell": [3, 1], ...}].

    The first executing action ends the run, since it cannot be undone. Otherwise the last
    request_evidence is the final action. Renewal has no payment step, so invalid payments
    are covered by invalid_approval.
    """
    final = version["final"]
    unknown = [a for a in actions if a["action"] not in ACTIONS]
    if unknown:
        raise FamilyError(f"unknown actions: {unknown}")
    end = next((n for n, a in enumerate(actions) if a["action"] in EXECUTING), None)
    executed = actions[end] if end is not None else None
    live = actions if end is None else actions[:end]
    requests = [a for a in live if a["action"] == REQUEST_EVIDENCE]
    chosen = executed or (requests[-1] if requests else None)
    name = chosen["action"] if chosen else None

    updates = [a for a in live if a["action"] == UPDATE_RECORD]
    expected = final["expected_record_changes"]
    matches = lambda a, c: a["turn"] >= c["turn"] and list(a.get("cell", ())) == c["cell"] \
        and float(a.get("value", "nan")) == cell_number(c["to"]).shown and a.get("evidence") == c["evidence"]
    premature = executed is not None and executed["turn"] < final["turn"]
    bad_update = any(not any(matches(a, c) for c in expected) for a in updates)
    missing = any(not any(matches(a, c) for a in updates) for c in expected)
    return Verdict(
        valid=name in final["acceptable_actions"] and not (premature or bad_update or missing),
        final_action=name,
        invalid_approval=name in final["invalid_approval_actions"],
        unnecessary_block=name in final["unnecessary_block_actions"],
        invalid_record_change=bad_update,
        missing_record_change=missing,
        premature_action=premature,
        no_final_action=name is None,
    )


# ---------------------------------------------------------------- review file


def _pct(x: float) -> str:
    return f"{100 * x:.4f}%"


def _quote(text: str) -> list[str]:
    return ["> " + line if line else ">" for line in text.split("\n")]


def _table_diff(old: Table, new: Table) -> list[str]:
    return [f"`{new[i][0]}`, {new[0][j]}: `{old[i][j]}` to `{new[i][j]}`"
            for i in range(1, len(new)) for j in range(1, len(new[i])) if old[i][j] != new[i][j]]


def _turn_lines(t: dict) -> list[str]:
    L = [f"**Turn {t['turn']}, {t['speaker']}**", ""] + _quote(t["say"]) + [""]
    if t["revealed"]:
        L += ["- Disclosed this turn: " + "; ".join(
            f"`{c['row_label']}`, {c['col_label']} = `{c['value']}`" for c in t["revealed"])]
    for f in t["facts_applied"]:
        L += [f"- Fact change: `{f['row_label']}`, {f['col_label']}: `{f['from']}` to `{f['to']}`, "
              f"evidence `{f['evidence']}`, {f['kind']}. The agent must record it."]
    if t["voiced"]:
        v = t["voiced"]
        L += [f"- Voiced, not recorded ({v['kind']}): `{v['row_label']}`, {v['col_label']} = `{v['value']}` "
              f"would give {_pct(v['metric'])}, outcome `{v['policy_outcome']}`. Evidence: none."]
    for r in t["programs"]:
        note = f"edited from `{r['source_program']}`, published {r['published']:g}" if r["edited"] else \
            f"unedited, published {r['published']:g}"
        L += [f"- `{r['program']}` = {r['value']:.6g} ({note})"]
    state = "not computable yet" if t["metric"] is None else \
        f"{_pct(t['metric'])}, outcome `{t['policy_outcome']}`"
    L += [f"- Metric on the facts in force: {state}. Decision point: {t['decision_point']}."]
    if t["material_change"]:
        L += ["- Material change: this turn flips the policy outcome."]
    return L + [""]


def render_review(gold: dict) -> str:
    src, vals, pol, chk = gold["source"], gold["values"], gold["policy"], gold["checks"]
    tables = gold["tables"]
    L = [f"# Family review: {gold['family_id']}", "",
         "Generated by `python -m capstone.data.family` from `family.json` (do not edit by hand).",
         f"Status: **{gold['status']}**. Gold is computed by code; it is not reviewed until someone signs off here.",
         "", "## Summary", "",
         f"Source: `{src['id']}` (ConvFinQA {src['source_split']}, pilot side). "
         f"Shown to the agent as {gold['display']['company']}, table only, no page text.", "",
         f"True metric **{_pct(vals['true_metric'])}** (`{vals['true_outcome']}`), "
         f"wrong metric **{_pct(vals['wrong_metric'])}** (`{vals['wrong_outcome']}`), "
         f"threshold **{100 * pol['threshold_rounded']:.1f}%**.", "",
         "| Version | Turns | Starts from | Final metric | Outcome | Acceptable next actions | "
         "Invalid approval | Unnecessary block |",
         "|---|---:|---|---:|---|---|---|---|"]
    for key, v in gold["versions"].items():
        f = v["final"]
        L.append(f"| {key} {v['name']} | {len(v['turns'])} | {v['start']} | {_pct(f['metric'])} | "
                 f"`{f['policy_outcome']}` | {', '.join(f'`{a}`' for a in f['acceptable_actions'])} | "
                 f"{', '.join(f'`{a}`' for a in f['invalid_approval_actions']) or 'none'} | "
                 f"{', '.join(f'`{a}`' for a in f['unnecessary_block_actions']) or 'none'} |")
    L += ["", "## Source table", "", render_table(tables["source"]), "",
          "| Turn | Question | Program | Published |", "|---:|---|---|---:|"]
    L += [f"| {k} | {t['question']} | `{t['program']}` | {t['published']:g} |" for k, t in enumerate(src["turns"])]
    L += ["", "## Threshold margins", "",
          f"Required gap = max(0.5 pp, 10% of the threshold, 10% of the value). Threshold {pol['threshold']} "
          f"rounds to {pol['threshold_rounded']}.", "",
          "| Value | Metric | Gap | Required | Holds |", "|---|---:|---:|---:|---|"]
    L += [f"| {m['name']} | {_pct(m['metric'])} | {100 * m['gap']:.4f} pp | {100 * m['required']:.4f} pp | "
          f"{'yes' if m['gap'] >= m['required'] else 'NO'} |" for m in vals["margins"]]
    L += ["", "## Edit checks", "",
          f"- Metric cell {chk['metric_cell']}: {len(chk['metric_cell_problems'])} problems "
          "(hidden sums, derived percents, page text, totals, duplicate new value).",
          f"- `irrelevant_cells()` offered {len(chk['irrelevant_offered'])} cells; "
          f"{len(chk['irrelevant_rejected'])} failed the edit checks; version 4 uses {chk['irrelevant_cell']}."]
    for c, problems in chk["irrelevant_rejected"].items():
        L += [f"  - Cell {c} rejected:"] + [f"    - {p}" for p in problems]
    L += ["", "Page text naming the row, for checking by hand (not shown to the agent):", ""]
    L += [f"- {s}" for s in chk["page_text_naming_the_row"]]
    L += ["", "## Policy as shown to the agent", ""] + _quote(pol["text"].rstrip())
    L += ["", "## Provisional choices", ""] + [f"- {p}" for p in gold["provisional"]] + [""]
    for key, v in gold["versions"].items():
        f = v["final"]
        L += [f"## Version {key}: {v['name']}", "", v["summary"], "", "**Table changes**", ""]
        diff = _table_diff(tables["source"], tables[v["start"]])
        L += [f"- Starts from `{v['start']}`: " + ("same as the source table." if not diff else
              "all figures pending." if v["start"] == "empty" else "; ".join(diff))]
        L += [f"- Ends with the source table: {'yes' if f['record'] == tables['source'] else 'NO'}.", "",
              "**Conversation**", ""]
        for t in v["turns"]:
            L += _turn_lines(t)
        L += ["**Gold**", "",
              f"- Final metric {_pct(f['metric'])}, outcome `{f['policy_outcome']}`.",
              "- Acceptable next actions: " + ", ".join(f"`{a}`" for a in f["acceptable_actions"]) + ".",
              "- Counted as an invalid approval: " + (", ".join(f"`{a}`" for a in f["invalid_approval_actions"]) or "none") + ".",
              "- Counted as an unnecessary block: " + (", ".join(f"`{a}`" for a in f["unnecessary_block_actions"]) or "none") + ".",
              "- Expected record changes: " + ("; ".join(
                  f"turn {c['turn']}, `{c['row_label']}` {c['col_label']} to `{c['to']}` citing `{c['evidence']}`"
                  for c in f["expected_record_changes"]) or "none") + ".",
              "- Tentative outcomes: " + (", ".join(f"`{o}`" for o in f["tentative_outcomes"]) or "none")
              + (". The final decision reverses the tentative one." if f["decision_reversed"] else "."),
              f"- Final facts SHA-256: `{f['facts_sha256'][:16]}`.", ""]
    return "\n".join(L)


# ---------------------------------------------------------------- run


def dump_gold(gold: dict) -> str:
    return json.dumps(gold, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_outputs(families: Path, gold: dict) -> list[Path]:
    """The only writes in this module; everything lands under `families`."""
    gold_path = families / gold["family_id"] / "gold.json"
    review_path = families / "review" / f"{gold['family_id']}.md"
    for path, text in ((gold_path, dump_gold(gold)), (review_path, render_review(gold))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return [gold_path, review_path]


def run(root: Path, out: Optional[Path] = None, check: bool = False,
        log: Callable[[str], None] = print) -> int:
    fetch.verify(fetch.Paths(root), log=log)
    families = root / "data" / "families"
    out = out or families
    ids = sorted(p.parent.name for p in families.glob("*/family.json"))
    log(f"  read {len(ids)} family spec(s) from {families.relative_to(root)}")
    failed = []
    for family_id in ids:
        gold = build(root, family_id)
        versions = gold["versions"]
        turns = [t for v in versions.values() for t in v["turns"]]
        runs = [r for t in turns for r in t["programs"]]
        log(f"  {family_id}: {len(gold['source']['turns'])} source turns -> {len(versions)} versions, "
            f"{len(turns)} scripted turns, {len(runs)} programs re-executed "
            f"({sum(r['edited'] for r in runs)} edited)")
        log(f"  {family_id}: irrelevant_cells() offered {len(gold['checks']['irrelevant_offered'])}, "
            f"{len(gold['checks']['irrelevant_rejected'])} rejected by the edit checks")
        if check:
            path = out / family_id / "gold.json"
            saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            if saved != gold:
                failed.append(family_id)
            log(f"  {family_id}: saved gold {'matches' if saved == gold else 'DIFFERS FROM'} the rebuild")
        else:
            for path in write_outputs(out, gold):
                log(f"  wrote {path.relative_to(out.parent) if out == families else path}")
    if failed:
        log("STOP: gold.json differs from a rebuild for " + ", ".join(failed))
        return 1
    log(f"OK: {len(ids)} family(ies) built, every rule passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    ap.add_argument("--check", action="store_true", help="compare with saved gold.json, write nothing")
    args = ap.parse_args(argv)
    try:
        return run(args.root, check=args.check)
    except (FamilyError, fetch.FetchError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
