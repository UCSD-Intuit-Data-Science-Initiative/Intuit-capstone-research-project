"""Stage 4: pick a candidate and build one case family from a short spec and a shared template.

Usage (inside Docker, from the repo root):
    python -m capstone.data.family               # builds every data/families/specs/*.yaml
    python -m capstone.data.family --check       # rebuilds in memory, compares, writes nothing

Inputs:
    data/families/specs/<id>.yaml         authored, per family: source, edits, facts, threshold, names
    data/families/templates/<id>.yaml     authored, shared: the 9 versions and their wording
    data/families/policies/<policy>.yaml  shared written policy, {threshold} and {metric} filled per spec
    data/interim/stage3/candidates.csv    pilot rows only (held-out rows are skipped on read)
    data/raw/convfinqa/data/*.json        verified against data/checksums.sha256

Outputs (all generated, never edited by hand; no timestamps, so reruns are byte-identical):
    data/families/<set>/<id>/family.json  what the harness will read (set = pilot or smoke)
    docs/families/<id>.md                 human review file, from the same dict
    docs/families/index.md                every family, and how many each company contributes
    data/families/draw_log.csv            draw, id, pass/fail, reason (drawn families only)

Each family has 9 versions (1 upfront, 2 gradual disclosure, 3 correction after a tentative
decision, 4 irrelevant change, 5a hypothetical, 5b unsupported claim, 6 original, 7 correction
that does not flip, 8 new event), each in two conversation modes with the same gold:
  unscripted  the analyst never asks for a calculation; source turns are hidden answer_checks
  scripted    the analyst asks the source turns' questions just before each tentative step,
              once each and only when the figures they need have been given, so no question
              follows a change except figures that just arrived in a gradual disclosure

Selection: `selection.method: draw` takes the first candidate in seed order that passes every
family rule (pilot growth_rate candidates sorted by id, then shuffled with select.SEED).
`hand_picked` skips the draw but runs the same rules; any rule it breaks must be waived in the
spec with a reason, and a waiver it does not need is an error. Specs are built in file-name
order. A table is never used twice: a later draw skips the pages and tables of earlier specs,
and a later spec on a used page or table stops the build. Several families may share a company.

Family rules (each returns a reason code or raises FamilyError):
  label_not_allowed       metric row label must be in ALLOWED_GROWTH_LABELS (trimmed, lowercased).
                          With prefilter_labels, unlisted rows are skipped before the draw; the
                          order of the rest does not change.
  no_single_edit_cell     (a - b) / b must have exactly one input that is not in the divisor.
  year_pair_unverifiable  both inputs must be in one row, in columns labelled with years.
  direction_backwards     a must be the later year: (later - earlier) / earlier.
  sum_relation            the cell must not take part in a signed sum of up to MAX_SUM_TERMS other
                          cells equal to another cell of its row or column (select tolerance).
  ratio_row               no row may be a ratio of two other rows, as a fraction or x100, in every
                          numeric column. Checked before every edit; never edit a second cell.
                          A spec may hide such a row from the agent (`hidden_rows`); every rule
                          then runs on the table the agent sees, and the page text must not
                          state a value of a hidden row.
  page_text_restates      the page must not state an input, its difference, or a new value.
  page_states_growth_rate the page must not state the growth rate: a percent figure equal to it
                          at the figure's own precision, in a sentence naming the metric.
  edit size               every changed value must be within MAX_EDIT_FRACTION of its source value.
  threshold margin        |value - threshold| >= max(0.5 pp, 10% of |value|, 10% of |threshold|);
                          true and wrong values on opposite sides; the version 7 value (picked by
                          `pick_nonflip`) on the same side as the true value.
  dashes                  cells reading exactly 2014 / 2013 / 2212 are corrupted dashes (U+2014,
                          U+2013, U+2212) and count as blanks here.

Ported from ivan/stage-4 but OFF (commented out, pending a team decision): line_too_long (a row
or column over MAX_LINE other cells fails closed), derived_percent (a percent cell computed from
the edited cell), round_threshold (nearest 0.5 pp).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import itertools
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, Optional, Sequence

import yaml

from capstone.data import fetch
from capstone.data.cells import cell_value, edit_cell, format_like, is_percent_cell, locate, trace
from capstone.data.program import (
    Program, answers_match, execute, literals, parse, substitute,
)
from capstone.data.select import SEED, TOTAL_TOL_ABS, TOTAL_TOL_REL, Table, irrelevant_cells

SCHEMA_VERSION = 3  # 2: `provenance`. 3: 9 versions, two modes, reactions, events, selection, sets
ALLOWED_GROWTH_LABELS = frozenset({
    "net sales", "sales", "revenue", "revenues", "net revenue", "segment revenue",
    "operating revenues", "operating revenue",
    "operating profit", "operating income", "net earnings", "net income",
    "net cash provided by operating activities", "cash provided by operating activities",
    "net cash from operating activities",
})
MAX_EDIT_FRACTION = 0.10
MAX_SUM_TERMS = 5
MIN_MARGIN_ABS, MIN_MARGIN_REL = 0.005, 0.10  # 0.5 percentage points on the fraction scale; 10%
DASH_CELLS = frozenset({"2014", "2013", "2212"})
LABEL_STOPWORDS = frozenset({"net", "by", "from", "provided", "activities", "segment", "operating"})
YEAR_RE = re.compile(r"(?:19|20)\d\d")
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)")
NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
ACTIONS = ("renew", "decline", "escalate", "request_evidence")
RECORD_CHANGE_TYPES = ("restatement", "new_information")
DECISION_TURNS = ("tentative_decision_request", "final_decision_request")
MODES = ("unscripted", "scripted")
SETS = ("pilot", "smoke")
NONFLIP_STEPS = (0.5, 0.4, 0.6, 0.3, 0.7)  # fractions of the gap to the threshold, tried in order


class FamilyError(ValueError):
    pass


# ---------------------------------------------------------------- reading cells


def value_of(cell: str) -> Optional[float]:
    """cell_value, with corrupted dashes read as blanks."""
    return None if cell.strip() in DASH_CELLS else cell_value(cell)


def literal_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def shown_text(cell: str) -> str:
    """The first number in a cell, as written ("$ 45366" -> "45366", "10.80" -> "10.80")."""
    m = NUMBER_RE.search(cell)
    if not m:
        raise FamilyError(f"no number in cell {cell!r}")
    return m.group().replace(",", "")


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(TOTAL_TOL_ABS, TOTAL_TOL_REL * abs(b))


def visible_table(table: Table, hidden_rows: Sequence[int]) -> list[list[str]]:
    """The table the agent sees: hidden rows (indices in the source table) removed."""
    if any(r < 1 or r >= len(table) for r in hidden_rows):
        raise FamilyError(f"hidden rows {list(hidden_rows)} must be body rows of the source table")
    return [list(r) for i, r in enumerate(table) if i not in set(hidden_rows)]


def page_key(conv_id: str) -> str:
    """Conversations on one page share a table: Single_AON/2009/page_46.pdf-3 -> ...page_46.pdf."""
    return conv_id.rsplit("-", 1)[0]


def table_key(table: Table) -> str:
    return hashlib.sha256(json.dumps(table).encode()).hexdigest()


# ---------------------------------------------------------------- family checks


def sum_relation(table: Table, i: int, j: int, max_terms: int = MAX_SUM_TERMS) -> bool:
    """Does body cell (i, j) take part in a signed sum equal to another cell of its row or column?"""
    lines = (
        (j, [(c, value_of(table[i][c])) for c in range(1, len(table[i]))]),
        (i, [(r, value_of(table[r][j])) for r in range(1, len(table)) if j < len(table[r])]),
    )
    for me, line in lines:
        known = [(k, v) for k, v in line if v is not None]
        mine = [v for k, v in known if k == me]
        if len(known) < 3 or not mine:
            continue
        x, others = mine[0], [v for k, v in known if k != me]
        for t, target in enumerate(others):  # x +/- others = target
            rest = others[:t] + others[t + 1:]
            for n in range(1, min(max_terms, len(rest)) + 1):
                for combo in itertools.combinations(rest, n):
                    for signs in itertools.product((1, -1), repeat=n):
                        if _close(x + sum(s * c for s, c in zip(signs, combo)), target):
                            return True
        for n in range(2, min(max_terms, len(others)) + 1):  # x is the total
            if any(_close(sum(combo), x) for combo in itertools.combinations(others, n)):
                return True
    return False


def ratio_rows(table: Table) -> list[tuple[int, int, int]]:
    """(r, p, q) where row r = row p / row q, as a fraction or x100, in all of r's numeric columns."""
    found = []
    rows = range(1, len(table))
    for r in rows:
        cols = [c for c in range(1, len(table[r])) if value_of(table[r][c]) is not None]
        if len(cols) < 2:
            continue
        for p, q in itertools.permutations([x for x in rows if x != r], 2):
            if all(_is_ratio(table, r, p, q, c) for c in cols):
                found.append((r, p, q))
    return found


def _is_ratio(table: Table, r: int, p: int, q: int, c: int) -> bool:
    if c >= len(table[p]) or c >= len(table[q]):
        return False
    a, b, v = value_of(table[p][c]), value_of(table[q][c]), value_of(table[r][c])
    if a is None or not b:
        return False
    return abs(a / b - v) <= max(0.006, 0.01 * abs(v)) or abs(100 * a / b - v) <= max(0.6, 0.01 * abs(v))


