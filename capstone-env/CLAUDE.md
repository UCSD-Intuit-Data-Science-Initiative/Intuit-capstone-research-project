# CLAUDE.md — D23 Capstone (UCSD DSC 180A × Intuit)

Read this first every session. Keep it current: when a decision is made, add it to the Decision log.
Claude Code maintains this file; teammates ask for changes instead of replacing it.

## Project in one paragraph
Research question (mentor Dhruvi Kothari, Oct 21 2026): **"Can a financial agent maintain valid decisions
when the facts, supporting evidence, or approvals change during a conversation?"**
Framing: pending financial decision → information changes → determine what remains valid → take the
appropriate next action. This is a formal project for Intuit: rigor, reproducibility, and verifiable checks
matter more than speed.

Full data plan (team doc): https://claude.ai/artifact/8FJ8v5tQYr4P5jbU9RM4AD

## Mentor requirements (do not drift from these)
- Matched versions of each scenario, same final authoritative facts where possible:
  1 upfront · 2 gradual disclosure · 3 correction after a tentative decision · 4 irrelevant change (decision stays)
  · 5 hypothetical / unsupported assertion (must NOT update recorded facts).
- "Someone asserts a change without the evidence required by the policy" = the biggest industry gotcha. Cover it.
- Distinguish a newly discovered past error from a genuinely new event (approval/audit rules may differ).
- Authored, scripted conversations with controlled updates (not an LLM user simulator) for the pilot.
- Success beyond one pass score: recognized the material change? updated conclusions? acceptable next action?
  Track invalid approvals/payments and unnecessary blocks SEPARATELY. Report latency, tokens, tool calls too.
- Acceptable outcomes come from explicit written policies; several may be acceptable; some need clarification.
- Verifiable checks: correct tool execution, correct state changes, accurate scoring, manual inspection.
  Test the evaluator on known valid/invalid actions. High performance is a valid result.
- Strong baseline: complete updated evidence at once, decide from scratch, equal access to tools/policies.
- Changes happen BEFORE execution only. Reserve unseen scenario structures for later (held-out).
- Mentor Victor Calderon: separate harness from model (plug-and-play, parallel); trace every step.

## Mentor feedback, Nov 4 2026 (relayed to the team; focus of the next discussion meeting)
Overall "looking decent"; clarity is needed on three things. The novelty is expected to come from how each
agent is defined (its job and boundaries) and how agents interact once they pass messages to each other.
1. Agent boundaries: a detailed diagram and explanation of when an agent stops, which tools it calls, and how
   it makes edits (record writes), designed so it still holds when we move to multi-agent work.
2. Policies: expand them to be thorough, with mathematical rules and procedures. Real lenders each have their
   own credit review policies and may not publish them, so the mentor questioned the choice of this setup (she
   did not know Victor recommended FinQA/ConvFinQA). Task: collect public policies (for "the big 4", meaning
   still to confirm: big 4 banks or Big 4 firms) and build a realistic policy from them. Clarify at the meeting
   that FinQA/ConvFinQA are the fact layer only and the policy is authored.
3. Diagnostic categories that cover the logs in detail: how we measure drift. Not yes/no checks; labels
   should be flexible enough to document everything (several per run, with where, how bad, and notes).
Consequence: pause building new pilot families until the policy is expanded (thresholds and metrics may
change). PR #3's format and tooling do not depend on the policy wording.
Status Nov 12: Sophia drafted RNW-1-v2 (branch `policy/rnw-1-v2`, 58 rules from OCC + big-bank 10-Ks).

## Architecture feedback, Nov 12 2026 (after the boundaries page; "great progress, good foundation")
1. The shared record shows errors, but not all work that can go stale: store calculations and tentative
   recommendations as objects tied to the facts (and record version) they used, enough to diagnose stale
   reasoning. Keep it simple for the pilot.
2. The open harness exposes mistakes (good for pilot/baselines). When validation/approval gates are added,
   measure how much protection they add, so a protected system does not look like a better-reasoning agent
   only because software blocked its mistakes.
3. Define when review is mandatory, what the reviewer can approve, and what happens after a return. A decision
   agent wrongly skipping review must be an explicitly measured failure.
4. Grader: use the event log to check intermediate states as well as the final state. Report attempted invalid
   changes, accepted invalid changes and final consequences separately.

## Mentor code-review feedback, acted on Nov 24 2026 (the review took place shortly before)
Their focus was mostly on observability and tracing the labels we track. Because a case can end up with
multiple labels, organize them so we do not conflate errors (e.g. an event wrongly logged in an earlier turn
leads to no decision later but should be measured as the former).
Priorities: 1. Give Dhruvi and Victor access to the telemetry. 2. A unique ID per run and per error.
3. More explainability (e.g. why the agent chose a tool). 4. Run on updated policies, generalizable and not
conflicting. 5. Narrow the labels, perhaps break them into smaller ones. 6. Show why and where a run fails,
especially for no decision, with less overlap with other labels such as event wrongly logged.
Dhruvi's advice: agents should pause when a correction is made instead of immediately logging an event. For a
ground truth, give initial info that leads to an expected result on the pin, then add a change the agent should
pause on and check against the policy, which moves the pin (the expected answer). Track whether the agent is in
the right decision state at each turn.

