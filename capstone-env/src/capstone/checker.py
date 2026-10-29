"""Placeholder checker until Person 2's lands.

The runner calls ``check(trace) -> dict`` on every completed run. The dict
must have ``passed`` (True, False or None); every other scalar key becomes a
``check_<key>`` column in runs.csv. Point the runner at the real checker with
``--checker module:function``.

This stub only compares the trace's final action with the case's
expected_outcome. It does not check rules, amounts or corrections, so its
numbers are placeholders, not findings.
"""

from __future__ import annotations


def check(trace: dict) -> dict:
    expected = trace.get("expected_outcome")
    final = trace.get("summary", {}).get("final_action")
    return {"passed": None if expected is None else final == expected, "checker": "stub"}
