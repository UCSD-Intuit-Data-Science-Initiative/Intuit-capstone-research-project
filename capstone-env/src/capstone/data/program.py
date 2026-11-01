"""Parse and execute FinQA / ConvFinQA answer programs.

Grammar (as the datasets use it):
    program   := selection | step (", " step)*
    selection := number literal, e.g. "6305" (ConvFinQA number-selection turn; no op)
    step      := op "(" arg ", " arg ")"
    arg       := number literal ("6305", "-1.5", "4.02%") | "const_<n>" | "const_m1" | "#<k>" | row name

"#k" is the result of step k of the same program. "%" literals are divided by 100.
Binary ops: add, subtract, multiply, divide, exp, greater ("yes"/"no").
Table ops take a row name: table_max, table_min, table_sum, table_average. They read
cells with the percent rule passed to execute(): "dataset" (default; "26.2%" -> 0.262,
matching '%' literals) or "nov1" (only to reproduce the Nov 1 profile; "26.2%" -> 26.2).

Gold labels in later stages are recomputed with execute(), so it fails loudly
(ProgramError) instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence, Union

from capstone.data.cells import PercentRule, cell_value

Value = Union[float, str]  # str only for greater(): "yes" / "no"

NUMBER_LITERAL = re.compile(r"-?[\d.]+%?")
STEP = re.compile(r"\s*([a-z_]+)\(([^()]*)\)\s*(?:,|$)")

BINARY: dict[str, Callable[[float, float], Value]] = {
    "add": lambda x, y: x + y,
    "subtract": lambda x, y: x - y,
    "multiply": lambda x, y: x * y,
    "divide": lambda x, y: x / y,
    "exp": lambda x, y: x ** y,
    "greater": lambda x, y: "yes" if x > y else "no",
}
TABLE: dict[str, Callable[[list[float]], float]] = {
    "table_max": max,
    "table_min": min,
    "table_sum": sum,
    "table_average": lambda v: sum(v) / len(v),
}


class ProgramError(ValueError):
    pass


@dataclass(frozen=True)
class Step:
    op: str
    args: tuple[str, ...]

    @property
    def is_table_op(self) -> bool:
        return self.op.startswith("table_")


@dataclass(frozen=True)
class Program:
    text: str
    steps: tuple[Step, ...] = ()
    selection: str | None = None  # set for a bare number-selection program

    @property
    def is_selection(self) -> bool:
        return self.selection is not None

    @property
    def is_multistep(self) -> bool:
        """Contains '#n', i.e. a later step uses an earlier step's result."""
        return any(a.startswith("#") for s in self.steps for a in s.args)


def is_number_literal(tok: str) -> bool:
    return NUMBER_LITERAL.fullmatch(tok) is not None


def parse(text: str) -> Program:
    s = text.strip()
    if "(" not in s:
        if not is_number_literal(s):
            raise ProgramError(f"not a program or number: {text!r}")
        return Program(text=text, selection=s)
    steps, pos = [], 0
    while pos < len(s):
        m = STEP.match(s, pos)
        if not m:
            raise ProgramError(f"cannot parse {text!r} at position {pos}")
        op, args = m.group(1), tuple(a.strip() for a in m.group(2).split(","))
        if op not in BINARY and op not in TABLE:
            raise ProgramError(f"unknown op {op!r} in {text!r}")
        if len(args) != 2:
            raise ProgramError(f"{op} needs 2 args, got {args} in {text!r}")
        steps.append(Step(op, args))
        pos = m.end()
    return Program(text=text, steps=tuple(steps))


def literal_value(tok: str) -> float:
    """Value of a number literal or constant: '4.02%' -> 0.0402, 'const_m1' -> -1."""
    t = tok.strip()
    if t.startswith("const_"):
        rest = t[len("const_"):]
        return -1.0 if rest == "m1" else float(rest)
    if not is_number_literal(t):
        raise ProgramError(f"not a number: {tok!r}")
    return float(t[:-1]) / 100 if t.endswith("%") else float(t)