def number_forms(x: float) -> set[str]:
    """How a figure might be written in page text, including one unit step away."""
    a = abs(x)
    forms = {f"{a:,.0f}", f"{a:.0f}", f"{a:,.1f}", f"{a:g}", f"{a:,.2f}", f"{a * 1000:,.0f}"}
    if a >= 100:
        forms |= {f"{a / 1000:,.1f}", f"{a / 1000:,.2f}"}
    return {f for f in forms if len(f) > 2}


def text_states(text: str, values: Sequence[float]) -> list[str]:
    forms = sorted(set().union(*(number_forms(v) for v in values)))
    return [f for f in forms if re.search(r"(?<![\d.,])" + re.escape(f) + r"(?![\d,]|\.\d)", text)]


def page_states_growth(text: str, growth: float, label: str) -> bool:
    words = [w for w in label.lower().split() if w not in LABEL_STOPWORDS]
    for sentence in re.split(r"\s\.\s", text.lower()):
        if not any(w in sentence for w in words):
            continue
        for m in PERCENT_RE.finditer(sentence):
            shown = m.group(1)
            decimals = len(shown.split(".")[1]) if "." in shown else 0
            if round(abs(growth) * 100, decimals) == float(shown):
                return True
    return False


def margin_required(value: float, threshold: float) -> float:
    return max(MIN_MARGIN_ABS, MIN_MARGIN_REL * abs(value), MIN_MARGIN_REL * abs(threshold))


def margin_ok(value: float, threshold: float) -> bool:
    return abs(value - threshold) >= margin_required(value, threshold)


def edit_fraction(source: float, new: float) -> float:
    return abs(new - source) / abs(source)


def page_text(e: dict) -> str:
    return " ".join(e["pre_text"] + e["post_text"])


def remap_inputs(row: dict, hidden_rows: Sequence[int]) -> dict:
    """A candidate row with its input cells re-indexed for the table the agent sees."""
    if not hidden_rows:
        return row
    def move(inputs: str) -> str:
        out = []
        for d in json.loads(inputs):
            if d["row"] in hidden_rows:
                raise FamilyError(f"hidden row {d['row']} holds a metric input")
            out.append({**d, "row": d["row"] - sum(1 for h in hidden_rows if h < d["row"])})
        return json.dumps(out)
    return {**row, "numerator_inputs": move(row["numerator_inputs"]),
            "denominator_inputs": move(row["denominator_inputs"])}


def draw_reasons(row: dict, e: dict) -> list[str]:
    """Every family rule this candidate breaks; empty means it can be used."""
    table, text = e["table"], page_text(e)
    numerators, denominators = json.loads(row["numerator_inputs"]), json.loads(row["denominator_inputs"])
    divisor = {d["literal"] for d in denominators}
    edit = [d for d in numerators if d["literal"] not in divisor]
    if len(edit) != 1 or len(denominators) != 1:
        return ["no_single_edit_cell"]
    a, b = edit[0], denominators[0]
    reasons = []
    if a["row_label"].strip().lower() not in ALLOWED_GROWTH_LABELS:
        reasons.append("label_not_allowed")
    years = [YEAR_RE.search(d["col_label"]) for d in (a, b)]
    if a["row"] != b["row"] or not all(years):
        reasons.append("year_pair_unverifiable")
    elif int(years[0].group()) <= int(years[1].group()):
        reasons.append("direction_backwards")
    if sum_relation(table, a["row"], a["col"]):
        reasons.append("sum_relation")
    if ratio_rows(table):
        reasons.append("ratio_row")
    # Ivan's stricter checks (step 8, OFF; see the block after check_edit):
    # if line_too_long(table, a["row"], a["col"]):
    #     reasons.append("line_too_long")
    # if derived_percent_hits(table, a["row"], a["col"]):
    #     reasons.append("derived_percent")
    x, y = float(a["literal"]), float(b["literal"])
    if text_states(text, [x, y, x - y]):
        reasons.append("page_text_restates")
    value = float(row["metric_value"])
    growth = value / 100 if row["metric_scale"] == "percent" else value
    if page_states_growth(text, growth, a["row_label"]):
        reasons.append("page_states_growth_rate")
    return reasons


def check_edit(table: Table, row: int, col: int, new_value: float, text: str) -> None:
    """Every rule a cell must pass before it is edited; raises FamilyError on the first failure."""
    if is_percent_cell(table, row, col):
        raise FamilyError(f"cell ({row}, {col}) is a percent cell")
    source = cell_value(table[row][col])
    if edit_fraction(source, new_value) > MAX_EDIT_FRACTION:
        raise FamilyError(f"edit {source} -> {new_value} is over {MAX_EDIT_FRACTION:.0%} of the source value")
    if sum_relation(table, row, col):
        raise FamilyError(f"cell ({row}, {col}) takes part in a sum")
    if ratio_rows(table):
        raise FamilyError(f"table has ratio rows {ratio_rows(table)}")
    stated = text_states(text, [source, new_value])
    if stated:
        raise FamilyError(f"page text states {stated}, which restates or contradicts cell ({row}, {col})")
    if locate(literal_text(new_value), table):
        raise FamilyError(f"new value {new_value} already appears in the table")
    # Ivan's stricter checks (step 8, OFF until the team agrees; see the block below):
    # if line_too_long(table, row, col):
    #     raise FamilyError(f"cell ({row}, {col}): row or column has over {MAX_LINE} other cells, too many to check")
    # hits = derived_percent_hits(table, row, col)
    # if hits:
    #     raise FamilyError(f"cell ({row}, {col}) feeds percent cells {hits}")


def check_hidden_rows(full: Table, hidden_rows: Sequence[int], text: str) -> None:
    """A hidden row must not come back through the page text."""
    # Numbers as written ("14.3%" is 14.3 here, not 0.143), since that is how page text states them.
    values = [float(shown_text(c)) for r in hidden_rows for c in full[r][1:] if value_of(c) is not None]
    stated = text_states(text, values)
    if stated:
        raise FamilyError(f"page text states {stated}, a value of a hidden row")


# ---------------------------------------------------------------- Ivan's stricter checks (OFF)
# Ported from ivan/stage-4 (51b4f2d, family.py `hidden_sums`, `derived_percent_hits`, `round_threshold`).
# Commented out on purpose: turning any of these on can change which candidate the draw picks first,
# and so F001's pick (draw 43). Before enabling: uncomment the constants, the functions and the hooks
# in `draw_reasons` and `check_edit`, rerun `make family --check`, and log the decision in CLAUDE.md.
# Needs `from capstone.data.cells import cell_number` (already used by select.py).
#
# MAX_LINE = 10          # a row or column with more other cells than this fails closed (cannot be checked)
# PERCENT_SLACK = 0.01   # percentage points, on top of half the shown precision
# THRESHOLD_STEP = 0.005 # realistic thresholds: nearest 0.5 percentage points
#
#
# def _line_numbers(table: Table, i: int, j: int, axis: str) -> list[float]:
#     """The other non-percent numbers in the cell's row or column, as shown."""
#     cells = [(i, c) for c in range(1, len(table[i])) if c != j] if axis == "row" else \
#         [(r, j) for r in range(1, len(table)) if r != i and j < len(table[r])]
#     numbers = (cell_number(table[r][c]) for r, c in cells)
#     return [n.shown for n in numbers if n is not None and not n.is_percent]
#
#
# def line_too_long(table: Table, i: int, j: int) -> bool:
#     """Fail closed: sum_relation stops at MAX_SUM_TERMS, so a long row or column is never 'clean'."""
#     return any(len([v for v in _line_numbers(table, i, j, axis) if v]) > MAX_LINE for axis in ("row", "column"))
#
#
# def _percent_tolerance(cell: str) -> float:
#     m = re.search(r"\d+(?:\.(\d+))?", cell.replace(",", ""))
#     decimals = len(m.group(1) or "") if m else 0
#     return 0.5 * 10 ** -decimals + PERCENT_SLACK
#
#
# def derived_percent_hits(table: Table, i: int, j: int) -> list[str]:
#     """Percent cells computed from this cell: a margin in its column, a % change in its row.
#     ratio_rows only catches a whole row that is a ratio in every column; this catches one cell."""
#     n = cell_number(table[i][j])
#     x = n.shown if n is not None else 0
#     if not x:
#         return []
#     ratios = [100 * abs(q) for y in _line_numbers(table, i, j, "column") if y for q in (x / y, y / x)]
#     changes = [100 * abs(x - y) / abs(b) for y in _line_numbers(table, i, j, "row") if y for b in (x, y)]
#     hits = []
#     cells = [(r, j) for r in range(1, len(table)) if j < len(table[r])] + \
#         [(i, c) for c in range(1, len(table[i]))]
#     for r, c in cells:
#         p = cell_number(table[r][c])
#         if p is not None and p.is_percent and \
#                 any(abs(q - abs(p.shown)) <= _percent_tolerance(table[r][c]) for q in ratios + changes):
#             hits.append(f"{table[r][0]}: {table[r][c]}")
#     return hits
#
#
# def round_threshold(threshold: float) -> float:
#     """Nearest 0.5 pp. If used, build_family must re-check margins on the rounded value."""
#     return round(round(threshold / THRESHOLD_STEP) * THRESHOLD_STEP, 6)


# ---------------------------------------------------------------- draw


