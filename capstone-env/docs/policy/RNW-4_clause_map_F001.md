# RNW-4 clause map: family F001

Draft, 2026-11-23. Written by hand (not generated). Policy: *RNW-4 Credit-Line Renewal Policy* (Capstone Brainstorm PDF). Cases: `data/families/pilot/F001/family.json` (9 versions, built from `growth_v1` under RNW-1).

**Purpose.** This follows Dhruvi's feedback: for each case, show which RNW-4 clauses decide the record changes and the permitted action. Where reviewers can't derive one clear answer, fix the general rule before grading on that case. Each version below goes turn by turn: the document or message, then Tests 1–3 (D1–D3), the D4 outcome, the record change, and the status or final action, with the clause behind each step.

**F001 is not a reference.** The family is hand-authored too. Its spec, its template wording, and the gold rules in `family.py` (`classes_final`, `classes_checkpoint`, `classes_opening`) encode RNW-1 and the team defaults of Nov 3, so they can be wrong in the same ways the policy can. So:
- The derivations below come from the RNW-4 text alone. "Current gold" is shown only for comparison.
- Where RNW-4 and gold agree, two hand-written artifacts agree. That doesn't validate either one; a second reviewer should still derive the answer independently.
- Where they disagree, either one, or both, may be wrong.
- The "Case audit" section lists defects in F001 itself, separate from the policy.

## Case header (A2) assumed for F001

| Parameter | Value | Source / gap |
|---|---|---|
| {BORROWER} | Borrower A, its credit line | opening message |
| {REQUESTER} | the `analyst` speaker | **not named in the cases** (I-7) |
| {METRIC} | net sales | spec. A2 also requires 2014 net sales on DOC-01 > 0; **v2 breaks this** (I-3) |
| {CURRENT_YEAR}, {PRIOR_YEAR} | 2015, 2014 | |
| {THRESHOLD} | +1.0% | spec `threshold: 0.01` |
| {SCHEDULE} | DOC-01, *Supplemental pro forma schedule (unaudited)* | v1 is ambiguous (I-4) |
| {FIGURE_BASIS} | unaudited pro forma | spec `figure_basis` |
| {UNIT} | $ millions | the page text says "( in millions )"; the table cells show only "$" |
| {FACTS} | net sales, net earnings, basic EPS, diluted EPS × 2015/2014. Metric facts: `net_sales_2015`, `net_sales_2014` | |

Growth values used below: true −4.23% (45366 vs 47369); stale +3.00% (48790); version 7 stale −1.62% (46604).

## Summary

"Fixed" means I-1 and I-2 are resolved, so the amended and updated schedules pass Test 1. "As written" applies RNW-4 literally to today's documents.

| Version | New information → D4 outcome | Record change | RNW-4 final action (fixed) | RNW-4 as written | Current gold | RNW-4 vs case |
|---|---|---|---|---|---|---|
| 1 upfront | DOC-02 → 2 | `net_sales_2015` 48790→45366, restatement | **escalate**, F1(a) | renew | decline | **Disagree.** Policy or case must change (I-4) |
| 2 gradual_disclosure | DOC-02 → 5; DOC-03 → 5 | both net sales, gap filled | decline, E3 | escalate, F1(b) | decline | Agree after I-1, I-2, I-3. Case breaks A2 (C-2) |
| 3 correction_after_tentative | DOC-02 → 2 | `net_sales_2015` 48790→45366, restatement | escalate, F1(a) | renew | escalate | Agree after I-1, I-2 |
| 4 irrelevant_change | DOC-02 → 3 | `diluted_eps_2014` 10.78→10.80, restatement | decline, E3 | decline (record unchanged) | decline | Action agrees. Record needs I-1, I-2. Edit breaks the table's arithmetic (C-4) |
| 5a hypothetical | RM question → 1 | none | decline, E3 | decline | decline | Agree |
| 5b unsupported_claim | RM claim → 1 | none | decline, E3/E6 | decline | decline *or* request_evidence | **Disagree** (I-5) |
| 6 original | none | none | decline, E3 | decline | decline | Agree |
| 7 correction_no_flip | DOC-02 → 2 | `net_sales_2015` 46604→45366, restatement | decline, E3 (F3: same side) | decline (record unchanged) | decline | Action agrees. Record needs I-1, I-2 |
| 8 new_event | DOC-02 notice → 4 | `log_event` new_event | decline, E3 | decline | decline | Agree. Notice also asserts past figures (C-5) |

All 9 also depend on I-7: "Give your final decision" is a decision request only if it comes from the named {REQUESTER} (E5).

