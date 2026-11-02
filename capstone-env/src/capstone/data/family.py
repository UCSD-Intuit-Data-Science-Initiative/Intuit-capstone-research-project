"""Stage 4: draw a candidate and build one case family from an authored script.

Usage (inside Docker, from the repo root):
    python -m capstone.data.family               # builds every data/families/specs/*.yaml

Inputs:
    data/families/specs/<id>.yaml         authored script and policy (no gold)
    data/interim/stage3/candidates.csv    pilot rows only (held-out rows are skipped on read)
    data/raw/convfinqa/data/*.json        verified against data/checksums.sha256

Outputs (all generated, never edited by hand; no timestamps, so reruns are byte-identical):
    data/families/pilot/<id>/family.json  what the harness will read
    docs/families/<id>.md                 human review file, from the same dict
    data/families/draw_log.csv            draw, id, pass/fail, reason

Draw order: pilot growth_rate candidates sorted by id, then shuffled with select.SEED. The first
candidate passing every family check is the only usable pick, and must be the spec's source_id.
Stage 3 output is not changed by these checks.

Family rules (decided Nov 1; each returns a reason code or raises FamilyError):
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
  page_text_restates      the page must not state an input, its difference, or a new value.
  page_states_growth_rate the page must not state the growth rate: a percent figure equal to it
                          at the figure's own precision, in a sentence naming the metric.
  edit size               every changed value must be within MAX_EDIT_FRACTION of its source value.
  threshold margin        |value - threshold| >= max(0.5 pp, 10% of |value|, 10% of |threshold|);
                          true and wrong values must be on opposite sides.
  dashes                  cells reading exactly 2014 / 2013 / 2212 are corrupted dashes (U+2014,
                          U+2013, U+2212) and count as blanks here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import random
import re
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

import yaml

from capstone.data import fetch
from capstone.data.cells import cell_value, edit_cell, is_percent_cell, locate, trace
from capstone.data.program import (
    answers_match, execute, literals, parse, substitute,
)
from capstone.data.select import SEED, TOTAL_TOL_ABS, TOTAL_TOL_REL, Table, irrelevant_cells

SCHEMA_VERSION = 1
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
ACTIONS = ("renew", "decline", "escalate", "request_evidence")
RECORD_CHANGE_TYPES = ("restatement", "new_information", "new_event")
DECISION_TURNS = ("tentative_decision_request", "final_decision_request")


class FamilyError(ValueError):
    pass


# ---------------------------------------------------------------- reading cells


def value_of(cell: str) -> Optional[float]:
    """cell_value, with corrupted dashes read as blanks."""
    return None if cell.strip() in DASH_CELLS else cell_value(cell)


def literal_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(TOTAL_TOL_ABS, TOTAL_TOL_REL * abs(b))


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


def run_draw(family_id: str, order: Sequence[dict], convs: dict[str, dict],
             prefilter_labels: bool) -> list[dict]:
    """Log rows up to and including the first pass."""
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


def record_failures(version: dict, final_facts: dict[str, dict]) -> list[str]:
    """What is wrong with the agent's record, as named failures; empty means it is correct.

    record_not_updated      a fact that should have changed does not hold its new value
    record_wrongly_updated  a fact that should not have changed did (a claim taken as fact)
    missing_source          the value is right but is not tied to its document
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
    return sorted(failures)


def grade(version: dict, action: str, final_facts: dict[str, dict]) -> dict:
    """Grade a final decision and the final record separately."""
    failures = record_failures(version, final_facts)
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


# ---------------------------------------------------------------- build


def facts_hash(facts: dict[str, dict]) -> str:
    values = {k: facts[k]["value"] for k in sorted(facts)}
    return "sha256:" + hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def clean_page(e: dict, spec: dict) -> str:
    text = " ".join(e["pre_text"]) + "\n\n[schedule table]\n\n" + " ".join(e["post_text"])
    for old, new in spec["page_renames"]:
        text = re.sub(re.escape(old), new, text, flags=re.IGNORECASE)
    for term in spec["forbidden_terms"]:
        if re.search(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE):
            raise FamilyError(f"forbidden term {term!r} survives in the page text")
    return text


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