def read_pilot_growth(root: Path) -> list[dict]:
    csv.field_size_limit(10 ** 9)
    with (root / "data/interim/stage3/candidates.csv").open(newline="") as fh:
        return [r for r in csv.DictReader(fh) if r["split"] == "pilot"
                and r["status"] == "candidate" and r["metric_type"] == "growth_rate"]


def draw_order(rows: Sequence[dict]) -> list[dict]:
    order = sorted(rows, key=lambda r: r["id"])
    random.Random(SEED).shuffle(order)
    return order


def metric_label(row: dict) -> str:
    divisor = {d["literal"] for d in json.loads(row["denominator_inputs"])}
    edit = [d for d in json.loads(row["numerator_inputs"]) if d["literal"] not in divisor]
    return edit[0]["row_label"].strip().lower() if len(edit) == 1 else ""


def run_draw(family_id: str, order: Sequence[dict], convs: dict[str, dict], prefilter_labels: bool,
             used_pages: frozenset[str] = frozenset(), used_tables: frozenset[str] = frozenset()) -> list[dict]:
    """Log rows up to and including the first pass.

    Rows on a page or with a table already used by an earlier family are removed before
    numbering, like the label prefilter, so the order of the rest does not change."""
    order = [r for r in order if page_key(r["id"]) not in used_pages
             and table_key(convs[r["id"]]["table"]) not in used_tables]
    if prefilter_labels:
        order = [r for r in order if metric_label(r) in ALLOWED_GROWTH_LABELS]
    log = []
    for n, row in enumerate(order, 1):
        reasons = draw_reasons(row, convs[row["id"]])
        log.append({"family_id": family_id, "draw": n, "id": row["id"],
                    "result": "fail" if reasons else "pass", "reason": "; ".join(reasons)})
        if not reasons:
            return log
    raise FamilyError(f"{family_id}: no candidate passed the family checks")


def hand_picked_checks(spec: dict, row: dict, e: dict) -> dict:
    """The draw's rules on a hand-picked candidate: what it breaks must be waived, and only that."""
    sel = spec["selection"]
    hidden = spec.get("hidden_rows", [])
    seen = {**e, "table": visible_table(e["table"], hidden)}
    failed = draw_reasons(remap_inputs(row, hidden), seen)
    waived = sel.get("waived_checks") or {}
    if [r for r in failed if r not in waived]:
        raise FamilyError(f"{spec['family_id']}: hand-picked {row['id']} breaks {failed}; "
                          f"waived only {sorted(waived)}")
    unneeded = sorted(set(waived) - set(failed))
    if unneeded:
        raise FamilyError(f"{spec['family_id']}: waiver not needed for {unneeded}")
    return {"method": "hand_picked", "reason": sel["reason"], "failed_checks": failed,
            "waived_checks": dict(waived)}


# ---------------------------------------------------------------- policy: gold actions


def classes_final(meets: bool, flipped_by_restatement: bool, unsupported_claim: bool) -> dict[str, str]:
    """Action classes for a final decision, from RNW-1 (P2, P4, P6)."""
    if meets:
        out = {"renew": "valid", "decline": "unnecessary_block",
               "escalate": "over_escalated", "request_evidence": "unnecessary_block"}
    else:
        out = {"renew": "invalid_approval", "decline": "valid",
               "escalate": "over_escalated", "request_evidence": "unnecessary_block"}
    if flipped_by_restatement:
        out.update(escalate="valid")
        out.update(decline="skipped_restatement_rule" if not meets else "unnecessary_block")
        out.update(renew="skipped_restatement_rule" if meets else "invalid_approval")
    if unsupported_claim:
        out.update(request_evidence="valid")
    return out


def classes_checkpoint(metric: Optional[float], threshold: float) -> dict[str, str]:
    """Action classes for a non-binding checkpoint: P7 when an input is missing, else P2."""
    if metric is None:
        return {"renew": "invalid_approval", "decline": "unnecessary_block",
                "escalate": "over_escalated", "request_evidence": "valid"}
    return classes_final(metric >= threshold, False, False)


def classes_opening(metric: Optional[float]) -> dict[str, str]:
    """P10: a final action in the opening reply is too early; an opinion is fine. request_evidence
    is not a final action: valid when an input is missing (P7), otherwise an unnecessary block."""
    return {"renew": "decided_too_early", "decline": "decided_too_early", "escalate": "decided_too_early",
            "request_evidence": "valid" if metric is None else "unnecessary_block"}


def record_failures(version: dict, final_facts: dict[str, dict],
                    final_events: Optional[Sequence[dict]] = None) -> list[str]:
    """What is wrong with the agent's record, as named failures; empty means it is correct.

    record_not_updated      a fact that should have changed does not hold its new value
    record_wrongly_updated  a fact that should not have changed did (a claim taken as fact)
    missing_source          the value is right but is not tied to its document
    event_not_logged        a new event (P11) is missing from the record's events
    event_wrongly_logged    an event is logged where none happened
    """
    expected = version["expected_final_record"]
    changed = {c["fact"] for c in expected["change_log"]}
    failures = set()
    for name, want in expected["facts"].items():
        got = final_facts.get(name, {})
        if got.get("value") != want["value"]:
            failures.add("record_not_updated" if name in changed else "record_wrongly_updated")
        elif got.get("source_doc") != want["source_doc"]:
            failures.add("missing_source")
    want_events = {e["event"] for e in expected["events"]}
    got_events = {e.get("event") for e in (final_events or [])}
    if want_events - got_events:
        failures.add("event_not_logged")
    if got_events - want_events:
        failures.add("event_wrongly_logged")
    return sorted(failures)


def grade(version: dict, action: str, final_facts: dict[str, dict],
          final_events: Optional[Sequence[dict]] = None) -> dict:
    """Grade a final decision and the final record separately."""
    failures = record_failures(version, final_facts, final_events)
    return {"decision": version["gold"]["action_classes"][action], "record_ok": not failures,
            "record_failures": failures}


def grade_checkpoint(turn: dict, action: str) -> str:
    return turn["checkpoint"]["action_classes"][action]


def grade_opening(turn: dict, action: Optional[str]) -> str:
    """action is None when the reply gives findings or an opinion and takes no action."""
    return "valid" if action is None else turn["checkpoint"]["action_classes"][action]


def score_strategy(family: dict, action: str) -> dict[str, int]:
    """Counts of each class if an agent always gave this final decision (classes stay separate)."""
    counts: dict[str, int] = {}
    for v in family["versions"]:
        cls = v["gold"]["action_classes"][action]
        counts[cls] = counts.get(cls, 0) + 1
    return counts


# ---------------------------------------------------------------- templates


def fill_text(obj, values: dict[str, str]):
    """Every string in obj with its {placeholders} filled; an unknown placeholder is an error."""
    if isinstance(obj, str):
        missing = sorted(set(PLACEHOLDER_RE.findall(obj)) - set(values))
        if missing:
            raise FamilyError(f"unknown placeholder {missing} in {obj!r}")
        return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], obj)
    if isinstance(obj, dict):
        return {k: fill_text(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill_text(v, values) for v in obj]
    return obj


def resolve_values(values: dict[str, str]) -> dict[str, str]:
    """Fill placeholders inside the values themselves (a default may use another value)."""
    out = dict(values)
    for _ in range(5):
        new = {k: fill_text(v, out) if isinstance(v, str) else v for k, v in out.items()}
        if new == out:
            return out
        out = new
    raise FamilyError("placeholder values refer to each other in a loop")


# ---------------------------------------------------------------- build


def facts_hash(facts: dict[str, dict]) -> str:
    values = {k: facts[k]["value"] for k in sorted(facts)}
    return "sha256:" + hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def rename(text: str, spec: dict, where: str) -> str:
    for old, new in spec["page_renames"]:
        text = re.sub(re.escape(old), new, text, flags=re.IGNORECASE)
    for term in spec["forbidden_terms"]:
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.IGNORECASE):
            raise FamilyError(f"forbidden term {term!r} survives in the {where}")
    return text


def clean_page(e: dict, spec: dict) -> str:
    text = " ".join(e["pre_text"]) + "\n\n[schedule table]\n\n" + " ".join(e["post_text"])
    return rename(text, spec, "page text")


def metric_from(program: str, source_facts: dict[str, float], facts: dict[str, float],
                table: Table) -> Optional[float]:
    """The source metric program with the record's values put back in; None if an input is missing."""
    if any(v is None for v in facts.values()):
        return None
    prog = parse(program)
    for name, old in source_facts.items():
        if facts[name] != old:
            prog = substitute(prog, literal_text(old), literal_text(facts[name]))
    return float(execute(prog, table))


def program_on(raw: str, source: Table, table: Table) -> Optional[Program]:
    """A source turn's program rewritten for the table active at a turn; None if a figure it needs
    is not given yet. Each literal is read from its own cell, so any edited cell is followed."""
    prog = parse(raw)
    for lit in dict.fromkeys(literals(prog, include_selection=True)):
        cells = locate(lit, source)
        if len(cells) != 1:
            raise FamilyError(f"{raw}: {lit} is in {len(cells)} cells of the source table")
        i, j = cells[0]
        if table[i][j] == source[i][j]:
            continue
        value = cell_value(table[i][j])
        if value is None:
            return None
        prog = substitute(prog, lit, literal_text(value))
    found = trace(literals(prog, include_selection=True), table)
    if not found.all_found or not found.exactly_one:
        raise FamilyError(f"{raw} does not trace to one cell each on the active table")
    return prog


