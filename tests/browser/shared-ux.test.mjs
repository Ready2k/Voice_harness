import assert from "node:assert/strict";
import test from "node:test";
import { absoluteTime, humanise, humaniseReportId, relativeTime, runLink, shortId } from "../../apps/web/format.mjs";
import { providerName, PROVIDER_NAMES } from "../../apps/web/providers.mjs";
import { PAGES, pageMeta } from "../../apps/web/nav.mjs";

test("short ids drop the prefix and keep 8 characters; run links target the Runs page", () => {
  assert.equal(shortId("run_6c1e0dc4a1b2c3d4e5f6"), "6c1e0dc4");
  assert.equal(shortId(null), "");
  assert.equal(runLink("run_a b"), "./runs.html?run=run_a%20b");
});

test("relative time has short and long forms and tolerates microsecond ISO strings", () => {
  const now = Date.parse("2026-10-20T12:00:00Z");
  assert.equal(relativeTime("2026-10-17T12:00:00Z", now), "3d ago");
  assert.equal(relativeTime("2026-10-17T12:00:00Z", now, { long: true }), "3 days ago");
  assert.equal(relativeTime("2026-10-20T11:59:00Z", now, { long: true }), "1 minute ago");
  assert.equal(relativeTime("2026-10-17T11:20:18.795999+00:00", now), "3d ago");
  assert.equal(relativeTime("nonsense", now), "");
  assert.equal(absoluteTime("2026-10-17T11:20:18.795999+00:00"), "17 Oct 2026, 11:20 UTC");
});

test("snake_case names become readable labels with acronyms kept", () => {
  assert.equal(humanise("first_audio_ms"), "First audio ms");
  assert.equal(humanise("asr_word_error_rate"), "ASR word error rate");
  assert.equal(humanise("tool-call.latency"), "Tool call latency");
  assert.equal(humanise(""), "Unknown");
});

test("each provider has one display name whatever identifier a record carries", () => {
  for (const id of ["nova-sonic-live", "amazon.nova-2-5-sonic", "aws_nova_sonic"]) assert.equal(providerName(id), "Amazon Nova Sonic");
  assert.equal(providerName("gemini-3.8-live"), "Google Gemini Live");
  assert.equal(providerName("openai"), PROVIDER_NAMES["openai-realtime-live"]);
  assert.equal(providerName("brand-new-live"), "brand-new-live");
  assert.equal(providerName(null), "Unknown provider");
});

test("page template gives one title and breadcrumb per page, with Review & holds named consistently", () => {
  assert.deepEqual(pageMeta("Review & holds"), { title: "Review & holds · Voice Harness", breadcrumb: "Voice Harness / Review & holds" });
  assert.equal(Object.keys(PAGES).length, 9);
  assert.equal(PAGES["telephone.html"], "Telephone");
  assert.equal(PAGES["review.html"], "Review & holds");
});

test("provider names resolve raw model IDs", () => {
  for (const id of ["gpt-realtime-2.1", "gpt-realtime-mini"]) assert.equal(providerName(id), "OpenAI Realtime");
  for (const id of ["grok-voice-3.0", "grok-voice-latest", "grok-transcribe", "grok-voice-4"]) assert.equal(providerName(id), "xAI Grok Voice");
  assert.equal(providerName("gemini-3.8-live"), "Google Gemini Live");
  assert.equal(providerName("gemini-live"), "Google Gemini Live");
  assert.equal(providerName("amazon.nova-2-5-sonic"), "Amazon Nova Sonic");
});

test("humaniseReportId makes capability report IDs readable", () => {
  assert.equal(humaniseReportId("cap_google_gemini_live_2_5_flash_native_audio_20261018"), "Google Gemini Live 2.5 flash native audio (18 Oct 2026)");
  assert.equal(humaniseReportId("cap_xai_grok_voice_3_0"), "xAI Grok Voice 3.0");
  assert.equal(humaniseReportId("cap_openai_gpt_realtime_2_1_20261016"), "OpenAI GPT realtime 2.1 (16 Oct 2026)");
  assert.equal(humaniseReportId(""), "Unknown report");
});

test("bake-off server NAMES agree with providers.mjs display names for every family", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../../src/voice_harness/bakeoff.py", import.meta.url), "utf8");
  const block = src.match(/^NAMES = \{([\s\S]*?)\}/m)[1];
  const pairs = [...block.matchAll(/"([^"]+)":\s*"([^"]+)"/g)];
  assert.ok(pairs.length >= 4);
  for (const [, id, name] of pairs) assert.equal(providerName(id), name, id);
});

import { stateLabel, scrollRegion } from '../../apps/web/format.mjs';
import { test as t2 } from 'node:test';
import assert2 from 'node:assert/strict';
t2('stateLabel is readable', () => {
  assert2.equal(stateLabel('completed'), 'Completed');
  assert2.equal(stateLabel('awaiting_playback'), 'Awaiting playback');
  assert2.equal(stateLabel(null), 'Unknown');
});
t2('scrollRegion makes a focusable named region', () => {
  const attrs = {}; const w = { classList: { add() {} }, setAttribute: (k, v) => { attrs[k] = v; } };
  assert2.equal(scrollRegion(w, 'X table'), w);
  assert2.equal(w.tabIndex, 0); assert2.equal(attrs.role, 'region'); assert2.equal(attrs['aria-label'], 'X table');
});

test("ElevenLabs resolves to one display name from its config id, family and model id", () => {
  for (const id of ["elevenlabs-live", "elevenlabs_agents", "elevenlabs-agent"]) assert.equal(providerName(id), "ElevenLabs Agents");
  assert.equal(PROVIDER_NAMES["elevenlabs-live"], "ElevenLabs Agents");
});
