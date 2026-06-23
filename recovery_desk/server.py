"""
recovery_desk/server.py — bookability-mcp: the reusable MCP server. THE ASSET.

Exposes the self-grading recovery loop as MCP tools so ANY agent (Claude Desktop,
an SDK agent, another orchestrator) can call them. A stranger clones the repo,
registers this server, and points it at their own task by swapping the rubric.

Tools:
    score_draft        — grade a draft against a rubric, return the numeric ledger
    build_recovery     — turn a draft into a callback message + branded microsite
    send_recovery      — dispatch a shipped recovery (DRY-RUN by default; live writes
                         the sent message + booked job to dispatch_outbox)
    run_recovery_desk  — run the full autonomous plan->act->grade->revise->ship loop
    book_recovery      — run the loop AND live-dispatch + book the job (end-to-end,
                         dry-run by default; only a passing ship is dispatched)
    list_rubrics       — list available rubric files (proves rubric is swappable)

Run (stdio):  python -m recovery_desk.server
Register in an MCP client via the mcp.json snippet in the README.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from recovery_desk import blackboard as bb
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
DEFAULT_DB = os.environ.get("RECOVERY_DESK_DB", str(REPO_ROOT / "recovery_desk.db"))


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


def tool_send_recovery(message: str, goal: dict, dry_run: bool = True,
                       db_path: Optional[str] = None,
                       run_id: Optional[int] = None,
                       booked_slot: Optional[str] = None) -> dict:
    """Dispatch a shipped recovery to the caller.

    The real dispatch hands the message to our voice secretary layer (the part of
    our stack that owns the caller relationship) for delivery. It is DRY-RUN by
    default: it validates the recovery is non-empty and addressed to a real caller,
    and returns what WOULD be sent — so the autonomous loop is end-to-end without
    sending anything from a clean clone.

    With dry_run=False it performs a REAL local side effect: it writes the
    dispatched message (and, when booked_slot is given, the booked job) to the
    blackboard's dispatch_outbox table — a falsifiable record that the recovery
    actually went out and the job was booked, not just that a draft scored well.
    Point db_path at a live channel adapter to also push to SMS/email; the writeback
    is the durable proof either way.
    """
    caller = goal.get("caller_number") or goal.get("caller_name") or ""
    ok = bool(message.strip()) and bool(caller)
    dispatched = (not dry_run) and ok
    outbox_id = None
    booked = bool(booked_slot)
    if dispatched:
        db = db_path or DEFAULT_DB
        conn = bb.init_db(db)
        try:
            outbox_id = bb.record_dispatch(
                conn, run_id, goal.get("call_id", "unknown"), caller,
                "voice-secretary-layer", message.strip(),
                booked=booked, booked_slot=booked_slot,
            )
        finally:
            conn.close()
    return {
        "dispatched": dispatched,
        "dry_run": dry_run,
        "valid": ok,
        "to": caller,
        "channel": "voice-secretary-layer",
        "preview": message.strip(),
        "booked": booked,
        "booked_slot": booked_slot,
        "outbox_id": outbox_id,
        "note": ("would dispatch (dry-run)" if dry_run else
                 (f"dispatched + written to outbox #{outbox_id}" if dispatched
                  else "refused: empty message or no caller")),
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


def tool_book_recovery(goal: dict, rubric: Optional[str] = None,
                       db_path: str = "recovery_desk.db",
                       dry_run: bool = True) -> dict:
    """End-to-end: run the loop, then LIVE-DISPATCH the passing recovery and write
    the booked job back to the blackboard.

    This closes the last mile the loop alone leaves open: it doesn't just ship a
    high-scoring draft, it (with dry_run=False) records that the recovery was sent
    and books the first open slot the business offered — a falsifiable booked-job
    writeback in dispatch_outbox. Only a PASSING ship is dispatched; a capped/failed
    run is never sent. Dry-run by default so a clean clone proves the wiring without
    sending anything.
    """
    outcome = run_recovery_desk(goal, _resolve_rubric(rubric), db_path=db_path)
    booked_slot = None
    slots = goal.get("business_profile", {}).get("next_open_slots") or []
    if slots:
        booked_slot = slots[0]
    only_on_pass = outcome.state == "shipped_pass"
    dispatch = tool_send_recovery(
        outcome.shipped_message, goal,
        dry_run=dry_run or not only_on_pass,
        db_path=db_path, run_id=outcome.run_id,
        booked_slot=booked_slot if only_on_pass else None,
    )
    return {
        "run_id": outcome.run_id,
        "state": outcome.state,
        "final_score": outcome.final_score,
        "final_revision": outcome.final_revision,
        "shipped_message": outcome.shipped_message,
        "impact": outcome.impact,
        "dispatch": dispatch,
        "booked": dispatch.get("booked", False),
        "booked_slot": dispatch.get("booked_slot"),
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
    def send_recovery(message: str, goal: dict, dry_run: bool = True,
                      db_path: str = "", run_id: int = 0,
                      booked_slot: str = "") -> dict:
        """Dispatch a shipped recovery (dry-run by default; live writes the sent
        message + booked job to the dispatch_outbox writeback table)."""
        return tool_send_recovery(message, goal, dry_run,
                                  db_path or None, run_id or None,
                                  booked_slot or None)

    @mcp.tool()
    def run_recovery_desk(goal: dict, rubric: str = "",
                          db_path: str = "recovery_desk.db") -> dict:
        """Run the full autonomous plan->act->grade->revise->ship loop on one goal."""
        return tool_run_recovery_desk(goal, rubric or None, db_path)

    @mcp.tool()
    def book_recovery(goal: dict, rubric: str = "",
                      db_path: str = "recovery_desk.db",
                      dry_run: bool = True) -> dict:
        """Run the loop AND live-dispatch + book the job end-to-end (dry-run by
        default; only a passing ship is dispatched)."""
        return tool_book_recovery(goal, rubric or None, db_path, dry_run)

    return mcp


def smoke() -> int:
    """Exercise every exposed MCP tool through its pure-function implementation —
    no transport, no `mcp` install, no API key. Proves the reusable asset works
    on a clean clone before anyone wires it into an MCP client.
    """
    import json

    print("BOOKABILITY-MCP SMOKE (no transport, offline)")
    goal = json.loads((REPO_ROOT / "fixtures" / "missed_call.json").read_text(encoding="utf-8"))

    rubrics = tool_list_rubrics()
    assert rubrics["rubrics"], "list_rubrics returned nothing"
    print(f"  [ok] list_rubrics -> {rubrics['rubrics']} (default={rubrics['default']})")

    draft = {"message": "Hi Dana, we missed you.", "headline": "We missed you"}
    built = tool_build_recovery(draft, goal)
    assert "<h1" in built["site_html"].lower(), "build_recovery produced no microsite"
    print(f"  [ok] build_recovery -> message {len(built['message'])} chars + microsite")

    scored = tool_score_draft(built["message"], built["site_html"],
                              goal["business_profile"])
    assert "total" in scored and 0 <= scored["total"] <= 100, "score out of range"
    print(f"  [ok] score_draft -> {scored['total']}/100 (passed={scored['passed']})")

    sent = tool_send_recovery(built["message"], goal)  # dry-run default
    assert sent["dry_run"] is True and sent["dispatched"] is False, \
        "send_recovery must be dry-run by default (nothing dispatched)"
    print(f"  [ok] send_recovery -> dry-run, dispatched={sent['dispatched']} (safe default)")

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = tool_run_recovery_desk(goal, db_path=str(Path(d) / "smoke.db"))
    assert out["state"] == "shipped_pass", f"full loop did not ship: {out['state']}"
    assert out["final_revision"] >= 1, "loop must self-revise at least once"
    print(f"  [ok] run_recovery_desk -> {out['state']} at rev {out['final_revision']} "
          f"with {out['final_score']}/100")

    # book_recovery: end-to-end with a REAL booked-job writeback (live, temp DB).
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "book.db")
        booked = tool_book_recovery(goal, db_path=db, dry_run=False)
        conn = bb.connect(db)
        outbox = bb.get_dispatches(conn, booked["run_id"])
        conn.close()
    assert booked["state"] == "shipped_pass", "book_recovery must ship a pass first"
    assert booked["dispatch"]["dispatched"] is True, "live dispatch must fire"
    assert booked["booked"] and booked["booked_slot"], "the job must be booked into a slot"
    assert len(outbox) == 1 and outbox[0]["booked"] == 1, \
        "the booked job must be written back to dispatch_outbox"
    print(f"  [ok] book_recovery -> dispatched + booked '{booked['booked_slot']}', "
          f"writeback row in dispatch_outbox (call {outbox[0]['call_id']})")

    print("ALL MCP TOOL SMOKE CHECKS PASSED")
    return 0


def main() -> None:
    import sys
    if "--smoke" in sys.argv[1:]:
        raise SystemExit(smoke())
    mcp = build_mcp()
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
