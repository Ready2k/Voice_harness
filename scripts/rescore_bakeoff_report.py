"""Re-score a saved bake-off report whose explicit-commit providers were charged the caller's trailing silence.

    PYTHONPATH=src .venv/bin/python scripts/rescore_bakeoff_report.py \
        artifacts/full-advanced-bakeoff-2026-10-26/report.json \
        artifacts/full-advanced-bakeoff-2026-10-26/rescored-2026-10-27

Before the latency-anchor fix, every caller turn was streamed with ``trail_ms`` of trailing silence and first audio was
timed from the end of speech. Providers on explicit commit could not answer until the commit, which came after that
silence, so each of their timed turns over-counts by exactly ``trail_ms``. This script subtracts it from those turns
(timed turns and the content-audio detail), marks them ``anchor: legacy_commit`` / ``turn_commit: explicit``, marks the
others ``legacy_speech_end`` / ``provider_vad``, and re-runs the shipped scoring (``bakeoff.report`` through
``bakeoff_export``) on the result. Nothing else is changed. Deterministic: the same input gives byte-identical output.

The input is an exported ``report.json`` (title, report, observations) or a stored bake-off payload (with cells).
Before correcting anything the input is re-scored unchanged and must reproduce its stored composites, so the corrected
figures come from the same code path as the originals (task completion is weighted 0 for that check, since the
original scoring had no such dimension).

The re-score then applies the current default weights, which score task completion. Scenarios in ``EXCLUDE_TASK`` are
left out of task completion with the stated reason: their tool calls were denied by a harness artefact that cannot be
corrected after the fact (the denied call never returned data, so the run stays as observed).
"""

import argparse
import copy
import json
from pathlib import Path

from voice_harness import bakeoff
from voice_harness.bakeoff_export import render, report_data

EXPLICIT = ("openai-realtime-live", "xai-voice-live", "gemini-live")  # provider-transport-v1 fixed-caller transport, 2026-10-20 on
TRAIL_MS = 1200  # live_providers.CALLER_TRAIL_MS when those runs were recorded
# The caller said "transaction txn_001"; the bank then matched it exactly, so the spoken forms TXN001 / txn001 were denied
# NOT_FOUND. Spoken-form ids are now canonicalised (spoken_ids), but denied calls cannot be replayed.
EXCLUDE_TASK = {"happy-transaction-details-001": "TRANSACTION_ID_SPOKEN_FORM_ARTEFACT"}


def payload_from(saved):
    """A bake-off payload ``bakeoff.report`` can score, from an exported report.json or a stored payload."""
    if "cells" in saved:
        return copy.deepcopy(saved)
    r = saved["report"]
    return {"name": saved["title"], "providers": saved["providers"], "weights": r["weights"],
            "repetitions": r["limits"]["repetitions"], "group_balanced": r["scoring"]["group_balanced"],
            "state": r["readiness"]["state"], "phase": r["readiness"]["phase"], "pause_reason": r["readiness"]["pause_reason"],
            "preflight": r["readiness"]["preflight"], "incidents": r["readiness"]["incidents"],
            "cells": [{"provider_config_id": o["provider"], "done": True, "observation": copy.deepcopy(o)} for o in saved["observations"]]}


def correct(payload, explicit=EXPLICIT, trail_ms=TRAIL_MS):
    """Re-anchor explicit-commit turns at the commit, in place. Returns {provider: turns corrected}. Idempotent: turns that
    already carry an anchor are left alone."""
    changed = {}
    for cell in payload["cells"]:
        o = cell.get("observation")
        if not o:
            continue
        is_explicit = o["provider"] in explicit
        o.setdefault("turn_commit", "explicit" if is_explicit else "provider_vad")
        details = {t["turn"]: t for t in (o.get("metrics") or {}).get("turns", [])}
        for t in o["turns"]:
            if t.get("anchor"):
                continue
            t["turn_commit"] = o["turn_commit"]
            t["anchor"] = "legacy_commit" if is_explicit else "legacy_speech_end"
            if not is_explicit:
                continue
            if t["first_audio_ms"] is not None:
                if t["first_audio_ms"] < trail_ms:
                    raise ValueError(f"AUDIO_BEFORE_COMMIT:{o['run_id']}:{t['turn']}")
                t["first_audio_ms"] -= trail_ms
                changed[o["provider"]] = changed.get(o["provider"], 0) + 1
            detail = details.get(t["turn"])
            if detail and detail.get("content_audio_ms") is not None:
                detail["content_audio_ms"] -= trail_ms
    return changed


