"""Placeholder grade for credit runs until the grader lands (PR 3).

Passes when the run ended normally (a final action after the final-decision request) and that
action is one of the version's acceptable actions. It does not look at the record, the
calculations or anything along the way; PR 3 replaces it with labels from the whole trace.
"""

from __future__ import annotations


def check(trace: dict) -> dict:
    final = (trace.get("final_db") or {}).get("final_action") or {}
    action, reason = final.get("action"), trace.get("stop_reason")
    if reason == "evidence_requested":   # asked for proof in reply to the final-decision request
        action = "request_evidence"
    acceptable = trace.get("expected_outcome") or []
    return {"passed": reason in ("final_action", "evidence_requested") and action in acceptable,
            "checker": "credit_stub", "stop_reason": reason, "final": action}
