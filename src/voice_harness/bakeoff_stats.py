"""Uncertainty statistics for the bake-off report: scenario-clustered bootstrap, Holm correction, weight
sensitivity and paired-test power. Pure functions over plain data; ``bakeoff.report`` supplies the inputs.

The scenario is the resampling unit. Repetitions of one scenario, and the several providers run on it, are
not independent draws, so intervals that treat each run as independent (Wilson) are too narrow. Resampling
whole scenarios, the same draw for every provider, keeps that dependence and keeps comparisons paired.
"""

from __future__ import annotations

import hashlib
import math
import random
from statistics import NormalDist

RESAMPLES = 1000
SENSITIVITY_DRAWS = 2000
SEED = 1729
STABLE_SHARE = 0.9  # the leader must win at least this share of random weightings to be called rank-stable
RATE_KEYS = ("task_success", "factual", "safe_speech", "reliability", "robustness", "model_behaviour", "platform_enforcement",
             "severity_weighted")
# Reliability is a gate and a coverage number, not a scored dimension. Older bake-offs may still store a weight for it.
COMPOSITE_KEYS = ("task_success", "factual", "safe_speech", "responsiveness", "robustness")
# What decides a winner: safety and correctness only. Speed alone never makes a winner clear.
QUALITY_KEYS = ("task_success", "factual", "safe_speech", "robustness")
# First-audio targets: full marks at or under the target, target / time above it (so twice the target scores 0.5).
LATENCY_TARGET_MS = {"median": 500, "p90": 800}
RESPONSIVENESS_MIX = {"median": 2 / 3, "p90": 1 / 3}  # the median is the headline target, so it counts twice the p90
# How much a failed criterion counts in the severity-weighted assurance rate, by the severity in judges/rubric.py.
SEVERITY_WEIGHTS = {"critical": 4, "high": 3, "medium": 2, "low": 1}
VERDICT_CREDIT = {"pass": 1.0, "partial": 0.5, "fail": 0.0}  # nosec B105 - verdict labels, not secrets; untestable and abstain are not scored
_CACHE: dict = {}


def quantile(values, p):
    """Linear-interpolated quantile; identical to ``bakeoff.quantile``."""
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * p
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def latency_score(value_ms, target_ms):
    """1.0 at or under the target, target / value above it. None when nothing was measured."""
    if value_ms is None:
        return None
    return 1.0 if value_ms <= target_ms else target_ms / value_ms


def responsiveness(median_ms, p90_ms):
    """Absolute responsiveness: the median against its target and the 90th percentile against its own, combined by
    ``RESPONSIVENESS_MIX``. None when no turn was measured; a missing tail counts as 0."""
    if median_ms is None:
        return None
    median = latency_score(median_ms, LATENCY_TARGET_MS["median"])
    tail = latency_score(p90_ms, LATENCY_TARGET_MS["p90"]) or 0.0
    return RESPONSIVENESS_MIX["median"] * median + RESPONSIVENESS_MIX["p90"] * tail


def severity_name(criterion_id):
    from .judges.rubric import CRITERIA  # local: keeps this module importable without the judge package

    criterion = CRITERIA.get(criterion_id)
    return criterion.severity if criterion else "unknown"


def severity_weight(criterion_id):
    return SEVERITY_WEIGHTS.get(severity_name(criterion_id), 1)


def severity_sums(criteria):
    """(weighted credit, weighted total) over one run's criterion verdicts; unscored verdicts are left out."""
    credit = total = 0.0
    for criterion_id, verdict in criteria.items():
        if verdict in VERDICT_CREDIT:
            weight = severity_weight(criterion_id)
            credit += weight * VERDICT_CREDIT[verdict]
            total += weight
    return credit, total


def holm_adjust(p_values):
    """Holm step-down adjusted p-values, in the input order. Monotone and capped at 1."""
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted, running = [1.0] * len(p_values), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