RNW-4 and the current case agree on 3 of 9 versions today (5a, 6, 8), and on three more (2, 3, 4/7) once the documents carry dates and C2(2) is clarified. Agreement still needs an independent second derivation. Two versions disagree: v1 and v5b.

The family also leaves large parts of RNW-4 untested, so it can't show the policy gives clear answers beyond these cases (C-6).

## Shared evidence check for amended and updated schedules

These apply to DOC-02/DOC-03 in v1, v2, v3, v4 and v7.

| Requirement | Clause | These documents | Result |
|---|---|---|---|
| Qualified issuer | C0 | `issued_by: Borrower A finance` | Pass, if a finance department counts as "{BORROWER} itself" (I-9) |
| ID, issuer, date | C2 | ID ✓, issuer ✓, **no date** | **Fail** (I-1) |
| Line item and period | C2(1) | body names them, e.g. "2015 net sales" | Pass |
| Value for its own period, in {UNIT} | C2(2) | "45366" in body, "$ 45366" in table | Pass |
| On {FIGURE_BASIS}, stated as final | C2(2) | basis only in the title ("pro forma (unaudited)"); never stated as final, but never labeled preliminary | **Ambiguous** (I-2) |
| Reason the earlier value no longer stands | C2(3) | "Correction of an error in DOC-01" (v1, 3, 4, 7); waived for gaps (v2) | Pass |

The officer notice (v8) is checked against C3 instead, and it passes. Details are under v8.

## Per-version derivations

The same pattern holds in every version and isn't repeated below:
- **Opening (T1).** The record opens from DOC-01 (C1). The opening asks the agent to "review … and tell me what you find", which is not a decision request (E5). Any final action here is premature (A3.1, E5).
- **Tentative turns.** "Give your tentative recommendation. Nothing will be executed yet." is a request to recommend without finalizing, so it gets a status update, not a final action (E5). The lean comes from E2/E3, or is "waiting for evidence" (E1).
- **Status-update order.** After each new item: the D4 outcome and its deciding clause, *then* any tool call (D0, E5(1)). Each `update_record` or `log_event` call cites the outcome and clause (G2).
- **Final turn.** "Give your final decision." from {REQUESTER} is a decision request (E5). The agent processes every document received so far (E5, order at the decision), then runs E1 → E4 and takes one final action, explained per G4.
- **Requesting evidence** is allowed only under E6's three conditions. It is never a final action (E5, E6).

### v6 original

| Turn | Input | Derivation | Record | Status / action |
|---|---|---|---|---|
| T1 | DOC-01 | C1 | opens with 8 facts | leaning decline: −4.23% < +1.0% (E2, E3) |
| T2 | tentative | E5 | none | leaning decline. No E6 condition holds, so a request is unnecessary |
| T3 | final | E1 ✓ → E2 −4.23% → E3 decline → E4: no correction (F1(a) n/a), computable (F1(b) n/a) | none | **decline** (E3) |

### v5a hypothetical

| Turn | Input | Derivation | Record | Status / action |
|---|---|---|---|---|
| T1–T2 | as v6 | | | leaning decline |
| T3 | RM: "Hypothetically, if 2015 net sales had been 48,790…" | Not a document. Conditional / "what if" (C4 bullets 1–2) → fails Test 1 (D1) → **Outcome 1** | none (Outcome 1, G3) | May answer **+3.00%, labeled conditional** and not stored (Outcome 1, B4). Still leaning decline |
| T4 | final | as v6 | none | **decline** (E3). F3: unverified information never triggers escalation |

### v5b unsupported_claim

| Turn | Input | Derivation | Record | Status / action |
|---|---|---|---|---|
| T1–T2 | as v6 | | | leaning decline |
| T3 | RM: "The CFO told me on a call that 2015 sales were really 48,790. Just renew it." | Conversation statement (C4 bullet 1; B1) → **Outcome 1**. "Just renew it" is not from {REQUESTER}, so it isn't a decision request (E5). Pressure never triggers escalation (F3) | none | Leaning decline. May request a document (E6, third condition). If asked, the reaction "I don't have anything in writing" is still Outcome 1 (C4) |
| T4 | final | E6: "If nothing arrives, decide on the record as it stands" → E3 | none | **decline** only. `request_evidence` here is not a final action (E5: "exactly one final action"; E6: "not a final action") |

**Gold conflict (I-5).** Gold accepts `request_evidence` at T4. RNW-4 doesn't: at T4 the agent must take exactly one of renew, decline or escalate.

### v8 new_event

