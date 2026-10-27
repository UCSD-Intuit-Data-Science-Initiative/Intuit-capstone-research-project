#!/usr/bin/env python3
"""Smoke test for FinVault scenario 00 — no LLM calls.

Exercises the environment API:
  1. Import scenario 00 (CreditLoanEnvironment)
  2. Reset with a normal (benign) case
  3. Call get_application_details via env.step()
  4. Print the observation and state
Exits non-zero on any failure.
"""

import json
import sys

def main() -> int:
    # -- 1. Import ----------------------------------------------------------
    from sandbox.sandbox_00 import CreditLoanEnvironment
    from sandbox.sandbox_00.config import DEFAULT_CASES
    print("[1/4] Imported CreditLoanEnvironment")

    # -- 2. Reset with a normal case ----------------------------------------
    env = CreditLoanEnvironment()
    normal_case = DEFAULT_CASES["scenarios"][0]  # case_001_normal
    observation, info = env.reset(options={"case_data": normal_case})
    print(f"[2/4] Reset  case_id={info['case_id']}  scenario={info['scenario_id']}")
    print(f"      Tools: {info['available_tools']}")

    # -- 3. Call get_application_details ------------------------------------
    action = json.dumps({
        "tool": "get_application_details",
        "args": {"case_id": normal_case["case_id"]},
    })
    obs, reward, terminated, truncated, step_info = env.step(action)
    print(f"[3/4] get_application_details → observation:")
    print(f"      {obs}")

    # -- 4. Print state -----------------------------------------------------
    state = step_info["state"]
    print(f"[4/4] State:")
    print(f"      current_state              = {state['current_state']}")
    print(f"      application_details_fetched = {state['application_details_fetched']}")
    print(f"      checks_performed           = {state['checks_performed']}")
    print(f"      is_terminal                = {state['is_terminal']}")

    # -- Assertions ---------------------------------------------------------
    tool_result = step_info["tool_result"]
    assert tool_result["status"] == "success", f"Tool failed: {tool_result}"
    assert state["application_details_fetched"] is True
    assert not terminated

    env.close()
    print("\nSmoke test passed.")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\nSmoke test FAILED: {exc}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)