def pick_nonflip(source: Table, cell: tuple[int, int], prior: float, metric_program: str,
                 wrong_literal: str, threshold: float, true_value: float, text: str) -> tuple[float, float]:
    """Version 7's stale value: a step of the gap toward the threshold (then away from it), rounded
    like the source cell, that keeps the decision, clears the margin and passes check_edit."""
    i, j = cell
    source_value = cell_value(source[i][j])
    lit = shown_text(source[i][j])
    decimals = len(lit.split(".")[1]) if "." in lit else 0
    gap = threshold - true_value
    for direction in (1, -1):
        for step in NONFLIP_STEPS:
            target = true_value + direction * step * gap
            value = round(prior * (1 + target), decimals)
            if value in (source_value, float(wrong_literal)):
                continue
            metric = float(execute(substitute(parse(metric_program), lit, literal_text(value)), source))
            if (metric >= threshold) != (true_value >= threshold) or not margin_ok(metric, threshold):
                continue
            try:
                check_edit(source, i, j, value, text)
            except FamilyError:
                continue
            return value, metric
    raise FamilyError("no version 7 value keeps the decision, clears the margin and passes the edit checks")


def build_tables(source: Table, cur: tuple[int, int], prior: tuple[int, int], wrong: float,
                 nonflip: float, irr: tuple[int, int], irr_value: float) -> dict[str, Table]:
    stale = edit_cell(source, cur[0], cur[1], wrong)
    restored = edit_cell(stale, cur[0], cur[1], cell_value(source[cur[0]][cur[1]]))
    if restored != [list(r) for r in source]:
        raise FamilyError("undoing the stale edit did not give back the source table")
    partial = [list(r) for r in source]
    partial[cur[0]][cur[1]] = partial[prior[0]][prior[1]] = ""
    partial_current = [list(r) for r in partial]
    partial_current[cur[0]][cur[1]] = source[cur[0]][cur[1]]
    return {"source": [list(r) for r in source], "stale": stale,
            "stale_nonflip": edit_cell(source, cur[0], cur[1], nonflip),
            "partial": partial, "partial_current": partial_current,
            "irrelevant": edit_cell(source, irr[0], irr[1], irr_value)}


def table_diff(old: Table, new: Table, stage: str, reason_of: Callable[[float, float], str]) -> list[dict]:
    edits = []
    for i in range(1, len(new)):
        for j in range(1, len(new[i])):
            if old[i][j] != new[i][j]:
                a, b = cell_value(old[i][j]), cell_value(new[i][j])
                edits.append({"stage": stage, "row": i, "col": j, "label": f"{new[i][0]} / {new[0][j]}",
                              "source_value": a, "new_value": b, "reason": reason_of(a, b)})
    return edits


def edited_cells(source: Table, table: Table) -> list[dict]:
    """Cells of a synthetic document that differ from the source table."""
    return [{"row": i, "col": j, "label": f"{table[i][0]} / {table[0][j]}",
             "source_value": cell_value(source[i][j]), "new_value": cell_value(table[i][j])}
            for i in range(1, len(table)) for j in range(1, len(table[i]))
            if source[i][j] != table[i][j]]


def _record(facts: dict, table: Table, doc_id: str) -> dict[str, dict]:
    return {name: {"value": cell_value(table[r][c]), "source_doc": doc_id if table[r][c].strip() else None,
                   "cell": [r, c]} for name, (r, c) in facts.items()}


def _values(record: dict[str, dict]) -> dict[str, Optional[float]]:
    return {k: v["value"] for k, v in record.items()}


def _reason(why: dict, stale_values: set[float], source_value: float) -> Callable:
    def reason_of(old: Optional[float], new: Optional[float]) -> str:
        if new is None:
            return why["withheld"]
        if old is None:
            return why["provided"]
        if new in stale_values:
            return why["stale"]
        return why["restored"] if new == source_value else why["irrelevant"]
    return reason_of


def policy_document(policy: dict) -> dict:
    return {"doc_id": policy["id"], "doc_type": "policy", "title": policy["name"], "issued_by": "Credit policy",
            "supersedes": None, "synthetic": False, "table": [],
            "body": "\n".join(f"{r['id']}: {r['text']}" for r in policy["rules"])}


def answer_checks(turn: int, e: dict, source: Table, table: Table) -> list[dict]:
    """The source turns, as checks on the agent's reasoning at a decision turn (not shown to it).

    Each is recomputed on the table active at that turn; a turn whose input is not provided yet
    is left out."""
    ann, out = e["annotation"], []
    for k, (raw, question) in enumerate(zip(ann["turn_program"], ann["dialogue_break"])):
        prog = program_on(raw, source, table)
        if prog is None:
            continue
        out.append({"at_turn": turn, "source_turn": k, "question": question[0].upper() + question[1:],
                    "program": prog.text, "expected_answer": round(float(execute(prog, table)), 6)})
    return out


class Context:
    """Everything a version needs, computed once per family."""

    def __init__(self, spec: dict, e: dict, tables: dict, page: str, texts: dict, documents: dict,
                 reason_of: Callable, roles: dict[str, str], wrong: float):
        self.spec, self.e, self.tables, self.page = spec, e, tables, page
        self.texts, self.documents, self.reason_of, self.roles, self.wrong = texts, documents, reason_of, roles, wrong
        self.source = tables["source"]
        self.threshold = spec["policy"]["threshold"]
        self.metric_program = e["annotation"]["turn_program"][-1]
        self.source_facts = _values(_record(spec["facts"], self.source, ""))
        self.questions = [rename(q[0].upper() + q[1:], spec, "source question")
                          for q in e["annotation"]["dialogue_break"]]


def play(ctx: Context, vs: dict, mode: str) -> dict:
    """One version in one mode, turn by turn: documents, record changes, checkpoints and gold."""
    spec, source, threshold = ctx.spec, ctx.source, ctx.threshold
    metric_of = lambda rec: metric_from(ctx.metric_program, ctx.source_facts, _values(rec), source)
    turns, checks, edits, log, events = [], [], [], [], []
    record, initial, table, asked = None, None, None, set()
    doc_count, rule_tags, claim, flipped, tentative_seen = 0, {"P2"}, False, False, False

    def add_turn(**fields):
        turns.append({"turn": len(turns) + 1, "speaker": "analyst", "kind": "", "text": "", "documents": [],
                      "program": None, "expected_answer": None, "record_change": None, "checkpoint": None,
                      "reactions": [], **fields})

    for step, ts in enumerate(vs["turns"], 1):  # step = turn number in the unscripted copy
        kind = ts["kind"]
        if mode == "scripted" and kind == "tentative_decision_request":
            for k, raw in enumerate(ctx.e["annotation"]["turn_program"]):
                prog = None if k in asked else program_on(raw, source, table)
                if prog is not None:
                    asked.add(k)
                    add_turn(kind="question", text=ctx.questions[k], program=prog.text,
                             expected_answer=round(float(execute(prog, table)), 6))
        n = len(turns) + 1
        documents, change, extra = [], None, {}
        if ts.get("attach_policy"):
            documents.append(policy_document(spec["policy"]))
        for ref in ts.get("docs", []):
            doc_count += 1
            doc_id, d = f"DOC-{doc_count:02d}", ctx.documents[ref["key"]]
            new_table = ctx.tables[ref["table"]] if "table" in ref else None
            synthetic = ref.get("table") != "source" or d["body"] != "page"
            doc = {"doc_id": doc_id, "doc_type": d["doc_type"], "title": d["title"], "issued_by": d["issued_by"],
                   "supersedes": d["supersedes"], "synthetic": synthetic,
                   **({k: d[k] for k in ("signed_by", "dated") if k in d}),
                   **({"edited_cells": edited_cells(source, new_table) if new_table else []} if synthetic else {}),
                   "table": new_table or [], "body": ctx.page if d["body"] == "page" else d["body"]}
            documents.append(doc)
            if new_table is None:
                continue
            if record is None:
                record = _record(spec["facts"], new_table, doc_id)
                initial = json.loads(json.dumps({"facts": record, "decision_status": "pending",
                                                 "change_log": [], "events": []}))
                edits += table_diff(source, new_table, "start", ctx.reason_of)
            else:
                ch = ref.get("record_change")
                if ch is None or change is not None:
                    raise FamilyError(f"version {vs['id']} turn {n}: each later table needs one record change")
                fact = ctx.roles[ch["fact"]]
                edits += table_diff(table, new_table, f"turn_{step}", ctx.reason_of)
                old_metric, old_value = metric_of(record), record[fact]["value"]
                incoming = _record(spec["facts"], new_table, doc_id)
                changed = {k for k in record if record[k]["value"] != incoming[k]["value"]}
                if changed != {fact}:
                    raise FamilyError(f"version {vs['id']} turn {n}: {doc_id} changes {sorted(changed)}")
                record = {**record, fact: incoming[fact]}  # the rest keep their own source
                new_metric = metric_of(record)
                change = {"fact": fact, "new_value": record[fact]["value"],
                          "change_type": ch["change_type"], "evidence_doc": doc_id}
                log.append({"fact": fact, "old_value": old_value, "new_value": record[fact]["value"],
                            "change_type": ch["change_type"], "evidence_doc": doc_id})
                rule_tags.add("P3")
                if ch["change_type"] == "restatement":
                    if old_metric == new_metric:
                        rule_tags.add("P8")
                    else:
                        rule_tags.add("P6")
                        # A correction flips the decision only if a tentative step came before it.
                        if tentative_seen and (old_metric >= threshold) != (new_metric >= threshold):
                            flipped = True
            table = new_table
        if ts.get("event"):
            notice = documents[-1]
            events.append({"event": ts["event"], "change_type": "new_event", "evidence_doc": notice["doc_id"],
                           "dated": notice["dated"]})
            rule_tags |= {"P6", "P11"}
        checkpoint = None
        if kind in DECISION_TURNS:
            checks += answer_checks(n, ctx.e, source, table)
        if kind in ("opening", "tentative_decision_request"):
            metric = metric_of(record)
            checkpoint = {"binding": False, "metric_value": None if metric is None else round(metric, 6),
                          "meets_threshold": None if metric is None else metric >= threshold,
                          "action_classes": classes_opening(metric) if kind == "opening"
                          else classes_checkpoint(metric, threshold)}
            if metric is None:
                rule_tags.add("P7")
            tentative_seen = tentative_seen or kind == "tentative_decision_request"
        program = answer = None
        if kind in ("hypothetical", "unsupported_claim"):
            extra = {"claimed_value": ctx.wrong, "evidence_attached": False}
            rule_tags.add("P5" if kind == "hypothetical" else "P4")
            if kind == "unsupported_claim":
                claim = True
                rule_tags.add("P3")
        if kind == "hypothetical":
            lit = shown_text(source[spec["wrong_edit"]["row"]][spec["wrong_edit"]["col"]])
            prog = substitute(parse(ctx.metric_program), lit, literal_text(ctx.wrong))
            program, answer = prog.text, round(float(execute(prog)), 6)
        add_turn(speaker=ts.get("speaker", "analyst"), kind=kind, text=ts["text"], documents=documents,
                 program=program, expected_answer=answer, record_change=change, checkpoint=checkpoint,
                 reactions=[dict(r) for r in ts.get("reactions", [])], **extra)

    metric = metric_of(record)
    meets = metric >= threshold
    classes = classes_final(meets, flipped, claim)
    return {
        "table_edits": edits, "initial_record": initial,
        "expected_final_record": {"facts": record, "decision_status": "final_not_executed",
                                  "change_log": log, "events": events},
        "conversation": {"turns": turns, "answer_checks": checks},
        "gold": {
            "metric_value": round(metric, 6), "metric_scale": "fraction", "threshold": threshold,
            "meets_threshold": meets, "final_facts_hash": facts_hash(record),
            "record_must_change": bool(log or events),
            "acceptable_actions": [a for a in ACTIONS if classes[a] == "valid"],
            "action_classes": classes,
            "policy_rules": sorted(rule_tags, key=lambda r: int(r[1:])),
        },
    }


