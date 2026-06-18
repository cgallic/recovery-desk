"""
recovery_desk/record_demo.py — render the demo to a real MP4 video, deterministically.

The gating hackathon artifact is a 60-120s demo VIDEO. This script PRODUCES it
from real runs of the loop, with no manual screen recording:

  1. runs the autonomous loop for real on the bookability rubric AND the
     compliance rubric (offline/deterministic, so every number is reproducible);
  2. builds a self-contained, deterministically-timed, captioned demo page that
     plays the whole narrative — hook -> self-fail at 66 -> the agent rejecting
     its own draft -> revision -> 100/100 PASS -> the second-domain swap
     (compliance 60 -> 100) -> close. Captions stand in for voiceover so the
     video is legible muted, which is how hackathon judges watch;
  3. drives that page in headless Chromium (Playwright) at 1920x1080 and captures
     a frame on a fixed cadence as the animation advances by wall-clock time;
  4. encodes the frames to demo/recovery-desk-demo.mp4 with ffmpeg, and saves the
     self-fail frame as demo/cover.png (the red 66/100 thumbnail).

The legibility-in-first-20s requirement is met by construction: the domain, the
goal, and "the agent grades itself" are on screen and captioned by 0:08, and the
first numeric self-score lands well inside 0:20.

    python -m recovery_desk.record_demo                 # writes the mp4 + cover
    python -m recovery_desk.record_demo --fps 12        # smaller file
    python -m recovery_desk.record_demo --page-only     # just the recordable html

Requires playwright (with chromium) and ffmpeg on PATH. If either is missing the
script prints exactly what to install and exits non-zero — it never claims to have
produced a video it did not.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from recovery_desk.make_replay import capture_run

REPO = Path(__file__).resolve().parent.parent

# Total storyboard duration in seconds and the per-beat schedule (start seconds).
# Each beat sets the caption, which pane/rubric to show, and the revision index.
STORYBOARD_SECONDS = 86


def build_storyboard(book: dict, comp: dict) -> dict:
    """Turn two real ledger snapshots into a timed beat list the page replays."""
    return {
        "duration": STORYBOARD_SECONDS,
        "bookability": book,
        "compliance": comp,
        "beats": [
            # t(s), domain, rev index, caption, callout(optional)
            {"t": 0,  "domain": "bookability", "rev": None,
             "title": "A plumber misses a call. That's a lost job.",
             "sub": "Watch an agent win it back — and grade its own work until it's good enough to send."},
            {"t": 7,  "domain": "bookability", "rev": 0,
             "title": "One goal in: a single missed call. No human in the loop now.",
             "sub": "Left: the callback + recovery page it writes. Right: it scores itself against a Bookability rubric."},
            {"t": 18, "domain": "bookability", "rev": 0,
             "title": "First draft: 66/100. It fails — itself.",
             "sub": "No specific time offered. The caller's price worry unanswered. It rejects its own draft.",
             "callout": "FAIL"},
            {"t": 34, "domain": "bookability", "rev": 1,
             "title": "It fixes exactly the two failing lines.",
             "sub": "Two real time slots. The price objection disarmed. Then it re-scores."},
            {"t": 48, "domain": "bookability", "rev": 1,
             "title": "100/100. Green. It ships.",
             "sub": "The agent failed, fixed, and finished itself — it owns the stopping condition.",
             "callout": "PASS"},
            {"t": 60, "domain": "compliance", "rev": 0,
             "title": "Same agent, different job — swap one YAML.",
             "sub": "A regulated-message rubric. The first draft fails its own opt-out line: 60/100.",
             "callout": "FAIL"},
            {"t": 72, "domain": "compliance", "rev": 1,
             "title": "It self-corrects on a domain it has never seen.",
             "sub": "Adds the opt-out, collapses to one CTA, re-scores to 100. No code change.",
             "callout": "PASS"},
            {"t": 81, "domain": "bookability", "rev": 1,
             "title": "The Recovery Desk · bookability-mcp",
             "sub": "An agent that owns the outcome, grades itself against a real standard, and won't ship until it passes. Built on Claude + MCP."},
        ],
    }


_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>The Recovery Desk — demo</title>
<style>
  :root{--bg:#0b0e13;--panel:#11151d;--border:#1e2530;--muted:#7c8794;--text:#e8edf2;
    --green:#5ad19a;--red:#e2645b;--accent:#6ea8fe;
    --mono:Consolas,'SFMono-Regular',Menlo,monospace;--sans:'Segoe UI',-apple-system,Roboto,sans-serif;}
  *{box-sizing:border-box;margin:0;padding:0;}
  html,body{width:1920px;height:1080px;background:var(--bg);color:var(--text);
    font-family:var(--sans);overflow:hidden;}
  header{display:flex;align-items:center;justify-content:space-between;padding:20px 36px;
    border-bottom:1px solid var(--border);background:var(--panel);height:84px;}
  header h1{font-size:28px;letter-spacing:.5px;}
  .goal{font-family:var(--mono);font-size:20px;color:var(--accent);}
  .split{display:grid;grid-template-columns:1fr 1fr;height:776px;}
  .pane{padding:30px 36px;overflow:hidden;}
  .pane.left{border-right:1px solid var(--border);}
  .pane h2{font-family:var(--mono);font-size:16px;text-transform:uppercase;
    letter-spacing:3px;color:var(--muted);margin-bottom:20px;}
  .msg{background:var(--panel);border:1px solid var(--border);border-radius:14px;
    padding:26px;font-size:24px;line-height:1.6;white-space:pre-wrap;margin-bottom:22px;min-height:230px;}
  .site{border:1px solid var(--border);border-radius:14px;overflow:hidden;height:300px;}
  .site iframe{width:100%;height:100%;border:0;background:#0f1115;}
  .scoreboard{display:flex;align-items:baseline;gap:22px;margin-bottom:10px;}
  .bigscore{font-family:var(--mono);font-size:120px;font-weight:700;line-height:1;transition:color .4s;}
  .bigscore.fail{color:var(--red);} .bigscore.pass{color:var(--green);}
  .thresh{font-size:22px;color:var(--muted);}
  .verdict{font-family:var(--mono);font-size:22px;padding:7px 18px;border-radius:9px;
    margin-bottom:24px;display:inline-block;}
  .verdict.fail{background:rgba(226,100,91,.16);color:var(--red);}
  .verdict.pass{background:rgba(90,209,154,.16);color:var(--green);}
  .lines{display:flex;flex-direction:column;gap:12px;}
  .line{display:flex;align-items:center;gap:16px;background:var(--panel);border:1px solid var(--border);
    border-left:6px solid var(--border);border-radius:11px;padding:15px 18px;transition:border-color .4s,background .4s;}
  .line.pass{border-left-color:var(--green);}
  .line.fail{border-left-color:var(--red);background:rgba(226,100,91,.08);}
  .line .id{font-family:var(--mono);font-size:21px;min-width:230px;}
  .line .detail{font-size:18px;color:var(--muted);flex:1;}
  .line .pts{font-family:var(--mono);font-size:21px;min-width:84px;text-align:right;}
  .line.pass .pts{color:var(--green);} .line.fail .pts{color:var(--red);}
  .revtabs{display:flex;gap:9px;margin-bottom:20px;}
  .revtab{font-family:var(--mono);font-size:19px;padding:7px 16px;border:1px solid var(--border);
    border-radius:8px;color:var(--muted);}
  .revtab.active{color:var(--text);border-color:var(--accent);}
  .revtab.fail{color:var(--red);} .revtab.pass{color:var(--green);}
  .callout{position:absolute;top:120px;right:60px;font-family:var(--mono);font-size:30px;
    font-weight:700;padding:14px 26px;border-radius:12px;opacity:0;transition:opacity .35s;}
  .callout.show{opacity:1;}
  .callout.FAIL{background:rgba(226,100,91,.18);color:var(--red);border:2px solid var(--red);}
  .callout.PASS{background:rgba(90,209,154,.18);color:var(--green);border:2px solid var(--green);}
  .caption{position:absolute;left:0;right:0;bottom:0;height:220px;
    background:linear-gradient(180deg,rgba(11,14,19,0),rgba(11,14,19,.96) 38%);
    display:flex;flex-direction:column;justify-content:flex-end;padding:0 60px 44px;}
  .caption .t{font-size:42px;font-weight:700;line-height:1.18;max-width:1500px;}
  .caption .s{font-size:26px;color:var(--muted);margin-top:12px;max-width:1500px;line-height:1.4;}
  .progress{position:absolute;left:0;top:0;height:5px;background:var(--accent);width:0;}
</style></head>
<body>
<div class="progress" id="progress"></div>
<header>
  <div><h1>The Recovery Desk</h1></div>
  <div class="goal" id="goal">goal: 1 missed call</div>
</header>
<div class="split">
  <section class="pane left">
    <h2 id="lefthead">Work product — what the agent wrote</h2>
    <div class="msg" id="message"></div>
    <div class="site"><iframe id="site" title="recovery microsite"></iframe></div>
  </section>
  <section class="pane right">
    <h2>Self-evaluation ledger — the agent grading itself</h2>
    <div class="revtabs" id="revtabs"></div>
    <div class="scoreboard"><div class="bigscore" id="bigscore">--</div><div class="thresh" id="thresh"></div></div>
    <div class="verdict" id="verdict" style="display:none;"></div>
    <div class="lines" id="lines"></div>
  </section>
</div>
<div class="callout" id="callout"></div>
<div class="caption"><div class="t" id="cap-t"></div><div class="s" id="cap-s"></div></div>
<script>
const SB = __DATA__;
const $=(id)=>document.getElementById(id);

function ledger(domain){ return SB[domain]; }

function render(beat){
  const L = ledger(beat.domain);
  $("goal").textContent = "goal: 1 missed call · rubric: "+L.rubric+" · ship at "+L.threshold;
  $("cap-t").textContent = beat.title;
  $("cap-s").textContent = beat.sub;
  const co=$("callout");
  if(beat.callout){ co.textContent=beat.callout; co.className="callout show "+beat.callout; }
  else { co.className="callout"; }

  if(beat.rev===null){ // hook frame: ledger idle
    $("message").textContent="";
    $("site").srcdoc="";
    $("revtabs").innerHTML="";
    $("bigscore").textContent="--"; $("bigscore").className="bigscore";
    $("thresh").textContent=""; $("verdict").style.display="none"; $("lines").innerHTML="";
    return;
  }
  const revs=L.revisions, show=revs[beat.rev];
  $("revtabs").innerHTML=revs.slice(0,beat.rev+1).map((r,j)=>
    `<div class="revtab ${r.passed?'pass':'fail'} ${j===beat.rev?'active':''}">rev ${r.revision} · ${r.total}</div>`).join("");
  $("message").textContent=show.message||"";
  $("site").srcdoc=show.site_html||"";
  const sc=$("bigscore"); sc.textContent=show.total; sc.className="bigscore "+(show.passed?"pass":"fail");
  $("thresh").textContent="/ 100   (ship at "+L.threshold+")";
  const v=$("verdict"); v.style.display="inline-block";
  v.className="verdict "+(show.passed?"pass":"fail");
  v.textContent=show.passed?"PASS — agent ships this":"FAIL — agent rejects its own draft";
  $("lines").innerHTML=(show.lines||[]).map(l=>
    `<div class="line ${l.passed?'pass':'fail'}"><div class="id">${l.line_id}</div>
       <div class="detail">${l.detail||""}</div><div class="pts">${l.points}/${l.weight}</div></div>`).join("");
}

// The recorder reads window.__seek(t) to place the page at wall-clock second t.
window.__duration = SB.duration;
window.__seek = function(t){
  $("progress").style.width = (100*Math.min(t,SB.duration)/SB.duration)+"%";
  let active = SB.beats[0];
  for(const b of SB.beats){ if(t>=b.t) active=b; }
  render(active);
};
window.__seek(0);
</script></body></html>"""


