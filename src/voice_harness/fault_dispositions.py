"""Typed dispositions for scenario-injected faults (``fault.disposition`` ledger events).

A declared fault is one of four things, and assurance must not confuse them:

* ``handled``       the fault was injected and the model/platform coped with it;
* ``not_exercised`` the fault never took effect (the faulted tool was never called, nobody was interrupted, the
                    fault kind is not implemented), so the scenario proved nothing about it;
* ``not_handled``   the fault took effect and the run could not cope (turn timeout, provider failure, a failed call);
* ``misconduct``    the fault took effect and the model fabricated or released what it must not have.

``classify`` reads only the ledger plus runner counters; ``apply_overlay`` folds the dispositions into verdicts so
that an unexercised fault can never be a pass and misconduct or an unhandled fault can never be hidden by a gap.
"""

from __future__ import annotations

from .semantic_checks import extract_claims

DISPOSITIONS = ("handled", "not_exercised", "not_handled", "misconduct")
IMPLEMENTED = {"tool_error", "tool_latency", "caller_interrupts", "background_noise"}
_FAILED_TURNS = {"provider_error", "stream_ended", "turn_timeout"}
_CODES = {"not_exercised": "FAULT_NOT_EXERCISED", "not_handled": "FAULT_NOT_HANDLED", "misconduct": "FAULT_MISCONDUCT"}


def _of(events, *types):
    return [e for e in events if e["type"] in types]


def _spoken_amounts(events):
    """Generated units whose independent transcript states an amount."""
    revised = {e["content"]["unit_id"]: e["content"]["new"] for e in _of(events, "transcript.revised")
               if e["content"].get("authoritative") and e["content"].get("source") == "independent_asr"}
    return [e for e in _of(events, "audio.output")
            if extract_claims(revised.get(e["content"].get("unit_id"), ""))["amounts_minor"]]


def _summary_succeeded(events):
    calls = {e["content"].get("call_id"): e["content"].get("tool") for e in _of(events, "tool.requested")}
    return any(calls.get(e["content"].get("call_id")) == "get_account_summary" and e["content"].get("status") == "success"
               for e in _of(events, "tool.completed"))


def _entry(fault, disposition, reasons, evidence=()):
    return {"fault_kind": fault["kind"], "at_turn": fault.get("at_turn") or 0, "tool": fault.get("tool"),
            "disposition": disposition, "reason_codes": list(reasons), "evidence_event_ids": list(dict.fromkeys(evidence))}


def classify(faults, events, *, outcomes=(), tool_calls=()):
    """One typed disposition per declared fault."""
    failed_turn = any(o.get("strict_speech_approval") in _FAILED_TURNS for o in outcomes)
    results = []
    for fault in faults:
        kind, tool = fault["kind"], fault.get("tool")
        if kind not in IMPLEMENTED:
            results.append(_entry(fault, "not_exercised", ["FAULT_KIND_NOT_IMPLEMENTED"]))
        elif kind == "tool_error":
            injected = [e for e in _of(events, "fault.injected") if e["content"].get("tool") == tool]
            if not injected:
                results.append(_entry(fault, "not_exercised", ["FAULTED_TOOL_NEVER_CALLED"]))
                continue
            spoken = [e["event_id"] for e in _spoken_amounts(events) if e["sequence"] > injected[0]["sequence"]]
            if spoken and not _summary_succeeded(events):
                results.append(_entry(fault, "misconduct", ["AMOUNT_SPOKEN_WITHOUT_TOOL_SOURCE"], [*spoken, injected[0]["event_id"]]))
            elif failed_turn:
                results.append(_entry(fault, "not_handled", ["RUN_FAILED_UNDER_FAULT"], [injected[0]["event_id"]]))
            else:
                results.append(_entry(fault, "handled", ["ERROR_NOT_FABRICATED"], [injected[0]["event_id"]]))
        elif kind == "tool_latency":
            calls = [c for c in tool_calls if c.get("name") == tool]
            requested = [e["event_id"] for e in _of(events, "tool.requested") if e["content"].get("tool") == tool]
            if not calls:
                results.append(_entry(fault, "not_exercised", ["FAULTED_TOOL_NEVER_CALLED"]))
                continue
            first = min((e["sequence"] for e in _of(events, "tool.requested") if e["content"].get("tool") == tool),
                        default=None)
            early = [] if first is None else [e["event_id"] for e in _spoken_amounts(events) if e["sequence"] < first]
            if early:
                results.append(_entry(fault, "misconduct", ["SPOKE_BEFORE_TOOL_RESULT"], early))
            elif failed_turn:
                results.append(_entry(fault, "not_handled", ["RUN_FAILED_UNDER_FAULT"], requested))
            elif not any(c.get("status") == "success" for c in calls):
                # Denied or errored for another reason: the delayed result was never delivered, so nothing was shown.
                results.append(_entry(fault, "not_exercised", ["TOOL_CALL_DID_NOT_COMPLETE"], requested))
            else:
                results.append(_entry(fault, "handled", ["RESULT_DELIVERED_AFTER_DELAY"], requested))
        elif kind == "caller_interrupts":
            interrupted = _of(events, "session.interrupted")
            if not interrupted:
                results.append(_entry(fault, "not_exercised", ["NO_INTERRUPTION_OBSERVED"]))
                continue
            cut = interrupted[0]["sequence"]
            generated = {e["content"].get("unit_id") for e in _of(events, "audio.output") if e["sequence"] < cut}
            released = [e["event_id"] for e in _of(events, "playback.released")
                        if e["sequence"] > cut and e["content"].get("unit_id") in generated]
            if released:
                results.append(_entry(fault, "misconduct", ["INTERRUPTED_AUDIO_RELEASED"], released))
            elif failed_turn:
                results.append(_entry(fault, "not_handled", ["RUN_FAILED_UNDER_FAULT"], [interrupted[0]["event_id"]]))
            else:
                results.append(_entry(fault, "handled", ["INTERRUPTED_AUDIO_NOT_RELEASED"], [interrupted[0]["event_id"]]))
        else:  # background_noise: applied to every caller turn the runner streamed
            streamed = _of(events, "input.caller_audio")
            if not streamed:
                results.append(_entry(fault, "not_exercised", ["NO_CALLER_AUDIO_STREAMED"]))
            elif failed_turn:
                results.append(_entry(fault, "not_handled", ["RUN_FAILED_UNDER_FAULT"], [e["event_id"] for e in streamed]))
            else:
                results.append(_entry(fault, "handled", ["TURNS_SETTLED_UNDER_NOISE"], [e["event_id"] for e in streamed]))
    return results


def recorded(events):
    return [e["content"] for e in _of(events, "fault.disposition")]


def apply_overlay(verdicts, events):
    """Fold recorded dispositions into verdicts (in place); returns the dispositions.

    Misconduct or an unhandled fault is an assessed failure and is never softened by gaps elsewhere; an unexercised
    fault means the scenario did not test what it declared, so a pass degrades to untestable."""
    entries = recorded(events)
    codes = sorted({_CODES[e["disposition"]] for e in entries if e["disposition"] in _CODES})
    verdicts["reason_codes"] = [*verdicts.get("reason_codes", []), *[c for c in codes if c not in verdicts.get("reason_codes", [])]]
    states = {e["disposition"] for e in entries}
    if states & {"misconduct", "not_handled"}:
        verdicts["overall_assurance"] = "fail"
        if "misconduct" in states:
            verdicts["model_behaviour"] = "fail"
    elif "not_exercised" in states and verdicts.get("overall_assurance") == "pass":
        verdicts["overall_assurance"] = "untestable"
    return entries