def exclude_task_completion(payload, excluded=None):
    """Mark the observations of artefact scenarios as not scored for task completion, in place. Returns {scenario: runs}."""
    excluded = EXCLUDE_TASK if excluded is None else excluded
    marked = {}
    for cell in payload["cells"]:
        o = cell.get("observation")
        if o and o["scenario_id"] in excluded:
            o["task_completion_excluded"] = excluded[o["scenario_id"]]
            marked[o["scenario_id"]] = marked.get(o["scenario_id"], 0) + 1
    return marked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("saved", help="exported report.json or stored bake-off payload")
    parser.add_argument("out_dir")
    parser.add_argument("--explicit", default=",".join(EXPLICIT), help="providers that ran on explicit commit")
    parser.add_argument("--trail-ms", type=int, default=TRAIL_MS)
    args = parser.parse_args()
    saved = json.loads(Path(args.saved).read_text())
    payload = payload_from(saved)

    original = bakeoff.report(payload, weights={**payload["weights"], "task_completion": 0.0})
    stored = {s["provider"]: s["composite"] for s in saved.get("report", {}).get("scores", [])}
    for s in original["scores"]:
        if s["provider"] in stored and abs((stored[s["provider"]] or 0) - (s["composite"] or 0)) > 1e-9:
            raise SystemExit(f"stored composite for {s['provider']} is not reproduced; refusing to re-score")

    changed = correct(payload, tuple(args.explicit.split(",")), args.trail_ms)
    latency_only = bakeoff.report(payload, weights={**payload["weights"], "task_completion": 0.0})
    marked = exclude_task_completion(payload)
    payload["weights"] = dict(bakeoff.DEFAULT_WEIGHTS)
    title = (saved.get("title") or payload["name"]) + " (re-scored: latency re-anchored, task completion scored)"
    source = ((saved.get("source") or "Voice Harness ledger") + f" · Re-scored 2026-10-27: {args.trail_ms} ms trailing silence removed from"
              " explicit-commit turns · 2026-10-28: task completion scored with the current default weights")
    options = {"title": title, "source": source, "generated_at": "2026-10-27 (re-scored from " + (saved.get("generated_at") or "stored payload") + ")"}
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = report_data(payload, **options)
    (out / "report.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    (out / "report.html").write_text(render(payload, **options))
    summary = {"turns_corrected": changed, "trail_ms": args.trail_ms, "explicit_commit": sorted(args.explicit.split(",")),
               "task_completion_excluded": {s: {"reason": EXCLUDE_TASK[s], "runs": n} for s, n in sorted(marked.items())},
               "weights": data["report"]["weights"],
               "before": table(original), "latency_only": table(latency_only), "after": table(data["report"]),
               "verdict_after": {k: data["report"]["verdict"][k] for k in (
                   "ranking", "leader", "runner_up", "no_clear_winner", "quality_lead", "latency_leader", "latency_comparable",
                   "latency_comparability", "speed_only_lead", "sensitivity_basis", "ranking_basis", "weight_win_share", "rank_stable")}}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=1, sort_keys=True))


def table(report):
    lat = {r["provider"]: r for r in report["latency"]}
    return [{"provider": s["provider"], "composite_order": s["composite_order"], "rank": s.get("rank"), "standing": s.get("standing"),
             "composite": round(s["composite"], 4), "composite_excluding_latency": round(s["composite_excluding_latency"], 4),
             "rank_excluding_latency": s.get("rank_excluding_latency"), "responsiveness": round(s["responsiveness"]["value"], 4),
             "task_completion": None if s.get("task_completion", {}).get("value") is None else round(s["task_completion"]["value"], 4),
             "task_completion_k_n": [s.get("task_completion", {}).get("k"), s.get("task_completion", {}).get("n")],
             "median_ms": lat[s["provider"]]["median_ms"], "p90_ms": lat[s["provider"]]["p90_ms"]}
            for s in sorted(report["scores"], key=lambda s: s.get("display_order") or s["composite_order"])]


if __name__ == "__main__":
    main()