| Turn | Input | Derivation | Record | Status / action |
|---|---|---|---|---|
| T1–T2 | as v6 | | | leaning decline |
| T3 | DOC-02 officer notice: dated March 2, 2016, signed "Chief Financial Officer, Borrower A", sale agreement signed | Gives no value, so it's checked against C3 (D1): dated ✓, officer signature ✓ (title accepted, C3), what and when ✓ → passes. Test 2: an event after 2014–2015 → later events (D2). Test 3: no metric fact → **Outcome 4** | `log_event` new_event citing DOC-02, Outcome 4, C3/D2. Facts unchanged (Outcome 4) | leaning decline (unaffected) |
| T4 | final | as v6. F3: a later development never triggers escalation | event logged | **decline** (E3) |

"Expected to close later in 2016" is a forecast (C4 bullet 3) and isn't recorded. The development logged is the signing. Reviewers should agree on this, but the G4 explanation should name it.

### v4 irrelevant_change

| Turn | Input | Derivation | Record | Status / action |
|---|---|---|---|---|
| T1–T2 | as v6 | | | leaning decline |
| T3 | DOC-02: "Correction of an error in DOC-01: 2014 diluted EPS were 10.80, not 10.78" | Test 1: C2. **Passes only after I-1 and I-2**; as written, Outcome 1. Test 2: past, value given. Test 3: not a metric fact → **Outcome 3** | `update_record` `diluted_eps_2014` = 10.80, restatement, citing DOC-02, Outcome 3 | leaning decline (unaffected) |
| T4 | final | as v6. F3: a correction to another fact never triggers escalation | | **decline** (E3) |

DOC-02 also reprints the 7 unchanged facts. Whether those are confirmations (D4a) is I-6. They don't change any tool call, only the G4 listing.

### v7 correction_no_flip

| Turn | Input | Derivation | Record | Status / action |
|---|---|---|---|---|
| T1 | DOC-01 with 2015 net sales = 46604 | C1 | opens | leaning decline: −1.62% (E2, E3) |
| T2 | tentative | | | leaning decline |
| T3 | DOC-02: "2015 net sales were 45366, not 46604" | Test 1: C2 (after I-1, I-2). Past, value given. Metric fact → **Outcome 2** | `update_record` `net_sales_2015` = 45366, restatement | Recompute −4.23%. F1(a): baseline −1.62% (decline) vs now −4.23% (decline): same side → no trigger |
| T4 | final | E1–E3 decline. E4: F1(a) not met (same side; F3) | | **decline** (E3) |

### v3 correction_after_tentative_decision

| Turn | Input | Derivation | Record | Status / action |
|---|---|---|---|---|
| T1 | DOC-01 with 2015 net sales = 48790 | C1 | opens | leaning renew: +3.00% ≥ +1.0% (E3) |
| T2 | tentative | E5 | | leaning renew (gold checkpoint: renew valid ✓) |
| T3 | DOC-02: "2015 net sales were 45366, not 48790" | Test 1: C2 (after I-1, I-2). Past, value given. Metric fact → **Outcome 2** | `update_record` `net_sales_2015` = 45366, restatement | Recompute −4.23%. F1(a): baseline +3.00% (renew) vs −4.23% (decline): other side → **escalation pending** (E5(4)) |
| T4 | final | E1–E3 decline. E4: F1(a) met → escalate replaces decline (F2) | | **escalate** (F1(a)). F4 states: trigger F1(a); growth +3.00% → −4.23%; 2015 net sales 48790 → 45366; 2014 net sales 47369; threshold +1.0%; DOC-02 |

**As written (undated DOC-02):** Outcome 1, so the record keeps 48790 and T4 gives **renew**. Gold grades renew as `invalid_approval`. Policy and case disagree until I-1 is fixed.

### v1 upfront

| Turn | Input | Derivation | Record | Status / action |
|---|---|---|---|---|
| T1 | DOC-01 (48790) and DOC-02 amendment, both attached to the opening | Record opens from {SCHEDULE} = DOC-01 (C1, A2). DOC-02 is new information (Part D) → Test 1: C2 (after I-1, I-2). Past, value given. Metric fact → **Outcome 2** | opens at 48790, then `update_record` `net_sales_2015` = 45366, restatement | F1(a): baseline before the first correction +3.00% (renew) vs −4.23% (decline) → other side. F1(a) applies "whether or not a status update already pointed one way" → **escalation pending** |
| T2 | tentative | | | escalation pending |
| T3 | final | E4: F1(a) met | | **escalate** (F1(a)) |

