"""Deterministic guardrail content layer (spec section 6, PB-22/AR-02).

Pure rules over text: prohibited disclosure, unsupported financial advice,
prompt injection and secrets. The AI review layer is Phase 3 and is not here.
Actions: allow, redact (mask matched spans), replace (substitute a fixed safe
response), deny (block outright) and escalate (block and require a human).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

POLICY_ID = "banking.v1.content"
POLICY_VERSION = "1"
SAFE_REPLACEMENT = (
    "I can't give financial advice. I can explain account features or "
    "connect you with an adviser."
)
REDACTION = "[REDACTED]"

# Severity order: the strongest action across all matched rules wins.
ACTION_ORDER = ("allow", "redact", "replace", "deny", "escalate")


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    action: str
    pattern: re.Pattern
    needs_unverified: bool = False


def _r(rule_id, category, action, pattern, needs_unverified=False):
    return Rule(rule_id, category, action, re.compile(pattern, re.I), needs_unverified)


RULES = (
    # Secrets: masked in place.
    _r("SECRET_API_KEY", "secrets", "redact", r"\b(?:sk-[a-z0-9]{16,}|AKIA[0-9A-Z]{16})\b"),
    _r("SECRET_CARD_NUMBER", "secrets", "redact", r"\b(?:\d[ -]?){15}\d\b"),
    _r(
        "SECRET_CREDENTIAL", "secrets", "redact",
        r"\b(?:password|passcode|pin|security answer)\s*(?:is|was|=|:)\s*"
        r"(?!(?:set|incorrect|correct|wrong|invalid|valid|not|required|needed|locked|verified|expired|missing|"
        r"stored|on|a|an|the|your|my|still|now|being|currently|already|too|case|different|accepted)\b)\S+",
    ),
    # Prohibited disclosure: balances before identity is verified, raw identifiers always.
    _r(
        "DISCLOSURE_BALANCE_UNVERIFIED", "prohibited_disclosure", "deny",
        r"\b(?:your|the)\s+(?:account\s+)?balance\s+(?:is|stands at|was)\b[^.]*\d",
        needs_unverified=True,
    ),
    _r(
        "DISCLOSURE_ACCOUNT_IDENTIFIERS", "prohibited_disclosure", "redact",
        r"\b(?:account number|sort code)\s*(?:is|:)?\s*\d[\d -]{5,}\d\b",
    ),
    # Unsupported financial advice: replaced with a fixed safe response.
    _r(
        "ADVICE_INVESTMENT", "financial_advice", "replace",
        r"\byou should (?:definitely |really )?(?:invest|buy|sell|put your (?:money|savings))\b",
    ),
    _r("ADVICE_GUARANTEED_RETURN", "financial_advice", "replace", r"\bguaranteed (?:returns?|profit|gains?)\b"),
    # Prompt injection: stop the turn and hand to a human.
    _r(
        "INJECTION_OVERRIDE", "prompt_injection", "escalate",
        r"\b(?:ignore|disregard|forget)\b[^.]{0,30}\b(?:previous|prior|above|earlier|all)\b[^.]{0,30}\b(?:instructions?|rules|policy)\b",
    ),
    _r("INJECTION_SYSTEM_PROMPT", "prompt_injection", "escalate", r"\b(?:reveal|print|repeat|show)\b[^.]{0,30}\bsystem prompt\b"),
    _r("INJECTION_SKIP_CHECKS", "prompt_injection", "escalate", r"\b(?:skip|bypass|disable)\b[^.]{0,20}\b(?:identity|verification|security)\b"),
)


@dataclass
class ContentVerdict:
    action: str = "allow"
    reason_codes: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    text: str = ""
    redacted_fields: int = 0

    @property
    def allowed(self):
        return self.action == "allow"


def evaluate_content(text, *, identity_verified=False) -> ContentVerdict:
    """Deterministic, side-effect-free evaluation; the output text applies the action."""
    if not isinstance(text, str):
        # Unassessable content never passes the content layer.
        return ContentVerdict("deny", ["CONTENT_NOT_TEXT"], ["unassessable"], "")
    action, reasons, categories, output, masked = "allow", [], [], text, 0
    for rule in RULES:
        if rule.needs_unverified and identity_verified:
            continue
        if rule.action == "redact":
            output, count = rule.pattern.subn(REDACTION, output)
            masked += count
        else:
            count = len(rule.pattern.findall(text))
        if not count:
            continue
        reasons.append(rule.rule_id)
        if rule.category not in categories:
            categories.append(rule.category)
        if ACTION_ORDER.index(rule.action) > ACTION_ORDER.index(action):
            action = rule.action
    if action == "replace":
        output = SAFE_REPLACEMENT
    elif action in {"deny", "escalate"}:
        output = ""
    return ContentVerdict(action, reasons, categories, output, masked)
