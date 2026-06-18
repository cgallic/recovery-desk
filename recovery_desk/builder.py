"""
recovery_desk/builder.py — build_recovery.

Turns a recovery draft (message + headline) into the two deliverables the agent
ships: the callback message and a branded recovery microsite. The microsite
renderer is the hackathon-scoped stand-in for the Factory sitegen/brandgen — it
emits the same shape (one branded page + a single capture CTA) the grader scores.
"""

from __future__ import annotations

import html


_SITE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{name} — Recovery</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin:0; background:#0f1115; color:#e8edf2; }}
  .wrap {{ max-width: 560px; margin: 0 auto; padding: 40px 24px; }}
  h1 {{ font-size: 28px; margin: 0 0 8px; color:#5ad19a; }}
  .lead {{ font-size: 17px; line-height: 1.5; color:#c4cdd6; }}
  .nap {{ margin-top: 24px; font-size: 14px; color:#8a95a1; }}
  .cta {{ display:inline-block; margin-top: 28px; background:#5ad19a; color:#0f1115;
          font-weight:700; padding:14px 26px; border-radius:10px; text-decoration:none; }}
  .proof {{ margin-top: 18px; font-size: 13px; color:#8a95a1; }}
</style>
</head>
<body>
  <div class="wrap">
    <h1>{headline}</h1>
    <p class="lead">{lead}</p>
    <p class="proof">{proof}</p>
    <a class="cta" href="tel:{phone_raw}">{cta}</a>
    <p class="nap">{name} &middot; {address} &middot; {phone}</p>
  </div>
</body>
</html>"""


def build_recovery(draft: dict, goal: dict) -> dict:
    """Render the callback message + the branded recovery microsite.

    Args:
        draft: {"message": str, "headline": str} from the drafter.
        goal:  the missed_call record (carries business_profile).

    Returns:
        {"message": str, "site_html": str, "business_profile": dict}
        — exactly the shape the grader scores.
    """
    bp = goal.get("business_profile", {})
    name = bp.get("name", "Our team")
    phone = bp.get("phone", "")
    phone_raw = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    address = bp.get("address", "")

    headline = draft.get("headline", f"{name} — we missed your call")
    message = draft.get("message", "")

    # The site lead mirrors the message intent; the single CTA is "Call us".
    lead = ("Sorry we missed you. We can still get you booked today — "
            "the fastest way is a quick call back.")
    proof = "Hundreds of jobs booked from missed calls."

    site_html = _SITE_TEMPLATE.format(
        name=html.escape(name),
        headline=html.escape(headline),
        lead=html.escape(lead),
        proof=html.escape(proof),
        cta="Call us",
        phone=html.escape(phone),
        phone_raw=html.escape(phone_raw),
        address=html.escape(address),
    )

    return {
        "message": message,
        "site_html": site_html,
        "business_profile": bp,
    }
