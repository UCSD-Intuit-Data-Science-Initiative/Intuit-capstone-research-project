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
Pilot: 2 smoke families → 10 reviewed families × 6 versions (1,2,3,4,5a hypothetical,5b unsupported claim) = 60 cases.
Held-out: 10+ families, unseen companies + ≥1 unseen metric type, sealed until Q2. Split by company.

## Repo map
- `src/capstone/data/` data pipeline: `fetch.py` (Stage 1 fetch + checksums), `program.py` (DSL parser/executor,
  answer tolerance, `substitute`), `cells.py` (percent rule, input tracing, `edit_cell`), `profile.py` (Stage 2),
  `answers.py` (written answer vs exe_ans), `select.py` (Stage 3 filters, metric classes, cut versions, split, gate),
  `family.py` (Stage 4 draw, family rules, build, gold, grading, JSON + review output).
- Harness v0.1 is NOT in this repo yet (see Status). `cases/` stays empty until it is merged.
  `results/` run outputs (git-ignored).
- Data: `data/raw/` (fetched, read-only, git-ignored), `data/interim/` (git-ignored, regenerable), `data/families/`
  (`specs/<id>.yaml` authored script with no gold; `pilot/<id>/family.json` and `draw_log.csv` generated;
  review file in `docs/families/<id>.md`, generated). Never edit generated files by hand.
  Pins: `data/sources.yaml` (commits + git blob ids), `data/checksums.sha256` (SHA-256 of all 15 raw files).
  `data/profile_expected.yaml` = Nov 1 numbers that `make profile` must reproduce. See `data/README.md`.
- `notebooks/` (02_profile.ipynb), `docs/` (profile_report.md, selection_report.md, reference/; later dataset
  card, review log).
- Run everything in Docker: `make build`, `make test`, `make smoke`, `make shell`, `make data`,
  `make data-verify`, `make profile`, `make select`, `make family`.

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
- [ ] Harness v0.1 (FinVault adapter, 24 tests) exists in a separate zip and is NOT merged into this repo yet;
  merge it as its own task before Stage 6.
- [x] Data profiling + data plan (Nov 1)
- [x] Stage 1 data layout + pinned fetch + checksums (Nov 1; tests/test_fetch.py)
- [x] Stage 2 profile: 54/54 Nov 1 fields reproduced (Nov 1; tests/test_program.py, tests/test_profile.py)
- [x] Stage 3 select: gate passed (Nov 1; tests/test_select.py, test_answers.py, test_cells_edit.py; 125 tests)
- [ ] 4 build families (1 of 12 built: F001 Lockheed, Nov 1; tests/test_family.py; 256 tests pass)
- [ ] 5 review + scale  · [ ] 6 connect to harness (report/record/calculator tools, record-state checks)

## Stage 4 notes (from Stage 3)
- Pilot mix target: growth_rate 5, ratio 5 (10 families, one per company); plus the 2 smoke families first.
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

## Stage 4 rules for the next family (from F001)
- Next family should have `renew` as its true answer: F001's true answer is decline, so `unnecessary_block` is
  only a needless hold there, and always-decline gets 5 of 6 final decisions.
- Family rules are written in `family.py`'s docstring. Use `prefilter_labels: true` in new specs: unlisted row
  labels are skipped before the draw and the order of the rest stays the same. Skip conversations already used
  and companies already used (one family per company); `run_draw` does not do that yet.
- The allowed-label list covers growth_rate only; ratio families need their own list. 68 of 243 pilot growth_rate
  candidates (20 companies) pass the label rule, before the other checks.
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
