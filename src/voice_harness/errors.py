"""Spec section 8 tool-error catalogue: code, retryable, safe message (MC-06)."""

CATALOGUE = {
    "IDENTITY_REQUIRED": (False, "Identity verification is required before this action."),
    "IDENTITY_LOCKED": (False, "Identity verification is locked."),
    "NOT_FOUND": (False, "The requested item was not found."),
    "FORBIDDEN": (False, "This action is not permitted."),
    "INVALID_ARGUMENT": (False, "The tool arguments are invalid."),
    "CONFIRMATION_REQUIRED": (False, "Explicit confirmation is required."),
    "EXPIRED": (False, "The request is no longer valid."),
    "LIMIT_EXCEEDED": (False, "A limit was exceeded."),
    "RATE_LIMITED": (True, "Too many tool calls; retry later."),
    "DEPENDENCY_TIMEOUT": (True, "A dependency did not respond in time."),
    "OUTCOME_UNKNOWN": (False, "The outcome is unknown and must be reconciled before any retry."),
}

# Internal diagnostic codes are mapped onto the catalogue and never exposed as
# the public code. Unmapped codes fail closed to FORBIDDEN.
INTERNAL = {
    "TOOL_NOT_ALLOWED": "FORBIDDEN",
    "RUN_TERMINAL": "FORBIDDEN",
    "ACCOUNT_MISMATCH": "FORBIDDEN",
    "SESSION_MISMATCH": "FORBIDDEN",
    "INVALID_TOOL_RESULT": "DEPENDENCY_TIMEOUT",
    "ABANDONED": "EXPIRED",
    "CANCELLED_BEFORE_EXECUTION": "EXPIRED",
    "INTERRUPTED_BEFORE_DISPATCH": "EXPIRED",
    "RECOVERY_BEFORE_DISPATCH": "EXPIRED",
    "INVALID_IDENTITY_DIGITS": "INVALID_ARGUMENT",
}


def tool_error(internal_code):
    """Return the catalogue-constrained {code, retryable, message} for any code."""
    code = (
        internal_code
        if internal_code in CATALOGUE
        else INTERNAL.get(internal_code, "FORBIDDEN")
    )
    retryable, message = CATALOGUE[code]
    return {"code": code, "retryable": retryable, "message": message}
