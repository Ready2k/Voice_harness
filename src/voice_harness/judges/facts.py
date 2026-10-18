"""Non-amount spoken facts for live FACT-01: account digits, payment/transaction ids and dates.

Spoken text (the independent ASR transcript) is compared with values a trusted tool *returned* earlier in the
ledger.  Anything ambiguous (number words, short digit runs, ambiguous dates) or without a returned source is
reported as a gap, never as a match, and a contradiction is a failure whatever else is unresolved.
"""

from __future__ import annotations

import re

from ..semantic_checks import _date_matches, _expected_date, extract_claims

_DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_AMBIGUOUS_WORDS = {"ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
                    "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty",
                    "ninety", "hundred", "thousand", "double", "triple"}
_CURRENCY = {"pound", "pounds", "pence", "penny", "gbp", "quid", "p"}
_TOKEN = re.compile(r"[a-z]+|\d+")
_ACCOUNT_CUE = re.compile(
    r"(?:\bending(?:\s+(?:in|with))?|\bends\s+(?:in|with)|\blast\s+(?:\w+\s+)?digits?(?:\s+(?:are|is))?"
    r"|\bacc(?:oun)?t[_\s-]*(?:number|id|no)?(?:\s+(?:is|ends))?[_\s:-]*)\s*")
_ID_CUE = re.compile(r"\b(payment|transaction|txn)(?:[_\s-]*(?:id|number|reference|ref))?[_\s:-]*(?:is\s+)?")
_KINDS = {"payment": "payment", "transaction": "txn", "txn": "txn"}
_LABELS = {"account": "ACCOUNT_DIGITS", "payment": "PAYMENT_ID", "txn": "TRANSACTION_ID"}


def _digit_run(text, start):
    """Digits spoken in a run from ``start``: (digits, ambiguous, end); digits is None when there is no run."""
    digits, ambiguous, end = "", False, start
    for token in _TOKEN.finditer(text, start):
        if token.start() - end > 3:
            break  # punctuation or a gap: the run ended
        word = token.group()
        if word.isdigit():
            digits += word
        elif word in _DIGIT_WORDS:
            digits += _DIGIT_WORDS[word]
        elif word in _AMBIGUOUS_WORDS:
            ambiguous = True  # "ten oh one", "double zero": the digit string is not reliably recoverable
        else:
            break
        end = token.end()
    return (digits or ambiguous or None) and digits, ambiguous, end


def _currency_follows(text, end):
    nxt = _TOKEN.search(text, end)
    return bool(nxt and nxt.start() - end <= 2 and nxt.group() in _CURRENCY)


def spoken_facts(text):
    """(claims, gaps): claims are (kind, digits) for account/payment/txn and ("date", {...}) for dates."""
    lowered = text.lower()
    claims, gaps = [], []
    cues = [(m, "account") for m in _ACCOUNT_CUE.finditer(lowered)] + \
           [(m, _KINDS[m.group(1)]) for m in _ID_CUE.finditer(lowered)]
    for cue, kind in cues:
        digits, ambiguous, end = _digit_run(lowered, cue.end())
        if digits is None or _currency_follows(lowered, end):
            continue  # nothing numeric was claimed, or it was an amount (the amount check handles those)
        if ambiguous or len(digits) < 2:
            gaps.append("FACT_ID_AMBIGUOUS")
        else:
            claims.append((kind, digits))
    found = extract_claims(text)
    claims += [("date", d) for d in found["dates"]]
    if "AMBIGUOUS_DATE" in found["ambiguities"]:
        gaps.append("DATE_AMBIGUOUS")
    return claims, gaps


def _walk(value, key, out):
    if isinstance(value, dict):
        for k, v in value.items():
            _walk(v, k, out)
    elif isinstance(value, list):
        for v in value:
            _walk(v, key, out)
    elif isinstance(value, str):
        if key == "account_id":
            out["account"].add(re.sub(r"\D", "", value))
        elif key in ("payment_id", "transaction_id") or re.match(r"^(payment|txn)_\d+$", value):
            out["payment" if value.startswith("payment") else "txn"].add(re.sub(r"\D", "", value))
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
        if key and match and re.search(r"(_at|date|as_of|until)$", key):
            out["date"].append(_expected_date(match.group(0)))


def returned_facts(ctx, before_sequence):
    """Values trusted tools returned (successful, executed) before ``before_sequence``."""
    out = {"account": set(), "payment": set(), "txn": set(), "date": []}
    for call in ctx.calls().values():
        done = call.get("completed")
        if done and done["content"].get("status") == "success" and call.get("result") \
                and done["sequence"] < before_sequence:
            _walk(call["result"], None, out)
    out["account"].discard("")
    return out


def check_unit(text, facts):
    """(failure codes, gap codes, verified count) for one independent transcript."""
    claims, gaps = spoken_facts(text)
    failures, verified = [], 0
    for kind, value in claims:
        if kind == "date":
            if not facts["date"]:
                gaps.append("DATE_SOURCE_UNAVAILABLE")
            elif any(_date_matches(value, d) for d in facts["date"]):
                verified += 1
            else:
                failures.append("DATE_MISMATCH")
            continue
        known = facts[kind]
        if not known:
            gaps.append(_LABELS[kind] + "_SOURCE_UNAVAILABLE")
        elif kind == "account":
            verified, failures = (verified + 1, failures) if any(a.endswith(value) for a in known) \
                else (verified, failures + ["ACCOUNT_DIGITS_MISMATCH"])
        elif any(k.lstrip("0") == value.lstrip("0") for k in known):
            verified += 1
        else:
            failures.append(_LABELS[kind] + "_MISMATCH")
    return failures, gaps, verified
