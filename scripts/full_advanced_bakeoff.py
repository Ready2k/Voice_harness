"""Run/resume the complete assessed catalogue with advanced-v1 prompts; export a portable report.

Paid live providers and independent ASR; full banking tools, verification and speech gates remain enabled.
Local evidence stays under --data; portable observations and reports go to --out. No credentials are exported.
"""

import argparse
import hashlib
import html
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from fastapi.testclient import TestClient
    from voice_conversation import load_environment, save_json

    from voice_harness import bakeoff, live_providers
    from voice_harness.api import create_app
    from voice_harness.bakeoff_export import render, report_data
    from voice_harness.storage import Database
    from voice_harness.target_prompts import instructions, provenance

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3, choices=range(1, 4))
    parser.add_argument("--resume", action="store_true", help="Recheck and resume a paused bake-off after fixing its cause")
    parser.add_argument("--name", default="Full advanced provider bake-off — 26 October 2026")
    parser.add_argument("--prompt-profile", default="advanced-v1", choices=["advanced-v1", "standardised-v2", "conversational-v1"],
                        help="prompt profile for every provider (default keeps the original advanced-v1 behaviour)")
    parser.add_argument("--providers", help="comma-separated provider_config_ids (default: every live provider)")
    args = parser.parse_args()
    load_environment()
    # MCP tools run in a child interpreter, which does not inherit this script's sys.path changes.
    os.environ["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ["VOICE_HARNESS_LIVE_ASR"] = "openai"
    os.environ.setdefault("HARNESS_PRICING_TABLE", str(ROOT / "artifacts/pricing/vendor-pricing-2026-10-29.json"))
    logging.basicConfig(level=logging.WARNING)
    args.out.mkdir(parents=True, exist_ok=True)
    args.data.mkdir(parents=True, exist_ok=True)
    database = Database(str(args.data.resolve() / "bakeoff.sqlite"))
    app = create_app(database, args.data.resolve())
    store = bakeoff.BakeoffStore(database)
    headers = {"Authorization": "Bearer " + (args.data / "operator-token").read_text().strip()}
    run_file = args.out / "launch.json"
    providers = args.providers.split(",") if args.providers else list(bakeoff.NAMES)
    request = {"name": args.name, "prompt_profile": args.prompt_profile,
               "provider_config_ids": providers, "repetitions": args.repetitions,
               "group_balanced": True}
    prompts = {p: provenance(args.prompt_profile, instructions(args.prompt_profile, p), live_providers.TOOLS) for p in providers}
    prompt_file = args.out / "prompts.json"
    if prompt_file.exists() and json.loads(prompt_file.read_text()) != prompts:
        if run_file.exists():
            old = store.get(json.loads(run_file.read_text())["bakeoff_id"])
            if any(c["attempts"] for c in old["cells"]):
                raise ValueError("PROMPTS_FROZEN_AFTER_SCORING_STARTS")
        history = args.out / "prompt-history"
        history.mkdir(exist_ok=True)
        (history / (hashlib.sha256(prompt_file.read_bytes()).hexdigest() + ".json")).write_bytes(prompt_file.read_bytes())
    save_json(prompt_file, prompts)
    with TestClient(app) as client:
        if run_file.exists():
            launched = json.loads(run_file.read_text())
            if launched["request"] != request:
                raise ValueError("EXISTING_BAKEOFF_PLAN_DIFFERS")
            identifier = launched["bakeoff_id"]
            if args.resume and store.get(identifier)["state"] == "paused":
                response = client.post(f"/api/v1/bakeoffs/{identifier}/resume", headers=headers)
                response.raise_for_status()
        else:
            response = client.post("/api/v1/bakeoffs", headers=headers, json=request)
            response.raise_for_status()
            identifier = response.json()["bakeoff_id"]
            save_json(run_file, {"bakeoff_id": identifier, "request": request,
                                 "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        prior = None
        while True:
            payload = store.get(identifier)
            status = bakeoff.summary(payload)
            signature = (status["state"], status["phase"], status["cells_done"], status["runs_in_flight"])
            if signature != prior:
                save_json(args.out / "status.json", status)
                print(json.dumps({k: status[k] for k in ("state", "phase", "cells_done", "cells", "runs_in_flight", "pause_reason")}), flush=True)
                prior = signature
            if status["state"] in {"completed", "cancelled", "paused"} and not status["runs_in_flight"]:
                break
            time.sleep(3)
        # All underlying observations are exported; raw retained evidence and secrets stay in the local data root.
        save_json(args.out / "payload.json", payload)
        save_json(args.out / "report.json", report_data(payload, source=identifier))
        (args.out / "report.html").write_text(render(payload, source=identifier))
        (args.out / "index.html").write_text(
            '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Full advanced bake-off</title><style>body{font:18px/1.6 system-ui;max-width:850px;'
            'margin:40px auto;padding:0 20px}</style><h1>Full advanced bake-off</h1>'
            f'<p>Status: <strong>{html.escape(status["state"])}</strong> · {status["cells_done"]} of {status["cells"]} cells finished.</p>'
            f'<p>{len(providers)} providers, {len({c["scenario_id"] for c in payload["cells"]})} scenarios and {args.repetitions} repetitions. Advanced provider-specific style with full banking tools '
            'and strict verification and speech gates.</p><p><a href="report.html">Open assessed report</a></p>'
            '<p><a href="run-notes.md">Run notes and limitations</a></p>'
            '<p>A paused or incomplete report is provisional; it is not a completed provider comparison.</p></html>')
        bindings = {}
        for cell in bakeoff.work_cells(payload):
            for attempt in cell["attempts"]:
                if not attempt.get("run_id"):
                    continue
                config = database.get(attempt["run_id"])["config"]
                if config.get("prompt_profile", "standardised-v2") != args.prompt_profile:
                    raise ValueError("RUN_PROMPT_PROFILE_MISMATCH")
                for event in database.events(attempt["run_id"]):
                    if event["type"] == "run.live_binding":
                        provider = cell["provider_config_id"]
                        bound = event["content"]["provider"]["target_prompt"]
                        if bound["profile"] != args.prompt_profile or bound["instructions"] != instructions(args.prompt_profile, provider):
                            raise ValueError("RUN_PROMPT_BINDING_MISMATCH")
                        bindings[provider] = bound
        save_json(args.out / "verified-prompt-bindings.json", bindings)
        print("Saved assessed report and prompt bindings.", flush=True)


if __name__ == "__main__":
    main()
