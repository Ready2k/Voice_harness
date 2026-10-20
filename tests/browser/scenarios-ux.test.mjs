import assert from "node:assert/strict";
import test from "node:test";
import { bakeoffIndex, buildGroups, expectedTools, filterOptions, latestResults, matches, matrix, summaryModel, tally } from "../../apps/web/scenarios-view.mjs";

const def = (locale, audio, extra = {}) => ({ locale, audio_profile: audio, customer: { persona: "social_engineer", vulnerability: "none" }, goal: "x_y", initial_state: { identity: "unverified", account_fixture: "fx" },
  turns: [{ customer: "hi", prohibited_actions: ["get_account_summary"] }], stop_conditions: [], expected_tool_sequence: [], applicable_criteria: ["IDENTITY-01", "ZZ-99"], max_turns: 5, max_duration_seconds: 60, ...extra });
const sc = (id, locale, audio, label) => ({ scenario_id: id, version: 1, runnable: true, execution: "live_adapter", presentation: { group: "Identity", group_order: 1, title: "Fraud caller · " + locale + " · " + label, expected_outcome: "Refuse" }, definition: def(locale, audio) });
const items = [sc("a", "en-GB", "clean_studio", "clean audio"), sc("b", "en-GB", "street_noise_10db", "street noise"), sc("c", "en-US", "clean_studio", "clean audio"),
  { scenario_id: "stored-1", version: 2, runnable: false, definition: def("en-GB", "clean_studio") }];

test("locale x audio variants collapse into one family and a matrix", () => {
  const groups = buildGroups(items);
  assert.deepEqual(groups.map((g) => g.group), ["Identity", "Other scenarios"]);
  const fam = groups[0].families;
  assert.equal(fam.length, 1);
  assert.equal(fam[0].title, "Fraud caller");
  const m = matrix(fam[0].variants);
  assert.deepEqual(m.audios, ["clean_studio", "street_noise_10db"]);
  assert.deepEqual(m.rows.map((r) => r.locale), ["en-GB", "en-US"]);
  assert.equal(m.rows[1].cells[1], null);
  assert.equal(m.isMatrix, true);
});

test("filters combine group, locale, audio, criterion, mode and text", () => {
  const f = { query: "", group: "all", locale: "en-US", audio: "all", criterion: "IDENTITY-01", mode: "all" };
  assert.deepEqual(items.filter((s) => matches(s, f)).map((s) => s.scenario_id), ["c"]);
  assert.deepEqual(items.filter((s) => matches(s, { mode: "stored" })).map((s) => s.scenario_id), ["stored-1"]);
  assert.deepEqual(items.filter((s) => matches(s, { group: "Identity", audio: "street_noise_10db" })).map((s) => s.scenario_id), ["b"]);
  const o = filterOptions(items);
  assert.deepEqual(o.locales, ["en-GB", "en-US"]);
  assert.deepEqual(o.criteria, ["IDENTITY-01", "ZZ-99"]);
});

test("latest result per provider keeps the newest run and links bake-off attempts", () => {
  const runs = [
    { run_id: "run_new", scenario_id: "a", provider_config_id: "p1", state: "completed", assurance: { overall_assurance: "fail" }, started_at: "2026-10-20T10:00:00Z" },
    { run_id: "run_old", scenario_id: "a", provider_config_id: "p1", state: "completed", assurance: { overall_assurance: "pass" }, started_at: "2026-10-19T10:00:00Z" },
    { run_id: "run_p2", scenario_id: "a", provider_config_id: "p2", state: "completed", assurance: { overall_assurance: "pass" } },
  ];
  const r = latestResults(runs).get("a");
  assert.equal(r.get("p1").run_id, "run_new");
  assert.equal(r.get("p1").result.key, "fail");
  assert.deepEqual(tally(r), { pass: 1, fail: 1, other: 0 });
  const idx = bakeoffIndex([{ bakeoff: { bakeoff_id: "bo_1", name: "N" }, cells: [{ attempts: [{ run_id: "run_p2" }] }] }]);
  assert.equal(idx.get("run_p2").id, "bo_1");
});

test("an empty expected tool sequence reads as by design, not missing data", () => {
  const t = expectedTools(items[0].definition);
  assert.equal(t.text, "No tool calls expected");
  assert.match(t.note, /get account summary/);
  assert.equal(expectedTools({ expected_tool_sequence: ["verify_identity", "get_account_summary"], turns: [] }).text, "verify_identity → get_account_summary");
});

test("structured summary carries persona, state and criteria with help text", () => {
  const m = summaryModel(items[0]);
  assert.equal(m.persona, "Social engineer");
  assert.equal(m.audio, "Clean audio");
  assert.match(m.criteria[0].help, /verified/);
  assert.match(m.criteria[1].help, /No description/);
});

test("tool IDs get readable labels, raw ids stay in text", async () => {
  const { toolLabel } = await import("../../apps/web/scenarios-view.mjs");
  assert.equal(toolLabel("verify_identity"), "Verify identity");
  const t = expectedTools({ expected_tool_sequence: ["verify_identity", "get_account_summary"], turns: [{ prohibited_actions: ["transfer_funds"] }] });
  assert.deepEqual(t.labels, ["Verify identity", "Get account summary"]);
  assert.match(expectedTools({ expected_tool_sequence: [], turns: [{ prohibited_actions: ["transfer_funds"] }] }).note, /transfer funds/);
});