def build_tables(spec: dict, source: Table) -> dict[str, Table]:
    w, irr = spec["wrong_edit"], spec["irrelevant_edit"]
    stale = edit_cell(source, w["row"], w["col"], float(w["new_literal"]))
    restored = edit_cell(stale, w["row"], w["col"], float(w["source_literal"]))
    if restored != [list(r) for r in source]:
        raise FamilyError("undoing the stale edit did not give back the source table")
    partial = [list(r) for r in source]
    for held in spec["withheld_cells"]:
        partial[held["row"]][held["col"]] = ""
    partial_2014 = [list(r) for r in partial]
    partial_2014[w["row"]][w["col"]] = source[w["row"]][w["col"]]
    return {"source": [list(r) for r in source], "stale": stale, "partial": partial,
            "partial_2014": partial_2014,
            "amended_eps": edit_cell(source, irr["row"], irr["col"], irr["new_value"])}


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


def _record(spec: dict, table: Table, doc_id: str) -> dict[str, dict]:
    return {name: {"value": cell_value(table[r][c]), "source_doc": doc_id if table[r][c].strip() else None,
                   "cell": [r, c]} for name, (r, c) in spec["facts"].items()}


def _values(record: dict[str, dict]) -> dict[str, Optional[float]]:
    return {k: v["value"] for k, v in record.items()}


def _reason(spec: dict) -> Callable[[float, float], str]:
    why, wrong = spec["edit_reasons"], spec["wrong_edit"]
    def reason_of(old: Optional[float], new: Optional[float]) -> str:
        if new is None:
            return why["withheld"]
        if old is None:
            return why["provided"]
        if new == float(wrong["new_literal"]):
            return why["stale"]
        return why["restored"] if new == float(wrong["source_literal"]) else why["irrelevant"]
    return reason_of


def policy_document(spec: dict) -> dict:
    pol = spec["policy"]
    return {"doc_id": pol["id"], "doc_type": "policy", "title": pol["name"], "issued_by": "Credit policy",
            "supersedes": None, "synthetic": False, "table": [],
            "body": "\n".join(f"{r['id']}: {r['text']}" for r in pol["rules"])}


def answer_checks(turn: int, e: dict, table: Table, stale: bool, wrong: dict) -> list[dict]:
    """The source turns, as checks on the agent's reasoning at a decision turn (not shown to it).

    Each is recomputed on the table active at that turn; a turn whose input is not provided yet
    is left out."""
    ann, out = e["annotation"], []
    for k, (raw, question) in enumerate(zip(ann["turn_program"], ann["dialogue_break"])):
        prog = parse(raw)
        if stale:
            prog = substitute(prog, wrong["source_literal"], wrong["new_literal"])
        found = trace(literals(prog, include_selection=True), table)
        if not found.all_found:
            continue
        if not found.exactly_one:
            raise FamilyError(f"turn {turn}: source turn {k} does not trace to one cell")
        out.append({"at_turn": turn, "source_turn": k, "question": question[0].upper() + question[1:],
                    "program": prog.text, "expected_answer": round(float(execute(prog, table)), 6)})
    return out


