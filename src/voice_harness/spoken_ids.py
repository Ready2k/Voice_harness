"""Spoken-form fixture identifiers.

A caller can say "transaction txn_001" but cannot convey the underscore or the case, so a model that sends ``TXN001``,
``txn 001`` or ``txn-001`` heard the identifier correctly. The trusted bank and the bake-off argument check accept those
forms as the canonical id: case is ignored and separators (anything not a letter or digit) are dropped. The digits must
match exactly: ``txn0001`` is ``txn_0001``, a different id, because 0001 and 001 are different spoken digit strings.
A bare number with no prefix (``001``) is not accepted. Values that do not match keep their original form.
"""

from __future__ import annotations

import re

PREFIXES = {"transaction_id": "txn", "transaction_ids": "txn", "payment_id": "payment"}


def canonical(value, prefix):
    if not isinstance(value, str):
        return value
    match = re.fullmatch(prefix + r"(\d+)", re.sub(r"[^a-z0-9]", "", value.lower()))
    return f"{prefix}_{match.group(1)}" if match else value


def canonical_argument(key, value):
    prefix = PREFIXES.get(key)
    if prefix is None:
        return value
    return [canonical(v, prefix) for v in value] if isinstance(value, list) else canonical(value, prefix)


def canonical_arguments(args):
    """``args`` with every spoken-form identifier argument canonicalised; other arguments unchanged."""
    return {k: canonical_argument(k, v) for k, v in args.items()} if isinstance(args, dict) else args
