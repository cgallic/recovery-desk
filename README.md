# The Recovery Desk

**An autonomous agent that turns a missed call into a booked job — and grades its own work until it passes.**

Built for the lablab.ai Autonomous Agents Hack (Anthropic track). Given one missed-call record, the agent drafts a callback message and a branded recovery microsite, scores its own draft against a numeric **Bookability** rubric, **rejects its own first draft**, revises, re-scores, and ships only when it clears the threshold — terminating itself. No human touches the loop after the goal goes in.

The reusable asset is `bookability-mcp`: an MCP server that exposes the self-grading loop as tools any agent can call, with the rubric as a swappable YAML.

---

## The 60-second version

```bash
git clone https://github.com/cgallic/recovery-desk
cd recovery-desk
cp .env.example .env          # optional: add ANTHROPIC_API_KEY for live mode
make demo                     # init db, seed a missed call, open the ledger UI
```

`make demo` opens a split screen. **Left:** the callback + microsite the agent is writing. **Right:** the live Bookability ledger. Watch the first draft land at **66/100**, one line flip red (`concrete_slot: no specific time offered`), the agent revise on its own, and the score climb to **100/100 — PASS**. Then it ships.

It runs **offline with no API key** (deterministic fallback) so the loop closes on a clean clone. Add an `ANTHROPIC_API_KEY` and the same command runs the live Claude planner/grader/reviser.

Prefer not to start a server? `make demo-replay` bakes a **real captured run** into a standalone, double-clickable `demo/replay-bookability.html` (and `demo/replay-compliance.html`) — every number in it came from running the loop, not from authored copy.

### The demo video is in the repo (`demo/recovery-desk-demo.mp4`)

An 86-second 1080p video ships in the repo: [`demo/recovery-desk-demo.mp4`](demo/recovery-desk-demo.mp4). It is **rendered from real runs**, not hand-edited — `make video` re-creates it deterministically: it runs the loop on the bookability rubric *and* the compliance rubric, builds a captioned storyboard, drives it headless in Chromium, and encodes with ffmpeg. The cover still (the red **66/100** self-fail frame) is written alongside as `demo/cover.png`. Captions stand in for voiceover so the video reads muted. The self-fail lands well inside the first 20 seconds.

```bash
pip install -e ".[video]" && playwright install chromium   # ffmpeg must be on PATH
make video                                                 # -> demo/recovery-desk-demo.mp4 + demo/cover.png
```

### The live Claude path is recorded in the repo (`demo/live-claude-run.md`)

The autonomous loop runs the live Claude reasoner when `ANTHROPIC_API_KEY` is set, and a deterministic reviser when it isn't. So the live path is not just claimed, the repo commits a captured transcript of it: [`demo/live-claude-run.md`](demo/live-claude-run.md). The shipped copy is generated with `make capture-live` (`--stub`), which runs the **identical** live branch in `llm.py` through a stub stand-in with no key — the transcript is plainly labelled `claude-live-stub` and its load-bearing reviser turn shows the failing rubric line id going *in* and a rewrite coming *out*, with the offline repair function never running. Set a real key and `python -m recovery_desk.capture_live_run` produces the same artifact with Claude's own prose:

```bash
make capture-live                                          # no key: stub of the live branch
ANTHROPIC_API_KEY=sk-ant-... python -m recovery_desk.capture_live_run   # real Claude
```

### The offline path is real self-correction, not a script

The offline reviser is deterministic so the demo is reproducible — but it is **not** a "weak draft on rev 0, strong draft on rev 1" toggle. It reads the *failing rubric line ids* the grader reported and applies one targeted repair per failing line (offer a slot, disarm the objection, add an opt-out, collapse to one CTA). Feed it different failing lines and it produces a different fix. It does not know in advance which lines will fail; it repairs whatever the grader flags — the same reasoning the live Claude reviser performs, reading the same fail signal. `make test` proves this (`test_offline_reviser_reads_fail_hints_not_revision_number`).

---

## What makes this autonomous (not a multi-step prompt)

1. **One goal in.** A `missed_call` record. That's the only human input.
2. **It grades itself against a named numeric rubric.** Six hard, falsifiable lines — not "looks good."
3. **It rejects its own first attempt.** The redo is driven by the score crossing a threshold, not by an exception or a human clicking retry.
4. **It owns its stopping condition.** `ship when score ≥ 85 OR after 3 revisions`. It terminates itself.
5. **Every action is an MCP tool call.** "Autonomous" means real actions through `bookability-mcp`, not text generation.

---

## The Bookability rubric (the invention)

`rubrics/bookability.rubric.yaml`. Six weighted lines, every one a checkable fact:

| Line | Weight | Passes when |
|------|--------|-------------|
| `callback_sla` | 18 | Promises contact within the business's SLA window |
| `concrete_slot` | 18 | Offers a **specific** date + clock time, not "let us know" |
| `objection_handled` | 16 | Pre-answers the caller's likely top objection |
| `contact_correct` | 18 | Business name, phone, address match the profile exactly (NAP) |
| `single_cta` | 15 | Exactly one call-to-action across message + site |
| `no_spam_words` | 15 | No deliverability spam-trigger words |

The grader is **deterministic** where it can be (string/regex/JSON) and **Claude-judged** where it must be — so the score is reproducible and improvement is falsifiable, not vibes.

### Swap the rubric, repoint the agent

The rubric is separable. The repo ships three:

```bash
RUBRIC=rubrics/bookability.rubric.yaml    make demo   # recover a missed call
RUBRIC=rubrics/compliance.rubric.yaml     make demo   # grade a regulated message
RUBRIC=rubrics/discoverability.rubric.yaml make demo  # grade a listing page
```

No code changes — the same self-grading agent becomes a compliance checker or a listing auditor by changing one file. And the **full self-improving loop** repoints, not just the score: offline, the compliance rubric drives a real self-fail-then-pass (the agent ships a 60/100 first draft, fails its own `opt_out_present` line, adds the opt-out, collapses to one CTA, and re-scores to 100/100). Proven by `test_second_domain_self_fails_then_passes_offline`.

---

## Architecture

```
RECOVERY DESK AGENT (Claude: plan / act / revise)
        │  every action via MCP
        ▼
bookability-mcp  ── build_recovery · score_draft · send_recovery · run_recovery_desk
        │  writes every step
        ▼
BLACKBOARD (SQLite)  ── runs · drafts · scores · score_lines · log
        │  polled by
        ▼
LEDGER UI (split-screen: draft | live score)
```

---

## Install the MCP server in your own client

`bookability-mcp` is a standard MCP server. Register it (example for a Claude Desktop-style `mcp.json`):

```json
{
  "mcpServers": {
    "bookability": {
      "command": "python",
      "args": ["-m", "recovery_desk.server"],
      "env": { "ANTHROPIC_API_KEY": "sk-ant-..." }
    }
  }
}
```

Then call `run_recovery_desk` with your own `missed_call` record, or `score_draft` with any draft + rubric to get a numeric Bookability score back. The server exposes four tools: `list_rubrics`, `score_draft`, `build_recovery`, `send_recovery` (dispatch, **dry-run by default**), and `run_recovery_desk` (the full loop).

## Recovered revenue

A passing ship returns a recovered-revenue figure computed from the business's own numbers in the goal record (`avg_job_value × recovery_conversion_rate`) — never an invented benchmark, and only claimed on a passing ship. For the bundled plumbing fixture (`$320` ticket × `35%` recovery conversion) that is **$112 expected per recovered call**. This is the same shape as our Stripe/funnel attribution: tie a shipped artifact to the revenue it earns.

---

## Commands

| Command | What it does |
|---------|--------------|
| `make demo` | Init db, seed a fixture, start the ledger UI, run the full loop |
| `make demo-replay` | Bake a real run into standalone `demo/replay-*.html` (no server) |
| `make video` | Render `demo/recovery-desk-demo.mp4` + `cover.png` from real runs (playwright + ffmpeg) |
| `make capture-live` | Record the live-Claude code path to `demo/live-claude-run.md` (`--stub`; or set a key) |
| `make test` | Grader tests + loop self-test + rubric-swap + fail-hint reviser + second-domain self-fail + live-path |
| `python -m recovery_desk.run fixtures/missed_call.json` | Headless loop run (prints the ledger + recovered revenue) |
| `python -m recovery_desk.server` | Start the MCP server (stdio) |

---

## Project layout

```
recovery-desk/
├── recovery_desk/
│   ├── blackboard.py     # SQLite coordination + agent log
│   ├── grader.py         # the Bookability rubric grader (the invention)
│   ├── builder.py        # build_recovery: callback + microsite (Factory sitegen reuse)
│   ├── agent.py          # the gated plan→act→grade→revise loop + stopping condition
│   ├── llm.py            # Claude planner/reviser + fail-hint-driven offline reviser
│   ├── impact.py         # recovered-revenue math (business's own numbers)
│   ├── server.py         # the MCP server (bookability-mcp) — the reusable asset
│   ├── run.py            # headless CLI entry point
│   ├── make_replay.py    # bake a real run into a standalone playable demo
│   ├── record_demo.py    # render the demo VIDEO (mp4 + cover) from real runs
│   ├── capture_live_run.py # bake the live-Claude code path into a transcript
│   └── webapp.py         # FastAPI ledger UI server
├── rubrics/              # bookability / compliance / discoverability YAMLs
├── fixtures/             # sample missed_call records (with economics)
├── demo/                 # the mp4 demo video, cover.png, live transcript, replays
├── ui/                   # the split-screen ledger frontend
├── Makefile
├── pyproject.toml
├── PUBLISH.md            # one-shot public-release checklist
└── .env.example
```

## License

MIT.