| Point | Status |
|---|---|
| 1 Access to telemetry | Open: Dhruvi and Victor still need an invite to the Langfuse project (a team action, not code) |
| 2 Unique IDs | Done: `run_uid` per run, `label_id` per label, in the grader and in Langfuse (trace tag and metadata; each `problem` score keyed by its `label_id`) |
| 3 Explainability | Decision: use the model's saved thinking (in every trace since #10, shown on each model call in Langfuse); no new field on the agent's tools, because that would change the agent's behaviour. Langfuse also shows it beside each tool call. To check in the first Claude run: Claude may return its thinking empty |
| 4 Updated policies | Done: RNW-4 (#10) |
| 5 Narrower labels | Done: a `detail` on four labels; further splits proposed, not built |
| 6 Why and where it fails, no overlap | Done: one primary problem per run (earliest problem), every label dated by turn, report tables that count each run once |
| Dhruvi: decision state at each turn | Done: `expected_state` on every turn of every family (schema 5) and per-turn grading (group `state`) |

Open items from this round are in docs/architecture_plan.md "Open questions".

## Stage 6: architecture (in progress) — read docs/architecture_plan.md first
Plan agreed Nov 12 (Ishaan): 7 PRs. Now: 1 harness core (Ivan's harness + optional Langfuse copy), 2 credit
environment + agent view + versioned record + family player, 3 grader v0 (labels from the whole trace, test
agents), 4 first real run (merged #9, Nov 19; the Claude run needs an API key). Later: 5 gates, 6 review +
multi-agent. PR 7 (policy swap) was replaced by Sophia's RNW-4 (#10, Nov 24). Rules: the harness never knows the policy (it reads family.json); the trace file is
the official record and Langfuse only a copy; calculations and recommendations are versioned objects; every
attempt is logged; open mode for the pilot (mechanics enforced, policy graded).
- [x] PR 1 harness core — merged as #5 (Nov 15): Ivan's `setup/script-player-agent-loop`
  modules ported onto main (his 21 tests pass), `observe.py` + `--langfuse` + `cli langfuse` (langfuse 5.2.0,
  off by default, keys `LANGFUSE_*` in .env), 396 tests.
- [x] PR 2 credit environment + player — merged as #6 (Nov 15): `src/capstone/credit/`
  (agent view with a no-leak test over all 72 conversations, versioned record with calculations and
  recommendations, 10 tools with mechanics-only checks, family player with the 7 stop reasons and the 5b
  reaction, stub checker, 4 scripted demo agents), `make run-families`, `make demo`; 442 tests.
- [x] PR 3 grader v0 — merged as #7 (Nov 15): `credit/grader.py`, labels in 5 groups with
  the codebook fields, attempted / accepted / final invalid changes counted apart, stale-reasoning labels from
  the versioned record; default checker of run-families; `cli grade`; 461 tests.
- [x] Pre-PR 4 fixes — branch `stage6/pre-pr4-fixes` (Nov 16; merged #8, Nov 19): the five issues from the PR 2/3 reviews
  (Nov 15): 5b `evidence_requested` ending; grader refuses a family file that changed since the run
  (`case_hash`, override `--allow-family-change`); `wrong_change_type_at_end` final failure;
  `stale_calculation_cited` by figures, not versions; schema-checked tool calls, tool bugs are ungraded
  errors. Oracle test over all 72 conversations. Details: docs/architecture_plan.md "Fixed before PR 4".
- [x] PR 4 first real run set-up — merged as #9 (Nov 19): claude = claude-opus-5-5 (effort high, no fallbacks),
  `make poc-estimate`, `make poc`, grader labels model_refusal / output_truncated. Proven with a local model
  (Qwen 3.5 9B via Ollama): 36/36 runs completed, no harness errors, 24 of 36 passed under RNW-1. That is a
  harness check, not a finding. The Claude run is not done: no ANTHROPIC_API_KEY yet.
- [x] RNW-4 policy swap — Sophia's PR #10, merged Nov 24 (replaces the old PR 7). All families on RNW-4
  (see the Nov 23 log entries). Langfuse scores: #11 was merged into the `policy/rnw-4` branch by mistake, not
  `main`; #12 put the same commit on `main` (merged Nov 24).
- [x] Mentor code-review round (Nov 24) — five changes plus docs, each on its own branch, reviewed, merged as
  PRs #13-#18 (Nov 24-25). On merged `main`: 1035 tests pass, `make family-check` clean.
  Details and rules: docs/architecture_plan.md "Mentor code-review round".
  - [x] `stage6/mechanics-wording`: system prompt says a final action is taken only by calling its tool; the
    `log_event` description says it never changes a figure (use `update_record`). Wording only.
  - [x] `stage6/expected-state`: schema 5, grader-only `expected_state` on every turn of every family.
  - [x] `stage6/grader-labels`: `run_uid`, `label_id`, one primary problem per run, `detail` on four labels.
  - [x] `stage6/turn-state`: per-turn decision state (`turn_states`, `state_summary`, group `state`).
  - [x] `observe/langfuse-ids`: Langfuse carries `run_uid`, `label_id`, the primary problem, the per-turn
    counts, and the model's saved thinking beside each tool call. Tried against the real project Nov 24.
  - [x] Merged #13-#18 (Nov 24-25). Next: the runs in "Next runs" of the plan.
- [x] Qwen RNW-4 run (Nov 26): 216 / 216 on llama.cpp with 4 slots in 6 h 48 min, 176 passed; branch
  `stage6/local-qwen-runs` (local Qwen set-up + Langfuse flush fix, on top of PR #19; its PR merges after #19).
  Next: the Claude runs.
- Naming: harness modes `scripted` / `baseline` (turns one at a time vs all facts at once) are not the
  families' `unscripted` / `scripted`; PR 2 replaces the harness case format with the families.

## Data (decided Nov 1 2026, pending Dhruvi sign-off on workflow)
Sources: FinQA (github.com/czyssrs/FinQA) and ConvFinQA (github.com/czyssrs/ConvFinQA), both MIT.
They are the FACT layer (real 10-K figures + executable programs). We author the DECISION layer
(pending action, explicit policy, scripted updates, gold per turn). Neither dataset is usable as-is.

Verified profile (Nov 1 2026; reproduced exactly by Stage 2 — `make profile`, docs/profile_report.md).
Exact definitions: docs/reference/profile_nov1.py (reference script; re-implemented with tests in src/capstone/data/).
- FinQA: 8,281 labelled (train 6,251 / dev 883 / test 1,147) + 919 private (no answers). 137 companies, 1999–2019.
  Programs re-execute to published answer: 8,249/8,281 (99.6%). Inputs trace to exactly one table cell: 4,477/8,006 (56%).
  All 32 non-matches are table ops: 30 rows of % cells (dataset reads "26.2%" as 0.262), 2 duplicate row names.
- ConvFinQA: 3,458 labelled conversations (train 3,037 / dev 421), 12,594 turns, + 434 private.
  Turn answers re-execute: 12,594/12,594 (100%). Type I 2,409 / type II 1,049. 3,704 turns (29%) have a
  multi-step program (contain '#n'); '#n' points inside the same turn's program, so this is only a proxy for
  "builds on earlier turns". 4,398 turns are bare number selections.
  1,304 conversations have ≥3 turns AND every input traces to exactly one table cell → our starting pool.
  Counting number-selection turns as inputs gives the same 1,754 / 1,304 (selected numbers are re-written
  into later op turns; only 3 conversations select a number no op turn reuses).
- Program DSL ops: add, subtract, multiply, divide, exp, greater, table_max/min/sum/average; `#n` = result of step n;
  `const_100`, `const_m1` (= -1); `%` literals are /100. Number-selection turns are a bare number (no op).
- Percent rule (Stage 3): ONE rule, the dataset's: a "26.2%" cell means 0.262, same as a "26.2%" literal
  (`cell_value`, `execute(percent_rule="dataset")`, the default). The Nov 1 rule ("26.2%" -> 26.2) is kept only
  for `make profile`. Under the dataset rule 8,279 / 8,281 FinQA programs re-execute (the 2 left: duplicate rows).
  Program literals copy percent cells as written (26.2), so tracing still compares as written; percent inputs
  are flagged and left out of the pilot.
- Written answer vs exe_ans: ConvFinQA's per-turn `answer_list` is NOT a written answer (calculated turns hold
  placeholders A0, #1); use `qa.answer`. Disagreement (sign flips, wrong values, unparseable) drops 821 / 8,281
  FinQA examples and 368 / 4,507 ConvFinQA qa blocks; formatting differences are kept (answers.py).
- Dataset limitation: type I ConvFinQA endings are dominated by growth rates (1,258 of 2,409 end in (a - b) / b,
  51 more in a / b - 1); only 23 end in a plain ratio and none in a plain difference.

Stage 3 selection (`make select`, docs/selection_report.md): 3,458 conversations -> 419 full candidates + 66
held-out cut (difference) candidates. Split by company, 30% held out (40 of 133), seed 1101:
- pilot: 260 candidates, 59 companies — growth_rate 243 (55 co.), ratio 17 (13 co.).
- held-out: 225 candidates, 25 companies — growth_rate 137 (25 co.), ratio 22 (7 co.), difference 66 (13 co.).
Gate (passed): ≥30 candidates per side; ≥10 companies per required metric (pilot growth_rate + ratio;
held-out growth_rate + difference; held-out ratios not gated).
- Known traps: label noise (FinQA train[0] asks "interest expense in 2009"; 3.8 is the CHANGE if LIBOR moves
  100 bp, and its written `answer` says 380 while `exe_ans` is 3.8); cells like "$ 6305", "14.3% ( 14.3 % )",
  "-23158 ( 23158 )"; units ("in millions") usually in page text; editing a cell that feeds a "total" row
  makes the table inconsistent.

Proposed workflow: annual credit-line renewal review (renew / decline / escalate / request evidence) with a
policy metric = the example's own program, so gold is recomputed by code after any edit.
Pilot: 2 smoke families → 10 reviewed families × 9 versions (1,2,3,4,5a hypothetical,5b unsupported claim,
6 original,7 correction that does not flip,8 new event) × 2 modes (unscripted, scripted) = 180 conversations
(Nov 3; was 6 versions = 60 cases).
Held-out: 10+ families, unseen companies + ≥1 unseen metric type, sealed until Q2. Split by company.

## Repo map
- `src/capstone/data/` data pipeline: `fetch.py` (Stage 1 fetch + checksums), `program.py` (DSL parser/executor,
  answer tolerance, `substitute`), `cells.py` (percent rule, input tracing, `edit_cell`), `profile.py` (Stage 2),
  `answers.py` (written answer vs exe_ans), `select.py` (Stage 3 filters, metric classes, cut versions, split, gate),
  `family.py` (Stage 4 draw or hand-pick, family rules, template fill, both modes, gold, grading, JSON + review
  + index output).
- `src/capstone/credit/` (Stage 6 PRs 2-3): `view.py` agent view + rendering, `record.py` versioned record,
  `env.py` credit tools (mechanics only), `player.py` family player + stop rules + run specs, `checker.py` stub,
  `grader.py` grader v0 (labels, `check`, `grade_experiment`),
  `demo.py` scripted mock agents (`make demo`).
- `src/capstone/` harness (Stage 6, from Ivan's branch): `models.py` (mock/Anthropic/OpenAI/OpenRouter adapters),
  `agent.py` (tool-call loop), `player.py` (case files, modes), `env.py` (environment interface + toy_invoice),
  `trace.py` (trace format), `runner.py` (repeats, pass^k, tables), `cli.py`
  (run/run-families/score/grade/show/chat/langfuse), `checker.py` (stub for the toy_invoice case only; credit
  runs are graded by `credit/grader.py`), `observe.py` (optional Langfuse copy). `configs/models.yaml` (model
  names → provider/id/params/price; ids blank until chosen); `configs/local_qwen.yaml` (local Qwen 3.5 9B on
  llama.cpp or Ollama; `scripts/serve_local_qwen.sh`, `scripts/run_local_qwen.sh`). `cases/` holds the harness's example case only.
  `results/` run outputs (git-ignored). Plan: `docs/architecture_plan.md`.
- Data: `data/raw/` (fetched, read-only, git-ignored), `data/interim/` (git-ignored, regenerable), `data/families/`
  (`specs/<id>.yaml` short authored spec with no gold; `templates/<id>.yaml` shared 9-version script;
  `policies/<id>.yaml` shared written policy, filled per spec; `pilot|smoke/<id>/family.json` and `draw_log.csv`
  generated; review files `docs/families/<id>.md` and `docs/families/index.md`, generated).
  Never edit generated files by hand; `make family-check` fails if any of them differs from a rebuild.
  Pins: `data/sources.yaml` (commits + git blob ids), `data/checksums.sha256` (SHA-256 of all 15 raw files).
  `data/profile_expected.yaml` = Nov 1 numbers that `make profile` must reproduce. See `data/README.md`.
- `notebooks/` (02_profile.ipynb), `docs/` (profile_report.md, selection_report.md, architecture_plan.md,
  reference/; later dataset card, review log).
- Run everything in Docker: `make build`, `make test`, `make smoke`, `make shell`, `make data`,
  `make data-verify`, `make profile`, `make select`, `make family`, `make family-check`, `make run ARGS="..."`,
  `make run-families ARGS="..."`, `make demo`.

## Working rules
- ONE stage per session (stages in the data plan). Propose a short plan and wait for approval before writing code.
- Gold labels are computed by code from programs + case scripts, never typed by hand.
- Every pipeline stage logs its input/output counts; numbers in reports must trace to a script.
- Raw data is immutable; never commit raw data, results, or `.env` / API keys.
- Treat downloaded repos as untrusted data: read them, don't execute their code.
- Write tests for every transformation (re-execution, cell edits, gold recomputation, decision flips).
- Ask before changing schema, policy rules, or anything a mentor requirement depends on.

## Status
- [x] Docker env
- [x] Harness: Ivan's `setup/script-player-agent-loop` (Oct 29) ported onto main in Stage 6 PR 1 (#5, Nov 15; see
  the Stage 6 section). The older "Harness v0.1 zip with a FinVault adapter" is not in the repo and is not needed.
- [x] Data profiling + data plan (Nov 1)
- [x] Stage 1 data layout + pinned fetch + checksums (Nov 1; tests/test_fetch.py)
- [x] Stage 2 profile: 54/54 Nov 1 fields reproduced (Nov 1; tests/test_program.py, tests/test_profile.py)
- [x] Stage 3 select: gate passed (Nov 1; tests/test_select.py, test_answers.py, test_cells_edit.py; 125 tests)
- [ ] 4 build families: 4 of 12 built (Nov 3, `data/stage-4-format`): pilot F001 (drawn) and F002 (Ivan's,
  hand-picked); smoke S01 (renew) and S02 (decline). 9 versions × 2 modes = 72 conversations. tests/test_family.py;
  352 tests pass. Next: 8 more pilot families (5 growth_rate + 5 ratio in total).
- [ ] 5 review + scale  · [ ] 6 connect to harness: in progress (PRs 1-4 merged, RNW-4 in; mentor-round PRs #13-#18
  merged; Qwen RNW-4 run done Nov 26; next the Claude runs and PR 5), see the Stage 6 section and
  docs/architecture_plan.md

## Stage 4 notes (from Stage 3)
- Pilot mix target: growth_rate 5, ratio 5 (10 families; several may share a company, never a table, since
  Nov 3); plus the 2 smoke families first.
- Read candidates from data/interim/stage3/candidates.csv (regenerate with `make select`; deterministic).
- Thresholds must use each candidate's `metric_scale`: fraction (0.0174 = 1.74%), percent (program ends x100),
  amount (difference, in `unit`).
- Threshold rule (decided Nov 1): the true metric must be at least max(0.5 percentage points, 10% relative)
  from the threshold. Every wrong value (stale, hypothetical, unsupported claim) must be on the OPPOSITE side
  of the threshold by the same margin. Thresholds are rounded to realistic values (and the margins re-checked
  after rounding).
- Hidden totals (decided Nov 1): before editing a cell, check numerically whether it takes part in any
  a ± b = c relation within its row or column (within the rounding tolerance). If it does, pick another cell
  or skip the family. (Stage 3 only caught rows/columns labelled "total".)
- Edit only the cells in `numerator_inputs` / `denominator_inputs` (difference: `inputs` roles minuend /
  subtrahend); version 4 edits one of the `n_irrelevant_cells`. Use `edit_cell` + `substitute`, then recompute.
- Difference families (held-out only) are built from the cut conversation: the first `truncated_at` turns.
- Held-out candidates feed only the sealed held-out set (Stage 5); do not use them to tune the pilot.
- Done in Stage 3 (from the Stage 2 notes): answer/exe_ans disagreements dropped; one percent rule with an
  edit-and-recompute test; table-op programs and duplicate row names dropped.

## Stage 4 rules for the next family (from F001-S02)
- Family rules are written in `family.py`'s docstring. Use `prefilter_labels: true` in new drawn specs: unlisted
  row labels are skipped before the draw and the order of the rest stays the same. `run_draw` skips the pages and
  tables used by earlier specs (file-name order), so a new family never changes an earlier draw.
- New specs use `template: growth_v1` and name a shared policy (`policy: {id, threshold, metric}`). The wording
  lives in the template; override it under `text:` only where it does not fit (e.g. `verb: was`, a claim pushing
  the other way in a renew family). Unknown `text` keys stop the build.
- The pilot still has no renew family: S01 (smoke) is the only one. At least one of the next pilot families
  should have a true value that passes the threshold.
- `hidden_rows` works for hand-picked families only; a drawn family cannot hide rows (the draw runs on the full
  table). Hiding margin rows in the draw itself would change F001's pick (draw 23, AON 2011, fails only
  ratio_row), so it needs a per-spec draw option first.
- Wrong-value direction is unchecked against the data plan: F001 and F002 put the wrong value above the true
  value, S01 and S02 below and above. The data plan link returned "artifact not found" on Nov 1 and Nov 2.
- The allowed-label list covers growth_rate only; ratio families need their own list and template.
- Usable pool (Nov 3 screen of Stage 3 with these rules, not committed): pilot growth_rate 7 candidates from 5
  companies pass every rule (6 of the 7 are pro forma pages); hiding margin rows would add 22 tables but
  only one new company (AON); held-out growth_rate 0. The held-out set needs a decision on the rules before it can be built.
- `family.json` `source` holds the real company; the harness must never show `source` to the agent.

## Stage 3 follow-up (not done; Stage 3 output unchanged)
The stricter checks live in family.py only. Run over Stage 3, they would flag these PILOT candidates (held-out
not counted). Screens, not reviewed by hand; sums can be coincidences (tolerance 1 unit).
- Sums up to 5 terms catching every metric input: growth_rate 126 / 243, ratio 9 / 17.
- Backwards calculation: 2 growth_rate, a minimum (47 could not be checked: no clear year pair).
- Page text restates an input or difference: growth_rate 46, ratio 1.
- Flagged by at least one: 149 of 260 (growth_rate 140, ratio 9), which could cut the ratio pool to 8.
- Corrupted dash cells in the table (not in that total; no metric input is a dash): growth_rate 24, ratio 2.

## Open decisions
- Dhruvi: credit review instead of invoice review?
- Restatement rule wording; acceptable actions for unsupported claims; real vs renamed companies.
  F001 uses provisional answers (policy RNW-1, renamed to Borrower A), approved by Jiya for this family only.
- Dhruvi: version 3 escalate only, or decline also valid? New-event rule (RNW-1 P11) and its evidence (a
  signed, dated officer notice)? Both are provisional.
- Team: evidence for a correction is any document naming the figure (RNW-1 P3), not audited filings only
  (Ivan's credit_renewal_v0); confirm. Turn on Ivan's stricter checks (commented out in family.py)? Sums up to
  5 terms (Jiya) or the Nov 1 a ± b = c rule (frees about 2 more pilot tables and 3 held-out)?
- Harness (Stage 6): limits (15 tool calls/turn, 60/run, 120 s/turn, 3 rejected calls in a row: proposed); which
  model and keys for the Claude run; multi-agent single writer vs proposals. Each run starts fresh (decided).
  Langfuse vs LangSmith is closed (Nov 24, see the log). Other open items from the Nov 24 round:
  docs/architecture_plan.md "Open questions" (public Langfuse sessions, stale scores, mentor access).

## Decision log
- 2026-10-21 Mentor narrowed RQ to decision validity under changing facts (see requirements above).
- 2026-11-01 FinVault grades a payout made on stale facts as valid → we use our own checker.
- 2026-11-01 Use FinQA/ConvFinQA as fact layer + authored decision layer (pending mentor sign-off on workflow).
- 2026-11-01 Stage 1: pinned FinQA @ 0f16e28 (2022-06-05) and ConvFinQA @ cf3eed2 (2022-11-09), both tip of main;
  fetch only data/LICENSE/README via raw.githubusercontent, verified by git blob id + SHA-256. Raw = 15 files,
  375,443,231 bytes (incl. unzipped ConvFinQA). `data/interim/` is git-ignored (regenerable).
  Docker Desktop ignores in-container chmod, so the Makefile makes raw files read-only from the host.
- 2026-11-01 "Reuse 3,704" relabelled "multi-step turn programs (contain #n)"; the old label was wrong.
- 2026-11-01 Stage 2 reproduced all 54 profile numbers; written answer and computed exe_ans can disagree,
  so we use exe_ans.
- 2026-11-01 Stage 3 decisions: (A) drop written-answer disagreements and unparseable answers, keep formatting
  differences, sign flips count as disagreements; store each metric's scale. (B) type I conversations only.
  (C) percent inputs left out of the pilot (flagged). (D) thresholds/edit values are Stage 4; degenerate metrics
  (zero/near-zero denominator, zero metric, absurd magnitude) dropped. (E) split by company, seed 1101.
- 2026-11-01 Metric classes: growth_rate = (a - b) / b or a / b - 1; ratio = last step divide over cells combined
  with add/subtract, no constants (composite ratios count); difference = a - b, held-out only, from conversations
  cut at their first plain subtract turn (turn 3+), with every filter re-run on the cut version. Pilot uses
  growth_rate + ratio. Gate: ≥30 candidates per side, ≥10 companies per required metric.
- 2026-11-01 Held-out share raised from 20% to 30% because the held-out difference gate (≥10 companies) failed
  at 20% under the strict unit rule. Decided before any model runs; gates unchanged.
- 2026-11-01 Stage 4 rules set: threshold margin max(0.5 pp, 10% relative), wrong values on the opposite side
  by the same margin, realistic rounded thresholds; numeric hidden-total check (a ± b = c in row/column)
  before any cell edit.
- 2026-11-01 Stage 4 F001 built from Single_LMT/2016/page_85.pdf-1 (net sales; unaudited pro forma figures, noted
  in the review file): draw 43 in seed 1101 order, first to pass every family rule (data/families/draw_log.csv).
  True value 45366 (-4.23%), wrong value 48790 (+3.00%), threshold +1.0%, true value = source value.
  Threshold moved from 0.0% because a sign check needs no calculation; margins 5.23 pp and 2.00 pp (0.50 required).
- 2026-11-01 F001 conversations: every version opens with the pending renewal, the policy and the action options;
  calculation questions are not scripted, so the agent must work out the metric. Source turns are kept per decision
  turn as `answer_checks`, not shown to the agent. Update turns do not say what changed; the documents still name
  the figure, as P3 requires. All versions but 2 share one skeleton: opening, non-binding tentative recommendation,
  the change (none in version 1), final decision. Any action in the opening reply is `decided_too_early`; findings
  or an opinion are fine.
- 2026-11-01 Policy RNW-1 (provisional, pending Dhruvi): below threshold -> decline. Escalate is correct only when a
  documented past correction flips the decision; otherwise `over_escalated`. At version 3's final decision only
  escalate is valid, and decline is `skipped_restatement_rule`. Invalid approvals, unnecessary blocks, over-escalation
  and skipped restatement rules are tracked separately; the record is graded apart from the decision.
- 2026-11-02 F001 documents carry a `synthetic` flag (true when any of it was invented). Only a changed fact moves
  to the new document; the rest keep their source. Record grading names failures: `record_not_updated`,
  `record_wrongly_updated`, `missing_source`. No per-turn `fail_if`: `action_classes` already covers decisions.
- 2026-11-02 RNW-1 gains P10: findings first, final actions (renew, decline, escalate) only when asked. Only final
  actions can be `decided_too_early`; `request_evidence` in an opening is valid when an input is missing (P7), otherwise
  an unnecessary block. Version 2 is gradual: both net sales blank, one document each, a tentative step after each
  (7 turns, the only version outside the shared skeleton). Synthetic documents list `edited_cells` against the source.
- 2026-11-01 Family rules added (family checks only): metric label on a written allowed list; signed sums up to 5
  terms; ratio-row check before every edit; year-pair and direction rules; page must not state an input, difference
  or the growth rate; corrupted dashes are blanks; every changed value within 10% of its source value (replaces the
  50% growth cap). Margin read strictly: max(0.5 pp, 10% of |value|, 10% of |threshold|).
- 2026-11-02 Merge on `sophia/stage-4`: Jiya's branch is the base (fast-forward). Ported from Ivan's branch: a shared
  policy file (RNW-1 moved unchanged to `data/families/policies/RNW-1.yaml`, `{threshold}` and `{metric}` filled per
  spec), the review summary table, `--check` (`make family-check`) and spec/policy sha256 in `family.json`
  (`provenance`; schema_version 2). `run_draw` now skips companies and source ids of earlier specs; F001's draw is
  unchanged (43 draws). Ivan's stricter checks (`line_too_long`, `derived_percent`, `round_threshold`) are ported
  but commented out, since turning them on can change F001's pick. Ivan's family gr01_lmt_2007_p55 and his policy
  credit_renewal_v0 are not merged (second LMT family; backlog not on the label list); both stay on ivan/stage-4.
- 2026-11-03 Team decisions after merging PR #2 (Ishaan, from the Nov 2-3 review of the ivan/jiya/sophia branches
  and the architecture doc), all built in the next PR on `data/stage-4-format`:
  9 versions per family (adds 6 original, 7 correction that does not flip, 8 new event); every version in two
  modes with the same gold, unscripted (agent works out the metric) and scripted (analyst asks the source
  questions before the tentative step), so the two can be compared; a scripted reaction in 5b only (if the agent
  asks for proof, the claimant says there is nothing in writing); several families may share a company, with no
  cap, but a table (page or identical table) is never used twice, and the build reports families per company;
  the allowed-label list is unchanged; Ivan's family is kept as hand-picked F002 with the label rule waived in
  its spec; the 2 smoke families are hand-picked AON brokerage (renew) and consulting (decline).
- 2026-11-03 Defaults A-F approved: A) version 1 upfront = stale schedule and its correction both in the opening,
  decided from scratch (nothing tentative came before, so it does not flip); version 6 original = the plain
  source. B) version 7's stale value is picked by code: a step of the gap toward the threshold (then away), same
  side as the true value, margin and edit checks as for the wrong value. C) new event = a dated notice signed by
  an officer; recorded figures stay, it is logged as new_event, the decision stands (RNW-1 P11, PROVISIONAL until
  Dhruvi). D) margin rows are hidden from the agent instead of rejecting the table (`hidden_rows`; the page text
  must not state a hidden value). E) version 3 stays escalate only until Dhruvi says otherwise. F) F001 is kept;
  RNW-1 P12 makes the pro forma schedule the basis of the review after an acquisition.
