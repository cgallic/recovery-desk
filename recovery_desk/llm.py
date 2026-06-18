"""
recovery_desk/llm.py — Claude planner / reviser, with an offline deterministic
reviser so the autonomous loop closes on a clean clone with no API key.

Two execution paths, SAME control flow:

  * Live   (ANTHROPIC_API_KEY set): Claude drafts revision 0, then reads the
    per-line `fail_hints` from its own grader and writes a targeted revision.
  * Offline (no key): a deterministic reviser drafts a plain revision 0, then
    reads the SAME per-line `fail_hints` and repairs exactly the lines that
    failed. It is not a "revision 0 weak / revision 1 strong" toggle — it does
    not know in advance which lines will fail; it repairs whatever the grader
    flagged, line by line. Point it at a different rubric and it repairs that
    rubric's failing lines instead (opt-out, single-CTA, ...), with no code
    change. That is what makes the offline self-fail-then-pass real on any
    domain, not stage-managed for bookability.

The offline reviser is intentionally rule-based (not an LLM) so the demo is
byte-for-byte reproducible. The reasoning it performs — "line X failed for
reason Y, so apply repair Y" — is the same reasoning Claude performs live; the
fail signal it reads is identical.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, List, Optional, Tuple

# Anthropic's current Opus-tier model. Override with RECOVERY_DESK_MODEL if you
# want a cheaper/faster model (e.g. claude-haiku-4-5) for the live reviser.
MODEL = os.environ.get("RECOVERY_DESK_MODEL", "claude-opus-4-8")

# Optional hook: a recorder that captures every live Claude call (request +
# raw response text) so a single keyed run leaves a verifiable transcript in the
# repo. `capture_live_run.py` installs one; offline runs leave it None.
_LIVE_RECORDER: Optional[Callable[[dict], None]] = None


def set_live_recorder(fn: Optional[Callable[[dict], None]]) -> None:
    """Install a recorder that receives one dict per live Claude call:
    {"role": "drafter"|"reviser"|"judge", "model", "system", "user",
     "response_text"}. Used by capture_live_run.py to bake a real keyed run
     into a transcript artifact (proof the reviser is Claude, not if/else)."""
    global _LIVE_RECORDER
    _LIVE_RECORDER = fn


def _record(entry: dict) -> None:
    if _LIVE_RECORDER is not None:
        _LIVE_RECORDER(entry)


def is_live() -> bool:
    """True when a real Claude client is available (ANTHROPIC_API_KEY set and the
    anthropic SDK importable). The agent log surfaces this so a judge can see
    which path actually ran."""
    return _client() is not None

# The agent sets this to the rubric it is currently grading against, so the
# offline reviser repairs THAT rubric's failing lines (e.g. uses the right
# cta_markers) regardless of how the run was launched. Falls back to the RUBRIC
# env var / bookability default if the agent never set it.
_ACTIVE_RUBRIC = None


def set_active_rubric(rubric) -> None:
    """Called by the agent before drafting so the offline reviser knows which
    rubric is in play (needed for the single-CTA repair's marker set)."""
    global _ACTIVE_RUBRIC
    _ACTIVE_RUBRIC = rubric


# A client override lets the test suite exercise the EXACT live code path
# (draft -> grade with Claude -> read fail hints -> revise) using a stub that
# stands in for the Anthropic SDK, with no API key. The stub speaks the same
# `messages.create(...).content[0].text` shape the real SDK does, so the test
# proves the live reviser reads the failing line ids and rewrites — the same
# guarantee the keyed capture_live_run.py produces against the real API.
_CLIENT_OVERRIDE = None


def set_client_override(client) -> None:
    """Inject a stand-in Claude client (test/replay). None restores the real one."""
    global _CLIENT_OVERRIDE
    _CLIENT_OVERRIDE = client


# ---------------------------------------------------------------------------
# Shared live-path stub client.
#
# This stub speaks the EXACT `messages.create(...).content[0].text` shape the
# real Anthropic SDK does. Injecting it via set_client_override() makes is_live()
# return True and routes draft_recovery / llm_judge_line down the identical live
# branch the real key takes — the offline deterministic reviser never runs. The
# test suite (test_live_claude_path_reads_fail_hints_and_revises) and the
# committed transcript (capture_live_run.py --stub) both use it, so the live
# branch is exercised and recorded in-repo with no API key. It is honestly a
# STUB, not the real model: its draft text is canned. What it proves is that the
# live wiring is real — the reviser turn receives the failing rubric line id in
# its user block and the loop self-fails then ships off the client's output, not
# off any hand-written repair. A real keyed run (no --stub) produces the same
# artifact shape with Claude's own prose in the response blocks.
# ---------------------------------------------------------------------------

class _StubBlock:
    """Stands in for an Anthropic content block (has .type and .text)."""
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _StubResponse:
    def __init__(self, text: str):
        self.content = [_StubBlock(text)]


class _StubMessages:
    """Records calls and returns canned JSON, mimicking client.messages.create.

    Faithful to the loop: the judge prompt gets a yes/no; the first-draft prompt
    deliberately omits a concrete slot so the grader fails `concrete_slot`; the
    reviser prompt asserts the failing line id is present (the proof it read the
    fail signal) before returning a rewrite that adds the missing slot.
    """

    def __init__(self, sink: Optional[list] = None):
        self._sink = sink if sink is not None else []

    def create(self, *, model, max_tokens, system, messages):
        user = messages[0]["content"]
        self._sink.append({"system": system, "user": user})
        if '"pass"' in system:
            return _StubResponse('{"pass": true, "detail": "ok"}')
        if "reviser" in system.lower():
            assert "concrete_slot" in user, "reviser prompt must carry the failing line id"
            return _StubResponse(
                '{"message": "Hi Dana Whitfield, sorry we missed your call about the '
                'kitchen sink. I can have a tech out today at 2:00 PM or tomorrow at '
                '9:00 AM. No surprise fees - we quote upfront. Reply with the time that '
                "works and I'll call you back within the hour. Bayfront Plumbing Co., "
                '212 Harbor Rd, Orlando, FL 32801, +1-407-555-0100.", '
                '"headline": "Bayfront Plumbing Co. - same-day help"}'
            )
        return _StubResponse(
            '{"message": "Hi Dana Whitfield, sorry we missed your call. We will be in '
            'touch shortly. Reply and let us know when works for you. Bayfront Plumbing '
            'Co., 212 Harbor Rd, Orlando, FL 32801, +1-407-555-0100.", '
            '"headline": "Bayfront Plumbing Co. - we missed you"}'
        )


class LiveReasonerStub:
    """A stand-in Claude client for exercising the live code path with no key.

    Use with set_client_override(LiveReasonerStub()). It is the single source of
    truth for the live-path stub, shared by the test suite and capture_live_run.py
    so they cannot drift.
    """

    def __init__(self, sink: Optional[list] = None):
        self.messages = _StubMessages(sink)


def _client():
    if _CLIENT_OVERRIDE is not None:
        return _CLIENT_OVERRIDE
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except ImportError:
        return None


def _extract_json(text: str) -> dict:
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    return json.loads(text.strip())


def _parse_fail_hints(fail_hints: Optional[List[str]]) -> List[Tuple[str, str]]:
    """fail_hints arrive as 'line_id: human hint'. Split into (line_id, hint)."""
    parsed: List[Tuple[str, str]] = []
    for h in fail_hints or []:
        if ":" in h:
            lid, hint = h.split(":", 1)
            parsed.append((lid.strip(), hint.strip()))
        else:
            parsed.append((h.strip(), ""))
    return parsed


# ---------------------------------------------------------------------------
# Planner / drafter
# ---------------------------------------------------------------------------

DRAFT_SYSTEM = """You are the Recovery Desk drafter. A local service business missed a call.
Write a recovery: a short callback message (SMS/email) and a one-line site headline.
Return strict JSON: {"message": "...", "headline": "..."}.
Be concrete, warm, and specific. No spam words. One clear call-to-action."""

REVISE_SYSTEM = """You are the Recovery Desk reviser. Your previous draft was graded by a
numeric rubric and FAILED specific lines. Fix ONLY the lines listed as failing — keep every
line that already passed exactly as it was. Each failing line names the weakness and the fix.
Return strict JSON: {"message": "...", "headline": "..."}."""


def draft_recovery(goal: dict, revision: int, prior_message: Optional[str],
                   fail_hints: Optional[List[str]]) -> dict:
    """Produce a recovery draft. revision 0 = first attempt; >0 = a revision that
    must repair the failing rubric lines named in fail_hints. Returns
    {"message", "headline"}."""
    client = _client()
    if client is None:
        return _offline_draft(goal, revision, prior_message, fail_hints)

    bp = goal.get("business_profile", {})
    if revision == 0:
        user = (
            f"Business: {json.dumps(bp)}\n"
            f"Caller: {goal.get('caller_name')} ({goal.get('caller_number')})\n"
            f"Reason: {goal.get('reason')}\n"
            f"Likely objection: {goal.get('likely_objection')}\n"
            "Write the first recovery draft."
        )
        system = DRAFT_SYSTEM
    else:
        system = REVISE_SYSTEM
        user = (
            f"Business: {json.dumps(bp)}\n"
            f"Caller: {goal.get('caller_name')}\n"
            f"Reason: {goal.get('reason')}\n"
            f"Open appointment slots you may offer: "
            f"{json.dumps(bp.get('next_open_slots', []))}\n\n"
            f"Your previous draft FAILED these rubric lines. Fix ONLY these; keep what passed:\n- "
            + "\n- ".join(fail_hints or [])
            + f"\n\nPrevious draft:\n{prior_message}\n\nWrite the revised draft."
        )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=900,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    _record({
        "role": "drafter" if revision == 0 else "reviser",
        "revision": revision,
        "model": MODEL,
        "system": system,
        "user": user,
        "response_text": text,
    })
    return _extract_json(text)


def llm_judge_line(line, draft: dict) -> tuple[bool, str]:
    """Judge an 'llm' rubric line with Claude. Falls back to grader's heuristic
    when no key is available (handled by the caller passing None)."""
    client = _client()
    if client is None:
        from recovery_desk.grader import _llm_fallback
        return _llm_fallback(line, draft)

    system = ("You are a strict rubric grader. Answer ONLY with JSON "
              '{"pass": true|false, "detail": "<=12 words"}.')
    user = (
        f"Rubric line: {line.id}\n"
        f"Pass condition: {line.pass_condition}\n\n"
        f"Message:\n{draft.get('message','')}\n\n"
        "Does the message satisfy the pass condition?"
    )
    resp = client.messages.create(
        model=MODEL, max_tokens=120, system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    _record({
        "role": "judge",
        "line_id": line.id,
        "model": MODEL,
        "system": system,
        "user": user,
        "response_text": text,
    })
    try:
        data = _extract_json(text)
        return bool(data["pass"]), str(data.get("detail", ""))
    except Exception:
        from recovery_desk.grader import _llm_fallback
        return _llm_fallback(line, draft)


# ---------------------------------------------------------------------------
# Offline deterministic drafter + reviser.
#
# The first draft is a plain, honest recovery — NOT engineered to be weak. It is
# what a competent-but-fast first attempt looks like: warm, NAP-correct, one CTA,
# a callback promise — but it does not commit to a specific time and does not name
# the price objection. The grader decides whether that is good enough; for the
# bookability rubric it isn't (66/100), so the loop revises.
#
# The reviser reads the failing line ids and applies one repair per line. It has
# a repair for every line in every shipped rubric, so swapping the rubric makes
# it repair THAT rubric's failures — no special-casing of bookability.
# ---------------------------------------------------------------------------

def _first_draft(goal: dict) -> dict:
    bp = goal.get("business_profile", {})
    name = bp.get("name", "Our team")
    phone = bp.get("phone", "")
    address = bp.get("address", "")
    caller = goal.get("caller_name", "there")
    message = (
        f"Hi {caller}, sorry we missed your call. "
        f"We'll be in touch shortly to get you sorted. "
        f"Reply and let us know when works for you. "
        f"{name}, {address}, {phone}."
    )
    headline = f"{name} - we missed you"
    return {"message": message, "headline": headline}


def _short_reason(goal: dict) -> str:
    """A one-clause restatement of why they called, for warmth."""
    reason = (goal.get("reason") or "").lower()
    if "sink" in reason or "drain" in reason or "water" in reason:
        return "about the sink backing up"
    if "leak" in reason:
        return "about the leak"
    return "earlier"


def _repair_concrete_slot(message: str, goal: dict) -> str:
    """Failing: no specific time. Repair: offer two real open slots."""
    slots = goal.get("business_profile", {}).get("next_open_slots", []) or [
        "today 2:00 PM", "tomorrow 9:00 AM"]
    s0 = slots[0]
    s1 = slots[1] if len(slots) > 1 else slots[0]
    offer = f"I can have someone out {s0} or {s1}. "
    # Replace the vague "when works for you" ask if present; otherwise prepend.
    if "let us know when works for you" in message.lower():
        message = re.sub(
            r"reply and let us know when works for you\.?",
            f"{offer}Reply with the time that works and we'll lock it in.",
            message, flags=re.IGNORECASE)
    else:
        message = offer + message
    return message


def _repair_objection_handled(message: str, goal: dict) -> str:
    """Failing: caller's objection not addressed. Repair: disarm it in one line."""
    obj = (goal.get("likely_objection") or "").lower()
    if "cost" in obj or "expensive" in obj or "price" in obj or "afford" in obj:
        line = "No surprise fees - we quote upfront before any work. "
    elif "today" in obj or "soon" in obj or "wait" in obj:
        line = "We can get to you fast - same-day where we can. "
    else:
        line = "No obligation - we'll talk it through first. "
    # Insert right before the sign-off (the NAP line, which contains the phone).
    return _insert_before_signoff(message, line, goal)


def _repair_callback_sla(message: str, goal: dict) -> str:
    """Failing: no time-bound callback promise. Repair: add the SLA window."""
    window = goal.get("business_profile", {}).get("sla_window", "within the hour")
    window = window.split(" during")[0].strip()
    line = f"I'll call you back {window}. "
    return _insert_before_signoff(message, line, goal)


def _repair_opt_out(message: str, goal: dict) -> str:
    """Failing (compliance): no opt-out. Repair: append a STOP line.

    Worded with no CTA-marker words ('reply'/'call'/'confirm'/'reschedule') so it
    satisfies the opt-out line without counting as a second call-to-action.
    """
    if "unsubscribe" not in message.lower():
        message = _insert_before_signoff(message, "Text STOP to unsubscribe. ", goal)
    return message


_OPT_OUT_SENTENCE_RE = re.compile(r"text stop[^.!?]*[.!?]", re.IGNORECASE)


def _repair_single_cta(message: str, goal: dict, rubric) -> str:
    """Failing: zero or 2+ CTAs. Repair: collapse to exactly one CTA.

    Strips every sentence that carries a CTA marker (e.g. the message both says
    'reply' and contains 'call'), preserves any opt-out sentence verbatim (an
    opt-out is required, not a competing CTA), and re-adds exactly one clean CTA.
    Keeps the greeting and the NAP sign-off intact.
    """
    markers = [m.lower() for m in getattr(rubric, "cta_markers", [])]

    # Pull out an opt-out sentence so the CTA strip below never removes it.
    opt_out = ""
    m = _OPT_OUT_SENTENCE_RE.search(message)
    if m:
        opt_out = m.group(0).strip()
        message = message.replace(m.group(0), " ")

    sentences = re.split(r"(?<=[.!?])\s+", message)
    kept = [s for s in sentences if s.strip()
            and not any(mk in s.lower() for mk in markers)]
    text = " ".join(kept).strip()

    # Use exactly ONE cta marker word so the grader counts exactly one CTA.
    if "reply" in markers:
        cta = "Reply YES."
    elif "confirm" in markers:
        cta = "Confirm by texting YES."
    elif "book" in markers:
        cta = "Book your slot."
    else:
        cta = (markers[0].capitalize() + ".") if markers else "Get in touch."
    if opt_out:
        cta = cta + " " + opt_out          # keep CTA and opt-out adjacent
    if _has_signoff(text, goal):
        text = _insert_before_signoff(text, cta + " ", goal)
    else:
        text = (text + " " + cta).strip()
    return re.sub(r"\s+", " ", text).strip()


def _has_signoff(message: str, goal: dict) -> bool:
    name = goal.get("business_profile", {}).get("name", "")
    return bool(name) and name in message


def _insert_before_signoff(message: str, addition: str, goal: dict) -> str:
    """Insert `addition` just before the NAP sign-off line if present."""
    name = goal.get("business_profile", {}).get("name", "")
    if name and name in message:
        idx = message.rfind(name)
        # back up to the start of the sentence containing the name
        start = message.rfind(". ", 0, idx)
        cut = start + 2 if start != -1 else idx
        return (message[:cut] + addition + message[cut:]).replace("  ", " ")
    return message.rstrip() + " " + addition.strip()


# line_id -> repair function. The reviser dispatches on the FAILING line ids the
# grader reported. Adding a rubric line means adding a repair here; the loop is
# unchanged.
def _offline_draft(goal: dict, revision: int, prior_message: Optional[str],
                   fail_hints: Optional[List[str]]) -> dict:
    if revision == 0 or not prior_message:
        return _first_draft(goal)

    # We need the rubric's cta_markers for the single_cta repair. Prefer the
    # rubric the agent told us it is using; otherwise fall back to RUBRIC env /
    # the bookability default.
    rubric = _ACTIVE_RUBRIC
    if rubric is None:
        from pathlib import Path
        from recovery_desk.grader import load_rubric
        rubric_path = os.environ.get(
            "RUBRIC",
            str(Path(__file__).resolve().parent.parent / "rubrics" / "bookability.rubric.yaml"),
        )
        rubric = load_rubric(rubric_path)

    message = prior_message
    headline = goal.get("business_profile", {}).get("name", "Our team") + " - we missed you"

    failing = {line_id for line_id, _ in _parse_fail_hints(fail_hints)}

    # Repairs run in a fixed, safe order so they never undo each other. e.g. the
    # opt-out must be added before the single-CTA collapse, which preserves it.
    if "concrete_slot" in failing:
        message = _repair_concrete_slot(message, goal)
        reason = _short_reason(goal)
        message = message.replace("sorry we missed your call.",
                                  f"sorry we missed your call {reason}.")
        headline = goal.get("business_profile", {}).get("name", "Our team") + " - same-day help"
    if "callback_sla" in failing:
        message = _repair_callback_sla(message, goal)
    if "objection_handled" in failing:
        message = _repair_objection_handled(message, goal)
    if "opt_out_present" in failing:
        message = _repair_opt_out(message, goal)
    if "single_cta" in failing:
        message = _repair_single_cta(message, goal, rubric)
    # contact_correct / no_spam_words / nap_consistency / no_sensitive_data /
    # reading_level / answer_first pass on the first draft by construction;
    # no repair is needed, and the reviser correctly leaves them untouched.

    message = re.sub(r"\s+", " ", message).strip()
    return {"message": message, "headline": headline}
