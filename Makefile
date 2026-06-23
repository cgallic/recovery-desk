.PHONY: selftest demo demo-compliance demo-donor demo-live serve install demo-replay video capture-live capture-live-cli test run mcp mcp-smoke clean

PY ?= python

# ---- The two judge commands (STDLIB-ONLY: no installs, no key, no network) ----

# All assertions: the loop self-fails then ships, the grader is falsifiable, the
# rubric is swappable across four domains (incl. an externally-authored one), the
# offline reviser is fail-hint driven, the live-Claude path is wired, the YAML
# reader is stdlib-only, and the MCP asset smokes clean. Runs on a clean clone.
selftest:
	$(PY) -m recovery_desk selftest

# Run the full autonomous loop on the seed missed call. Prints the live trace
# (self-fail at 66 -> revise -> ship at 100), ships + books the job (writeback to
# dispatch_outbox), and records the run in the blackboard. Exits 0. Runs offline
# by default; set ANTHROPIC_API_KEY or use `make demo-live` for the real model.
demo:
	$(PY) -m recovery_desk demo

# Same loop, different domain — proof the rubric is separable (no code change).
demo-compliance:
	$(PY) -m recovery_desk demo --rubric compliance

# The externally-authored rubric (foreign non-profit domain, YAML-only).
demo-donor:
	$(PY) -m recovery_desk demo --rubric donor_callback

# Run the loop with the REAL Claude model via the Claude Code CLI (no API key).
demo-live:
	RECOVERY_DESK_CLAUDE_CLI=1 $(PY) -m recovery_desk demo --live-cli

# ---- Optional surfaces ----

# The split-screen ledger UI (needs the server extra). Open the printed URL.
serve:
	$(PY) -m recovery_desk serve

install:
	$(PY) -m pip install -e ".[all]"

# Bake a real run into a standalone, double-clickable replay (no server).
demo-replay:
	$(PY) -m recovery_desk.make_replay --out demo/replay-bookability.html
	RUBRIC=rubrics/compliance.rubric.yaml $(PY) -m recovery_desk.make_replay --out demo/replay-compliance.html

# Re-render the demo VIDEO + cover.png from real runs (needs .[video] + ffmpeg).
video:
	$(PY) -m recovery_desk.record_demo

# Bake a transcript of the live-Claude code path into demo/live-claude-run.md.
# `capture-live` is the no-key stub of the identical live branch; `capture-live-cli`
# records a REAL Claude run via the Claude Code CLI (no API key needed).
capture-live:
	$(PY) -m recovery_desk.capture_live_run --stub

capture-live-cli:
	RECOVERY_DESK_CLAUDE_CLI=1 $(PY) -m recovery_desk.capture_live_run --live-cli

# Exercise every bookability-mcp tool offline (no transport, no key).
mcp-smoke:
	$(PY) -m recovery_desk mcp-smoke

# Start the reusable MCP server (stdio).
mcp:
	$(PY) -m recovery_desk.server

# Aliases kept for muscle memory.
test: selftest
run:
	$(PY) -m recovery_desk.run fixtures/missed_call.json

clean:
	rm -f recovery_desk.db demo/shipped-microsite.html
