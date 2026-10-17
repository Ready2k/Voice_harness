"""Ledger-derived run metrics: every null carries an exclusion reason (spec section 12)."""

from .events import PROCESS_CLOCK_DOMAIN

NS_PER_MS = 1_000_000


def _observation(name, unit, value, *, start=None, end=None, domain=None, reason=None):
    if value is None and reason is None:
        raise ValueError("NULL_METRIC_REQUIRES_REASON")
    return {
        "metric": name,
        "unit": unit,
        "value": value,
        "start_event_id": start,
        "end_event_id": end,
        "clock_domain_id": domain,
        "uncertainty_ms": 0.0 if value is not None and unit == "ms" else None,
        "exclusion_reason": reason,
    }


def _summary(values):
    ordered = sorted(values)
    pick = lambda p: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]  # noqa: E731
    return {"count": len(ordered), "p50": pick(0.5), "p95": pick(0.95), "max": ordered[-1]}


def _adapter_metrics(events):
    """Adapter-run boundaries (benchmark.adapter_metrics): one observation per metric name.

    Values come from adapter events in one declared clock domain; a metric with no
    value in any turn keeps its recorded exclusion reason.
    """
    values, reasons, source_events = {}, {}, {}
    for e in events:
        if e["type"] != "benchmark.adapter_metrics":
            continue
        for m in e["content"]["metrics"]:
            source_events.setdefault(m["metric"], e["event_id"])
            if m["value"] is None:
                reasons[m["metric"]] = m["exclusion_reason"]
            else:
                values.setdefault(m["metric"], []).append(m["value"])
    out = {}
    for name in sorted(set(values) | set(reasons)):
        if name in values:
            out[name] = _observation(name, "ms", _summary(values[name]), start=source_events[name],
                                     domain=PROCESS_CLOCK_DOMAIN)
        else:
            out[name] = _observation(name, "ms", None, start=source_events[name], reason=reasons[name])
    return out


def derive(events):
    events = [e for e in events if "tombstone" not in e]  # RT-02: payload expired
    observations = []
    generated = {e["content"]["unit_id"]: e for e in events if e["type"] == "audio.output"}
    ended = {}
    risk = {}
    for e in events:
        content = e["content"]
        if e["type"] == "playback.released":
            ended.setdefault(content["unit_id"], e)
        elif e["type"] == "decision" and content.get("target") == "audio_chunk":
            unit = content["unit_id"]
            if content["action"] in {"deny", "redact", "replace", "escalate"}:
                ended.setdefault(unit, e)
            else:
                risk[unit] = content.get("risk_mode")
    for unit_id, start in generated.items():
        end = ended.get(unit_id)
        if end is None:
            observations.append(_observation(
                "approval_hold_duration", "ms", None, start=start["event_id"],
                reason="UNIT_DISCARDED_OR_STILL_HELD_WITHOUT_DECISION"))
        elif end["clock_domain_id"] != start["clock_domain_id"]:
            observations.append(_observation(
                "approval_hold_duration", "ms", None, start=start["event_id"],
                end=end["event_id"], reason="CROSS_CLOCK_DOMAIN"))
        else:
            observations.append(_observation(
                "approval_hold_duration", "ms",
                (end["monotonic_ns"] - start["monotonic_ns"]) / NS_PER_MS,
                start=start["event_id"], end=end["event_id"], domain=start["clock_domain_id"]))
    released = [u for u, e in ended.items() if e["type"] == "playback.released"]
    blocked = [u for u in ended if u not in released]
    modes = {}
    for unit in released:
        modes[risk.get(unit) or "unknown"] = modes.get(risk.get(unit) or "unknown", 0) + 1
    observations += [
        _observation("generated_audio_units", "count", len(generated)),
        _observation("generated_but_blocked_units", "count", len(blocked)),
        _observation("released_audio_units", "count", len(released)),
    ]
    for mode, count in sorted(modes.items()):
        observations.append(_observation("released_units_" + mode, "count", count))
    latencies = [
        e["content"]["latency_ms"]
        for e in events
        if e["type"] == "decision" and "latency_ms" in e["content"]
    ]
    if latencies:
        observations.append(_observation("guardrail_decision_latency", "ms", _summary(latencies)))
    else:
        observations.append(_observation("guardrail_decision_latency", "ms", None, reason="NO_DECISIONS"))
    requested = {e["content"]["call_id"]: e for e in events if e["type"] == "tool.requested"}
    round_trips = []
    for e in events:
        if e["type"] in {"tool.completed", "tool.failed", "tool.denied"} and e["content"]["call_id"] in requested:
            start = requested[e["content"]["call_id"]]
            if start["clock_domain_id"] == e["clock_domain_id"]:
                round_trips.append((e["monotonic_ns"] - start["monotonic_ns"]) / NS_PER_MS)
    observations.append(
        _observation("mcp_round_trip", "ms", _summary(round_trips))
        if round_trips
        else _observation("mcp_round_trip", "ms", None, reason="NO_SAME_DOMAIN_TOOL_SPANS")
    )
    connects = sum(e["type"] == "session.connected" for e in events)
    observations += [
        _observation("reconnects", "count", max(0, connects - 1)),
        _observation("dropped_or_discontinuous_events", "count",
                     sum(e["type"] == "audio.discontinuity" for e in events)),
        _observation("buffer_backpressure_failures", "count",
                     sum(e["type"] == "audio.backpressure" for e in events)),
        _observation("decision_timeouts", "count",
                     sum(e["type"] == "audio.decision_timeout" for e in events)),
    ]
    adapter = _adapter_metrics(events)
    for name in ("time_to_first_generated_audio", "time_to_first_released_audio"):
        if name in adapter:
            observations.append(adapter.pop(name))
            continue
        observations.append(_observation(
            name, "ms", None, reason="NO_INPUT_SPEECH_END_EVENT_IN_FIXTURE_RUN"))
    observations += [adapter[name] for name in sorted(adapter)]
    observations.append(_observation(
        "audio_underruns", "count", None, reason="CLIENT_SIDE_NOT_REPORTED_TO_SERVER"))
    return observations