def render_page(storyboard: dict) -> str:
    return _PAGE.replace("__DATA__", json.dumps(storyboard))


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def record(out_mp4: Path, fps: int, page_only: bool) -> int:
    book = capture_run(str(REPO / "rubrics" / "bookability.rubric.yaml"),
                       str(REPO / "fixtures" / "missed_call.json"))
    comp = capture_run(str(REPO / "rubrics" / "compliance.rubric.yaml"),
                       str(REPO / "fixtures" / "missed_call.json"))
    storyboard = build_storyboard(book, comp)
    html = render_page(storyboard)

    demo_dir = out_mp4.parent
    demo_dir.mkdir(parents=True, exist_ok=True)
    page_path = demo_dir / "demo-recordable.html"
    page_path.write_text(html, encoding="utf-8")
    print(f"Wrote recordable demo page -> {page_path}")
    print(f"  bookability: rev0 {book['revisions'][0]['total']} -> "
          f"rev{book['outcome']['final_revision']} {book['outcome']['final_score']}")
    print(f"  compliance : rev0 {comp['revisions'][0]['total']} -> "
          f"rev{comp['outcome']['final_revision']} {comp['outcome']['final_score']}")
    if page_only:
        return 0

    if not _have("ffmpeg"):
        print("ffmpeg not found on PATH. Install it, then re-run.\n"
              "  (winget install Gyan.FFmpeg / choco install ffmpeg / apt install ffmpeg)",
              file=sys.stderr)
        return 4
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed.\n  pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 4

    duration = storyboard["duration"]
    n_frames = duration * fps
    cover_t = next(b["t"] for b in storyboard["beats"] if b.get("callout") == "FAIL")

    with tempfile.TemporaryDirectory() as d:
        frames = Path(d)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1920, "height": 1080},
                                    device_scale_factor=1)
            page.goto(page_path.as_uri())
            page.wait_for_function("typeof window.__seek === 'function'")
            cover_frame = round(cover_t * fps)
            for i in range(n_frames):
                t = i / fps
                page.evaluate("(t) => window.__seek(t)", t)
                # let srcdoc iframe + transitions settle on beat changes
                page.wait_for_timeout(15)
                shot = frames / f"f{i:05d}.png"
                page.screenshot(path=str(shot))
                if i == cover_frame:
                    shutil.copyfile(shot, demo_dir / "cover.png")
            browser.close()

        print(f"Captured {n_frames} frames @ {fps}fps. Encoding...")
        cmd = [
            "ffmpeg", "-y", "-framerate", str(fps),
            "-i", str(frames / "f%05d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-vf", "scale=1920:1080,format=yuv420p", "-r", "30",
            "-movflags", "+faststart", str(out_mp4),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(proc.stderr[-1500:], file=sys.stderr)
            return 5

    size_mb = out_mp4.stat().st_size / 1e6
    print(f"Wrote demo video -> {out_mp4}  ({size_mb:.1f} MB, {duration}s)")
    print(f"Wrote cover frame -> {demo_dir / 'cover.png'}  (the red 66/100 self-fail still)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "demo" / "recovery-desk-demo.mp4"))
    ap.add_argument("--fps", type=int, default=int(os.environ.get("DEMO_FPS", "12")))
    ap.add_argument("--page-only", action="store_true",
                    help="Only write the recordable HTML page; skip rendering the mp4.")
    args = ap.parse_args(argv[1:])
    return record(Path(args.out), args.fps, args.page_only)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
