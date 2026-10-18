import assert from "node:assert/strict";
import test from "node:test";
import { friendlyError, readinessItems, TOKEN_COMMAND } from "../../apps/web/onboarding.mjs";

test("readiness turns each reason code into a fix, and shows unknown codes verbatim", () => {
  const items = readinessItems({ items: [
    { provider_config_id: "openai-realtime-live", available: true, reason: null },
    { provider_config_id: "nova-sonic-live", available: false, reason: "NO_AWS_SESSION" },
    { provider_config_id: "gemini-live", available: false, reason: "NO_GOOGLE_ADC" },
    { provider_config_id: "x", available: false, reason: "SOMETHING_NEW" },
  ] });
  assert.deepEqual(items.map((i) => i.ready), [true, false, false, false]);
  assert.equal(items[0].fix, null);
  assert.match(items[1].fix, /aws-session\.sh/);
  assert.match(items[2].fix, /application-default login/);
  assert.equal(items[3].fix, "Unavailable: SOMETHING_NEW");
  assert.deepEqual(readinessItems(undefined), []);
});

test("friendly errors explain a rejected token and an unreachable server, and keep other codes", () => {
  assert.ok(friendlyError(new Error("AUTH_REQUIRED")).includes(TOKEN_COMMAND));
  assert.ok(friendlyError(new Error("HTTP 401")).includes(TOKEN_COMMAND));
  assert.match(friendlyError(new TypeError("Failed to fetch")), /make up/);
  assert.match(friendlyError(new Error("HTTP 403")), /operator token/);
  assert.equal(friendlyError(new Error("INVALID_PAGINATION")), "INVALID_PAGINATION");
});