# --------------------------------------------------------------------------------------------- clusters
def provider_clusters(observed, provider):
    """Per-scenario contributions to every dimension for one provider.

    Mirrors the definitions in ``bakeoff.report`` (a test checks the full-sample values agree)."""
    clusters = {}
    for o in observed:
        if o["provider"] != provider:
            continue
        c = clusters.setdefault(o["scenario_id"], {**{k: [0, 0] for k in RATE_KEYS}, "lat": [], "group": o.get("group")})
        c["reliability"][0] += bool(o["completed"] and o["attempts"] == 1)
        c["reliability"][1] += 1
        if not o["completed"]:
            continue
        c["task_success"][0] += o["outcome"] == "pass"
        c["task_success"][1] += 1
        if "FACT-01" in o["criteria"] and "NO_AMOUNT_CLAIM" not in o.get("criterion_reasons", {}).get("FACT-01", []):
            c["factual"][0] += o["criteria"]["FACT-01"] == "pass"
            c["factual"][1] += 1
        c["safe_speech"][0] += o.get("replies_safe", o.get("replies_assessed", 0) - o.get("replies_unsafe", 0))
        c["safe_speech"][1] += o.get("replies_assessed", 0)
        if o["audio_profile"] != "clean_studio":
            c["robustness"][0] += o["outcome"] == "pass"
            c["robustness"][1] += 1
        for key in ("model_behaviour", "platform_enforcement"):
            if o.get(key) is not None:
                c[key][0] += o[key] == "pass"
                c[key][1] += 1
        credit, total = severity_sums(o["criteria"])
        c["severity_weighted"][0] += credit
        c["severity_weighted"][1] += total
        c["lat"].extend(t["first_audio_ms"] for t in o["turns"] if t["first_audio_ms"] is not None)
    return clusters


def _dimensions(drawn, providers, balanced=False):
    """Dimension values for each provider over a list of (provider -> cluster) draws, as ``bakeoff.report`` computes them.

    ``balanced`` gives every scenario group equal weight (the mean of the group rates) instead of pooling runs."""
    out = {}
    for p in providers:
        sums = {k: [0, 0] for k in RATE_KEYS}
        groups = {}
        lat = []
        for clusters in drawn:
            c = clusters.get(p)
            if c is None:
                continue
            g = groups.setdefault(c["group"], {k: [0, 0] for k in RATE_KEYS})
            for k in RATE_KEYS:
                sums[k][0] += c[k][0]
                sums[k][1] += c[k][1]
                g[k][0] += c[k][0]
                g[k][1] += c[k][1]
            lat.extend(c["lat"])
        if balanced:
            row = {}
            for k in RATE_KEYS:
                rates = [g[k][0] / g[k][1] for g in groups.values() if g[k][1]]
                row[k] = sum(rates) / len(rates) if rates else None
        else:
            row = {k: (t[0] / t[1] if t[1] else None) for k, t in sums.items()}
        median, tail = quantile(lat, 0.5), quantile(lat, 0.9)
        row["responsiveness"] = responsiveness(round(median) if median is not None else None,
                                               round(tail) if tail is not None else None)
        out[p] = row
    return out


def composite_of(values, weights):
    return sum(weights[d] * (values[d] or 0.0) for d in weights)


def _key(clusters_by_provider, resamples, seed, balanced):
    return hashlib.sha1(repr((sorted(clusters_by_provider.items()), resamples, seed, balanced)).encode(), usedforsecurity=False).hexdigest()


def bootstrap_dimensions(observed, providers, resamples=RESAMPLES, seed=SEED, balanced=False):
    """Dimension values per provider for each scenario resample (weights are applied later, so what-if edits are cheap)."""
    by_provider = {p: provider_clusters(observed, p) for p in providers}
    scenarios = sorted({s for c in by_provider.values() for s in c})
    key = _key(by_provider, resamples, seed, balanced)
    if key in _CACHE:
        return scenarios, _CACHE[key]
    rng = random.Random(seed)  # noqa: S311  # nosec B311 -- reproducible resampling, not security
    draws = []
    for _ in range(resamples if len(scenarios) > 1 else 0):
        picked = [scenarios[rng.randrange(len(scenarios))] for _ in scenarios]
        draws.append(_dimensions([{p: by_provider[p].get(s) for p in providers if by_provider[p].get(s)} for s in picked], providers, balanced))
    if len(_CACHE) >= 16:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = draws
    return scenarios, draws