- 2026-11-04 Mentor feedback recorded (see "Mentor feedback, Nov 4"): agent-boundary diagram, thorough policies
  grounded in public sources, flexible diagnostic labels for drift. New pilot families paused until the policy
  is expanded. Agent-boundary diagram is the first of the three to prepare.
- 2026-11-12 Architecture feedback recorded (see "Architecture feedback, Nov 12"). Stage 6 plan agreed (Ishaan): 7
  PRs in docs/architecture_plan.md. Decisions: reuse Ivan's harness (`setup/script-player-agent-loop`, ported, not
  merged, since it predates Stages 1-4); tentative recommendations go through a `recommend` tool so they are
  stored objects; the calculator takes numbers or fact names and stores what it used; pilot in open mode, gates
  in PR 5; Langfuse as the optional viewer (trace files stay the official record; no OpenTelemetry layer of our
  own); the harness stays policy-agnostic so RNW-1-v2 is a spec change plus `make family`.
- 2026-11-12 PR 1: langfuse 5.2.0 added (requirements.txt `>=5.2,<6`; lock regenerated with uv keeping every
  existing pin, adds langfuse + otel exporter/sdk 1.45.1 + protobuf). Export is post-hoc from saved traces
  (`observe.py`): Langfuse times are export times, real timings stay in step metadata.
