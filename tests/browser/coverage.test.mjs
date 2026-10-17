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
    { provider_family: "xai_grok_voice", coverage_state: "not_implemented", model_id: null, reason_codes: ["NO_ADAPTER"] },
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
  const [, nova, xai] = view(summary).items;
  assert.equal(xai.stateLabel, "Not implemented");
  assert.equal(xai.counted, false);
  assert.match(xai.reasons[0], /No adapter/);
  assert.equal(nova.counted, false);
  assert.equal(nova.reasons.length, 2);
  for (const i of view(summary).items) assert.equal("score" in i || "rank" in i, false);
});

test("unknown reason codes pass through as text", () => {
  assert.deepEqual(describe({ provider_family: "x", coverage_state: "unavailable", reason_codes: ["NEW_CODE"] }).reasons, ["NEW_CODE"]);
});
