"""Queue a rerun of the scored cells whose independent ASR was unavailable, in a finished bake-off.

    PYTHONPATH=src .venv/bin/python scripts/rerun_asr_blocked_cells.py --data .local/bakeoff-2026-10-29-standard-5
    PYTHONPATH=src .venv/bin/python scripts/rerun_asr_blocked_cells.py --data ... --apply

An ``ASR_HTTP_429`` during assessment leaves a reply unchecked (FACT-01 ``untestable`` or ``review_required``) although the run
completed, so ``--resume`` never requeues it and the reply audio is not retained to re-transcribe. The selection rule is
ledger-based and independent of how the provider did: every scored cell whose final attempt recorded an
``assessment.asr_unavailable`` event, for every provider alike. Without ``--apply`` it only lists them.

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

REASON = "ASR_UNAVAILABLE"


def database_for(data):
    legacy = data.resolve() / "bakeoff.sqlite"
    return Database(str(legacy if legacy.exists() else data.resolve() / "development.sqlite"))


def select(payload, database):
    """Scored cells whose final attempt's ledger holds an ``assessment.asr_unavailable`` event, with the event count."""
    chosen = []
    for cell in payload["cells"]:
        final = cell["attempts"][-1] if cell["attempts"] else {}
        if not cell.get("done") or not final.get("run_id"):
            continue
        blocked = [e for e in database.events(final["run_id"]) if e["type"] == "assessment.asr_unavailable"]
        if blocked:
            chosen.append((cell, len(blocked), sorted({e["content"].get("reason") for e in blocked})))
    return chosen


def queue(payload, database, cells, clock):
    """Reset the chosen cells and move the plan to a fresh pre-flight (the same steps as ``bakeoff.resume``)."""
    for cell in cells:
        cell["attempts"][-1]["rerun_reason"] = REASON
        cell.pop("observation", None)
        cell.pop("check_failure", None)
        cell["done"] = False
        cell["attempts"].append({"pending": True})
    providers = sorted({c["provider_config_id"] for c in cells})
    bakeoff.pause(payload, providers[0], "TARGETED_RERUN:" + REASON, clock)
    payload.pop("completed_at", None)
    bakeoff.resume(payload, database)
    payload["preflight"]["checks"] = [c for c in payload["preflight"]["checks"] if c["provider_config_id"] in providers]
    return providers


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True, help="directory holding the bake-off ledger")
    parser.add_argument("--bakeoff-id", help="default: the only bake-off in the ledger")
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
    chosen = select(payload, database)
    for cell, count, reasons in chosen:
        print(f'{cell["provider_config_id"]:22} {cell["scenario_id"]:42} rep {cell["repetition"]} '
              f'attempts {len(cell["attempts"])} asr_unavailable x{count} {",".join(reasons)}')
    capped = [c for c, _, _ in chosen if len(c["attempts"]) >= bakeoff.MAX_ATTEMPTS]
    eligible = [c for c, _, _ in chosen if c not in capped]
    print(f"{len(chosen)} cell(s) selected; {len(eligible)} eligible, {len(capped)} at the attempt cap (left alone)")
    if not args.apply or not eligible:
        return
    providers = queue(payload, database, eligible, bakeoff.time.time)
    store.save(payload)
    print(json.dumps({"bakeoff_id": identifier, "state": payload["state"], "phase": payload["phase"],
                      "queued": len(eligible), "preflight_providers": providers}))


if __name__ == "__main__":
    main()