- 2026-11-13 Stage 6 PRs 3-4 built (Ishaan asked for both). Grader v0 is the default checker of run-families.
  First real run set up on claude-opus-5-5 with effort high and no server-side fallbacks (a fallback would
  answer with another model and blur which model was measured); not run, no API key on this machine yet.
- 2026-11-16 Pre-PR 4 fixes (Sophia, from the Nov 15 reviews of PRs 2-3), on `stage6/pre-pr4-fixes`:
  (A) Asking for proof means the `request_evidence` tool only; a plain-text request is left for human review
  (settles the open question). (B) `request_evidence` in reply to the final-decision request ends the run as
  `evidence_requested`, graded with the gold's class for it (valid in 5b per RNW-1 P4/P7, `unnecessary_block`
  elsewhere). (C) Pass rule changed: a correction left with the wrong change type (`new_information` vs
  `restatement`) is a final record failure, `wrong_change_type_at_end`, so the run fails (mentor requirement:
  tell a corrected past error from new information). (D) A run is graded only against the family file it was
  played with: a `case_hash` mismatch is refused unless `--allow-family-change`, which marks `family_changed`
  (no frozen copy of family files for now). Pass rates from before and after (C) are not comparable.
- 2026-11-23 RNW-4 logic fixes (Sophia). The corrected text is in docs/policy/RNW-4.md,
  with a change list F-1 to F-13; the per-case check of F001 is in docs/policy/RNW-4_clause_map_F001.md.
  Decisions: (F-1) keep Default A: documents attached to the opening message are processed before the F1(a)
  baseline, so v1 stays decline; (F-2) C2(2) treats a value as final and on the review's basis unless the
  document labels it otherwise; (F-3) the A2 rule "prior-year metric on the schedule > 0" is removed, so v2's
  blank opening stays. RNW-4 E5/E6 make `request_evidence` non-final, which reverses decision (B) of Nov 16 for 5b:
  apply this when F001 is rebuilt under RNW-4.
