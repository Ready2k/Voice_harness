"""Add the 28 October GPT-Live cells to the 29/30 October standard-prompt bake-off report, as a labelled carry-over.

    PYTHONPATH=src .venv/bin/python scripts/merge_gpt_live_into_standard.py

GPT-Live failed pre-flight under ``standardised-v2`` (``PREFLIGHT_VOICE_TOOL_INCOMPLETE``), so it has no run on the
standard prompt. Its 28 October result (``advanced-v1``, 40 scenarios x 3 repetitions, final claims-v2 re-score) is carried
over unchanged and scored by the same code as the other five; each carried observation is marked ``carried_over``.
Nothing is re-run. The one change to a carried observation is that its ``task_completion_excluded`` marker is dropped:
the 28 October re-score left ``commit-cancel-confirmed-001`` out of task completion for every provider, the standard
run did not, and a row scored on a different task-completion basis would not be comparable.

The original five-provider report is kept as ``report-five-provider.json`` (first run only; never overwritten).
Deterministic apart from the report timestamp, which is fixed.
"""

import copy
import json
import sqlite3
from pathlib import Path

from voice_harness import bakeoff
from voice_harness.bakeoff_export import render, report_data
from voice_harness.pricing import PricingTable

ROOT = Path(__file__).resolve().parents[1] / "artifacts"
STANDARD = ROOT / "bakeoff-2026-10-29-standard-5"
FAIR = ROOT / "fair-bakeoff-2026-10-28" / "rescored-2026-10-28" / "report.json"
LEDGER = ROOT.parent / ".local" / "fair-bakeoff-2026-10-28" / "bakeoff.sqlite"
PRICING = ROOT / "pricing"
USAGE_DATE = "2026-10-28"
COST_ASSUMPTION = ("GPT-Live was priced with its 29 October 2026 tariffs (gpt-live-1 $0.05 per minute; backend gpt-6-luna $0.10 / "
                   "$0.50 per 1M tokens) applied to 28 October usage, assuming the prices were the same; the tariffs are not "
                   "established for that date")
PROVIDER = "openai-live"
CARRIED = {"from": "fair-bakeoff-2026-10-28/rescored-2026-10-28", "prompt_profile": "advanced-v1",
           "reason": "PREFLIGHT_VOICE_TOOL_INCOMPLETE under standardised-v2"}


def backdated_table():
    """The 30 October vendor table with only the openai_live tariffs made effective from the 28 October usage date."""
    doc = json.loads((PRICING / "vendor-pricing-2026-10-30.json").read_text())
    for t in doc["tariffs"]:
        if t["provider"] == "openai_live":
            t["effective_from"] = USAGE_DATE
    doc["version"] = "2026-10-30+gpt-live-28oct"
    doc["status"] = "primary_source_partial_gpt_live_backdated"
    doc["note"] = ("DERIVED from vendor-pricing-2026-10-30.json by scripts/merge_gpt_live_into_standard.py: the openai_live tariffs "
                   f"(effective 2026-10-29) are made effective from {USAGE_DATE} so the 28 October GPT-Live runs can be priced. "
                   "Every price is unchanged; that they applied on 28 October is an assumption, not a vendor statement.")
    out = PRICING / "vendor-pricing-2026-10-30-gpt-live-28oct.json"
    out.write_text(json.dumps(doc, indent=2) + "\n")
    return PricingTable(doc)


def earlier_attempts():
    """{final run id: run ids of that cell's earlier, failed attempts}, from the stored 28 October payload."""
    cells = json.loads((ROOT / "fair-bakeoff-2026-10-28" / "payload.json").read_text())["cells"]
    return {c["observation"]["run_id"]: [a["run_id"] for a in c["attempts"] if a["run_id"] != c["observation"]["run_id"]]
            for c in cells if c.get("provider_config_id") == PROVIDER and c.get("observation")}


def reprice(o, table, db, earlier):
    """Re-derive a carried observation's voice cost from its stored usage events, read-only from the ledger."""
    def cost(run_id):
        events = [json.loads(p) for (p,) in db.execute("SELECT payload FROM harness_events WHERE run_id=? ORDER BY sequence", (run_id,))]
        return bakeoff.voice_cost(run_id, events, table)
    o["cost"] = {**cost(o["run_id"]), "assumption": COST_ASSUMPTION}
    o["earlier_costs"] = [{**cost(run_id), "assumption": COST_ASSUMPTION} for run_id in earlier[o["run_id"]]]


def main():
    five = STANDARD / "report-five-provider.json"
    if not five.exists():
        five.write_text((STANDARD / "report.json").read_text())
    base = json.loads(five.read_text())
    fair = json.loads(FAIR.read_text())
    table, db = backdated_table(), sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    earlier, carried = earlier_attempts(), []
    for o in fair["observations"]:
        if o["provider"] != PROVIDER:
            continue
        o = copy.deepcopy(o)
        o.pop("task_completion_excluded", None)
        o["carried_over"] = CARRIED
        reprice(o, table, db, earlier)
        carried.append(o)
    if len(carried) != 120:
        raise SystemExit(f"expected 120 GPT-Live cells, found {len(carried)}")
    weights = base["report"]["weights"]
    stored = json.loads((STANDARD / "payload.json").read_text())
    title = "Standard-prompt bake-off, six providers (GPT-Live carried over), normalised voices \u2014 29 October 2026"
    payload = {"name": title, "judge_configuration": stored["judge_configuration"], "providers": [PROVIDER] + base["providers"], "weights": weights,
               "repetitions": 3, "group_balanced": True, "state": "completed", "phase": "scored",
               "prompt_profile": "standardised-v2",
               "cells": [{"provider_config_id": o["provider"], "done": True, "observation": o}
                         for o in carried + base["observations"]]}
    options = {"title": title, "generated_at": "2026-10-30 (GPT-Live carried over from 2026-10-28)",
               "source": base["source"] + " · GPT-Live 1: 120 cells carried over from the 28 October fair bake-off "
                         "(advanced-v1 prompt; not run under standardised-v2)"}
    data = report_data(payload, **options)
    (STANDARD / "report.json").write_text(json.dumps(data, indent=1) + "\n")
    (STANDARD / "report.html").write_text(render(payload, **options))
    v = data["report"]["verdict"]
    print({k: v[k] for k in ("ranking", "leader", "runner_up", "no_clear_winner", "latency_leader")})
    for s in data["report"]["scores"]:
        print(s["provider"], round(s["composite"], 4))


main()
