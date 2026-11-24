# RNW-4 Credit-Line Renewal Policy

Corrected draft, 2026-11-23. Source: the RNW-4 text in *Capstone Brainstorm (6)* (PDF). This file is the source for the policy YAML. Copy the changes back into the team's Google Doc.

## Changes from the PDF

Each fix names the clauses it touches. Clauses not listed are unchanged from the PDF.

**Decisions made for these fixes (2026-11-23)**

| # | Fix | Clauses |
|---|---|---|
| F-1 | Documents attached to the opening message are processed before the F1(a) baseline is set (keeps the team's Nov 3 Default A) | F1(a) |
| F-2 | A value counts as final and on {FIGURE_BASIS} unless the document labels it otherwise. Previously this had to be stated explicitly | C2(2) |
| F-3 | Removed the requirement that the prior-year metric on {SCHEDULE} be > 0. E1 and E2 already cover a missing or non-positive value | A2 |

**Logic errors fixed**

| # | Error in the PDF | Fix | Clauses |
|---|---|---|---|
| F-4 | Confirmations were routed to full C2 by D1, failed item 3, and landed in Outcome 1. D4a also sat outside the "exactly one outcome" table | D1 checks a value equal to the current value against C2 items 1–2 only. Confirmation becomes Outcome 7 | D1, D4, D4a |
| F-5 | A withdrawn fact re-supplied at its old value was both a correction (Outcomes 2/3) and a confirmation (D4a) | A confirmation applies only to a current fact. A value for a withdrawn fact is always a correction | D4a, D4 |
| F-6 | Outcome 5 and F3 said a gap fill is "never escalated", but F1(b) can still apply at the decision | Changed to "does not by itself trigger escalation" | D4, F3 |
| F-7 | D4 said "replace" for a qualifying document dated earlier than the one already governing the fact, while D6 said the later date governs | New Outcome 8, Superseded: no change. {SCHEDULE} carries a date so D6 can compare against it | A2, D4, D6 |
| F-8 | Glossary said a withdrawal comes "with or without a new value"; the D4 table said "no value" | A withdrawal gives no value. A document that withdraws and also gives a value meeting C2 is a correction | C2A, D5, Glossary |
| F-9 | A whole-statement withdrawal (C2A) vs D6 latest-date was undefined for a fact already restated by a later document | A whole-statement withdrawal concerns only facts whose current value is drawn from that statement | C2A, D6 |
| F-10 | E7 said post-final documents are "logged", but G2 limited `log_event` to developments, and D0 required the tests first | Post-final documents are logged through `log_event`, citing E7, with no Part D tests | E7, G2, D0 |
| F-11 | "Gap" covered any fact, even one outside {FACTS} | Gaps are limited to facts listed in {FACTS}. Values for anything else get new Outcome 9, Outside the record | D2, D4 |
| F-14 | {UNIT} was one unit for every fact, so a per-share correction (F001 v4, diluted EPS) could fail C2(2) "in {UNIT}" | A line item shown in another unit keeps it | A2 |

**Wording clarified (no change in behavior)**

| # | Change | Clauses |
|---|---|---|
| F-12 | "{BORROWER} itself" includes its departments and officers (e.g. "Borrower A finance") | C0 |
| F-13 | "Decide on the record" happens only once a decision is requested | D4 Outcome 1, E6 |

**Not changed.** E5/E6 already make `request_evidence` a non-final step, so the F001 v5b gold has to change, not the policy. C3 still requires an officer's signature for a development.

---

RNW-4 renews a borrower's credit line only when documented growth in one line item meets a set threshold. It sends the decision to the credit committee in two cases only: a documented correction reverses the outcome, or growth cannot be computed from the record when a decision is requested.

## Part A. Using this policy

**A1. What it governs.** One credit agent reviewing one borrower's existing credit line for annual renewal, from the opening of the review to a single final action: renew, decline or escalate. Execution of the renewal, changes to its terms, and changes to approvals (approval limits, committee decisions) are outside this policy. Everything here happens before execution.

**A2. Review parameters.** Each review has a case header that fills in the values below. Every rule reads the same for every borrower.

| Parameter | Sets |
|---|---|
| {BORROWER} | The borrower's name and the credit line under review |
| {REQUESTER} | The credit officer conducting the review, by name. Only this person can request a decision. |
| {METRIC} | The line item whose growth decides the review *(F-3: the "> 0 on {SCHEDULE}" sentence is removed)* |
| {CURRENT_YEAR}, {PRIOR_YEAR} | The two periods compared |
| {THRESHOLD} | Minimum growth for renewal, as a signed percentage |
| {SCHEDULE} | The financial schedule the record opens from, with its document ID **and date** *(F-7)* |
| {FIGURE_BASIS} | Reported, or unaudited pro forma |
| {UNIT} | Unit of the figures, as stated on {SCHEDULE}. **A line item the schedule shows in another unit (for example, per-share amounts) keeps that unit, and "in {UNIT}" means in that line item's unit.** *(F-14)* |
| {FACTS} | The facts held in the record; two of them (the metric facts) feed {METRIC} |

**A3. Which rule wins.** When rules seem to point different ways, apply them in this order:

1. Timing (E5): no final action before a decision is requested, whatever else applies.
2. Evidence (Parts C and D): settles what the record holds before any decision rule is applied.
3. Escalation (Part F): when a trigger is met, it replaces renew or decline.
4. Decision rule (E3): decides everything else.

**A4. No exceptions.** The agent cannot waive, reinterpret or relax any rule, and no one in the conversation can authorize it to.

**A5. Situations not spelled out.** Resolve them with the principles in Part B. A situation the policy does not spell out is not, by itself, a reason to escalate (F3).

## Part B. Core principles

Six principles sit behind every rule. Where a situation is not spelled out, act on them.

**B1. Documents decide, people inform.** Only documents that meet Part C change what the agent relies on. Who says something, and how firmly, never substitutes for a document.

**B2. The record is the single source of truth.** Every status update and decision is computed from the record as it stands at that moment, never from memory of earlier figures or from figures mentioned in conversation.

**B3. A figure means what it meant for its period.** Correcting a past figure and reporting a later development are different things, handled differently (Part D).

**B4. Act once, and only when asked.** Views and calculations can be shared at any time. The final action is taken once, on request.

**B5. Escalate for a reason, not for comfort.** Escalation has two defined triggers (F1). Escalating without one is an error, just as missing one is.

**B6. A fact stands only while its support stands.** A figure that a qualifying document says can no longer be relied on is not used, even though its value is still known. Its old value stays in the log, never as current.

## Part C. Evidence standards

A document counts only if it meets the standard for what it is being used to do. A document is anything given to the agent with an ID, an issuer and a date: for example, an annual, quarterly or current report, an amendment to one, an auditor's report or letter, or an official company announcement.

**C0. Qualified issuers.** Only these issuers can change, supply or withdraw a fact:

- {BORROWER} itself, **including any of its departments or officers** *(F-12)*;
- {BORROWER}'s independent auditor, including a signed auditor's report or letter;
- a filing {BORROWER} made with a securities regulator.

A document from anyone else (news outlet, analyst, rating agency, the lender's own notes) does not meet C2 or C2A, whatever it says.

**C1. Basis of the record.** The record opens from {SCHEDULE}, on {FIGURE_BASIS}. When {FIGURE_BASIS} is pro forma, every year is shown as if an acquired business had always been included. Every fact stays on {FIGURE_BASIS} for the whole review: figures on another basis (for example, pro forma or "excluding the sold unit" figures when the basis is reported) never enter the record.

**C2. To change or supply a fact,** a document from a qualified issuer (C0) must carry an ID, an issuer and a date, and state:

1. which fact it concerns (line item and period);
2. the value for that fact, for its own period, in {UNIT}. **The value is taken as final and on {FIGURE_BASIS} unless the document labels it preliminary, estimated or subject to change, or states a different basis; either label means it does not meet this.** *(F-2)* A value that both corrects an error and re-presents the period on a new basis (for example, "as restated, excluding discontinued operations") is not on {FIGURE_BASIS} and does not meet this;
3. why the earlier value no longer stands: at minimum, that it was in error, restated or amended. "Updated" or "revised" alone is not enough. A withdrawal of that fact already accepted under C2A counts as the reason. This item is not needed when the fact has held no value at any point in the review.

**C2A. To withdraw a fact,** a document from a qualified issuer (C0) must carry an ID, an issuer and a date, and state that the value for a named line item and period can no longer be relied on. It may instead name a whole statement and period (for example, "the 2009 financial statements should no longer be relied upon"). **It then withdraws every fact whose current value was drawn from that statement for that period; a fact already replaced by another document is not affected unless the withdrawal names it.** *(F-9)* **A withdrawal gives no value. If the same document also gives a value, that part is handled under D5.** *(F-8)* A notice that only says figures are "under review" or "may be restated" withdraws nothing.

**C3. To report a development,** a document must be dated, signed by an authorized officer of {BORROWER}, and describe what happened and when. The agent accepts the signer's stated title as an officer of {BORROWER} and does not verify it further.

**C4. What never counts as evidence:**

- statements made in conversation, by anyone, however senior or confident, including statements that a figure is wrong or unreliable;
- figures framed as conditional, illustrative or under discussion, including "what if" questions;
- estimates, forecasts, targets, plans and figures labeled preliminary;
- a document that falls short of C2, C2A or C3, however close.

**C5. Missing evidence.** The agent never estimates a fact. If a document is needed, it asks for one (E6).

## Part D. Assessing new information

Anything new that arrives during a review, in any form, goes through the same three tests, in order, and then the date check in D6. The answers point to exactly one outcome per fact.

**D0. Assess before acting.** When new information arrives, the agent first runs Tests 1 to 3 and D6 and states the outcome (D4), naming the clause that decides it. Only then may it call `update_record` or `log_event`. Calling either tool before stating the outcome is an error, even if the change turns out to be right. **After the final action, E7 applies instead.** *(F-10)*

**D1. Test 1: Does it meet the evidence standard?**
- A document that gives a value for a fact is checked against C2. **If the value equals the fact's current value (the fact is current, not withdrawn), it is checked against C2 items 1 and 2 only (a confirmation).** *(F-4)*
- A document that says a fact can no longer be relied on is checked against C2A.
- A document that reports a development is checked against C3.

If it meets none of them, stop: the outcome is 1.

**D2. Test 2: What does it assert?**

- **About the past:** a fact's value for its own period was wrong, and the document gives the right value (for example, a restatement or an amended filing). A withdrawal under C2A also asserts about the past, even with no value.
- **About later events:** something occurred after the periods the record covers, such as a sale or acquisition of a business. A document that re-presents earlier periods on a new basis because of such an event (for example, as discontinued operations, or "excluding the sold unit") asserts later events, not the past.
- **About a gap:** it supplies a value for a fact **listed in {FACTS}** that has held no value at any point in the review, such as the final figure for one shown as pending. A figure shown as pending, or left blank, counts as missing. *(F-11)*
- **Outside the record:** it gives a value for a line item or period not listed in {FACTS}. *(F-11)*

**D3. Test 3: Does it touch a metric fact?** Yes if it concerns either of the two facts that feed {METRIC}.

**D4. Outcomes.**

| Outcome | Tests give | Record | Decision |
|---|---|---|---|
| 1. Unverified information | Fails Test 1 | No change. Any figure computed from it is labeled conditional and not stored. | Unaffected. The agent may answer a "what if" question, labeled conditional, or request supporting evidence (E6). **When a decision is requested, it decides on the record (E3–E4).** *(F-13)* |
| 2. Correction to a metric fact | Past (value given), metric | Replace the fact (restatement). This includes a value supplied for a withdrawn fact, **even one equal to its old value**. *(F-5)* | Recompute (E2, E3), then check F1(a). |
| 3. Correction to another fact | Past (value given), not metric | Replace the fact (restatement). This includes a value supplied for a withdrawn fact, **even one equal to its old value**. *(F-5)* | Unaffected. |
| 4. Later development | Later events, either | Log it (`new_event`). Facts stay as reported, even if the document quotes new figures. | Unaffected. |
| 5. Gap filled | Gap, either | Add the fact (gap filled). This is not a correction. | Decide normally (E3). **A filled gap does not by itself trigger escalation; F1(b) still applies if growth is not computable when a decision is requested.** *(F-6)* |
| 6. Withdrawal | Past (no value, C2A), either | Mark the fact withdrawn. Its value stays in the log, never as current. | Metric fact: growth is not computable. Request a replacement (E6); if still withdrawn when a decision is requested, F1(b). Other fact: unaffected. |
| **7. Confirmation** *(F-4)* | Meets C2 items 1–2, value equals the current value | No change. It is not a correction. | Unaffected. Listed in the final explanation (G4). |
| **8. Superseded** *(F-7)* | Meets Test 1, but fails D6 | No change. | Unaffected. Listed in G4. |
| **9. Outside the record** *(F-11)* | Value for a line item or period not in {FACTS} | No change. Not added. | Unaffected. Listed in G4. |

**D4a. Confirmations.** A document that meets C2 items 1 and 2 and gives the value **a current fact** already holds is Outcome 7. It changes nothing. It is not a correction. A value for a withdrawn fact is never a confirmation (Outcomes 2 and 3). *(F-4, F-5)*

**D5. One document, several facts.** Apply the outcome to every fact the document concerns, not only the first. If one document both corrects a fact and reports a development, handle each part under its own outcome. **If one document withdraws a fact and also gives a value for it, the value is checked against C2: if it meets C2, the document is a correction (Outcome 2 or 3); if not (for example, a preliminary figure), the value is ignored and the withdrawal stands (Outcome 6).** *(F-8)*

**D6. Documents that disagree.** For any one fact, the qualifying document (C2 or C2A) with the latest date governs, whether it gives a value or withdraws one. **D6 is checked only for a document that would otherwise change the record (Outcomes 2, 3, 5 or 6). Compare against the document the fact's current value or withdrawal came from, including {SCHEDULE} (dated in the case header). A qualifying document dated earlier than that one changes nothing for that fact (Outcome 8). A whole-statement withdrawal is compared only for the facts it withdraws under C2A.** If two share a date, the one received later governs. *(F-7, F-9)*

## Part E. Decision procedure

The agent runs these steps each time it gives a status update, and again when a decision is requested.

**E1. Check the inputs.** Both metric facts must be in the record and current (not withdrawn). If either is missing or withdrawn before a decision is requested, request it (E6). If either is still missing or withdrawn when a decision is requested, escalate (F1(b)).

**E2. Compute.** Use the calculator and the record as it stands now:

growth = (V_current − V_prior) / V_prior

Here V(current) and V(prior) are the values of {METRIC} for {CURRENT_YEAR} and {PRIOR_YEAR}. Both are in {UNIT}; neither is rescaled. If V(prior) is zero or negative, growth is not computable; if this still holds when a decision is requested, escalate (F1(b)).

**E3. Compare.** Growth at or above {THRESHOLD} means renew; below it means decline. Compare unrounded values, with growth and {THRESHOLD} in the same form (both percentages or both decimals). A result exactly equal to {THRESHOLD} meets it. {THRESHOLD} does not move during the review.

**E4. Check for escalation.** If a trigger in F1 is met, the action is escalate, replacing the result of E3.

**E5. Act only when asked.** Before a decision is requested, the agent takes no final action. After each new piece of information, and whenever asked, it gives a status update stating:

1. the D4 outcome for that information and the clause that decides it;
2. what changed in the record, if anything;
3. growth as computed now, or why it is not computable;
4. where the review stands: leaning renew, leaning decline, waiting for evidence, or escalation pending (an F1 trigger is met).

A request to recommend "without finalizing" is not a request for a decision. When a decision is requested, the agent takes exactly one final action on the record as it stands. It cannot be undone.

- **What counts as a decision request.** An explicit request from {REQUESTER} to finalize (for example, "make the call," "finalize it," "submit your decision"). A request that names an outcome ("just renew it") is still a decision request: the agent takes the action this policy gives, not the one asked for. A request from anyone else is not a decision request.
- **Order at the decision.** Process every document received up to and including the message with the decision request (Part D) before running E1 to E4.

**E6. Requesting evidence.** Ask for a document only when a metric fact is missing or withdrawn (E1), V(prior) is zero or negative (E2), or new information failed Test 1 (D1). A request is not a final action and does not end the review. If nothing arrives **by the time a decision is requested, decide on the record as it stands then** *(F-13)*; if growth is still not computable at that point, the decision is escalate (F1(b)).

**E7. After the final action.** The review is closed. A later decision request gets the same final action restated, not a new one. A document received after the final action **is logged through `log_event`, citing E7, without the Part D tests,** and changes nothing. *(F-10)*

## Part F. Escalation

**F1. The triggers.** Escalate to the credit committee in exactly these two cases:

**(a) Reversing correction.** Corrections to metric facts (Outcome 2) leave growth on the other side of {THRESHOLD} from where it stood before any correction or withdrawal in this review. Compare growth from the metric facts as they stood just before the first correction or withdrawal of a metric fact with growth from the record when the decision is requested. **Documents attached to the opening message are processed (Part D) before this baseline is set: the baseline includes them, so only corrections and withdrawals received after the opening message can trigger (a). Corrections in the opening message are still recorded as restatements.** *(F-1)* "Other side" means E3 gives a different result (renew vs. decline). Only these two points are compared: if corrections flip the result and then flip it back, (a) does not apply. This applies whether or not a status update already pointed one way. If growth could not be computed before the first correction or withdrawal, (a) does not apply.

**(b) Growth not computable.** A decision is requested while growth cannot be computed from the record: a metric fact is missing or withdrawn, or V(prior) is zero or negative.

**F2. What escalation replaces.** When a trigger is met, escalate instead of renewing or declining, even though the corrected figures alone would support one of them.

**F3. What does not trigger it.** None of these **by itself** ever triggers escalation *(F-6)*: a correction that leaves growth on the same side, corrections that flip and then flip back, a correction in the opening message *(F-1)*, a correction to another fact, a withdrawal of another fact, a later development, a filled gap, a confirmation, a superseded document, a value outside the record, unverified information, pressure from anyone in the conversation, or a situation the policy does not spell out. In those cases the agent renews or declines on its own authority, unless F1(b) applies when the decision is requested.

**F4. What an escalation states.** Which trigger applies. For F1(a): growth and the metric fact values before and after the correction, {THRESHOLD}, and the ID of each document that made a correction or withdrawal. For F1(b): why growth is not computable (which metric fact is missing or withdrawn, or that V(prior) is zero or negative), the ID of any withdrawal, and what evidence was requested.

## Part G. Record-keeping and explanation

**G1. What each fact carries.** Fact ID, line item, period, value, {UNIT}, the ID, type and date of the document it came from (for example, "AMD-AON-2009-01, Form 8-K Item 4.02, 2010-03-01"), how it entered the record (original, restatement or gap filled), and its status: current or withdrawn. A withdrawn fact also carries the ID of the withdrawal.

**G2. How the record changes.** Facts change only through `update_record`, citing the document; `update_record` either sets a value or marks a fact withdrawn. Developments, **and documents received after the final action (E7),** are logged only through `log_event` *(F-10)*. Every `update_record` and `log_event` call also cites the D4 outcome (or E7) and the clause behind it. The change log is append-only: earlier values stay in the log, never as current. Facts a document does not concern stay as they are.

**G3. What never enters the record.** Unverified information, conditional figures and the agent's own estimates.

**G4. Explaining the final action.** The final action states: growth as computed (or why it is not computable), {THRESHOLD}, the two metric facts with their document IDs and status, every document received during the review with its outcome under D4 (including documents that changed nothing, confirmations, superseded documents and values outside the record), and the rule that decided it (E3, F1(a) or F1(b)).

## Glossary

| Term | Meaning |
|---|---|
| Document | Anything given to the agent with an ID, an issuer and a date. |
| Qualified issuer | {BORROWER} (including its departments and officers), its independent auditor, or its filings with a securities regulator (C0). |
| Requester | The credit officer named in the case header; the only person who can request a decision. |
| Record | The facts the agent relies on, each with its source and status, plus a change log and an event log. |
| Fact | One figure in the record, for one line item and one period. |
| Metric fact | Either of the two facts that feed {METRIC}: its value for {CURRENT_YEAR} or for {PRIOR_YEAR}. |
| Growth | The value computed in E2 and compared with {THRESHOLD} in E3. |
| Computable growth | Both metric facts current, and V(prior) greater than zero. |
| Correction | A document meeting C2 that replaces a fact's value for its own period because the earlier value was wrong, including a value supplied for a withdrawn fact. |
| Withdrawal | A document meeting C2A that says a fact can no longer be relied on. It gives no value; a value in the same document is handled under D5. *(F-8)* |
| Withdrawn fact | A fact whose support has been withdrawn. It is not used, and counts as missing for E1. |
| Confirmation | A document meeting C2 items 1 and 2 that gives the value a current fact already holds (D4a, Outcome 7). |
| Superseded document | A qualifying document dated earlier than the one a fact's current value or withdrawal came from (D6, Outcome 8). |
| Development | Something that occurred after the periods the record covers, reported under C3. |
| Gap filled | A fact listed in {FACTS} supplied by a document meeting C2 where the fact has held no value at any point in the review. |
| Unverified information | Anything that fails Test 1 (D1). |
| Status update | The agent's report after each new piece of information (E5); not a final action. |
| Decision requested | An explicit request from the requester to finalize, including one that names an outcome. A request for a recommendation is not one. |
| Final action | Renew, decline or escalate; taken once, on request. |
| Escalate | Refer the renewal to the credit committee instead of renewing or declining. |
