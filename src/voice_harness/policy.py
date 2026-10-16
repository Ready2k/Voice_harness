from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ACCOUNT_TOOLS = {
    "get_account_summary",
    "get_account_transactions",
    "create_dispute_case",
    "update_dispute_case",
    "check_credit_score",
    "manage_recent_interactions",
}


def valid_identity_digits(args: Any) -> bool:
    return (
        isinstance(args, dict)
        and isinstance(args.get("accountNumber"), str)
        and re.fullmatch(r"[0-9]{8}", args["accountNumber"]) is not None
        and isinstance(args.get("sortCode"), str)
        and re.fullmatch(r"[0-9]{6}", args["sortCode"]) is not None
    )


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str
    message: str


def authorize_tool(
    tool_name: str,
    args: Any,
    *,
    allowed_tools: set[str],
    verified: bool,
    account_number: str | None = None,
    sort_code: str | None = None,
) -> Decision:
    if tool_name not in allowed_tools:
        return Decision(
            False, "TOOL_NOT_ALLOWED", "This action is not permitted by server policy."
        )
    if tool_name == "perform_idv_check" and not valid_identity_digits(args):
        return Decision(
            False,
            "INVALID_IDENTITY_DIGITS",
            "Verification requires an eight-digit account number and six-digit sort code.",
        )
    if tool_name not in ACCOUNT_TOOLS:
        return Decision(True, "ALLOW", "Allowed by server policy.")
    if not verified:
        return Decision(
            False,
            "IDENTITY_REQUIRED",
            "Identity verification is required before accessing this account.",
        )
    if (
        not isinstance(args, dict)
        or args.get("account_id", args.get("accountNumber")) != account_number
    ):
        return Decision(
            False,
            "ACCOUNT_MISMATCH",
            "Tool account does not match the verified account.",
        )
    if "sortCode" in args and args["sortCode"] != sort_code:
        return Decision(
            False,
            "ACCOUNT_MISMATCH",
            "Tool account does not match the verified account.",
        )
    return Decision(True, "ALLOW", "Allowed after identity verification.")