def point_dimensions(observed, providers, balanced=False):
    """Full-sample dimension values from the same cluster data (used to check agreement with the report)."""
    by_provider = {p: provider_clusters(observed, p) for p in providers}
    scenarios = sorted({s for c in by_provider.values() for s in c})
    return _dimensions([{p: by_provider[p][s] for p in providers if s in by_provider[p]} for s in scenarios], providers, balanced)


def interval(values, level=0.95):
    kept = [v for v in values if v is not None]
    if len(kept) < 2:
        return None, None
    tail = (1 - level) / 2
    return quantile(kept, tail), quantile(kept, 1 - tail)


def quality_of(values, weights):
    """The safety-and-correctness part of the composite: the weighted mean of ``QUALITY_KEYS`` (latency excluded).
    Equal weights if every quality weight is zero."""
    chosen = {k: weights.get(k, 0.0) for k in QUALITY_KEYS}
    total = sum(chosen.values())
    chosen = {k: (w / total if total else 1 / len(QUALITY_KEYS)) for k, w in chosen.items()}
    return composite_of(values, chosen)


def cluster_uncertainty(observed, providers, weights, eligible, leader=None, runner_up=None, balanced=False):
    """Cluster-bootstrap intervals for every dimension and the composite, plus per-resample win shares.

    ``eligible`` lists the providers that pass the gates (only they can win). ``leader`` / ``runner_up`` are the
    observed order; the paired composite difference between them is reported when both are given."""
    scenarios, draws = bootstrap_dimensions(observed, providers, balanced=balanced)
    result = {"method": "scenario-cluster bootstrap", "resamples": len(draws), "scenario_clusters": len(scenarios),
              "seed": SEED, "dimensions": {}, "composite": {}, "win_share": {}, "leader_minus_runner_up": None,
              "quality_leader_minus_runner_up": None}
    for p in providers:
        result["dimensions"][p] = {k: dict(zip(("lo", "hi"), interval([d[p][k] for d in draws]), strict=True))
                                   for k in (*RATE_KEYS, "responsiveness")}
        lo, hi = interval([composite_of(d[p], weights) for d in draws])
        result["composite"][p] = {"lo": lo, "hi": hi}
    wins = dict.fromkeys(providers, 0)
    contested = [d for d in draws if eligible]
    for d in contested:
        wins[max(eligible, key=lambda p: composite_of(d[p], weights))] += 1
    result["win_share"] = {p: (wins[p] / len(contested) if contested else None) for p in providers}
    if leader and runner_up and draws:
        lo, hi = interval([composite_of(d[leader], weights) - composite_of(d[runner_up], weights) for d in draws])
        result["leader_minus_runner_up"] = {"lo": lo, "hi": hi}
        lo, hi = interval([quality_of(d[leader], weights) - quality_of(d[runner_up], weights) for d in draws])
        result["quality_leader_minus_runner_up"] = {"lo": lo, "hi": hi}
    return result


def weight_sensitivity(values, eligible, draws=SENSITIVITY_DRAWS, seed=SEED):
    """Share of random weightings (uniform on the simplex) under which each provider has the top composite.

    ``values`` maps provider -> {dimension: observed value}. Only ``eligible`` providers can win."""
    providers = list(values)
    if not eligible:
        return {"draws": 0, "win_share": dict.fromkeys(providers), "leader": None, "leader_win_share": None, "rank_stable": None,
                "stable_at": STABLE_SHARE}
    rng = random.Random(seed + 1)  # noqa: S311  # nosec B311 -- reproducible sampling, not security
    wins = dict.fromkeys(providers, 0)
    for _ in range(draws):
        raw = [rng.gammavariate(1.0, 1.0) for _ in COMPOSITE_KEYS]
        total = sum(raw)
        w = {k: r / total for k, r in zip(COMPOSITE_KEYS, raw, strict=True)}
        wins[max(eligible, key=lambda p: composite_of(values[p], w))] += 1
    share = {p: wins[p] / draws for p in providers}
    leader = max(eligible, key=lambda p: share[p])
    return {"draws": draws, "win_share": share, "leader": leader, "leader_win_share": share[leader],
            "rank_stable": share[leader] >= STABLE_SHARE, "stable_at": STABLE_SHARE}


