# D23 Capstone: Decision Validity Under Changing Facts

*Stale or Correct? Evaluating Evidence and Fact Changes in Multi-Turn Credit-Review Agents* (UCSD DSC 180A × Intuit)

Can a financial agent keep its decisions valid when the facts or evidence change during a conversation? We are building a benchmark to test this. In each test conversation, an agent reviews a company's credit-line renewal under a written policy, using a real annual-report table from ConvFinQA. Each case family has nine matched versions that differ in how one number changes. In some, the agent should update its decision: a documented correction, or a new event. In others, it should keep its decision: an irrelevant change, a hypothetical, or a claim with no proof. Code recomputes the correct answer after each edit. A harness logs every tool call and record update, and a grader compares the agent's final action and record with the answer key.

**AI contributions.** A coding agent (Claude Code) wrote most of the code in this repository and much of its documentation, and helped with debugging. Other AI tools helped summarize papers, suggest report edits, and format citations. A team member reviewed every AI output before it was merged or submitted.

| Area | What the AI did | What we did | How we verified it |
|---|---|---|---|
| **Literature review** | Summarized and compared the papers' methods and limitations | Read every core paper ourselves | Discussed in group and mentor meetings |
| **Code** | Wrote the Docker setup, data pipeline, and tests, and helped debug | Wrote the requirements, approved each plan, made the design decisions | Every PR approved by a teammate; tests; `make profile` and `make family-check` |
| **Data curation** | Built the case families from ConvFinQA, one per session | Wrote or approved the specs, the policy, and the rules; hand-picked some families | Gold labels computed by code; manual review of each family's page |
| **Documentation** | Drafted the READMEs, `CLAUDE.md`, and reports | Reviewed the wording and facts | Reviewed in PRs |
| **Report** | Suggested edits and formatted citations and LaTeX | Wrote the draft, analysis, and conclusions | Each section reviewed by another member |

The full description of our AI-assisted workflow is in Appendix A of the project report.