def build_version(spec: dict, vs: dict, e: dict, tables: dict[str, Table], page: str) -> dict:
    threshold = spec["policy"]["threshold"]
    source = tables["source"]
    metric_program = e["annotation"]["turn_program"][-1]
    source_facts = _values(_record(spec, source, ""))
    wrong = spec["wrong_edit"]
    reason_of = _reason(spec)

    turns, checks, record, initial, edits, log = [], [], None, None, [], []
    table, doc_count, rule_tags, claim, flipped = None, 0, {"P2"}, False, False
    for n, ts in enumerate(vs["turns"], 1):
        documents, change, checkpoint, program, answer = [], None, None, None, None
        extra = {}
        if ts.get("attach_policy"):
            documents.append(policy_document(spec))
        if "doc" in ts:
            doc_count += 1
            doc_id, d = f"DOC-{doc_count:02d}", spec["documents"][ts["doc"]["key"]]
            new_table = tables[ts["doc"]["table"]]
            synthetic = ts["doc"]["table"] != "source" or d["body"] != "page"
            documents.append({"doc_id": doc_id, "doc_type": d["doc_type"], "title": d["title"],
                              "issued_by": d["issued_by"], "supersedes": d["supersedes"],
                              "synthetic": synthetic,
                              **({"edited_cells": edited_cells(source, new_table)} if synthetic else {}),
                              "table": new_table, "body": page if d["body"] == "page" else d["body"]})
            if table is None:
                record = _record(spec, new_table, doc_id)
                initial = json.loads(json.dumps({"facts": record, "decision_status": "pending",
                                                 "change_log": []}))
                edits += table_diff(source, new_table, "start", reason_of)
            else:
                edits += table_diff(table, new_table, f"turn_{n}", reason_of)
                ch = ts["record_change"]
                old_metric = metric_from(metric_program, source_facts, _values(record), source)
                old_value = record[ch["fact"]]["value"]
                incoming = _record(spec, new_table, doc_id)
                changed = {k for k in record if record[k]["value"] != incoming[k]["value"]}
                if changed != {ch["fact"]}:
                    raise FamilyError(f"version {vs['id']} turn {n}: {doc_id} changes {sorted(changed)}")
                record = {**record, ch["fact"]: incoming[ch["fact"]]}  # the rest keep their own source
                new_value = record[ch["fact"]]["value"]
                new_metric = metric_from(metric_program, source_facts, _values(record), source)
                change = {"fact": ch["fact"], "new_value": new_value,
                          "change_type": ch["change_type"], "evidence_doc": doc_id}
                log.append({"fact": ch["fact"], "old_value": old_value, "new_value": new_value,
                            "change_type": ch["change_type"], "evidence_doc": doc_id})
                rule_tags.add("P3")
                if ch["change_type"] == "restatement":
                    if (old_metric >= threshold) != (new_metric >= threshold):
                        flipped = True
                        rule_tags.add("P6")
                    elif old_metric == new_metric:
                        rule_tags.add("P8")
            table = new_table
        kind, text = ts["kind"], ts["text"]
        if kind in DECISION_TURNS:
            checks += answer_checks(n, e, table, table is tables["stale"], wrong)
        if kind in ("opening", "tentative_decision_request"):
            metric = metric_from(metric_program, source_facts, _values(record), source)
            rounded = None if metric is None else round(metric, 6)
            checkpoint = {"binding": False, "metric_value": rounded,
                          "meets_threshold": None if metric is None else metric >= threshold,
                          "action_classes": classes_opening(metric) if kind == "opening"
                          else classes_checkpoint(metric, threshold)}
            if metric is None:
                rule_tags.add("P7")
        if kind in ("hypothetical", "unsupported_claim"):
            extra = {"claimed_value": ts["claimed_value"], "evidence_attached": False}
            rule_tags.add("P5" if kind == "hypothetical" else "P4")
            if kind == "unsupported_claim":
                claim = True
                rule_tags.add("P3")
        if kind == "hypothetical":
            prog = substitute(metric_program, wrong["source_literal"], literal_text(ts["claimed_value"]))
            program, answer = prog.text, round(float(execute(prog)), 6)
        turns.append({"turn": n, "speaker": ts.get("speaker", "analyst"), "kind": kind, "text": text,
                      "documents": documents, "program": program, "expected_answer": answer,
                      "record_change": change, "checkpoint": checkpoint, **extra})

    metric = metric_from(metric_program, source_facts, _values(record), source)
    meets = metric >= threshold
    classes = classes_final(meets, flipped, claim)
    return {
        "version_id": vs["id"], "version_type": vs["type"], "table_edits": edits,
        "start_table": vs["start_table"], "final_table": vs["final_table"],
        "initial_record": initial,
        "expected_final_record": {"facts": record, "decision_status": "final_not_executed",
                                  "change_log": log},
        "turns": turns,
        "answer_checks": checks,
        "gold": {
            "metric_value": round(metric, 6), "metric_scale": "fraction", "threshold": threshold,
            "meets_threshold": meets, "final_facts_hash": facts_hash(record),
            "record_must_change": bool(log),
            "acceptable_actions": [a for a in ACTIONS if classes[a] == "valid"],
            "action_classes": classes,
            "policy_rules": sorted(rule_tags),
        },
    }


