"""Queue a rerun of the scored cells whose independent ASR was unavailable, in a finished bake-off.

    PYTHONPATH=src .venv/bin/python scripts/rerun_asr_blocked_cells.py --data .local/bakeoff-2026-10-29-standard-5
    PYTHONPATH=src .venv/bin/python scripts/rerun_asr_blocked_cells.py --data ... --apply

An ``ASR_HTTP_429`` during assessment leaves a reply unchecked (FACT-01 ``untestable`` or ``review_required``) although the run
completed, so ``--resume`` never requeues it and the reply audio is not retained to re-transcribe. The selection rule is
ledger-based and independent of how the provider did: every scored cell whose final attempt recorded an
``assessment.asr_unavailable`` event, for every provider alike. Without ``--apply`` it only lists them.

``--rule dropped-tool-call`` selects instead the ElevenLabs cells whose ledger shows more provider ``client_tool_call`` events than
``tool.requested`` events: a tool call the provider made and the harness never executed (30 Oct 2026, identity-fault-caller-interrupts-001;
fixed by ``BARGE_IDLE_S`` in the adapter). Run it with the fixed adapter, so the rerun is a real second sample.

``--apply`` puts the plan in the state ``bakeoff.resume`` expects (paused), clears each selected cell's observation and
appends a pending attempt (the earlier attempt and its cost stay on the cell), calls ``resume``, and keeps pre-flight to
the providers being rerun. Then ``scripts/full_advanced_bakeoff.py --resume`` (same --out, --data, --name,
--prompt-profile and --providers as the original launch) runs the pending cells and re-exports the report. The rerun is
a fresh sample from a live model and replaces the first result; say so in the run notes. Cells already at
``MAX_ATTEMPTS`` are listed and left alone. Never run it while a bake-off is in flight.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_harness import bakeoff  # noqa: E402
from voice_harness.storage import Database  # noqa: E402

NATIVE_TOOL_CALL = {"elevenlabs-live": "client_tool_call"}  # the provider's own tool-call event, per provider the rule knows
REASONS = {"asr-unavailable": "ASR_UNAVAILABLE", "dropped-tool-call": "DROPPED_TOOL_CALL"}


def database_for(data):
    legacy = data.resolve() / "bakeoff.sqlite"
    return Database(str(legacy if legacy.exists() else data.resolve() / "development.sqlite"))


def select(payload, database, rule="asr-unavailable"):
    """Scored cells matching ``rule``, with a count and reasons. ``asr-unavailable``: the final attempt's ledger holds an
    ``assessment.asr_unavailable`` event. ``dropped-tool-call``: it holds more native provider tool calls than ``tool.requested``."""
    chosen = []
    for cell in payload["cells"]:
        final = cell["attempts"][-1] if cell["attempts"] else {}
        if not cell.get("done") or not final.get("run_id"):
            continue
        events = database.events(final["run_id"])
        if rule == "asr-unavailable":
            blocked = [e for e in events if e["type"] == "assessment.asr_unavailable"]
            if blocked:
                chosen.append((cell, len(blocked), sorted({e["content"].get("reason") for e in blocked})))
        else:
            native_type = NATIVE_TOOL_CALL.get(cell["provider_config_id"])
            native = sum(e["type"] == "adapter.native_event" and e["content"].get("native_type") == native_type for e in events)
            requested = sum(e["type"] == "tool.requested" for e in events)
            if native_type and native > requested:
                chosen.append((cell, native - requested, [f"{native}_{native_type}_vs_{requested}_executed"]))
    return chosen


def queue(payload, database, cells, clock, reason=REASONS["asr-unavailable"]):
    """Reset the chosen cells and move the plan to a fresh pre-flight (the same steps as ``bakeoff.resume``)."""
    for cell in cells:
        cell["attempts"][-1]["rerun_reason"] = reason
        cell.pop("observation", None)
        cell.pop("check_failure", None)
        cell["done"] = False
        cell["attempts"].append({"pending": True})
    providers = sorted({c["provider_config_id"] for c in cells})
    bakeoff.pause(payload, providers[0], "TARGETED_RERUN:" + reason, clock)
    payload.pop("completed_at", None)
    bakeoff.resume(payload, database)
    payload["preflight"]["checks"] = [c for c in payload["preflight"]["checks"] if c["provider_config_id"] in providers]
    return providers


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True, help="directory holding the bake-off ledger")
    parser.add_argument("--bakeoff-id", help="default: the only bake-off in the ledger")
    parser.add_argument("--rule", choices=sorted(REASONS), default="asr-unavailable", help="which cells to select")
    parser.add_argument("--apply", action="store_true", help="queue the rerun (default: list only)")
    args = parser.parse_args()
    database = database_for(args.data)
    store = bakeoff.BakeoffStore(database)
    with database.connection() as db:
        ids = [r["bakeoff_id"] for r in db.execute("SELECT bakeoff_id FROM harness_bakeoffs").fetchall()]
    identifier = args.bakeoff_id or (ids[0] if len(ids) == 1 else None)
    if identifier is None:
        sys.exit("several bake-offs in this ledger; pass --bakeoff-id: " + ", ".join(ids))
    payload = store.get(identifier)
    if payload["state"] not in {"completed", "paused"}:
        sys.exit(f"bake-off is {payload['state']}; run it to the end (or pause it) first")
    chosen = select(payload, database, args.rule)
    for cell, count, reasons in chosen:
        print(f'{cell["provider_config_id"]:22} {cell["scenario_id"]:42} rep {cell["repetition"]} '
              f'attempts {len(cell["attempts"])} {args.rule} x{count} {",".join(reasons)}')
    capped = [c for c, _, _ in chosen if len(c["attempts"]) >= bakeoff.MAX_ATTEMPTS]
    eligible = [c for c, _, _ in chosen if c not in capped]
    print(f"{len(chosen)} cell(s) selected; {len(eligible)} eligible, {len(capped)} at the attempt cap (left alone)")
    if not args.apply or not eligible:
        return
    providers = queue(payload, database, eligible, bakeoff.time.time, REASONS[args.rule])
    store.save(payload)
    print(json.dumps({"bakeoff_id": identifier, "state": payload["state"], "phase": payload["phase"],
                      "queued": len(eligible), "preflight_providers": providers}))


if __name__ == "__main__":
    main()
