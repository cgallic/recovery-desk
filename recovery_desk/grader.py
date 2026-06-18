"""
recovery_desk/grader.py — the Bookability grader. THE INVENTION.

The agent runs this on its OWN output and reads the numeric result to decide
whether to ship or redo. Each rubric line returns hard pass/fail + points, so
"improvement" is falsifiable (you can point at the exact line that moved).

Two scoring methods per line:
  - deterministic : scored here with no LLM (string/regex/JSON). Reproducible.
  - llm           : scored by Claude with a strict pass/fail prompt. An offline
                    deterministic fallback exists so the demo runs with no key.

The rubric is loaded from YAML, so the same grader scores a different domain
when you swap the file (RUBRIC=rubrics/compliance.rubric.yaml).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover - install guard
    yaml = None


# ---------------------------------------------------------------------------
# Rubric model
# ---------------------------------------------------------------------------

@dataclass
class RubricLine:
    id: str
    weight: int
    method: str            # "deterministic" | "llm"
    pass_condition: str
    fail_hint: str
    check: Optional[str] = None   # deterministic check function name


@dataclass
class Rubric:
    name: str
    domain: str
    threshold: int
    max_revisions: int
    lines: List[RubricLine]
    spam_blocklist: List[str]
    cta_markers: List[str]
    extra: Dict[str, Any]   # opt_out_markers, sensitive_patterns, etc.

    @property
    def total_weight(self) -> int:
        return sum(l.weight for l in self.lines)


def load_rubric(path: str | Path) -> Rubric:
    if yaml is None:
        raise RuntimeError("PyYAML is required: pip install pyyaml")
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    lines = [
        RubricLine(
            id=l["id"],
            weight=int(l["weight"]),
            method=l["method"],
            pass_condition=l.get("pass_condition", "").strip(),
            fail_hint=l.get("fail_hint", "").strip(),
            check=l.get("check"),
        )
        for l in data["lines"]
    ]
    known = {"name", "description", "domain", "threshold", "max_revisions",
             "lines", "spam_blocklist", "cta_markers"}
    extra = {k: v for k, v in data.items() if k not in known}
    return Rubric(
        name=data["name"],
        domain=data.get("domain", ""),
        threshold=int(data["threshold"]),
        max_revisions=int(data["max_revisions"]),
        lines=lines,
        spam_blocklist=[s.lower() for s in data.get("spam_blocklist", [])],
        cta_markers=[s.lower() for s in data.get("cta_markers", [])],
        extra=extra,
    )


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class LineResult:
    line_id: str
    weight: int
    points: int
    passed: bool
    detail: str


@dataclass
class GradeResult:
    total: int                  # 0..100, normalized to rubric total weight
    passed: bool                # total >= threshold
    threshold: int
    lines: List[LineResult]

    def failing_lines(self) -> List[LineResult]:
        return [l for l in self.lines if not l.passed]


# ---------------------------------------------------------------------------
# Deterministic checks. Each returns (passed: bool, detail: str).
# A "draft" dict has: message (str), site_html (str), business_profile (dict).
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(
    r"\b(\d{1,2}(:\d{2})?\s*(am|pm)|\d{1,2}:\d{2})\b", re.IGNORECASE
)
_DAY_RE = re.compile(
    r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b",
    re.IGNORECASE,
)


def _check_time_slot_present(draft: dict, rubric: Rubric) -> tuple[bool, str]:
    text = draft.get("message", "")
    has_time = bool(_TIME_RE.search(text))
    has_day = bool(_DAY_RE.search(text))
    if has_time and has_day:
        m = _TIME_RE.search(text)
        return True, f"specific slot offered: '{m.group(0)}'"
    if has_time:
        return True, "specific clock time offered"
    return False, "no specific time offered (only a vague 'let us know')"


def _check_nap_consistency(draft: dict, rubric: Rubric) -> tuple[bool, str]:
    bp = draft.get("business_profile", {})
    blob = (draft.get("message", "") + " " + draft.get("site_html", "")).lower()
    name = str(bp.get("name", "")).lower().strip()
    phone = str(bp.get("phone", "")).strip()
    address = str(bp.get("address", "")).lower().strip()

    # normalize phone digits for a lenient match
    phone_digits = re.sub(r"\D", "", phone)
    blob_digits = re.sub(r"\D", "", blob)

    name_ok = bool(name) and name in blob
    phone_ok = bool(phone_digits) and phone_digits[-7:] in blob_digits
    addr_ok = bool(address) and address.split(",")[0].strip() in blob  # street line

    missing = [
        label for label, ok in
        [("name", name_ok), ("phone", phone_ok), ("address", addr_ok)]
        if not ok
    ]
    if not missing:
        return True, "name + phone + address all match the profile"
    return False, f"NAP mismatch — missing/incorrect: {', '.join(missing)}"


def _check_exactly_one_cta(draft: dict, rubric: Rubric) -> tuple[bool, str]:
    # Scope to the message: the microsite carries one persistent call button by
    # design; the line guards against the *message* asking for two different
    # actions ("reply AND call AND book"), which splits the recipient's attention.
    text = draft.get("message", "").lower()
    hits = sorted({m for m in rubric.cta_markers if m in text})
    n = len(hits)
    if n == 1:
        return True, f"exactly one CTA ('{hits[0]}')"
    if n == 0:
        return False, "no call-to-action found"
    return False, f"{n} competing CTAs: {hits}"


def _check_no_spam_words(draft: dict, rubric: Rubric) -> tuple[bool, str]:
    text = draft.get("message", "").lower()
    found = [w for w in rubric.spam_blocklist if w in text]
    if not found:
        return True, "no spam-trigger words"
    return False, f"spam-trigger words present: {found}"


def _check_opt_out_present(draft: dict, rubric: Rubric) -> tuple[bool, str]:
    markers = [m.lower() for m in rubric.extra.get("opt_out_markers", [])]
    text = draft.get("message", "").lower()
    if any(m in text for m in markers):
        return True, "opt-out instruction present"
    return False, "no opt-out instruction"


def _check_no_sensitive_data(draft: dict, rubric: Rubric) -> tuple[bool, str]:
    pats = [p.lower() for p in rubric.extra.get("sensitive_patterns", [])]
    text = draft.get("message", "").lower()
    found = [p for p in pats if p in text]
    if not found:
        return True, "no sensitive data in body"
    return False, f"sensitive data present: {found}"


def _check_reading_level_ok(draft: dict, rubric: Rubric) -> tuple[bool, str]:
    text = draft.get("message", "")
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    if not sentences:
        return False, "no sentences"
    avg_words = sum(len(s.split()) for s in sentences) / len(sentences)
    if avg_words <= 18:
        return True, f"avg {avg_words:.0f} words/sentence (simple)"
    return False, f"avg {avg_words:.0f} words/sentence (too dense)"


def _check_exactly_one_h1(draft: dict, rubric: Rubric) -> tuple[bool, str]:
    n = len(re.findall(r"<h1\b", draft.get("site_html", ""), re.IGNORECASE))
    if n == 1:
        return True, "exactly one H1"
    return False, f"{n} H1 tags"


def _check_proof_density_ok(draft: dict, rubric: Rubric) -> tuple[bool, str]:
    text = draft.get("site_html", "") + " " + draft.get("message", "")
    nums = re.findall(r"\b\d[\d,.]*\b", text)
    if len(nums) >= 1:
        return True, f"{len(nums)} concrete numbers present"
    return False, "no concrete numbers/stats"


_DETERMINISTIC_CHECKS: Dict[str, Callable[[dict, Rubric], tuple[bool, str]]] = {
    "time_slot_present": _check_time_slot_present,
    "nap_consistency": _check_nap_consistency,
    "exactly_one_cta": _check_exactly_one_cta,
    "no_spam_words": _check_no_spam_words,
    "opt_out_present": _check_opt_out_present,
    "no_sensitive_data": _check_no_sensitive_data,
    "reading_level_ok": _check_reading_level_ok,
    "exactly_one_h1": _check_exactly_one_h1,
    "proof_density_ok": _check_proof_density_ok,
}


# ---------------------------------------------------------------------------
# LLM-judged lines (with offline deterministic fallback)
# ---------------------------------------------------------------------------

def _llm_fallback(line: RubricLine, draft: dict) -> tuple[bool, str]:
    """Deterministic stand-in for Claude when no key is set. Keeps the demo
    reproducible: it uses simple keyword heuristics aligned with the rubric line."""
    text = draft.get("message", "").lower()
    if line.id == "callback_sla":
        ok = any(p in text for p in ["within the hour", "within an hour", "before end of day",
                                     "by end of day", "shortly", "right away", "today"])
        return ok, ("callback promise inside SLA window" if ok else "no time-bound callback promise")
    if line.id == "objection_handled":
        ok = any(p in text for p in ["no charge", "no obligation", "upfront", "free quote",
                                     "no surprise", "flat", "today", "same day", "transparent"])
        return ok, ("top objection pre-answered" if ok else "caller's likely objection not addressed")
    if line.id == "safe_language":
        risky = any(p in text for p in ["cure", "guaranteed result", "best in the world"])
        return (not risky), ("no claim needing disclaimer" if not risky else "unqualified claim present")
    if line.id == "answer_first":
        first = text.split(".")[0] if "." in text else text
        ok = len(first.split()) <= 30
        return ok, ("answer leads" if ok else "answer buried")
    # default: pass conservatively
    return True, "passed (fallback heuristic)"


# ---------------------------------------------------------------------------
# Public grade()
# ---------------------------------------------------------------------------

def grade(
    draft: dict,
    rubric: Rubric,
    llm_judge: Optional[Callable[[RubricLine, dict], tuple[bool, str]]] = None,
) -> GradeResult:
    """Score a draft against a rubric.

    Args:
        draft: dict with message, site_html, business_profile.
        rubric: a loaded Rubric.
        llm_judge: optional callable for "llm" lines. If None, the offline
            deterministic fallback is used so the loop runs with no API key.

    Returns:
        GradeResult with total (0..100), passed, and per-line results.
    """
    judge = llm_judge or _llm_fallback
    results: List[LineResult] = []
    raw_points = 0

    for line in rubric.lines:
        if line.method == "deterministic":
            fn = _DETERMINISTIC_CHECKS.get(line.check or "")
            if fn is None:
                passed, detail = False, f"unknown check '{line.check}'"
            else:
                passed, detail = fn(draft, rubric)
        else:  # llm
            passed, detail = judge(line, draft)

        pts = line.weight if passed else 0
        raw_points += pts
        results.append(LineResult(line.id, line.weight, pts, passed, detail))

    # normalize to 0..100 so thresholds are comparable across rubrics
    total = round(100 * raw_points / rubric.total_weight) if rubric.total_weight else 0
    return GradeResult(
        total=total,
        passed=total >= rubric.threshold,
        threshold=rubric.threshold,
        lines=results,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _run_tests() -> None:
    print("GRADER SELF-TEST")
    rubric_path = Path(__file__).resolve().parent.parent / "rubrics" / "bookability.rubric.yaml"
    rubric = load_rubric(rubric_path)
    bp = {
        "name": "Bayfront Plumbing Co.",
        "phone": "+1-407-555-0100",
        "address": "212 Harbor Rd, Orlando, FL 32801",
    }

    # A deliberately weak first draft: no concrete time, vague CTA-less.
    weak = {
        "message": "Hi Dana, sorry we missed you. Let us know when works for you and we'll "
                   "try to help. Bayfront Plumbing Co., 212 Harbor Rd, Orlando, FL 32801, "
                   "call +1-407-555-0100.",
        "site_html": "<html><h1>Bayfront Plumbing Co.</h1><p>212 Harbor Rd, Orlando, FL 32801. "
                     "Phone +1-407-555-0100.</p></html>",
        "business_profile": bp,
    }
    weak_grade = grade(weak, rubric)
    print(f"  weak draft -> {weak_grade.total}/100  passed={weak_grade.passed}")
    assert not weak_grade.passed, "weak draft should fail the threshold"
    failing = {l.line_id for l in weak_grade.failing_lines()}
    assert "concrete_slot" in failing, "weak draft must fail concrete_slot"
    print(f"  [PASS] weak draft fails, including concrete_slot ({sorted(failing)})")

    # A strong revised draft: specific slot, SLA promise, objection handled, one CTA.
    strong = {
        "message": "Hi Dana, sorry we missed your call about the kitchen sink backing up. "
                   "I can have a tech out today at 2:00 PM or tomorrow at 9:00 AM. "
                   "No surprise fees — we quote upfront before any work. "
                   "Reply with the time that works and we'll lock it in. "
                   "I'll also call you back within the hour. "
                   "Bayfront Plumbing Co., 212 Harbor Rd, Orlando, FL 32801, +1-407-555-0100.",
        "site_html": "<html><h1>Bayfront Plumbing Co.</h1><p>Drain backing up? "
                     "212 Harbor Rd, Orlando, FL 32801. +1-407-555-0100. "
                     "Over 1,200 drains cleared.</p></html>",
        "business_profile": bp,
    }
    strong_grade = grade(strong, rubric)
    print(f"  strong draft -> {strong_grade.total}/100  passed={strong_grade.passed}")
    assert strong_grade.passed, "strong draft should pass the threshold"
    # the falsifiable move: concrete_slot flips fail -> pass
    weak_slot = next(l for l in weak_grade.lines if l.line_id == "concrete_slot")
    strong_slot = next(l for l in strong_grade.lines if l.line_id == "concrete_slot")
    assert weak_slot.points == 0 and strong_slot.points == 18
    print("  [PASS] strong draft passes; concrete_slot moved 0 -> 18 (falsifiable)")

    # rubric swap: compliance rubric loads and scores without code change
    comp = load_rubric(rubric_path.parent / "compliance.rubric.yaml")
    assert comp.name == "compliance" and comp.threshold == 90
    print("  [PASS] compliance rubric swaps in (separable rubric proven)")

    print("ALL GRADER TESTS PASSED")


if __name__ == "__main__":
    _run_tests()
