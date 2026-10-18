import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  confirmations, decisions, launchBody, launchOptions, providerChoices, mergeEvents, metricRows, runRows, runView, toolCalls, transcript, unitStates,
} from "../../apps/web/runs.mjs";
import { describeHold, view as reviewView } from "../../apps/web/review.mjs";

// Real payloads captured from the API (tests/browser/fixtures/operator-payloads.json).
const fx = JSON.parse(readFileSync(new URL("./fixtures/operator-payloads.json", import.meta.url)));
const unsafe = fx["unsafe-disclosure-001"];
const verified = fx["balance-verified-001"];

test("run list rows come straight from the list endpoint and keep server order", () => {
  const rows = runRows(fx.runs);
  assert.equal(rows.length, fx.runs.items.length);
  assert.deepEqual(rows.map((r) => r.runId), fx.runs.items.map((r) => r.run_id));
  assert.ok(rows.every((r) => r.terminal || ["queued", "running", "awaiting_playback"].includes(r.state)));
  assert.match(rows[0].scenario, /@1$/);
});

test("only runnable scenarios can be launched and the body is a RunConfig subset", () => {
  const options = launchOptions(fx.scenarios);
  assert.ok(options.length >= 1);
  assert.ok(options.every((o) => fx.scenarios.items.find((s) => s.scenario_id === o.id).runnable));
  assert.deepEqual(launchBody(options[0]), { scenario_id: options[0].id, scenario_version: 1, policy_profile: "strict-v1" });
  assert.deepEqual(launchOptions({ items: [{ scenario_id: "x", version: 2, runnable: false, definition: {} }] }), []);
});

test("unsafe generated audio is shown as blocked and never as released", () => {
  const units = unitStates(unsafe.events);
  assert.equal(units.length, 1);
  assert.equal(units[0].state, "blocked");
  assert.equal(units[0].label, "Blocked");
  const lines = transcript(unsafe.events).filter((l) => l.speaker === "assistant");
  assert.equal(lines[0].generatedButNotReleased, true);
  const view = runView(unsafe.run, unsafe.events, unsafe.metrics, null);
  assert.deepEqual(view.tally, { pending: 0, blocked: 1, released: 0 });
});

test("verified scenario: customer line, released unit and tool lifecycle in ledger order", () => {
  const view = runView(verified.run, verified.events, verified.metrics, null);
  assert.equal(view.transcript[0].speaker, "customer");
  assert.ok(view.tally.released + view.tally.pending + view.tally.blocked >= 1);
  const calls = toolCalls(verified.events);
  assert.ok(calls.length >= 1);
  for (const call of calls) {
    const sequences = call.steps.map((s) => s.sequence);
    assert.deepEqual(sequences, [...sequences].sort((a, b) => a - b));
    assert.ok(call.steps[0].step === "requested" || call.steps[0].step === "assembling");
  }
});

test("identity bypass: denied tool call carries its error code and a guardrail decision", () => {
  const bypass = fx["identity-bypass-001"];
  const denied = toolCalls(bypass.events).find((c) => c.final === "denied");
  assert.ok(denied, "denied call present");
  assert.equal(denied.errorCode, "IDENTITY_REQUIRED");
  assert.ok(decisions(bypass.events).some((d) => d.target === "tool" && d.blocking));
});

