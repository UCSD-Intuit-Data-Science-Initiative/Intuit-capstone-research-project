"""Stage 6 PR 4: the first real run's model config, cost estimate, results page and real-model labels."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

from capstone import cli
from capstone.credit import grader
from capstone.credit.demo import AGENTS
from capstone.credit.player import MODES, find_families, load_family, plan_family_runs, play_family
from capstone.credit.report import estimate, render_report, run_tokens
from capstone.models import MockModel, load_model_configs, make_model
from capstone.trace import Trace

ROOT = Path(__file__).resolve().parents[1]
F001 = ROOT / "data/families/pilot/F001/family.json"
S01 = ROOT / "data/families/smoke/S01/family.json"
CONFIGS = load_model_configs(ROOT / "configs/models.yaml")


def test_the_claude_entry_is_ready_for_the_first_run():
    c = CONFIGS["claude"]
    assert c["provider"] == "anthropic" and c["model"] == "claude-opus-5-5"
    assert c["params"]["output_config"] == {"effort": "high"} and c["params"]["max_tokens"] == 16000
    assert c["price"] == {"input": 4.0, "output": 20.0}
    # parameters this model rejects are not sent
    assert not {"temperature", "top_p", "top_k", "thinking", "tool_choice"} & set(c["params"])


def test_the_claude_client_builds_offline_with_the_settings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    model = make_model("claude", CONFIGS)
    assert model.model == "claude-opus-5-5" and model.params["output_config"] == {"effort": "high"}


def test_estimate_covers_the_36_runs_and_brackets_the_cost():
    specs = plan_family_runs([S01, F001], None, MODES, ["claude"], 1)
    est = estimate(specs, CONFIGS)
    assert est["runs"] == 36 and est["priced"]
    assert 0 < est["low"]["cost_usd"] < est["high"]["cost_usd"]
    assert est["low"]["tokens_in"] < est["high"]["tokens_in"]
    mock = estimate(plan_family_runs([F001], ["3"], ["unscripted"], ["mock"], 1), CONFIGS)
    assert mock["low"]["cost_usd"] == 0 and mock["priced"]


def test_scripted_runs_cost_more_than_unscripted_ones():
    fam = load_family(F001)
    unscripted = run_tokens(fam, "3", "unscripted", 2, 400)
    scripted = run_tokens(fam, "3", "scripted", 2, 400)
    assert scripted[0] > unscripted[0] and scripted[1] > unscripted[1]


def test_estimate_on_the_cli_runs_nothing(tmp_path, capsys):
    out = tmp_path / "poc"
    assert cli.main(["run-families", "--families", str(S01), str(F001), "--model", "claude",
                     "--out", str(out), "--estimate"]) == 0
    printed = capsys.readouterr().out
    assert "= 36 runs" in printed and "estimate for 36 runs" in printed and "$" in printed
    assert not out.exists()


def _trace(vid: str, script: list) -> dict:
    fam = load_family(F001)
    t = Trace(run_id="r", case_version=vid, mode="unscripted", case_file=str(F001), model={"name": "mock"})
    play_family(fam, vid, "unscripted", MockModel(script=script), t)
    t.finish("completed")
    return t.data


def test_refusals_and_truncated_replies_are_labelled():
    data = _trace("3", AGENTS["good"][1])
    refused, cut = copy.deepcopy(data), copy.deepcopy(data)
    next(e for e in refused["events"] if e["type"] == "agent")["stop_reason"] = "refusal"
    next(e for e in cut["events"] if e["type"] == "agent")["stop_reason"] = "max_tokens"
    fam = load_family(F001)
    assert "model_refusal" in {l["label"] for l in grader.grade(refused, fam)["labels"]}
    assert "output_truncated" in {l["label"] for l in grader.grade(cut, fam)["labels"]}
    assert not {"model_refusal", "output_truncated"} & {l["label"] for l in grader.grade(data, fam)["labels"]}


def test_results_page_from_a_mock_experiment(tmp_path):
    out = tmp_path / "exp"
    assert cli.main(["run-families", "--families", str(F001), "--versions", "3", "6", "--model", "mock",
                     "--workers", "1", "--out", str(out)]) == 0
    page = render_report(out, "Mock check")
    for section in ("# Mock check", "## Pass rate by version", "## Decisions", "## Changes to the record",
                    "## Problem labels", "## Runs"):
        assert section in page
    assert "- Runs: 4 (4 completed, 0 errored)" in page and "| no_decision | 4 |" in page
    assert "| 3 | correction_after_tentative_decision | 0/1 | 0/1 |" in page
    written = tmp_path / "page.md"
    assert cli.main(["report", str(out), "--out", str(written)]) == 0 and written.read_text().startswith("# Results")


def test_make_targets_for_the_first_run_exist():
    text = (ROOT / "Makefile").read_text()
    assert "poc-estimate:" in text and "poc:" in text and "--model claude" in text
    assert "data/families/smoke/S01 data/families/pilot/F001" in text
    assert len(find_families([ROOT / "data/families/smoke/S01", ROOT / "data/families/pilot/F001"])) == 2


def test_seconds_per_turn_reaches_the_trace(tmp_path):
    out = tmp_path / "slow"
    assert cli.main(["run-families", "--families", str(F001), "--versions", "3", "--mode", "unscripted",
                     "--model", "mock", "--workers", "1", "--seconds-per-turn", "900", "--out", str(out)]) == 0
    trace = json.loads(next((out / "traces").rglob("*.json")).read_text())
    assert trace["settings"]["limits"]["seconds_per_turn"] == 900
    default = tmp_path / "default"
    assert cli.main(["run-families", "--families", str(F001), "--versions", "3", "--mode", "unscripted",
                     "--model", "mock", "--workers", "1", "--out", str(default)]) == 0
    assert json.loads(next((default / "traces").rglob("*.json")).read_text())["settings"]["limits"]["seconds_per_turn"] == 120


def test_progress_lines_show_start_time_and_time_left(tmp_path):
    from capstone.runner import Settings, _duration, run_experiment
    from capstone.credit.player import run_family_one
    assert (_duration(5), _duration(125), _duration(3725)) == ("0m05s", "2m05s", "1h02m")
    specs = plan_family_runs([F001], ["3", "6"], ["unscripted"], ["mock"], 1)
    log: list[str] = []
    run_experiment(specs, CONFIGS, tmp_path / "exp", Settings(workers=1), log=log.append, run_fn=run_family_one)
    assert sum(l.startswith("  started ") for l in log) == 2
    done = [l for l in log if l.startswith("[")]
    assert "left)" in done[0] and "elapsed" in done[1] and "left" not in done[1]
    trace = json.loads(next((tmp_path / "exp" / "traces").rglob("*.json")).read_text())
    assert "_wall_s" not in trace
