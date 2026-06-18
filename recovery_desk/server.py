"""
recovery_desk/server.py — bookability-mcp: the reusable MCP server. THE ASSET.

Exposes the self-grading recovery loop as MCP tools so ANY agent (Claude Desktop,
an SDK agent, another orchestrator) can call them. A stranger clones the repo,
registers this server, and points it at their own task by swapping the rubric.

Tools:
    score_draft        — grade a draft against a rubric, return the numeric ledger
    build_recovery     — turn a draft into a callback message + branded microsite
    send_recovery      — dispatch a shipped recovery (DRY-RUN by default)
    run_recovery_desk  — run the full autonomous plan->act->grade->revise->ship loop
    list_rubrics       — list available rubric files (proves rubric is swappable)

Run (stdio):  python -m recovery_desk.server
Register in an MCP client via the mcp.json snippet in the README.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from recovery_desk import builder
from recovery_desk.agent import run_recovery_desk
from recovery_desk.grader import grade, load_rubric

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - install guard
    FastMCP = None

REPO_ROOT = Path(__file__).resolve().parent.parent
RUBRIC_DIR = REPO_ROOT / "rubrics"
DEFAULT_RUBRIC = os.environ.get("RUBRIC", str(RUBRIC_DIR / "bookability.rubric.yaml"))


def _resolve_rubric(rubric: Optional[str]) -> str:
    if not rubric:
        return DEFAULT_RUBRIC
    p = Path(rubric)
    if p.exists():
        return str(p)
    # allow bare names like "compliance"
    cand = RUBRIC_DIR / f"{rubric}.rubric.yaml"
    return str(cand if cand.exists() else rubric)


# ---------------------------------------------------------------------------
# Tool implementations (pure functions — callable in tests without MCP installed)
# ---------------------------------------------------------------------------

def tool_list_rubrics() -> dict:
    """List available rubric files. Proves the standard is swappable."""
    return {
        "rubrics": [p.name for p in sorted(RUBRIC_DIR.glob("*.rubric.yaml"))],
        "default": Path(DEFAULT_RUBRIC).name,
    }


def tool_score_draft(message: str, site_html: str, business_profile: dict,
                     rubric: Optional[str] = None) -> dict:
    """Grade a draft against a rubric. Returns the numeric Bookability ledger."""
    r = load_rubric(_resolve_rubric(rubric))
    draft = {"message": message, "site_html": site_html, "business_profile": business_profile}
    g = grade(draft, r)
    return {
        "total": g.total,
        "passed": g.passed,
        "threshold": g.threshold,
        "rubric": r.name,
        "lines": [
            {"line_id": l.line_id, "weight": l.weight, "points": l.points,
             "passed": l.passed, "detail": l.detail}
            for l in g.lines
        ],
    }


def tool_build_recovery(draft: dict, goal: dict) -> dict:
    """Render a callback message + branded recovery microsite from a draft."""
    return builder.build_recovery(draft, goal)


def tool_send_recovery(message: str, goal: dict, dry_run: bool = True) -> dict:
    """Dispatch a shipped recovery to the caller.

    The real dispatch hands the message to our voice secretary layer (the part of
    our stack that owns the caller relationship) for delivery. In this hackathon
    build it is DRY-RUN by default: it validates the recovery is non-empty and
    addressed to a real caller number, and returns what WOULD be sent — so the
    autonomous loop is end-to-end without sending anything from a clean clone.
    Set dry_run=False only when wired to a live dispatch channel.
    """
    caller = goal.get("caller_number") or goal.get("caller_name") or ""
    ok = bool(message.strip()) and bool(caller)
    return {
        "dispatched": (not dry_run) and ok,
        "dry_run": dry_run,
        "valid": ok,
        "to": caller,
        "channel": "voice-secretary-layer",
        "preview": message.strip(),
        "note": ("would dispatch (dry-run)" if dry_run else
                 ("dispatched" if ok else "refused: empty message or no caller")),
    }


def tool_run_recovery_desk(goal: dict, rubric: Optional[str] = None,
                           db_path: str = "recovery_desk.db") -> dict:
    """Run the full autonomous loop. Returns the terminal outcome + shipped artifact."""
    outcome = run_recovery_desk(goal, _resolve_rubric(rubric), db_path=db_path)
    return {
        "run_id": outcome.run_id,
        "state": outcome.state,
        "final_score": outcome.final_score,
        "final_revision": outcome.final_revision,
        "shipped_message": outcome.shipped_message,
        "shipped_site_html": outcome.shipped_site_html,
        "impact": outcome.impact,
    }


# ---------------------------------------------------------------------------
# MCP wiring
# ---------------------------------------------------------------------------

def build_mcp() -> Any:
    if FastMCP is None:
        raise RuntimeError("mcp is required: pip install 'mcp[cli]'")
    mcp = FastMCP("bookability")

    @mcp.tool()
    def list_rubrics() -> dict:
        """List available self-evaluation rubric files."""
        return tool_list_rubrics()

    @mcp.tool()
    def score_draft(message: str, site_html: str, business_profile: dict,
                    rubric: str = "") -> dict:
        """Score a recovery draft against a numeric Bookability rubric."""
        return tool_score_draft(message, site_html, business_profile, rubric or None)

    @mcp.tool()
    def build_recovery(draft: dict, goal: dict) -> dict:
        """Render a callback message + branded recovery microsite from a draft."""
        return tool_build_recovery(draft, goal)

    @mcp.tool()
    def send_recovery(message: str, goal: dict, dry_run: bool = True) -> dict:
        """Dispatch a shipped recovery to the caller (dry-run by default)."""
        return tool_send_recovery(message, goal, dry_run)

    @mcp.tool()
    def run_recovery_desk(goal: dict, rubric: str = "",
                          db_path: str = "recovery_desk.db") -> dict:
        """Run the full autonomous plan->act->grade->revise->ship loop on one goal."""
        return tool_run_recovery_desk(goal, rubric or None, db_path)

    return mcp


def main() -> None:
    mcp = build_mcp()
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
