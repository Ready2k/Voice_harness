"""Judge CARE-01 (customer care and handoff) on a finished bake-off that ran without judges. Read-only.

    PYTHONPATH=src .venv/bin/python scripts/judge_care01_supplement.py \
        --data .local/combined-2026-11-03/baseline-default/data --payload artifacts/bakeoff-2026-11-03-v3-six/payload.json \
        --out artifacts/bakeoff-2026-11-03-v3-six/care01-supplement.json [--limit N]

For each scored cell whose final attempt has a judge-eligible criterion (CARE-01), rebuilds the judge request exactly as
``assess_run`` does (blinded oracle, same rubric) and asks each judge. Nothing is written to the evidence database or the
published report: the verdicts, tokens and failures go to ``--out`` only. Needs OPENAI_API_KEY and an AWS session for Bedrock.
"""
import argparse
import collections
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from voice_harness import live_providers  # noqa: E402
from voice_harness.adapters.nova_sonic import SESSION_ENV  # noqa: E402
from voice_harness.judges.backends import build_backend  # noqa: E402
from voice_harness.storage import Database  # noqa: E402

JUDGES = ({"backend": "openai"}, {"backend": "bedrock"})
CRITERION = "CARE-01"


def applicable_care(database, run_id):
    """(state, events, scenario, ctx) when CARE-01 applies to the run and is judge-eligible, else None."""
    from voice_harness.judges import rubric
    from voice_harness.judges.rubric import EvalContext
    from voice_harness.judges.service import _plain, fixture_for, scenario_for

    state = database.get(run_id)
    events = _plain(database.events(run_id))
    scenario = scenario_for(state)
    applicable, _, _ = rubric.applicability(scenario, None)
    if CRITERION not in applicable or not rubric.CRITERIA[CRITERION].judge_eligible:
        return None
    return state, events, scenario, EvalContext(events, scenario, fixture_for(state))


def judge_care(database, run_id, backend, tested_models, found):
    """CARE-01 verdict from one judge for one run: the request ``assess_run`` would build, for this criterion only."""
    from voice_harness.judges.offline import run_offline
    from voice_harness.judges.oracle import build_oracle
    from voice_harness.judges.service import _identity, fixture_for

    state, events, scenario, ctx = found
    identity = _identity(tested_models[state["config"]["provider_config_id"]])
    oracle = build_oracle(events, scenario=scenario, fixture=fixture_for(state), policy_version=state.get("policy_version", "strict-v1"),
                          criteria=[CRITERION], order_seed=0, identity=identity)
    backend.last_usage = None
    outcome = run_offline(backend, oracle, [CRITERION], ctx.by_id, evaluation_id="eval_care01_supplement")
    return {"status": outcome["status"], "error_code": outcome["error_code"],
            "verdicts": {r["criterion_id"]: r["verdict"] for r in outcome["results"]}, "usage": getattr(backend, "last_usage", None)}


def load_env():
    for path in (ROOT / ".env", SESSION_ENV):
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip().removeprefix("export ")
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ[key] = value.strip("'\"")
    os.environ["VOICE_HARNESS_LIVE_JUDGES"] = "openai,bedrock"
    os.environ.setdefault("VOICE_HARNESS_OPENAI_JUDGE_MODEL", "gpt-6-sol")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--count-only", action="store_true", help="count the runs CARE-01 applies to; no judge calls")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    load_env()
    database = Database(str((args.data / "development.sqlite").resolve()))
    cells = [c for c in json.loads(args.payload.read_text())["cells"] if c.get("done") and c["attempts"][-1].get("run_id")]
    tested = live_providers.TESTED_MODELS
    if args.count_only:
        print(collections.Counter(c["provider_config_id"] for c in cells if applicable_care(database, c["attempts"][-1]["run_id"])))
        return

    def work(cell):
        run_id = cell["attempts"][-1]["run_id"]
        found = applicable_care(database, run_id)
        if found is None:
            return None
        row = {"run_id": run_id, "provider": cell["provider_config_id"], "scenario": cell["scenario_id"],
               "repetition": cell.get("repetition"), "judges": {}}
        for spec in JUDGES:
            backend = build_backend(spec)
            try:
                got = judge_care(database, run_id, backend, tested, found)
            except Exception as exc:  # noqa: BLE001 - report the failure, never turn it into a verdict
                row["judges"][backend.config.judge_id] = {"status": "error", "error": type(exc).__name__}
                continue
            row["judges"][backend.config.judge_id] = {"model_id": backend.config.model_id, "family": backend.config.family,
                                                      **got}
        return row

    with ThreadPoolExecutor(args.workers) as pool:
        rows = [r for r in pool.map(work, cells[: args.limit] if args.limit else cells) if r]
    verdicts = collections.defaultdict(collections.Counter)
    tokens = collections.defaultdict(lambda: collections.Counter())
    for row in rows:
        for jid, j in row["judges"].items():
            verdicts[(row["provider"], jid)][(j.get("verdicts") or {}).get(CRITERION) or j.get("error") or j.get("error_code") or j.get("status", "error")] += 1
            for k, v in (j.get("usage") or {}).items():
                tokens[jid][k] += v or 0
    from voice_harness.pricing import load_table

    table = load_table(ROOT / "artifacts/pricing/vendor-pricing-2026-10-30.json")
    cost = {}
    for jid, t in tokens.items():
        model = next(r["judges"][jid]["model_id"] for r in rows if jid in r["judges"] and "model_id" in r["judges"][jid])
        price = {x.meter_id: x for x in table.tariffs if x.service == "judge" and x.model == model}
        if price.get("judge_input_tokens") and price.get("judge_output_tokens"):
            cost[jid] = round(t["input_tokens"] * float(price["judge_input_tokens"].unit_price) / 1e6
                              + t["output_tokens"] * float(price["judge_output_tokens"].unit_price) / 1e6, 4)
    report = {"data": str(args.data), "cost_usd": cost, "cost_basis": "tokens x vendor-pricing-2026-10-30; no cache discount or write premium", "runs_judged": len(rows), "criterion": CRITERION,
              "by_provider_and_judge": {f"{p} | {j}": dict(c) for (p, j), c in sorted(verdicts.items())},
              "tokens": {j: dict(t) for j, t in tokens.items()}, "rows": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
