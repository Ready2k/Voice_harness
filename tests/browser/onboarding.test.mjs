import assert from "node:assert/strict";
import test from "node:test";
import { friendlyError, readinessItems, TOKEN_COMMAND } from "../../apps/web/onboarding.mjs";

test("readiness shows what every provider is waiting on once, then each provider's own gaps", () => {
  const { shared, providers } = readinessItems({ items: [
    { provider_config_id: "openai-realtime-live", available: false, reason: "NO_ASR_BACKEND_CONFIGURED", reasons: ["NO_ASR_BACKEND_CONFIGURED", "NO_CALLER_VOICE_SYNTH"] },
    { provider_config_id: "nova-sonic-live", available: false, reason: "NO_ASR_BACKEND_CONFIGURED", reasons: ["NO_ASR_BACKEND_CONFIGURED", "NO_CALLER_VOICE_SYNTH", "NO_AWS_SESSION"] },
    { provider_config_id: "gemini-live", available: false, reason: "NO_ASR_BACKEND_CONFIGURED", reasons: ["NO_ASR_BACKEND_CONFIGURED", "NO_CALLER_VOICE_SYNTH", "NO_GOOGLE_ADC", "SOMETHING_NEW"] },
  ] });
  assert.deepEqual(shared.map((s) => s.code), ["NO_ASR_BACKEND_CONFIGURED", "NO_CALLER_VOICE_SYNTH"]);
  assert.match(shared[0].fix, /make serve-live/);
  assert.deepEqual(providers[0].fixes, []);
  assert.match(providers[1].fixes[0], /aws-session\.sh/);
  assert.match(providers[2].fixes[0], /application-default login/);
  assert.equal(providers[2].fixes[1], "Unavailable: SOMETHING_NEW");
});

test("a ready provider has no fixes, nothing is shared when one is ready, and old payloads still work", () => {
  const { shared, providers } = readinessItems({ items: [
    { provider_config_id: "a", available: true, reason: null, reasons: [] },
    { provider_config_id: "b", available: false, reason: "NO_XAI_API_KEY" },
  ] });
  assert.deepEqual(shared, []);
  assert.deepEqual(providers.map((p) => [p.ready, p.fixes.length]), [[true, 0], [false, 1]]);
  assert.deepEqual(readinessItems(undefined), { shared: [], providers: [] });
});

test("friendly errors explain a rejected token and an unreachable server, and keep other codes", () => {
  assert.ok(friendlyError(new Error("AUTH_REQUIRED")).includes(TOKEN_COMMAND));
  assert.ok(friendlyError(new Error("HTTP 401")).includes(TOKEN_COMMAND));
  assert.match(friendlyError(new TypeError("Failed to fetch")), /make up/);
  assert.match(friendlyError(new Error("HTTP 403")), /operator token/);
  assert.equal(friendlyError(new Error("INVALID_PAGINATION")), "INVALID_PAGINATION");
});
