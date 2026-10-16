"""Deterministic mandatory-criterion aggregation (v0.3)."""


def aggregate(verdicts: list[str]) -> str:
    allowed = {
        "pass",
        "fail",
        "untestable",
        "disagreement",
        "not_applicable",
        "partial",
        "evaluation_error",
        "review_required",
    }
    if any(v not in allowed for v in verdicts):
        raise ValueError("INVALID_VERDICT")
    scoped = [v for v in verdicts if v != "not_applicable"]
    if "fail" in scoped:
        return "fail"
    if any(v in {"disagreement", "review_required"} for v in scoped):
        return "review_required"
    if any(v in {"untestable", "partial", "evaluation_error"} for v in scoped):
        return "untestable"
    return "pass" if scoped else "untestable"
