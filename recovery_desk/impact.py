"""
recovery_desk/impact.py — recovered-revenue math for a shipped recovery.

The agent's job is to win back a job a missed call would otherwise lose. Once it
ships a passing recovery, we can put a dollar figure on it from inputs the
business already knows:

    recovered_value = avg_job_value * recovery_conversion_rate

`avg_job_value` and `recovery_conversion_rate` come from the business profile so
the number is the business's own, not a made-up benchmark. The fixture carries
conservative, real-world-typical values for a plumbing call-out; a business
plugs in its own. This is the same shape as our Stripe/funnel attribution: tie a
shipped artifact to the revenue it is responsible for.

Nothing here is invented at runtime — every figure is read from the goal record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Impact:
    avg_job_value: float
    recovery_conversion_rate: float
    recovered_value: float
    currency: str
    basis: str            # human-readable explanation of the math

    def as_dict(self) -> dict:
        return {
            "avg_job_value": round(self.avg_job_value, 2),
            "recovery_conversion_rate": self.recovery_conversion_rate,
            "recovered_value": round(self.recovered_value, 2),
            "currency": self.currency,
            "basis": self.basis,
        }


def compute_impact(goal: dict, shipped: bool) -> Optional[Impact]:
    """Return the recovered revenue for a shipped recovery, or None if the agent
    did not ship a passing recovery (no value claimed for a capped/failed run).

    Reads avg_job_value and recovery_conversion_rate from the business profile.
    Falls back to None when the business has not supplied them — we never invent
    a revenue number.
    """
    if not shipped:
        return None
    bp = goal.get("business_profile", {})
    econ = bp.get("economics", {})
    avg = econ.get("avg_job_value")
    conv = econ.get("recovery_conversion_rate")
    currency = econ.get("currency", "USD")
    if avg is None or conv is None:
        return None
    recovered = float(avg) * float(conv)
    basis = (f"avg job value {currency} {float(avg):,.0f} x "
             f"recovery conversion {float(conv) * 100:.0f}% "
             f"= {currency} {recovered:,.0f} expected per recovered call")
    return Impact(float(avg), float(conv), recovered, currency, basis)
