"""Does a dataset's human-written answer agree with its computed exe_ans?

Written answers are display strings ("1.7%", "$ 3.8 million", "-.62", "yes"); exe_ans is
the program's value. Categories (decided Nov 1, Stage 3 decision A):

  agree                   within the team tolerance, after reading "17.4%" as 0.174
  agree_display_rounding  within half a unit of the last digit shown ("1.7%" vs 0.01743)
  agree_percent_format    written with '%' but exe_ans is already in percent points
                          ("17.4%" vs 17.4), also allowing display rounding
  disagree                anything else, including any sign flip ("-50.1%" vs 0.50086)
  unparseable             no number (and not yes/no) in the written answer

The first three are formatting, so we keep them; the last two are dropped.
In ConvFinQA the written answer is qa.answer (qa_0 / qa_1 for type II). The per-turn
annotation.answer_list is NOT a written answer: calculated turns hold placeholders (A0, #1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from capstone.data.program import Value, answers_match

KEEP = frozenset({"agree", "agree_display_rounding", "agree_percent_format"})
WRITTEN_NUMBER = re.compile(r"-?(?:\d+\.?\d*|\.\d+)")


@dataclass(frozen=True)
class Written:
    kind: str  # "yesno" | "number" | "none"
    text: str
    number: float | None = None  # as written: "17.4%" -> 17.4
    is_percent: bool = False
    decimals: int = 0


def parse_written(text: str) -> Written:
    t = text.strip().lower()
    if t in ("yes", "no"):
        return Written("yesno", t)
    m = WRITTEN_NUMBER.search(t.replace(",", "").replace("$", ""))
    if not m:
        return Written("none", t)
    tok = m.group()
    return Written("number", t, float(tok), "%" in t, len(tok.split(".")[1]) if "." in tok else 0)


def _sign_flip(a: float, b: float) -> bool:
    return a * b < 0


def classify(written: str, gold: Value) -> str:
    w = parse_written(written)
    if w.kind == "none":
        return "unparseable"
    if w.kind == "yesno" or isinstance(gold, str):
        return "agree" if w.kind == "yesno" and w.text == str(gold).strip().lower() else "disagree"
    gold = float(gold)
    half_unit = 0.5 * 10 ** -w.decimals + 1e-12
    value = w.number / 100 if w.is_percent else w.number
    if not _sign_flip(value, gold):
        if answers_match(value, gold):
            return "agree"
        if abs(value - gold) <= half_unit * (0.01 if w.is_percent else 1):
            return "agree_display_rounding"
    if w.is_percent and not _sign_flip(w.number, gold):
        if answers_match(w.number, gold) or abs(w.number - gold) <= half_unit:
            return "agree_percent_format"
    return "disagree"
