"""
recovery_desk/blackboard.py — SQLite coordination layer for The Recovery Desk.

Every step of the autonomous loop is written here so the ledger UI can render the
self-evaluation as it happens. This is the audit trail that makes "the agent
graded itself and chose to redo it" watchable rather than narrated.

Schema: 5 tables
    runs         — one row per recovery run (goal in, terminal state out)
    drafts       — each draft the agent produces (revision 0, 1, 2, ...)
    scores       — the total Bookability score for each draft
    score_lines  — per-rubric-line pass/fail/points for each draft
    log          — chronological agent action log (planner/builder/grader/reviser)

The pattern (SQLite blackboard + agent log + polling watcher) mirrors the-loom.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

SCHEMA_VERSION = 1

RUN_STATES = {"planning", "building", "grading", "revising", "shipped_pass", "shipped_cap", "error"}


# ---------------------------------------------------------------------------
# Return dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Run:
    id: int
    call_id: str
    goal_json: str
    rubric_name: str
    threshold: int
    max_revisions: int
    state: str
    final_score: Optional[int]
    final_revision: Optional[int]
    created_at: str
    updated_at: str


@dataclass
class Draft:
    id: int
    run_id: int
    revision: int
    message: str
    site_html: str
    created_at: str


@dataclass
class ScoreLine:
    line_id: str
    weight: int
    points: int
    passed: bool
    detail: str


@dataclass
class LogEntry:
    id: int
    run_id: int
    actor: str
    action: str
    details: Optional[str]
    created_at: str


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id         TEXT    NOT NULL,
    goal_json       TEXT    NOT NULL,
    rubric_name     TEXT    NOT NULL,
    threshold       INTEGER NOT NULL,
    max_revisions   INTEGER NOT NULL,
    state           TEXT    NOT NULL DEFAULT 'planning',
    final_score     INTEGER,
    final_revision  INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    revision    INTEGER NOT NULL,
    message     TEXT    NOT NULL,
    site_html   TEXT    NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    draft_id    INTEGER NOT NULL REFERENCES drafts(id),
    revision    INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    passed      INTEGER NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS score_lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    score_id    INTEGER NOT NULL REFERENCES scores(id),
    line_id     TEXT    NOT NULL,
    weight      INTEGER NOT NULL,
    points      INTEGER NOT NULL,
    passed      INTEGER NOT NULL,
    detail      TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER REFERENCES runs(id),
    actor       TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    details     TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_drafts_run      ON drafts(run_id);
CREATE INDEX IF NOT EXISTS idx_scores_run      ON scores(run_id);
CREATE INDEX IF NOT EXISTS idx_score_lines_sid ON score_lines(score_id);
CREATE INDEX IF NOT EXISTS idx_log_run         ON log(run_id);
CREATE INDEX IF NOT EXISTS idx_log_created      ON log(created_at);
"""


def connect(db_path: Union[str, Path]) -> sqlite3.Connection:
    """Open a connection with row factory + foreign keys on."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Union[str, Path]) -> sqlite3.Connection:
    """Create all tables and return a connection."""
    conn = connect(db_path)
    conn.executescript(_CREATE_SQL)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def create_run(
    conn: sqlite3.Connection,
    call_id: str,
    goal: dict,
    rubric_name: str,
    threshold: int,
    max_revisions: int,
) -> int:
    with conn:
        cur = conn.execute(
            """INSERT INTO runs (call_id, goal_json, rubric_name, threshold, max_revisions)
               VALUES (?, ?, ?, ?, ?)""",
            (call_id, json.dumps(goal), rubric_name, threshold, max_revisions),
        )
        return int(cur.lastrowid)


def set_run_state(conn: sqlite3.Connection, run_id: int, state: str) -> None:
    if state not in RUN_STATES:
        raise ValueError(f"unknown run state: {state}")
    with conn:
        conn.execute(
            "UPDATE runs SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (state, run_id),
        )


def finalize_run(
    conn: sqlite3.Connection, run_id: int, state: str, final_score: int, final_revision: int
) -> None:
    with conn:
        conn.execute(
            """UPDATE runs SET state = ?, final_score = ?, final_revision = ?,
                       updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (state, final_score, final_revision, run_id),
        )


def get_run(conn: sqlite3.Connection, run_id: int) -> Optional[Run]:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return Run(**{k: row[k] for k in row.keys()})


def latest_run_id(conn: sqlite3.Connection) -> Optional[int]:
    row = conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else None


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------

def add_draft(
    conn: sqlite3.Connection, run_id: int, revision: int, message: str, site_html: str
) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO drafts (run_id, revision, message, site_html) VALUES (?, ?, ?, ?)",
            (run_id, revision, message, site_html),
        )
        return int(cur.lastrowid)


def get_draft(conn: sqlite3.Connection, draft_id: int) -> Optional[Draft]:
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        return None
    return Draft(**{k: row[k] for k in row.keys()})


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------

