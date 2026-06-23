# Demo — run it yourself

The demo IS the real code executing. No canned data, no replay required.

## Exact commands (clean clone, zero installs, zero API key, zero network)

The core is stdlib-only. On a fresh `git clone` with **nothing installed**, run:

```bash
python -m recovery_desk selftest    # ~20 assertions, all PASS
python -m recovery_desk demo        # the autonomous loop, end to end, exits 0
```

That is the whole demo. `selftest` proves the substrate; `demo` runs the agent
on the seed missed call and you watch it **reject its own first draft**.

### What `demo` prints (the money moment)

```
[grader]    self-scored rev 0:  66/100  [FAIL]
[conductor] rejecting OWN draft — failing lines: concrete_slot, objection_handled
[reviser]   fixing only those lines, keeping what passed
[grader]    self-scored rev 1: 100/100  [PASS]
[conductor] SHIP (cleared threshold): 100/100 at rev 1
```

No human touched the loop after the goal went in. The agent graded itself against
a numeric rubric, failed its own draft, revised the exact failing lines, re-scored,
and shipped — then terminated.

## The captured real run

[`run-output.log`](run-output.log) is the verbatim stdout of:

```bash
python -m recovery_desk selftest
python -m recovery_desk demo
python -m recovery_desk demo --rubric compliance      # same agent, different domain
python -m recovery_desk demo --rubric donor_callback  # an EXTERNALLY-AUTHORED rubric (foreign domain, YAML-only)
python -m recovery_desk mcp-smoke                     # the reusable asset, offline
RECOVERY_DESK_CLAUDE_CLI=1 python -m recovery_desk demo --live-cli   # the REAL Claude model (no API key)
```

Every number in it came from running the code, not from authored copy. The final
block is the real Claude model self-failing (82/100 on `callback_sla`) and shipping
(85/100) — model-driven, no API key, via the Claude Code CLI.

## Real artifacts the demo writes

| Artifact | What it is |
|----------|-----------|
| `recovery_desk.db` | the SQLite blackboard — `runs / drafts / scores / score_lines / log / dispatch_outbox` for the run you just executed |
| `demo/shipped-microsite.html` | the branded recovery microsite the agent shipped after it passed |
| `demo/run-output.log` | the captured stdout above |

The `dispatch_outbox` row is the **booked-job writeback**: the recovery the agent
actually dispatched and the open slot it booked the job into. Inspect it:

```bash
sqlite3 recovery_desk.db "SELECT call_id, to_contact, booked, booked_slot FROM dispatch_outbox;"
```

## Watch it instead of read it (optional)

```bash
pip install -e ".[server]"
python -m recovery_desk serve         # open the printed URL, click "Give it the goal"
```

The split-screen ledger: the work product on the left, the live score ledger on
the right. The first draft lands red, one rubric line flips, the score climbs to
PASS, and it ships. This is the source for the 86-second video
(`recovery-desk-demo.mp4`), which is itself rendered from real runs via `make video`.

## Live Claude (real model)

Two ways to run the loop on the real Claude model — both run the IDENTICAL control
flow, with Claude writing the draft and the revision instead of the offline
deterministic reviser:

```bash
# A) no API key — via the Claude Code CLI:
RECOVERY_DESK_CLAUDE_CLI=1 python -m recovery_desk demo --live-cli

# B) with an Anthropic API key:
pip install -e ".[live]"
ANTHROPIC_API_KEY=sk-ant-... python -m recovery_desk demo
```

A captured real run is committed at [`live-claude-run.md`](live-claude-run.md)
(`reasoner: claude-cli`, `model: claude-opus-4-8`): Claude's grader fails its own
first draft on `callback_sla`, Claude reads that fail hint and rewrites, and it
ships — the offline repair function never runs on that path.