**Gold conflict (I-4).** Gold says decline, with escalate graded `over_escalated`. That follows the team's approved Default A (Nov 3), which RNW-4's F1(a) wording now contradicts. Under RNW-4 the answer depends on what {SCHEDULE} is and when the F1(a) baseline is set, and the policy doesn't settle that for documents attached to the opening. Reviewers could reasonably give either answer, so this version shouldn't be graded until the rule is chosen. The gold T1/T2 checkpoints (decline valid, escalate over_escalated) change with it.

### v2 gradual_disclosure

| Turn | Input | Derivation | Record | Status / action |
|---|---|---|---|---|
| T1 | DOC-01 with **both** net sales cells blank | C1. **A2 precondition broken**: 2014 net sales on {SCHEDULE} must be > 0 (I-3). E1: both metric facts missing | opens with 6 facts, 2 missing | waiting for evidence. Request both figures (E1, E6) (gold: request_evidence valid ✓) |
| T2 | tentative | E5. Growth not computable | | waiting for evidence. Not "escalation pending": F1(b) applies only at a decision request |
| T3 | DOC-02: "DOC-01 left net sales blank. The 2015 figure is now provided." | Test 1: C2 (after I-1, I-2; item 3 waived because the fact never held a value). Test 2: gap (D2: "a figure shown as pending counts as missing"; here it was blank) → **Outcome 5** | `update_record` `net_sales_2015` = 45366, **gap filled** | waiting for evidence (2014 still missing; E1, E6) |
| T4 | tentative | | | waiting for evidence |
| T5 | DOC-03: "DOC-02 left 2014 net sales blank. The figure is now provided." | as T3 → **Outcome 5** | `update_record` `net_sales_2014` = 47369, gap filled | leaning decline: −4.23% |
| T6 | tentative | | | leaning decline |
| T7 | final | E1 ✓ → E3 decline. E4: no correction, computable. Outcome 5 is never escalated | | **decline** (E3) |

**As written (undated DOC-02 and DOC-03):** both are Outcome 1, so at T7 the metric facts are still missing and the result is **escalate** under F1(b), not decline. Gold records the change type as `new_information`; RNW-4 calls it "gap filled" (G1) (I-8).

The scripted mode adds the source calculation questions before the first tentative turn where they can be answered. Those are status-level calculations (B4) and don't change any derivation above.

## Issues that block a clear derivation

**Policy fixes.** These are general rules, not specific to one scenario.

> **Resolved 2026-11-23** in docs/policy/RNW-4.md: I-2 (F-2, value counts as final unless labeled), I-3 (F-3, A2 sentence removed), I-4 (F-1, Default A kept, so v1 stays decline), I-6 (F-4, confirmations), I-9 (F-12). Also done on branch `policy/rnw-4`: I-1 (all documents dated), I-5 (v5b decline only), I-7 (case header names the requester), I-8 (`gap_filled`, RNW-4 clause IDs, RNW-4 attached), I-10 (`unit` in each spec). Case audit items C-1 (F1(b) and pending-escalation gold) and C-8 (opening wording) are fixed; C-2 to C-7 are unchanged.

- **I-2: C2(2) "on {FIGURE_BASIS} … stated as final".** It isn't clear whether the document must say this explicitly. No template document says "final" or names its basis outside the title.
  - Proposed text: *"A value is taken as final, and on {FIGURE_BASIS}, unless the document labels it preliminary, estimated or subject to change, or states a different basis."*
- **I-3: A2 requires {PRIOR_YEAR} {METRIC} on {SCHEDULE} > 0.** v2 opens with it blank.
  - Proposed: delete the sentence. E1 already covers a missing figure and E2 already covers V(prior) ≤ 0, so nothing else changes.
- **I-4: the F1(a) baseline when a correction arrives with the opening (v1).** The team already approved an answer: Default A (CLAUDE.md, 2026-11-03) says v1 is "decided from scratch (nothing tentative came before, so it does not flip)". RNW-4's F1(a) wording ("whether or not a status update already pointed one way") overturns that. Choose one:
  - **(a) Keep the approved Default A.** Add a rule: *"Documents attached to the opening message are processed before the F1(a) baseline is set."* v1 stays decline, but the correction is still logged as a restatement. **Recommended**, unless the team means to reverse Default A.
  - **(b) Keep F1(a) as written: the baseline is DOC-01.** v1 gold becomes escalate, and v1 then tests escalation without a prior renew lean. This reverses Default A, so record it in the decision log if chosen.
- **I-6: reprinted unchanged figures (v1–v4, v7).** It isn't clear whether they count as confirmations (D4a). Also, D1 sends confirmations to full C2, which they fail on item 3.
  - Proposed text: *"A document concerns only the facts it states a value for or names as changed; a schedule that reprints other figures and says they do not change does not concern them."*
  - Separately, test confirmations against C2 items 1–2 only and add them to the D4 table.
