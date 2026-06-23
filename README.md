# The Recovery Desk

**An autonomous agent that turns a missed call into a booked job — and grades its own work until it passes.**

Built for the lablab.ai Autonomous Agents Hack (Anthropic track). Given one missed-call record, the agent drafts a callback message and a branded recovery microsite, scores its own draft against a numeric **Bookability** rubric, **rejects its own first draft**, revises, re-scores, and ships only when it clears the threshold — terminating itself. No human touches the loop after the goal goes in.

The reusable asset is `bookability-mcp`: an MCP server that exposes the self-grading loop as tools any agent can call, with the rubric as a swappable YAML.

---

## Live demo (deployed)

- **App:** https://kai-lablab-autonomous-agents-2026.vercel.app
- **Real autonomous agent endpoint:** `POST https://kai-lablab-autonomous-agents-2026.vercel.app/api/agent`
- **Deterministic (offline) loop endpoint:** `POST https://kai-lablab-autonomous-agents-2026.vercel.app/api/run`

`POST /api/agent` runs a **real model** (gemini-2.5-flash on the live deploy; swappable to Claude by setting `ANTHROPIC_API_KEY` — the Anthropic path is preferred when the key is present) that autonomously calls the engine's six tools. On the verified live run the model built a callback draft, **scored it and self-failed at 33/100**, ran the revise loop to **100/100**, then booked the job (today 2:00 PM) — and the booking gate only fired because a passing recovery shipped. The response carries the genuine tool-use transcript + token usage (no key set → it returns `ok:false` and the page falls back to `/api/run`, never a faked "real" run).

---

## Run it (2 commands, stdlib-only)

The core is **stdlib-only**. On a clean clone with **nothing installed, no API key, no network**:

```bash
git clone https://github.com/cgallic/recovery-desk && cd recovery-desk

# 1) prove the substrate (≈20 assertions: the loop self-fails then ships, the
#    grader is falsifiable, the rubric is swappable across 4 domains (incl. an
#    externally-authored one), the offline
#    reviser is fail-hint driven, the live-Claude path is wired, the YAML reader
#    is stdlib-only, and the bookability-mcp asset smokes clean)
python -m recovery_desk selftest

# 2) run the full autonomous loop end to end on the seed missed call. It prints
#    the live trace, writes the shipped microsite + the blackboard, and exits 0:
#    plan -> act -> self-grade 66/100 -> REJECT OWN DRAFT -> revise -> 100/100 -> SHIP
python -m recovery_desk demo
```

The money moment is in plain text, no UI required:

```
[grader]    self-scored rev 0:  66/100  [FAIL]
[conductor] rejecting OWN draft — failing lines: concrete_slot, objection_handled
[reviser]   fixing only those lines, keeping what passed
[grader]    self-scored rev 1: 100/100  [PASS]
[conductor] SHIP (cleared threshold): 100/100 at rev 1
```

No human touched the loop after the goal went in. The captured run is committed at
[`demo/run-output.log`](demo/run-output.log); the exact commands are in
[`demo/README.md`](demo/README.md).

How the offline core is honest: every external leaf (the Claude reasoner, the MCP
transport, the web UI, the SMS dispatch) sits behind an interface with a
deterministic offline stub that is auto-selected when its library/key is absent —
but the **real** control flow (plan → act → self-grade → revise → ship + the
stopping condition) executes for real. Add an `ANTHROPIC_API_KEY` (or run
`--live-cli`, see below) and the **identical** loop runs the real Claude model as
the drafter/reviser instead of the offline one.

### Run the REAL Claude reasoner with no API key (`--live-cli`)

If you are in a Claude Code environment you already have authenticated access to
the real model through the `claude` CLI — no `ANTHROPIC_API_KEY` needed. Point the
loop at it:

```bash
RECOVERY_DESK_CLAUDE_CLI=1 python -m recovery_desk demo --live-cli
```

The **identical** loop now drafts, self-grades, and revises with the real Claude
model (`claude -p` under the hood). In a captured run Claude wrote an 82/100 first
draft, its own grader failed `callback_sla`, Claude read that exact fail hint and
added a within-the-hour callback promise, and it shipped at 85/100 — its own prose,
no deterministic repair. That run is committed at [`demo/live-claude-run.md`](demo/live-claude-run.md)
and reproducible by any judge with the Claude CLI.

### Watch it instead of read it (optional)

