"""Graceful shutdown (SE-03): revoke audio, reconcile tools, flush evidence."""

NONTERMINAL = (
    "queued",
    "probing",
    "running",
    "evaluating",
    "awaiting_playback",
    "cancel_requested",
)


def _active(database):
    with database.connection() as db:
        rows = db.execute("SELECT run_id, state FROM harness_runs").fetchall()
    return [row["run_id"] for row in rows if row["state"] in NONTERMINAL]


def _settle(engine, database, run_id):
    """Revoke unreleased audio and settle tools; unresolved outcomes need recovery."""
    with database.transaction(run_id) as tx:
        if tx.state["state"] not in NONTERMINAL:
            return
        tx.event(
            "orchestrator",
            "run.shutdown_requested",
            {
                "revoked_tokens": len(tx.state["tokens"]),
                "discarded_units": sum(
                    u["state"] in {"pending", "approved", "scheduled"}
                    for u in tx.state["units"].values()
                ),
            },
        )
    if engine.cancel(run_id, "SERVER_SHUTDOWN") == "cancel_requested":
        # Tool outcomes remain unknown; a restart must reconcile them, not guess.
        with database.transaction(run_id) as tx:
            tx.state["state"] = "recovery_required"
            tx.event(
                "orchestrator",
                "run.recovery_required",
                {
                    "reason": "GRACEFUL_SHUTDOWN_UNRESOLVED_TOOLS",
                    "unresolved_call_ids": engine._unresolved(tx.state),
                },
            )


def graceful_shutdown(engine, evidence, database, executor):
    """Intake is already stopped by the caller. Settle every active run, drain
    workers, settle stragglers, then write a terminal manifest for each run."""
    touched = set()
    for run_id in _active(database):
        _settle(engine, database, run_id)
        touched.add(run_id)
    executor.shutdown(wait=True, cancel_futures=False)
    for run_id in _active(database):
        _settle(engine, database, run_id)
        touched.add(run_id)
    for run_id in sorted(touched):
        evidence.save_manifest(run_id)
