"""Ticket classification, urgency scoring and mock actions.

Classification and urgency are deliberately separate axes:
  * category  -> WHAT we do with the ticket (which action, or escalate)
  * urgency   -> WHEN we do it (queue order)
"My account was compromised" is critical urgency AND an escalation; neither
decision depends on the other.
"""
from __future__ import annotations

import re
from typing import Callable

# --------------------------------------------------------------------------
# Guardrails: checked before classification. If any match, we escalate even
# when a category would otherwise match (e.g. "hacked, please reset my
# password" must NOT trigger an automatic password reset).
# --------------------------------------------------------------------------
SECURITY_PATTERN = (
    r"\b(compromised|hacked|unauthori[sz]ed|breach\w*|stolen|phishing|fraud\w*"
    r"|someone else (logged|is using|has access))\b"
)
GUARDRAILS: list[tuple[str, str]] = [
    (SECURITY_PATTERN,
     "Possible security incident. Routed to a human rather than auto-resetting "
     "credentials or touching the account."),
    (r"\b(chargeback|lawyer|legal action|sue|lawsuit)\b",
     "Legal or chargeback language. Needs a human."),
]

# --------------------------------------------------------------------------
# Classification: weighted patterns. Strong, specific phrases score 3;
# supporting words score 1-2. A generic word alone ("password", "app")
# is never enough to act on.
# --------------------------------------------------------------------------
CATEGORY_PATTERNS: dict[str, list[tuple[str, int]]] = {
    "password_reset": [
        (r"\b(reset|change|recover)\w* (my |the |our )?password\b", 3),
        (r"\bforgot(ten)? (my |the )?password\b", 3),
        (r"\bpassword reset\b", 3),
        (r"\b(can'?t|cannot|unable to) (log ?in|sign ?in|login)\b", 2),
        (r"\blocked out\b", 2),
        (r"\bpassword\b", 1),
    ],
    "billing_status": [
        (r"\bbilling status\b", 3),
        (r"\b(invoice|invoiced|invoices)\b", 2),
        (r"\b(bill|billed|billing)\b", 2),
        (r"\b(charge|charged|charges)\b", 2),
        (r"\brefund\w*\b", 2),
        (r"\bpayments?\b", 2),
        (r"\b(subscription|receipt|renewal)\b", 1),
    ],
    "service_restart": [
        (r"\b(restart|reboot)\w*\b", 3),
        (r"\b(not responding|unresponsive|hung|frozen)\b", 2),
        (r"\b(crash\w*|outage)\b", 2),
        (r"\b50[0234]\b", 2),
        (r"\b(is|went|goes|keeps going) down\b", 2),
        (r"\b(server|service|instance|app|api)\b", 1),
    ],
}

MIN_SCORE = 2   # top category must reach this to be acted on
MIN_MARGIN = 2  # ...and beat the runner-up by at least this much


def _score(text: str) -> tuple[dict[str, int], dict[str, list[str]]]:
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for category, patterns in CATEGORY_PATTERNS.items():
        scores[category] = 0
        hits[category] = []
        for pattern, weight in patterns:
            m = re.search(pattern, text)
            if m:
                scores[category] += weight
                hits[category].append(m.group(0))
    return scores, hits


def classify(text: str) -> dict:
    """Return {decision: 'action'|'escalate', category, reason, scores, matched}."""
    t = text.lower()
    scores, hits = _score(t)
    base = {"scores": scores, "matched": {k: v for k, v in hits.items() if v}}

    for pattern, reason in GUARDRAILS:
        if re.search(pattern, t):
            return {**base, "decision": "escalate", "category": None, "reason": reason}

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (top, top_score), (second, second_score) = ranked[0], ranked[1]

    if top_score < MIN_SCORE:
        return {**base, "decision": "escalate", "category": None,
                "reason": "Doesn't clearly match billing, password reset or service "
                          "restart. Not guessing."}
    if top_score - second_score < MIN_MARGIN:
        return {**base, "decision": "escalate", "category": None,
                "reason": f"Ambiguous: looks like both {top} and {second}. "
                          "A human should decide which to act on."}

    return {**base, "decision": "action", "category": top,
            "reason": f"Matched: {', '.join(repr(h) for h in hits[top])}"}


# --------------------------------------------------------------------------
# Urgency: first matching level wins, checked most-severe first.
# --------------------------------------------------------------------------
LEVELS = {"critical": 0, "high": 1, "normal": 2, "low": 3}

URGENCY_RULES: list[tuple[str, str, str]] = [
    ("critical", SECURITY_PATTERN, "Security language"),
    ("high",
     r"\b(outage|production|prod|all (our )?users|everyone|urgent|asap|immediately"
     r"|(is|went) down|locked out|can'?t access|charged twice|double charged"
     r"|losing (money|revenue|customers))\b",
     "Outage, lockout or money-impacting language"),
    ("low",
     r"\b(how (do|can|would) i|just wondering|curious|when you get a chance|no rush"
     r"|feature request|question about)\b",
     "Informational question"),
]


def score_urgency(text: str) -> tuple[str, str]:
    t = text.lower()
    for level, pattern, label in URGENCY_RULES:
        m = re.search(pattern, t)
        if m:
            return level, f"{label} ({m.group(0)!r})"
    return "normal", "No urgency signals"


# --------------------------------------------------------------------------
# Mock integrations. Pretend these call real billing / IdP / infra APIs.
# --------------------------------------------------------------------------
def check_billing_status(ticket_id: str) -> str:
    return f"Billing system: account in good standing, last invoice paid, no failed charges (ref BIL-{ticket_id})."


def send_password_reset(ticket_id: str) -> str:
    return f"Identity provider: reset link sent to the email on file, expires in 30 min (ref PWD-{ticket_id})."


def restart_service(ticket_id: str) -> str:
    return f"Infra: rolling restart issued, health checks green (ref OPS-{ticket_id})."


ACTIONS: dict[str, Callable[[str], str]] = {
    "billing_status": check_billing_status,
    "password_reset": send_password_reset,
    "service_restart": restart_service,
}


def handle_ticket(ticket_id: str, text: str) -> dict:
    """Classify, then run the action. An action failure becomes an escalation."""
    result = classify(text)
    result["action"] = None
    result["action_result"] = None
    if result["decision"] == "action":
        action = ACTIONS[result["category"]]
        result["action"] = action.__name__
        try:
            result["action_result"] = action(ticket_id)
        except Exception as exc:  # real integrations fail; don't lose the ticket
            result.update(decision="escalate",
                          reason=f"{action.__name__} failed ({exc}). Needs a human.")
    return result