def add_score(
    conn: sqlite3.Connection,
    run_id: int,
    draft_id: int,
    revision: int,
    total: int,
    passed: bool,
    lines: List[ScoreLine],
) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO scores (run_id, draft_id, revision, total, passed) VALUES (?, ?, ?, ?, ?)",
            (run_id, draft_id, revision, total, 1 if passed else 0),
        )
        score_id = int(cur.lastrowid)
        for ln in lines:
            conn.execute(
                """INSERT INTO score_lines (score_id, line_id, weight, points, passed, detail)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (score_id, ln.line_id, ln.weight, ln.points, 1 if ln.passed else 0, ln.detail),
            )
        return score_id


def get_score_lines(conn: sqlite3.Connection, score_id: int) -> List[ScoreLine]:
    rows = conn.execute(
        "SELECT * FROM score_lines WHERE score_id = ? ORDER BY id", (score_id,)
    ).fetchall()
    return [
        ScoreLine(
            line_id=r["line_id"],
            weight=r["weight"],
            points=r["points"],
            passed=bool(r["passed"]),
            detail=r["detail"] or "",
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------

def log(
    conn: sqlite3.Connection,
    run_id: Optional[int],
    actor: str,
    action: str,
    details: Optional[dict] = None,
) -> int:
    payload = json.dumps(details) if details is not None else None
    with conn:
        cur = conn.execute(
            "INSERT INTO log (run_id, actor, action, details) VALUES (?, ?, ?, ?)",
            (run_id, actor, action, payload),
        )
        return int(cur.lastrowid)


def recent_log(conn: sqlite3.Connection, run_id: int, limit: int = 50) -> List[LogEntry]:
    rows = conn.execute(
        "SELECT * FROM log WHERE run_id = ? ORDER BY id ASC LIMIT ?", (run_id, limit)
    ).fetchall()
    return [
        LogEntry(
            id=r["id"],
            run_id=r["run_id"],
            actor=r["actor"],
            action=r["action"],
            details=r["details"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Ledger snapshot — exactly what the UI polls
# ---------------------------------------------------------------------------

def ledger_snapshot(conn: sqlite3.Connection, run_id: int) -> dict:
    """Return everything the split-screen ledger UI needs in one read."""
    run = get_run(conn, run_id)
    if run is None:
        return {"error": "run not found"}

    score_rows = conn.execute(
        "SELECT * FROM scores WHERE run_id = ? ORDER BY revision ASC", (run_id,)
    ).fetchall()
    revisions = []
    for s in score_rows:
        lines = get_score_lines(conn, s["id"])
        draft = get_draft(conn, s["draft_id"])
        revisions.append({
            "revision": s["revision"],
            "total": s["total"],
            "passed": bool(s["passed"]),
            "message": draft.message if draft else "",
            "site_html": draft.site_html if draft else "",
            "lines": [
                {
                    "line_id": ln.line_id,
                    "weight": ln.weight,
                    "points": ln.points,
                    "passed": ln.passed,
                    "detail": ln.detail,
                }
                for ln in lines
            ],
        })

    return {
        "run_id": run.id,
        "call_id": run.call_id,
        "rubric": run.rubric_name,
        "threshold": run.threshold,
        "state": run.state,
        "final_score": run.final_score,
        "final_revision": run.final_revision,
        "revisions": revisions,
        "log": [
            {"actor": e.actor, "action": e.action, "at": e.created_at}
            for e in recent_log(conn, run_id)
        ],
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _run_tests() -> None:
    print("BLACKBOARD SELF-TEST")
    conn = init_db(":memory:")

    rid = create_run(conn, "mc_1", {"goal": "recover"}, "bookability", 85, 3)
    assert rid == 1
    assert latest_run_id(conn) == 1

    d0 = add_draft(conn, rid, 0, "draft zero", "<html>0</html>")
    lines0 = [
        ScoreLine("concrete_slot", 18, 0, False, "no specific time offered"),
        ScoreLine("single_cta", 15, 15, True, "exactly one CTA"),
    ]
    add_score(conn, rid, d0, 0, 61, False, lines0)
    set_run_state(conn, rid, "revising")

    d1 = add_draft(conn, rid, 1, "draft one", "<html>1</html>")
    lines1 = [
        ScoreLine("concrete_slot", 18, 18, True, "offered 2:00 PM"),
        ScoreLine("single_cta", 15, 15, True, "exactly one CTA"),
    ]
    add_score(conn, rid, d1, 1, 91, True, lines1)
    finalize_run(conn, rid, "shipped_pass", 91, 1)

    snap = ledger_snapshot(conn, rid)
    assert snap["state"] == "shipped_pass"
    assert snap["final_score"] == 91
    assert len(snap["revisions"]) == 2
    assert snap["revisions"][0]["total"] == 61 and not snap["revisions"][0]["passed"]
    assert snap["revisions"][1]["total"] == 91 and snap["revisions"][1]["passed"]
    # the falsifiable improvement: concrete_slot flips 0 -> 18
    r0 = {l["line_id"]: l for l in snap["revisions"][0]["lines"]}
    r1 = {l["line_id"]: l for l in snap["revisions"][1]["lines"]}
    assert r0["concrete_slot"]["points"] == 0 and r1["concrete_slot"]["points"] == 18
    print("[PASS] run/draft/score/score_lines/ledger_snapshot all consistent")
    print("[PASS] falsifiable improvement recorded (concrete_slot 0 -> 18)")

    conn.close()
    print("ALL BLACKBOARD TESTS PASSED")


if __name__ == "__main__":
    _run_tests()
