"""Repeatable commit-before-delivery recovery fixture."""

import uuid
from pathlib import Path

from .execution import ExecutionStore, MockResultReceiver


def run_recovery(database: str | Path) -> dict:
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    run_id = f"recovery_{uuid.uuid4().hex}"
    store = ExecutionStore(database)
    store.create_run(run_id)
    store.request(
        run_id,
        "c1",
        "fixture_adjust_balance",
        {"account_id": "acct_1001", "delta_minor": 100},
    )
    store.execute(run_id, "c1", owner_id="customer_001", verified=True)
    del store
    recovered = ExecutionStore(database)
    outcome = recovered.reconcile(run_id, "c1")
    retry = recovered.execute(run_id, "c1", owner_id="customer_001", verified=True)
    receiver = MockResultReceiver()
    receiver.deliver(outcome["result"])
    receiver.deliver(retry)
    cancellation = recovered.cancel(run_id, "c1")
    return {
        "run_id": run_id,
        "fixture": "commit-before-delivery@1",
        "state": outcome["state"],
        "balance_minor": retry["structured_content"]["balance_minor"],
        "logical_result_count": len(receiver.results),
        "cancel_after_commit_status": cancellation["status"],
        "audit": recovered.audit(run_id),
        "limitations": [
            "Embedded SQLite development backend",
            "Restart simulation; no forced process kill",
            "Operator fixture mutation; no payment or customer consent claim",
        ],
    }