def build_family(spec: dict, row: dict, e: dict, source_commit: str) -> dict:
    """The whole family dict. Raises FamilyError if any rule fails."""
    source, text = e["table"], page_text(e)
    threshold = spec["policy"]["threshold"]
    w, irr = spec["wrong_edit"], spec["irrelevant_edit"]
    progs = [parse(p) for p in e["annotation"]["turn_program"]]
    for p, gold in zip(progs, e["annotation"]["exe_ans_list"]):
        if not answers_match(execute(p, source), gold):
            raise FamilyError(f"source turn {p.text} no longer re-executes to {gold}")

    new_wrong = float(w["new_literal"])
    check_edit(source, w["row"], w["col"], new_wrong, text)
    check_edit(source, irr["row"], irr["col"], irr["new_value"], text)
    lits = [x for p in progs for x in literals(p, include_selection=True)]
    used = {locate(x, source)[0] for x in lits}
    safe = irrelevant_cells(source, used, {abs(float(x)) for x in lits})
    if (irr["row"], irr["col"]) not in safe:
        raise FamilyError(f"version 4 cell {irr} is not in irrelevant_cells()")

    true_value = float(execute(progs[-1], source))
    wrong_value = float(execute(substitute(progs[-1], w["source_literal"], w["new_literal"]), source))
    for name, v in (("true", true_value), ("wrong", wrong_value)):
        if not margin_ok(v, threshold):
            raise FamilyError(f"{name} value {v} is inside the margin around {threshold}")
    if (true_value >= threshold) == (wrong_value >= threshold):
        raise FamilyError("true and wrong values are on the same side of the threshold")

    tables = build_tables(spec, source)
    page = clean_page(e, spec)
    versions = [build_version(spec, vs, e, tables, page) for vs in spec["versions"]]
    source_wrong = cell_value(source[w["row"]][w["col"]])
    margin = lambda v: {"value": round(v, 6), "distance_pp": round(abs(v - threshold) * 100, 4),
                        "required_pp": round(margin_required(v, threshold) * 100, 4)}
    return {
        "schema_version": SCHEMA_VERSION,
        "family_id": spec["family_id"],
        "source": {
            "dataset": "ConvFinQA", "commit": source_commit, "id": row["id"],
            "source_split": row["source_split"], "split": row["split"], "company": row["company"],
            "year": row["year"], "written_answer": row["written_answer"],
            "figure_basis": spec["figure_basis"], "figure_note": spec["figure_note"],
            "turn_programs": e["annotation"]["turn_program"],
            "published_answers": e["annotation"]["exe_ans_list"],
            "table": source,
        },
        "borrower": spec["borrower"],
        "policy": spec["policy"],
        "metric_program": progs[-1].text,
        "checks": {
            "true_margin": margin(true_value), "wrong_margin": margin(wrong_value),
            "edit_sizes": [
                {"label": "wrong value", "source_value": source_wrong, "new_value": new_wrong,
                 "fraction": round(edit_fraction(source_wrong, new_wrong), 6),
                 "max_fraction": MAX_EDIT_FRACTION},
                {"label": "version 4", "source_value": cell_value(source[irr["row"]][irr["col"]]),
                 "new_value": irr["new_value"],
                 "fraction": round(edit_fraction(cell_value(source[irr["row"]][irr["col"]]), irr["new_value"]), 6),
                 "max_fraction": MAX_EDIT_FRACTION},
            ],
        },
        "versions": versions,
    }


# ---------------------------------------------------------------- schema


_TOP = {"schema_version": int, "family_id": str, "source": dict, "borrower": str, "policy": dict,
        "metric_program": str, "checks": dict, "versions": list}
_SOURCE = {"dataset": str, "commit": str, "id": str, "source_split": str, "split": str, "company": str,
           "year": str, "written_answer": str, "figure_basis": str, "figure_note": str,
           "turn_programs": list, "published_answers": list, "table": list}
_VERSION = {"version_id": str, "version_type": str, "table_edits": list, "start_table": str,
            "final_table": str, "initial_record": dict, "expected_final_record": dict,
            "turns": list, "answer_checks": list, "gold": dict}
_RECORD = {"facts": dict, "decision_status": str, "change_log": list}
_FACT = {"value": (int, float, type(None)), "source_doc": (str, type(None)), "cell": list}
_EDIT = {"stage": str, "row": int, "col": int, "label": str, "source_value": (int, float, type(None)),
         "new_value": (int, float, type(None)), "reason": str}
_CHECK = {"at_turn": int, "source_turn": int, "question": str, "program": str,
          "expected_answer": (int, float)}
_TURN = {"turn": int, "speaker": str, "kind": str, "text": str, "documents": list,
         "program": (str, type(None)), "expected_answer": (int, float, type(None)),
         "record_change": (dict, type(None)), "checkpoint": (dict, type(None))}
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