```bash
pip install -e ".[server]"
python -m recovery_desk serve   # split screen: work product | live score ledger
```

Watch the first draft land at **66/100**, one line flip red (`concrete_slot: no specific time offered`), the agent revise on its own, and the score climb to **100/100 — PASS**. Then it ships.

Prefer not to start a server? `make demo-replay` bakes a **real captured run** into a standalone, double-clickable `demo/replay-bookability.html` (and `demo/replay-compliance.html`) — every number in it came from running the loop, not from authored copy.

### The demo video is in the repo (`demo/recovery-desk-demo.mp4`)

An 86-second 1080p video ships in the repo: [`demo/recovery-desk-demo.mp4`](demo/recovery-desk-demo.mp4). It is **rendered from real runs**, not hand-edited — `make video` re-creates it deterministically: it runs the loop on the bookability rubric *and* the compliance rubric, builds a captioned storyboard, drives it headless in Chromium, and encodes with ffmpeg. The cover still (the red **66/100** self-fail frame) is written alongside as `demo/cover.png`. Captions stand in for voiceover so the video reads muted. The self-fail lands well inside the first 20 seconds.

```bash
pip install -e ".[video]" && playwright install chromium   # ffmpeg must be on PATH
make video                                                 # -> demo/recovery-desk-demo.mp4 + demo/cover.png
```

### The live Claude path is recorded in the repo (`demo/live-claude-run.md`)

The committed transcript [`demo/live-claude-run.md`](demo/live-claude-run.md) is a **real Claude run** (`reasoner: claude-cli`, `model: claude-opus-4-8`), captured with no API key via the Claude Code CLI. Its load-bearing reviser turn shows the failing rubric line id (`callback_sla`) going *in* and Claude's own rewrite coming *out* — the offline repair function never runs on this path. Regenerate it yourself:

```bash
# real Claude via the Claude Code CLI (no API key)
RECOVERY_DESK_CLAUDE_CLI=1 python -m recovery_desk.capture_live_run --live-cli

# real Claude via the Anthropic API
ANTHROPIC_API_KEY=sk-ant-... python -m recovery_desk.capture_live_run

# no key and no CLI: an honest stub of the IDENTICAL live branch in llm.py,
# labelled `claude-live-stub` so it is never mistaken for a real-model capture
make capture-live                                          # (== --stub)
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

The rubric is separable. The repo ships four:

```bash
python -m recovery_desk demo --rubric bookability       # recover a missed call
python -m recovery_desk demo --rubric compliance        # grade a regulated message
python -m recovery_desk demo --rubric discoverability   # grade a listing page
python -m recovery_desk demo --rubric donor_callback    # a non-profit donor callback
```

No code changes — the same self-grading agent becomes a compliance checker or a listing auditor by changing one file. And the **full self-improving loop** repoints, not just the score: offline, the compliance rubric drives a real self-fail-then-pass (the agent ships a 60/100 first draft, fails its own `opt_out_present` line, adds the opt-out, collapses to one CTA, and re-scores to 100/100). Proven by `test_second_domain_self_fails_then_passes_offline`.

#### An externally-authored rubric (the stranger test)

`rubrics/donor_callback.rubric.yaml` was contributed by an **outside operator** (a non-profit's volunteer coordinator) in a domain unrelated to this project — donor/volunteer callbacks, not calls-as-a-service. The contributor wrote **only** that YAML file; no Python was touched. The same loop reads it, fails its own first draft (40/100 on `concrete_slot` + `single_cta`), repairs exactly those lines, and ships at 100/100 — proven by `test_external_rubric_self_fails_then_passes`. This is the falsifiable form of "a stranger repoints the agent at their own domain."

**Authoring a rubric.** A new rubric is a YAML of weighted lines. For the no-key offline reviser to auto-repair a failing line, reuse one of its published line ids — `concrete_slot`, `objection_handled`, `callback_sla`, `opt_out_present`, `single_cta` (each maps to a deterministic repair). Under the live Claude reasoner this is not required: the reviser reads each line's `fail_hint` text directly, so a brand-new line id repairs too.

---

## Architecture

```
RECOVERY DESK AGENT (Claude: plan / act / revise)
        │  every action via MCP
        ▼
bookability-mcp  ── build_recovery · score_draft · send_recovery · run_recovery_desk · book_recovery
        │  writes every step
        ▼