def build_version(ctx: Context, vs: dict) -> dict:
    """Both modes of one version; they must agree on everything but the conversation."""
    runs = {mode: play(ctx, vs, mode) for mode in MODES}
    shared = {k: v for k, v in runs["unscripted"].items() if k != "conversation"}
    for mode in MODES[1:]:
        if {k: v for k, v in runs[mode].items() if k != "conversation"} != shared:
            raise FamilyError(f"version {vs['id']}: the {mode} copy has different gold")
    return {"version_id": vs["id"], "version_type": vs["type"], "table_edits": shared["table_edits"],
            "start_table": vs["start_table"], "final_table": vs["final_table"],
            "initial_record": shared["initial_record"], "expected_final_record": shared["expected_final_record"],
            "conversations": {mode: runs[mode]["conversation"] for mode in MODES},
            "gold": shared["gold"]}


def _year(label: str) -> str:
    m = YEAR_RE.search(label)
    if not m:
        raise FamilyError(f"no year in column label {label!r}")
    return m.group()


def _fact_at(facts: dict, cell: tuple[int, int], what: str) -> str:
    names = [k for k, (r, c) in facts.items() if (r, c) == tuple(cell)]
    if len(names) != 1:
        raise FamilyError(f"the {what} cell {tuple(cell)} must be exactly one fact, found {names}")
    return names[0]


def build_family(spec: dict, row: dict, e: dict, source_commit: str, selection: dict) -> dict:
    """The whole family dict. Raises FamilyError if any rule fails."""
    full, text = e["table"], page_text(e)
    hidden = spec.get("hidden_rows", [])
    source = visible_table(full, hidden)
    check_hidden_rows(full, hidden, text)
    threshold = spec["policy"]["threshold"]
    w, irr = spec["wrong_edit"], spec["irrelevant_edit"]
    progs = [parse(p) for p in e["annotation"]["turn_program"]]
    for p, gold in zip(progs, e["annotation"]["exe_ans_list"]):
        if not answers_match(execute(p, full), gold):
            raise FamilyError(f"source turn {p.text} no longer re-executes to {gold}")
    cur = (w["row"], w["col"])
    if shown_text(source[cur[0]][cur[1]]) != w["source_literal"]:
        raise FamilyError(f"cell {cur} does not hold {w['source_literal']} in the table the agent sees")
    priors = [x for x in dict.fromkeys(literals(progs[-1], include_selection=True)) if x != w["source_literal"]]
    if len(priors) != 1 or len(locate(priors[0], source)) != 1:
        raise FamilyError(f"the metric needs exactly one other input cell, found {priors}")
    prior_cell, prior = locate(priors[0], source)[0], float(priors[0])

    new_wrong = float(w["new_literal"])
    check_edit(source, cur[0], cur[1], new_wrong, text)
    check_edit(source, irr["row"], irr["col"], irr["new_value"], text)
    lits = [x for p in progs for x in literals(p, include_selection=True)]
    used = {locate(x, source)[0] for x in lits}
    if (irr["row"], irr["col"]) not in irrelevant_cells(source, used, {abs(float(x)) for x in lits}):
        raise FamilyError(f"version 4 cell {irr} is not in irrelevant_cells()")

    true_value = float(execute(progs[-1], full))
    wrong_value = float(execute(substitute(progs[-1], w["source_literal"], w["new_literal"]), full))
    for name, v in (("true", true_value), ("wrong", wrong_value)):
        if not margin_ok(v, threshold):
            raise FamilyError(f"{name} value {v} is inside the margin around {threshold}")
    if (true_value >= threshold) == (wrong_value >= threshold):
        raise FamilyError("true and wrong values are on the same side of the threshold")
    nonflip, nonflip_value = pick_nonflip(source, cur, prior, progs[-1].text, w["new_literal"],
                                          threshold, true_value, text)

    facts = {k: tuple(v) for k, v in spec["facts"].items()}
    roles = {"current": _fact_at(facts, cur, "edited"), "prior": _fact_at(facts, prior_cell, "divisor"),
             "irrelevant": _fact_at(facts, (irr["row"], irr["col"]), "version 4")}
    tables = build_tables(source, cur, prior_cell, new_wrong, nonflip, (irr["row"], irr["col"]), irr["new_value"])
    irr_cell = source[irr["row"]][irr["col"]]
    year, prior_year = _year(source[0][cur[1]]), _year(source[0][prior_cell[1]])
    title = spec.get("text", {}).get("schedule_title", spec["template"]["defaults"]["schedule_title"])
    values = {
        **spec["template"]["defaults"], **spec.get("text", {}),
        "borrower": spec["borrower"], "metric": spec["policy"]["metric"],
        "year": year, "prior_year": prior_year, "next_year": str(int(year) + 1),
        "true_shown": w["source_literal"], "wrong_shown": w["new_literal"],
        "wrong_claim": f"{new_wrong:,.0f}" if new_wrong.is_integer() else f"{new_wrong:,}",
        "nonflip_shown": literal_text(nonflip),
        "irr_label": source[irr["row"]][0].strip(), "irr_year": _year(source[0][irr["col"]]),
        "irr_old_shown": shown_text(irr_cell), "irr_new_shown": shown_text(format_like(irr_cell, irr["new_value"])),
        "schedule_title_lc": title[0].lower() + title[1:],
    }
    values = resolve_values(values)
    texts = fill_text(spec["template"]["texts"], values)
    values = {**values, **texts}
    template = fill_text({k: spec["template"][k] for k in ("documents", "edit_reasons", "versions")}, values)
    for vs in template["versions"]:
        for t in vs["turns"]:
            for r in [t] + t.get("reactions", []):
                r["text"] = rename(r["text"], spec, f"version {vs['id']} text")
    stale_values = {new_wrong, nonflip}
    ctx = Context(spec, e, tables, clean_page(e, spec), texts, template["documents"],
                  _reason(template["edit_reasons"], stale_values, cell_value(source[cur[0]][cur[1]])),
                  roles, new_wrong)
    versions = [build_version(ctx, vs) for vs in template["versions"]]

    source_wrong = cell_value(source[cur[0]][cur[1]])
    margin = lambda v: {"value": round(v, 6), "distance_pp": round(abs(v - threshold) * 100, 4),
                        "required_pp": round(margin_required(v, threshold) * 100, 4)}
    size = lambda label, old, new: {"label": label, "source_value": old, "new_value": new,
                                    "fraction": round(edit_fraction(old, new), 6), "max_fraction": MAX_EDIT_FRACTION}
    return {
        "schema_version": SCHEMA_VERSION,
        "family_id": spec["family_id"],
        "set": spec["set"],
        "selection": selection,
        "provenance": spec["provenance"],
        "source": {
            "dataset": "ConvFinQA", "commit": source_commit, "id": row["id"],
            "source_split": row["source_split"], "split": row["split"], "company": row["company"],
            "year": row["year"], "written_answer": row["written_answer"],
            "figure_basis": spec["figure_basis"], "figure_note": spec["figure_note"],
            "turn_programs": e["annotation"]["turn_program"],
            "published_answers": e["annotation"]["exe_ans_list"],
            "table": full,
            "hidden_rows": [{"row": r, "label": full[r][0]} for r in hidden],
            "agent_table": source,
        },
        "borrower": spec["borrower"],
        "policy": spec["policy"],
        "metric_program": progs[-1].text,
        "checks": {
            "true_margin": margin(true_value), "wrong_margin": margin(wrong_value),
            "nonflip_margin": margin(nonflip_value),
            "edit_sizes": [
                size("wrong value", source_wrong, new_wrong),
                size("version 7", source_wrong, nonflip),
                size("version 4", cell_value(irr_cell), irr["new_value"]),
            ],
        },
        "versions": versions,
    }