def _table_row(table: Sequence[Sequence[str]], name: str, rule: PercentRule) -> list[float]:
    for row in table:
        if row and row[0].strip().lower() == name.lower():
            return [v for v in (cell_value(c, rule) for c in row[1:]) if v is not None]
    raise ProgramError(f"no table row named {name!r}")


def execute(program: Program | str, table: Sequence[Sequence[str]] = (),
            percent_rule: PercentRule = "dataset") -> Value:
    prog = parse(program) if isinstance(program, str) else program
    if prog.is_selection:
        return literal_value(prog.selection)
    return evaluate_steps(prog, table, percent_rule)[-1]


def evaluate_steps(program: Program | str, table: Sequence[Sequence[str]] = (),
                   percent_rule: PercentRule = "dataset") -> list[Value]:
    """The result of every step, in order (results[k] is what '#k' refers to)."""
    prog = parse(program) if isinstance(program, str) else program
    if prog.is_selection:
        return [literal_value(prog.selection)]
    results: list[Value] = []
    for step in prog.steps:
        if step.is_table_op:
            values = _table_row(table, step.args[0], percent_rule)
            if not values:
                raise ProgramError(f"row {step.args[0]!r} has no numbers")
            results.append(TABLE[step.op](values))
            continue
        x, y = (_resolve(a, results) for a in step.args)
        try:
            results.append(BINARY[step.op](x, y))
        except ZeroDivisionError as exc:
            raise ProgramError(f"division by zero in {prog.text!r}") from exc
    return results


def _resolve(arg: str, results: list[Value]) -> float:
    if arg.startswith("#"):
        k = int(arg[1:])
        if not 0 <= k < len(results):
            raise ProgramError(f"{arg} refers to a step that does not exist yet")
        v = results[k]
        if isinstance(v, str):
            raise ProgramError(f"{arg} is a yes/no result, not a number")
        return v
    return literal_value(arg)


def substitute(program: Program | str, old: str, new: str) -> Program:
    """Replace every number-literal arg equal to `old` with `new` (e.g. after editing that cell).

    Matching is by value as written ("6305" == "6305.0"); refs, constants and row names are untouched.
    """
    prog = parse(program) if isinstance(program, str) else program
    if not is_number_literal(old) or not is_number_literal(new):
        raise ProgramError(f"substitute needs number literals, got {old!r} -> {new!r}")
    same = lambda a: is_number_literal(a) and a.endswith("%") == old.endswith("%") \
        and float(a.rstrip("%")) == float(old.rstrip("%"))
    if prog.is_selection:
        return parse(new) if same(prog.selection) else prog
    steps = tuple(Step(s.op, tuple(new if (not s.is_table_op and same(a)) else a for a in s.args))
                  for s in prog.steps)
    text = ", ".join(f"{s.op}({', '.join(s.args)})" for s in steps)
    return Program(text=text, steps=steps)


def literals(program: Program, include_selection: bool = False) -> list[str]:
    """Number literals a program takes as inputs (what tracing looks for in the table).

    Counted: number-literal args of non-table steps. Not counted: '#n', 'const_*',
    table row names. A bare number-selection program contributes nothing unless
    include_selection=True (the Nov 1 definition leaves it out; see cells.py).
    """
    if program.is_selection:
        return [program.selection] if include_selection else []
    return [a for s in program.steps if not s.is_table_op for a in s.args if is_number_literal(a)]


def answers_match(ours: Value, gold: Value) -> bool:
    """Team tolerance: |ours - gold| <= 0.001 * max(1, |gold|); yes/no must match exactly."""
    if isinstance(ours, str) or isinstance(gold, str):
        return ours == gold
    return abs(ours - gold) <= 1e-3 * max(1.0, abs(gold))
