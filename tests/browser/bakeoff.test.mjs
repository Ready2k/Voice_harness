import test from "node:test";
import assert from "node:assert/strict";
import {conditionRows, costLimit, costRows, intervalFloor, ms, outcomeClass, outcomeLabel, pct, planSize, scenarioGroups, usd, verdictText} from "../../apps/web/bakeoff-report.mjs";

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

test("the interval axis starts just below the lowest bound so overlaps stay visible", () => {
  assert.equal(intervalFloor(report()), 0.7);
  const low = report();
  low.scores[0].factual = rate(0.4, 0.15, 0.7);
  assert.equal(intervalFloor(low), 0);
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
