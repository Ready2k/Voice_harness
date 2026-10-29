"""Driver for the PostgreSQL-outage chaos run (`make chaos-postgres`): keeps issuing tool calls with client-style
retries while the host stops and restarts the database, then checks the ledger. Exit 0 only if the outage was hit
and recovered with no lost, duplicated or unverifiable evidence."""

import collections
import json
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import RunConfig
from .engine import Engine
from .evidence import Evidence, verify_bundle
from .storage import Database

BALANCE = ("get_account_summary", {"account_id": "acct_1001"})


def drive(url, seconds, stop_after_calls=100000):
    database = Database(url)
    engine = Engine(database)
    errors = collections.Counter()
    longest_stall = 0.0

    def retrying(action):
        nonlocal longest_stall
        began = time.monotonic()
        while True:
            try:
                result = action()
            except Exception as error:  # noqa: BLE001 - the point is to ride out any storage failure
                errors[type(error).__name__] += 1
                time.sleep(0.2)
                continue
            longest_stall = max(longest_stall, time.monotonic() - began)
            return result

    run = retrying(lambda: engine.create(RunConfig().model_dump()))
    context = retrying(lambda: engine.context(run))
    retrying(lambda: engine.tool(run, context, "idv", "verify_identity", {"challenge_id": "challenge_001", "answer": "blue"}))
    acknowledged, deadline = [], time.monotonic() + seconds
    print(json.dumps({"event": "driving", "run": run}), flush=True)
    while time.monotonic() < deadline and len(acknowledged) < stop_after_calls:
        call_id = f"c{len(acknowledged)}"
        result = retrying(lambda call_id=call_id: engine.tool(run, context, call_id, *BALANCE))
        if result["status"] != "success":
            raise SystemExit(f"unexpected tool status {result['status']}")
        acknowledged.append(call_id)
        time.sleep(0.02)
    with TemporaryDirectory() as directory:
        events = retrying(lambda: database.events(run))
        bundle = retrying(lambda: Evidence(database, Path(directory)).bundle(run))
        verified = verify_bundle(bundle)
    requested = [e["content"]["call_id"] for e in events if e["type"] == "tool.requested" and e["content"]["call_id"].startswith("c")]
    sequences = [e["sequence"] for e in events]
    summary = {
        "calls_acknowledged": len(acknowledged),
        "outage_errors": dict(errors),
        "longest_stall_seconds": round(longest_stall, 2),
        "ledger_verified": verified,
        "exactly_once": sorted(requested) == sorted(acknowledged),
        "no_sequence_gaps": sequences == list(range(sequences[0], sequences[0] + len(sequences))),
    }
    summary["outage_was_hit"] = bool(errors)
    summary["passed"] = all(summary[k] for k in ("ledger_verified", "exactly_once", "no_sequence_gaps", "outage_was_hit"))
    return summary


def main():
    summary = drive(os.environ["HARNESS_TEST_DATABASE_URL"], float(sys.argv[1]) if len(sys.argv) > 1 else 30)
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
