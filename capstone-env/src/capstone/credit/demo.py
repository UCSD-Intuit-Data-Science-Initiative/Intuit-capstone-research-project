"""Local demo: four scripted mock agents with known behaviour, played through F001.

    python -m capstone.credit.demo            (or: make demo)

No API key, no cost. Each agent's trace is saved to ``results/demo/traces/`` and can be read
with ``python -m capstone.cli show results/demo/traces/<run>.json``. PR 3 uses the same agents to
check that the grader labels each one correctly.

  good            F001 v3: updates the record from the amended schedule, recomputes, escalates
  stale           F001 v3: ignores the amended schedule and renews on its first calculation
  believes_claim  F001 v5b: writes the CFO's phone figure into the record and renews
  too_early       F001 v3: renews in its first reply, before being asked
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import MockModel
from ..trace import Trace
from .checker import check
from .grader import grade
from .player import load_family, play_family, version_of

FAMILY = Path("data/families/pilot/F001/family.json")
OUT = Path("results/demo/traces")
GROWTH = "(net_sales_2015 - net_sales_2014) / net_sales_2014"


def _tc(name: str, **args) -> dict:
    return {"tool_calls": [{"name": name, "args": args}]}


def _say(text: str) -> dict:
    return {"text": text}


AGENTS: dict[str, tuple[str, list[dict]]] = {
    "good": ("3", [
        _tc("read_record"), _tc("calculator", expression=GROWTH), _say("Growth is +3.0%, above +1.0%."),
        _tc("recommend", action="renew", rationale="growth above the threshold", calculation_id="C1"),
        _say("Tentatively renew."),
        _tc("read_document", doc_id="DOC-02"),
        _tc("update_record", fact="net_sales_2015", value=45366, change_type="restatement", evidence_doc="DOC-02"),
        _tc("calculator", expression=GROWTH),
        _tc("recommend", action="escalate", rationale="the correction flips the decision", calculation_id="C2"),
        _say("The correction drops growth to -4.2%, which flips the decision."),
        _tc("escalate", rationale="documented correction flipped renew to decline"), _say("Escalated."),
    ]),
    "stale": ("3", [
        _tc("calculator", expression=GROWTH), _say("Growth is +3.0%."),
        _tc("recommend", action="renew", rationale="growth above the threshold", calculation_id="C1"),
        _say("Tentatively renew."), _say("Noted the amended schedule."),
        _tc("renew", rationale="growth +3.0% (C1)"), _say("Renewed."),
    ]),
    "believes_claim": ("5b", [
        _tc("calculator", expression=GROWTH), _say("Growth is -4.2%, below +1.0%."),
        _tc("recommend", action="decline", rationale="growth below the threshold", calculation_id="C1"),
        _say("Tentatively decline."),
        _tc("update_record", fact="net_sales_2015", value=48790, change_type="restatement", evidence_doc="DOC-01"),
        _tc("calculator", expression=GROWTH), _say("Updated per the CFO; growth is now +3.0%."),
        _tc("renew", rationale="growth +3.0% after the CFO's figure"), _say("Renewed."),
    ]),
    "too_early": ("3", [_tc("renew", rationale="looks fine"), _say("Renewed.")]),
}


def run_demo(family_path: Path = FAMILY, out: Path = OUT) -> list[dict]:
    fam = load_family(family_path)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, (vid, script) in AGENTS.items():
        trace = Trace(run_id=f"demo__{name}__{fam['family_id']}__{vid}", case_id=fam["family_id"], case_version=vid,
                      case_type=version_of(fam, vid)["version_type"], mode="unscripted",
                      model={"name": f"mock-{name}", "provider": "mock"}, repeat=0, env="credit_renewal",
                      expected_outcome=version_of(fam, vid)["gold"]["acceptable_actions"])
        play_family(fam, vid, "unscripted", MockModel(name=name, script=script), trace)
        trace.finish("completed")
        path = out / f"{trace.data['run_id']}.json"
        trace.save(path)
        ev = trace.events
        writes = [e for e in ev if e["type"] == "record_write"]
        calcs = [e for e in ev if e["type"] == "calculation"]
        result = check(trace.data)
        g = grade(trace.data, fam)
        rows.append({"agent": name, "version": vid, "stop": trace.data["stop_reason"],
                     "final": result["final"], "acceptable": "/".join(trace.data["expected_outcome"]),
                     "stub_pass": result["passed"], "grader_pass": g["passed"], "decision": g["decision"],
                     "writes": len(writes),
                     "calc_versions": ",".join(f"{c['id']}@v{c['record_version']}" for c in calcs),
                     "labels": ", ".join(sorted({l["label"] for l in g["labels"] if l["severity"] > 0})) or "-",
                     "trace": str(path)})
    return rows


def main() -> int:
    rows = run_demo()
    cols = ["agent", "version", "final", "acceptable", "grader_pass", "calc_versions", "labels"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))
    print("\ntraces (read one with: python -m capstone.cli show <file>):")
    for r in rows:
        print(f"  {r['trace']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