- 2026-11-23 All four families rebuilt under RNW-4 (branch `policy/rnw-4`, schema_version 4).
  - Policy: `data/families/policies/RNW-4.yaml` keeps the A2 parameters in the text. A generated case
    header (doc `CASE`) fills them in every opening.
  - Specs gain `unit`. Every document is dated (template defaults), and the build checks that a
    document is never dated before the one it supersedes.
  - Change type `new_information` is renamed `gap_filled` (record, tools, grader, tests).
  - Gold rules (family.py): request_evidence is never a valid final action (5b is now decline or
    renew only). F1(a) flips are measured from the record after the opening message. F1(b) makes
    escalate the only valid action; declining is the new class `skipped_escalation_rule`.
    `policy_rules` cite RNW-4 IDs.
  - Opening text no longer lists request_evidence as an option. The grader counts asking for proof
    after a hypothetical as allowed (E6).
  - New policy fix F-14: per-share line items keep their own unit.
  - RNW-1.yaml is kept, unused. Pass rates from before and after this change are not comparable.
- 2026-11-23 RNW-4 smoke run (harness check, not a finding): S01 + S02 × 9 versions × 2 modes on local
  Qwen 3.5 9B (Ollama, 4 parallel slots, `--seconds-per-turn 1800`). 36/36 completed, 22 passed. Failures:
  - 6 runs declined in the opening reply (decided_too_early), all in S02.
  - 4 runs logged a correction as an event instead of writing it to the record (missed_update).
  - 2 runs ended with no_decision: one only thought, one wrote "decline" without calling the tool.
  - S01 v3 unscripted renewed instead of escalating; S02 v1 unscripted over-escalated.
  Results and report.md are in `results/2026-11-23_1811_rnw4-smoke_S01-S02_qwen3.5-9b/`; traces are in
  Langfuse, tagged with the real company and filing. Results folders are named `<date>_<HHMM>_<description>`.
  The OpenAI adapter now raises on an empty stream, and tests can no longer reach Langfuse.
