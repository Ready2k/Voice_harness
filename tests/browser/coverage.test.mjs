import assert from "node:assert/strict";
import test from "node:test";
import { describe, view } from "../../apps/web/coverage.mjs";

const summary = {
  statement: "verified for 1 of 4 requested provider families",
  verified_count: 1,
  requested_count: 4,
  ranking: null,
  items: [
    { provider_family: "openai", coverage_state: "implemented_and_verified", model_id: "gpt-realtime-2.1", region: null,
      checked_at: "2026-10-16T00:00:00+00:00", capability_report_id: "cap_o", reason_codes: [], evidence_ids: ["a", "b"] },
    { provider_family: "aws_nova_sonic", coverage_state: "implemented_but_unverified", model_id: "amazon.nova-2-5-sonic", region: "us-east-1",
      reason_codes: ["REPORT_EXPIRED", "AC09_NOT_PASSED"], evidence_ids: [] },
    { provider_family: "xai_grok_voice", coverage_state: "implemented_but_unverified", model_id: "grok-voice-3.0", region: null,
      reason_codes: ["AC09_NOT_PASSED"], evidence_ids: [] },
    { provider_family: "google_gemini_live", coverage_state: "not_implemented", model_id: null, reason_codes: ["NO_ADAPTER"] },
  ],
};

test("all families stay visible in server order and only verified counts", () => {
  const m = view(summary);
  assert.deepEqual(m.items.map((i) => i.family), ["openai", "aws_nova_sonic", "xai_grok_voice", "google_gemini_live"]);
  assert.equal(m.items.filter((i) => i.counted).length, 1);
  assert.equal(m.requested, 4);
});

test("unavailable and unimplemented providers carry explicit reasons, no score", () => {
  const [, nova, xai, google] = view(summary).items;
  assert.equal(google.stateLabel, "Not implemented");
  assert.equal(google.counted, false);
  assert.match(google.reasons[0], /No adapter/);
  assert.equal(xai.stateLabel, "Implemented, not verified");
  assert.equal(xai.counted, false);
  assert.match(xai.reasons[0], /AC-09/);
  assert.equal(nova.counted, false);
  assert.equal(nova.reasons.length, 2);
  for (const i of view(summary).items) assert.equal("score" in i || "rank" in i, false);
});

test("unknown reason codes pass through as text", () => {
  assert.deepEqual(describe({ provider_family: "x", coverage_state: "unavailable", reason_codes: ["NEW_CODE"] }).reasons, ["NEW_CODE"]);
});

test("runtime-unavailable reason has explanatory text and stays uncounted", () => {
  const m = describe({ provider_family: "aws_nova_sonic", coverage_state: "implemented_but_unverified",
    reason_codes: ["ADAPTER_RUNTIME_UNAVAILABLE"] });
  assert.equal(m.counted, false);
  assert.match(m.reasons[0], /cannot run in this environment/);
});

import { expiryOf, readinessFor, summaryOf, REPORT_LIFETIME_DAYS } from "../../apps/web/coverage.mjs";
const DAYMS = 86400000;
const T0 = Date.parse("2026-10-16T00:00:00+00:00");

test("expiry: 7-day lifetime, ok/soon/expired with injectable now", () => {
  assert.equal(REPORT_LIFETIME_DAYS, 7);
  const ok = expiryOf("2026-10-16T00:00:00+00:00", T0 + 3 * DAYMS);
  assert.equal(ok.state, "ok"); assert.equal(ok.label, "expires in 4 days");
  assert.equal(expiryOf("2026-10-16T00:00:00+00:00", T0 + 5 * DAYMS + 1000).state, "soon");
  assert.equal(expiryOf("2026-10-16T00:00:00+00:00", T0 + 6.5 * DAYMS).label, "expires in 12 hours");
  const gone = expiryOf("2026-10-16T00:00:00+00:00", T0 + 9 * DAYMS);
  assert.equal(gone.state, "expired"); assert.equal(gone.label, "expired 2 days ago");
  assert.equal(expiryOf(null).state, "none");
  assert.equal(expiryOf("garbage").state, "none");
});

