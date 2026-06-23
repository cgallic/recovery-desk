"""api/run.py - one-request deterministic Recovery Desk run for the deployed demo.

Vercel functions are stateless, so a poll-based flow (start -> /api/state -> ...)
can land on different instances and lose the in-memory blackboard. This endpoint
runs the ENTIRE real autonomous loop inside a SINGLE request on an in-memory
SQLite blackboard - the same agent.run_recovery_desk the CLI and MCP server use -
and returns the full per-revision Bookability ledger so the page can replay the
real self-evaluation:

  PLAN -> build_recovery -> grade (self-score) -> FAIL (rev 0, ~66/100) ->
  revise ONLY the failing rubric lines -> re-grade -> PASS -> ship.

It is the real engine executing offline-deterministically (byte-for-byte
reproducible), NOT a canned replay. The shipped microsite the builder rendered is
returned so the page shows the genuine artifact. For the REAL model autonomously
driving these same ops as tools, see /api/agent.
"""
from http.server import BaseHTTPRequestHandler
import json
import sys
import tempfile
import uuid
from pathlib import Path

# Make the bundled recovery_desk package importable from the function.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recovery_desk import blackboard as bb  # noqa: E402
from recovery_desk.agent import run_recovery_desk  # noqa: E402

FIXTURE = ROOT / "fixtures" / "missed_call.json"
RUBRIC = ROOT / "rubrics" / "bookability.rubric.yaml"


def run_demo() -> dict:
    goal = json.loads(FIXTURE.read_text(encoding="utf-8"))
    db = str(Path(tempfile.mkdtemp(prefix="rd_")) / f"{uuid.uuid4().hex[:8]}.db")

    outcome = run_recovery_desk(goal, str(RUBRIC), db_path=db)

    conn = bb.connect(db)
    snap = bb.ledger_snapshot(conn, outcome.run_id)
    conn.close()

    return {
        "ok": True,
        "run_id": outcome.run_id,
        "rubric": snap.get("rubric"),
        "threshold": snap.get("threshold"),
        "state": outcome.state,
        "final_score": outcome.final_score,
        "final_revision": outcome.final_revision,
        "revisions": snap.get("revisions", []),
        "log": snap.get("log", []),
        "shipped_message": outcome.shipped_message,
        "shipped_site_html": outcome.shipped_site_html,
        "impact": outcome.impact,
    }


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("access-control-allow-origin", "*")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._send(200, run_demo())
        except Exception as e:  # pragma: no cover
            self._send(500, {"ok": False, "error": str(e)})

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._send(200, run_demo())
        except Exception as e:  # pragma: no cover
            self._send(500, {"ok": False, "error": str(e)})