- 2026-11-24 RNW-4 merged (Sophia, #10): every family is on RNW-4, which replaces the plan's PR 7. #11 (Langfuse
  scores) went into `policy/rnw-4` by mistake; #12 put the same commit on `main` (merged).
- 2026-11-24 Mentor code-review feedback acted on (see "Mentor code-review feedback"). Five changes, each built
  by its own agent on its own branch and reviewed, open as PRs #13-#17 (docs: #18): mechanics wording; schema 5
  `expected_state` on every turn; grader `run_uid` / `label_id` / one primary problem / `detail`; grader per-turn
  decision state; Langfuse IDs, scores and thinking beside each tool call. Motive for the wording: in the Nov 19 Qwen run, 6 of 36 runs stated a decision in text without
  calling the tool and 9 logged a correction through `log_event`. Runs made before the wording used the old text.
  1032 tests pass with all of them together; `make family-check` clean. Rules: docs/architecture_plan.md
  "Mentor code-review round".
- 2026-11-24 Decisions (Ishaan): (1) Stay on Langfuse. LangSmith was considered (one dev project plus dataset and
  experiments); not adopted because the team has free Langfuse credits. Closes the open question. (2) Explainability
  comes from the model's saved thinking, not from a new field on the tools. (3) Per-turn expected state was built
  directly from the mentor feedback, without a separate design proposal. (4) Version 1 (upfront) stays decline
  (F-1, Nov 23); nothing in this round changes it.
