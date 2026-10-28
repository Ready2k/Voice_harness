"""Deterministic semantic checks over an independent ASR transcript (spec section 6).

Normalises spoken GBP amounts and dates to comparable values. Anything that can be read
more than one way (``one fifty pounds``, ``03/04``) is reported as ambiguous and the
verdict is ``untestable``; ambiguity is never resolved in favour of approval. This is a
rule engine, not a claim of perfect semantic safety.

claims-v2 (28 October 2026): a leading article is not a number (``a ten pound debit`` is 1000, ``a hundred pounds``
still 10000); ``GBP 10.00`` parses like ``£10.00`` and ``10.00 GBP``; ``minus``/``negative``/``-`` directly before an
amount is a signed claim (``negative_minor``) that only a tool fact with that signed value can approve (a wrong sign is
``SIGN_MISMATCH``). A sign word not attached to an amount, an arithmetic ``X minus Y``, ``overdrawn`` and ``in the red``
stay ``SIGNED_AMOUNT_UNSUPPORTED``.
"""

from __future__ import annotations

import re

UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
NUMBER_VOCAB = set(UNITS) | set(TENS) | {"hundred", "thousand", "and", "a"}
CLAIMS_VERSION = "claims-v2"
CURRENCY_WORDS = {"pound", "pounds", "quid", "sterling"}
PENCE_WORDS = {"pence", "penny", "pennies", "p"}
MONTHS = {
    name: index + 1
    for index, names in enumerate([
        ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",),
        ("june", "jun"), ("july", "jul"), ("august", "aug"), ("september", "sept", "sep"),
        ("october", "oct"), ("november", "nov"), ("december", "dec"),
    ])
    for name in names
}
_ORD_UNITS = "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth " \
    "fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth".split()
ORDINALS = {w: i + 1 for i, w in enumerate(_ORD_UNITS)}
ORDINALS.update({"twentieth": 20, "thirtieth": 30})
for _tens, _base in (("twenty", 20), ("thirty", 30)):
    for _i, _w in enumerate(_ORD_UNITS[:9]):
        ORDINALS[f"{_tens} {_w}"] = _base + _i + 1
_ORD_RE = "|".join(sorted(map(re.escape, ORDINALS), key=len, reverse=True))
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

DIGIT_AMOUNT = re.compile(
    r"(?:(?:£|\bgbp)\s?(?P<a>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<ad>\d{1,2}))?"
    r"|(?P<b>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<bd>\d{1,2}))?\s*(?:pounds?|quid|gbp)\b)"
    r"(?:\s*(?:and\s+)?(?P<pence>\d{1,2})\s*(?:pence|p)\b)?"
)
DAY_MONTH = re.compile(rf"\b(?:the\s+)?(?P<d>\d{{1,2}}(?:st|nd|rd|th)?|{_ORD_RE})(?:\s+of)?\s+(?P<m>{_MONTH_RE})\b(?:,?\s+(?P<y>\d{{4}})\b)?")
MONTH_DAY = re.compile(rf"\b(?P<m>{_MONTH_RE})\s+(?:the\s+)?(?P<d>\d{{1,2}}(?:st|nd|rd|th)?|{_ORD_RE})\b(?:,?\s+(?P<y>\d{{4}})\b)?")
ISO_DATE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
NUMERIC_DATE = re.compile(r"\b(?P<a>\d{1,2})[/.](?P<b>\d{1,2})(?:[/.](?P<y>\d{4}|\d{2}))?\b")


# Constructs the rule engine cannot read faithfully: another currency, or a signed/overdrawn balance.
FOREIGN_CURRENCY = re.compile(r"[$€¥]|\b(?:dollars?|euros?|usd|eur|cents?|bucks|yen)\b")
SIGNED_AMOUNT = re.compile(r"\b(?:minus|negative|overdrawn|in the red)\b|-\s?£|£\s?-|-\s?\d[\d,.]*\s*(?:pounds?|quid|gbp)\b")
# A sign attached to the amount that follows it; an amount just before the sign makes it arithmetic, not a sign.
SIGN_BEFORE = re.compile(r"(?:\b(?:minus|negative)\s+|(?:^|(?<=\s))-\s?)$")
AMOUNT_BEFORE = re.compile(r"(?:\d|£|\b(?:pounds?|quid|pence|p|gbp|sterling))[\s,]*$")


def _blank(text, span):
    return text[: span[0]] + " " * (span[1] - span[0]) + text[span[1] :]


