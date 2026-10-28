"""Spoken-form fixture identifiers.

A caller can say "transaction txn_001" but cannot convey the underscore or the case, so a model that sends ``TXN001``,
``txn 001`` or ``txn-001`` heard the identifier correctly. The trusted bank and the bake-off argument check accept those
forms as the canonical id: case is ignored and separators (anything not a letter or digit) are dropped. The digits must
match exactly: ``txn0001`` is ``txn_0001``, a different id, because 0001 and 001 are different spoken digit strings.

A bare number (``001``, as said in "cancel payment zero zero one") is accepted only in a field that names the id type
(``payment_id``, ``transaction_id(s)``) and only when ``known`` ids of that type are supplied and exactly one of them has
exactly those digits; otherwise it keeps its form and is NOT_FOUND. With no ``known`` ids a bare number is refused.
Values that do not match keep their original form.
"""

from __future__ import annotations

import re

PREFIXES = {"transaction_id": "txn", "transaction_ids": "txn", "payment_id": "payment"}


def canonical(value, prefix, known=()):
    if not isinstance(value, str):
        return value
    flat = re.sub(r"[^a-z0-9]", "", value.lower())
    match = re.fullmatch(prefix + r"(\d+)", flat)
    if match:
        return f"{prefix}_{match.group(1)}"
    if flat.isdigit():
        hits = [k for k in known if k == f"{prefix}_{flat}"]
        return hits[0] if len(hits) == 1 else value
    return value


def canonical_argument(key, value, known=None):
    """``known``: {prefix: ids} of that type the bank holds; a bare number resolves only against those."""
    prefix = PREFIXES.get(key)
    if prefix is None:
        return value
    ids = sorted(set((known or {}).get(prefix, ())))
    return [canonical(v, prefix, ids) for v in value] if isinstance(value, list) else canonical(value, prefix, ids)


def canonical_arguments(args, known=None):
    """``args`` with every spoken-form identifier argument canonicalised; other arguments unchanged."""
    return {k: canonical_argument(k, v, known) for k, v in args.items()} if isinstance(args, dict) else args
