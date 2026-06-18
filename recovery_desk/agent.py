"""
recovery_desk/agent.py — the autonomous loop.

Given ONE goal (a missed_call record), the agent runs:

    PLAN -> ACT (build_recovery) -> SELF-EVAL (grade) -> decide:
        score >= threshold      -> SHIP (pass)        terminate
        revisions == max        -> SHIP best (cap)    terminate
        otherwise               -> REVISE (fix only the failing lines) -> SELF-EVAL

No human approves a step. The rejection of draft #0 is the agent's own decision,
driven by the numeric threshold — not a thrown exception, not a human clicking
retry. The agent owns its stopping condition, so it terminates itself.

Every step is written to the blackboard so the ledger UI can show the loop close.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from recovery_desk import blackboard as bb
from recovery_desk import builder, llm
from recovery_desk.grader import GradeResult, Rubric, grade, load_rubric
from recovery_desk.impact import compute_impact


@dataclass
class RunOutcome:
    run_id: int
    state: str            # "shipped_pass" | "shipped_cap"
    final_score: int
    final_revision: int
    shipped_message: str
    shipped_site_html: str
    impact: Optional[dict] = None   # recovered-revenue dict, or None if not shipped


def _to_score_lines(g: GradeResult) -> List[bb.ScoreLine]:
    return [
        bb.ScoreLine(l.line_id, l.weight, l.points, l.passed, l.detail)
        for l in g.lines
    ]


def run_recovery_desk(
    goal: dict,
    rubric_path: str | Path,
    db_path: str = "recovery_desk.db",
    on_step: Optional[Callable[[str, dict], None]] = None,
    step_delay: float = 0.0,
) -> RunOutcome:
    """Run the full autonomous recovery loop for one missed call.

    Args:
        goal: a missed_call record (caller, reason, business_profile).
        rubric_path: which rubric the agent grades itself against.
        db_path: blackboard path.
        on_step: optional callback(event, payload) for live UIs.
        step_delay: artificial pause between steps (demo pacing).

    Returns:
        RunOutcome with the terminal state and the shipped deliverable.
    """
    rubric: Rubric = load_rubric(rubric_path)
    # Tell the drafter/reviser which rubric is in play so the offline reviser
    # repairs THIS rubric's failing lines (correct cta_markers, etc.).
    llm.set_active_rubric(rubric)
    reasoner = "claude" if llm.is_live() else "offline-deterministic"
    conn = bb.init_db(db_path)

    def emit(event: str, payload: dict) -> None:
        if on_step:
            on_step(event, payload)
        if step_delay:
            time.sleep(step_delay)

    run_id = bb.create_run(
        conn, goal.get("call_id", "unknown"), goal,
        rubric.name, rubric.threshold, rubric.max_revisions,
    )
    bb.log(conn, run_id, "Conductor", "goal received",
           {"call_id": goal.get("call_id"), "rubric": rubric.name,
            "threshold": rubric.threshold, "reasoner": reasoner})
    emit("run_started", {"run_id": run_id, "reasoner": reasoner})

    best: Optional[tuple[int, int, dict]] = None  # (score, revision, built)
    prior_message: Optional[str] = None
    fail_hints: Optional[List[str]] = None

    for revision in range(rubric.max_revisions + 1):
        # ---- PLAN + ACT -------------------------------------------------
        bb.set_run_state(conn, run_id, "planning" if revision == 0 else "revising")
        bb.log(conn, run_id, "Planner",
               "drafting recovery" if revision == 0 else "revising failing lines",
               {"revision": revision, "fix": fail_hints})
        draft = llm.draft_recovery(goal, revision, prior_message, fail_hints)

        bb.set_run_state(conn, run_id, "building")
        built = builder.build_recovery(draft, goal)
        draft_id = bb.add_draft(conn, run_id, revision, built["message"], built["site_html"])
        bb.log(conn, run_id, "Builder", "recovery built",
               {"revision": revision, "message_chars": len(built["message"])})
        emit("built", {"revision": revision, "message": built["message"]})

        # ---- SELF-EVAL --------------------------------------------------
        bb.set_run_state(conn, run_id, "grading")
        g = grade(built, rubric, llm_judge=llm.llm_judge_line)
        bb.add_score(conn, run_id, draft_id, revision, g.total, g.passed, _to_score_lines(g))
        failing = g.failing_lines()
        bb.log(conn, run_id, "Grader", f"self-scored {g.total}/100",
               {"revision": revision, "passed": g.passed,
                "failing": [l.line_id for l in failing]})
        emit("scored", {"revision": revision, "total": g.total, "passed": g.passed,
                        "failing": [l.line_id for l in failing]})

        if best is None or g.total > best[0]:
            best = (g.total, revision, built)

        # ---- DECIDE (the agent's own stopping condition) ----------------
        if g.passed:
            bb.finalize_run(conn, run_id, "shipped_pass", g.total, revision)
            impact = compute_impact(goal, shipped=True)
            impact_d = impact.as_dict() if impact else None
            bb.log(conn, run_id, "Conductor",
                   f"score {g.total} >= threshold {rubric.threshold} — shipping",
                   {"decision": "ship_pass", "impact": impact_d})
            emit("shipped", {"state": "shipped_pass", "total": g.total,
                             "revision": revision, "impact": impact_d})
            conn.close()
            return RunOutcome(run_id, "shipped_pass", g.total, revision,
                              built["message"], built["site_html"], impact_d)

        if revision == rubric.max_revisions:
            score, rev, b = best
            bb.finalize_run(conn, run_id, "shipped_cap", score, rev)
            bb.log(conn, run_id, "Conductor",
                   f"revision cap reached — shipping best ({score}/100)",
                   {"decision": "ship_cap"})
            emit("shipped", {"state": "shipped_cap", "total": score, "revision": rev})
            conn.close()
            return RunOutcome(run_id, "shipped_cap", score, rev, b["message"], b["site_html"])

        # ---- prepare the revision (fix ONLY the failing lines) ----------
        prior_message = built["message"]
        fail_hints = [
            f"{l.line_id}: {next((rl.fail_hint for rl in rubric.lines if rl.id == l.line_id), '')}"
            for l in failing
        ]
        bb.log(conn, run_id, "Conductor",
               f"score {g.total} < threshold {rubric.threshold} — rejecting own draft, revising",
               {"decision": "revise", "fix": [l.line_id for l in failing]})

    # unreachable, but keeps type checkers happy
    raise RuntimeError("loop exited without terminating")  # pragma: no cover
