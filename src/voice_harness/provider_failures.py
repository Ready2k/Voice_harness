"""Safe, stable failure categories. Never put raw provider messages or credentials in reports."""

import re

ACCESS_FAILURES = {
    "PROVIDER_AUTH_MISSING", "PROVIDER_AUTH_FAILED", "PROVIDER_NOT_CONFIGURED",
    "PROVIDER_AUTH_UNSUPPORTED_CREDENTIAL_TYPE", "PROVIDER_HTTP_401", "PROVIDER_HTTP_402",
    "PROVIDER_HTTP_403", "PROVIDER_HTTP_429", "PROVIDER_QUOTA_EXCEEDED",
}
INFRA_FAILURES = ACCESS_FAILURES | {
    "PROVIDER_FAILURE", "PROVIDER_UNREACHABLE", "PROVIDER_TIMEOUT", "PROVIDER_AUTH_UNREACHABLE",
    "WORKER_FAILED", "TURN_TIMEOUT", "AUDIO_BACKPRESSURE", "RUN_NOT_FOUND", "CANCELLED",
}


def failure_code(error):
    """Allowlist diagnostics; SDK exception text can contain tokens or request bodies."""
    value = str(error)
    code = value.split(":", 1)[0]
    if code in INFRA_FAILURES or re.fullmatch(r"PROVIDER_HTTP_[45][0-9]{2}", value):
        return code
    closed = getattr(error, "rcvd", None)
    if getattr(closed, "code", None) == 3000 and getattr(closed, "reason", "").startswith("[quota_exceeded]"):
        # ElevenLabs may reject the socket before its first client message, without an error event.
        return "PROVIDER_QUOTA_EXCEEDED"
    response = getattr(error, "response", None)
    if isinstance(response, dict):  # botocore's structured error, without its message
        aws_code = (response.get("Error") or {}).get("Code")
        if aws_code in {"AccessDeniedException", "UnrecognizedClientException", "ExpiredTokenException"}:
            return "PROVIDER_AUTH_FAILED"
        if aws_code in {"ThrottlingException", "ServiceQuotaExceededException"}:
            return "PROVIDER_QUOTA_EXCEEDED"
    return "WORKER_FAILED"


def infrastructure_failure(reason):
    return reason in INFRA_FAILURES or bool(re.fullmatch(r"PROVIDER_HTTP_[45][0-9]{2}", reason or ""))


def stream_failure(errors):
    """Known provider error codes from an established session, without provider message text."""
    access = {"invalid_api_key", "authentication_error", "unauthorized", "permission_denied",
              "AccessDeniedException", "UnrecognizedClientException", "ExpiredTokenException"}
    quota = {"insufficient_quota", "quota_exceeded", "rate_limit_exceeded", "resource_exhausted", "RESOURCE_EXHAUSTED",
             "ThrottlingException", "ServiceQuotaExceededException"}
    for error in errors:
        code = error.get("code")
        if code in access:
            return "PROVIDER_AUTH_FAILED"
        if code in quota:
            return "PROVIDER_QUOTA_EXCEEDED"
    return "PROVIDER_FAILURE"
