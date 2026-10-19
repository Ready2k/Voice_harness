"""Run catalogue scenarios against live providers through the API and collect a scenario x provider matrix.

Usage (server from `make serve-live`):
    .venv/bin/python scripts/provider_matrix.py --base http://127.0.0.1:8766 \
        --token-file .local/host-data/operator-token --out artifacts/provider-matrix.json [--scenarios a,b] [--providers x,y]

Runs are launched with bounded concurrency (overall and per provider) and polled to a terminal state.
The output file is rewritten after every finished run, so an interrupted batch keeps its results and
a rerun with the same --out skips cells that already finished.
"""

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

TERMINAL = {"completed", "failed", "cancelled", "recovery_required"}


class Api:
    def __init__(self, base, token):
        if not base.startswith(("http://", "https://")):
            raise SystemExit("--base must be an http(s) URL")
        self.base, self.token = base.rstrip("/"), token

    def call(self, path, body=None, headers=None):
        request = urllib.request.Request(  # noqa: S310 -- scheme checked in __init__
            self.base + path, method="POST" if body is not None else "GET",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json", **(headers or {})})
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310  # nosec B310 -- scheme checked in __init__
            return json.load(response)


def summary(api, run_id):
    """What the matrix needs from one finished run: state, verdicts, criteria, tools, replies, duration."""
    run = api.call(f"/api/v1/runs/{run_id}")
    out = {"run_id": run_id, "state": run["state"]}
    try:
        assurance = api.call(f"/api/v1/runs/{run_id}/assurance")
        out["assurance"] = {k: assurance.get(k) for k in ("overall_assurance", "model_behaviour", "platform_enforcement")}
        out["criteria"] = {cid: {"verdict": c["verdict"], "reasons": c["reason_codes"]}
                           for cid, c in (assurance.get("criteria") or {}).items() if c.get("applicable")}
    except urllib.error.HTTPError:
        out["assurance"], out["criteria"] = None, {}
    events = []
    cursor = 0
    while True:
        page = api.call(f"/api/v1/runs/{run_id}/events?limit=1000&cursor={cursor}")
        events += page["items"]
        if page.get("next_cursor") is None:
            break
        cursor = page["next_cursor"]
    calls = {}
    for e in events:
        c = e["content"]
        if e["type"] == "tool.requested":
            calls[c["call_id"]] = {"tool": c.get("tool"), "final": None}
        elif e["type"] in ("tool.completed", "tool.denied", "tool.failed") and c.get("call_id") in calls:
            calls[c["call_id"]]["final"] = e["type"][5:]
    out["tools"] = list(calls.values())
    out["failure"] = next((e["content"].get("reason") for e in events if e["type"] == "run.failed"), None)
    out["provider_errors"] = [e["content"].get("native_type") for e in events
                              if e["type"] == "adapter.native_event" and "error" in str(e["content"].get("native_type", ""))][:3]
    held = [e for e in events if e["type"] == "assessment.independent_asr"]
    out["replies"] = {"assessed": len(held), "unsafe": sum(e["content"]["disposition"] == "unsafe" for e in held)}
    out["wall_s"] = round((events[-1]["monotonic_ns"] - events[0]["monotonic_ns"]) / 1e9, 1) if events else None
    out["caller_turns"] = sum(e["type"] == "input.caller_audio" for e in events)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--token-file", default=".local/host-data/operator-token")
    parser.add_argument("--out", required=True)
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--providers", default="")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--per-provider", type=int, default=2)
    args = parser.parse_args()
    api = Api(args.base, Path(args.token_file).read_text().strip())
    catalogue = [s for s in api.call("/api/v1/scenarios?limit=1000")["items"] if s["execution"] == "live_adapter"]
    wanted = {s for s in args.scenarios.split(",") if s}
    scenarios = [s for s in catalogue if not wanted or s["scenario_id"] in wanted]
    providers = [p["provider_config_id"] for p in api.call("/api/v1/live-providers")["items"] if p["available"]]
    if args.providers:
        providers = [p for p in providers if p in args.providers.split(",")]
    out_path = Path(args.out)
    results = json.loads(out_path.read_text()) if out_path.exists() else {"cells": {}}
    results.update({
        "scenarios": {s["scenario_id"]: s["presentation"] for s in catalogue},
        "providers": sorted(set(results.get("providers", [])) | set(providers)),
    })
    todo = [(s["scenario_id"], p) for s in scenarios for p in providers
            if results["cells"].get(f"{s['scenario_id']}|{p}", {}).get("state") not in TERMINAL]
    running = {}  # run_id -> (scenario, provider)
    started = time.time()
    print(f"{len(todo)} runs to do across {len(providers)} providers", flush=True)
    while todo or running:
        for key in list(running):
            state = api.call(f"/api/v1/runs/{key}")["state"]
            if state in TERMINAL:
                scenario, provider = running.pop(key)
                cell = summary(api, key)
                results["cells"][f"{scenario}|{provider}"] = {**cell, "scenario_id": scenario, "provider": provider}
                out_path.write_text(json.dumps(results, indent=1))
                done = sum(c.get("state") in TERMINAL for c in results["cells"].values())
                print(f"[{int(time.time() - started)}s] {done} done · {provider} {scenario}: {state} "
                      f"{(cell.get('assurance') or {}).get('overall_assurance')}", flush=True)
        busy = {}
        for _scenario, provider in running.values():
            busy[provider] = busy.get(provider, 0) + 1
        for item in list(todo):
            scenario, provider = item
            if len(running) >= args.concurrency or busy.get(provider, 0) >= args.per_provider:
                continue
            try:
                created = api.call("/api/v1/runs", {"scenario_id": scenario, "provider_config_id": provider},
                                   {"Idempotency-Key": "matrix-" + uuid.uuid4().hex})
            except urllib.error.HTTPError as error:
                if error.code == 429:
                    break  # concurrency limit: retry on the next poll
                results["cells"][f"{scenario}|{provider}"] = {"scenario_id": scenario, "provider": provider,
                                                              "state": "failed", "failure": f"LAUNCH_HTTP_{error.code}"}
                todo.remove(item)
                continue
            running[created["run_ids"][0]] = item
            busy[provider] = busy.get(provider, 0) + 1
            todo.remove(item)
        time.sleep(3)
    results["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out_path.write_text(json.dumps(results, indent=1))
    print("done", flush=True)


if __name__ == "__main__":
    main()