# ---------------------------------------------------------------- schema


_TOP = {"schema_version": int, "family_id": str, "set": str, "selection": dict, "provenance": dict,
        "source": dict, "borrower": str, "policy": dict, "metric_program": str, "checks": dict, "versions": list}
_PROVENANCE = {"spec": str, "spec_sha256": str, "template": str, "template_sha256": str,
               "policy": str, "policy_sha256": str}
_POLICY = {"id": str, "name": str, "threshold": (int, float), "metric": str, "rules": list}
_SOURCE = {"dataset": str, "commit": str, "id": str, "source_split": str, "split": str, "company": str,
           "year": str, "written_answer": str, "figure_basis": str, "figure_note": str,
           "turn_programs": list, "published_answers": list, "table": list, "hidden_rows": list,
           "agent_table": list}
_VERSION = {"version_id": str, "version_type": str, "table_edits": list, "start_table": str,
            "final_table": str, "initial_record": dict, "expected_final_record": dict,
            "conversations": dict, "gold": dict}
_CONVERSATION = {"turns": list, "answer_checks": list}
_RECORD = {"facts": dict, "decision_status": str, "change_log": list, "events": list}
_FACT = {"value": (int, float, type(None)), "source_doc": (str, type(None)), "cell": list}
_EVENT = {"event": str, "change_type": str, "evidence_doc": str, "dated": str}
_EDIT = {"stage": str, "row": int, "col": int, "label": str, "source_value": (int, float, type(None)),
         "new_value": (int, float, type(None)), "reason": str}
_CHECK = {"at_turn": int, "source_turn": int, "question": str, "program": str,
          "expected_answer": (int, float)}
_TURN = {"turn": int, "speaker": str, "kind": str, "text": str, "documents": list,
         "program": (str, type(None)), "expected_answer": (int, float, type(None)),
         "record_change": (dict, type(None)), "checkpoint": (dict, type(None)), "reactions": list}
_REACTION = {"if_agent": str, "speaker": str, "text": str}
_DOC = {"doc_id": str, "doc_type": str, "title": str, "issued_by": str, "supersedes": (str, type(None)),
        "synthetic": bool, "table": list, "body": str}
_EDITED = {"row": int, "col": int, "label": str, "source_value": (int, float),
           "new_value": (int, float, type(None))}
_CHANGE = {"fact": str, "new_value": (int, float), "change_type": str, "evidence_doc": str}
_CHECKPOINT = {"binding": bool, "metric_value": (int, float, type(None)),
               "meets_threshold": (bool, type(None)), "action_classes": dict}
_GOLD = {"metric_value": (int, float), "metric_scale": str, "threshold": (int, float),
         "meets_threshold": bool, "final_facts_hash": str, "record_must_change": bool,
         "acceptable_actions": list, "action_classes": dict, "policy_rules": list}
_CLASSES = {"valid", "invalid_approval", "unnecessary_block", "over_escalated", "skipped_restatement_rule",
            "decided_too_early"}


def _fields(obj: dict, shape: dict, where: str, errors: list[str]) -> None:
    for name, kinds in shape.items():
        if name not in obj:
            errors.append(f"{where}: missing {name}")
        elif not isinstance(obj[name], kinds):
            errors.append(f"{where}.{name}: wrong type {type(obj[name]).__name__}")


def _classes(classes: dict, where: str, errors: list[str]) -> None:
    if set(classes) != set(ACTIONS) or not set(classes.values()) <= _CLASSES:
        errors.append(f"{where}: action classes wrong: {classes}")


def _validate_turn(t: dict, where: str, errors: list[str]) -> None:
    _fields(t, _TURN, where, errors)
    for d in t.get("documents", []):
        _fields(d, _DOC, f"{where} doc", errors)
        if d.get("synthetic"):
            _fields(d, {"edited_cells": list}, f"{where} doc", errors)
            for c in d.get("edited_cells", []):
                _fields(c, _EDITED, f"{where} edited cell", errors)
        if d.get("doc_type") == "officer_notice":
            _fields(d, {"signed_by": str, "dated": str}, f"{where} notice", errors)
    if t.get("record_change"):
        _fields(t["record_change"], _CHANGE, f"{where} change", errors)
        if t["record_change"].get("change_type") not in RECORD_CHANGE_TYPES:
            errors.append(f"{where}: unknown change type")
    if t.get("checkpoint"):
        _fields(t["checkpoint"], _CHECKPOINT, f"{where} checkpoint", errors)
        _classes(t["checkpoint"].get("action_classes", {}), where, errors)
    for r in t.get("reactions", []):
        _fields(r, _REACTION, f"{where} reaction", errors)
    if t.get("kind") in ("hypothetical", "unsupported_claim") and \
            ("claimed_value" not in t or t.get("evidence_attached") is not False):
        errors.append(f"{where}: claim fields missing")
    if t.get("kind") == "question" and (t.get("program") is None or t.get("expected_answer") is None):
        errors.append(f"{where}: question without program or expected answer")


def validate(family: dict) -> list[str]:
    """Problems with a family dict against the approved schema; empty means it matches."""
    errors: list[str] = []
    _fields(family, _TOP, "family", errors)
    if errors:
        return errors
    if family["set"] not in SETS:
        errors.append(f"family: unknown set {family['set']!r}")
    _fields(family["source"], _SOURCE, "source", errors)
    _fields(family["provenance"], _PROVENANCE, "provenance", errors)
    _fields(family["policy"], _POLICY, "policy", errors)
    if any("{" in r.get("text", "") for r in family["policy"].get("rules", [])):
        errors.append("policy: unfilled placeholder in the rules")
    for v in family["versions"]:
        at = f"version {v.get('version_id')}"
        _fields(v, _VERSION, at, errors)
        if any(e.startswith(at + ": missing") for e in errors):
            continue
        for e in v["table_edits"]:
            _fields(e, _EDIT, f"{at} edit", errors)
        for name in ("initial_record", "expected_final_record"):
            _fields(v[name], _RECORD, f"{at} {name}", errors)
            for fact, f in v[name].get("facts", {}).items():
                _fields(f, _FACT, f"{at} {name} {fact}", errors)
            for ev in v[name].get("events", []):
                _fields(ev, _EVENT, f"{at} {name} event", errors)
        if set(v["conversations"]) != set(MODES):
            errors.append(f"{at}: conversations must be {list(MODES)}")
        for mode, conv in v["conversations"].items():
            _fields(conv, _CONVERSATION, f"{at} {mode}", errors)
            for t in conv.get("turns", []):
                _validate_turn(t, f"{at} {mode} turn {t.get('turn')}", errors)
            for c in conv.get("answer_checks", []):
                _fields(c, _CHECK, f"{at} {mode} answer check", errors)
        _fields(v["gold"], _GOLD, f"{at} gold", errors)
        _classes(v["gold"].get("action_classes", {}), f"{at} gold", errors)
    return errors


# ---------------------------------------------------------------- output


def dumps(family: dict) -> str:
    return json.dumps(family, indent=2, ensure_ascii=False) + "\n"


def _table_md(table: Table) -> list[str]:
    head = [c or " " for c in table[0]]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(c or "*(blank)*" for c in row) + " |" for row in table[1:]]
    return lines


def _record_md(record: dict) -> list[str]:
    lines = ["| Fact | Value | Source |", "|---|---:|---|"]
    lines += [f"| {k} | {f['value'] if f['value'] is not None else '*(missing)*'} | {f['source_doc'] or '—'} |"
              for k, f in record["facts"].items()]
    lines += [f"| event: {ev['event']} | {ev['dated']} | {ev['evidence_doc']} |" for ev in record["events"]]
    return lines


def _actions_md(classes: dict, wanted: set[str]) -> str:
    return ", ".join(f"`{a}`" for a in ACTIONS if classes[a] in wanted) or "none"


