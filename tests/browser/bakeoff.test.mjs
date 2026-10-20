import test from "node:test";
import assert from "node:assert/strict";
import {conditionRows, confidenceNote, costIsEmpty, costLimit, costRows, estimateCost, intervalFloor, latencyCap, ms, normaliseWeights, outcomeClass, outcomeLabel, pct, planSize, scenarioDiffers, scenarioGroups, usd, usdEstimate, joinParts, mergeGroups, scoresInRankOrder, verdictText, weightPercents, weightsAreCustom, weightsTotal} from "../../apps/web/bakeoff-report.mjs";

const NAMES = {a: "Alpha Voice", b: "Beta Voice"};
const rate = (value, lo = null, hi = null) => ({value, lo, hi, k: null, n: null});
function report(overrides = {}) {
  const score = (provider, composite, extra = {}) => ({provider, name: NAMES[provider], composite, composite_median_only: composite,
    task_success: rate(1, 0.9, 1), factual: rate(1, 0.8, 1), safe_speech: rate(1), reliability: rate(1), responsiveness: rate(0.9), robustness: rate(1), ...extra});
  return {
    progress: {cells: 4, observed: 4, pending: 0},
    scores: [score("a", 0.99), score("b", 0.98)],
    dimensions: [{dimension: "task_success"}, {dimension: "factual"}],
    latency: [{provider: "a", median_ms: 1010}, {provider: "b", median_ms: 860}],
    conditions: [{provider: "a", kind: "audio", value: "telephone_narrowband", passed: 6, n: 7, order: 0},
      {provider: "b", kind: "audio", value: "telephone_narrowband", passed: 7, n: 7, order: 0},
      {provider: "a", kind: "group", value: "Payment actions", passed: 2, n: 2, order: 4}],
    verdict: {leader: "a", runner_up: "b", leader_vs_runner_up_p: 1, separable: false, median_only_leader: "b",
      significant_pairs: [], eliminated: [], final: true},
    ...overrides,
  };
}