BLACKBOARD (SQLite)  ── runs · drafts · scores · score_lines · log · dispatch_outbox
        │  polled by
        ▼
LEDGER UI (split-screen: draft | live score)
```

### End-to-end: ship, dispatch, and BOOK the job

The loop doesn't stop at a high-scoring draft. On a passing ship, `book_recovery`
dispatches the recovery and writes the **booked job** back to the blackboard's
`dispatch_outbox` table — a falsifiable record that the recovery actually went out
and the job was booked into a real open slot, not just that a draft scored well.
It is dry-run by default (a clean clone proves the wiring without sending), and
only a *passing* ship is ever dispatched. The `demo` command runs this end-to-end
against the demo DB; inspect it:

```bash
sqlite3 recovery_desk.db "SELECT call_id, to_contact, booked, booked_slot FROM dispatch_outbox;"
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

Then call `run_recovery_desk` with your own `missed_call` record, or `score_draft` with any draft + rubric to get a numeric Bookability score back. The server exposes six tools: `list_rubrics`, `score_draft`, `build_recovery`, `send_recovery` (dispatch, **dry-run by default**; live writes the sent message + booked job to `dispatch_outbox`), `run_recovery_desk` (the full loop), and `book_recovery` (the loop + live dispatch + booked-job writeback, end-to-end).

## Recovered revenue

A passing ship returns a recovered-revenue figure computed from the business's own numbers in the goal record (`avg_job_value × recovery_conversion_rate`) — never an invented benchmark, and only claimed on a passing ship. For the bundled plumbing fixture (`$320` ticket × `35%` recovery conversion) that is **$112 expected per recovered call**. This is the same shape as our Stripe/funnel attribution: tie a shipped artifact to the revenue it earns.

---

## Commands

| Command | What it does |
|---------|--------------|
| `python -m recovery_desk selftest` | All ~20 assertions (stdlib-only; no installs/key/network) |
| `python -m recovery_desk demo` | Run the full autonomous loop; print the live trace; ship + book the job; exit 0 |
| `python -m recovery_desk demo --rubric compliance` | Same agent, different domain (rubric is separable) |
| `python -m recovery_desk demo --rubric donor_callback` | The externally-authored rubric (foreign non-profit domain) |
| `RECOVERY_DESK_CLAUDE_CLI=1 python -m recovery_desk demo --live-cli` | Run the loop with the REAL Claude model (no API key, via Claude Code CLI) |
| `python -m recovery_desk serve` | Split-screen ledger UI (needs `.[server]`) |
| `python -m recovery_desk mcp-smoke` | Exercise every `bookability-mcp` tool offline (no transport) |
| `python -m recovery_desk.server` | Start the reusable MCP server over stdio (needs `.[mcp]`) |
| `make demo-replay` | Bake a real run into standalone `demo/replay-*.html` (no server) |
| `make video` | Render `demo/recovery-desk-demo.mp4` + `cover.png` from real runs (playwright + ffmpeg) |
| `make capture-live` | Record the live-Claude code path to `demo/live-claude-run.md` (`--stub`; or set a key) |

---

## Project layout

```
recovery-desk/
├── recovery_desk/
│   ├── __main__.py       # the single judge entry: `python -m recovery_desk selftest|demo|serve|mcp-smoke`
│   ├── blackboard.py     # SQLite coordination + agent log + dispatch_outbox writeback
│   ├── grader.py         # the Bookability rubric grader (the invention)
│   ├── miniyaml.py       # vendored stdlib-only YAML reader (no PyYAML needed for the core)
│   ├── builder.py        # build_recovery: callback + microsite (Factory sitegen reuse)
│   ├── agent.py          # the plan→act→grade→revise loop + the agent's own stopping condition
│   ├── llm.py            # Claude planner/reviser + fail-hint-driven offline reviser
│   ├── impact.py         # recovered-revenue math (business's own numbers)
│   ├── server.py         # the MCP server (bookability-mcp) — the reusable asset (+ `--smoke`)
│   ├── run.py            # headless CLI entry point (legacy alias)
│   ├── make_replay.py    # bake a real run into a standalone playable demo
│   ├── record_demo.py    # render the demo VIDEO (mp4 + cover) from real runs
│   ├── capture_live_run.py # bake the live-Claude code path into a transcript
│   └── webapp.py         # FastAPI ledger UI server
├── rubrics/              # bookability / compliance / discoverability / donor_callback (external) YAMLs
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