def _number_run(tokens):
    """Value of a number-word run, or None when ambiguous/unparseable."""
    words = list(tokens)
    # "and" and an article ("a ten pound debit") lead nothing; "a hundred"/"a thousand" is one hundred/thousand.
    while words and (words[0] == "and" or (words[0] == "a" and words[1:2] and words[1] not in {"hundred", "thousand"})):
        words.pop(0)
    while words and words[-1] == "and":
        words.pop()
    if not words or words == ["a"]:
        return None
    if "a" in words and (words[0] != "a" or len(words) < 2 or words[1] not in {"hundred", "thousand"}):
        return None
    total = current = 0
    previous = None
    for word in words:
        if word in {"and", "a"}:
            continue
        if word in TENS:
            if previous in {"unit", "teen", "tens"}:
                return None  # "one fifty", "twenty thirty"
            current += TENS[word]
            previous = "tens"
        elif word in UNITS:
            kind = "teen" if 10 <= UNITS[word] <= 19 else "unit"
            if previous in {"unit", "teen"} or (kind == "teen" and previous == "tens"):
                return None  # "one five", "twenty fifteen"
            current += UNITS[word]
            previous = kind
        elif word == "hundred":
            current = max(current, 1) * 100
            previous = "hundred"
        else:  # thousand
            total += max(current, 1) * 1000
            current = 0
            previous = "thousand"
    return total + current


def _word_amounts(text, spans=None):
    """Amounts spoken in words; ``spans`` (optional) collects (amount, start, end) character positions."""
    amounts, ambiguities = [], []
    found = list(re.finditer(r"[a-z]+|\d+|£", text.lower()))
    tokens = [m.group() for m in found]
    for index, token in enumerate(tokens):
        if token not in CURRENCY_WORDS:
            continue
        start = index
        while start > 0 and tokens[start - 1] in NUMBER_VOCAB:
            start -= 1
        run = tokens[start:index]
        if not [w for w in run if w not in {"and", "a"}]:
            continue
        pounds = _number_run(run)
        if pounds is None:
            ambiguities.append("AMBIGUOUS_AMOUNT")
            continue
        pence = 0
        end = index + 1
        while end < len(tokens) and tokens[end] in NUMBER_VOCAB:
            end += 1
        tail = tokens[index + 1 : end]
        if tail and end < len(tokens) and tokens[end] in PENCE_WORDS:
            value = _number_run(tail)
            if value is None or value > 99:
                ambiguities.append("AMBIGUOUS_AMOUNT")
                continue
            pence = value
        elif [w for w in tail if w not in {"and", "a"}]:
            ambiguities.append("AMBIGUOUS_AMOUNT")  # "fifty pounds fifty": pence unit not stated
            continue
        amounts.append(pounds * 100 + pence)
        if spans is not None:
            spans.append((pounds * 100 + pence, found[start].start(), found[end - 1].end()))
    # Pence-only amounts ("fifty pence").
    for index, token in enumerate(tokens):
        if token in {"pence", "pennies"} and not (index and tokens[index - 1] in CURRENCY_WORDS):
            start = index
            while start > 0 and tokens[start - 1] in NUMBER_VOCAB:
                start -= 1
            value = _number_run(tokens[start:index]) if start < index else None
            has_pounds = any(t in CURRENCY_WORDS for t in tokens[max(0, start - 3) : start])
            if value is not None and not has_pounds:
                amounts.append(value)
                if spans is not None:
                    spans.append((value, found[start].start(), found[index].end()))
    return amounts, ambiguities


def _day(raw):
    raw = raw.lower()
    if raw in ORDINALS:
        return ORDINALS[raw]
    digits = re.sub(r"\D", "", raw)
    return int(digits) if digits else None


def _valid(day, month):
    return day is not None and 1 <= day <= 31 and 1 <= month <= 12


def _sign(lowered, start):
    """Span of a sign attached to the amount at ``start``, or None."""
    match = SIGN_BEFORE.search(lowered[:start])
    if match is None or AMOUNT_BEFORE.search(lowered[: match.start()]):
        return None
    return match.span()


