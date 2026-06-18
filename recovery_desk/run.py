"""
recovery_desk/run.py — headless CLI entry point.

    python -m recovery_desk.run fixtures/missed_call.json
    RUBRIC=rubrics/compliance.rubric.yaml python -m recovery_desk.run fixtures/missed_call.json

Runs the full autonomous loop and prints the score ledger to stdout. This is the
no-UI proof that the loop closes with zero human input after the goal goes in.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from recovery_desk.agent import run_recovery_desk


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m recovery_desk.run <missed_call.json>", file=sys.stderr)
        return 2

    goal = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    rubric = os.environ.get("RUBRIC", "rubrics/bookability.rubric.yaml")
    db = os.environ.get("RECOVERY_DESK_DB", "recovery_desk.db")

    print(f"== The Recovery Desk ==  rubric={rubric}  call={goal.get('call_id')}")
    print("-" * 60)

    def on_step(event: str, payload: dict) -> None:
        if event == "scored":
            mark = "PASS" if payload["passed"] else "FAIL"
            fail = ("  failing: " + ", ".join(payload["failing"])) if payload["failing"] else ""
            print(f"  rev {payload['revision']}: {payload['total']:>3}/100  [{mark}]{fail}")
        elif event == "shipped":
            print("-" * 60)
            print(f"  SHIPPED ({payload['state']}) at rev {payload['revision']} "
                  f"with {payload['total']}/100")

    outcome = run_recovery_desk(goal, rubric, db_path=db, on_step=on_step)
    print()
    print("Shipped message:")
    print("  " + outcome.shipped_message.replace("\n", "\n  "))
    if outcome.impact:
        print()
        print("Recovered revenue (the agent claims value only on a passing ship):")
        print("  " + outcome.impact["basis"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
