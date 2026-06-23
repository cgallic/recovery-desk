"""
recovery_desk/__main__.py — the single judge-facing entry point.

    python -m recovery_desk selftest    # all assertions (loop, grader, rubric swap,
                                         #   fail-hint reviser, 4 domains (incl. external), live path,
                                         #   stdlib-only YAML fallback, MCP smoke)
    python -m recovery_desk demo        # run the full autonomous loop on the seed
                                         #   missed call; prints the live trace + ledger,
                                         #   writes a passing recovery, exits 0
    python -m recovery_desk demo --rubric compliance   # same loop, different domain
    python -m recovery_desk serve       # the split-screen ledger UI (needs fastapi)
    python -m recovery_desk mcp-smoke   # exercise every bookability-mcp tool offline

The core (selftest + demo) is STDLIB-ONLY: it runs on a clean clone with no
installs, no API key, no network. External leaves (Claude, MCP transport, the web
UI) are behind interfaces with offline stubs auto-selected when the real lib/key
is absent — the real plan->act->grade->revise->ship control flow executes for real.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _rubric_path(name: str) -> str:
    p = Path(name)
    if p.exists():
        return str(p)
    cand = REPO_ROOT / "rubrics" / f"{name}.rubric.yaml"
    if cand.exists():
        return str(cand)
    return str(REPO_ROOT / "rubrics" / "bookability.rubric.yaml")


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def cmd_selftest(_args: argparse.Namespace) -> int:
    from recovery_desk import blackboard as bb
    from recovery_desk import grader, miniyaml
    from recovery_desk import test_recovery_desk as t

    print("=" * 64)
    print("THE RECOVERY DESK — SELFTEST")
    print("  live Claude:", "ON" if _has_key() else "OFF (offline deterministic)")
    print("=" * 64)

    # 1) stdlib-only rubric reader agrees with PyYAML (when PyYAML is present).
    #    On a no-deps clone PyYAML is absent and the vendored reader is the only
    #    path; either way load_rubric must produce a usable rubric.
    _assert_stdlib_yaml_fallback()

    # 2) component + end-to-end assertions (the existing battery).
    bb._run_tests()
    grader._run_tests()
    t.test_full_loop_self_fails_then_ships()
    t.test_rubric_is_swappable()
    t.test_offline_reviser_reads_fail_hints_not_revision_number()
    t.test_second_domain_self_fails_then_passes_offline()
    t.test_third_domain_self_fails_then_passes_offline()
    t.test_external_rubric_self_fails_then_passes()
    t.test_live_claude_path_reads_fail_hints_and_revises()

    # 3) the reusable asset works offline (MCP tool smoke, no transport).
    from recovery_desk import server
    server.smoke()

    print("=" * 64)
    print("ALL SELFTEST ASSERTIONS PASSED")
    print("=" * 64)
    return 0


def _assert_stdlib_yaml_fallback() -> None:
    """Prove the vendored YAML reader matches PyYAML on every shipped rubric, so
    the core is genuinely stdlib-only without silently changing behavior."""
    from recovery_desk import miniyaml
    try:
        import yaml as _pyyaml
    except ImportError:
        _pyyaml = None

    def _norm(x):
        if isinstance(x, dict):
            return {k: _norm(v) for k, v in x.items()}
        if isinstance(x, list):
            return [_norm(v) for v in x]
        if isinstance(x, str):
            return x.strip()
        return x

    rubrics = sorted((REPO_ROOT / "rubrics").glob("*.rubric.yaml"))
    assert rubrics, "no rubric files found"
    for f in rubrics:
        text = f.read_text(encoding="utf-8")
        mini = _norm(miniyaml.safe_load(text))
        assert mini.get("name"), f"miniyaml failed to read {f.name}"
        assert mini.get("lines"), f"miniyaml read no lines from {f.name}"
        if _pyyaml is not None:
            ref = _norm(_pyyaml.safe_load(text))
            assert mini == ref, f"miniyaml disagrees with PyYAML on {f.name}"
    src = "PyYAML present (agreement asserted)" if _pyyaml else "PyYAML absent (stdlib-only)"
    print(f"[PASS] stdlib-only YAML reader matches PyYAML on {len(rubrics)} rubrics [{src}]")


# ---------------------------------------------------------------------------
# demo (headless full loop with live trace)
# ---------------------------------------------------------------------------

def cmd_demo(args: argparse.Namespace) -> int:
    import os
    from recovery_desk.agent import run_recovery_desk
    from recovery_desk import llm

    if getattr(args, "live_cli", False):
        # Opt into the REAL Claude model via the Claude Code CLI (no API key).
        os.environ["RECOVERY_DESK_CLAUDE_CLI"] = "1"

    rubric = _rubric_path(args.rubric)
    fixture = Path(args.fixture) if args.fixture else (REPO_ROOT / "fixtures" / "missed_call.json")
    goal = json.loads(fixture.read_text(encoding="utf-8"))
    db = REPO_ROOT / "recovery_desk.db"
    if db.exists():
        db.unlink()

    print("=" * 64)
    print("THE RECOVERY DESK — autonomous demo")
    print(f"  goal     : recover missed call {goal.get('call_id')} "
          f"({goal.get('caller_name')})")
    print(f"  rubric   : {Path(rubric).name}")
    print(f"  reasoner : {'claude (live)' if llm.is_live() else 'offline-deterministic'}")
    print(f"  stop when: score >= threshold  OR  revision cap reached")
    print("=" * 64)
    print("  The human sets the goal ONCE. Everything below is the agent:")
    print("  plan -> act -> grade ITSELF -> reject its own draft -> revise -> re-grade -> ship.")
    print("-" * 64)

    def on_step(event: str, payload: dict) -> None:
        if event == "run_started":
            print(f"  [conductor] goal received (reasoner={payload['reasoner']})")
        elif event == "built":
            tag = "first draft" if payload["revision"] == 0 else f"revision {payload['revision']}"
            print(f"  [builder]   {tag} built")
        elif event == "scored":
            mark = "PASS" if payload["passed"] else "FAIL"
            print(f"  [grader]    self-scored rev {payload['revision']}: "
                  f"{payload['total']:>3}/100  [{mark}]")
            # Only narrate a rejection when the agent actually rejects this draft
            # (below threshold). A passing draft with a few non-blocking soft lines
            # is shipped, not rejected — keep the trace honest.
            if not payload["passed"] and payload["failing"]:
                print(f"  [conductor] rejecting OWN draft — failing lines: "
                      f"{', '.join(payload['failing'])}")
                print(f"  [reviser]   fixing only those lines, keeping what passed")
        elif event == "shipped":
            print("-" * 64)
            verb = "SHIP (cleared threshold)" if payload["state"] == "shipped_pass" \
                else "SHIP best (revision cap)"
            print(f"  [conductor] {verb}: {payload['total']}/100 at rev {payload['revision']}")

    outcome = run_recovery_desk(goal, rubric, db_path=str(db), on_step=on_step)

    print()
    print("Shipped recovery message:")
    print("  " + outcome.shipped_message.replace("\n", "\n  "))

    site_path = REPO_ROOT / "demo" / "shipped-microsite.html"
    site_path.parent.mkdir(exist_ok=True)
    site_path.write_text(outcome.shipped_site_html, encoding="utf-8")
    print()
    print(f"Shipped recovery microsite -> {site_path.relative_to(REPO_ROOT)}")

    if outcome.impact:
        print()
        print("Recovered revenue (claimed only on a passing ship, from the "
              "business's own numbers):")
        print("  " + outcome.impact["basis"])

    # End-to-end: dispatch the passing recovery and BOOK the job — a real local
    # writeback to dispatch_outbox, proving the loop closes the last mile (not just
    # a high-scoring draft). Live for the demo DB; no external send leaves the box.
    if outcome.state == "shipped_pass":
        from recovery_desk import server, blackboard as bb
        booked = server.tool_send_recovery(
            outcome.shipped_message, goal, dry_run=False,
            db_path=str(db), run_id=outcome.run_id,
            booked_slot=(goal.get("business_profile", {}).get("next_open_slots") or [None])[0],
        )
        conn = bb.connect(str(db))
        outbox = bb.get_dispatches(conn, outcome.run_id)
        conn.close()
        print()
        print("Booked job (end-to-end writeback, not a dry-run):")
        print(f"  dispatched to {booked['to']} via {booked['channel']}; "
              f"booked slot '{booked['booked_slot']}' "
              f"-> dispatch_outbox row #{outbox[0]['id']} (booked={bool(outbox[0]['booked'])})")

    print()
    print(f"Run recorded in the blackboard -> {db.relative_to(REPO_ROOT)} "
          f"(runs/drafts/scores/score_lines/log/dispatch_outbox)")
    print("Demo complete — the loop closed with zero human touches after the goal.")
    return 0


# ---------------------------------------------------------------------------
# serve / mcp-smoke (thin wrappers over existing modules)
# ---------------------------------------------------------------------------

def cmd_serve(_args: argparse.Namespace) -> int:
    try:
        from recovery_desk import webapp
    except ImportError as e:  # pragma: no cover
        print(f"serve needs the web extras (pip install -e .): {e}", file=sys.stderr)
        return 1
    webapp.main()
    return 0


def cmd_mcp_smoke(_args: argparse.Namespace) -> int:
    from recovery_desk import server
    return server.smoke()


def _has_key() -> bool:
    import os
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="recovery_desk",
        description="An autonomous agent that recovers a missed call and grades "
                    "its own work against a numeric rubric until it passes.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("selftest", help="run every assertion (stdlib-only)")
    s.set_defaults(func=cmd_selftest)

    d = sub.add_parser("demo", help="run the full autonomous loop on the seed call")
    d.add_argument("--rubric", default="bookability",
                   help="rubric name or path "
                        "(bookability|compliance|discoverability|donor_callback)")
    d.add_argument("--fixture", default="", help="path to a missed_call.json (optional)")
    d.add_argument("--live-cli", action="store_true",
                   help="run the REAL Claude reasoner via the Claude Code CLI "
                        "(no API key); default is the offline deterministic loop")
    d.set_defaults(func=cmd_demo)

    sv = sub.add_parser("serve", help="start the split-screen ledger UI (needs fastapi)")
    sv.set_defaults(func=cmd_serve)

    m = sub.add_parser("mcp-smoke", help="exercise every bookability-mcp tool offline")
    m.set_defaults(func=cmd_mcp_smoke)

    return p


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
