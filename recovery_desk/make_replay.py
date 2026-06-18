"""
recovery_desk/make_replay.py — bake a real run into a standalone demo replay.

Runs the autonomous loop for real (offline, deterministic), captures the exact
ledger the UI would poll, and writes a SINGLE self-contained HTML file that
replays the run — score, per-line fail->pass transitions, the work product, and
the agent log — with no server and no dependencies. A judge double-clicks it and
watches the agent self-fail at 66 and ship at 100.

This is the recorded demo as a playable artifact: every number in it came from an
actual run of this code, not from authored copy.

    python -m recovery_desk.make_replay              # -> demo/replay.html
    RUBRIC=rubrics/compliance.rubric.yaml python -m recovery_desk.make_replay \
        --out demo/replay-compliance.html
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from recovery_desk import blackboard as bb
from recovery_desk.agent import run_recovery_desk

REPO = Path(__file__).resolve().parent.parent


def capture_run(rubric_path: str, fixture_path: str) -> dict:
    """Run the loop for real and return the full ledger snapshot + outcome."""
    goal = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "replay.db")
        outcome = run_recovery_desk(goal, rubric_path, db_path=db)
        conn = bb.connect(db)
        snap = bb.ledger_snapshot(conn, outcome.run_id)
        conn.close()
    snap["outcome"] = {
        "state": outcome.state,
        "final_score": outcome.final_score,
        "final_revision": outcome.final_revision,
        "impact": outcome.impact,
    }
    return snap


_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Recovery Desk — demo replay</title>
<style>
  :root{--bg:#0b0e13;--panel:#11151d;--border:#1e2530;--muted:#7c8794;--text:#e8edf2;
    --green:#5ad19a;--red:#e2645b;--accent:#6ea8fe;
    --mono:'SFMono-Regular',Menlo,Consolas,monospace;--sans:-apple-system,'Segoe UI',Roboto,sans-serif;}
  *{box-sizing:border-box;margin:0;padding:0;}
  html,body{height:100%;background:var(--bg);color:var(--text);font-family:var(--sans);}
  header{display:flex;align-items:center;justify-content:space-between;padding:12px 20px;
    border-bottom:1px solid var(--border);background:var(--panel);}
  header h1{font-size:16px;letter-spacing:.5px;}
  header .sub{font-size:12px;color:var(--muted);}
  .goal{font-family:var(--mono);font-size:12px;color:var(--accent);}
  button#play{background:var(--green);color:#04110a;border:none;font-weight:700;
    padding:9px 18px;border-radius:8px;cursor:pointer;font-size:13px;}
  .split{display:grid;grid-template-columns:1fr 1fr;height:calc(100vh - 53px);}
  .pane{padding:18px 20px;overflow-y:auto;} .pane.left{border-right:1px solid var(--border);}
  .pane h2{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:2px;color:var(--muted);margin-bottom:12px;}
  .msg{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px;
    font-size:14px;line-height:1.55;white-space:pre-wrap;margin-bottom:16px;min-height:90px;}
  .site{border:1px solid var(--border);border-radius:10px;overflow:hidden;height:300px;}
  .site iframe{width:100%;height:100%;border:0;background:#0f1115;}
  .scoreboard{display:flex;align-items:baseline;gap:14px;margin-bottom:6px;}
  .bigscore{font-family:var(--mono);font-size:54px;font-weight:700;line-height:1;transition:color .4s;}
  .bigscore.fail{color:var(--red);} .bigscore.pass{color:var(--green);}
  .thresh{font-size:13px;color:var(--muted);}
  .verdict{font-family:var(--mono);font-size:13px;padding:3px 10px;border-radius:6px;
    margin-bottom:16px;display:inline-block;}
  .verdict.fail{background:rgba(226,100,91,.15);color:var(--red);}
  .verdict.pass{background:rgba(90,209,154,.15);color:var(--green);}
  .lines{display:flex;flex-direction:column;gap:8px;}
  .line{display:flex;align-items:center;gap:10px;background:var(--panel);border:1px solid var(--border);
    border-left:4px solid var(--border);border-radius:8px;padding:10px 12px;transition:border-color .4s,background .4s;}
  .line.pass{border-left-color:var(--green);}
  .line.fail{border-left-color:var(--red);background:rgba(226,100,91,.07);}
  .line .id{font-family:var(--mono);font-size:13px;min-width:150px;}
  .line .detail{font-size:12px;color:var(--muted);flex:1;}
  .line .pts{font-family:var(--mono);font-size:13px;min-width:54px;text-align:right;}
  .line.pass .pts{color:var(--green);} .line.fail .pts{color:var(--red);}
  .revtabs{display:flex;gap:6px;margin-bottom:14px;}
  .revtab{font-family:var(--mono);font-size:12px;padding:5px 11px;border:1px solid var(--border);
    border-radius:6px;color:var(--muted);}
  .revtab.active{color:var(--text);border-color:var(--accent);}
  .revtab.fail{color:var(--red);} .revtab.pass{color:var(--green);}
  .impact{margin-top:14px;font-family:var(--mono);font-size:12px;color:var(--green);}
  .feed{margin-top:18px;border-top:1px solid var(--border);padding-top:12px;}
  .feed .ev{font-family:var(--mono);font-size:11px;color:var(--muted);padding:2px 0;}
  .feed .ev b{color:var(--accent);}
  .badge{font-family:var(--mono);font-size:10px;color:var(--muted);}
</style>
</head>
<body>
<header>
  <div><h1>The Recovery Desk — demo replay</h1>
    <div class="sub">A real, captured run. Every number below came from running the loop.
      <span class="badge">offline · deterministic</span></div></div>
  <div class="goal" id="goal"></div>
  <button id="play">Replay the run &rarr;</button>
</header>
<div class="split">
  <section class="pane left">
    <h2>Work product — what the agent wrote</h2>
    <div class="msg" id="message">Press &ldquo;Replay the run&rdquo;.</div>
    <div class="site"><iframe id="site" title="recovery microsite"></iframe></div>
  </section>
  <section class="pane right">
    <h2>Self-evaluation ledger — the agent grading itself</h2>
    <div class="revtabs" id="revtabs"></div>
    <div class="scoreboard"><div class="bigscore" id="bigscore">--</div><div class="thresh" id="thresh"></div></div>
    <div class="verdict" id="verdict" style="display:none;"></div>
    <div class="lines" id="lines"></div>
    <div class="impact" id="impact"></div>
    <div class="feed" id="feed"></div>
  </section>
</div>
<script>
const DATA = __DATA__;
const $=(id)=>document.getElementById(id);
$("goal").textContent = "goal: 1 missed call · rubric: " + DATA.rubric +
  " · ship at " + DATA.threshold;

function showRev(i, active){
  const revs = DATA.revisions;
  $("revtabs").innerHTML = revs.slice(0,i+1).map((r,j)=>
    `<div class="revtab ${r.passed?'pass':'fail'} ${j===i?'active':''}">rev ${r.revision} · ${r.total}</div>`).join("");
  const show = revs[i];
  $("message").textContent = show.message || "";
  $("site").srcdoc = show.site_html || "";
  const sc=$("bigscore"); sc.textContent=show.total; sc.className="bigscore "+(show.passed?"pass":"fail");
  $("thresh").textContent="/ 100   (ship at "+DATA.threshold+")";
  const v=$("verdict"); v.style.display="inline-block";
  v.className="verdict "+(show.passed?"pass":"fail");
  v.textContent=show.passed?"PASS — agent ships this":"FAIL — agent rejects its own draft";
  $("lines").innerHTML=(show.lines||[]).map(l=>
    `<div class="line ${l.passed?'pass':'fail'}"><div class="id">${l.line_id}</div>
       <div class="detail">${l.detail||""}</div><div class="pts">${l.points}/${l.weight}</div></div>`).join("");
  const seen = DATA.log.slice(0, Math.min(DATA.log.length, (i+1)*4));
  $("feed").innerHTML = seen.slice(-8).map(e=>`<div class="ev"><b>${e.actor}</b> — ${e.action}</div>`).join("");
  if(active && DATA.outcome.impact){
    $("impact").textContent = "Recovered revenue: " + DATA.outcome.impact.basis;
  }
}

function replay(){
  $("impact").textContent="";
  const revs = DATA.revisions;
  let i=0; showRev(0,false);
  const timer=setInterval(()=>{
    i++;
    if(i>=revs.length){ clearInterval(timer); showRev(revs.length-1, true); return; }
    showRev(i, i===revs.length-1);
  }, 1600);
}
$("play").onclick=replay;
// auto-play once on load so a judge sees it move immediately
replay();
</script>
</body>
</html>"""


def render(snap: dict) -> str:
    return _TEMPLATE.replace("__DATA__", json.dumps(snap))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rubric", default=os.environ.get(
        "RUBRIC", str(REPO / "rubrics" / "bookability.rubric.yaml")))
    ap.add_argument("--fixture", default=os.environ.get(
        "FIXTURE", str(REPO / "fixtures" / "missed_call.json")))
    ap.add_argument("--out", default=str(REPO / "demo" / "replay.html"))
    args = ap.parse_args()

    snap = capture_run(args.rubric, args.fixture)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(snap), encoding="utf-8")
    o = snap["outcome"]
    print(f"Captured real run: {o['state']} at rev {o['final_revision']} "
          f"with {o['final_score']}/100")
    print(f"Wrote playable replay -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
