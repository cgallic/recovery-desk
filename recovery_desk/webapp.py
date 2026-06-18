"""
recovery_desk/webapp.py — the split-screen ledger UI server.

Serves ui/index.html and two endpoints:
    POST /api/run    -> starts an autonomous run on the fixture (background thread)
    GET  /api/state  -> the live ledger snapshot the front-end polls

The front-end shows the work product (callback + microsite) on the left and the
live Bookability ledger (score -> fail -> revise -> re-score) on the right.
This is what makes the self-fail watchable in 20 seconds.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from recovery_desk import blackboard as bb
from recovery_desk.agent import run_recovery_desk

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = REPO_ROOT / "ui"
DB_PATH = os.environ.get("RECOVERY_DESK_DB", str(REPO_ROOT / "recovery_desk.db"))
RUBRIC = os.environ.get("RUBRIC", str(REPO_ROOT / "rubrics" / "bookability.rubric.yaml"))
FIXTURE = os.environ.get("FIXTURE", str(REPO_ROOT / "fixtures" / "missed_call.json"))

app = FastAPI(title="The Recovery Desk")
_current_run_id: Optional[int] = None
_lock = threading.Lock()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((UI_DIR / "index.html").read_text(encoding="utf-8"))


@app.post("/api/run")
def start_run() -> JSONResponse:
    """Kick off an autonomous run in the background. step_delay paces it for the demo."""
    global _current_run_id
    goal = json.loads(Path(FIXTURE).read_text(encoding="utf-8"))

    def _go() -> None:
        global _current_run_id
        # fresh db each run so the demo always starts clean
        if Path(DB_PATH).exists():
            Path(DB_PATH).unlink()
        outcome = run_recovery_desk(goal, RUBRIC, db_path=DB_PATH, step_delay=0.9)
        with _lock:
            _current_run_id = outcome.run_id

    threading.Thread(target=_go, daemon=True).start()
    return JSONResponse({"started": True})


@app.get("/api/state")
def state() -> JSONResponse:
    conn = bb.connect(DB_PATH) if Path(DB_PATH).exists() else None
    if conn is None:
        return JSONResponse({"state": "idle", "revisions": []})
    rid = bb.latest_run_id(conn)
    if rid is None:
        conn.close()
        return JSONResponse({"state": "idle", "revisions": []})
    snap = bb.ledger_snapshot(conn, rid)
    conn.close()
    return JSONResponse(snap)


def main() -> None:
    import uvicorn
    port = int(os.environ.get("PORT", "8799"))
    print(f"The Recovery Desk ledger UI -> http://localhost:{port}")
    print(f"  rubric : {RUBRIC}")
    print(f"  fixture: {FIXTURE}")
    print(f"  live Claude: {'ON' if os.environ.get('ANTHROPIC_API_KEY') else 'OFF (offline fallback)'}")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
