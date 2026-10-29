"""Paired comparison of one provider set under two prompt profiles (e.g. standardised-v2 against advanced-v1).

    PYTHONPATH=src .venv/bin/python scripts/compare_prompt_profiles.py A/report.json B/report.json [--out DIR]

A and B are exported bake-off ``report.json`` files run over the same providers, scenarios and repetitions; A is the
baseline profile, B the one under test. For each provider, runs are paired on (scenario, repetition) and only pairs
completed in both are used. Reported per provider: pass rate under each profile, the B-minus-A pass-rate difference
with a scenario-cluster bootstrap interval, the exact McNemar p-value on the discordant pairs (Holm-adjusted across
providers), the paired runs needed to confirm the observed difference and the smallest difference detectable with the
pairs in hand, then median first-audio latency and cost per run. A null result is only meaningful next to the
detectable difference: with few discordant pairs it says the run was too small, not that the profiles are equal.
Deterministic: the same inputs give byte-identical output. No ranking, no verdict on which profile is better.
"""

import argparse
import json
import statistics
from pathlib import Path

from voice_harness import bakeoff, bakeoff_stats


def load(path):
    saved = json.loads(Path(path).read_text())
    return saved["observations"]


def _index(observations):
    return {(o["provider"], o["scenario_id"], o["repetition"]): o for o in observations if o["completed"]}


def _median(values):
    return statistics.median(values) if values else None


def _first_audio(runs):
    return [t["first_audio_ms"] for o in runs for t in o["turns"] if t.get("first_audio_ms") is not None]


def _cost(runs):
    costs = [float(o["cost"]["known_usd"]) for o in runs if o.get("cost", {}).get("priced") and o["cost"].get("known_usd") is not None]
    return statistics.fmean(costs) if costs else None


def compare(a_observations, b_observations):
    a, b = _index(a_observations), _index(b_observations)
    providers = sorted({p for p, *_ in a} & {p for p, *_ in b})
    rows = []
    for provider in providers:
        keys = sorted(k[1:] for k in a if k[0] == provider and (provider, *k[1:]) in b)
        pairs = [(k[0], a[(provider, *k)]["outcome"] == "pass", b[(provider, *k)]["outcome"] == "pass") for k in keys]
        a_pass, b_pass = [x[1] for x in pairs], [x[2] for x in pairs]
        a_only = sum(x and not y for _, x, y in pairs)  # passed only under the baseline
        b_only = sum(y and not x for _, x, y in pairs)  # passed only under the tested profile
        # pair_effect is (first minus second); the tested profile goes first so the difference reads B minus A.
        effect = bakeoff_stats.pair_effect([(s, y, x) for s, x, y in pairs], b_only, a_only)
        a_runs, b_runs = [a[(provider, *k)] for k in keys], [b[(provider, *k)] for k in keys]
        rows.append({"provider": provider, "n_pairs": len(pairs),
                     "a_pass_rate": sum(a_pass) / len(pairs) if pairs else None, "b_pass_rate": sum(b_pass) / len(pairs) if pairs else None,
                     "a_only": a_only, "b_only": b_only, "p_value": bakeoff.mcnemar_exact(a_only, b_only), **effect,
                     "a_median_first_audio_ms": _median(_first_audio(a_runs)), "b_median_first_audio_ms": _median(_first_audio(b_runs)),
                     "a_cost_usd_per_run": _cost(a_runs), "b_cost_usd_per_run": _cost(b_runs)})
    for row, adjusted in zip(rows, bakeoff_stats.holm_adjust([r["p_value"] for r in rows]), strict=True):
        row["p_adjusted"] = adjusted
    return {"a_only_providers": sorted({p for p, *_ in a} - set(providers)),
            "b_only_providers": sorted({p for p, *_ in b} - set(providers)), "providers": rows}


def _fmt(value, spec="{:.3f}"):
    return "n/a" if value is None else spec.format(value)


def markdown(result, a_label, b_label):
    lines = [f"# Prompt profile comparison: {b_label} against {a_label}", "",
             "| Provider | Pairs | Pass A | Pass B | B-A diff (95% CI) | A only / B only | p (Holm) | Min detectable | Median first audio A / B ms | $/run A / B |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in result["providers"]:
        diff = f"{_fmt(r['pass_rate_diff'], '{:+.3f}')} ({_fmt(r['diff_lo'], '{:+.3f}')}, {_fmt(r['diff_hi'], '{:+.3f}')})"
        lines.append(f"| {r['provider']} | {r['n_pairs']} | {_fmt(r['a_pass_rate'])} | {_fmt(r['b_pass_rate'])} | {diff} | "
                     f"{r['a_only']} / {r['b_only']} | {_fmt(r['p_adjusted'])} | {_fmt(r['min_detectable_diff_80'])} | "
                     f"{_fmt(r['a_median_first_audio_ms'], '{:.0f}')} / {_fmt(r['b_median_first_audio_ms'], '{:.0f}')} | "
                     f"{_fmt(r['a_cost_usd_per_run'], '{:.4f}')} / {_fmt(r['b_cost_usd_per_run'], '{:.4f}')} |")
    for key, label in (("a_only_providers", a_label), ("b_only_providers", b_label)):
        if result[key]:
            lines += ["", f"Only in {label}, not compared: {', '.join(result[key])}"]
    lines += ["", "Min detectable = smallest pass-rate difference the pairs in hand could confirm at 80% power; a difference "
              "below it is not evidence of equality."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("baseline")
    parser.add_argument("tested")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = compare(load(args.baseline), load(args.tested))
    labels = [json.loads(Path(p).read_text()).get("title", p) for p in (args.baseline, args.tested)]
    text = markdown(result, *labels)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "comparison.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        (args.out / "comparison.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
