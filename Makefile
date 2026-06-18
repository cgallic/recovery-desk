.PHONY: install demo demo-replay video capture-live test run mcp clean

PY ?= python

install:
	$(PY) -m pip install -e .

# One-command demo: starts the split-screen ledger UI. Open the printed URL,
# click "Give it the goal", watch the agent self-fail at 66 and ship at 100.
# Runs offline (no API key) by default.
demo:
	$(PY) -m recovery_desk.webapp

# Bake a real run into a standalone, double-clickable demo replay (no server).
# Writes demo/replay-bookability.html and demo/replay-compliance.html with the
# real numbers from running the loop. This is the recorded demo as an artifact.
demo-replay:
	$(PY) -m recovery_desk.make_replay --out demo/replay-bookability.html
	RUBRIC=rubrics/compliance.rubric.yaml $(PY) -m recovery_desk.make_replay --out demo/replay-compliance.html

# Re-render the demo VIDEO (demo/recovery-desk-demo.mp4) + cover.png from real
# runs, deterministically — no manual screen recording. Needs `pip install -e
# .[video]` (playwright) + `playwright install chromium` + ffmpeg on PATH. The
# shipped mp4 is already in the repo; this just regenerates it.
video:
	$(PY) -m recovery_desk.record_demo

# Bake a transcript of the LIVE Claude code path into demo/live-claude-run.md.
# With ANTHROPIC_API_KEY set it records a real keyed run; with --stub (no key) it
# records the identical live branch through the shared stub, marked as such.
capture-live:
	$(PY) -m recovery_desk.capture_live_run --stub

# Headless proof the loop closes with zero human input.
run:
	$(PY) -m recovery_desk.run fixtures/missed_call.json

# Start the reusable MCP server (stdio).
mcp:
	$(PY) -m recovery_desk.server

# Full self-test: grader, blackboard, end-to-end loop, rubric swap,
# fail-hint-driven reviser, and second-domain self-fail-then-pass.
test:
	$(PY) -m recovery_desk.test_recovery_desk

clean:
	rm -f recovery_desk.db