- 2026-11-25 Mentor-round PRs #13-#18 merged. Before #16 merged, `not_recomputed` was fixed (Sophia): a
  calculation on the corrected figure counts whether it comes before or after the record write (regrade of
  the Nov 23 Qwen run: 5 fewer `not_recomputed` labels, pass/fail unchanged). #17 was checked on Nov 24 with
  one graded run sent to the real Langfuse project. 1035 tests pass on merged `main`.
- 2026-11-26 RNW-4 tool fixes F-15 to F-18 (Sophia; docs/policy/RNW-4.md): the policy asked for things the
  tools refuse. (F-15) G2: the D4 outcome and clause are stated in the reply before the call, not in the tool
  call (no new tool fields, per the Nov 24 decision). (F-16) G2 no longer says `update_record` can mark a fact
  withdrawn; C2A and Outcome 6 stay, and the tool gains a withdrawal when a family needs one (no pilot family
  has one). (F-17) E7: a document received after the final action is not logged. (F-18) Outcome 1 and G1
  match what the calculator and `read_record` do. Families rebuilt (policy text and hash only; gold
  unchanged). Runs made before this saw the older policy text.
- 2026-11-26 Local Qwen on llama.cpp (Sophia; docs/parallel_qwen.md). Ollama runs qwen3.5:9b one request at a
  time whatever OLLAMA_NUM_PARALLEL says, so the 216-run Qwen job ran on llama.cpp `llama-server -np 4` (same
  Q4_K_M weights, Ollama's sampling defaults): 6 h 48 min instead of about 15-20 h, 216 / 216 completed, no
  harness errors, 176 passed. Both backends live in `configs/local_qwen.yaml`, not `configs/models.yaml`
  (still no open-weight id chosen). Runs on a new backend are not comparable with the Nov 23 Ollama runs.
  The Langfuse export dropped steps of 69 runs (one flush at the end, a bounded queue): `export_runs` now
  flushes after each run; the 69 were re-sent and all 216 match their trace files. 1.6% of Qwen replies are
  empty (answer written inside its thinking), the same on Ollama: a model trait, left as it is.
- 2026-11-26 RNW-4 is frozen for Q1 (Sophia), with F-15 to F-18: no change to the agent-facing policy text
  until the Q1 report, so all Q1 runs share one text and can be pooled. Open items are provisional decisions
  awaiting Dhruvi (version 3 escalate only, new-event rule, evidence for a correction) and the unused
  withdrawal path (F-16), not logic errors. Candidate for Q2: clearer F1(a) wording; all 17 failed version 1
  runs on Nov 26 escalated under F1(a) without noting that opening-message corrections set the baseline.
