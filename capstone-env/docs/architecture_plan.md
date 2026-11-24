# Architecture plan (Stage 6): harness, grader, first runs

Status: agreed Nov 12 2026. Updated as each PR lands (last: Nov 24 2026, mentor code-review round). **Start here when resuming this work.**
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
| Mentor, Nov 4: thorough policies from public sources | Sophia's `policy/rnw-1-v2` branch; RNW-4 (#10, Nov 24; replaced PR 7) |
| Architecture, Nov 12: calculations and tentative recommendations as objects tied to inputs | Rule 3; PR 2 (record), PR 3 (stale-reasoning labels) |
| Architecture, Nov 12: measure how much validation/approval gates protect | PR 5 (open vs guarded on the same families) |
| Architecture, Nov 12: when review is mandatory, what the reviewer approves, what a return does; skipping review is a measured failure | PR 6 |
| Architecture, Nov 12: grade intermediate states from the event log; attempted vs accepted invalid changes vs final consequences | Rule 4; PR 3 |
| Code review, Nov 24 (acted on): 1 telemetry access for Dhruvi and Victor | Open: Langfuse invites (team action, not code) |
| Code review, Nov 24: 2 unique IDs per run and per error | `run_uid`, `label_id` (round below); not yet in Langfuse |
| Code review, Nov 24: 3 explainability | Decision: the model's saved thinking, no new tool field; not yet beside each tool call in Langfuse |
| Code review, Nov 24: 4 updated policies | RNW-4 (#10) |
| Code review, Nov 24: 5 narrower labels | `detail` on four labels; more splits proposed, not built |
| Code review, Nov 24: 6 why and where it fails, no overlap | One primary problem per run, labels dated by turn, report tables that count each run once |
| Code review, Nov 24: Dhruvi, decision state at each turn | `expected_state` on every turn (schema 5); per-turn grading (group `state`) |

## The PRs

| PR | Branch | Status | What |
|---|---|---|---|
| 1 Harness core | `stage6/harness-core` | merged (#5, Nov 15) | Ivan's harness ported onto `main` (models: mock, Anthropic, OpenAI, OpenRouter; agent loop; trace; runner; CLI; his 21 tests). Optional Langfuse copy (`observe.py`, `--langfuse`, `cli langfuse`). This plan and the Nov 4 / Nov 12 feedback in CLAUDE.md |
| 2 Credit environment + player | `stage6/credit-env` | merged (#6, Nov 15) | Agent view, versioned record, credit tools, family player, stop rules (details below) |
| 3 Grader v0 | `stage6/grader` | merged (#7, Nov 15) | Labels from the whole trace; known-behaviour test agents |
| 4 First real run | `stage6/first-run` | merged (#9, Nov 19); set-up proven with a local model; the Claude run is not done (needs `ANTHROPIC_API_KEY`) | One real model, S01 + F001, 9 versions × 2 modes = 36 runs; results page |
| 5 Gates | — | later | Guarded mode; open vs guarded on the same families |
| 6 Review + multiple agents | — | later | Preparer → decision → reviewer; review rules; `skipped_review` |
| 7 Policy swap | `policy/rnw-4` | replaced by RNW-4: merged (#10, Nov 24) | Every family on RNW-4 (see "PR 7 notes") |
| Langfuse scores | `policy/rnw-4`, then `main` | #11 went into `policy/rnw-4` by mistake; #12 put the same commit on `main` (merged Nov 24) | Scores on Langfuse traces |
| Mentor round: mechanics wording | `stage6/mechanics-wording` | built and reviewed; on `integration/mentor-feedback`, not pushed | Prompt and `log_event` wording |
| Mentor round: expected state | `stage6/expected-state` | same | Schema 5, `expected_state` on every turn |
| Mentor round: grader labels | `stage6/grader-labels` | same | `run_uid`, `label_id`, primary problem, `detail` |
| Mentor round: turn state | `stage6/turn-state` | same | Per-turn decision state |

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

Since Nov 24 the grader also has IDs, one primary problem per run, `detail`, and per-turn state labels
(group `state`). The module docstring of `grader.py` is the source of truth for all of them; the rules
are summarised in "Mentor code-review round" below.

Validation: scripted mock agents with known behaviour (good; keeps the stale figure; believes the
claim; decides too early; skips the record update) must get exactly the expected labels.

## PR 4: the first real run

Model: `claude` in `configs/models.yaml` = `claude-opus-5-5`, effort `high` (its default is medium),
thinking adaptive (always on for this model), max_tokens 16000, no server-side fallbacks (every answer
must come from the named model; refusals are labelled `model_refusal`). Scope: S01 + F001, 9 versions
× 2 modes, k = 1: 36 runs. Estimate (`make poc-estimate`): about $12-$77, deliberately high. Run with
`make poc` (needs `ANTHROPIC_API_KEY` in `capstone-env/.env`; add `ARGS="--langfuse"` to copy runs to
Langfuse); it writes `results/poc1/` and the page `docs/results/poc_run_1.md` (`cli report`).
Cost lever not used yet: prompt caching (the system prompt, tools and early turns repeat every call)
would cut input cost a lot, but the adapter's cost accounting would need cache prices first.

**Local model check (Nov 19).** Before paying for the Claude run, the same 36 runs were played with a free
local model: Qwen 3.5 9B served by Ollama with a 16k context (Mistral 7B could not call tools). All 36
completed with no harness errors; the record, the versioned calculations and the stop rules behaved as
designed. Fixes it led to: an assistant message with only tool calls is sent with empty content, not null
(Ollama rejects null); each run logs a start line and its duration, elapsed time and time left;
`--seconds-per-turn` raises the 120 s turn limit for slow local models. To repeat it, put a model entry with
`provider: openai`, `base_url: http://host.docker.internal:11434/v1` and zero prices in a git-ignored
models file and pass `--models-config <file> --seconds-per-turn 900`. Its results are a harness check, not
a finding about Claude.

## PR 7 notes: switching to RNW-1-v2

From Sophia's draft (`policy/rnw-1-v2`, 58 rules): the decision logic matches today's gold. Needed:
gold rule tags move from P-ids to the new ids (E3, F1, O1-O5…); correction documents need a date
(C2 requires id, issuer and date); facts gain unit/period/how-entered (G1); "gap filled" naming vs
`new_information`. The harness needs no change if rules 1-3 above hold.

**Done for RNW-4 instead (2026-11-23, branch `policy/rnw-4`).** The specs now use RNW-4
(`data/families/policies/RNW-4.yaml`, text in `docs/policy/RNW-4.md`). Gold tags cite RNW-4 clause IDs.
Every document is dated, and each opening carries a case header (doc `CASE`) filling the A2 parameters.
The change type is renamed `gap_filled`. `request_evidence` is never a final action, and growth that
can't be computed at the decision means escalate (`skipped_escalation_rule` when declined).
Not done: withdrawals (C2A, O6) need an `update_record` mode the tools don't have yet, and no
family tests O6-O9 or F1(b) at the final decision.

## Mentor code-review round (Nov 24 2026)

Answers the code-review feedback in CLAUDE.md (status per point is in the feedback table above). Four
changes, each on its own local branch, reviewed, merged together on `integration/mentor-feedback`, not
pushed: 1008 tests pass, `make family-check` clean. Runs made before a change used the older behaviour.

**1. Mechanics wording** (`stage6/mechanics-wording`). The system prompt now says: "A final action is
taken only by calling its tool; stating a decision in your reply text does not take it." The `log_event`
description now says: "Log a development (something that happened) reported in a document. It never
changes a figure; to change or correct a figure, use update_record." Reason: in the Nov 19 Qwen run, 6 of
36 runs stated a decision in text without calling the tool, and 9 logged a correction through `log_event`.
Wording only; no tool or schema change.

**2. Expected state on every turn** (`stage6/expected-state`, schema 5). Every turn of every conversation
carries a grader-only `expected_state`: `metric_value`, `meets_threshold`, `lean` (renew, decline, escalate,
waiting_for_evidence), `pin_moved`, `metric_changed`, `reaction` (update_record, log_event, no_change). It is
computed in `play()` (`family.py`) from the same logic as the checkpoints (`lean_of`, `reaction_of`), never
typed. Conversations, gold and checkpoints are unchanged (checked: the four family files differ only by this
field and the schema version, 449 turns). The agent never sees it. `docs/families/*.md` show it per turn.

**3. Grader: IDs, one primary problem, details** (`stage6/grader-labels`).
- `run_uid` (`trace.run_uid`) is unique across experiments, unlike `run_id`. Every label has a `label_id` =
  `<run_uid>-<6 hex of sha256 of "label|turn|sorted evidence">`, the same on every regrade of the same
  trace; a repeat within one run gets `-2`, `-3` ... in list order.
- A problem is a label with severity above 0. The primary problem is the one with the earliest `turn` (a
  label with turn None counts as after the last turn). Within one turn, a label tied to a trace event
  (something the agent did) comes before a finding with no event (something it never did, known only at the
  end); then higher severity, then lower first evidence index (then list order). Every problem gets `role`
  "primary" or "secondary"; severity 0 gets None. The result has `primary_label`, `primary_label_id` and
  `first_problem_turn`. The rule reads only turn, severity and evidence, never a label's name. Example: a
  correction logged as an event on turn 3 and never written to the record counts as `event_wrongly_logged`,
  with `missed_update` secondary. It is a counting rule so each run is counted once, NOT proof that this
  problem caused the later ones.
- Labels with no single trace event are dated: a finding about the final record (`missed_update`,
  `missing_source`, `wrong_change_type_at_end`, `event_not_logged`) gets the turn its change or event was
  due (the turn the document was given); `record_wrong_at_end` has no turn; `no_decision`, `limit_hit` and
  `repeated_errors` get the turn the run stopped on; `tool_misuse` (one label per run) gets the turn of its
  first rejected call.
- `detail` (None when there is no finer split):
  - `no_decision`: recommended_instead, decision_stated_in_text, empty_reply, asked_question, other. Read from
    the agent's last reply after the final-decision request; these are heuristic.
  - `event_wrongly_logged`: correction_logged_as_event, past_fact_logged_as_event, other.
  - `decided_too_early`: in_opening_reply, before_final_request.
  - `tool_misuse`: schema_error, unknown_tool, unknown_fact, document_not_given, after_final_action,
    calculation_error, unknown_calculation, not_executed_limit, other (the most frequent kind of rejection
    in the run; the note lists all kinds).
- `labels.csv` columns: run_id, run_uid, model, family, version, version_type, mode, family_changed,
  label_id, label, group, detail, role, turn, severity, accepted, recovered, evidence, note. `runs.csv` gains
  `run_uid`, `check_primary_label`, `check_primary_label_id`, `check_first_problem_turn`.
- Report (`cli report`) new sections: "Primary problem per run" (each run counted once), "Problems that occur
  together" (the secondary labels that came with each primary problem), a Detail column in "Problem labels",
  and a run uid and primary problem in "Runs".
- Checked on the 36 saved Qwen runs: pass/fail, decisions and all earlier labels are identical with the old
  and new grader.

**4. Grader: decision state by turn** (`stage6/turn-state`).
- The observed lean on a turn is read from tool calls only, never text: the turn's final action; else its
  last accepted `recommend`; else, if it called `request_evidence`, waiting_for_evidence (not on a turn where
  the analyst made a claim or asked a "what if": RNW-4 E6 allows asking there and the pin does not move, so
  no lean is stated); else not stated.
- `turn_states` has one entry per played turn: turn, kind, expected_lean, pin_moved, observed_lean,
  lean_ok (None if not stated), reaction (expected), reaction_ok (None for no_change), recomputed (None
  unless metric_changed). reaction_ok: for update_record, an accepted write of the turn's fact to its new
  value citing its evidence document, in this turn; for log_event, an accepted event log citing the turn's
  notice, in this turn. `state_summary`: turns, lean_stated, lean_correct, lean_wrong, first_wrong_lean_turn.
  Both are empty for a family file without `expected_state` (schema 4), and then no state labels appear.
- Six labels in group `state`: `lean_correct` (severity 0); `lean_wrong` (2; not for a final action, which
  the decision label grades, nor for a request_evidence reply on the final-decision turn; detail
  `stale_lean` when the agent kept the lean from before the pin moved, else `wrong_lean`); `lean_not_stated`
  (0; a tentative request where the agent called neither recommend nor request_evidence; detail
  `after_pin_moved` or `at_tentative_request`); `late_update` and `late_event_log` (1; a turn's change or
  event applied in a later turn, dated by its due turn, `recovered` True; if never applied, `missed_update` /
  `event_not_logged` covers it); `not_recomputed` (1; after a metric change was applied, the next
  recommendation or final action had no calculation on the updated figures, and no math label already
  covers it).
- No state label changes `passed`, `decision` or any other label.
- `runs.csv` gains `check_first_wrong_lean_turn`; the report gains "Decision state by turn".

**Next runs.** Qwen on RNW-4, all four families, k = 3 (216 runs, overnight, free); then one Claude smoke run
and the 36-run Claude run.
**Next build rounds.** PR 5 gates; PR 6 review and multiple agents (waits on the team decision on single
writer vs proposals).
**Further label splits proposed, not built.** `wrong_change_type` by direction; `tool_misuse` as one label per
kind; `no_decision` details `named_tool_in_text` and `stopped_early`.

## Open questions

- Who adds the keys (Anthropic + Langfuse) to `capstone-env/.env` for the Claude run. (Model chosen: `claude`.)
- Settled Nov 24: stay on Langfuse. LangSmith was considered (one dev project plus dataset and experiments)
  and not adopted, because the team has free Langfuse credits.
- What counts as asking for proof: the `request_evidence` tool only, or plain text too.
- Limits (15 / 60 / 120 s / 3) — confirm.
- Multi-agent: single record writer vs proposals; which setup after the pilot (B or C).
- Langfuse: send `run_uid`, `label_id`, the primary problem and the per-turn summary as scores, and show the
  thinking beside each tool call. Not built: the agent assigned to it was stopped twice by an automatic safety
  filter before writing anything. Decision pending.
- Langfuse access: Dhruvi and Victor still need an invite to the project (team action).
- RNW-4 rule G2 asks every `update_record` and `log_event` call to cite the outcome and clause, and mentions
  marking a fact withdrawn; the tools have no field for either and reject extra fields. Team decision: add
  optional fields, or change G2.
- The two Langfuse sessions shared in #11 are public and carry real company names as tags that are described
  as team-only.
- Claude's thinking may come back empty unless a summary is requested; check in the first Claude smoke run.
- `docs/policy/RNW-4_clause_map_F001.md`: the version 1 row predates decision F-1 and still says escalate.

## Resuming

```bash
git fetch && git checkout main && git pull      # PRs 1-4 and RNW-4 are merged; the Nov 24 round is not (below)
git checkout integration/mentor-feedback        # local only until pushed: the four Nov 24 changes
cd capstone-env && make build && make test      # 1008 tests on that branch (per the Nov 24 round; 567 with PR 4)
make demo                                        # 4 scripted mock agents through F001, no keys
make poc-estimate                                # cost of the first real run; make poc runs it
make run-families ARGS="--model mock --versions 3 --out results/fam1"   # the real runner, mock model
```
Then read CLAUDE.md (Stage 6 section) and start the next PR in the table above. `make family-check` should
be clean.