def validate(family: dict) -> list[str]:
    """Problems with a family dict against the approved schema; empty means it matches."""
    errors: list[str] = []
    _fields(family, _TOP, "family", errors)
    if errors:
        return errors
    _fields(family["source"], _SOURCE, "source", errors)
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
        for t in v["turns"]:
            where = f"{at} turn {t.get('turn')}"
            _fields(t, _TURN, where, errors)
            for d in t.get("documents", []):
                _fields(d, _DOC, f"{where} doc", errors)
                if d.get("synthetic"):
                    _fields(d, {"edited_cells": list}, f"{where} doc", errors)
                    for c in d.get("edited_cells", []):
                        _fields(c, _EDITED, f"{where} edited cell", errors)
            if t.get("record_change"):
                _fields(t["record_change"], _CHANGE, f"{where} change", errors)
                if t["record_change"].get("change_type") not in RECORD_CHANGE_TYPES:
                    errors.append(f"{where}: unknown change type")
            if t.get("checkpoint"):
                _fields(t["checkpoint"], _CHECKPOINT, f"{where} checkpoint", errors)
                _classes(t["checkpoint"].get("action_classes", {}), where, errors)
            if t.get("kind") in ("hypothetical", "unsupported_claim") and \
                    ("claimed_value" not in t or t.get("evidence_attached") is not False):
                errors.append(f"{where}: claim fields missing")
        for c in v["answer_checks"]:
            _fields(c, _CHECK, f"{at} answer check", errors)
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
    return lines


def render_review(family: dict) -> str:
    src, checks, pol = family["source"], family["checks"], family["policy"]
    L = [f"# Family {family['family_id']} review file", "",
         "Generated by `python -m capstone.data.family` (do not edit by hand).", "",
         f"> **Figures are not reported results.** {src['figure_note']}", "",
         f"- Source: `{src['id']}` ({src['dataset']} @ {src['commit'][:7]}, {src['source_split']}, "
         f"{src['split']} split). Shown to the agent as {family['borrower']}.",
         f"- Figure basis: {src['figure_basis']}",
         f"- Metric: `{family['metric_program']}`",
         f"- Threshold: {pol['threshold']:.1%}",
         f"- True value: {checks['true_margin']['value']:.2%}, {checks['true_margin']['distance_pp']:.2f} pp "
         f"from the threshold (required {checks['true_margin']['required_pp']:.2f} pp)",
         f"- Wrong value: {checks['wrong_margin']['value']:.2%}, {checks['wrong_margin']['distance_pp']:.2f} pp "
         f"from the threshold (required {checks['wrong_margin']['required_pp']:.2f} pp)"]
    L += [f"- Edit size ({e['label']}): {e['source_value']} → {e['new_value']} = {e['fraction']:.2%} "
          f"(cap {e['max_fraction']:.0%})" for e in checks["edit_sizes"]]
    L += ["", "## Source table", "", f"*{src['figure_note']}*", ""] + _table_md(src["table"])
    L += ["", f"## Policy {pol['id']}: {pol['name']}", ""]
    L += [f"- **{r['id']}** {r['text']}" for r in pol["rules"]]
    page = next(d for t in family["versions"][0]["turns"] for d in t["documents"]
                if d["doc_type"] == "financial_schedule")["body"]
    L += ["", "## Page text sent with the schedule", "", page.replace("[schedule table]", "*[schedule table]*"), ""]
    for v in family["versions"]:
        g = v["gold"]
        L += [f"## Version {v['version_id']}: {v['version_type']}", "",
              f"Table: `{v['start_table']}` → `{v['final_table']}`", "", "### Table changes", ""]
        if v["table_edits"]:
            L += ["| Stage | Cell | From | To | Reason |", "|---|---|---:|---:|---|"]
            L += [f"| {e['stage']} | {e['label']} | {e['source_value']} | {e['new_value']} | {e['reason']} |"
                  for e in v["table_edits"]]
        else:
            L += ["None."]
        L += ["", "### Conversation", ""]
        for t in v["turns"]:
            L += [f"**{t['turn']}. {t['speaker']} ({t['kind']}):** {t['text']}", ""]
            for d in t["documents"]:
                L += [f"*{d['doc_id']}: {d['title']}, from {d['issued_by']}"
                      + (f", supersedes {d['supersedes']}" if d["supersedes"] else "") + "*"
                      + (" **[SYNTHETIC]**" if d["synthetic"] else ""), ""]
                if d["synthetic"]:
                    cells = "; ".join(f"{c['label']}: {c['source_value']} → {c['new_value']}"
                                      for c in d["edited_cells"])
                    L += [f"- Edited cells: {cells or 'none (the table matches the source; the document is invented)'}", ""]
                if d["table"]:
                    L += _table_md(d["table"]) + [""]
                if d["doc_type"] == "policy":
                    L += ["> Full policy attached (see above).", ""]
                elif len(d["body"]) < 300:
                    L += [f"> {d['body']}", ""]
                else:
                    L += ["> Page text attached (see above).", ""]
            if t["program"]:
                L += [f"- Expected: `{t['program']}` = {t['expected_answer']}", ""]
            if t["record_change"]:
                c = t["record_change"]
                L += [f"- Record change: {c['fact']} → {c['new_value']} ({c['change_type']}, "
                      f"evidence {c['evidence_doc']})", ""]
            if t["kind"] == "opening":
                L += ["- Graded: findings or an opinion are fine; renew, decline or escalate here is "
                      f"`decided_too_early`; `request_evidence` is {t['checkpoint']['action_classes']['request_evidence']}.", ""]
            elif t["checkpoint"]:
                c = t["checkpoint"]
                L += [f"- Checkpoint (non-binding): metric {c['metric_value']}, "
                      f"meets threshold: {c['meets_threshold']}",
                      "- Classes: " + ", ".join(f"`{a}` {k}" for a, k in c["action_classes"].items()), ""]
            if "claimed_value" in t:
                L += [f"- Claimed value {t['claimed_value']}, no evidence attached. Record must not change.", ""]
        L += ["### Answer checks (not shown to the agent)", "",
              "| At turn | Question | Program | Answer |", "|---:|---|---|---:|"]
        L += [f"| {c['at_turn']} | {c['question']} | `{c['program']}` | {c['expected_answer']} |"
              for c in v["answer_checks"]]
        L += ["", "### Gold", "",
              f"- Metric: {g['metric_value']:.2%}; meets threshold: {g['meets_threshold']}",
              f"- Acceptable next actions: {', '.join(g['acceptable_actions'])}",
              "- Classes: " + ", ".join(f"`{a}` {k}" for a, k in g["action_classes"].items()),
              f"- Record must change: {g['record_must_change']}",
              f"- Policy rules: {', '.join(g['policy_rules'])}", "", "### Final record", ""]
        L += _record_md(v["expected_final_record"]) + [""]
    return "\n".join(L)


