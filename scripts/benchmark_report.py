"""Render a shareable bake-off report (one self-contained HTML file) from a provider_matrix.py results file.

    PYTHONPATH=src .venv/bin/python scripts/benchmark_report.py artifacts/provider-matrix/matrix-2026-10-19.json \
        reports/voice-provider-benchmark-2026-10-19.html --database .local/host-data/development.sqlite

Each cell is read from the run ledger (the runs must be in that database); retried cells keep their first attempt.
Voice cost is priced with --pricing-table (default: HARNESS_PRICING_TABLE, else the shipped table).
--cost-from RESULTS:DATABASE (repeatable) takes a cell's cost from a later run of the same scenario and provider
(for example after a pricing or region fix); the cell's outcome and latency stay those of the original run, and the
report says its cost comes from a separate run.
"""

import argparse
import json
from pathlib import Path

from voice_harness.bakeoff import DEFAULT_WEIGHTS, observe, voice_cost
from voice_harness.bakeoff_export import render
from voice_harness.pricing import load_table
from voice_harness.storage import Database


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results")
    parser.add_argument("out")
    parser.add_argument("--database", default=".local/host-data/development.sqlite")
    parser.add_argument("--title", default="Voice provider benchmark and bake-off")
    parser.add_argument("--pricing-table", default=None)
    parser.add_argument("--cost-from", action="append", default=[], metavar="RESULTS:DATABASE")
    args = parser.parse_args()
    results = json.loads(Path(args.results).read_text())
    database = Database(args.database)
    table = load_table(args.pricing_table)
    first_attempts = results.get("first_attempts", {})
    cells = []
    for key, cell in sorted(results["cells"].items()):
        earlier = first_attempts.get(key)
        attempts = ([{"run_id": earlier["run_id"], "failure": earlier.get("failure")}] if earlier else [])
        cells.append({"scenario_id": cell["scenario_id"], "provider_config_id": cell["provider"], "repetition": 0,
                      "attempts": [*attempts, {"run_id": cell.get("run_id"), "failure": cell.get("failure")}]})
    for cell in cells:
        cell["observation"], cell["done"] = observe(database, cell, table), True
    by_key = {(c["scenario_id"], c["provider_config_id"]): c for c in cells}
    for spec in args.cost_from:
        results_path, _, database_path = spec.rpartition(":")
        later, later_db = json.loads(Path(results_path).read_text()), Database(database_path)
        for cell in later["cells"].values():
            target = by_key.get((cell["scenario_id"], cell["provider"]))
            if target and cell.get("run_id") and target["observation"]:
                target["observation"]["cost"] = {**voice_cost(cell["run_id"], later_db.events(cell["run_id"]), table),
                                                 "separate_run": True, "cost_run_id": cell["run_id"]}
    payload = {"name": args.title, "providers": sorted({c["provider_config_id"] for c in cells}),
               "weights": DEFAULT_WEIGHTS, "cells": cells, "repetitions": 1}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(render(payload, source=Path(args.results).name))
    print(f"wrote {args.out} ({Path(args.out).stat().st_size // 1024} KB, {len(cells)} runs)")


if __name__ == "__main__":
    main()
