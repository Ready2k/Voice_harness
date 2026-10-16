"""RFC 8785 (JSON Canonicalization Scheme) serialization for ledger hashing."""

from __future__ import annotations

import json
import math
from decimal import Decimal


def _number(value: float) -> str:
    """ECMAScript Number::toString as required by RFC 8785 section 3.2.2.3."""
    if not math.isfinite(value):
        raise ValueError("NON_FINITE_NUMBER")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    # repr() yields the shortest round-trip digits, as ECMAScript requires.
    _, digits, exponent = Decimal(repr(abs(value))).normalize().as_tuple()
    digit_string = "".join(map(str, digits))
    k = len(digit_string)
    n = exponent + k  # decimal point position relative to digit_string
    if k <= n <= 21:
        return sign + digit_string + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digit_string[:n] + "." + digit_string[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digit_string
    e = n - 1
    mantissa = digit_string[0] + ("." + digit_string[1:] if k > 1 else "")
    return f"{sign}{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"


def _encode(value, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, float):
        out.append(_number(value))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _encode(item, out)
        out.append("]")
    elif isinstance(value, dict):
        out.append("{")
        # RFC 8785 sorts member names by UTF-16 code units.
        for index, key in enumerate(sorted(value, key=lambda k: k.encode("utf-16-be"))):
            if not isinstance(key, str):
                raise TypeError("NON_STRING_KEY")
            if index:
                out.append(",")
            out.append(json.dumps(key, ensure_ascii=False))
            out.append(":")
            _encode(value[key], out)
        out.append("}")
    else:
        raise TypeError("NOT_JSON_SERIALIZABLE")


def canonical_json(value) -> bytes:
    out: list[str] = []
    _encode(value, out)
    return "".join(out).encode("utf-8")
