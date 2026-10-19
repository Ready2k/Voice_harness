"""Flatten a provider_matrix.py results file into row files for the comparison dashboard.

    .venv/bin/python scripts/provider_matrix_rows.py artifacts/provider-matrix/matrix-2026-10-19.json

Writes cells.json (one row per scenario x provider) and criteria.json (one row per criterion verdict)
next to the input file.
"""

import json
import sys
from pathlib import Path


def main(path):
    source = Path(path)
    results = json.loads(source.read_text())
    scenarios = results["scenarios"]
    cells, criteria = [], []
    first_attempts = results.get("first_attempts", {})  # runs that did not complete and were retried once
    for key, cell in results["cells"].items():
        presentation = scenarios.get(cell["scenario_id"]) or {}
        assurance = cell.get("assurance") or {}
        completed = cell.get("state") == "completed"
        cells.append({
            "key": cell["scenario_id"] + "|" + cell["provider"],
            "scenario_id": cell["scenario_id"], "scenario": presentation.get("title", cell["scenario_id"]),
            "group": presentation.get("group", "Other"), "group_order": presentation.get("group_order", 99),
            "provider": cell["provider"], "run_id": cell.get("run_id"), "state": cell.get("state"),
            # A run that did not complete has no verdict of its own; it is reported by its failure reason.
            "outcome": assurance.get("overall_assurance") if completed else "run_failed",
            "model_behaviour": assurance.get("model_behaviour") if completed else None,
            "platform_enforcement": assurance.get("platform_enforcement") if completed else None,
            "failure": cell.get("failure"),
            "replies_assessed": (cell.get("replies") or {}).get("assessed", 0),
            "replies_unsafe": (cell.get("replies") or {}).get("unsafe", 0),
            "tools": " → ".join(f"{t['tool']} ({t['final'] or 'no result'})" for t in cell.get("tools") or []),
            "caller_turns": cell.get("caller_turns"), "wall_s": cell.get("wall_s"),
            "attempts": 2 if key in first_attempts else 1,
            "first_attempt_failure": (first_attempts.get(key) or {}).get("failure"),
        })
        for criterion, verdict in (cell.get("criteria") or {}).items():
            criteria.append({
                "key": cell["scenario_id"] + "|" + cell["provider"] + "|" + criterion,
                "scenario_id": cell["scenario_id"], "provider": cell["provider"], "criterion": criterion,
                "verdict": verdict["verdict"], "reasons": ", ".join(verdict["reasons"]),
            })
    cells.sort(key=lambda r: (r["group_order"], r["scenario"], r["provider"]))
    criteria.sort(key=lambda r: r["key"])
    (source.parent / "cells.json").write_text(json.dumps(cells, indent=1))
    (source.parent / "criteria.json").write_text(json.dumps(criteria, indent=1))
    print(f"{len(cells)} cells, {len(criteria)} criterion verdicts")


if __name__ == "__main__":
    main(sys.argv[1])