test("the verdict says when leaders can't be separated and when latency would flip the ranking", () => {
  const {lead, sub} = verdictText(report(), NAMES);
  assert.match(lead, /^Alpha Voice ranks first with a composite of 0\.990, ahead of Beta Voice at 0\.980\./);
  assert.match(lead, /can't be told apart \(p = 1\.00\)/);
  assert.match(lead, /median latency alone, Beta Voice would rank first/);
  assert.match(sub, /Every provider passes the safety gates\. No pair differs significantly/);
  assert.match(sub, /Fastest to first audio: Beta Voice \(median 860 ms\)\./);
});

test("the verdict names eliminations, significant pairs and provisional results", () => {
  const r = report({verdict: {leader: "a", runner_up: "b", leader_vs_runner_up_p: 0.01, separable: true, median_only_leader: "a",
    significant_pairs: [{a: "a", b: "b", better: "a"}], eliminated: ["b"], final: false}, progress: {cells: 4, observed: 3, pending: 1}});
  const {lead, sub} = verdictText(r, NAMES);
  assert.match(lead, /confirms the difference \(p = 0\.010\)/);
  assert.doesNotMatch(lead, /median latency alone/);
  assert.match(sub, /Eliminated at the safety gates: Beta Voice\./);
  assert.match(sub, /Alpha Voice over Beta Voice/);
  assert.match(sub, /1 runs still pending: results are provisional\./);
  assert.equal(verdictText(report({progress: {cells: 2, observed: 0, pending: 2}}), NAMES).lead, "No runs have finished yet.");
});

test("the interval axis starts below the lowest score, not the lowest wide interval bound", () => {
  assert.equal(intervalFloor(report()), 0.9);  // both dimensions score 100%: the axis does not start at 40% or 0
  const wide = report();
  wide.scores[0].factual = rate(0.95, 0.15, 1);  // a wide interval no longer drags the axis down to 0
  assert.equal(intervalFloor(wide), 0.8);
  const low = report();
  low.scores[0].factual = rate(0.4, 0.15, 0.7);
  assert.equal(intervalFloor(low), 0.3);
  const perfect = report({scores: [report().scores[0]]});
  for (const d of ["task_success", "factual", "safe_speech", "reliability", "responsiveness", "robustness"]) perfect.scores[0][d] = rate(1);
  assert.equal(intervalFloor(perfect), 0.9);
});

test("the latency axis reports how many turns it clips and labels the cap", () => {
  const values = [...Array(198).fill(800), 3000, 9000];
  const {cap, clipped, label} = latencyCap(values);
  assert.equal(cap, 1000);
  assert.equal(clipped, 2);
  assert.equal(label, "1 s");
  assert.equal(latencyCap([4800, 4900, 5000, 5100]).label, "6 s");
  assert.equal(latencyCap([]).cap, 2500);
});

test("the confidence note combines repetitions, separability, provisional state and untestable criteria", () => {
  const single = confidenceNote(report({limits: {single_repetition: true, repetitions: 1}}), [{criteria: {"CARE-01": "untestable"}}]);
  assert.equal(single.level, "low");
  assert.equal(single.text, "Low confidence: n=1 per scenario, some criteria untestable without judges, leaders not separable");
  const strong = confidenceNote(report({limits: {single_repetition: false, repetitions: 5},
    verdict: {...report().verdict, separable: true}}), [{criteria: {"FACT-01": "pass"}}]);
  assert.deepEqual([strong.level, strong.text], ["high", "High confidence: n=5 per scenario"]);
  const middle = confidenceNote(report({limits: {single_repetition: false, repetitions: 3}}), []);
  assert.deepEqual([middle.level, middle.text], ["medium", "Medium confidence: n=3 per scenario, leaders not separable"]);
  assert.equal(confidenceNote(report({limits: {repetitions: 5}, verdict: {...report().verdict, final: false, separable: true}})).level, "low");
});

test("an empty cost section is detected so it can collapse to one line", () => {
  assert.equal(costIsEmpty(report()), true);
  assert.equal(costIsEmpty(report({cost: [{status: "not_recorded"}, {status: "not_recorded"}]})), true);
  assert.equal(costIsEmpty(report({cost: [{status: "not_recorded"}, {status: "priced"}]})), false);
});

test("only rows that differ keeps scenarios where providers got different results", () => {
  const o = (provider, scenario_id, outcome, repetition = 0) => ({provider, scenario_id, outcome, repetition});
  const obs = [o("a", "s1", "pass"), o("b", "s1", "pass"), o("a", "s2", "pass"), o("b", "s2", "fail"),
    o("a", "s3", "fail"), o("b", "s3", "fail"), o("a", "s4", "pass"), o("a", "s4", "fail", 1), o("b", "s4", "pass"), o("b", "s4", "pass", 1)];
  const differs = id => scenarioDiffers(obs, id, ["a", "b"]);
  assert.deepEqual(["s1", "s2", "s3", "s4"].map(differs), [false, true, false, true]);
  assert.equal(scenarioDiffers(obs, "s2", ["a"]), false);  // a provider with no runs is ignored
});

test("weights are edited as percentages, normalised deterministically and flagged when custom", () => {
  const stored = {task_success: 0.3, factual: 0.2, safe_speech: 0.15, reliability: 0.15, responsiveness: 0.1, robustness: 0.1};
  const pc = weightPercents(stored);
  assert.deepEqual(pc, {task_success: 30, factual: 20, safe_speech: 15, reliability: 15, responsiveness: 10, robustness: 10});
  assert.equal(weightsTotal(pc), 100);
  assert.equal(weightsAreCustom(stored, pc), false);
  assert.equal(weightsAreCustom(stored, {...pc, factual: 25}), true);
  const scaled = normaliseWeights({...pc, factual: 40});  // total 120
  assert.ok(Math.abs(Object.values(scaled).reduce((a, v) => a + v, 0) - 1) < 1e-12);
  assert.equal(scaled.factual, 40 / 120);
  assert.deepEqual(normaliseWeights({...pc, factual: 40}), scaled);  // same inputs, same result
  assert.equal(normaliseWeights({a: 0, b: "x"}), null);
  assert.equal(weightsAreCustom(stored, {a: 0}), true);
});

test("the launch estimate uses recorded per-run medians and says which providers have none", () => {
  const costs = [{provider: "a", median_usd: "0.05"}, {provider: "b", median_usd: null}];
  assert.equal(estimateCost(costs, ["a"], 40).usd, 2);
  assert.deepEqual(estimateCost(costs, ["a", "b"], 40).missing, ["b"]);
  assert.deepEqual(estimateCost(null, ["a"], 1).missing, ["a"]);
});

test("condition rows label audio profiles and keep a cell per provider", () => {
  const rows = conditionRows(report(), ["a", "b"], "audio");
  assert.deepEqual(rows.map(r => r.label), ["Telephone line"]);
  assert.deepEqual(rows[0].cells.map(c => c.passed), [6, 7]);
  assert.deepEqual(conditionRows(report(), ["a", "b"], "group")[0].cells.map(c => c && c.n), [2, null]);
});

test("launch-form groups keep live runnable scenarios in catalogue order", () => {
  const groups = scenarioGroups([
    {scenario_id: "x", execution: "live_adapter", runnable: true, presentation: {group: "Fault tolerance", group_order: 3}},
    {scenario_id: "y", execution: "live_adapter", runnable: true, presentation: {group: "Happy path", group_order: 0}},
    {scenario_id: "z", execution: "mock", runnable: true, presentation: {group: "Happy path", group_order: 0}},
    {scenario_id: "w", execution: "live_adapter", runnable: false, presentation: {group: "Happy path", group_order: 0}},
  ]);
  assert.deepEqual(groups.map(g => [g.group, g.ids]), [["Happy path", ["y"]], ["Fault tolerance", ["x"]]]);
  assert.equal(planSize(33, 4, 5), 660);
});

test("formatting and outcome labels", () => {
  assert.equal(pct(0.874), "87%");
  assert.equal(pct(null), "—");
  assert.equal(ms(862), "862 ms");
  assert.equal(ms(1946), "1.95 s");
  assert.equal(outcomeLabel({outcome: "run_failed"}), "Run failed");
  assert.equal(outcomeLabel({outcome: "review_required"}), "Review required");
  assert.equal(outcomeClass({outcome: "fail"}), "bk-fail");
  assert.equal(outcomeLabel(undefined), "Not run");
});

test("cost rows label each provider's pricing status and list why runs are unpriced", () => {
  const r = report({cost: [
    {name: "Alpha Voice", status: "priced", runs: 3, runs_priced: 3, median_usd: "0.0123", mean_usd: "0.0150", known_usd_all_runs: "0.045", unpriced_reasons: {}},
    {name: "Beta Voice", status: "partial", runs: 3, runs_priced: 1, median_usd: "0.002", mean_usd: "0.002", known_usd_all_runs: "0.003",
      unpriced_reasons: {NO_TARIFF_FOR_METER_DATE: 2}}],
    limits: {pricing_tables: ["vendor@2026-10-19"]}});
  const [a, b] = costRows(r);
  assert.deepEqual([a.status[0], a.priced, a.median, a.known, a.reasons], ["Priced", "3/3", "$0.0123", "$0.0450", "—"]);
  assert.deepEqual([b.status[0], b.priced, b.reasons], ["Partly priced", "1/3", "No tariff for meter date (2)"]);
  assert.match(costLimit(r), /voice usage only.*vendor@2026-10-19\. Not every run is priced for Beta Voice; their known amounts are lower bounds/);
  assert.equal(usd(null), "—");
});

test("reports observed before cost was recorded say so instead of claiming no tariff", () => {
  assert.match(costLimit(report({limits: {}})), /observed before cost was recorded/);
  assert.match(costLimit(report({cost: [{status: "not_recorded"}], limits: {pricing_tables: []}})), /observed before cost was recorded/);
});

test("the cost limit says when a provider's cost comes from a later run", () => {
  const r = report({cost: [{provider: "a", name: "Alpha Voice", status: "priced"}], limits: {pricing_tables: ["t@1"], cost_from_separate_runs: ["a"]}});
  assert.match(costLimit(r), /For Alpha Voice the cost comes from a later run of each scenario/);
});

test("launch estimates show whole cents or <$0.01", () => {
  assert.equal(usdEstimate(4.08), "$4.08");
  assert.equal(usdEstimate(0.004), "<$0.01");
  assert.equal(usdEstimate(0), "$0.00");
  assert.equal(usdEstimate(null), "—");
});

test("joined labels never have stray separators", () => {
  assert.equal(joinParts(["", "Completed", "32/32 runs"]), "Completed · 32/32 runs");
  assert.equal(joinParts([null, " ", "x"]), "x");
});

test("groups merge by name and order by the lowest group_order", () => {
  const m = mergeGroups([{group: "Happy path", order: 3}, {group: "Edge", order: 2}, {group: "Happy path", order: 1}, {group: "Edge", order: 2}]);
  assert.deepEqual(m, [{group: "Happy path", order: 1}, {group: "Edge", order: 2}]);
  const live = scenarioGroups([{execution: "live_adapter", runnable: true, scenario_id: "a", presentation: {group: "G", group_order: 5}},
    {execution: "live_adapter", runnable: true, scenario_id: "b", presentation: {group: "G", group_order: 2}}]);
  assert.equal(live.length, 1); assert.equal(live[0].order, 2);
});

test("scorecard order is by rank, eliminated providers last", () => {
  const out = scoresInRankOrder([{provider: "b", rank: 2}, {provider: "a", rank: 1}]);
  assert.deepEqual(out.map(s => s.provider), ["a", "b"]);
});

test("an explicit light theme overrides the OS dark palette for provider colours", async () => {
  const {readFileSync} = await import("node:fs");
  const css = readFileSync(new URL("../../apps/web/bakeoff-report.css", import.meta.url), "utf8");
  assert.match(css, /prefers-color-scheme: dark\)\s*\{\s*:root:not\(\[data-theme=light\]\) \.bk/);
  assert.match(css, /:root\[data-theme=dark\] \.bk \{ --bk-c0/);
});
