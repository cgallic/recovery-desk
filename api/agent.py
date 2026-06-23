"""api/agent.py - REAL model autonomously driving the Recovery Desk engine ops.

This is the "autonomous agent" path the hackathon demands: a real model (Anthropic
preferred, Gemini fallback) AUTONOMOUSLY selects and sequences THIS entry's engine
operations - exposed as 6 tools - to recover one missed call into a booked job,
including a recovery/gate moment:

  build_recovery -> score_draft (the model sees its own draft FAIL the numeric
  Bookability rubric, ~66/100) -> run_recovery_desk (self-revise the failing lines
  until it ships a PASS) -> book_recovery (which REFUSES to dispatch unless the run
  shipped a pass - the gate).

The model genuinely calls the API: the response carries the real tool-use
transcript + token usage, so it is verifiable, not simulated. The tools call the
SAME pure functions (recovery_desk.server.tool_*) the MCP server and CLI use - the
model is driving the real engine, not a mock.

Hard cost limits (a public endpoint on Connor's capped key): temperature 0,
maxOutputTokens/max_tokens 512, <=MAX_TURNS tool rounds. The un-bypassable ceiling
is the monthly spend cap set on the key. The key value is NEVER printed.

If neither ANTHROPIC_API_KEY nor GEMINI_API_KEY is set, it returns ok:false with a
clear message so the page falls back to the deterministic /api/run - never a fake
"real" run.
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import sys
import tempfile
import urllib.request
import urllib.error
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recovery_desk import server as rd  # noqa: E402  (pure tool_* implementations)

FIXTURE = ROOT / "fixtures" / "missed_call.json"

MODEL_ANTHROPIC = os.environ.get("AGENT_MODEL", "claude-haiku-4-5")
MODEL_GEMINI = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_TURNS = 6
MAX_TOKENS = 512

# ---------------------------------------------------------------------------
# The engine ops, exposed as model tools. Each maps to a recovery_desk.server
# pure function (the same code the MCP server exposes), so the model drives the
# real engine. A per-request DB path keeps each run isolated and stateless.
# ---------------------------------------------------------------------------

TOOLS = [
    {"name": "list_rubrics",
     "description": "List the self-evaluation rubric files available (proves the standard is swappable). No args.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "build_recovery",
     "description": "Render a callback message + a branded recovery microsite from a draft {message, headline}. Returns {message, site_html, business_profile}.",
     "input_schema": {"type": "object",
                      "properties": {"message": {"type": "string"}, "headline": {"type": "string"}},
                      "required": ["message"]}},
    {"name": "score_draft",
     "description": "Grade the LAST recovery you built with build_recovery against the numeric Bookability rubric. Call build_recovery first, then call this with NO arguments to self-evaluate it. Returns total (0-100), passed, threshold, and per-line pass/fail with details.",
     "input_schema": {"type": "object", "properties": {"rubric": {"type": "string"}}}},
    {"name": "run_recovery_desk",
     "description": "Run the FULL autonomous plan->build->grade->revise->ship loop on the missed call. The loop self-revises only the failing rubric lines until it ships a pass (or hits the revision cap). Returns state ('shipped_pass'|'shipped_cap'), final_score, final_revision, shipped_message, impact.",
     "input_schema": {"type": "object", "properties": {"rubric": {"type": "string"}}}},
    {"name": "send_recovery",
     "description": "Dispatch the last shipped recovery message to the caller (uses the loop's shipped message; pass message only to override). DRY-RUN by default (returns what WOULD send, sends nothing). Set dry_run=false to record a real dispatch to the outbox.",
     "input_schema": {"type": "object",
                      "properties": {"message": {"type": "string"}, "dry_run": {"type": "boolean"}}}},
    {"name": "book_recovery",
     "description": "End-to-end gate: run the loop, then book the job into the first open slot ONLY IF the run shipped a passing recovery. A capped/failed run is NEVER booked. dry_run defaults true; set dry_run=false to write the booked-job record. Returns state, booked, booked_slot, impact.",
     "input_schema": {"type": "object",
                      "properties": {"rubric": {"type": "string"}, "dry_run": {"type": "boolean"}}}},
]


def _new_db() -> str:
    return str(Path(tempfile.mkdtemp(prefix="rd_agent_")) / f"{uuid.uuid4().hex[:8]}.db")


def exec_tool(ctx: dict, name: str, args: dict) -> dict:
    """Dispatch a model tool call to the real engine op. ctx carries the goal and a
    per-request db path so book/send writebacks are isolated and stateless."""
    goal = ctx["goal"]
    db = ctx["db"]
    if name == "list_rubrics":
        return rd.tool_list_rubrics()
    if name == "build_recovery":
        draft = {"message": str(args.get("message", "")),
                 "headline": str(args.get("headline", ""))}
        built = rd.tool_build_recovery(draft, goal)
        # remember the last built artifact so the page can render it
        ctx["last_built"] = built
        return built
    if name == "score_draft":
        # Score the last built artifact (model passes no blobs — avoids echoing
        # large strings back through the model, which can malform a tool call).
        built = ctx.get("last_built") or {}
        msg = str(args.get("message") or built.get("message", ""))
        html = str(args.get("site_html") or built.get("site_html", ""))
        return rd.tool_score_draft(msg, html, goal.get("business_profile", {}),
                                   args.get("rubric") or None)
    if name == "run_recovery_desk":
        out = rd.tool_run_recovery_desk(goal, args.get("rubric") or None, db_path=db)
        ctx["last_run"] = out
        return out
    if name == "send_recovery":
        run = ctx.get("last_run") or {}
        msg = str(args.get("message") or run.get("shipped_message")
                  or (ctx.get("last_built") or {}).get("message", ""))
        return rd.tool_send_recovery(msg, goal,
                                     dry_run=bool(args.get("dry_run", True)), db_path=db)
    if name == "book_recovery":
        out = rd.tool_book_recovery(goal, args.get("rubric") or None, db_path=db,
                                    dry_run=bool(args.get("dry_run", True)))
        ctx["last_run"] = out
        return out
    return {"error": f"unknown tool {name}"}


SYSTEM = (
    "You are the Recovery Desk agent. A local service business missed a call from a "
    "potential customer. Your job: recover that missed call into a booked job, using "
    "ONLY the provided tools, and prove the recovery is good enough to ship by grading "
    "it against the numeric Bookability rubric.\n"
    "Workflow: write a first callback draft and call build_recovery, then call "
    "score_draft (no arguments - it grades the draft you just built) to "
    "self-evaluate. Your first draft will likely FAIL specific rubric lines (e.g. no "
    "concrete time slot). When it fails, call run_recovery_desk to run the full "
    "self-revising loop that repairs only the failing lines until it ships a passing "
    "recovery. Then call book_recovery to book the job - it will REFUSE to book unless a "
    "passing recovery shipped. Be decisive; do not ask the user questions. Stop once the "
    "job is booked."
)
FIRST_USER = (
    "Recover this missed call and take it all the way to a booked job, honoring the "
    "Bookability gate. Start by drafting and scoring; revise if it fails; only book a pass."
)


# ---------------------------------------------------------------------------
# Anthropic provider (preferred)
# ---------------------------------------------------------------------------

def _http_json(url: str, headers: dict, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=55) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"_error_text": e.read().decode("utf-8", "replace")[:300]}


def run_anthropic(key: str, ctx: dict) -> dict:
    messages = [{"role": "user", "content": FIRST_USER}]
    transcript: list = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    turns = 0
    while turns < MAX_TURNS:
        turns += 1
        status, data = _http_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            {"model": MODEL_ANTHROPIC, "max_tokens": MAX_TOKENS, "temperature": 0,
             "system": SYSTEM, "tools": TOOLS, "messages": messages})
        if status != 200:
            return {"ok": False, "reason": "api_error", "provider": "anthropic",
                    "status": status, "message": data.get("_error_text", ""),
                    "transcript": transcript}
        u = data.get("usage") or {}
        usage["input_tokens"] += u.get("input_tokens", 0)
        usage["output_tokens"] += u.get("output_tokens", 0)
        content = data.get("content", [])
        says = " ".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        if says:
            transcript.append({"role": "assistant", "text": says})
        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if data.get("stop_reason") != "tool_use" or not tool_uses:
            break
        messages.append({"role": "assistant", "content": content})
        results = []
        for tu in tool_uses:
            result = exec_tool(ctx, tu["name"], tu.get("input") or {})
            transcript.append({"tool": tu["name"], "input": tu.get("input") or {}, "result": result})
            results.append({"type": "tool_result", "tool_use_id": tu["id"],
                            "content": json.dumps(result)})
        messages.append({"role": "user", "content": results})
    return {"ok": True, "provider": "anthropic", "model": MODEL_ANTHROPIC,
            "turns": turns, "usage": usage, "transcript": transcript}


# ---------------------------------------------------------------------------
# Gemini provider (fallback) - real function-calling agent loop
# ---------------------------------------------------------------------------

def _gemini_tools() -> list:
    return [{"functionDeclarations": [
        {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}
        for t in TOOLS]}]


def run_gemini(key: str, ctx: dict) -> dict:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODEL_GEMINI}:generateContent?key={key}")
    contents = [{"role": "user", "parts": [{"text": FIRST_USER}]}]
    transcript: list = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    turns = 0
    while turns < MAX_TURNS:
        turns += 1
        status, data = _http_json(url, {}, {
            "system_instruction": {"parts": [{"text": SYSTEM}]},
            "contents": contents, "tools": _gemini_tools(),
            # thinkingBudget 0 turns OFF gemini-2.5-flash "thinking" so the small
            # output budget is spent on the tool call, not silent reasoning tokens
            # (otherwise a 512-cap turn can emit zero tool calls).
            "generationConfig": {"temperature": 0, "maxOutputTokens": MAX_TOKENS,
                                 "thinkingConfig": {"thinkingBudget": 0}}})
        if status != 200:
            return {"ok": False, "reason": "api_error", "provider": "gemini",
                    "status": status, "message": data.get("_error_text", ""),
                    "transcript": transcript}
        um = data.get("usageMetadata") or {}
        usage["input_tokens"] += um.get("promptTokenCount", 0)
        usage["output_tokens"] += um.get("candidatesTokenCount", 0)
        parts = (((data.get("candidates") or [{}])[0]).get("content") or {}).get("parts") or []
        says = " ".join(p["text"] for p in parts if p.get("text")).strip()
        if says:
            transcript.append({"role": "assistant", "text": says})
        calls = [p["functionCall"] for p in parts if p.get("functionCall")]
        if not calls:
            break
        contents.append({"role": "model", "parts": parts})
        resp_parts = []
        for fc in calls:
            result = exec_tool(ctx, fc["name"], fc.get("args") or {})
            transcript.append({"tool": fc["name"], "input": fc.get("args") or {}, "result": result})
            resp_parts.append({"functionResponse": {"name": fc["name"], "response": {"result": result}}})
        contents.append({"role": "user", "parts": resp_parts})
    return {"ok": True, "provider": "gemini", "model": MODEL_GEMINI,
            "turns": turns, "usage": usage, "transcript": transcript}


def run_agent() -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    gem = os.environ.get("GEMINI_API_KEY")
    if not key and not gem:
        return {"ok": False, "reason": "no_key",
                "message": "No model key configured (ANTHROPIC_API_KEY or GEMINI_API_KEY); "
                           "use /api/run for the deterministic demo."}
    goal = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ctx = {"goal": goal, "db": _new_db(), "last_built": None, "last_run": None}
    out = run_anthropic(key, ctx) if key else run_gemini(gem, ctx)
    # surface the genuine artifacts the model produced through the tools
    built = ctx.get("last_built") or {}
    run = ctx.get("last_run") or {}
    out["artifacts"] = {
        "shipped_message": run.get("shipped_message") or built.get("message", ""),
        "shipped_site_html": run.get("shipped_site_html") or built.get("site_html", ""),
        "state": run.get("state"),
        "final_score": run.get("final_score"),
        "final_revision": run.get("final_revision"),
        "booked": run.get("booked"),
        "booked_slot": run.get("booked_slot"),
        "impact": run.get("impact"),
    }
    return out


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("access-control-allow-origin", "*")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._send(200, run_agent())
        except Exception as e:  # pragma: no cover
            self._send(500, {"ok": False, "error": str(e)})

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._send(200, run_agent())
        except Exception as e:  # pragma: no cover
            self._send(500, {"ok": False, "error": str(e)})