test("readiness merges by config id and never invents readiness", () => {
  const r = { shared: [], providers: [{ id: "nova-sonic-live", ready: false, fixes: ["fix"] }, { id: "gemini-live", ready: true, fixes: [] }] };
  assert.equal(readinessFor("gemini-live", r).status, "ready");
  assert.deepEqual(readinessFor("nova-sonic-live", r), { status: "setup", fixes: ["fix"] });
  assert.equal(readinessFor("openai-realtime-live", r).status, "none");
  assert.equal(readinessFor("gemini-live", null).status, "unknown");
  const m = view(summary);
  assert.deepEqual(summaryOf(m, r), { verified: 1, requested: 4, ready: 1, checked: true, expiring: summaryOf(m, r).expiring });
  assert.equal(summaryOf(m, null).ready, 0);
  assert.equal(m.items[0].configId, "openai-realtime-live");
  assert.equal(m.items[0].title, "OpenAI Realtime");
});

import { render, renderSummary, probeRow } from "../../apps/web/coverage.mjs";
function fakeDocument() {
  const mk = (tag) => ({ tag, className: "", children: [], textContent: "", title: "",
    append(...n) { this.children.push(...n); }, replaceChildren(...n) { this.children = n; } });
  return { createElement: mk };
}
const flat = (n) => [n.textContent, ...(n.children ?? []).flatMap(flat)];

test("renderSummary honours the injected now (expiring-soon tile)", () => {
  globalThis.document = fakeDocument();
  const m = view(summary); // openai verified, checked 2026-10-16
  const tiles = (now) => { const root = document.createElement("div"); renderSummary(root, m, null, now); return flat(root); };
  assert.ok(tiles(T0 + 1 * DAYMS).includes("0"));
  assert.equal(tiles(T0 + 1 * DAYMS).filter((t) => t === "1").length, 0);
  assert.ok(tiles(T0 + 6 * DAYMS).includes("1"));
  delete globalThis.document;
});

test("server expires_at is preferred over checked_at + 7 days", () => {
  const x = expiryOf("2026-10-16T00:00:00+00:00", T0 + 3 * DAYMS, "2026-10-20T00:00:00+00:00");
  assert.equal(x.source, "expires_at"); assert.equal(x.label, "expires in 1 day");
  assert.equal(expiryOf("2026-10-16T00:00:00+00:00", T0).source, "checked_at");
  assert.equal(describe({ provider_family: "openai", coverage_state: "implemented_and_verified", expires_at: "2026-10-23T00:00:00+00:00" }).expiresAt, "2026-10-23T00:00:00+00:00");
});

test("a skewed browser clock never shows Expired beside a server-verified state", () => {
  globalThis.document = { createElement: (tag) => ({ tag, className: "", children: [], textContent: "", title: "",
    append(...n) { this.children.push(...n); }, replaceChildren(...n) { this.children = n; }, addEventListener() {}, setAttribute() {}, classList: { add() {} } }) };
  const root = document.createElement("div");
  render(root, view(summary), null, T0 + 30 * DAYMS);
  const text = flat(root).join("|");
  assert.match(text, /Verified per server/);
  assert.match(text, /your clock may differ/);
  assert.doesNotMatch(text, /^Expired|\|Expired/);
  const classes = (n) => [n.className, ...(n.children ?? []).flatMap(classes)];
  assert.equal(classes(root).some((c) => /expiry-expired/.test(c)), false);
  delete globalThis.document;
});

test("probeRow reports direction and errors without inventing an outcome", () => {
  const r = probeRow({ source: "a.jsonl", event: { id: "e1", t: "2026-10-17T00:00:00Z", dir: "in", type: "ws_open", detail: {} } });
  assert.equal(r.outcome, "received from provider"); assert.equal(r.failed, false);
  assert.equal(probeRow({ event: { id: "e2", type: "error", dir: "in", detail: { error: "x" } } }).failed, true);
});