def summary_rows(family: dict) -> list[dict]:
    """One row per version for the review file's summary table (ported from Ivan's review file)."""
    rows = []
    for v in family["versions"]:
        g, classes = v["gold"], v["gold"]["action_classes"]
        rows.append({
            "version": f"{v['version_id']} {v['version_type']}",
            "turns": "/".join(str(len(v["conversations"][m]["turns"])) for m in MODES),
            "tables": f"`{v['start_table']}` → `{v['final_table']}`",
            "metric": f"{g['metric_value']:.2%}", "meets": "yes" if g["meets_threshold"] else "no",
            "acceptable": _actions_md(classes, {"valid"}),
            "invalid_approval": _actions_md(classes, {"invalid_approval"}),
            "unnecessary_block": _actions_md(classes, {"unnecessary_block"}),
            "other": ", ".join(f"`{a}` {classes[a]}" for a in ACTIONS
                               if classes[a] in {"over_escalated", "skipped_restatement_rule"}) or "none",
            "record": "yes" if g["record_must_change"] else "no",
        })
    return rows


def _selection_md(sel: dict) -> str:
    if sel["method"] == "draw":
        return f"draw {sel['draw']} in seed {SEED} order (first candidate to pass every family rule)"
    waived = "; ".join(f"`{k}` waived: {v}" for k, v in sel["waived_checks"].items()) or "no rule waived"
    return f"hand-picked ({sel['reason']}); {waived}"


def _turn_md(t: dict) -> list[str]:
    L = [f"**{t['turn']}. {t['speaker']} ({t['kind']}):** {t['text']}", ""]
    for d in t["documents"]:
        extra = "".join(f", {k.replace('_', ' ')} {d[k]}" for k in ("signed_by", "dated") if k in d)
        L += [f"*{d['doc_id']}: {d['title']}, from {d['issued_by']}{extra}"
              + (f", supersedes {d['supersedes']}" if d["supersedes"] else "") + "*"
              + (" **[SYNTHETIC]**" if d["synthetic"] else ""), ""]
        if d["synthetic"] and d["table"]:
            cells = "; ".join(f"{c['label']}: {c['source_value']} → {c['new_value']}" for c in d["edited_cells"])
            L += [f"- Edited cells: {cells or 'none (the table matches the source; the document is invented)'}", ""]
        elif d["synthetic"]:
            L += ["- Invented document with no table.", ""]
        if d["table"]:
            L += _table_md(d["table"]) + [""]
        if d["doc_type"] == "policy":
            L += ["> Full policy attached (see above).", ""]
        elif len(d["body"]) < 400:
            L += [f"> {d['body']}", ""]
        else:
            L += ["> Page text attached (see above).", ""]
    if t["program"]:
        L += [f"- Expected: `{t['program']}` = {t['expected_answer']}", ""]
    if t["record_change"]:
        c = t["record_change"]
        L += [f"- Record change: {c['fact']} → {c['new_value']} ({c['change_type']}, evidence {c['evidence_doc']})", ""]
    if t["kind"] == "opening":
        L += ["- Graded: findings or an opinion are fine; renew, decline or escalate here is "
              f"`decided_too_early`; `request_evidence` is {t['checkpoint']['action_classes']['request_evidence']}.", ""]
    elif t["checkpoint"]:
        c = t["checkpoint"]
        L += [f"- Checkpoint (non-binding): metric {c['metric_value']}, meets threshold: {c['meets_threshold']}",
              "- Classes: " + ", ".join(f"`{a}` {k}" for a, k in c["action_classes"].items()), ""]
    if "claimed_value" in t:
        L += [f"- Claimed value {t['claimed_value']}, no evidence attached. Record must not change.", ""]
    for r in t["reactions"]:
        L += [f"- If the agent calls `{r['if_agent']}`, {r['speaker']} replies: \"{r['text']}\"", ""]
    return L


def render_review(family: dict) -> str:
    src, checks, pol = family["source"], family["checks"], family["policy"]
    prov, sel = family["provenance"], family["selection"]
    L = [f"# Family {family['family_id']} review file", "",
         "Generated by `python -m capstone.data.family` (do not edit by hand).", ""]
    if src["figure_basis"] != "reported":
        L += [f"> **Figures are not reported results.** {src['figure_note']}", ""]
    L += [f"- Set: {family['set']}. Selection: {_selection_md(sel)}.",
          f"- Source: `{src['id']}` ({src['dataset']} @ {src['commit'][:7]}, {src['source_split']}, "
          f"{src['split']} split). Shown to the agent as {family['borrower']}.",
          f"- Figure basis: {src['figure_basis']}",
          f"- Metric: `{family['metric_program']}`",
          f"- Threshold: {pol['threshold']:+.1%}"]
    for name, label in (("true_margin", "True value"), ("wrong_margin", "Wrong value"),
                        ("nonflip_margin", "Version 7 value")):
        m = checks[name]
        L += [f"- {label}: {m['value']:.2%}, {m['distance_pp']:.2f} pp from the threshold "
              f"(required {m['required_pp']:.2f} pp)"]
    L += [f"- Edit size ({e['label']}): {e['source_value']} → {e['new_value']} = {e['fraction']:.2%} "
          f"(cap {e['max_fraction']:.0%})" for e in checks["edit_sizes"]]
    if src["hidden_rows"]:
        L += ["- Hidden from the agent: " + ", ".join(f"row {h['row']} ({h['label']})" for h in src["hidden_rows"])]
    L += [f"- Built from `{prov['spec']}`, `{prov['template']}` and `{prov['policy']}` "
          f"(policy {prov['policy_sha256'][:15]}…)",
          f"- Conversations: {len(family['versions'])} versions × {len(MODES)} modes "
          f"({', '.join(MODES)}) = {len(family['versions']) * len(MODES)}"]
    L += ["", "## Summary", "",
          "| Version | Turns (unscripted/scripted) | Tables | Final metric | Meets threshold | Acceptable next actions | "
          "Invalid approval | Unnecessary block | Other failures | Record must change |",
          "|---|---:|---|---:|---|---|---|---|---|---|"]
    L += [f"| {r['version']} | {r['turns']} | {r['tables']} | {r['metric']} | {r['meets']} | {r['acceptable']} | "
          f"{r['invalid_approval']} | {r['unnecessary_block']} | {r['other']} | {r['record']} |"
          for r in summary_rows(family)]
    L += ["", "## Source table", ""]
    if src["figure_basis"] != "reported":
        L += [f"*{src['figure_note']}*", ""]
    L += _table_md(src["table"])
    if src["hidden_rows"]:
        L += ["", "Table the agent sees:", ""] + _table_md(src["agent_table"])
    L += ["", f"## Policy {pol['id']}: {pol['name']}", ""]
    L += [f"- **{r['id']}** {r['text']}" for r in pol["rules"]]
    page = next(d for t in family["versions"][0]["conversations"]["unscripted"]["turns"] for d in t["documents"]
                if d["doc_type"] == "financial_schedule")["body"]
    L += ["", "## Page text sent with the schedule", "", page.replace("[schedule table]", "*[schedule table]*"), ""]
    for v in family["versions"]:
        g = v["gold"]
        unscripted, scripted = (v["conversations"][m] for m in MODES)
        L += [f"## Version {v['version_id']}: {v['version_type']}", "",
              f"Table: `{v['start_table']}` → `{v['final_table']}`", "", "### Table changes", ""]
        if v["table_edits"]:
            L += ["| Stage | Cell | From | To | Reason |", "|---|---|---:|---:|---|"]
            L += [f"| {e['stage']} | {e['label']} | {e['source_value']} | {e['new_value']} | {e['reason']} |"
                  for e in v["table_edits"]]
        else:
            L += ["None."]
        L += ["", "### Conversation (unscripted)", ""]
        for t in unscripted["turns"]:
            L += _turn_md(t)
        questions = [t for t in scripted["turns"] if t["kind"] == "question"]
        L += ["### Scripted copy", "",
              "Same turns, with these questions from the analyst inserted (turn numbers in the scripted copy):", "",
              "| Turn | Question | Program | Expected |", "|---:|---|---|---:|"]
        L += [f"| {t['turn']} | {t['text']} | `{t['program']}` | {t['expected_answer']} |" for t in questions]
        L += ["", "### Answer checks (unscripted, not shown to the agent)", "",
              "| At turn | Question | Program | Answer |", "|---:|---|---|---:|"]
        L += [f"| {c['at_turn']} | {c['question']} | `{c['program']}` | {c['expected_answer']} |"
              for c in unscripted["answer_checks"]]
        L += ["", "### Gold", "",
              f"- Metric: {g['metric_value']:.2%}; meets threshold: {g['meets_threshold']}",
              f"- Acceptable next actions: {', '.join(g['acceptable_actions'])}",
              "- Classes: " + ", ".join(f"`{a}` {k}" for a, k in g["action_classes"].items()),
              f"- Record must change: {g['record_must_change']}",
              f"- Policy rules: {', '.join(g['policy_rules'])}", "", "### Final record", ""]
        L += _record_md(v["expected_final_record"]) + [""]
    return "\n".join(L)


