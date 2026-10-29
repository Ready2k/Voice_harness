"""Performance regression gate over `benchmark` output; absolute targets stay provisional.

A run fails on any failure/timeout/drop/discontinuity, a short sample, a missing profile, or a p95 above
max(baseline * FACTOR, baseline + FLOOR_MS). The tolerance is wide on purpose: CI runners are noisy, and the gate
exists to catch order-of-magnitude regressions (e.g. connection-per-event, ~85 ms), not 10% drifts.
"""

import argparse
import json
import sys
from pathlib import Path

FACTOR = 2.0
FLOOR_MS = 5.0
MIN_OBSERVATIONS = 1000


def _component_key(profile):
    return f"component/sessions={profile['sessions']}/scale={profile['payload_scale']}/instrumented={profile['instrumented']}"


def summarize(result):
    """Reduce a full benchmark artifact to the p50/p95 figures the gate compares."""
    profiles = {}
    for profile in result["profiles"]:
        total = profile["stages"]["total_path"]
        profiles[_component_key(profile)] = {"p50_ms": total["p50_ms"], "p95_ms": total["p95_ms"], "count": total["count"]}
    for profile in result.get("websocket_profiles", []):
        dist = profile["distribution"]
        profiles[f"websocket/sessions={profile['sessions']}"] = {"p50_ms": dist["p50_ms"], "p95_ms": dist["p95_ms"], "count": dist["count"]}
    return {
        "schema": "perf-baseline-v1",
        "benchmark_version": result.get("benchmark_version"),
        "source_hash": result.get("source_hash"),
        "environment": result.get("environment", {}),
        "profiles": profiles,
    }


def merge(summaries):
    """Combine several runs' summaries into one baseline holding the worst p50/p95 seen per profile."""
    merged = {**summaries[0], "runs_merged": len(summaries), "profiles": {}}
    for key in summaries[0]["profiles"]:
        rows = [summary["profiles"][key] for summary in summaries]
        merged["profiles"][key] = {
            "p50_ms": max(r["p50_ms"] for r in rows),
            "p95_ms": max(r["p95_ms"] for r in rows),
            "count": min(r["count"] for r in rows),
        }
    return merged


def _hard_failures(result):
    problems = []
    for profile in result["profiles"]:
        key = _component_key(profile)
        for field in ("timeouts", "failures", "dropped_samples", "discontinuous_samples"):
            if profile.get(field):
                problems.append(f"{key}: {field}={profile[field]}")
    for profile in result.get("websocket_profiles", []):
        for field in ("failures", "discontinuities"):
            if profile.get(field):
                problems.append(f"websocket/sessions={profile['sessions']}: {field}={profile[field]}")
    return problems


def check(result, baseline, factor=FACTOR, floor_ms=FLOOR_MS):
    """Return (problems, rows); an empty problem list means the gate passes."""
    problems = _hard_failures(result)
    current = summarize(result)["profiles"]
    rows = []
    for key, expected in baseline["profiles"].items():
        got = current.get(key)
        if got is None:
            problems.append(f"{key}: missing from run")
            continue
        limit = max(expected["p95_ms"] * factor, expected["p95_ms"] + floor_ms)
        if got["count"] < MIN_OBSERVATIONS:
            problems.append(f"{key}: only {got['count']} observations")
        if got["p95_ms"] > limit:
            problems.append(f"{key}: p95 {got['p95_ms']:.2f} ms exceeds limit {limit:.2f} ms (baseline {expected['p95_ms']:.2f})")
        rows.append((key, expected["p95_ms"], got["p95_ms"], limit))
    for key in current.keys() - baseline["profiles"].keys():
        problems.append(f"{key}: not in baseline (regenerate it deliberately)")
    return problems, rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("check")
    gate.add_argument("result")
    gate.add_argument("--baseline", default="artifacts/perf-baseline.json")
    gate.add_argument("--factor", type=float, default=FACTOR)
    gate.add_argument("--floor-ms", type=float, default=FLOOR_MS)
    base = sub.add_parser("baseline")
    base.add_argument("result", nargs="+", help="one or more benchmark outputs; the worst p95 per profile is kept")
    base.add_argument("--output", default="artifacts/perf-baseline.json")
    args = parser.parse_args(argv)
    if args.command == "baseline":
        summaries = [summarize(json.loads(Path(path).read_text())) for path in args.result]
        Path(args.output).write_text(json.dumps(merge(summaries), indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.output} from {len(summaries)} run(s)")
        return 0
    result = json.loads(Path(args.result).read_text())
    baseline = json.loads(Path(args.baseline).read_text())
    problems, rows = check(result, baseline, args.factor, args.floor_ms)
    for key, base_p95, got_p95, limit in rows:
        print(f"{key:62} baseline {base_p95:7.2f}  run {got_p95:7.2f}  limit {limit:7.2f} ms")
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    print("PERF GATE " + ("FAILED" if problems else "PASSED"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