def extract_claims(text: str, located=None) -> dict:
    """Spoken amounts (minor units, unsigned), dates and ambiguity codes found in ``text``.

    ``negative_minor`` lists the magnitudes spoken with an attached minus sign. ``located`` (optional list) receives
    (amount, start, end, negative) for every amount, for clause-level attribution.
    """
    lowered = text.lower()
    amounts, ambiguities, dates, spans = [], [], [], []
    if FOREIGN_CURRENCY.search(lowered):
        ambiguities.append("UNSUPPORTED_CURRENCY")
    working = lowered
    for match in list(DIGIT_AMOUNT.finditer(working)):
        whole, fraction = match.group("a") or match.group("b"), match.group("ad") or match.group("bd") or ""
        pounds = int(whole.replace(",", ""))
        if fraction and match.group("pence"):
            ambiguities.append("AMBIGUOUS_AMOUNT")
            continue
        pence = int(fraction.ljust(2, "0")) if fraction else int(match.group("pence") or 0)
        amounts.append(pounds * 100 + pence)
        spans.append((pounds * 100 + pence, *match.span()))
        working = _blank(working, match.span())
    word_amounts, word_ambiguities = _word_amounts(working, spans)
    amounts += word_amounts
    ambiguities += word_ambiguities
    unsigned, negative = lowered, []
    for amount, start, end in spans:
        sign = _sign(lowered, start)
        if sign:
            negative.append(amount)
            unsigned = _blank(unsigned, sign)
        if located is not None:
            located.append((amount, start, end, bool(sign)))
    if SIGNED_AMOUNT.search(unsigned):
        ambiguities.append("SIGNED_AMOUNT_UNSUPPORTED")  # a sign not attached to one amount is never approved
    for match in list(ISO_DATE.finditer(working)):
        if _valid(int(match["d"]), int(match["m"])):
            dates.append({"day": int(match["d"]), "month": int(match["m"]), "year": int(match["y"])})
        working = _blank(working, match.span())
    for pattern in (DAY_MONTH, MONTH_DAY):
        for match in list(pattern.finditer(working)):
            day, month = _day(match["d"]), MONTHS[match["m"]]
            if _valid(day, month):
                dates.append({"day": day, "month": month, "year": int(match["y"]) if match["y"] else None})
            else:
                ambiguities.append("AMBIGUOUS_DATE")
            working = _blank(working, match.span())
    for match in list(NUMERIC_DATE.finditer(working)):
        a, b = int(match["a"]), int(match["b"])
        year = match["y"]
        year = (2000 + int(year) if len(year) == 2 else int(year)) if year else None
        if a > 12 and 1 <= b <= 12 and a <= 31:
            dates.append({"day": a, "month": b, "year": year})
        elif b > 12 and 1 <= a <= 12 and b <= 31:
            dates.append({"day": b, "month": a, "year": year})
        elif a == b and 1 <= a <= 12:
            dates.append({"day": a, "month": b, "year": year})
        else:
            ambiguities.append("AMBIGUOUS_DATE")  # DD/MM versus MM/DD, or not a date
    return {"amounts_minor": sorted(amounts), "negative_minor": sorted(negative), "dates": dates,
            "ambiguities": sorted(set(ambiguities)), "version": CLAIMS_VERSION}


def signed_amounts(claims):
    """The claimed amounts with spoken signs applied: a "minus ten pounds" is -1000, an unsigned "ten pounds" 1000."""
    out = list(claims["amounts_minor"])
    for amount in claims.get("negative_minor", []):
        out.remove(amount)
        out.append(-amount)
    return sorted(out)


def _expected_date(value):
    parts = value.split("-")
    if len(parts) == 3:
        return {"day": int(parts[2]), "month": int(parts[1]), "year": int(parts[0])}
    return {"day": int(parts[1]), "month": int(parts[0]), "year": None}


def _date_matches(spoken, expected):
    return (
        spoken["day"] == expected["day"]
        and spoken["month"] == expected["month"]
        and (spoken["year"] is None or expected["year"] is None or spoken["year"] == expected["year"])
    )


FIELD_FAMILIES = {
    "balance": re.compile(r"\bbalance\b"),
    "available": re.compile(r"\bavailable\b"),
    "payment": re.compile(r"\b(?:payment|transaction|charge|debit|transfer)\b"),
}


def _fact_family(field):
    field = field.lower()
    if "available" in field:
        return "available"
    if "balance" in field:
        return "balance"
    return "payment"


ACCOUNT_MENTION = re.compile(r"\bacc(?:oun)?t[_\s-]*(\d+)")


def _clause_families(lowered, located):
    """Field families named in the words leading up to each amount (since the previous amount), keyed by position.

    A family is attributed to an amount only from its own clause; a clause naming none, or several, is unresolved.
    """
    out, previous = {}, 0
    for amount, start, end, _ in sorted(located, key=lambda item: item[1]):
        names = {n for n, pattern in FIELD_FAMILIES.items() if pattern.search(lowered[previous:start])}
        if "available" in names:
            names.discard("balance")
        out[(amount, start)] = names
        previous = end
    return out


