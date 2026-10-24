"""Shared defaults for the small OpenAI Chat Completions callers (the model simulator's chooser and the delegate
dialogue reviewer).

gpt-4.1-mini shuts down on 2027-01-14 and OpenAI names gpt-6-luna as its replacement. gpt-6-luna reasons at a
medium effort by default, which would add latency and tokens to what are short classification calls, so these callers
ask for no reasoning. The docs fetched on 2026-10-24 list Chat Completions and structured outputs for the model but do
not say that ``reasoning_effort`` or ``response_format: json_object`` are accepted together, so a 400 that names an
optional parameter is retried once without it instead of failing the call.
"""

DEFAULT_MODEL = "gpt-6-luna"
OPTIONAL_PARAMETERS = ("reasoning_effort", "temperature")


def request_body(model, system, payload_json):
    return {"model": model, "response_format": {"type": "json_object"}, "reasoning_effort": "none",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": payload_json}]}


def without_rejected(body, response) -> bool:
    """Drop the optional parameters a 400 response names; True when the body changed and the call is worth retrying."""
    if getattr(response, "status_code", None) != 400:
        return False
    text = str(getattr(response, "text", "") or "").lower()
    named = [p for p in OPTIONAL_PARAMETERS if p in body and p in text]
    for name in named:
        body.pop(name)
    return bool(named)
