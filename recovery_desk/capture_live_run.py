"""
recovery_desk/capture_live_run.py — run the loop with the LIVE Claude reasoner and
bake the result into a verifiable transcript artifact.

This is the proof that the self-correction is Claude reasoning, not the offline
if/else reviser. With an ANTHROPIC_API_KEY set, it:

  1. installs a recorder on the LLM layer that captures every live Claude call
     (drafter, reviser, judge) — the exact system prompt, the exact user message
     (which CONTAINS the failing rubric line ids the grader reported), and Claude's
     raw response;
  2. runs the full autonomous loop end to end against a fixture;
  3. writes two artifacts to demo/:
       - live-claude-run.json : machine-readable transcript + ledger snapshot
       - live-claude-run.md   : a human-readable transcript a judge can skim

The revision turn is the load-bearing one: its recorded `user` block shows the
fail hints going IN, and its `response_text` shows the reasoner's rewritten draft
coming OUT — so anyone can confirm the model read the failure and fixed exactly
those lines. No hand-written repair function is in this path.

Two ways to run it, both committing the SAME artifact shape:

    # 1. Real keyed run — Claude's own prose in the response blocks.
    ANTHROPIC_API_KEY=sk-ant-... python -m recovery_desk.capture_live_run
    ANTHROPIC_API_KEY=sk-ant-... RUBRIC=rubrics/compliance.rubric.yaml \
        python -m recovery_desk.capture_live_run --out demo/live-claude-compliance.json

    # 2. Stub run (no key) — runs the IDENTICAL live branch in llm.py through the
    #    shared LiveReasonerStub. The reasoner field is marked "claude-live-stub"
    #    so the artifact is never mistaken for a real API capture; what it proves
    #    is the live wiring (reviser receives the failing line id, ships off the
    #    client's output, offline repair never runs). This is what ships in-repo.
    python -m recovery_desk.capture_live_run --stub

Without a key AND without --stub it exits non-zero and prints how to run it — it
never fabricates a "live" transcript from the offline deterministic path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from recovery_desk import blackboard as bb
from recovery_desk import llm
from recovery_desk.agent import run_recovery_desk

REPO = Path(__file__).resolve().parent.parent


def _md(transcript: list[dict], snap: dict, model: str) -> str:
    stub = model.endswith("-stub")
    title = ("# Live-path run — captured transcript (stub reasoner)"
             if stub else "# Live Claude run — captured transcript")
    lines = [
        title,
        "",
        ("> Reasoner: **stub stand-in** running the IDENTICAL live branch in "
         "`llm.py` with no API key. The wiring is real (the reviser turn below "
         "receives the failing rubric line id and the loop ships off the client's "
         "output — the offline deterministic repair never runs); the response prose "
         "is canned, not Claude's. A real keyed run produces this exact artifact "
         "with Claude's own text in the response blocks: "
         "`ANTHROPIC_API_KEY=sk-ant-... python -m recovery_desk.capture_live_run`."
         "\n" if stub else ""),
        f"- Captured at: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Reasoner: `{model}`",
        f"- Rubric: `{snap.get('rubric')}`  ·  threshold `{snap.get('threshold')}`",
        f"- Outcome: **{snap['outcome']['state']}** at rev "
        f"{snap['outcome']['final_revision']} with "
        f"{snap['outcome']['final_score']}/100",
        "",
        ((
            "Every block below is one call through the live reasoner branch of "
            "this run (stub stand-in; a real key swaps in Claude's prose). "
        ) if stub else (
            "Every block below is a real Claude API call from this run. "
        )) +
        "The reviser turn proves the reasoner read the failing rubric line ids "
        "(in its `user` block) and rewrote the draft itself — no deterministic "
        "repair function runs on this path.",
        "",
    ]
    for i, t in enumerate(transcript, 1):
        head = t["role"].upper()
        if "revision" in t:
            head += f" (revision {t['revision']})"
        if "line_id" in t:
            head += f" — line `{t['line_id']}`"
        resp_label = ("**Reasoner response (stub stand-in)**" if stub
                      else "**Claude's response**")
        sent_label = ("**User message (sent to the reasoner)**" if stub
                      else "**User message (sent to Claude)**")
        lines += [f"## {i}. {head}", "", "**System prompt**", "", "```",
                  t["system"].strip(), "```", "", sent_label,
                  "", "```", t["user"].strip(), "```", "",
                  resp_label, "", "```", t["response_text"].strip(),
                  "```", ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rubric", default=os.environ.get(
        "RUBRIC", str(REPO / "rubrics" / "bookability.rubric.yaml")))
    ap.add_argument("--fixture", default=os.environ.get(
        "FIXTURE", str(REPO / "fixtures" / "missed_call.json")))
    ap.add_argument("--out", default=str(REPO / "demo" / "live-claude-run.json"))
    ap.add_argument(
        "--stub", action="store_true",
        help="Run the identical live branch through the shared LiveReasonerStub "
             "(no API key). Records an honest stub transcript marked "
             "'claude-live-stub' — never a fabricated real-API capture.")
    ap.add_argument(
        "--live-cli", action="store_true",
        help="Use the REAL Claude model via the Claude Code CLI (`claude -p`), no "
             "API key required. Records Claude's own prose. Equivalent to setting "
             "RECOVERY_DESK_CLAUDE_CLI=1.")
    args = ap.parse_args(argv[1:])

    if args.live_cli:
        os.environ["RECOVERY_DESK_CLAUDE_CLI"] = "1"

    reasoner_label = llm.MODEL
    if args.stub:
        # Inject the same stub the test suite uses. is_live() now returns True and
        # the loop runs the exact live code path; we tag the artifact so it is
        # never confused with a real keyed capture.
        llm.set_client_override(llm.LiveReasonerStub())
        reasoner_label = f"{llm.MODEL}-stub"
    elif llm.live_reasoner_label() == "claude-cli":
        reasoner_label = f"{llm.MODEL} (Claude Code CLI)"

    if not llm.is_live():
        print(
            "No live reasoner available. This script captures a REAL keyed run, or "
            "an honest stub run with --stub — it never fabricates a 'live' "
            "transcript from the offline deterministic path.\n\n"
            "  # real Claude:\n  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  pip install anthropic\n"
            f"  python -m recovery_desk.capture_live_run --out {args.out}\n\n"
            "  # in-repo stub (no key, identical live branch):\n"
            f"  python -m recovery_desk.capture_live_run --stub --out {args.out}\n",
            file=sys.stderr,
        )
        return 3

    transcript: list[dict] = []
    llm.set_live_recorder(transcript.append)

    goal = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "live.db")
        outcome = run_recovery_desk(goal, args.rubric, db_path=db)
        conn = bb.connect(db)
        snap = bb.ledger_snapshot(conn, outcome.run_id)
        conn.close()

    llm.set_live_recorder(None)
    llm.set_client_override(None)

    snap["outcome"] = {
        "state": outcome.state,
        "final_score": outcome.final_score,
        "final_revision": outcome.final_revision,
        "impact": outcome.impact,
    }
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "model": reasoner_label,
        "reasoner": ("claude-live-stub" if args.stub
                     else llm.live_reasoner_label()),
        "stub": bool(args.stub),
        "rubric": snap.get("rubric"),
        "transcript": transcript,
        "ledger": snap,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_out = out.with_suffix(".md")
    md_out.write_text(_md(transcript, snap, reasoner_label), encoding="utf-8")

    kind = "stub live-path" if args.stub else "LIVE Claude"
    revisions = [t for t in transcript if t["role"] == "reviser"]
    print(f"Captured {kind} run ({reasoner_label}): "
          f"{outcome.state} at rev {outcome.final_revision} "
          f"with {outcome.final_score}/100")
    print(f"  {len(transcript)} reasoner calls recorded "
          f"({len(revisions)} revision turn(s) - the proof the reviser reads the fail signal)")
    print(f"  wrote {out}")
    print(f"  wrote {md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