test("content-policy decisions with redact/replace/escalate block the unit and show safe text", () => {
  const events = [
    { sequence: 1, type: "audio.output", content: { unit_id: "u1", transcript: "You should invest", response_id: "r" } },
    { sequence: 2, type: "decision", content: { target: "audio_chunk", unit_id: "u1", action: "replace", reason_codes: ["ADVICE_INVESTMENT"], safe_text: "I can't give financial advice.", policy_id: "banking.v1.content", policy_version: "1", latency_ms: 0.1 } },
  ];
  const [unit] = unitStates(events);
  assert.equal(unit.state, "blocked");
  assert.equal(unit.action, "replace");
  assert.match(transcript(events)[0].note, /ADVICE_INVESTMENT; safe text: I can't give/);
  for (const action of ["redact", "escalate", "deny"]) {
    const e = structuredClone(events);
    e[1].content.action = action;
    assert.equal(unitStates(e)[0].state, "blocked", action);
  }
});

test("pending, approved, released, played, expired, discarded and superseded are distinguished", () => {
  const out = (id, seq) => ({ sequence: seq, type: "audio.output", content: { unit_id: id, transcript: id, response_id: "r" } });
  const allow = (id, seq) => ({ sequence: seq, type: "decision", content: { target: "audio_chunk", unit_id: id, action: "allow", reason_codes: [] } });
  const events = [
    out("pend", 1), out("appr", 2), allow("appr", 3), out("rel", 4), allow("rel", 5),
    { sequence: 6, type: "playback.released", content: { unit_id: "rel", token_id: "t" } },
    out("play", 7), allow("play", 8), { sequence: 9, type: "playback.released", content: { unit_id: "play", token_id: "t" } },
    { sequence: 10, type: "playback.ack", content: { unit_id: "play", observed_end_sample: 480 } },
    out("late", 11), { sequence: 12, type: "audio.decision_timeout", content: { unit_id: "late", discarded_unit_ids: ["late", "other"] } },
    out("old", 13), { sequence: 14, type: "audio.approval_replaced", content: { old_unit_id: "old", new_unit_id: "new" } },
  ];
  const by = Object.fromEntries(unitStates(events).map((u) => [u.unitId, u.state]));
  assert.deepEqual(by, { pend: "pending", appr: "approved", rel: "released", play: "played", late: "expired", other: "discarded", old: "superseded" });
});

test("mergeEvents is idempotent and ordered for live polling", () => {
  const first = unsafe.events.slice(0, 5);
  const merged = mergeEvents(first, unsafe.events.slice(3));
  assert.deepEqual(merged.map((e) => e.sequence), unsafe.events.map((e) => e.sequence));
  assert.deepEqual(mergeEvents(merged, merged), merged);
});

test("null metrics keep their stated reason", () => {
  const rows = metricRows({ observations: [{ metric: "a", unit: "ms", value: null, exclusion_reason: "X" }, { metric: "b", unit: "count", value: 1 }] });
  assert.equal(rows[0].reason, "X");
  assert.equal(rows[1].reason, null);
  assert.ok(metricRows(unsafe.metrics).length >= 1);
});

test("confirmations group by request and report the outcome", () => {
  const groups = confirmations([
    { sequence: 1, type: "confirmation.requested", content: { request_id: "q1" } },
    { sequence: 2, type: "customer.confirmation_observed", content: { request_id: "q1" } },
    { sequence: 3, type: "confirmation.accepted", content: { request_id: "q1", confirmation_id: "c1" } },
    { sequence: 4, type: "confirmation.consumed", content: { confirmation_id: "c1" } },
    { sequence: 5, type: "confirmation.rejected", content: { request_id: "q2", reason_code: "OBSERVATION_REPLAYED" } },
  ]);
  assert.equal(groups.find((g) => g.requestId === "q1").steps.length, 3);
  assert.equal(groups.find((g) => g.requestId === "q2").outcome, "rejected: OBSERVATION_REPLAYED");
});

test("hold review: finding-scoped holds are disputes, ordered by urgency, with the 7 day window", () => {
  const model = reviewView(fx.holds);
  assert.equal(model.total, fx.holds.items.filter((h) => h.released_at == null).length);
  assert.ok(model.disputes.length >= 1 && model.disputes.every((h) => h.isDispute && h.findings.length));
  assert.equal(model.disputes[0].urgency, "expiring");
  assert.ok(model.runWide.every((h) => !h.isDispute));
  const day = 86400;
  const base = { hold_id: "h", run_id: "r", owner_id: "o", scope_type: "findings", released_at: null };
  assert.equal(describeHold({ ...base, expires_at: 1000 + 8 * day }, 1000).urgency, "active");
  assert.equal(describeHold({ ...base, expires_at: 1000 + 7 * day }, 1000).urgency, "expiring");
  assert.equal(describeHold({ ...base, expires_at: 999 }, 1000).urgency, "expired");
  assert.equal(describeHold({ ...base, released_at: 5, expires_at: 9e9 }, 1000).urgency, "released");
  const released = { ...fx.holds, items: fx.holds.items.map((h) => ({ ...h, released_at: 1 })) };
  assert.equal(reviewView(released).total, 0);
});

test("a live scenario takes a provider config and unavailable providers are disabled with a reason", () => {
  const options = launchOptions({ items: [
    { scenario_id: "mock-1", version: 1, runnable: true, execution: "mock", definition: {} },
    { scenario_id: "live-1", version: 1, runnable: true, execution: "live_adapter", definition: {} },
  ] });
  assert.deepEqual(launchBody(options[0], "strict-v1", "ignored"), { scenario_id: "mock-1", scenario_version: 1, policy_profile: "strict-v1" });
  assert.equal(launchBody(options[1], "strict-v1", "nova-sonic-live").provider_config_id, "nova-sonic-live");
  const choices = providerChoices({ items: [
    { provider_config_id: "openai-realtime-live", available: true, reason: null },
    { provider_config_id: "nova-sonic-live", available: false, reason: "NO_AWS_SESSION" },
  ] });
  assert.deepEqual(choices.map((c) => c.disabled), [false, true]);
  assert.match(choices[1].label, /NO_AWS_SESSION/);
});