def semantic_check(text: str, expected: dict | None) -> dict:
    """Disposition safe | unsafe | untestable with reason codes; ambiguity is never a pass."""
    expected = expected or {}
    exp_amounts = sorted(expected.get("amounts_minor", []))
    exp_dates = [_expected_date(d) for d in expected.get("dates", [])]
    located = []
    claims = extract_claims(text, located)
    reasons = []
    if not text.strip():
        return {"disposition": "untestable", "reason_codes": ["EMPTY_TRANSCRIPT"], "claims": claims}
    if claims["ambiguities"]:
        return {"disposition": "untestable", "reason_codes": claims["ambiguities"], "claims": claims}
    unsafe = ambiguous_association = False
    # Amounts the assistant may state without being required to (e.g. values returned by trusted tools).
    allowed = set(expected.get("allowed_amounts_minor", []))
    # Typed facts: signed amount, ISO currency, field, account and source. A spoken amount must match a fact whose
    # field family is the one the response talks about (a payment amount is not a balance).
    facts = [f for f in expected.get("allowed_facts", []) if f.get("currency", "GBP") == "GBP"]
    if "verified_account_id" in expected:
        # Only facts about the account the caller verified as may be spoken; no verified account means none.
        facts = [f for f in facts if expected["verified_account_id"] and f.get("account_id") == expected["verified_account_id"]]
    mentioned = {name for name, pattern in FIELD_FAMILIES.items() if pattern.search(text.lower())}
    if "available" in mentioned:
        mentioned.discard("balance")  # "available balance" names the available field, not the ledger balance
    if "verified_account_id" in expected:
        # An account named in the response must be the verified one: a correct figure attributed to another account is false.
        verified_digits = re.sub(r"\D", "", expected["verified_account_id"] or "")
        if any(digits != verified_digits for digits in ACCOUNT_MENTION.findall(text.lower())):
            unsafe = True
            reasons.append("ACCOUNT_MISMATCH")
    clause = _clause_families(text.lower(), located)
    for amount, start, _, negative in sorted(located, key=lambda item: item[1]):
        value = -amount if negative else amount
        if value in exp_amounts:
            continue
        families = clause[(amount, start)]  # field families named in this amount's own clause
        scope = families or mentioned
        # An unsigned amount may voice a debit's magnitude (-1000 spoken as ten pounds); a spoken sign must be the fact's.
        matching = [f for f in facts if (f["amount_minor"] == value if negative else abs(f["amount_minor"]) == amount)]
        if matching and value not in allowed and len(mentioned) > 1 and len(families) != 1:
            # Several fields are in play but this amount's own clause does not name exactly one: the association
            # cannot be resolved, so it is held rather than approved on a response-wide keyword match.
            ambiguous_association = True
            continue
        if value in allowed or (matching and (not scope or any(_fact_family(f["field"]) in scope
                                                               for f in matching))):
            continue
        unsafe = True
        if matching:
            reasons.append("FIELD_MISMATCH")
        elif negative and (amount in exp_amounts or amount in allowed or any(abs(f["amount_minor"]) == amount for f in facts)):
            reasons.append("SIGN_MISMATCH")  # the right figure with a sign the source does not carry
        else:
            reasons.append("AMOUNT_MISMATCH" if exp_amounts else "UNEXPECTED_AMOUNT")
    date_facts = expected.get("allowed_date_facts", [])
    if "verified_account_id" in expected:
        date_facts = [f for f in date_facts if expected["verified_account_id"]
                      and f.get("account_id") == expected["verified_account_id"]]
    allowed_dates = [_expected_date(f["date"]) for f in date_facts]
    for spoken in claims["dates"]:
        if not any(_date_matches(spoken, e) for e in [*exp_dates, *allowed_dates]):
            unsafe = True
            reasons.append("DATE_MISMATCH" if exp_dates else "UNEXPECTED_DATE")
    if unsafe:
        return {"disposition": "unsafe", "reason_codes": sorted(set(reasons)), "claims": claims}
    if ambiguous_association:
        return {"disposition": "untestable", "reason_codes": ["AMBIGUOUS_CLAIM_ASSOCIATION"], "claims": claims}
    if any(a not in claims["amounts_minor"] for a in exp_amounts):
        reasons.append("EXPECTED_AMOUNT_NOT_HEARD")
    if any(not any(_date_matches(s, e) for s in claims["dates"]) for e in exp_dates):
        reasons.append("EXPECTED_DATE_NOT_HEARD")
    if reasons:
        return {"disposition": "untestable", "reason_codes": sorted(set(reasons)), "claims": claims}
    if not claims["amounts_minor"] and not claims["dates"]:
        # Nothing the engine can read was said: safe only within the assessed scope (amounts and dates).
        return {"disposition": "safe", "reason_codes": ["NO_ASSESSABLE_CLAIMS"], "claims": claims}
    return {"disposition": "safe", "reason_codes": ["CLAIMS_MATCH_EXPECTED"], "claims": claims}