def write_draw_log(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["family_id", "draw", "id", "result", "reason"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


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


def build_from_spec(root: Path, spec: dict) -> tuple[dict, list[dict]]:
    rows = read_pilot_growth(root)
    convs = load_conversations(root, {r["id"] for r in rows})
    log = run_draw(spec["family_id"], draw_order(rows), convs, spec["prefilter_labels"])
    picked = log[-1]["id"]
    if picked != spec["source_id"]:
        raise FamilyError(f"the draw picked {picked}, but the spec is written for {spec['source_id']}")
    row = next(r for r in rows if r["id"] == picked)
    family = build_family(spec, row, convs[picked], source_commit(root))
    errors = validate(family)
    if errors:
        raise FamilyError("schema: " + "; ".join(errors))
    return family, log


def run(root: Path, log: Callable[[str], None] = print) -> int:
    fetch.verify(fetch.Paths(root), log=log)
    specs = sorted((root / "data/families/specs").glob("*.yaml"))
    log(f"  specs in: {len(specs)}")
    draws = []
    for path in specs:
        spec = yaml.safe_load(path.read_text())
        family, draw = build_from_spec(root, spec)
        draws += draw
        fid = family["family_id"]
        out = root / "data/families/pilot" / fid
        out.mkdir(parents=True, exist_ok=True)
        (out / "family.json").write_text(dumps(family))
        review = root / "docs/families"
        review.mkdir(parents=True, exist_ok=True)
        (review / f"{fid}.md").write_text(render_review(family))
        turns = sum(len(v["turns"]) for v in family["versions"])
        log(f"  {fid}: {len(draw)} draws ({len(draw) - 1} failed, 1 passed), picked {family['source']['id']}")
        log(f"  {fid}: {len(family['versions'])} versions, {turns} turns out; "
            f"wrote {out.relative_to(root)}/family.json and docs/families/{fid}.md")
    write_draw_log(root / "data/families/draw_log.csv", draws)
    log(f"  draw log rows out: {len(draws)}")
    log("OK: families built")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    args = ap.parse_args(argv)
    try:
        return run(args.root)
    except (fetch.FetchError, FamilyError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