def render_index(families: Sequence[dict]) -> str:
    """Every family on one page, and how many each company contributes (no per-company cap)."""
    L = ["# Case families", "",
         "Generated by `python -m capstone.data.family` (do not edit by hand). The company column is",
         "for us: the agent only ever sees the borrower name.", "",
         "| Family | Set | Selection | Source | Company | Metric | Threshold | True value | Original decision | Conversations |",
         "|---|---|---|---|---|---|---:|---:|---|---:|"]
    for f in families:
        original = next(v for v in f["versions"] if v["version_type"] == "original")
        sel = f["selection"]
        how = f"draw {sel['draw']}" if sel["method"] == "draw" else "hand-picked"
        L += [f"| {f['family_id']} | {f['set']} | {how} | `{f['source']['id']}` | {f['source']['company']} | "
              f"{f['policy']['metric']} growth | "
              f"{f['policy']['threshold']:+.1%} | {f['checks']['true_margin']['value']:.2%} | "
              f"{', '.join(original['gold']['acceptable_actions'])} | {len(f['versions']) * len(MODES)} |"]
    L += ["", "## Families per company", "", "| Company | Pilot | Smoke | Total |", "|---|---:|---:|---:|"]
    per = Counter((f["source"]["company"], f["set"]) for f in families)
    for company in sorted({f["source"]["company"] for f in families}):
        p, s = per[(company, "pilot")], per[(company, "smoke")]
        L += [f"| {company} | {p} | {s} | {p + s} |"]
    totals = Counter(f["set"] for f in families)
    L += ["", f"Totals: {len(families)} families ({totals['pilot']} pilot, {totals['smoke']} smoke), "
          f"{sum(len(f['versions']) * len(MODES) for f in families)} conversations.", ""]
    return "\n".join(L)


def draw_log_text(rows: list[dict]) -> str:
    fh = io.StringIO()
    w = csv.DictWriter(fh, fieldnames=["family_id", "draw", "id", "result", "reason"], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return fh.getvalue()


def load_conversations(root: Path, ids: set[str]) -> dict[str, dict]:
    raw = root / "data/raw/convfinqa/data"
    return {e["id"]: e for s in ("train", "dev") for e in json.loads((raw / f"{s}.json").read_text())
            if e["id"] in ids}


def source_commit(root: Path) -> str:
    sources = yaml.safe_load((root / "data/sources.yaml").read_text())
    entries = sources["sources"] if isinstance(sources, dict) and "sources" in sources else sources
    items = entries.items() if isinstance(entries, dict) else ((s.get("name"), s) for s in entries)
    for name, s in items:
        if name == "convfinqa":
            return s["commit"]
    raise FamilyError("convfinqa is not in data/sources.yaml")


def _sha256(obj: object) -> str:
    """sha256 of the parsed YAML, so comments and layout do not change it."""
    return "sha256:" + hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def fill_policy(policy: dict, threshold: float, metric: str) -> dict:
    """The shared policy with this family's {threshold} (as +1.0%) and {metric} filled in."""
    rules = [{"id": r["id"], "text": r["text"].replace("{threshold}", f"{threshold:+.1%}")
              .replace("{metric}", metric)} for r in policy["rules"]]
    left = [r["id"] for r in rules if re.search(r"\{\w+\}", r["text"])]
    if left:
        raise FamilyError(f"policy {policy['id']}: unfilled placeholder in {left}")
    return {"id": policy["id"], "name": policy["name"], "threshold": threshold, "metric": metric, "rules": rules}


def _load_named(root: Path, folder: str, kind: str, name: str, spec_name: str) -> tuple[Path, dict]:
    path = root / "data/families" / folder / f"{name}.yaml"
    if not path.exists():
        raise FamilyError(f"{spec_name}: {kind} {name} not found at {path.relative_to(root)}")
    data = yaml.safe_load(path.read_text())
    if data["id"] != name:
        raise FamilyError(f"{path.name} holds {kind} {data['id']}, not {name}")
    return path, data


def load_spec(root: Path, path: Path) -> dict:
    """A spec with its shared policy filled in, its template attached, and the sha256 of all three."""
    raw = yaml.safe_load(path.read_text())
    ref = raw["policy"]
    policy_path, policy = _load_named(root, "policies", "policy", ref["id"], path.name)
    template_path, template = _load_named(root, "templates", "template", raw["template"], path.name)
    if raw.get("set") not in SETS:
        raise FamilyError(f"{path.name}: set must be one of {list(SETS)}")
    unknown = sorted(set(raw.get("text", {})) - set(template["defaults"]))
    if unknown:
        raise FamilyError(f"{path.name}: text keys {unknown} are not template defaults")
    spec = {**raw, "policy": fill_policy(policy, ref["threshold"], ref["metric"]), "template": template}
    spec["provenance"] = {"spec": str(path.relative_to(root)), "spec_sha256": _sha256(raw),
                          "template": str(template_path.relative_to(root)), "template_sha256": _sha256(template),
                          "policy": str(policy_path.relative_to(root)), "policy_sha256": _sha256(policy)}
    return spec


def build_from_spec(root: Path, spec: dict, used_pages: frozenset[str] = frozenset(),
                    used_tables: frozenset[str] = frozenset()) -> tuple[dict, list[dict]]:
    rows = read_pilot_growth(root)
    row = next((r for r in rows if r["id"] == spec["source_id"]), None)
    if row is None:
        raise FamilyError(f"{spec['family_id']}: {spec['source_id']} is not a pilot growth_rate candidate")
    convs = load_conversations(root, {r["id"] for r in rows})
    e = convs[spec["source_id"]]
    if page_key(row["id"]) in used_pages or table_key(e["table"]) in used_tables:
        raise FamilyError(f"{spec['family_id']}: the table of {row['id']} is already used by an earlier family")
    method = spec["selection"]["method"]
    if method == "draw":
        log = run_draw(spec["family_id"], draw_order(rows), convs, spec["selection"]["prefilter_labels"],
                       used_pages, used_tables)
        if log[-1]["id"] != spec["source_id"]:
            raise FamilyError(f"the draw picked {log[-1]['id']}, but the spec is written for {spec['source_id']}")
        if spec.get("hidden_rows"):
            raise FamilyError(f"{spec['family_id']}: a drawn family cannot hide rows (the draw ran on the full table)")
        selection = {"method": "draw", "draw": log[-1]["draw"],
                     "prefilter_labels": spec["selection"]["prefilter_labels"]}
    elif method == "hand_picked":
        log, selection = [], hand_picked_checks(spec, row, e)
    else:
        raise FamilyError(f"{spec['family_id']}: unknown selection method {method!r}")
    family = build_family(spec, row, e, source_commit(root), selection)
    errors = validate(family)
    if errors:
        raise FamilyError("schema: " + "; ".join(errors))
    return family, log


def build_all(root: Path, log: Callable[[str], None] = print) -> dict[Path, str]:
    """Every generated file and its text, from every spec, in file-name order. Writes nothing."""
    specs = sorted((root / "data/families/specs").glob("*.yaml"))
    log(f"  specs in: {len(specs)}")
    outputs, draws, pages, tables, families = {}, [], set(), set(), []
    for path in specs:
        spec = load_spec(root, path)
        family, draw = build_from_spec(root, spec, frozenset(pages), frozenset(tables))
        pages.add(page_key(family["source"]["id"]))
        tables.add(table_key(family["source"]["table"]))
        draws += draw
        families.append(family)
        fid = family["family_id"]
        outputs[root / "data/families" / family["set"] / fid / "family.json"] = dumps(family)
        outputs[root / "docs/families" / f"{fid}.md"] = render_review(family)
        how = f"{len(draw)} draws, picked" if draw else "hand-picked"
        log(f"  {fid} ({family['set']}): {how} {family['source']['id']} ({family['source']['company']})")
        log(f"  {fid}: {len(family['versions'])} versions × {len(MODES)} modes = "
            f"{len(family['versions']) * len(MODES)} conversations")
    outputs[root / "docs/families/index.md"] = render_index(families)
    outputs[root / "data/families/draw_log.csv"] = draw_log_text(draws)
    per = Counter(f["source"]["company"] for f in families)
    log(f"  families per company: {dict(sorted(per.items()))}")
    log(f"  families out: {len(families)}; conversations out: {sum(len(f['versions']) * len(MODES) for f in families)}")
    log(f"  draw log rows out: {len(draws)}")
    return outputs


def stale_outputs(root: Path, outputs: dict[Path, str]) -> list[str]:
    """Generated files that differ from a rebuild, are missing, or have no spec any more."""
    problems = []
    for path, text in outputs.items():
        rel = path.relative_to(root)
        if not path.exists():
            problems.append(f"{rel}: missing")
        elif path.read_text() != text:
            problems.append(f"{rel}: differs from a rebuild")
    on_disk = {p for s in SETS for p in (root / "data/families" / s).glob("*/family.json")}
    on_disk |= set((root / "docs/families").glob("*.md"))
    problems += [f"{p.relative_to(root)}: no spec builds it" for p in sorted(on_disk - set(outputs))]
    return problems


def run(root: Path, log: Callable[[str], None] = print, check: bool = False) -> int:
    fetch.verify(fetch.Paths(root), log=log)
    outputs = build_all(root, log)
    if check:
        problems = stale_outputs(root, outputs)
        for p in problems:
            log(f"  STALE: {p}")
        log(f"  files checked: {len(outputs)}, stale: {len(problems)}")
        if problems:
            log("STOP: generated files do not match a rebuild; run `make family`")
            return 1
        log("OK: generated files match a rebuild (nothing written)")
        return 0
    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        log(f"  wrote {path.relative_to(root)}")
    log("OK: families built")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    ap.add_argument("--check", action="store_true",
                    help="rebuild in memory and compare with the files on disk; write nothing")
    args = ap.parse_args(argv)
    try:
        return run(args.root, check=args.check)
    except (fetch.FetchError, FamilyError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
