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

Verified profile (Nov 1 2026, our own run — reproduce in stage 2, investigate any difference).
Exact definitions: docs/reference/profile_nov1.py (reference script; Stage 2 re-implements it with tests).
- FinQA: 8,281 labelled (train 6,251 / dev 883 / test 1,147) + 919 private (no answers). 137 companies, 1999–2019.
  Programs re-execute to published answer: 8,249/8,281 (99.6%). Inputs trace to exactly one table cell: 4,477/8,006 (56%).
- ConvFinQA: 3,458 labelled conversations (train 3,037 / dev 421), 12,594 turns, + 434 private.
  Turn answers re-execute: 12,594/12,594 (100%). Type I 2,409 / type II 1,049. 3,704 turns (29%) have a
  multi-step program (contain '#n'); '#n' points inside the same turn's program, so this is only a proxy for
  "builds on earlier turns". 4,398 turns are bare number selections.
  1,304 conversations have ≥3 turns AND every input traces to exactly one table cell → our starting pool.
- Program DSL ops: add, subtract, multiply, divide, exp, greater, table_max/min/sum/average; `#n` = result of step n;
  `const_100`, `const_m1` (= -1); `%` literals are /100. Number-selection turns are a bare number (no op).
- Known traps: label noise (FinQA train[0] asks "interest expense in 2009", answer 3.8 is the CHANGE);
  cells like "$ 6305", "14.3% ( 14.3 % )", "-23158 ( 23158 )"; units ("in millions") usually in page text;
  editing a cell that feeds a "total" row makes the table inconsistent.

Proposed workflow: annual credit-line renewal review (renew / decline / escalate / request evidence) with a
policy metric = the example's own program, so gold is recomputed by code after any edit.
Pilot: 2 smoke families → 10 reviewed families × 6 versions (1,2,3,4,5a hypothetical,5b unsupported claim) = 60 cases.
Held-out: 10+ families, unseen companies + ≥1 unseen metric type, sealed until Q2. Split by company.

## Repo map
- `src/capstone/data/` data pipeline: `fetch.py` (Stage 1 fetch + checksums).
- Harness v0.1 is NOT in this repo yet (see Status). `cases/` stays empty until it is merged.
  `results/` run outputs (git-ignored).
- Data: `data/raw/` (fetched, read-only, git-ignored), `data/interim/` (git-ignored, regenerable), `data/families/`.
  Pins: `data/sources.yaml` (commits + git blob ids), `data/checksums.sha256` (SHA-256 of all 15 raw files).
  See `data/README.md`.
- `notebooks/` (profiling), `docs/` (later dataset card, review log).
- Run everything in Docker: `make build`, `make test`, `make smoke`, `make shell`, `make data`,
  `make data-verify`.

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
- [ ] 2 profile  · [ ] 3 select  · [ ] 4 build families
- [ ] 5 review + scale  · [ ] 6 connect to harness (report/record/calculator tools, record-state checks)

## Open decisions
- Dhruvi: credit review instead of invoice review?
- Restatement rule wording; acceptable actions for unsupported claims; threshold margin; real vs renamed companies.

## Decision log
- 2026-10-21 Mentor narrowed RQ to decision validity under changing facts (see requirements above).
- 2026-11-01 FinVault grades a payout made on stale facts as valid → we use our own checker.
- 2026-11-01 Use FinQA/ConvFinQA as fact layer + authored decision layer (pending mentor sign-off on workflow).
- 2026-11-01 Stage 1: pinned FinQA @ 0f16e28 (2022-06-05) and ConvFinQA @ cf3eed2 (2022-11-09), both tip of main;
  fetch only data/LICENSE/README via raw.githubusercontent, verified by git blob id + SHA-256. Raw = 15 files,
  375,443,231 bytes (incl. unzipped ConvFinQA). `data/interim/` is git-ignored (regenerable).
  Docker Desktop ignores in-container chmod, so the Makefile makes raw files read-only from the host.
