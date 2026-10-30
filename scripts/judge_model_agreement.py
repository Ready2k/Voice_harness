"""Re-judge a finished bake-off's runs with another OpenAI judge model and compare verdicts with the stored judges.

    PYTHONPATH=src .venv/bin/python scripts/judge_model_agreement.py \
        --data .local/bakeoff-2026-10-29-standard-5 --model gpt-6-luna --out artifacts/bakeoff-2026-10-29-standard-5/judge-luna.json

Read-only on the evidence database: for each completed run it rebuilds the judge request exactly as ``assess_run`` does,
sends it to the candidate model, and compares each judged criterion's verdict with the verdicts the run's judges stored
(``judge.offline_evaluated``). Nothing is written back to the runs. Needs OPENAI_API_KEY and
VOICE_HARNESS_LIVE_JUDGES=openai. Cost is computed from the usage the API reports (shipped tariff for the model).
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


def stored_verdicts(events):
    """{judge_id: {criterion_id: verdict}} from a run's judge.offline_evaluated events."""
    out = {}
    for e in events:
        if e["type"] == "judge.offline_evaluated":
            c = e["content"]
            out[c["judge"]["judge_id"]] = {r["criterion_id"]: r["verdict"] for r in c["results"]}
    return out


def rejudge(database, run_id, backend, tested_models):
    from voice_harness.judges import rubric
    from voice_harness.judges.offline import run_offline
    from voice_harness.judges.oracle import build_oracle
    from voice_harness.judges.rubric import EvalContext
    from voice_harness.judges.service import _identity, _plain, fixture_for, scenario_for

    state = database.get(run_id)
    events = _plain(database.events(run_id))
    scenario = scenario_for(state)
    applicable, _, _ = rubric.applicability(scenario, None)
    ctx = EvalContext(events, scenario, fixture_for(state))
    judge_criteria = [c for c in applicable if rubric.CRITERIA[c].judge_eligible]
    if not judge_criteria:
        return None
    identity = _identity(tested_models[state["config"]["provider_config_id"]])
    oracle = build_oracle(events, scenario=scenario, fixture=fixture_for(state), policy_version=state.get("policy_version", "strict-v1"),
                          criteria=judge_criteria, order_seed=0, identity=identity)
    backend.last_usage = None
    outcome = run_offline(backend, oracle, judge_criteria, ctx.by_id, evaluation_id="eval_agreement")
    return {"status": outcome["status"], "verdicts": {r["criterion_id"]: r["verdict"] for r in outcome["results"]},
            "usage": getattr(backend, "last_usage", None)}


def main():
    from voice_harness import live_providers
    from voice_harness.judges.backends import OpenAIBackend
    from voice_harness.pricing import load_table
    from voice_harness.storage import Database

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--baseline", default="openai-gpt-6-sol", help="stored judge_id to compare against")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    os.environ.setdefault("VOICE_HARNESS_LIVE_JUDGES", "openai")
    database = Database(str((args.data / "bakeoff.sqlite").resolve()))
    with database.connection() as db:
        run_ids = [r["run_id"] for r in db.execute("SELECT run_id FROM harness_runs ORDER BY run_id").fetchall()]
    run_ids = [r for r in run_ids if database.get(r)["state"] == "completed"][: args.limit]
    table = load_table(ROOT / "artifacts/pricing/vendor-pricing-2026-10-30.json")
    price = {t.meter_id: t for t in table.tariffs if t.provider == "openai" and t.service == "judge" and t.model == args.model}
    tested = live_providers.TESTED_MODELS

    def work(run_id):
        backend = OpenAIBackend(args.model)  # one per call: last_usage is per instance
        try:
            return run_id, rejudge(database, run_id, backend, tested), None
        except Exception as exc:  # noqa: BLE001 - report, keep going
            return run_id, None, type(exc).__name__

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(work, run_ids))
    agree = collections.Counter()
    confusion = collections.Counter()
    other = collections.defaultdict(collections.Counter)
    tokens = {"input": 0, "output": 0, "cached": 0}
    errors = collections.Counter()
    runs_compared = 0
    for run_id, got, err in results:
        if err or not got:
            errors[err or "NO_JUDGE_CRITERIA"] += 1
            continue
        stored = stored_verdicts(database.events(run_id))
        base = stored.get(args.baseline)
        if not base:
            errors["NO_BASELINE_VERDICTS"] += 1
            continue
        runs_compared += 1
        for k, v in (got["usage"] or {}).items():
            tokens[{"input_tokens": "input", "output_tokens": "output", "cached_tokens": "cached"}[k]] += v or 0
        for cid, verdict in got["verdicts"].items():
            if cid not in base:
                continue
            agree[(cid, verdict == base[cid])] += 1
            confusion[(base[cid], verdict)] += 1
            for jid, theirs in stored.items():
                if jid != args.baseline and cid in theirs:
                    other[jid][("candidate_agrees_with_other" if verdict == theirs[cid] else "candidate_differs_from_other",
                                "baseline_agrees_with_other" if base[cid] == theirs[cid] else "baseline_differs_from_other")] += 1
    total = sum(agree.values())
    same = sum(v for (c, ok), v in agree.items() if ok)
    cost = None
    if price.get("judge_input_tokens") and price.get("judge_output_tokens"):
        pi, po = price["judge_input_tokens"], price["judge_output_tokens"]
        uncached = tokens["input"] - tokens["cached"]
        cost = round(uncached * float(pi.unit_price) / 1e6 + tokens["cached"] * float(pi.cached_unit_price or pi.unit_price) / 1e6
                     + tokens["output"] * float(po.unit_price) / 1e6, 4)
    report = {"candidate": args.model, "baseline": args.baseline, "runs_requested": len(run_ids), "runs_compared": runs_compared,
              "criteria_compared": total, "agreement": round(same / total, 4) if total else None,
              "by_criterion": {c: {"n": agree[(c, True)] + agree[(c, False)], "agree": agree[(c, True)]}
                               for c in sorted({c for c, _ in agree})},
              "baseline_to_candidate": {f"{b} -> {c}": n for (b, c), n in sorted(confusion.items()) if b != c},
              "against_other_judges": {j: {f"{a} / {b}": n for (a, b), n in sorted(v.items())} for j, v in other.items()},
              "tokens": tokens, "cost_usd": cost, "errors": dict(errors)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