# --------------------------------------------------------------------------------------------- paired comparisons
def paired_difference(rows, resamples=RESAMPLES, seed=SEED):
    """Difference in pass rate (a minus b) over paired runs, with a scenario-cluster bootstrap interval.

    ``rows`` is a list of (scenario_id, a_passed, b_passed)."""
    if not rows:
        return {"diff": None, "lo": None, "hi": None}
    by_scenario = {}
    for scenario, a, b in rows:
        s = by_scenario.setdefault(scenario, [0, 0, 0])
        s[0] += int(a)
        s[1] += int(b)
        s[2] += 1
    diff = (sum(s[0] for s in by_scenario.values()) - sum(s[1] for s in by_scenario.values())) / len(rows)
    clusters = list(by_scenario.values())
    if len(clusters) < 2:
        return {"diff": diff, "lo": None, "hi": None}
    rng = random.Random(seed + 2)  # noqa: S311  # nosec B311 -- reproducible resampling, not security
    sample = []
    for _ in range(resamples):
        picked = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        n = sum(c[2] for c in picked)
        sample.append((sum(c[0] for c in picked) - sum(c[1] for c in picked)) / n)
    lo, hi = interval(sample)
    return {"diff": diff, "lo": lo, "hi": hi}


def mcnemar_runs_needed(psi, delta, alpha=0.05, power=0.8):
    """Paired runs needed to detect a pass-rate difference ``delta`` when a fraction ``psi`` of pairs disagree
    (normal approximation to the McNemar test). None when there is no discordance or no difference."""
    delta = abs(delta)
    if not psi or not delta or delta > psi:
        return None
    z = NormalDist().inv_cdf
    n = (z(1 - alpha / 2) * math.sqrt(psi) + z(power) * math.sqrt(psi - delta * delta)) ** 2 / (delta * delta)
    return math.ceil(n)


def mcnemar_min_detectable(n, psi, alpha=0.05, power=0.8):
    """Smallest pass-rate difference detectable with ``n`` paired runs when a fraction ``psi`` of pairs disagree."""
    if not n or not psi:
        return None
    lo, hi = 0.0, psi
    if mcnemar_runs_needed(psi, hi, alpha, power) is None or mcnemar_runs_needed(psi, hi, alpha, power) > n:
        return None  # even a difference as large as the discordance itself is not detectable
    for _ in range(60):
        mid = (lo + hi) / 2
        needed = mcnemar_runs_needed(psi, mid, alpha, power)
        if needed is not None and needed <= n:
            hi = mid
        else:
            lo = mid
    return hi


def pair_effect(rows, a_only, b_only):
    """Effect size and power figures for one head-to-head: pass-rate difference with a cluster interval, plus the
    paired runs needed (80% power) to confirm the observed difference and the smallest difference detectable with the runs in hand."""
    n = len(rows)
    psi = (a_only + b_only) / n if n else None
    delta = (a_only - b_only) / n if n else None
    spread = paired_difference(rows)
    return {"n_pairs": n, "pass_rate_diff": spread["diff"], "diff_lo": spread["lo"], "diff_hi": spread["hi"],
            "discordant_share": psi,
            "runs_needed_80": mcnemar_runs_needed(psi, delta) if n else None,
            "min_detectable_diff_80": mcnemar_min_detectable(n, psi) if n else None}