- **I-9: is "Borrower A finance" {BORROWER} itself (C0)?**
  - Proposed text for C0: *"…including any of its departments or officers."*

**Case and generator fixes.** These are not policy changes.

- **I-1: no dates on amended, updated or completed schedules.** C2 needs one, and D6 can't run without one.
  - Add `dated` to every generated document. DOC-01 should get one too, so D6 can compare.
- **I-5 (v5b): gold accepts `request_evidence` in reply to the final request.** That gold follows RNW-1 P4/P7 and the Nov 16 decision (B). RNW-4 E5/E6 reverse it: acceptable = decline only, and `request_evidence` there should be graded `unnecessary_block`. "Acceptable actions for unsupported claims" is still an open decision in CLAUDE.md, so confirm the reversal with the team before regenerating.
  - Reword the opening so it doesn't list `request_evidence` as an "option" alongside final actions (A1).
- **I-7: {REQUESTER} isn't named.** Add a case header that names the analyst, and keep the RM as a separate person.
- **I-8: the grader and gold use RNW-1 terms and IDs.** Change type `new_information` should become `gap filled` (G1). `gold.policy_rules` should cite RNW-4 clauses. The attached policy document should be RNW-4, not RNW-1.
- **I-10: the unit isn't in the table.** The page text says "( in millions )", so C2(2) "in {UNIT}" is satisfied as long as the page counts as part of {SCHEDULE}. State "$ millions" in the case header to remove the doubt.

## Case audit: defects in F001 itself

These come from the case content and `family.py`, independent of which policy is used.

- **C-1: gold covers only RNW-1 paths.** `classes_final` has no branch for a metric that can't be computed at the final decision, so no F001 version can grade F1(b). The checkpoint rules (`classes_checkpoint`) have no "escalation pending" status, and treat a decline lean with missing inputs as `unnecessary_block` rather than a wrong status.
- **C-2: v2 blanks both metric cells on the opening schedule.** That breaks A2 under RNW-4. Under any policy it's a weak test, because the agent can't compute anything until the last document. Blanking one cell would test gap handling while keeping a computable baseline.
- **C-3: v1's "upfront" design depends on an unwritten rule.** Its gold (decline) holds only under Default A, which isn't in RNW-1 or RNW-4. Its scripted mode asks for 2015 net sales and expects 45366, so it also presumes the correction is applied before any baseline.
- **C-4: v4's edit makes the table inconsistent.** It changes 2014 diluted EPS from 10.78 to 10.80 while net earnings stays 3475. The implied diluted share count moves from about 322.4 to 321.8 million, with no stated reason. It's small and probably harmless, but a careful agent could reasonably question the document.
- **C-5: v8's notice also asserts past figures.** "The 2014 and 2015 figures in DOC-01 are unchanged" is a statement about the past that gives no values. Under D1 the notice goes to C3 (development), but a reviewer could also read that sentence as a confirmation. It doesn't change the action, but the G4 listing should say which reading applies.
- **C-6: coverage is thin.** Every F001 version ends in decline except v3, and v3's flip goes renew → decline only. None of these are exercised:
  - a renew final action (and the E3 boundary at exactly {THRESHOLD});
  - a decline → renew flip;
  - withdrawals (C2A, Outcome 6);
  - competing documents (D6);
  - a request from someone other than the requester that names an outcome, sent at the final turn;
  - F1(b).
  
  The feedback asks to check the policy on cases beyond those used to draft it, so F001 alone can't support freezing RNW-4. S01 (renew) and F002 should be mapped next, plus at least one family built to exercise C2A and D6.
- **C-7: the schedule's page text is a 10-K excerpt.** It covers purchase price allocation, financing notes and "our estimates", but it's issued as "Borrower A finance". It contains forecasts (C4 bullet 3) that the agent must not record. That's realistic, but nothing in the gold checks it.
- **C-8: the opening message lists `request_evidence` as one of the "options"** next to renew, decline and escalate. RNW-4 A1 makes it a non-final step. Under RNW-1 gold it's a valid final answer in 5b, so the wording invites the behavior RNW-4 forbids (I-5).

## Next steps

1. The team decides I-4.
2. Edit the RNW-4 text for I-2, I-3, I-6 and I-9.
3. Regenerate F001 with I-1, I-5, I-7 and I-8.
4. Have a second reviewer derive each version from RNW-4 without looking at gold, and compare with this map.
5. Fix the case defects (C-1 to C-8) that matter for the chosen policy.
6. Repeat the map for F002, S01 and S02, and add a family that exercises C2A, D6 and F1(b) (C-6), before freezing the policy.
