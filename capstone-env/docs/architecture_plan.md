# Architecture plan (Stage 6): harness, grader, first runs

Status: agreed Nov 12 2026. Updated as each PR lands. **Start here when resuming this work.**
Design picture for the team: [Credit Agent Boundaries](https://claude.ai/artifact/A8t2d2ht79c3h12o4fvs8E)
(private; ask Ishaan for access).

## Goal

Run the credit-renewal families in `data/families/` through a real agent, record every step, and
grade the run with labels, not only pass/fail. First a single agent (setup A), then gates, then
several agents with review.

## Rules every PR follows

1. **The harness never knows the policy.** It reads the policy text, the record layout and the
   conversation from each family's `family.json`. A policy change (RNW-1 → RNW-1-v2) is a change
   to the specs plus `make family`, not to the harness.
2. **The trace file is the official record.** `results/<exp>/traces/<run_id>.json` is what the
   grader reads. Langfuse is an optional copy for browsing (`--langfuse`), off by default.
3. **Everything that can go stale is stored and versioned.** The record has a version number that
   goes up on every accepted write. Each calculation and each tentative recommendation is stored
   with the record version and the figures it used.
4. **Every attempt is logged**, accepted or not. The grader reports attempted invalid changes,
   accepted invalid changes and final consequences separately.
5. **Open mode for the pilot.** The harness enforces only mechanics (the fact exists, the cited
   document was given, the decision is still pending). Policy is judged by the grader, so mistakes
   stay visible. Blocking gates come later as a separate condition (PR 5).

## Feedback this plan answers

| Feedback | Where |
|---|---|
| Mentor, Nov 4: agent boundaries diagram (stops, tool calls, edits; ready for multi-agent) | Boundaries page; PR 2; PR 6 |
| Mentor, Nov 4: diagnostics, flexible labels not yes/no | PR 3 (codebook in the team doc) |
| Mentor, Nov 4: thorough policies from public sources | Sophia's `policy/rnw-1-v2` branch; PR 7 |
| Architecture, Nov 12: calculations and tentative recommendations as objects tied to inputs | Rule 3; PR 2 (record), PR 3 (stale-reasoning labels) |
| Architecture, Nov 12: measure how much validation/approval gates protect | PR 5 (open vs guarded on the same families) |
| Architecture, Nov 12: when review is mandatory, what the reviewer approves, what a return does; skipping review is a measured failure | PR 6 |
| Architecture, Nov 12: grade intermediate states from the event log; attempted vs accepted invalid changes vs final consequences | Rule 4; PR 3 |

## The PRs

| PR | Branch | Status | What |
|---|---|---|---|
| 1 Harness core | `stage6/harness-core` | merged (#5, Nov 15) | Ivan's harness ported onto `main` (models: mock, Anthropic, OpenAI, OpenRouter; agent loop; trace; runner; CLI; his 21 tests). Optional Langfuse copy (`observe.py`, `--langfuse`, `cli langfuse`). This plan and the Nov 4 / Nov 12 feedback in CLAUDE.md |
| 2 Credit environment + player | `stage6/credit-env` | merged (#6, Nov 15) | Agent view, versioned record, credit tools, family player, stop rules (details below) |
| 3 Grader v0 | `stage6/grader` | merged (#7, Nov 15) | Labels from the whole trace; known-behaviour test agents |
| 4 First real run | — | next | One real model, S01 + F001, 9 versions × 2 modes = 36 runs; results page. Needs an API key |
| 5 Gates | — | later | Guarded mode; open vs guarded on the same families |
| 6 Review + multiple agents | — | later | Preparer → decision → reviewer; review rules; `skipped_review` |
| 7 Policy swap | — | when RNW-1-v2 is final | Specs point to RNW-1-v2; document dates; rule ids; record fields |

PRs 1-4 are the proof of concept. After PR 3 a full run can be demonstrated with mock agents at no
API cost.

**Fixed before PR 4** (found in the PR 2/3 reviews, Nov 15; fixed on `stage6/pre-pr4-fixes`, Nov 16;
each would have changed real results):
- 5b: `request_evidence` in reply to the final-decision request now ends the run as `evidence_requested`,
  graded with the gold's class for it (valid in 5b, `unnecessary_block` elsewhere). Only the tool counts as
  asking for proof. Before: graded `no_decision` and `asked_proof_not_needed`.
- The grader checks the family file against the trace's `case_hash` and refuses a mismatch (`check_error`,
  `cli grade` SKIP); `--allow-family-change` grades against the current file and marks `family_changed`.
- A correction left with the wrong change type is a final failure, `wrong_change_type_at_end`; `recovered`
  needs value, source and type right.
- `stale_calculation_cited` compares the figures the cited calculation used with the record at the
  recommendation, not record versions (an event log no longer triggers it).
- Tool calls are checked against their schema (plain rejection reasons); an exception inside a tool is a
  harness bug: the run is an error (`error_kind: tool_bug`), not graded and not retried.
- Regression guard: `tests/test_grader.py` plays a gold-following agent through all 72 conversations and
  requires a pass with no problem labels.

## PR 2 design: credit environment and family player

Built in `src/capstone/credit/` (view, record, env, player, checker stub, demo); `make run-families`,
`make demo`; tests in `tests/test_credit.py`. Limits as proposed below (configurable in `Limits`).

**Agent view.** One function turns a family turn into exactly what the agent may see: speaker,
text, documents (id, type, title, issuer, supersedes, signed_by, dated, table, body). Never:
`checkpoint`, `expected_answer`, `program`, `record_change`, `synthetic`, `edited_cells`,
`claimed_value`, `answer_checks`, gold, `source` (real company), version id. A test checks that no
gold key or gold value reaches the agent.

**Record** (one per run, reset from the version's `initial_record`):
- `version`: integer, +1 on every accepted write.
- `facts`: value, source document, cell; open to extra fields (unit, period, how it entered) so
  RNW-1-v2's rule G1 does not force a rewrite.
- `change_log` (append-only), `events` (new events, P11).
- `calculations`: expression, the facts and values it used, result, record version.
- `recommendations`: tentative action, rationale, calculation ids, record version.
- `decision_status`: pending → final.

**Tools** (names match the boundaries page and RNW-1-v2):

| Tool | Harness enforces (mechanics only) |
|---|---|
| `read_document(doc_id)` | the document was given |
| `read_record()` | — |
| `update_record(fact, value, change_type, evidence_doc)` | fact exists; document given; decision pending |
| `log_event(event, evidence_doc)` | document given |
| `calculator(expression)` | parses; accepts numbers or fact names; stores a calculation |
| `recommend(action, rationale)` | action is renew/decline/escalate; stores a recommendation (not final) |
| `request_evidence(what)` | — ; in 5b it triggers the scripted reaction; in reply to the final-decision request it ends the run (`evidence_requested`) |
| `renew` / `decline` / `escalate(rationale)` | at most one; locks the record; ends the run |

Every call is first checked against its tool's JSON schema (required fields, types, enums, no extra
fields); a call that breaks it is rejected with a plain reason and logged as that tool's attempt. An
exception inside a tool is a harness bug (`ToolError`): the run ends as an error, ungraded, not retried.

**Player.** Plays one version in one mode (`unscripted` or `scripted`, the families' modes),
sending each turn's text and documents in order. Stops: normal end (one final action after the
final-decision request); evidence requested in reply to it (RNW-1 P4/P7); decided too early (final
action before it); no decision; limits (15 tool calls per turn, 60 per run, 120 s per turn, 3 rejected
calls in a row; proposed numbers). The 5b reaction fires when the agent calls `request_evidence` on the
claim turn.

**Trace events added:** `analyst` (message + documents), `record_write` (accepted or rejected,
version before/after), `calculation`, `recommendation`, `final_action`, `stop` (reason). Each
carries the record version at that moment.

Replaces the harness's own case format and its `scripted` / `baseline` modes for families (the
toy invoice case stays for the harness tests).

## PR 3 design: grader v0

Built in `src/capstone/credit/grader.py` (default checker of `run-families`; `cli grade <exp>` relabels saved
runs; `labels/<run>.json` + `labels.csv` per experiment); tests in `tests/test_grader.py` with scripted
agents of known behaviour and a gold-following agent over all 72 conversations. Runs cut short (too
early, limits, errors) are not graded on the final record. A run is graded only against the family file
it was played with (`case_hash`); see "Fixed before PR 4".

Reads the trace and `family.json` gold. Reuses `family.py` (`grade`, `record_failures`,
`classes_final`). Labels per run (codebook fields: label, turn, severity 0-4, recovered, evidence
event index, note):
- Decision: valid, invalid_approval, unnecessary_block, over_escalated, skipped_restatement_rule,
  decided_too_early, no_decision.
- Record: correct_update, missed_update, believed_claim, wrong_source, wrong_change_type,
  wrong_change_type_at_end, event_not_logged, event_wrongly_logged; each counted as attempted, accepted,
  still wrong at the end.
- Math / stale reasoning: a recommendation citing a calculation whose figures have changed since; a
  calculation using a figure not in the current record (stale or claimed figure); arithmetic error.
- Process: tool misuse, limit hit, repeated errors.
- Noticing and consistency stay for human review (later, possibly an AI judge checked by people).

Validation: scripted mock agents with known behaviour (good; keeps the stale figure; believes the
claim; decides too early; skips the record update) must get exactly the expected labels.

## PR 7 notes: switching to RNW-1-v2

From Sophia's draft (`policy/rnw-1-v2`, 58 rules): the decision logic matches today's gold. Needed:
gold rule tags move from P-ids to the new ids (E3, F1, O1-O5…); correction documents need a date
(C2 requires id, issuer and date); facts gain unit/period/how-entered (G1); "gap filled" naming vs
`new_information`. The harness needs no change if rules 1-3 above hold.

## Open questions

- Which model for PR 4, and who adds the keys (model + Langfuse) to `capstone-env/.env`.
- LangSmith instead of Langfuse? (Same switch; Langfuse chosen for open source / self-hosting.)
- What counts as asking for proof: the `request_evidence` tool only, or plain text too.
- Limits (15 / 60 / 120 s / 3) — confirm.
- Multi-agent: single record writer vs proposals; which setup after the pilot (B or C).

## Resuming

```bash
git fetch && git checkout main && git pull      # PRs 1-3 are merged
cd capstone-env && make build && make test      # 557 tests after the pre-PR 4 fixes (461 at the end of PR 3)
make demo                                        # 4 scripted mock agents through F001, no keys
make run-families ARGS="--model mock --versions 3 --out results/fam1"   # the real runner, mock model
```
Then read CLAUDE.md (Stage 6 section) and start the next PR in the table above.
